"""Unit tests for signal generation — generic helpers + ICT signal generator.

Entry price logic (after fix):
  Bullish OB: entry = zone_high, SL = zone_low - 10% zone_height buffer
  Bearish OB: entry = zone_low,  SL = zone_high + 10% zone_height buffer

For zone=[100, 105] (height=5, buffer=0.5):
  Bullish: entry=105, SL=99.5, risk=5.5
  Bearish: entry=100, SL=105.5, risk=5.5
"""

from datetime import datetime, timezone

import pytest

from src.models import (
    BOSDirection,
    Candle,
    OrderBlock,
    SessionWindow,
    Signal,
    SignalDirection,
    SignalMode,
    StrategyConfig,
    RiskSettings,
    Timeframe,
)
from src.strategy.algorithms.ict_order_block import ICTSignalGenerator
from src.strategy.signal_helpers import (
    NewsWindow,
    check_spread_filter,
    check_volatility_guard,
    check_news_filter,
    check_slippage_tolerance,
    check_session_filter,
)


# ── Helpers ───────────────────────────────────────────────────────


def _candle(
    close: float,
    high: float | None = None,
    low: float | None = None,
    open_: float | None = None,
    index: int = 0,
    timeframe: Timeframe = Timeframe.FIVE_MINUTES,
) -> Candle:
    h = high if high is not None else close + 2
    l = low if low is not None else close - 2
    o = open_ if open_ is not None else close
    return Candle(
        instrument="US30", timeframe=timeframe,
        open=o, high=h, low=l, close=close, volume=100.0,
        timestamp=f"2024-06-15T14:{index:02d}:00Z",
    )


def _order_block(
    direction: BOSDirection = BOSDirection.BULLISH,
    zone_high: float = 105.0,
    zone_low: float = 100.0,
    is_valid: bool = True,
) -> OrderBlock:
    return OrderBlock(
        id="ob-001", instrument="US30", direction=direction,
        zone_high=zone_high, zone_low=zone_low,
        formation_timestamp="2024-06-15T14:00:00Z", is_valid=is_valid,
    )


def _risk_settings(**overrides) -> RiskSettings:
    defaults = dict(
        max_risk_per_trade_pct=1.0, max_daily_loss_pct=5.0,
        max_spread=5.0, max_slippage=2.0, volatility_multiplier=3.0,
    )
    defaults.update(overrides)
    return RiskSettings(**defaults)


def _config(**overrides) -> StrategyConfig:
    defaults = dict(
        id="strat-001", name="US30 OB Strategy", instruments=["US30"],
        timeframes=[Timeframe.ONE_HOUR, Timeframe.FIVE_MINUTES],
        higher_timeframe=Timeframe.ONE_HOUR,
        entry_timeframe=Timeframe.FIVE_MINUTES,
        session_windows=[
            SessionWindow(name="London", start_hour=8, start_minute=0, end_hour=16, end_minute=0),
            SessionWindow(name="New York", start_hour=13, start_minute=0, end_hour=21, end_minute=0),
        ],
        risk_settings=_risk_settings(), mode="live",
    )
    defaults.update(overrides)
    return StrategyConfig(**defaults)


@pytest.fixture
def gen() -> ICTSignalGenerator:
    return ICTSignalGenerator()


# ── Generic guard filter tests (standalone functions) ─────────────


class TestCheckSpreadFilter:
    def test_spread_within_threshold(self):
        assert check_spread_filter(2.0, 5.0) is True

    def test_spread_at_threshold(self):
        assert check_spread_filter(5.0, 5.0) is True

    def test_spread_exceeds_threshold(self):
        assert check_spread_filter(5.1, 5.0) is False

    def test_zero_spread(self):
        assert check_spread_filter(0.0, 5.0) is True


