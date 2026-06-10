"""Agent Registry — central registry managing agent instances, lifecycle, and heartbeats.

Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 1.8, 1.9, 1.10
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Optional, Protocol

from redis import Redis

from src.agents.base import Agent
from src.agents.models import AgentState, AgentTask
from src.agents.state_machine import AgentStateMachine

logger = logging.getLogger(__name__)


class EventPublisherProtocol(Protocol):
    """Minimal protocol for event publishing — accepts any object with a publish() method."""

    def publish(self, event: Any) -> None: ...


class AgentRegistry:
    """Manages agent instances, lifecycle operations, and heartbeats.

    Constructor takes:
      - redis_client: Redis connection for heartbeat keys and stream publishing
      - event_publisher: EventPublisher (or compatible) for lifecycle events
      - memory: AgentMemory placeholder (typed as Any)
      - task_queue: TaskQueue placeholder (typed as Any)
      - heartbeat_interval: seconds between heartbeats (default 30)
      - backend_url: backend REST API base URL for state machine persistence
    """

    HEARTBEAT_KEY_PREFIX = "agents:heartbeat"
    EVENTS_STREAM = "agents:events"

    def __init__(
        self,
        redis_client: Redis,
        event_publisher: EventPublisherProtocol,
        memory: Any = None,
        task_queue: Any = None,
        *,
        heartbeat_interval: int = 30,
        backend_url: str = "http://localhost:3000",
    ) -> None:
        self._redis = redis_client
        self._event_publisher = event_publisher
        self._memory = memory
        self._task_queue = task_queue
        self._heartbeat_interval = heartbeat_interval
        self._backend_url = backend_url

        # agent name → Agent instance
        self._agents: dict[str, Agent] = {}
        # agent name → AgentStateMachine
        self._state_machines: dict[str, AgentStateMachine] = {}
        # agent name → registration timestamp (for uptime)
        self._registered_at: dict[str, datetime] = {}
        # agent name → current task id
        self._current_tasks: dict[str, Optional[str]] = {}
        # agent name → asyncio.Task for heartbeat loop
        self._heartbeat_tasks: dict[str, asyncio.Task] = {}
        # agent name → last heartbeat datetime
        self._last_heartbeats: dict[str, datetime] = {}
        # agent name → healthy flag
        self._healthy: dict[str, bool] = {}

    # ── Registration ──────────────────────────────────────────────────

    def register(self, agent: Agent) -> None:
        """Register an agent. Raises ValueError if name already registered
        or agent doesn't implement the required interface.

        Requirement 1.1, 1.2
        """
        # Validate Agent interface
        for attr in ("name", "description", "supported_task_types", "supported_tools", "run"):
            if not callable(getattr(agent, attr, None)):
                raise TypeError(
                    f"Agent must implement callable '{attr}' — got {type(agent).__name__}"
                )

        name = agent.name()
        if name in self._agents:
            raise ValueError(f"Agent '{name}' is already registered")

        self._agents[name] = agent
        self._state_machines[name] = AgentStateMachine(
            agent_name=name, backend_url=self._backend_url
        )
        self._registered_at[name] = datetime.now(timezone.utc)
        self._current_tasks[name] = None
        self._last_heartbeats[name] = datetime.now(timezone.utc)
        self._healthy[name] = True

        logger.info("Registered agent '%s'", name)

    def deregister(self, name: str) -> None:
        """Remove an agent from the registry.

        Requirement 1.1
        """
        if name not in self._agents:
            raise KeyError(f"Agent '{name}' is not registered")

        # Cancel heartbeat if running
        self._cancel_heartbeat(name)

        del self._agents[name]
        del self._state_machines[name]
        del self._registered_at[name]
        del self._current_tasks[name]
        del self._last_heartbeats[name]
        del self._healthy[name]

        logger.info("Deregistered agent '%s'", name)

    def get(self, name: str) -> Agent:
        """Look up agent by name. Raises KeyError if not found.

        Requirement 1.1
        """
        if name not in self._agents:
            raise KeyError(f"Agent '{name}' is not registered")
        return self._agents[name]

    def list_agents(self) -> list[dict]:
        """Return metadata for all registered agents.

        Requirement 1.1
        """
        result = []
        for name, agent in self._agents.items():
            sm = self._state_machines[name]
            result.append(
                {
                    "name": name,
                    "description": agent.description(),
                    "state": sm.current_state.value,
                    "healthy": self._healthy.get(name, False),
                    "supported_task_types": agent.supported_task_types(),
                    "supported_tools": agent.supported_tools(),
                    "current_task_id": self._current_tasks.get(name),
                    "uptime_seconds": (
                        datetime.now(timezone.utc) - self._registered_at[name]
                    ).total_seconds(),
                }
            )
        return result

    # ── Lifecycle Operations ──────────────────────────────────────────

    async def start(self, name: str, task: Optional[AgentTask] = None) -> None:
        """Start an agent — transition IDLE → PLANNING, begin task processing.

        Requirement 1.3, 1.4
        """
        if name not in self._agents:
            raise KeyError(f"Agent '{name}' is not registered")

        sm = self._state_machines[name]
        prev_state = sm.current_state

        sm.transition(AgentState.PLANNING, reason="start")

        if task is not None:
            self._current_tasks[name] = task.id
            sm._current_task_id = task.id

        self._start_heartbeat(name)
        self._publish_lifecycle_event(name, prev_state, sm.current_state)

        logger.info("Started agent '%s' (task=%s)", name, task.id if task else None)

    async def stop(self, name: str) -> None:
        """Stop an agent — cancel in-progress work, transition to IDLE.

        Requirement 1.3, 1.5
        """
        if name not in self._agents:
            raise KeyError(f"Agent '{name}' is not registered")

        sm = self._state_machines[name]
        prev_state = sm.current_state

        # Transition to IDLE from any active state
        if prev_state == AgentState.PAUSED:
            # Resume first so we can transition to IDLE
            sm.resume()
            prev_state_for_event = AgentState.PAUSED
        else:
            prev_state_for_event = prev_state

        if sm.current_state != AgentState.IDLE:
            sm.transition(AgentState.IDLE, reason="stop")

        self._current_tasks[name] = None
        sm._current_task_id = None
        self._cancel_heartbeat(name)
        self._publish_lifecycle_event(name, prev_state_for_event, AgentState.IDLE)

        logger.info("Stopped agent '%s'", name)

    async def pause(self, name: str) -> None:
        """Pause an agent — suspend processing without losing progress.

        Requirement 1.3, 1.6
        """
        if name not in self._agents:
            raise KeyError(f"Agent '{name}' is not registered")

        sm = self._state_machines[name]
        prev_state = sm.current_state
        sm.pause()
        self._publish_lifecycle_event(name, prev_state, AgentState.PAUSED)

        logger.info("Paused agent '%s'", name)

    async def resume(self, name: str) -> None:
        """Resume a paused agent — restore previous state and continue.

        Requirement 1.3, 1.7
        """
        if name not in self._agents:
            raise KeyError(f"Agent '{name}' is not registered")

        sm = self._state_machines[name]
        sm.resume()
        restored_state = sm.current_state
        self._publish_lifecycle_event(name, AgentState.PAUSED, restored_state)

        logger.info("Resumed agent '%s' → %s", name, restored_state.value)

    # ── Health Check ──────────────────────────────────────────────────

    def health_check(self, name: str) -> dict:
        """Return agent health: state, last_heartbeat, current_task_id, uptime, error_count.

        Requirement 1.8
        """
        if name not in self._agents:
            raise KeyError(f"Agent '{name}' is not registered")

        sm = self._state_machines[name]
        return {
            "state": sm.current_state.value,
            "last_heartbeat": self._last_heartbeats[name].isoformat(),
            "current_task_id": self._current_tasks.get(name),
            "uptime": (
                datetime.now(timezone.utc) - self._registered_at[name]
            ).total_seconds(),
            "error_count": sm._error_count,
            "healthy": self._healthy.get(name, False),
        }

    # ── Heartbeat Loop ─────────────────────────────────────────────

    def _start_heartbeat(self, name: str) -> None:
        """Start the heartbeat loop for an agent.

        Requirement 1.9
        """
        self._cancel_heartbeat(name)
        self._last_heartbeats[name] = datetime.now(timezone.utc)
        self._healthy[name] = True

        task = asyncio.ensure_future(self._heartbeat_loop(name))
        self._heartbeat_tasks[name] = task

    def _cancel_heartbeat(self, name: str) -> None:
        """Cancel the heartbeat loop for an agent."""
        task = self._heartbeat_tasks.pop(name, None)
        if task is not None and not task.done():
            task.cancel()

    async def _heartbeat_loop(self, name: str) -> None:
        """Emit heartbeats at configurable interval. Mark unhealthy after 3 missed.

        Requirement 1.9
        """
        try:
            while True:
                await asyncio.sleep(self._heartbeat_interval)

                if name not in self._agents:
                    break

                now = datetime.now(timezone.utc)
                self._last_heartbeats[name] = now
                self._healthy[name] = True

                # Persist heartbeat to Redis
                try:
                    self._redis.set(
                        f"{self.HEARTBEAT_KEY_PREFIX}:{name}",
                        now.isoformat(),
                        ex=self._heartbeat_interval * 4,
                    )
                except Exception as e:
                    logger.warning("Failed to persist heartbeat for '%s': %s", name, e)

        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error("Heartbeat loop for '%s' crashed: %s", name, e)

    def check_heartbeat_health(self, name: str) -> bool:
        """Check if an agent's heartbeat is current. Mark unhealthy after 3 missed intervals.

        Requirement 1.9
        """
        if name not in self._last_heartbeats:
            return False

        now = datetime.now(timezone.utc)
        elapsed = (now - self._last_heartbeats[name]).total_seconds()
        threshold = self._heartbeat_interval * 3

        healthy = elapsed <= threshold
        self._healthy[name] = healthy
        return healthy

    # ── Event Publishing ─────────────────────────────────────────────

    def _publish_lifecycle_event(
        self, name: str, prev_state: AgentState, new_state: AgentState
    ) -> None:
        """Publish AgentLifecycleChanged event via EventPublisher and agents:events stream.

        Requirement 1.10
        """
        from src.models.trading_event import TradingEvent

        now = datetime.now(timezone.utc)
        event = TradingEvent(
            event_type="Agent:LifecycleChanged",
            aggregate_id=name,
            sequence_number=0,
            correlation_id=self._current_tasks.get(name),
            payload={
                "agent_name": name,
                "previous_state": prev_state.value,
                "new_state": new_state.value,
                "timestamp": now.isoformat(),
            },
            source_service="strategy-engine",
        )

        # Publish to events:stream via EventPublisher
        try:
            self._event_publisher.publish(event)
        except Exception as e:
            logger.warning("Failed to publish lifecycle event for '%s': %s", name, e)

        # Also publish to agents:events stream
        try:
            self._redis.xadd(
                self.EVENTS_STREAM,
                {
                    "type": "AgentLifecycleChanged",
                    "agent_name": name,
                    "previous_state": prev_state.value,
                    "new_state": new_state.value,
                    "timestamp": now.isoformat(),
                },
                maxlen=10000,
                approximate=True,
            )
        except Exception as e:
            logger.warning(
                "Failed to publish to agents:events for '%s': %s", name, e
            )

    # ── Internal Helpers ───────────────────────────────────────────

    def get_state_machine(self, name: str) -> AgentStateMachine:
        """Return the state machine for an agent (for advanced use)."""
        if name not in self._state_machines:
            raise KeyError(f"Agent '{name}' is not registered")
        return self._state_machines[name]
