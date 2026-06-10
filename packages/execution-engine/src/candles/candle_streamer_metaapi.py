"""Legacy MetaAPI candle streamer — kept as fallback when Deriv is not configured."""

import asyncio
import json
import logging
import time
from datetime import datetime, timezone

from redis import Redis

logger = logging.getLogger("execution_engine.candles.streamer_metaapi")

PUBLISH_INTERVAL = 2


def _floor_to_minute(dt: datetime) -> datetime:
    return dt.replace(second=0, microsecond=0)


class CandleStreamerMetaApi:
    """MetaAPI-based candle streamer (legacy fallback)."""

    MAX_BACKOFF = 30

    def __init__(self, provisioner, redis_url: str = "redis://localhost:6379"):
        self._provisioner = provisioner
        self._redis = Redis.from_url(redis_url, decode_responses=True)
        self._active_streams: dict[str, asyncio.Task] = {}
        self._stop_events: dict[str, asyncio.Event] = {}

    async def start_stream(self, account_id: str, symbols: list[str], symbol_map: dict[str, str] | None = None) -> None:
        if account_id in self._active_streams:
            await self.stop_stream(account_id)

        self._symbol_map = symbol_map or {}
        stop_event = asyncio.Event()
        self._stop_events[account_id] = stop_event

        task = asyncio.create_task(self._stream_loop(account_id, symbols, stop_event))
        self._active_streams[account_id] = task
        logger.info("Started MetaAPI candle stream for account %s", account_id)

    async def stop_stream(self, account_id: str) -> None:
        stop_event = self._stop_events.pop(account_id, None)
        if stop_event:
            stop_event.set()
        task = self._active_streams.pop(account_id, None)
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def _stream_loop(self, account_id: str, symbols: list[str], stop_event: asyncio.Event) -> None:
        backoff = 1
        while not stop_event.is_set():
            try:
                connection = self._provisioner._rpc_connections.get(account_id)
                if connection is None:
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, self.MAX_BACKOFF)
                    continue

                streaming_conn = await self._get_streaming_connection(account_id)
                if streaming_conn is None:
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, self.MAX_BACKOFF)
                    continue

                for symbol in symbols:
                    try:
                        await streaming_conn.subscribe_to_market_data(
                            symbol, [{"type": "candles", "timeframe": "1m"}]
                        )
                    except Exception as exc:
                        logger.warning("Failed to subscribe to %s: %s", symbol, exc)

                backoff = 1
                while not stop_event.is_set():
                    await asyncio.sleep(1)
                    if getattr(streaming_conn, '_closed', True):
                        break
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("Stream error for %s: %s", account_id, exc)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, self.MAX_BACKOFF)

    async def _get_streaming_connection(self, account_id: str):
        try:
            api = await self._provisioner._ensure_api()
            account = await api.metatrader_account_api.get_account(account_id)
            connection = account.get_streaming_connection()
            await connection.connect()
            await asyncio.wait_for(connection.wait_synchronized(), timeout=60)

            listener = _MetaApiCandleListener(self._redis, account_id, self._symbol_map)
            connection.add_synchronization_listener(listener)
            return connection
        except Exception as exc:
            logger.error("Failed to create streaming connection for %s: %s", account_id, exc)
            return None


class _MetaApiCandleListener:
    """MetaAPI synchronization listener that publishes 1m candles to Redis."""

    def __init__(self, redis_client, account_id, symbol_map):
        self._redis = redis_client
        self._account_id = account_id
        self._symbol_map = symbol_map or {}
        self._last_publish: dict[str, float] = {}
        self._instance_for: dict[str, int] = {}

    def _publish_candle(self, candle_dict: dict) -> None:
        instrument = candle_dict["instrument"]
        now = time.time()
        last = self._last_publish.get(instrument, 0)
        if now - last < PUBLISH_INTERVAL:
            return
        try:
            msg = json.dumps(candle_dict)
            self._redis.publish("candles:updates", msg)
            self._last_publish[instrument] = now
        except Exception as e:
            logger.warning("Failed to publish candle for %s: %s", instrument, e)

    async def on_candles_updated(self, instance_index, candles, **kwargs):
        for candle in candles:
            try:
                _get = candle.get if isinstance(candle, dict) else lambda k, d=None: getattr(candle, k, d)
                tf = _get("timeframe", "1m")
                if tf != "1m":
                    continue
                broker_sym = _get("symbol", "")
                instrument = self._symbol_map.get(broker_sym, broker_sym)

                prev_instance = self._instance_for.get(instrument)
                if prev_instance == 1 and instance_index == 0:
                    continue
                self._instance_for[instrument] = instance_index

                raw_time = _get("time", None)
                if raw_time is None:
                    candle_dt = datetime.now(timezone.utc)
                elif isinstance(raw_time, datetime):
                    candle_dt = raw_time if raw_time.tzinfo else raw_time.replace(tzinfo=timezone.utc)
                else:
                    candle_dt = datetime.fromisoformat(str(raw_time).replace("Z", "+00:00"))

                candle_dict = {
                    "instrument": instrument,
                    "timeframe": "1m",
                    "open": _get("open", 0),
                    "high": _get("high", 0),
                    "low": _get("low", 0),
                    "close": _get("close", 0),
                    "volume": _get("tickVolume", _get("volume", 0)),
                    "timestamp": _floor_to_minute(candle_dt).isoformat(),
                }
                self._publish_candle(candle_dict)
            except Exception as e:
                logger.warning("Failed to process candle update: %s", e)

    async def on_broker_connection_status_changed(self, instance_index, connected):
        pass

    async def on_health_status(self, instance_index, status):
        pass

    async def on_symbol_specifications_updated(self, instance_index, specifications, removed_symbols):
        pass

    async def on_symbol_specification_updated(self, instance_index, specification):
        pass
