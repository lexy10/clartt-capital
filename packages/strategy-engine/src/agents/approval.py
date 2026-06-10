"""Approval Gate Manager — human-in-the-loop approval for critical agent actions.

Manages approval requests stored in Redis hashes, publishes events via
EventPublisher, sends WebSocket notifications, and coordinates agent state
machine transitions for the approval workflow.

Critical actions requiring approval (in 'approval' autonomy mode):
  - Deploy a strategy to LIVE mode
  - Modify risk parameters on an existing strategy
  - Delete or disable a strategy that has open positions

Requirements: 9.1–9.5
"""

import logging
from datetime import datetime, timezone
from typing import Any, Optional, Protocol

from redis import Redis

from src.agents.models import ApprovalRequest, ApprovalStatus, AgentState

logger = logging.getLogger(__name__)

APPROVAL_KEY_PREFIX = "agents:approval"
APPROVAL_INDEX_KEY = "agents:approvals:pending"
EVENTS_STREAM = "agents:events"
ACTIVITY_CHANNEL = "agents:activity"

# Actions that always require approval in 'approval' mode
CRITICAL_ACTIONS = frozenset(
    {
        "deploy_live",
        "modify_risk_params",
        "delete_strategy_with_positions",
    }
)


class EventPublisherProtocol(Protocol):
    """Minimal protocol — any object with a publish() method."""

    def publish(self, event: Any) -> None: ...


class StateMachineProtocol(Protocol):
    """Minimal protocol for agent state machine transitions."""

    def transition(self, target: AgentState, reason: Optional[str] = None) -> None: ...

    @property
    def current_state(self) -> AgentState: ...


