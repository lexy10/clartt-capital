"""V25 Trend-Continuation Scalper — SMC continuation entries with phase awareness.

Trades 1m CHoCH-then-Fib-50% pullback retest entries IN the direction of the
4H + 1H bias, but only when both higher timeframes are in a fire-able
"impulse" or "resumption" structural phase. During a 4H retracement we sit
on our hands — buying the 1m HH/HL inside a falling 4H retrace is the most
common way these strategies bleed.

Timeframes (config-driven, but designed for these defaults):
- trend_candles    = 4H  (strategic bias + structural phase)
- structure_candles = 1H  (tactical bias + structural phase; must agree with 4H)
- entry_candles    = 1m  (CHoCH detection + Fib 50% retest trigger)
- M15 resampled internally from 1m (early-warning state machine)
- M5  resampled internally from 1m (second-leg completion check)

Why it's different from v25_structure_scalper:
- structure_scalper waits for a single oversold Stoch on 1H — fires ~1–2 / week.
- This continues with the trend on every pullback, can fire 5–15 / week,
  but only during impulse/resumption — phase awareness keeps the chop out.

Entry pipeline:
1. 4H bias detected (HH/HL or LH/LL) AND 4H structural phase ∈ {impulse, resumption}
2. 1H bias agrees AND 1H structural phase ∈ {impulse, resumption}
3. M15 state machine = aligned (not caution, not stand_down)
4. 1m CHoCH detected against bias → pullback in progress
5. M5 second-leg confirmed (≥1 fractal counter-bias after the CHoCH)
6. Mark pullback swing (last aligned-bias swing → pullback extreme) → 50% Fib
7. Current 1m price within Fib zone AND rejection candle in bias direction
8. Anti-clustering check, ATR sanity, position size from risk

Stops & targets:
- SL: pullback extreme ± buffer (structural)
- TP: configurable R:R (default 2.5 minimum, 3.0 typical)
- Trail: position monitor handles via ATR + structural HL/LH updates
"""

import logging
import statistics
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from src.models import BOSType, Candle, Signal, SignalDirection, StrategyConfig
from src.strategy.base import StrategyAlgorithm
from src.strategy.signal_helpers import build_signal

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Enums & state objects
# ---------------------------------------------------------------------------

class Bias(str, Enum):
    BULLISH = "bullish"
    BEARISH = "bearish"


class Phase(str, Enum):
    IMPULSE = "impulse"          # extending the trend — entries OK
    RETRACEMENT = "retracement"  # pulling back — NO ENTRIES (the chop trap)
    RESUMPTION = "resumption"    # retracement ended, new leg started — entries OK
    EXHAUSTED = "exhausted"      # deep counter move, likely real reversal — stand down


class M15State(str, Enum):
    ALIGNED = "aligned"          # M15 structure matches HTF bias — entries OK
    CAUTION = "caution"          # one M15 CHoCH against bias — skip entries
    STAND_DOWN = "stand_down"    # CHoCH + new counter swing — reversal real, stop


class SwingType(str, Enum):
    HH = "HH"; HL = "HL"; LH = "LH"; LL = "LL"


@dataclass
class SwingPoint:
    price: float
    index: int
    is_high: bool
    swing_type: Optional[SwingType] = None


@dataclass
class PhaseAnalysis:
    """Result of bias + phase detection on a single timeframe."""
    bias: Optional[Bias] = None
    phase: Optional[Phase] = None
    swings: list[SwingPoint] = field(default_factory=list)
    last_aligned_high: Optional[SwingPoint] = None
    last_aligned_low: Optional[SwingPoint] = None
    # The protected-swing level whose break would invalidate the bias
    invalidation: Optional[float] = None
    # The pullback extreme that started the current retracement (if any)
    retrace_extreme: Optional[float] = None


# ---------------------------------------------------------------------------
# Algorithm
# ---------------------------------------------------------------------------


