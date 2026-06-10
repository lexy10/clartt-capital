"""Kill Switch Monitor for the Execution Engine.

Monitors the global kill switch state via Redis key and pub/sub channel.
When activated, trading is halted; when deactivated, trading resumes.
Thread-safe with local state caching to minimize Redis calls.
"""

import logging
import threading
from typing import Callable

from redis import Redis

logger = logging.getLogger(__name__)

KILL_SWITCH_KEY = "kill_switch:status"
KILL_SWITCH_CHANNEL = "kill_switch:channel"
ACTIVE_VALUE = "active"
INACTIVE_VALUE = "inactive"


class KillSwitchMonitor:
    """Monitors the global kill switch state via Redis.

    - is_active() returns the cached local state (refreshed from Redis on first call
      or when a pub/sub message arrives).
    - subscribe() registers a callback that fires on state changes via pub/sub.
    """

    def __init__(self, redis_client: Redis) -> None:
        self._redis = redis_client
        self._lock = threading.Lock()
        self._active: bool = False
        self._callbacks: list[Callable[[bool], None]] = []
        self._initialized = False
        self._subscriber_thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    def _load_state(self) -> None:
        """Load the current kill switch state from Redis."""
        try:
            value = self._redis.get(KILL_SWITCH_KEY)
            if value is not None:
                if isinstance(value, bytes):
                    value = value.decode("utf-8")
                self._active = value == ACTIVE_VALUE
            else:
                self._active = False
            self._initialized = True
            logger.info("Kill switch state loaded from Redis: %s", self._active)
        except Exception:
            logger.exception("Failed to load kill switch state from Redis")
            self._active = False
            self._initialized = True

    def is_active(self) -> bool:
        """Check whether the kill switch is currently active.

        On first call, loads state from Redis. Subsequent calls return the
        cached value, which is updated by the pub/sub subscriber.
        """
        with self._lock:
            if not self._initialized:
                self._load_state()
            return self._active

    def subscribe(self, callback: Callable[[bool], None]) -> None:
        """Register a callback for kill switch state changes.

        The callback receives True when the kill switch is activated and
        False when deactivated. Starts a background listener thread on
        the first subscription if not already running.
        """
        with self._lock:
            self._callbacks.append(callback)
            if self._subscriber_thread is None or not self._subscriber_thread.is_alive():
                self._stop_event.clear()
                self._subscriber_thread = threading.Thread(
                    target=self._listen, daemon=True, name="kill-switch-listener"
                )
                self._subscriber_thread.start()

    def _listen(self) -> None:
        """Background thread that listens to Redis pub/sub for state changes."""
        try:
            pubsub = self._redis.pubsub()
            pubsub.subscribe(KILL_SWITCH_CHANNEL)
            logger.info("Subscribed to kill switch channel: %s", KILL_SWITCH_CHANNEL)

            for message in pubsub.listen():
                if self._stop_event.is_set():
                    break

                if message["type"] != "message":
                    continue

                data = message.get("data")
                if isinstance(data, bytes):
                    data = data.decode("utf-8")

                new_state = data == ACTIVE_VALUE

                with self._lock:
                    if new_state != self._active:
                        self._active = new_state
                        self._initialized = True
                        logger.info("Kill switch state changed to: %s", new_state)
                        callbacks = list(self._callbacks)

                for cb in callbacks:
                    try:
                        cb(new_state)
                    except Exception:
                        logger.exception("Error in kill switch callback")

            pubsub.unsubscribe(KILL_SWITCH_CHANNEL)
            pubsub.close()
        except Exception:
            logger.exception("Kill switch listener encountered an error")

    def stop(self) -> None:
        """Stop the background pub/sub listener thread."""
        self._stop_event.set()
        if self._subscriber_thread and self._subscriber_thread.is_alive():
            self._subscriber_thread.join(timeout=5.0)
