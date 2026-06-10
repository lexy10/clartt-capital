"""Session filter — avoids trading during low-liquidity periods.

For indices (US30) and commodities (XAUUSD), the London and New York sessions
provide the best liquidity and directional moves. Asian session tends to produce
choppy, range-bound price action that generates false BOS signals.

Deriv synthetic indices (V75, V25) trade 24/7 with consistent volatility,
so session filtering is bypassed for them.
"""

import logging
from datetime import datetime, timezone

from src.models import Candle, SignalDirection
from src.strategy.filters.base import ConfluenceFilter, FilterResult

logger = logging.getLogger(__name__)

# Session times in UTC
LONDON_OPEN = 7  # 07:00 UTC (08:00 BST)
LONDON_CLOSE = 16  # 16:00 UTC
NY_OPEN = 12  # 12:00 UTC (08:00 EST / 13:00 during DST)
NY_CLOSE = 21  # 21:00 UTC (17:00 EST)

# Overlap period (highest liquidity)
OVERLAP_START = 12  # NY open
OVERLAP_END = 16  # London close

# Synthetic instruments bypass session filtering
SYNTHETIC_PREFIXES = ("v75", "v25", "volatility", "boom", "crash", "step", "range")


class SessionFilter(ConfluenceFilter):
    """Filters signals based on trading session times.

    Assigns a confidence bonus during high-liquidity sessions (London/NY overlap)
    and a penalty during low-liquidity periods (Asian session for non-synthetic
    instruments).
    """

    @staticmethod
    def name() -> str:
        return "session"

    @staticmethod
    def default_params() -> dict:
        return {
            "session_instrument": "",
            "session_overlap_bonus": 0.05,
            "session_active_bonus": 0.0,
            "session_off_hours_penalty": -0.15,
            "session_bypass_synthetics": True,
        }

    @staticmethod
    def param_schema() -> dict:
        return {
            "session_instrument": {"type": "string"},
            "session_overlap_bonus": {"type": "number", "minimum": -0.5, "maximum": 0.5},
            "session_active_bonus": {"type": "number", "minimum": -0.5, "maximum": 0.5},
            "session_off_hours_penalty": {"type": "number", "minimum": -0.5, "maximum": 0.0},
            "session_bypass_synthetics": {"type": "boolean"},
        }

    def evaluate(
        self,
        candles: list[Candle],
        direction: SignalDirection,
        params: dict,
    ) -> FilterResult:
        instrument = params.get("session_instrument", "").lower()
        bypass_synthetics = params.get("session_bypass_synthetics", True)
        overlap_bonus = params.get("session_overlap_bonus", 0.05)
        active_bonus = params.get("session_active_bonus", 0.0)
        off_hours_penalty = params.get("session_off_hours_penalty", -0.15)

        # Bypass for synthetic instruments
        if bypass_synthetics and any(instrument.startswith(p) for p in SYNTHETIC_PREFIXES):
            return FilterResult(
                passed=True,
                confidence_adjustment=0.0,
                reason="Synthetic instrument — session filter bypassed",
            )

        # Determine current hour from the last candle timestamp
        if not candles:
            return FilterResult(passed=True, confidence_adjustment=0.0, reason="No candles")

        try:
            ts = candles[-1].timestamp
            if isinstance(ts, str):
                dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            else:
                dt = ts
            current_hour = dt.hour
        except (ValueError, AttributeError):
            current_hour = datetime.now(timezone.utc).hour

        # London/NY overlap — best liquidity
        if OVERLAP_START <= current_hour < OVERLAP_END:
            return FilterResult(
                passed=True,
                confidence_adjustment=overlap_bonus,
                reason=f"London/NY overlap session (hour={current_hour} UTC)",
            )

        # Active London or NY session
        if LONDON_OPEN <= current_hour < LONDON_CLOSE or NY_OPEN <= current_hour < NY_CLOSE:
            return FilterResult(
                passed=True,
                confidence_adjustment=active_bonus,
                reason=f"Active session (hour={current_hour} UTC)",
            )

        # Off-hours (Asian session for forex/indices/commodities)
        return FilterResult(
            passed=False,
            confidence_adjustment=off_hours_penalty,
            reason=f"Low-liquidity session (hour={current_hour} UTC) — Asian/off-hours",
        )
