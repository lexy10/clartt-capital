"""Unit tests for TradeExecutor using a mock broker client."""

import pytest
from unittest.mock import MagicMock, call

from src.executor.trade_executor import (
    TradeExecutor,
    OrderResult,
    BrokerConnectionError,
    MAX_RETRIES,
)
from src.models import (
    Signal,
    TradingAccount,
    TradeExecutionStatus,
)
from src.models.trading_event import TradingEventType


def _make_mock_client(
    connect_ok=True,
    order_success=True,
    fill_price=34501.0,
    order_id=100001,
    error_code=0,
    error_message="",
    bid=34500.0,
    ask=34503.0,
):
    """Create a mock broker client with configurable behavior."""
    client = MagicMock()
    client.connect.return_value = connect_ok
    client.send_order.return_value = OrderResult(
        success=order_success,
        order_id=order_id,
        fill_price=fill_price,
        error_code=error_code,
        error_message=error_message,
    )
    client.modify_position.return_value = OrderResult(success=True)
    client.close_position_by_id.return_value = OrderResult(
        success=True, fill_price=34510.0, order_id=100001
    )
    client.get_symbol_info_tick.return_value = {"bid": bid, "ask": ask}
    return client


def _make_mock_publisher():
    """Create a mock EventPublisher."""
    publisher = MagicMock()
    publisher.publish = MagicMock()
    return publisher


