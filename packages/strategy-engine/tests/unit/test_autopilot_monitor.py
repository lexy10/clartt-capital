"""Unit tests for AutopilotMonitor."""

import json
import threading
import time
from unittest.mock import MagicMock

import pytest

from src.autopilot import (
    AutopilotMonitor,
    AUTOPILOT_KEY_PREFIX,
    AUTOPILOT_CHANNEL,
    ENABLED_VALUE,
    DISABLED_VALUE,
)


@pytest.fixture
def mock_redis():
    """Create a mock Redis client."""
    return MagicMock()


@pytest.fixture
def monitor(mock_redis):
    """Create an AutopilotMonitor with a mock Redis client."""
    return AutopilotMonitor(redis_client=mock_redis)


class TestIsEnabled:
    """Tests for is_enabled() method."""

    def test_returns_false_when_redis_key_not_set(self, monitor, mock_redis):
        mock_redis.get.return_value = None
        assert monitor.is_enabled("acc-1") is False

    def test_returns_true_when_redis_key_is_enabled(self, monitor, mock_redis):
        mock_redis.get.return_value = ENABLED_VALUE
        assert monitor.is_enabled("acc-1") is True

    def test_returns_false_when_redis_key_is_disabled(self, monitor, mock_redis):
        mock_redis.get.return_value = DISABLED_VALUE
        assert monitor.is_enabled("acc-1") is False

    def test_handles_bytes_value_from_redis(self, monitor, mock_redis):
        mock_redis.get.return_value = b"enabled"
        assert monitor.is_enabled("acc-1") is True

    def test_handles_bytes_disabled_value(self, monitor, mock_redis):
        mock_redis.get.return_value = b"disabled"
        assert monitor.is_enabled("acc-1") is False

    def test_caches_state_after_first_call(self, monitor, mock_redis):
        mock_redis.get.return_value = ENABLED_VALUE

        assert monitor.is_enabled("acc-1") is True
        assert monitor.is_enabled("acc-1") is True

        # Redis should only be called once due to caching
        mock_redis.get.assert_called_once_with(f"{AUTOPILOT_KEY_PREFIX}acc-1")

    def test_returns_false_on_redis_error(self, monitor, mock_redis):
        mock_redis.get.side_effect = Exception("Connection refused")
        assert monitor.is_enabled("acc-1") is False

    def test_tracks_multiple_accounts_independently(self, monitor, mock_redis):
        def get_side_effect(key):
            if key == f"{AUTOPILOT_KEY_PREFIX}acc-1":
                return ENABLED_VALUE
            return DISABLED_VALUE

        mock_redis.get.side_effect = get_side_effect

        assert monitor.is_enabled("acc-1") is True
        assert monitor.is_enabled("acc-2") is False

    def test_reads_correct_redis_key(self, monitor, mock_redis):
        mock_redis.get.return_value = None
        monitor.is_enabled("my-account-123")
        mock_redis.get.assert_called_once_with(f"{AUTOPILOT_KEY_PREFIX}my-account-123")


class TestSubscribe:
    """Tests for subscribe() method."""

    def test_registers_callback(self, monitor, mock_redis):
        callback = MagicMock()

        mock_pubsub = MagicMock()
        mock_pubsub.listen.return_value = iter([])
        mock_redis.pubsub.return_value = mock_pubsub

        monitor.subscribe(callback)
        time.sleep(0.1)
        monitor.stop()

        assert callback in monitor._callbacks

    def test_multiple_callbacks_registered(self, monitor, mock_redis):
        cb1 = MagicMock()
        cb2 = MagicMock()

        mock_pubsub = MagicMock()
        mock_pubsub.listen.return_value = iter([])
        mock_redis.pubsub.return_value = mock_pubsub

        monitor.subscribe(cb1)
        time.sleep(0.05)
        monitor.subscribe(cb2)
        time.sleep(0.05)
        monitor.stop()

        assert cb1 in monitor._callbacks
        assert cb2 in monitor._callbacks

    def test_starts_listener_thread(self, monitor, mock_redis):
        mock_pubsub = MagicMock()
        mock_pubsub.listen.return_value = iter([])
        mock_redis.pubsub.return_value = mock_pubsub

        monitor.subscribe(MagicMock())
        time.sleep(0.1)

        assert monitor._subscriber_thread is not None
        monitor.stop()

    def test_subscribes_to_correct_channel(self, monitor, mock_redis):
        mock_pubsub = MagicMock()
        mock_pubsub.listen.return_value = iter([])
        mock_redis.pubsub.return_value = mock_pubsub

        monitor.subscribe(MagicMock())
        time.sleep(0.1)
        monitor.stop()

        mock_pubsub.subscribe.assert_called_once_with(AUTOPILOT_CHANNEL)


