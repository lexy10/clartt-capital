"""ICT Order Block algorithm — 3-timeframe model.

Uses three timeframes for proper ICT methodology:
1. Trend TF (e.g. 4H/Daily) — determines overall bias (bullish/bearish)
2. Structure TF (e.g. 1H) — detects pivots, order blocks, FVGs, liquidity levels
3. Entry TF (e.g. 5m/15m) — confirms retest, times entry, places SL/TP

Signal flow:
  Trend TF → bias direction only
  Structure TF → pivot swings aligned with bias → OB zones → FVG confirmation → structural TP
  Entry TF → liquidity sweep → retest confirmation → entry price → signal

Only trades aligned with the trend bias are taken. No counter-trend signals.

Order block detection follows the TradingView Pine Script methodology:
- Proper N-bar pivot detection (swing must be highest/lowest among N bars on each side)
- OB is the candle that touched the swing level (not just "candle before BOS")
- Candle size filter rejects OBs formed by abnormally large candles (> multiplier × median range)
- OBs are invalidated when price closes through the zone
"""

import hashlib
import statistics
from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field

from src.models import (
    BOS,
    BOSDirection,
    Candle,
    OrderBlock,
    Signal,
    SignalDirection,
    StrategyConfig,
    StructurePoint,
    StructureType,
)
from src.strategy.base import StrategyAlgorithm
from src.strategy.signal_helpers import (
    NewsWindow,
    build_signal,
    check_volatility_guard,
    run_guard_filters,
)


# ── New Data Models (ICT Strategy Optimization) ──────────────────


class KillZoneWindow(BaseModel):
    """A single kill zone time window in EST."""
    name: str
    start_hour: int = Field(ge=0, le=23)
    start_minute: int = Field(ge=0, le=59)
    end_hour: int = Field(ge=0, le=23)
    end_minute: int = Field(ge=0, le=59)


class KillZoneResult(BaseModel):
    """Result of kill zone evaluation."""
    in_kill_zone: bool
    matched_zone_name: str | None = None
    confidence_penalty: float = 0.0


class CHOCHEvent(BaseModel):
    """A detected Change of Character or Market Structure Shift."""
    direction: BOSDirection
    is_mss: bool
    break_price: float
    timestamp: str
    candle_index: int


class ZonePosition(str, Enum):
    PREMIUM = "premium"
    DISCOUNT = "discount"
    EQUILIBRIUM = "equilibrium"


class ZoneClassification(BaseModel):
    """Classification of a price relative to the dealing range."""
    position: ZonePosition
    in_ote: bool
    confidence_bonus: float


class DealingRange(BaseModel):
    """The price range between the most recent significant swing high and low."""
    swing_high: float
    swing_low: float
    equilibrium: float
    ote_high: float
    ote_low: float
    premium_threshold: float
    discount_threshold: float


class BreakerBlock(BaseModel):
    """A failed order block acting as support/resistance in the opposite direction."""
    id: str
    instrument: str
    direction: BOSDirection
    zone_high: float
    zone_low: float
    formation_timestamp: str
    is_valid: bool = True
    confidence_bonus: float = 0.1


class OBInvalidationResult(BaseModel):
    """Result of the enhanced OB invalidation process."""
    valid_obs: list[OrderBlock]
    invalidated_obs: list[OrderBlock]
    strong_invalidation_ids: set[str]
    mitigation_counts: dict[str, int]
    age_penalties: dict[str, float]


DEFAULT_KILL_ZONES: list[KillZoneWindow] = [
    KillZoneWindow(name="Asian", start_hour=20, start_minute=0, end_hour=0, end_minute=0),
    KillZoneWindow(name="London", start_hour=2, start_minute=0, end_hour=5, end_minute=0),
    KillZoneWindow(name="New York", start_hour=7, start_minute=0, end_hour=10, end_minute=0),
    KillZoneWindow(name="London Close", start_hour=10, start_minute=0, end_hour=12, end_minute=0),
]


# ── Kill Zone Filter ─────────────────────────────────────────────


class KillZoneFilter:
    """Evaluates whether a timestamp falls within configured ICT kill zones (EST)."""

    def __init__(
        self,
        kill_zones: list[KillZoneWindow],
        mode: str,
        confidence_penalty: float = 0.15,
    ):
        if mode not in ("strict", "soft", "disabled"):
            mode = "disabled"
        self._mode = mode
        self._confidence_penalty = max(0.0, min(0.5, confidence_penalty))

        # Req 1.7: empty list + non-disabled mode → treat as disabled
        if not kill_zones and mode != "disabled":
            self._mode = "disabled"
            self._kill_zones: list[KillZoneWindow] = []
        else:
            self._kill_zones = list(kill_zones)

    # ── timezone helper ──────────────────────────────────────────

    @staticmethod
    def _to_est(timestamp: datetime) -> datetime:
        """Convert a datetime to US/Eastern. Naive datetimes are assumed UTC."""
        from zoneinfo import ZoneInfo

        est = ZoneInfo("US/Eastern")
        if timestamp.tzinfo is None:
            utc = ZoneInfo("UTC")
            timestamp = timestamp.replace(tzinfo=utc)
        return timestamp.astimezone(est)

    # ── core check ───────────────────────────────────────────────

    @staticmethod
    def _time_in_window(hour: int, minute: int, zone: KillZoneWindow) -> bool:
        """Return True if (hour, minute) falls inside the zone window.

        Handles midnight-crossing zones (e.g. 20:00 → 00:00) correctly.
        """
        t = hour * 60 + minute
        start = zone.start_hour * 60 + zone.start_minute
        end = zone.end_hour * 60 + zone.end_minute

        if start <= end:
            # Normal window (e.g. 02:00 → 05:00)
            return start <= t < end
        else:
            # Midnight-crossing window (e.g. 20:00 → 00:00)
            return t >= start or t < end

    def is_in_kill_zone(self, timestamp: datetime) -> bool:
        """Return True if *timestamp* falls within any configured kill zone."""
        if self._mode == "disabled":
            return False

        est_dt = self._to_est(timestamp)
        h, m = est_dt.hour, est_dt.minute

        return any(self._time_in_window(h, m, zone) for zone in self._kill_zones)

    def evaluate(self, timestamp: datetime) -> KillZoneResult:
        """Return a KillZoneResult with in_kill_zone flag and confidence penalty."""
        if self._mode == "disabled":
            return KillZoneResult(
                in_kill_zone=False,
                matched_zone_name=None,
                confidence_penalty=0.0,
            )

        est_dt = self._to_est(timestamp)
        h, m = est_dt.hour, est_dt.minute

        for zone in self._kill_zones:
            if self._time_in_window(h, m, zone):
                return KillZoneResult(
                    in_kill_zone=True,
                    matched_zone_name=zone.name,
                    confidence_penalty=0.0,
                )

        # Outside all kill zones
        if self._mode == "strict":
            return KillZoneResult(
                in_kill_zone=False,
                matched_zone_name=None,
                confidence_penalty=1.0,  # sentinel for rejection
            )

        # soft mode — accept but apply penalty
        return KillZoneResult(
            in_kill_zone=False,
            matched_zone_name=None,
            confidence_penalty=self._confidence_penalty,
        )


# ── CHOCH / MSS Detector ─────────────────────────────────────────


