"""Unit tests for OrderBlockDetector."""

import pytest

from src.models import (
    BOS,
    BOSDirection,
    Candle,
    OrderBlock,
    StructurePoint,
    StructureType,
    Timeframe,
)
from src.strategy.algorithms.ict_order_block import OrderBlockDetector


def _candle(
    high: float,
    low: float,
    open_: float | None = None,
    close: float | None = None,
    index: int = 0,
) -> Candle:
    """Helper to create a candle with sensible defaults."""
    return Candle(
        instrument="US30",
        timeframe=Timeframe.FIVE_MINUTES,
        open=open_ if open_ is not None else low,
        high=high,
        low=low,
        close=close if close is not None else high,
        volume=100.0,
        timestamp=f"2024-01-01T00:{index:02d}:00Z",
    )


@pytest.fixture
def detector() -> OrderBlockDetector:
    """Use swing_length=1 so 3-5 candle test data produces pivots.

    The N-bar pivot detection with swing_length=N requires at least
    2*N+1 candles. Using 1 keeps the old 1-bar-each-side behavior
    so existing test data works without needing 11+ candles.
    """
    return OrderBlockDetector(swing_length=1)


# ── detect_structure ──────────────────────────────────────────────


class TestDetectStructure:
    def test_empty_candles(self, detector: OrderBlockDetector):
        assert detector.detect_structure([]) == []

    def test_two_candles_not_enough(self, detector: OrderBlockDetector):
        candles = [_candle(10, 5, index=0), _candle(12, 6, index=1)]
        assert detector.detect_structure(candles) == []

    def test_single_swing_high(self, detector: OrderBlockDetector):
        """Middle candle high > both neighbors → swing high."""
        candles = [
            _candle(high=10, low=5, index=0),
            _candle(high=15, low=6, index=1),
            _candle(high=10, low=5, index=2),
        ]
        points = detector.detect_structure(candles)
        highs = [p for p in points if p.type in (StructureType.HIGHER_HIGH, StructureType.LOWER_HIGH)]
        assert len(highs) == 1
        assert highs[0].price == 15.0
        assert highs[0].candle_index == 1

    def test_single_swing_low(self, detector: OrderBlockDetector):
        """Middle candle low < both neighbors → swing low."""
        candles = [
            _candle(high=10, low=5, index=0),
            _candle(high=9, low=2, index=1),
            _candle(high=10, low=5, index=2),
        ]
        points = detector.detect_structure(candles)
        lows = [p for p in points if p.type in (StructureType.HIGHER_LOW, StructureType.LOWER_LOW)]
        assert len(lows) == 1
        assert lows[0].price == 2.0
        assert lows[0].candle_index == 1

    def test_higher_high_classification(self, detector: OrderBlockDetector):
        """Second swing high above first → HIGHER_HIGH."""
        candles = [
            _candle(high=10, low=5, index=0),
            _candle(high=15, low=6, index=1),  # first swing high
            _candle(high=10, low=5, index=2),
            _candle(high=20, low=7, index=3),  # second swing high, higher
            _candle(high=10, low=5, index=4),
        ]
        points = detector.detect_structure(candles)
        highs = [p for p in points if p.type in (StructureType.HIGHER_HIGH, StructureType.LOWER_HIGH)]
        assert len(highs) == 2
        # First swing high has no predecessor → HIGHER_HIGH (default)
        assert highs[0].type == StructureType.HIGHER_HIGH
        # Second is higher → HIGHER_HIGH
        assert highs[1].type == StructureType.HIGHER_HIGH

    def test_lower_high_classification(self, detector: OrderBlockDetector):
        """Second swing high below first → LOWER_HIGH."""
        candles = [
            _candle(high=10, low=5, index=0),
            _candle(high=20, low=6, index=1),  # first swing high
            _candle(high=10, low=5, index=2),
            _candle(high=15, low=7, index=3),  # second swing high, lower
            _candle(high=10, low=5, index=4),
        ]
        points = detector.detect_structure(candles)
        highs = [p for p in points if p.type in (StructureType.HIGHER_HIGH, StructureType.LOWER_HIGH)]
        assert len(highs) == 2
        assert highs[1].type == StructureType.LOWER_HIGH
        assert highs[1].price == 15.0

    def test_lower_low_classification(self, detector: OrderBlockDetector):
        """Second swing low below first → LOWER_LOW."""
        candles = [
            _candle(high=10, low=5, index=0),
            _candle(high=9, low=3, index=1),   # first swing low
            _candle(high=10, low=5, index=2),
            _candle(high=9, low=1, index=3),   # second swing low, lower
            _candle(high=10, low=5, index=4),
        ]
        points = detector.detect_structure(candles)
        lows = [p for p in points if p.type in (StructureType.HIGHER_LOW, StructureType.LOWER_LOW)]
        assert len(lows) == 2
        assert lows[1].type == StructureType.LOWER_LOW
        assert lows[1].price == 1.0

    def test_higher_low_classification(self, detector: OrderBlockDetector):
        """Second swing low above first → HIGHER_LOW."""
        candles = [
            _candle(high=10, low=2, index=0),
            _candle(high=9, low=1, index=1),   # first swing low
            _candle(high=10, low=5, index=2),
            _candle(high=9, low=3, index=3),   # second swing low, higher
            _candle(high=10, low=5, index=4),
        ]
        points = detector.detect_structure(candles)
        lows = [p for p in points if p.type in (StructureType.HIGHER_LOW, StructureType.LOWER_LOW)]
        assert len(lows) == 2
        assert lows[1].type == StructureType.HIGHER_LOW
        assert lows[1].price == 3.0


