"""Prometheus metrics for the Execution Engine.

Exposes trade execution success rates, latency, risk validation metrics,
and a /metrics HTTP endpoint for Prometheus scraping.
"""

import logging
import os
import time
from contextlib import contextmanager
from typing import Generator

from prometheus_client import (
    Counter,
    Histogram,
    Gauge,
    start_http_server,
)

logger = logging.getLogger("execution_engine.metrics")

LATENCY_THRESHOLD_MS = int(os.environ.get("EXECUTION_LATENCY_THRESHOLD_MS", "500"))

# --- Trade Execution Metrics ---

trade_execution_duration = Histogram(
    "trade_execution_duration_seconds",
    "Time taken to execute a trade via broker",
    labelnames=["account_id", "status"],
    buckets=[0.01, 0.025, 0.05, 0.1, 0.2, 0.5, 1.0, 2.5],
)

trades_executed_total = Counter(
    "trades_executed_total",
    "Total number of trades executed",
    labelnames=["account_id", "status"],
)

trades_rejected_total = Counter(
    "trades_rejected_total",
    "Total number of trades rejected by risk validation",
    labelnames=["account_id", "rule"],
)

# --- Risk Validation Metrics ---

risk_validations_total = Counter(
    "risk_validations_total",
    "Total number of risk validations performed",
    labelnames=["account_id", "result"],
)

# --- Latency Alerting ---

latency_threshold_breaches_total = Counter(
    "execution_latency_threshold_breaches_total",
    "Number of times trade execution latency exceeded the configured threshold",
    labelnames=["account_id"],
)

# --- Worker Metrics ---

active_workers = Gauge(
    "active_workers",
    "Number of currently active account workers",
)

signals_consumed_total = Counter(
    "signals_consumed_total",
    "Total number of signals consumed from the message bus",
    labelnames=["account_id"],
)


@contextmanager
def track_trade_execution(account_id: str) -> Generator[dict, None, None]:
    """Context manager to track trade execution duration.

    Yields a dict where the caller should set result['status'] before exiting.
    """
    result: dict = {"status": "unknown"}
    start = time.perf_counter()
    try:
        yield result
    finally:
        duration = time.perf_counter() - start
        duration_ms = duration * 1000
        status = result.get("status", "unknown")
        trade_execution_duration.labels(
            account_id=account_id, status=status
        ).observe(duration)
        trades_executed_total.labels(
            account_id=account_id, status=status
        ).inc()
        if duration_ms > LATENCY_THRESHOLD_MS:
            latency_threshold_breaches_total.labels(account_id=account_id).inc()
            logger.warning(
                "Trade execution latency %.1fms exceeded threshold %dms for account %s",
                duration_ms,
                LATENCY_THRESHOLD_MS,
                account_id,
            )


def record_trade_rejection(account_id: str, rule: str) -> None:
    """Record a trade rejection due to a risk rule violation."""
    trades_rejected_total.labels(account_id=account_id, rule=rule).inc()


def record_risk_validation(account_id: str, passed: bool) -> None:
    """Record a risk validation result."""
    risk_validations_total.labels(
        account_id=account_id, result="passed" if passed else "failed"
    ).inc()


def record_signal_consumed(account_id: str) -> None:
    """Record a signal consumed from the message bus."""
    signals_consumed_total.labels(account_id=account_id).inc()


def start_metrics_server(port: int = 8002) -> None:
    """Start the Prometheus metrics HTTP server in a background thread.

    NOTE: This is the legacy standalone server. When running under FastAPI,
    use ``make_metrics_app()`` instead and mount it on the ASGI app.
    """
    logger.info("Starting Prometheus metrics server on port %d", port)
    start_http_server(port)


def make_metrics_app():
    """Create a WSGI app that serves Prometheus metrics at /metrics.

    Intended to be mounted on a FastAPI/Starlette app via ``app.mount``.
    """
    from prometheus_client import make_asgi_app
    return make_asgi_app()


# --- Circuit Breaker Metrics ---

circuit_breaker_state_transitions_total = Counter(
    "circuit_breaker_state_transitions_total",
    "Total circuit breaker state transitions",
    labelnames=["name", "to_state"],
)


def on_circuit_breaker_state_change(name: str, old_state: str, new_state: str) -> None:
    """Callback for circuit breaker state transitions — increments Prometheus counter."""
    circuit_breaker_state_transitions_total.labels(name=name, to_state=new_state).inc()
