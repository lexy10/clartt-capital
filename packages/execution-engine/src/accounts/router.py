"""FastAPI router for account provisioning endpoints.

Exposes REST endpoints for provisioning, fetching details, and
undeploying MetaTrader accounts via the account provisioner.

Also exposes Deriv-specific endpoints (POST /deriv/status, /deriv/details)
for fetching account info on Deriv-direct accounts (no MetaAPI).
"""

import asyncio
import json
import logging
import os
from typing import Optional

import websockets
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from src.accounts.account_provisioner import (
    AccountProvisioner,
    StubAccountProvisioner,
    ProvisionRequest,
    ProvisionResponse,
    AccountDetails,
    AccountStatus,
    UndeployResponse,
    BrokerSymbolsResponse,
    BrokerPositionsResponse,
)
from src.executor.clients.deriv import DERIV_WS_URL

logger = logging.getLogger("execution_engine.accounts.router")

router = APIRouter(prefix="/accounts", tags=["accounts"])

# Provisioner instance — set at startup via configure_provisioner()
_provisioner: AccountProvisioner | StubAccountProvisioner | None = None


def configure_provisioner(provisioner: AccountProvisioner | StubAccountProvisioner) -> None:
    """Set the provisioner instance used by the router endpoints."""
    global _provisioner
    _provisioner = provisioner


def _get_provisioner() -> AccountProvisioner | StubAccountProvisioner:
    if _provisioner is None:
        raise HTTPException(status_code=502, detail="MetaApi service unavailable")
    return _provisioner


@router.post("/provision", response_model=ProvisionResponse)
async def provision_account(request: ProvisionRequest) -> ProvisionResponse:
    """Provision a new MetaTrader account via MetaApi."""
    provisioner = _get_provisioner()
    try:
        return await provisioner.provision(request)
    except HTTPException:
        raise
    except Exception as exc:
        msg = str(exc).lower()
        if "validation" in msg:
            detail_info = getattr(exc, 'details', None) or str(exc)
            raise HTTPException(
                status_code=400,
                detail=f"MetaAPI validation failed: {detail_info}",
            )
        if "invalid" in msg or "credentials" in msg or "authentication" in msg:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid MT5 credentials: {exc}",
            )
        if "timeout" in msg or "timed out" in msg:
            raise HTTPException(
                status_code=504,
                detail="Account deployment timed out",
            )
        if "unavailable" in msg or "connect" in msg:
            raise HTTPException(
                status_code=502,
                detail="MetaApi service unavailable",
            )
        logger.exception("Unexpected error during account provisioning")
        raise HTTPException(status_code=500, detail=f"Provisioning failed: {exc}")


@router.get("/{metaapi_account_id}/details", response_model=AccountDetails)
async def get_account_details(metaapi_account_id: str) -> AccountDetails:
    """Fetch live account details from MetaApi."""
    provisioner = _get_provisioner()
    try:
        return await provisioner.get_details(metaapi_account_id)
    except HTTPException:
        raise
    except Exception as exc:
        msg = str(exc).lower()
        if "not found" in msg or "404" in msg:
            raise HTTPException(
                status_code=404,
                detail="MetaApi account not found",
            )
        if "not deployed" in msg or "deployed state" in msg:
            raise HTTPException(
                status_code=409,
                detail="Account not deployed",
            )
        if "timed out" in msg or "timeout" in msg or isinstance(exc, asyncio.TimeoutError):
            raise HTTPException(
                status_code=504,
                detail="RPC connection timed out",
            )
        if "unavailable" in msg or "connect" in msg:
            raise HTTPException(
                status_code=502,
                detail="MetaApi service unavailable",
            )
        logger.exception("Unexpected error fetching account details")
        raise HTTPException(status_code=500, detail=f"Failed to fetch account details: {exc}")


@router.get("/{metaapi_account_id}/status", response_model=AccountStatus)
async def get_account_status(metaapi_account_id: str) -> AccountStatus:
    """Get account state from MetaAPI (lightweight, no RPC connection)."""
    provisioner = _get_provisioner()
    try:
        return await provisioner.get_status(metaapi_account_id)
    except HTTPException:
        raise
    except Exception as exc:
        msg = str(exc).lower()
        if "not found" in msg or "404" in msg:
            raise HTTPException(
                status_code=404,
                detail="MetaApi account not found",
            )
        logger.exception("Unexpected error fetching account status")
        raise HTTPException(status_code=500, detail=f"Failed to fetch account status: {exc}")


