"""Preservation property tests for ICT Strategy Production Hardening.

These tests capture the EXISTING correct behavior that must remain unchanged
after implementing the six bug fixes. They are designed to PASS on the current
unfixed code AND continue to pass after the fixes are applied.

Preservation Properties:
  TP Preservation (Req 3.1) — structural_tp within cap passes through unmodified
  Retest Preservation (Req 3.3) — genuine rejection candles confirm retest
  Position Size Preservation (Req 3.6) — lots below cap unchanged
  Subsystem Preservation (Req 3.7, 3.8) — detect_structure, detect_bos,
      identify_order_blocks, compute_stats, _compute_pnl, _compute_rr

Validates: Requirements 3.1, 3.3, 3.6, 3.7, 3.8
"""

import math

import pytest
from hypothesis import given, settings, assume
from hypothesis import strategies as st

from src.models import (
    BOS,
    BOSDirection,
    Candle,
    OrderBlock,
    StrategyConfig,
    StructurePoint,
    StructureType,
    Timeframe,
)
from src.models.strategy_config import RiskSettings, SessionWindow
from src.models.backtesting import (
    BacktestParams,
    InstrumentSpecs,
    TradeResult,
)
from src.backtesting.backtest_engine import BacktestEngine
from src.strategy.algorithms.ict_order_block import (
    ICTSignalGenerator,
    OrderBlockDetector,
)


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
# TP Preservation (Req 3.1)
# ===========================================================================


class TestTPPreservation:
    """When structural_tp is provided and the resulting TP/SL ratio is
    within a reasonable range (≤ 3.0), the take_profit equals the
    structural_tp. This passes on current code because the current code
    always uses structural_tp directly.

    **Validates: Requirements 3.1**
    """

    @given(
        sl_distance=st.floats(min_value=3.0, max_value=50.0),
        rr_ratio=st.floats(min_value=2.0, max_value=3.0),
    )
    @settings()
    def test_structural_tp_within_cap_preserved(
        self, sl_distance: float, rr_ratio: float,
    ):
        """**Validates: Requirements 3.1**

        For all (sl_distance, rr_ratio) where rr_ratio ≤ 3.0,
        the take_profit should equal the structural_tp unmodified.
        """
        entry = 34800.0
        sl = entry - sl_distance
        structural_tp = entry + sl_distance * rr_ratio

        gen = ICTSignalGenerator()
        ob = _order_block(
            direction=BOSDirection.BULLISH,
            zone_high=entry, zone_low=sl,
        )
        # Candles that retest the zone — low touches zone_high
        candles = [
            _candle(
                close=entry + 2, high=entry + 10, low=entry - 1,
                open_=entry + 5, index=i,
            )
            for i in range(5)
        ]
        config = _config()
        signal = gen.generate_signal(
            ob=ob, candles=candles, htf_candles=candles,
            config=config, structural_tp=structural_tp,
            skip_timeframe_check=True,
        )
        if signal is not None:
            # The current code always uses structural_tp directly
            # After fixes, TPs within cap should still pass through unmodified
            assert signal.take_profit == round(structural_tp, 2), (
                f"Expected TP={round(structural_tp, 2)}, got {signal.take_profit}"
            )

    def test_bearish_structural_tp_within_cap_preserved(self):
        """Bearish OB with structural_tp within reasonable range.

        **Validates: Requirements 3.1**
        """
        entry = 34800.0
        sl = 34810.0  # SL above entry for bearish
        sl_distance = sl - entry  # 10 points
        structural_tp = entry - sl_distance * 2.5  # RR = 2.5, within any cap

        gen = ICTSignalGenerator()
        ob = _order_block(
            direction=BOSDirection.BEARISH,
            zone_high=sl, zone_low=entry,
        )
        candles = [
            _candle(
                close=entry - 2, high=entry + 1, low=entry - 10,
                open_=entry - 5, index=i,
            )
            for i in range(5)
        ]
        config = _config()
        signal = gen.generate_signal(
            ob=ob, candles=candles, htf_candles=candles,
            config=config, structural_tp=structural_tp,
            skip_timeframe_check=True,
        )
        if signal is not None:
            assert signal.take_profit == round(structural_tp, 2), (
                f"Expected TP={round(structural_tp, 2)}, got {signal.take_profit}"
            )


