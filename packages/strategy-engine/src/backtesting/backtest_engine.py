"""BacktestEngine for running strategies against historical data.

Provides backtesting, parameter optimization, walk-forward analysis,
and Monte Carlo simulation capabilities.
"""

from __future__ import annotations

import bisect
import itertools
import logging
import math
import random
import statistics
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from src.models import (
    Candle,
    StrategyConfig,
    SignalDirection,
    BOSDirection,
    StructureType,
)


def _resample_for_trail(candles: list[Candle], bars_per_unit: int) -> list[Candle]:
    """Aggregate every N consecutive entry-TF candles into one bar.

    Used by structural trailing to look at a higher-TF swing structure
    (e.g. M5 from 1m) so the trail doesn't ratchet on 1m noise.

    Returns the input unchanged if bars_per_unit <= 1.
    """
    if bars_per_unit <= 1 or len(candles) < bars_per_unit:
        return candles
    out: list[Candle] = []
    inst = getattr(candles[0], "instrument", "")
    # The resampled timeframe must satisfy the Timeframe enum on Candle.
    synth_tf_map = {3: "5m", 5: "5m", 15: "15m", 30: "30m", 60: "1h"}
    synth_tf = synth_tf_map.get(bars_per_unit, "5m")
    remainder = len(candles) % bars_per_unit
    start = remainder if remainder > 0 else 0
    for i in range(start, len(candles), bars_per_unit):
        bucket = candles[i: i + bars_per_unit]
        if len(bucket) < bars_per_unit:
            break
        out.append(Candle(
            instrument=inst,
            timeframe=synth_tf,
            timestamp=bucket[0].timestamp,
            open=bucket[0].open,
            high=max(c.high for c in bucket),
            low=min(c.low for c in bucket),
            close=bucket[-1].close,
            volume=sum(getattr(c, "volume", 0) or 0 for c in bucket),
        ))
    return out


def _latest_aligned_fractal(
    candles: list[Candle],
    direction: str,
    n: int = 2,
    min_age_bars: int = 3,
) -> float | None:
    """Return the price of the most recent confirmed fractal aligned with
    the trade direction.

    Bullish trade: looks for the latest swing LOW (higher low candidate).
    Bearish trade: looks for the latest swing HIGH (lower high candidate).
    A fractal is "confirmed" when at least min_age_bars have passed since
    its pivot — otherwise it could still be invalidated by a later bar.
    """
    n_total = len(candles)
    last_confirmable = n_total - min_age_bars
    if last_confirmable <= n:
        return None

    if direction == "BUY":
        # Walk backward from the most recent confirmable bar — find swing low
        for i in range(last_confirmable - 1, n - 1, -1):
            pivot_low = candles[i].low
            is_swing = True
            for k in range(1, n + 1):
                if candles[i - k].low <= pivot_low or candles[i + k].low <= pivot_low:
                    is_swing = False
                    break
            if is_swing:
                return pivot_low
        return None
    else:
        for i in range(last_confirmable - 1, n - 1, -1):
            pivot_high = candles[i].high
            is_swing = True
            for k in range(1, n + 1):
                if candles[i - k].high >= pivot_high or candles[i + k].high >= pivot_high:
                    is_swing = False
                    break
            if is_swing:
                return pivot_high
        return None
from src.models.backtesting import (
    BacktestParams,
    BacktestResult,
    InstrumentSpecs,
    MonteCarloResult,
    MonteCarloStats,
    OptimizationResult,
    PerformanceStats,
    TimeWindow,
    TradeResult,
    WalkForwardResult,
    WalkForwardWindow,
)
from src.strategy.algorithms.ict_order_block import OrderBlockDetector, ICTSignalGenerator

if TYPE_CHECKING:
    from src.strategy.registry import StrategyRegistry

logger = logging.getLogger(__name__)


