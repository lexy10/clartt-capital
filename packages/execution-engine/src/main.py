"""Execution Engine entry point."""

import logging
import os
import signal
import threading

from redis import Redis

from src.autopilot.autopilot_monitor import AutopilotMonitor
from src.circuit_breaker import CircuitBreaker
from src.consumer.signal_consumer import SignalConsumer
from src.executor.trade_executor import TradeExecutor
from src.health_router import health_router, set_metaapi_circuit_breaker
from src.kill_switch.kill_switch_monitor import KillSwitchMonitor
from src.lifecycle.trade_lifecycle_manager import TradeLifecycleManager
from src.logging_config import configure_logging
from src.metrics import make_metrics_app, on_circuit_breaker_state_change
from src.models import TradingAccount
from src.monitor.position_monitor import PositionMonitor
from src.risk.risk_manager import RiskManager
from src.worker.account_worker import AccountWorker
from src.worker.supervisor import WorkerSupervisor

configure_logging()

logger = logging.getLogger("execution_engine")


def _create_broker_router():
    """Build a BrokerRouter and register one client per available provider.

    This replaces the single hard-coded broker client. Each instrument category
    can now route to a different provider:
    - Synthetic (R_*, BOOM_*, CRASH_*) -> Deriv direct API (if DERIV_API_TOKEN set)
    - Forex / commodities / indices -> MetaAPI (if METAAPI_TOKEN set)
    - Stocks / crypto / futures -> placeholder stubs

    If no real providers are configured, falls back to a single Stub client
    so the platform still runs in demo mode.
    """
    from src.executor.clients import (
        AlpacaStockClient,
        BinanceCryptoClient,
        BrokerProvider,
        BrokerRouter,
        DerivSyntheticClient,
        IBKRFutureClient,
        MetaApiForexClient,
    )

    router = BrokerRouter()

    # Deriv direct (synthetics: R_*, BOOM_*, CRASH_*).
    # Deriv authenticates PER ACCOUNT at execution time via connect_with_token()
    # using each account's own stored token, so the client is registered
    # UNCONDITIONALLY. The app_id is public (defaults to 1089) and is all that's
    # needed to open the socket; a global DERIV_API_TOKEN is optional.
    #
    # Previously registration was gated on a global DERIV_API_TOKEN. Accounts
    # connected per-account through the UI (token in the DB, no global env) then
    # had NO Deriv client on the router, so their synthetic trades couldn't
    # resolve a broker and silently failed to route — signals fired but no
    # order was ever placed.
    deriv_app_id = os.environ.get("DERIV_APP_ID", "") or "1089"
    router.register(
        BrokerProvider.DERIV,
        DerivSyntheticClient(
            app_id=deriv_app_id,
            api_token=os.environ.get("DERIV_API_TOKEN", ""),
        ),
    )

    # MetaAPI (forex/commodities/indices via MT5)
    metaapi_token = os.environ.get("METAAPI_TOKEN", "")
    if metaapi_token:
        router.register(
            BrokerProvider.METAAPI,
            MetaApiForexClient(api_token=metaapi_token),
        )

    # Stubs — visible for diagnostics / providers not yet run live
    router.register(BrokerProvider.ALPACA, AlpacaStockClient())
    router.register(BrokerProvider.BINANCE, BinanceCryptoClient())
    router.register(BrokerProvider.IBKR, IBKRFutureClient())

    # Back MetaAPI with a stub when it has no creds, so forex/index trades don't
    # crash in demo setups. Synthetics always use the real Deriv client above.
    if not metaapi_token:
        from src.executor.stub_broker_client import StubBrokerClient
        router.register(BrokerProvider.METAAPI, StubBrokerClient())
        logger.warning(
            "No METAAPI_TOKEN configured — MetaAPI (forex/index) trades will use "
            "StubBrokerClient (demo). Synthetics use the real Deriv client."
        )
    else:
        logger.info(
            "BrokerRouter built with providers: %s",
            [p.value for p in router.list_registered()],
        )

    return router