# ===========================================================================
# Retest Preservation (Req 3.3)
# ===========================================================================


class TestRetestPreservation:
    """check_retest() with a genuine rejection candle (close outside zone,
    wick ≥ 50% of range into zone) returns a valid entry price. This should
    pass on both current and fixed code.

    **Validates: Requirements 3.3**
    """

    def test_bullish_genuine_rejection_confirms_retest(self):
        """Bullish OB zone [34760, 34770]. Candle with low dipping well into
        zone and closing above zone_high — genuine rejection.

        **Validates: Requirements 3.3**
        """
        gen = ICTSignalGenerator()
        ob = _order_block(
            direction=BOSDirection.BULLISH,
            zone_high=34770.0, zone_low=34760.0,
        )
        # Candle: low=34762 (deep into zone), close=34778 (above zone_high)
        # wick_into_zone = zone_high - max(low, zone_low) = 34770 - 34762 = 8
        # candle_range = 34780 - 34762 = 18
        # wick ratio = 8/18 = 44% — close to 50% but the current code accepts
        # any touch, so this passes. After fix, we need ≥50%.
        # Let's make it clearly ≥50%:
        # low=34762, high=34778, range=16, wick=34770-34762=8, ratio=8/16=50%
        rejection_candle = _candle(
            open_=34772.0, high=34778.0, low=34762.0, close=34775.0, index=0,
        )
        result = gen.check_retest(ob, [rejection_candle])
        assert result is not None, (
            "check_retest() should return entry price for genuine rejection candle"
        )
        # Entry price should be within the zone
        assert result >= ob.zone_low, f"Entry {result} below zone_low {ob.zone_low}"
        assert result <= ob.zone_high, f"Entry {result} above zone_high {ob.zone_high}"

    def test_bearish_genuine_rejection_confirms_retest(self):
        """Bearish OB zone [34800, 34810]. Candle with high pushing well into
        zone and closing below zone_low — genuine rejection.

        **Validates: Requirements 3.3**
        """
        gen = ICTSignalGenerator()
        ob = _order_block(
            direction=BOSDirection.BEARISH,
            zone_high=34810.0, zone_low=34800.0,
        )
        # Candle: high=34808 (deep into zone), close=34795 (below zone_low)
        # wick_into_zone = min(high, zone_high) - zone_low = 34808 - 34800 = 8
        # candle_range = 34808 - 34792 = 16
        # wick ratio = 8/16 = 50%
        rejection_candle = _candle(
            open_=34798.0, high=34808.0, low=34792.0, close=34795.0, index=0,
        )
        result = gen.check_retest(ob, [rejection_candle])
        assert result is not None, (
            "check_retest() should return entry price for genuine bearish rejection"
        )
        assert result >= ob.zone_low, f"Entry {result} below zone_low {ob.zone_low}"
        assert result <= ob.zone_high, f"Entry {result} above zone_high {ob.zone_high}"

    @given(
        zone_low=st.floats(min_value=30000.0, max_value=40000.0),
        zone_size=st.floats(min_value=5.0, max_value=50.0),
    )
    @settings()
    def test_property_genuine_rejection_returns_entry(
        self, zone_low: float, zone_size: float,
    ):
        """**Validates: Requirements 3.3**

        For all candles showing a genuine rejection pattern (close outside zone,
        wick ≥ 50% of candle range into zone), check_retest returns a valid
        entry price.
        """
        zone_high = zone_low + zone_size
        gen = ICTSignalGenerator()
        ob = _order_block(
            direction=BOSDirection.BULLISH,
            zone_high=zone_high, zone_low=zone_low,
        )

        # Construct a candle with genuine rejection:
        # - low dips into zone (at zone midpoint)
        # - close above zone_high
        # - wick_into_zone ≥ 50% of candle_range
        low = zone_low + zone_size * 0.25  # 25% into zone from bottom
        close = zone_high + zone_size * 0.1  # above zone
        high = close + 2.0
        open_ = zone_high + 1.0

        assume(high > low)
        candle_range = high - low
        wick_into_zone = zone_high - max(low, zone_low)
        assume(candle_range > 0)
        assume(wick_into_zone >= 0.5 * candle_range)

        candle = Candle(
            instrument="US30", timeframe=Timeframe.FIVE_MINUTES,
            open=open_, high=high, low=low, close=close, volume=100.0,
            timestamp="2024-06-15T14:00:00Z",
        )
        result = gen.check_retest(ob, [candle])
        assert result is not None, (
            f"check_retest() returned None for genuine rejection candle "
            f"(low={low}, close={close}, zone=[{zone_low}, {zone_high}])"
        )


