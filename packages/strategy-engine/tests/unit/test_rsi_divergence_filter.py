"""Unit tests for RSIDivergenceFilter."""

import pytest

from src.models import Candle, SignalDirection
from src.strategy.filters.rsi_divergence import RSIDivergenceFilter


def _make_candle(close: float, high: float | None = None, low: float | None = None) -> Candle:
    """Helper to create a candle with the given close price."""
    h = high if high is not None else close + 1.0
    lo = low if low is not None else close - 1.0
    return Candle(
        instrument="R_75",
        timeframe="1h",
        open=close,
        high=h,
        low=lo,
        close=close,
        volume=100.0,
        timestamp="2024-01-01T00:00:00Z",
    )


def _make_candles_from_closes(closes: list[float]) -> list[Candle]:
    return [_make_candle(c) for c in closes]


class TestRSIDivergenceFilterBasics:
    def test_name(self):
        assert RSIDivergenceFilter.name() == "rsi_divergence"

    def test_default_params(self):
        defaults = RSIDivergenceFilter.default_params()
        assert defaults == {"rsi_period": 14, "divergence_lookback": 5, "rsi_bonus": 0.1}

    def test_param_schema(self):
        schema = RSIDivergenceFilter.param_schema()
        assert "rsi_period" in schema
        assert "divergence_lookback" in schema
        assert "rsi_bonus" in schema

    def test_always_passed_true(self):
        """Filter should always return passed=True regardless of divergence."""
        f = RSIDivergenceFilter()
        # Even with insufficient data
        result = f.evaluate([], SignalDirection.BUY, f.default_params())
        assert result.passed is True


class TestRSIComputation:
    def test_rsi_range(self):
        """RSI values should always be in [0, 100]."""
        # Create a series with mixed up/down moves
        closes = [100.0]
        for i in range(30):
            closes.append(closes[-1] + (2.0 if i % 3 != 0 else -3.0))

        rsi_values = RSIDivergenceFilter._compute_rsi(closes, 14)
        for rsi in rsi_values:
            assert 0.0 <= rsi <= 100.0

    def test_rsi_all_gains(self):
        """RSI should be 100 when all moves are gains."""
        closes = [float(i) for i in range(20)]  # 0, 1, 2, ..., 19
        rsi_values = RSIDivergenceFilter._compute_rsi(closes, 5)
        # All gains → RSI should be 100
        for rsi in rsi_values:
            assert rsi == 100.0

    def test_rsi_all_losses(self):
        """RSI should be 0 when all moves are losses."""
        closes = [float(20 - i) for i in range(20)]  # 20, 19, 18, ..., 1
        rsi_values = RSIDivergenceFilter._compute_rsi(closes, 5)
        for rsi in rsi_values:
            assert rsi == 0.0


class TestInsufficientData:
    def test_too_few_candles_for_rsi(self):
        """Should return neutral when not enough candles for RSI."""
        f = RSIDivergenceFilter()
        candles = _make_candles_from_closes([100.0, 101.0, 102.0])
        result = f.evaluate(candles, SignalDirection.BUY, {"rsi_period": 14})
        assert result.passed is True
        assert result.confidence_adjustment == 0.0
        assert "Insufficient" in result.reason

    def test_empty_candles(self):
        f = RSIDivergenceFilter()
        result = f.evaluate([], SignalDirection.BUY, f.default_params())
        assert result.passed is True
        assert result.confidence_adjustment == 0.0


