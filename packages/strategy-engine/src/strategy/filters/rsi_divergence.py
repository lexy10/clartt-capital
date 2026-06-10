"""RSI Divergence confluence filter — detects bullish/bearish RSI divergences."""

from src.models import Candle, SignalDirection
from src.strategy.filters.base import ConfluenceFilter, FilterResult


class RSIDivergenceFilter(ConfluenceFilter):
    """Detects bullish/bearish RSI divergences between price and RSI oscillator."""

    @staticmethod
    def name() -> str:
        return "rsi_divergence"

    @staticmethod
    def default_params() -> dict:
        return {"rsi_period": 14, "divergence_lookback": 5, "rsi_bonus": 0.1}

    @staticmethod
    def param_schema() -> dict:
        return {
            "rsi_period": {"type": "integer", "minimum": 2, "maximum": 50},
            "divergence_lookback": {"type": "integer", "minimum": 2, "maximum": 20},
            "rsi_bonus": {"type": "number", "minimum": 0.0, "maximum": 0.5},
        }

    def evaluate(
        self,
        candles: list[Candle],
        direction: SignalDirection,
        params: dict,
    ) -> FilterResult:
        """Compute RSI, detect divergences, return confidence adjustment."""
        defaults = self.default_params()
        rsi_period = self._safe_int(params, "rsi_period", defaults["rsi_period"], 2, 50)
        divergence_lookback = self._safe_int(
            params, "divergence_lookback", defaults["divergence_lookback"], 2, 20
        )
        rsi_bonus = self._safe_float(params, "rsi_bonus", defaults["rsi_bonus"], 0.0, 0.5)

        closes = [c.close for c in candles]

        # Need at least rsi_period + 1 closes to compute RSI
        if len(closes) < rsi_period + 1:
            return FilterResult(
                passed=True,
                confidence_adjustment=0.0,
                reason="Insufficient candle data for RSI computation",
            )

        rsi_values = self._compute_rsi(closes, rsi_period)

        # Need at least divergence_lookback RSI values for divergence detection
        if len(rsi_values) < divergence_lookback:
            return FilterResult(
                passed=True,
                confidence_adjustment=0.0,
                reason="Insufficient RSI data for divergence detection",
            )

        # Get the price closes corresponding to the RSI values window
        # rsi_values[i] corresponds to closes[rsi_period + i]
        price_window = closes[-(len(rsi_values)):]
        rsi_window = rsi_values

        bullish = self._detect_bullish_divergence(
            price_window[-divergence_lookback:],
            rsi_window[-divergence_lookback:],
        )
        bearish = self._detect_bearish_divergence(
            price_window[-divergence_lookback:],
            rsi_window[-divergence_lookback:],
        )

        if bullish and not bearish:
            if direction == SignalDirection.BUY:
                return FilterResult(
                    passed=True,
                    confidence_adjustment=rsi_bonus,
                    reason="Bullish RSI divergence confirms BUY direction",
                )
            else:
                return FilterResult(
                    passed=True,
                    confidence_adjustment=-rsi_bonus,
                    reason="Bullish RSI divergence contradicts SELL direction",
                )

        if bearish and not bullish:
            if direction == SignalDirection.SELL:
                return FilterResult(
                    passed=True,
                    confidence_adjustment=rsi_bonus,
                    reason="Bearish RSI divergence confirms SELL direction",
                )
            else:
                return FilterResult(
                    passed=True,
                    confidence_adjustment=-rsi_bonus,
                    reason="Bearish RSI divergence contradicts BUY direction",
                )

        # No divergence (or both detected — treat as neutral)
        return FilterResult(
            passed=True,
            confidence_adjustment=0.0,
            reason="No RSI divergence detected",
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_rsi(closes: list[float], period: int) -> list[float]:
        """Compute Wilder's smoothed RSI over the given closes.

        Returns a list of RSI values. The first RSI value corresponds to
        closes[period] (i.e. after the first full window).
        """
        deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]

        gains = [max(d, 0.0) for d in deltas]
        losses = [max(-d, 0.0) for d in deltas]

        # First average is SMA of the first `period` deltas
        avg_gain = sum(gains[:period]) / period
        avg_loss = sum(losses[:period]) / period

        rsi_values: list[float] = []
        rsi_values.append(_rsi_from_averages(avg_gain, avg_loss))

        # Subsequent averages use Wilder's smoothing
        for i in range(period, len(deltas)):
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period
            rsi_values.append(_rsi_from_averages(avg_gain, avg_loss))

        return rsi_values

    @staticmethod
    def _detect_bullish_divergence(
        prices: list[float], rsi_vals: list[float]
    ) -> bool:
        """Detect bullish divergence: price lower low + RSI higher low.

        Finds the two most recent local lows in the window and checks
        whether price made a lower low while RSI made a higher low.
        """
        lows = _find_local_lows(prices, rsi_vals)
        if len(lows) < 2:
            return False
        # Two most recent local lows
        prev_price, prev_rsi = lows[-2]
        curr_price, curr_rsi = lows[-1]
        return curr_price < prev_price and curr_rsi > prev_rsi

    @staticmethod
    def _detect_bearish_divergence(
        prices: list[float], rsi_vals: list[float]
    ) -> bool:
        """Detect bearish divergence: price higher high + RSI lower high.

        Finds the two most recent local highs in the window and checks
        whether price made a higher high while RSI made a lower high.
        """
        highs = _find_local_highs(prices, rsi_vals)
        if len(highs) < 2:
            return False
        prev_price, prev_rsi = highs[-2]
        curr_price, curr_rsi = highs[-1]
        return curr_price > prev_price and curr_rsi < prev_rsi

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


# ------------------------------------------------------------------
# Module-level helpers
# ------------------------------------------------------------------


def _rsi_from_averages(avg_gain: float, avg_loss: float) -> float:
    """Compute RSI from average gain and average loss."""
    if avg_loss == 0.0:
        return 100.0 if avg_gain > 0.0 else 50.0
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + rs)


def _find_local_lows(
    prices: list[float], rsi_vals: list[float]
) -> list[tuple[float, float]]:
    """Find local lows in the price series.

    A local low at index i means prices[i] <= prices[i-1] and
    prices[i] <= prices[i+1]. The first and last points are considered
    local lows if they are lower than their single neighbour.

    Returns list of (price, rsi) tuples for each local low.
    """
    n = len(prices)
    if n == 0:
        return []
    if n == 1:
        return [(prices[0], rsi_vals[0])]

    lows: list[tuple[float, float]] = []
    for i in range(n):
        is_low = True
        if i > 0 and prices[i] > prices[i - 1]:
            is_low = False
        if i < n - 1 and prices[i] > prices[i + 1]:
            is_low = False
        if is_low:
            lows.append((prices[i], rsi_vals[i]))
    return lows


def _find_local_highs(
    prices: list[float], rsi_vals: list[float]
) -> list[tuple[float, float]]:
    """Find local highs in the price series.

    A local high at index i means prices[i] >= prices[i-1] and
    prices[i] >= prices[i+1]. The first and last points are considered
    local highs if they are higher than their single neighbour.

    Returns list of (price, rsi) tuples for each local high.
    """
    n = len(prices)
    if n == 0:
        return []
    if n == 1:
        return [(prices[0], rsi_vals[0])]

    highs: list[tuple[float, float]] = []
    for i in range(n):
        is_high = True
        if i > 0 and prices[i] < prices[i - 1]:
            is_high = False
        if i < n - 1 and prices[i] < prices[i + 1]:
            is_high = False
        if is_high:
            highs.append((prices[i], rsi_vals[i]))
    return highs
