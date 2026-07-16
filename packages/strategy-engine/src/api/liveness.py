"""Process liveness heartbeat.

The engine's main loop calls ``beat()`` once per cycle. ``/livez`` reports the
process as DOWN (HTTP 503) when the loop hasn't beaten within ``MAX_STALE_S`` —
i.e. it's hung and a restart is warranted (autoheal picks that up).

This is deliberately about the engine's OWN progress, not dependency health: an
engine that is *degraded* because a downstream dependency is down but whose loop
still ticks stays "live", so it is not needlessly restart-looped while the real
problem is external. Dependency status is reported separately by ``/health``.
"""

import os
import threading
import time

# Default sits comfortably above the loop's idle cadence + reconnect backoff
# (strategy runner beats ~1/s, max reconnect backoff 10s), so a healthy-but-idle
# engine never trips it; only a genuine stall does.
MAX_STALE_S = float(os.environ.get("LIVENESS_MAX_STALE_S", "90"))

_lock = threading.Lock()
_last_beat = time.monotonic()


def beat() -> None:
    """Record that the main loop just made progress."""
    global _last_beat
    with _lock:
        _last_beat = time.monotonic()


def seconds_since_beat() -> float:
    with _lock:
        return time.monotonic() - _last_beat


def is_live(max_stale_s: float = MAX_STALE_S) -> bool:
    return seconds_since_beat() <= max_stale_s
