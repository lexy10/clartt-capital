"""Multi-Timeframe Alignment confluence filter — confirms higher-timeframe trend alignment."""

from src.models import Candle, SignalDirection
from src.strategy.filters.base import ConfluenceFilter, FilterResult


class MTFAlignmentFilter(ConfluenceFilter):
    """Confirms higher-timeframe trend alignment with signal direction using EMA comparison."""

    @staticmethod
    def name() -> str:
        return "mtf_alignment"

    @staticmethod
    def default_params() -> dict:
        return {
            "mtf_fast_ema": 9,
            "mtf_slow_ema": 21,
            "alignment_bonus": 0.1,
            "ema_tolerance_pct": 0.1,
        }

    @staticmethod
    def param_schema() -> dict:
        return {
            "mtf_fast_ema": {"type": "integer", "minimum": 2, "maximum": 50},
            "mtf_slow_ema": {"type": "integer", "minimum": 5, "maximum": 200},
            "alignment_bonus": {"type": "number", "minimum": 0.0, "maximum": 0.5},
            "ema_tolerance_pct": {"type": "number", "minimum": 0.0, "maximum": 5.0},
        }

    def evaluate(
        self,
        candles: list[Candle],
        direction: SignalDirection,
        params: dict,
    ) -> FilterResult:
        """Compute fast/slow EMA on candles, compare with signal direction."""
        defaults = self.default_params()
        fast_period = self._safe_int(params, "mtf_fast_ema", defaults["mtf_fast_ema"], 2, 50)
        slow_period = self._safe_int(params, "mtf_slow_ema", defaults["mtf_slow_ema"], 5, 200)
        alignment_bonus = self._safe_float(
            params, "alignment_bonus", defaults["alignment_bonus"], 0.0, 0.5
        )
        ema_tolerance_pct = self._safe_float(
            params, "ema_tolerance_pct", defaults["ema_tolerance_pct"], 0.0, 5.0
        )

        closes = [c.close for c in candles]

        # Need at least slow_period candles to compute the slow EMA meaningfully
        if len(closes) < slow_period:
            return FilterResult(
                passed=True,
                confidence_adjustment=0.0,
                reason="Insufficient candle data for EMA computation",
            )

        fast_ema = self._compute_ema(closes, fast_period)
        slow_ema = self._compute_ema(closes, slow_period)

        # Division by zero guard
        if slow_ema == 0.0:
            return FilterResult(
                passed=True,
                confidence_adjustment=0.0,
                reason="Slow EMA is zero, treating as neutral",
            )

        # Check tolerance — EMAs within ema_tolerance_pct% of each other
        pct_diff = abs(fast_ema - slow_ema) / abs(slow_ema) * 100.0
        if pct_diff < ema_tolerance_pct:
            return FilterResult(
                passed=True,
                confidence_adjustment=0.0,
                reason="EMAs within tolerance, no clear trend",
            )

        # Determine trend and alignment
        trend_bullish = fast_ema > slow_ema

        if trend_bullish and direction == SignalDirection.BUY:
            return FilterResult(
                passed=True,
                confidence_adjustment=alignment_bonus,
                reason="Higher-timeframe trend aligns with BUY direction",
            )
        if not trend_bullish and direction == SignalDirection.SELL:
            return FilterResult(
                passed=True,
                confidence_adjustment=alignment_bonus,
                reason="Higher-timeframe trend aligns with SELL direction",
            )

        # Contradiction
        return FilterResult(
            passed=True,
            confidence_adjustment=-alignment_bonus,
            reason="Higher-timeframe trend contradicts signal direction",
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_ema(closes: list[float], period: int) -> float:
        """Compute EMA over closes and return the final EMA value.

        EMA[0] = close[0]
        EMA[i] = close[i] * multiplier + EMA[i-1] * (1 - multiplier)
        multiplier = 2 / (period + 1)
        """
        if not closes:
            return 0.0
        multiplier = 2.0 / (period + 1)
        ema = closes[0]
        for i in range(1, len(closes)):
            ema = closes[i] * multiplier + ema * (1.0 - multiplier)
        return ema

    @staticmethod
    def _safe_int(
        params: dict, key: str, default: int, min_val: int, max_val: int
    ) -> int:
        val = params.get(key, default)
        if not isinstance(val, int) or val < min_val or val > max_val:
            return default
        return val

    @staticmethod
    def _safe_float(
        params: dict, key: str, default: float, min_val: float, max_val: float
    ) -> float:
        val = params.get(key, default)
        if not isinstance(val, (int, float)) or val < min_val or val > max_val:
            return default
        return float(val)
