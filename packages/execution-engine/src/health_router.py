"""Health endpoint for the Execution Engine.

Returns service status and dependency health derived from circuit breaker states.
Uses cached circuit breaker status — no live pings — to respond within 5s.
"""

import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Response
from pydantic import BaseModel

from src import liveness
from src.circuit_breaker import CircuitBreaker, CircuitBreakerState

logger = logging.getLogger("execution_engine.health")

health_router = APIRouter(tags=["health"])

# Set by main.py after creating the circuit breaker
_metaapi_cb: Optional[CircuitBreaker] = None


def set_metaapi_circuit_breaker(cb: CircuitBreaker) -> None:
    """Inject the execution-to-metaapi circuit breaker for health reporting."""
    global _metaapi_cb
    _metaapi_cb = cb


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

    Returns 503 when the position-monitor loop has stalled (no heartbeat within
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
    """Return Execution Engine health with dependency statuses."""
    dependencies: list[DependencyHealth] = []
    dep_statuses: list[str] = []

    # MetaAPI — protected by execution-to-metaapi circuit breaker
    if _metaapi_cb is not None:
        cb_status = _metaapi_cb.get_status()
        dep_status = _status_from_cb_state(_metaapi_cb.state)
        dep_statuses.append(dep_status)
        dependencies.append(DependencyHealth(
            name="metaapi",
            status=dep_status,
            circuitBreakerState=cb_status.state.value,
            lastSuccessfulContact=cb_status.last_successful_contact,
        ))

    overall = _aggregate_status(dep_statuses) if dep_statuses else "healthy"

    return HealthResponse(
        service="execution-engine",
        status=overall,
        timestamp=datetime.now(timezone.utc).isoformat(),
        dependencies=dependencies,
    )
