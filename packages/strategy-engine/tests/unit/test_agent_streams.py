"""Unit tests for src/agents/streams.py — stream publisher/consumer helpers."""

import asyncio
from typing import Any, Optional
from unittest.mock import MagicMock

import pytest
from pydantic import BaseModel

from src.agents.streams import (
    AGENT_STREAMS,
    CONSUMER_GROUPS,
    STREAM_MAXLEN,
    AgentStreamConsumer,
    AgentStreamPublisher,
    agent_stream_deserialization_errors_total,
    create_consumer_groups,
)


# ── Test model ─────────────────────────────────────────────────────


class SampleModel(BaseModel):
    name: str
    value: int


# ── Fake Redis ─────────────────────────────────────────────────────


class FakeRedis:
    """Minimal Redis stub for stream operations."""

    def __init__(self):
        self.streams: dict[str, list[tuple[str, dict]]] = {}
        self.groups: dict[str, list[str]] = {}
        self.acked: list[tuple[str, str, Any]] = []
        self._msg_counter = 0
        self._xreadgroup_responses: list = []
        self._xgroup_create_error: Optional[Exception] = None
        self._consumer: Optional[AgentStreamConsumer] = None

    def xadd(self, stream, fields, maxlen=None, approximate=False):
        self._msg_counter += 1
        msg_id = f"{self._msg_counter}-0"
        self.streams.setdefault(stream, []).append((msg_id, fields))
        return msg_id.encode()

    def xgroup_create(self, name, groupname, id="0", mkstream=False):
        if self._xgroup_create_error:
            raise self._xgroup_create_error
        self.groups.setdefault(name, []).append(groupname)

    def xreadgroup(self, groupname, consumername, streams, count=10, block=1000):
        if self._xreadgroup_responses:
            return self._xreadgroup_responses.pop(0)
        # Auto-stop consumer when responses exhausted
        if self._consumer:
            self._consumer.stop()
        return None

    def xack(self, stream, group, message_id):
        self.acked.append((stream, group, message_id))


# ── Fixtures ───────────────────────────────────────────────────────


@pytest.fixture
def redis():
    return FakeRedis()


@pytest.fixture
def publisher(redis):
    return AgentStreamPublisher(redis)


@pytest.fixture
def consumer(redis):
    c = AgentStreamConsumer(redis)
    redis._consumer = c
    return c


# ── create_consumer_groups tests ───────────────────────────────────


class TestCreateConsumerGroups:
    def test_creates_all_groups(self, redis: FakeRedis):
        create_consumer_groups(redis)
        for stream, groups in CONSUMER_GROUPS.items():
            for group in groups:
                assert group in redis.groups.get(stream, []), (
                    f"Group '{group}' not created on stream '{stream}'"
                )

    def test_ignores_busygroup_error(self, redis: FakeRedis):
        redis._xgroup_create_error = Exception("BUSYGROUP Consumer Group name already exists")
        # Should not raise
        create_consumer_groups(redis)

    def test_logs_non_busygroup_error(self, redis: FakeRedis):
        redis._xgroup_create_error = Exception("Connection refused")
        # Should not raise — errors are logged
        create_consumer_groups(redis)


# ── AgentStreamPublisher tests ─────────────────────────────────────


class TestAgentStreamPublisher:
    def test_publish_serializes_model(self, publisher: AgentStreamPublisher, redis: FakeRedis):
        model = SampleModel(name="test", value=42)
        msg_id = publisher.publish("agents:tasks", model)

        assert msg_id is not None
        assert len(redis.streams["agents:tasks"]) == 1
        _, fields = redis.streams["agents:tasks"][0]
        assert "data" in fields
        # Verify round-trip
        deserialized = SampleModel.model_validate_json(fields["data"])
        assert deserialized.name == "test"
        assert deserialized.value == 42

    def test_publish_returns_message_id(self, publisher: AgentStreamPublisher):
        model = SampleModel(name="a", value=1)
        msg_id = publisher.publish("agents:events", model)
        assert msg_id == "1-0"

    def test_publish_uses_maxlen(self, redis: FakeRedis):
        """Verify XADD is called with MAXLEN ~ 10000."""
        redis.xadd = MagicMock(return_value=b"1-0")
        pub = AgentStreamPublisher(redis)
        pub.publish("agents:tasks", SampleModel(name="x", value=0))

        redis.xadd.assert_called_once()
        call_kwargs = redis.xadd.call_args
        assert call_kwargs[1]["maxlen"] == STREAM_MAXLEN
        assert call_kwargs[1]["approximate"] is True

    def test_publish_returns_none_on_error(self):
        broken_redis = MagicMock()
        broken_redis.xadd.side_effect = Exception("Connection lost")
        pub = AgentStreamPublisher(broken_redis)
        result = pub.publish("agents:tasks", SampleModel(name="x", value=0))
        assert result is None