class TestCheckVolatilityGuard:
    def test_stable_volatility_passes(self):
        candles = [_candle(close=100 + i * 0.1, index=i) for i in range(20)]
        assert check_volatility_guard(candles, multiplier=2.0) is True

    def test_spike_in_recent_volatility_fails(self):
        stable = [_candle(close=100.0, index=i) for i in range(16)]
        wild = [
            _candle(close=80.0, index=16), _candle(close=120.0, index=17),
            _candle(close=70.0, index=18), _candle(close=130.0, index=19),
        ]
        assert check_volatility_guard(stable + wild, multiplier=1.0) is False

    def test_few_candles_allows_signal(self):
        candles = [_candle(close=100.0, index=i) for i in range(3)]
        assert check_volatility_guard(candles, multiplier=1.0) is True

    def test_zero_historical_vol_allows(self):
        candles = [_candle(close=100.0, index=i) for i in range(10)]
        assert check_volatility_guard(candles, multiplier=1.0) is True


class TestCheckNewsFilter:
    def test_no_news_windows(self):
        ts = datetime(2024, 6, 15, 14, 30, tzinfo=timezone.utc)
        assert check_news_filter(ts, []) is True

    def test_timestamp_outside_news_window(self):
        ts = datetime(2024, 6, 15, 14, 30, tzinfo=timezone.utc)
        window = NewsWindow(
            start=datetime(2024, 6, 15, 10, 0, tzinfo=timezone.utc),
            end=datetime(2024, 6, 15, 10, 30, tzinfo=timezone.utc),
        )
        assert check_news_filter(ts, [window]) is True

    def test_timestamp_inside_news_window(self):
        ts = datetime(2024, 6, 15, 10, 15, tzinfo=timezone.utc)
        window = NewsWindow(
            start=datetime(2024, 6, 15, 10, 0, tzinfo=timezone.utc),
            end=datetime(2024, 6, 15, 10, 30, tzinfo=timezone.utc),
        )
        assert check_news_filter(ts, [window]) is False

    def test_timestamp_at_window_boundary(self):
        start = datetime(2024, 6, 15, 10, 0, tzinfo=timezone.utc)
        end = datetime(2024, 6, 15, 10, 30, tzinfo=timezone.utc)
        window = NewsWindow(start=start, end=end)
        assert check_news_filter(start, [window]) is False
        assert check_news_filter(end, [window]) is False


class TestCheckSlippageTolerance:
    def test_slippage_within_tolerance(self):
        assert check_slippage_tolerance(1.0, 2.0) is True

    def test_slippage_at_tolerance(self):
        assert check_slippage_tolerance(2.0, 2.0) is True

    def test_slippage_exceeds_tolerance(self):
        assert check_slippage_tolerance(2.1, 2.0) is False

    def test_zero_slippage(self):
        assert check_slippage_tolerance(0.0, 2.0) is True


class TestCheckSessionFilter:
    def test_within_session(self):
        ts = datetime(2024, 6, 15, 14, 30, tzinfo=timezone.utc)
        sessions = [SessionWindow(name="NY", start_hour=13, start_minute=0, end_hour=21, end_minute=0)]
        assert check_session_filter(ts, sessions) is True

    def test_outside_all_sessions(self):
        ts = datetime(2024, 6, 15, 5, 0, tzinfo=timezone.utc)
        sessions = [
            SessionWindow(name="London", start_hour=8, start_minute=0, end_hour=16, end_minute=0),
            SessionWindow(name="NY", start_hour=13, start_minute=0, end_hour=21, end_minute=0),
        ]
        assert check_session_filter(ts, sessions) is False

    def test_no_sessions_configured(self):
        ts = datetime(2024, 6, 15, 14, 30, tzinfo=timezone.utc)
        assert check_session_filter(ts, []) is True

    def test_at_session_boundary(self):
        ts = datetime(2024, 6, 15, 8, 0, tzinfo=timezone.utc)
        sessions = [SessionWindow(name="London", start_hour=8, start_minute=0, end_hour=16, end_minute=0)]
        assert check_session_filter(ts, sessions) is True

    def test_overnight_session(self):
        sessions = [SessionWindow(name="Asia", start_hour=22, start_minute=0, end_hour=6, end_minute=0)]
        ts_late = datetime(2024, 6, 15, 23, 0, tzinfo=timezone.utc)
        ts_early = datetime(2024, 6, 16, 3, 0, tzinfo=timezone.utc)
        ts_outside = datetime(2024, 6, 15, 12, 0, tzinfo=timezone.utc)
        assert check_session_filter(ts_late, sessions) is True
        assert check_session_filter(ts_early, sessions) is True
        assert check_session_filter(ts_outside, sessions) is False