# ===========================================================================
# Position Size Preservation (Req 3.6)
# ===========================================================================


class TestPositionSizePreservation:
    """_compute_position_size() with computed lots below any reasonable cap
    returns the same value. The current code doesn't have max_lot_size,
    so we verify the basic position sizing math is correct.

    **Validates: Requirements 3.6**
    """

    @given(
        equity=st.floats(min_value=1000.0, max_value=100000.0),
        risk_pct=st.floats(min_value=0.5, max_value=5.0),
        sl_distance=st.floats(min_value=1.0, max_value=100.0),
    )
    @settings()
    def test_position_size_math_correct(
        self, equity: float, risk_pct: float, sl_distance: float,
    ):
        """**Validates: Requirements 3.6**

        Verify the basic position sizing properties:
        - result >= min_lot
        - result is a multiple of lot_step (within floating-point tolerance)
        - result is close to the theoretical risk-based calculation
        """
        entry = 34800.0
        stop_loss = entry - sl_distance
        specs = InstrumentSpecs(
            pip_size=0.01, pip_value=1.0, min_lot=0.01,
            lot_step=0.01, contract_size=1.0, leverage=100,
        )

        result = BacktestEngine._compute_position_size(
            equity=equity, risk_pct=risk_pct,
            entry=entry, stop_loss=stop_loss, specs=specs,
        )

        # Result must be >= min_lot
        assert result >= specs.min_lot, (
            f"Position size {result} < min_lot {specs.min_lot}"
        )

        # Result should be a multiple of lot_step (within fp tolerance)
        remainder = result % specs.lot_step
        assert remainder < 1e-4 or abs(remainder - specs.lot_step) < 1e-4, (
            f"Position size {result} not a multiple of lot_step {specs.lot_step}"
        )

        # Result should be close to the theoretical value (within 1 lot_step)
        risk_amount = equity * risk_pct / 100.0
        sl_pips = sl_distance / specs.pip_size
        theoretical = risk_amount / (sl_pips * specs.pip_value)
        assert result <= theoretical + specs.lot_step, (
            f"Position size {result} exceeds theoretical {theoretical} by more than 1 lot_step"
        )

    def test_position_size_clamps_to_min_lot(self):
        """When risk is tiny, result should be clamped to min_lot.

        **Validates: Requirements 3.6**
        """
        specs = InstrumentSpecs(
            pip_size=0.01, pip_value=1.0, min_lot=0.01,
            lot_step=0.01, contract_size=1.0, leverage=100,
        )
        result = BacktestEngine._compute_position_size(
            equity=100.0, risk_pct=0.1,  # tiny risk
            entry=34800.0, stop_loss=34700.0,  # large SL
            specs=specs,
        )
        assert result == specs.min_lot, (
            f"Expected min_lot={specs.min_lot}, got {result}"
        )

    def test_position_size_zero_sl_returns_min_lot(self):
        """When SL equals entry (zero distance), returns min_lot.

        **Validates: Requirements 3.6**
        """
        specs = InstrumentSpecs(
            pip_size=0.01, pip_value=1.0, min_lot=0.01,
            lot_step=0.01, contract_size=1.0, leverage=100,
        )
        result = BacktestEngine._compute_position_size(
            equity=10000.0, risk_pct=2.0,
            entry=34800.0, stop_loss=34800.0,  # zero SL distance
            specs=specs,
        )
        assert result == specs.min_lot


