"""Unit tests for ApprovalGateManager.

Requirements: 9.1–9.5
"""

import json
from datetime import datetime, timedelta, timezone

import pytest

from src.agents.approval import (
    APPROVAL_INDEX_KEY,
    APPROVAL_KEY_PREFIX,
    ACTIVITY_CHANNEL,
    EVENTS_STREAM,
    CRITICAL_ACTIONS,
    ApprovalGateManager,
)
from src.agents.models import AgentState, ApprovalRequest, ApprovalStatus


# ── Fakes ──────────────────────────────────────────────────────────


class FakeRedis:
    """Minimal Redis stub supporting hashes, sets, streams, and pub/sub."""

    def __init__(self):
        self._hashes: dict[str, dict[str, str]] = {}
        self._sets: dict[str, set[str]] = {}
        self._streams: dict[str, list[dict]] = {}
        self._pubsub: list[tuple[str, str]] = []

    def hset(self, key, mapping=None, **kwargs):
        if mapping is None:
            mapping = kwargs
        self._hashes.setdefault(key, {}).update(
            {str(k): str(v) for k, v in mapping.items()}
        )

    def hgetall(self, key):
        return dict(self._hashes.get(key, {}))

    def sadd(self, key, *values):
        s = self._sets.setdefault(key, set())
        for v in values:
            s.add(v)

    def srem(self, key, *values):
        s = self._sets.get(key, set())
        for v in values:
            s.discard(v)

    def smembers(self, key):
        return set(self._sets.get(key, set()))

    def xadd(self, stream, fields, maxlen=None, approximate=False):
        self._streams.setdefault(stream, []).append(fields)
        return b"1-0"

    def publish(self, channel, message):
        self._pubsub.append((channel, message))


class FakeEventPublisher:
    """Captures published TradingEvents."""

    def __init__(self):
        self.events: list = []

    def publish(self, event) -> None:
        self.events.append(event)


class FakeStateMachine:
    """Tracks state transitions for assertions."""

    def __init__(self, initial_state: AgentState = AgentState.EXECUTING):
        self._state = initial_state
        self.transitions: list[tuple[AgentState, str]] = []

    @property
    def current_state(self) -> AgentState:
        return self._state

    def transition(self, target: AgentState, reason: str = None) -> None:
        self.transitions.append((target, reason))
        self._state = target


class FakeRegistry:
    """Minimal registry stub that returns a FakeStateMachine."""

    def __init__(self, state_machines: dict[str, FakeStateMachine] = None):
        self._sms = state_machines or {}

    def get_state_machine(self, name: str):
        if name not in self._sms:
            raise KeyError(f"Agent '{name}' not registered")
        return self._sms[name]


# ── Fixtures ───────────────────────────────────────────────────────


@pytest.fixture
def redis():
    return FakeRedis()


@pytest.fixture
def publisher():
    return FakeEventPublisher()


@pytest.fixture
def manager(redis, publisher):
    return ApprovalGateManager(
        redis_client=redis,
        event_publisher=publisher,
        backend_url="http://localhost:3000",
        timeout_hours=24.0,
    )


@pytest.fixture
def state_machine():
    return FakeStateMachine(initial_state=AgentState.EXECUTING)


# ── request_approval Tests ─────────────────────────────────────────


class TestRequestApproval:
    @pytest.mark.asyncio
    async def test_creates_approval_in_redis(self, manager, redis):
        approval_id = await manager.request_approval(
            agent_name="converter",
            task_id="task-1",
            action_description="deploy LIVE",
        )
        key = f"{APPROVAL_KEY_PREFIX}:{approval_id}"
        data = redis.hgetall(key)
        assert data["agent_name"] == "converter"
        assert data["task_id"] == "task-1"
        assert data["status"] == ApprovalStatus.PENDING.value

    @pytest.mark.asyncio
    async def test_adds_to_pending_index(self, manager, redis):
        approval_id = await manager.request_approval(
            agent_name="converter",
            task_id="task-1",
            action_description="deploy LIVE",
        )
        assert approval_id in redis.smembers(APPROVAL_INDEX_KEY)

    @pytest.mark.asyncio
    async def test_transitions_agent_to_waiting(self, manager, state_machine):
        await manager.request_approval(
            agent_name="converter",
            task_id="task-1",
            action_description="deploy LIVE",
            state_machine=state_machine,
        )
        assert state_machine.current_state == AgentState.WAITING_FOR_INPUT

    @pytest.mark.asyncio
    async def test_publishes_event(self, manager, publisher):
        await manager.request_approval(
            agent_name="converter",
            task_id="task-1",
            action_description="deploy LIVE",
        )
        assert len(publisher.events) == 1
        assert publisher.events[0].event_type == "Agent:ApprovalRequested"

    @pytest.mark.asyncio
    async def test_publishes_to_agents_stream(self, manager, redis):
        await manager.request_approval(
            agent_name="converter",
            task_id="task-1",
            action_description="deploy LIVE",
        )
        stream_entries = redis._streams.get(EVENTS_STREAM, [])
        assert len(stream_entries) == 1
        assert stream_entries[0]["type"] == "ApprovalRequested"

    @pytest.mark.asyncio
    async def test_publishes_activity(self, manager, redis):
        await manager.request_approval(
            agent_name="converter",
            task_id="task-1",
            action_description="deploy LIVE",
        )
        assert len(redis._pubsub) == 1
        channel, msg = redis._pubsub[0]
        assert channel == ACTIVITY_CHANNEL
        parsed = json.loads(msg)
        assert parsed["type"] == "ApprovalRequested"

    @pytest.mark.asyncio
    async def test_returns_uuid(self, manager):
        approval_id = await manager.request_approval(
            agent_name="converter",
            task_id="task-1",
            action_description="deploy LIVE",
        )
        assert isinstance(approval_id, str)
        assert len(approval_id) > 0


