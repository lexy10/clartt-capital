"""Bug condition exploration tests for ICT Strategy Production Hardening.

These tests encode the EXPECTED (fixed) behavior for all 6 bug conditions.
They are designed to FAIL on unfixed code — failure confirms the bugs exist.

Bug Conditions:
  C1 — Uncapped structural TP (TP/SL ratio can reach 731:1)
  C2 — No zone invalidation after SL hit (unlimited re-entries)
  C3 — Weak retest confirmation (any touch accepted)
  C4 — No signal cooldown per zone (rapid-fire entries)
  C5 — No daily loss limit in backtest engine
  C6 — No max position size cap in backtest engine

Validates: Requirements 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 2.1, 2.2, 2.3, 2.4, 2.5, 2.6
"""

import math

import pytest
from hypothesis import given, settings, assume
from hypothesis import strategies as st

from src.models import (
    BOSDirection,
    Candle,
    OrderBlock,
    StrategyConfig,
    Timeframe,
)
from src.models.strategy_config import RiskSettings, SessionWindow
from src.models.backtesting import BacktestParams, InstrumentSpecs
from src.backtesting.backtest_engine import BacktestEngine
from src.strategy.algorithms.ict_order_block import ICTSignalGenerator


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _candle(
    close: float,
    high: float | None = None,
    low: float | None = None,
    open_: float | None = None,
    index: int = 0,
    timeframe: Timeframe = Timeframe.FIVE_MINUTES,
    instrument: str = "US30",
) -> Candle:
    h = high if high is not None else close + 2
    l = low if low is not None else close - 2
    o = open_ if open_ is not None else close
    return Candle(
        instrument=instrument, timeframe=timeframe,
        open=o, high=h, low=l, close=close, volume=100.0,
        timestamp=f"2024-06-15T14:{index:02d}:00Z",
    )


def _order_block(
    direction: BOSDirection = BOSDirection.BULLISH,
    zone_high: float = 105.0,
    zone_low: float = 100.0,
    is_valid: bool = True,
    ob_id: str = "ob-001",
    instrument: str = "US30",
) -> OrderBlock:
    return OrderBlock(
        id=ob_id, instrument=instrument, direction=direction,
        zone_high=zone_high, zone_low=zone_low,
        formation_timestamp="2024-06-15T14:00:00Z", is_valid=is_valid,
    )


def _risk_settings(**overrides) -> RiskSettings:
    defaults = dict(
        max_risk_per_trade_pct=2.0, max_daily_loss_pct=5.0,
        max_spread=5.0, max_slippage=3.0, volatility_multiplier=2.0,
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
        risk_settings=_risk_settings(),
        mode="live",
    )
    defaults.update(overrides)
    return StrategyConfig(**defaults)


# ===========================================================================
# C1 — Uncapped Structural TP
# Validates: Requirements 2.1
# ===========================================================================


