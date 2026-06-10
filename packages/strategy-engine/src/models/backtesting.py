"""Pydantic models for the backtesting system.

Includes models for backtest parameters, trade results, performance statistics,
optimization results, walk-forward analysis, and Monte Carlo simulation.
"""

from pydantic import BaseModel, ConfigDict, Field


class TimeWindow(BaseModel):
    """Defines an in-sample/out-of-sample time window for walk-forward testing."""
    start: str  # ISO 8601
    end: str  # ISO 8601


class BacktestParams(BaseModel):
    """Parameters controlling a backtest run.

    Extra keys (e.g. algorithm_params passed by mistake) are silently ignored
    so the consumer never crashes on a malformed params dict.
    """
    model_config = ConfigDict(extra="ignore")

    initial_capital: float = Field(gt=0, default=10000.0)
    commission_per_trade: float = Field(ge=0, default=0.0)
    slippage: float = Field(ge=0, default=0.0)
    spread: float = Field(ge=0, default=0.0)
    max_lot_size: float = Field(gt=0, default=10.0, description="Maximum lot size cap")


class InstrumentSpecs(BaseModel):
    """Contract specifications for an instrument, used for position sizing."""
    contract_size: float = 1.0
    pip_size: float = 0.01
    pip_value: float = 1.0  # USD per pip per 1 lot
    min_lot: float = 0.01
    lot_step: float = 0.01
    leverage: int = 100


class TradeResult(BaseModel):
    """Result of a single simulated trade during backtesting."""
    signal_id: str
    direction: str  # "BUY" or "SELL"
    entry_price: float
    exit_price: float
    stop_loss: float | None = None
    take_profit: float | None = None
    initial_stop_loss: float | None = None  # Original SL before trailing modifies it
    position_size: float
    profit_loss: float
    reward_risk: float | None = None
    entry_time: str  # ISO 8601
    exit_time: str  # ISO 8601
    balance_before: float | None = None  # Account equity before this trade
    balance_after: float | None = None   # Account equity after this trade


class PerformanceStats(BaseModel):
    """Computed performance statistics for a backtest run."""
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate: float = 0.0
    average_rr: float = 0.0
    max_drawdown: float = 0.0
    sharpe_ratio: float = 0.0
    profit_factor: float = 0.0
    expectancy: float = 0.0
    gross_profit: float = 0.0
    gross_loss: float = 0.0
    net_profit: float = 0.0


class BacktestResult(BaseModel):
    """Complete result of a backtest run."""
    strategy_id: str
    trades: list[TradeResult] = Field(default_factory=list)
    stats: PerformanceStats = Field(default_factory=PerformanceStats)
    equity_curve: list[float] = Field(default_factory=list)
    initial_capital: float = 10000.0


class OptimizationResult(BaseModel):
    """Result of parameter optimization over a strategy."""
    best_params: dict = Field(default_factory=dict)
    best_score: float = 0.0
    all_results: list[dict] = Field(default_factory=list)
    metric: str = "net_profit"


class WalkForwardWindow(BaseModel):
    """Result for a single walk-forward window."""
    in_sample: TimeWindow
    out_of_sample: TimeWindow
    optimized_params: dict = Field(default_factory=dict)
    in_sample_stats: PerformanceStats = Field(default_factory=PerformanceStats)
    out_of_sample_stats: PerformanceStats = Field(default_factory=PerformanceStats)


class WalkForwardResult(BaseModel):
    """Result of walk-forward analysis across multiple windows."""
    windows: list[WalkForwardWindow] = Field(default_factory=list)
    combined_stats: PerformanceStats = Field(default_factory=PerformanceStats)


class MonteCarloStats(BaseModel):
    """Distribution statistics from Monte Carlo simulation."""
    mean_net_profit: float = 0.0
    std_net_profit: float = 0.0
    median_net_profit: float = 0.0
    percentile_5: float = 0.0
    percentile_25: float = 0.0
    percentile_75: float = 0.0
    percentile_95: float = 0.0
    mean_max_drawdown: float = 0.0
    probability_of_profit: float = 0.0


class MonteCarloResult(BaseModel):
    """Result of Monte Carlo simulation."""
    iterations: int = 0
    samples: list[float] = Field(default_factory=list)
    drawdown_samples: list[float] = Field(default_factory=list)
    stats: MonteCarloStats = Field(default_factory=MonteCarloStats)
