"""TradePersister — write trade rows directly to PostgreSQL from the executor.

Why direct DB writes?
- The backend's pub/sub subscriber for `trades:results` is flaky in production:
  ioredis sometimes silently drops messages after restarts.
- Trade persistence is a critical correctness path; it should not depend on a
  best-effort pub/sub channel.
- Direct writes are atomic — the trade is recorded the instant it fills.

The pub/sub channel still publishes for WebSocket dashboard updates;
persistence just doesn't depend on it anymore.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)


class TradePersister:
    """Synchronous Postgres writer for trade rows.

    Uses psycopg2 with a small connection pool. Lazy-initializes on first
    write so unit tests can construct without requiring a DB.

    Usage:
        persister = TradePersister()
        persister.record_entry(
            trade_id=..., signal_id=..., account_id=...,
            instrument='R_25', direction='BUY',
            entry_price=2785.0, fill_price=2785.0, position_size=0.02,
            broker_order_id=315000000099, status='filled',
            execution_latency_ms=1850, slippage=0.0, spread_at_execution=0.0,
            opened_at=datetime.now(timezone.utc),
        )

        persister.record_exit(
            trade_id=..., broker_order_id=315000000099,
            exit_price=2790.0, profit_loss=0.1,
            closed_at=datetime.now(timezone.utc), status='closed',
        )
    """

    def __init__(self, database_url: Optional[str] = None) -> None:
        self._database_url = database_url or os.environ.get("DATABASE_URL", "")
        self._lock = threading.RLock()
        self._pool = None  # psycopg2 SimpleConnectionPool, lazy

    def _ensure_pool(self) -> None:
        if self._pool is not None:
            return
        if not self._database_url:
            raise RuntimeError("TradePersister: DATABASE_URL not configured")
        # Import lazily so module imports cleanly even without psycopg installed
        from psycopg2 import pool

        with self._lock:
            if self._pool is None:
                self._pool = pool.SimpleConnectionPool(
                    minconn=1, maxconn=5, dsn=self._database_url,
                )
                logger.info("TradePersister: connection pool ready")

    @contextmanager
    def _conn(self):
        self._ensure_pool()
        conn = self._pool.getconn()
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
            self._pool.putconn(conn)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def record_entry(
        self,
        trade_id: str,
        signal_id: Optional[str],
        account_id: Optional[str],
        instrument: str,
        direction: str,
        entry_price: Optional[float],
        fill_price: Optional[float],
        position_size: float,
        broker_order_id: Optional[int],
        status: str,
        execution_latency_ms: Optional[int],
        slippage: Optional[float],
        spread_at_execution: Optional[float],
        opened_at: Optional[datetime] = None,
        rejection_reason: Optional[str] = None,
    ) -> bool:
        """Insert a new trade row. Idempotent — if trade_id exists, no-ops.

        Returns True on insert, False on duplicate / error.
        """
        if status.lower() not in ("filled", "partial"):
            logger.debug("TradePersister: skipping non-filled trade %s (status=%s)", trade_id, status)
            return False

        opened_at = opened_at or datetime.now(timezone.utc)

        def _do_insert(sid: Optional[str]) -> Optional[str]:
            with self._conn() as conn, conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO trades (
                        id, signal_id, account_id, broker_order_id,
                        instrument, direction,
                        entry_price, fill_price, position_size, status,
                        execution_latency_ms, slippage, spread_at_execution,
                        rejection_reason, opened_at
                    ) VALUES (
                        %s, %s, %s, %s,
                        %s, %s,
                        %s, %s, %s, %s,
                        %s, %s, %s,
                        %s, %s
                    )
                    ON CONFLICT (id) DO NOTHING
                    RETURNING id
                    """,
                    (
                        trade_id,
                        sid,
                        account_id,
                        broker_order_id,
                        instrument,
                        direction,
                        entry_price,
                        fill_price,
                        float(position_size),
                        status,
                        int(execution_latency_ms) if execution_latency_ms is not None else None,
                        slippage,
                        spread_at_execution,
                        rejection_reason,
                        opened_at,
                    ),
                )
                row = cur.fetchone()
                return row[0] if row else None

        try:
            inserted = _do_insert(signal_id)
            if inserted is None:
                logger.debug("TradePersister: trade %s already exists, skipped", trade_id)
                return False
            logger.info("TradePersister: recorded trade %s (order=%s)", trade_id, broker_order_id)
            return True
        except Exception as exc:
            # Most common cause: signal_id FK violation when signal wasn't persisted
            # (manually-injected test signals). Fall back to NULL signal_id so the
            # trade row still lands — strategy_id is recoverable from logs if needed.
            from psycopg2.errors import ForeignKeyViolation
            if isinstance(exc, ForeignKeyViolation) and signal_id is not None:
                logger.warning(
                    "TradePersister: FK violation on signal_id=%s, retrying with NULL",
                    signal_id,
                )
                try:
                    inserted = _do_insert(None)
                    if inserted is not None:
                        logger.info(
                            "TradePersister: recorded trade %s (order=%s, signal_id=NULL)",
                            trade_id, broker_order_id,
                        )
                        return True
                except Exception as inner_exc:
                    logger.exception("TradePersister: retry also failed: %s", inner_exc)
                    return False
            logger.exception("TradePersister: failed to record entry %s: %s", trade_id, exc)
            return False

    def record_exit(
        self,
        trade_id: Optional[str] = None,
        broker_order_id: Optional[int] = None,
        exit_price: Optional[float] = None,
        profit_loss: Optional[float] = None,
        closed_at: Optional[datetime] = None,
        status: str = "closed",
    ) -> bool:
        """Update an existing trade with exit data. Looks up by id, then order id."""
        closed_at = closed_at or datetime.now(timezone.utc)
        if trade_id is None and broker_order_id is None:
            logger.warning("TradePersister: record_exit needs trade_id or broker_order_id")
            return False

        try:
            with self._conn() as conn, conn.cursor() as cur:
                matched = 0
                if trade_id is not None:
                    cur.execute(
                        """
                        UPDATE trades
                           SET exit_price = COALESCE(%s, exit_price),
                               profit_loss = COALESCE(%s, profit_loss),
                               closed_at = %s,
                               status = %s
                         WHERE id = %s
                        """,
                        (exit_price, profit_loss, closed_at, status, trade_id),
                    )
                    matched = cur.rowcount
                if matched == 0 and broker_order_id is not None:
                    cur.execute(
                        """
                        UPDATE trades
                           SET exit_price = COALESCE(%s, exit_price),
                               profit_loss = COALESCE(%s, profit_loss),
                               closed_at = %s,
                               status = %s
                         WHERE broker_order_id = %s
                        """,
                        (exit_price, profit_loss, closed_at, status, broker_order_id),
                    )
                    matched = cur.rowcount
                if matched > 0:
                    logger.info(
                        "TradePersister: updated trade exit (id=%s, order=%s)",
                        trade_id, broker_order_id,
                    )
                    return True
                logger.warning(
                    "TradePersister: no matching trade for exit (id=%s, order=%s)",
                    trade_id, broker_order_id,
                )
                return False
        except Exception as exc:
            logger.exception("TradePersister: failed to record exit: %s", exc)
            return False

    def close(self) -> None:
        """Close the connection pool (for clean shutdown)."""
        with self._lock:
            if self._pool is not None:
                try:
                    self._pool.closeall()
                except Exception:
                    pass
                self._pool = None
