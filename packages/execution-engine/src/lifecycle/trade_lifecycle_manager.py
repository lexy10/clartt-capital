"""TradeLifecycleManager — manages the full trade lifecycle for autopilot trading.

Handles: entry placement → SL/TP attachment → position monitoring → exit execution.
Integrates with TradeExecutor for broker order placement and publishes results
to the ``trades:results`` pub/sub channel with autopilot marker data.
"""

from __future__ import annotations

import json
import logging
import time
from enum import Enum
from typing import Optional, TYPE_CHECKING

from redis import Redis

from src.executor.trade_executor import TradeExecutor
from src.models import (
    Signal,
    TradingAccount,
    TradeExecutionResult,
    TradeExecutionStatus,
)
from src.models.trading_event import (
    TradingEvent,
    TradingEventType,
    PositionOpenedPayload,
    PositionUpdatedPayload,
    PositionClosedPayload,
)

if TYPE_CHECKING:
    from src.events.event_publisher import EventPublisher

TRADES_RESULTS_CHANNEL = "trades:results"
SL_TP_MAX_RETRIES = 3

logger = logging.getLogger(__name__)


class PositionStatus(str, Enum):
    """Status of a monitored position."""

    OPEN = "open"
    CLOSED_SL = "closed_sl"
    CLOSED_TP = "closed_tp"
    CLOSED_EXIT = "closed_exit"
    UNKNOWN = "unknown"


