"""Unit tests for SignalPublisher with mode-dependent routing."""

import json
from unittest.mock import MagicMock, patch

import pytest

from src.models.signal import (
    BOSType,
    EntryZone,
    ExitZone,
    Signal,
    SignalDirection,
    SignalMetadata,
    SignalMode,
)
from src.models.timeframe import Timeframe
from src.signals.signal_publisher import (
    SIGNAL_STREAM_KEY,
    STRATEGY_OVERLAYS_CHANNEL,
    SignalPublisher,
)


def _make_signal(mode: SignalMode, with_zones: bool = False) -> Signal:
    """Create a test signal with the given mode."""
    metadata_kwargs = dict(
        bos_type=BOSType.BULLISH,
        liquidity_swept=True,
        session="new_york",
        spread_at_generation=2.5,
        volatility_ratio=1.1,
    )
    if with_zones:
        metadata_kwargs["entry_zone"] = EntryZone(
            price_high=34550.0, price_low=34450.0, timestamp="2024-01-15T14:30:00Z"
        )
        metadata_kwargs["exit_zone_sl"] = ExitZone(
            type="stop_loss", price=34400.0, timestamp="2024-01-15T14:30:00Z"
        )
        metadata_kwargs["exit_zone_tp"] = ExitZone(
            type="take_profit", price=34700.0, timestamp="2024-01-15T14:30:00Z"
        )

    return Signal(
        id="test-signal-001",
        instrument="US30",
        direction=SignalDirection.BUY,
        entry_price=34500.0,
        stop_loss=34400.0,
        take_profit=34700.0,
        position_size=0.1,
        confidence_score=0.85,
        timeframe=Timeframe.FIFTEEN_MINUTES,
        order_block_id="ob-001",
        strategy_id="strat-001",
        mode=mode,
        metadata=SignalMetadata(**metadata_kwargs),
        created_at="2024-01-15T14:30:00Z",
    )


class TestSignalPublisherLiveMode:
    """Tests for live mode signal publishing."""

    def test_live_signal_published_to_redis_stream(self):
        mock_redis = MagicMock()
        publisher = SignalPublisher(redis_client=mock_redis)
        signal = _make_signal(SignalMode.LIVE)

        publisher.publish(signal)

        mock_redis.xadd.assert_called_once()
        call_args = mock_redis.xadd.call_args
        assert call_args[0][0] == SIGNAL_STREAM_KEY
        payload = json.loads(call_args[0][1]["data"])
        assert payload["id"] == "test-signal-001"
        assert payload["mode"] == "live"

    def test_live_signal_not_recorded_in_forward_test_list(self):
        mock_redis = MagicMock()
        publisher = SignalPublisher(redis_client=mock_redis)
        signal = _make_signal(SignalMode.LIVE)

        publisher.publish(signal)

        assert len(publisher.forward_test_signals) == 0

    def test_live_signal_not_recorded_in_backtest_list(self):
        mock_redis = MagicMock()
        publisher = SignalPublisher(redis_client=mock_redis)
        signal = _make_signal(SignalMode.LIVE)

        publisher.publish(signal)

        assert len(publisher.backtest_signals) == 0

    def test_live_mode_raises_without_redis(self):
        publisher = SignalPublisher(redis_client=None)
        signal = _make_signal(SignalMode.LIVE)

        with pytest.raises(RuntimeError, match="Redis client is required"):
            publisher.publish(signal)


class TestSignalPublisherForwardTestMode:
    """Tests for forward test mode signal recording."""

    def test_forward_test_signal_recorded(self):
        publisher = SignalPublisher()
        signal = _make_signal(SignalMode.FORWARD_TEST)

        publisher.publish(signal)

        assert len(publisher.forward_test_signals) == 1
        assert publisher.forward_test_signals[0].id == "test-signal-001"

    def test_forward_test_signal_not_published_to_redis(self):
        mock_redis = MagicMock()
        publisher = SignalPublisher(redis_client=mock_redis)
        signal = _make_signal(SignalMode.FORWARD_TEST)

        publisher.publish(signal)

        mock_redis.xadd.assert_not_called()

    def test_forward_test_signal_not_in_backtest_list(self):
        publisher = SignalPublisher()
        signal = _make_signal(SignalMode.FORWARD_TEST)

        publisher.publish(signal)

        assert len(publisher.backtest_signals) == 0

    def test_multiple_forward_test_signals_accumulated(self):
        publisher = SignalPublisher()

        for _ in range(3):
            publisher.publish(_make_signal(SignalMode.FORWARD_TEST))

        assert len(publisher.forward_test_signals) == 3

    def test_clear_forward_test_signals(self):
        publisher = SignalPublisher()
        publisher.publish(_make_signal(SignalMode.FORWARD_TEST))

        publisher.clear_forward_test_signals()

        assert len(publisher.forward_test_signals) == 0


