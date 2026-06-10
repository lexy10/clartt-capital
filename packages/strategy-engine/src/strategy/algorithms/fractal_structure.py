"""Trend Persistence Model using fractal market structure.

Instead of re-deriving the entire multi-timeframe structure every bar, this algorithm:
1. Establishes HTF bias from H4 by finding the most recent BOS (HH+HL or LH+LL)
2. Stores an invalidation level — the key swing that kills the bias if broken
3. Keeps the bias active until invalidation — no re-checking required
4. Takes MANY entries on the LTF — every M5 fractal pullback in trend direction
5. Uses H1 as confluence — H1 structure must not break against the bias

Timeframe mapping:
- trend_candles → H4 (primary bias)
- structure_candles → H1 (confluence + SL/TP targets)
- entry_candles → M5 (entry timing via pullback fractals)

This produces far more trades than the old version because any M5 pullback fractal
in the direction of a valid H4 bias is a potential entry — not just the first one
after a BOS.
"""

import logging
import statistics
import uuid
from datetime import datetime, timezone
from enum import Enum

from src.models import BOSType, Candle, Signal, SignalDirection, StrategyConfig
from src.strategy.base import StrategyAlgorithm
from src.strategy.signal_helpers import build_signal

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Enums and data structures
# ---------------------------------------------------------------------------


class Bias(str, Enum):
    BULLISH = "bullish"
    BEARISH = "bearish"


class SwingType(str, Enum):
    HH = "HH"
    HL = "HL"
    LH = "LH"
    LL = "LL"


class SwingPoint:
    """A confirmed fractal swing point with classification."""

    __slots__ = ("price", "index", "is_high", "swing_type")

    def __init__(self, price: float, index: int, is_high: bool, swing_type: SwingType | None = None):
        self.price = price
        self.index = index
        self.is_high = is_high
        self.swing_type = swing_type


# ---------------------------------------------------------------------------
# Fractal detection
# ---------------------------------------------------------------------------


def detect_fractal_high(candles: list[Candle], index: int, n: int = 2) -> bool:
    """Fractal high: candles[index].high > N bars on each side."""
    if index < n or index >= len(candles) - n:
        return False
    high = candles[index].high
    for k in range(1, n + 1):
        if candles[index - k].high >= high:
            return False
        if candles[index + k].high >= high:
            return False
    return True


def detect_fractal_low(candles: list[Candle], index: int, n: int = 2) -> bool:
    """Fractal low: candles[index].low < N bars on each side."""
    if index < n or index >= len(candles) - n:
        return False
    low = candles[index].low
    for k in range(1, n + 1):
        if candles[index - k].low <= low:
            return False
        if candles[index + k].low <= low:
            return False
    return True


def find_all_fractals(candles: list[Candle], n: int = 2, lookback: int = 100) -> list[SwingPoint]:
    """Find all confirmed fractal swing points within lookback window.

    Returns chronologically sorted list. Last confirmable fractal is at
    len(candles) - n - 1 due to confirmation lag.
    """
    start_idx = max(n, len(candles) - lookback)
    end_idx = len(candles) - n

    swings: list[SwingPoint] = []
    for i in range(start_idx, end_idx):
        if detect_fractal_high(candles, i, n):
            swings.append(SwingPoint(price=candles[i].high, index=i, is_high=True))
        if detect_fractal_low(candles, i, n):
            swings.append(SwingPoint(price=candles[i].low, index=i, is_high=False))

    swings.sort(key=lambda s: s.index)
    return swings


def classify_swings(swings: list[SwingPoint]) -> list[SwingPoint]:
    """Classify each swing as HH/HL/LH/LL by comparing to previous same-type swing."""
    last_high: SwingPoint | None = None
    last_low: SwingPoint | None = None

    for swing in swings:
        if swing.is_high:
            if last_high is not None:
                swing.swing_type = SwingType.HH if swing.price > last_high.price else SwingType.LH
            last_high = swing
        else:
            if last_low is not None:
                swing.swing_type = SwingType.HL if swing.price > last_low.price else SwingType.LL
            last_low = swing

    return swings


# ---------------------------------------------------------------------------
# Phase 1: H4 Bias establishment
# ---------------------------------------------------------------------------


