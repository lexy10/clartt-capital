"""Candle streaming — builds 1m candles from Deriv live ticks.

Subscribes to Deriv tick stream for each symbol, builds in-progress 1m
candles from real-time prices, and publishes updates to Redis every
PUBLISH_INTERVAL seconds.
"""

import asyncio
import json
import logging
import random
import threading
import time
from datetime import datetime, timezone
from typing import Optional

from redis import Redis

logger = logging.getLogger("execution_engine.candles.streamer")

PUBLISH_INTERVAL = 1  # seconds between Redis publishes of in-progress candles
MAX_SUBSCRIBE_RETRIES = 5  # max retries per symbol when subscribing
RETRY_BASE_DELAY = 3  # base delay in seconds for exponential backoff


class InProgressCandle:
    """Tracks an in-progress 1m candle built from price ticks."""

    __slots__ = ("instrument", "open", "high", "low", "close", "volume", "minute_start")

    def __init__(self, instrument: str, price: float, minute_start: datetime):
        self.instrument = instrument
        self.open = price
        self.high = price
        self.low = price
        self.close = price
        self.volume = 0
        self.minute_start = minute_start

    def update(self, price: float) -> None:
        self.high = max(self.high, price)
        self.low = min(self.low, price)
        self.close = price
        self.volume += 1

    def to_dict(self, completed: bool = False) -> dict:
        return {
            "instrument": self.instrument,
            "timeframe": "1m",
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
            "timestamp": self.minute_start.isoformat(),
            "completed": completed,
        }


def _floor_to_minute(dt: datetime) -> datetime:
    return dt.replace(second=0, microsecond=0)


class CandleStreamer:
    """Streams live ticks from Deriv WebSocket API and builds 1m candles.

    Subscribes to tick updates for each symbol, accumulates them into
    in-progress 1m candles, and publishes to Redis `candles:updates`
    throttled to PUBLISH_INTERVAL per instrument.

    Includes retry logic with exponential backoff — if a subscription
    fails, it forces a Deriv reconnect and retries up to MAX_SUBSCRIBE_RETRIES
    times before giving up on that symbol.
    """

    MAX_BACKOFF = 30

    def __init__(self, deriv_client, redis_url: str = "redis://localhost:6379"):
        self._deriv = deriv_client
        self._redis = Redis.from_url(redis_url, decode_responses=True)
        self._sub_ids: dict[str, list[str]] = {}  # account_id -> [sub_id, ...]
        self._candles: dict[str, InProgressCandle] = {}  # symbol -> candle
        self._last_publish: dict[str, float] = {}  # symbol -> timestamp
        self._symbol_map: dict[str, str] = {}

    async def start_stream(
        self, account_id: str, symbols: list[str], symbol_map: dict[str, str] | None = None
    ) -> None:
        """Start streaming ticks for symbols via Deriv API.

        Retries failed subscriptions with exponential backoff and forces
        a Deriv reconnect between retry rounds.
        """
        await self.stop_stream(account_id)

        self._symbol_map = symbol_map or {}
        sub_ids = []
        failed_symbols = list(symbols)

        for attempt in range(MAX_SUBSCRIBE_RETRIES):
            if not failed_symbols:
                break

            if attempt > 0:
                delay = min(RETRY_BASE_DELAY * (2 ** (attempt - 1)), self.MAX_BACKOFF)
                logger.info(
                    "Retry %d/%d for %d symbol(s) in %ds...",
                    attempt + 1, MAX_SUBSCRIBE_RETRIES, len(failed_symbols), delay,
                )
                await asyncio.sleep(delay)
                # Force reconnect before retrying
                try:
                    await self._deriv.force_reconnect()
                except Exception as exc:
                    logger.error("Reconnect failed on retry %d: %s", attempt + 1, exc)
                    continue

            still_failed = []
            for symbol in failed_symbols:
                try:
                    sub_id = await self._deriv.subscribe_ticks(
                        symbol=symbol,
                        callback=self._make_tick_handler(symbol),
                    )
                    if sub_id:
                        sub_ids.append(sub_id)
                        logger.info("Subscribed to Deriv ticks for %s (sub_id=%s)", symbol, sub_id)
                    else:
                        still_failed.append(symbol)
                except Exception as exc:
                    logger.error("Error subscribing to %s: %s", symbol, exc)
                    still_failed.append(symbol)

            failed_symbols = still_failed

        if failed_symbols:
            logger.error(
                "Failed to subscribe to %d symbol(s) after %d retries: %s",
                len(failed_symbols), MAX_SUBSCRIBE_RETRIES, failed_symbols,
            )

        self._sub_ids[account_id] = sub_ids
        logger.info(
            "Started Deriv candle stream for account %s, symbols: %s (%d subscriptions)",
            account_id, symbols, len(sub_ids),
        )

    async def stop_stream(self, account_id: str) -> None:
        """Stop streaming ticks for an account."""
        sub_ids = self._sub_ids.pop(account_id, [])
        for sub_id in sub_ids:
            try:
                await self._deriv.unsubscribe(sub_id)
            except Exception as exc:
                logger.warning("Error unsubscribing %s: %s", sub_id, exc)
        logger.info("Stopped Deriv candle stream for account %s", account_id)

    def get_status(self) -> dict:
        """Return current streaming status for health checks."""
        total_subs = sum(len(subs) for subs in self._sub_ids.values())
        active_symbols = list(self._symbol_map.keys()) if self._symbol_map else []
        return {
            "active": total_subs > 0,
            "subscription_count": total_subs,
            "symbols": active_symbols,
            "accounts": list(self._sub_ids.keys()),
        }

    def _make_tick_handler(self, broker_symbol: str):
        """Create an async tick handler for a specific symbol."""
        instrument = self._symbol_map.get(broker_symbol, broker_symbol)

        async def on_tick(tick: dict) -> None:
            try:
                price = float(tick.get("quote", 0))
                epoch = tick.get("epoch", 0)
                if price <= 0:
                    return

                tick_dt = datetime.fromtimestamp(epoch, tz=timezone.utc)
                current_minute = _floor_to_minute(tick_dt)

                existing = self._candles.get(broker_symbol)
                if existing and existing.minute_start == current_minute:
                    existing.update(price)
                else:
                    # Publish final snapshot of the completed minute before rolling over
                    if existing:
                        try:
                            msg = json.dumps(existing.to_dict(completed=True))
                            self._redis.publish("candles:updates", msg)
                            logger.debug(
                                "Published final candle for %s minute %s (close: %s)",
                                instrument, existing.minute_start, existing.close,
                            )
                        except Exception as e:
                            logger.warning("Failed to publish final candle for %s: %s", instrument, e)
                    self._candles[broker_symbol] = InProgressCandle(
                        instrument, price, current_minute
                    )
                    # Reset throttle so the new minute's first publish isn't delayed
                    self._last_publish[instrument] = 0

                # Throttled publish to Redis
                now = time.time()
                last = self._last_publish.get(instrument, 0)
                if now - last >= PUBLISH_INTERVAL:
                    candle = self._candles[broker_symbol]
                    try:
                        msg = json.dumps(candle.to_dict())
                        subs = self._redis.publish("candles:updates", msg)
                        self._last_publish[instrument] = now
                        logger.debug(
                            "Published candle for %s (subscribers: %d, close: %s)",
                            instrument, subs, candle.close,
                        )
                    except Exception as e:
                        logger.warning("Failed to publish candle for %s: %s", instrument, e)
            except Exception as e:
                logger.warning("Tick handler error for %s: %s", broker_symbol, e)

        return on_tick



