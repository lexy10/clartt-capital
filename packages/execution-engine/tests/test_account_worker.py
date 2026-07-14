"""Unit tests for AccountWorker and WorkerSupervisor."""

import json
import threading
import time
from unittest.mock import MagicMock, patch, call

import pytest

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
    RiskValidationResult,
    RiskRuleName,
)
from src.worker.account_worker import AccountWorker
from src.worker.supervisor import WorkerSupervisor


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_signal(signal_id: str = "sig-001") -> Signal:
    return Signal(
        id=signal_id,
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


def _make_account(account_id: str = "acct-001") -> TradingAccount:
    return TradingAccount(
        id=account_id,
        user_id="user-001",
        metaapi_account_id="test-account-id",
        label="Test Account",
        is_active=True,
        equity=10000.0,
        balance=10000.0,
        open_positions=2,
        daily_loss=50.0,
        total_lot_exposure=0.5,
    )


def _make_execution_result(
    signal_id: str = "sig-001",
    account_id: str = "acct-001",
    status: TradeExecutionStatus = TradeExecutionStatus.FILLED,
) -> TradeExecutionResult:
    return TradeExecutionResult(
        id="exec-001",
        signal_id=signal_id,
        account_id=account_id,
        order_id=100001,
        fill_price=34501.0,
        execution_latency_ms=45.0,
        status=status,
        slippage=1.0,
        spread_at_execution=2.8,
        created_at="2024-01-15T10:30:01Z",
    )


def _make_risk_result(
    approved: bool = True,
    signal_id: str = "sig-001",
    account_id: str = "acct-001",
) -> RiskValidationResult:
    return RiskValidationResult(
        approved=approved,
        signal_id=signal_id,
        account_id=account_id,
        rules_checked=[],
        rejected_by=None if approved else RiskRuleName.DAILY_LOSS_LIMIT,
        validated_at="2024-01-15T10:30:00Z",
    )


def _build_worker(
    account=None,
    kill_switch_active=False,
    consume_returns=None,
    risk_approved=True,
    execution_result=None,
    autopilot_monitor=None,
):
    """Build an AccountWorker with mocked dependencies."""
    account = account or _make_account()

    risk_manager = MagicMock()
    risk_manager.validate.return_value = _make_risk_result(
        approved=risk_approved, account_id=account.id
    )

    executor = MagicMock()
    executor.execute.return_value = execution_result or _make_execution_result(
        account_id=account.id
    )

    signal_consumer = MagicMock()
    signal_consumer.consume.return_value = consume_returns

    kill_switch = MagicMock()
    kill_switch.is_active.return_value = kill_switch_active

    redis_client = MagicMock()

    worker = AccountWorker(
        account=account,
        risk_manager=risk_manager,
        executor=executor,
        signal_consumer=signal_consumer,
        kill_switch=kill_switch,
        redis_client=redis_client,
        autopilot_monitor=autopilot_monitor,
        poll_timeout_ms=100,
    )

    return worker, {
        "risk_manager": risk_manager,
        "executor": executor,
        "signal_consumer": signal_consumer,
        "kill_switch": kill_switch,
        "redis_client": redis_client,
        "autopilot_monitor": autopilot_monitor,
    }


# ===========================================================================
# AccountWorker Tests
# ===========================================================================


class TestAccountWorkerInit:
    """Tests for AccountWorker initialization."""

    def test_consumer_group_includes_account_id(self):
        worker, _ = _build_worker()
        assert "acct-001" in worker._group_name

    def test_consumer_id_includes_account_id(self):
        worker, _ = _build_worker()
        assert "acct-001" in worker._consumer_id

    def test_account_property(self):
        account = _make_account("acct-xyz")
        worker, _ = _build_worker(account=account)
        assert worker.account_id == "acct-xyz"
        assert worker.account is account


class TestAccountWorkerProcessCycle:
    """Tests for the _process_one_cycle method (single iteration)."""

    def test_skips_when_kill_switch_active(self):
        worker, mocks = _build_worker(kill_switch_active=True)

        result = worker._process_one_cycle()

        assert result is None
        mocks["signal_consumer"].consume.assert_not_called()

    def test_returns_none_when_no_signal(self):
        worker, mocks = _build_worker(consume_returns=None)

        result = worker._process_one_cycle()

        assert result is None
        mocks["risk_manager"].validate.assert_not_called()

    def test_executes_trade_when_risk_approved(self):
        signal = _make_signal()
        worker, mocks = _build_worker(
            consume_returns=("msg-001", signal),
            risk_approved=True,
        )

        result = worker._process_one_cycle()

        assert result is not None
        assert result.status == TradeExecutionStatus.FILLED
        mocks["risk_manager"].validate.assert_called_once_with(signal, worker.account)
        mocks["executor"].execute.assert_called_once_with(signal, worker.account)
        mocks["signal_consumer"].acknowledge.assert_called_once()

    def test_rejects_trade_when_risk_fails(self):
        signal = _make_signal()
        worker, mocks = _build_worker(
            consume_returns=("msg-001", signal),
            risk_approved=False,
        )

        result = worker._process_one_cycle()

        assert result is None
        mocks["executor"].execute.assert_not_called()
        # Should still acknowledge the message
        mocks["signal_consumer"].acknowledge.assert_called_once()

    def test_publishes_execution_result_to_redis(self):
        signal = _make_signal()
        exec_result = _make_execution_result()
        worker, mocks = _build_worker(
            consume_returns=("msg-001", signal),
            execution_result=exec_result,
        )

        worker._process_one_cycle()

        mocks["redis_client"].publish.assert_called_once()
        call_args = mocks["redis_client"].publish.call_args
        assert call_args[0][0] == "trades:results"
        assert exec_result.id in call_args[0][1]

    def test_acknowledges_after_execution(self):
        signal = _make_signal()
        worker, mocks = _build_worker(
            consume_returns=("msg-001", signal),
        )

        worker._process_one_cycle()

        mocks["signal_consumer"].acknowledge.assert_called_once_with(
            worker._stream_key, worker._group_name, "msg-001"
        )


class TestAccountWorkerAutopilotGate:
    """Tests for autopilot gate check in _process_one_cycle."""

    def test_skips_when_autopilot_disabled_no_open_positions(self):
        """When autopilot is disabled and no open positions, skip signal processing."""
        autopilot = MagicMock()
        autopilot.is_enabled.return_value = False
        account = _make_account()
        account.open_positions = 0

        worker, mocks = _build_worker(account=account, autopilot_monitor=autopilot)

        result = worker._process_one_cycle()

        assert result is None
        autopilot.is_enabled.assert_called_once_with(account.id)
        mocks["signal_consumer"].consume.assert_not_called()
        assert not worker._deactivated_while_open

    def test_skips_when_autopilot_disabled_with_open_positions(self):
        """When autopilot is disabled mid-position, set flag and skip signal processing."""
        autopilot = MagicMock()
        autopilot.is_enabled.return_value = False
        account = _make_account()
        account.open_positions = 3

        worker, mocks = _build_worker(account=account, autopilot_monitor=autopilot)

        result = worker._process_one_cycle()

        assert result is None
        mocks["signal_consumer"].consume.assert_not_called()
        assert worker._deactivated_while_open is True

    def test_processes_signals_when_autopilot_enabled(self):
        """When autopilot is enabled, signals are processed normally."""
        autopilot = MagicMock()
        autopilot.is_enabled.return_value = True
        signal = _make_signal()

        worker, mocks = _build_worker(
            autopilot_monitor=autopilot,
            consume_returns=("msg-001", signal),
            risk_approved=True,
        )

        result = worker._process_one_cycle()

        assert result is not None
        mocks["signal_consumer"].consume.assert_called_once()
        mocks["executor"].execute.assert_called_once()

    def test_resets_deactivated_flag_when_autopilot_reenabled(self):
        """When autopilot is re-enabled, the _deactivated_while_open flag resets."""
        autopilot = MagicMock()
        autopilot.is_enabled.return_value = True

        worker, _ = _build_worker(autopilot_monitor=autopilot, consume_returns=None)
        worker._deactivated_while_open = True

        worker._process_one_cycle()

        assert worker._deactivated_while_open is False

    def test_deactivated_flag_stays_true_across_cycles(self):
        """The _deactivated_while_open flag persists across cycles while disabled."""
        autopilot = MagicMock()
        autopilot.is_enabled.return_value = False
        account = _make_account()
        account.open_positions = 2

        worker, _ = _build_worker(account=account, autopilot_monitor=autopilot)

        worker._process_one_cycle()
        assert worker._deactivated_while_open is True

        # Second cycle — flag should remain True
        worker._process_one_cycle()
        assert worker._deactivated_while_open is True

    def test_no_autopilot_monitor_processes_normally(self):
        """When no autopilot monitor is provided, signals are processed normally."""
        signal = _make_signal()
        worker, mocks = _build_worker(
            autopilot_monitor=None,
            consume_returns=("msg-001", signal),
            risk_approved=True,
        )

        result = worker._process_one_cycle()

        assert result is not None
        mocks["executor"].execute.assert_called_once()

    def test_kill_switch_takes_precedence_over_autopilot(self):
        """Kill switch check happens before autopilot check."""
        autopilot = MagicMock()
        autopilot.is_enabled.return_value = True

        worker, mocks = _build_worker(
            kill_switch_active=True,
            autopilot_monitor=autopilot,
        )

        result = worker._process_one_cycle()

        assert result is None
        # Autopilot should not even be checked when kill switch is active
        autopilot.is_enabled.assert_not_called()
        mocks["signal_consumer"].consume.assert_not_called()

    def test_deactivated_flag_resets_when_no_open_positions(self):
        """When autopilot is disabled and positions close, flag resets to False."""
        autopilot = MagicMock()
        autopilot.is_enabled.return_value = False
        account = _make_account()

        worker, _ = _build_worker(account=account, autopilot_monitor=autopilot)

        # First: disabled with open positions
        account.open_positions = 2
        worker._process_one_cycle()
        assert worker._deactivated_while_open is True

        # Positions close while still disabled
        account.open_positions = 0
        worker._process_one_cycle()
        assert worker._deactivated_while_open is False


class TestAccountWorkerRunStop:
    """Tests for run() and stop() lifecycle."""

    def test_stop_breaks_run_loop(self):
        worker, _ = _build_worker(consume_returns=None)

        def stop_after_delay():
            time.sleep(0.1)
            worker.stop()

        stopper = threading.Thread(target=stop_after_delay)
        stopper.start()

        worker.run()  # Should exit once stop is called
        stopper.join()

        assert not worker.is_running

    def test_is_running_flag(self):
        worker, _ = _build_worker(consume_returns=None)
        assert not worker.is_running

        started = threading.Event()
        original_process = worker._process_one_cycle

        def patched_cycle():
            started.set()
            return original_process()

        worker._process_one_cycle = patched_cycle

        thread = threading.Thread(target=worker.run, daemon=True)
        thread.start()
        started.wait(timeout=2.0)
        assert worker.is_running

        worker.stop()
        thread.join(timeout=2.0)
        assert not worker.is_running


class TestAccountWorkerIsolation:
    """Tests verifying per-account state isolation."""

    def test_separate_accounts_have_separate_groups(self):
        worker_a, _ = _build_worker(account=_make_account("acct-A"))
        worker_b, _ = _build_worker(account=_make_account("acct-B"))

        assert worker_a._group_name != worker_b._group_name
        assert worker_a._consumer_id != worker_b._consumer_id

    def test_worker_uses_own_account_for_risk(self):
        account = _make_account("acct-isolated")
        signal = _make_signal()
        worker, mocks = _build_worker(
            account=account,
            consume_returns=("msg-001", signal),
        )

        worker._process_one_cycle()

        mocks["risk_manager"].validate.assert_called_once_with(signal, account)

    def test_worker_uses_own_account_for_execution(self):
        account = _make_account("acct-isolated")
        signal = _make_signal()
        worker, mocks = _build_worker(
            account=account,
            consume_returns=("msg-001", signal),
        )

        worker._process_one_cycle()

        mocks["executor"].execute.assert_called_once_with(signal, account)


class TestAccountWorkerPublishFailure:
    """Tests for resilience when Redis publish fails."""

    def test_publish_failure_does_not_crash_worker(self):
        signal = _make_signal()
        worker, mocks = _build_worker(
            consume_returns=("msg-001", signal),
        )
        mocks["redis_client"].publish.side_effect = ConnectionError("Redis down")

        # Should not raise
        result = worker._process_one_cycle()
        assert result is not None


class TestAccountWorkerStrategyFilter:
    """Tests for strategy-based signal filtering."""

    def test_skips_signal_from_unassigned_strategy(self):
        """Signals from strategies not assigned to the account are skipped."""
        signal = _make_signal()
        signal.strategy_id = "strat-unassigned"

        account = _make_account()
        redis_client = MagicMock()
        # Return assigned strategies that do NOT include the signal's strategy
        redis_client.get.return_value = json.dumps(["strat-A", "strat-B"])

        worker = AccountWorker(
            account=account,
            risk_manager=MagicMock(),
            executor=MagicMock(),
            signal_consumer=MagicMock(),
            kill_switch=MagicMock(),
            redis_client=redis_client,
            poll_timeout_ms=100,
        )
        worker._kill_switch.is_active.return_value = False
        worker._consumer.consume.return_value = ("msg-001", signal)

        result = worker._process_one_cycle()

        assert result is None
        # Signal should be acknowledged (not re-delivered)
        worker._consumer.acknowledge.assert_called_once()
        # Risk manager and executor should NOT be called
        worker._risk_manager.validate.assert_not_called()
        worker._executor.execute.assert_not_called()

    def test_processes_signal_from_assigned_strategy(self):
        """Signals from assigned strategies are processed normally."""
        signal = _make_signal()
        signal.strategy_id = "strat-A"

        account = _make_account()
        redis_client = MagicMock()
        redis_client.get.return_value = json.dumps(["strat-A", "strat-B"])

        risk_result = _make_risk_result(approved=True, account_id=account.id)
        exec_result = _make_execution_result(account_id=account.id)

        worker = AccountWorker(
            account=account,
            risk_manager=MagicMock(),
            executor=MagicMock(),
            signal_consumer=MagicMock(),
            kill_switch=MagicMock(),
            redis_client=redis_client,
            poll_timeout_ms=100,
        )
        worker._kill_switch.is_active.return_value = False
        worker._consumer.consume.return_value = ("msg-001", signal)
        worker._risk_manager.validate.return_value = risk_result
        worker._executor.execute.return_value = exec_result

        result = worker._process_one_cycle()

        assert result is not None
        worker._risk_manager.validate.assert_called_once()
        worker._executor.execute.assert_called_once()

    def test_accepts_all_signals_when_no_strategies_assigned(self):
        """When no strategies are assigned (empty set), all signals pass through."""
        signal = _make_signal()
        signal.strategy_id = "any-strategy"

        account = _make_account()
        redis_client = MagicMock()
        # No key in Redis → returns None → empty set → accept all
        redis_client.get.return_value = None

        risk_result = _make_risk_result(approved=True, account_id=account.id)
        exec_result = _make_execution_result(account_id=account.id)

        worker = AccountWorker(
            account=account,
            risk_manager=MagicMock(),
            executor=MagicMock(),
            signal_consumer=MagicMock(),
            kill_switch=MagicMock(),
            redis_client=redis_client,
            poll_timeout_ms=100,
        )
        worker._kill_switch.is_active.return_value = False
        worker._consumer.consume.return_value = ("msg-001", signal)
        worker._risk_manager.validate.return_value = risk_result
        worker._executor.execute.return_value = exec_result

        result = worker._process_one_cycle()

        assert result is not None
        worker._executor.execute.assert_called_once()

    def test_loads_strategy_ids_from_redis_on_init(self):
        """Strategy IDs are loaded from Redis during __init__."""
        account = _make_account()
        redis_client = MagicMock()
        redis_client.get.return_value = json.dumps(["strat-X", "strat-Y"])

        worker = AccountWorker(
            account=account,
            risk_manager=MagicMock(),
            executor=MagicMock(),
            signal_consumer=MagicMock(),
            kill_switch=MagicMock(),
            redis_client=redis_client,
            poll_timeout_ms=100,
        )

        assert worker.assigned_strategy_ids == {"strat-X", "strat-Y"}
        # assert_any_call, not assert_called_with: init also loads
        # account:symbols:{id} after the strategy IDs, so the strategies
        # fetch is no longer the LAST redis.get call.
        redis_client.get.assert_any_call(f"account:strategies:{account.id}")

    def test_handles_redis_failure_on_strategy_load(self):
        """If Redis fails during strategy load, worker defaults to empty set (accept all)."""
        account = _make_account()
        redis_client = MagicMock()
        redis_client.get.side_effect = ConnectionError("Redis down")

        worker = AccountWorker(
            account=account,
            risk_manager=MagicMock(),
            executor=MagicMock(),
            signal_consumer=MagicMock(),
            kill_switch=MagicMock(),
            redis_client=redis_client,
            poll_timeout_ms=100,
        )

        assert worker.assigned_strategy_ids == set()

    def test_pubsub_updates_assigned_strategies(self):
        """Strategy listener updates assigned IDs when pub/sub message arrives."""
        account = _make_account("acct-pubsub")
        redis_client = MagicMock()
        redis_client.get.return_value = json.dumps(["strat-old"])

        worker = AccountWorker(
            account=account,
            risk_manager=MagicMock(),
            executor=MagicMock(),
            signal_consumer=MagicMock(),
            kill_switch=MagicMock(),
            redis_client=redis_client,
            poll_timeout_ms=100,
        )

        assert worker.assigned_strategy_ids == {"strat-old"}

        # Simulate a pub/sub update by directly setting (the listener would do this)
        worker._assigned_strategy_ids = {"strat-new-A", "strat-new-B"}

        assert worker.assigned_strategy_ids == {"strat-new-A", "strat-new-B"}


# ===========================================================================
# WorkerSupervisor Tests
# ===========================================================================


class TestWorkerSupervisor:
    """Tests for WorkerSupervisor."""

    def _make_supervisor(self):
        """Create a supervisor with a factory that produces mock-backed workers."""
        workers_created = []

        def factory(account):
            worker, _ = _build_worker(account=account, consume_returns=None)
            workers_created.append(worker)
            return worker

        supervisor = WorkerSupervisor(worker_factory=factory)
        return supervisor, workers_created

    def test_start_worker(self):
        supervisor, created = self._make_supervisor()
        account = _make_account("acct-1")

        supervisor.start_worker(account)
        time.sleep(0.1)

        assert "acct-1" in supervisor.all_workers
        assert len(created) == 1

    def test_stop_worker(self):
        supervisor, _ = self._make_supervisor()
        account = _make_account("acct-1")

        supervisor.start_worker(account)
        time.sleep(0.1)
        supervisor.stop_worker("acct-1")

        assert "acct-1" not in supervisor.all_workers

    def test_stop_all(self):
        supervisor, _ = self._make_supervisor()

        supervisor.start_worker(_make_account("acct-1"))
        supervisor.start_worker(_make_account("acct-2"))
        time.sleep(0.1)

        assert len(supervisor.all_workers) == 2

        supervisor.stop_all()

        assert len(supervisor.all_workers) == 0

    def test_restart_worker(self):
        supervisor, created = self._make_supervisor()
        account = _make_account("acct-1")

        supervisor.start_worker(account)
        time.sleep(0.1)
        supervisor.restart_worker("acct-1")
        time.sleep(0.1)

        assert "acct-1" in supervisor.all_workers
        # Factory called twice: initial + restart
        assert len(created) == 2

    def test_restart_unknown_account_is_safe(self):
        supervisor, _ = self._make_supervisor()
        # Should not raise
        supervisor.restart_worker("nonexistent")

    def test_stop_unknown_worker_is_safe(self):
        supervisor, _ = self._make_supervisor()
        # Should not raise
        supervisor.stop_worker("nonexistent")

    def test_multiple_accounts_isolated(self):
        supervisor, created = self._make_supervisor()

        supervisor.start_worker(_make_account("acct-A"))
        supervisor.start_worker(_make_account("acct-B"))
        time.sleep(0.1)

        assert "acct-A" in supervisor.all_workers
        assert "acct-B" in supervisor.all_workers

        # Stop one, other should remain
        supervisor.stop_worker("acct-A")

        assert "acct-A" not in supervisor.all_workers
        assert "acct-B" in supervisor.all_workers

        supervisor.stop_all()

    def test_is_worker_alive(self):
        supervisor, _ = self._make_supervisor()
        account = _make_account("acct-1")

        supervisor.start_worker(account)
        time.sleep(0.1)

        assert supervisor.is_worker_alive("acct-1")
        assert not supervisor.is_worker_alive("nonexistent")

        supervisor.stop_all()