class TestC1UncappedTP:
    """Structural TP must be capped at max_rr_cap × SL distance from entry.

    **Validates: Requirements 2.1**
    """

    def test_c1_bullish_tp_capped_at_max_rr(self):
        """Bullish OB with entry=34800, SL=34795, structural_tp=38450.
        TP/SL ratio = (38450-34800)/5 = 730. Must be capped to ≤ 5.0.
        """
        gen = ICTSignalGenerator()
        ob = _order_block(
            direction=BOSDirection.BULLISH,
            zone_high=34800.0, zone_low=34795.0,
        )
        # Candles with genuine rejection pattern:
        # low dips deep into zone, close above zone_high, wick ≥ 50% of range
        # low=34796 (4 pts into 5-pt zone), close=34802 (above zone_high)
        # wick_into_zone = 34800 - max(34796, 34795) = 4
        # candle_range = 34804 - 34796 = 8
        # wick ratio = 4/8 = 50% ✓
        candles = [
            _candle(close=34802.0, high=34804.0, low=34796.0, open_=34801.0, index=i)
            for i in range(5)
        ]
        config = _config(algorithm_params={"max_rr_cap": 5.0})
        signal = gen.generate_signal(
            ob=ob, candles=candles, htf_candles=candles,
            config=config, structural_tp=38450.0,
            skip_timeframe_check=True,
        )
        assert signal is not None, "Signal should be generated"
        sl_distance = signal.entry_price - signal.stop_loss
        assert sl_distance > 0, "SL distance must be positive for bullish"
        tp_distance = signal.take_profit - signal.entry_price
        actual_rr = tp_distance / sl_distance
        assert actual_rr <= 5.0, (
            f"TP/SL ratio {actual_rr:.2f} exceeds max_rr_cap=5.0 — "
            f"TP={signal.take_profit}, entry={signal.entry_price}, SL={signal.stop_loss}"
        )

    @given(
        max_rr_cap=st.floats(min_value=2.0, max_value=10.0),
        sl_distance=st.floats(min_value=1.0, max_value=50.0),
    )
    @settings(max_examples=50)
    def test_c1_property_tp_never_exceeds_cap(self, max_rr_cap: float, sl_distance: float):
        """**Validates: Requirements 2.1**

        For all max_rr_cap in [2.0, 10.0] and sl_distance in [1.0, 50.0],
        when structural_tp exceeds the cap, the actual TP/SL ratio must be ≤ max_rr_cap.
        """
        entry = 34800.0
        sl = entry - sl_distance
        # structural_tp exceeds the cap by 2x
        structural_tp = entry + sl_distance * max_rr_cap * 2.0

        gen = ICTSignalGenerator()
        ob = _order_block(
            direction=BOSDirection.BULLISH,
            zone_high=entry, zone_low=sl,
        )
        candles = [
            _candle(close=entry + 2, high=entry + 10, low=entry - 1, open_=entry + 5, index=i)
            for i in range(5)
        ]
        config = _config(algorithm_params={"max_rr_cap": max_rr_cap})
        signal = gen.generate_signal(
            ob=ob, candles=candles, htf_candles=candles,
            config=config, structural_tp=structural_tp,
            skip_timeframe_check=True,
        )
        if signal is not None:
            actual_sl_dist = abs(signal.entry_price - signal.stop_loss)
            assume(actual_sl_dist > 0)
            tp_dist = abs(signal.take_profit - signal.entry_price)
            actual_rr = tp_dist / actual_sl_dist
            assert actual_rr <= max_rr_cap + 0.01, (
                f"TP/SL ratio {actual_rr:.4f} exceeds max_rr_cap={max_rr_cap}"
            )


# ===========================================================================
# C2 — Zone Re-entry After SL Hit
# Validates: Requirements 2.2
# ===========================================================================


class TestC2ZoneReentry:
    """Zones that had trades hit SL must be invalidated — no further signals.

    **Validates: Requirements 2.2**

    Note: analyze() doesn't have invalidated_zones parameter yet.
    The test calls it WITH the parameter — it will fail with TypeError
    on unfixed code, proving the feature doesn't exist.
    """

    def test_c2_invalidated_zone_produces_no_signal(self):
        """Pass zone ID in invalidated_zones set to analyze().
        Assert no signal generated from that zone.
        """
        from src.strategy.algorithms.ict_order_block import ICTOrderBlockAlgorithm

        algo = ICTOrderBlockAlgorithm()
        config = _config(mode="backtest")

        # Create candles that would normally produce signals
        # Use enough candles for structure detection
        candles = []
        base = 34800.0
        for i in range(30):
            offset = (i % 5) * 2
            candles.append(_candle(
                close=base + offset, high=base + offset + 5,
                low=base + offset - 5, open_=base + offset - 1,
                index=i, timeframe=Timeframe.FIVE_MINUTES,
            ))

        htf_candles = []
        for i in range(30):
            offset = (i % 5) * 5
            htf_candles.append(_candle(
                close=base + offset, high=base + offset + 10,
                low=base + offset - 10, open_=base + offset - 2,
                index=i, timeframe=Timeframe.ONE_HOUR,
            ))

        trend_candles = htf_candles

        invalidated_zones = {"ob-001", "ob-002", "ob-003"}

        # This call should accept invalidated_zones parameter.
        # On unfixed code, it will raise TypeError (unexpected keyword argument).
        signals = algo.analyze(
            entry_candles=candles,
            structure_candles=htf_candles,
            trend_candles=trend_candles,
            config=config,
            invalidated_zones=invalidated_zones,
        )

        # No signal should reference an invalidated zone
        for sig in signals:
            assert sig.order_block_id not in invalidated_zones, (
                f"Signal generated from invalidated zone {sig.order_block_id}"
            )


# ===========================================================================
# C3 — Weak Retest Confirmation
# Validates: Requirements 2.3
# ===========================================================================