class TestSignalPublisherBacktestMode:
    """Tests for backtest mode signal recording."""

    def test_backtest_signal_recorded(self):
        publisher = SignalPublisher()
        signal = _make_signal(SignalMode.BACKTEST)

        publisher.publish(signal)

        assert len(publisher.backtest_signals) == 1
        assert publisher.backtest_signals[0].id == "test-signal-001"

    def test_backtest_signal_not_published_to_redis(self):
        mock_redis = MagicMock()
        publisher = SignalPublisher(redis_client=mock_redis)
        signal = _make_signal(SignalMode.BACKTEST)

        publisher.publish(signal)

        mock_redis.xadd.assert_not_called()

    def test_backtest_signal_not_in_forward_test_list(self):
        publisher = SignalPublisher()
        signal = _make_signal(SignalMode.BACKTEST)

        publisher.publish(signal)

        assert len(publisher.forward_test_signals) == 0

    def test_multiple_backtest_signals_accumulated(self):
        publisher = SignalPublisher()

        for _ in range(5):
            publisher.publish(_make_signal(SignalMode.BACKTEST))

        assert len(publisher.backtest_signals) == 5

    def test_clear_backtest_signals(self):
        publisher = SignalPublisher()
        publisher.publish(_make_signal(SignalMode.BACKTEST))

        publisher.clear_backtest_signals()

        assert len(publisher.backtest_signals) == 0


class TestSignalPublisherIsolation:
    """Tests verifying mode isolation — signals only go to the correct destination."""

    def test_all_modes_routed_correctly(self):
        mock_redis = MagicMock()
        publisher = SignalPublisher(redis_client=mock_redis)

        publisher.publish(_make_signal(SignalMode.LIVE))
        publisher.publish(_make_signal(SignalMode.FORWARD_TEST))
        publisher.publish(_make_signal(SignalMode.BACKTEST))

        # Live went to Redis
        assert mock_redis.xadd.call_count == 1
        # Forward test recorded
        assert len(publisher.forward_test_signals) == 1
        # Backtest recorded
        assert len(publisher.backtest_signals) == 1

    def test_forward_test_signals_returns_copy(self):
        publisher = SignalPublisher()
        publisher.publish(_make_signal(SignalMode.FORWARD_TEST))

        signals = publisher.forward_test_signals
        signals.clear()

        assert len(publisher.forward_test_signals) == 1

    def test_backtest_signals_returns_copy(self):
        publisher = SignalPublisher()
        publisher.publish(_make_signal(SignalMode.BACKTEST))

        signals = publisher.backtest_signals
        signals.clear()

        assert len(publisher.backtest_signals) == 1