class StubCandleStreamer:
    """Stub streamer for demo mode — simulates tick-based 1m candle updates."""

    def __init__(self, redis_url: str = "redis://localhost:6379"):
        self._redis = Redis.from_url(redis_url, decode_responses=True)
        self._active: dict[str, bool] = {}
        self._threads: dict[str, threading.Thread] = {}

    async def start_stream(
        self, account_id: str, symbols: list[str], symbol_map: dict[str, str] | None = None
    ) -> None:
        await self.stop_stream(account_id)
        self._active[account_id] = True
        t = threading.Thread(
            target=self._mock_stream, args=(account_id, symbols), daemon=True
        )
        self._threads[account_id] = t
        t.start()
        logger.info("StubCandleStreamer: started mock stream for %s", account_id)

    async def stop_stream(self, account_id: str) -> None:
        self._active[account_id] = False
        self._threads.pop(account_id, None)
        logger.info("StubCandleStreamer: stopped mock stream for %s", account_id)

    def get_status(self) -> dict:
        """Return current streaming status for health checks."""
        active_accounts = [k for k, v in self._active.items() if v]
        return {
            "active": len(active_accounts) > 0,
            "subscription_count": len(active_accounts),
            "symbols": [],
            "accounts": active_accounts,
        }

    def _mock_stream(self, account_id: str, symbols: list[str]) -> None:
        prices: dict[str, float] = {s: 39000.0 + random.uniform(-500, 500) for s in symbols}
        candles: dict[str, InProgressCandle] = {}

        while self._active.get(account_id, False):
            now = datetime.now(timezone.utc)
            current_minute = _floor_to_minute(now)

            for symbol in symbols:
                tick = random.uniform(-5, 5)
                prices[symbol] = round(prices[symbol] + tick, 2)
                price = prices[symbol]

                existing = candles.get(symbol)
                if existing and existing.minute_start == current_minute:
                    existing.update(price)
                else:
                    candles[symbol] = InProgressCandle(symbol, price, current_minute)

                try:
                    msg = json.dumps(candles[symbol].to_dict())
                    self._redis.publish("candles:updates", msg)
                except Exception as e:
                    logger.warning("StubCandleStreamer publish failed: %s", e)

            time.sleep(PUBLISH_INTERVAL)
