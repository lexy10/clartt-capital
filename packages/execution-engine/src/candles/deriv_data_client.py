"""Deriv WebSocket API client for market data (historical candles + live ticks).

Uses Deriv's native WebSocket API at wss://ws.derivws.com/websockets/v3
for all market data operations. This replaces MetaAPI for chart/candle data
while MetaAPI remains for trade execution.
"""

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Optional, Callable, Awaitable

import websockets

logger = logging.getLogger("execution_engine.candles.deriv_client")

DERIV_WS_URL = "wss://ws.derivws.com/websockets/v3"

# Deriv granularity mapping (timeframe -> seconds)
GRANULARITY_MAP = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
    "4h": 14400,
    "1d": 86400,
}

# Max candles Deriv returns per ticks_history call
DERIV_BATCH_LIMIT = 5000

# Number of consecutive errors before forcing a reconnect
ERROR_THRESHOLD = 3


class DerivDataClient:
    """WebSocket client for Deriv market data API.

    Handles:
    - Historical OHLC candle fetching via ticks_history
    - Live tick subscriptions for real-time price streaming
    - Automatic reconnection on persistent errors
    """

    def __init__(self, app_id: str, api_token: str = ""):
        self._app_id = app_id
        self._api_token = api_token
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._req_id = 0
        self._lock = asyncio.Lock()
        self._pending: dict[int, asyncio.Future] = {}
        self._tick_callbacks: dict[str, Callable] = {}
        self._reader_task: Optional[asyncio.Task] = None
        self._connected = False
        self._consecutive_errors = 0

    @property
    def url(self) -> str:
        return f"{DERIV_WS_URL}?app_id={self._app_id}"

    async def connect(self) -> None:
        """Establish WebSocket connection to Deriv."""
        if self._connected and self._ws:
            return
        try:
            self._ws = await websockets.connect(
                self.url,
                ping_interval=30,
                ping_timeout=10,
                close_timeout=5,
            )
            self._connected = True
            self._consecutive_errors = 0
            self._reader_task = asyncio.create_task(self._read_loop())

            # Authorize if token provided
            if self._api_token:
                await self._send_and_wait({"authorize": self._api_token})

            logger.info("Connected to Deriv WebSocket API (app_id=%s)", self._app_id)
        except Exception as exc:
            logger.error("Failed to connect to Deriv: %s", exc)
            self._connected = False
            raise

    async def disconnect(self) -> None:
        """Close the WebSocket connection."""
        self._connected = False
        if self._reader_task and not self._reader_task.done():
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None
        self._pending.clear()
        self._tick_callbacks.clear()
        self._consecutive_errors = 0
        logger.info("Disconnected from Deriv WebSocket API")

    async def force_reconnect(self) -> None:
        """Force a full disconnect + reconnect cycle."""
        logger.warning("Forcing Deriv WebSocket reconnect...")
        await self.disconnect()
        await asyncio.sleep(2)
        await self.connect()

    async def get_historical_candles(
        self,
        symbol: str,
        granularity: int,
        start: int,
        end: int,
        count: int = DERIV_BATCH_LIMIT,
    ) -> list[dict]:
        """Fetch historical OHLC candles from Deriv.

        Uses start + end range.  Deriv returns up to 5000 candles anchored
        to `end`, so callers should keep the time window small enough that
        the full range fits within the limit.
        """
        await self._ensure_connected()

        req = {
            "ticks_history": symbol,
            "adjust_start_time": 1,
            "count": count,
            "end": str(end),
            "start": str(start),
            "granularity": granularity,
            "style": "candles",
        }

        resp = await self._send_and_wait(req)

        if "error" in resp:
            err = resp["error"]
            logger.error("Deriv ticks_history error: %s (code: %s)", err.get("message"), err.get("code"))
            await self._track_error()
            return []

        self._consecutive_errors = 0
        candles = resp.get("candles", [])
        return candles

    async def subscribe_ticks(
        self,
        symbol: str,
        callback: Callable[[dict], Awaitable[None]],
    ) -> Optional[str]:
        """Subscribe to live tick updates for a symbol.

        Args:
            symbol: Deriv symbol
            callback: Async function called with each tick dict

        Returns:
            Subscription ID string, or None on failure.
        """
        await self._ensure_connected()

        req = {
            "ticks": symbol,
            "subscribe": 1,
        }

        resp = await self._send_and_wait(req)

        if "error" in resp:
            err = resp["error"]
            logger.error("Deriv tick subscribe error for %s: %s", symbol, err.get("message"))
            await self._track_error()
            return None

        self._consecutive_errors = 0
        sub_id = resp.get("subscription", {}).get("id")
        if sub_id:
            self._tick_callbacks[sub_id] = callback
            logger.info("Subscribed to ticks for %s (sub_id=%s)", symbol, sub_id)

        return sub_id

    async def unsubscribe(self, sub_id: str) -> None:
        """Unsubscribe from a tick stream."""
        self._tick_callbacks.pop(sub_id, None)
        if self._connected and self._ws:
            try:
                await self._send_and_wait({"forget": sub_id})
            except Exception as exc:
                logger.warning("Failed to unsubscribe %s: %s", sub_id, exc)

    async def _track_error(self) -> None:
        """Track consecutive errors and force reconnect if threshold exceeded."""
        self._consecutive_errors += 1
        if self._consecutive_errors >= ERROR_THRESHOLD:
            logger.warning(
                "Hit %d consecutive Deriv errors, forcing reconnect",
                self._consecutive_errors,
            )
            await self.force_reconnect()

    async def _ensure_connected(self) -> None:
        """Reconnect if not connected."""
        if not self._connected or not self._ws:
            await self.connect()

    async def _send_and_wait(self, payload: dict, timeout: float = 30.0) -> dict:
        """Send a request and wait for the matching response."""
        async with self._lock:
            self._req_id += 1
            req_id = self._req_id

        payload["req_id"] = req_id
        future: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[req_id] = future

        try:
            await self._ws.send(json.dumps(payload))
            return await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError:
            self._pending.pop(req_id, None)
            logger.warning("Request %d timed out", req_id)
            return {"error": {"message": "Request timed out", "code": "Timeout"}}
        except websockets.ConnectionClosed:
            self._pending.pop(req_id, None)
            self._connected = False
            logger.warning("Connection closed during send, will reconnect on next call")
            return {"error": {"message": "Connection closed", "code": "ConnectionClosed"}}
        except Exception as exc:
            self._pending.pop(req_id, None)
            self._connected = False
            logger.error("Send failed: %s", exc)
            return {"error": {"message": str(exc), "code": "SendError"}}

    async def _read_loop(self) -> None:
        """Background task that reads messages from the WebSocket."""
        try:
            async for raw in self._ws:
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue

                # Handle response to a pending request
                req_id = msg.get("req_id")
                if req_id and req_id in self._pending:
                    future = self._pending.pop(req_id)
                    if not future.done():
                        future.set_result(msg)

                # Handle streaming tick updates
                if "tick" in msg:
                    sub_id = msg.get("subscription", {}).get("id")
                    cb = self._tick_callbacks.get(sub_id)
                    if cb:
                        try:
                            await cb(msg["tick"])
                        except Exception as exc:
                            logger.warning("Tick callback error: %s", exc)

        except websockets.ConnectionClosed:
            logger.warning("Deriv WebSocket connection closed")
            self._connected = False
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.error("Deriv read loop error: %s", exc)
            self._connected = False
