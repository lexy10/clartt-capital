"""Health endpoint for the Strategy Engine.

Returns service status and dependency health derived from circuit breaker states.
Uses cached circuit breaker status — no live pings — to respond within 5s.
"""

import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Response
from pydantic import BaseModel

from src.api import liveness
from src.circuit_breaker import CircuitBreaker, CircuitBreakerState

logger = logging.getLogger("strategy_engine.health")

health_router = APIRouter(tags=["health"])

# Set by main.py after creating pipeline components
_config_cb: Optional[CircuitBreaker] = None
_signals_cb: Optional[CircuitBreaker] = None
_config_loader = None  # StrategyConfigLoader — avoids circular import


def set_circuit_breakers(
    config_cb: CircuitBreaker,
    signals_cb: CircuitBreaker,
) -> None:
    """Inject circuit breaker references for health reporting."""
    global _config_cb, _signals_cb
    _config_cb = config_cb
    _signals_cb = signals_cb


def set_config_loader(loader) -> None:
    """Inject the strategy config loader so /health can report strategies
    that are configured in the backend but failing validation (= silently
    not trading)."""
    global _config_loader
    _config_loader = loader


class DependencyHealth(BaseModel):
    name: str
    status: str  # "healthy" | "degraded" | "unhealthy"
    circuitBreakerState: Optional[str] = None
    lastSuccessfulContact: str


class HealthResponse(BaseModel):
    service: str
    status: str  # "healthy" | "degraded" | "unhealthy"
    timestamp: str
    dependencies: list[DependencyHealth]
    # Strategies configured in the backend that failed validation and are
    # NOT running (name -> error summary). Non-empty => degraded.
    invalidStrategyConfigs: dict[str, str] = {}


def _status_from_cb_state(state: CircuitBreakerState) -> str:
    """Derive dependency status from circuit breaker state."""
    if state == CircuitBreakerState.CLOSED:
        return "healthy"
    elif state == CircuitBreakerState.HALF_OPEN:
        return "degraded"
    else:
        return "unhealthy"


def _aggregate_status(dep_statuses: list[str]) -> str:
    """Derive overall service status from dependency statuses.

    healthy: all deps healthy
    unhealthy: any critical dep unhealthy
    degraded: otherwise
    """
    if all(s == "healthy" for s in dep_statuses):
        return "healthy"
    if any(s == "unhealthy" for s in dep_statuses):
        return "degraded"
    return "degraded"


@health_router.get("/livez")
def livez(response: Response) -> dict:
    """Liveness probe for the Docker healthcheck / autoheal.

    Returns 503 when the signal-pipeline loop has stalled (no heartbeat within
    the staleness window) so the container is restarted. Independent of
    dependency health — see /health for that.
    """
    stale = liveness.seconds_since_beat()
    alive = stale <= liveness.MAX_STALE_S
    if not alive:
        response.status_code = 503
    return {
        "status": "alive" if alive else "stale",
        "secondsSinceBeat": round(stale, 1),
        "maxStaleSeconds": liveness.MAX_STALE_S,
    }


@health_router.get("/health")
def health() -> HealthResponse:
    """Return Strategy Engine health with dependency statuses from circuit breakers."""
    dependencies: list[DependencyHealth] = []

    dep_statuses: list[str] = []

    # Backend (config) — protected by strategy-to-backend-config CB
    if _config_cb is not None:
        cb_status = _config_cb.get_status()
        dep_status = _status_from_cb_state(_config_cb.state)
        dep_statuses.append(dep_status)
        dependencies.append(DependencyHealth(
            name="backend-config",
            status=dep_status,
            circuitBreakerState=cb_status.state.value,
            lastSuccessfulContact=cb_status.last_successful_contact,
        ))

    # Backend (signals) — protected by strategy-to-backend-signals CB
    if _signals_cb is not None:
        cb_status = _signals_cb.get_status()
        dep_status = _status_from_cb_state(_signals_cb.state)
        dep_statuses.append(dep_status)
        dependencies.append(DependencyHealth(
            name="backend-signals",
            status=dep_status,
            circuitBreakerState=cb_status.state.value,
            lastSuccessfulContact=cb_status.last_successful_contact,
        ))

    overall = _aggregate_status(dep_statuses) if dep_statuses else "healthy"

    # A strategy the backend thinks is enabled but the engine can't parse is
    # a silent trading gap — surface it and degrade overall status.
    invalid = _config_loader.invalid_configs if _config_loader is not None else {}
    if invalid and overall == "healthy":
        overall = "degraded"

    return HealthResponse(
        service="strategy-engine",
        status=overall,
        timestamp=datetime.now(timezone.utc).isoformat(),
        dependencies=dependencies,
        invalidStrategyConfigs=invalid,
    )
