"""Unit tests for AgentRegistry."""

import asyncio
import time
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from src.agents.base import Agent
from src.agents.models import AgentState, AgentTask, TaskResult
from src.agents.registry import AgentRegistry
from src.agents.state_machine import InvalidTransitionError


# ── Helpers ────────────────────────────────────────────────────────


class FakeAgent(Agent):
    """Minimal concrete Agent for testing."""

    def __init__(self, agent_name: str = "test-agent", desc: str = "A test agent"):
        self._name = agent_name
        self._desc = desc

    def name(self) -> str:
        return self._name

    def description(self) -> str:
        return self._desc

    def supported_task_types(self) -> list[str]:
        return ["test_task"]

    def supported_tools(self) -> list[str]:
        return ["tool_a"]

    async def run(self, task: AgentTask) -> TaskResult:
        return TaskResult(
            task_id=task.id, agent_name=self._name, status=task.status, output={}
        )

    def get_system_prompt(self) -> str:
        return "You are a test agent."


class FakeRedis:
    """Minimal Redis stub for unit tests."""

    def __init__(self):
        self._store: dict[str, str] = {}
        self._streams: dict[str, list] = {}

    def set(self, key, value, ex=None):
        self._store[key] = value

    def get(self, key):
        return self._store.get(key)

    def xadd(self, stream, fields, maxlen=None, approximate=False):
        self._streams.setdefault(stream, []).append(fields)
        return b"1-0"


class FakeEventPublisher:
    """Captures published events for assertions."""

    def __init__(self):
        self.events: list = []

    def publish(self, event) -> None:
        self.events.append(event)


@pytest.fixture
def redis():
    return FakeRedis()


@pytest.fixture
def publisher():
    return FakeEventPublisher()


@pytest.fixture
def registry(redis, publisher):
    return AgentRegistry(
        redis_client=redis,
        event_publisher=publisher,
        heartbeat_interval=30,
        backend_url="http://localhost:3000",
    )


@pytest.fixture
def agent():
    return FakeAgent()


# ── Registration Tests ─────────────────────────────────────────────


class TestRegister:
    def test_register_agent(self, registry: AgentRegistry, agent: FakeAgent):
        registry.register(agent)
        assert registry.get("test-agent") is agent

    def test_register_duplicate_raises(self, registry: AgentRegistry, agent: FakeAgent):
        registry.register(agent)
        with pytest.raises(ValueError, match="already registered"):
            registry.register(agent)

    def test_register_invalid_interface_raises(self, registry: AgentRegistry):
        with pytest.raises(TypeError, match="Agent must implement"):
            registry.register("not-an-agent")  # type: ignore

    def test_register_multiple_agents(self, registry: AgentRegistry):
        a1 = FakeAgent("agent-1")
        a2 = FakeAgent("agent-2")
        registry.register(a1)
        registry.register(a2)
        assert registry.get("agent-1") is a1
        assert registry.get("agent-2") is a2


class TestDeregister:
    def test_deregister_agent(self, registry: AgentRegistry, agent: FakeAgent):
        registry.register(agent)
        registry.deregister("test-agent")
        with pytest.raises(KeyError):
            registry.get("test-agent")

    def test_deregister_unknown_raises(self, registry: AgentRegistry):
        with pytest.raises(KeyError, match="not registered"):
            registry.deregister("ghost")


class TestGet:
    def test_get_existing(self, registry: AgentRegistry, agent: FakeAgent):
        registry.register(agent)
        assert registry.get("test-agent") is agent

    def test_get_missing_raises(self, registry: AgentRegistry):
        with pytest.raises(KeyError, match="not registered"):
            registry.get("nope")


class TestListAgents:
    def test_list_empty(self, registry: AgentRegistry):
        assert registry.list_agents() == []

    def test_list_returns_metadata(self, registry: AgentRegistry, agent: FakeAgent):
        registry.register(agent)
        agents = registry.list_agents()
        assert len(agents) == 1
        info = agents[0]
        assert info["name"] == "test-agent"
        assert info["description"] == "A test agent"
        assert info["state"] == "IDLE"
        assert info["healthy"] is True
        assert info["supported_task_types"] == ["test_task"]
        assert info["supported_tools"] == ["tool_a"]
        assert info["current_task_id"] is None
        assert info["uptime_seconds"] >= 0


