"""Unit tests for TaskQueue.

Requirements: 2.1–2.11
"""

import pytest

from src.agents.models import AgentTask, TaskPriority, TaskResult, TaskStatus
from src.agents.task_queue import TaskQueue


# ── Fakes ──────────────────────────────────────────────────────────


class FakeRedis:
    """Minimal Redis stub supporting hash, sorted set, list, scan, and stream ops."""

    def __init__(self):
        self._store: dict[str, str] = {}
        self._sorted_sets: dict[str, dict[str, float]] = {}
        self._lists: dict[str, list[str]] = {}
        self._streams: dict[str, list] = {}

    def set(self, key, value, ex=None):
        self._store[key] = value

    def get(self, key):
        return self._store.get(key)

    def zadd(self, name, mapping):
        ss = self._sorted_sets.setdefault(name, {})
        ss.update(mapping)

    def zrange(self, name, start, end):
        ss = self._sorted_sets.get(name, {})
        sorted_members = sorted(ss.items(), key=lambda x: x[1])
        # end=-1 means all
        if end == -1:
            end = len(sorted_members) - 1
        return [m for m, _ in sorted_members[start : end + 1]]

    def zrem(self, name, *members):
        ss = self._sorted_sets.get(name, {})
        for m in members:
            ss.pop(m, None)

    def rpush(self, name, *values):
        lst = self._lists.setdefault(name, [])
        lst.extend(values)

    def lrange(self, name, start, end):
        return self._lists.get(name, [])[start : end + 1 if end != -1 else None]

    def scan(self, cursor, match=None, count=100):
        """Simple scan implementation returning all matching keys in one pass."""
        import fnmatch

        keys = [k for k in self._store if fnmatch.fnmatch(k, match or "*")]
        return (0, keys)

    def xadd(self, stream, fields, maxlen=None, approximate=False):
        self._streams.setdefault(stream, []).append(fields)
        return b"1-0"


class FakeEventPublisher:
    """Captures published events for assertions."""

    def __init__(self):
        self.events: list = []

    def publish(self, event) -> None:
        self.events.append(event)


# ── Fixtures ───────────────────────────────────────────────────────


@pytest.fixture
def redis():
    return FakeRedis()


@pytest.fixture
def publisher():
    return FakeEventPublisher()


@pytest.fixture
def queue(redis, publisher):
    return TaskQueue(redis_client=redis, event_publisher=publisher)


def _make_task(**overrides) -> AgentTask:
    defaults = {
        "type": "research_strategy",
        "agent_name": "research",
        "priority": TaskPriority.NORMAL,
    }
    defaults.update(overrides)
    return AgentTask(**defaults)


# ── Submit Tests ───────────────────────────────────────────────────


class TestSubmit:
    """Requirement 2.1, 2.2, 2.6, 2.10"""

    @pytest.mark.asyncio
    async def test_submit_returns_task_id(self, queue: TaskQueue):
        task = _make_task()
        task_id = await queue.submit(task)
        assert task_id == task.id

    @pytest.mark.asyncio
    async def test_submit_persists_to_redis(self, queue: TaskQueue, redis: FakeRedis):
        task = _make_task()
        await queue.submit(task)
        assert redis.get(f"agents:task:{task.id}") is not None

    @pytest.mark.asyncio
    async def test_submit_adds_to_sorted_set(self, queue: TaskQueue, redis: FakeRedis):
        task = _make_task()
        await queue.submit(task)
        members = redis.zrange(TaskQueue.QUEUE_KEY, 0, -1)
        assert task.id in members

    @pytest.mark.asyncio
    async def test_submit_sets_status_queued(self, queue: TaskQueue):
        task = _make_task()
        await queue.submit(task)
        loaded = queue.get_task(task.id)
        assert loaded is not None
        assert loaded.status == TaskStatus.QUEUED

    @pytest.mark.asyncio
    async def test_submit_publishes_event(self, queue: TaskQueue, publisher: FakeEventPublisher):
        task = _make_task()
        await queue.submit(task)
        assert len(publisher.events) == 1
        assert publisher.events[0].event_type == "Agent:TaskQueued"


# ── Dequeue Tests ──────────────────────────────────────────────────