def establish_htf_bias(
    candles: list[Candle], n: int, lookback: int
) -> tuple[Bias | None, float | None, list[SwingPoint]]:
    """Derive H4 bias from fractal swing sequence.

    Returns (bias, invalidation_level, classified_swings) or (None, None, [])
    if no clear bias or invalidation has been breached.
    """
    swings = find_all_fractals(candles, n=n, lookback=lookback)
    swings = classify_swings(swings)

    classified = [s for s in swings if s.swing_type is not None]
    if len(classified) < 2:
        return None, None, swings

    # Find the most recent pair that establishes a sequence
    # Look at last few classified swings for HH+HL or LH+LL
    recent_highs = [s for s in classified if s.is_high]
    recent_lows = [s for s in classified if not s.is_high]

    has_hh = any(s.swing_type == SwingType.HH for s in recent_highs[-3:]) if recent_highs else False
    has_hl = any(s.swing_type == SwingType.HL for s in recent_lows[-3:]) if recent_lows else False
    has_lh = any(s.swing_type == SwingType.LH for s in recent_highs[-3:]) if recent_highs else False
    has_ll = any(s.swing_type == SwingType.LL for s in recent_lows[-3:]) if recent_lows else False

    current_price = candles[-1].close

    if has_hh and has_hl:
        # BULLISH bias — invalidation = last confirmed fractal low (the HL)
        last_fractal_low = recent_lows[-1] if recent_lows else None
        if last_fractal_low is None:
            return None, None, swings
        invalidation = last_fractal_low.price
        # Check if invalidation has been breached
        if current_price < invalidation:
            return None, None, swings  # Bias dead
        return Bias.BULLISH, invalidation, swings

    if has_lh and has_ll:
        # BEARISH bias — invalidation = last confirmed fractal high (the LH)
        last_fractal_high = recent_highs[-1] if recent_highs else None
        if last_fractal_high is None:
            return None, None, swings
        invalidation = last_fractal_high.price
        # Check if invalidation has been breached
        if current_price > invalidation:
            return None, None, swings  # Bias dead
        return Bias.BEARISH, invalidation, swings

    return None, None, swings


# ---------------------------------------------------------------------------
# Phase 2: H1 Confluence
# ---------------------------------------------------------------------------


def check_h1_confluence(
    candles: list[Candle],
    bias: Bias,
    invalidation_level: float,
    confluence_lookback: int,
) -> tuple[bool, SwingPoint | None, list[SwingPoint]]:
    """Check H1 structure hasn't broken against the H4 bias.

    Returns (is_confluent, last_h1_swing_for_sl, h1_swings).
    """
    if len(candles) < confluence_lookback:
        check_candles = candles
    else:
        check_candles = candles[-confluence_lookback:]

    # Find H1 fractals for structural targets
    h1_swings = find_all_fractals(candles, n=2, lookback=len(candles))
    h1_swings = classify_swings(h1_swings)

    if bias == Bias.BULLISH:
        # H1 must NOT have made a lower low below the H4 invalidation level recently
        for c in check_candles:
            if c.low < invalidation_level:
                return False, None, h1_swings
        # Find last H1 swing low for SL placement
        h1_lows = [s for s in h1_swings if not s.is_high]
        last_h1_swing = h1_lows[-1] if h1_lows else None
        return True, last_h1_swing, h1_swings

    else:  # BEARISH
        # H1 must NOT have made a higher high above the H4 invalidation level recently
        for c in check_candles:
            if c.high > invalidation_level:
                return False, None, h1_swings
        # Find last H1 swing high for SL placement
        h1_highs = [s for s in h1_swings if s.is_high]
        last_h1_swing = h1_highs[-1] if h1_highs else None
        return True, last_h1_swing, h1_swings


# ---------------------------------------------------------------------------
# Phase 3: M5 Entry detection
# ---------------------------------------------------------------------------


def find_entry_fractal(
    candles: list[Candle],
    bias: Bias,
    invalidation_level: float,
    n: int,
    recency: int,
) -> SwingPoint | None:
    """Find a recent M5 fractal pullback in the direction of bias.

    For BUY: find a fractal low ABOVE the invalidation level.
    For SELL: find a fractal high BELOW the invalidation level.
    Must be within the last `recency` entry candles.
    """
    end_idx = len(candles) - n
    start_idx = max(n, end_idx - recency)

    if bias == Bias.BULLISH:
        # Look for most recent fractal low above invalidation
        for i in range(end_idx - 1, start_idx - 1, -1):
            if detect_fractal_low(candles, i, n):
                if candles[i].low > invalidation_level:
                    return SwingPoint(price=candles[i].low, index=i, is_high=False)
    else:
        # Look for most recent fractal high below invalidation
        for i in range(end_idx - 1, start_idx - 1, -1):
            if detect_fractal_high(candles, i, n):
                if candles[i].high < invalidation_level:
                    return SwingPoint(price=candles[i].high, index=i, is_high=True)

    return None


