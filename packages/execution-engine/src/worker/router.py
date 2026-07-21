"""FastAPI router for worker management endpoints.

Exposes REST endpoints to start/stop AccountWorker threads via the
WorkerSupervisor. Called by the backend when accounts are deployed
or autopilot state changes.
"""

import asyncio
import logging
from typing import Any, Optional, Union

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from src.accounts.account_provisioner import AccountProvisioner, StubAccountProvisioner
from src.models import TradingAccount
from src.worker.supervisor import WorkerSupervisor

logger = logging.getLogger("execution_engine.worker.router")

router = APIRouter(prefix="/workers", tags=["workers"])

_supervisor: Optional[WorkerSupervisor] = None
_provisioner: Optional[Union[AccountProvisioner, StubAccountProvisioner]] = None
_broker_client: Optional[Any] = None


def configure_supervisor(supervisor: WorkerSupervisor) -> None:
    """Set the supervisor instance used by the router endpoints."""
    global _supervisor
    _supervisor = supervisor


def configure_provisioner(provisioner: Union[AccountProvisioner, StubAccountProvisioner]) -> None:
    """Set the provisioner instance for fetching account details on worker start."""
    global _provisioner
    _provisioner = provisioner


def configure_broker_client(broker_client: Any) -> None:
    """Set the broker client for position management (close-all, etc.)."""
    global _broker_client
    _broker_client = broker_client


def _get_supervisor() -> WorkerSupervisor:
    if _supervisor is None:
        raise HTTPException(status_code=502, detail="WorkerSupervisor not initialized")
    return _supervisor


class StartWorkerRequest(BaseModel):
    account_id: str
    user_id: str
    metaapi_account_id: Optional[str] = None  # Optional for Deriv-direct
    label: str = ""
    broker_provider: Optional[str] = None       # 'metaapi' | 'deriv' | None
    account_kind: str = "personal"
    deriv_api_token: Optional[str] = None
    deriv_login_id: Optional[str] = None


class WorkerStatusResponse(BaseModel):
    account_id: str
    running: bool


@router.post("/start", response_model=WorkerStatusResponse)
async def start_worker(request: StartWorkerRequest) -> WorkerStatusResponse:
    """Start an AccountWorker for the given account.

    Fetches live account details (equity, balance, open positions) from the
    broker via the provisioner so the risk manager has accurate data.
    """
    supervisor = _get_supervisor()

    # Build base account
    equity = 0.0
    balance = 0.0
    open_positions = 0

    # Fetch live account info from broker.
    # For MetaAPI accounts: use the provisioner.
    # For Deriv-direct accounts: query the Deriv WebSocket balance API directly
    # (don't go through the sync wrapper — we're already in an async context).
    if request.broker_provider == "deriv" and request.deriv_api_token:
        try:
            import json, os
            import websockets
            from src.executor.clients.deriv import DERIV_WS_URL
            app_id = os.environ.get("DERIV_APP_ID", "1089")
            url = f"{DERIV_WS_URL}?app_id={app_id}"
            async with websockets.connect(
                url, ping_interval=30, ping_timeout=10, close_timeout=5,
            ) as ws:
                # 1. Authorize
                await ws.send(json.dumps({"authorize": request.deriv_api_token, "req_id": 1}))
                resp = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
                if "error" in resp:
                    raise RuntimeError(f"Deriv authorize error: {resp['error']}")
                auth = resp.get("authorize", {})

                balance = float(auth.get("balance") or 0)
                equity = balance  # no open positions tracked yet
                logger.info(
                    "Fetched Deriv account details for %s: loginid=%s, equity=%.2f, balance=%.2f, virtual=%s",
                    request.account_id, auth.get("loginid"), equity, balance, bool(auth.get("is_virtual")),
                )

                # 2. Open positions count
                try:
                    await ws.send(json.dumps({"portfolio": 1, "req_id": 2}))
                    pfresp = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
                    contracts = pfresp.get("portfolio", {}).get("contracts", []) or []
                    open_positions = len(contracts)
                except Exception:
                    open_positions = 0

                # 3. Cache live balance to Redis so the backend's performance
                #    overview can include this Deriv account in totals
                #    (no portfolio_snapshots row gets written for Deriv).
                #    24h TTL gives the worker plenty of time to refresh —
                #    the periodic balance loop in account_worker re-ups it.
                try:
                    import json as _json
                    from redis import Redis as _Redis
                    _redis = _Redis.from_url(os.environ.get("REDIS_URL", "redis://localhost:6379"))
                    _redis.setex(
                        f"account:liveBalance:{request.account_id}",
                        86400,  # 24h TTL — periodic refresh keeps it warm
                        _json.dumps({"balance": balance, "equity": equity, "open_positions": open_positions}),
                    )
                except Exception:
                    pass
        except Exception:
            logger.warning(
                "Failed to fetch Deriv account details for %s — starting with defaults",
                request.account_id, exc_info=True,
            )
    elif _provisioner is not None and request.metaapi_account_id:
        try:
            details = await _provisioner.get_details(request.metaapi_account_id)
            equity = details.equity
            balance = details.balance
            open_positions = details.open_positions
            logger.info(
                "Fetched account details for %s: equity=%.2f, balance=%.2f, positions=%d",
                request.account_id, equity, balance, open_positions,
            )
        except Exception:
            logger.warning(
                "Failed to fetch account details for %s — starting with defaults",
                request.account_id,
                exc_info=True,
            )

    account = TradingAccount(
        id=request.account_id,
        user_id=request.user_id,
        metaapi_account_id=request.metaapi_account_id,
        label=request.label,
        broker_provider=request.broker_provider,
        account_kind=request.account_kind,
        deriv_api_token=request.deriv_api_token,
        deriv_login_id=request.deriv_login_id,
        equity=equity,
        balance=balance,
        open_positions=open_positions,
    )
    supervisor.start_worker(account)
    return WorkerStatusResponse(
        account_id=request.account_id,
        running=supervisor.is_worker_alive(request.account_id),
    )