# ── detect_bos ────────────────────────────────────────────────────


class TestDetectBOS:
    def test_empty_structure(self, detector: OrderBlockDetector):
        assert detector.detect_bos([]) == []

    def test_single_point_no_bos(self, detector: OrderBlockDetector):
        point = StructurePoint(
            type=StructureType.HIGHER_HIGH,
            price=100.0,
            timestamp="2024-01-01T00:00:00Z",
            candle_index=1,
        )
        assert detector.detect_bos([point]) == []

    def test_bullish_bos(self, detector: OrderBlockDetector):
        """A higher swing high breaking above a previous swing high → bullish BOS."""
        points = [
            StructurePoint(type=StructureType.HIGHER_HIGH, price=100.0, timestamp="2024-01-01T00:01:00Z", candle_index=1),
            StructurePoint(type=StructureType.HIGHER_HIGH, price=110.0, timestamp="2024-01-01T00:03:00Z", candle_index=3),
        ]
        bos_list = detector.detect_bos(points)
        assert len(bos_list) == 1
        assert bos_list[0].direction == BOSDirection.BULLISH
        assert bos_list[0].break_price == 110.0

    def test_bearish_bos(self, detector: OrderBlockDetector):
        """A lower swing low breaking below a previous swing low → bearish BOS."""
        points = [
            StructurePoint(type=StructureType.LOWER_LOW, price=50.0, timestamp="2024-01-01T00:01:00Z", candle_index=1),
            StructurePoint(type=StructureType.LOWER_LOW, price=40.0, timestamp="2024-01-01T00:03:00Z", candle_index=3),
        ]
        bos_list = detector.detect_bos(points)
        assert len(bos_list) == 1
        assert bos_list[0].direction == BOSDirection.BEARISH
        assert bos_list[0].break_price == 40.0

    def test_no_bos_when_highs_descend(self, detector: OrderBlockDetector):
        """Descending swing highs don't produce a bullish BOS."""
        points = [
            StructurePoint(type=StructureType.HIGHER_HIGH, price=110.0, timestamp="2024-01-01T00:01:00Z", candle_index=1),
            StructurePoint(type=StructureType.LOWER_HIGH, price=100.0, timestamp="2024-01-01T00:03:00Z", candle_index=3),
        ]
        bos_list = detector.detect_bos(points)
        bullish = [b for b in bos_list if b.direction == BOSDirection.BULLISH]
        assert len(bullish) == 0

    def test_no_bos_when_lows_ascend(self, detector: OrderBlockDetector):
        """Ascending swing lows don't produce a bearish BOS."""
        points = [
            StructurePoint(type=StructureType.LOWER_LOW, price=40.0, timestamp="2024-01-01T00:01:00Z", candle_index=1),
            StructurePoint(type=StructureType.HIGHER_LOW, price=50.0, timestamp="2024-01-01T00:03:00Z", candle_index=3),
        ]
        bos_list = detector.detect_bos(points)
        bearish = [b for b in bos_list if b.direction == BOSDirection.BEARISH]
        assert len(bearish) == 0

    def test_mixed_structure_produces_both_bos(self, detector: OrderBlockDetector):
        """Mixed structure with both bullish and bearish BOS."""
        points = [
            StructurePoint(type=StructureType.HIGHER_HIGH, price=100.0, timestamp="2024-01-01T00:01:00Z", candle_index=1),
            StructurePoint(type=StructureType.LOWER_LOW, price=50.0, timestamp="2024-01-01T00:02:00Z", candle_index=2),
            StructurePoint(type=StructureType.HIGHER_HIGH, price=120.0, timestamp="2024-01-01T00:03:00Z", candle_index=3),
            StructurePoint(type=StructureType.LOWER_LOW, price=40.0, timestamp="2024-01-01T00:04:00Z", candle_index=4),
        ]
        bos_list = detector.detect_bos(points)
        bullish = [b for b in bos_list if b.direction == BOSDirection.BULLISH]
        bearish = [b for b in bos_list if b.direction == BOSDirection.BEARISH]
        assert len(bullish) == 1
        assert len(bearish) == 1


