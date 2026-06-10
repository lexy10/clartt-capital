"""Moving Average Pullback strategy for Deriv synthetic indices (V75/V25).

Based on proven backtest results from real trading:
- 56% win rate, 1.6:1 reward-risk ratio over 1000 trades
- Uses dual EMA (50/200) for trend detection on higher timeframe
- Waits for price to pull back to the fast EMA before entry
- Confirms with RSI momentum, ADX trend strength, and momentum candle
- Structural SL placement using recent swing highs/lows with ATR buffer

Entry logic:
1. EMA(50) > EMA(200) = BULLISH trend; EMA(50) < EMA(200) = BEARISH
2. Pullback: price touches or crosses EMA(50)
3. RSI(14) confirms momentum direction (>50 BUY, <50 SELL)
4. Momentum candle: close > prev close (BUY), close < prev close (SELL)
5. ADX(14) > threshold to confirm trending market
"""

import logging
import statistics
import uuid
from datetime import datetime, timezone

from src.models import BOSType, Candle, Signal, SignalDirection, StrategyConfig
from src.strategy.base import StrategyAlgorithm
from src.strategy.signal_helpers import build_signal

logger = logging.getLogger(__name__)


class SyntheticMAPullbackAlgorithm(StrategyAlgorithm):
    """EMA pullback strategy with RSI/ADX confirmation for Deriv V75/V25 synthetics."""

    @staticmethod
    def name() -> str:
        return "synthetic_ma_pullback"

    @staticmethod
    def description() -> str:
        return (
            "Moving Average Pullback strategy using dual EMA (50/200) trend "
            "detection with pullback-to-EMA entry, RSI momentum confirmation, "
            "and ADX trend strength filter for Deriv V75/V25 synthetics. "
            "Based on 56% win rate / 1.6:1 RR over 1000 backtest trades."
        )

    @staticmethod
    def default_params() -> dict:
        return {
            "fast_ema_period": 50,
            "slow_ema_period": 200,
            "rsi_period": 14,
            "rsi_buy_threshold": 50,
            "rsi_sell_threshold": 50,
            "adx_period": 14,
            "adx_threshold": 20,
            "atr_period": 14,
            "atr_buffer_mult": 0.5,
            "reward_risk_ratio": 2.0,
            "cooldown_candles": 5,
            "swing_lookback": 10,
            "confluence_filters": [],
            # V75 overrides — higher volatility needs wider buffers/thresholds
            "v75_adx_threshold": 25,
            "v75_atr_buffer_mult": 0.7,
            # V25 overrides — lower volatility allows tighter parameters
            "v25_adx_threshold": 18,
            "v25_atr_buffer_mult": 0.3,
        }

    @staticmethod
    def param_schema() -> dict:
        return {
            "fast_ema_period": {"type": "integer", "minimum": 5, "maximum": 100},
            "slow_ema_period": {"type": "integer", "minimum": 50, "maximum": 500},
            "rsi_period": {"type": "integer", "minimum": 2, "maximum": 50},
            "rsi_buy_threshold": {"type": "number", "minimum": 30, "maximum": 70},
            "rsi_sell_threshold": {"type": "number", "minimum": 30, "maximum": 70},
            "adx_period": {"type": "integer", "minimum": 2, "maximum": 50},
            "adx_threshold": {"type": "number", "minimum": 10, "maximum": 60},
            "atr_period": {"type": "integer", "minimum": 2, "maximum": 50},
            "atr_buffer_mult": {"type": "number", "minimum": 0.1, "maximum": 3.0},
            "reward_risk_ratio": {"type": "number", "minimum": 1.0, "maximum": 10.0},
            "cooldown_candles": {"type": "integer", "minimum": 0, "maximum": 50},
            "swing_lookback": {"type": "integer", "minimum": 3, "maximum": 50},
            "confluence_filters": {"type": "array", "items": {"type": "string"}},
        }

    def _resolve_params(self, config: StrategyConfig) -> dict:
        """Resolve instrument-specific parameter defaults.

        Resolution order:
        1. Explicit algorithm_params values (highest priority)
        2. Instrument-specific defaults (v75_*/v25_* → base key when instrument matches)
        3. Base defaults from default_params() (lowest priority)
        """
        params = {**self.default_params(), **config.algorithm_params}
        instrument = config.instruments[0] if config.instruments else ""

        if instrument == "R_75":
            prefix = "v75_"
        elif instrument == "R_25":
            prefix = "v25_"
        else:
            return params

        # Apply instrument defaults for keys not explicitly overridden
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
        """EMA pullback analysis with RSI/ADX/momentum confirmation.

        Entry logic:
        1. Compute EMA(50) and EMA(200) on structure_candles for trend
        2. BULLISH: EMA(50) > EMA(200); BEARISH: EMA(50) < EMA(200)
        3. Pullback: low <= EMA(50) for BUY, high >= EMA(50) for SELL
        4. RSI(14) > 50 for BUY, RSI(14) < 50 for SELL
        5. Momentum candle: close > prev close (BUY), close < prev close (SELL)
        6. ADX(14) > threshold to confirm trend exists
        7. SL at recent swing low/high ± ATR buffer
        8. TP at reward_risk_ratio × SL distance
        """
        params = self._resolve_params(config)

        fast_ema_period: int = params["fast_ema_period"]
        slow_ema_period: int = params["slow_ema_period"]
        rsi_period: int = params["rsi_period"]
        rsi_buy_threshold: float = params["rsi_buy_threshold"]
        rsi_sell_threshold: float = params["rsi_sell_threshold"]
        adx_period: int = params["adx_period"]
        adx_threshold: float = params["adx_threshold"]
        atr_period: int = params["atr_period"]
        atr_buffer_mult: float = params["atr_buffer_mult"]
        reward_risk_ratio: float = params["reward_risk_ratio"]
        swing_lookback: int = params["swing_lookback"]

        min_candles = max(slow_ema_period + 1, 2 * adx_period, atr_period + 1, rsi_period + 1)
        if len(structure_candles) < min_candles:
            return []

        closes = [c.close for c in structure_candles]
        highs = [c.high for c in structure_candles]
        lows = [c.low for c in structure_candles]

        # ── Compute indicators ──
        fast_ema = _compute_ema(closes, fast_ema_period)
        slow_ema = _compute_ema(closes, slow_ema_period)
        adx_values = _compute_adx(highs, lows, closes, adx_period)
        atr_values = _compute_atr(highs, lows, closes, atr_period)
        rsi = _compute_rsi(closes, rsi_period)

        if not fast_ema or not slow_ema or not adx_values or not atr_values or rsi is None:
            return []

        current_atr = atr_values[-1]
        current_adx = adx_values[-1]

        if current_atr == 0:
            return []

        # ── 1. Trend detection via dual EMA ──
        # fast_ema and slow_ema are seeded from SMA so indices align with closes
        # from period onwards. Use last values.
        current_fast_ema = fast_ema[-1]
        current_slow_ema = slow_ema[-1]

        is_bullish = current_fast_ema > current_slow_ema
        is_bearish = current_fast_ema < current_slow_ema

        if not is_bullish and not is_bearish:
            return []  # EMAs are equal — no trend

        # ── 2. ADX trend strength filter ──
        if current_adx < adx_threshold:
            return []  # Sideways market — avoid

        # ── 3. Pullback to fast EMA ──
        current_low = lows[-1]
        current_high = highs[-1]

        pullback_buy = is_bullish and current_low <= current_fast_ema
        pullback_sell = is_bearish and current_high >= current_fast_ema

        if not pullback_buy and not pullback_sell:
            return []  # No pullback to EMA

        # ── 4. RSI confirmation ──
        if pullback_buy and rsi <= rsi_buy_threshold:
            return []  # RSI not confirming bullish momentum
        if pullback_sell and rsi >= rsi_sell_threshold:
            return []  # RSI not confirming bearish momentum

        # ── 5. Momentum candle confirmation ──
        if len(closes) < 2:
            return []
        current_close = closes[-1]
        prev_close = closes[-2]

        if pullback_buy and current_close <= prev_close:
            return []  # No bullish momentum candle
        if pullback_sell and current_close >= prev_close:
            return []  # No bearish momentum candle

        # ── Determine direction ──
        direction = SignalDirection.BUY if pullback_buy else SignalDirection.SELL
        entry_price = current_close

        # ── SL placement: structural swing + ATR buffer ──
        lookback = min(swing_lookback, len(lows) - 1)
        if lookback < 1:
            return []

        if direction == SignalDirection.BUY:
            recent_swing_low = min(lows[-lookback:])
            stop_loss = recent_swing_low - atr_buffer_mult * current_atr
        else:
            recent_swing_high = max(highs[-lookback:])
            stop_loss = recent_swing_high + atr_buffer_mult * current_atr

        sl_distance = abs(entry_price - stop_loss)
        if sl_distance == 0:
            return []

        # ── TP placement: reward_risk_ratio × SL distance ──
        if direction == SignalDirection.BUY:
            take_profit = entry_price + reward_risk_ratio * sl_distance
        else:
            take_profit = entry_price - reward_risk_ratio * sl_distance

        # ── Confidence scoring ──
        confidence = 0.55

        # RSI strength bonus: +0.1 if RSI > 60 (BUY) or RSI < 40 (SELL)
        if direction == SignalDirection.BUY and rsi > 60:
            confidence += 0.1
        elif direction == SignalDirection.SELL and rsi < 40:
            confidence += 0.1

        # ADX strength bonus: +0.1 if ADX > 1.5× threshold
        if current_adx > 1.5 * adx_threshold:
            confidence += 0.1

        # Pullback quality: +0.1 if price touched EMA(50) precisely (within 0.5× ATR)
        if direction == SignalDirection.BUY:
            pullback_distance = abs(current_low - current_fast_ema)
        else:
            pullback_distance = abs(current_high - current_fast_ema)

        if pullback_distance <= 0.5 * current_atr:
            confidence += 0.1

        # Momentum candle size: +0.05 if current candle range > median range
        candle_ranges = [h - l for h, l in zip(highs, lows)]
        current_range = highs[-1] - lows[-1]
        if candle_ranges:
            median_range = statistics.median(candle_ranges)
            if current_range > median_range:
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

        # ── Build signal ──
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
            extra_metadata={
                "bos_type": bos_type,
                "adx": round(current_adx, 2),
                "rsi": round(rsi, 2),
                "fast_ema": round(current_fast_ema, 2),
                "slow_ema": round(current_slow_ema, 2),
                "pullback_distance": round(pullback_distance, 2),
                "atr_sl_multiplier": atr_buffer_mult,
            },
            confidence_score=confidence,
        )

        return [signal]


