"""Boom/Crash Spike Rider — optimized for Deriv Boom & Crash indices.

Post-spike continuation strategy for Boom 500/1000 and Crash 500/1000.
These instruments are FUNDAMENTALLY DIFFERENT from volatility indices:
- Boom: price drifts DOWN slowly, then SPIKES UP at random intervals
- Crash: price drifts UP slowly, then SPIKES DOWN at random intervals

You CANNOT predict the spike timing. Instead, this strategy trades the
POST-SPIKE continuation: after a spike, the instrument resets and begins
its drift pattern again. Enter in the drift direction with a spike-
detection confirmation.

Core strengths:
- Boom spikes up → the drift between spikes is DOWN → SELL the drift
- Crash spikes down → the drift between spikes is UP → BUY the drift
- After a spike completes, the drift is very consistent
- The spike itself provides a clear structural level for SL

Timeframes: 1H (spike detection) → 15m (entry timing) → 5m (confirmation)

Technique:
- Detect recent spike on 1H (candle range > 3x median ATR)
- Wait for post-spike consolidation (Stochastic normalizes)
- Enter in the DRIFT direction (opposite of spike)
  - Boom: spike UP → sell the drift DOWN
  - Crash: spike DOWN → buy the drift UP
- SL above/below the spike high/low
- TP at 2.0R (drift moves are reliable but not huge)
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
    _compute_atr, _compute_stochastic,
)

logger = logging.getLogger(__name__)


class BoomCrashSpikeAlgorithm(StrategyAlgorithm):
    """Post-spike drift trader for Boom/Crash indices.

    Entry gates:
    1. Spike detected: recent 1H candle with range > spike_atr_mult × ATR
    2. Post-spike cooldown: at least N candles after the spike
    3. Stochastic normalization: %K returns to mid-range (40-60) after spike extreme
    4. 5m confirmation candle in drift direction
    + anti-clustering
    """

    def __init__(self) -> None:
        self._recent_signals: deque[tuple[float, str]] = deque(maxlen=20)

    @staticmethod
    def name() -> str:
        return "boom_crash_spike"

    @staticmethod
    def description() -> str:
        return (
            "Boom/Crash Spike Rider — post-spike drift continuation for "
            "Boom 500/1000 and Crash 500/1000. Detects spikes, waits for "
            "consolidation, enters in drift direction. SL beyond spike level, "
            "2R target. Boom = sell drift, Crash = buy drift."
        )

    @staticmethod
    def default_params() -> dict:
        return {
            # --- Spike detection ---
            "spike_atr_mult": 3.0,          # Candle range > this × ATR = spike
            "spike_lookback": 10,           # Look back N candles for recent spike
            "post_spike_cooldown": 3,       # Wait N candles after spike before entry
            # --- Instrument direction ---
            "instrument_direction": "auto", # "auto" detects from symbol, or "boom"/"crash"
            # --- Stochastic normalization ---
            "stoch_k_period": 14,
            "stoch_k_smooth": 3,
            "stoch_d_smooth": 3,
            "stoch_neutral_low": 35,        # %K must be between these after spike
            "stoch_neutral_high": 65,
            # --- Confirmation ---
            "require_confirm_candle": True,
            # --- ATR ---
            "atr_period": 14,
            "sl_spike_buffer_mult": 0.3,    # Buffer beyond spike high/low for SL
            "min_sl_atr": 0.5,
            "atr_lookback_window": 50,
            # --- Targets ---
            "reward_risk_ratio": 2.0,       # Drift moves are reliable but moderate
            # --- Anti-clustering ---
            "cooldown_candles": 3,
            "cooldown_price_atr_mult": 1.5,
            # --- Confidence ---
            "base_confidence": 0.60,
            "confluence_filters": [],
        }

    @staticmethod
    def param_schema() -> dict:
        return {
            "spike_atr_mult": {"type": "number", "minimum": 1.5, "maximum": 10.0},
            "spike_lookback": {"type": "integer", "minimum": 3, "maximum": 30},
            "post_spike_cooldown": {"type": "integer", "minimum": 1, "maximum": 10},
            "instrument_direction": {"type": "string"},
            "stoch_k_period": {"type": "integer", "minimum": 5, "maximum": 30},
            "stoch_k_smooth": {"type": "integer", "minimum": 1, "maximum": 10},
            "stoch_d_smooth": {"type": "integer", "minimum": 1, "maximum": 10},
            "stoch_neutral_low": {"type": "number", "minimum": 20, "maximum": 50},
            "stoch_neutral_high": {"type": "number", "minimum": 50, "maximum": 80},
            "require_confirm_candle": {"type": "boolean"},
            "atr_period": {"type": "integer", "minimum": 2, "maximum": 50},
            "sl_spike_buffer_mult": {"type": "number", "minimum": 0.1, "maximum": 2.0},
            "min_sl_atr": {"type": "number", "minimum": 0.1, "maximum": 3.0},
            "atr_lookback_window": {"type": "integer", "minimum": 10, "maximum": 200},
            "reward_risk_ratio": {"type": "number", "minimum": 1.0, "maximum": 5.0},
            "cooldown_candles": {"type": "integer", "minimum": 0, "maximum": 20},
            "cooldown_price_atr_mult": {"type": "number", "minimum": 0.3, "maximum": 5.0},
            "base_confidence": {"type": "number", "minimum": 0.3, "maximum": 0.9},
            "confluence_filters": {"type": "array", "items": {"type": "string"}},
        }

    def _resolve_params(self, config: StrategyConfig) -> dict:
        return {**self.default_params(), **config.algorithm_params}

    def _detect_instrument_type(self, instrument: str, params: dict) -> str | None:
        """Determine if instrument is boom or crash. Returns 'boom', 'crash', or None."""
        override = params.get("instrument_direction", "auto")
        if override == "boom":
            return "boom"
        if override == "crash":
            return "crash"
        inst = instrument.upper()
        if "BOOM" in inst:
            return "boom"
        if "CRASH" in inst:
            return "crash"
        return None

    def _is_in_cooldown(self, price: float, atr: float, params: dict) -> bool:
        threshold = params["cooldown_price_atr_mult"] * atr
        return any(abs(price - p) < threshold for p, _ in self._recent_signals)

    def _record_signal(self, price: float, ts: str) -> None:
        self._recent_signals.append((price, ts))

    def analyze(
        self, entry_candles: list[Candle], structure_candles: list[Candle],
        trend_candles: list[Candle], config: StrategyConfig, **kwargs,
    ) -> list[Signal]:
        """
        Timeframe mapping for Boom/Crash:
        - trend_candles (1H): spike detection + ATR
        - structure_candles (15m): Stochastic timing
        - entry_candles (5m): confirmation
        """
        params = self._resolve_params(config)
        instrument = config.instruments[0] if config.instruments else ""
        inst_type = self._detect_instrument_type(instrument, params)
        if inst_type is None:
            return []  # Not a Boom/Crash instrument

        # Drift direction: Boom drifts down (SELL), Crash drifts up (BUY)
        if inst_type == "boom":
            direction = SignalDirection.SELL
        else:
            direction = SignalDirection.BUY

        spike_mult = params["spike_atr_mult"]
        spike_lookback = params["spike_lookback"]
        post_cooldown = params["post_spike_cooldown"]
        atr_period = params["atr_period"]

        # Guards
        stoch_min = params["stoch_k_period"] + params["stoch_k_smooth"] + params["stoch_d_smooth"] + 1
        if len(trend_candles) < max(atr_period + 1, spike_lookback + post_cooldown + 5):
            return []
        if len(structure_candles) < stoch_min:
            return []
        if len(entry_candles) < 2:
            return []

        # GATE 1: Spike detection on trend candles (1H)
        t_highs = [c.high for c in trend_candles]
        t_lows = [c.low for c in trend_candles]
        t_closes = [c.close for c in trend_candles]

        atr_values = _compute_atr(t_highs, t_lows, t_closes, atr_period)
        if not atr_values or atr_values[-1] <= 0:
            return []
        current_atr = atr_values[-1]

        # Find most recent spike within lookback
        spike_idx = None
        spike_high = None
        spike_low = None
        n = len(trend_candles)
        for i in range(n - post_cooldown - 1, max(n - spike_lookback - post_cooldown, 0) - 1, -1):
            if i < 0 or i >= n:
                continue
            candle_range = trend_candles[i].high - trend_candles[i].low
            atr_idx = min(i - (n - len(atr_values)), len(atr_values) - 1)
            if atr_idx < 0:
                continue
            local_atr = atr_values[atr_idx]
            if local_atr > 0 and candle_range > spike_mult * local_atr:
                spike_idx = i
                spike_high = trend_candles[i].high
                spike_low = trend_candles[i].low
                break

        if spike_idx is None:
            return []  # No recent spike

        # Ensure post-spike cooldown (enough candles after spike)
        candles_since_spike = n - 1 - spike_idx
        if candles_since_spike < post_cooldown:
            return []

        # GATE 2: Stochastic normalization on structure candles (15m)
        s_highs = [c.high for c in structure_candles]
        s_lows = [c.low for c in structure_candles]
        s_closes = [c.close for c in structure_candles]

        stoch_k, stoch_d = _compute_stochastic(
            s_highs, s_lows, s_closes,
            params["stoch_k_period"], params["stoch_k_smooth"], params["stoch_d_smooth"],
        )
        if not stoch_k:
            return []

        k_now = stoch_k[-1]
        neutral_low = params["stoch_neutral_low"]
        neutral_high = params["stoch_neutral_high"]

        # After a boom spike (up), stoch should have been overbought and now normalized
        # After a crash spike (down), stoch should have been oversold and now normalized
        if not (neutral_low <= k_now <= neutral_high):
            return []  # Not yet normalized

        # GATE 3: Confirmation candle (5m)
        if params["require_confirm_candle"]:
            c = entry_candles[-1]
            if direction == SignalDirection.BUY and c.close <= c.open:
                return []
            if direction == SignalDirection.SELL and c.close >= c.open:
                return []

        # Anti-clustering
        entry_price = s_closes[-1]
        if self._is_in_cooldown(entry_price, current_atr, params):
            return []

        # SL beyond spike level + TP
        buffer = params["sl_spike_buffer_mult"] * current_atr
        if direction == SignalDirection.SELL:
            # Selling after boom spike: SL above the spike high
            stop_loss = spike_high + buffer
            sl_distance = stop_loss - entry_price
        else:
            # Buying after crash spike: SL below the spike low
            stop_loss = spike_low - buffer
            sl_distance = entry_price - stop_loss

        min_sl = params["min_sl_atr"] * current_atr
        if sl_distance < min_sl:
            if direction == SignalDirection.BUY:
                stop_loss = entry_price - min_sl
            else:
                stop_loss = entry_price + min_sl
            sl_distance = min_sl
        if sl_distance <= 0:
            return []

        tp_distance = params["reward_risk_ratio"] * sl_distance
        take_profit = entry_price + tp_distance if direction == SignalDirection.BUY else entry_price - tp_distance

        # Confidence
        confidence = params["base_confidence"]
        # Bigger spike = stronger signal
        spike_range = spike_high - spike_low
        if spike_range > spike_mult * 1.5 * current_atr:
            confidence += 0.10
        # Stochastic near 50 = well-normalized
        if 45 <= k_now <= 55:
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
            instrument=instrument, direction=direction,
            entry_price=entry_price, stop_loss=stop_loss, take_profit=take_profit,
            config=config, candles=structure_candles, timestamp=timestamp,
            order_block_id=str(uuid.uuid4()),
            extra_metadata={
                "bos_type": BOSType.BULLISH if direction == SignalDirection.BUY else BOSType.BEARISH,
                "instrument_type": inst_type,
                "spike_high": round(spike_high, 2), "spike_low": round(spike_low, 2),
                "candles_since_spike": candles_since_spike,
                "stoch_k": round(k_now, 2),
            },
            confidence_score=confidence,
        )
        logger.info(
            "Boom/Crash: %s %s @ %.2f | Spike [%.2f-%.2f] %d bars ago | SL=%.2f TP=%.2f",
            direction.value, instrument, entry_price, spike_low, spike_high,
            candles_since_spike, stop_loss, take_profit,
        )
        return [signal]