# ── identify_order_blocks ─────────────────────────────────────────


class TestIdentifyOrderBlocks:
    def test_order_block_from_bullish_bos(self, detector: OrderBlockDetector):
        """Order block zone is defined by the candle before the BOS break candle."""
        candles = [
            _candle(high=10, low=5, index=0),
            _candle(high=15, low=8, index=1),   # swing high
            _candle(high=12, low=7, index=2),    # candle before BOS
            _candle(high=20, low=10, index=3),   # BOS candle
            _candle(high=18, low=9, index=4),
        ]
        bos = BOS(
            direction=BOSDirection.BULLISH,
            break_price=20.0,
            break_timestamp="2024-01-01T00:03:00Z",
            from_point=StructurePoint(type=StructureType.HIGHER_HIGH, price=15.0, timestamp="2024-01-01T00:01:00Z", candle_index=1),
            to_point=StructurePoint(type=StructureType.HIGHER_HIGH, price=20.0, timestamp="2024-01-01T00:03:00Z", candle_index=3),
        )
        obs = detector.identify_order_blocks(candles, bos)
        assert len(obs) == 1
        assert obs[0].zone_high == 12.0  # candle at index 2
        assert obs[0].zone_low == 7.0
        assert obs[0].direction == BOSDirection.BULLISH
        assert obs[0].is_valid is True

    def test_order_block_from_bearish_bos(self, detector: OrderBlockDetector):
        candles = [
            _candle(high=20, low=15, index=0),
            _candle(high=18, low=12, index=1),   # swing low
            _candle(high=19, low=14, index=2),    # candle before BOS
            _candle(high=17, low=8, index=3),     # BOS candle
            _candle(high=16, low=9, index=4),
        ]
        bos = BOS(
            direction=BOSDirection.BEARISH,
            break_price=8.0,
            break_timestamp="2024-01-01T00:03:00Z",
            from_point=StructurePoint(type=StructureType.LOWER_LOW, price=12.0, timestamp="2024-01-01T00:01:00Z", candle_index=1),
            to_point=StructurePoint(type=StructureType.LOWER_LOW, price=8.0, timestamp="2024-01-01T00:03:00Z", candle_index=3),
        )
        obs = detector.identify_order_blocks(candles, bos)
        assert len(obs) == 1
        assert obs[0].zone_high == 19.0
        assert obs[0].zone_low == 14.0
        assert obs[0].direction == BOSDirection.BEARISH

    def test_no_order_block_when_bos_at_start(self, detector: OrderBlockDetector):
        """If BOS is at index 0, there's no preceding candle."""
        candles = [_candle(high=20, low=10, index=0)]
        bos = BOS(
            direction=BOSDirection.BULLISH,
            break_price=20.0,
            break_timestamp="2024-01-01T00:00:00Z",
            from_point=StructurePoint(type=StructureType.HIGHER_HIGH, price=15.0, timestamp="2024-01-01T00:00:00Z", candle_index=0),
            to_point=StructurePoint(type=StructureType.HIGHER_HIGH, price=20.0, timestamp="2024-01-01T00:00:00Z", candle_index=0),
        )
        obs = detector.identify_order_blocks(candles, bos)
        assert obs == []

    def test_order_block_has_instrument(self, detector: OrderBlockDetector):
        candles = [
            _candle(high=10, low=5, index=0),
            _candle(high=20, low=10, index=1),
        ]
        bos = BOS(
            direction=BOSDirection.BULLISH,
            break_price=20.0,
            break_timestamp="2024-01-01T00:01:00Z",
            from_point=StructurePoint(type=StructureType.HIGHER_HIGH, price=10.0, timestamp="2024-01-01T00:00:00Z", candle_index=0),
            to_point=StructurePoint(type=StructureType.HIGHER_HIGH, price=20.0, timestamp="2024-01-01T00:01:00Z", candle_index=1),
        )
        obs = detector.identify_order_blocks(candles, bos)
        assert len(obs) == 1
        assert obs[0].instrument == "US30"


