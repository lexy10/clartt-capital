"""AccountWorker — per-account isolated worker that consumes signals, validates risk, and executes trades.

Each AccountWorker runs in its own thread, processing signals for a single
TradingAccount. The worker maintains its own state via the TradingAccount
object (positions, daily loss, risk counters). The consumer group name
includes the account ID for isolation.

Strategy filtering: Each account has a set of assigned strategy IDs loaded
from Redis (key ``account:strategies:{account_id}``). Signals whose
``strategy_id`` is not in the assigned set are acknowledged and skipped.
Updates are received via the ``account:strategies:channel`` pub/sub channel.
"""

import json
import logging
import threading
from typing import Optional

from redis import Redis

from src.autopilot.autopilot_monitor import AutopilotMonitor
from src.consumer.signal_consumer import SignalConsumer, SIGNAL_STREAM_KEY
from src.events.event_publisher import EventPublisher
from src.executor.trade_executor import TradeExecutor
from src.persistence.trade_persister import TradePersister
from src.kill_switch.kill_switch_monitor import KillSwitchMonitor
from src.models import Signal, TradingAccount, TradeExecutionResult, TradeExecutionStatus
from src.models.trading_event import (
    TradingEvent,
    TradingEventType,
    RiskEvaluatedPayload,
    RiskRuleEvaluation,
)
from src.risk.risk_manager import RiskManager

TRADES_RESULTS_CHANNEL = "trades:results"
ACCOUNT_STRATEGIES_CHANNEL = "account:strategies:channel"

logger = logging.getLogger(__name__)


