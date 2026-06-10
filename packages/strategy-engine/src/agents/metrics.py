"""Prometheus metric definitions for the Autonomous Trading Agents framework.

Centralizes all agent-related Prometheus metrics. Some metrics (tool, LLM, stream)
are already defined in their respective modules (tools/registry.py, llm_client.py,
streams.py) — this module re-exports them for convenience and defines the remaining
per-agent and task-queue metrics.

Requirements: 8.1, 8.2
"""

from prometheus_client import Counter, Gauge, Histogram

# ---------------------------------------------------------------------------
# Per-agent metrics (Req 8.1)
# ---------------------------------------------------------------------------

agent_tasks_completed_total = Counter(
    "agent_tasks_completed_total",
    "Total tasks completed by agent",
    labelnames=["agent_name", "status"],
)

agent_tasks_duration_seconds = Histogram(
    "agent_tasks_duration_seconds",
    "Task execution duration in seconds",
    labelnames=["agent_name", "task_type"],
)

agent_state_transitions_total = Counter(
    "agent_state_transitions_total",
    "Total state transitions by agent",
    labelnames=["agent_name", "from_state", "to_state"],
)

agent_errors_total = Counter(
    "agent_errors_total",
    "Total errors by agent",
    labelnames=["agent_name", "error_type"],
)

# ---------------------------------------------------------------------------
# Task queue metrics (Req 8.2)
# ---------------------------------------------------------------------------

agent_queue_depth = Gauge(
    "agent_queue_depth",
    "Current queue depth by priority level",
    labelnames=["priority"],
)

agent_queue_wait_seconds = Histogram(
    "agent_queue_wait_seconds",
    "Time tasks spend waiting in the queue before being dequeued",
)

agent_dlq_size = Gauge(
    "agent_dlq_size",
    "Current size of the dead letter queue",
)

# ---------------------------------------------------------------------------
# Refinement metrics (Req 6.3)
# ---------------------------------------------------------------------------

agent_refinements_total = Counter(
    "agent_refinements_total",
    "Total refinements by algorithm and status",
    labelnames=["algorithm_name", "status"],
)

agent_refinement_duration_seconds = Histogram(
    "agent_refinement_duration_seconds",
    "Duration of refinement tasks in seconds",
    labelnames=["algorithm_name"],
)

agent_refinement_improvement_score = Gauge(
    "agent_refinement_improvement_score",
    "Improvement score from most recent refinement",
    labelnames=["algorithm_name"],
)

# ---------------------------------------------------------------------------
# Config refinement metrics (Req 7.3)
# ---------------------------------------------------------------------------

agent_config_refinements_total = Counter(
    "agent_config_refinements_total",
    "Total config refinements by strategy and status",
    labelnames=["strategy_id", "status"],
)

agent_config_refinement_duration_seconds = Histogram(
    "agent_config_refinement_duration_seconds",
    "Duration of config refinement tasks in seconds",
    labelnames=["strategy_id"],
)
