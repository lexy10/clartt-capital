"""Legacy MetaAPI candle service — kept as fallback when Deriv is not configured."""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from .models import CandleResponse, HistoricalCandleRequest

logger = logging.getLogger("execution_engine.candles")

TIMEFRAME_MAP = {
    "1m": "1m", "5m": "5m", "15m": "15m", "30m": "30m",
    "1h": "1h", "4h": "4h", "1d": "1d",
}
METAAPI_BATCH_LIMIT = 1000


class CandleServiceMetaApi:
    """Fetches historical candles using MetatraderAccount.get_historical_candles()."""

    def __init__(self, provisioner):
        self._provisioner = provisioner

    async def get_historical(self, request: HistoricalCandleRequest) -> list[CandleResponse]:
        try:
            account = await self._provisioner.get_account_object(request.account_id)
        except Exception as exc:
            logger.error("Failed to get account object for %s: %s", request.account_id, exc)
            return []

        tf = TIMEFRAME_MAP.get(request.timeframe, request.timeframe)
        start_time = datetime.fromisoformat(request.start_date.replace("Z", "+00:00"))
        end_time = datetime.fromisoformat(request.end_date.replace("Z", "+00:00"))

        if start_time.tzinfo is None:
            start_time = start_time.replace(tzinfo=timezone.utc)
        if end_time.tzinfo is None:
            end_time = end_time.replace(tzinfo=timezone.utc)

        all_results: list[CandleResponse] = []
        cursor = end_time

        while cursor > start_time:
            try:
                raw_candles = await asyncio.wait_for(
                    account.get_historical_candles(
                        request.broker_symbol, tf,
                        start_time=cursor, limit=METAAPI_BATCH_LIMIT,
                    ),
                    timeout=300,
                )
            except asyncio.TimeoutError:
                logger.warning("get_historical_candles timed out for %s %s", request.broker_symbol, tf)
                break
            except Exception as exc:
                logger.error("get_historical_candles failed for %s %s: %s", request.broker_symbol, tf, exc)
                break

            if not raw_candles:
                break

            batch = []
            oldest_time = cursor
            for c in raw_candles:
                try:
                    ts = c.get("time", c.get("timestamp", ""))
                    if isinstance(ts, datetime):
                        candle_dt = ts
                        ts = ts.isoformat()
                    else:
                        candle_dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))

                    if candle_dt.tzinfo is None:
                        candle_dt = candle_dt.replace(tzinfo=timezone.utc)
                    if candle_dt < start_time:
                        continue

                    batch.append(CandleResponse(
                        instrument=request.broker_symbol,
                        timeframe=request.timeframe,
                        open=float(c.get("open", 0)),
                        high=float(c.get("high", 0)),
                        low=float(c.get("low", 0)),
                        close=float(c.get("close", 0)),
                        volume=float(c.get("tickVolume", c.get("volume", 0))),
                        timestamp=str(ts),
                    ))
                    if candle_dt < oldest_time:
                        oldest_time = candle_dt
                except Exception as exc:
                    logger.warning("Skipping malformed candle: %s", exc)

            all_results.extend(batch)
            fetched = len(raw_candles)

            if fetched < METAAPI_BATCH_LIMIT:
                break
            if oldest_time >= cursor:
                break
            cursor = oldest_time - timedelta(seconds=1)
            await asyncio.sleep(0.3)

        all_results.sort(key=lambda c: c.timestamp)
        logger.info("Total: %d candles for %s %s (MetaAPI)", len(all_results), request.broker_symbol, tf)
        return all_results