class V25TrendContinuationScalperAlgorithm(StrategyAlgorithm):
    """1m continuation scalper with 4H+1H phase awareness + M15 early-warning."""

    def __init__(self) -> None:
        self._recent_signals: deque[tuple[float, str]] = deque(maxlen=30)
        # Per-instrument M15 state machine
        self._m15_state_by_instrument: dict[str, M15State] = {}

    @staticmethod
    def name() -> str:
        return "v25_trend_continuation_scalper"

    @staticmethod
    def description() -> str:
        return (
            "V25 Trend-Continuation Scalper — fires 1m CHoCH + Fib-50% retest "
            "continuation entries with 4H+1H trend bias AND phase awareness "
            "(impulse/resumption only). M15 state machine pauses entries when "
            "early reversal signs appear. Designed for Deriv R_25."
        )

    @staticmethod
    def default_params() -> dict:
        return {
            # ── Structural fractals (per TF) ──
            "fractal_n_trend": 2,       # 4H: 2 = swings need 2 bars left+right
            "fractal_n_structure": 2,    # 1H
            "fractal_n_entry": 2,        # 1m
            # ── How far back to scan for swings (per TF) ──
            "lookback_trend": 200,       # 4H bars
            "lookback_structure": 200,   # 1H bars
            "lookback_entry": 200,       # 1m bars
            # ── Phase classification ──
            # If retrace extreme is within (impulse_low ± shallow_pct * impulse_size)
            # of the last aligned swing low (bullish case), phase = resumption.
            # If retrace deeper than deep_pct, phase = exhausted (likely real reversal).
            # R_25 has high volatility — retraces often go 50-80% before resuming,
            # so we tune deep_pct relatively high. shallow_pct stays moderate so
            # most fresh impulses register as "impulse" not "retracement".
            "phase_shallow_pct": 0.35,   # < 35% retrace → still impulse
            "phase_deep_pct":    0.88,   # > 88% retrace → exhausted (likely reversal)
            # ── M15 state machine ──
            "m15_resample_bars": 15,     # 15 × 1m = 1 M15 bar
            "m15_lookback_bars": 80,     # ~ 20 hours of M15
            # ── 1m CHoCH detection ──
            "min_candles_after_choch": 3,    # wait for a few candles before reading Fib
            "max_candles_since_choch": 30,    # CHoCH must be recent
            # ── Second leg requirement ──
            "second_leg_min_fractals": 1,    # ≥1 counter-bias fractal after CHoCH
            # ── 1m BOS resumption confirmation (the NEW gate) ──
            #
            # After the CHoCH + second leg, we wait for the 1m to actually
            # break structure back in the trend direction before entering.
            # Concretely: a 1m close above the most recent counter-bias swing
            # high (in bull bias) means the pullback is over and continuation
            # has started. THIS becomes the entry trigger.
            #
            # Without this gate the strategy fires on "price at Fib + wick" —
            # which on 1m is roughly a coin flip. With it, we only enter once
            # the structure says continuation is real.
            "require_resumption_bos": True,
            "bos_freshness": 3,              # BOS must be within last N entry bars to enter
            # ── Fib 50% zone (now OPTIONAL quality filter, off by default) ──
            "use_fib_zone_filter": False,
            "fib_target_pct": 0.5,            # 50% retrace
            "fib_zone_atr_mult": 0.8,         # ± this × 1m ATR around Fib 50
            # ── Rejection / body candle ──
            "rejection_wick_ratio": 0.30,    # min wick/range for the rejection variant
            "require_body_in_direction": True, # BOS bar must close in bias direction (true body)
            # ── ATR sanity ──
            "atr_period": 14,
            "atr_lookback_window": 50,
            "min_atr_ratio": 0.3,
            "max_atr_ratio": 3.5,
            # ── Stops & targets ──
            "sl_atr_buffer": 0.5,           # SL = swing ± (this × 1m ATR)
            "min_sl_atr": 0.6,              # SL distance floor
            "reward_risk_ratio": 3.0,
            # ── Anti-clustering ──
            "cooldown_candles": 5,           # in 1m bars
            "cooldown_price_atr_mult": 1.5,
            # ── Confidence ──
            "base_confidence": 0.62,
            "confluence_filters": [],
            # ── Instrument overrides (V25 specific) ──
            "v25_fib_zone_atr_mult": 0.8,
            "v25_reward_risk_ratio": 3.0,
            "v25_sl_atr_buffer": 0.5,
            # ── Instrument overrides (V10 specific) ──
            "v10_fib_zone_atr_mult": 0.4,
            "v10_reward_risk_ratio": 2.5,
            "v10_sl_atr_buffer": 0.4,
            "v10_min_sl_atr": 0.4,
        }

    @staticmethod
    def param_schema() -> dict:
        return {
            "fractal_n_trend": {"type": "integer", "minimum": 1, "maximum": 5},
            "fractal_n_structure": {"type": "integer", "minimum": 1, "maximum": 5},
            "fractal_n_entry": {"type": "integer", "minimum": 1, "maximum": 5},
            "lookback_trend": {"type": "integer", "minimum": 30, "maximum": 500},
            "lookback_structure": {"type": "integer", "minimum": 30, "maximum": 500},
            "lookback_entry": {"type": "integer", "minimum": 30, "maximum": 500},
            "phase_shallow_pct": {"type": "number", "minimum": 0.10, "maximum": 0.40},
            "phase_deep_pct": {"type": "number", "minimum": 0.60, "maximum": 0.95},
            "m15_resample_bars": {"type": "integer", "minimum": 5, "maximum": 60},
            "m15_lookback_bars": {"type": "integer", "minimum": 20, "maximum": 300},
            "min_candles_after_choch": {"type": "integer", "minimum": 1, "maximum": 20},
            "max_candles_since_choch": {"type": "integer", "minimum": 5, "maximum": 100},
            "second_leg_min_fractals": {"type": "integer", "minimum": 1, "maximum": 5},
            "require_resumption_bos": {"type": "boolean"},
            "bos_freshness": {"type": "integer", "minimum": 1, "maximum": 20},
            "use_fib_zone_filter": {"type": "boolean"},
            "fib_target_pct": {"type": "number", "minimum": 0.30, "maximum": 0.70},
            "fib_zone_atr_mult": {"type": "number", "minimum": 0.10, "maximum": 2.0},
            "rejection_wick_ratio": {"type": "number", "minimum": 0.20, "maximum": 0.70},
            "require_body_in_direction": {"type": "boolean"},
            "atr_period": {"type": "integer", "minimum": 5, "maximum": 50},
            "atr_lookback_window": {"type": "integer", "minimum": 10, "maximum": 200},
            "min_atr_ratio": {"type": "number", "minimum": 0.10, "maximum": 1.50},
            "max_atr_ratio": {"type": "number", "minimum": 1.50, "maximum": 10.0},
            "sl_atr_buffer": {"type": "number", "minimum": 0.10, "maximum": 2.0},
            "min_sl_atr": {"type": "number", "minimum": 0.10, "maximum": 3.0},
            "reward_risk_ratio": {"type": "number", "minimum": 1.5, "maximum": 10.0},
            "cooldown_candles": {"type": "integer", "minimum": 0, "maximum": 100},
            "cooldown_price_atr_mult": {"type": "number", "minimum": 0.30, "maximum": 5.0},
            "base_confidence": {"type": "number", "minimum": 0.30, "maximum": 0.90},
            "confluence_filters": {"type": "array", "items": {"type": "string"}},
        }

    def _resolve_params(self, config: StrategyConfig) -> dict:
        """Merge defaults with strategy config + apply instrument-specific overrides."""
        params = {**self.default_params(), **config.algorithm_params}
        instrument = (config.instruments[0] if config.instruments else "").upper()
        if "R_25" in instrument or "V25" in instrument:
            prefix = "v25_"
        elif "R_10" in instrument or "V10" in instrument:
            prefix = "v10_"
        else:
            return params
        for key, value in list(params.items()):
            if key.startswith(prefix):
                base_key = key[len(prefix):]
                if base_key not in config.algorithm_params:
                    params[base_key] = value
        return params

    # ── Anti-clustering ────────────────────────────────────────────

    def _is_in_cooldown(self, entry_price: float, current_atr: float, params: dict) -> bool:
        threshold = params["cooldown_price_atr_mult"] * current_atr
        for prev_price, _ in self._recent_signals:
            if abs(entry_price - prev_price) < threshold:
                return True
        return False

    def _record_signal(self, entry_price: float, timestamp: str) -> None:
        self._recent_signals.append((entry_price, timestamp))

    # ── Main entry ─────────────────────────────────────────────────

    def analyze(
        self,
        entry_candles: list[Candle],
        structure_candles: list[Candle],
        trend_candles: list[Candle],
        config: StrategyConfig,
        **kwargs,
    ) -> list[Signal]:
        params = self._resolve_params(config)
        instrument = (config.instruments[0] if config.instruments else "").upper()

        # ── Minimum candle guards ──
        min_trend = params["fractal_n_trend"] * 2 + 5
        min_struct = params["fractal_n_structure"] * 2 + 5
        min_entry = max(
            params["m15_resample_bars"] * 3,
            params["fractal_n_entry"] * 2 + params["max_candles_since_choch"] + 10,
            params["atr_period"] + 1,
        )
        if len(trend_candles) < min_trend or len(structure_candles) < min_struct:
            return []
        if len(entry_candles) < min_entry:
            return []

        # ============================================================
        # GATE 1: 4H bias + phase (strategic)
        # ============================================================
        h4 = _analyze_phase(
            trend_candles,
            fractal_n=params["fractal_n_trend"],
            lookback=params["lookback_trend"],
            shallow_pct=params["phase_shallow_pct"],
            deep_pct=params["phase_deep_pct"],
        )
        if h4.bias is None:
            return []
        if h4.phase not in (Phase.IMPULSE, Phase.RESUMPTION):
            # In retracement → the chop trap. Stand down.
            return []

        # ============================================================
        # GATE 2: 1H bias + phase (tactical) — MUST AGREE with 4H
        # ============================================================
        h1 = _analyze_phase(
            structure_candles,
            fractal_n=params["fractal_n_structure"],
            lookback=params["lookback_structure"],
            shallow_pct=params["phase_shallow_pct"],
            deep_pct=params["phase_deep_pct"],
        )
        if h1.bias is None or h1.bias != h4.bias:
            return []
        if h1.phase not in (Phase.IMPULSE, Phase.RESUMPTION):
            return []

        bias = h4.bias
        direction = SignalDirection.BUY if bias == Bias.BULLISH else SignalDirection.SELL

        # ============================================================
        # GATE 3: M15 early-warning state machine
        # ============================================================
        m15_candles = _resample_candles(entry_candles, params["m15_resample_bars"])
        if len(m15_candles) < 10:
            return []
        prev_state = self._m15_state_by_instrument.get(instrument, M15State.ALIGNED)
        m15_state = _update_m15_state(
            m15_candles,
            bias,
            prev_state,
            fractal_n=params["fractal_n_structure"],
            lookback=params["m15_lookback_bars"],
        )
        self._m15_state_by_instrument[instrument] = m15_state
        if m15_state != M15State.ALIGNED:
            return []

        # ============================================================
        # GATE 4: 1m CHoCH against bias = pullback signal
        # ============================================================
        choch = _detect_recent_choch_against_bias(
            entry_candles,
            bias,
            fractal_n=params["fractal_n_entry"],
            lookback=params["lookback_entry"],
            max_age=params["max_candles_since_choch"],
            min_age=params["min_candles_after_choch"],
        )
        if choch is None:
            return []
        # choch is (choch_index, last_aligned_extreme_price, last_aligned_extreme_idx)
        choch_idx, anchor_price, anchor_idx = choch

        # ============================================================
        # GATE 5: Second leg completed
        # We need at least N counter-bias fractals AFTER the CHoCH break,
        # so we know the pullback's full shape (not just the initial impulse).
        # ============================================================
        if not _has_completed_second_leg(
            entry_candles,
            bias,
            after_idx=choch_idx,
            fractal_n=params["fractal_n_entry"],
            min_fractals=params["second_leg_min_fractals"],
        ):
            return []

        # ============================================================
        # GATE 6: 1m BOS confirmation that pullback is over
        #
        # Wait for a 1m close above the most recent counter-bias swing high
        # (in bullish bias) — the structural signal that the pullback has
        # ended and continuation has resumed. The BOS candle's close becomes
        # the entry; the pullback extreme becomes the SL anchor.
        # ============================================================
        if params.get("require_resumption_bos", True):
            bos_result = _detect_resumption_bos(
                entry_candles, bias, choch_idx,
                fractal_n=params["fractal_n_entry"],
                freshness=params["bos_freshness"],
            )
            if bos_result is None:
                return []
            bos_idx, pullback_extreme = bos_result
            entry_candle = entry_candles[bos_idx]
            current_price = entry_candle.close
        else:
            # Legacy path — entry off current price + Fib retest
            pullback_extreme = _find_pullback_extreme(entry_candles, anchor_idx, bias)
            if pullback_extreme is None:
                return []
            bos_idx = len(entry_candles) - 1
            entry_candle = entry_candles[-1]
            current_price = entry_candle.close

        # Compute 1m ATR for SL/TP sanity (computed once, used below)
        e_highs = [c.high for c in entry_candles]
        e_lows = [c.low for c in entry_candles]
        e_closes = [c.close for c in entry_candles]
        atr_values = _compute_atr(e_highs, e_lows, e_closes, params["atr_period"])
        if not atr_values or atr_values[-1] <= 0:
            return []
        current_atr = atr_values[-1]

        # ATR regime
        lookback_atrs = atr_values[-params["atr_lookback_window"]:] if len(atr_values) >= params["atr_lookback_window"] else atr_values
        median_atr = statistics.median(lookback_atrs)
        if median_atr <= 0:
            return []
        atr_ratio = current_atr / median_atr
        if atr_ratio < params["min_atr_ratio"] or atr_ratio > params["max_atr_ratio"]:
            return []

        # Optional: Fib zone quality filter — narrows entries to those that
        # came after a substantial (50%+) retracement of the prior impulse.
        if params.get("use_fib_zone_filter", False):
            if bias == Bias.BULLISH:
                fib_target = pullback_extreme + params["fib_target_pct"] * (anchor_price - pullback_extreme)
            else:
                fib_target = pullback_extreme - params["fib_target_pct"] * (pullback_extreme - anchor_price)
            zone_half = params["fib_zone_atr_mult"] * current_atr
            # Pullback extreme should have reached at least the Fib level
            if bias == Bias.BULLISH and pullback_extreme > fib_target + zone_half:
                return []
            if bias == Bias.BEARISH and pullback_extreme < fib_target - zone_half:
                return []
        else:
            fib_target = None  # noqa: F841

        # ============================================================
        # GATE 7: Body confirms direction
        #
        # The BOS bar should close in the bias direction relative to its open.
        # A small bullish bar that just barely closes above the swing is much
        # weaker than one with a strong positive body.
        # ============================================================
        if params.get("require_body_in_direction", True):
            if not _has_body_in_direction(entry_candle, bias):
                return []

        # ============================================================
        # GATE 8: Anti-clustering
        # ============================================================
        if self._is_in_cooldown(current_price, current_atr, params):
            return []

        # ============================================================
        # SL + TP: structural SL below pullback extreme, fixed R:R TP
        # ============================================================
        buffer = params["sl_atr_buffer"] * current_atr
        if bias == Bias.BULLISH:
            stop_loss = pullback_extreme - buffer
            sl_distance = current_price - stop_loss
        else:
            stop_loss = pullback_extreme + buffer
            sl_distance = stop_loss - current_price

        min_sl = params["min_sl_atr"] * current_atr
        if sl_distance < min_sl:
            if bias == Bias.BULLISH:
                stop_loss = current_price - min_sl
            else:
                stop_loss = current_price + min_sl
            sl_distance = min_sl
        if sl_distance <= 0:
            return []

        tp_distance = params["reward_risk_ratio"] * sl_distance
        take_profit = current_price + tp_distance if bias == Bias.BULLISH else current_price - tp_distance

        # ============================================================
        # Confidence score
        # ============================================================
        confidence = params["base_confidence"]
        # 4H impulse fresh + 1H impulse fresh → high confidence
        if h4.phase == Phase.IMPULSE and h1.phase == Phase.IMPULSE:
            confidence += 0.10
        elif h4.phase == Phase.RESUMPTION and h1.phase == Phase.RESUMPTION:
            confidence += 0.08
        # Tighter Fib hit (only if Fib zone filter active)
        if fib_target is not None:
            fib_distance = abs(current_price - fib_target)
            if fib_distance < 0.25 * current_atr:
                confidence += 0.05
        # BOS freshness — most recent BOS is highest quality
        bos_freshness_actual = (len(entry_candles) - 1) - bos_idx
        if bos_freshness_actual == 0:
            confidence += 0.05  # BOS happened on the very last completed bar
        # Volatility sweet spot
        if 0.7 <= atr_ratio <= 1.3:
            confidence += 0.05
        # Body strength of the BOS bar
        rng = entry_candle.high - entry_candle.low
        body = abs(entry_candle.close - entry_candle.open)
        if rng > 0 and (body / rng) > 0.5:
            confidence += 0.05
        # Legacy wick confidence (only when Fib zone is used too)
        rng = entry_candle.high - entry_candle.low
        if fib_target is not None and rng > 0:
            if bias == Bias.BULLISH:
                wick = entry_candle.open - entry_candle.low if entry_candle.open > entry_candle.close else (
                    entry_candle.close - entry_candle.low
                )
            else:
                wick = entry_candle.high - entry_candle.open if entry_candle.open < entry_candle.close else (
                    entry_candle.high - entry_candle.close
                )
            if wick / rng > 0.5:
                confidence += 0.05

        # Apply confluence filters if configured
        filter_names = params.get("confluence_filters", [])
        if filter_names and isinstance(filter_names, list):
            from src.strategy.filters.apply import apply_confluence_filters
            confidence += apply_confluence_filters(
                filter_names, entry_candles, direction, params,
            )

        confidence = max(0.0, min(1.0, confidence))
        if confidence < config.min_confidence_score:
            return []

        # ============================================================
        # Build signal
        # ============================================================
        try:
            timestamp = datetime.fromisoformat(
                entry_candles[-1].timestamp.replace("Z", "+00:00")
            )
        except (ValueError, AttributeError):
            timestamp = datetime.now(timezone.utc)

        self._record_signal(current_price, timestamp.isoformat())

        bos_type = BOSType.BULLISH if direction == SignalDirection.BUY else BOSType.BEARISH
        signal = build_signal(
            instrument=instrument,
            direction=direction,
            entry_price=current_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            config=config,
            candles=entry_candles,
            timestamp=timestamp,
            order_block_id=str(uuid.uuid4()),
            extra_metadata={
                "bos_type": bos_type,
                "bias": bias.value,
                "phase_4h": h4.phase.value if h4.phase else "unknown",
                "phase_1h": h1.phase.value if h1.phase else "unknown",
                "m15_state": m15_state.value,
                "pullback_extreme": round(pullback_extreme, 2),
                "anchor": round(anchor_price, 2),
                "atr_ratio": round(atr_ratio, 2),
                "sl_distance_atr": round(sl_distance / current_atr, 2),
                "bos_idx_from_end": (len(entry_candles) - 1) - bos_idx,
            },
            confidence_score=confidence,
        )

        logger.info(
            "TrendContinuation: %s %s @ %.2f | SL=%.2f TP=%.2f | "
            "4H=%s/%s 1H=%s/%s M15=%s | atr_ratio=%.2f conf=%.2f",
            direction.value, instrument, current_price, stop_loss, take_profit,
            bias.value, h4.phase.value if h4.phase else "?",
            bias.value, h1.phase.value if h1.phase else "?",
            m15_state.value, atr_ratio, confidence,
        )
        return [signal]


