"""V10 Range Sniper — optimized for Deriv Volatility 10 Index.

Mean-reversion strategy designed for V10's extremely low volatility and
tight ranging behavior. V10 has ~10% annualized volatility — it barely
moves, ranges most of the time, and mean-reverts very reliably.

Core strengths for V10:
- V10 is the MOST mean-reverting synthetic — oversold/overbought snaps back fast
- Very tight ATR means tight SL = tiny risk per trade even at high % risk
- Ranges create clean support/resistance for structural SL
- High win rate (70%+) expected because mean reversion is so reliable
- Best instrument for small accounts ($10-$25) due to tiny absolute risk

Timeframes: 1H (swing structure) → 15m (Stochastic entry) → 5m (confirmation)

Note: lower timeframes than V25/V75 because V10's moves are so small
that 4H structure barely produces any swings.

Technique:
- 1H Williams fractals → swing structure for trend bias
- 15m Stochastic(10,3,3) for oversold/overbought pullback timing
- 5m confirmation candle
- Very tight structural SL + 1.5R target (smaller but more consistent)
"""

import logging
import statistics
import uuid
from collections import deque
from datetime import datetime, timezone

from src.models import BOSType, Candle, Signal, SignalDirection, StrategyConfig
from src.strategy.base import StrategyAlgorithm
from src.strategy.signal_helpers import build_signal

from src.strategy.algorithms.v25_structure_scalper import (
    Bias, SwingType, SwingPoint,
    _analyze_structure, _find_fractals, _classify_swings,
    _compute_atr, _compute_stochastic,
)

logger = logging.getLogger(__name__)


