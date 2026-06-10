"""V25 Structure Scalper — optimized for Deriv Volatility 25 Index.

Market-structure trend following with Stochastic pullback entry, designed
specifically for V25's moderate-volatility, mean-reverting characteristics.

Timeframes: 4H (swing structure) → 1H (Stochastic entry) → 5m (confirmation)

Core strengths for V25:
- V25 has consistent ~25% annualized volatility with predictable ATR
- Mean-reverting micro-structure creates reliable pullback entries
- Moderate moves (not as wild as V75) suit 2.5-3R fixed targets
- 24/7 trading, no gaps, pure technical edge

Technique:
- Williams fractals → HH/HL/LH/LL swing classification for trend bias
- Optional BOS (Break of Structure) confirmation for stricter trend detection
- Premium/Discount zone filtering (optional — found to reduce V25 signals)
- Stochastic(14,3,3) on 1H for pullback entry timing
- Structural SL (beyond protected swing) + configurable R:R TP
"""

import logging
import statistics
import uuid
from collections import deque
from datetime import datetime, timezone
from enum import Enum

from src.models import BOSType, Candle, Signal, SignalDirection, StrategyConfig
from src.strategy.base import StrategyAlgorithm
from src.strategy.signal_helpers import build_signal

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Market structure types
# ---------------------------------------------------------------------------

class SwingType(str, Enum):
    HH = "HH"
    HL = "HL"
    LH = "LH"
    LL = "LL"


class Bias(str, Enum):
    BULLISH = "bullish"
    BEARISH = "bearish"


class SwingPoint:
    __slots__ = ("price", "index", "is_high", "swing_type")

    def __init__(self, price: float, index: int, is_high: bool, swing_type: SwingType | None = None):
        self.price = price
        self.index = index
        self.is_high = is_high
        self.swing_type = swing_type


class StructureResult:
    """Result of market structure analysis."""

    __slots__ = ("bias", "invalidation", "range_low", "range_high", "swings", "bos_count")

    def __init__(self, bias, invalidation, range_low, range_high, swings, bos_count=0):
        self.bias = bias
        self.invalidation = invalidation      # protected swing level (CHoCH trigger)
        self.range_low = range_low            # dealing-range low (for premium/discount)
        self.range_high = range_high          # dealing-range high
        self.swings = swings
        self.bos_count = bos_count            # consecutive BOS in trend direction (strength)