class TestSignalRequest(StartWorkerRequest):
    """Same account fields as StartWorkerRequest, plus the test parameters."""
    instrument: str
    direction: str = "BUY"
    place_live: bool = False


@router.post("/test-signal")
async def test_signal(request: TestSignalRequest) -> dict:
    """Diagnostic: run a synthetic entry at the current price through the full
    pipeline for the given account and report each gate. Dry-run by default;
    place_live=True places a REAL minimum-size order. Builds a transient worker
    so it works even when the account has no live (autopilot-on) worker."""
    supervisor = _get_supervisor()
    account = TradingAccount(
        id=request.account_id,
        user_id=request.user_id,
        metaapi_account_id=request.metaapi_account_id,
        label=request.label,
        broker_provider=request.broker_provider,
        account_kind=request.account_kind,
        deriv_api_token=request.deriv_api_token,
        deriv_login_id=request.deriv_login_id,
    )
    worker = supervisor.build_worker(account)
    # Run in a worker thread — NOT this request's event loop. The broker clients
    # connect via loop.run_until_complete(), which raises "event loop is already
    # running" if called inside the async handler. Real AccountWorkers run in
    # their own threads, so this also matches the true execution context.
    try:
        trace = await asyncio.to_thread(
            worker.simulate_signal,
            request.instrument,
            request.direction,
            request.place_live,
        )
    except Exception as exc:
        # A diagnostic must never 500 — return the error as trace context.
        logger.exception("test-signal failed for account %s", request.account_id)
        return {
            "account_id": request.account_id,
            "steps": [{"step": "test-signal", "ok": False, "detail": f"{type(exc).__name__}: {exc}"}],
            "wouldTrade": False,
            "placed": False,
        }
    return {"account_id": request.account_id, **trace}


@router.post("/{account_id}/stop", response_model=WorkerStatusResponse)
async def stop_worker(account_id: str) -> WorkerStatusResponse:
    """Stop the AccountWorker for the given account."""
    supervisor = _get_supervisor()
    supervisor.stop_worker(account_id)
    return WorkerStatusResponse(account_id=account_id, running=False)


@router.get("/status")
async def list_workers() -> dict:
    """List all managed workers and their status."""
    supervisor = _get_supervisor()
    active = supervisor.active_workers
    all_ids = supervisor.all_workers
    return {
        "workers": [
            {"account_id": aid, "running": aid in active}
            for aid in all_ids
        ],
        "total": len(all_ids),
        "active": len(active),
    }


class CloseAllRequest(BaseModel):
    metaapi_account_id: str


class CloseAllResponse(BaseModel):
    closed: int
    failed: int
    positions_found: int


@router.post("/close-all-positions", response_model=CloseAllResponse)
async def close_all_positions(request: CloseAllRequest) -> CloseAllResponse:
    """Close all open positions on a MetaAPI account.

    Called by the backend when the kill switch is activated (hard mode).
    Connects to the broker, fetches all open positions, and closes each one.
    Uses async methods directly since we're inside FastAPI's event loop.
    """
    if _broker_client is None:
        raise HTTPException(status_code=502, detail="Broker client not initialized")

    # Connect to the account
    try:
        if hasattr(_broker_client, '_async_connect'):
            await _broker_client._async_connect(request.metaapi_account_id)
        else:
            _broker_client.connect(request.metaapi_account_id)
    except Exception as e:
        logger.error("Failed to connect for close-all: %s", e)
        raise HTTPException(status_code=502, detail=f"Broker connection failed: {e}")

    # Fetch open positions
    if hasattr(_broker_client, '_async_get_positions'):
        positions = await _broker_client._async_get_positions()
    else:
        positions = _broker_client.get_positions()

    if not positions:
        logger.info("No open positions to close for account %s", request.metaapi_account_id)
        return CloseAllResponse(closed=0, failed=0, positions_found=0)

    logger.info(
        "Kill switch: closing %d position(s) for account %s",
        len(positions),
        request.metaapi_account_id,
    )

    async def _close_one(pos: dict) -> bool:
        position_id = pos.get("id") or pos.get("positionId") or pos.get("ticket")
        if position_id is None:
            logger.warning("Position missing ID, skipping: %s", pos)
            return False
        try:
            if hasattr(_broker_client, '_async_close_position'):
                result = await _broker_client._async_close_position(int(position_id))
            else:
                result = _broker_client.close_position_by_id(int(position_id))
            if result.success:
                logger.info("Closed position %s (symbol=%s)", position_id, pos.get("symbol", "?"))
                return True
            else:
                logger.warning("Failed to close position %s: %s", position_id, result.error_message)
                return False
        except Exception as e:
            logger.error("Error closing position %s: %s", position_id, e)
            return False

    results = await asyncio.gather(*[_close_one(pos) for pos in positions])
    closed = sum(1 for r in results if r)
    failed = len(positions) - closed

    logger.info(
        "Kill switch close-all complete: %d closed, %d failed out of %d",
        closed, failed, len(positions),
    )
    return CloseAllResponse(closed=closed, failed=failed, positions_found=len(positions))
