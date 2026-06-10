"""Redis stream publisher/consumer helpers for inter-agent communication.

Provides AgentStreamPublisher for XADD with MAXLEN ~ 10000,
AgentStreamConsumer for XREADGROUP with BLOCK 1000ms + XACK,
and create_consumer_groups() for startup initialization.

Serialization: Pydantic model_dump_json() / model_validate_json().
Deserialization failures are logged, acked, and counted via Prometheus.
"""

import asyncio
import logging
from typing import Any, Awaitable, Callable, Optional, Type

from prometheus_client import Counter
from pydantic import BaseModel
from redis import Redis

logger = logging.getLogger("strategy_engine.agents.streams")

# All agent streams used for inter-agent communication
AGENT_STREAMS = [
    "agents:tasks",
    "agents:results",
    "agents:events",
    "agents:research_output",
    "agents:backtest_output",
    "agents:forward_test_output",
]

# Consumer groups created on startup
CONSUMER_GROUPS = {
    "agents:tasks": ["agents-orchestrator"],
    "agents:results": ["agents-orchestrator"],
    "agents:events": ["backend-agents"],
    "agents:research_output": ["agents-orchestrator"],
    "agents:backtest_output": ["agents-orchestrator"],
    "agents:forward_test_output": ["agents-orchestrator"],
}

STREAM_MAXLEN = 10000

agent_stream_deserialization_errors_total = Counter(
    "agent_stream_deserialization_errors_total",
    "Total deserialization errors when consuming agent streams",
    ["stream"],
)


def create_consumer_groups(redis_client: Redis) -> None:
    """Create consumer groups for all agent streams on startup.

    Silently ignores BUSYGROUP errors (group already exists).
    Creates the stream if it does not exist (MKSTREAM).
    """
    for stream, groups in CONSUMER_GROUPS.items():
        for group in groups:
            try:
                redis_client.xgroup_create(
                    name=stream,
                    groupname=group,
                    id="0",
                    mkstream=True,
                )
                logger.info(
                    "Created consumer group '%s' on stream '%s'", group, stream
                )
            except Exception as e:
                # BUSYGROUP = group already exists — safe to ignore
                if "BUSYGROUP" in str(e):
                    logger.debug(
                        "Consumer group '%s' already exists on stream '%s'",
                        group,
                        stream,
                    )
                else:
                    logger.error(
                        "Failed to create consumer group '%s' on stream '%s': %s",
                        group,
                        stream,
                        e,
                    )


class AgentStreamPublisher:
    """Publishes Pydantic models to Redis streams via XADD with MAXLEN ~ 10000."""

    def __init__(self, redis_client: Redis) -> None:
        self._redis = redis_client

    def publish(self, stream_name: str, model: BaseModel) -> Optional[str]:
        """Serialize model and publish to the given stream.

        Returns the message ID on success, None on failure.
        """
        try:
            payload = model.model_dump_json()
            message_id = self._redis.xadd(
                stream_name,
                {"data": payload},
                maxlen=STREAM_MAXLEN,
                approximate=True,
            )
            logger.debug(
                "Published to stream '%s': message_id=%s", stream_name, message_id
            )
            return message_id if isinstance(message_id, str) else message_id.decode() if message_id else None
        except Exception as e:
            logger.error("Failed to publish to stream '%s': %s", stream_name, e)
            return None


class AgentStreamConsumer:
    """Consumes Pydantic models from Redis streams via XREADGROUP + XACK."""

    def __init__(self, redis_client: Redis) -> None:
        self._redis = redis_client
        self._running = False

    async def consume(
        self,
        stream_name: str,
        group: str,
        consumer_name: str,
        handler: Callable[[BaseModel], Awaitable[None]],
        model_class: Type[BaseModel] = BaseModel,
        batch_size: int = 10,
    ) -> None:
        """Read messages from a stream consumer group and invoke handler.

        Uses XREADGROUP with BLOCK 1000ms. Deserializes each message
        via model_class.model_validate_json(). On deserialization failure,
        logs the error, acks the message, and increments the Prometheus counter.

        Args:
            stream_name: Redis stream to consume from.
            group: Consumer group name.
            consumer_name: Unique consumer name within the group.
            handler: Async callable invoked with each deserialized model.
            model_class: Pydantic model class for deserialization.
            batch_size: Max messages to read per XREADGROUP call.
        """
        self._running = True
        logger.info(
            "Starting consumer '%s' on stream '%s' (group='%s')",
            consumer_name,
            stream_name,
            group,
        )

        while self._running:
            try:
                messages = self._redis.xreadgroup(
                    groupname=group,
                    consumername=consumer_name,
                    streams={stream_name: ">"},
                    count=batch_size,
                    block=1000,
                )

                if not messages:
                    await asyncio.sleep(0)
                    continue

                for _stream, entries in messages:
                    for message_id, fields in entries:
                        await self._process_message(
                            stream_name,
                            group,
                            message_id,
                            fields,
                            handler,
                            model_class,
                        )

            except Exception as e:
                if not self._running:
                    break
                logger.error(
                    "Error reading from stream '%s': %s", stream_name, e
                )
                await asyncio.sleep(1)

        logger.info(
            "Consumer '%s' on stream '%s' stopped", consumer_name, stream_name
        )

    async def _process_message(
        self,
        stream_name: str,
        group: str,
        message_id: Any,
        fields: dict,
        handler: Callable[[BaseModel], Awaitable[None]],
        model_class: Type[BaseModel],
    ) -> None:
        """Deserialize, handle, and ack a single message."""
        raw_data = fields.get(b"data") or fields.get("data")
        if raw_data is None:
            logger.warning(
                "Message %s on stream '%s' has no 'data' field, acking",
                message_id,
                stream_name,
            )
            self._ack(stream_name, group, message_id)
            return

        if isinstance(raw_data, bytes):
            raw_data = raw_data.decode("utf-8")

        # Deserialize
        try:
            model = model_class.model_validate_json(raw_data)
        except Exception as e:
            logger.error(
                "Deserialization failed for message %s on stream '%s': %s",
                message_id,
                stream_name,
                e,
            )
            agent_stream_deserialization_errors_total.labels(
                stream=stream_name
            ).inc()
            self._ack(stream_name, group, message_id)
            return

        # Handle
        try:
            await handler(model)
        except Exception as e:
            logger.error(
                "Handler error for message %s on stream '%s': %s",
                message_id,
                stream_name,
                e,
            )

        # Always ack after processing (or failure) to prevent redelivery loops
        self._ack(stream_name, group, message_id)

    def _ack(self, stream_name: str, group: str, message_id: Any) -> None:
        """Acknowledge a message in the consumer group."""
        try:
            self._redis.xack(stream_name, group, message_id)
        except Exception as e:
            logger.error(
                "Failed to XACK message %s on stream '%s': %s",
                message_id,
                stream_name,
                e,
            )

    def stop(self) -> None:
        """Signal the consumer loop to stop."""
        self._running = False
