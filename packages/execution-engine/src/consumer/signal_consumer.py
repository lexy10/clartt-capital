"""Signal consumer for reading signals from Redis streams using consumer groups.

Uses XREADGROUP for per-account consumption, ensuring each signal is delivered
to exactly one consumer per group (no duplication). Each account worker has its
own consumer_id within the group.
"""

import json
import logging
from typing import Optional

from redis import Redis, ResponseError

from ..models.signal import Signal

logger = logging.getLogger(__name__)

SIGNAL_STREAM_KEY = "signals:stream"


class SignalConsumer:
    """Consumes signals from a Redis stream using consumer groups.

    Each consumer group represents a logical processing unit (e.g., all account
    workers). Within a group, each consumer_id represents an individual worker.
    Redis guarantees that each message is delivered to exactly one consumer
    within a group.
    """

    def __init__(self, redis_client: Redis) -> None:
        self._redis = redis_client

    def _ensure_group(self, stream: str, group: str) -> None:
        """Create the consumer group if it doesn't already exist.

        Uses XGROUP CREATE with MKSTREAM so the stream is also created
        if it doesn't exist yet. The group starts reading from the
        beginning of the stream (id='0').
        """
        try:
            self._redis.xgroup_create(stream, group, id="0", mkstream=True)
            logger.info("Created consumer group '%s' on stream '%s'", group, stream)
        except ResponseError as e:
            # "BUSYGROUP Consumer Group name already exists" is expected
            if "BUSYGROUP" in str(e):
                logger.debug("Consumer group '%s' already exists on stream '%s'", group, stream)
            else:
                raise

    def consume(
        self, stream: str, group: str, consumer_id: str, block_ms: int = 0, count: int = 1
    ) -> Optional[tuple[str, Signal]]:
        """Read the next signal from the stream using XREADGROUP.

        Args:
            stream: Redis stream key (e.g. 'signals:stream').
            group: Consumer group name.
            consumer_id: Unique consumer identifier within the group.
            block_ms: How long to block waiting for messages (0 = no block).
            count: Maximum number of messages to read.

        Returns:
            A tuple of (message_id, Signal) if a message was available,
            or None if no messages are pending.
        """
        self._ensure_group(stream, group)

        # '>' means read only new (undelivered) messages
        results = self._redis.xreadgroup(
            group, consumer_id, {stream: ">"}, count=count, block=block_ms
        )

        if not results:
            return None

        # results format: [[stream_name, [(message_id, fields_dict), ...]]]
        stream_name, messages = results[0]
        if not messages:
            return None

        message_id, fields = messages[0]

        # Decode bytes if needed (redis-py may return bytes)
        data_raw = fields.get("data") or fields.get(b"data")
        if data_raw is None:
            logger.warning("Message %s has no 'data' field, skipping", message_id)
            self._redis.xack(stream, group, message_id)
            return None

        if isinstance(data_raw, bytes):
            data_raw = data_raw.decode("utf-8")
        if isinstance(message_id, bytes):
            message_id = message_id.decode("utf-8")

        try:
            signal = Signal.model_validate_json(data_raw)
        except Exception:
            logger.exception("Failed to parse signal from message %s", message_id)
            self._redis.xack(stream, group, message_id)
            return None

        logger.info("Consumed signal %s from message %s", signal.id, message_id)
        return message_id, signal

    def acknowledge(self, stream: str, group: str, message_id: str) -> None:
        """Acknowledge a processed message so it won't be re-delivered.

        Args:
            stream: Redis stream key.
            group: Consumer group name.
            message_id: The message ID returned by consume().
        """
        self._redis.xack(stream, group, message_id)
        logger.debug("Acknowledged message %s on stream '%s' group '%s'", message_id, stream, group)
