"""Process liveness heartbeat.

The engine's position-monitor loop calls ``beat()`` once per cycle. ``/livez``
reports the process as DOWN (HTTP 503) when the loop hasn't beaten within
``MAX_STALE_S`` — i.e. it's hung and a restart is warranted (autoheal picks that
up).

This is deliberately about the engine's OWN progress, not dependency health: an
engine that is *degraded* because the broker is unreachable but whose loop still
ticks stays "live", so it is not needlessly restart-looped while the real problem
is external. Dependency status is reported separately by ``/health``.
"""

import os
import threading
import time

# Position monitor polls every 3s; 90s leaves ample margin so only a genuine
# stall trips it.
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
