"""Strategy Engine entry point.

Runs the live signal pipeline, backtest consumer, and a FastAPI server
for algorithm management (CRUD + hot-reload).
"""

import logging
import os
import signal
import threading

import uvicorn
from fastapi import FastAPI
from redis import Redis

from src.api.algorithm_manager import AlgorithmManager
from src.agents.api.router import agents_router
from src.api.router import router as algorithms_router, set_manager
from src.api.health_router import health_router, set_circuit_breakers
from src.autopilot.autopilot_monitor import AutopilotMonitor
from src.backtesting.backtest_consumer import BacktestConsumer
from src.backtesting.backtest_engine import BacktestEngine
from src.logging_config import configure_logging
from src.metrics import start_metrics_server
from src.pipeline.signal_persister import SignalPersister
from src.pipeline.strategy_config_loader import StrategyConfigLoader
from src.pipeline.strategy_runner import StrategyRunner
from src.signals.signal_publisher import SignalPublisher
from src.strategy.algorithms.ict_order_block import ICTOrderBlockAlgorithm
from src.strategy.registry import StrategyRegistry

configure_logging()

logger = logging.getLogger("strategy_engine")


def create_app(registry: StrategyRegistry, algorithm_manager: AlgorithmManager) -> FastAPI:
    """Create the FastAPI application for algorithm management."""
    app = FastAPI(title="Strategy Engine", docs_url="/docs")
    set_manager(algorithm_manager)
    app.include_router(algorithms_router)
    app.include_router(health_router)
    app.include_router(agents_router)
    return app


def main() -> None:
    logger.info("Strategy Engine starting...")
    start_metrics_server(port=8001)

    # Create Redis client
    redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379")
    redis_client = Redis.from_url(redis_url, decode_responses=False)
    logger.info("Connected to Redis at %s", redis_url)

    backend_url = os.environ.get("BACKEND_URL", "http://backend:3000")

    # Instantiate AutopilotMonitor
    autopilot_monitor = AutopilotMonitor(redis_client=redis_client)

    # Subscribe to autopilot state changes with a logging callback
    def on_autopilot_state_change(account_id: str, enabled: bool) -> None:
        state_label = "enabled" if enabled else "disabled"
        logger.info("Autopilot %s for account %s", state_label, account_id)

    autopilot_monitor.subscribe(on_autopilot_state_change)
    logger.info("AutopilotMonitor subscribed to state changes.")

    # Create SignalPublisher with autopilot monitor injected
    signal_publisher = SignalPublisher(
        redis_client=redis_client,
        autopilot_monitor=autopilot_monitor,
    )
    logger.info("SignalPublisher initialized with AutopilotMonitor.")

    # Create strategy algorithm registry and register built-in algorithms
    registry = StrategyRegistry()
    registry.register(ICTOrderBlockAlgorithm())
    logger.info("Registered %d built-in algorithm(s)", len(registry.list_algorithms()))

    # Create algorithm manager for dynamic loading + file watching
    algorithm_manager = AlgorithmManager(registry)
    dynamic_loaded = algorithm_manager.scan_and_load()
    if dynamic_loaded:
        logger.info("Dynamically loaded %d algorithm(s): %s", len(dynamic_loaded), dynamic_loaded)
    algorithm_manager.start_watcher()

    # Instantiate BacktestEngine and BacktestConsumer
    backtest_engine = BacktestEngine(registry=registry)
    backtest_consumer = BacktestConsumer(
        redis_client=redis_client,
        backtest_engine=backtest_engine,
        backend_url=backend_url,
    )
    backtest_consumer.start()
    logger.info("BacktestConsumer started.")

    # Create pipeline components for live signal generation
    config_loader = StrategyConfigLoader(backend_url=backend_url)
    signal_persister = SignalPersister(backend_url=backend_url)
    strategy_runner = StrategyRunner(
        redis_client=redis_client,
        config_loader=config_loader,
        registry=registry,
        signal_publisher=signal_publisher,
        signal_persister=signal_persister,
        backend_url=backend_url,
    )
    strategy_runner.start()
    logger.info("StrategyRunner started — live signal pipeline active.")

    # Wire circuit breakers into health endpoint
    set_circuit_breakers(
        config_cb=config_loader.circuit_breaker,
        signals_cb=signal_persister.circuit_breaker,
    )

    # Create FastAPI app for algorithm management API
    api_port = int(os.environ.get("API_PORT", "8003"))
    app = create_app(registry, algorithm_manager)

    # Run FastAPI in a background thread
    api_server = uvicorn.Server(
        uvicorn.Config(app, host="0.0.0.0", port=api_port, log_level="warning")
    )
    api_thread = threading.Thread(target=api_server.run, daemon=True, name="api-server")
    api_thread.start()
    logger.info("Algorithm management API started on port %d", api_port)

    logger.info("Strategy Engine ready.")

    # Keep the process alive until terminated
    stop_event = threading.Event()

    def handle_signal(signum, frame):
        logger.info("Received signal %s, shutting down...", signum)
        stop_event.set()

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    stop_event.wait()

    # Shutdown
    algorithm_manager.stop_watcher()
    logger.info("Algorithm watcher stopped.")
    api_server.should_exit = True
    logger.info("API server stopping.")
    strategy_runner.stop()
    logger.info("StrategyRunner stopped.")
    signal_persister.shutdown()
    logger.info("SignalPersister shutdown.")
    backtest_consumer.stop()
    logger.info("BacktestConsumer stopped.")
    autopilot_monitor.stop()
    logger.info("AutopilotMonitor stopped.")
    redis_client.close()
    logger.info("Strategy Engine stopped.")


if __name__ == "__main__":
    main()
