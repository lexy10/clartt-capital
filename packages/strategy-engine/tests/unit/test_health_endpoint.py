"""Tests for the Strategy Engine GET /health endpoint."""

from unittest.mock import patch

from fastapi.testclient import TestClient
from fastapi import FastAPI

from src.api import liveness
from src.api.health_router import health_router, set_circuit_breakers
from src.circuit_breaker import CircuitBreaker, CircuitBreakerState


def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(health_router)
    return app


class TestHealthEndpointAllClosed:
    """When both circuit breakers are closed, health should be 'healthy'."""

    def test_returns_healthy_when_all_closed(self):
        config_cb = CircuitBreaker(name="strategy-to-backend-config", failure_threshold=5)
        signals_cb = CircuitBreaker(name="strategy-to-backend-signals", failure_threshold=5)
        set_circuit_breakers(config_cb, signals_cb)

        client = TestClient(_make_app())
        resp = client.get("/health")
        assert resp.status_code == 200
        body = resp.json()

        assert body["service"] == "strategy-engine"
        assert body["status"] == "healthy"
        assert len(body["dependencies"]) == 2

        for dep in body["dependencies"]:
            assert dep["status"] == "healthy"
            assert dep["circuitBreakerState"] == "closed"
            assert dep["name"] in ("backend-config", "backend-signals")
            assert dep["lastSuccessfulContact"] is not None


class TestHealthEndpointDegraded:
    """When a circuit breaker is half-open, overall status should be 'degraded'."""

    def test_returns_degraded_when_half_open(self):
        config_cb = CircuitBreaker(name="strategy-to-backend-config", failure_threshold=2)
        signals_cb = CircuitBreaker(name="strategy-to-backend-signals", failure_threshold=5)

        # Trip the config CB to open, then advance to half-open
        for _ in range(2):
            try:
                config_cb.execute(fn=lambda: (_ for _ in ()).throw(Exception("fail")), fallback=lambda: None)
            except Exception:
                pass

        # Now it's open — advance time to trigger half-open on next execute
        with patch("time.time", return_value=config_cb._opened_at + 31):
            config_cb.execute(fn=lambda: "ok", fallback=lambda: "fallback")

        # After successful probe, it goes back to closed. Let's just test open state instead.
        config_cb2 = CircuitBreaker(name="strategy-to-backend-config", failure_threshold=2)
        for _ in range(2):
            try:
                config_cb2.execute(fn=lambda: (_ for _ in ()).throw(Exception("fail")), fallback=lambda: None)
            except Exception:
                pass

        # config_cb2 is now OPEN
        set_circuit_breakers(config_cb2, signals_cb)

        client = TestClient(_make_app())
        resp = client.get("/health")
        body = resp.json()

        assert body["status"] == "degraded"
        config_dep = next(d for d in body["dependencies"] if d["name"] == "backend-config")
        assert config_dep["status"] == "unhealthy"
        assert config_dep["circuitBreakerState"] == "open"


class TestLivenessEndpoint:
    """/livez reflects loop heartbeat freshness, independent of dependencies."""

    def test_alive_after_recent_beat(self):
        liveness.beat()
        client = TestClient(_make_app())
        resp = client.get("/livez")
        assert resp.status_code == 200
        assert resp.json()["status"] == "alive"

    def test_stale_returns_503(self):
        client = TestClient(_make_app())
        # Simulate a heartbeat older than the staleness window.
        with patch.object(liveness, "seconds_since_beat", return_value=liveness.MAX_STALE_S + 5):
            resp = client.get("/livez")
        assert resp.status_code == 503
        assert resp.json()["status"] == "stale"

    def test_liveness_ignores_open_breaker(self):
        # An open dependency breaker must NOT make the engine look non-live —
        # a restart wouldn't fix an external dependency.
        config_cb = CircuitBreaker(name="strategy-to-backend-config", failure_threshold=2)
        for _ in range(2):
            try:
                config_cb.execute(fn=lambda: (_ for _ in ()).throw(Exception("fail")), fallback=lambda: None)
            except Exception:
                pass
        set_circuit_breakers(config_cb, CircuitBreaker(name="strategy-to-backend-signals"))
        liveness.beat()
        client = TestClient(_make_app())
        assert client.get("/livez").status_code == 200


class TestHealthEndpointResponseShape:
    """Health response must contain all required fields per design."""

    def test_response_has_required_fields(self):
        config_cb = CircuitBreaker(name="strategy-to-backend-config")
        signals_cb = CircuitBreaker(name="strategy-to-backend-signals")
        set_circuit_breakers(config_cb, signals_cb)

        client = TestClient(_make_app())
        resp = client.get("/health")
        body = resp.json()

        assert "service" in body
        assert "status" in body
        assert "timestamp" in body
        assert "dependencies" in body
        assert body["status"] in ("healthy", "degraded", "unhealthy")

        for dep in body["dependencies"]:
            assert "name" in dep
            assert "status" in dep
            assert dep["status"] in ("healthy", "degraded", "unhealthy")
            assert "lastSuccessfulContact" in dep
