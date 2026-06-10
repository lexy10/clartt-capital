"""Regime filter — detects consolidation/ranging markets and penalizes signals.

Market structure trading (BOS/CHoCH) works best in trending environments.
When ADX is low and price is range-bound, fractal breaks are noise rather than
genuine directional moves. This filter uses ADX to measure trend strength and
optionally checks if recent candles are compressing (low ATR relative to average).

Signals are penalized or blocked entirely in ranging regimes.
"""

import logging

from src.models import Candle, SignalDirection
from src.strategy.filters.base import ConfluenceFilter, FilterResult

logger = logging.getLogger(__name__)


def compute_dx_series(candles: list[Candle], period: int) -> list[float]:
    """Compute DX (Directional Movement Index) values for ADX calculation.

    Uses Wilder's smoothing for +DI and -DI, then DX = |+DI - -DI| / (+DI + -DI).
    """
    if len(candles) < period + 1:
        return []

    plus_dm_list: list[float] = []
    minus_dm_list: list[float] = []
    tr_list: list[float] = []

    for i in range(1, len(candles)):
        high = candles[i].high
        low = candles[i].low
        prev_high = candles[i - 1].high
        prev_low = candles[i - 1].low
        prev_close = candles[i - 1].close

        # True Range
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        tr_list.append(tr)

        # Directional Movement
        up_move = high - prev_high
        down_move = prev_low - low

        plus_dm = up_move if up_move > down_move and up_move > 0 else 0.0
        minus_dm = down_move if down_move > up_move and down_move > 0 else 0.0

        plus_dm_list.append(plus_dm)
        minus_dm_list.append(minus_dm)

    if len(tr_list) < period:
        return []

    # Wilder's smoothing for initial values
    smoothed_tr = sum(tr_list[:period])
    smoothed_plus_dm = sum(plus_dm_list[:period])
    smoothed_minus_dm = sum(minus_dm_list[:period])

    dx_values: list[float] = []

    for i in range(period, len(tr_list)):
        smoothed_tr = smoothed_tr - (smoothed_tr / period) + tr_list[i]
        smoothed_plus_dm = smoothed_plus_dm - (smoothed_plus_dm / period) + plus_dm_list[i]
        smoothed_minus_dm = smoothed_minus_dm - (smoothed_minus_dm / period) + minus_dm_list[i]

        if smoothed_tr == 0:
            dx_values.append(0.0)
            continue

        plus_di = 100.0 * smoothed_plus_dm / smoothed_tr
        minus_di = 100.0 * smoothed_minus_dm / smoothed_tr

        di_sum = plus_di + minus_di
        if di_sum == 0:
            dx_values.append(0.0)
        else:
            dx = 100.0 * abs(plus_di - minus_di) / di_sum
            dx_values.append(dx)

    return dx_values


def compute_adx(candles: list[Candle], period: int = 14) -> float | None:
    """Compute current ADX value using Wilder's method.

    Returns None if insufficient data.
    """
    dx_values = compute_dx_series(candles, period)

    if len(dx_values) < period:
        return None

    # Initial ADX = average of first `period` DX values
    adx = sum(dx_values[:period]) / period

    # Smooth remaining DX values
    for i in range(period, len(dx_values)):
        adx = (adx * (period - 1) + dx_values[i]) / period

    return adx


def compute_atr_compression(candles: list[Candle], period: int = 14) -> float | None:
    """Compute ATR compression ratio: current ATR / average ATR over lookback.

    Values < 1.0 indicate compression (ranging). Values > 1.0 indicate expansion.
    Returns None if insufficient data.
    """
    if len(candles) < period + 1:
        return None

    tr_values: list[float] = []
    for i in range(1, len(candles)):
        high = candles[i].high
        low = candles[i].low
        prev_close = candles[i - 1].close
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        tr_values.append(tr)

    if len(tr_values) < period:
        return None

    # Current ATR (last `period` TRs)
    current_atr = sum(tr_values[-period:]) / period

    # Average ATR over all available data
    avg_atr = sum(tr_values) / len(tr_values)

    if avg_atr == 0:
        return None

    return current_atr / avg_atr


