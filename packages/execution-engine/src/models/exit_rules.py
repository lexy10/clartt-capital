"""Exit rule configuration models for position management."""

from pydantic import BaseModel, Field


class TrailingStopConfig(BaseModel):
    enabled: bool = False
    activation_pips: float = Field(default=20.0, gt=0.0)
    trail_distance_pips: float = Field(default=10.0, gt=0.0)


class BreakEvenConfig(BaseModel):
    enabled: bool = False
    activation_pips: float = Field(default=15.0, gt=0.0)
    buffer_pips: float = Field(default=2.0, ge=0.0)


class TimeExitConfig(BaseModel):
    enabled: bool = False
    max_duration_minutes: int = Field(default=240, gt=0)


class PartialCloseConfig(BaseModel):
    enabled: bool = False
    trigger_pips: float = Field(default=30.0, gt=0.0)
    close_percent: float = Field(default=50.0, gt=0.0, le=100.0)


class AtrTrailingStopConfig(BaseModel):
    """ATR-based trailing stop — uses ATR multiples instead of fixed pips."""
    enabled: bool = False
    activation_atr_mult: float = Field(default=1.5, gt=0.0)
    trail_atr_mult: float = Field(default=1.0, gt=0.0)


class StructuralTrailingStopConfig(BaseModel):
    """Structural trailing — ratchets SL to each new aligned 1m swing."""
    enabled: bool = False
    fractal_n: int = Field(default=2, ge=1, le=5)
    buffer_atr: float = Field(default=0.5, ge=0.0, le=5.0)
    min_swing_age_bars: int = Field(default=3, ge=1, le=30)
    activation_atr_mult: float = Field(default=0.0, ge=0.0, le=10.0)
    atr_period: int = Field(default=14, gt=0, le=50)
    only_during_aligned_phase: bool = Field(default=False)
    trail_resample_bars: int = Field(default=1, ge=1, le=60)


class ExitRules(BaseModel):
    """Configurable exit rules parsed from signal's exit_rules dict."""
    trailing_stop: TrailingStopConfig = Field(default_factory=TrailingStopConfig)
    break_even: BreakEvenConfig = Field(default_factory=BreakEvenConfig)
    time_exit: TimeExitConfig = Field(default_factory=TimeExitConfig)
    partial_close: PartialCloseConfig = Field(default_factory=PartialCloseConfig)
    atr_trailing_stop: AtrTrailingStopConfig = Field(default_factory=AtrTrailingStopConfig)
    structural_trailing_stop: StructuralTrailingStopConfig = Field(default_factory=StructuralTrailingStopConfig)

    @classmethod
    def from_dict(cls, data: dict | None) -> "ExitRules":
        """Parse exit rules from a signal's exit_rules dict, with safe defaults."""
        if not data:
            return cls()
        return cls.model_validate(data)

    def has_any_enabled(self) -> bool:
        """Return True if at least one exit rule is enabled."""
        return (
            self.trailing_stop.enabled
            or self.break_even.enabled
            or self.time_exit.enabled
            or self.partial_close.enabled
            or self.atr_trailing_stop.enabled
            or self.structural_trailing_stop.enabled
        )
