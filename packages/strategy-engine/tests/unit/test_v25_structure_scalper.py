"""Unit tests for V25StructureScalperAlgorithm (Market Structure + Stochastic)."""

import pytest

from src.models import Candle, RiskSettings, SignalDirection, StrategyConfig, Timeframe
from src.strategy.algorithms.v25_structure_scalper import (
    V25StructureScalperAlgorithm,
    _compute_atr,
    _compute_stochastic,
    _establish_bias,
    _find_fractals,
    _classify_swings,
    Bias,
    SwingType,
)


def _candle(close, high=None, low=None, open_=None, index=0, tf=Timeframe.ONE_HOUR, inst="R_25"):
    h = high if high is not None else close + 2
    l = low if low is not None else close - 2
    o = open_ if open_ is not None else close
    return Candle(instrument=inst, timeframe=tf, open=o, high=h, low=l, close=close,
                  volume=100, timestamp=f"2026-06-01T{10+index//60:02d}:{index%60:02d}:00Z")


def _config(**overrides):
    defaults = dict(
        id="test-001", name="Test", algorithm="v25_structure_scalper",
        instruments=["R_25"],
        timeframes=[Timeframe.ONE_HOUR, Timeframe.FIFTEEN_MINUTES],
        higher_timeframe=Timeframe.ONE_HOUR, entry_timeframe=Timeframe.FIFTEEN_MINUTES,
        trend_timeframe=Timeframe.FOUR_HOURS, session_windows=[],
        risk_settings=RiskSettings(
            max_risk_per_trade_pct=10.0, max_daily_loss_pct=25.0,
            max_trailing_drawdown_pct=50.0, max_spread=5.0,
            max_slippage=3.0, volatility_multiplier=3.0,
        ),
        mode="live", min_confidence_score=0.50,
    )
    defaults.update(overrides)
    return StrategyConfig(**defaults)


@pytest.fixture
def algo():
    return V25StructureScalperAlgorithm()


# ── Static methods ───────────────────────────────────────────────

class TestStatic:
    def test_name(self):
        assert V25StructureScalperAlgorithm.name() == "v25_structure_scalper"

    def test_description(self):
        d = V25StructureScalperAlgorithm.description()
        assert "structure" in d.lower() or "Structure" in d

    def test_default_rr(self):
        assert V25StructureScalperAlgorithm.default_params()["reward_risk_ratio"] == 3.0

    def test_has_fractal_params(self):
        p = V25StructureScalperAlgorithm.default_params()
        assert "fractal_n" in p
        assert "structure_lookback" in p

    def test_schema_covers_params(self):
        schema = V25StructureScalperAlgorithm.param_schema()
        for k in V25StructureScalperAlgorithm.default_params():
            if not k.startswith("v25_") and not k.startswith("v10_"):
                assert k in schema, f"Missing schema: {k}"


# ── Market structure ─────────────────────────────────────────────