class RegimeFilter(ConfluenceFilter):
    """Detects ranging/consolidating markets using ADX and ATR compression.

    - ADX < threshold → ranging market, penalize or block signal
    - ATR compression < threshold → volatility squeeze, penalize
    - Both trending → bonus confidence
    """

    @staticmethod
    def name() -> str:
        return "regime"

    @staticmethod
    def default_params() -> dict:
        return {
            "regime_adx_period": 14,
            "regime_adx_trending_threshold": 25.0,
            "regime_adx_strong_threshold": 35.0,
            "regime_adx_weak_threshold": 20.0,
            "regime_atr_compression_threshold": 0.75,
            "regime_trending_bonus": 0.05,
            "regime_strong_trend_bonus": 0.1,
            "regime_ranging_penalty": -0.15,
            "regime_block_below_adx": 15.0,
        }

    @staticmethod
    def param_schema() -> dict:
        return {
            "regime_adx_period": {"type": "integer", "minimum": 5, "maximum": 50},
            "regime_adx_trending_threshold": {"type": "number", "minimum": 10, "maximum": 50},
            "regime_adx_strong_threshold": {"type": "number", "minimum": 20, "maximum": 60},
            "regime_adx_weak_threshold": {"type": "number", "minimum": 5, "maximum": 30},
            "regime_atr_compression_threshold": {"type": "number", "minimum": 0.3, "maximum": 1.5},
            "regime_trending_bonus": {"type": "number", "minimum": 0.0, "maximum": 0.3},
            "regime_strong_trend_bonus": {"type": "number", "minimum": 0.0, "maximum": 0.3},
            "regime_ranging_penalty": {"type": "number", "minimum": -0.5, "maximum": 0.0},
            "regime_block_below_adx": {"type": "number", "minimum": 0, "maximum": 25},
        }

    def evaluate(
        self,
        candles: list[Candle],
        direction: SignalDirection,
        params: dict,
    ) -> FilterResult:
        adx_period = int(params.get("regime_adx_period", 14))
        trending_threshold = params.get("regime_adx_trending_threshold", 25.0)
        strong_threshold = params.get("regime_adx_strong_threshold", 35.0)
        weak_threshold = params.get("regime_adx_weak_threshold", 20.0)
        atr_compression_threshold = params.get("regime_atr_compression_threshold", 0.75)
        trending_bonus = params.get("regime_trending_bonus", 0.05)
        strong_bonus = params.get("regime_strong_trend_bonus", 0.1)
        ranging_penalty = params.get("regime_ranging_penalty", -0.15)
        block_below = params.get("regime_block_below_adx", 15.0)

        adx = compute_adx(candles, adx_period)
        if adx is None:
            # Insufficient data — pass with no adjustment
            return FilterResult(
                passed=True,
                confidence_adjustment=0.0,
                reason="Insufficient data for ADX calculation",
            )

        atr_ratio = compute_atr_compression(candles, adx_period)

        # Hard block — very low ADX means no trend at all
        if adx < block_below:
            return FilterResult(
                passed=False,
                confidence_adjustment=ranging_penalty,
                reason=f"ADX={adx:.1f} below block threshold ({block_below}) — no trend detected",
            )

        # Ranging — ADX below trending threshold
        if adx < weak_threshold:
            penalty = ranging_penalty
            # Additional penalty if ATR is compressed
            if atr_ratio is not None and atr_ratio < atr_compression_threshold:
                penalty += -0.05
            return FilterResult(
                passed=False,
                confidence_adjustment=penalty,
                reason=f"ADX={adx:.1f} below weak threshold ({weak_threshold}) — ranging market",
            )

        # Weak trend — between weak and trending threshold
        if adx < trending_threshold:
            adjustment = ranging_penalty * 0.5  # Half penalty
            return FilterResult(
                passed=True,
                confidence_adjustment=adjustment,
                reason=f"ADX={adx:.1f} — weak trend, reduced confidence",
            )

        # Strong trend
        if adx >= strong_threshold:
            bonus = strong_bonus
            # Extra bonus if ATR is expanding
            if atr_ratio is not None and atr_ratio > 1.2:
                bonus += 0.03
            return FilterResult(
                passed=True,
                confidence_adjustment=bonus,
                reason=f"ADX={adx:.1f} — strong trend with expanding volatility",
            )

        # Normal trending
        return FilterResult(
            passed=True,
            confidence_adjustment=trending_bonus,
            reason=f"ADX={adx:.1f} — trending market",
        )