# ── detect_liquidity_sweep ────────────────────────────────────────


class TestDetectLiquiditySweep:
    def test_bullish_sweep_below_level(self, detector: OrderBlockDetector):
        """Price dips below level then closes above → sweep detected (bullish)."""
        candles = [_candle(high=105, low=95, open_=100, close=102)]
        assert detector.detect_liquidity_sweep(candles, level=100.0, direction=BOSDirection.BULLISH) is True

    def test_bearish_sweep_above_level(self, detector: OrderBlockDetector):
        """Price spikes above level then closes below → sweep detected (bearish)."""
        candles = [_candle(high=105, low=95, open_=100, close=98)]
        assert detector.detect_liquidity_sweep(candles, level=100.0, direction=BOSDirection.BEARISH) is True

    def test_no_sweep_when_price_stays_above(self, detector: OrderBlockDetector):
        """Price stays above level → no bullish sweep."""
        candles = [_candle(high=110, low=102, open_=105, close=108)]
        assert detector.detect_liquidity_sweep(candles, level=100.0, direction=BOSDirection.BULLISH) is False

    def test_no_sweep_when_price_stays_below(self, detector: OrderBlockDetector):
        """Price stays below level → no bearish sweep."""
        candles = [_candle(high=98, low=90, open_=95, close=92)]
        assert detector.detect_liquidity_sweep(candles, level=100.0, direction=BOSDirection.BEARISH) is False

    def test_no_sweep_when_close_equals_level(self, detector: OrderBlockDetector):
        """Price dips below but closes exactly at level → no sweep (not reversed past)."""
        candles = [_candle(high=105, low=95, open_=100, close=100)]
        assert detector.detect_liquidity_sweep(candles, level=100.0, direction=BOSDirection.BULLISH) is False

    def test_sweep_detected_in_later_candle(self, detector: OrderBlockDetector):
        """Sweep can be detected in any candle in the list."""
        candles = [
            _candle(high=110, low=102, open_=105, close=108, index=0),  # no sweep
            _candle(high=105, low=95, open_=100, close=102, index=1),   # sweep here
        ]
        assert detector.detect_liquidity_sweep(candles, level=100.0, direction=BOSDirection.BULLISH) is True

    def test_empty_candles_no_sweep(self, detector: OrderBlockDetector):
        assert detector.detect_liquidity_sweep([], level=100.0, direction=BOSDirection.BULLISH) is False

    def test_bullish_direction_ignores_bearish_sweep(self, detector: OrderBlockDetector):
        """Bearish sweep pattern should not match when direction is BULLISH."""
        candles = [_candle(high=105, low=95, open_=100, close=98)]  # bearish pattern
        assert detector.detect_liquidity_sweep(candles, level=100.0, direction=BOSDirection.BULLISH) is False

    def test_bearish_direction_ignores_bullish_sweep(self, detector: OrderBlockDetector):
        """Bullish sweep pattern should not match when direction is BEARISH."""
        candles = [_candle(high=105, low=95, open_=100, close=102)]  # bullish pattern
        assert detector.detect_liquidity_sweep(candles, level=100.0, direction=BOSDirection.BEARISH) is False


