"""Generic signal generation helpers reusable by any algorithm.

Provides guard filters (spread, volatility, news, slippage, session),
position sizing, and signal construction utilities. Algorithm-specific
logic (entry triggers, SL/TP calculation) stays in each algorithm file.
"""

import statistics
import uuid
from datetime import datetime, time, timezone
from typing import Optional

from src.models import (
    Candle,
    Signal,
    SignalDirection,
    SignalMetadata,
    SignalMode,
    StrategyConfig,
    SessionWindow,
    Timeframe,
)


class NewsWindow:
    """Time window around a news event during which signals are blocked."""

    def __init__(self, start: datetime, end: datetime) -> None:
        self.start = start
        self.end = end


def check_spread_filter(spread: float, threshold: float) -> bool:
    """Return True if spread is within threshold (signal allowed)."""
    return spread <= threshold


def check_volatility_guard(candles: list[Candle], multiplier: float) -> bool:
    """Check if recent volatility is within multiplier of historical norm.

    Uses stdev of recent closes (last 25%) vs full history.
    Returns True if recent_vol <= multiplier * historical_vol.
    """
    if len(candles) < 4:
        return True

    closes = [c.close for c in candles]
    historical_vol = statistics.stdev(closes)
    if historical_vol == 0:
        return True

    recent_count = max(2, len(closes) // 4)
    recent_closes = closes[-recent_count:]
    if len(recent_closes) < 2:
        return True

    recent_vol = statistics.stdev(recent_closes)
    return recent_vol <= multiplier * historical_vol


def check_news_filter(timestamp: datetime, news_windows: list[NewsWindow]) -> bool:
    """Return True if timestamp is NOT within any news window (signal allowed)."""
    for window in news_windows:
        if window.start <= timestamp <= window.end:
            return False
    return True


def check_slippage_tolerance(estimated_slippage: float, max_slippage: float) -> bool:
    """Return True if estimated slippage is within tolerance."""
    return estimated_slippage <= max_slippage


def check_session_filter(timestamp: datetime, sessions: list[SessionWindow]) -> bool:
    """Return True if timestamp falls within any configured session window."""
    if not sessions:
        return True  # No sessions configured = allow all

    t = timestamp.time()
    for session in sessions:
        start = time(session.start_hour, session.start_minute)
        end = time(session.end_hour, session.end_minute)

        if start <= end:
            if start <= t <= end:
                return True
        else:
            # Overnight window (e.g., 22:00 - 06:00)
            if t >= start or t <= end:
                return True

    return False


def compute_volatility_ratio(candles: list[Candle]) -> float:
    """Compute recent/historical volatility ratio from candle closes."""
    closes = [c.close for c in candles]
    if len(closes) < 4:
        return 0.0
    historical_vol = statistics.stdev(closes)
    if historical_vol == 0:
        return 0.0
    recent_count = max(2, len(closes) // 4)
    recent_vol = statistics.stdev(closes[-recent_count:])
    return recent_vol / historical_vol


def compute_position_size(
    risk_pct: float, entry_price: float, stop_loss: float, min_size: float = 0.01
) -> float:
    """Compute position size from risk percentage and SL distance."""
    risk_distance = abs(entry_price - stop_loss)
    if risk_distance == 0:
        return min_size
    size = round(risk_pct / (risk_distance * 100), 2)
    return max(min_size, size)


def resolve_session_name(timestamp: datetime, sessions: list[SessionWindow]) -> str:
    """Find which session window a timestamp falls in. Returns 'unknown' if none."""
    t = timestamp.time()
    for sw in sessions:
        start = time(sw.start_hour, sw.start_minute)
        end = time(sw.end_hour, sw.end_minute)
        if start <= end:
            if start <= t <= end:
                return sw.name
        else:
            if t >= start or t <= end:
                return sw.name
    return "unknown"


def run_guard_filters(
    candles: list[Candle],
    config: StrategyConfig,
    timestamp: datetime,
    spread: float = 0.0,
    estimated_slippage: float = 0.0,
    news_windows: Optional[list[NewsWindow]] = None,
) -> bool:
    """Run all standard guard filters. Returns True if all pass."""
    if news_windows is None:
        news_windows = []

    if not check_spread_filter(spread, config.risk_settings.max_spread):
        return False
    if not check_volatility_guard(candles, config.risk_settings.volatility_multiplier):
        return False
    if not check_news_filter(timestamp, news_windows):
        return False
    if not check_slippage_tolerance(estimated_slippage, config.risk_settings.max_slippage):
        return False
    if not check_session_filter(timestamp, config.session_windows):
        return False
    return True


def build_signal(
    instrument: str,
    direction: SignalDirection,
    entry_price: float,
    stop_loss: float,
    take_profit: float,
    config: StrategyConfig,
    candles: list[Candle],
    timestamp: datetime,
    order_block_id: Optional[str] = None,
    spread: float = 0.0,
    extra_metadata: Optional[dict] = None,
    confidence_score: Optional[float] = None,
) -> Signal:
    """Construct a Signal object with standard metadata.

    Algorithms call this after their own entry logic determines
    direction, entry, SL, and TP.

    If confidence_score is provided it is used directly; otherwise it is
    derived from the volatility ratio (1.0 - vol_ratio).
    """
    vol_ratio = compute_volatility_ratio(candles)
    session_name = resolve_session_name(timestamp, config.session_windows)
    position_size = compute_position_size(
        config.risk_settings.max_risk_per_trade_pct, entry_price, stop_loss
    )

    # Use caller-supplied confidence if provided, otherwise fall back to vol-ratio heuristic
    if confidence_score is not None:
        final_confidence = round(min(1.0, max(0.0, confidence_score)), 4)
    else:
        final_confidence = round(min(1.0, max(0.0, 1.0 - vol_ratio)), 4)

    metadata = SignalMetadata(
        bos_type=extra_metadata.get("bos_type", "unknown") if extra_metadata else "unknown",
        liquidity_swept=extra_metadata.get("liquidity_swept", False) if extra_metadata else False,
        session=session_name,
        spread_at_generation=spread,
        volatility_ratio=round(vol_ratio, 4),
    )

    return Signal(
        id=str(uuid.uuid4()),
        instrument=instrument,
        direction=direction,
        entry_price=round(entry_price, 2),
        stop_loss=round(stop_loss, 2),
        take_profit=round(take_profit, 2),
        position_size=position_size,
        confidence_score=final_confidence,
        timeframe=config.entry_timeframe,
        order_block_id=order_block_id,
        strategy_id=config.id,
        mode=SignalMode(config.mode),
        metadata=metadata,
        exit_rules=config.exit_rules.model_dump() if config.exit_rules else None,
        created_at=timestamp.isoformat(),
    )
