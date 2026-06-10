"""Tests for the account provisioning FastAPI router."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.accounts.account_provisioner import (
    AccountDetails,
    ProvisionRequest,
    ProvisionResponse,
    StubAccountProvisioner,
    UndeployResponse,
)
from src.accounts.router import configure_provisioner, router


@pytest.fixture
def app():
    """Create a FastAPI app with the accounts router for testing."""
    test_app = FastAPI()
    test_app.include_router(router)
    return test_app


@pytest.fixture
def stub_provisioner():
    """Create and configure a stub provisioner."""
    provisioner = StubAccountProvisioner()
    configure_provisioner(provisioner)
    return provisioner


@pytest.fixture
def client(app, stub_provisioner):
    """Create a test client with the stub provisioner configured."""
    return TestClient(app)


class TestProvisionEndpoint:
    """Tests for POST /accounts/provision."""

    def test_provision_returns_metaapi_account_id(self, client):
        response = client.post(
            "/accounts/provision",
            json={
                "login": "12345",
                "password": "secret",
                "server": "ICMarkets-Demo",
                "platform": "mt5",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert "metaapi_account_id" in data
        assert data["state"] == "DEPLOYED"

    def test_provision_invalid_credentials_returns_400(self, app):
        class FailingProvisioner:
            def provision(self, request):
                raise Exception("Invalid credentials provided")

        configure_provisioner(FailingProvisioner())
        client = TestClient(app)
        response = client.post(
            "/accounts/provision",
            json={
                "login": "bad",
                "password": "bad",
                "server": "Unknown",
                "platform": "mt5",
            },
        )
        assert response.status_code == 400
        assert "Invalid MT5 credentials" in response.json()["detail"]

    def test_provision_timeout_returns_504(self, app):
        class TimeoutProvisioner:
            def provision(self, request):
                raise Exception("Deployment timed out after 300 seconds")

        configure_provisioner(TimeoutProvisioner())
        client = TestClient(app)
        response = client.post(
            "/accounts/provision",
            json={
                "login": "12345",
                "password": "secret",
                "server": "ICMarkets-Demo",
                "platform": "mt5",
            },
        )
        assert response.status_code == 504
        assert "timed out" in response.json()["detail"].lower()

    def test_provision_sdk_unavailable_returns_502(self, app):
        class UnavailableProvisioner:
            def provision(self, request):
                raise Exception("MetaApi service unavailable")

        configure_provisioner(UnavailableProvisioner())
        client = TestClient(app)
        response = client.post(
            "/accounts/provision",
            json={
                "login": "12345",
                "password": "secret",
                "server": "ICMarkets-Demo",
                "platform": "mt5",
            },
        )
        assert response.status_code == 502

    def test_provision_missing_fields_returns_422(self, client):
        response = client.post("/accounts/provision", json={"login": "12345"})
        assert response.status_code == 422


class TestGetDetailsEndpoint:
    """Tests for GET /accounts/{metaapi_account_id}/details."""

    def test_get_details_returns_all_fields(self, client):
        response = client.get("/accounts/stub-account-123/details")
        assert response.status_code == 200
        data = response.json()
        assert "balance" in data
        assert "equity" in data
        assert "margin" in data
        assert "free_margin" in data
        assert "open_positions" in data
        assert "leverage" in data

    def test_get_details_not_found_returns_404(self, app):
        class NotFoundProvisioner:
            def get_details(self, metaapi_account_id):
                raise Exception("Account not found")

        configure_provisioner(NotFoundProvisioner())
        client = TestClient(app)
        response = client.get("/accounts/nonexistent/details")
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    def test_get_details_sdk_unavailable_returns_502(self, app):
        class UnavailableProvisioner:
            def get_details(self, metaapi_account_id):
                raise Exception("MetaApi service unavailable")

        configure_provisioner(UnavailableProvisioner())
        client = TestClient(app)
        response = client.get("/accounts/some-id/details")
        assert response.status_code == 502


class TestUndeployEndpoint:
    """Tests for POST /accounts/{metaapi_account_id}/undeploy."""

    def test_undeploy_returns_success(self, client):
        response = client.post("/accounts/stub-account-123/undeploy")
        assert response.status_code == 200
        assert response.json()["success"] is True

    def test_undeploy_failure_returns_500(self, app):
        class FailingProvisioner:
            def undeploy(self, metaapi_account_id):
                raise Exception("SDK error during undeploy")

        configure_provisioner(FailingProvisioner())
        client = TestClient(app)
        response = client.post("/accounts/some-id/undeploy")
        assert response.status_code == 500
        assert "Failed to undeploy" in response.json()["detail"]

    def test_undeploy_not_found_returns_404(self, app):
        class NotFoundProvisioner:
            def undeploy(self, metaapi_account_id):
                raise Exception("Account not found")

        configure_provisioner(NotFoundProvisioner())
        client = TestClient(app)
        response = client.post("/accounts/nonexistent/undeploy")
        assert response.status_code == 404


class TestNoProvisionerConfigured:
    """Tests for when no provisioner is configured (SDK unavailable)."""

    def test_provision_returns_502_when_no_provisioner(self, app):
        configure_provisioner(None)
        client = TestClient(app)
        response = client.post(
            "/accounts/provision",
            json={
                "login": "12345",
                "password": "secret",
                "server": "ICMarkets-Demo",
                "platform": "mt5",
            },
        )
        assert response.status_code == 502


class TestGetBrokerSymbolsEndpoint:
    """Tests for GET /accounts/{metaapi_account_id}/symbols."""

    def test_get_symbols_returns_list_of_strings(self, client):
        response = client.get("/accounts/stub-account-123/symbols")
        assert response.status_code == 200
        data = response.json()
        assert "symbols" in data
        assert isinstance(data["symbols"], list)
        assert all(isinstance(s, str) for s in data["symbols"])

    def test_get_symbols_stub_returns_hardcoded_demo_symbols(self, client):
        response = client.get("/accounts/stub-account-123/symbols")
        assert response.status_code == 200
        assert response.json()["symbols"] == ["US30.raw", "XAUUSD.r", "V75"]

    def test_get_symbols_400_when_account_not_deployed(self, app):
        class NotDeployedProvisioner:
            def get_symbols(self, metaapi_account_id):
                raise Exception("Account not in deployed state")

        configure_provisioner(NotDeployedProvisioner())
        client = TestClient(app)
        response = client.get("/accounts/some-id/symbols")
        assert response.status_code == 400
        assert "deployed" in response.json()["detail"].lower()

    def test_get_symbols_404_when_account_not_found(self, app):
        class NotFoundProvisioner:
            def get_symbols(self, metaapi_account_id):
                raise Exception("Account not found")

        configure_provisioner(NotFoundProvisioner())
        client = TestClient(app)
        response = client.get("/accounts/nonexistent/symbols")
        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    def test_get_symbols_502_when_metaapi_unavailable(self, app):
        class UnavailableProvisioner:
            def get_symbols(self, metaapi_account_id):
                raise Exception("MetaApi service unavailable")

        configure_provisioner(UnavailableProvisioner())
        client = TestClient(app)
        response = client.get("/accounts/some-id/symbols")
        assert response.status_code == 502
        assert "unavailable" in response.json()["detail"].lower()
