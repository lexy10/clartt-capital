"""Account provisioning via MetaApi SDK.

Handles creating, deploying, fetching details for, and undeploying
MetaTrader accounts through the MetaApi cloud service.

All public methods are async — designed to run on uvicorn's event loop
via async FastAPI route handlers.
"""

import asyncio
import hashlib
import logging
import os
from typing import Optional

from pydantic import BaseModel


logger = logging.getLogger("execution_engine.account_provisioner")


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class ProvisionRequest(BaseModel):
    login: str
    password: str
    server: str
    platform: str  # "mt5" or "mt4"


class ProvisionResponse(BaseModel):
    metaapi_account_id: str
    state: str  # "DEPLOYED"


class AccountDetails(BaseModel):
    balance: float
    equity: float
    margin: float
    free_margin: float
    open_positions: int
    leverage: int


class UndeployResponse(BaseModel):
    success: bool


class AccountStatus(BaseModel):
    metaapi_account_id: str
    state: str  # DEPLOYED, UNDEPLOYED, DEPLOYING, etc.
    connection_status: str  # CONNECTED, DISCONNECTED, etc.


class BrokerSymbolsResponse(BaseModel):
    symbols: list[str]


class BrokerPosition(BaseModel):
    id: str
    symbol: str
    type: str  # "POSITION_TYPE_BUY" or "POSITION_TYPE_SELL"
    volume: float
    openPrice: float
    currentPrice: float
    profit: float
    swap: float
    commission: float


class BrokerPositionsResponse(BaseModel):
    positions: list[BrokerPosition]


# ---------------------------------------------------------------------------
# Real provisioner (uses MetaApi SDK)
# ---------------------------------------------------------------------------