class TestSignalPublisherAutopilotGate:
    """Tests for autopilot-gated live signal publishing."""

    def test_autopilot_enabled_publishes_to_stream(self):
        mock_redis = MagicMock()
        mock_monitor = MagicMock()
        mock_monitor.is_enabled.return_value = True
        publisher = SignalPublisher(redis_client=mock_redis, autopilot_monitor=mock_monitor)
        signal = _make_signal(SignalMode.LIVE)

        publisher.publish(signal, account_id="acc-001")

        mock_monitor.is_enabled.assert_called_once_with("acc-001")
        mock_redis.xadd.assert_called_once()
        assert len(publisher.analytics_signals) == 0

    def test_autopilot_disabled_records_for_analytics(self):
        mock_redis = MagicMock()
        mock_monitor = MagicMock()
        mock_monitor.is_enabled.return_value = False
        publisher = SignalPublisher(redis_client=mock_redis, autopilot_monitor=mock_monitor)
        signal = _make_signal(SignalMode.LIVE)

        publisher.publish(signal, account_id="acc-001")

        mock_monitor.is_enabled.assert_called_once_with("acc-001")
        mock_redis.xadd.assert_not_called()
        assert len(publisher.analytics_signals) == 1
        assert publisher.analytics_signals[0].id == "test-signal-001"

    def test_autopilot_disabled_does_not_publish_overlay(self):
        mock_redis = MagicMock()
        mock_monitor = MagicMock()
        mock_monitor.is_enabled.return_value = False
        publisher = SignalPublisher(redis_client=mock_redis, autopilot_monitor=mock_monitor)
        signal = _make_signal(SignalMode.LIVE, with_zones=True)

        publisher.publish(signal, account_id="acc-001")

        mock_redis.publish.assert_not_called()

    def test_no_monitor_publishes_normally(self):
        """Without an autopilot monitor, live signals publish as before."""
        mock_redis = MagicMock()
        publisher = SignalPublisher(redis_client=mock_redis)
        signal = _make_signal(SignalMode.LIVE)

        publisher.publish(signal, account_id="acc-001")

        mock_redis.xadd.assert_called_once()

    def test_no_account_id_publishes_normally(self):
        """Without an account_id, live signals publish even with a monitor."""
        mock_redis = MagicMock()
        mock_monitor = MagicMock()
        publisher = SignalPublisher(redis_client=mock_redis, autopilot_monitor=mock_monitor)
        signal = _make_signal(SignalMode.LIVE)

        publisher.publish(signal)

        mock_redis.xadd.assert_called_once()
        mock_monitor.is_enabled.assert_not_called()

    def test_analytics_signals_returns_copy(self):
        mock_redis = MagicMock()
        mock_monitor = MagicMock()
        mock_monitor.is_enabled.return_value = False
        publisher = SignalPublisher(redis_client=mock_redis, autopilot_monitor=mock_monitor)
        publisher.publish(_make_signal(SignalMode.LIVE), account_id="acc-001")

        signals = publisher.analytics_signals
        signals.clear()

        assert len(publisher.analytics_signals) == 1

    def test_clear_analytics_signals(self):
        mock_redis = MagicMock()
        mock_monitor = MagicMock()
        mock_monitor.is_enabled.return_value = False
        publisher = SignalPublisher(redis_client=mock_redis, autopilot_monitor=mock_monitor)
        publisher.publish(_make_signal(SignalMode.LIVE), account_id="acc-001")

        publisher.clear_analytics_signals()

        assert len(publisher.analytics_signals) == 0


class TestSignalPublisherOverlayPublishing:
    """Tests for overlay data publishing to strategy:overlays channel."""

    def test_overlay_published_when_signal_has_zones(self):
        mock_redis = MagicMock()
        publisher = SignalPublisher(redis_client=mock_redis)
        signal = _make_signal(SignalMode.LIVE, with_zones=True)

        publisher.publish(signal, account_id="acc-001")

        mock_redis.publish.assert_called_once()
        call_args = mock_redis.publish.call_args
        assert call_args[0][0] == STRATEGY_OVERLAYS_CHANNEL
        overlay_data = json.loads(call_args[0][1])
        assert overlay_data["accountId"] == "acc-001"
        assert len(overlay_data["overlays"]) == 3

    def test_overlay_contains_entry_zone_data(self):
        mock_redis = MagicMock()
        publisher = SignalPublisher(redis_client=mock_redis)
        signal = _make_signal(SignalMode.LIVE, with_zones=True)

        publisher.publish(signal, account_id="acc-001")

        overlay_data = json.loads(mock_redis.publish.call_args[0][1])
        entry_overlay = [o for o in overlay_data["overlays"] if o["kind"] == "entry_zone"][0]
        assert entry_overlay["priceHigh"] == 34550.0
        assert entry_overlay["priceLow"] == 34450.0
        assert entry_overlay["signalId"] == "test-signal-001"
        assert entry_overlay["direction"] == "bullish"

    def test_overlay_contains_exit_zone_data(self):
        mock_redis = MagicMock()
        publisher = SignalPublisher(redis_client=mock_redis)
        signal = _make_signal(SignalMode.LIVE, with_zones=True)

        publisher.publish(signal, account_id="acc-001")

        overlay_data = json.loads(mock_redis.publish.call_args[0][1])
        exit_overlays = [o for o in overlay_data["overlays"] if o["kind"] == "exit_zone"]
        assert len(exit_overlays) == 2
        sl = [o for o in exit_overlays if o["type"] == "stop_loss"][0]
        tp = [o for o in exit_overlays if o["type"] == "take_profit"][0]
        assert sl["price"] == 34400.0
        assert tp["price"] == 34700.0

    def test_no_overlay_published_without_zones(self):
        mock_redis = MagicMock()
        publisher = SignalPublisher(redis_client=mock_redis)
        signal = _make_signal(SignalMode.LIVE, with_zones=False)

        publisher.publish(signal)

        mock_redis.publish.assert_not_called()

    def test_overlay_with_autopilot_enabled(self):
        mock_redis = MagicMock()
        mock_monitor = MagicMock()
        mock_monitor.is_enabled.return_value = True
        publisher = SignalPublisher(redis_client=mock_redis, autopilot_monitor=mock_monitor)
        signal = _make_signal(SignalMode.LIVE, with_zones=True)

        publisher.publish(signal, account_id="acc-001")

        # Both xadd (stream) and publish (overlay) should be called
        mock_redis.xadd.assert_called_once()
        mock_redis.publish.assert_called_once()