# ── find_structural_target ────────────────────────────────────────


class TestFindStructuralTarget:
    def test_bullish_ob_finds_nearest_swing_high(self, detector: OrderBlockDetector):
        """For bullish OB, find the nearest swing high above the zone."""
        structure = [
            StructurePoint(type=StructureType.HIGHER_HIGH, price=90.0, timestamp="t1", candle_index=1),
            StructurePoint(type=StructureType.HIGHER_HIGH, price=115.0, timestamp="t2", candle_index=3),
            StructurePoint(type=StructureType.HIGHER_HIGH, price=130.0, timestamp="t3", candle_index=5),
        ]
        ob = OrderBlock(
            id="ob-1", instrument="US30", direction=BOSDirection.BULLISH,
            zone_high=105.0, zone_low=100.0, formation_timestamp="t0", is_valid=True,
        )
        target = detector.find_structural_target(structure, ob)
        assert target == 115.0  # nearest above zone_high

    def test_bearish_ob_finds_nearest_swing_low(self, detector: OrderBlockDetector):
        """For bearish OB, find the nearest swing low below the zone."""
        structure = [
            StructurePoint(type=StructureType.LOWER_LOW, price=110.0, timestamp="t1", candle_index=1),
            StructurePoint(type=StructureType.LOWER_LOW, price=92.0, timestamp="t2", candle_index=3),
            StructurePoint(type=StructureType.LOWER_LOW, price=80.0, timestamp="t3", candle_index=5),
        ]
        ob = OrderBlock(
            id="ob-1", instrument="US30", direction=BOSDirection.BEARISH,
            zone_high=105.0, zone_low=100.0, formation_timestamp="t0", is_valid=True,
        )
        target = detector.find_structural_target(structure, ob)
        assert target == 92.0  # nearest below zone_low

    def test_no_target_when_no_opposing_levels(self, detector: OrderBlockDetector):
        """Returns None when no swing levels exist beyond the OB zone."""
        structure = [
            StructurePoint(type=StructureType.HIGHER_HIGH, price=95.0, timestamp="t1", candle_index=1),
        ]
        ob = OrderBlock(
            id="ob-1", instrument="US30", direction=BOSDirection.BULLISH,
            zone_high=105.0, zone_low=100.0, formation_timestamp="t0", is_valid=True,
        )
        target = detector.find_structural_target(structure, ob)
        assert target is None

    def test_empty_structure_returns_none(self, detector: OrderBlockDetector):
        ob = OrderBlock(
            id="ob-1", instrument="US30", direction=BOSDirection.BULLISH,
            zone_high=105.0, zone_low=100.0, formation_timestamp="t0", is_valid=True,
        )
        target = detector.find_structural_target([], ob)
        assert target is None