class AccountProvisioner:
    """Provisions and manages MetaTrader accounts via the MetaApi SDK.

    All public methods are async — concurrent requests for different accounts
    run cooperatively on the same event loop without thread-safety issues.
    """

    DEPLOY_TIMEOUT_SECONDS = 300  # 5 minutes
    RPC_TIMEOUT_SECONDS = 60  # timeout for RPC calls

    def __init__(self, api_token: Optional[str] = None):
        self._api_token = api_token or os.environ.get("METAAPI_TOKEN", "")
        self._api = None
        self._rpc_connections: dict = {}  # cache: metaapi_account_id -> connection
        self._accounts: dict = {}  # cache: metaapi_account_id -> MetatraderAccount

    async def _ensure_api(self):
        if self._api is None:
            from metaapi_cloud_sdk import MetaApi
            self._api = MetaApi(token=self._api_token)
        return self._api

    # -- provision ----------------------------------------------------------

    async def provision(self, request: ProvisionRequest) -> ProvisionResponse:
        """Create, deploy, and wait for a MetaTrader account."""
        api = await self._ensure_api()
        magic = int(hashlib.sha256(request.login.encode()).hexdigest(), 16) % 2_000_000_000
        account = await api.metatrader_account_api.create_account({
            "login": request.login,
            "password": request.password,
            "name": f"account-{request.login}",
            "server": request.server,
            "platform": request.platform,
            "type": "cloud",
            "magic": magic,
        })
        await account.deploy()
        await account.wait_deployed(timeout_in_seconds=self.DEPLOY_TIMEOUT_SECONDS)
        logger.info("Account %s deployed (metaapi id: %s)", request.login, account.id)

        # Cache account object for historical candle fetching
        self._accounts[account.id] = account

        # Warm up RPC connection so get_symbols/get_details can reuse it
        try:
            connection = account.get_rpc_connection()
            await connection.connect()
            await asyncio.wait_for(
                connection.wait_synchronized(),
                timeout=self.RPC_TIMEOUT_SECONDS,
            )
            self._rpc_connections[account.id] = connection
            logger.info("Warmed up RPC connection for newly provisioned account %s", account.id)
        except Exception as exc:
            logger.warning("Failed to warm up RPC connection for %s: %s", account.id, exc)

        return ProvisionResponse(metaapi_account_id=account.id, state="DEPLOYED")

    # -- get_status (lightweight, no RPC) -----------------------------------

    async def get_status(self, metaapi_account_id: str) -> AccountStatus:
        """Get account state from MetaAPI without opening an RPC connection."""
        api = await self._ensure_api()
        account = await api.metatrader_account_api.get_account(metaapi_account_id)
        return AccountStatus(
            metaapi_account_id=metaapi_account_id,
            state=account.state,
            connection_status=getattr(account, 'connection_status', 'UNKNOWN'),
        )

    # -- get_details --------------------------------------------------------

    async def get_details(self, metaapi_account_id: str) -> AccountDetails:
        """Fetch live account details from MetaApi."""
        api = await self._ensure_api()
        account = await api.metatrader_account_api.get_account(metaapi_account_id)
        if account.state != "DEPLOYED":
            raise Exception(f"Account not deployed (state: {account.state})")

        # Reuse cached RPC connection if available and still open
        connection = self._rpc_connections.get(metaapi_account_id)
        need_new = connection is None or getattr(connection, '_closed', True) or not getattr(connection, '_opened', False)

        if need_new:
            connection = account.get_rpc_connection()
            await connection.connect()
            await asyncio.wait_for(
                connection.wait_synchronized(),
                timeout=self.RPC_TIMEOUT_SECONDS,
            )
            self._rpc_connections[metaapi_account_id] = connection

        try:
            info = await asyncio.wait_for(
                connection.get_account_information(),
                timeout=self.RPC_TIMEOUT_SECONDS,
            )
        except (asyncio.TimeoutError, Exception) as exc:
            # Cached connection went stale — reconnect once
            logger.warning("Cached RPC connection stale for %s (%s), reconnecting", metaapi_account_id, type(exc).__name__)
            self._rpc_connections.pop(metaapi_account_id, None)
            try:
                await connection.close()
            except Exception:
                pass
            connection = account.get_rpc_connection()
            await connection.connect()
            await asyncio.wait_for(
                connection.wait_synchronized(),
                timeout=self.RPC_TIMEOUT_SECONDS,
            )
            self._rpc_connections[metaapi_account_id] = connection
            info = await asyncio.wait_for(
                connection.get_account_information(),
                timeout=self.RPC_TIMEOUT_SECONDS,
            )

        positions = await asyncio.wait_for(
            connection.get_positions(),
            timeout=self.RPC_TIMEOUT_SECONDS,
        )

        return AccountDetails(
            balance=info.get("balance", 0.0),
            equity=info.get("equity", 0.0),
            margin=info.get("margin", 0.0),
            free_margin=info.get("freeMargin", 0.0),
            open_positions=len(positions),
            leverage=info.get("leverage", 0),
        )


    # -- get_positions ------------------------------------------------------

    async def get_positions(self, metaapi_account_id: str) -> BrokerPositionsResponse:
        """Fetch open positions from the broker via MetaApi RPC."""
        api = await self._ensure_api()
        account = await api.metatrader_account_api.get_account(metaapi_account_id)
        if account.state != "DEPLOYED":
            raise Exception(f"Account not deployed (state: {account.state})")

        # Reuse cached RPC connection if available and still open
        connection = self._rpc_connections.get(metaapi_account_id)
        need_new = connection is None or getattr(connection, '_closed', True) or not getattr(connection, '_opened', False)

        if need_new:
            connection = account.get_rpc_connection()
            await connection.connect()
            await asyncio.wait_for(
                connection.wait_synchronized(),
                timeout=self.RPC_TIMEOUT_SECONDS,
            )
            self._rpc_connections[metaapi_account_id] = connection

        try:
            raw_positions = await asyncio.wait_for(
                connection.get_positions(),
                timeout=self.RPC_TIMEOUT_SECONDS,
            )
        except (asyncio.TimeoutError, Exception) as exc:
            # Cached connection went stale — reconnect once
            logger.warning("Cached RPC connection stale for %s (%s), reconnecting", metaapi_account_id, type(exc).__name__)
            self._rpc_connections.pop(metaapi_account_id, None)
            try:
                await connection.close()
            except Exception:
                pass
            connection = account.get_rpc_connection()
            await connection.connect()
            await asyncio.wait_for(
                connection.wait_synchronized(),
                timeout=self.RPC_TIMEOUT_SECONDS,
            )
            self._rpc_connections[metaapi_account_id] = connection
            raw_positions = await asyncio.wait_for(
                connection.get_positions(),
                timeout=self.RPC_TIMEOUT_SECONDS,
            )

        positions = []
        for p in (raw_positions or []):
            positions.append(BrokerPosition(
                id=str(p.get("id", "")),
                symbol=p.get("symbol", ""),
                type=p.get("type", ""),
                volume=float(p.get("volume", 0.0)),
                openPrice=float(p.get("openPrice", 0.0)),
                currentPrice=float(p.get("currentPrice", 0.0)),
                profit=float(p.get("profit", 0.0)),
                swap=float(p.get("swap", 0.0)),
                commission=float(p.get("commission", 0.0)),
            ))
        return BrokerPositionsResponse(positions=positions)

    # -- deploy (re-deploy an existing account) ----------------------------

    async def deploy(self, metaapi_account_id: str) -> UndeployResponse:
        """Deploy (or re-deploy) an existing MetaApi account."""
        api = await self._ensure_api()
        account = await api.metatrader_account_api.get_account(metaapi_account_id)
        await account.deploy()
        await account.wait_deployed(timeout_in_seconds=self.DEPLOY_TIMEOUT_SECONDS)
        logger.info("Account %s re-deployed", metaapi_account_id)

        # Cache account object for historical candle fetching
        self._accounts[metaapi_account_id] = account

        # Warm up RPC connection for subsequent get_symbols/get_details calls
        try:
            connection = account.get_rpc_connection()
            await connection.connect()
            await asyncio.wait_for(
                connection.wait_synchronized(),
                timeout=self.RPC_TIMEOUT_SECONDS,
            )
            self._rpc_connections[metaapi_account_id] = connection
            logger.info("Warmed up RPC connection for re-deployed account %s", metaapi_account_id)
        except Exception as exc:
            logger.warning("Failed to warm up RPC connection for %s: %s", metaapi_account_id, exc)

        return UndeployResponse(success=True)

    # -- undeploy -----------------------------------------------------------

    async def undeploy(self, metaapi_account_id: str) -> UndeployResponse:
        """Undeploy a MetaApi account."""
        # Evict cached RPC connection and account object
        conn = self._rpc_connections.pop(metaapi_account_id, None)
        self._accounts.pop(metaapi_account_id, None)
        if conn:
            try:
                await conn.close()
            except Exception:
                pass

        api = await self._ensure_api()
        account = await api.metatrader_account_api.get_account(metaapi_account_id)
        await account.undeploy()
        logger.info("Account %s undeployed", metaapi_account_id)
        return UndeployResponse(success=True)

    # -- remove (undeploy + delete from MetaApi) ----------------------------

    async def remove(self, metaapi_account_id: str) -> UndeployResponse:
        """Undeploy and permanently delete a MetaApi account."""
        # Evict cached RPC connection
        conn = self._rpc_connections.pop(metaapi_account_id, None)
        if conn:
            try:
                await conn.close()
            except Exception:
                pass

        api = await self._ensure_api()
        account = await api.metatrader_account_api.get_account(metaapi_account_id)
        if account.state == "DEPLOYED":
            await account.undeploy()
            await account.wait_undeployed(timeout_in_seconds=self.DEPLOY_TIMEOUT_SECONDS)
        await account.remove()
        logger.info("Account %s removed from MetaApi", metaapi_account_id)
        return UndeployResponse(success=True)

    # -- get_account_object -------------------------------------------------

    async def get_account_object(self, metaapi_account_id: str):
        """Get or fetch the MetatraderAccount object (cached)."""
        if metaapi_account_id in self._accounts:
            return self._accounts[metaapi_account_id]
        api = await self._ensure_api()
        account = await api.metatrader_account_api.get_account(metaapi_account_id)
        self._accounts[metaapi_account_id] = account
        return account

    # -- get_symbols --------------------------------------------------------

    async def get_symbols(self, metaapi_account_id: str) -> BrokerSymbolsResponse:
        """Fetch available trading symbols from the broker via MetaApi."""
        api = await self._ensure_api()
        account = await api.metatrader_account_api.get_account(metaapi_account_id)
        if account.state != "DEPLOYED":
            raise Exception("Account not in deployed state")

        conn_status = getattr(account, 'connection_status', 'DISCONNECTED')
        logger.info(
            "get_symbols: account %s state=%s connection_status=%s",
            metaapi_account_id, account.state, conn_status,
        )

        # Try cached RPC connection first (warmed up by provision or get_details)
        connection = self._rpc_connections.get(metaapi_account_id)
        if connection is not None and not getattr(connection, '_closed', True) and getattr(connection, '_opened', False):
            logger.info("get_symbols: reusing cached RPC connection for %s", metaapi_account_id)
            try:
                symbols = await asyncio.wait_for(
                    connection.get_symbols(),
                    timeout=self.RPC_TIMEOUT_SECONDS,
                )
                return self._parse_symbols(symbols, metaapi_account_id)
            except Exception as exc:
                logger.warning("get_symbols: cached connection failed for %s (%s), falling back to fresh connection", metaapi_account_id, exc)
                self._rpc_connections.pop(metaapi_account_id, None)
                try:
                    await connection.close()
                except Exception:
                    pass

        # No cached connection — create fresh one with retry logic
        connection = account.get_rpc_connection()
        await connection.connect()

        last_error = None
        for attempt in range(3):
            timeout = 60 + (attempt * 30)  # 60s, 90s, 120s
            try:
                logger.info(
                    "get_symbols: attempt %d, waiting for sync (timeout=%ds)",
                    attempt + 1, timeout,
                )
                await asyncio.wait_for(connection.wait_synchronized(), timeout=timeout)
                last_error = None
                break
            except asyncio.TimeoutError as e:
                last_error = e
                logger.warning(
                    "get_symbols: wait_synchronized timed out on attempt %d for %s",
                    attempt + 1, metaapi_account_id,
                )
                if attempt < 2:
                    try:
                        await connection.close()
                    except Exception:
                        pass
                    await asyncio.sleep(5)
                    connection = account.get_rpc_connection()
                    await connection.connect()

        if last_error is not None:
            raise asyncio.TimeoutError(
                f"wait_synchronized timed out after 3 attempts for {metaapi_account_id}"
            )

        # Cache the connection for future use (get_details sync loop, etc.)
        self._rpc_connections[metaapi_account_id] = connection

        symbols = await connection.get_symbols()
        return self._parse_symbols(symbols, metaapi_account_id)

    def _parse_symbols(self, symbols, metaapi_account_id: str) -> BrokerSymbolsResponse:
        """Parse MetaAPI symbols response into BrokerSymbolsResponse."""
        logger.info(
            "get_symbols: raw response type=%s, len=%d, sample=%s",
            type(symbols).__name__,
            len(symbols) if symbols else 0,
            repr(symbols[:3]) if symbols and len(symbols) > 0 else "empty",
        )
        symbol_names = []
        for s in (symbols or []):
            if isinstance(s, dict) and "symbol" in s:
                symbol_names.append(s["symbol"])
            elif isinstance(s, str):
                symbol_names.append(s)
        logger.info("get_symbols: fetched %d symbols for %s", len(symbol_names), metaapi_account_id)
        return BrokerSymbolsResponse(symbols=symbol_names)


