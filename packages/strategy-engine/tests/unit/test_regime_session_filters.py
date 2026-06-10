"""Tests for RegimeFilter and SessionFilter confluence filters."""

import pytest

from src.models import Candle, SignalDirection
from src.strategy.filters.regime_filter import RegimeFilter, compute_adx, compute_atr_compression
from src.strategy.filters.session_filter import SessionFilter


def _make_candle(open_: float, high: float, low: float, close: float, timestamp: str = "2025-06-10T14:00:00Z") -> Candle:
    return Candle(
        instrument="US30",
        timeframe="1h",
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=1000,
        timestamp=timestamp,
    )


def _make_trending_candles(count: int = 60, start_price: float = 100.0, step: float = 1.5) -> list[Candle]:
    """Create candles with a clear upward trend (high ADX)."""
    candles = []
    price = start_price
    for i in range(count):
        price += step
        candles.append(_make_candle(
            open_=price - 0.5,
            high=price + 2.0,
            low=price - 1.0,
            close=price,
            timestamp=f"2025-06-10T{7 + (i % 12):02d}:00:00Z",
        ))
    return candles


def _make_ranging_candles(count: int = 60, center: float = 100.0, width: float = 1.0) -> list[Candle]:
    """Create candles oscillating around a center (low ADX)."""
    candles = []
    for i in range(count):
        offset = width * (1 if i % 2 == 0 else -1) * 0.5
        price = center + offset
        candles.append(_make_candle(
            open_=price - 0.2,
            high=price + 0.3,
            low=price - 0.3,
            close=price,
            timestamp=f"2025-06-10T{7 + (i % 12):02d}:00:00Z",
        ))
    return candles


class TestRegimeFilter:
    def test_trending_market_passes(self):
        candles = _make_trending_candles(count=60, step=2.0)
        f = RegimeFilter()
        result = f.evaluate(candles, SignalDirection.BUY, f.default_params())
        assert result.passed is True
        assert result.confidence_adjustment >= 0

    def test_ranging_market_blocked(self):
        candles = _make_ranging_candles(count=60, width=0.5)
        f = RegimeFilter()
        result = f.evaluate(candles, SignalDirection.BUY, f.default_params())
        # Ranging market should be penalized (passed=False or negative adjustment)
        assert result.confidence_adjustment < 0

    def test_insufficient_data_passes(self):
        candles = [_make_candle(100, 101, 99, 100)] * 5
        f = RegimeFilter()
        result = f.evaluate(candles, SignalDirection.BUY, f.default_params())
        assert result.passed is True
        assert result.confidence_adjustment == 0.0

    def test_compute_adx_trending(self):
        candles = _make_trending_candles(count=60, step=2.0)
        adx = compute_adx(candles, 14)
        assert adx is not None
        assert adx > 20  # Should indicate a trend

    def test_compute_adx_ranging(self):
        candles = _make_ranging_candles(count=60, width=0.5)
        adx = compute_adx(candles, 14)
        assert adx is not None
        assert adx < 25  # Should indicate no trend

    def test_compute_atr_compression(self):
        candles = _make_ranging_candles(count=60, width=0.5)
        ratio = compute_atr_compression(candles, 14)
        assert ratio is not None
        # Tight range → compression should be around 1.0 (consistent)
        assert 0.5 < ratio < 2.0


class TestSessionFilter:
    def test_overlap_session_bonus(self):
        """London/NY overlap (12-16 UTC) should give bonus."""
        candles = [_make_candle(100, 101, 99, 100, "2025-06-10T14:00:00Z")]
        f = SessionFilter()
        params = {**f.default_params(), "session_instrument": "US30"}
        result = f.evaluate(candles, SignalDirection.BUY, params)
        assert result.passed is True
        assert result.confidence_adjustment > 0

    def test_off_hours_penalty(self):
        """Asian session (hour 3 UTC) should block non-synthetic instruments."""
        candles = [_make_candle(100, 101, 99, 100, "2025-06-10T03:00:00Z")]
        f = SessionFilter()
        params = {**f.default_params(), "session_instrument": "US30"}
        result = f.evaluate(candles, SignalDirection.BUY, params)
        assert result.passed is False
        assert result.confidence_adjustment < 0

    def test_synthetic_bypass(self):
        """Synthetic instruments should bypass session filter entirely."""
        candles = [_make_candle(100, 101, 99, 100, "2025-06-10T03:00:00Z")]
        f = SessionFilter()
        params = {**f.default_params(), "session_instrument": "Volatility_75"}
        result = f.evaluate(candles, SignalDirection.BUY, params)
        assert result.passed is True
        assert result.confidence_adjustment == 0.0

    def test_london_session_active(self):
        """London session (07-16 UTC) should pass."""
        candles = [_make_candle(100, 101, 99, 100, "2025-06-10T09:00:00Z")]
        f = SessionFilter()
        params = {**f.default_params(), "session_instrument": "XAUUSD"}
        result = f.evaluate(candles, SignalDirection.SELL, params)
        assert result.passed is True

    def test_ny_session_active(self):
        """NY session (12-21 UTC) should pass."""
        candles = [_make_candle(100, 101, 99, 100, "2025-06-10T18:00:00Z")]
        f = SessionFilter()
        params = {**f.default_params(), "session_instrument": "US30"}
        result = f.evaluate(candles, SignalDirection.BUY, params)
        assert result.passed is True
