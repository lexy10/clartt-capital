"""Autonomy Mode Manager — configurable operating mode for the agent framework.

Supports two modes:
  - ``approval``: agents pause for human approval on critical actions
  - ``full_autonomy``: agents auto-approve all actions (subject to safety limits)

Persisted to Redis key ``agents:autonomy_mode``.
Default read from ``AGENT_AUTONOMY_MODE`` env var (default "approval").

Requirements: 9.1, 9.2
"""

import logging
import os
from enum import Enum
from typing import Optional

from redis import Redis

logger = logging.getLogger("strategy_engine.agents.autonomy")

AUTONOMY_MODE_KEY = "agents:autonomy_mode"


class AutonomyMode(str, Enum):
    APPROVAL = "approval"
    FULL_AUTONOMY = "full_autonomy"


class AutonomyManager:
    """Manages the autonomy mode setting for the agent framework.

    Constructor:
        redis_client: Redis connection for persistence
    """

    def __init__(self, redis_client: Redis) -> None:
        self._redis = redis_client

        # Initialize from Redis or env var on first access
        stored = self._redis.get(AUTONOMY_MODE_KEY)
        if stored is None:
            default = os.environ.get("AGENT_AUTONOMY_MODE", "approval")
            try:
                mode = AutonomyMode(default)
            except ValueError:
                logger.warning(
                    "Invalid AGENT_AUTONOMY_MODE '%s', falling back to 'approval'",
                    default,
                )
                mode = AutonomyMode.APPROVAL
            self._redis.set(AUTONOMY_MODE_KEY, mode.value)

    def get_mode(self) -> AutonomyMode:
        """Return the current autonomy mode (Req 9.1)."""
        raw = self._redis.get(AUTONOMY_MODE_KEY)
        if raw is None:
            return AutonomyMode.APPROVAL
        decoded = raw if isinstance(raw, str) else raw.decode("utf-8")
        try:
            return AutonomyMode(decoded)
        except ValueError:
            return AutonomyMode.APPROVAL

    def set_mode(self, mode: AutonomyMode) -> None:
        """Set the autonomy mode, persisted to Redis (Req 9.2)."""
        self._redis.set(AUTONOMY_MODE_KEY, mode.value)
        logger.info("Autonomy mode set to '%s'", mode.value)