class TestBullishDivergence:
    def test_bullish_divergence_with_buy(self):
        """Bullish divergence (price lower low, RSI higher low) + BUY → +bonus."""
        f = RSIDivergenceFilter()
        params = {"rsi_period": 3, "divergence_lookback": 7, "rsi_bonus": 0.15}

        # Pattern: long uptrend → single sharp drop to 70 (first low, RSI crashes)
        # → recovery → single sharp drop to 69 (lower price, but RSI not as low
        # because it had recovered) → recovery
        closes = [
            50.0, 55.0, 60.0, 65.0, 70.0, 75.0, 80.0, 85.0, 90.0, 95.0, 100.0,
            70.0, 85.0, 95.0, 100.0,  # first trough at 70, then recovery
            69.0, 80.0,               # second trough at 69 (lower price, higher RSI)
        ]
        candles = _make_candles_from_closes(closes)
        result = f.evaluate(candles, SignalDirection.BUY, params)
        assert result.passed is True
        assert result.confidence_adjustment == pytest.approx(0.15)

    def test_bullish_divergence_with_sell(self):
        """Bullish divergence + SELL → -bonus (contradicts)."""
        f = RSIDivergenceFilter()
        params = {"rsi_period": 3, "divergence_lookback": 7, "rsi_bonus": 0.15}
        closes = [
            50.0, 55.0, 60.0, 65.0, 70.0, 75.0, 80.0, 85.0, 90.0, 95.0, 100.0,
            70.0, 85.0, 95.0, 100.0,
            69.0, 80.0,
        ]
        candles = _make_candles_from_closes(closes)
        result = f.evaluate(candles, SignalDirection.SELL, params)
        assert result.passed is True
        assert result.confidence_adjustment == pytest.approx(-0.15)


class TestBearishDivergence:
    def test_bearish_divergence_with_sell(self):
        """Bearish divergence (price higher high, RSI lower high) + SELL → +bonus."""
        f = RSIDivergenceFilter()
        params = {"rsi_period": 3, "divergence_lookback": 7, "rsi_bonus": 0.2}

        # Pattern: long downtrend → single sharp spike to 80 (first high, RSI spikes)
        # → pullback → single sharp spike to 81 (higher price, but RSI not as high
        # because it had pulled back) → pullback
        closes = [
            100.0, 95.0, 90.0, 85.0, 80.0, 75.0, 70.0, 65.0, 60.0, 55.0, 50.0,
            80.0, 65.0, 55.0, 50.0,  # first peak at 80, then pullback
            81.0, 70.0,               # second peak at 81 (higher price, lower RSI)
        ]
        candles = _make_candles_from_closes(closes)
        result = f.evaluate(candles, SignalDirection.SELL, params)
        assert result.passed is True
        assert result.confidence_adjustment == pytest.approx(0.2)

    def test_bearish_divergence_with_buy(self):
        """Bearish divergence + BUY → -bonus (contradicts)."""
        f = RSIDivergenceFilter()
        params = {"rsi_period": 3, "divergence_lookback": 7, "rsi_bonus": 0.2}
        closes = [
            100.0, 95.0, 90.0, 85.0, 80.0, 75.0, 70.0, 65.0, 60.0, 55.0, 50.0,
            80.0, 65.0, 55.0, 50.0,
            81.0, 70.0,
        ]
        candles = _make_candles_from_closes(closes)
        result = f.evaluate(candles, SignalDirection.BUY, params)
        assert result.passed is True
        assert result.confidence_adjustment == pytest.approx(-0.2)


class TestNoDivergence:
    def test_no_divergence_returns_zero(self):
        """When no divergence is detected, adjustment should be 0.0."""
        f = RSIDivergenceFilter()
        params = {"rsi_period": 3, "divergence_lookback": 5, "rsi_bonus": 0.1}
        # Steady uptrend — no divergence
        closes = [float(100 + i) for i in range(20)]
        candles = _make_candles_from_closes(closes)
        result = f.evaluate(candles, SignalDirection.BUY, params)
        assert result.passed is True
        assert result.confidence_adjustment == 0.0


class TestParameterFallback:
    def test_missing_params_use_defaults(self):
        """Missing params should fall back to defaults."""
        f = RSIDivergenceFilter()
        # Pass empty params — should use defaults and not crash
        candles = _make_candles_from_closes([float(100 + i) for i in range(20)])
        result = f.evaluate(candles, SignalDirection.BUY, {})
        assert result.passed is True

    def test_invalid_rsi_period_uses_default(self):
        """Invalid rsi_period should fall back to default."""
        f = RSIDivergenceFilter()
        candles = _make_candles_from_closes([float(100 + i) for i in range(20)])
        result = f.evaluate(candles, SignalDirection.BUY, {"rsi_period": -1})
        assert result.passed is True

    def test_invalid_type_uses_default(self):
        """Non-numeric param should fall back to default."""
        f = RSIDivergenceFilter()
        candles = _make_candles_from_closes([float(100 + i) for i in range(20)])
        result = f.evaluate(candles, SignalDirection.BUY, {"rsi_period": "bad"})
        assert result.passed is True
