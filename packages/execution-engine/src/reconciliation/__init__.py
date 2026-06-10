"""Reconciliation — backstop for missed broker close events.

When a position closes at the broker (SL/TP, manual close in the broker UI,
expiry) but our position monitor wasn't running or missed the event, the
`trades` row stays open (`closed_at IS NULL`) in our DB. The reconciler
sweeps these and pulls authoritative close data from the broker.

This is the safety net behind PositionMonitor's per-tick reconciliation —
it covers process restarts, network blips, and historical backfills.
"""

from src.reconciliation.router import router as reconciliation_router  # noqa: F401