class V10RangeSniperAlgorithm(StrategyAlgorithm):
    """Mean-reversion sniper for V10 using structure + Stochastic.

    Entry gates:
    1. 1H market structure: HH+HL (bullish) or LH+LL (bearish) — lighter structure
    2. 15m Stochastic(10,3,3) in oversold/overbought + momentum shift
    3. 5m confirmation candle closes in trend direction
    + volatility regime + anti-clustering

    Lower R:R (1.5) but very high win rate expected.
    """

    def __init__(self) -> None:
        self._recent_signals: deque[tuple[float, str]] = deque(maxlen=20)

    @staticmethod
    def name() -> str:
        return "v10_range_sniper"

    @staticmethod
    def description() -> str:
        return (
            "V10 Range Sniper — mean-reversion strategy for Volatility 10 Index. "
            "Uses 1H swing structure for trend, 15m Stochastic for pullback timing, "
            "5m confirmation. Very tight SL, 1.5R target, high win rate. "
            "Best for tiny accounts ($10-$25)."
        )

    @staticmethod
    def default_params() -> dict:
        return {
            # --- Market structure ---
            "fractal_n": 2,
            "structure_lookback": 80,       # 1H lookback (shorter for V10)
            "trend_lookback": 30,
            "require_bos": False,
            # --- Stochastic (faster for V10's quick mean reversion) ---
            "stoch_k_period": 10,           # Faster than V25's 14
            "stoch_k_smooth": 3,
            "stoch_d_smooth": 3,
            "stoch_oversold": 20,           # Tight zones for reliable entries
            "stoch_overbought": 80,
            "require_stoch_cross": False,
            # --- Confirmation ---
            "require_confirm_candle": True,
            # --- Volatility & ATR ---
            "atr_period": 14,
            "sl_atr_buffer": 0.2,           # Very tight buffer (V10 has low noise)
            "min_sl_atr": 0.3,              # Very tight minimum SL
            "atr_lookback_window": 50,
            "min_atr_ratio": 0.2,           # V10 can get very quiet
            "max_atr_ratio": 4.0,           # Spikes are proportionally large
            # --- Targets ---
            "reward_risk_ratio": 1.5,       # Smaller but high-probability targets
            # --- Anti-clustering ---
            "cooldown_candles": 1,
            "cooldown_price_atr_mult": 0.5, # Tight — V10 doesn't move far
            # --- Confidence ---
            "base_confidence": 0.60,
            "confluence_filters": [],
        }

    @staticmethod
    def param_schema() -> dict:
        return {
            "fractal_n": {"type": "integer", "minimum": 1, "maximum": 5},
            "structure_lookback": {"type": "integer", "minimum": 20, "maximum": 200},
            "trend_lookback": {"type": "integer", "minimum": 10, "maximum": 200},
            "require_bos": {"type": "boolean"},
            "stoch_k_period": {"type": "integer", "minimum": 5, "maximum": 30},
            "stoch_k_smooth": {"type": "integer", "minimum": 1, "maximum": 10},
            "stoch_d_smooth": {"type": "integer", "minimum": 1, "maximum": 10},
            "stoch_oversold": {"type": "number", "minimum": 5, "maximum": 40},
            "stoch_overbought": {"type": "number", "minimum": 60, "maximum": 95},
            "require_stoch_cross": {"type": "boolean"},
            "require_confirm_candle": {"type": "boolean"},
            "atr_period": {"type": "integer", "minimum": 2, "maximum": 50},
            "sl_atr_buffer": {"type": "number", "minimum": 0.05, "maximum": 1.0},
            "min_sl_atr": {"type": "number", "minimum": 0.1, "maximum": 2.0},
            "atr_lookback_window": {"type": "integer", "minimum": 10, "maximum": 200},
            "min_atr_ratio": {"type": "number", "minimum": 0.1, "maximum": 1.0},
            "max_atr_ratio": {"type": "number", "minimum": 1.5, "maximum": 10.0},
            "reward_risk_ratio": {"type": "number", "minimum": 1.0, "maximum": 5.0},
            "cooldown_candles": {"type": "integer", "minimum": 0, "maximum": 20},
            "cooldown_price_atr_mult": {"type": "number", "minimum": 0.2, "maximum": 3.0},
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

        # Guards
        stoch_min = params["stoch_k_period"] + params["stoch_k_smooth"] + params["stoch_d_smooth"] + 1
        if len(structure_candles) < max(stoch_min, params["atr_period"] + 1):
            return []
        if len(trend_candles) < params["fractal_n"] * 2 + 5:
            return []
        if len(entry_candles) < 2:
            return []

        # GATE 1: Market structure from trend candles (1H for V10)
        sr = _analyze_structure(trend_candles, params["fractal_n"], params["structure_lookback"], params["require_bos"])
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

        # GATE 2: Stochastic on structure candles (15m)
        s_highs = [c.high for c in structure_candles]
        s_lows = [c.low for c in structure_candles]
        s_closes = [c.close for c in structure_candles]

        stoch_k, stoch_d = _compute_stochastic(
            s_highs, s_lows, s_closes,
            params["stoch_k_period"], params["stoch_k_smooth"], params["stoch_d_smooth"],
        )
        if len(stoch_k) < 2 or len(stoch_d) < 2:
            return []

        k_now, k_prev = stoch_k[-1], stoch_k[-2]
        d_now = stoch_d[-1]

        stoch_ok = False
        oversold = params["stoch_oversold"]
        overbought = params["stoch_overbought"]
        if direction == SignalDirection.BUY:
            in_zone = k_prev < oversold or k_now < oversold + 5
            trigger = k_now > d_now and k_now > k_prev
            stoch_ok = in_zone and trigger
        else:
            in_zone = k_prev > overbought or k_now > overbought - 5
            trigger = k_now < d_now and k_now < k_prev
            stoch_ok = in_zone and trigger

        if not stoch_ok:
            return []

        # GATE 3: Confirmation candle (5m)
        if params["require_confirm_candle"]:
            c = entry_candles[-1]
            if direction == SignalDirection.BUY and c.close <= c.open:
                return []
            if direction == SignalDirection.SELL and c.close >= c.open:
                return []

        # ATR
        atr_values = _compute_atr(s_highs, s_lows, s_closes, params["atr_period"])
        if not atr_values or atr_values[-1] <= 0:
            return []
        current_atr = atr_values[-1]
        lookback_atrs = atr_values[-params["atr_lookback_window"]:] if len(atr_values) >= params["atr_lookback_window"] else atr_values
        median_atr = statistics.median(lookback_atrs)
        if median_atr <= 0:
            return []
        atr_ratio = current_atr / median_atr
        if atr_ratio < params["min_atr_ratio"] or atr_ratio > params["max_atr_ratio"]:
            return []

        entry_price = s_closes[-1]
        if self._is_in_cooldown(entry_price, current_atr, params):
            return []

        # Structural SL + TP
        buffer = params["sl_atr_buffer"] * current_atr
        if direction == SignalDirection.BUY:
            stop_loss = sl_swing.price - buffer
            sl_distance = entry_price - stop_loss
        else:
            stop_loss = sl_swing.price + buffer
            sl_distance = stop_loss - entry_price

        min_sl = params["min_sl_atr"] * current_atr
        if sl_distance < min_sl:
            stop_loss = entry_price - min_sl if direction == SignalDirection.BUY else entry_price + min_sl
            sl_distance = min_sl
        if sl_distance <= 0:
            return []

        tp_distance = params["reward_risk_ratio"] * sl_distance
        take_profit = entry_price + tp_distance if direction == SignalDirection.BUY else entry_price - tp_distance

        # Confidence
        confidence = params["base_confidence"]
        if direction == SignalDirection.BUY and k_now < 15:
            confidence += 0.10
        elif direction == SignalDirection.SELL and k_now > 85:
            confidence += 0.10
        if abs(k_now - d_now) > 8:
            confidence += 0.05

        confidence = max(0.0, min(1.0, confidence))
        if confidence < config.min_confidence_score:
            return []

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
                "bias": sr.bias.value, "stoch_k": round(k_now, 2),
                "sl_distance_atr": round(sl_distance / current_atr, 2),
            },
            confidence_score=confidence,
        )
        logger.info("V10 Sniper: %s @ %.4f | SL=%.4f TP=%.4f | StochK=%.0f", direction.value, entry_price, stop_loss, take_profit, k_now)
        return [signal]
