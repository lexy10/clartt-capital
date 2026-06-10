"""Shared confluence filter framework — exports all filter classes and registry."""

from src.strategy.filters.base import ConfluenceFilter, FilterResult
from src.strategy.filters.mtf_alignment import MTFAlignmentFilter
from src.strategy.filters.regime_filter import RegimeFilter
from src.strategy.filters.rsi_divergence import RSIDivergenceFilter
from src.strategy.filters.session_filter import SessionFilter
from src.strategy.filters.volume_profile import VolumeProfileFilter

FILTER_REGISTRY: dict[str, type[ConfluenceFilter]] = {
    RSIDivergenceFilter.name(): RSIDivergenceFilter,
    VolumeProfileFilter.name(): VolumeProfileFilter,
    MTFAlignmentFilter.name(): MTFAlignmentFilter,
    RegimeFilter.name(): RegimeFilter,
    SessionFilter.name(): SessionFilter,
}

__all__ = [
    "ConfluenceFilter",
    "FilterResult",
    "FILTER_REGISTRY",
    "RSIDivergenceFilter",
    "VolumeProfileFilter",
    "MTFAlignmentFilter",
    "RegimeFilter",
    "SessionFilter",
]