# ===========================================================================
# Phase analysis — the core "are we in impulse, retrace, or resumption?"
# ===========================================================================


def _analyze_phase(
    candles: list[Candle],
    fractal_n: int,
    lookback: int,
    shallow_pct: float,
    deep_pct: float,
) -> PhaseAnalysis:
    """Detect bias AND structural phase from a candle stream.

    Bias is set by the most recent confirmed BOS in either direction.
    Phase is determined by whether price is still extending the bias-aligned
    leg (impulse), pulling back inside that leg (retracement), starting a
    new aligned leg after a pullback (resumption), or has gone too deep to
    be "just a retracement" (exhausted).
    """
    swings = _find_fractals(candles, fractal_n, lookback)
    swings = _classify_swings(swings)
    if len(swings) < 4:
        return PhaseAnalysis()

    closes = [c.close for c in candles]
    swing_by_idx: dict[int, list[SwingPoint]] = {}
    for s in swings:
        swing_by_idx.setdefault(s.index, []).append(s)

    bias: Optional[Bias] = None
    last_sh: Optional[tuple[int, float]] = None
    last_sl: Optional[tuple[int, float]] = None
    # Track the swing that most recently extended the trend (the impulse
    # high in a bull market, impulse low in a bear).
    last_aligned_extreme_high: Optional[SwingPoint] = None
    last_aligned_extreme_low: Optional[SwingPoint] = None
    # Track the protected swing (whose break = CHoCH against current bias)
    protected: Optional[float] = None

    # ── First pass: walk candles, identify bias-establishing BOSs ──
    for i in range(len(candles)):
        if i in swing_by_idx:
            for s in swing_by_idx[i]:
                if s.is_high:
                    last_sh = (s.index, s.price)
                else:
                    last_sl = (s.index, s.price)
        c = closes[i]
        # Bullish BOS — close above last unbroken swing high
        if last_sh is not None and c > last_sh[1]:
            bias = Bias.BULLISH
            sh_swing = next(
                (s for s in swings if s.index == last_sh[0] and s.is_high),
                None,
            )
            if sh_swing is not None:
                last_aligned_extreme_high = sh_swing
            if last_sl is not None:
                protected = last_sl[1]
            last_sh = None
        # Bearish BOS
        if last_sl is not None and c < last_sl[1]:
            bias = Bias.BEARISH
            sl_swing = next(
                (s for s in swings if s.index == last_sl[0] and not s.is_high),
                None,
            )
            if sl_swing is not None:
                last_aligned_extreme_low = sl_swing
            if last_sh is not None:
                protected = last_sh[1]
            last_sl = None

    if bias is None:
        return PhaseAnalysis(swings=swings)

    # ── Phase classification ──
    current = closes[-1]

    if bias == Bias.BULLISH:
        # We need at least one HH (aligned-extreme) AND one HL (impulse start
        # or last bias-aligned low) to measure retracement.
        if last_aligned_extreme_high is None:
            return PhaseAnalysis(
                bias=bias, swings=swings,
                invalidation=protected,
                last_aligned_high=last_aligned_extreme_high,
            )
        # Find the most recent aligned HL (the swing low that anchors the impulse leg)
        aligned_lows = [
            s for s in swings
            if not s.is_high
            and s.index < last_aligned_extreme_high.index
            and s.swing_type in (SwingType.HL, SwingType.LL)
        ]
        anchor_low = aligned_lows[-1] if aligned_lows else None
        if anchor_low is None:
            return PhaseAnalysis(
                bias=bias, swings=swings,
                invalidation=protected,
                last_aligned_high=last_aligned_extreme_high,
            )

        impulse_size = last_aligned_extreme_high.price - anchor_low.price
        if impulse_size <= 0:
            return PhaseAnalysis(
                bias=bias, swings=swings,
                invalidation=protected,
                last_aligned_high=last_aligned_extreme_high,
                last_aligned_low=anchor_low,
            )
        # Find the LOWEST point since the impulse top — that's the retrace extreme
        retrace_low = min(
            (c.low for c in candles[last_aligned_extreme_high.index + 1:]),
            default=current,
        )
        retrace_pct = (last_aligned_extreme_high.price - retrace_low) / impulse_size
        if retrace_pct > deep_pct:
            phase = Phase.EXHAUSTED
        elif retrace_pct < shallow_pct:
            phase = Phase.IMPULSE
        else:
            # We're in the meat of a retracement. Has price started moving back up?
            # If the most recent close has reclaimed above the midpoint of the
            # retrace, treat it as resumption; otherwise still in retracement.
            mid = retrace_low + 0.5 * (last_aligned_extreme_high.price - retrace_low)
            if current > mid:
                phase = Phase.RESUMPTION
            else:
                phase = Phase.RETRACEMENT
        return PhaseAnalysis(
            bias=bias, phase=phase, swings=swings,
            last_aligned_high=last_aligned_extreme_high,
            last_aligned_low=anchor_low,
            invalidation=protected,
            retrace_extreme=retrace_low,
        )

    # BEARISH mirror
    if last_aligned_extreme_low is None:
        return PhaseAnalysis(
            bias=bias, swings=swings, invalidation=protected,
            last_aligned_low=last_aligned_extreme_low,
        )
    aligned_highs = [
        s for s in swings
        if s.is_high
        and s.index < last_aligned_extreme_low.index
        and s.swing_type in (SwingType.LH, SwingType.HH)
    ]
    anchor_high = aligned_highs[-1] if aligned_highs else None
    if anchor_high is None:
        return PhaseAnalysis(
            bias=bias, swings=swings, invalidation=protected,
            last_aligned_low=last_aligned_extreme_low,
        )
    impulse_size = anchor_high.price - last_aligned_extreme_low.price
    if impulse_size <= 0:
        return PhaseAnalysis(
            bias=bias, swings=swings, invalidation=protected,
            last_aligned_low=last_aligned_extreme_low,
            last_aligned_high=anchor_high,
        )
    retrace_high = max(
        (c.high for c in candles[last_aligned_extreme_low.index + 1:]),
        default=current,
    )
    retrace_pct = (retrace_high - last_aligned_extreme_low.price) / impulse_size
    if retrace_pct > deep_pct:
        phase = Phase.EXHAUSTED
    elif retrace_pct < shallow_pct:
        phase = Phase.IMPULSE
    else:
        mid = retrace_high - 0.5 * (retrace_high - last_aligned_extreme_low.price)
        if current < mid:
            phase = Phase.RESUMPTION
        else:
            phase = Phase.RETRACEMENT
    return PhaseAnalysis(
        bias=bias, phase=phase, swings=swings,
        last_aligned_low=last_aligned_extreme_low,
        last_aligned_high=anchor_high,
        invalidation=protected,
        retrace_extreme=retrace_high,
    )