# ── ICT-specific tests (ICTSignalGenerator) ───────────────────────
#
# Zone=[100, 105], height=5, buffer=0.5
#   Bullish: entry=105, SL=99.5, risk=5.5
#   Bearish: entry=100, SL=105.5, risk=5.5
#
# Retest candle requirements (bullish):
#   c.low <= zone_high(105), c.close > zone_high(105)
#   wick_into_zone = zone_high - max(c.low, zone_low) >= 0.5 * (c.high - c.low)
#
# Retest candle requirements (bearish):
#   c.high >= zone_low(100), c.close < zone_low(100)
#   wick_into_zone = min(c.high, zone_high) - zone_low >= 0.5 * (c.high - c.low)


class TestICTConfirmation:
    def _retesting_candles_bullish(self, ob: OrderBlock) -> list[Candle]:
        # zone=[100,105]: low=101, high=109, close=107
        # wick_into_zone = 105 - max(101,100) = 4, range=8, 4>=4 ✓
        return [
            _candle(close=108, high=110, low=107, index=0),
            _candle(close=109, high=111, low=108, index=1),
            _candle(close=107, high=109, low=101, index=2),  # rejection retest
            _candle(close=108, high=110, low=107, index=3),
            _candle(close=109, high=111, low=108, index=4),
        ]

    def test_retest_passes_bullish(self, gen: ICTSignalGenerator):
        ob = _order_block(direction=BOSDirection.BULLISH, zone_high=105, zone_low=100)
        entry = gen.check_retest(ob, self._retesting_candles_bullish(ob))
        assert entry is not None
        # Entry must be zone_high
        assert entry == pytest.approx(105.0)

    def test_no_retest_fails(self, gen: ICTSignalGenerator):
        ob = _order_block(direction=BOSDirection.BULLISH, zone_high=105, zone_low=100)
        candles = [_candle(close=120, high=122, low=118, index=i) for i in range(5)]
        assert gen.check_retest(ob, candles) is None

    def test_bearish_ob_retest_returns_zone_low(self, gen: ICTSignalGenerator):
        ob = _order_block(direction=BOSDirection.BEARISH, zone_high=105, zone_low=100)
        # high=104, low=96, close=97
        # wick_into_zone = min(104,105)-100=4, range=8, 4>=4 ✓
        candles = [
            _candle(close=97, high=104, low=96, index=0),
            _candle(close=94, high=96, low=92, index=1),
        ]
        entry = gen.check_retest(ob, candles)
        assert entry is not None
        # Entry must be zone_low
        assert entry == pytest.approx(100.0)


