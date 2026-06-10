"""Unit tests for TradeLifecycleManager."""

import json
from unittest.mock import MagicMock, call, patch

import pytest

from src.executor.trade_executor import TradeExecutor, OrderResult
from src.lifecycle.trade_lifecycle_manager import (
    TradeLifecycleManager,
    PositionStatus,
    TRADES_RESULTS_CHANNEL,
    SL_TP_MAX_RETRIES,
)
from src.models import (
    Signal,
    SignalDirection,
    SignalMetadata,
    SignalMode,
    BOSType,
    Timeframe,
    TradingAccount,
    TradeExecutionResult,
    TradeExecutionStatus,
)
from src.models.trading_event import TradingEventType


@pytest.fixture
def mock_executor() -> MagicMock:
    return MagicMock(spec=TradeExecutor)


@pytest.fixture
def mock_redis() -> MagicMock:
    return MagicMock()


@pytest.fixture
def mock_event_publisher() -> MagicMock:
    return MagicMock()


@pytest.fixture
def manager(mock_executor, mock_redis, mock_event_publisher) -> TradeLifecycleManager:
    return TradeLifecycleManager(
        executor=mock_executor,
        redis_client=mock_redis,
        event_publisher=mock_event_publisher,
    )


@pytest.fixture
def manager_no_publisher(mock_executor, mock_redis) -> TradeLifecycleManager:
    """Manager without event publisher — backwards-compatible mode."""
    return TradeLifecycleManager(executor=mock_executor, redis_client=mock_redis)


@pytest.fixture
def signal() -> Signal:
    return Signal(
        id="sig-001",
        instrument="US30",
        direction=SignalDirection.BUY,
        entry_price=34500.0,
        stop_loss=34450.0,
        take_profit=34600.0,
        position_size=0.1,
        confidence_score=0.85,
        timeframe=Timeframe.FIFTEEN_MINUTES,
        order_block_id="ob-001",
        strategy_id="strat-001",
        mode=SignalMode.LIVE,
        metadata=SignalMetadata(
            bos_type=BOSType.BULLISH,
            liquidity_swept=True,
            session="new_york",
            spread_at_generation=2.5,
            volatility_ratio=1.2,
        ),
        created_at="2024-01-15T10:30:00Z",
    )


@pytest.fixture
def account() -> TradingAccount:
    return TradingAccount(
        id="acc-001",
        user_id="user-001",
        metaapi_account_id="test-account-id",
        label="Demo",
        is_active=True,
        equity=10000.0,
        balance=10000.0,
        open_positions=0,
        daily_loss=0.0,
        total_lot_exposure=0.0,
    )


def _filled_result(signal_id: str = "sig-001", account_id: str = "acc-001") -> TradeExecutionResult:
    return TradeExecutionResult(
        id="res-001",
        signal_id=signal_id,
        account_id=account_id,
        order_id=100001,
        fill_price=34501.0,
        execution_latency_ms=42.0,
        status=TradeExecutionStatus.FILLED,
        slippage=1.0,
        spread_at_execution=2.8,
        created_at="2024-01-15T10:30:01Z",
    )


def _error_result(signal_id: str = "sig-001", account_id: str = "acc-001") -> TradeExecutionResult:
    return TradeExecutionResult(
        id="res-002",
        signal_id=signal_id,
        account_id=account_id,
        order_id=0,
        fill_price=0.0,
        execution_latency_ms=100.0,
        status=TradeExecutionStatus.ERROR,
        rejection_reason="Connection failed",
        slippage=0.0,
        spread_at_execution=0.0,
        created_at="2024-01-15T10:30:01Z",
    )


# --- place_entry tests ---