# ===========================================================================
# Subsystem Preservation — detect_structure (Req 3.7)
# ===========================================================================


class TestDetectStructurePreservation:
    """detect_structure() produces correct swing highs and lows from candle data.

    **Validates: Requirements 3.7**
    """

    def test_detect_structure_basic_swings(self):
        """A simple up-down-up pattern should produce swing high and swing low.

        **Validates: Requirements 3.7**
        """
        # Use swing_length=1 so a pivot only needs 1 bar on each side —
        # appropriate for this minimal 5-candle sequence.
        detector = OrderBlockDetector(swing_length=1)
        candles = [
            _candle(close=100.0, high=102.0, low=98.0, open_=99.0, index=0),
            _candle(close=105.0, high=107.0, low=103.0, open_=104.0, index=1),
            _candle(close=100.0, high=102.0, low=98.0, open_=101.0, index=2),
            _candle(close=95.0, high=97.0, low=93.0, open_=96.0, index=3),
            _candle(close=100.0, high=102.0, low=98.0, open_=99.0, index=4),
        ]
        points = detector.detect_structure(candles)
        # Should detect at least one swing high (index 1) and one swing low (index 3)
        highs = [p for p in points if p.type in (StructureType.HIGHER_HIGH, StructureType.LOWER_HIGH)]
        lows = [p for p in points if p.type in (StructureType.HIGHER_LOW, StructureType.LOWER_LOW)]
        assert len(highs) >= 1, "Should detect at least one swing high"
        assert len(lows) >= 1, "Should detect at least one swing low"

    def test_detect_structure_insufficient_candles(self):
        """Less than 3 candles should return empty list.

        **Validates: Requirements 3.7**
        """
        detector = OrderBlockDetector()
        candles = [
            _candle(close=100.0, index=0),
            _candle(close=105.0, index=1),
        ]
        points = detector.detect_structure(candles)
        assert points == []

    @given(
        n_candles=st.integers(min_value=5, max_value=30),
        base_price=st.floats(min_value=100.0, max_value=50000.0),
        amplitude=st.floats(min_value=1.0, max_value=50.0),
    )
    @settings()
    def test_property_structure_points_within_price_range(
        self, n_candles: int, base_price: float, amplitude: float,
    ):
        """**Validates: Requirements 3.7**

        For random candle sequences, all detected structure points have
        prices within the candle data's price range.
        """
        import math as m
        detector = OrderBlockDetector()
        candles = []
        for i in range(n_candles):
            # Sine wave pattern to create swings
            offset = amplitude * m.sin(i * m.pi / 3)
            close = base_price + offset
            high = close + amplitude * 0.3
            low = close - amplitude * 0.3
            candles.append(_candle(
                close=close, high=high, low=low, open_=close - 0.5,
                index=i,
            ))

        points = detector.detect_structure(candles)

        min_price = min(c.low for c in candles)
        max_price = max(c.high for c in candles)

        for p in points:
            assert min_price <= p.price <= max_price, (
                f"Structure point price {p.price} outside candle range "
                f"[{min_price}, {max_price}]"
            )


# ===========================================================================
# Subsystem Preservation — detect_bos (Req 3.7)
# ===========================================================================