class AccountWorker:
    """Dedicated worker for a single TradingAccount.

    The run() loop:
      1. Check kill switch — if active, skip processing
      2. Check autopilot — if disabled, skip signal processing (graceful
         deactivation: continue monitoring open positions but stop opening new ones)
      3. Consume next signal from Redis stream
      4. Strategy filter — skip signals from unassigned strategies
      5. Validate risk
      6. If approved, execute trade
      7. Acknowledge the message
      8. Publish execution result back to Redis
      9. Log all events with account ID
    """

    def __init__(
        self,
        account: TradingAccount,
        risk_manager: RiskManager,
        executor: TradeExecutor,
        signal_consumer: SignalConsumer,
        kill_switch: KillSwitchMonitor,
        redis_client: Redis,
        autopilot_monitor: Optional[AutopilotMonitor] = None,
        position_monitor=None,
        event_publisher: Optional[EventPublisher] = None,
        stream_key: str = SIGNAL_STREAM_KEY,
        poll_timeout_ms: int = 1000,
        trade_persister: Optional[TradePersister] = None,
    ) -> None:
        self._account = account
        self._risk_manager = risk_manager
        self._executor = executor
        self._consumer = signal_consumer
        self._kill_switch = kill_switch
        self._redis = redis_client
        self._autopilot_monitor = autopilot_monitor
        self._position_monitor = position_monitor
        self._event_publisher = event_publisher
        self._stream_key = stream_key
        self._poll_timeout_ms = poll_timeout_ms
        # Optional direct DB writer — when set, trades are persisted as soon
        # as they fill (no dependency on the backend's pub/sub subscriber).
        self._trade_persister = trade_persister

        # Consumer group includes account ID for isolation
        self._group_name = f"account:{account.id}"
        self._consumer_id = f"worker:{account.id}"

        self._stop_event = threading.Event()
        self._running = False
        self._deactivated_while_open = False
        self._was_kill_switched = False  # Track kill switch transitions

        # Strategy filtering: load assigned strategy IDs from Redis
        self._assigned_strategy_ids: set[str] = self._load_strategy_ids()
        self._strategy_listener_thread: Optional[threading.Thread] = None

        # Broker symbol mapping: load from Redis (canonical → broker symbol)
        self._broker_symbol_map: dict[str, str] = self._load_broker_symbols()

    @property
    def account(self) -> TradingAccount:
        return self._account

    @property
    def account_id(self) -> str:
        return self._account.id

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def assigned_strategy_ids(self) -> set[str]:
        """Return a copy of the currently assigned strategy IDs."""
        return set(self._assigned_strategy_ids)

    def _load_strategy_ids(self) -> set[str]:
        """Load assigned strategy IDs from Redis key ``account:strategies:{id}``."""
        redis_key = f"account:strategies:{self._account.id}"
        try:
            raw = self._redis.get(redis_key)
            if raw is None:
                logger.info(
                    "[account:%s] No strategy assignments in Redis — accepting all signals",
                    self._account.id,
                )
                return set()
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            ids = json.loads(raw)
            if isinstance(ids, list):
                result = set(ids)
                logger.info(
                    "[account:%s] Loaded %d assigned strategy IDs from Redis",
                    self._account.id,
                    len(result),
                )
                return result
        except Exception:
            logger.exception(
                "[account:%s] Failed to load strategy IDs from Redis",
                self._account.id,
            )
        return set()

    def _load_broker_symbols(self) -> dict[str, str]:
        """Load broker symbol mappings from Redis key ``account:symbols:{id}``.

        Returns a dict mapping canonical instrument symbol (e.g. 'R_75')
        to the broker-specific trading symbol (e.g. 'Volatility 75 Index').
        """
        redis_key = f"account:symbols:{self._account.id}"
        try:
            raw = self._redis.get(redis_key)
            if raw is None:
                logger.info(
                    "[account:%s] No broker symbol mappings in Redis — using canonical symbols",
                    self._account.id,
                )
                return {}
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            mapping = json.loads(raw)
            if isinstance(mapping, dict):
                logger.info(
                    "[account:%s] Loaded %d broker symbol mappings from Redis",
                    self._account.id,
                    len(mapping),
                )
                return mapping
        except Exception:
            logger.exception(
                "[account:%s] Failed to load broker symbol mappings from Redis",
                self._account.id,
            )
        return {}

    def _resolve_broker_symbol(self, signal: Signal) -> Signal:
        """Resolve the signal's instrument to the account's broker symbol.

        If a broker_symbol is already set on the signal, it takes precedence.
        Otherwise, looks up the canonical instrument in the account's broker
        symbol mapping and sets broker_symbol accordingly.
        """
        if signal.broker_symbol:
            return signal

        broker_sym = self._broker_symbol_map.get(signal.instrument)
        if broker_sym and broker_sym != signal.instrument:
            signal = signal.model_copy(update={"broker_symbol": broker_sym})
            logger.debug(
                "[account:%s] Resolved %s → %s",
                self._account.id,
                signal.instrument,
                broker_sym,
            )
        return signal
    def _drain_pending_signals(self) -> int:
        """Consume and discard all pending signals in this worker's consumer group.

        Called when transitioning out of kill switch to prevent stale signals
        (with outdated prices) from executing. Returns the number of signals
        drained.
        """
        drained = 0
        while True:
            result = self._consumer.consume(
                stream=self._stream_key,
                group=self._group_name,
                consumer_id=self._consumer_id,
                block_ms=0,  # non-blocking
                count=1,
            )
            if result is None:
                break
            message_id, signal = result
            self._consumer.acknowledge(self._stream_key, self._group_name, message_id)
            drained += 1
            logger.info(
                "[account:%s] Drained stale signal %s (msg=%s) after kill switch",
                self._account.id,
                signal.id,
                message_id,
            )
        return drained

    def _start_strategy_listener(self) -> None:
        """Start a background thread that listens for strategy assignment changes."""
        self._strategy_listener_thread = threading.Thread(
            target=self._strategy_listener_loop,
            daemon=True,
            name=f"strategy-listener-{self._account.id}",
        )
        self._strategy_listener_thread.start()

    def _strategy_listener_loop(self) -> None:
        """Subscribe to ``account:strategies:channel`` and update assigned IDs."""
        try:
            # Use a separate Redis connection for pub/sub (blocking)
            sub_client = self._redis.pubsub()
            sub_client.subscribe(ACCOUNT_STRATEGIES_CHANNEL)
            logger.info(
                "[account:%s] Strategy listener subscribed to %s",
                self._account.id,
                ACCOUNT_STRATEGIES_CHANNEL,
            )
            while not self._stop_event.is_set():
                message = sub_client.get_message(timeout=1.0)
                if message and message["type"] == "message":
                    try:
                        data_raw = message["data"]
                        if isinstance(data_raw, bytes):
                            data_raw = data_raw.decode("utf-8")
                        payload = json.loads(data_raw)
                        msg_account_id = payload.get("accountId")
                        if msg_account_id == self._account.id:
                            new_ids = set(payload.get("strategyIds", []))
                            self._assigned_strategy_ids = new_ids
                            logger.info(
                                "[account:%s] Strategy assignments updated: %d strategies",
                                self._account.id,
                                len(new_ids),
                            )
                    except Exception:
                        logger.exception(
                            "[account:%s] Failed to parse strategy update",
                            self._account.id,
                        )
            sub_client.unsubscribe()
            sub_client.close()
        except Exception:
            logger.exception(
                "[account:%s] Strategy listener crashed",
                self._account.id,
            )

    def run(self) -> None:
        """Main loop: consume signal → validate risk → execute trade."""
        self._running = True
        logger.info("[account:%s] Worker started", self._account.id)

        # Resume monitoring positions that were open before this engine started
        # (e.g. after a redeploy/crash) so their time-exit / trailing rules keep
        # running. Broker-native SL/TP already protected them regardless.
        self._rehydrate_positions()

        # Start background listener for strategy assignment changes
        self._start_strategy_listener()

        try:
            while not self._stop_event.is_set():
                self._process_one_cycle()
        except Exception:
            logger.exception("[account:%s] Worker crashed", self._account.id)
            raise
        finally:
            self._running = False
            logger.info("[account:%s] Worker stopped", self._account.id)

    def _rehydrate_positions(self) -> None:
        """Reload this account's still-open positions from the DB and re-register
        them with the position monitor so engine-managed exit rules (time-exit,
        trailing, break-even) resume after a restart.

        Best-effort: a bad row is logged and skipped rather than aborting the
        worker. Positions with no engine-managed exits, or that can't be
        reconstructed, are simply left to their broker-native SL/TP.
        """
        if self._trade_persister is None or self._position_monitor is None:
            return

        try:
            rows = self._trade_persister.fetch_open_positions(self._account.id)
        except Exception:
            logger.exception(
                "[account:%s] Failed to fetch open positions for rehydration",
                self._account.id,
            )
            return

        if not rows:
            return

        rehydrated = 0
        for row in rows:
            try:
                strategy_config = row.get("strategy_config") or {}
                created = row.get("created_at")
                opened = row.get("opened_at")
                signal = Signal(
                    id=str(row["signal_id"]),
                    instrument=row["instrument"],
                    direction=row["direction"],
                    entry_price=float(row.get("entry_price") or row["fill_price"]),
                    stop_loss=float(row["stop_loss"]),
                    take_profit=float(row["take_profit"]),
                    position_size=float(row["position_size"]),
                    confidence_score=float(row.get("confidence_score") or 0.0),
                    timeframe=row["timeframe"],
                    order_block_id=row.get("order_block_id") or "rehydrated",
                    strategy_id=str(row["strategy_id"]),
                    mode=row["mode"],
                    metadata=row.get("metadata") or {},
                    exit_rules=strategy_config.get("exit_rules"),
                    created_at=created.isoformat() if hasattr(created, "isoformat") else str(created),
                )
                self._position_monitor.rehydrate_position(
                    position_id=int(row["broker_order_id"]),
                    signal=signal,
                    account=self._account,
                    entry_price=float(row.get("fill_price") or signal.entry_price),
                    entry_time=opened,
                )
                rehydrated += 1
            except Exception:
                logger.exception(
                    "[account:%s] Could not rehydrate position (order=%s) — "
                    "leaving it to broker-native SL/TP",
                    self._account.id, row.get("broker_order_id"),
                )

        if rehydrated:
            logger.info(
                "[account:%s] Rehydrated %d open position(s) into the monitor",
                self._account.id, rehydrated,
            )

    def _process_one_cycle(self) -> Optional[TradeExecutionResult]:
        """Process a single consume → validate → execute cycle.

        Returns the execution result if a trade was executed, or None.
        Exposed as a separate method for testability.
        """
        # 1. Check kill switch
        if self._kill_switch.is_active():
            logger.debug("[account:%s] Kill switch active, skipping", self._account.id)
            self._was_kill_switched = True
            # Brief sleep to avoid busy-waiting when kill switch is on
            self._stop_event.wait(timeout=self._poll_timeout_ms / 1000.0)
            return None

        # 1b. Drain stale signals after kill switch deactivation
        if self._was_kill_switched:
            self._was_kill_switched = False
            drained = self._drain_pending_signals()
            if drained > 0:
                logger.info(
                    "[account:%s] Kill switch deactivated — drained %d stale signal(s)",
                    self._account.id,
                    drained,
                )
            else:
                logger.info(
                    "[account:%s] Kill switch deactivated — no pending signals to drain",
                    self._account.id,
                )

        # 2. Check autopilot state
        if self._autopilot_monitor is not None:
            autopilot_enabled = self._autopilot_monitor.is_enabled(self._account.id)

            if not autopilot_enabled:
                has_open_positions = self._account.open_positions > 0

                if has_open_positions:
                    # Deactivated mid-position: stop opening new positions
                    # but continue monitoring existing ones
                    if not self._deactivated_while_open:
                        logger.info(
                            "[account:%s] Autopilot disabled with %d open position(s), "
                            "continuing to monitor existing positions",
                            self._account.id,
                            self._account.open_positions,
                        )
                    self._deactivated_while_open = True
                    # Skip signal processing (no new positions) but allow
                    # the cycle to continue for position monitoring
                    self._stop_event.wait(timeout=self._poll_timeout_ms / 1000.0)
                    return None
                else:
                    # No open positions: skip signal processing entirely
                    logger.debug(
                        "[account:%s] Autopilot disabled, discarding signals",
                        self._account.id,
                    )
                    self._deactivated_while_open = False
                    self._stop_event.wait(timeout=self._poll_timeout_ms / 1000.0)
                    return None
            else:
                # Autopilot is enabled — reset deactivation flag
                if self._deactivated_while_open:
                    logger.info(
                        "[account:%s] Autopilot re-enabled, resuming normal signal processing",
                        self._account.id,
                    )
                self._deactivated_while_open = False

        # 3. Consume next signal
        result = self._consumer.consume(
            stream=self._stream_key,
            group=self._group_name,
            consumer_id=self._consumer_id,
            block_ms=self._poll_timeout_ms,
            count=1,
        )

        if result is None:
            return None

        message_id, signal = result
        logger.info(
            "[account:%s] Received signal %s (msg=%s)",
            self._account.id,
            signal.id,
            message_id,
        )

        # 4. Strategy filter — skip signals from unassigned strategies
        if self._assigned_strategy_ids and signal.strategy_id not in self._assigned_strategy_ids:
            logger.info(
                "[account:%s] Signal %s skipped — strategy %s not assigned to this account",
                self._account.id,
                signal.id,
                signal.strategy_id,
            )
            self._consumer.acknowledge(self._stream_key, self._group_name, message_id)
            return None

        # 4b. Resolve broker symbol for this account's broker
        signal = self._resolve_broker_symbol(signal)

        # 5. Validate risk
        risk_result = self._risk_manager.validate(signal, self._account)

        # 5b. Publish RiskEvaluated event (fire-and-forget)
        self._publish_risk_evaluated_event(signal, risk_result)

        if not risk_result.approved:
            logger.warning(
                "[account:%s] Signal %s rejected by risk manager — rule: %s",
                self._account.id,
                signal.id,
                risk_result.rejected_by,
            )
            # Acknowledge even rejected signals so they aren't re-delivered
            self._consumer.acknowledge(self._stream_key, self._group_name, message_id)
            return None

        # 6. Execute trade
        logger.info(
            "[account:%s] Executing trade for signal %s",
            self._account.id,
            signal.id,
        )
        execution_result = self._executor.execute(signal, self._account)

        # 7. Acknowledge the message
        self._consumer.acknowledge(self._stream_key, self._group_name, message_id)

        # 8. Publish execution result back to Redis (forwarded to WebSocket clients)
        self._publish_result(execution_result, signal=signal)

        # 8b. Persist the trade directly to PostgreSQL — atomic and immediate,
        #     no dependency on the backend's pub/sub subscriber being healthy.
        if (
            self._trade_persister is not None
            and execution_result.status == TradeExecutionStatus.FILLED
        ):
            try:
                self._trade_persister.record_entry(
                    trade_id=execution_result.id,
                    signal_id=execution_result.signal_id,
                    account_id=self._account.id,
                    instrument=signal.instrument,
                    direction=signal.direction.value,
                    entry_price=signal.entry_price,
                    fill_price=execution_result.fill_price,
                    position_size=signal.position_size,
                    broker_order_id=execution_result.order_id,
                    status=execution_result.status.value,
                    execution_latency_ms=int(execution_result.execution_latency_ms),
                    slippage=execution_result.slippage,
                    spread_at_execution=execution_result.spread_at_execution,
                )
            except Exception:
                logger.exception(
                    "[account:%s] TradePersister.record_entry raised for signal %s",
                    self._account.id, signal.id,
                )

        # 9. Track position for active exit rule monitoring
        if (
            execution_result.status == TradeExecutionStatus.FILLED
            and self._position_monitor is not None
        ):
            self._position_monitor.track_position(
                position_id=execution_result.order_id,
                signal=signal,
                account=self._account,
                fill_price=execution_result.fill_price,
            )

        # 10. Log execution outcome
        logger.info(
            "[account:%s] Trade execution complete — signal=%s, status=%s, order_id=%d",
            self._account.id,
            execution_result.signal_id,
            execution_result.status.value,
            execution_result.order_id,
        )

        return execution_result

    def _publish_risk_evaluated_event(self, signal: Signal, risk_result) -> None:
        """Publish a RiskEvaluated event after risk validation (fire-and-forget)."""
        if self._event_publisher is None:
            return
        try:
            payload = RiskEvaluatedPayload(
                signal_id=signal.id,
                account_id=self._account.id,
                passed=risk_result.approved,
                rules_evaluated=[
                    RiskRuleEvaluation(
                        rule=r.rule.value,
                        result=r.passed,
                        threshold=0.0,
                    )
                    for r in risk_result.rules_checked
                ],
                rejection_reason=(
                    risk_result.rejected_by.value if risk_result.rejected_by else None
                ),
            )
            context_snapshot = {
                "account_equity": self._account.equity,
                "account_balance": self._account.balance,
                "open_position_count": self._account.open_positions,
                "current_lot_exposure": self._account.total_lot_exposure,
                "daily_loss": self._account.daily_loss,
                "risk_thresholds": {
                    "max_risk_per_trade": self._risk_manager.max_risk_per_trade_pct,
                    "max_daily_loss": self._risk_manager.max_daily_loss_pct,
                    "max_open_positions": self._risk_manager.max_positions,
                    "max_lot_exposure": self._risk_manager.max_lot_exposure,
                },
            }
            event = TradingEvent(
                event_type=TradingEventType.RiskEvaluated,
                aggregate_id=signal.id,
                sequence_number=0,
                payload=payload.model_dump(),
                context_snapshot=context_snapshot,
                source_service="execution-engine",
            )
            self._event_publisher.publish(event)
        except Exception:
            logger.exception(
                "[account:%s] Failed to publish RiskEvaluated event for signal %s",
                self._account.id,
                signal.id,
            )

    def _publish_result(
        self,
        result: TradeExecutionResult,
        signal: Optional[Signal] = None,
    ) -> None:
        """Publish execution result to Redis pub/sub channel for downstream consumers.

        Publishes the rich `trade_entry` envelope expected by the Backend's
        RedisStreamService so it can:
          - Forward to WebSocket clients
          - Persist a complete row to the ``trades`` table

        Backwards-compatible: if ``signal`` is None, falls back to the raw result
        for legacy callers that don't have signal context.
        """
        try:
            if signal is not None:
                payload = {
                    "type": "trade_entry",
                    "userId": self._account.user_id,
                    "accountId": self._account.id,
                    "trade": {
                        "id": result.id,
                        "signalId": result.signal_id,
                        "accountId": result.account_id,
                        "instrument": signal.instrument,
                        "direction": signal.direction.value,
                        "entryPrice": signal.entry_price,
                        "stopLoss": signal.stop_loss,
                        "takeProfit": signal.take_profit,
                        "positionSize": signal.position_size,
                        "fillPrice": result.fill_price,
                        "orderId": result.order_id,
                        "executionLatencyMs": result.execution_latency_ms,
                        "status": result.status.value,
                        "rejectionReason": result.rejection_reason,
                        "slippage": result.slippage,
                        "spreadAtExecution": result.spread_at_execution,
                        "executedAt": result.created_at,
                    },
                    "autopilot": True,
                }
                message = json.dumps(payload)
            else:
                message = result.model_dump_json()

            self._redis.publish(TRADES_RESULTS_CHANNEL, message)
            logger.debug(
                "[account:%s] Published execution result %s to %s",
                self._account.id,
                result.id,
                TRADES_RESULTS_CHANNEL,
            )
        except Exception:
            logger.exception(
                "[account:%s] Failed to publish execution result %s",
                self._account.id,
                result.id,
            )

    def stop(self) -> None:
        """Signal the worker to stop gracefully."""
        logger.info("[account:%s] Stop requested", self._account.id)
        self._stop_event.set()