# ---------------------------------------------------------------------------
# Stub provisioner (demo mode — no METAAPI_TOKEN)
# ---------------------------------------------------------------------------

class StubAccountProvisioner:
    """Stub provisioner that returns mock data when no MetaApi token is available."""

    _counter = 0

    async def provision(self, request: ProvisionRequest) -> ProvisionResponse:
        StubAccountProvisioner._counter += 1
        mock_id = f"stub-account-{request.login}-{StubAccountProvisioner._counter}"
        logger.info("StubAccountProvisioner: simulated provision for login %s → %s", request.login, mock_id)
        return ProvisionResponse(metaapi_account_id=mock_id, state="DEPLOYED")

    async def get_details(self, metaapi_account_id: str) -> AccountDetails:
        logger.info("StubAccountProvisioner: simulated get_details for %s", metaapi_account_id)
        return AccountDetails(
            balance=10000.0,
            equity=10000.0,
            margin=0.0,
            free_margin=10000.0,
            open_positions=0,
            leverage=100,
        )

    async def get_status(self, metaapi_account_id: str) -> AccountStatus:
        logger.info("StubAccountProvisioner: simulated get_status for %s", metaapi_account_id)
        return AccountStatus(
            metaapi_account_id=metaapi_account_id,
            state="DEPLOYED",
            connection_status="CONNECTED",
        )

    async def deploy(self, metaapi_account_id: str) -> UndeployResponse:
        logger.info("StubAccountProvisioner: simulated deploy for %s", metaapi_account_id)
        return UndeployResponse(success=True)

    async def undeploy(self, metaapi_account_id: str) -> UndeployResponse:
        logger.info("StubAccountProvisioner: simulated undeploy for %s", metaapi_account_id)
        return UndeployResponse(success=True)

    async def remove(self, metaapi_account_id: str) -> UndeployResponse:
        logger.info("StubAccountProvisioner: simulated remove for %s", metaapi_account_id)
        return UndeployResponse(success=True)

    async def get_symbols(self, metaapi_account_id: str) -> BrokerSymbolsResponse:
        logger.info("StubAccountProvisioner: simulated get_symbols for %s", metaapi_account_id)
        return BrokerSymbolsResponse(symbols=["US30.raw", "XAUUSD.r", "V75"])

    async def get_positions(self, metaapi_account_id: str) -> BrokerPositionsResponse:
        logger.info("StubAccountProvisioner: simulated get_positions for %s", metaapi_account_id)
        return BrokerPositionsResponse(positions=[
            BrokerPosition(
                id="stub-pos-1",
                symbol="US30.raw",
                type="POSITION_TYPE_BUY",
                volume=0.1,
                openPrice=39500.0,
                currentPrice=39550.0,
                profit=50.0,
                swap=-1.2,
                commission=-3.5,
            ),
            BrokerPosition(
                id="stub-pos-2",
                symbol="XAUUSD.r",
                type="POSITION_TYPE_SELL",
                volume=0.05,
                openPrice=2350.0,
                currentPrice=2345.0,
                profit=25.0,
                swap=-0.5,
                commission=-2.0,
            ),
        ])
