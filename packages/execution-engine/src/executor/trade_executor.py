"""Trade Executor — wraps MetaApi SDK calls with retry logic and result recording.

All broker calls are protected by an "execution-to-metaapi" circuit breaker.
When the breaker is Open, trade execution returns BROKER_UNAVAILABLE immediately.
"""

from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Optional, Protocol, TYPE_CHECKING

from src.circuit_breaker import CircuitBreaker
from src.models import (
    Signal,
    TradingAccount,
    TradeExecutionResult,
    TradeExecutionStatus,
)
from src.models.trading_event import (
    TradingEvent,
    TradingEventType,
    TradeRequestedPayload,
    TradeExecutedPayload,
    TradeFailedPayload,
)

if TYPE_CHECKING:
    from src.events.event_publisher import EventPublisher

logger = logging.getLogger(__name__)

MAX_RETRIES = 3
BACKOFF_SECONDS = [1, 2, 4]

# Module-level reference so the health endpoint can read CB state
_metaapi_circuit_breaker: Optional[CircuitBreaker] = None


def get_metaapi_circuit_breaker() -> Optional[CircuitBreaker]:
    """Return the execution-to-metaapi circuit breaker (set during TradeExecutor init)."""
    return _metaapi_circuit_breaker


class OrderResult:
    """Represents the result returned by the broker for an order request."""

    def __init__(
        self,
        success: bool,
        order_id: int = 0,
        fill_price: float = 0.0,
        error_code: int = 0,
        error_message: str = "",
        volume: float = 0.0,
        bid: float = 0.0,
        ask: float = 0.0,
    ):
        self.success = success
        self.order_id = order_id
        self.fill_price = fill_price
        self.error_code = error_code
        self.error_message = error_message
        self.volume = volume
        self.bid = bid
        self.ask = ask


class BrokerClient(Protocol):
    """Protocol for broker API abstraction — allows mocking in tests."""

    def connect(self, account_id: str) -> bool: ...

    def send_order(
        self,
        instrument: str,
        direction: str,
        volume: float,
        price: float,
        sl: float,
        tp: float,
    ) -> OrderResult: ...

    def modify_position(
        self,
        order_id: int,
        sl: Optional[float] = None,
        tp: Optional[float] = None,
    ) -> OrderResult: ...

    def close_position_by_id(self, position_id: int) -> OrderResult: ...

    def get_symbol_info_tick(self, instrument: str) -> Optional[dict]: ...


class BrokerConnectionError(Exception):
    """Raised when broker connection fails after all retries."""
    pass


