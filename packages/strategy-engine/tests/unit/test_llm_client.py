"""Unit tests for LLMClient.

Tests cover: config resolution, budget enforcement, rate limiting,
retry logic, response parsing, usage tracking, and Prometheus metrics.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agents.llm_client import (
    BudgetExceededError,
    LLMClient,
    RateLimitExceededError,
)
from src.agents.models import AgentConfig, LLMResponse


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeRedis:
    """Minimal Redis fake supporting get/set/incr/decr/expire/pipeline."""

    def __init__(self):
        self._data: dict[str, str] = {}
        self._ttls: dict[str, int] = {}

    def get(self, key):
        return self._data.get(key)

    def set(self, key, value, ex=None):
        self._data[key] = str(value)
        if ex:
            self._ttls[key] = ex

    def incr(self, key):
        val = int(self._data.get(key, 0)) + 1
        self._data[key] = str(val)
        return val

    def decr(self, key):
        val = int(self._data.get(key, 0)) - 1
        self._data[key] = str(val)
        return val

    def incrbyfloat(self, key, amount):
        val = float(self._data.get(key, 0)) + amount
        self._data[key] = str(val)
        return val

    def expire(self, key, seconds):
        self._ttls[key] = seconds

    def ttl(self, key):
        return self._ttls.get(key, -1)

    def pipeline(self):
        return FakePipeline(self)


class FakePipeline:
    """Minimal Redis pipeline fake."""

    def __init__(self, redis: FakeRedis):
        self._redis = redis
        self._ops: list = []

    def incr(self, key):
        self._ops.append(("incr", key))
        return self

    def incrbyfloat(self, key, amount):
        self._ops.append(("incrbyfloat", key, amount))
        return self

    def expire(self, key, seconds):
        self._ops.append(("expire", key, seconds))
        return self

    def ttl(self, key):
        self._ops.append(("ttl", key))
        return self

    def execute(self):
        results = []
        for op in self._ops:
            if op[0] == "incr":
                results.append(self._redis.incr(op[1]))
            elif op[0] == "incrbyfloat":
                results.append(self._redis.incrbyfloat(op[1], op[2]))
            elif op[0] == "expire":
                self._redis.expire(op[1], op[2])
                results.append(True)
            elif op[0] == "ttl":
                results.append(self._redis.ttl(op[1]))
        self._ops.clear()
        return results


class FakeEventPublisher:
    def __init__(self):
        self.events = []

    def publish(self, event):
        self.events.append(event)


def _make_litellm_response(
    content="Hello",
    prompt_tokens=10,
    completion_tokens=5,
    total_tokens=15,
    tool_calls=None,
):
    """Build a mock litellm response object."""
    message = MagicMock()
    message.content = content
    message.tool_calls = tool_calls

    choice = MagicMock()
    choice.message = message

    usage = MagicMock()
    usage.prompt_tokens = prompt_tokens
    usage.completion_tokens = completion_tokens
    usage.total_tokens = total_tokens

    response = MagicMock()
    response.choices = [choice]
    response.usage = usage
    return response


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def redis():
    return FakeRedis()


@pytest.fixture
def publisher():
    return FakeEventPublisher()


@pytest.fixture
def client(redis, publisher):
    return LLMClient(
        redis_client=redis,
        backend_url="http://localhost:3000",
        global_daily_budget_usd=50.0,
        global_requests_per_minute=100,
        event_publisher=publisher,
    )


# ---------------------------------------------------------------------------
# Tests: get_agent_config
# ---------------------------------------------------------------------------


class TestGetAgentConfig:
    def test_returns_agent_config(self, client: LLMClient, monkeypatch):
        # litellm's import loads the repo .env, which may set these — clear
        # them so we test the hardcoded defaults, not the local machine.
        monkeypatch.delenv("DEFAULT_LLM_PROVIDER", raising=False)
        monkeypatch.delenv("DEFAULT_LLM_MODEL", raising=False)
        config = client.get_agent_config("research")
        assert isinstance(config, AgentConfig)
        assert config.llm_provider == "openai"
        assert config.llm_model == "gpt-4o"

    def test_per_agent_override(self, client: LLMClient, monkeypatch):
        monkeypatch.setenv("AGENT_RESEARCH_LLM_PROVIDER", "anthropic")
        monkeypatch.setenv("AGENT_RESEARCH_LLM_MODEL", "claude-3-opus")
        config = client.get_agent_config("research")
        assert config.llm_provider == "anthropic"
        assert config.llm_model == "claude-3-opus"


# ---------------------------------------------------------------------------
# Tests: Budget enforcement
# ---------------------------------------------------------------------------


class TestBudgetEnforcement:
    def test_check_budget_passes_when_under_limit(self, client: LLMClient):
        config = AgentConfig(daily_budget_usd=10.0)
        # Should not raise
        client._check_budget("research", config)

    def test_check_budget_raises_when_agent_budget_exceeded(self, client: LLMClient, redis: FakeRedis):
        config = AgentConfig(daily_budget_usd=10.0)
        # Simulate agent having spent $10
        from datetime import datetime, timezone
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        key = f"agents:budget:research:{today}"
        redis._data[key] = "10.0"

        with pytest.raises(BudgetExceededError, match="per-agent"):
            client._check_budget("research", config)

    def test_check_budget_raises_when_global_budget_exceeded(self, client: LLMClient, redis: FakeRedis):
        config = AgentConfig(daily_budget_usd=100.0)  # Agent budget is fine
        from datetime import datetime, timezone
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        key = f"agents:budget:global:{today}"
        redis._data[key] = "50.0"

        with pytest.raises(BudgetExceededError, match="global"):
            client._check_budget("research", config)

    def test_budget_exceeded_publishes_event(self, client: LLMClient, redis: FakeRedis, publisher: FakeEventPublisher):
        config = AgentConfig(daily_budget_usd=5.0)
        from datetime import datetime, timezone
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        key = f"agents:budget:research:{today}"
        redis._data[key] = "5.0"

        with pytest.raises(BudgetExceededError):
            client._check_budget("research", config)

        assert len(publisher.events) == 1
        assert publisher.events[0].event_type == "Agent:BudgetExceeded"
        assert publisher.events[0].payload["agent_name"] == "research"

    def test_increment_budget_updates_redis(self, client: LLMClient, redis: FakeRedis):
        client._increment_budget("research", 1.5)
        from datetime import datetime, timezone
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        agent_key = f"agents:budget:research:{today}"
        global_key = f"agents:budget:global:{today}"
        assert float(redis._data[agent_key]) == pytest.approx(1.5)
        assert float(redis._data[global_key]) == pytest.approx(1.5)


# ---------------------------------------------------------------------------
# Tests: Rate limiting
# ---------------------------------------------------------------------------


class TestRateLimiting:
    @pytest.mark.asyncio
    async def test_acquire_rate_limit_succeeds_under_limit(self, client: LLMClient):
        # Should not raise — first request in window
        await client._acquire_rate_limit("research", 30)

    @pytest.mark.asyncio
    async def test_acquire_rate_limit_global(self, client: LLMClient):
        await client._acquire_rate_limit("__global__", 100, is_global=True)


# ---------------------------------------------------------------------------
# Tests: Retry logic
# ---------------------------------------------------------------------------


class TestRetryLogic:
    def test_is_retryable_429(self):
        err = Exception("Rate limit exceeded (429)")
        assert LLMClient._is_retryable(err) is True

    def test_is_retryable_500(self):
        err = Exception("Internal server error 500")
        assert LLMClient._is_retryable(err) is True

    def test_is_retryable_timeout(self):
        err = Exception("Request timed out")
        assert LLMClient._is_retryable(err) is True

    def test_not_retryable_400(self):
        err = Exception("Bad request 400")
        assert LLMClient._is_retryable(err) is False

    def test_not_retryable_auth(self):
        err = Exception("Authentication failed")
        assert LLMClient._is_retryable(err) is False

    def test_retryable_status_code_attribute(self):
        err = Exception("error")
        err.status_code = 429
        assert LLMClient._is_retryable(err) is True

    def test_retryable_5xx_status_code(self):
        err = Exception("error")
        err.status_code = 503
        assert LLMClient._is_retryable(err) is True

    @pytest.mark.asyncio
    async def test_call_with_retry_succeeds_first_try(self, client: LLMClient):
        mock_response = _make_litellm_response()
        config = AgentConfig()
        with patch("litellm.acompletion", new_callable=AsyncMock, return_value=mock_response):
            result = await client._call_with_retry("research", config, {"model": "openai/gpt-4o", "messages": []}, 3)
        assert result is mock_response

    @pytest.mark.asyncio
    async def test_call_with_retry_retries_on_retryable_error(self, client: LLMClient):
        mock_response = _make_litellm_response()
        config = AgentConfig()
        call_count = 0

        async def side_effect(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise Exception("Rate limit exceeded (429)")
            return mock_response

        with patch("litellm.acompletion", side_effect=side_effect):
            with patch("asyncio.sleep", new_callable=AsyncMock):
                result = await client._call_with_retry("research", config, {"model": "openai/gpt-4o", "messages": []}, 3)
        assert result is mock_response
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_call_with_retry_raises_on_non_retryable(self, client: LLMClient):
        config = AgentConfig()

        async def side_effect(**kwargs):
            raise Exception("Authentication failed")

        with patch("litellm.acompletion", side_effect=side_effect):
            with pytest.raises(Exception, match="Authentication failed"):
                await client._call_with_retry("research", config, {"model": "openai/gpt-4o", "messages": []}, 3)


# ---------------------------------------------------------------------------
# Tests: Response parsing
# ---------------------------------------------------------------------------


class TestResponseParsing:
    def test_parse_basic_response(self):
        mock_resp = _make_litellm_response(content="Test response", prompt_tokens=20, completion_tokens=10)
        with patch("litellm.completion_cost", return_value=0.001):
            result = LLMClient._parse_response(mock_resp)
        assert isinstance(result, LLMResponse)
        assert result.content == "Test response"
        assert result.prompt_tokens == 20
        assert result.completion_tokens == 10
        assert result.total_tokens == 15
        assert result.estimated_cost_usd == 0.001

    def test_parse_response_with_tool_calls(self):
        tc = MagicMock()
        tc.id = "call_123"
        tc.type = "function"
        tc.function.name = "run_backtest"
        tc.function.arguments = '{"strategy_id": "abc"}'

        mock_resp = _make_litellm_response(tool_calls=[tc])
        with patch("litellm.completion_cost", return_value=0.0):
            result = LLMClient._parse_response(mock_resp)
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0]["function"]["name"] == "run_backtest"

    def test_parse_response_no_content(self):
        mock_resp = _make_litellm_response(content=None)
        with patch("litellm.completion_cost", return_value=0.0):
            result = LLMClient._parse_response(mock_resp)
        assert result.content == ""

    def test_parse_response_cost_estimation_failure(self):
        mock_resp = _make_litellm_response()
        with patch("litellm.completion_cost", side_effect=Exception("unknown model")):
            result = LLMClient._parse_response(mock_resp)
        assert result.estimated_cost_usd == 0.0


# ---------------------------------------------------------------------------
# Tests: get_usage
# ---------------------------------------------------------------------------


class TestGetUsage:
    def test_get_usage_no_spend(self, client: LLMClient):
        usage = client.get_usage("research")
        assert usage["agent_name"] == "research"
        assert usage["spent_usd"] == 0.0
        assert usage["daily_budget_usd"] == 10.0
        assert usage["remaining_usd"] == 10.0

    def test_get_usage_with_spend(self, client: LLMClient, redis: FakeRedis):
        from datetime import datetime, timezone
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        redis._data[f"agents:budget:research:{today}"] = "3.5"
        usage = client.get_usage("research")
        assert usage["spent_usd"] == pytest.approx(3.5)
        assert usage["remaining_usd"] == pytest.approx(6.5)


# ---------------------------------------------------------------------------
# Tests: complete (integration of all pieces)
# ---------------------------------------------------------------------------


class TestComplete:
    @pytest.mark.asyncio
    async def test_complete_success(self, client: LLMClient):
        mock_resp = _make_litellm_response(content="Analysis complete")
        with patch("litellm.acompletion", new_callable=AsyncMock, return_value=mock_resp):
            with patch("litellm.completion_cost", return_value=0.002):
                with patch.object(client, "_persist_usage_record"):
                    result = await client.complete(
                        agent_name="research",
                        system_prompt="You are a research agent.",
                        messages=[{"role": "user", "content": "Analyze momentum strategies"}],
                    )
        assert isinstance(result, LLMResponse)
        assert result.content == "Analysis complete"

    @pytest.mark.asyncio
    async def test_complete_rejects_when_budget_exceeded(self, client: LLMClient, redis: FakeRedis):
        from datetime import datetime, timezone
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        redis._data[f"agents:budget:research:{today}"] = "10.0"

        with pytest.raises(BudgetExceededError):
            await client.complete(
                agent_name="research",
                system_prompt="You are a research agent.",
                messages=[{"role": "user", "content": "test"}],
            )

    @pytest.mark.asyncio
    async def test_complete_uses_custom_temperature(self, client: LLMClient):
        mock_resp = _make_litellm_response()
        captured_kwargs = {}

        async def capture_call(**kwargs):
            captured_kwargs.update(kwargs)
            return mock_resp

        with patch("litellm.acompletion", side_effect=capture_call):
            with patch("litellm.completion_cost", return_value=0.0):
                with patch.object(client, "_persist_usage_record"):
                    await client.complete(
                        agent_name="research",
                        system_prompt="sys",
                        messages=[],
                        temperature=0.2,
                        max_tokens=1000,
                    )
        assert captured_kwargs["temperature"] == 0.2
        assert captured_kwargs["max_tokens"] == 1000


# ---------------------------------------------------------------------------
# Tests: Timeout and max retries config
# ---------------------------------------------------------------------------


class TestConfigHelpers:
    def test_default_timeout(self):
        assert LLMClient._get_timeout("research") == 120

    def test_custom_timeout(self, monkeypatch):
        monkeypatch.setenv("AGENT_RESEARCH_LLM_TIMEOUT_SECONDS", "60")
        assert LLMClient._get_timeout("research") == 60

    def test_global_timeout_fallback(self, monkeypatch):
        monkeypatch.setenv("DEFAULT_LLM_TIMEOUT_SECONDS", "90")
        assert LLMClient._get_timeout("converter") == 90

    def test_default_max_retries(self):
        assert LLMClient._get_max_retries("research") == 3

    def test_custom_max_retries(self, monkeypatch):
        monkeypatch.setenv("AGENT_RESEARCH_LLM_MAX_RETRIES", "5")
        assert LLMClient._get_max_retries("research") == 5
