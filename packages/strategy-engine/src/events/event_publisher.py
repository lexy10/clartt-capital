"""EventPublisher — publishes TradingEvents to the events:stream Redis stream.

Fire-and-forget: errors are logged but never raised, so the primary
trading pipeline is never blocked by event publishing failures.
"""

import logging

from prometheus_client import Counter
from redis import Redis

from src.models.trading_event import TradingEvent

logger = logging.getLogger("strategy_engine.event_publisher")

event_publisher_events_published_total = Counter(
    "event_publisher_events_published_total",
    "Total events successfully published to the Redis event stream",
    labelnames=["event_type"],
)


class EventPublisher:
    """Publishes TradingEvents to the events:stream Redis stream."""

    def __init__(self, redis_client: Redis, source_service: str = "strategy-engine"):
        self._redis = redis_client
        self._source_service = source_service
        self._stream_key = "events:stream"
        self._max_len = 10000

    def publish(self, event: TradingEvent) -> None:
        """Validate, serialize, and publish event. Logs errors without raising."""
        try:
            event.source_service = self._source_service
            # Pydantic validation happens implicitly via model_dump_json;
            # any schema violation will raise a ValidationError caught below.
            payload = event.model_dump_json()
            self._redis.xadd(
                self._stream_key,
                {"data": payload},
                maxlen=self._max_len,
                approximate=True,
            )
            event_publisher_events_published_total.labels(
                event_type=event.event_type,
            ).inc()
        except Exception as e:
            logger.error("Failed to publish event %s: %s", event.event_type, e)