class V25StructureScalperAlgorithm(StrategyAlgorithm):
    """Smart-Money structure (BOS) + Stochastic pullback scalper.

    Entry gates:
    1. Market structure (4H): confirmed BOS sets bias (bullish/bearish)
    2. Premium/Discount: pullback must be in discount (buy) / premium (sell)
    3. Stochastic pullback (1H): %K oversold/overbought + momentum shift
    4. Confirmation (5m/15m): candle closes in trend direction
    + volatility regime + anti-clustering

    SL: structural (beyond protected swing). TP: configurable R:R.
    """

    def __init__(self) -> None:
        self._recent_signals: deque[tuple[float, str]] = deque(maxlen=20)

    @staticmethod
    def name() -> str:
        return "v25_structure_scalper"

    @staticmethod
    def description() -> str:
        return (
            "V25 Structure Scalper — market-structure trend following with "
            "Stochastic pullback entry, optimized for Volatility 25 Index. "
            "4H swing structure for trend, 1H Stochastic for entry timing, "
            "5m confirmation. Structural SL, 3R target. Best on R_25."
        )

    @staticmethod
    def default_params() -> dict:
        return {
            # --- Market structure ---
            "fractal_n": 2,             # N-bar fractal confirmation
            "structure_lookback": 120,  # 4H candles to scan for swings
            "trend_lookback": 30,
            "require_bos": True,        # Require confirmed Break of Structure
            "min_bos_count": 1,         # Min consecutive BOS for valid trend
            # --- Premium / Discount ---
            "use_premium_discount": True,
            "discount_threshold": 0.5,  # Buy only below this fraction of range; sell above (1-this)
            # --- Stochastic ---
            "stoch_k_period": 14,
            "stoch_k_smooth": 3,
            "stoch_d_smooth": 3,
            "stoch_oversold": 25,
            "stoch_overbought": 75,
            "require_stoch_cross": True,  # Require an actual K-over-D cross (not just momentum)
            # --- Confirmation ---
            "require_confirm_candle": True,
            # --- Volatility & ATR ---
            "atr_period": 14,
            "sl_atr_buffer": 0.3,
            "min_sl_atr": 0.5,
            "max_sl_atr": 0.0,            # SL ceiling in ATR multiples (0 = no cap)
            "sl_swing_source": "trend",   # "trend" (4H swings) | "structure" (1H swings)
            "atr_lookback_window": 50,
            "min_atr_ratio": 0.3,
            "max_atr_ratio": 3.5,
            # --- Targets ---
            "reward_risk_ratio": 3.0,
            # --- Anti-clustering ---
            "cooldown_candles": 2,
            "cooldown_price_atr_mult": 1.0,
            # --- Confidence ---
            "base_confidence": 0.60,
            "confluence_filters": [],
            # --- V25 overrides ---
            "v25_sl_atr_buffer": 0.3,
            "v25_reward_risk_ratio": 3.0,
            "v25_stoch_oversold": 25,
            "v25_stoch_overbought": 75,
            # --- V10 overrides ---
            "v10_sl_atr_buffer": 0.2,
            "v10_min_sl_atr": 0.4,
            "v10_reward_risk_ratio": 2.5,
            "v10_stoch_oversold": 20,
            "v10_stoch_overbought": 80,
        }

    @staticmethod
    def param_schema() -> dict:
        return {
            "fractal_n": {"type": "integer", "minimum": 1, "maximum": 5},
            "structure_lookback": {"type": "integer", "minimum": 20, "maximum": 300},
            "trend_lookback": {"type": "integer", "minimum": 10, "maximum": 200},
            "require_bos": {"type": "boolean"},
            "min_bos_count": {"type": "integer", "minimum": 1, "maximum": 5},
            "use_premium_discount": {"type": "boolean"},
            "discount_threshold": {"type": "number", "minimum": 0.3, "maximum": 0.7},
            "stoch_k_period": {"type": "integer", "minimum": 5, "maximum": 50},
            "stoch_k_smooth": {"type": "integer", "minimum": 1, "maximum": 10},
            "stoch_d_smooth": {"type": "integer", "minimum": 1, "maximum": 10},
            "stoch_oversold": {"type": "number", "minimum": 5, "maximum": 45},
            "stoch_overbought": {"type": "number", "minimum": 55, "maximum": 95},
            "require_stoch_cross": {"type": "boolean"},
            "require_confirm_candle": {"type": "boolean"},
            "atr_period": {"type": "integer", "minimum": 2, "maximum": 50},
            "sl_atr_buffer": {"type": "number", "minimum": 0.1, "maximum": 2.0},
            "min_sl_atr": {"type": "number", "minimum": 0.1, "maximum": 3.0},
            "max_sl_atr": {"type": "number", "minimum": 0.0, "maximum": 20.0},
            "sl_swing_source": {"type": "string", "enum": ["trend", "structure", "entry_15m", "entry_5m"]},
            "atr_lookback_window": {"type": "integer", "minimum": 10, "maximum": 200},
            "min_atr_ratio": {"type": "number", "minimum": 0.1, "maximum": 1.5},
            "max_atr_ratio": {"type": "number", "minimum": 1.5, "maximum": 10.0},
            "reward_risk_ratio": {"type": "number", "minimum": 1.5, "maximum": 10.0},
            "cooldown_candles": {"type": "integer", "minimum": 0, "maximum": 50},
            "cooldown_price_atr_mult": {"type": "number", "minimum": 0.3, "maximum": 5.0},
            "base_confidence": {"type": "number", "minimum": 0.3, "maximum": 0.9},
            "confluence_filters": {"type": "array", "items": {"type": "string"}},
        }

    def _resolve_params(self, config: StrategyConfig) -> dict:
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

    def _is_in_cooldown(self, entry_price: float, current_atr: float, params: dict) -> bool:
        threshold = params["cooldown_price_atr_mult"] * current_atr
        for prev_price, _ in self._recent_signals:
            if abs(entry_price - prev_price) < threshold:
                return True
        return False

    def _record_signal(self, entry_price: float, timestamp: str) -> None:
        self._recent_signals.append((entry_price, timestamp))

    def analyze(
        self,
        entry_candles: list[Candle],
        structure_candles: list[Candle],
        trend_candles: list[Candle],
        config: StrategyConfig,
        **kwargs,
    ) -> list[Signal]:
        params = self._resolve_params(config)

        fractal_n = params["fractal_n"]
        struct_lookback = params["structure_lookback"]
        require_bos = params["require_bos"]
        min_bos = params["min_bos_count"]
        use_pd = params["use_premium_discount"]
        discount_thr = params["discount_threshold"]
        k_period = params["stoch_k_period"]
        k_smooth = params["stoch_k_smooth"]
        d_smooth = params["stoch_d_smooth"]
        oversold = params["stoch_oversold"]
        overbought = params["stoch_overbought"]
        require_cross = params["require_stoch_cross"]
        require_confirm = params["require_confirm_candle"]
        atr_period = params["atr_period"]
        sl_buffer = params["sl_atr_buffer"]
        min_sl_mult = params["min_sl_atr"]
        atr_lookback = params["atr_lookback_window"]
        min_atr_r = params["min_atr_ratio"]
        max_atr_r = params["max_atr_ratio"]
        rr_ratio = params["reward_risk_ratio"]
        base_conf = params["base_confidence"]

        # ---- Guards ----
        stoch_min = k_period + k_smooth + d_smooth + 1
        if len(structure_candles) < max(stoch_min, atr_period + 1):
            return []
        if len(trend_candles) < fractal_n * 2 + 5:
            return []
        if len(entry_candles) < 2:
            return []

        # ============================================================
        # GATE 1: Market Structure with BOS (4H trend candles)
        # ============================================================
        sr = _analyze_structure(trend_candles, fractal_n, struct_lookback, require_bos)
        if sr.bias is None:
            return []
        if require_bos and sr.bos_count < min_bos:
            return []

        bias = sr.bias
        swings = sr.swings

        # SL anchor source: choose which timeframe's structural swings act
        # as the protected stop. Bias is ALWAYS from the 4H trend; this only
        # changes where the stop sits.
        #   "trend"     → 4H swings (wide, multi-day holds)
        #   "structure" → 1H swings (moderate stops, ~6h resolution)
        #   "entry_15m" → 15m swings resampled from 5m entry candles (tight)
        #   "entry_5m"  → raw 5m swings (tightest, fastest resolution)
        sl_source = params.get("sl_swing_source", "trend")
        sl_swings_pool = swings
        if sl_source == "structure":
            s_swings = _classify_swings(
                _find_fractals(structure_candles, fractal_n, struct_lookback)
            )
            if s_swings:
                sl_swings_pool = s_swings
        elif sl_source == "entry_15m":
            m15 = _resample_5m_to_15m(entry_candles)
            if len(m15) >= fractal_n * 2 + 5:
                m15_swings = _classify_swings(
                    _find_fractals(m15, fractal_n, min(len(m15), struct_lookback))
                )
                if m15_swings:
                    sl_swings_pool = m15_swings
        elif sl_source == "entry_5m":
            if len(entry_candles) >= fractal_n * 2 + 5:
                e_swings = _classify_swings(
                    _find_fractals(entry_candles, fractal_n, min(len(entry_candles), struct_lookback))
                )
                if e_swings:
                    sl_swings_pool = e_swings

        if bias == Bias.BULLISH:
            direction = SignalDirection.BUY
            swing_lows = [s for s in sl_swings_pool if not s.is_high and s.swing_type in (SwingType.HL, SwingType.LL)]
            if not swing_lows:
                # Fall back to 4H swings if the 1H pool has no aligned swing
                swing_lows = [s for s in swings if not s.is_high and s.swing_type in (SwingType.HL, SwingType.LL)]
            if not swing_lows:
                return []
            sl_swing = swing_lows[-1]
        else:
            direction = SignalDirection.SELL
            swing_highs = [s for s in sl_swings_pool if s.is_high and s.swing_type in (SwingType.LH, SwingType.HH)]
            if not swing_highs:
                swing_highs = [s for s in swings if s.is_high and s.swing_type in (SwingType.LH, SwingType.HH)]
            if not swing_highs:
                return []
            sl_swing = swing_highs[-1]

        # ============================================================
        # GATE 2: Premium / Discount filter
        # ============================================================
        entry_price = structure_candles[-1].close

        if use_pd and sr.range_low is not None and sr.range_high is not None:
            rng = sr.range_high - sr.range_low
            if rng > 0:
                pos = (entry_price - sr.range_low) / rng  # 0 = at low, 1 = at high
                if direction == SignalDirection.BUY and pos > discount_thr:
                    return []  # Not in discount — too expensive to buy
                if direction == SignalDirection.SELL and pos < (1.0 - discount_thr):
                    return []  # Not in premium — too cheap to sell

        # ============================================================
        # GATE 3: Stochastic pullback (1H structure candles)
        # ============================================================
        s_highs = [c.high for c in structure_candles]
        s_lows = [c.low for c in structure_candles]
        s_closes = [c.close for c in structure_candles]

        stoch_k, stoch_d = _compute_stochastic(s_highs, s_lows, s_closes, k_period, k_smooth, d_smooth)
        if len(stoch_k) < 2 or len(stoch_d) < 2:
            return []

        k_now, k_prev = stoch_k[-1], stoch_k[-2]
        d_now, d_prev = stoch_d[-1], stoch_d[-2]

        stoch_ok = False
        if direction == SignalDirection.BUY:
            in_zone = k_prev < oversold or k_now < oversold + 5
            if require_cross:
                trigger = k_prev <= d_prev and k_now > d_now  # fresh K-over-D cross
            else:
                trigger = k_now > d_now and k_now > k_prev
            stoch_ok = in_zone and trigger
        else:
            in_zone = k_prev > overbought or k_now > overbought - 5
            if require_cross:
                trigger = k_prev >= d_prev and k_now < d_now
            else:
                trigger = k_now < d_now and k_now < k_prev
            stoch_ok = in_zone and trigger

        if not stoch_ok:
            return []

        # ============================================================
        # GATE 4: Confirmation candle (5m/15m)
        # ============================================================
        if require_confirm:
            confirm = entry_candles[-1]
            if direction == SignalDirection.BUY and confirm.close <= confirm.open:
                return []
            if direction == SignalDirection.SELL and confirm.close >= confirm.open:
                return []

        # ============================================================
        # Volatility regime (1H ATR)
        # ============================================================
        atr_values = _compute_atr(s_highs, s_lows, s_closes, atr_period)
        if not atr_values or atr_values[-1] <= 0:
            return []
        current_atr = atr_values[-1]
        lookback_atrs = atr_values[-atr_lookback:] if len(atr_values) >= atr_lookback else atr_values
        median_atr = statistics.median(lookback_atrs)
        if median_atr <= 0:
            return []
        atr_ratio = current_atr / median_atr
        if atr_ratio < min_atr_r or atr_ratio > max_atr_r:
            return []

        # ============================================================
        # Anti-clustering
        # ============================================================
        if self._is_in_cooldown(entry_price, current_atr, params):
            return []

        # ============================================================
        # Structural SL + R:R TP
        # ============================================================
        buffer = sl_buffer * current_atr
        if direction == SignalDirection.BUY:
            stop_loss = sl_swing.price - buffer
            sl_distance = entry_price - stop_loss
        else:
            stop_loss = sl_swing.price + buffer
            sl_distance = stop_loss - entry_price

        min_sl = min_sl_mult * current_atr
        if sl_distance < min_sl:
            if direction == SignalDirection.BUY:
                stop_loss = entry_price - min_sl
            else:
                stop_loss = entry_price + min_sl
            sl_distance = min_sl

        # SL distance ceiling — skip setups whose structural stop is too far.
        # Without this, a distant 4H/1H swing produces multi-day holds with
        # week-long TP targets (the "scalper" stops being a scalper).
        # 0 disables the cap.
        max_sl_mult = params.get("max_sl_atr", 0.0)
        if max_sl_mult and max_sl_mult > 0:
            max_sl = max_sl_mult * current_atr
            if sl_distance > max_sl:
                return []

        if sl_distance <= 0:
            return []

        tp_distance = rr_ratio * sl_distance
        if direction == SignalDirection.BUY:
            take_profit = entry_price + tp_distance
        else:
            take_profit = entry_price - tp_distance

        # ============================================================
        # Confidence
        # ============================================================
        confidence = base_conf
        if sr.bos_count >= 2:
            confidence += 0.10  # Strong multi-BOS trend
        if direction == SignalDirection.BUY and k_now < 15:
            confidence += 0.10
        elif direction == SignalDirection.SELL and k_now > 85:
            confidence += 0.10
        if abs(k_now - d_now) > 8:
            confidence += 0.05
        if abs(atr_ratio - 1.0) < 0.3:
            confidence += 0.05

        filter_names = params.get("confluence_filters", [])
        if filter_names and isinstance(filter_names, list):
            from src.strategy.filters.apply import apply_confluence_filters
            confidence += apply_confluence_filters(filter_names, structure_candles, direction, params)

        confidence = max(0.0, min(1.0, confidence))
        if confidence < config.min_confidence_score:
            return []

        # Build signal
        timestamp = datetime.now(timezone.utc)
        try:
            timestamp = datetime.fromisoformat(structure_candles[-1].timestamp.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            pass

        self._record_signal(entry_price, timestamp.isoformat())

        bos_type = BOSType.BULLISH if direction == SignalDirection.BUY else BOSType.BEARISH
        signal = build_signal(
            instrument=config.instruments[0] if config.instruments else "",
            direction=direction,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            config=config,
            candles=structure_candles,
            timestamp=timestamp,
            order_block_id=str(uuid.uuid4()),
            extra_metadata={
                "bos_type": bos_type,
                "bias": bias.value,
                "bos_count": sr.bos_count,
                "invalidation": round(sr.invalidation, 2) if sr.invalidation else None,
                "stoch_k": round(k_now, 2),
                "stoch_d": round(d_now, 2),
                "sl_distance_atr": round(sl_distance / current_atr, 2),
            },
            confidence_score=confidence,
        )

        logger.info(
            "Precision scalper: %s %s @ %.2f | SL=%.2f TP=%.2f | "
            "Bias=%s BOS=%d StochK=%.0f | R:R=%.1f",
            direction.value, config.instruments[0] if config.instruments else "?",
            entry_price, stop_loss, take_profit, bias.value, sr.bos_count, k_now, rr_ratio,
        )
        return [signal]


# ---------------------------------------------------------------------------
# Market structure functions
# ---------------------------------------------------------------------------


def _analyze_structure(candles: list[Candle], n: int, lookback: int, require_bos: bool) -> StructureResult:
    """Analyze market structure using BOS (Break of Structure) detection.

    Walks candles chronologically, tracking the most recent confirmed swing
    high/low. A bullish BOS fires when a candle CLOSES above the last swing high;
    a bearish BOS when a candle closes below the last swing low. The bias is set
    by the most recent BOS. Consecutive BOS in the same direction increase
    bos_count (trend strength). A break of the protected swing = CHoCH (invalidation).
    """
    swings = _find_fractals(candles, n, lookback)
    swings = _classify_swings(swings)

    if not require_bos:
        # Fallback: simple swing-existence bias (legacy behaviour)
        return _simple_bias(candles, swings)

    if len(swings) < 2:
        return StructureResult(None, None, None, None, swings, 0)

    closes = [c.close for c in candles]

    # Index swings for chronological registration
    swing_by_idx: dict[int, list[SwingPoint]] = {}
    for s in swings:
        swing_by_idx.setdefault(s.index, []).append(s)

    bias: Bias | None = None
    protected: float | None = None        # CHoCH trigger level
    range_low: float | None = None
    range_high: float | None = None
    bos_count = 0

    last_sh: tuple[int, float] | None = None  # (index, price) of last unbroken swing high
    last_sl: tuple[int, float] | None = None

    for i in range(len(candles)):
        if i in swing_by_idx:
            for s in swing_by_idx[i]:
                if s.is_high:
                    last_sh = (s.index, s.price)
                else:
                    last_sl = (s.index, s.price)

        c = closes[i]

        # Bullish BOS: close above last swing high
        if last_sh is not None and c > last_sh[1]:
            if bias == Bias.BULLISH:
                bos_count += 1
            else:
                bias = Bias.BULLISH
                bos_count = 1
            range_high = last_sh[1]
            range_low = last_sl[1] if last_sl else range_low
            protected = last_sl[1] if last_sl else protected
            last_sh = None  # consumed

        # Bearish BOS: close below last swing low
        if last_sl is not None and c < last_sl[1]:
            if bias == Bias.BEARISH:
                bos_count += 1
            else:
                bias = Bias.BEARISH
                bos_count = 1
            range_low = last_sl[1]
            range_high = last_sh[1] if last_sh else range_high
            protected = last_sh[1] if last_sh else protected
            last_sl = None  # consumed

    if bias is None:
        return StructureResult(None, None, None, None, swings, 0)

    # CHoCH check — has the protected swing been broken by current price?
    current = closes[-1]
    if bias == Bias.BULLISH and protected is not None and current < protected:
        return StructureResult(None, None, None, None, swings, 0)
    if bias == Bias.BEARISH and protected is not None and current > protected:
        return StructureResult(None, None, None, None, swings, 0)

    # Extend dealing range to current extremes so premium/discount tracks pullbacks
    if range_low is not None and range_high is not None:
        range_low = min(range_low, current)
        range_high = max(range_high, current)

    return StructureResult(bias, protected, range_low, range_high, swings, bos_count)


def _simple_bias(candles: list[Candle], swings: list[SwingPoint]) -> StructureResult:
    """Legacy swing-existence bias (no BOS confirmation)."""
    classified = [s for s in swings if s.swing_type is not None]
    if len(classified) < 2:
        return StructureResult(None, None, None, None, swings, 0)

    highs = [s for s in classified if s.is_high]
    lows = [s for s in classified if not s.is_high]
    has_hh = any(s.swing_type == SwingType.HH for s in highs[-3:]) if highs else False
    has_hl = any(s.swing_type == SwingType.HL for s in lows[-3:]) if lows else False
    has_lh = any(s.swing_type == SwingType.LH for s in highs[-3:]) if highs else False
    has_ll = any(s.swing_type == SwingType.LL for s in lows[-3:]) if lows else False
    current = candles[-1].close

    if has_hh and has_hl:
        last_low = lows[-1] if lows else None
        if last_low and current > last_low.price:
            rl = min(s.price for s in lows[-3:])
            rh = max(s.price for s in highs[-3:]) if highs else current
            return StructureResult(Bias.BULLISH, last_low.price, rl, rh, swings, 1)
    if has_lh and has_ll:
        last_high = highs[-1] if highs else None
        if last_high and current < last_high.price:
            rh = max(s.price for s in highs[-3:])
            rl = min(s.price for s in lows[-3:]) if lows else current
            return StructureResult(Bias.BEARISH, last_high.price, rl, rh, swings, 1)
    return StructureResult(None, None, None, None, swings, 0)


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
    last_high: SwingPoint | None = None
    last_low: SwingPoint | None = None
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


# Backwards-compatible wrapper used by tests
def _establish_bias(candles, n, lookback):
    sr = _analyze_structure(candles, n, lookback, require_bos=True)
    return sr.bias, sr.invalidation, sr.swings


# ---------------------------------------------------------------------------
# Indicators
# ---------------------------------------------------------------------------


def _resample_5m_to_15m(candles: list[Candle]) -> list[Candle]:
    """Aggregate every 3 consecutive 5m candles into one 15m candle.

    Used by the SL anchor option `entry_15m`. Drops the leading remainder
    so each output bar represents a full 15-minute window.
    """
    if len(candles) < 3:
        return []
    out: list[Candle] = []
    inst = getattr(candles[0], "instrument", "")
    remainder = len(candles) % 3
    start = remainder if remainder > 0 else 0
    for i in range(start, len(candles), 3):
        bucket = candles[i: i + 3]
        if len(bucket) < 3:
            break
        out.append(Candle(
            instrument=inst,
            timeframe="15m",
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
        tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
        tr_list.append(tr)
    if len(tr_list) < period:
        return []
    atr = sum(tr_list[:period]) / period
    atr_values = [atr]
    for i in range(period, len(tr_list)):
        atr = (atr * (period - 1) + tr_list[i]) / period
        atr_values.append(atr)
    return atr_values


def _compute_stochastic(highs, lows, closes, k_period=14, k_smooth=3, d_smooth=3):
    n = len(closes)
    if n < k_period:
        return [], []
    raw_k = []
    for i in range(k_period - 1, n):
        wh = highs[i - k_period + 1: i + 1]
        wl = lows[i - k_period + 1: i + 1]
        hi, lo = max(wh), min(wl)
        raw_k.append(50.0 if hi == lo else (closes[i] - lo) / (hi - lo) * 100.0)
    if len(raw_k) < k_smooth:
        return [], []
    slow_k = [sum(raw_k[i - k_smooth + 1: i + 1]) / k_smooth for i in range(k_smooth - 1, len(raw_k))]
    if len(slow_k) < d_smooth:
        return slow_k, []
    d_line = [sum(slow_k[i - d_smooth + 1: i + 1]) / d_smooth for i in range(d_smooth - 1, len(slow_k))]
    aligned_k = slow_k[-(len(d_line)):] if len(d_line) < len(slow_k) else slow_k
    return aligned_k, d_line
