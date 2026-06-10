"""Agent Kill Switch — global and per-agent emergency stop controls.

Provides a global kill switch that immediately stops all running agents
and per-agent enable/disable toggles. State is persisted to Redis and
broadcast via pub/sub and event stream.

Requirements: 19.1–19.8
"""

import json
import logging
from datetime import datetime, timezone
from typing import Any, Optional, Protocol

from prometheus_client import Counter, Gauge
from redis import Redis

logger = logging.getLogger("strategy_engine.agents.kill_switch")

KILL_SWITCH_KEY = "agents:kill_switch"
DISABLED_KEY_PREFIX = "agents:disabled"
EVENTS_STREAM = "agents:events"
ACTIVITY_CHANNEL = "agents:activity"

# Prometheus metrics (Req 19.10)
agent_kill_switch_activations_total = Counter(
    "agent_kill_switch_activations_total",
    "Total number of agent kill switch activations",
)
agent_kill_switch_active = Gauge(
    "agent_kill_switch_active",
    "Whether the agent kill switch is currently active (0 or 1)",
)


class EventPublisherProtocol(Protocol):
    def publish(self, event: Any) -> None: ...


class AgentRegistryProtocol(Protocol):
    def list_agents(self) -> list[dict]: ...
    async def stop(self, name: str) -> None: ...


class AgentKillSwitch:
    """Global and per-agent kill switch for the agent framework.

    Constructor:
        redis_client:     Redis connection for state persistence
        event_publisher:  EventPublisher for TradingEvent publishing
        registry:         AgentRegistry for stopping running agents (optional, set later)
    """

    def __init__(
        self,
        redis_client: Redis,
        event_publisher: Optional[EventPublisherProtocol] = None,
        registry: Optional[AgentRegistryProtocol] = None,
    ) -> None:
        self._redis = redis_client
        self._event_publisher = event_publisher
        self._registry = registry

        # Sync gauge with persisted state on init
        if self.is_active():
            agent_kill_switch_active.set(1)
        else:
            agent_kill_switch_active.set(0)

    def set_registry(self, registry: AgentRegistryProtocol) -> None:
        """Set the registry reference (for deferred wiring)."""
        self._registry = registry

    # ── Global Kill Switch ────────────────────────────────────────────

    def is_active(self) -> bool:
        """Return True if the global kill switch is active (Req 19.2)."""
        val = self._redis.get(KILL_SWITCH_KEY)
        if val is None:
            return False
        decoded = val if isinstance(val, str) else val.decode("utf-8")
        return decoded == "active"

    async def activate(self, activated_by: str = "operator") -> None:
        """Activate the global kill switch (Req 19.1).

        - Persists state to Redis
        - Stops all running agents
        - Rejects new task submissions (checked via is_active())
        - Publishes events
        """
        self._redis.set(KILL_SWITCH_KEY, "active")
        agent_kill_switch_active.set(1)
        agent_kill_switch_activations_total.inc()

        # Stop all running agents
        if self._registry is not None:
            agents = self._registry.list_agents()
            for info in agents:
                state = info.get("state", "IDLE")
                if state != "IDLE":
                    try:
                        await self._registry.stop(info["name"])
                    except Exception as exc:
                        logger.warning(
                            "Failed to stop agent '%s' during kill switch activation: %s",
                            info["name"],
                            exc,
                        )

        now = datetime.now(timezone.utc).isoformat()

        # Publish event (Req 19.3)
        self._publish_event(
            event_type="Agent:KillSwitchActivated",
            payload={
                "activated_by": activated_by,
                "timestamp": now,
            },
        )

        # Broadcast to pub/sub (Req 19.3)
        self._broadcast_activity(
            event_type="KillSwitchActivated",
            activated_by=activated_by,
            timestamp=now,
        )

        logger.warning("Agent kill switch ACTIVATED by %s", activated_by)

    async def deactivate(self, deactivated_by: str = "operator") -> None:
        """Deactivate the global kill switch (Req 19.4).

        Allows new task submissions but does NOT auto-resume agents.
        """
        self._redis.set(KILL_SWITCH_KEY, "inactive")
        agent_kill_switch_active.set(0)

        now = datetime.now(timezone.utc).isoformat()

        self._publish_event(
            event_type="Agent:KillSwitchDeactivated",
            payload={
                "deactivated_by": deactivated_by,
                "timestamp": now,
            },
        )

        self._broadcast_activity(
            event_type="KillSwitchDeactivated",
            deactivated_by=deactivated_by,
            timestamp=now,
        )

        logger.info("Agent kill switch DEACTIVATED by %s", deactivated_by)

    # ── Per-Agent Disable/Enable ──────────────────────────────────────

    def is_agent_disabled(self, agent_name: str) -> bool:
        """Return True if the specific agent is disabled (Req 19.6)."""
        key = f"{DISABLED_KEY_PREFIX}:{agent_name}"
        val = self._redis.get(key)
        if val is None:
            return False
        decoded = val if isinstance(val, str) else val.decode("utf-8")
        return decoded == "disabled"

    async def disable_agent(self, agent_name: str) -> None:
        """Disable a specific agent (Req 19.6).

        Stops the agent if running and marks it as disabled.
        """
        key = f"{DISABLED_KEY_PREFIX}:{agent_name}"
        self._redis.set(key, "disabled")

        # Stop the agent if running
        if self._registry is not None:
            try:
                agents = self._registry.list_agents()
                for info in agents:
                    if info["name"] == agent_name and info.get("state", "IDLE") != "IDLE":
                        await self._registry.stop(agent_name)
                        break
            except Exception as exc:
                logger.warning(
                    "Failed to stop agent '%s' during disable: %s",
                    agent_name,
                    exc,
                )

        self._broadcast_activity(
            event_type="AgentDisabled",
            agent_name=agent_name,
        )

        logger.info("Agent '%s' disabled", agent_name)

    async def enable_agent(self, agent_name: str) -> None:
        """Re-enable a specific agent (Req 19.7).

        Removes the disabled flag but does NOT auto-start the agent.
        """
        key = f"{DISABLED_KEY_PREFIX}:{agent_name}"
        self._redis.delete(key)

        self._broadcast_activity(
            event_type="AgentEnabled",
            agent_name=agent_name,
        )

        logger.info("Agent '%s' enabled", agent_name)

    # ── Guard Check ───────────────────────────────────────────────────

    def can_accept_task(self, agent_name: str) -> tuple[bool, str]:
        """Check if a task can be accepted for the given agent.

        Returns (allowed, reason). Checks global kill switch first (Req 19.8),
        then per-agent disabled state.
        """
        if self.is_active():
            return False, "Global agent kill switch is active"
        if self.is_agent_disabled(agent_name):
            return False, f"Agent '{agent_name}' is disabled"
        return True, ""

    # ── Private Helpers ───────────────────────────────────────────────

    def _publish_event(self, event_type: str, payload: dict) -> None:
        if self._event_publisher is None:
            return
        from src.models.trading_event import TradingEvent

        event = TradingEvent(
            event_type=event_type,
            aggregate_id="kill_switch",
            sequence_number=0,
            payload=payload,
            source_service="strategy-engine",
        )
        try:
            self._event_publisher.publish(event)
        except Exception as exc:
            logger.warning("Failed to publish %s event: %s", event_type, exc)

    def _broadcast_activity(self, event_type: str, **fields: Any) -> None:
        try:
            message = {"type": event_type, **{k: str(v) for k, v in fields.items()}}
            self._redis.publish(ACTIVITY_CHANNEL, json.dumps(message))
        except Exception as exc:
            logger.warning("Failed to broadcast %s: %s", event_type, exc)