class CHOCHDetector:
    """Detects Change of Character and Market Structure Shift events."""

    def detect(
        self,
        structure_points: list[StructurePoint],
        candles: list[Candle],
        lookback: int = 3,
    ) -> list[CHOCHEvent]:
        """Scan structure points for CHOCH/MSS events.

        A bullish CHOCH: price breaks above the most recent lower high
        in a bearish sequence (containing LOWER_HIGH structure points).
        A bearish CHOCH: price breaks below the most recent higher low
        in a bullish sequence (containing HIGHER_LOW structure points).
        MSS: the breakout candle body covers >= 70% of its total range.
        """
        if not structure_points or not candles:
            return []

        events: list[CHOCHEvent] = []

        # Collect lower highs (bearish sequence markers) and higher lows
        # (bullish sequence markers) from structure points.
        lower_highs = [
            sp for sp in structure_points
            if sp.type == StructureType.LOWER_HIGH
        ]
        higher_lows = [
            sp for sp in structure_points
            if sp.type == StructureType.HIGHER_LOW
        ]

        # --- Bullish CHOCH: break above a recent lower high ---
        # Take the most recent `lookback` lower highs.
        recent_lhs = lower_highs[-lookback:] if lower_highs else []
        for lh in recent_lhs:
            # Find the first candle after this structure point that
            # closes above the lower high price.
            for candle_idx in range(lh.candle_index + 1, len(candles)):
                c = candles[candle_idx]
                if c.close > lh.price:
                    is_mss = self._is_displacement(c)
                    events.append(CHOCHEvent(
                        direction=BOSDirection.BULLISH,
                        is_mss=is_mss,
                        break_price=lh.price,
                        timestamp=c.timestamp,
                        candle_index=candle_idx,
                    ))
                    break  # Only first break per lower high

        # --- Bearish CHOCH: break below a recent higher low ---
        # Take the most recent `lookback` higher lows.
        recent_hls = higher_lows[-lookback:] if higher_lows else []
        for hl in recent_hls:
            for candle_idx in range(hl.candle_index + 1, len(candles)):
                c = candles[candle_idx]
                if c.close < hl.price:
                    is_mss = self._is_displacement(c)
                    events.append(CHOCHEvent(
                        direction=BOSDirection.BEARISH,
                        is_mss=is_mss,
                        break_price=hl.price,
                        timestamp=c.timestamp,
                        candle_index=candle_idx,
                    ))
                    break  # Only first break per higher low

        # Sort by candle index for chronological order.
        events.sort(key=lambda e: e.candle_index)
        return events

    @staticmethod
    def _is_displacement(candle: Candle) -> bool:
        """Return True if the candle is a displacement candle.

        A displacement candle has a body (abs(close - open)) that covers
        at least 70% of the total range (high - low).
        """
        total_range = candle.high - candle.low
        if total_range <= 0:
            return False
        body = abs(candle.close - candle.open)
        return body >= 0.70 * total_range


# ── Zone Filter ───────────────────────────────────────────────────


class ZoneFilter:
    """Computes dealing range and classifies price position relative to it.

    The dealing range spans from the most recent significant swing low
    to the most recent significant swing high. Prices above the 50%
    equilibrium are in the premium zone; below are in the discount zone.
    The OTE (Optimal Trade Entry) zone sits between the 62% and 79%
    Fibonacci retracement levels of the range.
    """

    def compute_dealing_range(
        self,
        pivot_highs: list[StructurePoint],
        pivot_lows: list[StructurePoint],
    ) -> DealingRange | None:
        """Find the most recent significant swing high and low.

        Returns None for empty pivots or a degenerate range where
        swing_high == swing_low.
        """
        if not pivot_highs or not pivot_lows:
            return None

        swing_high = pivot_highs[-1].price
        swing_low = pivot_lows[-1].price

        if swing_high == swing_low:
            return None

        # Ensure swing_high > swing_low; swap if pivots are inverted
        if swing_high < swing_low:
            swing_high, swing_low = swing_low, swing_high

        rng = swing_high - swing_low
        equilibrium = (swing_high + swing_low) / 2.0
        ote_low = swing_low + 0.62 * rng
        ote_high = swing_low + 0.79 * rng

        return DealingRange(
            swing_high=swing_high,
            swing_low=swing_low,
            equilibrium=equilibrium,
            ote_high=ote_high,
            ote_low=ote_low,
            premium_threshold=equilibrium,
            discount_threshold=equilibrium,
        )

    def classify_price(
        self,
        price: float,
        dealing_range: DealingRange,
        direction: BOSDirection | None = None,
    ) -> ZoneClassification:
        """Classify price as premium, discount, or equilibrium.

        The equilibrium tolerance is 0.1% of the dealing range.
        in_ote is True when price falls between ote_low and ote_high.

        confidence_bonus logic:
        - If in OTE and (direction indicates correct zone or direction
          is None): 0.15
        - If in correct zone for the direction but not in OTE: 0.10
        - Otherwise: 0.0

        When direction is None the caller is responsible for applying
        the 0.10 correct-zone bonus separately.
        """
        rng = dealing_range.swing_high - dealing_range.swing_low
        tolerance = 0.001 * rng  # 0.1% of range

        diff = price - dealing_range.equilibrium

        if abs(diff) <= tolerance:
            position = ZonePosition.EQUILIBRIUM
        elif diff > 0:
            position = ZonePosition.PREMIUM
        else:
            position = ZonePosition.DISCOUNT

        in_ote = dealing_range.ote_low <= price <= dealing_range.ote_high

        # Determine confidence bonus
        confidence_bonus = 0.0
        if direction is not None:
            correct_zone = (
                (direction == BOSDirection.BULLISH and position == ZonePosition.DISCOUNT)
                or (direction == BOSDirection.BEARISH and position == ZonePosition.PREMIUM)
            )
            if in_ote and correct_zone:
                confidence_bonus = 0.15
            elif correct_zone:
                confidence_bonus = 0.10
        else:
            # No direction provided — only award OTE bonus
            if in_ote:
                confidence_bonus = 0.15

        return ZoneClassification(
            position=position,
            in_ote=in_ote,
            confidence_bonus=confidence_bonus,
        )

    def should_reject_signal(
        self,
        direction: BOSDirection,
        classification: ZoneClassification,
    ) -> bool:
        """Return True if the signal should be rejected based on zone.

        Reject bullish signals in the premium zone and bearish signals
        in the discount zone. Equilibrium signals are never rejected.
        """
        if direction == BOSDirection.BULLISH and classification.position == ZonePosition.PREMIUM:
            return True
        if direction == BOSDirection.BEARISH and classification.position == ZonePosition.DISCOUNT:
            return True
        return False


# ── Breaker Detector ──────────────────────────────────────────────


class BreakerDetector:
    """Creates and manages breaker blocks from invalidated order blocks.

    When an order block is invalidated (price closes through it), it becomes
    a breaker block acting as support/resistance in the opposite direction.
    """

    def create_breakers(
        self,
        invalidated_obs: list[OrderBlock],
        strong_invalidations: set[str],
    ) -> list[BreakerBlock]:
        """Convert invalidated OBs to breaker blocks with flipped direction.

        Bullish OB → bearish breaker; bearish OB → bullish breaker.
        Strong invalidations (IDs in strong_invalidations) get
        confidence_bonus=0.15, normal get 0.1.
        """
        breakers: list[BreakerBlock] = []
        for ob in invalidated_obs:
            flipped = (
                BOSDirection.BEARISH
                if ob.direction == BOSDirection.BULLISH
                else BOSDirection.BULLISH
            )
            bonus = 0.15 if ob.id in strong_invalidations else 0.1

            breaker_key = f"breaker:{ob.id}:{flipped.value}"
            breaker_id = hashlib.sha256(breaker_key.encode()).hexdigest()[:16]

            breakers.append(BreakerBlock(
                id=breaker_id,
                instrument=ob.instrument,
                direction=flipped,
                zone_high=ob.zone_high,
                zone_low=ob.zone_low,
                formation_timestamp=ob.formation_timestamp,
                is_valid=True,
                confidence_bonus=bonus,
            ))
        return breakers

    def invalidate_broken_breakers(
        self,
        breakers: list[BreakerBlock],
        candles: list[Candle],
    ) -> list[BreakerBlock]:
        """Remove breakers that have been closed through by price.

        Bullish breaker invalid if any candle closes below zone_low.
        Bearish breaker invalid if any candle closes above zone_high.
        Returns only valid breakers.
        """
        valid: list[BreakerBlock] = []
        for bb in breakers:
            if not bb.is_valid:
                continue
            broken = False
            for c in candles:
                if bb.direction == BOSDirection.BULLISH and c.close < bb.zone_low:
                    broken = True
                    break
                if bb.direction == BOSDirection.BEARISH and c.close > bb.zone_high:
                    broken = True
                    break
            if not broken:
                valid.append(bb)
        return valid

    def check_breaker_confluence(
        self,
        entry_price: float,
        direction: BOSDirection,
        breakers: list[BreakerBlock],
        bias: "TrendBias",
    ) -> bool:
        """Return True if entry price retests a valid breaker aligned with bias.

        For bullish bias: check if entry_price is within a valid bullish
        breaker zone. For bearish bias: check within a valid bearish
        breaker zone. The breaker direction must match the bias direction.
        "Within zone" means zone_low <= entry_price <= zone_high.
        """
        if bias.value == "neutral":
            return False

        target_direction = (
            BOSDirection.BULLISH if bias.value == "bullish" else BOSDirection.BEARISH
        )

        for bb in breakers:
            if not bb.is_valid:
                continue
            if bb.direction != target_direction:
                continue
            if bb.zone_low <= entry_price <= bb.zone_high:
                return True
        return False