class TestMarketStructure:
    def _uptrend_candles(self, n=30, base=1000):
        """Create zigzag candles making HH and HL (bullish structure).

        Pattern repeats: up-up-up-DOWN-DOWN, up-up-up-DOWN-DOWN
        Each wave higher than the last → HH and HL.
        """
        candles = []
        # Wave 1: 1000→1040 then pullback to 1020
        wave = [1000, 1010, 1020, 1030, 1040, 1035, 1025, 1020,
                # Wave 2: 1020→1070 then pullback to 1045
                1030, 1040, 1050, 1060, 1070, 1065, 1055, 1045,
                # Wave 3: 1045→1100 then pullback to 1075
                1055, 1065, 1075, 1085, 1100, 1095, 1085, 1075,
                # Wave 4: 1075→1130
                1085, 1095, 1105, 1115, 1130, 1125]
        for i, c in enumerate(wave[:n]):
            candles.append(_candle(c, high=c+8, low=c-8, tf=Timeframe.FOUR_HOURS, index=i))
        return candles

    def _downtrend_candles(self, n=30, base=2000):
        """Zigzag making LH and LL (bearish structure)."""
        wave = [2000, 1990, 1980, 1970, 1960, 1965, 1975, 1980,
                1970, 1960, 1950, 1940, 1930, 1935, 1945, 1955,
                1945, 1935, 1925, 1915, 1900, 1905, 1915, 1925,
                1915, 1905, 1895, 1885, 1870, 1875]
        candles = []
        for i, c in enumerate(wave[:n]):
            candles.append(_candle(c, high=c+8, low=c-8, tf=Timeframe.FOUR_HOURS, index=i))
        return candles

    def test_bullish_bias_detected(self):
        candles = self._uptrend_candles()
        bias, inv, swings = _establish_bias(candles, n=2, lookback=80)
        assert bias == Bias.BULLISH
        assert inv is not None

    def test_bearish_bias_detected(self):
        candles = self._downtrend_candles()
        bias, inv, swings = _establish_bias(candles, n=2, lookback=80)
        assert bias == Bias.BEARISH
        assert inv is not None

    def test_flat_market_no_bias(self):
        candles = [_candle(1000, high=1005, low=995, tf=Timeframe.FOUR_HOURS, index=i) for i in range(30)]
        bias, inv, swings = _establish_bias(candles, n=2, lookback=80)
        assert bias is None

    def test_fractals_detected(self):
        candles = self._uptrend_candles(20)
        swings = _find_fractals(candles, n=2, lookback=50)
        assert len(swings) > 0

    def test_swings_classified(self):
        candles = self._uptrend_candles(30)
        swings = _find_fractals(candles, n=2, lookback=80)
        classified = _classify_swings(swings)
        types = [s.swing_type for s in classified if s.swing_type]
        assert len(types) > 0


# ── Stochastic ───────────────────────────────────────────────────

class TestStochastic:
    def test_range(self):
        h = [float(100+i+5) for i in range(30)]
        l = [float(100+i-5) for i in range(30)]
        c = [float(100+i) for i in range(30)]
        k, d = _compute_stochastic(h, l, c, 14, 3, 3)
        assert all(0 <= v <= 100 for v in k)
        assert all(0 <= v <= 100 for v in d)

    def test_oversold_after_drop(self):
        h, l, c = [], [], []
        for i in range(20):
            v = 100+i*3; h.append(v+2); l.append(v-2); c.append(v)
        peak = c[-1]
        for i in range(10):
            v = peak-(i+1)*5; h.append(v+1); l.append(v-3); c.append(v)
        k, d = _compute_stochastic(h, l, c, 14, 3, 3)
        assert k[-1] < 30

    def test_too_few(self):
        k, d = _compute_stochastic([1,2,3],[0,1,2],[0.5,1.5,2.5],14,3,3)
        assert k == [] and d == []


# ── Anti-clustering ──────────────────────────────────────────────

class TestCooldown:
    def test_empty(self, algo):
        assert algo._is_in_cooldown(1000, 5.0, algo.default_params()) is False

    def test_blocks_nearby(self, algo):
        algo._record_signal(1000, "2026-06-01T10:00:00Z")
        assert algo._is_in_cooldown(1003, 5.0, algo.default_params()) is True

    def test_allows_distant(self, algo):
        algo._record_signal(1000, "2026-06-01T10:00:00Z")
        assert algo._is_in_cooldown(1010, 5.0, algo.default_params()) is False


# ── ATR ──────────────────────────────────────────────────────────

class TestATR:
    def test_positive(self):
        atr = _compute_atr([float(i+2) for i in range(30)], [float(i-2) for i in range(30)],
                           [float(i) for i in range(30)], 14)
        assert len(atr) > 0 and all(v > 0 for v in atr)

    def test_empty_short(self):
        assert _compute_atr([1,2],[0,1],[0.5,1.5],14) == []
