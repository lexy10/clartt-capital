"""Tests for the Execution Engine GET /livez liveness endpoint."""

from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src import liveness
from src.health_router import health_router


def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(health_router)
    return app


def test_alive_after_recent_beat():
    liveness.beat()
    resp = TestClient(_make_app()).get("/livez")
    assert resp.status_code == 200
    assert resp.json()["status"] == "alive"


def test_stale_returns_503():
    with patch.object(liveness, "seconds_since_beat", return_value=liveness.MAX_STALE_S + 5):
        resp = TestClient(_make_app()).get("/livez")
    assert resp.status_code == 503
    assert resp.json()["status"] == "stale"