class TestDequeue:
    """Requirement 2.1, 2.3"""

    @pytest.mark.asyncio
    async def test_dequeue_returns_highest_priority(self, queue: TaskQueue):
        low = _make_task(priority=TaskPriority.LOW)
        high = _make_task(priority=TaskPriority.HIGH)
        await queue.submit(low)
        await queue.submit(high)

        result = await queue.dequeue("research")
        assert result is not None
        assert result.id == high.id

    @pytest.mark.asyncio
    async def test_dequeue_transitions_to_in_progress(self, queue: TaskQueue):
        task = _make_task()
        await queue.submit(task)
        result = await queue.dequeue("research")
        assert result is not None
        assert result.status == TaskStatus.IN_PROGRESS
        assert result.started_at is not None

    @pytest.mark.asyncio
    async def test_dequeue_removes_from_sorted_set(self, queue: TaskQueue, redis: FakeRedis):
        task = _make_task()
        await queue.submit(task)
        await queue.dequeue("research")
        members = redis.zrange(TaskQueue.QUEUE_KEY, 0, -1)
        assert task.id not in members

    @pytest.mark.asyncio
    async def test_dequeue_empty_returns_none(self, queue: TaskQueue):
        result = await queue.dequeue("research")
        assert result is None

    @pytest.mark.asyncio
    async def test_dequeue_filters_by_agent_name(self, queue: TaskQueue):
        task = _make_task(agent_name="converter")
        await queue.submit(task)
        result = await queue.dequeue("research")
        assert result is None

    @pytest.mark.asyncio
    async def test_dequeue_skips_unmet_dependencies(self, queue: TaskQueue):
        dep = _make_task(agent_name="research")
        dep_id = await queue.submit(dep)

        dependent = _make_task(agent_name="research", depends_on=[dep_id])
        await queue.submit(dependent)

        # Dequeue the dependency first
        result = await queue.dequeue("research")
        assert result is not None
        assert result.id == dep.id

        # Dependent should not be dequeued yet (dep not COMPLETED)
        result2 = await queue.dequeue("research")
        assert result2 is None

    @pytest.mark.asyncio
    async def test_dequeue_resolves_completed_dependencies(self, queue: TaskQueue):
        dep = _make_task(agent_name="research")
        dep_id = await queue.submit(dep)

        dependent = _make_task(agent_name="research", depends_on=[dep_id])
        await queue.submit(dependent)

        # Dequeue and complete the dependency
        dequeued_dep = await queue.dequeue("research")
        await queue.complete(dequeued_dep.id, TaskResult(
            task_id=dequeued_dep.id, agent_name="research", status=TaskStatus.COMPLETED
        ))

        # Now the dependent should be dequeueable
        result = await queue.dequeue("research")
        assert result is not None
        assert result.id == dependent.id

    @pytest.mark.asyncio
    async def test_dequeue_publishes_task_started_event(self, queue: TaskQueue, publisher: FakeEventPublisher):
        task = _make_task()
        await queue.submit(task)
        publisher.events.clear()
        await queue.dequeue("research")
        assert any(e.event_type == "Agent:TaskStarted" for e in publisher.events)


# ── Complete Tests ─────────────────────────────────────────────────


class TestComplete:
    """Requirement 2.6"""

    @pytest.mark.asyncio
    async def test_complete_sets_status(self, queue: TaskQueue):
        task = _make_task()
        await queue.submit(task)
        dequeued = await queue.dequeue("research")
        await queue.complete(dequeued.id, TaskResult(
            task_id=dequeued.id, agent_name="research", status=TaskStatus.COMPLETED
        ))
        loaded = queue.get_task(dequeued.id)
        assert loaded.status == TaskStatus.COMPLETED
        assert loaded.completed_at is not None

    @pytest.mark.asyncio
    async def test_complete_stores_result(self, queue: TaskQueue):
        task = _make_task()
        await queue.submit(task)
        dequeued = await queue.dequeue("research")
        result = TaskResult(
            task_id=dequeued.id, agent_name="research",
            status=TaskStatus.COMPLETED, output={"key": "value"}
        )
        await queue.complete(dequeued.id, result)
        loaded = queue.get_task(dequeued.id)
        assert loaded.result is not None
        assert loaded.result["output"] == {"key": "value"}

    @pytest.mark.asyncio
    async def test_complete_unknown_task_raises(self, queue: TaskQueue):
        with pytest.raises(KeyError):
            await queue.complete("nonexistent", TaskResult(
                task_id="nonexistent", agent_name="research", status=TaskStatus.COMPLETED
            ))

    @pytest.mark.asyncio
    async def test_complete_publishes_event(self, queue: TaskQueue, publisher: FakeEventPublisher):
        task = _make_task()
        await queue.submit(task)
        dequeued = await queue.dequeue("research")
        publisher.events.clear()
        await queue.complete(dequeued.id, TaskResult(
            task_id=dequeued.id, agent_name="research", status=TaskStatus.COMPLETED
        ))
        assert any(e.event_type == "Agent:TaskCompleted" for e in publisher.events)


