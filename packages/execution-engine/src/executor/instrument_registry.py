"""InstrumentRegistry — fetches and caches instrument metadata from the Backend.

The TradeExecutor needs to know an instrument's category (synthetic, forex, etc.)
and any per-instrument broker provider override to route trades correctly.
Rather than hitting the Backend API on every trade, this registry caches the
data with a TTL.

Talks to GET /api/instruments which returns:
[
  { "symbol": "R_25", "category": "synthetic", "preferredProvider": "deriv", ... },
  ...
]
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Optional

import requests

from src.executor.clients.base import (
    BrokerProvider,
    InstrumentCategory,
    detect_category,
)

logger = logging.getLogger(__name__)

DEFAULT_TTL_SECONDS = 300  # 5 minutes


class InstrumentInfo:
    """Cached metadata for a single instrument."""

    __slots__ = ("symbol", "category", "preferred_provider")

    def __init__(
        self,
        symbol: str,
        category: Optional[InstrumentCategory] = None,
        preferred_provider: Optional[BrokerProvider] = None,
    ):
        self.symbol = symbol
        self.category = category
        self.preferred_provider = preferred_provider


class InstrumentRegistry:
    """Thread-safe instrument metadata cache.

    Lookups:
        registry.get("R_25") -> InstrumentInfo(category=SYNTHETIC, preferred_provider=DERIV)
        registry.get("UNKNOWN_SYMBOL") -> InstrumentInfo with auto-detected category

    Refresh:
        registry.refresh()  # Force reload from backend
    """

    def __init__(
        self,
        backend_url: Optional[str] = None,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
    ):
        self._backend_url = (backend_url or os.environ.get("BACKEND_URL", "http://backend:3000")).rstrip("/")
        self._ttl_seconds = ttl_seconds
        self._cache: dict[str, InstrumentInfo] = {}
        self._last_refresh: float = 0.0
        self._lock = threading.RLock()

    def _maybe_refresh(self) -> None:
        if time.time() - self._last_refresh > self._ttl_seconds:
            self.refresh()

    def refresh(self) -> None:
        """Reload the entire instrument table from the backend."""
        url = f"{self._backend_url}/api/instruments"
        try:
            r = requests.get(url, timeout=5)
            r.raise_for_status()
            data = r.json()
        except Exception as exc:
            logger.warning("InstrumentRegistry refresh failed (%s): %s", url, exc)
            self._last_refresh = time.time()  # Don't hammer on failure
            return

        with self._lock:
            self._cache.clear()
            for row in data:
                symbol = row.get("symbol") or ""
                if not symbol:
                    continue
                category = _parse_category(row.get("category"))
                provider = _parse_provider(row.get("preferredProvider") or row.get("preferred_provider"))
                self._cache[symbol] = InstrumentInfo(
                    symbol=symbol,
                    category=category,
                    preferred_provider=provider,
                )
            self._last_refresh = time.time()
        logger.info("InstrumentRegistry: cached %d instruments", len(self._cache))

    def get(self, symbol: str) -> InstrumentInfo:
        """Look up an instrument. Auto-detects category for unknown symbols.

        Never returns None — falls back to detect_category() so trades on
        new/unregistered symbols still route somewhere reasonable.
        """
        self._maybe_refresh()
        with self._lock:
            info = self._cache.get(symbol)
        if info is not None and info.category is not None:
            return info

        # Unknown or partially-populated — fall back to auto-detection
        detected = detect_category(symbol)
        if info is None:
            info = InstrumentInfo(symbol=symbol, category=detected)
            logger.debug("InstrumentRegistry: %s not in DB, auto-detected as %s", symbol, detected)
        else:
            info.category = detected
        return info


def _parse_category(value) -> Optional[InstrumentCategory]:
    if not value or not isinstance(value, str):
        return None
    try:
        return InstrumentCategory(value.lower())
    except ValueError:
        return None


def _parse_provider(value) -> Optional[BrokerProvider]:
    if not value or not isinstance(value, str):
        return None
    try:
        return BrokerProvider(value.lower())
    except ValueError:
        return None