# ===========================================================================
# M15 state machine — early-warning filter
# ===========================================================================


def _update_m15_state(
    m15_candles: list[Candle],
    htf_bias: Bias,
    prev_state: M15State,
    fractal_n: int,
    lookback: int,
) -> M15State:
    """Drive the M15 state machine forward by one tick.

    States: aligned → caution (M15 prints first CHoCH against HTF)
           → stand_down (M15 prints new counter-bias swing after CHoCH)
           OR → aligned (M15 reclaims with new HH/LL aligned with HTF — false alarm)

    We re-derive state every tick from current M15 structure rather than
    persist transitions; this makes the function pure for backtesting and
    means we never get stuck in a stale state.
    """
    swings = _find_fractals(m15_candles, fractal_n, lookback)
    swings = _classify_swings(swings)
    if len(swings) < 4:
        return M15State.ALIGNED  # not enough data — be permissive

    # Did M15 break structure AGAINST the HTF bias?
    closes = [c.close for c in m15_candles]
    if htf_bias == Bias.BULLISH:
        # Look for: most recent swing low broken by close → CHoCH down
        swing_lows = [s for s in swings if not s.is_high]
        if len(swing_lows) < 2:
            return M15State.ALIGNED
        # Walk from oldest to newest, track the last unbroken swing low
        last_sl = None
        choch_idx = None
        for i in range(len(m15_candles)):
            for s in swings:
                if s.index == i and not s.is_high:
                    last_sl = (s.index, s.price)
            if last_sl is not None and closes[i] < last_sl[1]:
                choch_idx = i
                last_sl = None  # consumed
        if choch_idx is None:
            return M15State.ALIGNED

        # CHoCH happened. Has M15 since printed a NEW lower high?
        # (counter-bias swing = full reversal warning)
        new_counter_swings = [
            s for s in swings
            if s.index > choch_idx and s.is_high
            and s.swing_type == SwingType.LH
        ]
        if new_counter_swings:
            return M15State.STAND_DOWN
        # Or has M15 reclaimed — new HH = false alarm, back to aligned
        recovery = [
            s for s in swings
            if s.index > choch_idx and s.is_high
            and s.swing_type == SwingType.HH
        ]
        if recovery:
            return M15State.ALIGNED
        return M15State.CAUTION

    # BEARISH mirror
    swing_highs = [s for s in swings if s.is_high]
    if len(swing_highs) < 2:
        return M15State.ALIGNED
    last_sh = None
    choch_idx = None
    for i in range(len(m15_candles)):
        for s in swings:
            if s.index == i and s.is_high:
                last_sh = (s.index, s.price)
        if last_sh is not None and closes[i] > last_sh[1]:
            choch_idx = i
            last_sh = None
    if choch_idx is None:
        return M15State.ALIGNED
    new_counter_swings = [
        s for s in swings
        if s.index > choch_idx and not s.is_high
        and s.swing_type == SwingType.HL
    ]
    if new_counter_swings:
        return M15State.STAND_DOWN
    recovery = [
        s for s in swings
        if s.index > choch_idx and not s.is_high
        and s.swing_type == SwingType.LL
    ]
    if recovery:
        return M15State.ALIGNED
    return M15State.CAUTION