class TestExecute:
    """Tests for TradeExecutor.execute()."""

    def test_successful_execution(self, sample_signal, sample_trading_account):
        client = _make_mock_client(fill_price=34501.0)
        executor = TradeExecutor(client, sleep_fn=lambda _: None)

        result = executor.execute(sample_signal, sample_trading_account)

        assert result.status == TradeExecutionStatus.FILLED
        assert result.order_id == 100001
        assert result.fill_price == 34501.0
        assert result.signal_id == sample_signal.id
        assert result.account_id == sample_trading_account.id
        assert result.execution_latency_ms >= 0
        assert result.spread_at_execution == 3.0  # ask - bid = 34503 - 34500
        assert result.slippage == abs(34501.0 - sample_signal.entry_price)

    def test_records_slippage(self, sample_signal, sample_trading_account):
        client = _make_mock_client(fill_price=34505.0)
        executor = TradeExecutor(client, sleep_fn=lambda _: None)

        result = executor.execute(sample_signal, sample_trading_account)

        assert result.slippage == abs(34505.0 - sample_signal.entry_price)

    def test_connection_failure_returns_error(self, sample_signal, sample_trading_account):
        client = _make_mock_client()
        client.connect.return_value = False
        executor = TradeExecutor(client, sleep_fn=lambda _: None)

        result = executor.execute(sample_signal, sample_trading_account)

        assert result.status == TradeExecutionStatus.ERROR
        assert "Connection failed" in result.rejection_reason

    def test_order_rejection_logs_broker_error(self, sample_signal, sample_trading_account):
        client = _make_mock_client(
            order_success=False, error_code=10013, error_message="Invalid volume"
        )
        executor = TradeExecutor(client, sleep_fn=lambda _: None)

        result = executor.execute(sample_signal, sample_trading_account)

        assert result.status == TradeExecutionStatus.REJECTED
        assert "10013" in result.rejection_reason
        assert "Invalid volume" in result.rejection_reason

    def test_order_submission_retries_on_exception(self, sample_signal, sample_trading_account):
        client = _make_mock_client()
        # Fail twice, succeed on third attempt
        client.send_order.side_effect = [
            ConnectionError("network error"),
            ConnectionError("network error"),
            OrderResult(success=True, order_id=100002, fill_price=34501.0),
        ]
        sleep_calls = []
        executor = TradeExecutor(client, sleep_fn=lambda s: sleep_calls.append(s))

        result = executor.execute(sample_signal, sample_trading_account)

        assert result.status == TradeExecutionStatus.FILLED
        assert result.order_id == 100002
        assert client.send_order.call_count == 3
        assert sleep_calls == [1, 2]  # exponential backoff

    def test_all_order_retries_exhausted(self, sample_signal, sample_trading_account):
        client = _make_mock_client()
        client.send_order.side_effect = ConnectionError("persistent failure")
        executor = TradeExecutor(client, sleep_fn=lambda _: None)

        result = executor.execute(sample_signal, sample_trading_account)

        assert result.status == TradeExecutionStatus.ERROR
        assert f"after {MAX_RETRIES} attempts" in result.rejection_reason
        assert client.send_order.call_count == MAX_RETRIES

    def test_connection_retry_on_exception(self, sample_signal, sample_trading_account):
        client = _make_mock_client()
        # connect raises exception twice, then succeeds
        client.connect.side_effect = [
            ConnectionError("timeout"),
            ConnectionError("timeout"),
            True,
        ]
        sleep_calls = []
        executor = TradeExecutor(client, sleep_fn=lambda s: sleep_calls.append(s))

        result = executor.execute(sample_signal, sample_trading_account)

        assert result.status == TradeExecutionStatus.FILLED
        assert client.connect.call_count == 3
        assert sleep_calls == [1, 2]

    def test_connection_all_retries_exhausted(self, sample_signal, sample_trading_account):
        client = _make_mock_client()
        client.connect.side_effect = ConnectionError("persistent timeout")
        executor = TradeExecutor(client, sleep_fn=lambda _: None)

        result = executor.execute(sample_signal, sample_trading_account)

        assert result.status == TradeExecutionStatus.ERROR
        assert "Connection failed" in result.rejection_reason

    def test_spread_recorded_from_tick(self, sample_signal, sample_trading_account):
        client = _make_mock_client(bid=34500.0, ask=34505.0)
        executor = TradeExecutor(client, sleep_fn=lambda _: None)

        result = executor.execute(sample_signal, sample_trading_account)

        assert result.spread_at_execution == 5.0

    def test_spread_zero_when_tick_unavailable(self, sample_signal, sample_trading_account):
        client = _make_mock_client()
        client.get_symbol_info_tick.return_value = None
        executor = TradeExecutor(client, sleep_fn=lambda _: None)

        result = executor.execute(sample_signal, sample_trading_account)

        assert result.spread_at_execution == 0.0

    def test_result_has_valid_uuid_and_timestamp(self, sample_signal, sample_trading_account):
        client = _make_mock_client()
        executor = TradeExecutor(client, sleep_fn=lambda _: None)

        result = executor.execute(sample_signal, sample_trading_account)

        assert len(result.id) == 36  # UUID format
        assert "T" in result.created_at  # ISO 8601


class TestModifyOrder:
    """Tests for TradeExecutor.modify_order()."""

    def test_successful_modification(self, sample_trading_account):
        client = _make_mock_client()
        executor = TradeExecutor(client, sleep_fn=lambda _: None)

        success = executor.modify_order(
            100001, sample_trading_account, {"stop_loss": 34440.0, "take_profit": 34610.0}
        )

        assert success is True
        client.modify_position.assert_called_once_with(
            order_id=100001, sl=34440.0, tp=34610.0
        )

    def test_modification_failure(self, sample_trading_account):
        client = _make_mock_client()
        client.modify_position.return_value = OrderResult(
            success=False, error_code=10016, error_message="Invalid stops"
        )
        executor = TradeExecutor(client, sleep_fn=lambda _: None)

        success = executor.modify_order(
            100001, sample_trading_account, {"stop_loss": 34440.0}
        )

        assert success is False

    def test_modification_connection_failure(self, sample_trading_account):
        client = _make_mock_client()
        client.connect.return_value = False
        executor = TradeExecutor(client, sleep_fn=lambda _: None)

        success = executor.modify_order(
            100001, sample_trading_account, {"stop_loss": 34440.0}
        )

        assert success is False

    def test_modification_exception(self, sample_trading_account):
        client = _make_mock_client()
        client.modify_position.side_effect = RuntimeError("unexpected")
        executor = TradeExecutor(client, sleep_fn=lambda _: None)

        success = executor.modify_order(
            100001, sample_trading_account, {"stop_loss": 34440.0}
        )

        assert success is False


