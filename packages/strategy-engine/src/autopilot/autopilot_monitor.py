"""Autopilot Monitor for the Strategy Engine.

Monitors per-account autopilot state via Redis key and pub/sub channel.
When enabled for an account, signals are routed to the execution stream;
when disabled, signals are recorded for analytics only.
Thread-safe with local per-account state caching to minimize Redis calls.
"""

import json
import logging
import threading
from typing import Callable

from redis import Redis

logger = logging.getLogger(__name__)

AUTOPILOT_KEY_PREFIX = "autopilot:"
AUTOPILOT_CHANNEL = "autopilot:channel"
ENABLED_VALUE = "enabled"
DISABLED_VALUE = "disabled"


class AutopilotMonitor:
    """Monitors per-account autopilot state via Redis key + pub/sub.

    - is_enabled(account_id) returns the cached local state for a given account.
      On first call per account, loads state from Redis key `autopilot:{account_id}`.
    - subscribe(callback) registers a callback that fires on state changes via pub/sub.
      Callback receives (account_id: str, enabled: bool).
    - stop() stops the background pub/sub listener thread.
    """

    def __init__(self, redis_client: Redis) -> None:
        self._redis = redis_client
        self._lock = threading.Lock()
        self._states: dict[str, bool] = {}
        self._callbacks: list[Callable[[str, bool], None]] = []
        self._subscriber_thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    def _load_state(self, account_id: str) -> bool:
        """Load the autopilot state for a specific account from Redis."""
        try:
            key = f"{AUTOPILOT_KEY_PREFIX}{account_id}"
            value = self._redis.get(key)
            if value is not None:
                if isinstance(value, bytes):
                    value = value.decode("utf-8")
                enabled = value == ENABLED_VALUE
            else:
                enabled = False
            self._states[account_id] = enabled
            logger.info(
                "Autopilot state loaded from Redis for account %s: %s",
                account_id,
                enabled,
            )
            return enabled
        except Exception:
            logger.exception(
                "Failed to load autopilot state from Redis for account %s",
                account_id,
            )
            self._states[account_id] = False
            return False

    def is_enabled(self, account_id: str) -> bool:
        """Check whether autopilot is enabled for a specific account.

        On first call per account, loads state from Redis. Subsequent calls
        return the cached value, which is updated by the pub/sub subscriber.
        """
        with self._lock:
            if account_id not in self._states:
                return self._load_state(account_id)
            return self._states[account_id]

    def subscribe(self, callback: Callable[[str, bool], None]) -> None:
        """Register a callback for autopilot state changes.

        The callback receives (account_id, enabled) when the autopilot state
        changes for any account. Starts a background listener thread on the
        first subscription if not already running.
        """
        with self._lock:
            self._callbacks.append(callback)
            if self._subscriber_thread is None or not self._subscriber_thread.is_alive():
                self._stop_event.clear()
                self._subscriber_thread = threading.Thread(
                    target=self._listen, daemon=True, name="autopilot-listener"
                )
                self._subscriber_thread.start()

    def _listen(self) -> None:
        """Background thread that listens to Redis pub/sub for state changes."""
        try:
            pubsub = self._redis.pubsub()
            pubsub.subscribe(AUTOPILOT_CHANNEL)
            logger.info("Subscribed to autopilot channel: %s", AUTOPILOT_CHANNEL)

            for message in pubsub.listen():
                if self._stop_event.is_set():
                    break

                if message["type"] != "message":
                    continue

                data = message.get("data")
                if isinstance(data, bytes):
                    data = data.decode("utf-8")

                try:
                    payload = json.loads(data)
                    account_id = payload.get("accountId", "")
                    enabled = payload.get("enabled", False)
                except (json.JSONDecodeError, AttributeError):
                    logger.warning("Invalid autopilot pub/sub message: %s", data)
                    continue

                if not account_id:
                    logger.warning("Autopilot message missing accountId: %s", data)
                    continue

                with self._lock:
                    old_state = self._states.get(account_id)
                    self._states[account_id] = enabled

                    if old_state != enabled:
                        logger.info(
                            "Autopilot state changed for account %s: %s",
                            account_id,
                            enabled,
                        )
                        callbacks = list(self._callbacks)
                    else:
                        callbacks = []

                for cb in callbacks:
                    try:
                        cb(account_id, enabled)
                    except Exception:
                        logger.exception("Error in autopilot callback")

            pubsub.unsubscribe(AUTOPILOT_CHANNEL)
            pubsub.close()
        except Exception:
            logger.exception("Autopilot listener encountered an error")

    def stop(self) -> None:
        """Stop the background pub/sub listener thread."""
        self._stop_event.set()
        if self._subscriber_thread and self._subscriber_thread.is_alive():
            self._subscriber_thread.join(timeout=5.0)
