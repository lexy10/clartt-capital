"""FastAPI router for trade reconciliation endpoints.

`POST /reconciliation/deriv/sweep` — for a given Deriv account, finds all
trades in the DB that are still "open" (status=filled, closed_at IS NULL)
and writes their exit details back using Deriv's `proposal_open_contract`
(per-contract) with a `profit_table` fallback for older contracts.

This is the safety net behind PositionMonitor: if the monitor missed a
close (process restart, broker SL/TP fired while we were down, etc.),
the reconciler catches up.
"""

from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

logger = logging.getLogger("execution_engine.reconciliation")

router = APIRouter(prefix="/reconciliation", tags=["reconciliation"])

_trade_persister = None  # type: ignore[assignment]
_broker_router = None    # type: ignore[assignment]


def configure(trade_persister, broker_router) -> None:
    """Wire dependencies from main.py at startup."""
    global _trade_persister, _broker_router
    _trade_persister = trade_persister
    _broker_router = broker_router


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class DerivSweepRequest(BaseModel):
    """Reconcile a single Deriv account's open trades against the broker.

    `account_id` is our internal account UUID. `deriv_api_token` authorizes
    the WS session. `lookback_hours` bounds the profit_table query when
    `proposal_open_contract` fails (contracts too old).

    `refresh_closed_hours` (optional): also re-reconcile already-closed
    trades within this many hours back. Useful for correcting historical
    rows after a fix to the close-parsing logic.
    """
    account_id: str
    deriv_api_token: str
    deriv_login_id: Optional[str] = None
    lookback_hours: int = 168  # 7 days
    refresh_closed_hours: int = 0


class SweepResult(BaseModel):
    account_id: str
    candidates: int       # trades flagged as open in our DB
    updated: int          # successfully written exits
    still_open: int       # contracts still open at broker (no action)
    failed: int           # contracts we couldn't resolve
    via_profit_table: int = 0
    via_proposal: int = 0


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


@contextmanager
def _db_conn():
    """Borrow a connection from the TradePersister's pool."""
    if _trade_persister is None:
        raise RuntimeError("Reconciler: TradePersister not configured")
    _trade_persister._ensure_pool()
    pool = _trade_persister._pool
    conn = pool.getconn()
    try:
        yield conn
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        pool.putconn(conn)


def _list_open_trades(
    account_id: str,
    refresh_closed_hours: int = 0,
) -> list[tuple[int, datetime]]:
    """Return [(broker_order_id, opened_at), ...] for trades to reconcile.

    Always includes trades still open in our DB (closed_at IS NULL).
    If refresh_closed_hours > 0, also includes recently-closed trades
    so we can re-fetch their exit data from the broker.
    """
    with _db_conn() as conn, conn.cursor() as cur:
        if refresh_closed_hours and refresh_closed_hours > 0:
            cur.execute(
                """
                SELECT broker_order_id, opened_at
                  FROM trades
                 WHERE account_id = %s
                   AND status IN ('filled', 'closed')
                   AND broker_order_id IS NOT NULL
                   AND (
                        closed_at IS NULL
                        OR closed_at >= now() - (%s * interval '1 hour')
                   )
                 ORDER BY opened_at ASC
                """,
                (account_id, refresh_closed_hours),
            )
        else:
            cur.execute(
                """
                SELECT broker_order_id, opened_at
                  FROM trades
                 WHERE account_id = %s
                   AND status = 'filled'
                   AND closed_at IS NULL
                   AND broker_order_id IS NOT NULL
                 ORDER BY opened_at ASC
                """,
                (account_id,),
            )
        rows = cur.fetchall() or []
    return [(int(r[0]), r[1]) for r in rows]


# ---------------------------------------------------------------------------
# Sweep endpoint
# ---------------------------------------------------------------------------