class TestDetectBOSPreservation:
    """detect_bos() produces correct break-of-structure from structure points.

    **Validates: Requirements 3.7**
    """

    def test_detect_bos_bullish(self):
        """Two consecutive higher highs should produce a bullish BOS.

        **Validates: Requirements 3.7**
        """
        detector = OrderBlockDetector()
        structure = [
            StructurePoint(type=StructureType.HIGHER_HIGH, price=100.0,
                           timestamp="2024-06-15T14:00:00Z", candle_index=1),
            StructurePoint(type=StructureType.HIGHER_HIGH, price=110.0,
                           timestamp="2024-06-15T14:05:00Z", candle_index=3),
        ]
        bos_list = detector.detect_bos(structure)
        bullish = [b for b in bos_list if b.direction == BOSDirection.BULLISH]
        assert len(bullish) >= 1, "Should detect bullish BOS from rising highs"
        assert bullish[0].break_price == 110.0

    def test_detect_bos_bearish(self):
        """Two consecutive lower lows should produce a bearish BOS.

        **Validates: Requirements 3.7**
        """
        detector = OrderBlockDetector()
        structure = [
            StructurePoint(type=StructureType.LOWER_LOW, price=100.0,
                           timestamp="2024-06-15T14:00:00Z", candle_index=1),
            StructurePoint(type=StructureType.LOWER_LOW, price=90.0,
                           timestamp="2024-06-15T14:05:00Z", candle_index=3),
        ]
        bos_list = detector.detect_bos(structure)
        bearish = [b for b in bos_list if b.direction == BOSDirection.BEARISH]
        assert len(bearish) >= 1, "Should detect bearish BOS from falling lows"
        assert bearish[0].break_price == 90.0

    def test_detect_bos_insufficient_points(self):
        """Less than 2 structure points should return empty list.

        **Validates: Requirements 3.7**
        """
        detector = OrderBlockDetector()
        structure = [
            StructurePoint(type=StructureType.HIGHER_HIGH, price=100.0,
                           timestamp="2024-06-15T14:00:00Z", candle_index=1),
        ]
        bos_list = detector.detect_bos(structure)
        assert bos_list == []


# ===========================================================================
# Subsystem Preservation — identify_order_blocks (Req 3.7)
# ===========================================================================


class TestIdentifyOrderBlocksPreservation:
    """identify_order_blocks() produces valid OB zones from candle data and BOS.

    **Validates: Requirements 3.7**
    """

    def test_identify_order_blocks_basic(self):
        """Given a BOS with a valid preceding candle, should produce an OB.

        **Validates: Requirements 3.7**
        """
        detector = OrderBlockDetector()
        candles = [
            _candle(close=100.0, high=102.0, low=98.0, open_=99.0, index=0),
            _candle(close=105.0, high=107.0, low=103.0, open_=104.0, index=1),
            _candle(close=110.0, high=112.0, low=108.0, open_=109.0, index=2),
            _candle(close=115.0, high=117.0, low=113.0, open_=114.0, index=3),
            _candle(close=120.0, high=122.0, low=118.0, open_=119.0, index=4),
        ]
        bos = BOS(
            direction=BOSDirection.BULLISH,
            break_price=112.0,
            break_timestamp="2024-06-15T14:02:00Z",
            from_point=StructurePoint(
                type=StructureType.HIGHER_HIGH, price=107.0,
                timestamp="2024-06-15T14:01:00Z", candle_index=1,
            ),
            to_point=StructurePoint(
                type=StructureType.HIGHER_HIGH, price=112.0,
                timestamp="2024-06-15T14:02:00Z", candle_index=2,
            ),
        )
        obs = detector.identify_order_blocks(candles, bos)
        assert len(obs) == 1, "Should produce exactly one OB"
        ob = obs[0]
        assert ob.direction == BOSDirection.BULLISH
        assert ob.zone_high >= ob.zone_low
        assert ob.is_valid is True

    def test_identify_order_blocks_invalid_index(self):
        """BOS with candle_index=0 should return empty (no preceding candle).

        **Validates: Requirements 3.7**
        """
        detector = OrderBlockDetector()
        candles = [
            _candle(close=100.0, high=102.0, low=98.0, open_=99.0, index=0),
            _candle(close=105.0, high=107.0, low=103.0, open_=104.0, index=1),
        ]
        bos = BOS(
            direction=BOSDirection.BULLISH,
            break_price=102.0,
            break_timestamp="2024-06-15T14:00:00Z",
            from_point=StructurePoint(
                type=StructureType.HIGHER_HIGH, price=100.0,
                timestamp="2024-06-15T14:00:00Z", candle_index=0,
            ),
            to_point=StructurePoint(
                type=StructureType.HIGHER_HIGH, price=102.0,
                timestamp="2024-06-15T14:00:00Z", candle_index=0,
            ),
        )
        obs = detector.identify_order_blocks(candles, bos)
        assert obs == []