class TestClosePosition:
    """Tests for TradeExecutor.close_position()."""

    def test_successful_close(self, sample_trading_account):
        client = _make_mock_client()
        executor = TradeExecutor(client, sleep_fn=lambda _: None)

        result = executor.close_position(100001, sample_trading_account)

        assert result.status == TradeExecutionStatus.FILLED
        assert result.fill_price == 34510.0
        assert result.account_id == sample_trading_account.id
        assert result.execution_latency_ms >= 0

    def test_close_rejection(self, sample_trading_account):
        client = _make_mock_client()
        client.close_position_by_id.return_value = OrderResult(
            success=False, error_code=10018, error_message="Position not found"
        )
        executor = TradeExecutor(client, sleep_fn=lambda _: None)

        result = executor.close_position(100001, sample_trading_account)

        assert result.status == TradeExecutionStatus.REJECTED
        assert "10018" in result.rejection_reason

    def test_close_connection_failure(self, sample_trading_account):
        client = _make_mock_client()
        client.connect.return_value = False
        executor = TradeExecutor(client, sleep_fn=lambda _: None)

        result = executor.close_position(100001, sample_trading_account)

        assert result.status == TradeExecutionStatus.ERROR
        assert "Connection failed" in result.rejection_reason

    def test_close_exception(self, sample_trading_account):
        client = _make_mock_client()
        client.close_position_by_id.side_effect = RuntimeError("broker down")
        executor = TradeExecutor(client, sleep_fn=lambda _: None)

        result = executor.close_position(100001, sample_trading_account)

        assert result.status == TradeExecutionStatus.ERROR
        assert "Close failed" in result.rejection_reason


