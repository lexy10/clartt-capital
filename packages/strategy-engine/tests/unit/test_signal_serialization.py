"""Tests verifying signal JSON serialization matches the shared schema.

Ensures that Python Pydantic model serialization produces JSON compatible
with the TypeScript Signal interface consumed by the Backend and Dashboard.
"""

import json

import pytest

from src.models.signal import (
    BOSType,
    Signal,
    SignalDirection,
    SignalMetadata,
    SignalMode,
)
from src.models.timeframe import Timeframe


def _make_signal() -> Signal:
    return Signal(
        id="sig-round-trip-001",
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
        mode=SignalMode.LIVE,
        metadata=SignalMetadata(
            bos_type=BOSType.BULLISH,
            liquidity_swept=True,
            session="new_york",
            spread_at_generation=2.5,
            volatility_ratio=1.1,
        ),
        created_at="2024-01-15T14:30:00Z",
    )


class TestSignalJsonSerialization:
    """Verify model_dump_json() output matches the shared TypeScript schema."""

    def test_serialization_produces_valid_json(self):
        signal = _make_signal()
        json_str = signal.model_dump_json()
        parsed = json.loads(json_str)
        assert isinstance(parsed, dict)

    def test_all_required_fields_present(self):
        signal = _make_signal()
        parsed = json.loads(signal.model_dump_json())

        required_fields = [
            "id", "instrument", "direction", "entry_price", "stop_loss",
            "take_profit", "position_size", "confidence_score", "timeframe",
            "order_block_id", "strategy_id", "mode", "metadata", "created_at",
        ]
        for field in required_fields:
            assert field in parsed, f"Missing required field: {field}"

    def test_direction_serializes_as_string(self):
        signal = _make_signal()
        parsed = json.loads(signal.model_dump_json())
        assert parsed["direction"] == "BUY"

    def test_mode_serializes_as_string(self):
        signal = _make_signal()
        parsed = json.loads(signal.model_dump_json())
        assert parsed["mode"] == "live"

    def test_timeframe_serializes_as_string(self):
        signal = _make_signal()
        parsed = json.loads(signal.model_dump_json())
        assert parsed["timeframe"] == "15m"

    def test_metadata_nested_object(self):
        signal = _make_signal()
        parsed = json.loads(signal.model_dump_json())
        meta = parsed["metadata"]

        assert meta["bos_type"] == "bullish"
        assert meta["liquidity_swept"] is True
        assert meta["session"] == "new_york"
        assert isinstance(meta["spread_at_generation"], (int, float))
        assert isinstance(meta["volatility_ratio"], (int, float))

    def test_round_trip_preserves_all_fields(self):
        """Serialize to JSON and deserialize back — all fields must match."""
        original = _make_signal()
        json_str = original.model_dump_json()
        restored = Signal.model_validate_json(json_str)

        assert restored.id == original.id
        assert restored.instrument == original.instrument
        assert restored.direction == original.direction
        assert restored.entry_price == original.entry_price
        assert restored.stop_loss == original.stop_loss
        assert restored.take_profit == original.take_profit
        assert restored.position_size == original.position_size
        assert restored.confidence_score == original.confidence_score
        assert restored.timeframe == original.timeframe
        assert restored.order_block_id == original.order_block_id
        assert restored.strategy_id == original.strategy_id
        assert restored.mode == original.mode
        assert restored.metadata == original.metadata
        assert restored.created_at == original.created_at

    def test_sell_direction_serialization(self):
        signal = _make_signal()
        signal.direction = SignalDirection.SELL
        parsed = json.loads(signal.model_dump_json())
        assert parsed["direction"] == "SELL"

    def test_all_modes_serialize_correctly(self):
        for mode in SignalMode:
            signal = _make_signal()
            signal.mode = mode
            parsed = json.loads(signal.model_dump_json())
            assert parsed["mode"] == mode.value

    def test_all_timeframes_serialize_correctly(self):
        for tf in Timeframe:
            signal = _make_signal()
            signal.timeframe = tf
            parsed = json.loads(signal.model_dump_json())
            assert parsed["timeframe"] == tf.value

    def test_xadd_payload_format(self):
        """Verify the payload format used by SignalPublisher._publish_live matches expectations."""
        signal = _make_signal()
        payload = signal.model_dump_json()

        # The XADD call uses {"data": payload} — verify the data field is parseable
        xadd_fields = {"data": payload}
        parsed_signal = json.loads(xadd_fields["data"])
        assert parsed_signal["id"] == "sig-round-trip-001"