# ===========================================================================
# 1m CHoCH detection
# ===========================================================================


def _detect_recent_choch_against_bias(
    candles: list[Candle],
    bias: Bias,
    fractal_n: int,
    lookback: int,
    max_age: int,
    min_age: int,
) -> Optional[tuple[int, float, int]]:
    """Find the most recent 1m CHoCH against the HTF bias.

    Returns (choch_idx, anchor_price, anchor_idx) where:
      - choch_idx is the candle index where the counter-bias break occurred
      - anchor is the bias-aligned swing extreme that started the pullback
        (last HH in bullish bias, last LL in bearish bias)

    Returns None if no qualifying CHoCH exists in the lookback window.
    """
    swings = _find_fractals(candles, fractal_n, lookback)
    if len(swings) < 4:
        return None
    closes = [c.close for c in candles]
    n_candles = len(candles)

    if bias == Bias.BULLISH:
        # CHoCH down = close breaking the most recent swing low.
        # Anchor = the swing high that preceded that swing low (the leg's top).
        swing_lows = sorted([s for s in swings if not s.is_high], key=lambda s: s.index)
        swing_highs = sorted([s for s in swings if s.is_high], key=lambda s: s.index)
        if not swing_lows or not swing_highs:
            return None
        # Walk forward to find the LATEST CHoCH within max_age
        last_sl_idx = None
        last_sl_price = None
        choch_idx = None
        for i in range(n_candles):
            for s in swing_lows:
                if s.index == i:
                    last_sl_idx = i
                    last_sl_price = s.price
            if last_sl_price is not None and closes[i] < last_sl_price:
                if n_candles - 1 - i <= max_age and n_candles - 1 - i >= min_age:
                    choch_idx = i
                last_sl_price = None
        if choch_idx is None:
            return None
        # Anchor: the most recent swing HIGH before this CHoCH
        anchor = next(
            (s for s in reversed(swing_highs) if s.index < choch_idx),
            None,
        )
        if anchor is None:
            return None
        return (choch_idx, anchor.price, anchor.index)

    # BEARISH mirror
    swing_highs = sorted([s for s in swings if s.is_high], key=lambda s: s.index)
    swing_lows = sorted([s for s in swings if not s.is_high], key=lambda s: s.index)
    if not swing_highs or not swing_lows:
        return None
    last_sh_price = None
    choch_idx = None
    for i in range(n_candles):
        for s in swing_highs:
            if s.index == i:
                last_sh_price = s.price
        if last_sh_price is not None and closes[i] > last_sh_price:
            if n_candles - 1 - i <= max_age and n_candles - 1 - i >= min_age:
                choch_idx = i
            last_sh_price = None
    if choch_idx is None:
        return None
    anchor = next(
        (s for s in reversed(swing_lows) if s.index < choch_idx),
        None,
    )
    if anchor is None:
        return None
    return (choch_idx, anchor.price, anchor.index)