def _create_broker_client():
    """Backward-compatible single client.

    Returns the highest-priority real client (MetaAPI > Deriv > Stub) for
    legacy code paths that don't yet use the router. New code should use
    _create_broker_router() instead.
    """
    metaapi_token = os.environ.get("METAAPI_TOKEN", "")
    deriv_token = os.environ.get("DERIV_API_TOKEN", "")
    deriv_app_id = os.environ.get("DERIV_APP_ID", "")

    if metaapi_token:
        from src.executor.clients.metaapi import MetaApiForexClient
        logger.info("Using MetaAPI as default broker client (legacy entry point)")
        return MetaApiForexClient(api_token=metaapi_token)
    elif deriv_token and deriv_app_id:
        from src.executor.clients.deriv import DerivSyntheticClient
        logger.info("Using Deriv as default broker client (legacy entry point)")
        return DerivSyntheticClient(app_id=deriv_app_id, api_token=deriv_token)
    else:
        from src.executor.stub_broker_client import StubBrokerClient
        logger.info("No broker tokens set — using stub broker client (demo mode)")
        return StubBrokerClient()


def _create_account_provisioner():
    """Create the appropriate account provisioner based on environment config."""
    metaapi_token = os.environ.get("METAAPI_TOKEN", "")

    if metaapi_token:
        from src.accounts import AccountProvisioner
        logger.info("Using MetaApi account provisioner")
        return AccountProvisioner(api_token=metaapi_token)
    else:
        from src.accounts import StubAccountProvisioner
        logger.info("No METAAPI_TOKEN set — using stub account provisioner (demo mode)")
        return StubAccountProvisioner()


def _create_deriv_client():
    """Create a Deriv data client if DERIV_APP_ID is configured."""
    deriv_app_id = os.environ.get("DERIV_APP_ID", "")
    deriv_api_token = os.environ.get("DERIV_API_TOKEN", "")

    if deriv_app_id:
        from src.candles.deriv_data_client import DerivDataClient
        logger.info("Using Deriv WebSocket API for market data (app_id=%s)", deriv_app_id)
        return DerivDataClient(app_id=deriv_app_id, api_token=deriv_api_token)
    return None


def _create_candle_service(deriv_client, provisioner):
    """Create the appropriate candle service based on environment config.

    Priority: Deriv API > MetaAPI > Stub (demo mode)
    """
    if deriv_client:
        from src.candles.candle_service import CandleService
        logger.info("Using Deriv candle service for historical data")
        return CandleService(deriv_client=deriv_client)

    metaapi_token = os.environ.get("METAAPI_TOKEN", "")
    if metaapi_token:
        # Fallback: MetaAPI for historical candles (legacy)
        from src.candles.candle_service_metaapi import CandleServiceMetaApi
        logger.info("Using MetaApi candle service (Deriv not configured)")
        return CandleServiceMetaApi(provisioner=provisioner)

    from src.candles.candle_service import StubCandleService
    logger.info("No DERIV_APP_ID or METAAPI_TOKEN — using stub candle service (demo mode)")
    return StubCandleService()


def _create_candle_streamer(deriv_client, provisioner):
    """Create the appropriate candle streamer based on environment config.

    Priority: Deriv API > MetaAPI > Stub (demo mode)
    """
    redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379")

    if deriv_client:
        from src.candles.candle_streamer import CandleStreamer
        logger.info("Using Deriv candle streamer for live ticks")
        return CandleStreamer(deriv_client=deriv_client, redis_url=redis_url)

    metaapi_token = os.environ.get("METAAPI_TOKEN", "")
    if metaapi_token:
        from src.candles.candle_streamer_metaapi import CandleStreamerMetaApi
        logger.info("Using MetaApi candle streamer (Deriv not configured)")
        return CandleStreamerMetaApi(provisioner=provisioner, redis_url=redis_url)

    from src.candles.candle_streamer import StubCandleStreamer
    logger.info("No DERIV_APP_ID or METAAPI_TOKEN — using stub candle streamer (demo mode)")
    return StubCandleStreamer(redis_url=redis_url)