class TestC3WeakRetest:
    """Retest must require rejection candle pattern — close outside zone
    AND wick into zone ≥ 50% of candle range.

    **Validates: Requirements 2.3**
    """

    def test_c3_candle_closing_inside_zone_rejected(self):
        """Bullish OB zone [34760, 34770]. Candle with low touching zone
        but closing inside zone (close < zone_high) with tiny wick.
        check_retest() must return None.
        """
        gen = ICTSignalGenerator()
        ob = _order_block(
            direction=BOSDirection.BULLISH,
            zone_high=34770.0, zone_low=34760.0,
        )
        # Candle: touches zone (low=34769 <= zone_high=34770)
        # but closes inside zone (close=34768 < zone_high=34770)
        # tiny wick into zone: zone_high - low = 34770 - 34769 = 1
        # candle range: 34780 - 34769 = 11
        # wick ratio: 1/11 = 9% < 50%
        weak_candle = _candle(
            open_=34772.0, high=34780.0, low=34769.0, close=34768.0, index=0,
        )
        result = gen.check_retest(ob, [weak_candle])
        assert result is None, (
            f"check_retest() returned {result} for a candle closing inside zone "
            f"with tiny wick — should return None (weak retest)"
        )

    def test_c3_candle_with_insufficient_wick_rejected(self):
        """Candle closes outside zone but wick into zone is < 50% of range.
        check_retest() must return None.
        """
        gen = ICTSignalGenerator()
        ob = _order_block(
            direction=BOSDirection.BULLISH,
            zone_high=34770.0, zone_low=34760.0,
        )
        # Candle: low=34769 touches zone, close=34785 (outside zone, above zone_high)
        # wick_into_zone = zone_high - max(low, zone_low) = 34770 - 34769 = 1
        # candle_range = 34790 - 34769 = 21
        # wick ratio = 1/21 = 4.8% < 50%
        weak_candle = _candle(
            open_=34780.0, high=34790.0, low=34769.0, close=34785.0, index=0,
        )
        result = gen.check_retest(ob, [weak_candle])
        assert result is None, (
            f"check_retest() returned {result} for a candle with insufficient wick "
            f"(wick ratio < 50%) — should return None"
        )

    @given(
        zone_low=st.floats(min_value=30000.0, max_value=40000.0),
        zone_size=st.floats(min_value=5.0, max_value=50.0),
        candle_range=st.floats(min_value=5.0, max_value=100.0),
    )
    @settings(max_examples=50)
    def test_c3_property_weak_touch_returns_none(
        self, zone_low: float, zone_size: float, candle_range: float,
    ):
        """**Validates: Requirements 2.3**

        For candles where close is inside zone OR wick_into_zone < 50% of
        candle_range, check_retest must return None.
        """
        zone_high = zone_low + zone_size
        gen = ICTSignalGenerator()
        ob = _order_block(
            direction=BOSDirection.BULLISH,
            zone_high=zone_high, zone_low=zone_low,
        )

        # Create a candle that touches zone but closes inside it
        low = zone_high - 1.0  # touches zone
        high = low + candle_range
        close = zone_high - 0.5  # inside zone (below zone_high)
        open_ = (low + high) / 2.0

        assume(high > low)
        assume(close >= low and close <= high)

        candle = Candle(
            instrument="US30", timeframe=Timeframe.FIVE_MINUTES,
            open=open_, high=high, low=low, close=close, volume=100.0,
            timestamp="2024-06-15T14:00:00Z",
        )
        result = gen.check_retest(ob, [candle])
        assert result is None, (
            f"check_retest() returned {result} for candle closing inside zone "
            f"(close={close} < zone_high={zone_high})"
        )


# ===========================================================================
# C4 — Rapid Signal Cooldown
# Validates: Requirements 2.4
# ===========================================================================


class TestC4RapidSignalCooldown:
    """Zones must have a cooldown period between signals to prevent rapid-fire entries.

    **Validates: Requirements 2.4**

    Note: analyze() doesn't have zone_cooldowns parameter yet.
    The test calls it WITH the parameter — it will fail with TypeError
    on unfixed code, proving the feature doesn't exist.
    """

    def test_c4_zone_in_cooldown_produces_no_signal(self):
        """Pass zone_cooldowns dict with zone ID mapped to recent timestamp.
        Assert no signal from that zone within cooldown window.
        """
        from src.strategy.algorithms.ict_order_block import ICTOrderBlockAlgorithm

        algo = ICTOrderBlockAlgorithm()
        config = _config(
            mode="backtest",
            algorithm_params={"cooldown_candles": 6},
        )

        # Create candles
        candles = []
        base = 34800.0
        for i in range(30):
            offset = (i % 5) * 2
            candles.append(_candle(
                close=base + offset, high=base + offset + 5,
                low=base + offset - 5, open_=base + offset - 1,
                index=i, timeframe=Timeframe.FIVE_MINUTES,
            ))

        htf_candles = []
        for i in range(30):
            offset = (i % 5) * 5
            htf_candles.append(_candle(
                close=base + offset, high=base + offset + 10,
                low=base + offset - 10, open_=base + offset - 2,
                index=i, timeframe=Timeframe.ONE_HOUR,
            ))

        trend_candles = htf_candles

        # Zone cooldowns: all possible zone IDs mapped to very recent timestamp
        # (within the cooldown window of the last candle)
        zone_cooldowns = {
            "ob-001": "2024-06-15T14:28:00Z",
            "ob-002": "2024-06-15T14:28:00Z",
            "ob-003": "2024-06-15T14:28:00Z",
        }

        # This call should accept zone_cooldowns parameter.
        # On unfixed code, it will raise TypeError (unexpected keyword argument).
        signals = algo.analyze(
            entry_candles=candles,
            structure_candles=htf_candles,
            trend_candles=trend_candles,
            config=config,
            zone_cooldowns=zone_cooldowns,
        )

        # No signal should come from a zone that's in cooldown
        for sig in signals:
            assert sig.order_block_id not in zone_cooldowns, (
                f"Signal generated from zone {sig.order_block_id} "
                f"which is within cooldown window"
            )


