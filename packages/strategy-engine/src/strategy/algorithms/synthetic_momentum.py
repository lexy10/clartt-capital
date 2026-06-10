"""Momentum-based strategy algorithm for Deriv synthetic indices (V75/V25).

Uses Rate of Change (ROC), EMA crossover, ADX trend strength, and ATR-based
risk management. Designed for continuous 24/7 synthetic instruments with
distinct volatility profiles.
"""

import logging
import statistics
import uuid
from datetime import datetime, timezone

from src.models import BOSType, Candle, Signal, SignalDirection, StrategyConfig
from src.strategy.base import StrategyAlgorithm
from src.strategy.signal_helpers import build_signal

logger = logging.getLogger(__name__)


class SyntheticMomentumAlgorithm(StrategyAlgorithm):
    """Momentum strategy using ROC, EMA crossover, and ADX for Deriv V75/V25 synthetics."""

    @staticmethod
    def name() -> str:
        return "synthetic_momentum"

    @staticmethod
    def description() -> str:
        return (
            "Momentum strategy using ROC, EMA crossover, and ADX "
            "for Deriv V75/V25 synthetics"
        )

    @staticmethod
    def default_params() -> dict:
        return {
            "roc_period": 14,
            "fast_ema_period": 9,
            "slow_ema_period": 21,
            "adx_period": 14,
            "adx_threshold": 25,
            "atr_period": 14,
            "atr_sl_multiplier": 1.5,
            "reward_risk_ratio": 2.0,
            "cooldown_candles": 3,
            "confluence_filters": [],
            # Smart entry filters
            "pullback_atr_mult": 1.5,       # Max distance from slow EMA (in ATR) for pullback entry
            "fresh_trend_lookback": 10,      # Look back N candles for recent EMA crossover
            "require_pullback": True,        # Only enter on pullbacks to slow EMA
            "require_fresh_trend": False,    # Only enter near fresh crossovers
            "require_expanding_vol": True,   # Require ATR above its median
            # V75 overrides
            "v75_atr_sl_multiplier": 2.0,
            "v75_adx_threshold": 30,
            "v75_pullback_atr_mult": 2.0,
            # V25 overrides
            "v25_atr_sl_multiplier": 1.2,
            "v25_adx_threshold": 20,
        }

    @staticmethod
    def param_schema() -> dict:
        return {
            "roc_period": {"type": "integer", "minimum": 2, "maximum": 100},
            "fast_ema_period": {"type": "integer", "minimum": 2, "maximum": 50},
            "slow_ema_period": {"type": "integer", "minimum": 5, "maximum": 200},
            "adx_period": {"type": "integer", "minimum": 2, "maximum": 50},
            "adx_threshold": {"type": "number", "minimum": 10, "maximum": 60},
            "atr_period": {"type": "integer", "minimum": 2, "maximum": 50},
            "atr_sl_multiplier": {"type": "number", "minimum": 0.5, "maximum": 5.0},
            "reward_risk_ratio": {"type": "number", "minimum": 1.0, "maximum": 10.0},
            "cooldown_candles": {"type": "integer", "minimum": 0, "maximum": 50},
            "confluence_filters": {"type": "array", "items": {"type": "string"}},
            "pullback_atr_mult": {"type": "number", "minimum": 0.5, "maximum": 5.0},
            "fresh_trend_lookback": {"type": "integer", "minimum": 3, "maximum": 50},
            "require_pullback": {"type": "boolean"},
            "require_fresh_trend": {"type": "boolean"},
            "require_expanding_vol": {"type": "boolean"},
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
        """Smart momentum analysis with pullback entries and trend freshness.

        Entry logic:
        1. EMA trend direction (fast > slow = bullish, fast < slow = bearish)
        2. ROC confirms momentum in trend direction
        3. ADX confirms trend strength above threshold
        4. SMART FILTER: Pullback — price must be near slow EMA (within N×ATR)
        5. SMART FILTER: Fresh trend — EMA crossover happened within last N candles
        6. SMART FILTER: Expanding volatility — current ATR above its median
        7. SL placed below recent swing low (BUY) or above swing high (SELL)
        """
        params = self._resolve_params(config)

        roc_period: int = params["roc_period"]
        fast_ema_period: int = params["fast_ema_period"]
        slow_ema_period: int = params["slow_ema_period"]
        adx_period: int = params["adx_period"]
        adx_threshold: float = params["adx_threshold"]
        atr_period: int = params["atr_period"]
        atr_sl_multiplier: float = params["atr_sl_multiplier"]
        reward_risk_ratio: float = params["reward_risk_ratio"]
        pullback_atr_mult: float = params.get("pullback_atr_mult", 1.5)
        fresh_trend_lookback: int = params.get("fresh_trend_lookback", 10)
        require_pullback: bool = params.get("require_pullback", True)
        require_fresh: bool = params.get("require_fresh_trend", False)
        require_expanding: bool = params.get("require_expanding_vol", True)

        min_candles = max(roc_period + 1, slow_ema_period + fresh_trend_lookback, 2 * adx_period, atr_period + 1)
        if len(structure_candles) < min_candles:
            return []

        closes = [c.close for c in structure_candles]
        highs = [c.high for c in structure_candles]
        lows = [c.low for c in structure_candles]

        # Compute indicators
        roc_values = _compute_roc(closes, roc_period)
        fast_ema = _compute_ema(closes, fast_ema_period)
        slow_ema = _compute_ema(closes, slow_ema_period)
        adx_values = _compute_adx(highs, lows, closes, adx_period)
        atr_values = _compute_atr(highs, lows, closes, atr_period)

        if not atr_values or not adx_values or not roc_values:
            return []

        current_atr = atr_values[-1]
        current_adx = adx_values[-1]
        current_roc = roc_values[-1]

        if current_atr == 0:
            return []

        n = len(closes)
        if n - 1 >= len(fast_ema) or n - 1 >= len(slow_ema):
            return []

        # ── Core trend check ──
        ema_bullish = fast_ema[n - 1] > slow_ema[n - 1]
        ema_bearish = fast_ema[n - 1] < slow_ema[n - 1]

        direction: SignalDirection | None = None
        if ema_bullish and current_roc > 0 and current_adx > adx_threshold:
            direction = SignalDirection.BUY
        elif ema_bearish and current_roc < 0 and current_adx > adx_threshold:
            direction = SignalDirection.SELL
        else:
            return []

        # ── SMART FILTER 1: Pullback to slow EMA ──
        price_to_slow_ema = abs(closes[-1] - slow_ema[n - 1])
        if require_pullback and price_to_slow_ema > pullback_atr_mult * current_atr:
            return []  # Price too far from slow EMA — chasing the trend

        # ── SMART FILTER 2: Fresh trend (recent crossover) ──
        if require_fresh:
            has_recent_crossover = False
            lookback_start = max(1, n - fresh_trend_lookback)
            for j in range(lookback_start, n):
                if j >= len(fast_ema) or j - 1 >= len(fast_ema):
                    continue
                if j >= len(slow_ema) or j - 1 >= len(slow_ema):
                    continue
                if direction == SignalDirection.BUY:
                    if fast_ema[j] > slow_ema[j] and fast_ema[j - 1] <= slow_ema[j - 1]:
                        has_recent_crossover = True
                        break
                else:
                    if fast_ema[j] < slow_ema[j] and fast_ema[j - 1] >= slow_ema[j - 1]:
                        has_recent_crossover = True
                        break
            if not has_recent_crossover:
                return []  # Trend is stale — no recent crossover

        # ── SMART FILTER 3: Expanding volatility ──
        if require_expanding and len(atr_values) > 1:
            median_atr = statistics.median(atr_values)
            if current_atr < median_atr:
                return []  # Low volatility — not a strong trending move

        # ── Entry price and SL/TP ──
        entry_price = closes[-1]

        # Smart SL: use recent swing low/high instead of just ATR
        swing_lookback = min(10, len(lows) - 1)
        if direction == SignalDirection.BUY:
            recent_low = min(lows[-swing_lookback:])
            atr_sl = entry_price - atr_sl_multiplier * current_atr
            stop_loss = min(atr_sl, recent_low - 0.1 * current_atr)  # Below swing low
        else:
            recent_high = max(highs[-swing_lookback:])
            atr_sl = entry_price + atr_sl_multiplier * current_atr
            stop_loss = max(atr_sl, recent_high + 0.1 * current_atr)  # Above swing high

        sl_distance = abs(entry_price - stop_loss)
        if sl_distance == 0:
            return []

        if direction == SignalDirection.BUY:
            take_profit = entry_price + reward_risk_ratio * sl_distance
        else:
            take_profit = entry_price - reward_risk_ratio * sl_distance

        # ── Confidence scoring ──
        confidence = 0.55

        # Strong ADX bonus
        if current_adx > 1.5 * adx_threshold:
            confidence += 0.15

        # Pullback quality — closer to slow EMA = better entry
        pullback_quality = 1.0 - (price_to_slow_ema / (pullback_atr_mult * current_atr)) if current_atr > 0 else 0
        confidence += 0.1 * max(0, pullback_quality)

        # ROC strength bonus
        roc_magnitudes = [abs(r) for r in roc_values]
        if roc_magnitudes:
            median_roc = statistics.median(roc_magnitudes)
            if abs(current_roc) > median_roc:
                confidence += 0.1

        # Expanding vol bonus (already filtered, but reward extra expansion)
        if len(atr_values) > 1:
            median_atr = statistics.median(atr_values)
            if current_atr > 1.5 * median_atr:
                confidence += 0.05

        # Confluence filters
        filter_names = params.get("confluence_filters", [])
        if filter_names:
            from src.strategy.filters.apply import apply_confluence_filters
            adjustment = apply_confluence_filters(filter_names, structure_candles, direction, params)
            confidence += adjustment

        confidence = max(0.0, min(1.0, confidence))
        if confidence < config.min_confidence_score:
            return []

        # Build signal
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
                "roc": round(current_roc, 2),
                "pullback_quality": round(pullback_quality, 2),
                "atr_sl_multiplier": atr_sl_multiplier,
            },
            confidence_score=confidence,
        )

        return [signal]