def _has_completed_second_leg(
    candles: list[Candle],
    bias: Bias,
    after_idx: int,
    fractal_n: int,
    min_fractals: int,
) -> bool:
    """After the CHoCH, require at least min_fractals counter-bias fractals.

    Translation: don't enter on the very first lurch down (in bull bias) —
    wait until the pullback has formed its full shape (at least one or two
    swing lows after the CHoCH break).
    """
    # Limit scan to candles AFTER the CHoCH
    sliced = candles[after_idx:]
    if len(sliced) < fractal_n * 2 + 1:
        return False
    swings = _find_fractals(sliced, fractal_n, len(sliced))
    if bias == Bias.BULLISH:
        counter = [s for s in swings if not s.is_high]  # need lows in the pullback
    else:
        counter = [s for s in swings if s.is_high]
    return len(counter) >= min_fractals


def _find_pullback_extreme(
    candles: list[Candle], anchor_idx: int, bias: Bias,
) -> Optional[float]:
    """Lowest low (bull) / highest high (bear) since the pullback anchor."""
    if anchor_idx >= len(candles):
        return None
    sliced = candles[anchor_idx:]
    if not sliced:
        return None
    if bias == Bias.BULLISH:
        return min(c.low for c in sliced)
    return max(c.high for c in sliced)


