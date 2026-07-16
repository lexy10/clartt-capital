"""Strategy runner — subscribes to candle updates and orchestrates analysis cycles.

Listens on Redis pub/sub channel ``candles:updates``, loads active strategy
configurations, dispatches each to the appropriate algorithm via the
StrategyRegistry, and manages signal publishing, persistence, duplicate
prevention, and error isolation.
"""

import json
import logging
import threading
import time
from typing import Optional

import requests
from redis import Redis

from src.api import liveness
from src.models import Candle, StrategyConfig
from src.models.trading_event import (
    TradingEvent,
    TradingEventType,
    SignalGeneratedPayload,
)
from src.events.event_publisher import EventPublisher
from src.pipeline.signal_persister import SignalPersister
from src.pipeline.strategy_config_loader import StrategyConfigLoader
from src.metrics import (
    pipeline_active_strategies,
    pipeline_cycle_duration_seconds,
    pipeline_cycles_total,
    pipeline_errors_total,
    pipeline_signals_generated_total,
)
from src.signals.signal_publisher import SignalPublisher
from src.strategy.registry import StrategyRegistry

logger = logging.getLogger(__name__)

CANDLE_CHANNEL = "candles:updates"
MAX_CANDLES = 500
DUPLICATE_EXPIRY_SECONDS = 86400  # 24 hours
MAX_PROCESSED_PER_STRATEGY = 1000
FAILURE_WARNING_THRESHOLD = 5
INITIAL_BACKOFF = 0.5
MAX_BACKOFF = 10.0

# Timeframe → minutes mapping for throttle calculations
_TF_MINUTES = {
    "1m": 1, "5m": 5, "15m": 15, "30m": 30,
    "1h": 60, "4h": 240, "1d": 1440,
}