def _start_fastapi_server(
    port: int = 8002,
    supervisor: WorkerSupervisor | None = None,
    broker_client=None,
    trade_persister=None,
    broker_router=None,
) -> None:
    """Start the FastAPI server with account routes and Prometheus metrics."""
    import uvicorn
    from fastapi import FastAPI
    from src.accounts import accounts_router, configure_provisioner
    from src.candles import candles_router
    from src.candles.router import configure_candle_service, configure_candle_streamer
    from src.worker.router import (
        router as workers_router,
        configure_supervisor,
        configure_provisioner as configure_worker_provisioner,
        configure_broker_client,
    )

    app = FastAPI(title="Execution Engine", version="1.0.0")

    # Mount Prometheus metrics at /metrics
    metrics_app = make_metrics_app()
    app.mount("/metrics", metrics_app)

    # Configure and include account provisioning routes
    provisioner = _create_account_provisioner()
    configure_provisioner(provisioner)
    app.include_router(accounts_router)

    # Create Deriv client for market data (if configured)
    deriv_client = _create_deriv_client()

    # Configure and include candle data routes
    candle_service = _create_candle_service(deriv_client, provisioner)
    candle_streamer = _create_candle_streamer(deriv_client, provisioner)
    configure_candle_service(candle_service)
    configure_candle_streamer(candle_streamer)
    app.include_router(candles_router)

    # Include health endpoint
    app.include_router(health_router)

    # Reconciliation routes — backstop for missed broker close events
    if trade_persister is not None and broker_router is not None:
        try:
            from src.reconciliation.router import (
                router as reconciliation_router,
                configure as configure_reconciliation,
            )
            configure_reconciliation(
                trade_persister=trade_persister,
                broker_router=broker_router,
            )
            app.include_router(reconciliation_router)
            logger.info("Reconciliation router enabled at /reconciliation")
        except Exception as exc:
            logger.warning("Reconciliation router not loaded: %s", exc)

    # Configure worker management routes
    if supervisor:
        configure_supervisor(supervisor)
        configure_worker_provisioner(provisioner)
        if broker_client:
            configure_broker_client(broker_client)
        app.include_router(workers_router)

    # Connect Deriv client on startup
    if deriv_client:
        @app.on_event("startup")
        async def connect_deriv():
            try:
                await deriv_client.connect()
                logger.info("Deriv WebSocket client connected on startup")
            except Exception as exc:
                logger.error("Failed to connect Deriv client on startup: %s", exc)

    logger.info("Starting FastAPI server on port %d", port)

    server_thread = threading.Thread(
        target=uvicorn.run,
        args=(app,),
        kwargs={"host": "0.0.0.0", "port": port, "log_level": "info"},
        daemon=True,
    )
    server_thread.start()


