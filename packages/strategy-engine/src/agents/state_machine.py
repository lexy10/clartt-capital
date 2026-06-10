"""Agent state machine with validated transitions, history, and persistence."""

from datetime import datetime, timezone
from typing import Optional

import requests

from src.agents.models import AgentState, AgentStateRecord, StateTransition


# Valid transitions map
VALID_TRANSITIONS: dict[AgentState, set[AgentState]] = {
    AgentState.IDLE: {AgentState.PLANNING},
    AgentState.PLANNING: {
        AgentState.EXECUTING,
        AgentState.WAITING_FOR_INPUT,
        AgentState.PAUSED,
        AgentState.IDLE,
    },
    AgentState.EXECUTING: {
        AgentState.REVIEWING,
        AgentState.FAILED,
        AgentState.WAITING_FOR_INPUT,
        AgentState.PAUSED,
        AgentState.IDLE,
    },
    AgentState.WAITING_FOR_INPUT: {
        AgentState.EXECUTING,
        AgentState.REVIEWING,
        AgentState.PAUSED,
        AgentState.IDLE,
    },
    AgentState.REVIEWING: {
        AgentState.COMPLETED,
        AgentState.PLANNING,
        AgentState.FAILED,
        AgentState.PAUSED,
        AgentState.IDLE,
    },
    AgentState.COMPLETED: {AgentState.IDLE},
    AgentState.FAILED: {AgentState.IDLE},
    AgentState.PAUSED: set(),  # Restored to previous state via resume()
}


class InvalidTransitionError(Exception):
    """Raised when an invalid state transition is attempted."""

    def __init__(self, agent_name: str, current: AgentState, target: AgentState):
        self.agent_name = agent_name
        self.current = current
        self.target = target
        super().__init__(
            f"Agent '{agent_name}': invalid transition {current.value} → {target.value}"
        )


