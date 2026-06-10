"""Pydantic models for backtest Redis stream/pub-sub messages.

Defines the message schemas exchanged between the NestJS backend and the
strategy engine over Redis streams (backtest:requests, backtest:results)
and the backtest:status pub/sub channel.
"""

from typing import Optional

from pydantic import BaseModel, Field

from .backtesting import BacktestParams, PerformanceStats, TradeResult


class BacktestRequestMessage(BaseModel):
    """Message published to the backtest:requests Redis stream by the backend."""
    result_id: str
    strategy_id: str
    strategy_config: dict  # Raw dict, deserialized into StrategyConfig by consumer
    instrument: str = ""  # Instrument to backtest on (overrides strategy default)
    timeframe: str = ""   # Timeframe to use (overrides strategy entry_timeframe)
    params: BacktestParams
    start_date: str  # ISO 8601
    end_date: str    # ISO 8601


class BacktestResultMessage(BaseModel):
    """Message published to the backtest:results Redis stream by the strategy engine."""
    result_id: str
    strategy_id: str
    status: str  # "completed" or "failed"
    stats: Optional[PerformanceStats] = None
    equity_curve: list[float] = Field(default_factory=list)
    trade_results: list[TradeResult] = Field(default_factory=list)
    error: Optional[str] = None


class BacktestStatusMessage(BaseModel):
    """Message published to the backtest:status pub/sub channel."""
    result_id: str
    strategy_id: str
    status: str  # "running", "completed", "failed"
    error: Optional[str] = None