class BacktestEngine:
    """Runs strategies against historical candle data and computes performance statistics."""

    def __init__(self, registry: StrategyRegistry | None = None) -> None:
        self._registry = registry
        self.detector = OrderBlockDetector()
        self.generator = ICTSignalGenerator()

    def run(
        self,
        strategy: StrategyConfig,
        data: list[Candle],
        params: BacktestParams,
        instrument_specs: InstrumentSpecs | None = None,
        htf_data: list[Candle] | None = None,
        trend_data: list[Candle] | None = None,
        tick_data: list[Candle] | None = None,
    ) -> BacktestResult:
        """Process historical candles through the strategy pipeline and collect trade results.

        Walks through candle data using a sliding window, detects order blocks,
        generates signals, and simulates trades with proper position sizing.

        If htf_data is provided, it is used for structure-timeframe detection.
        If trend_data is provided, it is used for trend-timeframe bias.
        If tick_data is provided (e.g. 1m candles), it is used for granular exit
        checking — iterating through sub-candles within each entry-TF bar for
        more realistic SL/TP/trailing stop simulation.
        Otherwise, entry data is used as fallback (backward-compatible).
        """
        specs = instrument_specs or InstrumentSpecs()
        trades: list[TradeResult] = []
        equity = params.initial_capital
        equity_curve: list[float] = [equity]

        if len(data) < 5:
            return BacktestResult(
                strategy_id=strategy.id,
                trades=trades,
                stats=self.compute_stats(trades, params.initial_capital),
                equity_curve=equity_curve,
                initial_capital=params.initial_capital,
            )

        higher_tf_candles = htf_data if htf_data else data
        trend_tf_candles = trend_data if trend_data else higher_tf_candles

        # Pre-extract sorted HTF timestamps for O(log n) bisect lookups
        htf_timestamps = [c.timestamp for c in higher_tf_candles]
        trend_timestamps = [c.timestamp for c in trend_tf_candles]

        # Pre-index tick data (1m candles) by timestamp range for granular exit checking
        tick_timestamps = [c.timestamp for c in tick_data] if tick_data else []
        use_tick_exits = tick_data is not None and len(tick_data) > 0

        # Use structure_lookback from strategy's algorithm_params for the sliding window
        lookback = strategy.algorithm_params.get("structure_lookback", 20)
        window_size = min(max(int(lookback), 5), len(data))
        htf_window_size = min(max(int(lookback), 5), len(higher_tf_candles))
        open_trade: dict | None = None
        htf_pointer = 0  # Track position in HTF candles

        # Parse exit rules from strategy config
        exit_rules = strategy.exit_rules

        # Daily loss limit tracking (C5)
        current_day: str | None = None
        day_start_equity = equity
        daily_pnl = 0.0
        max_daily_loss_pct = strategy.risk_settings.max_daily_loss_pct

        # Trailing drawdown tracking (prop firm rule)
        equity_hwm = params.initial_capital  # High-water mark
        max_trailing_dd_pct = strategy.risk_settings.max_trailing_drawdown_pct
        trailing_dd_breached = False

        # Account-blown floor — when equity drops to or below this, stop trading
        # ENTIRELY. Below the floor, position sizing would clamp to min_lot
        # regardless of intended risk %, producing "phantom trades" that don't
        # reflect reality. We hard-stop and let the result reflect what actually
        # happened: the account was killed and no more trades were taken.
        # Floor: max($1 absolute, 2% of starting capital).
        account_blown_floor = max(1.0, params.initial_capital * 0.02)
        account_blown = False
        account_blown_at_ts: str | None = None

        # Zone invalidation tracking (C2) and signal cooldown tracking (C4)
        invalidated_zones: set[str] = set()
        zone_cooldowns: dict[str, str] = {}

        for i in range(window_size, len(data)):
            window = data[i - window_size : i]
            current_candle = data[i]

            # Daily loss limit: reset on new day (C5)
            candle_day = current_candle.timestamp[:10]
            if candle_day != current_day:
                current_day = candle_day
                day_start_equity = equity
                daily_pnl = 0.0

            # Trailing drawdown check — stop all trading if breached
            if not trailing_dd_breached:
                if equity > equity_hwm:
                    equity_hwm = equity
                if equity_hwm > 0:
                    current_dd_pct = (equity_hwm - equity) / equity_hwm * 100
                    if current_dd_pct >= max_trailing_dd_pct:
                        trailing_dd_breached = True
                        # Don't open new trades, but let open trade close naturally

            # Account-blown — hard stop, no more trades ever.
            # Triggered when equity drops to/below the floor. The open trade (if any)
            # will close naturally on the next exit check; from then on no new trades.
            if not account_blown and equity <= account_blown_floor:
                account_blown = True
                account_blown_at_ts = current_candle.timestamp
                logger.warning(
                    "Backtest %s: ACCOUNT BLOWN at %s — equity=$%.2f, floor=$%.2f. "
                    "No new trades will be opened. %d trades completed before blown.",
                    strategy.id, account_blown_at_ts, equity, account_blown_floor, len(trades),
                )

            if (trailing_dd_breached or account_blown) and open_trade is None:
                continue  # Skip signal generation when drawdown breached or account blown

            # Check if we have an open trade to close
            if open_trade is not None:
                closed = None

                # Granular exit: iterate through 1m candles within this bar
                if use_tick_exits:
                    prev_ts = data[i - 1].timestamp if i > 0 else ""
                    cur_ts = current_candle.timestamp
                    # Find tick candles between previous bar and current bar
                    tick_start = bisect.bisect_right(tick_timestamps, prev_ts)
                    tick_end = bisect.bisect_right(tick_timestamps, cur_ts)
                    for ti in range(tick_start, tick_end):
                        # Structural trailing needs recent entry-TF candles to
                        # detect fractals. Pass the tick window since trade entry.
                        entry_ts = open_trade.get("entry_time", "")
                        struct_start_idx = bisect.bisect_left(tick_timestamps, entry_ts)
                        recent = tick_data[struct_start_idx: ti + 1]
                        closed = self._check_exit(
                            open_trade, tick_data[ti], specs,
                            exit_rules=exit_rules, spread=params.spread,
                            recent_candles=recent,
                        )
                        if closed:
                            break
                else:
                    # Non-tick mode: use the entry-TF window since trade open
                    entry_ts = open_trade.get("entry_time", "")
                    bar_timestamps = [c.timestamp for c in data[: i + 1]]
                    struct_start_idx = bisect.bisect_left(bar_timestamps, entry_ts)
                    recent = data[struct_start_idx: i + 1]
                    closed = self._check_exit(
                        open_trade, current_candle, specs,
                        exit_rules=exit_rules, spread=params.spread,
                        recent_candles=recent,
                    )

                if closed:
                    balance_before = equity
                    pnl = closed["profit_loss"] - params.commission_per_trade
                    equity += pnl
                    # Update high-water mark after trade close
                    if equity > equity_hwm:
                        equity_hwm = equity
                    # Track daily PnL (C5)
                    daily_pnl += pnl
                    rr = self._compute_rr(
                        closed["direction"], closed["entry_price"],
                        closed["exit_price"], closed.get("stop_loss"),
                    )
                    trades.append(TradeResult(
                        signal_id=closed["signal_id"],
                        direction=closed["direction"],
                        entry_price=closed["entry_price"],
                        exit_price=closed["exit_price"],
                        stop_loss=closed.get("stop_loss"),
                        take_profit=closed.get("take_profit"),
                        initial_stop_loss=closed.get("initial_stop_loss"),
                        position_size=closed["position_size"],
                        profit_loss=pnl,
                        reward_risk=rr,
                        entry_time=closed["entry_time"],
                        exit_time=current_candle.timestamp,
                        balance_before=round(balance_before, 2),
                        balance_after=round(equity, 2),
                    ))
                    equity_curve.append(equity)
                    # Zone invalidation: add zone to invalidated set on SL hit (C2)
                    if rr is not None and rr <= -1.0:
                        ob_id = closed.get("order_block_id")
                        if ob_id:
                            invalidated_zones.add(ob_id)
                    open_trade = None
                continue

            # Build HTF window using bisect for O(log n) lookup instead of O(n) scan
            current_ts = current_candle.timestamp
            htf_pointer = bisect.bisect_right(htf_timestamps, current_ts)
            htf_start = max(0, htf_pointer - htf_window_size)
            htf_window = higher_tf_candles[htf_start:htf_pointer]

            # Build trend window using bisect
            trend_pointer = bisect.bisect_right(trend_timestamps, current_ts)
            trend_lookback = strategy.algorithm_params.get("trend_lookback", 50)
            trend_win_size = min(max(trend_lookback, 5), len(trend_tf_candles))
            trend_start = max(0, trend_pointer - trend_win_size)
            trend_window = trend_tf_candles[trend_start:trend_pointer]

            # Detect structure and generate signals using registry or direct calls
            signals = self._generate_signals(
                window, htf_window, trend_window, strategy, params,
                invalidated_zones=invalidated_zones,
                zone_cooldowns=zone_cooldowns,
            )

            # Daily loss limit check: skip new trades if daily loss exceeds threshold (C5)
            if daily_pnl < 0 and day_start_equity > 0 and abs(daily_pnl) >= day_start_equity * max_daily_loss_pct / 100.0:
                continue

            for signal in signals:
                if open_trade is None:
                    # Validate entry price is reachable within the current candle
                    raw_entry = signal.entry_price
                    direction = signal.direction.value

                    # Apply directional spread: BUY pays ask (entry + spread), SELL pays bid (entry - spread)
                    if direction == "BUY":
                        raw_entry += params.slippage + params.spread
                    else:
                        raw_entry -= params.slippage + params.spread

                    # Clamp entry to the candle's actual OHLC range
                    entry_price = max(current_candle.low, min(current_candle.high, raw_entry))

                    # Compute position size based on instrument specs and risk
                    risk_pct = strategy.risk_settings.max_risk_per_trade_pct
                    position_size = self._compute_position_size(
                        equity, risk_pct, entry_price, signal.stop_loss, specs,
                        max_lot_size=params.max_lot_size,
                    )
                    open_trade = {
                        "signal_id": signal.id,
                        "direction": direction,
                        "entry_price": entry_price,
                        "stop_loss": signal.stop_loss,
                        "take_profit": signal.take_profit,
                        "initial_stop_loss": signal.stop_loss,
                        "position_size": position_size,
                        "entry_time": current_candle.timestamp,
                        "remaining_size": position_size,
                        "partial_closed": False,
                        "trailing_active": False,
                        "atr_trailing_active": False,
                        "break_even_applied": False,
                        "order_block_id": getattr(signal, "order_block_id", None),
                        "entry_atr": self._compute_entry_atr(window, strategy.algorithm_params.get("atr_period", 14)),
                    }
                    # Record signal cooldown for this zone (C4)
                    ob_id = open_trade.get("order_block_id")
                    if ob_id:
                        zone_cooldowns[ob_id] = current_candle.timestamp
                    break

        # Close any remaining open trade at the last candle
        if open_trade is not None and len(data) > 0:
            last_candle = data[-1]
            exit_price = last_candle.close
            direction = open_trade["direction"]
            size = open_trade.get("remaining_size", open_trade["position_size"])
            balance_before = equity
            pnl = self._compute_pnl(
                direction, open_trade["entry_price"], exit_price,
                size, specs,
            )
            pnl -= params.commission_per_trade
            equity += pnl
            rr = self._compute_rr(
                direction, open_trade["entry_price"], exit_price,
                open_trade.get("stop_loss"),
            )
            trades.append(TradeResult(
                signal_id=open_trade["signal_id"],
                direction=direction,
                entry_price=open_trade["entry_price"],
                exit_price=exit_price,
                stop_loss=open_trade.get("stop_loss"),
                take_profit=open_trade.get("take_profit"),
                initial_stop_loss=open_trade.get("initial_stop_loss"),
                position_size=open_trade["position_size"],
                profit_loss=pnl,
                reward_risk=rr,
                entry_time=open_trade["entry_time"],
                exit_time=last_candle.timestamp,
                balance_before=round(balance_before, 2),
                balance_after=round(equity, 2),
            ))
            equity_curve.append(equity)

        return BacktestResult(
            strategy_id=strategy.id,
            trades=trades,
            stats=self.compute_stats(trades, params.initial_capital),
            equity_curve=equity_curve,
            initial_capital=params.initial_capital,
        )

    def _generate_signals(
        self,
        window: list[Candle],
        htf_window: list[Candle],
        trend_window: list[Candle],
        strategy: StrategyConfig,
        params: BacktestParams,
        invalidated_zones: set[str] | None = None,
        zone_cooldowns: dict[str, str] | None = None,
    ) -> list:
        """Generate signals using registry dispatch or direct detector/generator calls."""
        from src.models import Signal

        if self._registry is not None:
            try:
                algorithm = self._registry.get(strategy.algorithm)
                return algorithm.analyze(
                    window, htf_window, trend_window, strategy,
                    invalidated_zones=invalidated_zones,
                    zone_cooldowns=zone_cooldowns,
                )
            except KeyError:
                logger.error(
                    "Unknown algorithm '%s' for strategy '%s', skipping",
                    strategy.algorithm,
                    strategy.name,
                )
                return []

        # Fallback: direct detector/generator usage (uses HTF for structure)
        signals: list[Signal] = []
        structure = self.detector.detect_structure(htf_window)
        bos_list = self.detector.detect_bos(structure)

        # Average range for FVG detection
        ranges = [c.high - c.low for c in htf_window if (c.high - c.low) > 0]
        avg_range = sum(ranges) / len(ranges) if ranges else 1.0

        swing_lows = [p.price for p in structure if p.type in (StructureType.HIGHER_LOW, StructureType.LOWER_LOW)]
        swing_highs = [p.price for p in structure if p.type in (StructureType.HIGHER_HIGH, StructureType.LOWER_HIGH)]

        for bos in bos_list:
            order_blocks = self.detector.identify_order_blocks(htf_window, bos)
            for ob in order_blocks:
                # Zone invalidation check (C2)
                if invalidated_zones and ob.id in invalidated_zones:
                    continue

                # Signal cooldown per zone check (C4)
                if zone_cooldowns and ob.id in zone_cooldowns:
                    cooldown_candles = strategy.algorithm_params.get("cooldown_candles", 6)
                    # Derive entry TF minutes for cooldown window
                    tf_minutes_map = {
                        "1m": 1, "5m": 5, "15m": 15, "30m": 30,
                        "1h": 60, "4h": 240, "1d": 1440,
                    }
                    entry_tf_str = strategy.entry_timeframe.value if hasattr(strategy.entry_timeframe, "value") else str(strategy.entry_timeframe)
                    entry_tf_minutes = tf_minutes_map.get(entry_tf_str, 5)
                    cooldown_minutes = cooldown_candles * entry_tf_minutes
                    try:
                        last_signal_dt = datetime.fromisoformat(
                            zone_cooldowns[ob.id].replace("Z", "+00:00")
                        )
                        if window:
                            current_dt = datetime.fromisoformat(
                                window[-1].timestamp.replace("Z", "+00:00")
                            )
                            diff_minutes = (current_dt - last_signal_dt).total_seconds() / 60.0
                            if diff_minutes < cooldown_minutes:
                                continue
                    except (ValueError, AttributeError):
                        pass

                # Liquidity sweep check
                liquidity_confirmed = False
                if ob.direction == BOSDirection.BULLISH and swing_lows:
                    relevant = [l for l in swing_lows if l <= ob.zone_high]
                    if relevant:
                        liquidity_confirmed = self.detector.detect_liquidity_sweep(
                            window, max(relevant), ob.direction,
                        )
                elif ob.direction == BOSDirection.BEARISH and swing_highs:
                    relevant = [h for h in swing_highs if h >= ob.zone_low]
                    if relevant:
                        liquidity_confirmed = self.detector.detect_liquidity_sweep(
                            window, min(relevant), ob.direction,
                        )

                fvg_confirmed = self.detector.detect_fvg(htf_window, ob, avg_range)
                structural_tp = self.detector.find_structural_target(structure, ob)

                signal = self.generator.generate_signal(
                    ob=ob,
                    candles=window,
                    htf_candles=htf_window,
                    config=strategy,
                    spread=params.spread,
                    estimated_slippage=params.slippage,
                    structural_tp=structural_tp,
                    liquidity_confirmed=liquidity_confirmed,
                    fvg_confirmed=fvg_confirmed,
                )
                if signal is not None:
                    signals.append(signal)

        return signals

    def _check_exit(
        self, trade: dict, candle: Candle, specs: InstrumentSpecs,
        exit_rules=None, spread: float = 0.0,
        recent_candles: list[Candle] | None = None,
    ) -> dict | None:
        """Check if a candle triggers an exit condition for an open trade.

        Priority order: SL/TP → time exit → break-even (modifies SL) →
        trailing stop (modifies SL) → partial close (reduces size, doesn't close).
        """
        direction = trade["direction"]
        sl = trade["stop_loss"]
        tp = trade["take_profit"]
        entry = trade["entry_price"]
        size = trade.get("remaining_size", trade["position_size"])

        # --- 1. Hard SL/TP check (always first) ---
        # Apply directional spread on exit: BUY exits at bid (- spread), SELL exits at ask (+ spread)
        if direction == "BUY":
            effective_low = candle.low - spread
            effective_high = candle.high - spread
            sl_hit = sl is not None and effective_low <= sl
            tp_hit = tp is not None and effective_high >= tp
            if sl_hit and tp_hit:
                # Both SL and TP within this candle — use open to disambiguate.
                # If open is closer to SL, assume SL was hit first; otherwise TP.
                effective_open = candle.open - spread
                dist_to_sl = abs(effective_open - sl) if sl is not None else float("inf")
                dist_to_tp = abs(effective_open - tp) if tp is not None else float("inf")
                if dist_to_sl <= dist_to_tp:
                    pnl = self._compute_pnl("BUY", entry, sl, size, specs)
                    return {**trade, "exit_price": sl, "profit_loss": pnl, "position_size": trade["position_size"]}
                else:
                    pnl = self._compute_pnl("BUY", entry, tp, size, specs)
                    return {**trade, "exit_price": tp, "profit_loss": pnl, "position_size": trade["position_size"]}
            elif sl_hit:
                pnl = self._compute_pnl("BUY", entry, sl, size, specs)
                return {**trade, "exit_price": sl, "profit_loss": pnl, "position_size": trade["position_size"]}
            elif tp_hit:
                pnl = self._compute_pnl("BUY", entry, tp, size, specs)
                return {**trade, "exit_price": tp, "profit_loss": pnl, "position_size": trade["position_size"]}
        else:
            effective_low = candle.low + spread
            effective_high = candle.high + spread
            sl_hit = sl is not None and effective_high >= sl
            tp_hit = tp is not None and effective_low <= tp
            if sl_hit and tp_hit:
                effective_open = candle.open + spread
                dist_to_sl = abs(effective_open - sl) if sl is not None else float("inf")
                dist_to_tp = abs(effective_open - tp) if tp is not None else float("inf")
                if dist_to_sl <= dist_to_tp:
                    pnl = self._compute_pnl("SELL", entry, sl, size, specs)
                    return {**trade, "exit_price": sl, "profit_loss": pnl, "position_size": trade["position_size"]}
                else:
                    pnl = self._compute_pnl("SELL", entry, tp, size, specs)
                    return {**trade, "exit_price": tp, "profit_loss": pnl, "position_size": trade["position_size"]}
            elif sl_hit:
                pnl = self._compute_pnl("SELL", entry, sl, size, specs)
                return {**trade, "exit_price": sl, "profit_loss": pnl, "position_size": trade["position_size"]}
            elif tp_hit:
                pnl = self._compute_pnl("SELL", entry, tp, size, specs)
                return {**trade, "exit_price": tp, "profit_loss": pnl, "position_size": trade["position_size"]}

        if exit_rules is None:
            return None

        # Current best price for the trade direction (used for pip calculations)
        if direction == "BUY":
            best_price = candle.high - spread
        else:
            best_price = candle.low + spread

        profit_pips = self._price_to_pips(direction, entry, best_price, specs)

        # --- 2. Time exit ---
        if exit_rules.time_exit.enabled:
            entry_time = trade["entry_time"]
            max_minutes = exit_rules.time_exit.max_duration_minutes
            try:
                entry_dt = datetime.fromisoformat(entry_time.replace("Z", "+00:00"))
                candle_dt = datetime.fromisoformat(candle.timestamp.replace("Z", "+00:00"))
                elapsed = (candle_dt - entry_dt).total_seconds() / 60.0
                if elapsed >= max_minutes:
                    exit_price = candle.close
                    pnl = self._compute_pnl(direction, entry, exit_price, size, specs)
                    return {**trade, "exit_price": exit_price, "profit_loss": pnl, "position_size": trade["position_size"]}
            except (ValueError, TypeError):
                pass

        # --- 3. Break-even (modifies SL in-place, doesn't close) ---
        if exit_rules.break_even.enabled and not trade.get("break_even_applied", False):
            activation = exit_rules.break_even.activation_pips
            buffer = exit_rules.break_even.buffer_pips
            if profit_pips >= activation:
                if direction == "BUY":
                    new_sl = entry + buffer * specs.pip_size
                else:
                    new_sl = entry - buffer * specs.pip_size
                trade["stop_loss"] = new_sl
                trade["break_even_applied"] = True

        # --- 4. Trailing stop (modifies SL in-place, doesn't close) ---
        if exit_rules.trailing_stop.enabled:
            activation = exit_rules.trailing_stop.activation_pips
            trail_dist = exit_rules.trailing_stop.trail_distance_pips
            if profit_pips >= activation:
                trade["trailing_active"] = True

            if trade.get("trailing_active", False):
                trail_price_dist = trail_dist * specs.pip_size
                if direction == "BUY":
                    new_sl = best_price - trail_price_dist
                    if trade["stop_loss"] is None or new_sl > trade["stop_loss"]:
                        trade["stop_loss"] = new_sl
                else:
                    new_sl = best_price + trail_price_dist
                    if trade["stop_loss"] is None or new_sl < trade["stop_loss"]:
                        trade["stop_loss"] = new_sl

        # --- 5. Partial close (reduces remaining size, records partial PnL) ---
        if exit_rules.partial_close.enabled and not trade.get("partial_closed", False):
            trigger = exit_rules.partial_close.trigger_pips
            close_pct = exit_rules.partial_close.close_percent / 100.0
            if profit_pips >= trigger:
                close_size = size * close_pct
                trade["remaining_size"] = size - close_size
                trade["partial_closed"] = True

        # --- 6. ATR-based trailing stop (uses ATR instead of pips) ---
        if exit_rules.atr_trailing_stop.enabled:
            entry_atr = trade.get("entry_atr", 0.0)
            if entry_atr > 0:
                activation_dist = exit_rules.atr_trailing_stop.activation_atr_mult * entry_atr
                trail_dist = exit_rules.atr_trailing_stop.trail_atr_mult * entry_atr

                # Compute profit in price terms (not pips)
                if direction == "BUY":
                    price_profit = best_price - entry
                else:
                    price_profit = entry - best_price

                if price_profit >= activation_dist:
                    trade["atr_trailing_active"] = True

                if trade.get("atr_trailing_active", False):
                    if direction == "BUY":
                        new_sl = best_price - trail_dist
                        if trade["stop_loss"] is None or new_sl > trade["stop_loss"]:
                            trade["stop_loss"] = new_sl
                    else:
                        new_sl = best_price + trail_dist
                        if trade["stop_loss"] is None or new_sl < trade["stop_loss"]:
                            trade["stop_loss"] = new_sl

        # --- 7. Structural trailing stop — ratchet SL to each new aligned 1m swing ---
        if exit_rules.structural_trailing_stop.enabled and recent_candles is not None:
            cfg = exit_rules.structural_trailing_stop
            entry_atr = trade.get("entry_atr", 0.0)
            buffer = cfg.buffer_atr * entry_atr

            # Activation: don't ratchet until trade is in sufficient profit.
            # Prevents the trail from kicking in on the very first 1m HL after
            # entry, which on R_25 is usually noise.
            activation_dist = cfg.activation_atr_mult * entry_atr
            if direction == "BUY":
                price_profit = best_price - entry
            else:
                price_profit = entry - best_price
            activation_ok = activation_dist <= 0 or price_profit >= activation_dist

            # Resample to higher-TF bars if requested. Trailing on M5/M15
            # swings instead of 1m removes most of the per-tick fractal noise
            # that's been capping winners at +1R.
            resampled = _resample_for_trail(recent_candles, cfg.trail_resample_bars)
            min_required = cfg.fractal_n * 2 + cfg.min_swing_age_bars + 2

            if (
                buffer > 0
                and activation_ok
                and len(resampled) >= min_required
            ):
                latest_swing_price = _latest_aligned_fractal(
                    resampled, direction,
                    n=cfg.fractal_n, min_age_bars=cfg.min_swing_age_bars,
                )
                if latest_swing_price is not None:
                    if direction == "BUY":
                        new_sl = latest_swing_price - buffer
                        if trade["stop_loss"] is None or new_sl > trade["stop_loss"]:
                            trade["stop_loss"] = new_sl
                    else:
                        new_sl = latest_swing_price + buffer
                        if trade["stop_loss"] is None or new_sl < trade["stop_loss"]:
                            trade["stop_loss"] = new_sl

        return None

    @staticmethod
    def _compute_entry_atr(candles: list, period: int = 14) -> float:
        """Compute ATR from candle window at trade entry time for ATR-based trailing stop."""
        if len(candles) < period + 1:
            return 0.0
        tr_list = []
        for i in range(1, len(candles)):
            tr = max(
                candles[i].high - candles[i].low,
                abs(candles[i].high - candles[i - 1].close),
                abs(candles[i].low - candles[i - 1].close),
            )
            tr_list.append(tr)
        if len(tr_list) < period:
            return 0.0
        atr = sum(tr_list[-period:]) / period
        return atr

    @staticmethod
    def _price_to_pips(direction: str, entry: float, current: float, specs: InstrumentSpecs) -> float:
        """Convert price movement to pips based on direction and instrument specs."""
        if direction == "BUY":
            return (current - entry) / specs.pip_size
        else:
            return (entry - current) / specs.pip_size

    @staticmethod
    def _compute_pnl(
        direction: str, entry: float, exit_price: float,
        lot_size: float, specs: InstrumentSpecs,
    ) -> float:
        """Compute PnL in USD using instrument specs.

        PnL = (price_diff / pip_size) * pip_value * lot_size
        """
        if direction == "BUY":
            price_diff = exit_price - entry
        else:
            price_diff = entry - exit_price
        pips = price_diff / specs.pip_size
        return pips * specs.pip_value * lot_size

    @staticmethod
    def _compute_rr(direction: str, entry: float, exit_price: float, stop_loss: float | None) -> float | None:
        """Compute reward-to-risk ratio for a closed trade.

        RR = price_move / risk_distance. Returns -1 if SL hit, positive if profitable.
        """
        if stop_loss is None:
            return None
        risk_distance = abs(entry - stop_loss)
        if risk_distance == 0:
            return None
        if direction == "BUY":
            move = exit_price - entry
        else:
            move = entry - exit_price
        return round(move / risk_distance, 2)

    @staticmethod
    def _compute_position_size(
        equity: float, risk_pct: float, entry: float, stop_loss: float,
        specs: InstrumentSpecs,
        max_lot_size: float | None = None,
    ) -> float:
        """Compute lot size based on risk percentage and instrument specs.

        risk_amount = equity * risk_pct / 100
        sl_pips = abs(entry - stop_loss) / pip_size
        lot_size = risk_amount / (sl_pips * pip_value)
        Rounded down to lot_step, clamped to min_lot, capped at max_lot_size.
        """
        risk_amount = equity * risk_pct / 100.0
        sl_distance = abs(entry - stop_loss)
        sl_pips = sl_distance / specs.pip_size
        if sl_pips == 0:
            return specs.min_lot

        raw_lots = risk_amount / (sl_pips * specs.pip_value)
        # Round down to lot_step
        if specs.lot_step > 0:
            raw_lots = math.floor(raw_lots / specs.lot_step) * specs.lot_step
        # Clamp to min_lot
        result = max(specs.min_lot, round(raw_lots, 4))
        # Cap at max_lot_size (C6)
        if max_lot_size is not None:
            result = min(result, max_lot_size)
        return result

    @staticmethod
    def compute_stats(trades: list[TradeResult], initial_capital: float = 10000.0) -> PerformanceStats:
        """Compute performance statistics from a list of trade results.

        - win_rate = winning_trades / total_trades
        - max_drawdown = largest peak-to-trough equity decline
        - sharpe_ratio = mean_return / std_return * sqrt(252)
        - profit_factor = gross_profit / gross_loss
        - expectancy = average profit per trade
        """
        total = len(trades)
        if total == 0:
            return PerformanceStats()

        pnls = [t.profit_loss for t in trades]
        winning = [p for p in pnls if p > 0]
        losing = [p for p in pnls if p <= 0]

        gross_profit = sum(winning)
        gross_loss = abs(sum(losing))
        net_profit = sum(pnls)

        win_rate = len(winning) / total if total > 0 else 0.0

        # Profit factor
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else (
            float("inf") if gross_profit > 0 else 0.0
        )

        # Expectancy
        expectancy = net_profit / total

        # Max drawdown from cumulative equity curve (as ratio)
        max_drawdown = BacktestEngine._compute_max_drawdown(pnls, initial_capital)

        # Sharpe ratio (annualized, assuming daily returns)
        sharpe_ratio = BacktestEngine._compute_sharpe_ratio(pnls)

        # Average reward-to-risk ratio
        rr_values = [t.reward_risk for t in trades if t.reward_risk is not None]
        average_rr = sum(rr_values) / len(rr_values) if rr_values else 0.0

        return PerformanceStats(
            total_trades=total,
            winning_trades=len(winning),
            losing_trades=len(losing),
            win_rate=round(win_rate, 4),
            average_rr=round(average_rr, 2),
            max_drawdown=round(max_drawdown, 4),
            sharpe_ratio=round(sharpe_ratio, 4),
            profit_factor=round(profit_factor, 4) if not math.isinf(profit_factor) else float("inf"),
            expectancy=round(expectancy, 4),
            gross_profit=round(gross_profit, 4),
            gross_loss=round(gross_loss, 4),
            net_profit=round(net_profit, 4),
        )

    @staticmethod
    def _compute_max_drawdown(pnls: list[float], initial_capital: float = 10000.0) -> float:
        """Compute the largest peak-to-trough equity decline as a ratio (0.0 to 1.0)."""
        if not pnls:
            return 0.0

        equity = initial_capital
        peak = initial_capital
        max_dd = 0.0

        for pnl in pnls:
            equity += pnl
            if equity > peak:
                peak = equity
            if peak > 0:
                drawdown = (peak - equity) / peak
                if drawdown > max_dd:
                    max_dd = drawdown

        return max_dd

    @staticmethod
    def _compute_sharpe_ratio(pnls: list[float]) -> float:
        """Compute annualized Sharpe ratio: mean / std * sqrt(252)."""
        if len(pnls) < 2:
            return 0.0

        mean_ret = statistics.mean(pnls)
        std_ret = statistics.stdev(pnls)

        if std_ret == 0:
            return 0.0

        return (mean_ret / std_ret) * math.sqrt(252)

    def optimize_parameters(
        self,
        strategy: StrategyConfig,
        data: list[Candle],
        param_space: dict,
        instrument_specs: InstrumentSpecs | None = None,
        htf_data: list[Candle] | None = None,
        trend_data: list[Candle] | None = None,
    ) -> OptimizationResult:
        """Grid search over parameter space to find optimal strategy configuration.

        param_space is a dict mapping parameter names to lists of values, e.g.:
        {"max_risk_per_trade_pct": [1.0, 2.0], "volatility_multiplier": [1.5, 2.0]}

        Supported parameters are applied to the strategy's risk_settings.
        Returns the best configuration by net_profit.
        """
        param_names = list(param_space.keys())
        param_values = list(param_space.values())

        if not param_names:
            # No parameters to optimize — run once with current config
            result = self.run(strategy, data, BacktestParams(), instrument_specs, htf_data=htf_data, trend_data=trend_data)
            return OptimizationResult(
                best_params={},
                best_score=result.stats.net_profit,
                all_results=[{"params": {}, "score": result.stats.net_profit}],
                metric="net_profit",
            )

        all_results: list[dict] = []
        best_score = float("-inf")
        best_params: dict = {}

        for combo in itertools.product(*param_values):
            current_params = dict(zip(param_names, combo))

            # Apply parameters to a copy of the strategy config
            config_dict = strategy.model_dump()
            for key, value in current_params.items():
                if key in config_dict.get("risk_settings", {}):
                    config_dict["risk_settings"][key] = value
                elif key in config_dict:
                    config_dict[key] = value

            modified_strategy = StrategyConfig(**config_dict)
            result = self.run(modified_strategy, data, BacktestParams(), instrument_specs, htf_data=htf_data, trend_data=trend_data)
            score = result.stats.net_profit

            all_results.append({"params": current_params, "score": score})

            if score > best_score:
                best_score = score
                best_params = current_params

        return OptimizationResult(
            best_params=best_params,
            best_score=best_score,
            all_results=all_results,
            metric="net_profit",
        )

    def walk_forward(
        self,
        strategy: StrategyConfig,
        data: list[Candle],
        windows: list[TimeWindow],
        instrument_specs: InstrumentSpecs | None = None,
        htf_data: list[Candle] | None = None,
        trend_data: list[Candle] | None = None,
    ) -> WalkForwardResult:
        """Run independent in-sample/out-of-sample walk-forward analysis.

        Each TimeWindow pair defines an in-sample period (for optimization)
        and an out-of-sample period (for validation). Windows are processed
        independently — results from one window do not leak into another.
        """
        wf_windows: list[WalkForwardWindow] = []
        all_oos_trades: list[TradeResult] = []

        for i in range(0, len(windows) - 1, 2):
            in_sample_window = windows[i]
            if i + 1 >= len(windows):
                break
            oos_window = windows[i + 1]

            # Filter candles for in-sample and out-of-sample periods
            is_candles = self._filter_candles(data, in_sample_window)
            oos_candles = self._filter_candles(data, oos_window)

            # Filter HTF candles if available
            is_htf = self._filter_candles(htf_data, in_sample_window) if htf_data else None
            oos_htf = self._filter_candles(htf_data, oos_window) if htf_data else None

            # Filter trend candles if available
            is_trend = self._filter_candles(trend_data, in_sample_window) if trend_data else None
            oos_trend = self._filter_candles(trend_data, oos_window) if trend_data else None

            # Run in-sample backtest
            is_result = self.run(strategy, is_candles, BacktestParams(), instrument_specs, htf_data=is_htf, trend_data=is_trend)

            # Run out-of-sample backtest independently
            oos_result = self.run(strategy, oos_candles, BacktestParams(), instrument_specs, htf_data=oos_htf, trend_data=oos_trend)

            all_oos_trades.extend(oos_result.trades)

            wf_windows.append(WalkForwardWindow(
                in_sample=in_sample_window,
                out_of_sample=oos_window,
                optimized_params={},
                in_sample_stats=is_result.stats,
                out_of_sample_stats=oos_result.stats,
            ))

        combined_stats = self.compute_stats(all_oos_trades, BacktestParams().initial_capital)

        return WalkForwardResult(
            windows=wf_windows,
            combined_stats=combined_stats,
        )

    @staticmethod
    def _filter_candles(data: list[Candle], window: TimeWindow) -> list[Candle]:
        """Filter candles that fall within the given time window."""
        start = window.start
        end = window.end
        return [c for c in data if start <= c.timestamp <= end]

    def monte_carlo(
        self,
        results: BacktestResult,
        iterations: int,
    ) -> MonteCarloResult:
        """Resample/reorder trades for N iterations and compute distribution stats.

        Randomly shuffles the order of trades to simulate different possible
        sequences, computing net profit and max drawdown for each iteration.
        """
        if not results.trades or iterations <= 0:
            return MonteCarloResult(iterations=iterations)

        pnls = [t.profit_loss for t in results.trades]
        net_profits: list[float] = []
        drawdowns: list[float] = []

        for _ in range(iterations):
            shuffled = pnls.copy()
            random.shuffle(shuffled)

            net_profit = sum(shuffled)
            net_profits.append(net_profit)

            max_dd = self._compute_max_drawdown(shuffled, results.initial_capital)
            drawdowns.append(max_dd)

        # Compute distribution statistics
        sorted_profits = sorted(net_profits)
        n = len(sorted_profits)

        mean_profit = statistics.mean(net_profits)
        std_profit = statistics.stdev(net_profits) if n >= 2 else 0.0
        median_profit = statistics.median(net_profits)

        profitable = sum(1 for p in net_profits if p > 0)
        prob_profit = profitable / n if n > 0 else 0.0

        mean_dd = statistics.mean(drawdowns) if drawdowns else 0.0

        stats = MonteCarloStats(
            mean_net_profit=round(mean_profit, 4),
            std_net_profit=round(std_profit, 4),
            median_net_profit=round(median_profit, 4),
            percentile_5=round(sorted_profits[max(0, int(n * 0.05))], 4),
            percentile_25=round(sorted_profits[max(0, int(n * 0.25))], 4),
            percentile_75=round(sorted_profits[max(0, min(n - 1, int(n * 0.75)))], 4),
            percentile_95=round(sorted_profits[max(0, min(n - 1, int(n * 0.95)))], 4),
            mean_max_drawdown=round(mean_dd, 4),
            probability_of_profit=round(prob_profit, 4),
        )

        return MonteCarloResult(
            iterations=iterations,
            samples=net_profits,
            drawdown_samples=drawdowns,
            stats=stats,
        )