# ===========================================================================
# C5 — Daily Loss Limit in Backtest
# Validates: Requirements 2.5
# ===========================================================================


class TestC5DailyLossLimit:
    """Backtest engine must stop opening new trades after daily losses
    reach max_daily_loss_pct of the day's starting equity.

    **Validates: Requirements 2.5**
    """

    def test_c5_run_has_no_daily_loss_tracking(self):
        """Verify that BacktestEngine.run() does not contain daily loss
        tracking logic. Inspect the source code for the absence of
        'daily_pnl' or 'day_start_equity' variables.

        On unfixed code, the run() method has no daily loss enforcement,
        so this test will fail — proving the bug exists.
        """
        import inspect
        source = inspect.getsource(BacktestEngine.run)

        # The fixed code should track daily PnL and enforce the limit.
        # Check for the presence of daily loss tracking variables.
        has_daily_tracking = (
            "daily_pnl" in source
            or "day_start_equity" in source
            or "max_daily_loss" in source
        )
        assert has_daily_tracking, (
            "BacktestEngine.run() does not contain daily loss tracking logic "
            "(no 'daily_pnl', 'day_start_equity', or 'max_daily_loss' found in source). "
            "The backtest engine must enforce max_daily_loss_pct from risk settings."
        )


# ===========================================================================
# C6 — Max Position Size Cap
# Validates: Requirements 2.6
# ===========================================================================


class TestC6MaxPositionSize:
    """Position size must be clamped to max_lot_size after risk-based calculation.

    **Validates: Requirements 2.6**
    """

    def test_c6_position_size_clamped_to_max(self):
        """Call _compute_position_size() with equity=$200,000, risk_pct=2.0,
        tight SL (0.51 points), pip_size=0.001, pip_value=1.0, max_lot_size=10.0.
        Result must be ≤ 10.0.

        On unfixed code, _compute_position_size() doesn't accept max_lot_size
        parameter — will fail with TypeError, proving the feature doesn't exist.
        """
        specs = InstrumentSpecs(
            pip_size=0.001, pip_value=1.0, min_lot=0.01,
            lot_step=0.01, contract_size=1.0, leverage=100,
        )
        result = BacktestEngine._compute_position_size(
            equity=200000.0,
            risk_pct=2.0,
            entry=34800.0,
            stop_loss=34799.49,  # 0.51 point SL
            specs=specs,
            max_lot_size=10.0,
        )
        assert result <= 10.0, (
            f"Position size {result} exceeds max_lot_size=10.0 — "
            f"_compute_position_size() must clamp to max_lot_size"
        )

    @given(
        equity=st.floats(min_value=1000.0, max_value=500000.0),
        sl_distance=st.floats(min_value=0.01, max_value=100.0),
    )
    @settings(max_examples=50)
    def test_c6_property_position_size_never_exceeds_max(
        self, equity: float, sl_distance: float,
    ):
        """**Validates: Requirements 2.6**

        For all equity in [1000, 500000] and sl_distance in [0.01, 100.0],
        position size must be ≤ max_lot_size (10.0).
        """
        entry = 34800.0
        stop_loss = entry - sl_distance
        max_lot_size = 10.0
        specs = InstrumentSpecs(
            pip_size=0.001, pip_value=1.0, min_lot=0.01,
            lot_step=0.01, contract_size=1.0, leverage=100,
        )
        result = BacktestEngine._compute_position_size(
            equity=equity,
            risk_pct=2.0,
            entry=entry,
            stop_loss=stop_loss,
            specs=specs,
            max_lot_size=max_lot_size,
        )
        assert result <= max_lot_size, (
            f"Position size {result} exceeds max_lot_size={max_lot_size} "
            f"with equity={equity}, sl_distance={sl_distance}"
        )