class TestPlaceEntry:
    def test_successful_entry_attaches_sl_tp_and_publishes(
        self, manager, mock_executor, mock_redis, signal, account
    ):
        """On a filled entry, SL/TP should be attached and a trade_entry event published."""
        mock_executor.execute.return_value = _filled_result()
        mock_executor.modify_order.return_value = True

        result = manager.place_entry(signal, account)

        assert result.status == TradeExecutionStatus.FILLED
        # SL/TP attachment called
        mock_executor.modify_order.assert_called_once_with(
            order_id=100001,
            account=account,
            modifications={"stop_loss": 34450.0, "take_profit": 34600.0},
        )
        # Pub/sub publish called
        mock_redis.publish.assert_called_once()
        channel, payload_str = mock_redis.publish.call_args[0]
        assert channel == TRADES_RESULTS_CHANNEL
        payload = json.loads(payload_str)
        assert payload["type"] == "trade_entry"
        assert payload["userId"] == "user-001"
        assert payload["accountId"] == "acc-001"
        assert payload["autopilot"] is True
        assert payload["trade"]["signalId"] == "sig-001"
        assert payload["trade"]["direction"] == "BUY"

    def test_failed_entry_does_not_attach_or_publish(
        self, manager, mock_executor, mock_redis, signal, account
    ):
        """On a failed entry, no SL/TP attachment or pub/sub publish should occur."""
        mock_executor.execute.return_value = _error_result()

        result = manager.place_entry(signal, account)

        assert result.status == TradeExecutionStatus.ERROR
        mock_executor.modify_order.assert_not_called()
        mock_redis.publish.assert_not_called()


# --- attach_sl_tp tests ---


class TestAttachSlTp:
    def test_successful_first_attempt(self, manager, mock_executor, account):
        mock_executor.modify_order.return_value = True

        success = manager.attach_sl_tp(100001, 34450.0, 34600.0, account=account)

        assert success is True
        assert mock_executor.modify_order.call_count == 1

    def test_retries_on_failure_then_succeeds(self, manager, mock_executor, account):
        """Should retry and succeed on the second attempt."""
        mock_executor.modify_order.side_effect = [False, True]

        success = manager.attach_sl_tp(100001, 34450.0, 34600.0, account=account)

        assert success is True
        assert mock_executor.modify_order.call_count == 2

    def test_exhausts_retries(self, manager, mock_executor, account):
        """Should return False after all retries are exhausted."""
        mock_executor.modify_order.return_value = False

        success = manager.attach_sl_tp(100001, 34450.0, 34600.0, account=account)

        assert success is False
        assert mock_executor.modify_order.call_count == SL_TP_MAX_RETRIES

    def test_no_account_returns_false(self, manager):
        success = manager.attach_sl_tp(100001, 34450.0, 34600.0, account=None)
        assert success is False


# --- monitor_position tests ---


class TestMonitorPosition:
    def test_returns_open_status(self, manager):
        status = manager.monitor_position(100001)
        assert status == PositionStatus.OPEN


# --- execute_exit tests ---


class TestExecuteExit:
    def test_successful_exit_publishes_event(
        self, manager, mock_executor, mock_redis, signal, account
    ):
        close_result = TradeExecutionResult(
            id="res-exit-001",
            signal_id="",
            account_id="acc-001",
            order_id=100001,
            fill_price=34595.0,
            execution_latency_ms=30.0,
            status=TradeExecutionStatus.FILLED,
            slippage=0.0,
            spread_at_execution=0.0,
            created_at="2024-01-15T11:00:00Z",
        )
        mock_executor.close_position.return_value = close_result

        result = manager.execute_exit(
            position_id=100001,
            reason="take_profit",
            account=account,
            signal=signal,
        )

        assert result.status == TradeExecutionStatus.FILLED
        assert result.signal_id == "sig-001"  # enriched from signal
        mock_redis.publish.assert_called_once()
        channel, payload_str = mock_redis.publish.call_args[0]
        assert channel == TRADES_RESULTS_CHANNEL
        payload = json.loads(payload_str)
        assert payload["type"] == "trade_exit"
        assert payload["userId"] == "user-001"
        assert payload["trade"]["exitReason"] == "take_profit"
        assert payload["trade"]["exitPrice"] == 34595.0

    def test_failed_exit_does_not_publish(
        self, manager, mock_executor, mock_redis, account
    ):
        mock_executor.close_position.return_value = _error_result()

        result = manager.execute_exit(
            position_id=100001, reason="strategy_exit", account=account
        )

        assert result.status == TradeExecutionStatus.ERROR
        mock_redis.publish.assert_not_called()

    def test_no_account_returns_error(self, manager, signal):
        result = manager.execute_exit(
            position_id=100001, reason="strategy_exit", account=None, signal=signal
        )

        assert result.status == TradeExecutionStatus.ERROR
        assert result.rejection_reason == "No account provided for exit"

    def test_redis_publish_failure_does_not_raise(
        self, manager, mock_executor, mock_redis, signal, account
    ):
        """Redis publish failure should be logged but not raise."""
        close_result = _filled_result()
        close_result = close_result.model_copy(
            update={"signal_id": "", "order_id": 100001}
        )
        mock_executor.close_position.return_value = close_result
        mock_redis.publish.side_effect = Exception("Redis down")

        # Should not raise
        result = manager.execute_exit(
            position_id=100001,
            reason="stop_loss",
            account=account,
            signal=signal,
        )
        assert result.status == TradeExecutionStatus.FILLED


