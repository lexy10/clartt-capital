"""Backpressure monitor using Redis XINFO GROUPS to throttle stream producers.

Uses hysteresis to avoid rapid toggling:
- Pause when consumer lag >= high_water_mark
- Resume when consumer lag <= low_water_mark
"""

import logging
import os

from redis import Redis

from ..metrics import set_backpressure_active

logger = logging.getLogger(__name__)


class BackpressureMonitor:
    """Monitors consumer lag on a Redis stream and controls publishing."""

    def __init__(
        self,
        redis_client: Redis,
        stream_key: str,
        group_name: str,
        high_water_mark: int | None = None,
        low_water_mark: int | None = None,
    ) -> None:
        self._redis = redis_client
        self._stream_key = stream_key
        self._group_name = group_name

        self._high_water_mark = (
            high_water_mark
            if high_water_mark is not None
            else int(os.environ.get("BACKPRESSURE_HIGH_WATER_MARK", "100"))
        )
        self._low_water_mark = (
            low_water_mark
            if low_water_mark is not None
            else int(os.environ.get("BACKPRESSURE_LOW_WATER_MARK", "20"))
        )

        self._paused: bool = False

    def should_publish(self) -> bool:
        """Check consumer lag and return True if publishing is allowed.

        Uses hysteresis: pauses at >= high_water_mark, resumes at <= low_water_mark.
        Fails open on Redis errors (returns True).
        """
        lag = self.get_consumer_lag()

        if self._paused:
            if lag <= self._low_water_mark:
                self._paused = False
                set_backpressure_active(False)
                logger.info(
                    "Backpressure released on '%s' (lag=%d, low_water=%d)",
                    self._stream_key,
                    lag,
                    self._low_water_mark,
                )
        else:
            if lag >= self._high_water_mark:
                self._paused = True
                set_backpressure_active(True)
                logger.warning(
                    "Backpressure active on '%s' (lag=%d, high_water=%d)",
                    self._stream_key,
                    lag,
                    self._high_water_mark,
                )

        return not self._paused

    def get_consumer_lag(self) -> int:
        """Return current pending message count for the consumer group.

        Uses XINFO GROUPS to read the pel-count. Fails open on errors (returns 0).
        """
        try:
            groups = self._redis.xinfo_groups(self._stream_key)
            for group in groups:
                name = group.get("name", b"")
                if isinstance(name, bytes):
                    name = name.decode("utf-8", errors="replace")
                if name == self._group_name:
                    return int(group.get("pel-count", 0))
            # Consumer group not found — treat as lag 0
            return 0
        except Exception:
            # Fail-open: assume lag 0, allow publishing
            logger.warning(
                "Failed to read consumer lag for '%s:%s', assuming lag=0",
                self._stream_key,
                self._group_name,
                exc_info=True,
            )
            return 0

    @property
    def is_paused(self) -> bool:
        """Whether backpressure is currently active (publishing paused)."""
        return self._paused