# ---------------------------------------------------------------------------
# ATR computation
# ---------------------------------------------------------------------------


def compute_atr(
    highs: list[float], lows: list[float], closes: list[float], period: int
) -> list[float]:
    """Compute ATR using Wilder's smoothing."""
    n = len(closes)
    if n < period + 1:
        return []

    tr_list: list[float] = []
    for i in range(1, n):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        tr_list.append(tr)

    if len(tr_list) < period:
        return []

    atr = sum(tr_list[:period]) / period
    atr_values = [atr]

    for i in range(period, len(tr_list)):
        atr = (atr * (period - 1) + tr_list[i]) / period
        atr_values.append(atr)

    return atr_values


# ---------------------------------------------------------------------------
# Algorithm class
# ---------------------------------------------------------------------------


class FractalStructureAlgorithm(StrategyAlgorithm):
    """Trend Persistence Model — establishes H4 bias once, takes many M5 entries
    until the bias invalidation level breaks.
    """

    @staticmethod
    def name() -> str:
        return "fractal_structure"

    @staticmethod
    def description() -> str:
        return (
            "Trend Persistence Model using fractal market structure. Establishes "
            "H4 bias via BOS detection (HH+HL or LH+LL), locks bias until "
            "invalidation level breaks, uses H1 confluence confirmation, and "
            "enters on every M5 fractal pullback in the trend direction."
        )

    @staticmethod
    def default_params() -> dict:
        return {
            "fractal_n": 2,
            "htf_lookback": 80,
            "structure_lookback": 200,
            "entry_lookback": 50,
            "atr_period": 14,
            "atr_buffer_mult": 0.5,
            "min_sl_atr_mult": 1.0,
            "min_rr_ratio": 2.0,
            "cooldown_candles": 5,
            "h1_confluence_lookback": 20,
            "entry_fractal_recency": 10,
            "confluence_filters": [],
            # Regime filter (built-in) — blocks signals in ranging markets
            "regime_filter_enabled": True,
            "regime_adx_period": 14,
            "regime_adx_block_threshold": 18.0,
            # Session filter (built-in) — blocks off-hours for non-synthetic instruments
            "session_filter_enabled": True,
            "session_instrument": "",
            # CHoCH exit detection — adds opposing CHoCH level to signal metadata
            "choch_exit_enabled": True,
        }

    @staticmethod
    def param_schema() -> dict:
        return {
            "fractal_n": {"type": "integer", "minimum": 1, "maximum": 5},
            "htf_lookback": {"type": "integer", "minimum": 20, "maximum": 200},
            "structure_lookback": {"type": "integer", "minimum": 50, "maximum": 500},
            "entry_lookback": {"type": "integer", "minimum": 20, "maximum": 200},
            "atr_period": {"type": "integer", "minimum": 2, "maximum": 50},
            "atr_buffer_mult": {"type": "number", "minimum": 0.1, "maximum": 2.0},
            "min_sl_atr_mult": {"type": "number", "minimum": 0.5, "maximum": 5.0},
            "min_rr_ratio": {"type": "number", "minimum": 1.0, "maximum": 10.0},
            "cooldown_candles": {"type": "integer", "minimum": 0, "maximum": 50},
            "h1_confluence_lookback": {"type": "integer", "minimum": 5, "maximum": 50},
            "entry_fractal_recency": {"type": "integer", "minimum": 3, "maximum": 30},
            "confluence_filters": {"type": "array", "items": {"type": "string"}},
            "regime_filter_enabled": {"type": "boolean"},
            "regime_adx_period": {"type": "integer", "minimum": 5, "maximum": 50},
            "regime_adx_block_threshold": {"type": "number", "minimum": 5, "maximum": 35},
            "session_filter_enabled": {"type": "boolean"},
            "session_instrument": {"type": "string"},
            "choch_exit_enabled": {"type": "boolean"},
        }

    def analyze(
        self,
        entry_candles: list[Candle],
        structure_candles: list[Candle],
        trend_candles: list[Candle],
        config: StrategyConfig,
        **kwargs,
    ) -> list[Signal]:
        """Trend Persistence Model analysis.

        Phase 1: Establish H4 bias from trend_candles (find last BOS, check invalidation)
        Phase 2: H1 confluence from structure_candles (no break against bias)
        Phase 3: M5 entry from entry_candles (fractal pullback in trend direction)
        """
        params = {**self.default_params(), **config.algorithm_params}

        fractal_n: int = params["fractal_n"]
        htf_lookback: int = params["htf_lookback"]
        structure_lookback: int = params["structure_lookback"]
        entry_lookback: int = params["entry_lookback"]
        atr_period: int = params["atr_period"]
        atr_buffer_mult: float = params["atr_buffer_mult"]
        min_sl_atr_mult: float = params["min_sl_atr_mult"]
        min_rr_ratio: float = params["min_rr_ratio"]
        h1_confluence_lookback: int = params["h1_confluence_lookback"]
        entry_fractal_recency: int = params["entry_fractal_recency"]
        regime_filter_enabled: bool = params.get("regime_filter_enabled", True)
        session_filter_enabled: bool = params.get("session_filter_enabled", True)
        choch_exit_enabled: bool = params.get("choch_exit_enabled", True)

        # Minimum data requirements
        min_trend = max(htf_lookback, 2 * fractal_n + 3)
        min_structure = max(h1_confluence_lookback, 2 * fractal_n + 3)
        min_entry = max(entry_fractal_recency + 2 * fractal_n + 1, atr_period + 1)

        if len(trend_candles) < min_trend:
            return []
        if len(structure_candles) < min_structure:
            return []
        if len(entry_candles) < min_entry:
            return []

        # ── Built-in Regime Filter (ADX check on H1 structure candles) ──
        adx_value: float | None = None
        if regime_filter_enabled:
            from src.strategy.filters.regime_filter import compute_adx

            adx_period = int(params.get("regime_adx_period", 14))
            adx_block = params.get("regime_adx_block_threshold", 18.0)
            adx_value = compute_adx(structure_candles, adx_period)
            if adx_value is not None and adx_value < adx_block:
                logger.debug(
                    "Regime filter: ADX=%.1f < %.1f — ranging market, skipping signal",
                    adx_value, adx_block,
                )
                return []

        # ── Built-in Session Filter (skip off-hours for non-synthetic instruments) ──
        if session_filter_enabled:
            from src.strategy.filters.session_filter import (
                LONDON_OPEN,
                NY_CLOSE,
                SYNTHETIC_PREFIXES,
            )

            instrument = params.get("session_instrument", "")
            if not instrument and config.instruments:
                instrument = config.instruments[0]
            instrument_lower = instrument.lower()

            is_synthetic = any(
                instrument_lower.startswith(p) for p in SYNTHETIC_PREFIXES
            )

            if not is_synthetic and entry_candles:
                try:
                    ts = entry_candles[-1].timestamp
                    if isinstance(ts, str):
                        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                    else:
                        dt = ts
                    current_hour = dt.hour
                except (ValueError, AttributeError):
                    current_hour = datetime.now(timezone.utc).hour

                if not (LONDON_OPEN <= current_hour < NY_CLOSE):
                    logger.debug(
                        "Session filter: hour=%d UTC outside London/NY — skipping signal",
                        current_hour,
                    )
                    return []

        # ── Phase 1: Establish HTF Bias (H4) ──
        bias, invalidation_level, h4_swings = establish_htf_bias(
            trend_candles, n=fractal_n, lookback=htf_lookback
        )

        if bias is None or invalidation_level is None:
            return []  # No clear bias or bias invalidated

        # ── Phase 2: H1 Confluence Check ──
        is_confluent, h1_sl_swing, h1_swings = check_h1_confluence(
            structure_candles, bias, invalidation_level, h1_confluence_lookback
        )

        if not is_confluent:
            return []  # H1 structure broke against H4 bias

        # ── Phase 3: M5 Entry ──
        entry_fractal = find_entry_fractal(
            entry_candles, bias, invalidation_level, fractal_n, entry_fractal_recency
        )

        if entry_fractal is None:
            return []  # No valid pullback fractal found

        # ── Compute ATR from entry candles ──
        entry_highs = [c.high for c in entry_candles]
        entry_lows = [c.low for c in entry_candles]
        entry_closes = [c.close for c in entry_candles]
        atr_values = compute_atr(entry_highs, entry_lows, entry_closes, atr_period)

        if not atr_values:
            return []

        current_atr = atr_values[-1]
        if current_atr == 0:
            return []

        # ── Direction and entry price ──
        direction = SignalDirection.BUY if bias == Bias.BULLISH else SignalDirection.SELL
        entry_price = entry_candles[-1].close

        # ── SL Placement ──
        atr_buffer = atr_buffer_mult * current_atr

        if direction == SignalDirection.BUY:
            # SL = M5 fractal low - ATR buffer
            sl_price = entry_fractal.price - atr_buffer
            # If too close (< 0.5 ATR), use H1 swing low instead
            if abs(entry_price - sl_price) < 0.5 * current_atr:
                if h1_sl_swing is not None:
                    sl_price = h1_sl_swing.price - atr_buffer
        else:
            # SL = M5 fractal high + ATR buffer
            sl_price = entry_fractal.price + atr_buffer
            # If too close, use H1 swing high instead
            if abs(entry_price - sl_price) < 0.5 * current_atr:
                if h1_sl_swing is not None:
                    sl_price = h1_sl_swing.price + atr_buffer

        # Enforce minimum SL distance
        sl_distance = abs(entry_price - sl_price)
        min_sl_distance = min_sl_atr_mult * current_atr

        if sl_distance < min_sl_distance:
            if direction == SignalDirection.BUY:
                sl_price = entry_price - min_sl_distance
            else:
                sl_price = entry_price + min_sl_distance
            sl_distance = min_sl_distance

        if sl_distance == 0:
            return []

        # ── TP Placement ──
        structural_tp: float | None = None

        if direction == SignalDirection.BUY:
            # Target last H1 fractal high (structural resistance)
            h1_highs = [s for s in h1_swings if s.is_high and s.price > entry_price]
            if h1_highs:
                structural_tp = h1_highs[-1].price
        else:
            # Target last H1 fractal low (structural support)
            h1_lows = [s for s in h1_swings if not s.is_high and s.price < entry_price]
            if h1_lows:
                structural_tp = h1_lows[-1].price

        # Apply min RR check
        if structural_tp is not None:
            tp_distance = abs(structural_tp - entry_price)
            if tp_distance >= min_rr_ratio * sl_distance:
                take_profit = structural_tp
            else:
                if direction == SignalDirection.BUY:
                    take_profit = entry_price + min_rr_ratio * sl_distance
                else:
                    take_profit = entry_price - min_rr_ratio * sl_distance
        else:
            if direction == SignalDirection.BUY:
                take_profit = entry_price + min_rr_ratio * sl_distance
            else:
                take_profit = entry_price - min_rr_ratio * sl_distance

        # ── Confidence Scoring ──
        confidence = 0.55

        # H4 bias clear (both HH+HL or LH+LL present, not just one swing): +0.1
        h4_classified = [s for s in h4_swings if s.swing_type is not None]
        h4_highs_classified = [s for s in h4_classified if s.is_high]
        h4_lows_classified = [s for s in h4_classified if not s.is_high]
        if bias == Bias.BULLISH:
            has_both = (
                any(s.swing_type == SwingType.HH for s in h4_highs_classified[-3:])
                and any(s.swing_type == SwingType.HL for s in h4_lows_classified[-3:])
            )
        else:
            has_both = (
                any(s.swing_type == SwingType.LH for s in h4_highs_classified[-3:])
                and any(s.swing_type == SwingType.LL for s in h4_lows_classified[-3:])
            )
        if has_both:
            confidence += 0.1

        # H1 confluence strong (H1 also making HH/HL or LH/LL in same direction): +0.1
        h1_classified = [s for s in h1_swings if s.swing_type is not None]
        if bias == Bias.BULLISH:
            h1_bullish_count = sum(
                1 for s in h1_classified[-5:]
                if s.swing_type in (SwingType.HH, SwingType.HL)
            )
            if h1_bullish_count >= 3:
                confidence += 0.1
        else:
            h1_bearish_count = sum(
                1 for s in h1_classified[-5:]
                if s.swing_type in (SwingType.LH, SwingType.LL)
            )
            if h1_bearish_count >= 3:
                confidence += 0.1

        # Entry fractal fresh (formed within last 5 candles): +0.05
        candles_since_fractal = len(entry_candles) - 1 - entry_fractal.index
        if candles_since_fractal <= 5:
            confidence += 0.05

        # Pullback depth moderate (> 30% retracement of last H1 impulse move): +0.1
        if direction == SignalDirection.BUY:
            h1_recent_highs = [s for s in h1_swings if s.is_high]
            h1_recent_lows = [s for s in h1_swings if not s.is_high]
            if h1_recent_highs and h1_recent_lows:
                impulse_high = h1_recent_highs[-1].price
                impulse_low = h1_recent_lows[-1].price
                impulse_range = impulse_high - impulse_low
                if impulse_range > 0:
                    retracement = (impulse_high - entry_fractal.price) / impulse_range
                    if retracement > 0.3:
                        confidence += 0.1
        else:
            h1_recent_highs = [s for s in h1_swings if s.is_high]
            h1_recent_lows = [s for s in h1_swings if not s.is_high]
            if h1_recent_highs and h1_recent_lows:
                impulse_high = h1_recent_highs[-1].price
                impulse_low = h1_recent_lows[-1].price
                impulse_range = impulse_high - impulse_low
                if impulse_range > 0:
                    retracement = (entry_fractal.price - impulse_low) / impulse_range
                    if retracement > 0.3:
                        confidence += 0.1

        # ATR expanding (current ATR > median ATR): +0.05
        if len(atr_values) > 1:
            median_atr = statistics.median(atr_values)
            if current_atr > median_atr:
                confidence += 0.05

        # Confluence filters
        filter_names = params.get("confluence_filters", [])
        if filter_names:
            from src.strategy.filters.apply import apply_confluence_filters

            adjustment = apply_confluence_filters(
                filter_names, structure_candles, direction, params
            )
            confidence += adjustment

        confidence = max(0.0, min(1.0, confidence))
        if confidence < config.min_confidence_score:
            return []

        # ── CHoCH Exit Level Detection ──
        # Find the level where an opposing CHoCH would invalidate the trade
        # This gives the execution engine a structural exit trigger
        choch_exit_level: float | None = None
        if choch_exit_enabled:
            if direction == SignalDirection.BUY:
                # For a BUY, CHoCH exit = if M5 makes a Lower Low below the entry fractal
                # Use the most recent M5 swing low as the CHoCH trigger level
                entry_swings = find_all_fractals(entry_candles, n=fractal_n, lookback=entry_fractal_recency * 2)
                entry_swings = classify_swings(entry_swings)
                recent_lows = [s for s in entry_swings if not s.is_high and s.swing_type == SwingType.HL]
                if recent_lows:
                    # CHoCH exit = if price breaks below the last HL (making it an LL)
                    choch_exit_level = recent_lows[-1].price
                else:
                    # Fallback: use the entry fractal itself
                    choch_exit_level = entry_fractal.price
            else:
                # For a SELL, CHoCH exit = if M5 makes a Higher High above the entry fractal
                entry_swings = find_all_fractals(entry_candles, n=fractal_n, lookback=entry_fractal_recency * 2)
                entry_swings = classify_swings(entry_swings)
                recent_highs = [s for s in entry_swings if s.is_high and s.swing_type == SwingType.LH]
                if recent_highs:
                    # CHoCH exit = if price breaks above the last LH (making it an HH)
                    choch_exit_level = recent_highs[-1].price
                else:
                    choch_exit_level = entry_fractal.price

        # ── Build signal ──
        timestamp = datetime.now(timezone.utc)
        if entry_candles:
            try:
                timestamp = datetime.fromisoformat(
                    entry_candles[-1].timestamp.replace("Z", "+00:00")
                )
            except (ValueError, AttributeError):
                pass

        bos_type = BOSType.BULLISH if direction == SignalDirection.BUY else BOSType.BEARISH

        signal = build_signal(
            instrument=config.instruments[0] if config.instruments else "",
            direction=direction,
            entry_price=entry_price,
            stop_loss=sl_price,
            take_profit=take_profit,
            config=config,
            candles=entry_candles,
            timestamp=timestamp,
            order_block_id=str(uuid.uuid4()),
            extra_metadata={
                "bos_type": bos_type,
                "atr_sl_multiplier": atr_buffer_mult,
                "bias": bias.value,
                "invalidation_level": round(invalidation_level, 2),
                "entry_fractal_price": round(entry_fractal.price, 2),
                "entry_fractal_age": candles_since_fractal,
                "h1_confluent": True,
                "structural_tp_used": structural_tp is not None,
                "choch_exit_level": round(choch_exit_level, 2) if choch_exit_level is not None else None,
                "regime_adx": round(adx_value, 1) if adx_value is not None else None,
            },
            confidence_score=confidence,
        )

        return [signal]