# ── Lifecycle Tests ────────────────────────────────────────────────


class TestStart:
    @pytest.mark.asyncio
    async def test_start_transitions_to_planning(
        self, registry: AgentRegistry, agent: FakeAgent
    ):
        registry.register(agent)
        await registry.start("test-agent")
        sm = registry.get_state_machine("test-agent")
        assert sm.current_state == AgentState.PLANNING

    @pytest.mark.asyncio
    async def test_start_with_task_sets_current_task(
        self, registry: AgentRegistry, agent: FakeAgent
    ):
        registry.register(agent)
        task = AgentTask(type="test_task", agent_name="test-agent")
        await registry.start("test-agent", task=task)
        health = registry.health_check("test-agent")
        assert health["current_task_id"] == task.id

    @pytest.mark.asyncio
    async def test_start_publishes_lifecycle_event(
        self, registry: AgentRegistry, agent: FakeAgent, publisher: FakeEventPublisher
    ):
        registry.register(agent)
        await registry.start("test-agent")
        assert len(publisher.events) == 1
        evt = publisher.events[0]
        assert evt.event_type == "Agent:LifecycleChanged"
        assert evt.payload["previous_state"] == "IDLE"
        assert evt.payload["new_state"] == "PLANNING"

    @pytest.mark.asyncio
    async def test_start_unknown_agent_raises(self, registry: AgentRegistry):
        with pytest.raises(KeyError):
            await registry.start("ghost")


class TestStop:
    @pytest.mark.asyncio
    async def test_stop_transitions_to_idle(
        self, registry: AgentRegistry, agent: FakeAgent
    ):
        registry.register(agent)
        await registry.start("test-agent")
        await registry.stop("test-agent")
        sm = registry.get_state_machine("test-agent")
        assert sm.current_state == AgentState.IDLE

    @pytest.mark.asyncio
    async def test_stop_clears_current_task(
        self, registry: AgentRegistry, agent: FakeAgent
    ):
        registry.register(agent)
        task = AgentTask(type="test_task", agent_name="test-agent")
        await registry.start("test-agent", task=task)
        await registry.stop("test-agent")
        health = registry.health_check("test-agent")
        assert health["current_task_id"] is None

    @pytest.mark.asyncio
    async def test_stop_unknown_agent_raises(self, registry: AgentRegistry):
        with pytest.raises(KeyError):
            await registry.stop("ghost")


class TestPause:
    @pytest.mark.asyncio
    async def test_pause_from_planning(
        self, registry: AgentRegistry, agent: FakeAgent
    ):
        registry.register(agent)
        await registry.start("test-agent")
        await registry.pause("test-agent")
        sm = registry.get_state_machine("test-agent")
        assert sm.current_state == AgentState.PAUSED

    @pytest.mark.asyncio
    async def test_pause_publishes_event(
        self, registry: AgentRegistry, agent: FakeAgent, publisher: FakeEventPublisher
    ):
        registry.register(agent)
        await registry.start("test-agent")
        publisher.events.clear()
        await registry.pause("test-agent")
        assert len(publisher.events) == 1
        assert publisher.events[0].payload["new_state"] == "PAUSED"

    @pytest.mark.asyncio
    async def test_pause_from_idle_raises(
        self, registry: AgentRegistry, agent: FakeAgent
    ):
        registry.register(agent)
        with pytest.raises(InvalidTransitionError):
            await registry.pause("test-agent")


class TestResume:
    @pytest.mark.asyncio
    async def test_resume_restores_state(
        self, registry: AgentRegistry, agent: FakeAgent
    ):
        registry.register(agent)
        await registry.start("test-agent")
        await registry.pause("test-agent")
        await registry.resume("test-agent")
        sm = registry.get_state_machine("test-agent")
        assert sm.current_state == AgentState.PLANNING

    @pytest.mark.asyncio
    async def test_resume_publishes_event(
        self, registry: AgentRegistry, agent: FakeAgent, publisher: FakeEventPublisher
    ):
        registry.register(agent)
        await registry.start("test-agent")
        await registry.pause("test-agent")
        publisher.events.clear()
        await registry.resume("test-agent")
        assert len(publisher.events) == 1
        assert publisher.events[0].payload["previous_state"] == "PAUSED"
        assert publisher.events[0].payload["new_state"] == "PLANNING"


