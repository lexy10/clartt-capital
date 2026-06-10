"""Prometheus metrics for the Strategy Engine.

Exposes signal generation speed, strategy performance metrics,
and a /metrics HTTP endpoint for Prometheus scraping.
"""

import logging
import time
from contextlib import contextmanager
from threading import Thread
from typing import Generator

from prometheus_client import (
    Counter,
    Histogram,
    Gauge,
    start_http_server,
    generate_latest,
    CONTENT_TYPE_LATEST,
)

logger = logging.getLogger("strategy_engine.metrics")

# --- Signal Generation Metrics ---

signal_generation_duration = Histogram(
    "signal_generation_duration_seconds",
    "Time taken to generate a trading signal",
    labelnames=["instrument", "timeframe"],
    buckets=[0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5],
)

signals_generated_total = Counter(
    "signals_generated_total",
    "Total number of signals generated",
    labelnames=["instrument", "direction", "mode"],
)

# --- Pipeline Metrics ---

candle_aggregation_duration = Histogram(
    "candle_aggregation_duration_seconds",
    "Time taken to aggregate ticks into a candle",
    labelnames=["timeframe"],
    buckets=[0.0001, 0.0005, 0.001, 0.005, 0.01],
)

ticks_processed_total = Counter(
    "ticks_processed_total",
    "Total number of ticks processed by the pipeline",
    labelnames=["instrument"],
)

ticks_rejected_total = Counter(
    "ticks_rejected_total",
    "Total number of invalid ticks rejected",
    labelnames=["instrument", "reason"],
)

# --- Strategy Performance Metrics ---

strategy_cumulative_pnl = Gauge(
    "strategy_cumulative_pnl",
    "Cumulative profit/loss for a strategy",
    labelnames=["strategy_id"],
)

strategy_win_rate = Gauge(
    "strategy_win_rate",
    "Current win rate for a strategy",
    labelnames=["strategy_id"],
)

strategy_max_drawdown = Gauge(
    "strategy_max_drawdown",
    "Current maximum drawdown for a strategy",
    labelnames=["strategy_id"],
)

# --- Live Signal Pipeline Metrics ---

pipeline_cycles_total = Counter(
    "pipeline_cycles_total",
    "Total completed analysis cycles",
    labelnames=["strategy_name", "instrument"],
)

pipeline_signals_generated_total = Counter(
    "pipeline_signals_generated_total",
    "Total signals produced by the pipeline",
    labelnames=["strategy_name", "instrument", "direction"],
)

pipeline_cycle_duration_seconds = Histogram(
    "pipeline_cycle_duration_seconds",
    "Duration of each analysis cycle in seconds",
    labelnames=["strategy_name"],
    buckets=[0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0],
)

pipeline_errors_total = Counter(
    "pipeline_errors_total",
    "Total analysis cycle failures",
    labelnames=["strategy_name", "error_type"],
)

pipeline_active_strategies = Gauge(
    "pipeline_active_strategies",
    "Current count of enabled strategies loaded by the pipeline",
)

# --- Circuit Breaker & Backpressure Metrics ---

circuit_breaker_state_transitions_total = Counter(
    "circuit_breaker_state_transitions_total",
    "Total circuit breaker state transitions",
    labelnames=["name", "to_state"],
)

signal_backpressure_active = Gauge(
    "signal_backpressure_active",
    "Whether signal publishing is paused due to backpressure (1=paused, 0=active)",
)


@contextmanager
def track_signal_generation(
    instrument: str, timeframe: str
) -> Generator[None, None, None]:
    """Context manager to track signal generation duration."""
    start = time.perf_counter()
    try:
        yield
    finally:
        duration = time.perf_counter() - start
        signal_generation_duration.labels(
            instrument=instrument, timeframe=timeframe
        ).observe(duration)


def record_signal(instrument: str, direction: str, mode: str) -> None:
    """Record a generated signal."""
    signals_generated_total.labels(
        instrument=instrument, direction=direction, mode=mode
    ).inc()


def update_strategy_performance(
    strategy_id: str,
    cumulative_pnl: float,
    win_rate: float,
    max_drawdown: float,
) -> None:
    """Update strategy performance gauges."""
    strategy_cumulative_pnl.labels(strategy_id=strategy_id).set(cumulative_pnl)
    strategy_win_rate.labels(strategy_id=strategy_id).set(win_rate)
    strategy_max_drawdown.labels(strategy_id=strategy_id).set(max_drawdown)


def on_circuit_breaker_state_change(name: str, old_state: str, new_state: str) -> None:
    """Callback for circuit breaker state transitions — increments Prometheus counter."""
    circuit_breaker_state_transitions_total.labels(name=name, to_state=new_state).inc()


def set_backpressure_active(paused: bool) -> None:
    """Update the backpressure gauge (1 when paused, 0 when active)."""
    signal_backpressure_active.set(1 if paused else 0)


def start_metrics_server(port: int = 8001) -> None:
    """Start the Prometheus metrics HTTP server in a background thread."""
    logger.info("Starting Prometheus metrics server on port %d", port)
    start_http_server(port)
