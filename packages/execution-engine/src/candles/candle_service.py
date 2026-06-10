"""Candle data service — fetches historical candles via Deriv API or MetaAPI fallback."""

import asyncio
import logging
import random
from datetime import datetime, timedelta, timezone
from typing import Optional

from .models import CandleResponse, HistoricalCandleRequest
from .deriv_data_client import DerivDataClient, GRANULARITY_MAP, DERIV_BATCH_LIMIT

logger = logging.getLogger("execution_engine.candles")


class CandleService:
    """Fetches historical candles using Deriv WebSocket API."""

    def __init__(self, deriv_client: DerivDataClient):
        self._deriv = deriv_client

    async def get_historical(self, request: HistoricalCandleRequest) -> list[CandleResponse]:
        """Fetch historical candles from Deriv API.

        Paginates through the requested time range in batches of up to 5000
        candles per call, moving forward through time.
        """
        granularity = GRANULARITY_MAP.get(request.timeframe)
        if granularity is None:
            logger.error("Unsupported timeframe: %s", request.timeframe)
            return []

        start_dt = datetime.fromisoformat(request.start_date.replace("Z", "+00:00"))
        end_dt = datetime.fromisoformat(request.end_date.replace("Z", "+00:00"))
        if start_dt.tzinfo is None:
            start_dt = start_dt.replace(tzinfo=timezone.utc)
        if end_dt.tzinfo is None:
            end_dt = end_dt.replace(tzinfo=timezone.utc)

        start_epoch = int(start_dt.timestamp())
        end_epoch = int(end_dt.timestamp())

        all_results: list[CandleResponse] = []

        # Deriv's ticks_history returns up to `count` candles anchored to
        # `end` (i.e. the LAST N candles before `end`).  To paginate
        # correctly we move `end` backward through time, collecting batches
        # from newest to oldest, then sort at the end.
        cursor_end = end_epoch

        while cursor_end > start_epoch:
            try:
                candles = await self._deriv.get_historical_candles(
                    symbol=request.broker_symbol,
                    granularity=granularity,
                    start=start_epoch,
                    end=cursor_end,
                    count=DERIV_BATCH_LIMIT,
                )
            except Exception as exc:
                logger.error(
                    "Deriv historical candles failed for %s %s (cursor_end=%d): %s",
                    request.broker_symbol, request.timeframe, cursor_end, exc,
                )
                break

            if not candles:
                logger.info(
                    "No more candles from Deriv for %s %s before %s",
                    request.broker_symbol, request.timeframe,
                    datetime.fromtimestamp(cursor_end, tz=timezone.utc).isoformat(),
                )
                break

            batch_count = 0
            earliest_epoch = cursor_end
            for c in candles:
                epoch = c.get("epoch", 0)
                if epoch < start_epoch:
                    continue
                if epoch > end_epoch:
                    continue

                candle_dt = datetime.fromtimestamp(epoch, tz=timezone.utc)
                # Floor to the minute — Deriv sometimes returns epochs
                # with non-zero seconds for synthetic indices
                candle_dt = candle_dt.replace(second=0, microsecond=0)
                all_results.append(CandleResponse(
                    instrument=request.broker_symbol,
                    timeframe=request.timeframe,
                    open=float(c.get("open", 0)),
                    high=float(c.get("high", 0)),
                    low=float(c.get("low", 0)),
                    close=float(c.get("close", 0)),
                    volume=0,  # Deriv doesn't provide volume for most instruments
                    timestamp=candle_dt.isoformat(),
                ))
                batch_count += 1
                if epoch < earliest_epoch:
                    earliest_epoch = epoch

            logger.info(
                "Fetched %d candles from Deriv for %s %s (cursor_end=%s)",
                batch_count, request.broker_symbol, request.timeframe,
                datetime.fromtimestamp(cursor_end, tz=timezone.utc).strftime("%Y-%m-%d %H:%M"),
            )

            # If we got fewer than the limit, all data in the range is fetched
            if len(candles) < DERIV_BATCH_LIMIT:
                break

            # Move cursor_end backward to just before the earliest candle
            if earliest_epoch >= cursor_end:
                logger.warning("Cursor stuck at %d, breaking", cursor_end)
                break
            cursor_end = earliest_epoch - 1

            # Rate limit
            await asyncio.sleep(0.3)

        # Sort chronologically (we collected newest-first)
        all_results.sort(key=lambda c: c.timestamp)

        logger.info(
            "Total: %d candles from Deriv for %s %s",
            len(all_results), request.broker_symbol, request.timeframe,
        )
        return all_results


class StubCandleService:
    """Returns mock historical candles for demo mode."""

    async def get_historical(self, request: HistoricalCandleRequest) -> list[CandleResponse]:
        start = datetime.fromisoformat(request.start_date.replace("Z", "+00:00"))
        end = datetime.fromisoformat(request.end_date.replace("Z", "+00:00"))

        tf_minutes = {
            "1m": 1, "5m": 5, "15m": 15, "30m": 30,
            "1h": 60, "4h": 240, "1d": 1440,
        }
        interval = timedelta(minutes=tf_minutes.get(request.timeframe, 1))

        results = []
        current = start
        price = 39000.0

        while current < end and len(results) < 5000:
            change = random.uniform(-50, 50)
            o = round(price, 2)
            c = round(price + change, 2)
            h = round(max(o, c) + random.uniform(0, 20), 2)
            l = round(min(o, c) - random.uniform(0, 20), 2)
            v = round(random.uniform(50, 500), 0)
            results.append(CandleResponse(
                instrument=request.broker_symbol,
                timeframe=request.timeframe,
                open=o, high=h, low=l, close=c, volume=v,
                timestamp=current.isoformat(),
            ))
            price = c
            current += interval

        return results