# --- Event publishing tests ---


class TestPositionEventPublishing:
    """Tests for PositionOpened/PositionClosed event publishing via EventPublisher."""

    def test_position_opened_event_published_on_fill(
        self, manager, mock_executor, mock_event_publisher, signal, account
    ):
        """PositionOpened event should be published when entry is filled."""
        mock_executor.execute.return_value = _filled_result()
        mock_executor.modify_order.return_value = True

        manager.place_entry(signal, account)

        mock_event_publisher.publish.assert_called_once()
        event = mock_event_publisher.publish.call_args[0][0]
        assert event.event_type == TradingEventType.PositionOpened
        assert event.aggregate_id == "100001"  # position_id
        assert event.correlation_id == "sig-001"  # signal_id
        assert event.payload["position_id"] == "100001"
        assert event.payload["account_id"] == "acc-001"
        assert event.payload["instrument"] == "US30"
        assert event.payload["direction"] == "BUY"
        assert event.payload["entry_price"] == 34501.0
        assert event.payload["position_size"] == 0.1

    def test_no_event_published_on_failed_entry(
        self, manager, mock_executor, mock_event_publisher, signal, account
    ):
        """No PositionOpened event when entry fails."""
        mock_executor.execute.return_value = _error_result()

        manager.place_entry(signal, account)

        mock_event_publisher.publish.assert_not_called()

    def test_position_closed_event_published_on_exit(
        self, manager, mock_executor, mock_event_publisher, signal, account
    ):
        """PositionClosed event should be published when position is closed."""
        close_result = TradeExecutionResult(
            id="res-exit-001",
            signal_id="",
            account_id="acc-001",
            order_id=100001,
            fill_price=34595.0,
            execution_latency_ms=30.0,
            status=TradeExecutionStatus.FILLED,
            slippage=0.0,
            spread_at_execution=0.0,
            created_at="2024-01-15T11:00:00Z",
        )
        mock_executor.close_position.return_value = close_result

        manager.execute_exit(
            position_id=100001,
            reason="take_profit",
            account=account,
            signal=signal,
        )

        mock_event_publisher.publish.assert_called_once()
        event = mock_event_publisher.publish.call_args[0][0]
        assert event.event_type == TradingEventType.PositionClosed
        assert event.aggregate_id == "100001"
        assert event.correlation_id == "sig-001"
        assert event.payload["position_id"] == "100001"
        assert event.payload["account_id"] == "acc-001"
        assert event.payload["exit_price"] == 34595.0
        assert event.payload["close_reason"] == "take_profit"
        assert event.payload["realized_pnl"] == pytest.approx(
            (34595.0 - 34500.0) * 0.1
        )

    def test_no_event_published_on_failed_exit(
        self, manager, mock_executor, mock_event_publisher, account
    ):
        """No PositionClosed event when exit fails."""
        mock_executor.close_position.return_value = _error_result()

        manager.execute_exit(position_id=100001, reason="strategy_exit", account=account)

        mock_event_publisher.publish.assert_not_called()

    def test_event_publisher_failure_does_not_block(
        self, manager, mock_executor, mock_event_publisher, signal, account
    ):
        """EventPublisher failure should not block the position management flow."""
        mock_executor.execute.return_value = _filled_result()
        mock_executor.modify_order.return_value = True
        mock_event_publisher.publish.side_effect = Exception("Redis down")

        # Should not raise
        result = manager.place_entry(signal, account)
        assert result.status == TradeExecutionStatus.FILLED

    def test_no_publisher_works_without_error(
        self, manager_no_publisher, mock_executor, signal, account
    ):
        """Manager without event_publisher should work normally (backwards compatible)."""
        mock_executor.execute.return_value = _filled_result()
        mock_executor.modify_order.return_value = True

        result = manager_no_publisher.place_entry(signal, account)
        assert result.status == TradeExecutionStatus.FILLED