class TradeExecutor:
    """Executes trades via broker API, recording fill price, latency, slippage, and spread.

    Routing modes:
    - **Single-client mode (legacy):** pass ``broker_client=...``; all trades go through it.
    - **Router mode (multi-broker):** pass ``broker_router=...`` and optionally
      ``instrument_registry=...``; each call resolves the right client based on
      ``signal.instrument``'s category and any per-account / per-instrument override.

    When a circuit breaker is provided, all broker calls go through it.
    If the breaker is Open, execute() returns a BROKER_UNAVAILABLE rejection
    without contacting the broker.
    """

    def __init__(
        self,
        broker_client: Optional[BrokerClient] = None,
        sleep_fn=time.sleep,
        circuit_breaker: Optional[CircuitBreaker] = None,
        event_publisher: Optional["EventPublisher"] = None,
        broker_router=None,
        instrument_registry=None,
    ):
        global _metaapi_circuit_breaker

        if broker_client is None and broker_router is None:
            raise ValueError("TradeExecutor requires broker_client or broker_router")

        self._client = broker_client            # Legacy single client (may be None in router mode)
        self._router = broker_router            # Optional BrokerRouter
        self._registry = instrument_registry    # Optional InstrumentRegistry
        self._sleep = sleep_fn
        self._cb = circuit_breaker
        self._event_publisher = event_publisher
        if circuit_breaker is not None:
            _metaapi_circuit_breaker = circuit_breaker

    # ------------------------------------------------------------------
    # Client resolution
    # ------------------------------------------------------------------

    def _resolve_client(
        self,
        instrument: str,
        account: Optional[TradingAccount] = None,
    ) -> BrokerClient:
        """Pick the right broker client for an instrument + account.

        Priority (highest wins):
          1. Account-level explicit provider override
          2. Instrument-level explicit provider override
          3. Category default (synthetic -> Deriv, forex/index -> MetaAPI, etc.)

        Falls back to the legacy single ``_client`` if router resolution fails
        or no router was configured. This keeps existing test code working.
        """
        if self._router is None:
            if self._client is None:
                raise RuntimeError("TradeExecutor has no broker_client and no router")
            return self._client

        # Account override
        account_override = None
        if account is not None:
            raw = getattr(account, "broker_provider", None)
            if raw:
                try:
                    from src.executor.clients.base import BrokerProvider
                    account_override = BrokerProvider(raw)
                except (ValueError, ImportError):
                    account_override = None

        # Instrument metadata
        category = None
        instrument_override = None
        if self._registry is not None:
            info = self._registry.get(instrument)
            category = info.category
            instrument_override = info.preferred_provider
        else:
            # No registry — fall back to symbol-based auto-detection
            from src.executor.clients.base import detect_category
            category = detect_category(instrument)

        client = self._router.get_client_for(
            instrument_category=category,
            account_provider_override=account_override,
            instrument_provider_override=instrument_override,
        )
        if client is None:
            # No router match — fall back to legacy client if we have one
            if self._client is not None:
                logger.warning(
                    "TradeExecutor: router resolution failed for %s (category=%s), using legacy client",
                    instrument, category,
                )
                return self._client
            raise RuntimeError(
                f"No broker client found for instrument {instrument} (category={category})"
            )
        return client

    # ------------------------------------------------------------------
    # Event publishing helpers (fire-and-forget)
    # ------------------------------------------------------------------

    def _build_execution_context(self, spread: float, tick: Optional[dict] = None) -> dict:
        """Build an ExecutionContextSnapshot dict."""
        bid = tick.get("bid", 0.0) if tick else 0.0
        ask = tick.get("ask", 0.0) if tick else 0.0
        return {
            "broker_connected": True,
            "current_spread": spread,
            "bid_price": bid,
            "ask_price": ask,
            "queue_depth": 0,
        }

    def _publish_trade_requested(
        self, signal: Signal, account: TradingAccount,
    ) -> None:
        """Publish TradeRequested event before broker order submission."""
        if self._event_publisher is None:
            return
        try:
            payload = TradeRequestedPayload(
                signal_id=signal.id,
                account_id=account.id,
                instrument=signal.broker_symbol or signal.instrument,
                direction=signal.direction.value,
                requested_size=signal.position_size,
                broker_order_id=None,
            )
            event = TradingEvent(
                event_type=TradingEventType.TradeRequested,
                aggregate_id=signal.id,
                sequence_number=0,
                payload=payload.model_dump(),
                source_service="execution-engine",
            )
            self._event_publisher.publish(event)
        except Exception:
            logger.exception(
                "Failed to publish TradeRequested event for signal %s", signal.id,
            )

    def _publish_trade_executed(
        self,
        signal: Signal,
        account: TradingAccount,
        result: TradeExecutionResult,
        spread: float,
        tick: Optional[dict],
    ) -> None:
        """Publish TradeExecuted event on successful fill."""
        if self._event_publisher is None:
            return
        try:
            payload = TradeExecutedPayload(
                signal_id=signal.id,
                account_id=account.id,
                trade_id=result.id,
                fill_price=result.fill_price,
                position_size=signal.position_size,
                execution_latency_ms=result.execution_latency_ms,
                slippage=result.slippage,
                spread_at_execution=spread,
            )
            event = TradingEvent(
                event_type=TradingEventType.TradeExecuted,
                aggregate_id=signal.id,
                sequence_number=0,
                payload=payload.model_dump(),
                context_snapshot=self._build_execution_context(spread, tick),
                source_service="execution-engine",
            )
            self._event_publisher.publish(event)
        except Exception:
            logger.exception(
                "Failed to publish TradeExecuted event for signal %s", signal.id,
            )

    def _publish_trade_failed(
        self,
        signal: Signal,
        account: TradingAccount,
        failure_reason: str,
        error_code: str,
        retry_count: int,
        spread: float,
        tick: Optional[dict],
        broker_connected: bool = True,
    ) -> None:
        """Publish TradeFailed event on execution failure."""
        if self._event_publisher is None:
            return
        try:
            payload = TradeFailedPayload(
                signal_id=signal.id,
                account_id=account.id,
                failure_reason=failure_reason,
                error_code=error_code,
                retry_count=retry_count,
            )
            ctx = self._build_execution_context(spread, tick)
            ctx["broker_connected"] = broker_connected
            event = TradingEvent(
                event_type=TradingEventType.TradeFailed,
                aggregate_id=signal.id,
                sequence_number=0,
                payload=payload.model_dump(),
                context_snapshot=ctx,
                source_service="execution-engine",
            )
            self._event_publisher.publish(event)
        except Exception:
            logger.exception(
                "Failed to publish TradeFailed event for signal %s", signal.id,
            )

    def _connect_with_retry(
        self,
        account: TradingAccount,
        client: Optional[BrokerClient] = None,
    ) -> BrokerClient:
        """Connect to broker with exponential backoff retry (3 attempts).

        For Deriv-direct accounts (account.deriv_api_token set), uses the
        per-account token via ``connect_with_token()``. For MetaAPI accounts,
        uses the legacy ``connect(metaapi_account_id)``.
        """
        target = client if client is not None else self._client
        if target is None:
            raise BrokerConnectionError("No broker client available for connect")

        # Pick connection identity per provider
        deriv_token = getattr(account, "deriv_api_token", None)
        deriv_login = getattr(account, "deriv_login_id", None)
        use_deriv_auth = bool(deriv_token and deriv_login and hasattr(target, "connect_with_token"))

        for attempt in range(MAX_RETRIES):
            try:
                if use_deriv_auth:
                    connected = target.connect_with_token(deriv_login, deriv_token)
                    identity = deriv_login
                else:
                    connected = target.connect(account.metaapi_account_id)
                    identity = account.metaapi_account_id
                if connected:
                    return target
                raise BrokerConnectionError(
                    f"Broker connect returned False for account={identity}"
                )
            except BrokerConnectionError:
                raise
            except Exception as e:
                logger.warning(
                    "Broker connection attempt %d/%d failed for account %s: %s",
                    attempt + 1,
                    MAX_RETRIES,
                    account.id,
                    e,
                )
                if attempt < MAX_RETRIES - 1:
                    self._sleep(BACKOFF_SECONDS[attempt])
                else:
                    raise BrokerConnectionError(
                        f"Failed to connect to broker after {MAX_RETRIES} attempts: {e}"
                    ) from e

    def _get_spread(self, instrument: str, client: Optional[BrokerClient] = None) -> float:
        """Get current spread for the instrument."""
        target = client if client is not None else self._client
        if target is None:
            return 0.0
        tick = target.get_symbol_info_tick(instrument)
        if tick and "ask" in tick and "bid" in tick:
            return tick["ask"] - tick["bid"]
        return 0.0

    def execute(
        self, signal: Signal, account: TradingAccount
    ) -> TradeExecutionResult:
        """Submit order to broker, record fill price, latency, order ID, slippage, spread.

        When the circuit breaker is Open, returns a BROKER_UNAVAILABLE rejection
        immediately without contacting the broker.
        """
        start_time = time.monotonic()
        result_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()

        if self._cb is not None:
            def _broker_call() -> TradeExecutionResult:
                return self._execute_broker_call(
                    signal, account, start_time, result_id, now,
                )

            def _fallback() -> TradeExecutionResult:
                elapsed_ms = (time.monotonic() - start_time) * 1000
                logger.warning(
                    "Circuit breaker OPEN — rejecting signal %s with BROKER_UNAVAILABLE",
                    signal.id,
                )
                self._publish_trade_failed(
                    signal, account,
                    failure_reason="BROKER_UNAVAILABLE",
                    error_code="CIRCUIT_BREAKER_OPEN",
                    retry_count=0,
                    spread=0.0,
                    tick=None,
                    broker_connected=False,
                )
                return TradeExecutionResult(
                    id=result_id,
                    signal_id=signal.id,
                    account_id=account.id,
                    order_id=0,
                    fill_price=0.0,
                    execution_latency_ms=elapsed_ms,
                    status=TradeExecutionStatus.REJECTED,
                    rejection_reason="BROKER_UNAVAILABLE",
                    slippage=0.0,
                    spread_at_execution=0.0,
                    created_at=now,
                )

            return self._cb.execute(_broker_call, _fallback)

        # No circuit breaker — direct execution (backwards compatible)
        return self._execute_broker_call(signal, account, start_time, result_id, now)

    def _execute_broker_call(
        self,
        signal: Signal,
        account: TradingAccount,
        start_time: float,
        result_id: str,
        now: str,
    ) -> TradeExecutionResult:
        """Core broker interaction: resolve client, connect, get spread, submit order."""

        # Resolve the right client for this signal's instrument
        trading_symbol = signal.broker_symbol or signal.instrument
        try:
            client = self._resolve_client(signal.instrument, account)
        except Exception as e:
            elapsed_ms = (time.monotonic() - start_time) * 1000
            logger.error("No broker client for %s: %s", signal.instrument, e)
            return TradeExecutionResult(
                id=result_id, signal_id=signal.id, account_id=account.id,
                order_id=0, fill_price=0.0, execution_latency_ms=elapsed_ms,
                status=TradeExecutionStatus.ERROR,
                rejection_reason=f"No broker: {e}",
                slippage=0.0, spread_at_execution=0.0, created_at=now,
            )

        # Attempt connection with retry (using resolved client)
        try:
            client = self._connect_with_retry(account, client=client)
        except BrokerConnectionError as e:
            elapsed_ms = (time.monotonic() - start_time) * 1000
            logger.error(
                "Broker connection failed for account %s: %s", account.id, e
            )
            self._publish_trade_failed(
                signal, account,
                failure_reason=f"Connection failed: {e}",
                error_code="CONNECTION_FAILED",
                retry_count=MAX_RETRIES,
                spread=0.0,
                tick=None,
                broker_connected=False,
            )
            return TradeExecutionResult(
                id=result_id,
                signal_id=signal.id,
                account_id=account.id,
                order_id=0,
                fill_price=0.0,
                execution_latency_ms=elapsed_ms,
                status=TradeExecutionStatus.ERROR,
                rejection_reason=f"Connection failed: {e}",
                slippage=0.0,
                spread_at_execution=0.0,
                created_at=now,
            )

        # Get current spread and tick data (use resolved client)
        tick = client.get_symbol_info_tick(trading_symbol)
        spread = 0.0
        if tick and "ask" in tick and "bid" in tick:
            spread = tick["ask"] - tick["bid"]

        # Publish TradeRequested event before broker order submission
        self._publish_trade_requested(signal, account)

        # Submit order with retry logic (use resolved client)
        order_result: Optional[OrderResult] = None
        last_error: Optional[Exception] = None

        for attempt in range(MAX_RETRIES):
            try:
                order_result = client.send_order(
                    instrument=trading_symbol,
                    direction=signal.direction.value,
                    volume=signal.position_size,
                    price=signal.entry_price,
                    sl=signal.stop_loss,
                    tp=signal.take_profit,
                )
                break
            except Exception as e:
                last_error = e
                logger.warning(
                    "Order submission attempt %d/%d failed for signal %s: %s",
                    attempt + 1,
                    MAX_RETRIES,
                    signal.id,
                    e,
                )
                if attempt < MAX_RETRIES - 1:
                    self._sleep(BACKOFF_SECONDS[attempt])

        elapsed_ms = (time.monotonic() - start_time) * 1000

        # All retries exhausted
        if order_result is None:
            logger.error(
                "Order submission failed after %d attempts for signal %s: %s",
                MAX_RETRIES,
                signal.id,
                last_error,
            )
            self._publish_trade_failed(
                signal, account,
                failure_reason=f"Order submission failed after {MAX_RETRIES} attempts: {last_error}",
                error_code="ORDER_SUBMISSION_FAILED",
                retry_count=MAX_RETRIES,
                spread=spread,
                tick=tick,
            )
            return TradeExecutionResult(
                id=result_id,
                signal_id=signal.id,
                account_id=account.id,
                order_id=0,
                fill_price=0.0,
                execution_latency_ms=elapsed_ms,
                status=TradeExecutionStatus.ERROR,
                rejection_reason=f"Order submission failed after {MAX_RETRIES} attempts: {last_error}",
                slippage=0.0,
                spread_at_execution=spread,
                created_at=now,
            )

        # Broker rejected the order
        if not order_result.success:
            logger.warning(
                "Order rejected for signal %s — broker error code: %d, message: %s",
                signal.id,
                order_result.error_code,
                order_result.error_message,
            )
            self._publish_trade_failed(
                signal, account,
                failure_reason=f"Broker error {order_result.error_code}: {order_result.error_message}",
                error_code=str(order_result.error_code),
                retry_count=0,
                spread=spread,
                tick=tick,
            )
            return TradeExecutionResult(
                id=result_id,
                signal_id=signal.id,
                account_id=account.id,
                order_id=0,
                fill_price=0.0,
                execution_latency_ms=elapsed_ms,
                status=TradeExecutionStatus.REJECTED,
                rejection_reason=f"Broker error {order_result.error_code}: {order_result.error_message}",
                slippage=0.0,
                spread_at_execution=spread,
                created_at=now,
            )

        # Success — compute slippage
        slippage = abs(order_result.fill_price - signal.entry_price)

        logger.info(
            "Order filled for signal %s — order_id=%d, fill_price=%.2f, latency=%.1fms, slippage=%.2f",
            signal.id,
            order_result.order_id,
            order_result.fill_price,
            elapsed_ms,
            slippage,
        )

        exec_result = TradeExecutionResult(
            id=result_id,
            signal_id=signal.id,
            account_id=account.id,
            order_id=order_result.order_id,
            fill_price=order_result.fill_price,
            execution_latency_ms=elapsed_ms,
            status=TradeExecutionStatus.FILLED,
            slippage=slippage,
            spread_at_execution=spread,
            created_at=now,
        )

        # Publish TradeExecuted event on successful fill
        self._publish_trade_executed(signal, account, exec_result, spread, tick)

        return exec_result

    def modify_order(
        self,
        order_id: int,
        account: TradingAccount,
        modifications: dict,
        instrument: Optional[str] = None,
    ) -> bool:
        """Modify SL/TP on an existing order. Returns True on success.

        If ``instrument`` is provided, routes to the right broker by category.
        Otherwise falls back to the legacy single client.
        """
        try:
            if instrument is not None:
                client = self._resolve_client(instrument, account)
            else:
                client = self._client if self._client is not None else self._resolve_client("", account)
            client = self._connect_with_retry(account, client=client)
        except BrokerConnectionError as e:
            logger.error(
                "Cannot modify order %d — connection failed: %s", order_id, e
            )
            return False
        except Exception as e:
            logger.error("Cannot modify order %d — no client: %s", order_id, e)
            return False

        try:
            result = client.modify_position(
                order_id=order_id,
                sl=modifications.get("stop_loss"),
                tp=modifications.get("take_profit"),
            )
            if result.success:
                logger.info("Order %d modified successfully", order_id)
                return True
            else:
                logger.warning(
                    "Failed to modify order %d — broker error %d: %s",
                    order_id,
                    result.error_code,
                    result.error_message,
                )
                return False
        except Exception as e:
            logger.error("Exception modifying order %d: %s", order_id, e)
            return False

    def close_position(
        self,
        position_id: int,
        account: TradingAccount,
        instrument: Optional[str] = None,
    ) -> TradeExecutionResult:
        """Close a position and return the execution result.

        If ``instrument`` is provided, routes to the right broker by category.
        Otherwise falls back to the legacy single client.
        """
        start_time = time.monotonic()
        result_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()

        try:
            if instrument is not None:
                client = self._resolve_client(instrument, account)
            else:
                client = self._client if self._client is not None else self._resolve_client("", account)
            client = self._connect_with_retry(account, client=client)
        except BrokerConnectionError as e:
            elapsed_ms = (time.monotonic() - start_time) * 1000
            logger.error(
                "Cannot close position %d — connection failed: %s",
                position_id,
                e,
            )
            return TradeExecutionResult(
                id=result_id,
                signal_id="",
                account_id=account.id,
                order_id=position_id,
                fill_price=0.0,
                execution_latency_ms=elapsed_ms,
                status=TradeExecutionStatus.ERROR,
                rejection_reason=f"Connection failed: {e}",
                slippage=0.0,
                spread_at_execution=0.0,
                created_at=now,
            )
        except Exception as e:
            elapsed_ms = (time.monotonic() - start_time) * 1000
            return TradeExecutionResult(
                id=result_id, signal_id="", account_id=account.id,
                order_id=position_id, fill_price=0.0, execution_latency_ms=elapsed_ms,
                status=TradeExecutionStatus.ERROR,
                rejection_reason=f"No broker: {e}",
                slippage=0.0, spread_at_execution=0.0, created_at=now,
            )

        try:
            result = client.close_position_by_id(position_id)
        except Exception as e:
            elapsed_ms = (time.monotonic() - start_time) * 1000
            logger.error(
                "Exception closing position %d: %s", position_id, e
            )
            return TradeExecutionResult(
                id=result_id,
                signal_id="",
                account_id=account.id,
                order_id=position_id,
                fill_price=0.0,
                execution_latency_ms=elapsed_ms,
                status=TradeExecutionStatus.ERROR,
                rejection_reason=f"Close failed: {e}",
                slippage=0.0,
                spread_at_execution=0.0,
                created_at=now,
            )

        elapsed_ms = (time.monotonic() - start_time) * 1000

        if not result.success:
            logger.warning(
                "Failed to close position %d — broker error %d: %s",
                position_id,
                result.error_code,
                result.error_message,
            )
            return TradeExecutionResult(
                id=result_id,
                signal_id="",
                account_id=account.id,
                order_id=position_id,
                fill_price=0.0,
                execution_latency_ms=elapsed_ms,
                status=TradeExecutionStatus.REJECTED,
                rejection_reason=f"Broker error {result.error_code}: {result.error_message}",
                slippage=0.0,
                spread_at_execution=0.0,
                created_at=now,
            )

        logger.info(
            "Position %d closed — fill_price=%.2f, latency=%.1fms",
            position_id,
            result.fill_price,
            elapsed_ms,
        )

        return TradeExecutionResult(
            id=result_id,
            signal_id="",
            account_id=account.id,
            order_id=position_id,
            fill_price=result.fill_price,
            execution_latency_ms=elapsed_ms,
            status=TradeExecutionStatus.FILLED,
            slippage=0.0,
            spread_at_execution=0.0,
            created_at=now,
        )