class TradeLifecycleManager:
    """Manages the full trade lifecycle: entry → SL/TP attachment → monitoring → exit.

    Integrates with :class:`TradeExecutor` for broker order placement. After a
    successful entry fill, automatically attaches SL/TP orders (with retry up
    to 3 times). Publishes trade entry/exit results to the ``trades:results``
    pub/sub channel with autopilot marker data for WebSocket forwarding.
    """

    def __init__(
        self,
        executor: TradeExecutor,
        redis_client: Redis,
        event_publisher: Optional["EventPublisher"] = None,
    ) -> None:
        self._executor = executor
        self._redis = redis_client
        self._event_publisher = event_publisher

    def place_entry(
        self, signal: Signal, account: TradingAccount
    ) -> TradeExecutionResult:
        """Place an entry order via TradeExecutor and auto-attach SL/TP on fill.

        After a successful fill, SL/TP attachment is attempted with up to
        :data:`SL_TP_MAX_RETRIES` retries. The trade entry result is published
        to ``trades:results`` with autopilot marker data regardless of SL/TP
        attachment outcome.

        Args:
            signal: The trading signal to execute.
            account: The trading account to place the order on.

        Returns:
            The trade execution result from the entry order.
        """
        result = self._executor.execute(signal, account)

        if result.status == TradeExecutionStatus.FILLED:
            logger.info(
                "[lifecycle] Entry filled — order_id=%d, signal=%s, account=%s",
                result.order_id,
                signal.id,
                account.id,
            )

            # Auto-attach SL/TP with retry
            self.attach_sl_tp(
                position_id=result.order_id,
                stop_loss=signal.stop_loss,
                take_profit=signal.take_profit,
                account=account,
            )

            # Publish trade entry result with autopilot marker data
            self._publish_trade_event(
                result=result,
                signal=signal,
                account=account,
                event_type="trade_entry",
            )

            # Publish PositionOpened event (fire-and-forget)
            self._publish_position_opened(
                result=result, signal=signal, account=account,
            )
        else:
            logger.warning(
                "[lifecycle] Entry not filled — status=%s, signal=%s, account=%s, reason=%s",
                result.status.value,
                signal.id,
                account.id,
                result.rejection_reason,
            )

        return result

    def attach_sl_tp(
        self,
        position_id: int,
        stop_loss: float,
        take_profit: float,
        account: Optional[TradingAccount] = None,
    ) -> bool:
        """Attach stop-loss and take-profit orders to a filled position.

        Retries up to :data:`SL_TP_MAX_RETRIES` times on failure.

        Args:
            position_id: The broker order/position ID.
            stop_loss: The stop-loss price level.
            take_profit: The take-profit price level.
            account: The trading account (required for broker connection).

        Returns:
            True if SL/TP was attached successfully, False otherwise.
        """
        if account is None:
            logger.error(
                "[lifecycle] Cannot attach SL/TP — no account provided for position %d",
                position_id,
            )
            return False

        modifications = {"stop_loss": stop_loss, "take_profit": take_profit}

        for attempt in range(1, SL_TP_MAX_RETRIES + 1):
            success = self._executor.modify_order(
                order_id=position_id,
                account=account,
                modifications=modifications,
            )
            if success:
                logger.info(
                    "[lifecycle] SL/TP attached — position=%d, sl=%.2f, tp=%.2f",
                    position_id,
                    stop_loss,
                    take_profit,
                )
                return True

            logger.warning(
                "[lifecycle] SL/TP attachment attempt %d/%d failed for position %d",
                attempt,
                SL_TP_MAX_RETRIES,
                position_id,
            )

        logger.error(
            "[lifecycle] SL/TP attachment failed after %d attempts for position %d",
            SL_TP_MAX_RETRIES,
            position_id,
        )
        return False

    def monitor_position(self, position_id: int) -> PositionStatus:
        """Check the current status of a monitored position.

        Args:
            position_id: The broker position ID to monitor.

        Returns:
            The current position status.
        """
        # Position monitoring is handled by the broker's SL/TP orders.
        # This method provides a status check interface for the AccountWorker.
        # In a full implementation, this would query the broker for position state.
        logger.debug("[lifecycle] Monitoring position %d", position_id)
        return PositionStatus.OPEN

    def execute_exit(
        self,
        position_id: int,
        reason: str,
        account: Optional[TradingAccount] = None,
        signal: Optional[Signal] = None,
    ) -> TradeExecutionResult:
        """Close a position and publish the exit result.

        Args:
            position_id: The broker position ID to close.
            reason: The exit reason (e.g. 'stop_loss', 'take_profit', 'strategy_exit').
            account: The trading account (required for broker connection).
            signal: The original signal (optional, for marker data enrichment).

        Returns:
            The trade execution result from the close order.
        """
        if account is None:
            logger.error(
                "[lifecycle] Cannot exit position %d — no account provided",
                position_id,
            )
            return TradeExecutionResult(
                id="",
                signal_id=signal.id if signal else "",
                account_id="",
                order_id=position_id,
                fill_price=0.0,
                execution_latency_ms=0.0,
                status=TradeExecutionStatus.ERROR,
                rejection_reason="No account provided for exit",
                slippage=0.0,
                spread_at_execution=0.0,
                created_at="",
            )

        result = self._executor.close_position(position_id, account)

        # Enrich result with signal_id if available
        if signal and not result.signal_id:
            result = result.model_copy(update={"signal_id": signal.id})

        if result.status == TradeExecutionStatus.FILLED:
            logger.info(
                "[lifecycle] Position %d closed — reason=%s, account=%s",
                position_id,
                reason,
                account.id,
            )
            self._publish_trade_event(
                result=result,
                signal=signal,
                account=account,
                event_type="trade_exit",
                exit_reason=reason,
            )

            # Publish PositionClosed event (fire-and-forget)
            self._publish_position_closed(
                position_id=position_id,
                result=result,
                signal=signal,
                account=account,
                reason=reason,
            )
        else:
            logger.warning(
                "[lifecycle] Exit failed for position %d — status=%s, reason=%s",
                position_id,
                result.status.value,
                result.rejection_reason,
            )

        return result

    def _publish_trade_event(
        self,
        result: TradeExecutionResult,
        signal: Optional[Signal],
        account: TradingAccount,
        event_type: str,
        exit_reason: Optional[str] = None,
    ) -> None:
        """Publish trade entry/exit result to ``trades:results`` with autopilot marker data.

        The payload includes a ``type`` field (``trade_entry`` or ``trade_exit``)
        and a ``userId`` field for WebSocket room scoping by the Backend's
        RedisStreamService.
        """
        payload = {
            "type": event_type,
            "userId": account.user_id,
            "accountId": account.id,
            "trade": {
                "id": result.id,
                "signalId": result.signal_id,
                "direction": signal.direction.value if signal else "",
                "entryPrice": signal.entry_price if signal else 0.0,
                "stopLoss": signal.stop_loss if signal else 0.0,
                "takeProfit": signal.take_profit if signal else 0.0,
                "positionSize": signal.position_size if signal else 0.0,
                "fillPrice": result.fill_price,
                "orderId": result.order_id,
                "executionLatencyMs": result.execution_latency_ms,
                "status": result.status.value,
                "slippage": result.slippage,
                "spreadAtExecution": result.spread_at_execution,
                "executedAt": result.created_at,
            },
            "autopilot": True,
        }

        if exit_reason is not None:
            payload["trade"]["exitReason"] = exit_reason
            payload["trade"]["exitPrice"] = result.fill_price
            # Compute profitLoss from entry and exit prices
            entry_price = signal.entry_price if signal else 0.0
            exit_price = result.fill_price
            direction = signal.direction.value if signal else "BUY"
            if direction == "BUY":
                profit_loss = (exit_price - entry_price) * (signal.position_size if signal else 0.0)
            else:
                profit_loss = (entry_price - exit_price) * (signal.position_size if signal else 0.0)
            payload["trade"]["profitLoss"] = profit_loss

        try:
            self._redis.publish(
                TRADES_RESULTS_CHANNEL,
                json.dumps(payload),
            )
            logger.debug(
                "[lifecycle] Published %s event for order %d to %s",
                event_type,
                result.order_id,
                TRADES_RESULTS_CHANNEL,
            )
        except Exception:
            logger.exception(
                "[lifecycle] Failed to publish %s event for order %d",
                event_type,
                result.order_id,
            )

    # ------------------------------------------------------------------
    # Event-sourcing publishers (fire-and-forget)
    # ------------------------------------------------------------------

    def _publish_position_opened(
        self,
        result: TradeExecutionResult,
        signal: Signal,
        account: TradingAccount,
    ) -> None:
        """Publish PositionOpened event. Errors are logged, never raised."""
        if self._event_publisher is None:
            return
        try:
            payload = PositionOpenedPayload(
                position_id=str(result.order_id),
                account_id=account.id,
                trade_id=result.id,
                instrument=signal.instrument,
                direction=signal.direction.value,
                entry_price=result.fill_price,
                position_size=signal.position_size,
            )
            event = TradingEvent(
                event_type=TradingEventType.PositionOpened,
                aggregate_id=str(result.order_id),
                sequence_number=0,
                correlation_id=signal.id,
                payload=payload.model_dump(),
                source_service="execution-engine",
            )
            self._event_publisher.publish(event)
        except Exception:
            logger.exception(
                "[lifecycle] Failed to publish PositionOpened event for order %d",
                result.order_id,
            )

    def _publish_position_updated(
        self,
        position_id: int,
        account: TradingAccount,
        current_price: float,
        unrealized_pnl: float,
        update_reason: str,
        signal_id: Optional[str] = None,
    ) -> None:
        """Publish PositionUpdated event. Errors are logged, never raised."""
        if self._event_publisher is None:
            return
        try:
            payload = PositionUpdatedPayload(
                position_id=str(position_id),
                account_id=account.id,
                current_price=current_price,
                unrealized_pnl=unrealized_pnl,
                update_reason=update_reason,
            )
            event = TradingEvent(
                event_type=TradingEventType.PositionUpdated,
                aggregate_id=str(position_id),
                sequence_number=0,
                correlation_id=signal_id,
                payload=payload.model_dump(),
                source_service="execution-engine",
            )
            self._event_publisher.publish(event)
        except Exception:
            logger.exception(
                "[lifecycle] Failed to publish PositionUpdated event for position %d",
                position_id,
            )

    def _publish_position_closed(
        self,
        position_id: int,
        result: TradeExecutionResult,
        signal: Optional[Signal],
        account: TradingAccount,
        reason: str,
    ) -> None:
        """Publish PositionClosed event. Errors are logged, never raised."""
        if self._event_publisher is None:
            return
        try:
            # Compute realized PnL and duration
            entry_price = signal.entry_price if signal else 0.0
            exit_price = result.fill_price
            direction = signal.direction.value if signal else "BUY"
            position_size = signal.position_size if signal else 0.0
            if direction == "BUY":
                realized_pnl = (exit_price - entry_price) * position_size
            else:
                realized_pnl = (entry_price - exit_price) * position_size

            # Duration: approximate from created_at timestamps if available
            duration_seconds = 0.0
            if signal and signal.created_at and result.created_at:
                try:
                    from datetime import datetime, timezone

                    def _parse_ts(ts: str) -> float:
                        for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ"):
                            try:
                                return datetime.strptime(ts, fmt).replace(
                                    tzinfo=timezone.utc
                                ).timestamp()
                            except ValueError:
                                continue
                        return 0.0

                    t_open = _parse_ts(signal.created_at)
                    t_close = _parse_ts(result.created_at)
                    if t_open > 0 and t_close > 0:
                        duration_seconds = max(0.0, t_close - t_open)
                except Exception:
                    pass

            payload = PositionClosedPayload(
                position_id=str(position_id),
                account_id=account.id,
                exit_price=exit_price,
                realized_pnl=realized_pnl,
                close_reason=reason,
                duration_seconds=duration_seconds,
            )
            event = TradingEvent(
                event_type=TradingEventType.PositionClosed,
                aggregate_id=str(position_id),
                sequence_number=0,
                correlation_id=signal.id if signal else None,
                payload=payload.model_dump(),
                source_service="execution-engine",
            )
            self._event_publisher.publish(event)
        except Exception:
            logger.exception(
                "[lifecycle] Failed to publish PositionClosed event for position %d",
                position_id,
            )
