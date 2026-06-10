import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Callable, Optional, TypeVar

T = TypeVar("T")

logger = logging.getLogger(__name__)


class CircuitBreakerState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class CircuitBreakerStatus:
    name: str
    state: CircuitBreakerState
    failure_count: int
    last_state_change: str       # ISO timestamp
    last_successful_contact: str  # ISO timestamp


class CircuitBreaker:
    def __init__(
        self,
        name: str,
        failure_threshold: Optional[int] = None,
        recovery_timeout_ms: Optional[int] = None,
        probe_interval_ms: Optional[int] = None,
        on_state_change: Optional[Callable[[str, str, str], None]] = None,
    ):
        self._name = name
        self._logger = logging.getLogger(f"CircuitBreaker:{name}")
        self._on_state_change = on_state_change

        env_threshold = os.environ.get("CB_FAILURE_THRESHOLD")
        env_recovery = os.environ.get("CB_RECOVERY_TIMEOUT_MS")
        env_probe = os.environ.get("CB_PROBE_INTERVAL_MS")

        self._failure_threshold = (
            failure_threshold
            if failure_threshold is not None
            else (int(env_threshold) if env_threshold else 5)
        )
        self._recovery_timeout_ms = (
            recovery_timeout_ms
            if recovery_timeout_ms is not None
            else (int(env_recovery) if env_recovery else 30000)
        )
        self._probe_interval_ms = (
            probe_interval_ms
            if probe_interval_ms is not None
            else (int(env_probe) if env_probe else 10000)
        )

        self._state = CircuitBreakerState.CLOSED
        self._failure_count = 0
        self._last_state_change_time: float = time.time()
        self._last_success_time: float = time.time()
        self._opened_at: float = 0.0
        self._probe_in_flight: bool = False

    def execute(self, fn: Callable[[], T], fallback: Callable[[], T]) -> T:
        if self._state == CircuitBreakerState.CLOSED:
            return self._execute_closed(fn)
        elif self._state == CircuitBreakerState.OPEN:
            return self._execute_open(fn, fallback)
        else:  # HALF_OPEN
            return self._execute_half_open(fn, fallback)

    def get_status(self) -> CircuitBreakerStatus:
        return CircuitBreakerStatus(
            name=self._name,
            state=self._state,
            failure_count=self._failure_count,
            last_state_change=datetime.fromtimestamp(
                self._last_state_change_time, tz=timezone.utc
            ).isoformat(),
            last_successful_contact=datetime.fromtimestamp(
                self._last_success_time, tz=timezone.utc
            ).isoformat(),
        )

    @property
    def state(self) -> CircuitBreakerState:
        return self._state

    @property
    def name(self) -> str:
        return self._name

    # -- Private state handlers --

    def _execute_closed(self, fn: Callable[[], T]) -> T:
        try:
            result = fn()
            self._on_success()
            return result
        except Exception:
            self._on_failure()
            raise

    def _execute_open(self, fn: Callable[[], T], fallback: Callable[[], T]) -> T:
        now = time.time()
        elapsed_ms = (now - self._opened_at) * 1000
        if elapsed_ms >= self._recovery_timeout_ms:
            self._transition_to(CircuitBreakerState.HALF_OPEN)
            return self._execute_half_open(fn, fallback)
        return fallback()

    def _execute_half_open(self, fn: Callable[[], T], fallback: Callable[[], T]) -> T:
        if self._probe_in_flight:
            return fallback()

        self._probe_in_flight = True
        try:
            result = fn()
            self._probe_in_flight = False
            self._on_probe_success()
            return result
        except Exception:
            self._probe_in_flight = False
            self._on_probe_failure()
            return fallback()

    # -- Outcome handlers --

    def _on_success(self) -> None:
        self._failure_count = 0
        self._last_success_time = time.time()

    def _on_failure(self) -> None:
        self._failure_count += 1
        if self._failure_count >= self._failure_threshold:
            self._transition_to(CircuitBreakerState.OPEN)

    def _on_probe_success(self) -> None:
        self._failure_count = 0
        self._last_success_time = time.time()
        self._transition_to(CircuitBreakerState.CLOSED)

    def _on_probe_failure(self) -> None:
        self._transition_to(CircuitBreakerState.OPEN)

    # -- State transition --

    def _transition_to(self, new_state: CircuitBreakerState) -> None:
        previous_state = self._state
        if previous_state == new_state:
            return

        self._state = new_state
        self._last_state_change_time = time.time()

        if new_state == CircuitBreakerState.OPEN:
            self._opened_at = time.time()

        self._logger.warning(
            "State transition: %s → %s [breaker=%s]",
            previous_state.value,
            new_state.value,
            self._name,
        )

        if self._on_state_change:
            try:
                self._on_state_change(
                    self._name, previous_state.value, new_state.value
                )
            except Exception as e:
                self._logger.warning("State change callback error: %s", str(e))