def main() -> None:
    logger.info("Execution Engine starting...")

    # Create Redis client
    redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379")
    redis_client = Redis.from_url(redis_url, decode_responses=False)
    logger.info("Connected to Redis at %s", redis_url)

    # Instantiate KillSwitchMonitor
    kill_switch_monitor = KillSwitchMonitor(redis_client=redis_client)

    def on_kill_switch_change(active: bool) -> None:
        state_label = "ACTIVE" if active else "INACTIVE"
        logger.info("Kill switch %s", state_label)

    kill_switch_monitor.subscribe(on_kill_switch_change)
    logger.info("KillSwitchMonitor subscribed to state changes.")

    # Instantiate AutopilotMonitor
    autopilot_monitor = AutopilotMonitor(redis_client=redis_client)

    def on_autopilot_state_change(account_id: str, enabled: bool) -> None:
        state_label = "enabled" if enabled else "disabled"
        logger.info("Autopilot %s for account %s", state_label, account_id)

    autopilot_monitor.subscribe(on_autopilot_state_change)
    logger.info("AutopilotMonitor subscribed to state changes.")

    # Create shared components
    from src.executor.instrument_registry import InstrumentRegistry

    broker_router = _create_broker_router()
    instrument_registry = InstrumentRegistry()
    # Best-effort initial refresh — if the backend isn't up yet, the registry
    # auto-detects categories from symbol patterns so trading still routes correctly.
    try:
        instrument_registry.refresh()
    except Exception as exc:
        logger.warning("InstrumentRegistry initial refresh failed (will retry on demand): %s", exc)

    metaapi_cb = CircuitBreaker(
        name="execution-to-metaapi",
        on_state_change=on_circuit_breaker_state_change,
    )

    # Pick a default legacy client for backwards compatibility with components
    # (PositionMonitor, etc.) that still take a single broker_client.
    broker_client = _create_broker_client()

    trade_executor = TradeExecutor(
        broker_client=broker_client,        # legacy fallback
        broker_router=broker_router,        # category routing
        instrument_registry=instrument_registry,
        circuit_breaker=metaapi_cb,
    )
    # Expose CB to health endpoint
    set_metaapi_circuit_breaker(metaapi_cb)
    trade_lifecycle_manager = TradeLifecycleManager(
        executor=trade_executor,
        redis_client=redis_client,
    )
    signal_consumer = SignalConsumer(redis_client=redis_client)
    risk_manager = RiskManager()

    logger.info("TradeLifecycleManager initialized.")

    # Direct DB persister — bypasses the pub/sub round-trip so trade rows
    # are written the moment they fill (resilient to backend issues).
    from src.persistence.trade_persister import TradePersister
    trade_persister = TradePersister()
    try:
        trade_persister._ensure_pool()
        logger.info("TradePersister: DB pool ready.")
    except Exception as exc:
        logger.warning("TradePersister: failed to initialize pool, persistence disabled: %s", exc)
        trade_persister = None

    # Position monitor for active exit rule processing.
    # Gets the trade_persister so it can write exit rows when a position
    # closes (broker SL/TP, time-exit, etc.). Gets the broker_router so it
    # can route per-account multi-broker close lookups.
    backend_url = os.environ.get("BACKEND_URL", "http://backend:3000")
    position_monitor = PositionMonitor(
        broker_client=broker_client,
        executor=trade_executor,
        redis_client=redis_client,
        backend_url=backend_url,
        trade_persister=trade_persister,
        broker_router=broker_router,
    )
    position_monitor.start()
    logger.info("PositionMonitor initialized and started.")

    # Worker factory: creates an AccountWorker with all dependencies injected
    def worker_factory(account: TradingAccount) -> AccountWorker:
        return AccountWorker(
            account=account,
            risk_manager=risk_manager,
            executor=trade_executor,
            signal_consumer=signal_consumer,
            kill_switch=kill_switch_monitor,
            redis_client=redis_client,
            autopilot_monitor=autopilot_monitor,
            position_monitor=position_monitor,
            trade_persister=trade_persister,
        )

    supervisor = WorkerSupervisor(worker_factory=worker_factory)
    logger.info("WorkerSupervisor initialized.")

    # Start FastAPI server with supervisor wired in
    _start_fastapi_server(
        port=8002,
        supervisor=supervisor,
        broker_client=broker_client,
        trade_persister=trade_persister,
        broker_router=broker_router,
    )

    logger.info("Execution Engine ready.")

    # Keep the process alive until terminated
    stop_event = threading.Event()

    def handle_signal(signum, frame):
        logger.info("Received signal %s, shutting down...", signum)
        stop_event.set()

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    stop_event.wait()

    # Graceful shutdown
    supervisor.stop_all()
    logger.info("WorkerSupervisor stopped.")
    position_monitor.stop()
    logger.info("PositionMonitor stopped.")
    autopilot_monitor.stop()
    logger.info("AutopilotMonitor stopped.")
    kill_switch_monitor.stop()
    logger.info("KillSwitchMonitor stopped.")
    redis_client.close()
    logger.info("Execution Engine stopped.")


if __name__ == "__main__":
    main()