class ApprovalGateManager:
    """Manages human-in-the-loop approval gates for critical agent actions.

    Pending approvals are stored as Redis hashes at ``agents:approval:{id}``
    and tracked in a Redis set ``agents:approvals:pending`` for fast listing.

    In ``full_autonomy`` mode, approvals are auto-granted immediately
    (Req 9.3, 9.4, 9.15). Hard safety limits (budget, rate limits,
    equity pause, kill switch) are always enforced regardless of mode.

    Constructor:
        redis_client:     Redis connection
        event_publisher:  EventPublisher (or compatible) for TradingEvent publishing
        backend_url:      Backend REST API base URL (for future WebSocket relay)
        timeout_hours:    Hours before a pending approval auto-expires (default 24)
        autonomy_manager: Optional AutonomyManager for checking autonomy mode
    """

    def __init__(
        self,
        redis_client: Redis,
        event_publisher: EventPublisherProtocol,
        backend_url: str,
        timeout_hours: float = 24.0,
        autonomy_manager: Any = None,
    ) -> None:
        self._redis = redis_client
        self._event_publisher = event_publisher
        self._backend_url = backend_url.rstrip("/")
        self._timeout_hours = timeout_hours
        self._autonomy_manager = autonomy_manager

    def set_autonomy_manager(self, manager: Any) -> None:
        """Set the autonomy manager reference (for deferred wiring)."""
        self._autonomy_manager = manager

    # ── Request Approval ──────────────────────────────────────────────

    async def request_approval(
        self,
        agent_name: str,
        task_id: str,
        action_description: str,
        state_machine: Optional[StateMachineProtocol] = None,
    ) -> str:
        """Create an approval request, publish event, notify via WebSocket.

        In ``full_autonomy`` mode (Req 9.4), the request is auto-approved
        immediately — the agent continues without pausing. An
        ``ApprovalAutoGranted`` event is published for audit.

        If a *state_machine* is provided and mode is ``approval``, the agent
        is transitioned to WAITING_FOR_INPUT so it pauses until the operator
        responds.

        Returns the approval_id (UUID).
        """
        # Check autonomy mode (Req 9.3, 9.4)
        if self._autonomy_manager is not None:
            from src.agents.autonomy import AutonomyMode

            mode = self._autonomy_manager.get_mode()
            if mode == AutonomyMode.FULL_AUTONOMY:
                return await self._auto_approve(
                    agent_name, task_id, action_description, state_machine
                )

        request = ApprovalRequest(
            agent_name=agent_name,
            task_id=task_id,
            action_description=action_description,
            status=ApprovalStatus.PENDING,
        )

        # Persist to Redis hash
        key = f"{APPROVAL_KEY_PREFIX}:{request.id}"
        self._redis.hset(key, mapping=request.model_dump(mode="json"))

        # Add to pending index set
        self._redis.sadd(APPROVAL_INDEX_KEY, request.id)

        # Transition agent to WAITING_FOR_INPUT
        if state_machine is not None:
            try:
                state_machine.transition(
                    AgentState.WAITING_FOR_INPUT,
                    reason=f"approval requested: {action_description}",
                )
            except Exception as exc:
                logger.warning(
                    "Could not transition agent '%s' to WAITING_FOR_INPUT: %s",
                    agent_name,
                    exc,
                )

        # Publish ApprovalRequested event to events:stream
        self._publish_event(
            event_type="Agent:ApprovalRequested",
            aggregate_id=agent_name,
            correlation_id=task_id,
            payload={
                "approval_id": request.id,
                "agent_name": agent_name,
                "task_id": task_id,
                "action_description": action_description,
                "status": ApprovalStatus.PENDING.value,
                "created_at": request.created_at,
            },
        )

        # Publish to agents:events stream for WebSocket relay
        self._publish_to_agents_stream(
            event_type="ApprovalRequested",
            approval_id=request.id,
            agent_name=agent_name,
            task_id=task_id,
            action_description=action_description,
        )

        # Publish to agents:activity pub/sub for real-time dashboard
        self._publish_activity(
            event_type="ApprovalRequested",
            approval_id=request.id,
            agent_name=agent_name,
            action_description=action_description,
        )

        logger.info(
            "Approval requested: id=%s agent=%s action='%s'",
            request.id,
            agent_name,
            action_description,
        )
        return request.id

    # ── Auto-Approve (full_autonomy mode) ────────────────────────────

    async def _auto_approve(
        self,
        agent_name: str,
        task_id: str,
        action_description: str,
        state_machine: Optional[StateMachineProtocol] = None,
    ) -> str:
        """Auto-approve in full_autonomy mode (Req 9.4).

        Creates the approval record as APPROVED immediately, publishes
        an ``ApprovalAutoGranted`` event, and lets the agent continue.
        """
        request = ApprovalRequest(
            agent_name=agent_name,
            task_id=task_id,
            action_description=action_description,
            status=ApprovalStatus.APPROVED,
            resolved_by="auto:full_autonomy",
            resolved_at=datetime.now(timezone.utc).isoformat(),
        )

        # Persist for audit trail
        key = f"{APPROVAL_KEY_PREFIX}:{request.id}"
        self._redis.hset(key, mapping=request.model_dump(mode="json"))

        # Publish ApprovalAutoGranted event
        self._publish_event(
            event_type="Agent:ApprovalAutoGranted",
            aggregate_id=agent_name,
            correlation_id=task_id,
            payload={
                "approval_id": request.id,
                "agent_name": agent_name,
                "task_id": task_id,
                "action_description": action_description,
                "mode": "full_autonomy",
            },
        )

        self._publish_activity(
            event_type="ApprovalAutoGranted",
            approval_id=request.id,
            agent_name=agent_name,
            action_description=action_description,
        )

        logger.info(
            "Auto-approved (full_autonomy): id=%s agent=%s action='%s'",
            request.id,
            agent_name,
            action_description,
        )
        return request.id

    # ── Approve ───────────────────────────────────────────────────────

    async def approve(
        self,
        approval_id: str,
        resolved_by: str,
        state_machine: Optional[StateMachineProtocol] = None,
    ) -> None:
        """Approve a pending request. Transitions agent to EXECUTING."""
        request = self._load_request(approval_id)
        if request is None:
            raise KeyError(f"Approval '{approval_id}' not found")
        if request.status != ApprovalStatus.PENDING:
            raise ValueError(
                f"Approval '{approval_id}' is not pending (status={request.status.value})"
            )

        now = datetime.now(timezone.utc).isoformat()
        request.status = ApprovalStatus.APPROVED
        request.resolved_by = resolved_by
        request.resolved_at = now

        self._save_request(request)
        self._redis.srem(APPROVAL_INDEX_KEY, approval_id)

        # Transition agent to EXECUTING
        if state_machine is not None:
            try:
                state_machine.transition(
                    AgentState.EXECUTING,
                    reason=f"approval granted by {resolved_by}",
                )
            except Exception as exc:
                logger.warning(
                    "Could not transition agent '%s' to EXECUTING: %s",
                    request.agent_name,
                    exc,
                )

        self._publish_event(
            event_type="Agent:ApprovalResolved",
            aggregate_id=request.agent_name,
            correlation_id=request.task_id,
            payload={
                "approval_id": approval_id,
                "agent_name": request.agent_name,
                "task_id": request.task_id,
                "action_description": request.action_description,
                "status": ApprovalStatus.APPROVED.value,
                "resolved_by": resolved_by,
                "resolved_at": now,
            },
        )

        self._publish_to_agents_stream(
            event_type="ApprovalResolved",
            approval_id=approval_id,
            agent_name=request.agent_name,
            task_id=request.task_id,
            status=ApprovalStatus.APPROVED.value,
            resolved_by=resolved_by,
        )

        self._publish_activity(
            event_type="ApprovalApproved",
            approval_id=approval_id,
            agent_name=request.agent_name,
            resolved_by=resolved_by,
        )

        logger.info("Approval %s approved by %s", approval_id, resolved_by)

    # ── Deny ──────────────────────────────────────────────────────────

    async def deny(
        self,
        approval_id: str,
        resolved_by: str,
        reason: str,
        state_machine: Optional[StateMachineProtocol] = None,
    ) -> None:
        """Deny a pending request. Transitions agent to REVIEWING with denial reason."""
        request = self._load_request(approval_id)
        if request is None:
            raise KeyError(f"Approval '{approval_id}' not found")
        if request.status != ApprovalStatus.PENDING:
            raise ValueError(
                f"Approval '{approval_id}' is not pending (status={request.status.value})"
            )

        now = datetime.now(timezone.utc).isoformat()
        request.status = ApprovalStatus.DENIED
        request.resolved_by = resolved_by
        request.resolution_reason = reason
        request.resolved_at = now

        self._save_request(request)
        self._redis.srem(APPROVAL_INDEX_KEY, approval_id)

        # Transition agent to REVIEWING so it can decide on an alternative
        if state_machine is not None:
            try:
                state_machine.transition(
                    AgentState.REVIEWING,
                    reason=f"approval denied by {resolved_by}: {reason}",
                )
            except Exception as exc:
                logger.warning(
                    "Could not transition agent '%s' to REVIEWING: %s",
                    request.agent_name,
                    exc,
                )

        self._publish_event(
            event_type="Agent:ApprovalResolved",
            aggregate_id=request.agent_name,
            correlation_id=request.task_id,
            payload={
                "approval_id": approval_id,
                "agent_name": request.agent_name,
                "task_id": request.task_id,
                "action_description": request.action_description,
                "status": ApprovalStatus.DENIED.value,
                "resolved_by": resolved_by,
                "resolution_reason": reason,
                "resolved_at": now,
            },
        )

        self._publish_to_agents_stream(
            event_type="ApprovalResolved",
            approval_id=approval_id,
            agent_name=request.agent_name,
            task_id=request.task_id,
            status=ApprovalStatus.DENIED.value,
            resolved_by=resolved_by,
            resolution_reason=reason,
        )

        self._publish_activity(
            event_type="ApprovalDenied",
            approval_id=approval_id,
            agent_name=request.agent_name,
            resolved_by=resolved_by,
            reason=reason,
        )

        logger.info("Approval %s denied by %s: %s", approval_id, resolved_by, reason)

    # ── Check Expiry ──────────────────────────────────────────────────

    async def check_expiry(
        self,
        registry: Any = None,
    ) -> list[str]:
        """Expire approvals older than ``timeout_hours``.

        For each expired approval the agent is transitioned to IDLE (via the
        registry's state machine if *registry* is provided).

        Returns a list of expired approval IDs.
        """
        expired_ids: list[str] = []
        now = datetime.now(timezone.utc)
        pending_ids = self._redis.smembers(APPROVAL_INDEX_KEY)

        for raw_id in pending_ids:
            approval_id = raw_id if isinstance(raw_id, str) else raw_id.decode()
            request = self._load_request(approval_id)
            if request is None or request.status != ApprovalStatus.PENDING:
                self._redis.srem(APPROVAL_INDEX_KEY, approval_id)
                continue

            created = datetime.fromisoformat(request.created_at)
            # Ensure timezone-aware comparison
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            elapsed_hours = (now - created).total_seconds() / 3600.0

            if elapsed_hours >= self._timeout_hours:
                request.status = ApprovalStatus.EXPIRED
                request.resolved_at = now.isoformat()
                request.resolution_reason = (
                    f"Expired after {self._timeout_hours}h with no response"
                )
                self._save_request(request)
                self._redis.srem(APPROVAL_INDEX_KEY, approval_id)

                # Transition agent to IDLE
                if registry is not None:
                    try:
                        sm = registry.get_state_machine(request.agent_name)
                        sm.transition(
                            AgentState.IDLE,
                            reason=f"approval {approval_id} expired",
                        )
                    except Exception as exc:
                        logger.warning(
                            "Could not transition agent '%s' to IDLE on expiry: %s",
                            request.agent_name,
                            exc,
                        )

                self._publish_event(
                    event_type="Agent:ApprovalExpired",
                    aggregate_id=request.agent_name,
                    correlation_id=request.task_id,
                    payload={
                        "approval_id": approval_id,
                        "agent_name": request.agent_name,
                        "task_id": request.task_id,
                        "action_description": request.action_description,
                        "status": ApprovalStatus.EXPIRED.value,
                        "timeout_hours": self._timeout_hours,
                    },
                )

                self._publish_activity(
                    event_type="ApprovalExpired",
                    approval_id=approval_id,
                    agent_name=request.agent_name,
                )

                expired_ids.append(approval_id)
                logger.info("Approval %s expired for agent '%s'", approval_id, request.agent_name)

        return expired_ids

    # ── List Pending ──────────────────────────────────────────────────

    def list_pending(self) -> list[dict]:
        """Return all pending approval requests as dicts."""
        results: list[dict] = []
        pending_ids = self._redis.smembers(APPROVAL_INDEX_KEY)

        for raw_id in pending_ids:
            approval_id = raw_id if isinstance(raw_id, str) else raw_id.decode()
            request = self._load_request(approval_id)
            if request is not None and request.status == ApprovalStatus.PENDING:
                results.append(request.model_dump(mode="json"))

        return results

    # ── Private Helpers ───────────────────────────────────────────────

    def _load_request(self, approval_id: str) -> Optional[ApprovalRequest]:
        """Load an ApprovalRequest from its Redis hash."""
        key = f"{APPROVAL_KEY_PREFIX}:{approval_id}"
        data = self._redis.hgetall(key)
        if not data:
            return None
        # Redis may return bytes keys/values
        decoded = {
            (k.decode() if isinstance(k, bytes) else k): (
                v.decode() if isinstance(v, bytes) else v
            )
            for k, v in data.items()
        }
        return ApprovalRequest.model_validate(decoded)

    def _save_request(self, request: ApprovalRequest) -> None:
        """Persist an ApprovalRequest back to its Redis hash."""
        key = f"{APPROVAL_KEY_PREFIX}:{request.id}"
        mapping = request.model_dump(mode="json")
        # Convert None values to empty strings for Redis hash compatibility
        clean = {k: ("" if v is None else str(v)) for k, v in mapping.items()}
        self._redis.hset(key, mapping=clean)

    def _publish_event(
        self,
        event_type: str,
        aggregate_id: str,
        correlation_id: Optional[str],
        payload: dict,
    ) -> None:
        """Publish a TradingEvent to events:stream via EventPublisher."""
        from src.models.trading_event import TradingEvent

        event = TradingEvent(
            event_type=event_type,
            aggregate_id=aggregate_id,
            sequence_number=0,
            correlation_id=correlation_id,
            payload=payload,
            source_service="strategy-engine",
        )
        try:
            self._event_publisher.publish(event)
        except Exception as exc:
            logger.warning("Failed to publish %s event: %s", event_type, exc)

    def _publish_to_agents_stream(self, event_type: str, **fields: Any) -> None:
        """Publish to the agents:events Redis stream for WebSocket relay."""
        try:
            data = {"type": event_type}
            for k, v in fields.items():
                data[k] = str(v) if v is not None else ""
            self._redis.xadd(
                EVENTS_STREAM,
                data,
                maxlen=10000,
                approximate=True,
            )
        except Exception as exc:
            logger.warning("Failed to publish to agents:events: %s", exc)

    def _publish_activity(self, event_type: str, **fields: Any) -> None:
        """Publish to agents:activity pub/sub channel for real-time dashboard."""
        import json

        try:
            message = {"type": event_type, **fields}
            self._redis.publish(ACTIVITY_CHANNEL, json.dumps(message))
        except Exception as exc:
            logger.warning("Failed to publish to agents:activity: %s", exc)