@router.post("/{metaapi_account_id}/deploy", response_model=UndeployResponse)
async def deploy_account(metaapi_account_id: str) -> UndeployResponse:
    """Deploy (or re-deploy) an existing MetaApi account."""
    provisioner = _get_provisioner()
    try:
        return await provisioner.deploy(metaapi_account_id)
    except HTTPException:
        raise
    except Exception as exc:
        msg = str(exc).lower()
        if "not found" in msg or "404" in msg:
            raise HTTPException(
                status_code=404,
                detail="MetaApi account not found",
            )
        if "timeout" in msg or "timed out" in msg:
            raise HTTPException(
                status_code=504,
                detail="Account deployment timed out",
            )
        logger.exception("Failed to deploy account %s", metaapi_account_id)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to deploy account: {exc}",
        )


@router.post("/{metaapi_account_id}/undeploy", response_model=UndeployResponse)
async def undeploy_account(metaapi_account_id: str) -> UndeployResponse:
    """Undeploy a MetaApi account."""
    provisioner = _get_provisioner()
    try:
        return await provisioner.undeploy(metaapi_account_id)
    except HTTPException:
        raise
    except Exception as exc:
        msg = str(exc).lower()
        if "not found" in msg or "404" in msg:
            raise HTTPException(
                status_code=404,
                detail="MetaApi account not found",
            )
        logger.exception("Failed to undeploy account %s", metaapi_account_id)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to undeploy account: {exc}",
        )


@router.post("/{metaapi_account_id}/remove", response_model=UndeployResponse)
async def remove_account(metaapi_account_id: str) -> UndeployResponse:
    """Undeploy and permanently delete a MetaApi account."""
    provisioner = _get_provisioner()
    try:
        return await provisioner.remove(metaapi_account_id)
    except HTTPException:
        raise
    except Exception as exc:
        msg = str(exc).lower()
        if "not found" in msg or "404" in msg:
            raise HTTPException(
                status_code=404,
                detail="MetaApi account not found",
            )
        logger.exception("Failed to remove account %s", metaapi_account_id)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to remove account: {exc}",
        )


@router.get("/{metaapi_account_id}/symbols", response_model=BrokerSymbolsResponse)
async def get_broker_symbols(metaapi_account_id: str) -> BrokerSymbolsResponse:
    """Fetch available trading symbols from the broker for a given account."""
    provisioner = _get_provisioner()
    try:
        return await provisioner.get_symbols(metaapi_account_id)
    except HTTPException:
        raise
    except Exception as exc:
        msg = str(exc).lower()
        if "not deployed" in msg or "deployed state" in msg:
            raise HTTPException(
                status_code=400,
                detail="Account not in deployed state",
            )
        if "not found" in msg or "404" in msg:
            raise HTTPException(
                status_code=404,
                detail="MetaApi account not found",
            )
        if "timed out" in msg or "timeout" in msg or isinstance(exc, asyncio.TimeoutError):
            raise HTTPException(
                status_code=504,
                detail="Broker sync timed out — account may still be synchronizing. Try again shortly.",
            )
        if "unavailable" in msg or "connect" in msg:
            raise HTTPException(
                status_code=502,
                detail="MetaApi service unavailable",
            )
        logger.exception("Unexpected error fetching broker symbols for %s", metaapi_account_id)
        raise HTTPException(status_code=500, detail=f"Failed to fetch broker symbols: {exc}")


@router.get("/{metaapi_account_id}/positions", response_model=BrokerPositionsResponse)
async def get_broker_positions(metaapi_account_id: str) -> BrokerPositionsResponse:
    """Fetch open positions from broker via MetaAPI RPC."""
    provisioner = _get_provisioner()
    try:
        return await provisioner.get_positions(metaapi_account_id)
    except HTTPException:
        raise
    except Exception as exc:
        msg = str(exc).lower()
        if "not found" in msg or "404" in msg:
            raise HTTPException(
                status_code=404,
                detail="MetaApi account not found",
            )
        if "not deployed" in msg or "deployed state" in msg:
            raise HTTPException(
                status_code=409,
                detail="Account not deployed",
            )
        if "timed out" in msg or "timeout" in msg or isinstance(exc, asyncio.TimeoutError):
            raise HTTPException(
                status_code=504,
                detail="RPC connection timed out",
            )
        if "unavailable" in msg or "connect" in msg:
            raise HTTPException(
                status_code=502,
                detail="MetaApi service unavailable",
            )
        logger.exception("Unexpected error fetching positions for %s", metaapi_account_id)
        raise HTTPException(status_code=500, detail=f"Failed to fetch positions: {exc}")


