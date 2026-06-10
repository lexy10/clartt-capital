"""Signal publisher with mode-dependent routing.

Routes signals based on their mode:
- live: publish to Redis stream `signals:stream` (only when autopilot is enabled)
- forward_test: record signal internally, do NOT publish to execution stream
- backtest: record signal in backtest results only (in-memory list)

When autopilot is disabled for a live signal, the signal is recorded for
analytics but not published to the execution stream. Overlay data from
signal metadata is published to the `strategy:overlays` pub/sub channel
whenever a live signal is published to the stream.
"""

import json
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

from redis import Redis

from ..models.signal import Signal, SignalMode
from ..models.trading_event import (
    SignalPublishedPayload,
    TradingEvent,
    TradingEventType,
)

if TYPE_CHECKING:
    from ..autopilot.autopilot_monitor import AutopilotMonitor
    from ..backpressure.backpressure_monitor import BackpressureMonitor
    from ..events.event_publisher import EventPublisher

logger = logging.getLogger(__name__)

SIGNAL_STREAM_KEY = "signals:stream"
STRATEGY_OVERLAYS_CHANNEL = "strategy:overlays"


class SignalPublisher:
    """Publishes signals with mode-dependent routing.

    - In live mode, signals are published to the Redis stream for execution
      only when master autopilot is enabled. When disabled, signals are
      recorded for analytics.
    - In forward_test mode, signals are recorded but not published to the execution stream.
    - In backtest mode, signals are recorded in an in-memory list only.
    """

    def __init__(
        self,
        redis_client: Optional[Redis] = None,
        autopilot_monitor: Optional["AutopilotMonitor"] = None,
        backpressure_monitor: Optional["BackpressureMonitor"] = None,
        event_publisher: Optional["EventPublisher"] = None,
    ) -> None:
        self._redis = redis_client
        self._autopilot_monitor = autopilot_monitor
        self._backpressure_monitor = backpressure_monitor
        self._event_publisher = event_publisher
        self._forward_test_signals: list[Signal] = []
        self._backtest_signals: list[Signal] = []
        self._analytics_signals: list[Signal] = []

    def publish(self, signal: Signal, account_id: Optional[str] = None) -> None:
        """Publish a signal with routing based on signal.mode.

        For live mode, an account_id is required to check autopilot state.
        """
        if signal.mode == SignalMode.LIVE:
            self._publish_live(signal, account_id=account_id)
        elif signal.mode == SignalMode.FORWARD_TEST:
            self._record_forward_test(signal)
        elif signal.mode == SignalMode.BACKTEST:
            self._record_backtest(signal)
        else:
            raise ValueError(f"Unknown signal mode: {signal.mode}")

    def _publish_live(self, signal: Signal, account_id: Optional[str] = None) -> None:
        """Publish signal to Redis stream for execution engine consumption.

        When an autopilot_monitor and account_id are provided, checks per-account
        autopilot state before publishing. When autopilot is disabled, the signal
        is recorded for analytics but not published to the execution stream.
        Without a monitor (or without an account_id), signals publish directly.
        """
        if self._redis is None:
            raise RuntimeError("Redis client is required for live mode publishing")

        # Check per-account autopilot gate when monitor and account_id are both present
        if self._autopilot_monitor is not None and account_id is not None:
            if not self._autopilot_monitor.is_enabled(account_id):
                self._analytics_signals.append(signal)
                logger.info(
                    "Autopilot disabled for account %s — signal %s recorded for analytics only",
                    account_id,
                    signal.id,
                )
                return

        payload = signal.model_dump_json()

        # Check backpressure before live signal XADD
        if self._backpressure_monitor is not None:
            if not self._backpressure_monitor.should_publish():
                lag = self._backpressure_monitor.get_consumer_lag()
                logger.warning(
                    "Backpressure active — skipping live publish for signal %s (lag=%d)",
                    signal.id,
                    lag,
                )
                return

        self._redis.xadd(SIGNAL_STREAM_KEY, {"data": payload})
        logger.info("Published live signal %s to stream %s", signal.id, SIGNAL_STREAM_KEY)

        # Publish SignalPublished event (fire-and-forget)
        self._publish_signal_published_event(signal)

        # Publish overlay data to strategy:overlays pub/sub channel
        self._publish_overlay(signal, account_id)

    def _publish_signal_published_event(self, signal: Signal) -> None:
        """Build and publish a SignalPublished trading event (fire-and-forget)."""
        if self._event_publisher is None:
            return
        try:
            payload = SignalPublishedPayload(
                signal_id=signal.id,
                instrument=signal.instrument,
                direction=signal.direction.value,
                publish_timestamp=datetime.now(timezone.utc).isoformat(),
            )
            event = TradingEvent(
                event_type=TradingEventType.SignalPublished.value,
                aggregate_id=signal.id,
                sequence_number=2,
                payload=payload.model_dump(),
                source_service="strategy-engine",
            )
            self._event_publisher.publish(event)
        except Exception as exc:
            logger.error(
                "Failed to publish SignalPublished event for signal %s: %s",
                signal.id,
                exc,
            )

    def _publish_overlay(self, signal: Signal, account_id: Optional[str] = None) -> None:
        """Publish overlay data from signal metadata to strategy:overlays channel."""
        if self._redis is None:
            return

        metadata = signal.metadata
        overlays = []

        if metadata.entry_zone is not None:
            overlays.append({
                "kind": "entry_zone",
                "priceHigh": metadata.entry_zone.price_high,
                "priceLow": metadata.entry_zone.price_low,
                "startTime": metadata.entry_zone.timestamp,
                "direction": metadata.bos_type.value,
                "signalId": signal.id,
            })

        if metadata.exit_zone_sl is not None:
            overlays.append({
                "kind": "exit_zone",
                "price": metadata.exit_zone_sl.price,
                "startTime": metadata.exit_zone_sl.timestamp,
                "direction": metadata.bos_type.value,
                "signalId": signal.id,
                "type": "stop_loss",
            })

        if metadata.exit_zone_tp is not None:
            overlays.append({
                "kind": "exit_zone",
                "price": metadata.exit_zone_tp.price,
                "startTime": metadata.exit_zone_tp.timestamp,
                "direction": metadata.bos_type.value,
                "signalId": signal.id,
                "type": "take_profit",
            })

        if overlays:
            overlay_message = json.dumps({
                "accountId": account_id,
                "overlays": overlays,
            })
            self._redis.publish(STRATEGY_OVERLAYS_CHANNEL, overlay_message)
            logger.info(
                "Published %d overlay(s) for signal %s to %s",
                len(overlays),
                signal.id,
                STRATEGY_OVERLAYS_CHANNEL,
            )

    def _record_forward_test(self, signal: Signal) -> None:
        """Record signal for forward testing — no execution stream publish."""
        self._forward_test_signals.append(signal)
        logger.info("Recorded forward_test signal %s (not published to execution stream)", signal.id)

    def _record_backtest(self, signal: Signal) -> None:
        """Record signal in backtest results only."""
        self._backtest_signals.append(signal)
        logger.debug("Recorded backtest signal %s", signal.id)

    @property
    def forward_test_signals(self) -> list[Signal]:
        """Return recorded forward test signals."""
        return list(self._forward_test_signals)

    @property
    def backtest_signals(self) -> list[Signal]:
        """Return recorded backtest signals."""
        return list(self._backtest_signals)

    @property
    def analytics_signals(self) -> list[Signal]:
        """Return signals recorded for analytics (autopilot disabled)."""
        return list(self._analytics_signals)

    def clear_backtest_signals(self) -> None:
        """Clear backtest signal records (e.g., between backtest runs)."""
        self._backtest_signals.clear()

    def clear_forward_test_signals(self) -> None:
        """Clear forward test signal records."""
        self._forward_test_signals.clear()

    def clear_analytics_signals(self) -> None:
        """Clear analytics signal records."""
        self._analytics_signals.clear()