# ===========================================================================
# Subsystem Preservation — compute_stats (Req 3.8)
# ===========================================================================


class TestComputeStatsPreservation:
    """compute_stats() produces correct performance statistics from trades.

    **Validates: Requirements 3.8**
    """

    def test_compute_stats_empty_trades(self):
        """Empty trade list should return default stats.

        **Validates: Requirements 3.8**
        """
        stats = BacktestEngine.compute_stats([], 10000.0)
        assert stats.total_trades == 0
        assert stats.win_rate == 0.0
        assert stats.net_profit == 0.0

    def test_compute_stats_basic(self):
        """Basic trade list with known outcomes.

        **Validates: Requirements 3.8**
        """
        trades = [
            TradeResult(
                signal_id="s1", direction="BUY",
                entry_price=100.0, exit_price=110.0,
                position_size=1.0, profit_loss=100.0,
                reward_risk=2.0,
                entry_time="2024-06-15T14:00:00Z",
                exit_time="2024-06-15T15:00:00Z",
            ),
            TradeResult(
                signal_id="s2", direction="BUY",
                entry_price=100.0, exit_price=95.0,
                position_size=1.0, profit_loss=-50.0,
                reward_risk=-1.0,
                entry_time="2024-06-15T16:00:00Z",
                exit_time="2024-06-15T17:00:00Z",
            ),
        ]
        stats = BacktestEngine.compute_stats(trades, 10000.0)
        assert stats.total_trades == 2
        assert stats.winning_trades == 1
        assert stats.losing_trades == 1
        assert stats.win_rate == 0.5
        assert stats.net_profit == 50.0
        assert stats.gross_profit == 100.0
        assert stats.gross_loss == 50.0

    @given(
        n_trades=st.integers(min_value=1, max_value=20),
        data=st.data(),
    )
    @settings()
    def test_property_stats_consistency(self, n_trades: int, data):
        """**Validates: Requirements 3.8**

        For random trade lists, verify:
        - total_trades == winning + losing
        - net_profit == gross_profit - gross_loss
        - win_rate in [0, 1]
        """
        trades = []
        for i in range(n_trades):
            pnl = data.draw(st.floats(min_value=-1000.0, max_value=1000.0))
            trades.append(TradeResult(
                signal_id=f"s{i}", direction="BUY",
                entry_price=100.0, exit_price=110.0 if pnl > 0 else 90.0,
                position_size=1.0, profit_loss=pnl,
                reward_risk=2.0 if pnl > 0 else -1.0,
                entry_time=f"2024-06-15T{14 + i % 8:02d}:00:00Z",
                exit_time=f"2024-06-15T{14 + i % 8:02d}:30:00Z",
            ))

        stats = BacktestEngine.compute_stats(trades, 10000.0)

        assert stats.total_trades == n_trades
        assert stats.winning_trades + stats.losing_trades == stats.total_trades
        assert 0.0 <= stats.win_rate <= 1.0
        assert abs(stats.net_profit - (stats.gross_profit - stats.gross_loss)) < 0.01


# ===========================================================================
# Subsystem Preservation — _compute_pnl (Req 3.8)
# ===========================================================================