class TestICTGenerateSignal:
    # zone=[100,105], height=5, buffer=0.5
    # Bullish: entry=105, SL=99.5, risk=5.5, min_rr=2 → TP=105+11=116
    # Bearish: entry=100, SL=105.5, risk=5.5, min_rr=2 → TP=100-11=89

    def _valid_candles_bullish(self) -> list[Candle]:
        # Rejection candle: low=101, high=109, close=107
        return [
            _candle(close=108, high=110, low=107, index=0),
            _candle(close=109, high=111, low=108, index=1),
            _candle(close=107, high=109, low=101, index=2),
            _candle(close=108, high=110, low=107, index=3),
            _candle(close=109, high=111, low=108, index=4),
        ]

    def _valid_candles_bearish(self) -> list[Candle]:
        # Rejection candle: high=104, low=96, close=97
        return [
            _candle(close=94, high=96, low=92, index=0),
            _candle(close=93, high=95, low=91, index=1),
            _candle(close=97, high=104, low=96, index=2),
            _candle(close=94, high=96, low=92, index=3),
            _candle(close=93, high=95, low=91, index=4),
        ]

    def test_generates_buy_signal(self, gen: ICTSignalGenerator):
        ob = _order_block(direction=BOSDirection.BULLISH, zone_high=105, zone_low=100)
        config = _config(algorithm_params={"max_rr_cap": 50.0})
        signal = gen.generate_signal(ob, self._valid_candles_bullish(), self._valid_candles_bullish(), config, spread=1.0, liquidity_confirmed=True, fvg_confirmed=True)
        assert signal is not None
        assert signal.direction == SignalDirection.BUY
        assert signal.instrument == "US30"
        assert signal.order_block_id == "ob-001"
        assert signal.strategy_id == "strat-001"
        assert signal.mode == SignalMode.LIVE
        # Entry at zone_high
        assert signal.entry_price == pytest.approx(105.0)
        # SL below zone_low with 10% buffer
        assert signal.stop_loss == pytest.approx(99.5)

    def test_generates_sell_signal(self, gen: ICTSignalGenerator):
        ob = _order_block(direction=BOSDirection.BEARISH, zone_high=105, zone_low=100)
        config = _config(algorithm_params={"max_rr_cap": 50.0})
        signal = gen.generate_signal(ob, self._valid_candles_bearish(), self._valid_candles_bearish(), config, spread=1.0, liquidity_confirmed=True, fvg_confirmed=True)
        assert signal is not None
        assert signal.direction == SignalDirection.SELL
        # Entry at zone_low
        assert signal.entry_price == pytest.approx(100.0)
        # SL above zone_high with 10% buffer
        assert signal.stop_loss == pytest.approx(105.5)

    def test_returns_none_for_invalid_ob(self, gen: ICTSignalGenerator):
        ob = _order_block(is_valid=False)
        config = _config(algorithm_params={"max_rr_cap": 50.0})
        assert gen.generate_signal(ob, self._valid_candles_bullish(), self._valid_candles_bullish(), config) is None

    def test_returns_none_for_empty_candles(self, gen: ICTSignalGenerator):
        ob = _order_block()
        config = _config(algorithm_params={"max_rr_cap": 50.0})
        assert gen.generate_signal(ob, [], [], config) is None

    def test_returns_none_when_spread_exceeds(self, gen: ICTSignalGenerator):
        ob = _order_block(direction=BOSDirection.BULLISH, zone_high=105, zone_low=100)
        config = _config(algorithm_params={"max_rr_cap": 50.0})
        assert gen.generate_signal(ob, self._valid_candles_bullish(), self._valid_candles_bullish(), config, spread=10.0) is None

    def test_returns_none_when_slippage_exceeds(self, gen: ICTSignalGenerator):
        ob = _order_block(direction=BOSDirection.BULLISH, zone_high=105, zone_low=100)
        config = _config(algorithm_params={"max_rr_cap": 50.0})
        assert gen.generate_signal(ob, self._valid_candles_bullish(), self._valid_candles_bullish(), config, spread=1.0, estimated_slippage=10.0) is None

    def test_returns_none_during_news_window(self, gen: ICTSignalGenerator):
        ob = _order_block(direction=BOSDirection.BULLISH, zone_high=105, zone_low=100)
        config = _config(algorithm_params={"max_rr_cap": 50.0})
        news = [NewsWindow(
            start=datetime(2024, 6, 15, 14, 0, tzinfo=timezone.utc),
            end=datetime(2024, 6, 15, 15, 0, tzinfo=timezone.utc),
        )]
        assert gen.generate_signal(ob, self._valid_candles_bullish(), self._valid_candles_bullish(), config, spread=1.0, news_windows=news) is None

    def test_returns_none_outside_session(self, gen: ICTSignalGenerator):
        ob = _order_block(direction=BOSDirection.BULLISH, zone_high=105, zone_low=100)
        candles = [_candle(close=107, high=109, low=101, index=0)]
        for c in candles:
            c.timestamp = "2024-06-15T03:00:00Z"
        config = _config(algorithm_params={"max_rr_cap": 50.0})
        assert gen.generate_signal(ob, candles, candles, config, spread=1.0) is None

    def test_signal_has_valid_metadata(self, gen: ICTSignalGenerator):
        ob = _order_block(direction=BOSDirection.BULLISH, zone_high=105, zone_low=100)
        config = _config(algorithm_params={"max_rr_cap": 50.0})
        signal = gen.generate_signal(ob, self._valid_candles_bullish(), self._valid_candles_bullish(), config, spread=1.5, liquidity_confirmed=True, fvg_confirmed=True)
        assert signal is not None
        assert signal.metadata.spread_at_generation == 1.5
        assert signal.metadata.bos_type == "bullish"
        assert signal.metadata.session in ("London", "New York")

    def test_confidence_score_reflects_confirmations(self, gen: ICTSignalGenerator):
        ob = _order_block(direction=BOSDirection.BULLISH, zone_high=105, zone_low=100)
        config = _config(algorithm_params={"max_rr_cap": 50.0})
        # With both liquidity + FVG confirmed: 0.5 + 0.2 + 0.15 = 0.85
        sig_full = gen.generate_signal(ob, self._valid_candles_bullish(), self._valid_candles_bullish(), config, spread=1.0, liquidity_confirmed=True, fvg_confirmed=True)
        sig_none = gen.generate_signal(ob, self._valid_candles_bullish(), self._valid_candles_bullish(), config, spread=1.0, liquidity_confirmed=False, fvg_confirmed=False)
        assert sig_full is not None
        assert sig_none is not None
        assert sig_full.confidence_score > sig_none.confidence_score


