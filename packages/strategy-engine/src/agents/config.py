"""Configuration loader for the Autonomous Trading Agents framework.

Loads global and per-agent configuration from environment variables,
validates values at startup, and falls back to defaults for missing
or invalid values.

Requirements: 18.1, 18.2, 18.3, 18.4, 18.5, 18.6
"""

import logging
import os
from typing import Any, Callable, TypeVar

from src.agents.models import AgentConfig, AgentFrameworkConfig

logger = logging.getLogger(__name__)

T = TypeVar("T")


def _read_env(
    var_name: str,
    default: T,
    cast: Callable[[str], Any],
    *,
    min_val: Any = None,
    max_val: Any = None,
) -> T:
    """Read an environment variable, cast it, validate range, and fall back to default.

    Logs a warning and returns the default when the value is missing, unparseable,
    or outside the optional [min_val, max_val] range.
    """
    raw = os.environ.get(var_name)
    if raw is None:
        return default

    try:
        value = cast(raw)
    except (ValueError, TypeError):
        logger.warning(
            "Invalid value for %s: %r — falling back to default %r",
            var_name,
            raw,
            default,
        )
        return default

    if min_val is not None and value < min_val:
        logger.warning(
            "%s=%r is below minimum %r — falling back to default %r",
            var_name,
            value,
            min_val,
            default,
        )
        return default

    if max_val is not None and value > max_val:
        logger.warning(
            "%s=%r exceeds maximum %r — falling back to default %r",
            var_name,
            value,
            max_val,
            default,
        )
        return default

    return value


def load_framework_config() -> AgentFrameworkConfig:
    """Load the global ``AgentFrameworkConfig`` from environment variables.

    Every setting has a documented default so the framework can start
    without any env vars being set (Requirement 18.5).  Invalid values
    are logged as warnings and replaced with defaults (Requirement 18.6).
    """
    return AgentFrameworkConfig(
        # LLM defaults (Req 18.1)
        default_llm_provider=_read_env("DEFAULT_LLM_PROVIDER", "openai", str),
        default_llm_model=_read_env("DEFAULT_LLM_MODEL", "gpt-4o", str),
        default_llm_temperature=_read_env(
            "DEFAULT_LLM_TEMPERATURE", 0.7, float, min_val=0.0, max_val=2.0
        ),
        default_llm_max_tokens=_read_env(
            "DEFAULT_LLM_MAX_TOKENS", 4096, int, min_val=1
        ),
        # Budget / rate limits (Req 18.1)
        global_daily_budget_usd=_read_env(
            "GLOBAL_AGENT_DAILY_BUDGET_USD", 50.0, float, min_val=0.0
        ),
        global_requests_per_minute=_read_env(
            "GLOBAL_LLM_REQUESTS_PER_MINUTE", 100, int, min_val=1
        ),
        # Task / heartbeat / memory (Req 18.1)
        task_timeout_seconds=_read_env(
            "TASK_TIMEOUT_SECONDS", 600, int, min_val=1
        ),
        heartbeat_interval_seconds=_read_env(
            "HEARTBEAT_INTERVAL_SECONDS", 30, int, min_val=1
        ),
        memory_retention_days=_read_env(
            "AGENT_MEMORY_RETENTION_DAYS", 90, int, min_val=1
        ),
        # Sandbox (Req 18.3)
        sandbox_timeout_seconds=_read_env(
            "SANDBOX_TIMEOUT_SECONDS", 30, int, min_val=1
        ),
        sandbox_memory_mb=_read_env(
            "SANDBOX_MEMORY_MB", 512, int, min_val=1
        ),
        # Pipeline thresholds (Req 18.4)
        pipeline_backtest_win_rate_threshold=_read_env(
            "DEFAULT_PIPELINE_BACKTEST_WIN_RATE_THRESHOLD",
            0.5,
            float,
            min_val=0.0,
            max_val=1.0,
        ),
        pipeline_max_drawdown_threshold=_read_env(
            "DEFAULT_PIPELINE_MAX_DRAWDOWN_THRESHOLD",
            0.2,
            float,
            min_val=0.0,
            max_val=1.0,
        ),
        pipeline_profit_factor_threshold=_read_env(
            "DEFAULT_PIPELINE_PROFIT_FACTOR_THRESHOLD",
            1.0,
            float,
            min_val=0.0,
        ),
        # Approval timeout (Req 18.1)
        approval_timeout_hours=_read_env(
            "APPROVAL_TIMEOUT_HOURS", 24.0, float, min_val=0.0
        ),
    )


def load_agent_config(agent_name: str) -> AgentConfig:
    """Load per-agent configuration with fallback to global defaults.

    Per-agent env vars follow the pattern ``AGENT_{UPPERCASE_NAME}_{SETTING}``
    (Requirement 18.2).  When a per-agent var is absent the corresponding
    global default is used; when the global default is also absent the
    documented model default applies (Requirement 18.5).
    """
    prefix = f"AGENT_{agent_name.upper()}"

    # Resolve global defaults first so per-agent vars can fall back to them.
    global_provider = _read_env("DEFAULT_LLM_PROVIDER", "openai", str)
    global_model = _read_env("DEFAULT_LLM_MODEL", "gpt-4o", str)
    global_temperature = _read_env(
        "DEFAULT_LLM_TEMPERATURE", 0.7, float, min_val=0.0, max_val=2.0
    )
    global_max_tokens = _read_env("DEFAULT_LLM_MAX_TOKENS", 4096, int, min_val=1)

    return AgentConfig(
        llm_provider=_read_env(f"{prefix}_LLM_PROVIDER", global_provider, str),
        llm_model=_read_env(f"{prefix}_LLM_MODEL", global_model, str),
        llm_temperature=_read_env(
            f"{prefix}_LLM_TEMPERATURE",
            global_temperature,
            float,
            min_val=0.0,
            max_val=2.0,
        ),
        llm_max_tokens=_read_env(
            f"{prefix}_LLM_MAX_TOKENS", global_max_tokens, int, min_val=1
        ),
        daily_budget_usd=_read_env(
            f"{prefix}_DAILY_BUDGET_USD", 10.0, float, min_val=0.0
        ),
        requests_per_minute=_read_env(
            f"{prefix}_REQUESTS_PER_MINUTE", 30, int, min_val=1
        ),
    )
