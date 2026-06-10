"""Unit tests for AgentStateMachine."""

import time

import pytest

from src.agents.models import AgentState
from src.agents.state_machine import (
    VALID_TRANSITIONS,
    AgentStateMachine,
    InvalidTransitionError,
)


@pytest.fixture
def sm() -> AgentStateMachine:
    return AgentStateMachine(agent_name="test-agent", backend_url="http://localhost:3000")


class TestInitialization:
    def test_starts_in_idle(self, sm: AgentStateMachine):
        assert sm.current_state == AgentState.IDLE

    def test_empty_history(self, sm: AgentStateMachine):
        assert sm.state_history == []


class TestTransition:
    def test_valid_idle_to_planning(self, sm: AgentStateMachine):
        sm.transition(AgentState.PLANNING, reason="starting task")
        assert sm.current_state == AgentState.PLANNING

    def test_records_history(self, sm: AgentStateMachine):
        sm.transition(AgentState.PLANNING, reason="test")
        history = sm.state_history
        assert len(history) == 1
        assert history[0]["from_state"] == AgentState.IDLE.value
        assert history[0]["to_state"] == AgentState.PLANNING.value
        assert history[0]["reason"] == "test"
        assert "timestamp" in history[0]

    def test_invalid_transition_raises(self, sm: AgentStateMachine):
        with pytest.raises(InvalidTransitionError) as exc_info:
            sm.transition(AgentState.COMPLETED)
        assert exc_info.value.agent_name == "test-agent"
        assert exc_info.value.current == AgentState.IDLE
        assert exc_info.value.target == AgentState.COMPLETED

    def test_full_happy_path(self, sm: AgentStateMachine):
        sm.transition(AgentState.PLANNING)
        sm.transition(AgentState.EXECUTING)
        sm.transition(AgentState.REVIEWING)
        sm.transition(AgentState.COMPLETED)
        sm.transition(AgentState.IDLE)
        assert sm.current_state == AgentState.IDLE
        assert len(sm.state_history) == 5

    def test_history_capped_at_100(self, sm: AgentStateMachine):
        for _ in range(60):
            sm.transition(AgentState.PLANNING)
            sm.transition(AgentState.IDLE)
        # 120 transitions total, should be capped at 100
        assert len(sm.state_history) <= AgentStateMachine.MAX_HISTORY


class TestPauseResume:
    def test_pause_from_planning(self, sm: AgentStateMachine):
        sm.transition(AgentState.PLANNING)
        sm.pause()
        assert sm.current_state == AgentState.PAUSED

    def test_resume_restores_previous_state(self, sm: AgentStateMachine):
        sm.transition(AgentState.PLANNING)
        sm.pause()
        sm.resume()
        assert sm.current_state == AgentState.PLANNING

    def test_pause_from_executing(self, sm: AgentStateMachine):
        sm.transition(AgentState.PLANNING)
        sm.transition(AgentState.EXECUTING)
        sm.pause()
        assert sm.current_state == AgentState.PAUSED
        sm.resume()
        assert sm.current_state == AgentState.EXECUTING

    def test_pause_from_idle_raises(self, sm: AgentStateMachine):
        with pytest.raises(InvalidTransitionError):
            sm.pause()

    def test_pause_from_completed_raises(self, sm: AgentStateMachine):
        sm.transition(AgentState.PLANNING)
        sm.transition(AgentState.EXECUTING)
        sm.transition(AgentState.REVIEWING)
        sm.transition(AgentState.COMPLETED)
        with pytest.raises(InvalidTransitionError):
            sm.pause()

    def test_resume_when_not_paused_raises(self, sm: AgentStateMachine):
        sm.transition(AgentState.PLANNING)
        with pytest.raises(InvalidTransitionError):
            sm.resume()

    def test_pause_resume_records_history(self, sm: AgentStateMachine):
        sm.transition(AgentState.PLANNING)
        sm.pause()
        sm.resume()
        history = sm.state_history
        assert len(history) == 3
        assert history[1]["to_state"] == AgentState.PAUSED.value
        assert history[2]["from_state"] == AgentState.PAUSED.value
        assert history[2]["to_state"] == AgentState.PLANNING.value


class TestReset:
    def test_reset_from_failed(self, sm: AgentStateMachine):
        sm.transition(AgentState.PLANNING)
        sm.transition(AgentState.EXECUTING)
        sm.transition(AgentState.FAILED)
        sm.reset()
        assert sm.current_state == AgentState.IDLE

    def test_reset_from_non_failed_raises(self, sm: AgentStateMachine):
        with pytest.raises(InvalidTransitionError):
            sm.reset()

    def test_reset_clears_failure_info(self, sm: AgentStateMachine):
        sm.transition(AgentState.PLANNING)
        sm.transition(AgentState.EXECUTING)
        sm.transition(AgentState.FAILED)
        sm.record_failure("boom", "traceback...", "task-123")
        sm.reset()
        assert sm.current_state == AgentState.IDLE


class TestStateDuration:
    def test_duration_is_positive(self, sm: AgentStateMachine):
        duration = sm.get_state_duration()
        assert duration >= 0.0

    def test_duration_increases(self, sm: AgentStateMachine):
        time.sleep(0.05)
        assert sm.get_state_duration() >= 0.04


class TestRecordFailure:
    def test_records_failure_details(self, sm: AgentStateMachine):
        sm.transition(AgentState.PLANNING)
        sm.transition(AgentState.EXECUTING)
        sm.transition(AgentState.FAILED)
        sm.record_failure("timeout", "stack...", "task-1")
        assert sm._failure_reason == "timeout"
        assert sm._failure_stack_trace == "stack..."
        assert sm._failure_task_id == "task-1"
        assert sm._error_count == 1

    def test_error_count_increments(self, sm: AgentStateMachine):
        sm.transition(AgentState.PLANNING)
        sm.transition(AgentState.EXECUTING)
        sm.transition(AgentState.FAILED, reason="err1")
        sm.record_failure("err1", "s1", "t1")
        sm.reset()
        sm.transition(AgentState.PLANNING)
        sm.transition(AgentState.EXECUTING)
        sm.transition(AgentState.FAILED, reason="err2")
        sm.record_failure("err2", "s2", "t2")
        assert sm._error_count == 2


class TestInvalidTransitionError:
    def test_error_message(self):
        err = InvalidTransitionError("my-agent", AgentState.IDLE, AgentState.COMPLETED)
        assert "my-agent" in str(err)
        assert "IDLE" in str(err)
        assert "COMPLETED" in str(err)

    def test_attributes(self):
        err = InvalidTransitionError("a", AgentState.IDLE, AgentState.FAILED)
        assert err.agent_name == "a"
        assert err.current == AgentState.IDLE
        assert err.target == AgentState.FAILED


class TestValidTransitionsMap:
    def test_all_states_have_entries(self):
        for state in AgentState:
            assert state in VALID_TRANSITIONS

    def test_paused_has_no_direct_transitions(self):
        assert VALID_TRANSITIONS[AgentState.PAUSED] == set()

    def test_failed_only_to_idle(self):
        assert VALID_TRANSITIONS[AgentState.FAILED] == {AgentState.IDLE}

    def test_completed_only_to_idle(self):
        assert VALID_TRANSITIONS[AgentState.COMPLETED] == {AgentState.IDLE}