class TestSignalPublisherEventPublishing:
    """Tests for SignalPublished event integration."""

    def test_signal_published_event_emitted_for_live_signal(self):
        mock_redis = MagicMock()
        mock_event_publisher = MagicMock()
        publisher = SignalPublisher(
            redis_client=mock_redis, event_publisher=mock_event_publisher
        )
        signal = _make_signal(SignalMode.LIVE)

        publisher.publish(signal)

        mock_event_publisher.publish.assert_called_once()
        event = mock_event_publisher.publish.call_args[0][0]
        assert event.event_type == "SignalPublished"
        assert event.aggregate_id == signal.id
        assert event.sequence_number == 2
        assert event.payload["signal_id"] == signal.id
        assert event.payload["instrument"] == "US30"
        assert event.payload["direction"] == "BUY"
        assert "publish_timestamp" in event.payload

    def test_no_event_published_without_event_publisher(self):
        """When no event_publisher is provided, live signals still publish normally."""
        mock_redis = MagicMock()
        publisher = SignalPublisher(redis_client=mock_redis)
        signal = _make_signal(SignalMode.LIVE)

        publisher.publish(signal)

        mock_redis.xadd.assert_called_once()

    def test_no_event_published_for_forward_test_signal(self):
        mock_event_publisher = MagicMock()
        publisher = SignalPublisher(event_publisher=mock_event_publisher)
        signal = _make_signal(SignalMode.FORWARD_TEST)

        publisher.publish(signal)

        mock_event_publisher.publish.assert_not_called()

    def test_no_event_published_for_backtest_signal(self):
        mock_event_publisher = MagicMock()
        publisher = SignalPublisher(event_publisher=mock_event_publisher)
        signal = _make_signal(SignalMode.BACKTEST)

        publisher.publish(signal)

        mock_event_publisher.publish.assert_not_called()

    def test_event_publisher_failure_does_not_block_signal_flow(self):
        mock_redis = MagicMock()
        mock_event_publisher = MagicMock()
        mock_event_publisher.publish.side_effect = Exception("Redis down")
        publisher = SignalPublisher(
            redis_client=mock_redis, event_publisher=mock_event_publisher
        )
        signal = _make_signal(SignalMode.LIVE)

        # Should not raise — fire-and-forget
        publisher.publish(signal)

        # Signal was still published to the stream
        mock_redis.xadd.assert_called_once()

    def test_no_event_when_autopilot_disabled(self):
        mock_redis = MagicMock()
        mock_monitor = MagicMock()
        mock_monitor.is_enabled.return_value = False
        mock_event_publisher = MagicMock()
        publisher = SignalPublisher(
            redis_client=mock_redis,
            autopilot_monitor=mock_monitor,
            event_publisher=mock_event_publisher,
        )
        signal = _make_signal(SignalMode.LIVE)

        publisher.publish(signal, account_id="acc-001")

        # Signal was NOT published to stream, so no event either
        mock_redis.xadd.assert_not_called()
        mock_event_publisher.publish.assert_not_called()
