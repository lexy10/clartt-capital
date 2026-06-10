"""Mean-reversion strategy for Deriv synthetic indices (V75/V25).

Adapted from Connors RSI mean-reversion methodology:
- Uses short-period RSI (4-bar) for oversold/overbought detection
- Requires price above/below EMA on higher timeframe for trend alignment
- Only mean-reverts in the direction of the higher-TF trend
- Exits when short RSI normalizes (crosses 55 for longs, 45 for shorts)
- ATR-based stop loss for risk management

Key insight: mean reversion works best when you fade short-term extremes
in the direction of the prevailing trend, not against it.
"""

import logging
import statistics
import uuid
from datetime import datetime, timezone

from src.models import BOSType, Candle, Signal, SignalDirection, StrategyConfig
from src.strategy.base import StrategyAlgorithm
from src.strategy.signal_helpers import build_signal

logger = logging.getLogger(__name__)


class SyntheticMeanReversionAlgorithm(StrategyAlgorithm):
    """Connors-style mean reversion with trend alignment for Deriv synthetics."""

    @staticmethod
    def name() -> str:
        return "synthetic_mean_reversion"

    @staticmethod
    def description() -> str:
        return (
            "Mean-reversion strategy using short-period RSI with EMA trend "
            "alignment for Deriv V75/V25 synthetics. Fades short-term extremes "
            "in the direction of the higher-timeframe trend."
        )

    @staticmethod
    def default_params() -> dict:
        return {
            # Short RSI for entry timing
            "rsi_period": 4,
            "rsi_entry_threshold": 25,
            "rsi_exit_threshold": 55,
            # Trend alignment on structure candles
            "trend_ema_period": 50,
            # Bollinger Band confirmation (optional extra filter)
            "bb_period": 20,
            "bb_std_dev": 2.0,
            "require_bb_touch": True,
            # Risk management
            "atr_period": 14,
            "atr_sl_multiplier": 2.0,
            "min_rr_ratio": 2.0,
            "cooldown_candles": 5,
            "confluence_filters": [],
            # V75 overrides
            "v75_atr_sl_multiplier": 2.5,
            "v75_rsi_entry_threshold": 20,
            # V25 overrides
            "v25_atr_sl_multiplier": 1.8,
            "v25_rsi_entry_threshold": 28,
        }

    @staticmethod
    def param_schema() -> dict:
        return {
            "rsi_period": {"type": "integer", "minimum": 2, "maximum": 20},
            "rsi_entry_threshold": {"type": "number", "minimum": 10, "maximum": 40},
            "rsi_exit_threshold": {"type": "number", "minimum": 40, "maximum": 80},
            "trend_ema_period": {"type": "integer", "minimum": 10, "maximum": 200},
            "bb_period": {"type": "integer", "minimum": 5, "maximum": 100},
            "bb_std_dev": {"type": "number", "minimum": 0.5, "maximum": 5.0},
            "require_bb_touch": {"type": "boolean"},
            "atr_period": {"type": "integer", "minimum": 2, "maximum": 50},
            "atr_sl_multiplier": {"type": "number", "minimum": 0.5, "maximum": 5.0},
            "min_rr_ratio": {"type": "number", "minimum": 1.0, "maximum": 10.0},
            "cooldown_candles": {"type": "integer", "minimum": 0, "maximum": 50},
            "confluence_filters": {"type": "array", "items": {"type": "string"}},
        }

    def _resolve_params(self, config: StrategyConfig) -> dict:
        """Resolve instrument-specific parameter defaults."""
        params = {**self.default_params(), **config.algorithm_params}
        instrument = config.instruments[0] if config.instruments else ""
        if instrument == "R_75":
            prefix = "v75_"
        elif instrument == "R_25":
            prefix = "v25_"
        else:
            return params
        for key, value in list(params.items()):
            if key.startswith(prefix):
                base_key = key[len(prefix):]
                if base_key not in config.algorithm_params:
                    params[base_key] = value
        return params

    def analyze(
        self,
        entry_candles: list[Candle],
        structure_candles: list[Candle],
        trend_candles: list[Candle],
        config: StrategyConfig,
        **kwargs,
    ) -> list[Signal]:
        """Connors-style mean reversion with trend alignment.

        Logic:
        1. Determine trend direction from structure candles EMA
        2. Compute short-period RSI on structure candles
        3. BUY when: price > EMA (uptrend) AND RSI < entry_threshold (oversold dip)
        4. SELL when: price < EMA (downtrend) AND RSI > (100 - entry_threshold) (overbought rally)
        5. Optional: require price touching Bollinger Band for extra confirmation
        6. SL via ATR, TP targets EMA (mean) with min R:R ratio
        """
        params = self._resolve_params(config)

        rsi_period: int = params["rsi_period"]
        rsi_entry: float = params["rsi_entry_threshold"]
        trend_ema_period: int = params["trend_ema_period"]
        bb_period: int = params["bb_period"]
        bb_std_dev: float = params["bb_std_dev"]
        require_bb: bool = params["require_bb_touch"]
        atr_period: int = params["atr_period"]
        atr_sl_mult: float = params["atr_sl_multiplier"]
        min_rr: float = params["min_rr_ratio"]

        min_candles = max(trend_ema_period, bb_period, atr_period + 1, rsi_period + 1)
        if len(structure_candles) < min_candles:
            return []

        closes = [c.close for c in structure_candles]
        highs = [c.high for c in structure_candles]
        lows = [c.low for c in structure_candles]
        current_close = closes[-1]

        # 1. Trend direction from EMA
        ema = _compute_ema(closes, trend_ema_period)
        if not ema:
            return []
        current_ema = ema[-1]
        is_uptrend = current_close > current_ema
        is_downtrend = current_close < current_ema

        # 2. Short-period RSI
        rsi = _compute_rsi(closes, rsi_period)
        if rsi is None:
            return []

        # 3. ATR for risk management
        atr_values = _compute_atr(highs, lows, closes, atr_period)
        if not atr_values or atr_values[-1] == 0:
            return []
        current_atr = atr_values[-1]

        # 4. Optional Bollinger Band confirmation
        if require_bb:
            sma = _compute_sma(closes, bb_period)
            std = _compute_std_dev(closes, bb_period)
            if sma is None or std is None or std == 0:
                return []
            upper_bb = sma + bb_std_dev * std
            lower_bb = sma - bb_std_dev * std
        else:
            upper_bb = float('inf')
            lower_bb = float('-inf')
            sma = current_ema  # Use EMA as mean target

        # 5. Signal logic
        direction: SignalDirection | None = None
        rsi_overbought = 100 - rsi_entry  # Mirror threshold (e.g., 25 -> 75)

        if is_uptrend and rsi < rsi_entry:
            # Uptrend + oversold dip = BUY
            if not require_bb or current_close <= lower_bb:
                direction = SignalDirection.BUY
        elif is_downtrend and rsi > rsi_overbought:
            # Downtrend + overbought rally = SELL
            if not require_bb or current_close >= upper_bb:
                direction = SignalDirection.SELL

        if direction is None:
            return []

        entry_price = current_close

        # 6. SL computation
        sl_distance = atr_sl_mult * current_atr
        if direction == SignalDirection.BUY:
            stop_loss = entry_price - sl_distance
        else:
            stop_loss = entry_price + sl_distance

        # 7. TP: target EMA (the mean), clamped to min R:R
        tp_natural = current_ema
        tp_distance = abs(tp_natural - entry_price)
        min_tp_distance = min_rr * sl_distance

        if tp_distance < min_tp_distance:
            if direction == SignalDirection.BUY:
                take_profit = entry_price + min_tp_distance
            else:
                take_profit = entry_price - min_tp_distance
        else:
            take_profit = tp_natural

        # 8. Confidence scoring
        confidence = 0.6
        # Deeper RSI extreme = higher confidence
        if direction == SignalDirection.BUY and rsi < 15:
            confidence += 0.15
        elif direction == SignalDirection.SELL and rsi > 85:
            confidence += 0.15
        # Strong trend alignment bonus
        trend_strength = abs(current_close - current_ema) / current_atr
        if trend_strength > 1.5:
            confidence += 0.1
        # Low volatility bonus
        if len(atr_values) > 1:
            median_atr = statistics.median(atr_values)
            if current_atr < median_atr:
                confidence += 0.05

        # Confluence filters
        filter_names = params.get("confluence_filters", [])
        if filter_names:
            from src.strategy.filters.apply import apply_confluence_filters
            adjustment = apply_confluence_filters(
                filter_names, structure_candles, direction, params,
            )
            confidence += adjustment

        confidence = max(0.0, min(1.0, confidence))
        if confidence < config.min_confidence_score:
            return []

        timestamp = datetime.now(timezone.utc)
        if structure_candles:
            try:
                timestamp = datetime.fromisoformat(
                    structure_candles[-1].timestamp.replace("Z", "+00:00")
                )
            except (ValueError, AttributeError):
                pass

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
            extra_metadata={"bos_type": bos_type, "rsi": round(rsi, 2)},
            confidence_score=confidence,
        )
        return [signal]


