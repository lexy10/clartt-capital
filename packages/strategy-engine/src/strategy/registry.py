"""Strategy algorithm registry for plugin discovery and dispatch.

Algorithms are registered at startup. The StrategyRunner looks up the
correct algorithm by name for each strategy config.
"""

import logging

from src.strategy.base import StrategyAlgorithm

logger = logging.getLogger(__name__)


class StrategyRegistry:
    """Registry for strategy algorithm plugins.

    Algorithms are registered at startup. The StrategyRunner
    looks up the correct algorithm by name for each strategy config.
    """

    def __init__(self) -> None:
        self._algorithms: dict[str, StrategyAlgorithm] = {}

    def register(self, algorithm: StrategyAlgorithm) -> None:
        """Register an algorithm instance. Uses algorithm.name() as the key.

        Raises ValueError if an algorithm with the same name is already registered.
        """
        key = algorithm.name()
        if key in self._algorithms:
            raise ValueError(f"Algorithm '{key}' is already registered")
        self._algorithms[key] = algorithm
        logger.info("Registered strategy algorithm: %s", key)

    def get(self, name: str) -> StrategyAlgorithm:
        """Look up an algorithm by name.

        Raises KeyError with available algorithm names if not found.
        """
        if name not in self._algorithms:
            raise KeyError(
                f"Unknown algorithm '{name}'. "
                f"Available: {list(self._algorithms.keys())}"
            )
        return self._algorithms[name]

    def list_algorithms(self) -> list[dict]:
        """Return metadata for all registered algorithms.

        Each entry contains name, description, default_params, and param_schema.
        Used by the backend /algorithms endpoint.
        """
        return [
            {
                "name": alg.name(),
                "description": alg.description(),
                "default_params": alg.default_params(),
                "param_schema": alg.param_schema(),
            }
            for alg in self._algorithms.values()
        ]

    def has(self, name: str) -> bool:
        """Check if an algorithm is registered."""
        return name in self._algorithms