# ── Health Check Tests ─────────────────────────────────────────────


class TestHealthCheck:
    def test_health_check_returns_required_fields(
        self, registry: AgentRegistry, agent: FakeAgent
    ):
        registry.register(agent)
        health = registry.health_check("test-agent")
        assert "state" in health
        assert "last_heartbeat" in health
        assert "current_task_id" in health
        assert "uptime" in health
        assert "error_count" in health
        assert "healthy" in health

    def test_health_check_initial_values(
        self, registry: AgentRegistry, agent: FakeAgent
    ):
        registry.register(agent)
        health = registry.health_check("test-agent")
        assert health["state"] == "IDLE"
        assert health["current_task_id"] is None
        assert health["error_count"] == 0
        assert health["healthy"] is True
        assert health["uptime"] >= 0

    def test_health_check_unknown_agent_raises(self, registry: AgentRegistry):
        with pytest.raises(KeyError):
            registry.health_check("ghost")


# ── Heartbeat Tests ────────────────────────────────────────────────


class TestHeartbeat:
    def test_check_heartbeat_healthy(
        self, registry: AgentRegistry, agent: FakeAgent
    ):
        registry.register(agent)
        assert registry.check_heartbeat_health("test-agent") is True

    def test_check_heartbeat_unhealthy_after_threshold(self, redis, publisher):
        reg = AgentRegistry(
            redis_client=redis,
            event_publisher=publisher,
            heartbeat_interval=1,  # 1 second interval → 3s threshold
        )
        agent = FakeAgent()
        reg.register(agent)
        # Manually set last heartbeat to the past
        from datetime import timedelta

        reg._last_heartbeats["test-agent"] = datetime.now(timezone.utc) - timedelta(
            seconds=5
        )
        assert reg.check_heartbeat_health("test-agent") is False
        assert reg._healthy["test-agent"] is False

    def test_check_heartbeat_unknown_agent(self, registry: AgentRegistry):
        assert registry.check_heartbeat_health("ghost") is False


# ── Event Publishing Tests ─────────────────────────────────────────


class TestEventPublishing:
    @pytest.mark.asyncio
    async def test_lifecycle_events_published_to_event_publisher(
        self, registry: AgentRegistry, agent: FakeAgent, publisher: FakeEventPublisher
    ):
        registry.register(agent)
        await registry.start("test-agent")
        assert len(publisher.events) == 1
        evt = publisher.events[0]
        assert evt.event_type == "Agent:LifecycleChanged"
        assert evt.aggregate_id == "test-agent"
        assert evt.payload["agent_name"] == "test-agent"

    @pytest.mark.asyncio
    async def test_lifecycle_events_published_to_redis_stream(
        self, registry: AgentRegistry, agent: FakeAgent, redis: FakeRedis
    ):
        registry.register(agent)
        await registry.start("test-agent")
        stream_events = redis._streams.get("agents:events", [])
        assert len(stream_events) == 1
        assert stream_events[0]["type"] == "AgentLifecycleChanged"
        assert stream_events[0]["agent_name"] == "test-agent"

    @pytest.mark.asyncio
    async def test_multiple_lifecycle_events(
        self, registry: AgentRegistry, agent: FakeAgent, publisher: FakeEventPublisher
    ):
        registry.register(agent)
        await registry.start("test-agent")
        await registry.pause("test-agent")
        await registry.resume("test-agent")
        await registry.stop("test-agent")
        # start + pause + resume + stop = 4 events
        assert len(publisher.events) == 4


# ── State Machine Access ───────────────────────────────────────────


class TestGetStateMachine:
    def test_get_state_machine(self, registry: AgentRegistry, agent: FakeAgent):
        registry.register(agent)
        sm = registry.get_state_machine("test-agent")
        assert sm.current_state == AgentState.IDLE

    def test_get_state_machine_unknown_raises(self, registry: AgentRegistry):
        with pytest.raises(KeyError):
            registry.get_state_machine("ghost")
