"""PositionMonitor — active position monitoring with pip-aware exit rule processing.

Runs a polling loop that checks all tracked positions against their configured
exit rules (trailing stop, break-even, time exit, partial close). Uses instrument
specs for proper pip calculations per instrument.

The monitor fetches instrument specs from the backend on startup and caches them.
Each position is tracked with its signal data, entry time, and current exit rule state.
"""

import json
import logging
import threading
import time as time_module
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, TYPE_CHECKING

import requests
from redis import Redis

from src import liveness
from src.executor.clients.base import BrokerProvider, BrokerRouter
from src.executor.trade_executor import TradeExecutor, BrokerClient
from src.models import (
    Signal,
    SignalDirection,
    TradingAccount,
    TradeExecutionResult,
    TradeExecutionStatus,
    InstrumentSpecs,
    ExitRules,
)

if TYPE_CHECKING:
    from src.persistence.trade_persister import TradePersister

logger = logging.getLogger(__name__)

TRADES_RESULTS_CHANNEL = "trades:results"
# How often to poll positions (seconds)
MONITOR_POLL_INTERVAL_S = 3
# How often to refresh instrument specs cache (seconds)
SPECS_CACHE_TTL_S = 300


@dataclass
class TrackedPosition:
    """State for a position being actively monitored."""
    position_id: int
    signal: Signal
    account: TradingAccount
    exit_rules: ExitRules
    entry_time: datetime
    entry_price: float
    direction: SignalDirection
    instrument: str
    original_size: float
    current_size: float
    current_sl: float
    # State tracking
    break_even_applied: bool = False
    trailing_activated: bool = False
    trailing_sl: float = 0.0
    partial_close_done: bool = False
    # ATR trailing state
    atr_trailing_activated: bool = False
    entry_atr: float = 0.0