class TestPubSubStateChanges:
    """Tests for pub/sub message handling and callback invocation."""

    def test_callback_invoked_on_enable(self, monitor, mock_redis):
        callback = MagicMock()
        event = threading.Event()

        def side_effect(account_id, enabled):
            callback(account_id, enabled)
            event.set()

        payload = json.dumps({"accountId": "acc-1", "enabled": True})
        mock_pubsub = MagicMock()
        messages = [
            {"type": "subscribe", "data": 1},
            {"type": "message", "data": payload.encode()},
        ]
        mock_pubsub.listen.return_value = iter(messages)
        mock_redis.pubsub.return_value = mock_pubsub

        monitor.subscribe(side_effect)
        event.wait(timeout=2.0)
        monitor.stop()

        callback.assert_called_once_with("acc-1", True)

    def test_callback_invoked_on_disable(self, monitor, mock_redis):
        callback = MagicMock()
        event = threading.Event()

        # Pre-load account as enabled
        mock_redis.get.return_value = ENABLED_VALUE
        monitor.is_enabled("acc-1")

        def side_effect(account_id, enabled):
            callback(account_id, enabled)
            event.set()

        payload = json.dumps({"accountId": "acc-1", "enabled": False})
        mock_pubsub = MagicMock()
        messages = [
            {"type": "subscribe", "data": 1},
            {"type": "message", "data": payload.encode()},
        ]
        mock_pubsub.listen.return_value = iter(messages)
        mock_redis.pubsub.return_value = mock_pubsub

        monitor.subscribe(side_effect)
        event.wait(timeout=2.0)
        monitor.stop()

        callback.assert_called_once_with("acc-1", False)

    def test_no_callback_when_state_unchanged(self, monitor, mock_redis):
        """If the state doesn't change, callbacks should not fire."""
        callback = MagicMock()

        # State starts as False (disabled), and we send disabled — no change
        mock_redis.get.return_value = DISABLED_VALUE
        monitor.is_enabled("acc-1")

        payload = json.dumps({"accountId": "acc-1", "enabled": False})
        mock_pubsub = MagicMock()
        messages = [
            {"type": "subscribe", "data": 1},
            {"type": "message", "data": payload.encode()},
        ]
        mock_pubsub.listen.return_value = iter(messages)
        mock_redis.pubsub.return_value = mock_pubsub

        monitor.subscribe(callback)
        time.sleep(0.2)
        monitor.stop()

        callback.assert_not_called()

    def test_state_updated_after_pubsub_message(self, monitor, mock_redis):
        """is_enabled() should reflect the new state after a pub/sub message."""
        mock_redis.get.return_value = DISABLED_VALUE
        assert monitor.is_enabled("acc-1") is False

        event = threading.Event()

        payload = json.dumps({"accountId": "acc-1", "enabled": True})
        mock_pubsub = MagicMock()
        messages = [
            {"type": "subscribe", "data": 1},
            {"type": "message", "data": payload.encode()},
        ]
        mock_pubsub.listen.return_value = iter(messages)
        mock_redis.pubsub.return_value = mock_pubsub

        monitor.subscribe(lambda aid, en: event.set())
        event.wait(timeout=2.0)
        monitor.stop()

        assert monitor.is_enabled("acc-1") is True

    def test_callback_error_does_not_break_monitor(self, monitor, mock_redis):
        """A failing callback should not prevent other callbacks from running."""
        bad_callback = MagicMock(side_effect=Exception("callback error"))
        good_callback = MagicMock()
        event = threading.Event()

        def tracked_good(account_id, enabled):
            good_callback(account_id, enabled)
            event.set()

        payload = json.dumps({"accountId": "acc-1", "enabled": True})
        mock_pubsub = MagicMock()
        messages = [
            {"type": "subscribe", "data": 1},
            {"type": "message", "data": payload.encode()},
        ]
        mock_pubsub.listen.return_value = iter(messages)
        mock_redis.pubsub.return_value = mock_pubsub

        monitor.subscribe(bad_callback)
        monitor.subscribe(tracked_good)
        event.wait(timeout=2.0)
        monitor.stop()

        bad_callback.assert_called_once_with("acc-1", True)
        good_callback.assert_called_once_with("acc-1", True)

    def test_ignores_non_message_types(self, monitor, mock_redis):
        callback = MagicMock()

        mock_pubsub = MagicMock()
        messages = [
            {"type": "subscribe", "data": 1},
            {"type": "psubscribe", "data": 1},
        ]
        mock_pubsub.listen.return_value = iter(messages)
        mock_redis.pubsub.return_value = mock_pubsub

        monitor.subscribe(callback)
        time.sleep(0.2)
        monitor.stop()

        callback.assert_not_called()

    def test_ignores_invalid_json(self, monitor, mock_redis):
        callback = MagicMock()

        mock_pubsub = MagicMock()
        messages = [
            {"type": "subscribe", "data": 1},
            {"type": "message", "data": b"not-json"},
        ]
        mock_pubsub.listen.return_value = iter(messages)
        mock_redis.pubsub.return_value = mock_pubsub

        monitor.subscribe(callback)
        time.sleep(0.2)
        monitor.stop()

        callback.assert_not_called()

    def test_ignores_message_without_account_id(self, monitor, mock_redis):
        callback = MagicMock()

        payload = json.dumps({"enabled": True})
        mock_pubsub = MagicMock()
        messages = [
            {"type": "subscribe", "data": 1},
            {"type": "message", "data": payload.encode()},
        ]
        mock_pubsub.listen.return_value = iter(messages)
        mock_redis.pubsub.return_value = mock_pubsub

        monitor.subscribe(callback)
        time.sleep(0.2)
        monitor.stop()

        callback.assert_not_called()


class TestStop:
    """Tests for stop() method."""

    def test_stop_without_subscriber(self, monitor):
        # Should not raise
        monitor.stop()

    def test_stop_sets_event(self, monitor, mock_redis):
        mock_pubsub = MagicMock()
        mock_pubsub.listen.return_value = iter([])
        mock_redis.pubsub.return_value = mock_pubsub

        monitor.subscribe(MagicMock())
        time.sleep(0.1)
        monitor.stop()

        assert monitor._stop_event.is_set()


class TestConstants:
    """Verify Redis key and channel constants."""

    def test_autopilot_key_prefix(self):
        assert AUTOPILOT_KEY_PREFIX == "autopilot:"

    def test_autopilot_channel(self):
        assert AUTOPILOT_CHANNEL == "autopilot:channel"

    def test_enabled_value(self):
        assert ENABLED_VALUE == "enabled"

    def test_disabled_value(self):
        assert DISABLED_VALUE == "disabled"