class StrategyRunner:
    """Core pipeline orchestrator.

    Subscribes to ``candles:updates``, filters by instrument, runs analysis
    cycles for matching strategies, publishes and persists generated signals.
    Fetches candle data from the backend API (PostgreSQL) instead of Redis sorted sets.

    Analysis cycles are throttled to the strategy's entry timeframe — a 5m
    strategy only runs once per 5-minute candle close, not on every 1m tick.
    """

    def __init__(
        self,
        redis_client: Redis,
        config_loader: StrategyConfigLoader,
        registry: StrategyRegistry,
        signal_publisher: SignalPublisher,
        signal_persister: SignalPersister,
        backend_url: str = "http://backend:3000",
        event_publisher: EventPublisher | None = None,
        correlation_guard: object | None = None,
        equity_monitor: object | None = None,
    ) -> None:
        self._redis = redis_client
        self._config_loader = config_loader
        self._registry = registry
        self._signal_publisher = signal_publisher
        self._signal_persister = signal_persister
        self._backend_url = backend_url
        self._event_publisher = event_publisher or EventPublisher(redis_client)
        self._correlation_guard = correlation_guard
        self._equity_monitor = equity_monitor
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        # Per-strategy duplicate tracking: {strategy_id: {ob_id: expiry_timestamp}}
        self._processed_obs: dict[str, dict[str, float]] = {}
        # Per-strategy consecutive failure count
        self._failure_counts: dict[str, int] = {}
        # Set while an analysis cycle is running
        self._in_progress = threading.Event()
        # Per-strategy+instrument last-run timestamp for throttling
        # Key: "{strategy_id}:{instrument}", Value: epoch seconds
        self._last_run: dict[str, float] = {}

        # ── Zone state tracking (mirrors backtest engine behaviour) ──
        # Per-strategy invalidated zones: zones that hit SL are never re-entered
        self._invalidated_zones: dict[str, set[str]] = {}
        # Per-strategy zone cooldowns: {ob_id: ISO timestamp of last signal}
        self._zone_cooldowns: dict[str, dict[str, str]] = {}
        # signal_id → (strategy_id, order_block_id) mapping for trade result lookups
        self._signal_ob_map: dict[str, tuple[str, str]] = {}
        # Background thread for trades:results subscription
        self._trade_result_thread: threading.Thread | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the pub/sub listener and trade-result listener in background threads."""
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._listen, daemon=True)
        self._thread.start()
        self._trade_result_thread = threading.Thread(
            target=self._listen_trade_results, daemon=True,
        )
        self._trade_result_thread.start()
        logger.info("StrategyRunner started — listening on %s", CANDLE_CHANNEL)

    def stop(self) -> None:
        """Signal stop and wait for any in-progress cycle to complete."""
        self._stop_event.set()
        # Wait for any running cycle to finish
        while self._in_progress.is_set():
            time.sleep(0.05)
        if self._thread is not None:
            self._thread.join(timeout=5.0)
            self._thread = None
        if self._trade_result_thread is not None:
            self._trade_result_thread.join(timeout=5.0)
            self._trade_result_thread = None
        logger.info("StrategyRunner stopped")

    # ------------------------------------------------------------------
    # Pub/sub listener with reconnection
    # ------------------------------------------------------------------

    def _listen(self) -> None:
        """Main pub/sub loop with exponential-backoff reconnection."""
        backoff = INITIAL_BACKOFF
        liveness.beat()  # mark alive as soon as the loop thread starts
        while not self._stop_event.is_set():
            liveness.beat()  # each reconnect cycle counts as progress
            pubsub = None
            try:
                pubsub = self._redis.pubsub()
                pubsub.subscribe(CANDLE_CHANNEL)
                logger.info("Subscribed to %s", CANDLE_CHANNEL)
                backoff = INITIAL_BACKOFF  # reset on successful connect

                while not self._stop_event.is_set():
                    # Heartbeat every poll (~1s idle) BEFORE processing, so a
                    # hang inside _on_candle_update stops the beats → restart.
                    liveness.beat()
                    msg = pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                    if msg is None:
                        continue
                    if msg["type"] != "message":
                        continue
                    try:
                        data = json.loads(msg["data"])
                        instrument = data.get("instrument")
                        timeframe = data.get("timeframe")
                        if instrument is None:
                            logger.warning("Candle update missing 'instrument' field")
                            continue
                        self._on_candle_update(instrument, timeframe)
                    except (json.JSONDecodeError, TypeError) as exc:
                        logger.warning("Invalid candle update message: %s", exc)

            except Exception as exc:
                logger.error("Redis pub/sub connection error: %s", exc)
            finally:
                if pubsub is not None:
                    try:
                        pubsub.unsubscribe(CANDLE_CHANNEL)
                        pubsub.close()
                    except Exception:
                        pass

            # Reconnect with exponential backoff
            if not self._stop_event.is_set():
                logger.info("Reconnecting in %.1fs …", backoff)
                self._stop_event.wait(timeout=backoff)
                backoff = min(backoff * 2, MAX_BACKOFF)

    # ------------------------------------------------------------------
    # Trade results listener (zone invalidation from SL hits)
    # ------------------------------------------------------------------

    def _listen_trade_results(self) -> None:
        """Subscribe to ``trades:results`` pub/sub to detect SL hits.

        When a trade result arrives, look up the signal_id → order_block_id
        mapping. If the execution was filled, we can't know the RR yet (the
        trade just opened). Trade *exit* events carry ``type: trade_exit``
        and include profit_loss — a negative P&L indicates SL hit, so we
        invalidate the zone.

        This mirrors the backtest engine's ``rr <= -1.0`` invalidation logic.
        """
        TRADES_CHANNEL = "trades:results"
        backoff = INITIAL_BACKOFF
        while not self._stop_event.is_set():
            pubsub = None
            try:
                pubsub = self._redis.pubsub()
                pubsub.subscribe(TRADES_CHANNEL)
                logger.info("Trade result listener subscribed to %s", TRADES_CHANNEL)
                backoff = INITIAL_BACKOFF

                while not self._stop_event.is_set():
                    msg = pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                    if msg is None:
                        continue
                    if msg["type"] != "message":
                        continue
                    try:
                        data = json.loads(msg["data"])
                        self._handle_trade_result(data)
                    except (json.JSONDecodeError, TypeError) as exc:
                        logger.warning("Invalid trade result message: %s", exc)
            except Exception as exc:
                logger.error("Trade results pub/sub error: %s", exc)
            finally:
                if pubsub is not None:
                    try:
                        pubsub.unsubscribe(TRADES_CHANNEL)
                        pubsub.close()
                    except Exception:
                        pass
            if not self._stop_event.is_set():
                self._stop_event.wait(timeout=backoff)
                backoff = min(backoff * 2, MAX_BACKOFF)

    def _handle_trade_result(self, data: dict) -> None:
        """Process a trade result message for zone invalidation.

        Trade exit events with negative profit_loss indicate SL was hit.
        We invalidate the corresponding OB zone so no further signals
        are generated from it (matching backtest C2 behaviour).
        """
        # Only process trade exits (not entries)
        result_type = data.get("type", "")
        if result_type not in ("trade_exit", ""):
            return

        signal_id = data.get("signal_id")
        if not signal_id:
            return

        mapping = self._signal_ob_map.get(signal_id)
        if mapping is None:
            return

        strategy_id, ob_id = mapping

        # Check for SL hit: negative profit_loss
        profit_loss = data.get("profit_loss")
        if profit_loss is not None and profit_loss < 0:
            zones = self._invalidated_zones.setdefault(strategy_id, set())
            if ob_id not in zones:
                zones.add(ob_id)
                logger.info(
                    "Zone %s invalidated for strategy %s (SL hit, P&L=%.2f)",
                    ob_id, strategy_id, profit_loss,
                )

    # ------------------------------------------------------------------
    # Candle update handler
    # ------------------------------------------------------------------

    def _on_candle_update(self, instrument: str, timeframe: str) -> None:
        """Run analysis cycles for all strategies matching *instrument*.

        Throttled: each strategy only runs once per entry-timeframe interval.
        A 5m strategy ignores candle updates until ≥5 minutes have passed
        since its last run for this instrument.
        """
        strategies = self._config_loader.get_active_strategies(instrument=instrument)
        if not strategies:
            return

        pipeline_active_strategies.set(len(strategies))
        now = time.time()

        self._in_progress.set()
        try:
            for config in strategies:
                # Throttle: only run once per entry-timeframe interval
                throttle_key = f"{config.id}:{instrument}"
                entry_tf = config.entry_timeframe.value if hasattr(config.entry_timeframe, "value") else str(config.entry_timeframe)
                interval_seconds = _TF_MINUTES.get(entry_tf, 5) * 60
                last = self._last_run.get(throttle_key, 0.0)
                if now - last < interval_seconds:
                    continue

                try:
                    count = self._run_analysis_cycle(config, instrument)
                    self._last_run[throttle_key] = now
                    # Reset failure count on success
                    self._failure_counts[config.id] = 0
                    logger.info(
                        "Analysis cycle complete: strategy=%s instrument=%s signals=%d",
                        config.name,
                        instrument,
                        count,
                    )
                except Exception as exc:
                    self._failure_counts[config.id] = self._failure_counts.get(config.id, 0) + 1
                    count_val = self._failure_counts[config.id]
                    logger.error(
                        "Analysis cycle failed: strategy=%s error=%s (consecutive=%d)",
                        config.name,
                        exc,
                        count_val,
                    )
                    pipeline_errors_total.labels(
                        strategy_name=config.name,
                        error_type=type(exc).__name__,
                    ).inc()
                    if count_val >= FAILURE_WARNING_THRESHOLD:
                        logger.warning(
                            "Strategy '%s' has failed %d consecutive cycles",
                            config.name,
                            count_val,
                        )
        finally:
            self._in_progress.clear()

    # ------------------------------------------------------------------
    # Analysis cycle
    # ------------------------------------------------------------------

    def _run_analysis_cycle(self, config: StrategyConfig, instrument: str) -> int:
        """Full analysis cycle for one strategy + one instrument. Returns signal count."""
        start_time = time.perf_counter()

        # Equity pause check (Req 23.3, 23.5) — skip if any account is paused
        if self._equity_monitor is not None:
            # Check all accounts; if any are paused, skip signal generation
            accounts_status = self._equity_monitor.list_accounts_status()
            all_paused = accounts_status and all(
                a.get("paused", False) for a in accounts_status
            )
            if all_paused and accounts_status:
                logger.warning(
                    "Skipping analysis for strategy=%s instrument=%s — all accounts equity-paused",
                    config.name,
                    instrument,
                )
                return 0

        entry_candles = self._fetch_candles_from_backend(
            instrument, config.entry_timeframe.value, MAX_CANDLES
        )
        structure_candles = self._fetch_candles_from_backend(
            instrument, config.higher_timeframe.value, MAX_CANDLES
        )
        trend_candles = self._fetch_candles_from_backend(
            instrument, config.trend_timeframe.value, MAX_CANDLES
        )

        algorithm = self._registry.get(config.algorithm)
        # Pass zone state to the algorithm — mirrors backtest engine behaviour
        inv_zones = self._invalidated_zones.get(config.id, set())
        cooldowns = self._zone_cooldowns.get(config.id, {})
        signals = algorithm.analyze(
            entry_candles, structure_candles, trend_candles, config,
            invalidated_zones=inv_zones,
            zone_cooldowns=cooldowns,
        )

        published = 0
        for signal in signals:
            if self._is_duplicate(config.id, signal.order_block_id):
                continue
            if signal.confidence_score < config.min_confidence_score:
                continue

            # Correlation Guard check (Req 22.1, 22.6)
            if self._correlation_guard is not None:
                guard_result = self._correlation_guard.evaluate(signal, config)
                if guard_result["action"] == "block":
                    logger.warning(
                        "Signal blocked by correlation guard: %s — %s",
                        signal.instrument,
                        guard_result["reason"],
                    )
                    continue
                if guard_result["action"] == "reduce":
                    signal.position_size = guard_result["adjusted_position_size"]
                    logger.info(
                        "Signal reduced by correlation guard: %s — %s",
                        signal.instrument,
                        guard_result["reason"],
                    )

            self._signal_publisher.publish(signal)
            self._signal_persister.persist(signal)
            self._mark_processed(config.id, signal.order_block_id)

            # Record zone cooldown (C4) — same as backtest engine
            if config.id not in self._zone_cooldowns:
                self._zone_cooldowns[config.id] = {}
            self._zone_cooldowns[config.id][signal.order_block_id] = (
                entry_candles[-1].timestamp if entry_candles else ""
            )

            # Track signal_id → (strategy_id, ob_id) for trade result lookups
            self._signal_ob_map[signal.id] = (config.id, signal.order_block_id)

            # Publish SignalGenerated event (fire-and-forget)
            self._publish_signal_generated_event(
                signal, config, entry_candles,
            )

            published += 1
            pipeline_signals_generated_total.labels(
                strategy_name=config.name,
                instrument=instrument,
                direction=signal.direction.value,
            ).inc()

        duration = time.perf_counter() - start_time
        pipeline_cycle_duration_seconds.labels(strategy_name=config.name).observe(duration)
        pipeline_cycles_total.labels(
            strategy_name=config.name,
            instrument=instrument,
        ).inc()

        return published

    # ------------------------------------------------------------------
    # Event publishing helpers
    # ------------------------------------------------------------------

    def _publish_signal_generated_event(
        self,
        signal,
        config: StrategyConfig,
        entry_candles: list[Candle],
    ) -> None:
        """Build and publish a SignalGenerated trading event (fire-and-forget)."""
        try:
            payload = SignalGeneratedPayload(
                signal_id=signal.id,
                instrument=signal.instrument,
                direction=signal.direction.value,
                entry_price=signal.entry_price,
                stop_loss=signal.stop_loss,
                take_profit=signal.take_profit,
                position_size=signal.position_size,
                confidence_score=signal.confidence_score,
                timeframe=signal.timeframe.value if hasattr(signal.timeframe, "value") else str(signal.timeframe),
                strategy_id=signal.strategy_id,
                algorithm_name=config.algorithm,
                order_block_id=signal.order_block_id,
            )

            # Build context snapshot
            current_candle = None
            recent_candles: list[dict] = []
            if entry_candles:
                last = entry_candles[-1]
                current_candle = {
                    "open": last.open,
                    "high": last.high,
                    "low": last.low,
                    "close": last.close,
                    "volume": last.volume,
                }
                for c in entry_candles[-5:]:
                    recent_candles.append({
                        "open": c.open,
                        "high": c.high,
                        "low": c.low,
                        "close": c.close,
                        "volume": c.volume,
                    })

            # Active order blocks from signal metadata entry zone
            active_order_blocks: list[dict] = []
            if signal.metadata and signal.metadata.entry_zone:
                ez = signal.metadata.entry_zone
                active_order_blocks.append({
                    "price_high": ez.price_high,
                    "price_low": ez.price_low,
                    "direction": signal.direction.value,
                })

            context_snapshot = {
                "current_candle": current_candle,
                "recent_candles": recent_candles,
                "active_order_blocks": active_order_blocks,
                "current_spread": signal.metadata.spread_at_generation if signal.metadata else 0.0,
                "strategy_config": config.model_dump(mode="json"),
            }

            # Determine sequence number: use 1 for the first event in this aggregate
            event = TradingEvent(
                event_type=TradingEventType.SignalGenerated.value,
                aggregate_id=signal.id,
                sequence_number=1,
                payload=payload.model_dump(),
                context_snapshot=context_snapshot,
                source_service="strategy-engine",
            )
            self._event_publisher.publish(event)
        except Exception as exc:
            logger.error(
                "Failed to build/publish SignalGenerated event for signal %s: %s",
                getattr(signal, "id", "unknown"),
                exc,
            )

    # ------------------------------------------------------------------
    # Backend API candle fetcher
    # ------------------------------------------------------------------

    def _fetch_candles_from_backend(
        self, instrument: str, timeframe: str, count: int
    ) -> list[Candle]:
        """Fetch candles from the backend internal API (PostgreSQL)."""
        try:
            url = f"{self._backend_url}/api/internal/candles"
            resp = requests.get(
                url,
                params={"instrument": instrument, "timeframe": timeframe, "count": str(count)},
                timeout=10,
            )
            resp.raise_for_status()
            raw_candles = resp.json()
            return [
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
        except Exception as exc:
            logger.warning(
                "Failed to fetch candles from backend for %s:%s: %s",
                instrument, timeframe, exc,
            )
            return []

    # ------------------------------------------------------------------
    # Duplicate tracking
    # ------------------------------------------------------------------

    def _is_duplicate(self, strategy_id: str, ob_id: str) -> bool:
        """Return True if *ob_id* was already processed within its expiry window."""
        bucket = self._processed_obs.get(strategy_id)
        if bucket is None:
            return False
        expiry = bucket.get(ob_id)
        if expiry is None:
            return False
        if time.time() >= expiry:
            del bucket[ob_id]
            return False
        return True

    def _mark_processed(self, strategy_id: str, ob_id: str) -> None:
        """Record *ob_id* with a 24-hour expiry, evicting oldest if at cap."""
        if strategy_id not in self._processed_obs:
            self._processed_obs[strategy_id] = {}
        bucket = self._processed_obs[strategy_id]
        self._evict_expired(strategy_id)
        # Evict oldest entries if at capacity
        while len(bucket) >= MAX_PROCESSED_PER_STRATEGY:
            oldest_key = min(bucket, key=bucket.get)
            del bucket[oldest_key]
        bucket[ob_id] = time.time() + DUPLICATE_EXPIRY_SECONDS

    def _evict_expired(self, strategy_id: str) -> None:
        """Remove entries whose expiry timestamp has passed."""
        bucket = self._processed_obs.get(strategy_id)
        if bucket is None:
            return
        now = time.time()
        expired = [k for k, v in bucket.items() if now >= v]
        for k in expired:
            del bucket[k]