# ===========================================================================
# 1m BOS confirmation — the new resumption gate
# ===========================================================================


def _detect_resumption_bos(
    candles: list[Candle],
    bias: Bias,
    after_idx: int,
    fractal_n: int,
    freshness: int,
) -> Optional[tuple[int, float]]:
    """Detect a 1m Break of Structure in the bias direction confirming the pullback is over.

    In bullish bias:
    1. Locate the lowest low after the CHoCH (= pullback extreme).
    2. Find counter-bias swing highs (bounce tops) that formed AFTER the extreme.
    3. Look for a candle that CLOSED above the most recent of those swing highs.
    4. The BOS must have happened in the last ``freshness`` bars (we only enter
       fresh signals, not stale ones from minutes ago).

    Returns (bos_bar_index, pullback_extreme_price) or None if no fresh BOS.

    Mirrors for bearish bias (highest high, swing lows, close below).
    """
    n = len(candles)
    if after_idx >= n - fractal_n * 2 - 1:
        return None

    # Step 1: pullback extreme
    if bias == Bias.BULLISH:
        extreme_idx = after_idx
        extreme_price = candles[after_idx].low
        for i in range(after_idx, n):
            if candles[i].low < extreme_price:
                extreme_price = candles[i].low
                extreme_idx = i
    else:
        extreme_idx = after_idx
        extreme_price = candles[after_idx].high
        for i in range(after_idx, n):
            if candles[i].high > extreme_price:
                extreme_price = candles[i].high
                extreme_idx = i

    # Step 2: need at least 2*fractal_n + 1 bars after extreme to form a swing
    after_extreme = candles[extreme_idx:]
    if len(after_extreme) < fractal_n * 2 + 2:
        return None
    sub_swings = _find_fractals(after_extreme, fractal_n, len(after_extreme))
    if bias == Bias.BULLISH:
        bounce_swings = [s for s in sub_swings if s.is_high]
    else:
        bounce_swings = [s for s in sub_swings if not s.is_high]
    if not bounce_swings:
        return None

    # Step 3: walk forward to find any BOS — track the most recent one
    most_recent_bos: Optional[int] = None
    for sw in bounce_swings:
        sw_global_idx = extreme_idx + sw.index
        for i in range(sw_global_idx + 1, n):
            if bias == Bias.BULLISH:
                if candles[i].close > sw.price:
                    most_recent_bos = i
                    break  # consumed — move to next (more recent) swing
            else:
                if candles[i].close < sw.price:
                    most_recent_bos = i
                    break

    if most_recent_bos is None:
        return None

    # Step 4: must be fresh — within last ``freshness`` bars
    if (n - 1) - most_recent_bos > freshness:
        return None
    return (most_recent_bos, extreme_price)


