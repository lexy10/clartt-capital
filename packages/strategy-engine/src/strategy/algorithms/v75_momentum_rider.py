"""V75 Momentum Rider — optimized for Deriv Volatility 75 Index.

Trend-momentum strategy designed for V75's high-volatility, strongly trending
characteristics. V75 has ~75% annualized volatility (3x V25) — it trends HARD
and pullbacks are deep but fast.

Core strengths for V75:
- V75 produces massive directional moves — perfect for 5R+ targets
- Strong trends persist for days/weeks — momentum continuation works
- Deep pullbacks to structure create high-R:R entries
- Wide ATR means structural SL placement gives room without noise stops

Timeframes: 4H (swing structure) → 1H (EMA pullback + RSI momentum) → 15m (confirmation)

Technique:
- 4H Williams fractals → HH/HL/LH/LL swing structure for trend bias
- 1H EMA(21) pullback: price must touch or cross the EMA during a pullback
- 1H RSI(14) confirms momentum is still intact (>45 for buy, <55 for sell)
- 15m confirmation candle closes in trend direction
- Structural SL (beyond last swing) with wide ATR buffer
- 5.0R target — V75 actually reaches these because it trends so hard

REQUIRES SERIOUS CAPITAL — V75's wide SL means each trade risks more in
absolute terms. Not recommended for accounts under $100.
"""

import logging
import statistics
import uuid
from collections import deque
from datetime import datetime, timezone

from src.models import BOSType, Candle, Signal, SignalDirection, StrategyConfig
from src.strategy.base import StrategyAlgorithm
from src.strategy.signal_helpers import build_signal

logger = logging.getLogger(__name__)


# Reuse structure types from v25
from src.strategy.algorithms.v25_structure_scalper import (
    Bias,
    SwingType,
    SwingPoint,
    _analyze_structure,
    _find_fractals,
    _classify_swings,
    _compute_atr,
    _compute_stochastic,
)