# =====================================================================
# Deriv-direct endpoints
# =====================================================================

class DerivAccountRequest(BaseModel):
    """Request payload for Deriv-direct status/details lookups."""
    login_id: str
    api_token: str


class DerivStatusResponse(BaseModel):
    state: str
    connection_status: str
    login_id: Optional[str] = None
    is_virtual: Optional[bool] = None


class DerivDetailsResponse(BaseModel):
    state: str
    connection_status: str
    login_id: Optional[str] = None
    balance: float = 0.0
    equity: float = 0.0
    currency: str = "USD"
    open_positions: int = 0
    is_virtual: Optional[bool] = None


async def _deriv_authorize_and_fetch(login_id: str, api_token: str) -> dict:
    """Open a WebSocket, authorize, and fetch balance + portfolio.

    Used by both /deriv/status and /deriv/details. Returns the parsed
    authorize and portfolio responses bundled in one dict.
    """
    app_id = os.environ.get("DERIV_APP_ID", "1089")
    url = f"{DERIV_WS_URL}?app_id={app_id}"
    async with websockets.connect(
        url, ping_interval=30, ping_timeout=10, close_timeout=5,
    ) as ws:
        await ws.send(json.dumps({"authorize": api_token, "req_id": 1}))
        auth_resp = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
        if "error" in auth_resp:
            raise RuntimeError(f"Deriv authorize: {auth_resp['error'].get('message')}")
        auth = auth_resp.get("authorize", {})

        await ws.send(json.dumps({"portfolio": 1, "req_id": 2}))
        pf_resp = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
        contracts = (
            pf_resp.get("portfolio", {}).get("contracts", []) or []
            if "error" not in pf_resp
            else []
        )

        return {"authorize": auth, "contracts": contracts}


@router.post("/deriv/status", response_model=DerivStatusResponse)
async def deriv_status(request: DerivAccountRequest) -> DerivStatusResponse:
    """Get status for a Deriv-direct account by authorizing the token."""
    try:
        data = await _deriv_authorize_and_fetch(request.login_id, request.api_token)
        auth = data["authorize"]
        # If the loginid in the auth response doesn't match the expected one,
        # mark as DISCONNECTED so the dashboard flags it.
        match = auth.get("loginid") == request.login_id
        return DerivStatusResponse(
            state="DEPLOYED" if match else "MISMATCHED",
            connection_status="CONNECTED" if match else "DISCONNECTED",
            login_id=auth.get("loginid"),
            is_virtual=bool(auth.get("is_virtual")),
        )
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="Deriv API timeout")
    except RuntimeError as exc:
        # Authorize failed — invalid token, revoked, or wrong account
        logger.warning("Deriv status: %s", exc)
        return DerivStatusResponse(
            state="UNAUTHORIZED",
            connection_status="DISCONNECTED",
        )
    except Exception as exc:
        logger.exception("Deriv status unexpected error: %s", exc)
        raise HTTPException(status_code=502, detail=f"Deriv API unreachable: {exc}")


@router.post("/deriv/details", response_model=DerivDetailsResponse)
async def deriv_details(request: DerivAccountRequest) -> DerivDetailsResponse:
    """Get balance + open positions for a Deriv-direct account."""
    try:
        data = await _deriv_authorize_and_fetch(request.login_id, request.api_token)
        auth = data["authorize"]
        contracts = data["contracts"]
        match = auth.get("loginid") == request.login_id
        balance = float(auth.get("balance") or 0)
        return DerivDetailsResponse(
            state="DEPLOYED" if match else "MISMATCHED",
            connection_status="CONNECTED" if match else "DISCONNECTED",
            login_id=auth.get("loginid"),
            balance=balance,
            equity=balance,  # No floating P&L tracked at this granularity
            currency=str(auth.get("currency") or "USD"),
            open_positions=len(contracts),
            is_virtual=bool(auth.get("is_virtual")),
        )
    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="Deriv API timeout")
    except RuntimeError as exc:
        logger.warning("Deriv details: %s", exc)
        return DerivDetailsResponse(
            state="UNAUTHORIZED",
            connection_status="DISCONNECTED",
        )
    except Exception as exc:
        logger.exception("Deriv details unexpected error: %s", exc)
        raise HTTPException(status_code=502, detail=f"Deriv API unreachable: {exc}")