# ── Structural TP and R:R filtering tests ─────────────────────────
#
# zone=[100,105], height=5, buffer=0.5
# Bullish: entry=105, SL=99.5, risk=5.5
# min_rr=2.0 → fallback TP = 105 + 5.5*2 = 116.0


class TestStructuralTP:
    def _valid_candles(self) -> list[Candle]:
        return [
            _candle(close=108, high=110, low=107, index=0),
            _candle(close=109, high=111, low=108, index=1),
            _candle(close=107, high=109, low=101, index=2),
            _candle(close=108, high=110, low=107, index=3),
            _candle(close=109, high=111, low=108, index=4),
        ]

    def test_fallback_tp_when_no_structural(self, gen: ICTSignalGenerator):
        ob = _order_block(direction=BOSDirection.BULLISH, zone_high=105, zone_low=100)
        config = _config(risk_settings=_risk_settings(), algorithm_params={"max_rr_cap": 50.0})
        signal = gen.generate_signal(ob, self._valid_candles(), self._valid_candles(), config, spread=1.0, liquidity_confirmed=True, fvg_confirmed=True)
        assert signal is not None
        # entry=105, SL=99.5, risk=5.5, min_rr=2.0 → TP=105+11=116.0
        assert signal.take_profit == pytest.approx(116.0)

    def test_structural_tp_used_when_rr_sufficient(self, gen: ICTSignalGenerator):
        ob = _order_block(direction=BOSDirection.BULLISH, zone_high=105, zone_low=100)
        config = _config(risk_settings=_risk_settings(), algorithm_params={"max_rr_cap": 50.0})
        # structural_tp=120 → rr=(120-105)/5.5=2.73 >= 2.0 ✓
        signal = gen.generate_signal(ob, self._valid_candles(), self._valid_candles(), config, spread=1.0, structural_tp=120.0, liquidity_confirmed=True, fvg_confirmed=True)
        assert signal is not None
        assert signal.take_profit == pytest.approx(120.0)

    def test_structural_tp_below_min_rr_falls_back(self, gen: ICTSignalGenerator):
        ob = _order_block(direction=BOSDirection.BULLISH, zone_high=105, zone_low=100)
        config = _config(risk_settings=_risk_settings(), algorithm_params={"max_rr_cap": 50.0})
        # structural_tp=108 → rr=(108-105)/5.5=0.55 < 2.0 → fallback TP=116.0
        signal = gen.generate_signal(ob, self._valid_candles(), self._valid_candles(), config, spread=1.0, structural_tp=108.0, liquidity_confirmed=True, fvg_confirmed=True)
        assert signal is not None
        assert signal.take_profit == pytest.approx(116.0)

    def test_rejects_signal_when_actual_rr_below_min(self, gen: ICTSignalGenerator):
        # Use a zone where risk is very large so TP can't meet min_rr
        # zone=[100,200], height=100, buffer=10
        # entry=200, SL=90, risk=110, min_rr=2 → TP=200+220=420
        # structural_tp=210 → rr=(210-200)/110=0.09 < 2.0 → fallback TP=420
        # actual_rr=(420-200)/110=2.0 >= 2.0 → signal generated
        ob = _order_block(direction=BOSDirection.BULLISH, zone_high=200, zone_low=100)
        candles = [
            _candle(close=207, high=209, low=101, index=0),
            _candle(close=208, high=210, low=207, index=1),
        ]
        config = _config(risk_settings=_risk_settings(min_reward_risk_ratio=2.0), algorithm_params={"max_rr_cap": 50.0})
        signal = gen.generate_signal(ob, candles, candles, config, spread=1.0, structural_tp=210.0, liquidity_confirmed=True, fvg_confirmed=True)
        # fallback TP = 200 + 110*2 = 420, actual_rr = 2.0 → signal generated
        assert signal is not None

    def test_bearish_structural_tp(self, gen: ICTSignalGenerator):
        ob = _order_block(direction=BOSDirection.BEARISH, zone_high=105, zone_low=100)
        # Bearish: entry=100, SL=105.5, risk=5.5
        # structural_tp=89 → rr=(100-89)/5.5=2.0 >= 2.0 ✓
        candles = [
            _candle(close=94, high=96, low=92, index=0),
            _candle(close=93, high=95, low=91, index=1),
            _candle(close=97, high=104, low=96, index=2),
            _candle(close=94, high=96, low=92, index=3),
            _candle(close=93, high=95, low=91, index=4),
        ]
        config = _config(algorithm_params={"max_rr_cap": 50.0})
        signal = gen.generate_signal(ob, candles, candles, config, spread=1.0, structural_tp=89.0, liquidity_confirmed=True, fvg_confirmed=True)
        assert signal is not None
        assert signal.take_profit == pytest.approx(89.0)

    def test_custom_min_rr_from_risk_settings(self, gen: ICTSignalGenerator):
        ob = _order_block(direction=BOSDirection.BULLISH, zone_high=105, zone_low=100)
        config = _config(risk_settings=_risk_settings(min_reward_risk_ratio=3.0), algorithm_params={"max_rr_cap": 50.0})
        # entry=105, SL=99.5, risk=5.5, min_rr=3.0 → fallback TP=105+16.5=121.5
        signal = gen.generate_signal(ob, self._valid_candles(), self._valid_candles(), config, spread=1.0, liquidity_confirmed=True, fvg_confirmed=True)
        assert signal is not None
        assert signal.take_profit == pytest.approx(121.5)