# ── AgentStreamConsumer tests ──────────────────────────────────────


class TestAgentStreamConsumer:
    @pytest.mark.asyncio
    async def test_consume_deserializes_and_calls_handler(
        self, consumer: AgentStreamConsumer, redis: FakeRedis
    ):
        model = SampleModel(name="hello", value=99)
        payload = model.model_dump_json()

        redis._xreadgroup_responses = [
            [("agents:tasks", [("1-0", {b"data": payload.encode()})])],
        ]

        received = []

        async def handler(m):
            received.append(m)
            consumer.stop()

        await consumer.consume(
            "agents:tasks", "agents-orchestrator", "worker-1", handler, SampleModel
        )

        assert len(received) == 1
        assert received[0].name == "hello"
        assert received[0].value == 99

    @pytest.mark.asyncio
    async def test_consume_acks_after_processing(
        self, consumer: AgentStreamConsumer, redis: FakeRedis
    ):
        model = SampleModel(name="ack-test", value=1)
        payload = model.model_dump_json()

        redis._xreadgroup_responses = [
            [("agents:tasks", [("2-0", {b"data": payload.encode()})])],
        ]

        async def handler(m):
            consumer.stop()

        await consumer.consume(
            "agents:tasks", "agents-orchestrator", "worker-1", handler, SampleModel
        )

        assert any(
            stream == "agents:tasks" and group == "agents-orchestrator"
            for stream, group, _ in redis.acked
        )

    @pytest.mark.asyncio
    async def test_consume_acks_deserialization_failures(
        self, consumer: AgentStreamConsumer, redis: FakeRedis
    ):
        """Deserialization failures should be acked to prevent redelivery."""
        good_model = SampleModel(name="ok", value=1)
        redis._xreadgroup_responses = [
            [("agents:tasks", [("3-0", {b"data": b"not valid json"})])],
            [("agents:tasks", [("4-0", {b"data": good_model.model_dump_json().encode()})])],
        ]

        before = agent_stream_deserialization_errors_total.labels(
            stream="agents:tasks"
        )._value.get()

        async def handler(m):
            # Stop after the good message arrives (bad one never reaches handler)
            consumer.stop()

        await consumer.consume(
            "agents:tasks", "agents-orchestrator", "worker-1", handler, SampleModel
        )

        # Bad message was acked
        assert any(
            msg_id == "3-0" for _, _, msg_id in redis.acked
        )

        after = agent_stream_deserialization_errors_total.labels(
            stream="agents:tasks"
        )._value.get()
        assert after > before

    @pytest.mark.asyncio
    async def test_consume_handles_missing_data_field(
        self, consumer: AgentStreamConsumer, redis: FakeRedis
    ):
        """Messages without a 'data' field should be acked and skipped."""
        good_model = SampleModel(name="ok", value=1)
        redis._xreadgroup_responses = [
            [("agents:events", [("4-0", {b"other": b"stuff"})])],
            [("agents:events", [("5-0", {b"data": good_model.model_dump_json().encode()})])],
        ]

        async def handler(m):
            consumer.stop()

        await consumer.consume(
            "agents:events", "backend-agents", "worker-1", handler, SampleModel
        )

        # The bad message (no data field) was acked
        assert any(
            msg_id == "4-0" for _, _, msg_id in redis.acked
        )

    @pytest.mark.asyncio
    async def test_consume_acks_on_handler_error(
        self, consumer: AgentStreamConsumer, redis: FakeRedis
    ):
        """Even if the handler raises, the message should be acked."""
        model = SampleModel(name="err", value=0)
        payload = model.model_dump_json()

        redis._xreadgroup_responses = [
            [("agents:results", [("5-0", {b"data": payload.encode()})])],
        ]

        async def handler(m):
            consumer.stop()
            raise ValueError("handler boom")

        await consumer.consume(
            "agents:results", "agents-orchestrator", "worker-1", handler, SampleModel
        )

        assert any(
            stream == "agents:results" for stream, _, _ in redis.acked
        )

    def test_stop_sets_running_false(self, consumer: AgentStreamConsumer):
        consumer._running = True
        consumer.stop()
        assert consumer._running is False


# ── Constants tests ────────────────────────────────────────────────


class TestConstants:
    def test_all_streams_defined(self):
        expected = {
            "agents:tasks",
            "agents:results",
            "agents:events",
            "agents:research_output",
            "agents:backtest_output",
            "agents:forward_test_output",
        }
        assert set(AGENT_STREAMS) == expected

    def test_maxlen_is_10000(self):
        assert STREAM_MAXLEN == 10000

    def test_consumer_groups_include_orchestrator_and_backend(self):
        assert "agents-orchestrator" in CONSUMER_GROUPS["agents:tasks"]
        assert "agents-orchestrator" in CONSUMER_GROUPS["agents:results"]
        assert "backend-agents" in CONSUMER_GROUPS["agents:events"]
