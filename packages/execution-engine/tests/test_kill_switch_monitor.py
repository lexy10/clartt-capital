"""Unit tests for KillSwitchMonitor."""

import threading
import time
from unittest.mock import MagicMock, patch, call

import pytest

from src.kill_switch import (
    KillSwitchMonitor,
    KILL_SWITCH_KEY,
    KILL_SWITCH_CHANNEL,
    ACTIVE_VALUE,
    INACTIVE_VALUE,
)


@pytest.fixture
def mock_redis():
    """Create a mock Redis client."""
    return MagicMock()


@pytest.fixture
def monitor(mock_redis):
    """Create a KillSwitchMonitor with a mock Redis client."""
    return KillSwitchMonitor(redis_client=mock_redis)


class TestIsActive:
    """Tests for is_active() method."""

    def test_returns_false_when_redis_key_not_set(self, monitor, mock_redis):
        mock_redis.get.return_value = None
        assert monitor.is_active() is False

    def test_returns_true_when_redis_key_is_active(self, monitor, mock_redis):
        mock_redis.get.return_value = ACTIVE_VALUE
        assert monitor.is_active() is True

    def test_returns_false_when_redis_key_is_inactive(self, monitor, mock_redis):
        mock_redis.get.return_value = INACTIVE_VALUE
        assert monitor.is_active() is False

    def test_handles_bytes_value_from_redis(self, monitor, mock_redis):
        mock_redis.get.return_value = b"active"
        assert monitor.is_active() is True

    def test_handles_bytes_inactive_value(self, monitor, mock_redis):
        mock_redis.get.return_value = b"inactive"
        assert monitor.is_active() is False

    def test_caches_state_after_first_call(self, monitor, mock_redis):
        mock_redis.get.return_value = ACTIVE_VALUE

        assert monitor.is_active() is True
        assert monitor.is_active() is True

        # Redis should only be called once due to caching
        mock_redis.get.assert_called_once_with(KILL_SWITCH_KEY)

    def test_returns_false_on_redis_error(self, monitor, mock_redis):
        mock_redis.get.side_effect = Exception("Connection refused")
        assert monitor.is_active() is False


class TestSubscribe:
    """Tests for subscribe() method."""

    def test_registers_callback(self, monitor, mock_redis):
        callback = MagicMock()

        # Set up pubsub mock that stops immediately
        mock_pubsub = MagicMock()
        mock_pubsub.listen.return_value = iter([])
        mock_redis.pubsub.return_value = mock_pubsub

        monitor.subscribe(callback)

        # Give the thread a moment to start
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

        mock_pubsub.subscribe.assert_called_once_with(KILL_SWITCH_CHANNEL)


class TestPubSubStateChanges:
    """Tests for pub/sub message handling and callback invocation."""

    def test_callback_invoked_on_activation(self, monitor, mock_redis):
        callback = MagicMock()
        event = threading.Event()

        def side_effect(new_state):
            callback(new_state)
            event.set()

        mock_pubsub = MagicMock()
        messages = [
            {"type": "subscribe", "data": 1},
            {"type": "message", "data": b"active"},
        ]
        mock_pubsub.listen.return_value = iter(messages)
        mock_redis.pubsub.return_value = mock_pubsub

        monitor.subscribe(side_effect)
        event.wait(timeout=2.0)
        monitor.stop()

        callback.assert_called_once_with(True)

    def test_callback_invoked_on_deactivation(self, monitor, mock_redis):
        callback = MagicMock()
        event = threading.Event()

        # Start with active state
        mock_redis.get.return_value = ACTIVE_VALUE
        monitor.is_active()  # Initialize as active

        def side_effect(new_state):
            callback(new_state)
            event.set()

        mock_pubsub = MagicMock()
        messages = [
            {"type": "subscribe", "data": 1},
            {"type": "message", "data": b"inactive"},
        ]
        mock_pubsub.listen.return_value = iter(messages)
        mock_redis.pubsub.return_value = mock_pubsub

        monitor.subscribe(side_effect)
        event.wait(timeout=2.0)
        monitor.stop()

        callback.assert_called_once_with(False)

    def test_no_callback_when_state_unchanged(self, monitor, mock_redis):
        """If the state doesn't change, callbacks should not fire."""
        callback = MagicMock()

        # State starts as False (inactive), and we send "inactive" — no change
        mock_pubsub = MagicMock()
        messages = [
            {"type": "subscribe", "data": 1},
            {"type": "message", "data": b"inactive"},
        ]
        mock_pubsub.listen.return_value = iter(messages)
        mock_redis.pubsub.return_value = mock_pubsub

        monitor.subscribe(callback)
        time.sleep(0.2)
        monitor.stop()

        callback.assert_not_called()

    def test_state_updated_after_pubsub_message(self, monitor, mock_redis):
        """is_active() should reflect the new state after a pub/sub message."""
        mock_redis.get.return_value = INACTIVE_VALUE
        assert monitor.is_active() is False

        event = threading.Event()

        mock_pubsub = MagicMock()
        messages = [
            {"type": "subscribe", "data": 1},
            {"type": "message", "data": b"active"},
        ]
        mock_pubsub.listen.return_value = iter(messages)
        mock_redis.pubsub.return_value = mock_pubsub

        monitor.subscribe(lambda _: event.set())
        event.wait(timeout=2.0)
        monitor.stop()

        assert monitor.is_active() is True

    def test_callback_error_does_not_break_monitor(self, monitor, mock_redis):
        """A failing callback should not prevent other callbacks from running."""
        bad_callback = MagicMock(side_effect=Exception("callback error"))
        good_callback = MagicMock()
        event = threading.Event()
        both_registered = threading.Event()

        def tracked_good(state):
            good_callback(state)
            event.set()

        mock_pubsub = MagicMock()

        def delayed_messages():
            yield {"type": "subscribe", "data": 1}
            # Wait until both callbacks are registered before yielding the message
            both_registered.wait(timeout=5.0)
            yield {"type": "message", "data": b"active"}

        mock_pubsub.listen.return_value = delayed_messages()
        mock_redis.pubsub.return_value = mock_pubsub

        monitor.subscribe(bad_callback)
        monitor.subscribe(tracked_good)
        both_registered.set()
        event.wait(timeout=2.0)
        monitor.stop()

        bad_callback.assert_called_once_with(True)
        good_callback.assert_called_once_with(True)

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

    def test_kill_switch_key(self):
        assert KILL_SWITCH_KEY == "kill_switch:status"

    def test_kill_switch_channel(self):
        assert KILL_SWITCH_CHANNEL == "kill_switch:channel"

    def test_active_value(self):
        assert ACTIVE_VALUE == "active"

    def test_inactive_value(self):
        assert INACTIVE_VALUE == "inactive"