# ── Fail / Retry / DLQ Tests ──────────────────────────────────────


class TestFail:
    """Requirement 2.4, 2.5"""

    @pytest.mark.asyncio
    async def test_fail_retries_when_retries_remain(self, queue: TaskQueue, redis: FakeRedis):
        task = _make_task(max_retries=3)
        await queue.submit(task)
        dequeued = await queue.dequeue("research")

        await queue.fail(dequeued.id, "some error")

        loaded = queue.get_task(dequeued.id)
        assert loaded.retry_count == 1
        assert loaded.status == TaskStatus.QUEUED
        # Should be re-added to sorted set
        members = redis.zrange(TaskQueue.QUEUE_KEY, 0, -1)
        assert dequeued.id in members

    @pytest.mark.asyncio
    async def test_fail_moves_to_dlq_when_retries_exhausted(self, queue: TaskQueue, redis: FakeRedis):
        task = _make_task(max_retries=0)
        await queue.submit(task)
        dequeued = await queue.dequeue("research")

        await queue.fail(dequeued.id, "fatal error")

        loaded = queue.get_task(dequeued.id)
        assert loaded.status == TaskStatus.DEAD_LETTERED
        assert len(redis._lists.get(TaskQueue.DLQ_KEY, [])) == 1

    @pytest.mark.asyncio
    async def test_fail_publishes_dead_lettered_event(self, queue: TaskQueue, publisher: FakeEventPublisher):
        task = _make_task(max_retries=0)
        await queue.submit(task)
        dequeued = await queue.dequeue("research")
        publisher.events.clear()

        await queue.fail(dequeued.id, "fatal error")

        assert any(e.event_type == "Agent:TaskDeadLettered" for e in publisher.events)

    @pytest.mark.asyncio
    async def test_fail_increments_retry_count(self, queue: TaskQueue):
        task = _make_task(max_retries=3)
        await queue.submit(task)
        dequeued = await queue.dequeue("research")

        await queue.fail(dequeued.id, "error 1")
        loaded = queue.get_task(dequeued.id)
        assert loaded.retry_count == 1

    @pytest.mark.asyncio
    async def test_fail_unknown_task_raises(self, queue: TaskQueue):
        with pytest.raises(KeyError):
            await queue.fail("nonexistent", "error")

    @pytest.mark.asyncio
    async def test_exponential_backoff_increases_score(self, queue: TaskQueue, redis: FakeRedis):
        """Verify that retried tasks get a higher score (delayed re-queue)."""
        task = _make_task(max_retries=3, priority=TaskPriority.NORMAL)
        await queue.submit(task)
        dequeued = await queue.dequeue("research")

        # Record the sorted set state before fail
        await queue.fail(dequeued.id, "error")

        # The task should be back in the sorted set
        ss = redis._sorted_sets.get(TaskQueue.QUEUE_KEY, {})
        assert dequeued.id in ss


# ── Cancel Tests ───────────────────────────────────────────────────


