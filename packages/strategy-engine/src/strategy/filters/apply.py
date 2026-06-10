"""Shared helper for applying confluence filters to signal confidence scores."""

import logging
from typing import TYPE_CHECKING

from src.models import Candle, SignalDirection

if TYPE_CHECKING:
    from src.strategy.filters import FILTER_REGISTRY as _REG  # noqa: F401

logger = logging.getLogger(__name__)


def apply_confluence_filters(
    filter_names: list[str],
    candles: list[Candle],
    direction: SignalDirection,
    algorithm_params: dict,
) -> float:
    """Apply all named confluence filters and return total confidence adjustment.

    Args:
        filter_names: List of filter name strings to apply. If not a list,
            treated as empty with a warning.
        candles: Candle data for filter evaluation.
        direction: The proposed signal direction (BUY or SELL).
        algorithm_params: Algorithm parameters dict; filter-specific params
            are merged with each filter's default_params().

    Returns:
        Sum of all confidence_adjustment values from evaluated filters.
        Unrecognized filter names are logged as warnings and skipped.
        Filter exceptions are caught, logged, and treated as neutral (0.0).
    """
    if not isinstance(filter_names, list):
        logger.warning(
            "confluence_filters is not a list (got %s), treating as empty",
            type(filter_names).__name__,
        )
        return 0.0

    from src.strategy.filters import FILTER_REGISTRY

    total_adjustment = 0.0
    for name in filter_names:
        filter_cls = FILTER_REGISTRY.get(name)
        if filter_cls is None:
            logger.warning("Unknown confluence filter '%s', skipping", name)
            continue
        try:
            filter_instance = filter_cls()
            params = {**filter_instance.default_params(), **algorithm_params}
            result = filter_instance.evaluate(candles, direction, params)
            total_adjustment += result.confidence_adjustment
        except Exception:
            logger.exception(
                "Filter '%s' raised an exception, treating as neutral", name
            )
    return total_adjustment