# ---------------------------------------------------------------------------
# Pure indicator computation functions
# ---------------------------------------------------------------------------


def _compute_roc(closes: list[float], period: int) -> list[float]:
    """Compute Rate of Change: (close[i] - close[i-period]) / close[i-period] * 100.

    Returns a list of length len(closes) with None for indices < period.
    """
    result: list[float | None] = [None] * len(closes)
    for i in range(period, len(closes)):
        prev = closes[i - period]
        if prev == 0:
            result[i] = 0.0
        else:
            result[i] = (closes[i] - prev) / prev * 100.0
    # Filter to only valid values for downstream use
    return [v for v in result if v is not None]


def _compute_ema(closes: list[float], period: int) -> list[float]:
    """Compute Exponential Moving Average.

    EMA[0] = close[0], EMA[i] = close[i] * mult + EMA[i-1] * (1 - mult)
    where mult = 2 / (period + 1).
    """
    if not closes:
        return []
    multiplier = 2.0 / (period + 1)
    ema = [closes[0]]
    for i in range(1, len(closes)):
        ema.append(closes[i] * multiplier + ema[-1] * (1 - multiplier))
    return ema


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

    # Step 1: +DM, -DM, TR
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

    # Step 2: Wilder's smoothing for first period values (SMA)
    smoothed_plus_dm = sum(plus_dm[:period])
    smoothed_minus_dm = sum(minus_dm[:period])
    smoothed_tr = sum(tr_list[:period])

    # Compute +DI, -DI, DX series
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

    # Continue Wilder's smoothing
    for i in range(period, len(tr_list)):
        smoothed_plus_dm = smoothed_plus_dm * (period - 1) / period + plus_dm[i]
        smoothed_minus_dm = smoothed_minus_dm * (period - 1) / period + minus_dm[i]
        smoothed_tr = smoothed_tr * (period - 1) / period + tr_list[i]
        dx_values.append(_compute_di_dx(smoothed_plus_dm, smoothed_minus_dm, smoothed_tr))

    if len(dx_values) < period:
        return []

    # Step 3: ADX = Wilder's smoothed average of DX
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

    # First ATR = SMA of first period TRs
    atr = sum(tr_list[:period]) / period
    atr_values = [atr]

    for i in range(period, len(tr_list)):
        atr = (atr * (period - 1) + tr_list[i]) / period
        atr_values.append(atr)

    return atr_values
