"""Unit tests for SignalPersister."""

import logging
from unittest.mock import patch, MagicMock

import pytest
import requests

from src.models.signal import (
    BOSType,
    Signal,
    SignalDirection,
    SignalMetadata,
    SignalMode,
)
from src.models.timeframe import Timeframe
from src.pipeline.signal_persister import SignalPersister


def _make_signal() -> Signal:
    """Build a test Signal object."""
    return Signal(
        id="sig-001",
        instrument="US30",
        direction=SignalDirection.BUY,
        entry_price=38505.0,
        stop_loss=38490.0,
        take_profit=38535.0,
        position_size=0.05,
        confidence_score=0.82,
        timeframe=Timeframe.FIFTEEN_MINUTES,
        order_block_id="ob-001",
        strategy_id="strat-001",
        mode=SignalMode.LIVE,
        metadata=SignalMetadata(
            bos_type=BOSType.BULLISH,
            liquidity_swept=True,
            session="new_york",
            spread_at_generation=2.5,
            volatility_ratio=0.65,
        ),
        created_at="2024-01-15T14:30:00+00:00",
    )


class TestSendsCorrectHTTPPost:
    """SignalPersister sends correct HTTP POST with all fields."""

    @patch("src.pipeline.signal_persister.requests.post")
    def test_sends_post_with_all_fields(self, mock_post):
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_post.return_value = mock_response

        persister = SignalPersister(backend_url="http://backend:3000")
        signal = _make_signal()

        # Call _send directly to test synchronously
        persister._send(signal)

        mock_post.assert_called_once()
        call_args = mock_post.call_args
        assert call_args[0][0] == "http://backend:3000/api/signals"

        payload = call_args[1]["json"]
        assert payload["instrument"] == "US30"
        assert payload["direction"] == "BUY"
        assert payload["entryPrice"] == 38505.0
        assert payload["stopLoss"] == 38490.0
        assert payload["takeProfit"] == 38535.0
        assert payload["positionSize"] == 0.05
        assert payload["confidenceScore"] == 0.82
        assert payload["timeframe"] == "15m"
        assert payload["orderBlockId"] == "ob-001"
        assert payload["strategyId"] == "strat-001"
        assert payload["mode"] == "live"
        assert payload["metadata"]["bos_type"] == "bullish"
        assert payload["metadata"]["liquidity_swept"] is True
        assert payload["metadata"]["session"] == "new_york"
        assert payload["metadata"]["spread_at_generation"] == 2.5
        assert payload["metadata"]["volatility_ratio"] == 0.65


class TestLogsErrorAndContinues:
    """SignalPersister logs error and continues when API returns 500."""

    @patch("src.pipeline.signal_persister.requests.post")
    def test_raises_on_500_for_circuit_breaker(self, mock_post):
        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = requests.HTTPError("500 Server Error")
        mock_post.return_value = mock_response

        persister = SignalPersister(backend_url="http://backend:3000")
        signal = _make_signal()

        # _send now raises so the circuit breaker can detect failures
        with pytest.raises(requests.HTTPError):
            persister._send(signal)

    @patch("src.pipeline.signal_persister.requests.post")
    def test_raises_on_connection_failure(self, mock_post):
        mock_post.side_effect = requests.ConnectionError("Connection refused")

        persister = SignalPersister(backend_url="http://backend:3000")
        signal = _make_signal()

        with pytest.raises(requests.ConnectionError):
            persister._send(signal)
