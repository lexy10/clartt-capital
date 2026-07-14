"""Tests for position rehydration after an execution-engine restart.

Focus on the safety guarantees of PositionMonitor.rehydrate_position:
 - original entry_time is preserved (time-exit fires at the real deadline)
 - partial_close_done defaults True (never a double partial close)
 - positions with no engine-managed exits are not tracked
 - idempotent re-registration
"""
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock

import pytest

from src.monitor.position_monitor import PositionMonitor
from src.models import Signal, SignalDirection, SignalMode, Timeframe
from src.models.signal import SignalMetadata, BOSType


def _monitor() -> PositionMonitor:
    return PositionMonitor(
        broker_client=MagicMock(),
        executor=MagicMock(),
        redis_client=MagicMock(),
        trade_persister=MagicMock(),
    )


def _signal(exit_rules: dict | None) -> Signal:
    return Signal(
        id="550e8400-e29b-41d4-a716-446655440000",
        instrument="R_25",
        direction=SignalDirection.BUY,
        entry_price=2000.0,
        stop_loss=1990.0,
        take_profit=2030.0,
        position_size=0.5,
        confidence_score=0.8,
        timeframe=Timeframe.FIFTEEN_MINUTES,
        order_block_id="ob-1",
        strategy_id="strat-1",
        mode=SignalMode.LIVE,
        metadata=SignalMetadata(
            bos_type=BOSType.BULLISH, liquidity_swept=True, session="ny",
            spread_at_generation=1.0, volatility_ratio=1.0,
        ),
        exit_rules=exit_rules,
        created_at="2026-01-01T00:00:00Z",
    )


def test_rehydrate_preserves_original_entry_time():
    mon = _monitor()
    opened = datetime.now(timezone.utc) - timedelta(hours=3)
    sig = _signal({"time_exit": {"enabled": True, "max_duration_minutes": 240}})

    mon.rehydrate_position(
        position_id=111, signal=sig, account=MagicMock(),
        entry_price=2001.0, entry_time=opened,
    )

    assert mon.tracked_count == 1
    pos = mon._positions[111]
    # The real entry time is kept — NOT reset to "now" — so a 240m time-exit
    # correctly fires ~1h from now, not 4h from now.
    assert pos.entry_time == opened
    assert pos.entry_price == 2001.0


def test_rehydrate_suppresses_partial_close():
    mon = _monitor()
    sig = _signal({
        "time_exit": {"enabled": True, "max_duration_minutes": 60},
        "partial_close": {"enabled": True, "trigger_pips": 10, "close_percent": 50},
    })
    mon.rehydrate_position(
        position_id=222, signal=sig, account=MagicMock(),
        entry_price=2000.0, entry_time=datetime.now(timezone.utc),
    )
    # partial_close_done starts True so a prior partial can never be repeated.
    assert mon._positions[222].partial_close_done is True
    # current_sl is the original protective level; trailing can only tighten it.
    assert mon._positions[222].current_sl == sig.stop_loss


def test_rehydrate_skips_positions_without_engine_exits():
    mon = _monitor()
    # All exits disabled → broker SL/TP covers it, nothing to monitor.
    sig = _signal({"time_exit": {"enabled": False}})
    mon.rehydrate_position(
        position_id=333, signal=sig, account=MagicMock(),
        entry_price=2000.0, entry_time=datetime.now(timezone.utc),
    )
    assert mon.tracked_count == 0


def test_rehydrate_is_idempotent():
    mon = _monitor()
    sig = _signal({"time_exit": {"enabled": True, "max_duration_minutes": 60}})
    for _ in range(3):
        mon.rehydrate_position(
            position_id=444, signal=sig, account=MagicMock(),
            entry_price=2000.0, entry_time=datetime.now(timezone.utc),
        )
    assert mon.tracked_count == 1