class TestEventPublisherIntegration:
    """Tests for EventPublisher integration in TradeExecutor."""

    def test_successful_execution_publishes_requested_and_executed(
        self, sample_signal, sample_trading_account
    ):
        client = _make_mock_client(fill_price=34501.0)
        publisher = _make_mock_publisher()
        executor = TradeExecutor(client, sleep_fn=lambda _: None, event_publisher=publisher)

        result = executor.execute(sample_signal, sample_trading_account)

        assert result.status == TradeExecutionStatus.FILLED
        assert publisher.publish.call_count == 2

        # First call: TradeRequested
        requested_event = publisher.publish.call_args_list[0][0][0]
        assert requested_event.event_type == TradingEventType.TradeRequested
        assert requested_event.aggregate_id == sample_signal.id
        assert requested_event.payload["signal_id"] == sample_signal.id
        assert requested_event.payload["account_id"] == sample_trading_account.id
        assert requested_event.payload["instrument"] == sample_signal.instrument
        assert requested_event.payload["direction"] == sample_signal.direction.value
        assert requested_event.payload["requested_size"] == sample_signal.position_size

        # Second call: TradeExecuted
        executed_event = publisher.publish.call_args_list[1][0][0]
        assert executed_event.event_type == TradingEventType.TradeExecuted
        assert executed_event.aggregate_id == sample_signal.id
        assert executed_event.payload["fill_price"] == 34501.0
        assert executed_event.payload["signal_id"] == sample_signal.id
        assert executed_event.context_snapshot is not None
        assert executed_event.context_snapshot["broker_connected"] is True
        assert executed_event.context_snapshot["current_spread"] == 3.0
        assert executed_event.context_snapshot["bid_price"] == 34500.0
        assert executed_event.context_snapshot["ask_price"] == 34503.0

    def test_connection_failure_publishes_trade_failed(
        self, sample_signal, sample_trading_account
    ):
        client = _make_mock_client()
        client.connect.return_value = False
        publisher = _make_mock_publisher()
        executor = TradeExecutor(client, sleep_fn=lambda _: None, event_publisher=publisher)

        result = executor.execute(sample_signal, sample_trading_account)

        assert result.status == TradeExecutionStatus.ERROR
        assert publisher.publish.call_count == 1

        failed_event = publisher.publish.call_args_list[0][0][0]
        assert failed_event.event_type == TradingEventType.TradeFailed
        assert failed_event.aggregate_id == sample_signal.id
        assert failed_event.payload["signal_id"] == sample_signal.id
        assert failed_event.payload["error_code"] == "CONNECTION_FAILED"
        assert failed_event.context_snapshot["broker_connected"] is False

    def test_order_rejection_publishes_trade_failed(
        self, sample_signal, sample_trading_account
    ):
        client = _make_mock_client(
            order_success=False, error_code=10013, error_message="Invalid volume"
        )
        publisher = _make_mock_publisher()
        executor = TradeExecutor(client, sleep_fn=lambda _: None, event_publisher=publisher)

        result = executor.execute(sample_signal, sample_trading_account)

        assert result.status == TradeExecutionStatus.REJECTED
        # TradeRequested + TradeFailed
        assert publisher.publish.call_count == 2

        failed_event = publisher.publish.call_args_list[1][0][0]
        assert failed_event.event_type == TradingEventType.TradeFailed
        assert failed_event.payload["error_code"] == "10013"
        assert "Invalid volume" in failed_event.payload["failure_reason"]
        assert failed_event.context_snapshot["broker_connected"] is True
        assert failed_event.context_snapshot["current_spread"] == 3.0

    def test_retries_exhausted_publishes_trade_failed(
        self, sample_signal, sample_trading_account
    ):
        client = _make_mock_client()
        client.send_order.side_effect = ConnectionError("persistent failure")
        publisher = _make_mock_publisher()
        executor = TradeExecutor(client, sleep_fn=lambda _: None, event_publisher=publisher)

        result = executor.execute(sample_signal, sample_trading_account)

        assert result.status == TradeExecutionStatus.ERROR
        # TradeRequested + TradeFailed
        assert publisher.publish.call_count == 2

        failed_event = publisher.publish.call_args_list[1][0][0]
        assert failed_event.event_type == TradingEventType.TradeFailed
        assert failed_event.payload["error_code"] == "ORDER_SUBMISSION_FAILED"
        assert failed_event.payload["retry_count"] == MAX_RETRIES

    def test_publisher_failure_does_not_block_trading(
        self, sample_signal, sample_trading_account
    ):
        client = _make_mock_client(fill_price=34501.0)
        publisher = _make_mock_publisher()
        publisher.publish.side_effect = RuntimeError("Redis down")
        executor = TradeExecutor(client, sleep_fn=lambda _: None, event_publisher=publisher)

        # Trade should still succeed even if publisher fails
        result = executor.execute(sample_signal, sample_trading_account)

        assert result.status == TradeExecutionStatus.FILLED
        assert result.fill_price == 34501.0

    def test_no_publisher_does_not_raise(self, sample_signal, sample_trading_account):
        client = _make_mock_client(fill_price=34501.0)
        executor = TradeExecutor(client, sleep_fn=lambda _: None, event_publisher=None)

        result = executor.execute(sample_signal, sample_trading_account)

        assert result.status == TradeExecutionStatus.FILLED

    def test_signal_id_used_as_aggregate_id(self, sample_signal, sample_trading_account):
        client = _make_mock_client(fill_price=34501.0)
        publisher = _make_mock_publisher()
        executor = TradeExecutor(client, sleep_fn=lambda _: None, event_publisher=publisher)

        executor.execute(sample_signal, sample_trading_account)

        for call_args in publisher.publish.call_args_list:
            event = call_args[0][0]
            assert event.aggregate_id == sample_signal.id