def _has_body_in_direction(candle: Candle, bias: Bias) -> bool:
    """Confirms the BOS bar closed in the bias direction relative to its open.

    A close that's merely above the swing high but with a small/red body is
    weak. A strong green body says "the move continues."
    """
    if bias == Bias.BULLISH:
        return candle.close > candle.open
    return candle.close < candle.open


# ===========================================================================
# Rejection candle
# ===========================================================================


def _has_rejection_in_direction(
    candle: Candle, bias: Bias, wick_ratio: float,
) -> bool:
    """Confirms the entry candle has a rejection wick in the bias direction.

    Bull bias: lower wick must be ≥ wick_ratio of the candle's full range,
    and the close must be above the open (or at least not deeply red).
    Bear bias: mirror.
    """
    rng = candle.high - candle.low
    if rng <= 0:
        return False
    if bias == Bias.BULLISH:
        body_low = min(candle.open, candle.close)
        lower_wick = body_low - candle.low
        if lower_wick / rng < wick_ratio:
            return False
        # Don't accept deeply bearish candles
        return candle.close >= candle.open or (candle.open - candle.close) / rng < 0.4
    # Bearish
    body_high = max(candle.open, candle.close)
    upper_wick = candle.high - body_high
    if upper_wick / rng < wick_ratio:
        return False
    return candle.close <= candle.open or (candle.close - candle.open) / rng < 0.4


# ===========================================================================
# Helpers (copied to keep this algorithm file self-contained, per the
# repo convention used by other algorithms in this directory)
# ===========================================================================


def _find_fractals(candles: list[Candle], n: int, lookback: int) -> list[SwingPoint]:
    start_idx = max(n, len(candles) - lookback)
    end_idx = len(candles) - n
    swings: list[SwingPoint] = []
    for i in range(start_idx, end_idx):
        is_high = True
        for k in range(1, n + 1):
            if candles[i - k].high >= candles[i].high or candles[i + k].high >= candles[i].high:
                is_high = False
                break
        if is_high:
            swings.append(SwingPoint(candles[i].high, i, True))
        is_low = True
        for k in range(1, n + 1):
            if candles[i - k].low <= candles[i].low or candles[i + k].low <= candles[i].low:
                is_low = False
                break
        if is_low:
            swings.append(SwingPoint(candles[i].low, i, False))
    swings.sort(key=lambda s: s.index)
    return swings


def _classify_swings(swings: list[SwingPoint]) -> list[SwingPoint]:
    last_high: Optional[SwingPoint] = None
    last_low: Optional[SwingPoint] = None
    for s in swings:
        if s.is_high:
            if last_high is not None:
                s.swing_type = SwingType.HH if s.price > last_high.price else SwingType.LH
            last_high = s
        else:
            if last_low is not None:
                s.swing_type = SwingType.HL if s.price > last_low.price else SwingType.LL
            last_low = s
    return swings


def _resample_candles(candles: list[Candle], bars_per_unit: int) -> list[Candle]:
    """Aggregate every `bars_per_unit` consecutive candles into one.

    Used to synthesize M15 / M5 streams from the 1m entry candles so the
    algorithm doesn't need an extra framework-level timeframe wired in.
    """
    if bars_per_unit <= 1 or len(candles) < bars_per_unit:
        return candles
    out: list[Candle] = []
    # Drop the partial leading remainder so each output bar is a full unit
    remainder = len(candles) % bars_per_unit
    start = remainder if remainder > 0 else 0
    # Inherit instrument/timeframe metadata from the source bars so the
    # synthesized candles pass model validation downstream.
    inst = getattr(candles[0], "instrument", "")
    # Map common bars_per_unit values to the closest registered Timeframe
    # enum. Candle.timeframe is strict — we have to stay within the allowed set.
    synth_tf_map = {3: "5m", 5: "5m", 15: "15m", 30: "30m", 60: "1h"}
    synth_tf = synth_tf_map.get(bars_per_unit, "5m")
    for i in range(start, len(candles), bars_per_unit):
        bucket = candles[i: i + bars_per_unit]
        if len(bucket) < bars_per_unit:
            break
        out.append(Candle(
            instrument=inst,
            timeframe=synth_tf,
            timestamp=bucket[0].timestamp,
            open=bucket[0].open,
            high=max(c.high for c in bucket),
            low=min(c.low for c in bucket),
            close=bucket[-1].close,
            volume=sum(getattr(c, "volume", 0) or 0 for c in bucket),
        ))
    return out


def _compute_atr(highs, lows, closes, period):
    n = len(closes)
    if n < period + 1:
        return []
    tr_list = []
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