class PositionMonitor:
    """Monitors open positions and applies exit rules using pip-aware calculations.

    Lifecycle:
    1. AccountWorker calls track_position() after a successful fill
    2. Monitor loop polls every MONITOR_POLL_INTERVAL_S seconds
    3. For each tracked position, gets current price from broker
    4. Evaluates exit rules in priority order:
       a. Time exit (highest priority — hard deadline)
       b. Trailing stop (modify SL on broker)
       c. Break-even (modify SL to entry + buffer)
       d. Partial close (close percentage of position)
    5. When a position is closed (by broker SL/TP or by monitor), untrack it
    """

    def __init__(
        self,
        broker_client: BrokerClient,
        executor: TradeExecutor,
        redis_client: Redis,
        backend_url: str = "http://backend:3000",
        trade_persister: Optional["TradePersister"] = None,
        broker_router: Optional[BrokerRouter] = None,
    ) -> None:
        self._broker = broker_client
        self._executor = executor
        self._redis = redis_client
        self._backend_url = backend_url
        self._trade_persister = trade_persister
        self._broker_router = broker_router

        self._positions: dict[int, TrackedPosition] = {}
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

        # Instrument specs cache
        self._specs_cache: dict[str, InstrumentSpecs] = {}
        self._specs_cache_time: float = 0.0

    # ── Public API ──────────────────────────────────────────────────

    def start(self) -> None:
        """Start the monitoring loop in a background thread."""
        if self._thread and self._thread.is_alive():
            logger.warning("PositionMonitor already running")
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="position-monitor")
        self._thread.start()
        logger.info("PositionMonitor started (poll interval: %ds)", MONITOR_POLL_INTERVAL_S)

    def stop(self) -> None:
        """Stop the monitoring loop."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=10)
        logger.info("PositionMonitor stopped")

    def track_position(
        self,
        position_id: int,
        signal: Signal,
        account: TradingAccount,
        fill_price: float,
    ) -> None:
        """Register a filled position for active monitoring."""
        exit_rules = ExitRules.from_dict(signal.exit_rules)
        if not exit_rules.has_any_enabled():
            logger.debug("No exit rules enabled for position %d, skipping tracking", position_id)
            return

        tracked = TrackedPosition(
            position_id=position_id,
            signal=signal,
            account=account,
            exit_rules=exit_rules,
            entry_time=datetime.now(timezone.utc),
            entry_price=fill_price,
            direction=signal.direction,
            instrument=signal.instrument,
            original_size=signal.position_size,
            current_size=signal.position_size,
            current_sl=signal.stop_loss,
            entry_atr=self._estimate_entry_atr(signal, fill_price),
        )

        with self._lock:
            self._positions[position_id] = tracked

        logger.info(
            "[monitor] Tracking position %d (%s %s) with exit rules: "
            "trailing=%s, break_even=%s, time_exit=%s, partial_close=%s, atr_trailing=%s",
            position_id,
            signal.direction.value,
            signal.instrument,
            exit_rules.trailing_stop.enabled,
            exit_rules.break_even.enabled,
            exit_rules.time_exit.enabled,
            exit_rules.partial_close.enabled,
            exit_rules.atr_trailing_stop.enabled,
        )

    def rehydrate_position(
        self,
        position_id: int,
        signal: Signal,
        account: TradingAccount,
        entry_price: float,
        entry_time: datetime,
    ) -> None:
        """Re-register a position that was open before this engine started.

        Unlike track_position(), this preserves the ORIGINAL entry_time (so a
        time-exit fires at the real deadline, not restart + duration) and takes
        deliberately safe defaults for stateful exit rules:

        - current_sl = original signal SL. Trailing re-arms from here; the
          "only move SL in the profitable direction" guard means the SL can
          never be pushed below the original protective level. Worst case after
          a restart+retracement is giving back some already-trailed profit —
          the position is never less protected than at entry.
        - partial_close_done = True. We can't reliably tell from the broker
          whether a partial already executed, so we never risk a DOUBLE partial
          close. A not-yet-taken partial is skipped (safe; no shipped strategy
          uses partial_close).

        Idempotent: re-registering an already-tracked position is a no-op.
        """
        exit_rules = ExitRules.from_dict(signal.exit_rules)
        if not exit_rules.has_any_enabled():
            # No engine-managed exits → nothing to resume; broker SL/TP covers it.
            return

        with self._lock:
            if position_id in self._positions:
                return

        tracked = TrackedPosition(
            position_id=position_id,
            signal=signal,
            account=account,
            exit_rules=exit_rules,
            entry_time=entry_time,
            entry_price=entry_price,
            direction=signal.direction,
            instrument=signal.instrument,
            original_size=signal.position_size,
            current_size=signal.position_size,
            current_sl=signal.stop_loss,
            entry_atr=self._estimate_entry_atr(signal, entry_price),
            # Safe defaults — see docstring.
            partial_close_done=True,
        )

        with self._lock:
            self._positions[position_id] = tracked

        logger.info(
            "[monitor] Rehydrated position %d (%s %s) opened %s — resuming exits: "
            "trailing=%s, break_even=%s, time_exit=%s, atr_trailing=%s "
            "(partial_close suppressed for safety)",
            position_id,
            signal.direction.value,
            signal.instrument,
            entry_time.isoformat(),
            exit_rules.trailing_stop.enabled,
            exit_rules.break_even.enabled,
            exit_rules.time_exit.enabled,
            exit_rules.atr_trailing_stop.enabled,
        )

    def untrack_position(self, position_id: int) -> None:
        """Remove a position from monitoring (e.g., closed by broker SL/TP)."""
        with self._lock:
            removed = self._positions.pop(position_id, None)
        if removed:
            logger.info("[monitor] Untracked position %d", position_id)

    @property
    def tracked_count(self) -> int:
        with self._lock:
            return len(self._positions)

    # ── Instrument Specs ────────────────────────────────────────────

    def _get_specs(self, instrument: str) -> Optional[InstrumentSpecs]:
        """Get instrument specs, refreshing cache if stale."""
        now = time_module.time()
        if now - self._specs_cache_time >= SPECS_CACHE_TTL_S:
            self._refresh_specs()

        return self._specs_cache.get(instrument)

    def _refresh_specs(self) -> None:
        """Fetch instrument specs from backend internal endpoint."""
        try:
            url = f"{self._backend_url}/api/internal/instruments/specs"
            resp = requests.get(url, timeout=10)
            resp.raise_for_status()
            raw_list = resp.json()

            new_cache: dict[str, InstrumentSpecs] = {}
            for raw in raw_list:
                specs = InstrumentSpecs(
                    symbol=raw["symbol"],
                    pip_size=float(raw["pipSize"]),
                    pip_value=float(raw["pipValue"]),
                    contract_size=float(raw["contractSize"]),
                    min_lot=float(raw.get("minLot", 0.01)),
                    lot_step=float(raw.get("lotStep", 0.01)),
                    leverage=int(raw.get("leverage", 100)),
                )
                new_cache[specs.symbol] = specs

            self._specs_cache = new_cache
            self._specs_cache_time = time_module.time()
            logger.info("[monitor] Refreshed instrument specs: %d instruments", len(new_cache))
        except Exception as exc:
            logger.warning("[monitor] Failed to refresh instrument specs: %s", exc)

    # ── Main Loop ───────────────────────────────────────────────────

    def _run_loop(self) -> None:
        """Main monitoring loop."""
        # Initial specs load
        self._refresh_specs()

        liveness.beat()  # mark alive as soon as the loop thread starts
        while not self._stop_event.is_set():
            # Heartbeat BEFORE the cycle so a hang inside _check_all_positions
            # stops the beats → the container is restarted by autoheal.
            liveness.beat()
            try:
                self._check_all_positions()
            except Exception:
                logger.exception("[monitor] Error in monitoring cycle")

            self._stop_event.wait(timeout=MONITOR_POLL_INTERVAL_S)

    def _check_all_positions(self) -> None:
        """Check all tracked positions against their exit rules + reconcile."""
        with self._lock:
            positions = list(self._positions.values())

        if not positions:
            return

        to_remove: list[int] = []

        for pos in positions:
            try:
                closed = self._evaluate_position(pos)
                if closed:
                    to_remove.append(pos.position_id)
            except Exception:
                logger.exception(
                    "[monitor] Error evaluating position %d", pos.position_id
                )

        # Reconcile externally-closed positions (broker SL/TP fired).
        # Group by account so we authorize once per Deriv token, then diff
        # tracked positions vs. broker's open list.
        if self._trade_persister is not None:
            try:
                self._reconcile_external_closes(positions, to_remove)
            except Exception:
                logger.exception("[monitor] Error during external-close reconciliation")

        # Remove closed positions
        if to_remove:
            with self._lock:
                for pid in to_remove:
                    self._positions.pop(pid, None)

    # ── Reconciliation of broker-side closes ────────────────────────

    def _client_for_account(self, account: TradingAccount) -> Optional[BrokerClient]:
        """Resolve and authorize the right broker client for an account."""
        provider_str = (account.broker_provider or "").lower()
        client: Optional[BrokerClient] = None
        if self._broker_router is not None and provider_str:
            try:
                provider = BrokerProvider(provider_str)
                client = self._broker_router.get(provider)
            except ValueError:
                client = None
        if client is None:
            client = self._broker  # legacy fallback
        # For Deriv per-account, authorize with the account's token
        if client is not None and provider_str == "deriv":
            token = getattr(account, "deriv_api_token", None)
            login = getattr(account, "deriv_login_id", None) or account.id
            if token and hasattr(client, "connect_with_token"):
                try:
                    client.connect_with_token(login, token)
                except Exception as exc:
                    logger.warning(
                        "[monitor] Deriv connect_with_token failed for %s: %s",
                        account.id, exc,
                    )
                    return None
        return client

    def _reconcile_external_closes(
        self,
        positions: list[TrackedPosition],
        to_remove: list[int],
    ) -> None:
        """Detect positions closed by the broker (e.g., SL/TP fired)
        and write their exit details to the DB.

        Positions already queued for removal by our own exit logic are skipped.
        """
        already_closing = set(to_remove)
        by_account: dict[str, list[TrackedPosition]] = defaultdict(list)
        for pos in positions:
            if pos.position_id in already_closing:
                continue
            by_account[pos.account.id].append(pos)

        for acct_id, acct_positions in by_account.items():
            account = acct_positions[0].account
            client = self._client_for_account(account)
            if client is None:
                continue
            try:
                broker_positions = client.get_positions() or []
            except Exception as exc:
                logger.debug(
                    "[monitor] get_positions failed for account %s: %s",
                    acct_id, exc,
                )
                continue

            open_ids = set()
            for p in broker_positions:
                try:
                    open_ids.add(int(p.get("id")))
                except (TypeError, ValueError):
                    continue

            for pos in acct_positions:
                if pos.position_id in open_ids:
                    continue  # Still open at broker
                # Vanished — fetch authoritative exit details
                details = None
                if hasattr(client, "fetch_closed_contract"):
                    try:
                        details = client.fetch_closed_contract(pos.position_id)
                    except Exception as exc:
                        logger.debug(
                            "[monitor] fetch_closed_contract %s failed: %s",
                            pos.position_id, exc,
                        )
                if details is None:
                    logger.debug(
                        "[monitor] Position %d not at broker and no exit details — skip",
                        pos.position_id,
                    )
                    continue
                ok = self._trade_persister.record_exit(
                    broker_order_id=details.broker_order_id,
                    exit_price=details.exit_price,
                    profit_loss=details.profit_loss,
                    closed_at=details.closed_at,
                    status="closed",
                )
                if ok:
                    logger.info(
                        "[monitor] Position %d closed externally — persisted exit "
                        "(price=%.5f, pnl=%.2f, status=%s)",
                        pos.position_id, details.exit_price,
                        details.profit_loss, details.status,
                    )
                    self._publish_external_close_event(pos, details)
                    to_remove.append(pos.position_id)

    def _publish_external_close_event(
        self,
        pos: TrackedPosition,
        details,
    ) -> None:
        """Publish a trade_exit event for dashboards when broker closes a position."""
        payload = {
            "type": "trade_exit",
            "userId": pos.account.user_id,
            "accountId": pos.account.id,
            "trade": {
                "signalId": pos.signal.id,
                "direction": pos.direction.value,
                "entryPrice": pos.entry_price,
                "exitPrice": details.exit_price,
                "stopLoss": pos.signal.stop_loss,
                "takeProfit": pos.signal.take_profit,
                "positionSize": pos.current_size,
                "orderId": pos.position_id,
                "executedAt": details.closed_at.isoformat(),
                "exitReason": "broker_sl_tp",
                "profitLoss": details.profit_loss,
                "status": details.status,
            },
            "autopilot": True,
            "exitRule": "broker_sl_tp",
        }
        try:
            self._redis.publish(TRADES_RESULTS_CHANNEL, json.dumps(payload))
        except Exception:
            logger.debug(
                "[monitor] Failed to publish external-close event for %d",
                pos.position_id,
            )

    def _evaluate_position(self, pos: TrackedPosition) -> bool:
        """Evaluate exit rules for a single position.

        Returns True if the position was closed (should be untracked).
        """
        specs = self._get_specs(pos.instrument)
        if specs is None:
            logger.debug("[monitor] No specs for %s, skipping position %d", pos.instrument, pos.position_id)
            return False

        # Get current price from broker
        tick = self._broker.get_symbol_info_tick(pos.instrument)
        if tick is None:
            logger.debug("[monitor] No tick data for %s, skipping", pos.instrument)
            return False

        # Use bid for BUY positions (selling price), ask for SELL positions (buying price)
        if pos.direction == SignalDirection.BUY:
            current_price = tick.get("bid", tick.get("last", 0.0))
        else:
            current_price = tick.get("ask", tick.get("last", 0.0))

        if current_price <= 0:
            return False

        # Calculate profit in pips and price distance
        if pos.direction == SignalDirection.BUY:
            profit_distance = current_price - pos.entry_price
        else:
            profit_distance = pos.entry_price - current_price

        profit_pips = specs.price_to_pips(profit_distance)

        now = datetime.now(timezone.utc)
        rules = pos.exit_rules

        # Priority 1: Time exit (hard deadline)
        if rules.time_exit.enabled:
            elapsed_minutes = (now - pos.entry_time).total_seconds() / 60.0
            if elapsed_minutes >= rules.time_exit.max_duration_minutes:
                logger.info(
                    "[monitor] Time exit triggered for position %d "
                    "(%.0f min elapsed, max %d min)",
                    pos.position_id,
                    elapsed_minutes,
                    rules.time_exit.max_duration_minutes,
                )
                self._close_position(pos, "time_exit")
                return True

        # Priority 2: Break-even (one-time SL modification)
        if rules.break_even.enabled and not pos.break_even_applied:
            if profit_pips >= rules.break_even.activation_pips:
                buffer_price = specs.pips_to_price(rules.break_even.buffer_pips)
                if pos.direction == SignalDirection.BUY:
                    new_sl = pos.entry_price + buffer_price
                else:
                    new_sl = pos.entry_price - buffer_price

                success = self._modify_sl(pos, new_sl)
                if success:
                    pos.break_even_applied = True
                    pos.current_sl = new_sl
                    logger.info(
                        "[monitor] Break-even applied for position %d — "
                        "SL moved to %.5f (entry + %.1f pip buffer)",
                        pos.position_id,
                        new_sl,
                        rules.break_even.buffer_pips,
                    )

        # Priority 3: Trailing stop (continuous SL adjustment)
        if rules.trailing_stop.enabled:
            if profit_pips >= rules.trailing_stop.activation_pips:
                trail_distance_price = specs.pips_to_price(rules.trailing_stop.trail_distance_pips)

                if pos.direction == SignalDirection.BUY:
                    new_trail_sl = current_price - trail_distance_price
                else:
                    new_trail_sl = current_price + trail_distance_price

                # Only move SL in the profitable direction
                should_update = False
                if not pos.trailing_activated:
                    pos.trailing_activated = True
                    should_update = True
                elif pos.direction == SignalDirection.BUY and new_trail_sl > pos.current_sl:
                    should_update = True
                elif pos.direction == SignalDirection.SELL and new_trail_sl < pos.current_sl:
                    should_update = True

                if should_update:
                    success = self._modify_sl(pos, new_trail_sl)
                    if success:
                        pos.trailing_sl = new_trail_sl
                        pos.current_sl = new_trail_sl
                        logger.info(
                            "[monitor] Trailing stop updated for position %d — "
                            "SL moved to %.5f (%.1f pips from current price %.5f)",
                            pos.position_id,
                            new_trail_sl,
                            rules.trailing_stop.trail_distance_pips,
                            current_price,
                        )

        # Priority 4: ATR-based trailing stop (uses price distance, not pips)
        if rules.atr_trailing_stop.enabled and pos.entry_atr > 0:
            activation_dist = rules.atr_trailing_stop.activation_atr_mult * pos.entry_atr
            trail_dist = rules.atr_trailing_stop.trail_atr_mult * pos.entry_atr

            if profit_distance >= activation_dist:
                if not pos.atr_trailing_activated:
                    pos.atr_trailing_activated = True
                    logger.info(
                        "[monitor] ATR trailing activated for position %d — "
                        "profit %.2f >= activation %.2f (%.1fx ATR)",
                        pos.position_id,
                        profit_distance,
                        activation_dist,
                        rules.atr_trailing_stop.activation_atr_mult,
                    )

            if pos.atr_trailing_activated:
                if pos.direction == SignalDirection.BUY:
                    new_trail_sl = current_price - trail_dist
                else:
                    new_trail_sl = current_price + trail_dist

                should_update = False
                if pos.direction == SignalDirection.BUY and new_trail_sl > pos.current_sl:
                    should_update = True
                elif pos.direction == SignalDirection.SELL and new_trail_sl < pos.current_sl:
                    should_update = True

                if should_update:
                    success = self._modify_sl(pos, new_trail_sl)
                    if success:
                        pos.current_sl = new_trail_sl
                        logger.info(
                            "[monitor] ATR trailing SL updated for position %d — "
                            "SL moved to %.5f (%.1fx ATR from price %.5f)",
                            pos.position_id,
                            new_trail_sl,
                            rules.atr_trailing_stop.trail_atr_mult,
                            current_price,
                        )

        # Priority 5: Partial close
        if rules.partial_close.enabled and not pos.partial_close_done:
            if profit_pips >= rules.partial_close.trigger_pips:
                close_lots = round(
                    pos.original_size * (rules.partial_close.close_percent / 100.0),
                    2,
                )
                # Ensure we don't close more than current size and respect min lot
                close_lots = min(close_lots, pos.current_size)
                if close_lots >= specs.min_lot:
                    success = self._partial_close(pos, close_lots)
                    if success:
                        pos.partial_close_done = True
                        pos.current_size -= close_lots
                        logger.info(
                            "[monitor] Partial close for position %d — "
                            "closed %.2f lots (%.0f%%), remaining %.2f lots",
                            pos.position_id,
                            close_lots,
                            rules.partial_close.close_percent,
                            pos.current_size,
                        )

        return False

    # ── Broker Actions ──────────────────────────────────────────────

    @staticmethod
    def _estimate_entry_atr(signal: Signal, fill_price: float) -> float:
        """Estimate ATR at entry from the SL distance and ATR multiplier.

        The signal's SL is set at entry_price ± (atr_sl_multiplier × ATR).
        So ATR ≈ |entry - SL| / atr_sl_multiplier.
        Falls back to the raw SL distance if multiplier is unknown.
        """
        sl_distance = abs(fill_price - signal.stop_loss)
        if sl_distance == 0:
            return 0.0
        # Try to get the ATR multiplier from signal metadata. metadata may be a
        # Pydantic SignalMetadata model, a plain dict, or None — handle all
        # three (a model has no .get(), so calling it would raise).
        metadata = getattr(signal, "metadata", None)
        if hasattr(metadata, "get"):
            atr_mult = metadata.get("atr_sl_multiplier", 2.0)
        elif metadata is not None:
            atr_mult = getattr(metadata, "atr_sl_multiplier", 2.0)
        else:
            atr_mult = 2.0
        if atr_mult <= 0:
            return sl_distance
        return sl_distance / atr_mult

    def _modify_sl(self, pos: TrackedPosition, new_sl: float) -> bool:
        """Modify the stop-loss on the broker for a tracked position."""
        try:
            modifications = {"stop_loss": new_sl}
            return self._executor.modify_order(
                order_id=pos.position_id,
                account=pos.account,
                modifications=modifications,
            )
        except Exception:
            logger.exception(
                "[monitor] Failed to modify SL for position %d", pos.position_id
            )
            return False

    def _close_position(self, pos: TrackedPosition, reason: str) -> bool:
        """Close a position entirely, persist the exit, and publish the result."""
        try:
            result = self._executor.close_position(pos.position_id, pos.account)
            if result.status == TradeExecutionStatus.FILLED:
                self._publish_exit_event(pos, result, reason)
                # Persist exit to DB — prefer broker's profit number when available
                if self._trade_persister is not None:
                    exit_price = result.fill_price
                    closed_at = datetime.now(timezone.utc)
                    profit_loss: Optional[float] = None

                    client = self._client_for_account(pos.account)
                    if client is not None and hasattr(client, "fetch_closed_contract"):
                        try:
                            details = client.fetch_closed_contract(pos.position_id)
                        except Exception:
                            details = None
                        if details is not None:
                            exit_price = details.exit_price
                            closed_at = details.closed_at
                            profit_loss = details.profit_loss

                    if profit_loss is None:
                        # Local fallback — better than nothing
                        if pos.direction == SignalDirection.BUY:
                            profit_loss = (exit_price - pos.entry_price) * pos.current_size
                        else:
                            profit_loss = (pos.entry_price - exit_price) * pos.current_size

                    try:
                        self._trade_persister.record_exit(
                            broker_order_id=pos.position_id,
                            exit_price=exit_price,
                            profit_loss=profit_loss,
                            closed_at=closed_at,
                            status="closed",
                        )
                    except Exception:
                        logger.exception(
                            "[monitor] Failed to persist exit for position %d",
                            pos.position_id,
                        )
                return True
            else:
                logger.warning(
                    "[monitor] Close failed for position %d — status=%s",
                    pos.position_id,
                    result.status.value,
                )
                return False
        except Exception:
            logger.exception(
                "[monitor] Failed to close position %d", pos.position_id
            )
            return False

    def _partial_close(self, pos: TrackedPosition, lots: float) -> bool:
        """Partially close a position by closing a specified number of lots.

        Note: MetaAPI/MT5 partial close is done by closing the full position
        and re-opening a smaller one, or by using a close-by-volume API.
        For now, we use the broker's close_position which closes the full position.
        A proper partial close would need broker-specific implementation.
        """
        # For stub/demo mode, just log it. For real MetaAPI, this would need
        # the partial close API. For now, we modify the position size tracking
        # but don't actually partially close on the broker — the trailing stop
        # and break-even will protect the remaining position.
        logger.info(
            "[monitor] Partial close requested for position %d: %.2f lots "
            "(broker partial close not yet implemented — tracking only)",
            pos.position_id,
            lots,
        )
        return True

    # ── Event Publishing ────────────────────────────────────────────

    def _publish_exit_event(
        self,
        pos: TrackedPosition,
        result: TradeExecutionResult,
        reason: str,
    ) -> None:
        """Publish a trade exit event to Redis for WebSocket forwarding."""
        entry_price = pos.entry_price
        exit_price = result.fill_price
        if pos.direction == SignalDirection.BUY:
            profit_loss = (exit_price - entry_price) * pos.current_size
        else:
            profit_loss = (entry_price - exit_price) * pos.current_size

        payload = {
            "type": "trade_exit",
            "userId": pos.account.user_id,
            "accountId": pos.account.id,
            "trade": {
                "id": result.id,
                "signalId": pos.signal.id,
                "direction": pos.direction.value,
                "entryPrice": entry_price,
                "exitPrice": exit_price,
                "stopLoss": pos.signal.stop_loss,
                "takeProfit": pos.signal.take_profit,
                "positionSize": pos.current_size,
                "fillPrice": exit_price,
                "orderId": pos.position_id,
                "executionLatencyMs": result.execution_latency_ms,
                "status": result.status.value,
                "slippage": result.slippage,
                "spreadAtExecution": result.spread_at_execution,
                "executedAt": result.created_at,
                "exitReason": reason,
                "profitLoss": profit_loss,
            },
            "autopilot": True,
            "exitRule": reason,
        }

        try:
            self._redis.publish(TRADES_RESULTS_CHANNEL, json.dumps(payload))
            logger.debug(
                "[monitor] Published %s exit event for position %d",
                reason,
                pos.position_id,
            )
        except Exception:
            logger.exception(
                "[monitor] Failed to publish exit event for position %d",
                pos.position_id,
            )
