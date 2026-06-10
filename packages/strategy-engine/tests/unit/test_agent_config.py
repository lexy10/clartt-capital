"""Unit tests for src/agents/config.py — configuration loader."""

import os
from unittest.mock import patch

import pytest

from src.agents.config import load_agent_config, load_framework_config
from src.agents.models import AgentConfig, AgentFrameworkConfig


class TestLoadFrameworkConfig:
    """Tests for load_framework_config()."""

    def test_returns_defaults_when_no_env_vars(self):
        """All defaults should be used when no env vars are set."""
        with patch.dict(os.environ, {}, clear=True):
            cfg = load_framework_config()

        assert isinstance(cfg, AgentFrameworkConfig)
        assert cfg.default_llm_provider == "openai"
        assert cfg.default_llm_model == "gpt-4o"
        assert cfg.default_llm_temperature == 0.7
        assert cfg.default_llm_max_tokens == 4096
        assert cfg.global_daily_budget_usd == 50.0
        assert cfg.global_requests_per_minute == 100
        assert cfg.task_timeout_seconds == 600
        assert cfg.heartbeat_interval_seconds == 30
        assert cfg.memory_retention_days == 90
        assert cfg.sandbox_timeout_seconds == 30
        assert cfg.sandbox_memory_mb == 512
        assert cfg.pipeline_backtest_win_rate_threshold == 0.5
        assert cfg.pipeline_max_drawdown_threshold == 0.2
        assert cfg.pipeline_profit_factor_threshold == 1.0
        assert cfg.approval_timeout_hours == 24.0

    def test_reads_env_vars(self):
        """Env vars should override defaults."""
        env = {
            "DEFAULT_LLM_PROVIDER": "anthropic",
            "DEFAULT_LLM_MODEL": "claude-3",
            "DEFAULT_LLM_TEMPERATURE": "0.3",
            "DEFAULT_LLM_MAX_TOKENS": "2048",
            "GLOBAL_AGENT_DAILY_BUDGET_USD": "100.0",
            "GLOBAL_LLM_REQUESTS_PER_MINUTE": "200",
            "TASK_TIMEOUT_SECONDS": "300",
            "HEARTBEAT_INTERVAL_SECONDS": "15",
            "AGENT_MEMORY_RETENTION_DAYS": "60",
            "SANDBOX_TIMEOUT_SECONDS": "60",
            "SANDBOX_MEMORY_MB": "1024",
            "DEFAULT_PIPELINE_BACKTEST_WIN_RATE_THRESHOLD": "0.6",
            "DEFAULT_PIPELINE_MAX_DRAWDOWN_THRESHOLD": "0.15",
            "DEFAULT_PIPELINE_PROFIT_FACTOR_THRESHOLD": "1.5",
            "APPROVAL_TIMEOUT_HOURS": "48.0",
        }
        with patch.dict(os.environ, env, clear=True):
            cfg = load_framework_config()

        assert cfg.default_llm_provider == "anthropic"
        assert cfg.default_llm_model == "claude-3"
        assert cfg.default_llm_temperature == 0.3
        assert cfg.default_llm_max_tokens == 2048
        assert cfg.global_daily_budget_usd == 100.0
        assert cfg.global_requests_per_minute == 200
        assert cfg.task_timeout_seconds == 300
        assert cfg.heartbeat_interval_seconds == 15
        assert cfg.memory_retention_days == 60
        assert cfg.sandbox_timeout_seconds == 60
        assert cfg.sandbox_memory_mb == 1024
        assert cfg.pipeline_backtest_win_rate_threshold == 0.6
        assert cfg.pipeline_max_drawdown_threshold == 0.15
        assert cfg.pipeline_profit_factor_threshold == 1.5
        assert cfg.approval_timeout_hours == 48.0

    def test_invalid_float_falls_back_to_default(self):
        """Non-numeric value for a float field should fall back to default."""
        env = {"DEFAULT_LLM_TEMPERATURE": "not_a_number"}
        with patch.dict(os.environ, env, clear=True):
            cfg = load_framework_config()

        assert cfg.default_llm_temperature == 0.7

    def test_invalid_int_falls_back_to_default(self):
        """Non-numeric value for an int field should fall back to default."""
        env = {"DEFAULT_LLM_MAX_TOKENS": "abc"}
        with patch.dict(os.environ, env, clear=True):
            cfg = load_framework_config()

        assert cfg.default_llm_max_tokens == 4096

    def test_negative_value_falls_back_to_default(self):
        """Negative value below min_val should fall back to default."""
        env = {"SANDBOX_TIMEOUT_SECONDS": "-5"}
        with patch.dict(os.environ, env, clear=True):
            cfg = load_framework_config()

        assert cfg.sandbox_timeout_seconds == 30

    def test_temperature_above_max_falls_back(self):
        """Temperature above 2.0 should fall back to default."""
        env = {"DEFAULT_LLM_TEMPERATURE": "3.0"}
        with patch.dict(os.environ, env, clear=True):
            cfg = load_framework_config()

        assert cfg.default_llm_temperature == 0.7


