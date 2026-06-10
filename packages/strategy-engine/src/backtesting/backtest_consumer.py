"""Backtest consumer that processes backtest requests from Redis streams.

Consumes messages from the backtest:requests stream, executes backtests
via BacktestEngine, and publishes results/status updates back to Redis.
"""

import json
import logging
import os
import threading
from datetime import datetime

import requests
from redis import Redis

from src.backtesting.backtest_engine import BacktestEngine
from src.models.backtest_messages import (
    BacktestResultMessage,
    BacktestStatusMessage,
)
from src.models.backtesting import BacktestParams, BacktestResult, InstrumentSpecs
from src.models.candle import Candle
from src.models.strategy_config import StrategyConfig

logger = logging.getLogger("strategy_engine")


class BacktestConsumer:
    """Consumes backtest requests from Redis stream and executes them."""

    def __init__(self, redis_client: Redis, backtest_engine: BacktestEngine, backend_url: str = "http://backend:3000"):
        self.redis = redis_client
        self.engine = backtest_engine
        self.backend_url = backend_url
        self.running = False
        self.stream_key = "backtest:requests"
        self.group_name = "strategy-engine"
        self.consumer_name = "backtest-consumer"
        self.result_stream = "backtest:results"
        self.status_channel = "backtest:status"
        self._thread: threading.Thread | None = None
        self._backtest_result_high_water = int(
            os.environ.get("BACKTEST_RESULT_HIGH_WATER_MARK", "50")
        )

    def start(self) -> None:
        """Create consumer group and start polling loop in a daemon thread."""
        try:
            self.redis.xgroup_create(
                name=self.stream_key,
                groupname=self.group_name,
                id="0",
                mkstream=True,
            )
            logger.info("Created consumer group '%s' on '%s'", self.group_name, self.stream_key)
        except Exception as e:
            # BUSYGROUP means the group already exists — that's fine
            if "BUSYGROUP" in str(e):
                logger.debug("Consumer group '%s' already exists", self.group_name)
            else:
                raise

        self.running = True
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()
        logger.info("BacktestConsumer started polling '%s'", self.stream_key)

    def stop(self) -> None:
        """Signal the polling loop to stop."""
        self.running = False
        if self._thread is not None:
            self._thread.join(timeout=5.0)
            self._thread = None
        logger.info("BacktestConsumer stopped")

    def _poll_loop(self) -> None:
        """XREADGROUP loop that processes backtest requests."""
        while self.running:
            try:
                results = self.redis.xreadgroup(
                    groupname=self.group_name,
                    consumername=self.consumer_name,
                    streams={self.stream_key: ">"},
                    count=1,
                    block=1000,
                )
                if not results:
                    continue

                for _stream_name, messages in results:
                    for message_id, fields in messages:
                        try:
                            self._process_request(message_id, fields)
                        except Exception:
                            logger.exception(
                                "Unexpected error processing message %s", message_id
                            )
                        finally:
                            self.redis.xack(self.stream_key, self.group_name, message_id)
            except Exception:
                if self.running:
                    logger.exception("Error in poll loop, will retry")

    def _process_request(self, message_id: bytes, fields: dict) -> None:
        """Process a single backtest request message.

        1. Publish running status to backtest:status
        2. Deserialize StrategyConfig from message
        3. Fetch candle data
        4. Call BacktestEngine.run()
        5. Publish results to backtest:results
        6. Publish completed status to backtest:status
        On error: publish failed status + failure result
        """
        raw_data = fields.get(b"data")
        if raw_data is None:
            logger.error("Message %s missing 'data' field, skipping", message_id)
            return

        try:
            data = json.loads(raw_data)
        except json.JSONDecodeError:
            logger.error("Message %s has invalid JSON, skipping", message_id)
            return

        result_id = data.get("result_id", "unknown")
        strategy_id = data.get("strategy_id", "unknown")

        # 1. Publish running status
        self._publish_status(result_id, strategy_id, "running")

        try:
            # 2. Deserialize StrategyConfig
            strategy_config = StrategyConfig(**data["strategy_config"])

            # 3. Parse backtest params
            params = BacktestParams(**data["params"])

            # 3b. Parse instrument specs if provided, or fetch from backend
            raw_specs = data.get("instrument_specs")
            instrument_specs = InstrumentSpecs(**raw_specs) if raw_specs else None

            # Fallback: fetch instrument specs from backend if not in message
            if instrument_specs is None:
                backtest_instrument_for_specs = data.get("instrument") or strategy_config.instruments[0]
                instrument_specs = self._fetch_instrument_specs(backtest_instrument_for_specs)

            # 4. Fetch candle data — use entry timeframe, higher timeframe, and trend timeframe
            backtest_instrument = data.get("instrument") or strategy_config.instruments[0]
            entry_tf = data.get("timeframe") or strategy_config.entry_timeframe.value
            higher_tf = strategy_config.higher_timeframe.value
            trend_tf = strategy_config.trend_timeframe.value

            entry_candles = self._fetch_candle_data(
                instrument=backtest_instrument,
                timeframe=entry_tf,
                start_date=data["start_date"],
                end_date=data["end_date"],
            )

            # Fetch HTF candles separately (only if different from entry TF)
            htf_candles = None
            if higher_tf != entry_tf:
                htf_candles = self._fetch_candle_data(
                    instrument=backtest_instrument,
                    timeframe=higher_tf,
                    start_date=data["start_date"],
                    end_date=data["end_date"],
                )
                if not htf_candles:
                    logger.warning(
                        "No HTF candle data for %s:%s, falling back to entry TF for both",
                        backtest_instrument, higher_tf,
                    )
                    htf_candles = None

            # Fetch trend TF candles (only if different from HTF)
            trend_candles = None
            if trend_tf != higher_tf and trend_tf != entry_tf:
                trend_candles = self._fetch_candle_data(
                    instrument=backtest_instrument,
                    timeframe=trend_tf,
                    start_date=data["start_date"],
                    end_date=data["end_date"],
                )
                if not trend_candles:
                    logger.warning(
                        "No trend candle data for %s:%s, falling back to HTF",
                        backtest_instrument, trend_tf,
                    )
                    trend_candles = None

            if not entry_candles:
                raise ValueError(
                    f"No candle data found for {backtest_instrument}:"
                    f"{entry_tf} "
                    f"between {data['start_date']} and {data['end_date']}"
                )

            # Fetch 1m candles for granular exit checking (if entry TF is not 1m)
            tick_candles = None
            if entry_tf != "1m":
                tick_candles = self._fetch_candle_data(
                    instrument=backtest_instrument,
                    timeframe="1m",
                    start_date=data["start_date"],
                    end_date=data["end_date"],
                )
                if not tick_candles:
                    logger.warning(
                        "No 1m tick data for %s, exit checking will use %s candles",
                        backtest_instrument, entry_tf,
                    )
                    tick_candles = None

            # 5. Run backtest
            result: BacktestResult = self.engine.run(
                strategy_config, entry_candles, params, instrument_specs,
                htf_data=htf_candles, trend_data=trend_candles,
                tick_data=tick_candles,
            )

            # 6. Publish results to backtest:results stream
            result_message = BacktestResultMessage(
                result_id=result_id,
                strategy_id=strategy_id,
                status="completed",
                stats=result.stats,
                equity_curve=result.equity_curve,
                trade_results=result.trades,
            )
            self._check_result_stream_lag()
            self.redis.xadd(
                self.result_stream,
                {"data": result_message.model_dump_json()},
            )

            # 7. Publish completed status
            self._publish_status(result_id, strategy_id, "completed")

            logger.info(
                "Backtest %s completed: %d trades, net_profit=%.2f",
                result_id,
                result.stats.total_trades,
                result.stats.net_profit,
            )

        except Exception as e:
            error_msg = str(e)
            logger.exception("Backtest %s failed: %s", result_id, error_msg)

            # Publish failure result to backtest:results stream
            failure_message = BacktestResultMessage(
                result_id=result_id,
                strategy_id=strategy_id,
                status="failed",
                error=error_msg,
            )
            try:
                self._check_result_stream_lag()
                self.redis.xadd(
                    self.result_stream,
                    {"data": failure_message.model_dump_json()},
                )
            except Exception:
                logger.exception("Failed to publish failure result for %s", result_id)

            # Publish failed status
            self._publish_status(result_id, strategy_id, "failed", error=error_msg)

    def _check_result_stream_lag(self) -> None:
        """Check consumer lag on backtest:results before publishing.

        Logs a warning when lag exceeds threshold but never drops results.
        """
        try:
            groups = self.redis.xinfo_groups(self.result_stream)
            for group in groups:
                name = group.get("name", b"")
                if isinstance(name, bytes):
                    name = name.decode("utf-8", errors="replace")
                lag = int(group.get("pel-count", 0))
                if lag > self._backtest_result_high_water:
                    logger.warning(
                        "Consumer lag on '%s' group '%s' is %d (threshold=%d) — continuing publish",
                        self.result_stream,
                        name,
                        lag,
                        self._backtest_result_high_water,
                    )
        except Exception:
            # Fail-open: log and continue publishing
            logger.warning(
                "Failed to check consumer lag on '%s', continuing publish",
                self.result_stream,
                exc_info=True,
            )

    def _fetch_instrument_specs(self, instrument: str) -> InstrumentSpecs | None:
        """Fetch instrument specs from the backend API."""
        try:
            url = f"{self.backend_url}/api/instruments"
            resp = requests.get(url, timeout=10)
            resp.raise_for_status()
            instruments = resp.json()
            for inst in instruments:
                if inst.get("symbol") == instrument:
                    return InstrumentSpecs(
                        contract_size=float(inst.get("contractSize", 1.0)),
                        pip_size=float(inst.get("pipSize", 0.01)),
                        pip_value=float(inst.get("pipValue", 1.0)),
                        min_lot=float(inst.get("minLot", 0.01)),
                        lot_step=float(inst.get("lotStep", 0.01)),
                        leverage=int(inst.get("leverage", 100)),
                    )
            logger.warning("Instrument '%s' not found in backend, using defaults", instrument)
            return None
        except Exception as exc:
            logger.warning("Failed to fetch instrument specs for '%s': %s", instrument, exc)
            return None

    def _fetch_candle_data(
        self, instrument: str, timeframe: str, start_date: str, end_date: str
    ) -> list[Candle]:
        """Fetch candles from the backend API for the given date range."""
        try:
            url = f"{self.backend_url}/api/internal/candles"
            resp = requests.get(
                url,
                params={
                    "instrument": instrument,
                    "timeframe": timeframe,
                    "start_date": start_date,
                    "end_date": end_date,
                },
                timeout=60,
            )
            resp.raise_for_status()
            raw_candles = resp.json()

            candles = [
                Candle(
                    instrument=c["instrument"],
                    timeframe=c["timeframe"],
                    open=c["open"],
                    high=c["high"],
                    low=c["low"],
                    close=c["close"],
                    volume=c.get("volume", 0),
                    timestamp=c["timestamp"],
                )
                for c in raw_candles
            ]
            logger.info(
                "Fetched %d candles for %s:%s [%s → %s]",
                len(candles), instrument, timeframe, start_date, end_date,
            )
            return candles
        except Exception as exc:
            logger.error(
                "Failed to fetch candle data %s:%s [%s → %s]: %s",
                instrument, timeframe, start_date, end_date, exc,
            )
            return []

    def _fetch_candle_chunk(
        self, instrument: str, timeframe: str, start_date: str, end_date: str
    ) -> list[Candle]:
        """Alias for _fetch_candle_data for backward compatibility."""
        return self._fetch_candle_data(instrument, timeframe, start_date, end_date)

    def _publish_status(
        self, result_id: str, strategy_id: str, status: str, error: str | None = None
    ) -> None:
        """Publish a status update to the backtest:status pub/sub channel."""
        status_message = BacktestStatusMessage(
            result_id=result_id,
            strategy_id=strategy_id,
            status=status,
            error=error,
        )
        try:
            self.redis.publish(self.status_channel, status_message.model_dump_json())
        except Exception:
            logger.exception("Failed to publish status '%s' for %s", status, result_id)