class AgentStateMachine:
    """Manages agent state transitions with validation, history, and persistence."""

    MAX_HISTORY = 100

    # States that can be paused (active states)
    _PAUSABLE_STATES = {
        AgentState.PLANNING,
        AgentState.EXECUTING,
        AgentState.WAITING_FOR_INPUT,
        AgentState.REVIEWING,
    }

    def __init__(self, agent_name: str, backend_url: str) -> None:
        self._agent_name = agent_name
        self._backend_url = backend_url.rstrip("/")
        self._state = AgentState.IDLE
        self._state_entered_at = datetime.now(timezone.utc)
        self._history: list[StateTransition] = []
        self._pre_pause_state: Optional[AgentState] = None
        self._failure_reason: Optional[str] = None
        self._failure_stack_trace: Optional[str] = None
        self._failure_task_id: Optional[str] = None
        self._error_count: int = 0
        self._current_task_id: Optional[str] = None

    @property
    def current_state(self) -> AgentState:
        """Return the current agent state."""
        return self._state

    def transition(self, target: AgentState, reason: Optional[str] = None) -> None:
        """Validate and execute state transition.

        Raises InvalidTransitionError if the transition is not allowed.
        Records transition in history (max 100 entries).
        """
        if target not in VALID_TRANSITIONS.get(self._state, set()):
            raise InvalidTransitionError(self._agent_name, self._state, target)

        from_state = self._state
        self._state = target
        self._state_entered_at = datetime.now(timezone.utc)

        transition = StateTransition(
            from_state=from_state,
            to_state=target,
            timestamp=datetime.now(timezone.utc).isoformat(),
            reason=reason,
        )
        self._history.append(transition)

        # Cap history at MAX_HISTORY entries
        if len(self._history) > self.MAX_HISTORY:
            self._history = self._history[-self.MAX_HISTORY :]

    def pause(self) -> None:
        """Transition to PAUSED from any active state. Stores pre-pause state for resume."""
        if self._state not in self._PAUSABLE_STATES:
            raise InvalidTransitionError(self._agent_name, self._state, AgentState.PAUSED)

        self._pre_pause_state = self._state
        from_state = self._state
        self._state = AgentState.PAUSED
        self._state_entered_at = datetime.now(timezone.utc)

        transition = StateTransition(
            from_state=from_state,
            to_state=AgentState.PAUSED,
            timestamp=datetime.now(timezone.utc).isoformat(),
            reason="paused",
        )
        self._history.append(transition)
        if len(self._history) > self.MAX_HISTORY:
            self._history = self._history[-self.MAX_HISTORY :]

    def resume(self) -> None:
        """Restore to pre-pause state."""
        if self._state != AgentState.PAUSED or self._pre_pause_state is None:
            raise InvalidTransitionError(
                self._agent_name,
                self._state,
                self._pre_pause_state or AgentState.IDLE,
            )

        target = self._pre_pause_state
        self._state = target
        self._state_entered_at = datetime.now(timezone.utc)

        transition = StateTransition(
            from_state=AgentState.PAUSED,
            to_state=target,
            timestamp=datetime.now(timezone.utc).isoformat(),
            reason="resumed",
        )
        self._history.append(transition)
        if len(self._history) > self.MAX_HISTORY:
            self._history = self._history[-self.MAX_HISTORY :]

        self._pre_pause_state = None

    def reset(self) -> None:
        """Transition from FAILED → IDLE."""
        if self._state != AgentState.FAILED:
            raise InvalidTransitionError(self._agent_name, self._state, AgentState.IDLE)

        self.transition(AgentState.IDLE, reason="reset")
        self._failure_reason = None
        self._failure_stack_trace = None
        self._failure_task_id = None

    def get_state_duration(self) -> float:
        """Return seconds the agent has been in the current state."""
        now = datetime.now(timezone.utc)
        return (now - self._state_entered_at).total_seconds()

    def record_failure(self, reason: str, stack_trace: str, task_id: str) -> None:
        """Record failure details when entering FAILED state."""
        self._failure_reason = reason
        self._failure_stack_trace = stack_trace
        self._failure_task_id = task_id
        self._error_count += 1

    async def persist(self) -> None:
        """Persist current state and history to PostgreSQL via backend REST API."""
        record = AgentStateRecord(
            agent_name=self._agent_name,
            current_state=self._state,
            state_history=self._history,
            current_task_id=self._current_task_id,
            error_count=self._error_count,
            failure_reason=self._failure_reason,
            failure_stack_trace=self._failure_stack_trace,
        )
        url = f"{self._backend_url}/api/agents/states/{self._agent_name}"
        payload = record.model_dump(mode="json")

        try:
            response = requests.get(url, timeout=10)
            if response.status_code == 200:
                requests.put(url, json=payload, timeout=10)
            else:
                requests.post(
                    f"{self._backend_url}/api/agents/states",
                    json=payload,
                    timeout=10,
                )
        except requests.RequestException:
            # POST as fallback if GET fails (first persist)
            try:
                requests.post(
                    f"{self._backend_url}/api/agents/states",
                    json=payload,
                    timeout=10,
                )
            except requests.RequestException:
                pass

    async def restore(self) -> None:
        """Restore state from PostgreSQL via backend REST API."""
        url = f"{self._backend_url}/api/agents/states/{self._agent_name}"

        try:
            response = requests.get(url, timeout=10)
            if response.status_code == 200:
                data = response.json()
                record = AgentStateRecord.model_validate(data)
                self._state = record.current_state
                self._history = record.state_history
                self._error_count = record.error_count
                self._current_task_id = record.current_task_id
                self._failure_reason = record.failure_reason
                self._failure_stack_trace = record.failure_stack_trace
                self._state_entered_at = datetime.now(timezone.utc)

                # Resume from PAUSED if previous state was an active state
                if self._state in (AgentState.PLANNING, AgentState.EXECUTING):
                    self._pre_pause_state = self._state
                    self._state = AgentState.PAUSED
                    self._state_entered_at = datetime.now(timezone.utc)
        except requests.RequestException:
            pass

    @property
    def state_history(self) -> list[dict]:
        """Return state transition history as list of dicts."""
        return [t.model_dump(mode="json") for t in self._history]