# ── Confidence Scorer ─────────────────────────────────────────────


class ConfidenceScorer:
    """Computes composite confidence score from all confluence factors."""

    def compute(
        self,
        base: float = 0.5,
        liquidity_confirmed: bool = False,
        fvg_confirmed: bool = False,
        structural_tp_found: bool = False,
        in_kill_zone: bool = False,
        zone_bonus: float = 0.0,
        choch_penalty: bool = False,
        breaker_confluence: bool = False,
        breaker_strong: bool = False,
        ob_partial_mitigation_count: int = 0,
        ob_age_penalty: float = 0.0,
        kill_zone_penalty: float = 0.0,
    ) -> float:
        """Return clamped [0.0, 1.0] confidence score."""
        score = base

        if liquidity_confirmed:
            score += 0.2
        if fvg_confirmed:
            score += 0.15
        if structural_tp_found:
            score += 0.1
        if in_kill_zone:
            score += 0.1

        score += zone_bonus

        if choch_penalty:
            score -= 0.1

        if breaker_confluence:
            score += 0.15 if breaker_strong else 0.1

        score -= 0.05 * ob_partial_mitigation_count
        score -= ob_age_penalty
        score -= kill_zone_penalty

        return max(0.0, min(1.0, score))


# ── OB Invalidation Engine ───────────────────────────────────────


