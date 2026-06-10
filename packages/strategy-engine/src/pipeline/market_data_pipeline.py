"""Market Data Pipeline for ingesting ticks and aggregating candles."""

import logging
from collections import defaultdict
from datetime import datetime, timezone
from typing import Callable, Optional

from ..models.candle import Candle
from ..models.tick import Tick
from ..models.timeframe import Timeframe

logger = logging.getLogger(__name__)

# Timeframe durations in seconds
TIMEFRAME_SECONDS: dict[Timeframe, int] = {
    Timeframe.ONE_MINUTE: 60,
    Timeframe.FIVE_MINUTES: 300,
    Timeframe.FIFTEEN_MINUTES: 900,
    Timeframe.THIRTY_MINUTES: 1800,
    Timeframe.ONE_HOUR: 3600,
    Timeframe.FOUR_HOURS: 14400,
    Timeframe.ONE_DAY: 86400,
}


def _get_window_start(timestamp: datetime, timeframe: Timeframe) -> datetime:
    """Compute the start of the timeframe window containing the given timestamp."""
    seconds = TIMEFRAME_SECONDS[timeframe]
    epoch = int(timestamp.replace(tzinfo=timezone.utc).timestamp()) if timestamp.tzinfo is None else int(timestamp.timestamp())
    window_epoch = (epoch // seconds) * seconds
    return datetime.fromtimestamp(window_epoch, tz=timezone.utc)


class MarketDataPipeline:
    """Ingests tick data, validates it, aggregates into candles, and serves candle queries.

    Supports both live broker feeds and simulated data sources.
    Supports time manipulation for replay and backtesting via an optional time_fn.
    """

    def __init__(self, time_fn: Optional[Callable[[], datetime]] = None) -> None:
        # time_fn allows injecting a custom clock for replay/backtesting
        self._time_fn = time_fn or (lambda: datetime.now(timezone.utc))

        # Accumulated ticks per (instrument, timeframe, window_start_iso)
        self._tick_buffers: dict[str, list[Tick]] = defaultdict(list)

        # Completed candles stored per (instrument, timeframe) as a list sorted by time
        self._candles: dict[str, list[Candle]] = defaultdict(list)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def validate_tick(self, tick: Tick) -> bool:
        """Return True if the tick is valid, False otherwise.

        Rejects ticks with:
        - missing instrument
        - price <= 0
        - volume < 0
        - missing or unparseable timestamp
        """
        if not tick.instrument or not tick.instrument.strip():
            logger.warning("Invalid tick: missing instrument")
            return False
        if tick.price <= 0:
            logger.warning("Invalid tick: price must be positive, got %s", tick.price)
            return False
        if tick.volume < 0:
            logger.warning("Invalid tick: volume must be non-negative, got %s", tick.volume)
            return False
        if not tick.timestamp or not tick.timestamp.strip():
            logger.warning("Invalid tick: missing timestamp")
            return False
        try:
            datetime.fromisoformat(tick.timestamp)
        except (ValueError, TypeError):
            logger.warning("Invalid tick: unparseable timestamp '%s'", tick.timestamp)
            return False
        return True

    def ingest_tick(self, tick: Tick) -> None:
        """Receive and validate a raw tick, then buffer it for candle aggregation.

        Invalid ticks are logged and skipped without halting the pipeline.
        """
        if not self.validate_tick(tick):
            return

        ts = datetime.fromisoformat(tick.timestamp)

        for timeframe in Timeframe:
            window_start = _get_window_start(ts, timeframe)
            key = self._buffer_key(tick.instrument, timeframe, window_start)
            self._tick_buffers[key].append(tick)

    def aggregate_candle(self, ticks: list[Tick], timeframe: Timeframe) -> Candle:
        """Aggregate a list of ticks into a single candle.

        open  = first tick price
        high  = max tick price
        low   = min tick price
        close = last tick price
        volume = sum of tick volumes
        """
        if not ticks:
            raise ValueError("Cannot aggregate an empty tick list")

        first = ticks[0]
        ts = datetime.fromisoformat(first.timestamp)
        window_start = _get_window_start(ts, timeframe)

        prices = [t.price for t in ticks]
        return Candle(
            instrument=first.instrument,
            timeframe=timeframe,
            open=prices[0],
            high=max(prices),
            low=min(prices),
            close=prices[-1],
            volume=sum(t.volume for t in ticks),
            timestamp=window_start.isoformat(),
        )

    def flush_candles(self, instrument: str, timeframe: Timeframe) -> list[Candle]:
        """Aggregate all buffered ticks for the given instrument/timeframe into candles.

        Completed candles are moved to the candle store and returned.
        This is useful for forcing aggregation at the end of a replay or backtest.
        """
        prefix = f"{instrument}:{timeframe.value}:"
        keys_to_flush = [k for k in self._tick_buffers if k.startswith(prefix)]
        new_candles: list[Candle] = []

        for key in sorted(keys_to_flush):
            ticks = self._tick_buffers.pop(key)
            if ticks:
                candle = self.aggregate_candle(ticks, timeframe)
                store_key = self._store_key(instrument, timeframe)
                self._candles[store_key].append(candle)
                new_candles.append(candle)

        return new_candles

    def flush_all(self) -> list[Candle]:
        """Flush all buffered ticks across all instruments and timeframes."""
        all_candles: list[Candle] = []
        keys = list(self._tick_buffers.keys())
        for key in keys:
            parts = key.split(":")
            instrument = parts[0]
            tf_value = parts[1]
            timeframe = Timeframe(tf_value)
            ticks = self._tick_buffers.pop(key)
            if ticks:
                candle = self.aggregate_candle(ticks, timeframe)
                store_key = self._store_key(instrument, timeframe)
                self._candles[store_key].append(candle)
                all_candles.append(candle)
        return all_candles

    def get_candles(self, instrument: str, timeframe: Timeframe, count: int) -> list[Candle]:
        """Retrieve the most recent *count* candles for the given instrument and timeframe."""
        store_key = self._store_key(instrument, timeframe)
        candles = self._candles.get(store_key, [])
        return candles[-count:] if count < len(candles) else list(candles)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _buffer_key(instrument: str, timeframe: Timeframe, window_start: datetime) -> str:
        return f"{instrument}:{timeframe.value}:{window_start.isoformat()}"

    @staticmethod
    def _store_key(instrument: str, timeframe: Timeframe) -> str:
        return f"{instrument}:{timeframe.value}"
