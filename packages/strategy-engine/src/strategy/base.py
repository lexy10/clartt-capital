"""Abstract base class for strategy algorithm plugins.

Every trading algorithm must subclass StrategyAlgorithm and implement
the five abstract methods: name, description, default_params, param_schema,
and analyze.
"""

from abc import ABC, abstractmethod

from src.models import Candle, Signal, StrategyConfig


class StrategyAlgorithm(ABC):
    """Base class for all strategy algorithm plugins."""

    @staticmethod
    @abstractmethod
    def name() -> str:
        """Unique identifier for this algorithm (e.g., 'ict_order_block').

        Used as the registry key and stored in the database.
        """

    @staticmethod
    @abstractmethod
    def description() -> str:
        """Human-readable description of the algorithm."""

    @staticmethod
    @abstractmethod
    def default_params() -> dict:
        """Default algorithm-specific parameters.

        Returned as a plain dict that can be serialized to JSON.
        Used by the dashboard to populate the config form for new strategies.
        """

    @staticmethod
    @abstractmethod
    def param_schema() -> dict:
        """JSON Schema describing the algorithm-specific parameters.

        Used by the dashboard to render dynamic config forms
        and by the backend for validation.
        """

    @abstractmethod
    def analyze(
        self,
        entry_candles: list[Candle],
        structure_candles: list[Candle],
        trend_candles: list[Candle],
        config: StrategyConfig,
    ) -> list[Signal]:
        """Run the full analysis cycle and return generated signals.

        Receives three timeframe candle sets:
        - entry_candles: entry-timeframe candles for retest confirmation
        - structure_candles: structure-timeframe candles for BOS/OB/FVG detection
        - trend_candles: trend-timeframe candles for overall bias determination
        and the strategy config (which includes algorithm_params).
        Returns a list of Signal objects (may be empty).
        """