# ---------------------------------------------------------------------------
# Pure indicator computation functions
# ---------------------------------------------------------------------------


def _compute_ema(closes: list[float], period: int) -> list[float]:
    """Compute Exponential Moving Average with SMA seed.

    First value is SMA of the first `period` closes.
    Subsequent values use EMA formula: close * mult + prev_ema * (1 - mult)
    where mult = 2 / (period + 1).
    """
    if not closes or len(closes) < period:
        return []
    mult = 2.0 / (period + 1)
    ema = [sum(closes[:period]) / period]
    for i in range(period, len(closes)):
        ema.append(closes[i] * mult + ema[-1] * (1 - mult))
    return ema


def _compute_rsi(closes: list[float], period: int) -> float | None:
    """Compute RSI using Wilder's smoothing. Returns current RSI value."""
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


def _compute_adx(
    highs: list[float], lows: list[float], closes: list[float], period: int
) -> list[float]:
    """Compute ADX using Wilder's smoothing.

    Steps:
    1. Compute +DM and -DM from high/low deltas
    2. Compute True Range
    3. Smooth +DM, -DM, TR over period using Wilder's smoothing
    4. Compute +DI = smoothed_+DM / smoothed_TR * 100
    5. Compute -DI = smoothed_-DM / smoothed_TR * 100
    6. Compute DX = |+DI - -DI| / (+DI + -DI) * 100
    7. ADX = Wilder's smoothed average of DX over period
    """
    n = len(closes)
    if n < 2 * period:
        return []

    plus_dm = []
    minus_dm = []
    tr_list = []

    for i in range(1, n):
        high_diff = highs[i] - highs[i - 1]
        low_diff = lows[i - 1] - lows[i]

        pdm = high_diff if high_diff > low_diff and high_diff > 0 else 0.0
        mdm = low_diff if low_diff > high_diff and low_diff > 0 else 0.0

        plus_dm.append(pdm)
        minus_dm.append(mdm)

        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        tr_list.append(tr)

    if len(tr_list) < 2 * period - 1:
        return []

    smoothed_plus_dm = sum(plus_dm[:period])
    smoothed_minus_dm = sum(minus_dm[:period])
    smoothed_tr = sum(tr_list[:period])

    dx_values: list[float] = []

    def _compute_di_dx(s_pdm: float, s_mdm: float, s_tr: float) -> float:
        if s_tr == 0:
            return 0.0
        plus_di = s_pdm / s_tr * 100.0
        minus_di = s_mdm / s_tr * 100.0
        di_sum = plus_di + minus_di
        if di_sum == 0:
            return 0.0
        return abs(plus_di - minus_di) / di_sum * 100.0

    dx_values.append(_compute_di_dx(smoothed_plus_dm, smoothed_minus_dm, smoothed_tr))

    for i in range(period, len(tr_list)):
        smoothed_plus_dm = smoothed_plus_dm * (period - 1) / period + plus_dm[i]
        smoothed_minus_dm = smoothed_minus_dm * (period - 1) / period + minus_dm[i]
        smoothed_tr = smoothed_tr * (period - 1) / period + tr_list[i]
        dx_values.append(_compute_di_dx(smoothed_plus_dm, smoothed_minus_dm, smoothed_tr))

    if len(dx_values) < period:
        return []

    adx = sum(dx_values[:period]) / period
    adx_values = [adx]

    for i in range(period, len(dx_values)):
        adx = (adx * (period - 1) + dx_values[i]) / period
        adx_values.append(adx)

    return adx_values


def _compute_atr(
    highs: list[float], lows: list[float], closes: list[float], period: int
) -> list[float]:
    """Compute ATR using Wilder's smoothing.

    TR = max(high-low, abs(high-prev_close), abs(low-prev_close))
    First ATR = SMA of first `period` TRs.
    Subsequent: (prev_atr * (period-1) + current_tr) / period
    """
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