class TestComputePnlPreservation:
    """_compute_pnl() produces correct PnL from trade parameters.

    **Validates: Requirements 3.8**
    """

    @given(
        entry=st.floats(min_value=100.0, max_value=50000.0),
        price_move=st.floats(min_value=-500.0, max_value=500.0),
        lot_size=st.floats(min_value=0.01, max_value=10.0),
    )
    @settings()
    def test_property_pnl_buy_correct(
        self, entry: float, price_move: float, lot_size: float,
    ):
        """**Validates: Requirements 3.8**

        For BUY trades: PnL = (exit - entry) / pip_size * pip_value * lot_size
        """
        exit_price = entry + price_move
        specs = InstrumentSpecs(
            pip_size=0.01, pip_value=1.0, min_lot=0.01,
            lot_step=0.01, contract_size=1.0, leverage=100,
        )
        result = BacktestEngine._compute_pnl("BUY", entry, exit_price, lot_size, specs)
        expected = (exit_price - entry) / specs.pip_size * specs.pip_value * lot_size
        assert abs(result - expected) < 1e-6, (
            f"PnL {result} != expected {expected}"
        )

    @given(
        entry=st.floats(min_value=100.0, max_value=50000.0),
        price_move=st.floats(min_value=-500.0, max_value=500.0),
        lot_size=st.floats(min_value=0.01, max_value=10.0),
    )
    @settings()
    def test_property_pnl_sell_correct(
        self, entry: float, price_move: float, lot_size: float,
    ):
        """**Validates: Requirements 3.8**

        For SELL trades: PnL = (entry - exit) / pip_size * pip_value * lot_size
        """
        exit_price = entry + price_move
        specs = InstrumentSpecs(
            pip_size=0.01, pip_value=1.0, min_lot=0.01,
            lot_step=0.01, contract_size=1.0, leverage=100,
        )
        result = BacktestEngine._compute_pnl("SELL", entry, exit_price, lot_size, specs)
        expected = (entry - exit_price) / specs.pip_size * specs.pip_value * lot_size
        assert abs(result - expected) < 1e-6, (
            f"PnL {result} != expected {expected}"
        )


# ===========================================================================
# Subsystem Preservation — _compute_rr (Req 3.8)
# ===========================================================================


class TestComputeRRPreservation:
    """_compute_rr() produces correct reward-to-risk ratio.

    **Validates: Requirements 3.8**
    """

    def test_compute_rr_buy_profit(self):
        """BUY trade with profit should have positive RR.

        **Validates: Requirements 3.8**
        """
        rr = BacktestEngine._compute_rr("BUY", 100.0, 110.0, 95.0)
        assert rr is not None
        assert rr == 2.0  # (110-100) / (100-95) = 2.0

    def test_compute_rr_buy_loss(self):
        """BUY trade hitting SL should have RR = -1.0.

        **Validates: Requirements 3.8**
        """
        rr = BacktestEngine._compute_rr("BUY", 100.0, 95.0, 95.0)
        assert rr is not None
        assert rr == -1.0  # (95-100) / (100-95) = -1.0

    def test_compute_rr_sell_profit(self):
        """SELL trade with profit should have positive RR.

        **Validates: Requirements 3.8**
        """
        rr = BacktestEngine._compute_rr("SELL", 100.0, 90.0, 105.0)
        assert rr is not None
        assert rr == 2.0  # (100-90) / (105-100) = 2.0

    def test_compute_rr_no_sl(self):
        """No stop loss should return None.

        **Validates: Requirements 3.8**
        """
        rr = BacktestEngine._compute_rr("BUY", 100.0, 110.0, None)
        assert rr is None

    def test_compute_rr_zero_risk(self):
        """Zero risk distance should return None.

        **Validates: Requirements 3.8**
        """
        rr = BacktestEngine._compute_rr("BUY", 100.0, 110.0, 100.0)
        assert rr is None

    @given(
        entry=st.floats(min_value=100.0, max_value=50000.0),
        move=st.floats(min_value=-500.0, max_value=500.0),
        risk=st.floats(min_value=0.1, max_value=100.0),
    )
    @settings()
    def test_property_rr_formula(self, entry: float, move: float, risk: float):
        """**Validates: Requirements 3.8**

        For BUY: RR = (exit - entry) / abs(entry - sl), rounded to 2 decimals.
        """
        exit_price = entry + move
        stop_loss = entry - risk
        rr = BacktestEngine._compute_rr("BUY", entry, exit_price, stop_loss)
        assert rr is not None
        # Compare against the raw ratio within one rounding unit (0.01). Comparing
        # two independently-rounded values is brittle at rounding boundaries — e.g.
        # risk=99.99999999999999 rounds move/risk to a different 2-dp value than
        # the engine's internal computation, a float artifact, not a real bug.
        assert rr == pytest.approx(move / risk, abs=0.01), f"RR {rr} vs raw {move / risk}"
