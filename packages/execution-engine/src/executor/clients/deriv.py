"""Deriv direct broker client — used for synthetic indices via Deriv WebSocket API.

Uses Deriv's native WebSocket API at wss://ws.derivws.com/websockets/v3 to
place trades on synthetic instruments (R_10, R_25, R_75, R_100, BOOM, CRASH)
without going through MetaAPI or MT5. This is faster and eliminates the
MetaAPI subscription cost for synthetic-only setups.

Implements the BrokerClient Protocol via MULTUP/MULTDOWN multiplier contracts,
which are the closest Deriv equivalent to traditional market orders with
configurable SL/TP.

Notes:
- Authentication uses a Deriv API token (free from app.deriv.com/account/api-token)
- Volume maps to the multiplier amount in account currency, not lots
- SL/TP map to Deriv's stop_loss / take_profit limit_order_amount

The position_id returned by Deriv buy responses is the contract_id used
for subsequent sell/portfolio calls.
"""

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Optional

import websockets

from src.executor.clients.base import BrokerProvider, ExitDetails, OrderResult

logger = logging.getLogger(__name__)

DERIV_WS_URL = "wss://ws.derivws.com/websockets/v3"


class DerivSyntheticClient:
    """Broker client for Deriv synthetic indices.

    Places trades as MULTUP/MULTDOWN multiplier contracts which most closely
    match traditional leveraged market orders. SL/TP are placed via Deriv's
    limit_order mechanism.

    All methods are sync wrappers around an internal asyncio loop.
    """

    provider = BrokerProvider.DERIV

    # Default contract parameters
    # Deriv requires symbol-specific multiplier values (the leverage).
    DEFAULT_MULTIPLIER = 400
    SYMBOL_MULTIPLIER: dict[str, int] = {
        "R_10":  400,
        "R_25":  400,
        "R_50":  400,
        "R_75":  150,
        "R_100": 400,
    }
    # Full set of multipliers Deriv accepts per symbol, ascending. We pick the
    # largest that keeps the strategy's stop-loss WITHIN the stake — a multiplier
    # contract can never lose more than its stake, so Deriv rejects any
    # stop_loss whose USD value exceeds the stake ("Enter an amount equal to or
    # lower than <stake>"). The stop fits only when multiplier * (sl_distance /
    # price) <= 1, which is independent of the stake size — the multiplier is the
    # only lever. Smaller multiplier => wider stop allowed.
    ALLOWED_MULTIPLIERS: dict[str, list[int]] = {
        "R_10":  [100, 200, 300, 400, 500],
        "R_25":  [160, 400, 800, 1200, 1600],
        "R_50":  [100, 200, 300, 400, 500],
        "R_75":  [100, 150, 200, 250, 300],
        "R_100": [100, 200, 300, 400, 500],
    }
    # Keep the broker stop-loss this fraction below the stake so Deriv's own
    # commission deduction can't tip it over the limit.
    SL_STAKE_SAFETY = 0.9
    REQUEST_TIMEOUT = 30      # seconds

    def __init__(
        self,
        app_id: Optional[str] = None,
        api_token: Optional[str] = None,
        default_multiplier: int = DEFAULT_MULTIPLIER,
    ):
        self._app_id = app_id or os.environ.get("DERIV_APP_ID", "1089")
        self._api_token = api_token or os.environ.get("DERIV_API_TOKEN", "")
        self._default_multiplier = default_multiplier
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._req_id = 0
        self._connected_account: Optional[str] = None
        self._active_token: Optional[str] = None  # Currently authorized token
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._lock = asyncio.Lock()
        self._pending: dict[int, asyncio.Future] = {}
        self._reader_task: Optional[asyncio.Task] = None

    # ------------------------------------------------------------------
    # Sync wrappers
    # ------------------------------------------------------------------

    def _get_loop(self):
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

    def _run(self, coro):
        return self._get_loop().run_until_complete(coro)

    # ------------------------------------------------------------------
    # Connection & request plumbing
    # ------------------------------------------------------------------

    @property
    def _url(self) -> str:
        return f"{DERIV_WS_URL}?app_id={self._app_id}"

    async def _ensure_connected(self, token: Optional[str] = None) -> None:
        """Ensure a WebSocket connection is open and authorized.

        If ``token`` is provided, authorize with that token (per-account).
        Otherwise fall back to the constructor-level token (legacy env-based).

        Reconnects proactively if the connection has been idle too long
        (asyncio loop is paused between worker calls so pings stop).
        """
        if self._ws_is_open(self._ws) and not self._is_connection_stale():
            return

        # Close any stale connection first so we don't leak
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None
            self._active_token = None

        self._ws = await websockets.connect(
            self._url, ping_interval=30, ping_timeout=10, close_timeout=5,
        )
        self._reader_task = asyncio.create_task(self._read_loop())
        self._last_activity_ts = time.time()
        auth_token = token or self._api_token
        if auth_token:
            await self._send_and_wait({"authorize": auth_token})
            self._active_token = auth_token

    async def _read_loop(self) -> None:
        try:
            async for msg in self._ws:
                try:
                    data = json.loads(msg)
                except (ValueError, TypeError):
                    continue
                req_id = data.get("req_id")
                fut = self._pending.pop(req_id, None) if req_id is not None else None
                if fut and not fut.done():
                    fut.set_result(data)
        except websockets.exceptions.ConnectionClosed:
            logger.warning("Deriv WebSocket closed")
        except Exception as exc:
            logger.exception("Deriv reader loop error: %s", exc)

    async def _send_and_wait(self, payload: dict, timeout: float = REQUEST_TIMEOUT) -> dict:
        """Send a request and wait for the response with matching req_id."""
        async with self._lock:
            self._req_id += 1
            req_id = self._req_id
        payload["req_id"] = req_id
        fut = asyncio.get_event_loop().create_future()
        self._pending[req_id] = fut
        await self._ws.send(json.dumps(payload))
        self._last_activity_ts = time.time()
        try:
            resp = await asyncio.wait_for(fut, timeout=timeout)
            self._last_activity_ts = time.time()
            return resp
        except asyncio.TimeoutError:
            self._pending.pop(req_id, None)
            raise TimeoutError(
                f"Deriv response timed out after {timeout}s for req_id={req_id} "
                f"(action={payload.get('buy') and 'buy' or payload.get('sell') and 'sell' or list(payload.keys())[0]})"
            )

    # ------------------------------------------------------------------
    # BrokerClient Protocol implementation
    # ------------------------------------------------------------------

    def connect(self, account_id: str) -> bool:
        """Authorize the WebSocket session using the constructor token.

        For per-account tokens, use ``connect_with_token(login_id, token)``.
        """
        try:
            self._run(self._ensure_connected())
            self._connected_account = account_id
            logger.info("Deriv client connected (account=%s, app_id=%s)", account_id, self._app_id)
            return True
        except Exception as exc:
            logger.error("Deriv connection failed: %s", exc)
            return False

    # Treat any connection idle longer than this as stale (Deriv times out idle
    # connections, and our asyncio loop is paused between worker calls so pings
    # don't happen).
    CONNECTION_MAX_IDLE_SECS = 25

    @staticmethod
    def _ws_is_open(ws) -> bool:
        """Check if a websocket is still open (handles new + old library versions)."""
        if ws is None:
            return False
        # Newer websockets (≥12): use .state enum
        state = getattr(ws, "state", None)
        if state is not None:
            try:
                return getattr(state, "name", str(state)).upper() == "OPEN"
            except Exception:
                pass
        # Older websockets (<12): use .closed bool
        closed = getattr(ws, "closed", None)
        if closed is not None:
            return not closed
        return True  # If we can't tell, assume open

    def _is_connection_stale(self) -> bool:
        """Return True if the WS hasn't been used recently and is probably timed out.

        The asyncio loop is paused between worker calls, so pings can't keep
        the connection alive. Reconnect proactively rather than risk a hang.
        """
        if not hasattr(self, "_last_activity_ts"):
            return False
        return (time.time() - self._last_activity_ts) > self.CONNECTION_MAX_IDLE_SECS

    def connect_with_token(self, login_id: str, api_token: str) -> bool:
        """Authorize the WebSocket session with a specific Deriv API token.

        Use this for per-account credentials. If the connection is already
        authorized with a different token, it is re-authorized.
        """
        try:
            # If already connected with a different token, re-authorize
            if (
                self._active_token != api_token
                and self._ws_is_open(self._ws)
            ):
                self._run(self._send_and_wait({"authorize": api_token}))
                self._active_token = api_token
            else:
                self._run(self._ensure_connected(token=api_token))
            self._connected_account = login_id
            logger.info(
                "Deriv client connected with per-account token (login=%s, app_id=%s)",
                login_id, self._app_id,
            )
            return True
        except Exception as exc:
            logger.error("Deriv connection failed for login %s: %s", login_id, exc)
            return False

    # Translation factor: strategy provides position_size in "lots".
    # For Deriv multipliers we need a USD stake. This factor converts
    # lots -> stake. For V25 at typical $50-$200 accounts this lands
    # stakes in the $1-$10 range which is appropriate for demo.
    # TODO: replace with proper risk-based sizing using account balance + SL distance.
    LOT_TO_STAKE_FACTOR = 100.0
    MIN_STAKE_USD = 1.0  # Deriv multiplier minimum

    def _convert_volume_to_stake(self, position_size: float) -> float:
        """Translate strategy position_size (lots) -> Deriv USD stake.

        Strategies compute position_size in lots assuming CFD-style sizing.
        Deriv multipliers use a USD stake amount instead. We multiply by a
        constant factor and floor at Deriv's minimum stake.
        """
        if position_size >= self.MIN_STAKE_USD:
            # Already looks like a USD stake — pass through
            return round(position_size, 2)
        stake = round(position_size * self.LOT_TO_STAKE_FACTOR, 2)
        return max(stake, self.MIN_STAKE_USD)

    def _select_multiplier(self, instrument: str, price: float, sl: float) -> int:
        """Pick the largest allowed multiplier that keeps the stop-loss inside
        the stake.

        A multiplier contract auto-closes at a 100%-of-stake loss, so the
        strategy's stop only takes effect when it is TIGHTER than that auto
        stop-out — i.e. when multiplier * (sl_distance / price) <= 1. We pick the
        largest allowed multiplier satisfying that (with a safety margin) to keep
        capital efficiency high while letting Deriv accept the stop. If the stop
        is so wide that even the smallest multiplier can't contain it, we return
        the smallest (the auto stop-out then protects the position and the stop
        is clamped to the stake in _async_send_order).
        """
        allowed = sorted(self.ALLOWED_MULTIPLIERS.get(instrument, [])) or [
            self.SYMBOL_MULTIPLIER.get(instrument, self._default_multiplier)
        ]
        if not (price and price > 0 and sl and sl > 0):
            return allowed[0]
        sl_ratio = abs(price - sl) / price
        if sl_ratio <= 0:
            return allowed[-1]
        cap = self.SL_STAKE_SAFETY / sl_ratio
        fits = [m for m in allowed if m <= cap]
        return fits[-1] if fits else allowed[0]

    async def _async_send_order(
        self,
        instrument: str,
        direction: str,
        volume: float,
        price: float,
        sl: float,
        tp: float,
    ) -> OrderResult:
        await self._ensure_connected()

        contract_type = "MULTUP" if direction == "BUY" else "MULTDOWN"

        # Translate lots -> USD stake for Deriv multipliers
        stake = self._convert_volume_to_stake(volume)

        # Pick the multiplier from the stop distance so the broker stop-loss
        # stays within the stake (Deriv rejects a stop larger than the stake).
        multiplier = self._select_multiplier(instrument, price, sl)

        # Build the buy parameters for a multiplier contract
        buy_params: dict = {
            "buy": 1,
            "price": stake,  # Stake amount in account currency
            "parameters": {
                "amount": stake,
                "basis": "stake",
                "contract_type": contract_type,
                "currency": "USD",
                "symbol": instrument,
                "multiplier": multiplier,
            },
        }

        # SL / TP via limit_order — Deriv expects loss/profit amount in USD
        # Formula: loss_usd = stake * (price_move / entry_price) * multiplier
        limit_order = {}
        if sl and sl > 0 and price > 0:
            sl_distance = abs(price - sl)
            if sl_distance > 0:
                stop_loss_usd = round(stake * (sl_distance / price) * multiplier, 2)
                # Deriv caps the stop at the stake (max loss on a multiplier).
                # If the strategy stop is wider than the auto stop-out even at the
                # lowest multiplier, clamp so the order is accepted — the auto
                # stop-out then enforces the same max loss.
                max_sl = round(stake * self.SL_STAKE_SAFETY, 2)
                if stop_loss_usd > max_sl:
                    stop_loss_usd = max_sl
                if stop_loss_usd > 0:
                    limit_order["stop_loss"] = stop_loss_usd
        if tp and tp > 0 and price > 0:
            tp_distance = abs(tp - price)
            if tp_distance > 0:
                take_profit_usd = round(stake * (tp_distance / price) * multiplier, 2)
                if take_profit_usd > 0:
                    limit_order["take_profit"] = take_profit_usd
        if limit_order:
            buy_params["parameters"]["limit_order"] = limit_order

        try:
            resp = await self._send_and_wait(buy_params)
        except Exception as exc:
            return OrderResult(success=False, error_message=f"Deriv buy error: {exc}")

        if "error" in resp:
            err = resp["error"]
            return OrderResult(
                success=False,
                error_code=int(err.get("code", 0)) if str(err.get("code", 0)).isdigit() else 0,
                error_message=err.get("message", "Unknown Deriv error"),
            )

        buy = resp.get("buy", {})
        # Multiplier contracts don't have a single "fill price" — the
        # underlying spot at contract start is what counts. We use the
        # request price as the entry reference (no slippage on multipliers).
        return OrderResult(
            success=True,
            order_id=int(buy.get("contract_id", 0)),
            fill_price=float(price),
            volume=stake,
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
        return self._run(self._async_send_order(instrument, direction, volume, price, sl, tp))

    async def _async_modify_position(
        self, order_id: int, sl: Optional[float] = None, tp: Optional[float] = None,
    ) -> OrderResult:
        await self._ensure_connected()
        params: dict = {
            "contract_update": 1,
            "contract_id": order_id,
            "limit_order": {},
        }
        if sl is not None:
            params["limit_order"]["stop_loss"] = sl
        if tp is not None:
            params["limit_order"]["take_profit"] = tp
        try:
            resp = await self._send_and_wait(params)
        except Exception as exc:
            return OrderResult(success=False, error_message=f"Deriv modify error: {exc}")
        if "error" in resp:
            return OrderResult(success=False, error_message=resp["error"].get("message", ""))
        return OrderResult(success=True, order_id=order_id)

    def modify_position(self, order_id: int, sl: Optional[float] = None, tp: Optional[float] = None) -> OrderResult:
        return self._run(self._async_modify_position(order_id, sl, tp))

    async def _async_close_position(self, position_id: int) -> OrderResult:
        await self._ensure_connected()
        try:
            resp = await self._send_and_wait({"sell": position_id, "price": 0})
        except Exception as exc:
            return OrderResult(success=False, error_message=f"Deriv sell error: {exc}")
        if "error" in resp:
            return OrderResult(success=False, error_message=resp["error"].get("message", ""))
        sold = resp.get("sell", {})
        return OrderResult(
            success=True,
            order_id=position_id,
            fill_price=float(sold.get("sold_for", 0)),
        )

    def close_position_by_id(self, position_id: int) -> OrderResult:
        return self._run(self._async_close_position(position_id))

    async def _async_get_tick(self, instrument: str) -> Optional[dict]:
        """Fetch the most recent tick using ticks_history (non-subscribing)."""
        await self._ensure_connected()
        try:
            resp = await self._send_and_wait({
                "ticks_history": instrument,
                "count": 1,
                "end": "latest",
                "style": "ticks",
            })
        except Exception as exc:
            logger.warning("Deriv get_tick failed for %s: %s", instrument, exc)
            return None

        if "error" in resp:
            logger.warning("Deriv get_tick error for %s: %s", instrument, resp["error"])
            return None

        history = resp.get("history", {})
        prices = history.get("prices", [])
        times = history.get("times", [])
        if not prices:
            return None
        quote = float(prices[-1])
        # Deriv synthetic ticks don't separate bid/ask — use the same quote
        return {
            "bid": quote,
            "ask": quote,
            "time": int(times[-1]) if times else int(time.time()),
        }

    def get_symbol_info_tick(self, instrument: str) -> Optional[dict]:
        try:
            return self._run(self._async_get_tick(instrument))
        except Exception:
            return None

    async def _async_get_positions(self) -> list[dict]:
        await self._ensure_connected()
        try:
            resp = await self._send_and_wait({"portfolio": 1})
        except Exception:
            return []
        portfolio = resp.get("portfolio", {})
        contracts = portfolio.get("contracts", []) or []
        # Map Deriv portfolio shape to a generic positions shape
        out = []
        for c in contracts:
            out.append({
                "id": c.get("contract_id"),
                "symbol": c.get("symbol"),
                "type": c.get("contract_type"),  # MULTUP / MULTDOWN
                "volume": c.get("buy_price"),    # stake
                "open_price": c.get("buy_price"),
                "current_price": c.get("bid_price"),
                "profit": c.get("profit", 0),
            })
        return out

    def get_positions(self) -> list[dict]:
        return self._run(self._async_get_positions())

    # ------------------------------------------------------------------
    # Closed-contract lookups (used by PositionMonitor + reconciler)
    # ------------------------------------------------------------------

    async def _async_fetch_closed_contract(
        self, contract_id: int,
    ) -> Optional[ExitDetails]:
        """Fetch a contract's final state from Deriv.

        Uses ``proposal_open_contract`` which works for both still-open and
        recently-sold contracts. Returns None if the contract is still open
        or if Deriv refuses the request.
        """
        await self._ensure_connected()
        try:
            resp = await self._send_and_wait({
                "proposal_open_contract": 1,
                "contract_id": int(contract_id),
            })
        except Exception as exc:
            logger.warning(
                "Deriv proposal_open_contract failed for %s: %s",
                contract_id, exc,
            )
            return None
        if "error" in resp:
            # Common: "ContractIsSold" if too old / not retrievable
            err = resp["error"]
            logger.debug(
                "Deriv contract %s error: %s",
                contract_id, err.get("message", err),
            )
            return None
        c = resp.get("proposal_open_contract", {}) or {}
        if not c.get("is_sold"):
            return None  # Still open
        try:
            sell_price = float(c.get("sell_price", 0))
            buy_price = float(c.get("buy_price", 0))
            profit = float(c.get("profit", sell_price - buy_price))
            sell_time = int(c.get("sell_time") or c.get("date_expiry") or time.time())
            # For exit_price, prefer the underlying spot at sell so it's
            # comparable to our entry_price (which is the underlying spot
            # at buy). Fall back to sell_price (contract value) if Deriv
            # didn't return a spot.
            exit_spot = (
                c.get("sell_spot")
                or c.get("exit_tick")
                or c.get("sell_spot_display_value")
                or c.get("exit_tick_display_value")
            )
            try:
                exit_price = float(exit_spot) if exit_spot is not None else sell_price
            except (TypeError, ValueError):
                exit_price = sell_price
            return ExitDetails(
                broker_order_id=int(contract_id),
                exit_price=exit_price,
                profit_loss=profit,
                closed_at=datetime.fromtimestamp(sell_time, tz=timezone.utc),
                status=c.get("status", "closed") or "closed",
                raw=c,
            )
        except (TypeError, ValueError) as exc:
            logger.warning(
                "Deriv: failed to parse contract %s: %s",
                contract_id, exc,
            )
            return None

    def fetch_closed_contract(self, contract_id: int) -> Optional[ExitDetails]:
        """Sync wrapper for the close-state fetcher."""
        try:
            return self._run(self._async_fetch_closed_contract(contract_id))
        except Exception as exc:
            logger.warning(
                "Deriv fetch_closed_contract %s failed: %s", contract_id, exc,
            )
            return None

    async def _async_fetch_profit_table(
        self,
        date_from: Optional[datetime] = None,
        date_to: Optional[datetime] = None,
        limit: int = 200,
    ) -> list[ExitDetails]:
        """Fetch historical closed contracts via ``profit_table``.

        Used by the reconciler to backfill exits for contracts that are too
        old for ``proposal_open_contract``. Each row maps to ExitDetails.
        """
        await self._ensure_connected()
        params: dict = {
            "profit_table": 1,
            "description": 1,
            "limit": int(limit),
        }
        if date_from is not None:
            params["date_from"] = int(date_from.timestamp())
        if date_to is not None:
            params["date_to"] = int(date_to.timestamp())
        try:
            resp = await self._send_and_wait(params)
        except Exception as exc:
            logger.warning("Deriv profit_table failed: %s", exc)
            return []
        if "error" in resp:
            logger.warning("Deriv profit_table error: %s", resp["error"])
            return []
        transactions = (resp.get("profit_table", {}) or {}).get("transactions", []) or []
        out: list[ExitDetails] = []
        for t in transactions:
            try:
                cid = t.get("contract_id")
                if cid is None:
                    continue
                sell_price = float(t.get("sell_price", 0))
                buy_price = float(t.get("buy_price", 0))
                profit = sell_price - buy_price
                sell_time = int(t.get("sell_time") or time.time())
                out.append(ExitDetails(
                    broker_order_id=int(cid),
                    exit_price=sell_price,
                    profit_loss=profit,
                    closed_at=datetime.fromtimestamp(sell_time, tz=timezone.utc),
                    status="closed",
                    raw=t,
                ))
            except (TypeError, ValueError) as exc:
                logger.debug("Deriv profit_table: skipping malformed row %s: %s", t, exc)
                continue
        return out

    def fetch_profit_table(
        self,
        date_from: Optional[datetime] = None,
        date_to: Optional[datetime] = None,
        limit: int = 200,
    ) -> list[ExitDetails]:
        """Sync wrapper for the profit-table fetcher."""
        try:
            return self._run(self._async_fetch_profit_table(date_from, date_to, limit))
        except Exception as exc:
            logger.warning("Deriv fetch_profit_table failed: %s", exc)
            return []