class V75MomentumRiderAlgorithm(StrategyAlgorithm):
    """Trend-momentum rider for V75 using structure + EMA pullback + RSI.

    Entry gates:
    1. 4H market structure: HH+HL (bullish) or LH+LL (bearish)
    2. 1H EMA pullback: price pulls back to or below EMA(21) in uptrend (above in downtrend)
    3. 1H RSI momentum: RSI(14) still above 45 for buy / below 55 for sell (momentum intact)
    4. 15m confirmation candle closes in trend direction
    + volatility regime + anti-clustering
    """

    def __init__(self) -> None:
        self._recent_signals: deque[tuple[float, str]] = deque(maxlen=20)

    @staticmethod
    def name() -> str:
        return "v75_momentum_rider"

    @staticmethod
    def description() -> str:
        return (
            "V75 Momentum Rider — trend-momentum strategy for Volatility 75 Index. "
            "Uses 4H swing structure for trend, 1H EMA pullback + RSI momentum "
            "confirmation for entry, 15m confirmation candle. Wide structural SL, "
            "5R target. Designed for V75's strong trending behavior."
        )

    @staticmethod
    def default_params() -> dict:
        return {
            # --- Market structure ---
            "fractal_n": 2,
            "structure_lookback": 100,
            "trend_lookback": 30,
            "require_bos": False,
            # --- EMA pullback (legacy, not used in stochastic mode) ---
            "ema_period": 21,
            "pullback_atr_tolerance": 0.5,
            # --- Stochastic (primary entry signal) ---
            "stoch_k_period": 14,
            "stoch_k_smooth": 3,
            "stoch_d_smooth": 3,
            "stoch_oversold": 25,
            "stoch_overbought": 75,
            # --- RSI momentum filter (optional) ---
            "rsi_period": 14,
            "rsi_bull_floor": 45,
            "rsi_bear_ceil": 55,
            "require_rsi_filter": False,    # Optional momentum filter on top of stoch
            # --- Volatility & ATR ---
            "atr_period": 14,
            "sl_atr_buffer": 0.5,           # Wider buffer for V75 noise
            "min_sl_atr": 0.8,              # V75 needs wider minimum SL
            "max_sl_atr": 1.5,              # CAP structural SL to this ATR (V75 swings are huge)
            "atr_lookback_window": 50,
            "min_atr_ratio": 0.3,
            "max_atr_ratio": 3.5,
            # --- Targets ---
            "reward_risk_ratio": 5.0,       # V75 can hit 5R
            # --- Anti-clustering ---
            "cooldown_candles": 2,
            "cooldown_price_atr_mult": 1.5, # Wider for V75's big moves
            # --- Confirmation ---
            "require_confirm_candle": True,
            # --- Confidence ---
            "base_confidence": 0.60,
            "confluence_filters": [],
        }

    @staticmethod
    def param_schema() -> dict:
        return {
            "fractal_n": {"type": "integer", "minimum": 1, "maximum": 5},
            "structure_lookback": {"type": "integer", "minimum": 20, "maximum": 300},
            "trend_lookback": {"type": "integer", "minimum": 10, "maximum": 200},
            "require_bos": {"type": "boolean"},
            "ema_period": {"type": "integer", "minimum": 5, "maximum": 100},
            "pullback_atr_tolerance": {"type": "number", "minimum": 0.1, "maximum": 3.0},
            "rsi_period": {"type": "integer", "minimum": 5, "maximum": 30},
            "rsi_bull_floor": {"type": "number", "minimum": 30, "maximum": 60},
            "rsi_bear_ceil": {"type": "number", "minimum": 40, "maximum": 70},
            "atr_period": {"type": "integer", "minimum": 2, "maximum": 50},
            "sl_atr_buffer": {"type": "number", "minimum": 0.1, "maximum": 3.0},
            "min_sl_atr": {"type": "number", "minimum": 0.3, "maximum": 5.0},
            "max_sl_atr": {"type": "number", "minimum": 0.5, "maximum": 5.0},
            "atr_lookback_window": {"type": "integer", "minimum": 10, "maximum": 200},
            "min_atr_ratio": {"type": "number", "minimum": 0.1, "maximum": 1.5},
            "max_atr_ratio": {"type": "number", "minimum": 1.5, "maximum": 10.0},
            "reward_risk_ratio": {"type": "number", "minimum": 2.0, "maximum": 15.0},
            "cooldown_candles": {"type": "integer", "minimum": 0, "maximum": 50},
            "cooldown_price_atr_mult": {"type": "number", "minimum": 0.3, "maximum": 5.0},
            "require_confirm_candle": {"type": "boolean"},
            "base_confidence": {"type": "number", "minimum": 0.3, "maximum": 0.9},
            "confluence_filters": {"type": "array", "items": {"type": "string"}},
        }

    def _resolve_params(self, config: StrategyConfig) -> dict:
        return {**self.default_params(), **config.algorithm_params}

    def _is_in_cooldown(self, price: float, atr: float, params: dict) -> bool:
        threshold = params["cooldown_price_atr_mult"] * atr
        return any(abs(price - p) < threshold for p, _ in self._recent_signals)

    def _record_signal(self, price: float, ts: str) -> None:
        self._recent_signals.append((price, ts))

    def analyze(
        self, entry_candles: list[Candle], structure_candles: list[Candle],
        trend_candles: list[Candle], config: StrategyConfig, **kwargs,
    ) -> list[Signal]:
        params = self._resolve_params(config)
        fractal_n = params["fractal_n"]
        ema_period = params["ema_period"]
        pullback_tol = params["pullback_atr_tolerance"]
        rsi_period = params["rsi_period"]
        rsi_bull = params["rsi_bull_floor"]
        rsi_bear = params["rsi_bear_ceil"]
        atr_period = params["atr_period"]
        sl_buffer = params["sl_atr_buffer"]
        min_sl_mult = params["min_sl_atr"]
        rr_ratio = params["reward_risk_ratio"]
        base_conf = params["base_confidence"]

        # Guards
        if len(structure_candles) < max(ema_period + 1, rsi_period + 1, atr_period + 1):
            return []
        if len(trend_candles) < fractal_n * 2 + 5:
            return []
        if len(entry_candles) < 2:
            return []

        # GATE 1: 4H market structure
        sr = _analyze_structure(trend_candles, fractal_n, params["structure_lookback"], params["require_bos"])
        if sr.bias is None:
            return []

        direction = SignalDirection.BUY if sr.bias == Bias.BULLISH else SignalDirection.SELL
        swings = sr.swings

        if direction == SignalDirection.BUY:
            sl_swings = [s for s in swings if not s.is_high and s.swing_type in (SwingType.HL, SwingType.LL)]
        else:
            sl_swings = [s for s in swings if s.is_high and s.swing_type in (SwingType.LH, SwingType.HH)]
        if not sl_swings:
            return []
        sl_swing = sl_swings[-1]

        # GATE 2: 1H Stochastic pullback (V75 needs mean-reversion timing, not just EMA)
        s_closes = [c.close for c in structure_candles]
        s_highs = [c.high for c in structure_candles]
        s_lows = [c.low for c in structure_candles]

        stoch_k_period = params.get("stoch_k_period", 14)
        stoch_k_smooth = params.get("stoch_k_smooth", 3)
        stoch_d_smooth = params.get("stoch_d_smooth", 3)
        stoch_oversold = params.get("stoch_oversold", 25)
        stoch_overbought = params.get("stoch_overbought", 75)

        stoch_k, stoch_d = _compute_stochastic(
            s_highs, s_lows, s_closes,
            stoch_k_period, stoch_k_smooth, stoch_d_smooth,
        )
        if len(stoch_k) < 2 or len(stoch_d) < 2:
            return []

        k_now, k_prev = stoch_k[-1], stoch_k[-2]
        d_now = stoch_d[-1]
        current_close = s_closes[-1]

        stoch_ok = False
        if direction == SignalDirection.BUY:
            in_zone = k_prev < stoch_oversold or k_now < stoch_oversold + 5
            trigger = k_now > d_now and k_now > k_prev
            stoch_ok = in_zone and trigger
        else:
            in_zone = k_prev > stoch_overbought or k_now > stoch_overbought - 5
            trigger = k_now < d_now and k_now < k_prev
            stoch_ok = in_zone and trigger

        if not stoch_ok:
            return []

        # ATR for SL/TP
        atr_values = _compute_atr(s_highs, s_lows, s_closes, atr_period)
        if not atr_values or atr_values[-1] <= 0:
            return []
        current_atr = atr_values[-1]

        # Optional RSI momentum filter
        rsi = _compute_rsi(s_closes, rsi_period)
        if rsi is None:
            return []
        if params.get("require_rsi_filter", False):
            if direction == SignalDirection.BUY and rsi < rsi_bull:
                return []
            if direction == SignalDirection.SELL and rsi > rsi_bear:
                return []

        # GATE 4: 15m confirmation candle
        if params["require_confirm_candle"]:
            c = entry_candles[-1]
            if direction == SignalDirection.BUY and c.close <= c.open:
                return []
            if direction == SignalDirection.SELL and c.close >= c.open:
                return []

        # Volatility regime
        lookback_atrs = atr_values[-params["atr_lookback_window"]:] if len(atr_values) >= params["atr_lookback_window"] else atr_values
        median_atr = statistics.median(lookback_atrs)
        if median_atr <= 0:
            return []
        atr_ratio = current_atr / median_atr
        if atr_ratio < params["min_atr_ratio"] or atr_ratio > params["max_atr_ratio"]:
            return []

        # Anti-clustering
        entry_price = current_close
        if self._is_in_cooldown(entry_price, current_atr, params):
            return []

        # ATR-based SL (NOT structural — V75 swings are too wide)
        # Use swing as reference but CAP the SL to max ATR multiple
        max_sl_atr = params.get("max_sl_atr", 1.5)
        buffer = sl_buffer * current_atr

        if direction == SignalDirection.BUY:
            structural_sl = sl_swing.price - buffer
            atr_sl = entry_price - max_sl_atr * current_atr
            stop_loss = max(structural_sl, atr_sl)  # Use tighter of the two
            sl_distance = entry_price - stop_loss
        else:
            structural_sl = sl_swing.price + buffer
            atr_sl = entry_price + max_sl_atr * current_atr
            stop_loss = min(structural_sl, atr_sl)  # Use tighter of the two
            sl_distance = stop_loss - entry_price

        min_sl = min_sl_mult * current_atr
        if sl_distance < min_sl:
            stop_loss = entry_price - min_sl if direction == SignalDirection.BUY else entry_price + min_sl
            sl_distance = min_sl
        if sl_distance <= 0:
            return []

        tp_distance = rr_ratio * sl_distance
        take_profit = entry_price + tp_distance if direction == SignalDirection.BUY else entry_price - tp_distance

        # Confidence
        confidence = base_conf
        if direction == SignalDirection.BUY and rsi > 55:
            confidence += 0.10
        elif direction == SignalDirection.SELL and rsi < 45:
            confidence += 0.10
        # Deep stochastic extreme bonus
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

        signal = build_signal(
            instrument=config.instruments[0] if config.instruments else "",
            direction=direction, entry_price=entry_price,
            stop_loss=stop_loss, take_profit=take_profit,
            config=config, candles=structure_candles, timestamp=timestamp,
            order_block_id=str(uuid.uuid4()),
            extra_metadata={
                "bos_type": BOSType.BULLISH if direction == SignalDirection.BUY else BOSType.BEARISH,
                "bias": sr.bias.value, "rsi": round(rsi, 1),
                "stoch_k": round(k_now, 2),
                "stoch_d": round(d_now, 2),
                "sl_distance_atr": round(sl_distance / current_atr, 2),
            },
            confidence_score=confidence,
        )
        logger.info("V75 Momentum: %s @ %.2f | SL=%.2f TP=%.2f | RSI=%.0f", direction.value, entry_price, stop_loss, take_profit, rsi)
        return [signal]


# ---------------------------------------------------------------------------
# Indicators (EMA and RSI — not in v25 module)
# ---------------------------------------------------------------------------

def _compute_ema(closes: list[float], period: int) -> list[float]:
    if not closes or len(closes) < period:
        return []
    mult = 2.0 / (period + 1)
    ema = [sum(closes[:period]) / period]
    for i in range(period, len(closes)):
        ema.append(closes[i] * mult + ema[-1] * (1 - mult))
    return ema


def _compute_rsi(closes: list[float], period: int) -> float | None:
    if len(closes) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        gains.append(max(d, 0.0))
        losses.append(max(-d, 0.0))
    if len(gains) < period:
        return None
    avg_g = sum(gains[:period]) / period
    avg_l = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_g = (avg_g * (period - 1) + gains[i]) / period
        avg_l = (avg_l * (period - 1) + losses[i]) / period
    if avg_l == 0:
        return 100.0
    return 100.0 - 100.0 / (1 + avg_g / avg_l)
