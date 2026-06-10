from typing import Optional

from pydantic import BaseModel, Field

from .timeframe import Timeframe


class SessionWindow(BaseModel):
    """Defines a trading session time window (e.g., London, New York)."""
    name: str
    start_hour: int = Field(ge=0, le=23)
    start_minute: int = Field(ge=0, le=59)
    end_hour: int = Field(ge=0, le=23)
    end_minute: int = Field(ge=0, le=59)


class RiskSettings(BaseModel):
    """Risk parameters for signal generation and position sizing."""
    max_risk_per_trade_pct: float = Field(gt=0.0, le=100.0, description="Max risk per trade as % of equity")
    max_daily_loss_pct: float = Field(gt=0.0, le=100.0, description="Max daily loss as % of equity")
    max_trailing_drawdown_pct: float = Field(default=10.0, gt=0.0, le=100.0, description="Max drawdown from equity high-water mark as % — stops all trading when breached (prop firm rule)")
    max_spread: float = Field(gt=0.0, description="Max allowed spread for signal generation")
    max_slippage: float = Field(gt=0.0, description="Max allowed slippage tolerance")
    volatility_multiplier: float = Field(gt=0.0, description="Multiplier above historical norm to pause signals")
    min_reward_risk_ratio: float = Field(default=2.0, gt=0.0, le=10.0, description="Minimum R:R ratio — reject signals below this")


class TrailingStopConfig(BaseModel):
    """Trailing stop: after price moves activation_pips in your favor, SL follows at trail_distance_pips."""
    enabled: bool = False
    activation_pips: float = Field(default=20.0, gt=0.0, description="Pips of profit before trailing activates")
    trail_distance_pips: float = Field(default=10.0, gt=0.0, description="Distance in pips the SL trails behind price")


class BreakEvenConfig(BaseModel):
    """Break-even stop: after activation_pips profit, move SL to entry + buffer_pips."""
    enabled: bool = False
    activation_pips: float = Field(default=15.0, gt=0.0, description="Pips of profit before moving SL to break-even")
    buffer_pips: float = Field(default=2.0, ge=0.0, description="Buffer pips above/below entry for break-even SL")


class TimeExitConfig(BaseModel):
    """Time-based exit: close position after max_duration_minutes."""
    enabled: bool = False
    max_duration_minutes: int = Field(default=240, gt=0, description="Max minutes a position can stay open")


class PartialCloseConfig(BaseModel):
    """Partial close: at trigger_pips profit, close close_percent of the position."""
    enabled: bool = False
    trigger_pips: float = Field(default=30.0, gt=0.0, description="Pips of profit to trigger partial close")
    close_percent: float = Field(default=50.0, gt=0.0, le=100.0, description="Percentage of position to close")


class AtrTrailingStopConfig(BaseModel):
    """ATR-based trailing stop: activates after price moves activation_atr_mult × ATR in profit,
    then trails at trail_atr_mult × ATR behind the best price. Works correctly for all instruments
    regardless of pip scale."""
    enabled: bool = False
    activation_atr_mult: float = Field(default=1.5, gt=0.0, description="ATR multiples of profit before trailing activates")
    trail_atr_mult: float = Field(default=1.0, gt=0.0, description="ATR multiples the SL trails behind best price")


class StructuralTrailingStopConfig(BaseModel):
    """Structural trailing stop: ratchets SL up (long) / down (short) to each
    newly-confirmed swing in the trade's direction.

    On every entry-timeframe candle, we look for the latest confirmed fractal
    that's aligned with the trade direction (HL for longs, LH for shorts) and
    move SL there minus (buffer_atr × ATR). This follows market structure
    instead of volatility — keeps the SL behind real support, not behind a
    moving ATR band that gets hit by noise.

    Use it on strategies whose thesis is "follow structure until structure breaks."
    The position closes at the trailed SL the moment a swing breaks.
    """
    enabled: bool = False
    fractal_n: int = Field(default=2, ge=1, le=5, description="Fractal lookback bars on each side — same definition as entry algo")
    buffer_atr: float = Field(default=0.5, ge=0.0, le=5.0, description="Buffer below the swing (× entry-TF ATR)")
    min_swing_age_bars: int = Field(default=3, ge=1, le=30, description="Swing must be at least this many bars old (in resampled bars)")
    activation_atr_mult: float = Field(default=0.0, ge=0.0, le=10.0, description="Only start ratcheting after profit ≥ this × ATR (0 = ratchet from entry)")
    atr_period: int = Field(default=14, gt=0, le=50, description="ATR period for buffer sizing")
    only_during_aligned_phase: bool = Field(default=False, description="If true, stop ratcheting when HTF flips out of impulse/resumption")
    trail_resample_bars: int = Field(default=1, ge=1, le=60, description="Resample entry candles into N-bar groups for fractal detection (1=use raw, 5=M5 on 1m entry, 15=M15)")


class ExitRules(BaseModel):
    """Configurable exit rules applied per strategy to open positions."""
    trailing_stop: TrailingStopConfig = Field(default_factory=TrailingStopConfig)
    break_even: BreakEvenConfig = Field(default_factory=BreakEvenConfig)
    time_exit: TimeExitConfig = Field(default_factory=TimeExitConfig)
    partial_close: PartialCloseConfig = Field(default_factory=PartialCloseConfig)
    atr_trailing_stop: AtrTrailingStopConfig = Field(default_factory=AtrTrailingStopConfig)
    structural_trailing_stop: StructuralTrailingStopConfig = Field(default_factory=StructuralTrailingStopConfig)


class StrategyConfig(BaseModel):
    """Configuration for a trading strategy instance."""
    id: str
    name: str
    algorithm: str = "ict_order_block"
    algorithm_params: dict = Field(default_factory=dict, description="Algorithm-specific parameters")
    instruments: list[str] = Field(min_length=1, description="Instrument symbols this strategy trades (e.g. R_75, US30)")
    timeframes: list[Timeframe] = Field(min_length=1, description="Timeframes to analyze")
    trend_timeframe: Timeframe = Field(default=Timeframe.FOUR_HOURS, description="Highest timeframe for overall trend bias (e.g. 4H or 1D)")
    higher_timeframe: Timeframe = Field(description="Structure timeframe for BOS, OBs, FVGs (e.g. 1H)")
    entry_timeframe: Timeframe = Field(description="Entry timeframe for retest confirmation and timing (e.g. 5m/15m)")
    session_windows: list[SessionWindow] = Field(default_factory=list, description="Allowed trading sessions")
    risk_settings: RiskSettings
    mode: str = Field(default="live", pattern="^(backtest|forward_test|live)$")
    news_protection_minutes: int = Field(default=30, ge=0, description="Minutes around news to pause signals")
    min_confidence_score: float = Field(default=0.6, ge=0.0, le=1.0, description="Minimum confidence to generate signal")
    exit_rules: ExitRules = Field(default_factory=ExitRules, description="Configurable exit rules for position management")
    enabled: bool = True
