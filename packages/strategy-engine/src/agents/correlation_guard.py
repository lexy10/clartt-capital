"""Correlation Guard — multi-instrument position sizing guard.

Prevents over-concentration by detecting correlated signals across
instruments and reducing or blocking overlapping exposure.

Requirements: 22.1–22.7
"""

import json
import logging
import os
from typing import Any, Optional, Protocol

import requests
from prometheus_client import Counter
from redis import Redis

logger = logging.getLogger("strategy_engine.agents.correlation_guard")

CORRELATION_MATRIX_KEY = "correlation:matrix"

# Prometheus metrics (Req 22.9)
correlation_guard_reductions_total = Counter(
    "correlation_guard_reductions_total",
    "Total signals reduced by correlation guard",
    labelnames=["instrument_pair"],
)
correlation_guard_blocks_total = Counter(
    "correlation_guard_blocks_total",
    "Total signals blocked by correlation guard",
    labelnames=["instrument_pair"],
)


class EventPublisherProtocol(Protocol):
    def publish(self, event: Any) -> None: ...


class CorrelationGuard:
    """Pre-signal filter that evaluates correlated exposure.

    Constructor:
        redis_client:       Redis connection for correlation matrix and state
        event_publisher:    EventPublisher for CorrelationGuardTriggered events
        execution_engine_url: Base URL of the execution engine REST API
    """

    def __init__(
        self,
        redis_client: Redis,
        event_publisher: Optional[EventPublisherProtocol] = None,
        execution_engine_url: str = "http://execution-engine:8002",
    ) -> None:
        self._redis = redis_client
        self._event_publisher = event_publisher
        self._execution_engine_url = execution_engine_url.rstrip("/")

        # Configuration from env vars (Req 22.8)
        self._correlation_threshold = float(
            os.environ.get("CORRELATION_THRESHOLD", "0.7")
        )
        self._reduction_pct = float(
            os.environ.get("CORRELATION_REDUCTION_PCT", "50")
        ) / 100.0
        self._max_correlated_exposure = float(
            os.environ.get("MAX_CORRELATED_EXPOSURE", "3.0")
        )

    def evaluate(self, signal: Any, strategy_config: Any = None) -> dict:
        """Evaluate a signal against correlated exposure.

        Returns a dict with:
          - ``action``: "pass", "reduce", or "block"
          - ``adjusted_position_size``: the (possibly reduced) position size
          - ``reason``: human-readable explanation

        Args:
            signal: Signal object with instrument, direction, position_size attributes
            strategy_config: Optional StrategyConfig; if correlation_guard_enabled
                             is False, the signal passes through unchanged.
        """
        # Check per-strategy opt-out (Req 22.6)
        if strategy_config is not None:
            params = getattr(strategy_config, "algorithm_params", None) or {}
            if isinstance(params, dict) and not params.get("correlation_guard_enabled", True):
                return {
                    "action": "pass",
                    "adjusted_position_size": signal.position_size,
                    "reason": "correlation guard disabled for strategy",
                }

        instrument = signal.instrument
        direction = signal.direction
        direction_str = direction.value if hasattr(direction, "value") else str(direction)
        position_size = signal.position_size

        # Load correlation matrix (Req 22.2)
        matrix = self._load_correlation_matrix()
        if not matrix:
            return {
                "action": "pass",
                "adjusted_position_size": position_size,
                "reason": "no correlation matrix configured",
            }

        # Get open positions (Req 22.5)
        open_positions = self._get_open_positions()

        # Find correlated instruments with same-direction exposure
        correlated_exposure = 0.0
        correlated_pairs: list[tuple[str, float]] = []

        for pos in open_positions:
            pos_instrument = pos.get("instrument", "")
            pos_direction = pos.get("direction", "")
            pos_size = float(pos.get("position_size", 0))

            if pos_instrument == instrument:
                continue

            # Check correlation in both key orderings
            corr = self._get_correlation(matrix, instrument, pos_instrument)
            if corr is None or corr < self._correlation_threshold:
                continue

            # Only same-direction positions count (Req 22.3)
            if pos_direction.lower() != direction_str.lower():
                continue

            correlated_exposure += pos_size
            correlated_pairs.append((pos_instrument, corr))

        if not correlated_pairs:
            return {
                "action": "pass",
                "adjusted_position_size": position_size,
                "reason": "no correlated exposure detected",
            }

        # Check if total correlated exposure would exceed max (Req 22.4)
        total_after = correlated_exposure + position_size
        if total_after > self._max_correlated_exposure:
            pair_str = f"{instrument}:{correlated_pairs[0][0]}"
            correlation_guard_blocks_total.labels(instrument_pair=pair_str).inc()
            self._publish_guard_event(
                instrument=instrument,
                pairs=correlated_pairs,
                action="block",
                original_size=position_size,
                adjusted_size=0.0,
            )
            return {
                "action": "block",
                "adjusted_position_size": 0.0,
                "reason": (
                    f"Correlated exposure {total_after:.2f} lots would exceed "
                    f"max {self._max_correlated_exposure:.2f} lots"
                ),
            }

        # Reduce position size (Req 22.3)
        adjusted_size = round(position_size * (1.0 - self._reduction_pct), 4)
        pair_str = f"{instrument}:{correlated_pairs[0][0]}"
        correlation_guard_reductions_total.labels(instrument_pair=pair_str).inc()
        self._publish_guard_event(
            instrument=instrument,
            pairs=correlated_pairs,
            action="reduce",
            original_size=position_size,
            adjusted_size=adjusted_size,
        )
        return {
            "action": "reduce",
            "adjusted_position_size": adjusted_size,
            "reason": (
                f"Correlated with {[p[0] for p in correlated_pairs]}, "
                f"reduced from {position_size} to {adjusted_size}"
            ),
        }

    # ── Private Helpers ───────────────────────────────────────────────

    def _load_correlation_matrix(self) -> dict:
        """Load correlation matrix from Redis."""
        raw = self._redis.get(CORRELATION_MATRIX_KEY)
        if raw is None:
            return {}
        try:
            decoded = raw if isinstance(raw, str) else raw.decode("utf-8")
            return json.loads(decoded)
        except (json.JSONDecodeError, ValueError):
            logger.warning("Failed to parse correlation matrix from Redis")
            return {}

    def _get_correlation(
        self, matrix: dict, inst_a: str, inst_b: str
    ) -> Optional[float]:
        """Look up correlation coefficient for a pair (order-independent)."""
        key1 = f"{inst_a}:{inst_b}"
        key2 = f"{inst_b}:{inst_a}"
        val = matrix.get(key1) or matrix.get(key2)
        if val is not None:
            return float(val)
        return None

    def _get_open_positions(self) -> list[dict]:
        """Query open positions from the execution engine REST API."""
        try:
            resp = requests.get(
                f"{self._execution_engine_url}/api/positions",
                timeout=5,
            )
            resp.raise_for_status()
            data = resp.json()
            return data if isinstance(data, list) else data.get("positions", [])
        except Exception as exc:
            logger.warning("Failed to fetch open positions: %s", exc)
            return []

    def _publish_guard_event(
        self,
        instrument: str,
        pairs: list[tuple[str, float]],
        action: str,
        original_size: float,
        adjusted_size: float,
    ) -> None:
        """Publish CorrelationGuardTriggered event (Req 22.7)."""
        if self._event_publisher is None:
            return
        from src.models.trading_event import TradingEvent

        event = TradingEvent(
            event_type="Agent:CorrelationGuardTriggered",
            aggregate_id=instrument,
            sequence_number=0,
            payload={
                "instrument": instrument,
                "correlated_pairs": [
                    {"instrument": p[0], "correlation": p[1]} for p in pairs
                ],
                "action": action,
                "original_position_size": original_size,
                "adjusted_position_size": adjusted_size,
            },
            source_service="strategy-engine",
        )
        try:
            self._event_publisher.publish(event)
        except Exception as exc:
            logger.warning("Failed to publish CorrelationGuardTriggered: %s", exc)
