"""Abstract base class for shared confluence filter modules and FilterResult model."""

from abc import ABC, abstractmethod
from typing import Optional

from pydantic import BaseModel

from src.models import Candle, SignalDirection


class FilterResult(BaseModel):
    """Result of a confluence filter evaluation."""

    passed: bool
    confidence_adjustment: float = 0.0  # positive = bonus, negative = penalty
    reason: Optional[str] = None


class ConfluenceFilter(ABC):
    """Abstract interface for shared confluence filter modules."""

    @staticmethod
    @abstractmethod
    def name() -> str:
        """Unique identifier for this filter (e.g., 'rsi_divergence')."""

    @staticmethod
    @abstractmethod
    def default_params() -> dict:
        """Default filter-specific parameters."""

    @staticmethod
    @abstractmethod
    def param_schema() -> dict:
        """JSON Schema for filter-specific parameters."""

    @abstractmethod
    def evaluate(
        self,
        candles: list[Candle],
        direction: SignalDirection,
        params: dict,
    ) -> FilterResult:
        """Evaluate the filter against candle data and signal direction.

        Args:
            candles: Candle data (typically structure-timeframe).
            direction: The proposed signal direction (BUY or SELL).
            params: Filter-specific params from algorithm_params.

        Returns:
            FilterResult with passed flag, confidence adjustment, and reason.
        """
