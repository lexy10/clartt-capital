"""Strategy configuration loader with caching and backend API integration."""

import logging
import threading
import time

import requests
from pydantic import ValidationError

from src.circuit_breaker import CircuitBreaker, CircuitBreakerState
from src.metrics import on_circuit_breaker_state_change
from src.models import StrategyConfig

logger = logging.getLogger(__name__)


class StrategyConfigLoader:
    """Fetches and caches strategy configurations from the backend API."""

    def __init__(self, backend_url: str) -> None:
        self._backend_url = backend_url
        self._cache: list[StrategyConfig] = []
        self._cache_time: float = 0.0
        self._ttl: float = 60.0
        # Strategies that failed validation on the last refresh, keyed by
        # "name (id)". A strategy in here is configured in the backend but
        # NOT trading — that mismatch must be visible (health endpoint),
        # not buried in a log line.
        self._invalid: dict[str, str] = {}
        self._cb = CircuitBreaker(
            name="strategy-to-backend-config",
            on_state_change=self._on_state_change,
        )

    @property
    def invalid_configs(self) -> dict[str, str]:
        """Strategies that failed to parse on the last refresh (name -> error)."""
        return dict(self._invalid)

    def get_active_strategies(self, instrument: str | None = None) -> list[StrategyConfig]:
        """Return enabled strategies, optionally filtered by instrument.

        Refreshes from API if cache is expired.
        Falls back to cached configs when circuit breaker is open.
        """
        now = time.time()
        if now - self._cache_time >= self._ttl:
            try:
                self._cb.execute(
                    fn=self._refresh,
                    fallback=self._fallback_cached,
                )
            except requests.RequestException:
                logger.warning("Backend API unreachable, using stale cache")

        if instrument is None:
            return list(self._cache)
        return [s for s in self._cache if instrument in s.instruments]

    @property
    def circuit_breaker(self) -> CircuitBreaker:
        """Expose circuit breaker for health reporting."""
        return self._cb

    def _fallback_cached(self) -> None:
        """Fallback: return most recently cached strategy configurations (no-op refresh)."""
        logger.warning(
            "Circuit breaker open for strategy-to-backend-config, using cached configs (%d strategies)",
            len(self._cache),
        )

    def _on_state_change(self, name: str, old_state: str, new_state: str) -> None:
        """On recovery (transition to Closed): refresh configs from Backend within 5s."""
        on_circuit_breaker_state_change(name, old_state, new_state)
        if new_state == CircuitBreakerState.CLOSED.value:
            logger.info("Circuit breaker '%s' recovered, scheduling config refresh", name)
            timer = threading.Timer(0.0, self._refresh_on_recovery)
            timer.daemon = True
            timer.start()

    def _refresh_on_recovery(self) -> None:
        """Refresh configs from Backend after circuit breaker recovery."""
        try:
            self._refresh()
            logger.info("Strategy configs refreshed after circuit breaker recovery")
        except Exception:
            logger.warning("Failed to refresh configs after recovery", exc_info=True)

    def _refresh(self) -> None:
        """Fetch strategies from backend internal endpoint, parse and cache enabled ones."""
        url = f"{self._backend_url}/api/strategies"
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        raw_strategies = response.json()

        parsed: list[StrategyConfig] = []
        invalid: dict[str, str] = {}
        for raw in raw_strategies:
            # Skip disabled strategies before validation — a strategy that
            # isn't running shouldn't be validated or reported as "invalid".
            # This is what lets an incompletely-configured strategy sit in the
            # catalogue (disabled) without spamming parse errors every cycle.
            if not raw.get("enabled", True):
                continue
            config = self._parse_strategy(raw, invalid)
            if config is not None and config.enabled:
                parsed.append(config)

        self._cache = parsed
        self._invalid = invalid
        self._cache_time = time.time()

    def _parse_strategy(
        self, raw: dict, invalid: dict[str, str] | None = None
    ) -> StrategyConfig | None:
        """Parse a backend strategy response into StrategyConfig.

        Extracts id, name, algorithm from top-level response and merges
        them into the config dict before pydantic validation.
        Returns None on validation error; the failure is recorded in
        `invalid` (when provided) so the health endpoint can surface it.
        """
        if invalid is None:
            invalid = {}
        key = f"{raw.get('name', 'unnamed')} ({raw.get('id', '?')})"
        try:
            config_dict = dict(raw.get("config", {}))
            config_dict["id"] = raw.get("id")
            config_dict["name"] = raw.get("name")
            config_dict["algorithm"] = raw.get("algorithm", "ict_order_block")
            if "enabled" in raw:
                config_dict["enabled"] = raw["enabled"]
            return StrategyConfig(**config_dict)
        except ValidationError as exc:
            # Compact one-line summary: which fields, what kind of error.
            fields = ", ".join(
                ".".join(str(loc) for loc in err["loc"]) for err in exc.errors()
            )
            summary = f"invalid config — bad/missing fields: {fields}"
            invalid[key] = summary
            # First sighting is a warning; the same broken config on every
            # 60s refresh only logs at debug to keep the log readable.
            level = logging.DEBUG if self._invalid.get(key) == summary else logging.WARNING
            logger.log(level, "Strategy %s NOT loaded: %s", key, summary)
            return None
        except Exception as exc:
            invalid[key] = f"parse error: {exc}"
            logger.warning("Strategy %s NOT loaded: unexpected parse error", key, exc_info=True)
            return None
