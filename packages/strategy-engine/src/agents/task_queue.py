"""Task Queue — Redis-backed persistent task queue with priority, retry, dependencies, and DLQ.

Requirements: 2.1–2.11
"""

import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, Optional, Protocol
from uuid import uuid4

from redis import Redis

from src.agents.models import AgentTask, TaskPriority, TaskResult, TaskStatus

logger = logging.getLogger(__name__)


class EventPublisherProtocol(Protocol):
    """Minimal protocol for event publishing — accepts any object with a publish() method."""

    def publish(self, event: Any) -> None: ...


class TaskQueue:
    """Redis-backed task queue with priority, retry, dependencies, and DLQ.

    Redis key patterns:
      - Task hash:       agents:task:{task_id}
      - Queue sorted set: agents:task_queue
      - DLQ list:         agents:dlq

    Priority ordering uses a composite score: priority * 1e12 + submission_timestamp_ns.
    This ensures CRITICAL (0) tasks sort before HIGH (1), etc., with FIFO within the same level.
    """

    TASK_PREFIX = "agents:task"
    QUEUE_KEY = "agents:task_queue"
    DLQ_KEY = "agents:dlq"
    EVENTS_STREAM = "agents:events"

    # Retry backoff: base_seconds * (2 ** retry_count)
    RETRY_BASE_SECONDS = 2

    def __init__(self, redis_client: Redis, event_publisher: EventPublisherProtocol) -> None:
        self._redis = redis_client
        self._event_publisher = event_publisher

    # ── Helpers ─────────────────────────────────────────────────────

    def _task_key(self, task_id: str) -> str:
        """Return the Redis hash key for a task."""
        return f"{self.TASK_PREFIX}:{task_id}"

    def _now_iso(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def _priority_score(self, priority: TaskPriority) -> float:
        """Composite score: priority * 1e12 + current time in nanoseconds.

        Lower score = higher priority. FIFO within the same priority level.
        Requirement 2.1
        """
        return priority.value * 1e12 + time.time_ns()

    def _persist_task(self, task: AgentTask) -> None:
        """Persist task as a Redis hash (JSON-serialized).

        Requirement 2.10
        """
        self._redis.set(self._task_key(task.id), task.model_dump_json())

    def _load_task(self, task_id: str) -> Optional[AgentTask]:
        """Load a task from Redis by ID."""
        raw = self._redis.get(self._task_key(task_id))
        if raw is None:
            return None
        return AgentTask.model_validate_json(raw)

    def _update_status(self, task: AgentTask, new_status: TaskStatus) -> None:
        """Transition task status, record timestamp, and persist.

        Requirement 2.6
        """
        now = self._now_iso()
        task.status = new_status

        if new_status == TaskStatus.IN_PROGRESS and task.started_at is None:
            task.started_at = now
        elif new_status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.DEAD_LETTERED, TaskStatus.CANCELLED):
            task.completed_at = now

        self._persist_task(task)

    def _publish_event(self, event_type: str, payload: dict) -> None:
        """Publish a TradingEvent via EventPublisher and to agents:events stream."""
        from src.models.trading_event import TradingEvent

        event = TradingEvent(
            event_type=f"Agent:{event_type}",
            aggregate_id=payload.get("task_id", ""),
            sequence_number=0,
            correlation_id=payload.get("task_id"),
            payload=payload,
            source_service="strategy-engine",
        )

        try:
            self._event_publisher.publish(event)
        except Exception as e:
            logger.warning("Failed to publish %s event: %s", event_type, e)

        try:
            stream_fields = {"type": event_type, "timestamp": self._now_iso()}
            stream_fields.update({k: str(v) for k, v in payload.items()})
            self._redis.xadd(self.EVENTS_STREAM, stream_fields, maxlen=10000, approximate=True)
        except Exception as e:
            logger.warning("Failed to publish to agents:events stream: %s", e)

    # ── Submit ─────────────────────────────────────────────────────

    async def submit(self, task: AgentTask) -> str:
        """Assign UUID, persist to Redis hash, add to sorted set by priority. Return task_id.

        Requirement 2.1, 2.2, 2.6, 2.10
        """
        # Ensure a unique ID
        if not task.id:
            task.id = str(uuid4())

        task.status = TaskStatus.QUEUED
        task.created_at = self._now_iso()

        # Persist task hash
        self._persist_task(task)

        # Add to sorted set with priority score
        score = self._priority_score(task.priority)
        self._redis.zadd(self.QUEUE_KEY, {task.id: score})

        self._publish_event("TaskQueued", {
            "task_id": task.id,
            "task_type": task.type,
            "agent_name": task.agent_name,
            "priority": task.priority.value,
        })

        logger.info("Submitted task %s (type=%s, agent=%s, priority=%s)",
                     task.id, task.type, task.agent_name, task.priority.name)
        return task.id

    # ── Dequeue ────────────────────────────────────────────────────

    async def dequeue(self, agent_name: str) -> Optional[AgentTask]:
        """Pop highest-priority task for the given agent. Resolves dependencies first.

        Skips tasks whose depends_on aren't all COMPLETED.
        Requirement 2.1, 2.3
        """
        # Get all members sorted by score (lowest = highest priority)
        members = self._redis.zrange(self.QUEUE_KEY, 0, -1)
        if not members:
            return None

        for member in members:
            task_id = member if isinstance(member, str) else member.decode("utf-8")
            task = self._load_task(task_id)
            if task is None:
                # Stale entry — remove from sorted set
                self._redis.zrem(self.QUEUE_KEY, task_id)
                continue

            # Filter by agent name
            if task.agent_name != agent_name:
                continue

            # Skip if not in a dequeueable state
            if task.status not in (TaskStatus.QUEUED, TaskStatus.PENDING):
                continue

            # Resolve dependencies — skip if any dependency is not COMPLETED
            if task.depends_on:
                deps_met = True
                for dep_id in task.depends_on:
                    dep_task = self._load_task(dep_id)
                    if dep_task is None or dep_task.status != TaskStatus.COMPLETED:
                        deps_met = False
                        break
                if not deps_met:
                    continue

            # Found a ready task — remove from sorted set and transition to IN_PROGRESS
            self._redis.zrem(self.QUEUE_KEY, task_id)
            self._update_status(task, TaskStatus.IN_PROGRESS)

            self._publish_event("TaskStarted", {
                "task_id": task.id,
                "task_type": task.type,
                "agent_name": task.agent_name,
            })

            logger.info("Dequeued task %s for agent '%s'", task.id, agent_name)
            return task

        return None

    # ── Complete / Fail / Cancel ─────────────────────────────────────

    async def complete(self, task_id: str, result: TaskResult) -> None:
        """Mark task COMPLETED, store result, notify dependent tasks.

        Requirement 2.6
        """
        task = self._load_task(task_id)
        if task is None:
            raise KeyError(f"Task '{task_id}' not found")

        task.result = result.model_dump()
        self._update_status(task, TaskStatus.COMPLETED)

        self._publish_event("TaskCompleted", {
            "task_id": task.id,
            "task_type": task.type,
            "agent_name": task.agent_name,
            "duration_seconds": result.duration_seconds,
        })

        logger.info("Completed task %s", task_id)

    async def fail(self, task_id: str, error: str) -> None:
        """Mark task FAILED. Retry with exponential backoff if retries remain, else DLQ.

        Requirement 2.4, 2.5, 2.6
        """
        task = self._load_task(task_id)
        if task is None:
            raise KeyError(f"Task '{task_id}' not found")

        task.error_message = error

        if task.retry_count < task.max_retries:
            await self._retry_with_backoff(task)
        else:
            await self._move_to_dlq(task)

    async def cancel(self, task_id: str) -> None:
        """Cancel a PENDING/QUEUED task. Signal abort for IN_PROGRESS tasks.

        Requirement 2.7, 2.8
        """
        task = self._load_task(task_id)
        if task is None:
            raise KeyError(f"Task '{task_id}' not found")

        if task.status in (TaskStatus.PENDING, TaskStatus.QUEUED):
            # Remove from sorted set and mark cancelled
            self._redis.zrem(self.QUEUE_KEY, task_id)
            self._update_status(task, TaskStatus.CANCELLED)

            self._publish_event("TaskCancelled", {
                "task_id": task.id,
                "task_type": task.type,
                "agent_name": task.agent_name,
                "reason": "cancelled_by_operator",
            })
            logger.info("Cancelled task %s (was %s)", task_id, task.status.value)

        elif task.status == TaskStatus.IN_PROGRESS:
            # Signal abort — mark cancelled so the owning agent can check
            self._update_status(task, TaskStatus.CANCELLED)

            self._publish_event("TaskCancelled", {
                "task_id": task.id,
                "task_type": task.type,
                "agent_name": task.agent_name,
                "reason": "abort_in_progress",
            })
            logger.info("Signalled abort for in-progress task %s", task_id)

        else:
            logger.warning("Cannot cancel task %s in status %s", task_id, task.status.value)

    # ── Retry / DLQ ───────────────────────────────────────────────

    async def _retry_with_backoff(self, task: AgentTask) -> None:
        """Retry task with exponential backoff (base 2s, ×2 per retry).

        Requirement 2.4
        """
        task.retry_count += 1
        backoff_seconds = self.RETRY_BASE_SECONDS * (2 ** (task.retry_count - 1))

        self._update_status(task, TaskStatus.FAILED)

        self._publish_event("TaskRetrying", {
            "task_id": task.id,
            "task_type": task.type,
            "agent_name": task.agent_name,
            "retry_count": task.retry_count,
            "max_retries": task.max_retries,
            "backoff_seconds": backoff_seconds,
        })

        logger.info("Retrying task %s in %ds (attempt %d/%d)",
                     task.id, backoff_seconds, task.retry_count, task.max_retries)

        # Re-queue after backoff
        task.status = TaskStatus.QUEUED
        task.completed_at = None
        self._persist_task(task)

        # Use a delayed score: current priority score + backoff offset in nanoseconds
        score = self._priority_score(task.priority) + backoff_seconds * 1e9
        self._redis.zadd(self.QUEUE_KEY, {task.id: score})

    async def _move_to_dlq(self, task: AgentTask) -> None:
        """Move task to dead letter queue. Publish TaskDeadLettered event.

        Requirement 2.5
        """
        self._update_status(task, TaskStatus.DEAD_LETTERED)

        # Push to DLQ list
        self._redis.rpush(self.DLQ_KEY, task.model_dump_json())

        self._publish_event("TaskDeadLettered", {
            "task_id": task.id,
            "task_type": task.type,
            "agent_name": task.agent_name,
            "error": task.error_message or "unknown",
            "retry_count": task.retry_count,
            "max_retries": task.max_retries,
        })

        logger.warning("Task %s moved to DLQ after %d retries: %s",
                        task.id, task.retry_count, task.error_message)

    # ── Timeout Check ──────────────────────────────────────────────

    async def _check_timeout(self, task_id: str) -> None:
        """Check if IN_PROGRESS task exceeded task_timeout_seconds. Mark FAILED if so.

        Requirement 2.11
        """
        task = self._load_task(task_id)
        if task is None:
            return

        if task.status != TaskStatus.IN_PROGRESS:
            return

        if task.started_at is None:
            return

        started = datetime.fromisoformat(task.started_at)
        elapsed = (datetime.now(timezone.utc) - started).total_seconds()

        if elapsed > task.task_timeout_seconds:
            logger.warning("Task %s timed out after %.1fs (limit=%ds)",
                           task_id, elapsed, task.task_timeout_seconds)
            await self.fail(task_id, f"timeout after {elapsed:.1f}s (limit={task.task_timeout_seconds}s)")

    # ── Query Methods ──────────────────────────────────────────────

    def get_task(self, task_id: str) -> Optional[AgentTask]:
        """Fetch task by ID from Redis hash.

        Requirement 2.10
        """
        return self._load_task(task_id)

    def get_queue_depth(self) -> dict[str, int]:
        """Return count of PENDING+QUEUED tasks per priority level.

        Requirement 2.9
        """
        counts: dict[str, int] = {p.name: 0 for p in TaskPriority}

        members = self._redis.zrange(self.QUEUE_KEY, 0, -1)
        for member in members:
            task_id = member if isinstance(member, str) else member.decode("utf-8")
            task = self._load_task(task_id)
            if task is not None and task.status in (TaskStatus.PENDING, TaskStatus.QUEUED):
                counts[task.priority.name] = counts.get(task.priority.name, 0) + 1

        return counts

    def list_tasks(
        self, status: Optional[TaskStatus] = None, agent_name: Optional[str] = None
    ) -> list[AgentTask]:
        """List tasks with optional filters.

        Scans all task keys in Redis. For production at scale, consider maintaining
        a secondary index, but for the agent framework's task volume this is fine.
        """
        tasks: list[AgentTask] = []
        cursor = 0

        while True:
            cursor, keys = self._redis.scan(cursor, match=f"{self.TASK_PREFIX}:*", count=100)
            for key in keys:
                key_str = key if isinstance(key, str) else key.decode("utf-8")
                # Skip if key matches the queue sorted set key pattern
                if key_str == self.QUEUE_KEY:
                    continue
                raw = self._redis.get(key_str)
                if raw is None:
                    continue
                try:
                    task = AgentTask.model_validate_json(raw)
                except Exception:
                    continue

                if status is not None and task.status != status:
                    continue
                if agent_name is not None and task.agent_name != agent_name:
                    continue

                tasks.append(task)

            if cursor == 0:
                break

        # Sort by created_at descending
        tasks.sort(key=lambda t: t.created_at, reverse=True)
        return tasks