class TestLoadAgentConfig:
    """Tests for load_agent_config()."""

    def test_returns_defaults_when_no_env_vars(self):
        """Per-agent config should use model defaults when nothing is set."""
        with patch.dict(os.environ, {}, clear=True):
            cfg = load_agent_config("research")

        assert isinstance(cfg, AgentConfig)
        assert cfg.llm_provider == "openai"
        assert cfg.llm_model == "gpt-4o"
        assert cfg.llm_temperature == 0.7
        assert cfg.llm_max_tokens == 4096
        assert cfg.daily_budget_usd == 10.0
        assert cfg.requests_per_minute == 30

    def test_per_agent_overrides_global(self):
        """Per-agent env vars should take precedence over global defaults."""
        env = {
            "DEFAULT_LLM_PROVIDER": "openai",
            "AGENT_RESEARCH_LLM_PROVIDER": "anthropic",
            "AGENT_RESEARCH_LLM_MODEL": "claude-3-opus",
            "AGENT_RESEARCH_LLM_TEMPERATURE": "0.2",
            "AGENT_RESEARCH_LLM_MAX_TOKENS": "8192",
            "AGENT_RESEARCH_DAILY_BUDGET_USD": "25.0",
            "AGENT_RESEARCH_REQUESTS_PER_MINUTE": "50",
        }
        with patch.dict(os.environ, env, clear=True):
            cfg = load_agent_config("research")

        assert cfg.llm_provider == "anthropic"
        assert cfg.llm_model == "claude-3-opus"
        assert cfg.llm_temperature == 0.2
        assert cfg.llm_max_tokens == 8192
        assert cfg.daily_budget_usd == 25.0
        assert cfg.requests_per_minute == 50

    def test_falls_back_to_global_when_per_agent_missing(self):
        """When per-agent vars are absent, global defaults should be used."""
        env = {
            "DEFAULT_LLM_PROVIDER": "anthropic",
            "DEFAULT_LLM_MODEL": "claude-3-sonnet",
        }
        with patch.dict(os.environ, env, clear=True):
            cfg = load_agent_config("converter")

        assert cfg.llm_provider == "anthropic"
        assert cfg.llm_model == "claude-3-sonnet"

    def test_agent_name_uppercased_in_env_var(self):
        """Agent name should be uppercased for env var lookup."""
        env = {"AGENT_FORWARD_TEST_LLM_PROVIDER": "google"}
        with patch.dict(os.environ, env, clear=True):
            cfg = load_agent_config("forward_test")

        assert cfg.llm_provider == "google"

    def test_invalid_per_agent_value_falls_back_to_global(self):
        """Invalid per-agent value should fall back to the global default."""
        env = {
            "DEFAULT_LLM_TEMPERATURE": "0.5",
            "AGENT_BACKTEST_LLM_TEMPERATURE": "not_valid",
        }
        with patch.dict(os.environ, env, clear=True):
            cfg = load_agent_config("backtest")

        # Falls back to global default 0.5
        assert cfg.llm_temperature == 0.5
