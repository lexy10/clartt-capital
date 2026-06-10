"""MetaAPI broker client — used for forex, commodities, indices on MT5.

Wraps the MetaApi cloud SDK. All async SDK calls are run via asyncio.run()
to provide a sync interface matching the BrokerClient protocol.

Routes traffic for:
- forex (EURUSD, GBPUSD, ...)
- commodities (XAUUSD, OIL)
- indices (US30, NAS100)
- and any other instrument the user explicitly routes here
"""

import asyncio
import logging
import os
from typing import Optional

from src.executor.clients.base import BrokerProvider, OrderResult

logger = logging.getLogger(__name__)


class MetaApiForexClient:
    """Broker client backed by MetaApi cloud SDK.

    Connects to MetaApi using the provided API token and account ID.
    All async SDK calls are run via asyncio.run() to provide a sync interface.
    Implements the BrokerClient Protocol from clients.base.
    """

    provider = BrokerProvider.METAAPI

    def __init__(self, api_token: Optional[str] = None):
        self._api_token = api_token or os.environ.get("METAAPI_TOKEN", "")
        self._api = None
        self._account = None
        self._connection = None

    def _get_or_create_loop(self):
        """Get existing event loop or create a new one."""
        try:
            loop = asyncio.get_event_loop()
            if loop.is_closed():
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
            return loop
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            return loop

    def _run_async(self, coro):
        """Run an async coroutine synchronously."""
        loop = self._get_or_create_loop()
        return loop.run_until_complete(coro)

    async def _async_connect(self, account_id: str) -> bool:
        """Connect to a MetaApi trading account.

        Skips reconnection if already connected to the same account.
        """
        from metaapi_cloud_sdk import MetaApi

        # Skip if already connected to this account
        if (
            self._connection is not None
            and self._account is not None
            and getattr(self._account, 'id', None) == account_id
        ):
            logger.info("MetaApi: already connected to account %s, reusing", account_id)
            return True

        if self._api is None:
            self._api = MetaApi(token=self._api_token)

        self._account = await self._api.metatrader_account_api.get_account(account_id)

        if self._account.state != "DEPLOYED":
            await self._account.deploy()
            await self._account.wait_deployed()

        self._connection = self._account.get_rpc_connection()
        await self._connection.connect()
        await self._connection.wait_synchronized()

        logger.info("MetaApi: connected to account %s", account_id)
        return True

    def connect(self, account_id: str) -> bool:
        """Connect to a MetaApi trading account (sync wrapper)."""
        try:
            return self._run_async(self._async_connect(account_id))
        except Exception as e:
            logger.error("MetaApi connection failed for %s: %s", account_id, e)
            raise

    async def _async_send_order(
        self,
        instrument: str,
        direction: str,
        volume: float,
        price: float,
        sl: float,
        tp: float,
    ) -> OrderResult:
        """Place a trade order via MetaApi RPC connection."""
        if self._connection is None:
            return OrderResult(success=False, error_message="Not connected")

        action_type = "ORDER_TYPE_BUY" if direction == "BUY" else "ORDER_TYPE_SELL"

        result = await self._connection.create_market_buy_order(
            symbol=instrument,
            volume=volume,
            stop_loss=sl,
            take_profit=tp,
        ) if direction == "BUY" else await self._connection.create_market_sell_order(
            symbol=instrument,
            volume=volume,
            stop_loss=sl,
            take_profit=tp,
        )

        if result and result.get("stringCode") == "TRADE_RETCODE_DONE":
            return OrderResult(
                success=True,
                order_id=result.get("orderId", 0),
                fill_price=result.get("price", price),
                volume=volume,
            )
        else:
            error_msg = result.get("message", "Unknown error") if result else "No response"
            error_code = result.get("numericCode", 0) if result else 0
            return OrderResult(
                success=False,
                error_code=error_code,
                error_message=error_msg,
            )

    def send_order(
        self,
        instrument: str,
        direction: str,
        volume: float,
        price: float,
        sl: float,
        tp: float,
    ) -> OrderResult:
        """Place a trade order (sync wrapper)."""
        return self._run_async(
            self._async_send_order(instrument, direction, volume, price, sl, tp)
        )

    async def _async_modify_position(
        self, order_id: int, sl: Optional[float] = None, tp: Optional[float] = None
    ) -> OrderResult:
        """Modify SL/TP on an existing position."""
        if self._connection is None:
            return OrderResult(success=False, error_message="Not connected")

        result = await self._connection.modify_position(
            position_id=str(order_id),
            stop_loss=sl,
            take_profit=tp,
        )

        if result and result.get("stringCode") == "TRADE_RETCODE_DONE":
            return OrderResult(success=True, order_id=order_id)
        else:
            error_msg = result.get("message", "Unknown error") if result else "No response"
            return OrderResult(success=False, error_message=error_msg)

    def modify_position(
        self, order_id: int, sl: Optional[float] = None, tp: Optional[float] = None
    ) -> OrderResult:
        """Modify SL/TP (sync wrapper)."""
        return self._run_async(self._async_modify_position(order_id, sl, tp))

    async def _async_close_position(self, position_id: int) -> OrderResult:
        """Close a position by ID."""
        if self._connection is None:
            return OrderResult(success=False, error_message="Not connected")

        result = await self._connection.close_position(position_id=str(position_id))

        if result and result.get("stringCode") == "TRADE_RETCODE_DONE":
            return OrderResult(
                success=True,
                order_id=position_id,
                fill_price=result.get("price", 0.0),
            )
        else:
            error_msg = result.get("message", "Unknown error") if result else "No response"
            return OrderResult(success=False, error_message=error_msg)

    def close_position_by_id(self, position_id: int) -> OrderResult:
        """Close a position (sync wrapper)."""
        return self._run_async(self._async_close_position(position_id))

    async def _async_get_tick(self, instrument: str) -> Optional[dict]:
        """Get current tick data for an instrument."""
        if self._connection is None:
            return None

        tick = await self._connection.get_symbol_price(symbol=instrument)
        if tick:
            return {
                "bid": tick.get("bid", 0.0),
                "ask": tick.get("ask", 0.0),
                "time": tick.get("time", 0),
            }
        return None

    def get_symbol_info_tick(self, instrument: str) -> Optional[dict]:
        """Get tick data (sync wrapper)."""
        try:
            return self._run_async(self._async_get_tick(instrument))
        except Exception:
            return None
    async def _async_get_positions(self) -> list[dict]:
        """Get all open positions from the connected account."""
        if self._connection is None:
            return []
        positions = await self._connection.get_positions()
        return positions or []

    def get_positions(self) -> list[dict]:
        """Get all open positions (sync wrapper)."""
        try:
            return self._run_async(self._async_get_positions())
        except Exception:
            logger.exception("Failed to get positions")
            return []