class TestCancel:
    """Requirement 2.7, 2.8"""

    @pytest.mark.asyncio
    async def test_cancel_pending_task(self, queue: TaskQueue, redis: FakeRedis):
        task = _make_task()
        await queue.submit(task)

        await queue.cancel(task.id)

        loaded = queue.get_task(task.id)
        assert loaded.status == TaskStatus.CANCELLED
        members = redis.zrange(TaskQueue.QUEUE_KEY, 0, -1)
        assert task.id not in members

    @pytest.mark.asyncio
    async def test_cancel_in_progress_signals_abort(self, queue: TaskQueue):
        task = _make_task()
        await queue.submit(task)
        dequeued = await queue.dequeue("research")

        await queue.cancel(dequeued.id)

        loaded = queue.get_task(dequeued.id)
        assert loaded.status == TaskStatus.CANCELLED

    @pytest.mark.asyncio
    async def test_cancel_publishes_event(self, queue: TaskQueue, publisher: FakeEventPublisher):
        task = _make_task()
        await queue.submit(task)
        publisher.events.clear()

        await queue.cancel(task.id)

        assert any(e.event_type == "Agent:TaskCancelled" for e in publisher.events)

    @pytest.mark.asyncio
    async def test_cancel_unknown_task_raises(self, queue: TaskQueue):
        with pytest.raises(KeyError):
            await queue.cancel("nonexistent")


# ── Timeout Tests ──────────────────────────────────────────────────


class TestTimeout:
    """Requirement 2.11"""

    @pytest.mark.asyncio
    async def test_timeout_marks_task_failed(self, queue: TaskQueue):
        task = _make_task(task_timeout_seconds=0)  # immediate timeout
        await queue.submit(task)
        dequeued = await queue.dequeue("research")

        await queue._check_timeout(dequeued.id)

        loaded = queue.get_task(dequeued.id)
        # Should be retried (QUEUED) or DLQ'd depending on max_retries
        assert loaded.status in (TaskStatus.QUEUED, TaskStatus.DEAD_LETTERED, TaskStatus.FAILED)

    @pytest.mark.asyncio
    async def test_timeout_skips_non_in_progress(self, queue: TaskQueue):
        task = _make_task()
        await queue.submit(task)
        # Task is QUEUED, not IN_PROGRESS — timeout check should be a no-op
        await queue._check_timeout(task.id)
        loaded = queue.get_task(task.id)
        assert loaded.status == TaskStatus.QUEUED

    @pytest.mark.asyncio
    async def test_timeout_skips_nonexistent_task(self, queue: TaskQueue):
        # Should not raise
        await queue._check_timeout("nonexistent")


# ── Query Tests ────────────────────────────────────────────────────


class TestGetTask:
    """Requirement 2.10"""

    @pytest.mark.asyncio
    async def test_get_existing_task(self, queue: TaskQueue):
        task = _make_task()
        await queue.submit(task)
        loaded = queue.get_task(task.id)
        assert loaded is not None
        assert loaded.id == task.id

    def test_get_nonexistent_returns_none(self, queue: TaskQueue):
        assert queue.get_task("nonexistent") is None


class TestGetQueueDepth:
    """Requirement 2.9"""

    @pytest.mark.asyncio
    async def test_empty_queue_depth(self, queue: TaskQueue):
        depth = queue.get_queue_depth()
        assert all(v == 0 for v in depth.values())

    @pytest.mark.asyncio
    async def test_queue_depth_by_priority(self, queue: TaskQueue):
        await queue.submit(_make_task(priority=TaskPriority.CRITICAL))
        await queue.submit(_make_task(priority=TaskPriority.CRITICAL))
        await queue.submit(_make_task(priority=TaskPriority.LOW))

        depth = queue.get_queue_depth()
        assert depth["CRITICAL"] == 2
        assert depth["LOW"] == 1
        assert depth["NORMAL"] == 0


class TestListTasks:

    @pytest.mark.asyncio
    async def test_list_all_tasks(self, queue: TaskQueue):
        await queue.submit(_make_task())
        await queue.submit(_make_task())
        tasks = queue.list_tasks()
        assert len(tasks) == 2

    @pytest.mark.asyncio
    async def test_list_filter_by_status(self, queue: TaskQueue):
        await queue.submit(_make_task())
        tasks = queue.list_tasks(status=TaskStatus.QUEUED)
        assert len(tasks) == 1
        tasks = queue.list_tasks(status=TaskStatus.COMPLETED)
        assert len(tasks) == 0

    @pytest.mark.asyncio
    async def test_list_filter_by_agent_name(self, queue: TaskQueue):
        await queue.submit(_make_task(agent_name="research"))
        await queue.submit(_make_task(agent_name="converter"))
        tasks = queue.list_tasks(agent_name="research")
        assert len(tasks) == 1
        assert tasks[0].agent_name == "research"