@router.post("/deriv/sweep", response_model=SweepResult)
def deriv_sweep(request: DerivSweepRequest) -> SweepResult:
    """Walk the account's open trades and reconcile each against Deriv.

    For each open trade:
      1. Try `proposal_open_contract` — works for recent contracts (still
         open OR sold within Deriv's lookback window).
      2. If that returns None and the contract isn't in the broker's
         current portfolio, fall back to `profit_table` to find its
         historical sell record.

    Each successful reconciliation writes an exit row via TradePersister.
    """
    if _trade_persister is None:
        raise HTTPException(status_code=502, detail="TradePersister not configured")
    if _broker_router is None:
        raise HTTPException(status_code=502, detail="BrokerRouter not configured")

    # Get Deriv client
    from src.executor.clients.base import BrokerProvider
    deriv_client = _broker_router.get(BrokerProvider.DERIV)
    if deriv_client is None:
        raise HTTPException(status_code=502, detail="Deriv client not registered")

    # Authorize with per-account token
    login = request.deriv_login_id or request.account_id
    try:
        if hasattr(deriv_client, "connect_with_token"):
            deriv_client.connect_with_token(login, request.deriv_api_token)
        else:
            raise HTTPException(
                status_code=502,
                detail="Deriv client does not support per-account token auth",
            )
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("Deriv connect_with_token failed: %s", exc)
        raise HTTPException(
            status_code=502,
            detail=f"Deriv authorize failed: {exc}",
        )

    candidates = _list_open_trades(
        request.account_id,
        refresh_closed_hours=request.refresh_closed_hours,
    )
    logger.info(
        "Reconciler: %d candidate trades for account %s (refresh_closed_hours=%d)",
        len(candidates), request.account_id, request.refresh_closed_hours,
    )
    if not candidates:
        return SweepResult(
            account_id=request.account_id,
            candidates=0, updated=0, still_open=0, failed=0,
        )

    # Build broker's current open set so we know what's still open vs. closed
    try:
        broker_positions = deriv_client.get_positions() or []
    except Exception as exc:
        logger.warning("Reconciler: get_positions failed: %s", exc)
        broker_positions = []
    open_ids: set[int] = set()
    for p in broker_positions:
        try:
            open_ids.add(int(p.get("id")))
        except (TypeError, ValueError):
            continue

    updated = 0
    still_open = 0
    failed = 0
    via_profit_table = 0
    via_proposal = 0

    # Build a cached profit_table index keyed by contract_id (lazy)
    profit_index: Optional[dict[int, "object"]] = None

    def _ensure_profit_index():
        nonlocal profit_index
        if profit_index is not None:
            return profit_index
        from datetime import timedelta
        date_from = datetime.now(timezone.utc) - timedelta(
            hours=max(1, request.lookback_hours),
        )
        try:
            rows = deriv_client.fetch_profit_table(date_from=date_from)
        except Exception as exc:
            logger.warning("Reconciler: profit_table fetch failed: %s", exc)
            rows = []
        profit_index = {r.broker_order_id: r for r in rows}
        logger.info(
            "Reconciler: profit_table index built — %d rows (since %s)",
            len(profit_index), date_from.isoformat(),
        )
        return profit_index

    for contract_id, opened_at in candidates:
        if contract_id in open_ids:
            still_open += 1
            continue
        # Try the per-contract endpoint first (most accurate)
        details = None
        if hasattr(deriv_client, "fetch_closed_contract"):
            try:
                details = deriv_client.fetch_closed_contract(contract_id)
            except Exception as exc:
                logger.debug("fetch_closed_contract %s failed: %s", contract_id, exc)
                details = None
            if details is not None:
                via_proposal += 1
        # Fall back to profit_table for older contracts
        if details is None:
            idx = _ensure_profit_index()
            details = idx.get(contract_id)
            if details is not None:
                via_profit_table += 1
        if details is None:
            failed += 1
            logger.warning(
                "Reconciler: could not resolve contract %s (opened %s)",
                contract_id, opened_at.isoformat() if opened_at else "?",
            )
            continue
        ok = _trade_persister.record_exit(
            broker_order_id=details.broker_order_id,
            exit_price=details.exit_price,
            profit_loss=details.profit_loss,
            closed_at=details.closed_at,
            status="closed",
        )
        if ok:
            updated += 1
            logger.info(
                "Reconciler: updated trade for contract %s (pnl=%.2f, closed_at=%s)",
                contract_id, details.profit_loss, details.closed_at.isoformat(),
            )
        else:
            failed += 1

    return SweepResult(
        account_id=request.account_id,
        candidates=len(candidates),
        updated=updated,
        still_open=still_open,
        failed=failed,
        via_profit_table=via_profit_table,
        via_proposal=via_proposal,
    )


@router.get("/deriv/open-trades/{account_id}")
def list_open_trades(account_id: str) -> dict:
    """Diagnostic: list trades that look open in our DB for an account."""
    if _trade_persister is None:
        raise HTTPException(status_code=502, detail="TradePersister not configured")
    rows = _list_open_trades(account_id)
    return {
        "account_id": account_id,
        "count": len(rows),
        "open_trades": [
            {"broker_order_id": cid, "opened_at": opened_at.isoformat() if opened_at else None}
            for cid, opened_at in rows
        ],
    }