# ── approve Tests ──────────────────────────────────────────────────


class TestApprove:
    @pytest.mark.asyncio
    async def test_approve_updates_status(self, manager, redis):
        approval_id = await manager.request_approval(
            agent_name="converter", task_id="t1", action_description="deploy LIVE"
        )
        await manager.approve(approval_id, resolved_by="admin")
        data = redis.hgetall(f"{APPROVAL_KEY_PREFIX}:{approval_id}")
        assert data["status"] == ApprovalStatus.APPROVED.value
        assert data["resolved_by"] == "admin"

    @pytest.mark.asyncio
    async def test_approve_removes_from_pending(self, manager, redis):
        approval_id = await manager.request_approval(
            agent_name="converter", task_id="t1", action_description="deploy LIVE"
        )
        await manager.approve(approval_id, resolved_by="admin")
        assert approval_id not in redis.smembers(APPROVAL_INDEX_KEY)

    @pytest.mark.asyncio
    async def test_approve_transitions_to_executing(self, manager, state_machine):
        approval_id = await manager.request_approval(
            agent_name="converter",
            task_id="t1",
            action_description="deploy LIVE",
            state_machine=state_machine,
        )
        sm_exec = FakeStateMachine(initial_state=AgentState.WAITING_FOR_INPUT)
        await manager.approve(approval_id, resolved_by="admin", state_machine=sm_exec)
        assert sm_exec.current_state == AgentState.EXECUTING

    @pytest.mark.asyncio
    async def test_approve_publishes_resolved_event(self, manager, publisher):
        approval_id = await manager.request_approval(
            agent_name="converter", task_id="t1", action_description="deploy LIVE"
        )
        await manager.approve(approval_id, resolved_by="admin")
        resolved_events = [
            e for e in publisher.events if e.event_type == "Agent:ApprovalResolved"
        ]
        assert len(resolved_events) == 1
        assert resolved_events[0].payload["status"] == "APPROVED"

    @pytest.mark.asyncio
    async def test_approve_not_found_raises(self, manager):
        with pytest.raises(KeyError):
            await manager.approve("nonexistent", resolved_by="admin")

    @pytest.mark.asyncio
    async def test_approve_already_resolved_raises(self, manager):
        approval_id = await manager.request_approval(
            agent_name="converter", task_id="t1", action_description="deploy LIVE"
        )
        await manager.approve(approval_id, resolved_by="admin")
        with pytest.raises(ValueError, match="not pending"):
            await manager.approve(approval_id, resolved_by="admin")


# ── deny Tests ─────────────────────────────────────────────────────


class TestDeny:
    @pytest.mark.asyncio
    async def test_deny_updates_status(self, manager, redis):
        approval_id = await manager.request_approval(
            agent_name="converter", task_id="t1", action_description="deploy LIVE"
        )
        await manager.deny(approval_id, resolved_by="admin", reason="too risky")
        data = redis.hgetall(f"{APPROVAL_KEY_PREFIX}:{approval_id}")
        assert data["status"] == ApprovalStatus.DENIED.value
        assert data["resolution_reason"] == "too risky"

    @pytest.mark.asyncio
    async def test_deny_transitions_to_reviewing(self, manager, state_machine):
        approval_id = await manager.request_approval(
            agent_name="converter",
            task_id="t1",
            action_description="deploy LIVE",
            state_machine=state_machine,
        )
        sm_review = FakeStateMachine(initial_state=AgentState.WAITING_FOR_INPUT)
        await manager.deny(
            approval_id, resolved_by="admin", reason="too risky", state_machine=sm_review
        )
        assert sm_review.current_state == AgentState.REVIEWING

    @pytest.mark.asyncio
    async def test_deny_publishes_resolved_event(self, manager, publisher):
        approval_id = await manager.request_approval(
            agent_name="converter", task_id="t1", action_description="deploy LIVE"
        )
        await manager.deny(approval_id, resolved_by="admin", reason="too risky")
        resolved_events = [
            e for e in publisher.events if e.event_type == "Agent:ApprovalResolved"
        ]
        assert len(resolved_events) == 1
        assert resolved_events[0].payload["status"] == "DENIED"
        assert resolved_events[0].payload["resolution_reason"] == "too risky"

    @pytest.mark.asyncio
    async def test_deny_not_found_raises(self, manager):
        with pytest.raises(KeyError):
            await manager.deny("nonexistent", resolved_by="admin", reason="nope")


