"""Pytest configuration — Hypothesis profiles.

The property-based tests in tests/property default to 100 generated examples
each, which dominates CI runtime (and, when a runner is loaded, triggers
Hypothesis deadline health-checks that balloon a normally-fast test into
minutes). CI selects the fast "ci" profile via HYPOTHESIS_PROFILE=ci; local and
nightly runs keep the full 100-example depth.
"""

import os

from hypothesis import HealthCheck, settings

# Fast, deterministic pass for CI: fewer examples and no per-example deadline
# (a slow shared runner shouldn't cause flaky DeadlineExceeded reruns).
settings.register_profile(
    "ci",
    max_examples=20,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
# Full depth for local development / nightly. deadline=None here too: these
# property tests are computational and a busy machine shouldn't fail them with
# DeadlineExceeded (which also triggers slow shrinking).
settings.register_profile("dev", max_examples=100, deadline=None)

settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "dev"))