class OBInvalidationEngine:
    """Enhanced OB lifecycle management.

    Runs five invalidation checks on each order block:
    1. Close-through invalidation (existing behavior)
    2. Partial mitigation tracking (3+ mitigations → invalid)
    3. Time-based decay (age penalty + hard cutoff)
    4. Volume-based strong invalidation classification
    5. CHOCH-based invalidation of opposing OBs
    """

    def process(
        self,
        order_blocks: list[OrderBlock],
        candles: list[Candle],
        current_candle_index: int,
        ob_max_age_candles: int = 500,
        choch_events: list[CHOCHEvent] | None = None,
        bias: "TrendBias" = None,
    ) -> OBInvalidationResult:
        """Run all invalidation checks and return result.

        Args:
            order_blocks: Active order blocks to evaluate.
            candles: Full candle series for close-through / mitigation checks.
            current_candle_index: Index of the current (latest) candle.
            ob_max_age_candles: Age threshold before time-decay starts.
            choch_events: Detected CHOCH events for structural invalidation.
            bias: Current trend bias (used only for type reference).

        Returns:
            OBInvalidationResult with valid_obs, invalidated_obs,
            strong_invalidation_ids, mitigation_counts, and age_penalties.
        """
        if choch_events is None:
            choch_events = []

        valid_obs: list[OrderBlock] = []
        invalidated_obs: list[OrderBlock] = []
        strong_invalidation_ids: set[str] = set()
        mitigation_counts: dict[str, int] = {}
        age_penalties: dict[str, float] = {}

        for ob in order_blocks:
            invalidated = False
            close_through_candle_idx: int | None = None

            # ── 1. Close-through invalidation ────────────────────
            for ci in range(len(candles)):
                c = candles[ci]
                if ob.direction == BOSDirection.BULLISH and c.close < ob.zone_low:
                    invalidated = True
                    close_through_candle_idx = ci
                    break
                if ob.direction == BOSDirection.BEARISH and c.close > ob.zone_high:
                    invalidated = True
                    close_through_candle_idx = ci
                    break

            # ── 2. Partial mitigation tracking ───────────────────
            partial_count = ob.partial_mitigation_count
            if not invalidated:
                for c in candles:
                    if ob.direction == BOSDirection.BULLISH:
                        # Candle enters zone: low touches or goes below zone_high
                        # But doesn't close through: close >= zone_low
                        if c.low <= ob.zone_high and c.close >= ob.zone_low:
                            # Must actually enter the zone (not just be above it)
                            if c.low <= ob.zone_high and c.high >= ob.zone_low:
                                partial_count += 1
                    else:
                        # Bearish OB: candle high reaches into zone (>= zone_low)
                        # But doesn't close through: close <= zone_high
                        if c.high >= ob.zone_low and c.close <= ob.zone_high:
                            if c.high >= ob.zone_low and c.low <= ob.zone_high:
                                partial_count += 1

                if partial_count >= 3:
                    invalidated = True

            mitigation_counts[ob.id] = partial_count

            # ── 3. Time-based decay ──────────────────────────────
            age_penalty = 0.0
            if ob.formation_candle_index is not None:
                age = current_candle_index - ob.formation_candle_index
                if age > ob_max_age_candles:
                    excess = age - ob_max_age_candles
                    age_penalty = 0.05 * (excess // 100)
                    if excess >= 500:
                        invalidated = True
            age_penalties[ob.id] = age_penalty

            # ── 4. Volume-based strong invalidation ──────────────
            if close_through_candle_idx is not None:
                closing_candle = candles[close_through_candle_idx]
                closing_volume = closing_candle.volume
                if closing_volume and closing_volume > 0:
                    # Compute median volume of last 50 candles before the closing candle
                    lookback_start = max(0, close_through_candle_idx - 50)
                    volume_window = [
                        candles[i].volume
                        for i in range(lookback_start, close_through_candle_idx)
                        if candles[i].volume and candles[i].volume > 0
                    ]
                    if volume_window:
                        median_vol = statistics.median(volume_window)
                        if median_vol > 0 and closing_volume > 1.5 * median_vol:
                            strong_invalidation_ids.add(ob.id)

            # ── 5. CHOCH-based invalidation ──────────────────────
            if not invalidated:
                for event in choch_events:
                    # Bearish CHOCH invalidates bullish OBs formed before it
                    if (
                        event.direction == BOSDirection.BEARISH
                        and ob.direction == BOSDirection.BULLISH
                    ):
                        if self._ob_formed_before_choch(ob, event):
                            invalidated = True
                            break
                    # Bullish CHOCH invalidates bearish OBs formed before it
                    if (
                        event.direction == BOSDirection.BULLISH
                        and ob.direction == BOSDirection.BEARISH
                    ):
                        if self._ob_formed_before_choch(ob, event):
                            invalidated = True
                            break

            # ── Classify ─────────────────────────────────────────
            if invalidated:
                invalidated_obs.append(ob)
            else:
                valid_obs.append(ob)

        return OBInvalidationResult(
            valid_obs=valid_obs,
            invalidated_obs=invalidated_obs,
            strong_invalidation_ids=strong_invalidation_ids,
            mitigation_counts=mitigation_counts,
            age_penalties=age_penalties,
        )

    @staticmethod
    def _ob_formed_before_choch(ob: OrderBlock, event: CHOCHEvent) -> bool:
        """Check if an OB was formed before a CHOCH event.

        Uses formation_candle_index if available, otherwise falls back
        to timestamp comparison.
        """
        if ob.formation_candle_index is not None:
            return ob.formation_candle_index < event.candle_index
        return ob.formation_timestamp < event.timestamp


# ── Constants ─────────────────────────────────────────────────────

# Default swing detection length — a pivot high must be higher than
# this many bars on each side (and vice versa for pivot low).
DEFAULT_SWING_LENGTH = 5

# How many candles to look back from a swing to find the OB candle.
OB_SEARCH_LOOKBACK = 20

# Max OB candle size as a multiple of median candle range.
DEFAULT_MAX_CANDLE_SIZE_MULTIPLIER = 2.0

# Median candle size calculation lookback.
MEDIAN_LOOKBACK = 50

# Number of recent entry-TF candles to check for a retest.
RETEST_LOOKBACK = 3

# FVG minimum gap size as a fraction of average candle range.
MIN_FVG_RATIO = 0.3

# Minimum trend candles needed to determine bias.
MIN_TREND_CANDLES = 5

# Default trend lookback.
DEFAULT_TREND_LOOKBACK = 50


class TrendBias(str, Enum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"


# ── Pivot Detection ───────────────────────────────────────────────

def detect_pivot_high(candles: list[Candle], index: int, length: int) -> bool:
    """Check if candle at `index` is a pivot high.

    A pivot high must have a higher high than ALL candles within `length`
    bars on both sides. Mirrors the Pine Script pivotHigh() function.
    """
    if index < length or index >= len(candles) - length:
        return False

    pivot_high_val = candles[index].high
    for i in range(1, length + 1):
        if pivot_high_val <= candles[index - i].high:
            return False
        if pivot_high_val <= candles[index + i].high:
            return False
    return True


def detect_pivot_low(candles: list[Candle], index: int, length: int) -> bool:
    """Check if candle at `index` is a pivot low.

    A pivot low must have a lower low than ALL candles within `length`
    bars on both sides. Mirrors the Pine Script pivotLow() function.
    """
    if index < length or index >= len(candles) - length:
        return False

    pivot_low_val = candles[index].low
    for i in range(1, length + 1):
        if pivot_low_val >= candles[index - i].low:
            return False
        if pivot_low_val >= candles[index + i].low:
            return False
    return True


def find_all_pivots(
    candles: list[Candle], swing_length: int
) -> tuple[list[StructurePoint], list[StructurePoint]]:
    """Find all pivot highs and pivot lows in the candle series.

    Returns (pivot_highs, pivot_lows) as lists of StructurePoint.
    Each pivot is classified as HH/LH or HL/LL relative to the previous
    pivot of the same type.
    """
    pivot_highs: list[StructurePoint] = []
    pivot_lows: list[StructurePoint] = []
    last_high: float | None = None
    last_low: float | None = None

    for i in range(swing_length, len(candles) - swing_length):
        if detect_pivot_high(candles, i, swing_length):
            s_type = (
                StructureType.HIGHER_HIGH
                if last_high is None or candles[i].high > last_high
                else StructureType.LOWER_HIGH
            )
            pivot_highs.append(StructurePoint(
                type=s_type,
                price=candles[i].high,
                timestamp=candles[i].timestamp,
                candle_index=i,
            ))
            last_high = candles[i].high

        if detect_pivot_low(candles, i, swing_length):
            s_type = (
                StructureType.HIGHER_LOW
                if last_low is None or candles[i].low > last_low
                else StructureType.LOWER_LOW
            )
            pivot_lows.append(StructurePoint(
                type=s_type,
                price=candles[i].low,
                timestamp=candles[i].timestamp,
                candle_index=i,
            ))
            last_low = candles[i].low

    return pivot_highs, pivot_lows


# ── Candle Size Filter ────────────────────────────────────────────

def compute_median_candle_range(candles: list[Candle], lookback: int = MEDIAN_LOOKBACK) -> float:
    """Compute the median candle range (high - low) over the last `lookback` candles.

    Mirrors the Pine Script getMedianCandleSize() function.
    """
    window = candles[-lookback:] if len(candles) > lookback else candles
    ranges = sorted(c.high - c.low for c in window if (c.high - c.low) > 0)
    if not ranges:
        return 0.0
    mid = len(ranges) // 2
    if len(ranges) % 2 == 0:
        return (ranges[mid - 1] + ranges[mid]) / 2.0
    return ranges[mid]


def is_candle_size_valid(
    candle: Candle,
    median_range: float,
    max_multiplier: float = DEFAULT_MAX_CANDLE_SIZE_MULTIPLIER,
) -> bool:
    """Check if a candle's range is within the allowed size.

    Rejects candles larger than max_multiplier × median_range.
    Mirrors the Pine Script isCandleSizeValid() function.
    """
    candle_range = candle.high - candle.low
    if candle_range <= 0:
        return False
    if median_range <= 0:
        return True  # Can't filter without a valid median
    return candle_range <= max_multiplier * median_range


# ── Trend Analyzer ────────────────────────────────────────────────

class TrendAnalyzer:
    """Determines overall market bias from trend-timeframe candles.

    Uses proper N-bar pivot detection on the highest timeframe.
    Trend is determined by the latest swing structure:
    - Higher high → bullish
    - Lower low → bearish
    - No clear structure → neutral

    This mirrors the Pine Script trend logic where:
    - A new pivot high that's higher than the last → bullish
    - A new pivot low that's lower than the last → bearish
    """

    def determine_bias(
        self,
        candles: list[Candle],
        lookback: int = DEFAULT_TREND_LOOKBACK,
        swing_length: int = DEFAULT_SWING_LENGTH,
    ) -> TrendBias:
        """Analyze trend TF candles and return the overall bias."""
        window = candles[-lookback:] if len(candles) > lookback else candles
        if len(window) < MIN_TREND_CANDLES:
            return TrendBias.NEUTRAL

        pivot_highs, pivot_lows = find_all_pivots(window, swing_length)

        # Mirror Pine Script: track last swing high/low and update trend
        # on each new pivot, keeping the most recent trend determination.
        trend = TrendBias.NEUTRAL
        last_swing_high: float | None = None
        last_swing_low: float | None = None

        # Merge pivots by candle_index to process in chronological order
        all_pivots = []
        for ph in pivot_highs:
            all_pivots.append(("high", ph))
        for pl in pivot_lows:
            all_pivots.append(("low", pl))
        all_pivots.sort(key=lambda x: x[1].candle_index)

        for kind, point in all_pivots:
            if kind == "high":
                if last_swing_high is not None and point.price > last_swing_high:
                    trend = TrendBias.BULLISH
                last_swing_high = point.price
            else:
                if last_swing_low is not None and point.price < last_swing_low:
                    trend = TrendBias.BEARISH
                last_swing_low = point.price

        return trend


# ── Order Block Detector ─────────────────────────────────────────

class OrderBlockDetector:
    """Detects order blocks using proper pivot-based swing detection.

    Follows the Pine Script methodology:
    1. Detect pivots using N-bar confirmation
    2. In bullish trend, find bullish OBs at pivot lows
    3. In bearish trend, find bearish OBs at pivot highs
    4. OB candle is found by searching back from the swing for the
       candle that touched the swing level (with size filter)
    5. OBs are invalidated when price closes through the zone
    """

    def __init__(self, swing_length: int = DEFAULT_SWING_LENGTH):
        self.swing_length = swing_length

    def detect_structure(self, candles: list[Candle]) -> list[StructurePoint]:
        """Identify all pivot highs and lows, returned as a merged sorted list."""
        pivot_highs, pivot_lows = find_all_pivots(candles, self.swing_length)
        all_points = pivot_highs + pivot_lows
        all_points.sort(key=lambda p: p.candle_index)
        return all_points

    def detect_bos(self, structure: list[StructurePoint]) -> list[BOS]:
        """Detect breaks of structure from structure points.

        A bullish BOS occurs when a pivot high exceeds the previous pivot high.
        A bearish BOS occurs when a pivot low breaks below the previous pivot low.
        """
        if len(structure) < 2:
            return []

        bos_list: list[BOS] = []
        highs = [p for p in structure if p.type in (StructureType.HIGHER_HIGH, StructureType.LOWER_HIGH)]
        lows = [p for p in structure if p.type in (StructureType.HIGHER_LOW, StructureType.LOWER_LOW)]

        for i in range(1, len(highs)):
            if highs[i].price > highs[i - 1].price:
                bos_list.append(BOS(
                    direction=BOSDirection.BULLISH,
                    break_price=highs[i].price,
                    break_timestamp=highs[i].timestamp,
                    from_point=highs[i - 1], to_point=highs[i],
                ))

        for i in range(1, len(lows)):
            if lows[i].price < lows[i - 1].price:
                bos_list.append(BOS(
                    direction=BOSDirection.BEARISH,
                    break_price=lows[i].price,
                    break_timestamp=lows[i].timestamp,
                    from_point=lows[i - 1], to_point=lows[i],
                ))

        bos_list.sort(key=lambda b: b.to_point.candle_index)
        return bos_list

    def find_ob_candle_at_swing(
        self,
        candles: list[Candle],
        swing_index: int,
        swing_price: float,
        direction: BOSDirection,
        median_range: float,
        max_candle_multiplier: float = DEFAULT_MAX_CANDLE_SIZE_MULTIPLIER,
    ) -> Candle | None:
        """Search back from a swing to find the OB candle.

        Mirrors the Pine Script findBullishOB / findBearishOB functions:
        - For bullish OB: look back from the pivot low to find the last candle
          whose low touched or went below the swing low price.
        - For bearish OB: look back from the pivot high to find the last candle
          whose high touched or exceeded the swing high price.
        - The candle must pass the size filter.

        Returns the OB candle or None if not found.
        """
        for offset in range(1, OB_SEARCH_LOOKBACK + 1):
            idx = swing_index - offset
            if idx < 0:
                break

            c = candles[idx]

            if direction == BOSDirection.BULLISH:
                # Bullish OB: candle's low must touch or go below the swing low
                if c.low <= swing_price:
                    if is_candle_size_valid(c, median_range, max_candle_multiplier):
                        return c
            else:
                # Bearish OB: candle's high must touch or exceed the swing high
                if c.high >= swing_price:
                    if is_candle_size_valid(c, median_range, max_candle_multiplier):
                        return c

        return None

    def identify_order_blocks_at_pivots(
        self,
        candles: list[Candle],
        pivot_highs: list[StructurePoint],
        pivot_lows: list[StructurePoint],
        bias: TrendBias,
        median_range: float,
        max_candle_multiplier: float = DEFAULT_MAX_CANDLE_SIZE_MULTIPLIER,
    ) -> list[OrderBlock]:
        """Identify order blocks at pivot swing points.

        Mirrors the Pine Script main logic:
        - In BULLISH trend: mark bullish OBs at pivot lows
        - In BEARISH trend: mark bearish OBs at pivot highs

        Each OB is the candle that touched the swing level, found by
        searching backwards with a size filter.
        """
        order_blocks: list[OrderBlock] = []

        if bias == TrendBias.BULLISH:
            for pivot in pivot_lows:
                ob_candle = self.find_ob_candle_at_swing(
                    candles, pivot.candle_index, pivot.price,
                    BOSDirection.BULLISH, median_range, max_candle_multiplier,
                )
                if ob_candle is not None:
                    ob_key = f"{ob_candle.instrument}:bullish:{ob_candle.high}:{ob_candle.low}:{ob_candle.timestamp}"
                    ob_id = hashlib.sha256(ob_key.encode()).hexdigest()[:16]
                    order_blocks.append(OrderBlock(
                        id=ob_id,
                        instrument=ob_candle.instrument,
                        direction=BOSDirection.BULLISH,
                        zone_high=ob_candle.high,
                        zone_low=ob_candle.low,
                        formation_timestamp=ob_candle.timestamp,
                        bos_id=None,
                        is_valid=True,
                    ))

        elif bias == TrendBias.BEARISH:
            for pivot in pivot_highs:
                ob_candle = self.find_ob_candle_at_swing(
                    candles, pivot.candle_index, pivot.price,
                    BOSDirection.BEARISH, median_range, max_candle_multiplier,
                )
                if ob_candle is not None:
                    ob_key = f"{ob_candle.instrument}:bearish:{ob_candle.high}:{ob_candle.low}:{ob_candle.timestamp}"
                    ob_id = hashlib.sha256(ob_key.encode()).hexdigest()[:16]
                    order_blocks.append(OrderBlock(
                        id=ob_id,
                        instrument=ob_candle.instrument,
                        direction=BOSDirection.BEARISH,
                        zone_high=ob_candle.high,
                        zone_low=ob_candle.low,
                        formation_timestamp=ob_candle.timestamp,
                        bos_id=None,
                        is_valid=True,
                    ))

        return order_blocks

    def invalidate_broken_obs(
        self, order_blocks: list[OrderBlock], candles: list[Candle]
    ) -> list[OrderBlock]:
        """Invalidate OBs that have been broken by price.

        Mirrors the Pine Script isOBValid() logic:
        - Bullish OB is invalid if any candle closes below its low
        - Bearish OB is invalid if any candle closes above its high

        Returns only the still-valid OBs.
        """
        valid: list[OrderBlock] = []
        for ob in order_blocks:
            broken = False
            for c in candles:
                if ob.direction == BOSDirection.BULLISH:
                    if c.close < ob.zone_low:
                        broken = True
                        break
                else:
                    if c.close > ob.zone_high:
                        broken = True
                        break
            if not broken:
                valid.append(ob)
        return valid

    # ── Legacy-compatible methods (used by backtest fallback path) ──

    def identify_order_blocks(self, candles: list[Candle], bos: BOS) -> list[OrderBlock]:
        """Legacy method: find OB from a BOS using the new pivot-based approach.

        Instead of just taking candle[index-1], we search back from the BOS
        point to find the candle that touched the swing level with size filter.
        """
        idx = bos.to_point.candle_index
        if idx < 1 or idx >= len(candles):
            return []

        median_range = compute_median_candle_range(candles)

        ob_candle = self.find_ob_candle_at_swing(
            candles, idx, bos.to_point.price, bos.direction, median_range,
        )
        if ob_candle is None:
            return []

        ob_key = f"{ob_candle.instrument}:{bos.direction.value}:{ob_candle.high}:{ob_candle.low}:{ob_candle.timestamp}"
        ob_id = hashlib.sha256(ob_key.encode()).hexdigest()[:16]

        return [OrderBlock(
            id=ob_id,
            instrument=ob_candle.instrument,
            direction=bos.direction,
            zone_high=ob_candle.high,
            zone_low=ob_candle.low,
            formation_timestamp=ob_candle.timestamp,
            bos_id=None,
            is_valid=True,
        )]

    def detect_liquidity_sweep(
        self, candles: list[Candle], level: float, direction: BOSDirection
    ) -> bool:
        """Check if price swept beyond a liquidity level and reversed."""
        for c in candles:
            if direction == BOSDirection.BULLISH:
                if c.low < level and c.close > level:
                    return True
            else:
                if c.high > level and c.close < level:
                    return True
        return False

    def detect_fvg(
        self, candles: list[Candle], ob: OrderBlock, avg_range: float
    ) -> bool:
        """Detect a Fair Value Gap (imbalance) near the order block zone.

        Bullish FVG: gap between candle[i-1].low and candle[i+1].high
        (price skipped upward, leaving a gap).
        Bearish FVG: gap between candle[i-1].high and candle[i+1].low
        (price skipped downward, leaving a gap).
        """
        if len(candles) < 3:
            return False

        min_gap = MIN_FVG_RATIO * avg_range

        for i in range(1, len(candles) - 1):
            prev_c, nxt_c = candles[i - 1], candles[i + 1]

            if ob.direction == BOSDirection.BULLISH:
                # Bullish FVG: gap up — prev candle's low > next candle's high
                gap_low = nxt_c.high
                gap_high = prev_c.low
                if gap_high > gap_low and (gap_high - gap_low) >= min_gap:
                    if gap_low <= ob.zone_high + avg_range and gap_high >= ob.zone_low - avg_range:
                        return True
            else:
                # Bearish FVG: gap down — prev candle's high < next candle's low
                gap_low = prev_c.high
                gap_high = nxt_c.low
                if gap_high > gap_low and (gap_high - gap_low) >= min_gap:
                    if gap_low <= ob.zone_high + avg_range and gap_high >= ob.zone_low - avg_range:
                        return True

        return False

    def find_structural_target(
        self, structure: list[StructurePoint], ob: OrderBlock
    ) -> float | None:
        """Find the next opposing swing level as a structural take-profit."""
        if ob.direction == BOSDirection.BULLISH:
            targets = [
                p.price for p in structure
                if p.type in (StructureType.HIGHER_HIGH, StructureType.LOWER_HIGH)
                and p.price > ob.zone_high
            ]
            return min(targets) if targets else None
        else:
            targets = [
                p.price for p in structure
                if p.type in (StructureType.HIGHER_LOW, StructureType.LOWER_LOW)
                and p.price < ob.zone_low
            ]
            return max(targets) if targets else None


# ── ICT Signal Generator ─────────────────────────────────────────

class ICTSignalGenerator:
    """Generates trading signals from confirmed order block setups.

    Checks retest confirmation on entry-TF candles, applies guard filters
    (spread, volatility, news, session, slippage), computes entry/SL/TP,
    and enforces minimum reward-to-risk ratio.
    """

    def check_retest(self, ob: OrderBlock, candles: list[Candle]) -> float | None:
        """Check if any of the last RETEST_LOOKBACK candles retested the OB zone
        with a rejection candle pattern.

        For bullish OB: candle must touch zone (low <= zone_high), close outside
        zone (close > zone_high), and wick into zone >= 50% of candle range.
        Entry is placed at zone_high (top of OB) — this gives a clean risk distance
        to SL at zone_low.

        For bearish OB: candle must touch zone (high >= zone_low), close outside
        zone (close < zone_low), and wick into zone >= 50% of candle range.
        Entry is placed at zone_low (bottom of OB) — clean risk distance to SL at zone_high.

        Returns the entry price or None.
        """
        if not candles:
            return None

        recent = candles[-RETEST_LOOKBACK:]

        for c in recent:
            candle_range = c.high - c.low
            if candle_range <= 0:
                continue

            if ob.direction == BOSDirection.BULLISH:
                if c.low <= ob.zone_high and c.close > ob.zone_high:
                    wick_into_zone = ob.zone_high - max(c.low, ob.zone_low)
                    if wick_into_zone >= 0.5 * candle_range:
                        # Entry at zone_high — SL is at zone_low, giving a clean risk distance
                        return ob.zone_high
            else:
                if c.high >= ob.zone_low and c.close < ob.zone_low:
                    wick_into_zone = min(c.high, ob.zone_high) - ob.zone_low
                    if wick_into_zone >= 0.5 * candle_range:
                        # Entry at zone_low — SL is at zone_high, giving a clean risk distance
                        return ob.zone_low

        return None

    def generate_signal(
        self,
        ob: OrderBlock,
        candles: list[Candle],
        htf_candles: list[Candle],
        config: StrategyConfig,
        spread: float = 0.0,
        estimated_slippage: float = 0.0,
        news_windows: list[NewsWindow] | None = None,
        structural_tp: float | None = None,
        skip_timeframe_check: bool = False,
        liquidity_confirmed: bool = False,
        fvg_confirmed: bool = False,
        confidence_score: float | None = None,
    ) -> Signal | None:
        """Generate a signal from a confirmed OB setup."""
        if not ob.is_valid or not candles:
            return None

        # Retest confirmation
        entry_price = self.check_retest(ob, candles)
        if entry_price is None:
            return None

        # Parse timestamp from last candle
        from datetime import datetime, timezone
        try:
            ts = datetime.fromisoformat(candles[-1].timestamp.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            ts = datetime.now(timezone.utc)

        # Guard filters
        if not run_guard_filters(
            candles, config, ts, spread, estimated_slippage, news_windows,
        ):
            return None

        # SL and TP
        max_rr_cap = config.algorithm_params.get("max_rr_cap", 5.0)
        min_rr = config.risk_settings.min_reward_risk_ratio
        trailing_enabled = config.exit_rules.trailing_stop.enabled
        # SL buffer: place SL slightly beyond the OB zone to avoid wick stop-outs.
        # Use 10% of the zone height as buffer, capped at a reasonable distance.
        zone_height = ob.zone_high - ob.zone_low

        if ob.direction == BOSDirection.BULLISH:
            direction = SignalDirection.BUY
            sl_buffer = zone_height * 0.1
            stop_loss = ob.zone_low - sl_buffer
            risk = entry_price - stop_loss
            if risk <= 0:
                return None
            if structural_tp is not None:
                structural_rr = (structural_tp - entry_price) / risk
                if structural_rr >= min_rr:
                    take_profit = structural_tp
                    if not trailing_enabled:
                        max_tp = entry_price + risk * max_rr_cap
                        take_profit = min(take_profit, max_tp)
                else:
                    take_profit = entry_price + risk * min_rr
            else:
                take_profit = entry_price + risk * min_rr
        else:
            direction = SignalDirection.SELL
            sl_buffer = zone_height * 0.1
            stop_loss = ob.zone_high + sl_buffer
            risk = stop_loss - entry_price
            if risk <= 0:
                return None
            if structural_tp is not None:
                structural_rr = (entry_price - structural_tp) / risk
                if structural_rr >= min_rr:
                    take_profit = structural_tp
                    if not trailing_enabled:
                        max_tp = entry_price - risk * max_rr_cap
                        take_profit = max(take_profit, max_tp)
                else:
                    take_profit = entry_price - risk * min_rr
            else:
                take_profit = entry_price - risk * min_rr

        # Enforce minimum R:R
        if ob.direction == BOSDirection.BULLISH:
            actual_rr = (take_profit - entry_price) / risk if risk > 0 else 0
        else:
            actual_rr = (entry_price - take_profit) / risk if risk > 0 else 0
        if actual_rr < min_rr:
            return None

        # Confidence scoring
        if confidence_score is not None:
            confidence = confidence_score
        else:
            confidence = 0.5
            if liquidity_confirmed:
                confidence += 0.2
            if fvg_confirmed:
                confidence += 0.15
            if structural_tp is not None:
                confidence += 0.1
            confidence = min(1.0, confidence)

        return build_signal(
            instrument=ob.instrument,
            direction=direction,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            config=config,
            candles=candles,
            timestamp=ts,
            order_block_id=ob.id,
            spread=spread,
            extra_metadata={
                "bos_type": ob.direction.value,
                "liquidity_swept": liquidity_confirmed,
            },
            confidence_score=confidence,
        )


# ── ICT Order Block Algorithm (3-Timeframe) ──────────────────────

_TIMEFRAME_MINUTES = {
    "1m": 1, "5m": 5, "15m": 15, "30m": 30,
    "1h": 60, "4h": 240, "1d": 1440,
}


def _timeframe_to_minutes(tf) -> int:
    """Convert a Timeframe enum (or string) to minutes."""
    return _TIMEFRAME_MINUTES.get(str(tf), 5)


class ICTOrderBlockAlgorithm(StrategyAlgorithm):
    """ICT Order Block algorithm using 3-timeframe model with proper pivot detection.

    1. Trend TF → TrendAnalyzer determines bullish/bearish/neutral bias
       using N-bar pivot detection (configurable swing_length).
    2. Structure TF → OrderBlockDetector finds pivots, identifies OB zones
       at swing levels using candle-touch search with size filter,
       detects FVGs, finds structural TP targets.
    3. Entry TF → ICTSignalGenerator confirms retest, times entry.

    Only signals aligned with the trend bias are generated.
    Neutral bias = no signals.
    """

    def __init__(self) -> None:
        self.trend_analyzer = TrendAnalyzer()
        self.generator = ICTSignalGenerator()

    @staticmethod
    def name() -> str:
        return "ict_order_block"

    @staticmethod
    def description() -> str:
        return (
            "ICT Order Block strategy using 3-timeframe model with proper "
            "N-bar pivot detection: trend TF for bias, structure TF for "
            "pivot-based OB zones with candle size filter, entry TF for "
            "retest confirmation and timing."
        )

    @staticmethod
    def default_params() -> dict:
        return {
            "structure_lookback": 20,
            "trend_lookback": 50,
            "swing_length": 5,
            "max_rr_cap": 5.0,
            "cooldown_candles": 6,
            "max_candle_size_multiplier": 2.0,
            "kill_zone_mode": "disabled",
            "kill_zones": [],
            "kill_zone_confidence_penalty": 0.15,
            "choch_lookback": 3,
            "zone_filter_enabled": True,
            "breaker_blocks_enabled": True,
            "ob_max_age_candles": 500,
        }

    @staticmethod
    def param_schema() -> dict:
        return {
            "type": "object",
            "properties": {
                "structure_lookback": {
                    "type": "integer",
                    "minimum": 5,
                    "maximum": 100,
                    "description": "Number of structure-TF candles for pivot/OB detection",
                },
                "trend_lookback": {
                    "type": "integer",
                    "minimum": 10,
                    "maximum": 200,
                    "description": "Number of trend-TF candles for bias determination",
                },
                "swing_length": {
                    "type": "integer",
                    "minimum": 2,
                    "maximum": 20,
                    "description": "N-bar pivot detection length (must be highest/lowest among N bars on each side)",
                },
                "max_rr_cap": {
                    "type": "number",
                    "minimum": 1.0,
                    "maximum": 50.0,
                    "description": "Maximum TP/SL ratio cap for structural targets",
                },
                "cooldown_candles": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 100,
                    "description": "Minimum candles between signals at the same OB zone",
                },
                "max_candle_size_multiplier": {
                    "type": "number",
                    "minimum": 0.5,
                    "maximum": 10.0,
                    "description": "Max OB candle size as multiple of median candle range",
                },
                "kill_zone_mode": {
                    "type": "string",
                    "enum": ["strict", "soft", "disabled"],
                    "description": "Kill zone filtering mode",
                },
                "kill_zones": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "start_hour": {"type": "integer", "minimum": 0, "maximum": 23},
                            "start_minute": {"type": "integer", "minimum": 0, "maximum": 59},
                            "end_hour": {"type": "integer", "minimum": 0, "maximum": 23},
                            "end_minute": {"type": "integer", "minimum": 0, "maximum": 59},
                        },
                        "required": ["name", "start_hour", "start_minute", "end_hour", "end_minute"],
                    },
                    "description": "Custom kill zone windows in EST",
                },
                "kill_zone_confidence_penalty": {
                    "type": "number",
                    "minimum": 0.0,
                    "maximum": 0.5,
                    "description": "Confidence penalty for signals outside kill zones in soft mode",
                },
                "choch_lookback": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 10,
                    "description": "Number of recent opposing swing points for CHOCH detection",
                },
                "zone_filter_enabled": {
                    "type": "boolean",
                    "description": "Enable premium/discount zone filtering",
                },
                "breaker_blocks_enabled": {
                    "type": "boolean",
                    "description": "Enable breaker block detection and tracking",
                },
                "ob_max_age_candles": {
                    "type": "integer",
                    "minimum": 50,
                    "maximum": 5000,
                    "description": "Max OB age in structure-TF candles before time decay starts",
                },
            },
            "additionalProperties": False,
        }

    def analyze(
        self,
        entry_candles: list[Candle],
        structure_candles: list[Candle],
        trend_candles: list[Candle],
        config: StrategyConfig,
        invalidated_zones: set[str] | None = None,
        zone_cooldowns: dict[str, str] | None = None,
    ) -> list[Signal]:
        """Run the full 3-TF analysis cycle.

        Step 1: Determine trend bias from trend_candles using N-bar pivots
        Step 2: Detect pivots on structure_candles
        Step 3: Identify OBs at swing levels (candle-touch search + size filter)
        Step 4: Invalidate broken OBs
        Step 5: For each valid OB, check liquidity sweep + FVG on entry candles
        Step 6: Confirm retest and generate signal
        """
        if not entry_candles or not structure_candles:
            return []

        # Extract params
        swing_length = config.algorithm_params.get("swing_length", DEFAULT_SWING_LENGTH)
        trend_lookback = config.algorithm_params.get("trend_lookback", DEFAULT_TREND_LOOKBACK)
        max_candle_multiplier = config.algorithm_params.get(
            "max_candle_size_multiplier", DEFAULT_MAX_CANDLE_SIZE_MULTIPLIER
        )

        # Validate new params with fallback to defaults
        choch_lookback = config.algorithm_params.get("choch_lookback", 3)
        if not isinstance(choch_lookback, int) or choch_lookback < 1:
            choch_lookback = 3

        kill_zone_mode = config.algorithm_params.get("kill_zone_mode", "disabled")
        if kill_zone_mode not in ("strict", "soft", "disabled"):
            kill_zone_mode = "disabled"

        ob_max_age_candles = config.algorithm_params.get("ob_max_age_candles", 500)
        if not isinstance(ob_max_age_candles, int) or ob_max_age_candles < 50:
            ob_max_age_candles = 500

        # Step 1: Trend bias
        bias = self.trend_analyzer.determine_bias(trend_candles, trend_lookback, swing_length)
        if bias == TrendBias.NEUTRAL:
            return []

        # Step 2: Detect pivots on structure TF
        detector = OrderBlockDetector(swing_length)
        structure = detector.detect_structure(structure_candles)
        pivot_highs, pivot_lows = find_all_pivots(structure_candles, swing_length)

        # Step 2.5: CHOCH/MSS detection
        choch_detector = CHOCHDetector()
        choch_events = choch_detector.detect(structure, structure_candles, choch_lookback)

        # Step 2.6: Compute dealing range for zone filtering
        zone_filter = ZoneFilter()
        dealing_range = zone_filter.compute_dealing_range(pivot_highs, pivot_lows)

        # Precompute median range for candle size filter
        median_range = compute_median_candle_range(structure_candles)

        # Step 3: Identify OBs at swing levels
        order_blocks = detector.identify_order_blocks_at_pivots(
            structure_candles, pivot_highs, pivot_lows, bias,
            median_range, max_candle_multiplier,
        )

        # Set formation_candle_index on newly created OBs for age tracking
        for ob in order_blocks:
            if ob.formation_candle_index is None:
                ob.formation_candle_index = len(structure_candles) - 1

        # Step 4: Enhanced OB invalidation (partial mitigation, time decay, volume, CHOCH)
        invalidation_engine = OBInvalidationEngine()
        invalidation_result = invalidation_engine.process(
            order_blocks, entry_candles, len(structure_candles) - 1,
            ob_max_age_candles, choch_events, bias,
        )
        order_blocks = invalidation_result.valid_obs
        ob_invalidated = invalidation_result.invalidated_obs
        ob_strong_ids = invalidation_result.strong_invalidation_ids
        ob_mitigation_counts = invalidation_result.mitigation_counts
        ob_age_penalties = invalidation_result.age_penalties

        # Also filter by external invalidation set
        if invalidated_zones:
            order_blocks = [ob for ob in order_blocks if ob.id not in invalidated_zones]

        # Step 4.5: Create breaker blocks from invalidated OBs
        breaker_blocks: list[BreakerBlock] = []
        breaker_blocks_enabled = config.algorithm_params.get("breaker_blocks_enabled", True)
        if breaker_blocks_enabled and ob_invalidated:
            breaker_detector = BreakerDetector()
            breaker_blocks = breaker_detector.create_breakers(ob_invalidated, ob_strong_ids)
            breaker_blocks = breaker_detector.invalidate_broken_breakers(breaker_blocks, entry_candles)

        if not order_blocks:
            return []

        # Precompute avg range and swing levels for FVG/liquidity checks
        ranges = [c.high - c.low for c in structure_candles if (c.high - c.low) > 0]
        avg_range = sum(ranges) / len(ranges) if ranges else 1.0

        swing_lows = [p.price for p in pivot_lows]
        swing_highs = [p.price for p in pivot_highs]

        # Step 5 & 6: For each valid OB, check confirmations and generate signals
        signals: list[Signal] = []
        skip_tf = bool(
            entry_candles and entry_candles[0].timeframe != config.entry_timeframe
        )

        # Initialize KillZoneFilter
        kill_zones_raw = config.algorithm_params.get("kill_zones", [])
        kill_zone_penalty_val = config.algorithm_params.get("kill_zone_confidence_penalty", 0.15)

        if kill_zones_raw:
            kill_zone_windows = [KillZoneWindow(**kz) for kz in kill_zones_raw]
        elif kill_zone_mode != "disabled":
            kill_zone_windows = DEFAULT_KILL_ZONES
        else:
            kill_zone_windows = []

        kill_zone_filter = KillZoneFilter(kill_zone_windows, kill_zone_mode, kill_zone_penalty_val)

        for ob in order_blocks:
            # Signal cooldown per zone check
            if zone_cooldowns and ob.id in zone_cooldowns:
                cooldown_candles = config.algorithm_params.get("cooldown_candles", 6)
                entry_tf_minutes = _timeframe_to_minutes(config.entry_timeframe)
                cooldown_minutes = cooldown_candles * entry_tf_minutes
                try:
                    last_signal_dt = datetime.fromisoformat(
                        zone_cooldowns[ob.id].replace("Z", "+00:00")
                    )
                    if entry_candles:
                        current_dt = datetime.fromisoformat(
                            entry_candles[-1].timestamp.replace("Z", "+00:00")
                        )
                        diff_minutes = (current_dt - last_signal_dt).total_seconds() / 60.0
                        if diff_minutes < cooldown_minutes:
                            continue
                except (ValueError, AttributeError):
                    pass

            # Zone filter: reject premium buys / discount sells
            zone_filter_enabled = config.algorithm_params.get("zone_filter_enabled", True)
            zone_classification = None
            zone_bonus = 0.0
            if zone_filter_enabled and dealing_range is not None:
                zone_classification = zone_filter.classify_price(
                    ob.zone_high if ob.direction == BOSDirection.BULLISH else ob.zone_low,
                    dealing_range,
                    ob.direction,
                )
                if zone_filter.should_reject_signal(ob.direction, zone_classification):
                    continue
                zone_bonus = zone_classification.confidence_bonus

            # Liquidity sweep check on entry candles
            liquidity_confirmed = False
            if ob.direction == BOSDirection.BULLISH and swing_lows:
                relevant = [l for l in swing_lows if l <= ob.zone_high]
                if relevant:
                    liquidity_confirmed = detector.detect_liquidity_sweep(
                        entry_candles, max(relevant), ob.direction,
                    )
            elif ob.direction == BOSDirection.BEARISH and swing_highs:
                relevant = [h for h in swing_highs if h >= ob.zone_low]
                if relevant:
                    liquidity_confirmed = detector.detect_liquidity_sweep(
                        entry_candles, min(relevant), ob.direction,
                    )

            fvg_confirmed = detector.detect_fvg(structure_candles, ob, avg_range)
            structural_tp = detector.find_structural_target(structure, ob)

            # Kill zone evaluation
            kz_result = KillZoneResult(in_kill_zone=False, confidence_penalty=0.0)
            if entry_candles:
                from datetime import timezone
                try:
                    ts = datetime.fromisoformat(entry_candles[-1].timestamp.replace("Z", "+00:00"))
                except (ValueError, AttributeError):
                    ts = datetime.now(timezone.utc)
                kz_result = kill_zone_filter.evaluate(ts)
                if kill_zone_mode == "strict" and kz_result.confidence_penalty >= 1.0:
                    continue  # Reject signal in strict mode when outside kill zones

            # Compute confidence using ConfidenceScorer
            # Check for CHOCH penalty: any CHOCH event opposing the OB direction
            has_choch_penalty = False
            for evt in choch_events:
                if (evt.direction == BOSDirection.BEARISH and ob.direction == BOSDirection.BULLISH) or \
                   (evt.direction == BOSDirection.BULLISH and ob.direction == BOSDirection.BEARISH):
                    has_choch_penalty = True
                    break

            # Check breaker confluence
            has_breaker = False
            breaker_is_strong = False
            if breaker_blocks_enabled and breaker_blocks:
                bd = BreakerDetector()
                has_breaker = bd.check_breaker_confluence(
                    ob.zone_high if ob.direction == BOSDirection.BULLISH else ob.zone_low,
                    ob.direction, breaker_blocks, bias,
                )
                if has_breaker:
                    # Check if any matching breaker has strong bonus
                    for bb in breaker_blocks:
                        if bb.is_valid and bb.confidence_bonus >= 0.15:
                            if bb.zone_low <= (ob.zone_high if ob.direction == BOSDirection.BULLISH else ob.zone_low) <= bb.zone_high:
                                breaker_is_strong = True
                                break

            scorer = ConfidenceScorer()
            computed_confidence = scorer.compute(
                base=0.5,
                liquidity_confirmed=liquidity_confirmed,
                fvg_confirmed=fvg_confirmed,
                structural_tp_found=(structural_tp is not None),
                in_kill_zone=kz_result.in_kill_zone,
                zone_bonus=zone_bonus,
                choch_penalty=has_choch_penalty,
                breaker_confluence=has_breaker,
                breaker_strong=breaker_is_strong,
                ob_partial_mitigation_count=ob_mitigation_counts.get(ob.id, 0),
                ob_age_penalty=ob_age_penalties.get(ob.id, 0.0),
                kill_zone_penalty=kz_result.confidence_penalty,
            )

            # Apply shared confluence filters (if configured)
            confluence_filter_names = config.algorithm_params.get("confluence_filters", [])
            if confluence_filter_names:
                from src.strategy.filters.apply import apply_confluence_filters
                signal_direction = (
                    SignalDirection.BUY if ob.direction == BOSDirection.BULLISH
                    else SignalDirection.SELL
                )
                adjustment = apply_confluence_filters(
                    confluence_filter_names, structure_candles, signal_direction, config.algorithm_params
                )
                computed_confidence += adjustment
                computed_confidence = max(0.0, min(1.0, computed_confidence))

            # Check against min_confidence_score threshold
            if computed_confidence < config.min_confidence_score:
                continue

            signal = self.generator.generate_signal(
                ob=ob,
                candles=entry_candles,
                htf_candles=structure_candles,
                config=config,
                structural_tp=structural_tp,
                skip_timeframe_check=skip_tf,
                liquidity_confirmed=liquidity_confirmed,
                fvg_confirmed=fvg_confirmed,
                confidence_score=computed_confidence,
            )
            if signal is not None:
                signals.append(signal)

        return signals