# ---------------------------------------------------------------------------
# Indicator functions
# ---------------------------------------------------------------------------

def _compute_ema(closes: list[float], period: int) -> list[float]:
    """Compute EMA series."""
    if not closes or len(closes) < period:
        return []
    mult = 2.0 / (period + 1)
    ema = [sum(closes[:period]) / period]  # SMA seed
    for i in range(period, len(closes)):
        ema.append(closes[i] * mult + ema[-1] * (1 - mult))
    return ema


def _compute_sma(closes: list[float], period: int) -> float | None:
    if len(closes) < period:
        return None
    return sum(closes[-period:]) / period


def _compute_std_dev(closes: list[float], period: int) -> float | None:
    if len(closes) < period:
        return None
    window = closes[-period:]
    mean = sum(window) / period
    variance = sum((x - mean) ** 2 for x in window) / period
    return variance ** 0.5


def _compute_atr(
    highs: list[float], lows: list[float], closes: list[float], period: int
) -> list[float]:
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


def _compute_rsi(closes: list[float], period: int) -> float | None:
    """Compute RSI using Wilder's smoothing."""
    if len(closes) < period + 1:
        return None
    gains: list[float] = []
    losses: list[float] = []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gains.append(max(diff, 0.0))
        losses.append(max(-diff, 0.0))
    if len(gains) < period:
        return None
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))
