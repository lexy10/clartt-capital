"""Volume Profile confluence filter — identifies high-volume price nodes and value areas."""

from src.models import Candle, SignalDirection
from src.strategy.filters.base import ConfluenceFilter, FilterResult


class VolumeProfileFilter(ConfluenceFilter):
    """Identifies high-volume price nodes and value areas from recent candle data."""

    @staticmethod
    def name() -> str:
        return "volume_profile"

    @staticmethod
    def default_params() -> dict:
        return {
            "profile_lookback": 50,
            "value_area_pct": 70,
            "volume_bonus": 0.1,
            "num_bins": 50,
        }

    @staticmethod
    def param_schema() -> dict:
        return {
            "profile_lookback": {"type": "integer", "minimum": 10, "maximum": 500},
            "value_area_pct": {"type": "integer", "minimum": 50, "maximum": 95},
            "volume_bonus": {"type": "number", "minimum": 0.0, "maximum": 0.5},
            "num_bins": {"type": "integer", "minimum": 10, "maximum": 200},
        }

    def evaluate(
        self,
        candles: list[Candle],
        direction: SignalDirection,
        params: dict,
    ) -> FilterResult:
        """Build volume profile, check if entry price is in value area."""
        defaults = self.default_params()
        profile_lookback = self._safe_int(params, "profile_lookback", defaults["profile_lookback"], 10, 500)
        value_area_pct = self._safe_int(params, "value_area_pct", defaults["value_area_pct"], 50, 95)
        volume_bonus = self._safe_float(params, "volume_bonus", defaults["volume_bonus"], 0.0, 0.5)
        num_bins = self._safe_int(params, "num_bins", defaults["num_bins"], 10, 200)

        # Take the last profile_lookback candles
        window = candles[-profile_lookback:]

        # Check for all-zero volume
        total_volume = sum(c.volume for c in window)
        if total_volume == 0.0:
            return FilterResult(
                passed=True,
                confidence_adjustment=0.0,
                reason="All candles have zero volume",
            )

        # Find overall price range
        price_low = min(c.low for c in window)
        price_high = max(c.high for c in window)
        price_range = price_high - price_low

        # Zero price range — cannot build meaningful profile
        if price_range == 0.0:
            return FilterResult(
                passed=True,
                confidence_adjustment=0.0,
                reason="Zero price range, cannot build volume profile",
            )

        # Build volume profile: distribute each candle's volume across bins
        bin_width = price_range / num_bins
        bins = [0.0] * num_bins

        for c in window:
            if c.volume <= 0.0:
                continue
            # Determine which bins this candle spans
            candle_low = c.low
            candle_high = c.high
            candle_range = candle_high - candle_low

            if candle_range == 0.0:
                # Single-price candle: all volume goes to one bin
                bin_idx = int((candle_low - price_low) / bin_width)
                bin_idx = min(bin_idx, num_bins - 1)
                bins[bin_idx] += c.volume
            else:
                # Find the range of bins this candle spans
                first_bin = int((candle_low - price_low) / bin_width)
                last_bin = int((candle_high - price_low) / bin_width)
                first_bin = max(0, min(first_bin, num_bins - 1))
                last_bin = max(0, min(last_bin, num_bins - 1))
                span = last_bin - first_bin + 1
                vol_per_bin = c.volume / span
                for b in range(first_bin, last_bin + 1):
                    bins[b] += vol_per_bin

        # Identify POC (bin with highest accumulated volume)
        poc_idx = 0
        for i in range(1, num_bins):
            if bins[i] > bins[poc_idx]:
                poc_idx = i

        # Build Value Area by expanding outward from POC
        in_area = [False] * num_bins
        in_area[poc_idx] = True
        area_volume = bins[poc_idx]
        target_volume = total_volume * value_area_pct / 100.0

        left = poc_idx - 1
        right = poc_idx + 1

        while area_volume < target_volume and (left >= 0 or right < num_bins):
            left_vol = bins[left] if left >= 0 else -1.0
            right_vol = bins[right] if right < num_bins else -1.0

            if left_vol >= right_vol:
                in_area[left] = True
                area_volume += left_vol
                left -= 1
            else:
                in_area[right] = True
                area_volume += right_vol
                right += 1

        # Determine entry price (last candle's close)
        entry_price = candles[-1].close

        # Check if entry price falls within the Value Area
        entry_bin = int((entry_price - price_low) / bin_width)
        entry_bin = max(0, min(entry_bin, num_bins - 1))

        if in_area[entry_bin]:
            return FilterResult(
                passed=True,
                confidence_adjustment=volume_bonus,
                reason="Entry price within Value Area",
            )

        return FilterResult(
            passed=True,
            confidence_adjustment=0.0,
            reason="Entry price outside Value Area",
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

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