# ── check_expiry Tests ─────────────────────────────────────────────


class TestCheckExpiry:
    @pytest.mark.asyncio
    async def test_expires_old_approvals(self, redis, publisher):
        mgr = ApprovalGateManager(
            redis_client=redis,
            event_publisher=publisher,
            backend_url="http://localhost:3000",
            timeout_hours=1.0,
        )
        approval_id = await mgr.request_approval(
            agent_name="converter", task_id="t1", action_description="deploy LIVE"
        )
        # Backdate the created_at to 2 hours ago
        key = f"{APPROVAL_KEY_PREFIX}:{approval_id}"
        old_time = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        redis.hset(key, mapping={"created_at": old_time})

        expired = await mgr.check_expiry()
        assert approval_id in expired

        data = redis.hgetall(key)
        assert data["status"] == ApprovalStatus.EXPIRED.value

    @pytest.mark.asyncio
    async def test_does_not_expire_recent(self, manager, redis):
        approval_id = await manager.request_approval(
            agent_name="converter", task_id="t1", action_description="deploy LIVE"
        )
        expired = await manager.check_expiry()
        assert approval_id not in expired

    @pytest.mark.asyncio
    async def test_expiry_transitions_agent_to_idle(self, redis, publisher):
        mgr = ApprovalGateManager(
            redis_client=redis,
            event_publisher=publisher,
            backend_url="http://localhost:3000",
            timeout_hours=1.0,
        )
        sm = FakeStateMachine(initial_state=AgentState.WAITING_FOR_INPUT)
        registry = FakeRegistry({"converter": sm})

        approval_id = await mgr.request_approval(
            agent_name="converter", task_id="t1", action_description="deploy LIVE"
        )
        key = f"{APPROVAL_KEY_PREFIX}:{approval_id}"
        old_time = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        redis.hset(key, mapping={"created_at": old_time})

        await mgr.check_expiry(registry=registry)
        assert sm.current_state == AgentState.IDLE

    @pytest.mark.asyncio
    async def test_expiry_removes_from_pending(self, redis, publisher):
        mgr = ApprovalGateManager(
            redis_client=redis,
            event_publisher=publisher,
            backend_url="http://localhost:3000",
            timeout_hours=1.0,
        )
        approval_id = await mgr.request_approval(
            agent_name="converter", task_id="t1", action_description="deploy LIVE"
        )
        key = f"{APPROVAL_KEY_PREFIX}:{approval_id}"
        old_time = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        redis.hset(key, mapping={"created_at": old_time})

        await mgr.check_expiry()
        assert approval_id not in redis.smembers(APPROVAL_INDEX_KEY)


# ── list_pending Tests ─────────────────────────────────────────────


class TestListPending:
    @pytest.mark.asyncio
    async def test_list_empty(self, manager):
        assert manager.list_pending() == []

    @pytest.mark.asyncio
    async def test_list_returns_pending(self, manager):
        await manager.request_approval(
            agent_name="converter", task_id="t1", action_description="deploy LIVE"
        )
        await manager.request_approval(
            agent_name="backtest", task_id="t2", action_description="modify risk"
        )
        pending = manager.list_pending()
        assert len(pending) == 2
        names = {p["agent_name"] for p in pending}
        assert names == {"converter", "backtest"}

    @pytest.mark.asyncio
    async def test_list_excludes_resolved(self, manager):
        approval_id = await manager.request_approval(
            agent_name="converter", task_id="t1", action_description="deploy LIVE"
        )
        await manager.approve(approval_id, resolved_by="admin")
        pending = manager.list_pending()
        assert len(pending) == 0


# ── Critical Actions ───────────────────────────────────────────────


class TestCriticalActions:
    def test_critical_actions_defined(self):
        assert "deploy_live" in CRITICAL_ACTIONS
        assert "modify_risk_params" in CRITICAL_ACTIONS
        assert "delete_strategy_with_positions" in CRITICAL_ACTIONS
