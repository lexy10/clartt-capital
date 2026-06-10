"""Unit tests for SignalConsumer."""

import json
from unittest.mock import MagicMock, patch

import pytest
from redis import ResponseError

from src.consumer.signal_consumer import SignalConsumer, SIGNAL_STREAM_KEY
from src.models import Signal, SignalDirection, SignalMetadata, SignalMode, BOSType, Timeframe


@pytest.fixture
def mock_redis():
    """Create a mock Redis client."""
    return MagicMock()


@pytest.fixture
def consumer(mock_redis):
    """Create a SignalConsumer with a mock Redis client."""
    return SignalConsumer(redis_client=mock_redis)


@pytest.fixture
def signal_json(sample_signal: Signal) -> str:
    """Serialize the sample signal to JSON."""
    return sample_signal.model_dump_json()


class TestEnsureGroup:
    """Tests for consumer group creation."""

    def test_creates_group_when_not_exists(self, consumer, mock_redis):
        mock_redis.xgroup_create.return_value = True

        consumer._ensure_group("signals:stream", "my-group")

        mock_redis.xgroup_create.assert_called_once_with(
            "signals:stream", "my-group", id="0", mkstream=True
        )

    def test_ignores_busygroup_error(self, consumer, mock_redis):
        mock_redis.xgroup_create.side_effect = ResponseError(
            "BUSYGROUP Consumer Group name already exists"
        )

        # Should not raise
        consumer._ensure_group("signals:stream", "my-group")

    def test_raises_on_other_response_error(self, consumer, mock_redis):
        mock_redis.xgroup_create.side_effect = ResponseError("Some other error")

        with pytest.raises(ResponseError, match="Some other error"):
            consumer._ensure_group("signals:stream", "my-group")


class TestConsume:
    """Tests for signal consumption from Redis streams."""

    def test_returns_signal_on_success(self, consumer, mock_redis, sample_signal):
        signal_data = sample_signal.model_dump_json()
        mock_redis.xgroup_create.side_effect = ResponseError(
            "BUSYGROUP Consumer Group name already exists"
        )
        mock_redis.xreadgroup.return_value = [
            ["signals:stream", [("1234-0", {"data": signal_data})]]
        ]

        result = consumer.consume("signals:stream", "group1", "worker-1")

        assert result is not None
        msg_id, signal = result
        assert msg_id == "1234-0"
        assert signal.id == sample_signal.id
        assert signal.instrument == "US30"
        assert signal.direction == SignalDirection.BUY

    def test_returns_none_when_no_messages(self, consumer, mock_redis):
        mock_redis.xgroup_create.side_effect = ResponseError(
            "BUSYGROUP Consumer Group name already exists"
        )
        mock_redis.xreadgroup.return_value = []

        result = consumer.consume("signals:stream", "group1", "worker-1")

        assert result is None

    def test_returns_none_when_results_empty_messages(self, consumer, mock_redis):
        mock_redis.xgroup_create.side_effect = ResponseError(
            "BUSYGROUP Consumer Group name already exists"
        )
        mock_redis.xreadgroup.return_value = [["signals:stream", []]]

        result = consumer.consume("signals:stream", "group1", "worker-1")

        assert result is None

    def test_handles_bytes_keys(self, consumer, mock_redis, sample_signal):
        """Redis may return bytes for keys and values."""
        signal_data = sample_signal.model_dump_json().encode("utf-8")
        mock_redis.xgroup_create.side_effect = ResponseError(
            "BUSYGROUP Consumer Group name already exists"
        )
        mock_redis.xreadgroup.return_value = [
            [b"signals:stream", [(b"1234-0", {b"data": signal_data})]]
        ]

        result = consumer.consume("signals:stream", "group1", "worker-1")

        assert result is not None
        msg_id, signal = result
        assert msg_id == "1234-0"
        assert signal.id == sample_signal.id

    def test_returns_none_on_missing_data_field(self, consumer, mock_redis):
        mock_redis.xgroup_create.side_effect = ResponseError(
            "BUSYGROUP Consumer Group name already exists"
        )
        mock_redis.xreadgroup.return_value = [
            ["signals:stream", [("1234-0", {"other_field": "value"})]]
        ]

        result = consumer.consume("signals:stream", "group1", "worker-1")

        assert result is None

    def test_returns_none_on_invalid_json(self, consumer, mock_redis):
        mock_redis.xgroup_create.side_effect = ResponseError(
            "BUSYGROUP Consumer Group name already exists"
        )
        mock_redis.xreadgroup.return_value = [
            ["signals:stream", [("1234-0", {"data": "not-valid-json"})]]
        ]

        result = consumer.consume("signals:stream", "group1", "worker-1")

        assert result is None

    def test_passes_block_and_count_params(self, consumer, mock_redis):
        mock_redis.xgroup_create.side_effect = ResponseError(
            "BUSYGROUP Consumer Group name already exists"
        )
        mock_redis.xreadgroup.return_value = []

        consumer.consume("signals:stream", "group1", "worker-1", block_ms=5000, count=10)

        mock_redis.xreadgroup.assert_called_once_with(
            "group1", "worker-1", {"signals:stream": ">"}, count=10, block=5000
        )

    def test_calls_xreadgroup_with_correct_args(self, consumer, mock_redis):
        mock_redis.xgroup_create.side_effect = ResponseError(
            "BUSYGROUP Consumer Group name already exists"
        )
        mock_redis.xreadgroup.return_value = []

        consumer.consume("signals:stream", "group1", "worker-1")

        mock_redis.xreadgroup.assert_called_once_with(
            "group1", "worker-1", {"signals:stream": ">"}, count=1, block=0
        )


class TestAcknowledge:
    """Tests for message acknowledgement."""

    def test_calls_xack(self, consumer, mock_redis):
        consumer.acknowledge("signals:stream", "group1", "1234-0")

        mock_redis.xack.assert_called_once_with("signals:stream", "group1", "1234-0")


class TestStreamKeyConstant:
    """Verify the stream key matches the publisher."""

    def test_stream_key_matches_publisher(self):
        assert SIGNAL_STREAM_KEY == "signals:stream"
