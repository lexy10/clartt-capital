"""LLM Client for the Autonomous Trading Agents framework.

Unified LLM client abstraction over multiple providers via litellm,
with retry logic, rate limiting, cost tracking, and budget enforcement.

Requirements: 5.1–5.9
"""

import asyncio
import logging
import time
from datetime import datetime, timezone

import litellm
from prometheus_client import Counter, Histogram
from redis import Redis

from src.agents.config import load_agent_config
from src.agents.models import AgentConfig, LLMResponse, LLMUsageRecord
from src.events.event_publisher import EventPublisher
from src.models.trading_event import TradingEvent

logger = logging.getLogger("strategy_engine.agents.llm_client")

# --- Prometheus Metrics (Req 5.7) ---

agent_llm_requests_total = Counter(
    "agent_llm_requests_total",
    "Total LLM API requests by agent",
    labelnames=["agent_name", "provider", "model", "status"],
)

agent_llm_tokens_total = Counter(
    "agent_llm_tokens_total",
    "Total LLM tokens consumed by agent",
    labelnames=["agent_name", "provider", "token_type"],
)

agent_llm_request_duration_seconds = Histogram(
    "agent_llm_request_duration_seconds",
    "LLM request duration in seconds",
    labelnames=["agent_name", "provider"],
)

agent_llm_cost_usd_total = Counter(
    "agent_llm_cost_usd_total",
    "Total estimated LLM cost in USD by agent",
    labelnames=["agent_name", "provider"],
)


class BudgetExceededError(Exception):
    """Raised when per-agent or global daily budget is exceeded."""

    def __init__(self, agent_name: str, budget_type: str, spent: float, limit: float):
        self.agent_name = agent_name
        self.budget_type = budget_type
        self.spent = spent
        self.limit = limit
        super().__init__(
            f"{budget_type} budget exceeded for '{agent_name}': "
            f"${spent:.4f} / ${limit:.2f}"
        )


class RateLimitExceededError(Exception):
    """Raised when rate limit is exceeded and cannot be satisfied."""

    def __init__(self, agent_name: str, bucket_type: str):
        self.agent_name = agent_name
        self.bucket_type = bucket_type
        super().__init__(
            f"Rate limit exceeded for '{agent_name}' ({bucket_type})"
        )


class LLMClient:
    """Unified LLM client with retry, rate limiting, cost tracking, and budget enforcement.

    Constructor:
        redis_client: Redis connection for rate limiting and budget tracking.
        backend_url: Base URL of the backend API for persisting usage records.
        global_daily_budget_usd: Maximum daily spend across all agents (default 50.0).
        global_requests_per_minute: Global token bucket capacity (default 100).
    """

    # Redis key templates
    _BUDGET_AGENT_KEY = "agents:budget:{agent_name}:{date}"
    _BUDGET_GLOBAL_KEY = "agents:budget:global:{date}"
    _RATE_AGENT_KEY = "agents:rate:{agent_name}"
    _RATE_GLOBAL_KEY = "agents:rate:global"
    _BUDGET_TTL = 86400  # 24 hours

    def __init__(
        self,
        redis_client: Redis,
        backend_url: str,
        global_daily_budget_usd: float = 50.0,
        global_requests_per_minute: int = 100,
        event_publisher: EventPublisher | None = None,
    ) -> None:
        self._redis = redis_client
        self._backend_url = backend_url.rstrip("/")
        self._global_daily_budget_usd = global_daily_budget_usd
        self._global_requests_per_minute = global_requests_per_minute
        self._event_publisher = event_publisher

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_agent_config(self, agent_name: str) -> AgentConfig:
        """Load per-agent config from env vars, falling back to global defaults (Req 5.1, 5.9)."""
        return load_agent_config(agent_name)

    async def complete(
        self,
        agent_name: str,
        system_prompt: str,
        messages: list[dict],
        tools: list[dict] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        """Send a completion request to the configured LLM provider for the agent.

        Steps (Req 5.1–5.9):
        1. Check per-agent and global daily budget — reject if exceeded.
        2. Check per-agent rate limit (token bucket) — wait if exceeded.
        3. Check global rate limit — wait if exceeded.
        4. Call litellm.acompletion() with configured provider/model.
        5. Retry on 429/5xx/timeout up to llm_max_retries with exponential backoff.
        6. Track token usage and cost, persist to agent_llm_usage via backend API.
        7. Record Prometheus metrics.
        8. Return structured LLMResponse.
        """
        config = self.get_agent_config(agent_name)

        # 1. Budget enforcement (Req 5.6)
        self._check_budget(agent_name, config)

        # 2–3. Rate limiting (Req 5.4, 9.12)
        await self._acquire_rate_limit(agent_name, config.requests_per_minute)
        await self._acquire_rate_limit("__global__", self._global_requests_per_minute, is_global=True)

        # Resolve parameters
        resolved_temp = temperature if temperature is not None else config.llm_temperature
        resolved_max_tokens = max_tokens if max_tokens is not None else config.llm_max_tokens
        model_str = f"{config.llm_provider}/{config.llm_model}"
        timeout = self._get_timeout(agent_name)
        max_retries = self._get_max_retries(agent_name)

        # Build messages list with system prompt
        full_messages = [{"role": "system", "content": system_prompt}] + messages

        # Build kwargs
        kwargs: dict = {
            "model": model_str,
            "messages": full_messages,
            "temperature": resolved_temp,
            "max_tokens": resolved_max_tokens,
            "timeout": timeout,
        }
        if tools:
            kwargs["tools"] = tools

        # 4–5. Call with retry (Req 5.3, 5.8)
        start_time = time.monotonic()
        response = await self._call_with_retry(
            agent_name, config, kwargs, max_retries
        )
        duration = time.monotonic() - start_time

        # Parse response
        llm_response = self._parse_response(response)

        # 6. Track usage and cost (Req 5.5)
        self._record_usage(agent_name, llm_response, config.llm_provider, config.llm_model)

        # 7. Prometheus metrics (Req 5.7)
        agent_llm_requests_total.labels(
            agent_name=agent_name,
            provider=config.llm_provider,
            model=config.llm_model,
            status="success",
        ).inc()
        agent_llm_request_duration_seconds.labels(
            agent_name=agent_name,
            provider=config.llm_provider,
        ).observe(duration)

        return llm_response

    def get_usage(self, agent_name: str) -> dict:
        """Return cumulative token usage and cost for the agent today."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        budget_key = self._BUDGET_AGENT_KEY.format(agent_name=agent_name, date=today)
        spent_raw = self._redis.get(budget_key)
        spent = float(spent_raw) if spent_raw else 0.0
        config = self.get_agent_config(agent_name)
        return {
            "agent_name": agent_name,
            "date": today,
            "spent_usd": spent,
            "daily_budget_usd": config.daily_budget_usd,
            "remaining_usd": max(0.0, config.daily_budget_usd - spent),
        }

    # ------------------------------------------------------------------
    # Budget enforcement (Req 5.6)
    # ------------------------------------------------------------------

    def _check_budget(self, agent_name: str, config: AgentConfig) -> None:
        """Raise BudgetExceededError if per-agent or global daily budget exceeded."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        # Per-agent budget
        agent_key = self._BUDGET_AGENT_KEY.format(agent_name=agent_name, date=today)
        agent_spent = float(self._redis.get(agent_key) or 0)
        if agent_spent >= config.daily_budget_usd:
            self._publish_budget_exceeded_event(agent_name, "per-agent", agent_spent, config.daily_budget_usd)
            raise BudgetExceededError(agent_name, "per-agent", agent_spent, config.daily_budget_usd)

        # Global budget
        global_key = self._BUDGET_GLOBAL_KEY.format(date=today)
        global_spent = float(self._redis.get(global_key) or 0)
        if global_spent >= self._global_daily_budget_usd:
            self._publish_budget_exceeded_event(agent_name, "global", global_spent, self._global_daily_budget_usd)
            raise BudgetExceededError(agent_name, "global", global_spent, self._global_daily_budget_usd)

    def _increment_budget(self, agent_name: str, cost: float) -> None:
        """Atomically increment per-agent and global daily budget counters in Redis."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        agent_key = self._BUDGET_AGENT_KEY.format(agent_name=agent_name, date=today)
        pipe = self._redis.pipeline()
        pipe.incrbyfloat(agent_key, cost)
        pipe.expire(agent_key, self._BUDGET_TTL)
        global_key = self._BUDGET_GLOBAL_KEY.format(date=today)
        pipe.incrbyfloat(global_key, cost)
        pipe.expire(global_key, self._BUDGET_TTL)
        pipe.execute()

    def _publish_budget_exceeded_event(
        self, agent_name: str, budget_type: str, spent: float, limit: float
    ) -> None:
        """Publish AgentBudgetExceeded event via EventPublisher."""
        if not self._event_publisher:
            return
        try:
            event = TradingEvent(
                event_type="Agent:BudgetExceeded",
                aggregate_id=agent_name,
                sequence_number=0,
                payload={
                    "agent_name": agent_name,
                    "budget_type": budget_type,
                    "spent_usd": spent,
                    "limit_usd": limit,
                    "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                },
                source_service="strategy-engine",
            )
            self._event_publisher.publish(event)
        except Exception as e:
            logger.error("Failed to publish AgentBudgetExceeded event: %s", e)

    # ------------------------------------------------------------------
    # Token bucket rate limiting (Req 5.4, 9.12)
    # ------------------------------------------------------------------

    async def _acquire_rate_limit(
        self, agent_name: str, requests_per_minute: int, *, is_global: bool = False
    ) -> None:
        """Token bucket rate limiter backed by Redis.

        Uses a simple sliding-window counter approach:
        - Key stores the count of requests in the current minute window.
        - If count >= limit, sleep until the window resets.
        """
        if is_global:
            rate_key = self._RATE_GLOBAL_KEY
        else:
            rate_key = self._RATE_AGENT_KEY.format(agent_name=agent_name)

        for _ in range(60):  # max ~60s of waiting
            pipe = self._redis.pipeline()
            pipe.incr(rate_key)
            pipe.ttl(rate_key)
            results = pipe.execute()
            current_count = results[0]
            ttl = results[1]

            # Set expiry on first request in window
            if ttl == -1:
                self._redis.expire(rate_key, 60)

            if current_count <= requests_per_minute:
                return  # Token acquired

            # Over limit — decrement back and wait
            self._redis.decr(rate_key)
            await asyncio.sleep(1.0)

        bucket_type = "global" if is_global else f"per-agent ({agent_name})"
        raise RateLimitExceededError(agent_name, bucket_type)

    # ------------------------------------------------------------------
    # Retry with exponential backoff (Req 5.3)
    # ------------------------------------------------------------------

    async def _call_with_retry(
        self,
        agent_name: str,
        config: AgentConfig,
        kwargs: dict,
        max_retries: int,
    ) -> object:
        """Call litellm.acompletion with retry on 429/5xx/timeout."""
        last_error: Exception | None = None

        for attempt in range(max_retries + 1):
            try:
                response = await litellm.acompletion(**kwargs)
                return response
            except Exception as e:
                last_error = e
                if not self._is_retryable(e) or attempt >= max_retries:
                    # Record failure metric
                    agent_llm_requests_total.labels(
                        agent_name=agent_name,
                        provider=config.llm_provider,
                        model=config.llm_model,
                        status="error",
                    ).inc()
                    raise

                # Exponential backoff: 1s, 2s, 4s, ...
                backoff = (2 ** attempt) * 1.0
                logger.warning(
                    "LLM call attempt %d/%d failed for agent '%s': %s — retrying in %.1fs",
                    attempt + 1,
                    max_retries + 1,
                    agent_name,
                    e,
                    backoff,
                )
                await asyncio.sleep(backoff)

        # Should not reach here, but just in case
        raise last_error  # type: ignore[misc]

    @staticmethod
    def _is_retryable(error: Exception) -> bool:
        """Check if an error is retryable (429, 5xx, timeout)."""
        error_str = str(error).lower()
        # litellm wraps errors — check for common retryable patterns
        if "timeout" in error_str or "timed out" in error_str:
            return True
        if "rate limit" in error_str or "429" in error_str:
            return True
        if "500" in error_str or "502" in error_str or "503" in error_str or "504" in error_str:
            return True
        if "server error" in error_str or "internal error" in error_str:
            return True
        # Check for litellm-specific exception attributes
        if hasattr(error, "status_code"):
            status = getattr(error, "status_code", 0)
            if status == 429 or (500 <= status < 600):
                return True
        return False

    # ------------------------------------------------------------------
    # Response parsing
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_response(response: object) -> LLMResponse:
        """Parse litellm response into our LLMResponse model."""
        choice = response.choices[0]  # type: ignore[attr-defined]
        message = choice.message

        tool_calls_list: list[dict] = []
        if message.tool_calls:
            for tc in message.tool_calls:
                tool_calls_list.append({
                    "id": tc.id,
                    "type": tc.type,
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                })

        usage = getattr(response, "usage", None)  # type: ignore[attr-defined]
        prompt_tokens = getattr(usage, "prompt_tokens", 0) if usage else 0
        completion_tokens = getattr(usage, "completion_tokens", 0) if usage else 0
        total_tokens = getattr(usage, "total_tokens", 0) if usage else 0

        # litellm provides cost estimation
        try:
            cost = litellm.completion_cost(completion_response=response)
        except Exception:
            cost = 0.0

        return LLMResponse(
            content=message.content or "",
            tool_calls=tool_calls_list,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            estimated_cost_usd=cost,
        )

    # ------------------------------------------------------------------
    # Usage tracking and persistence (Req 5.5)
    # ------------------------------------------------------------------

    def _record_usage(
        self, agent_name: str, response: LLMResponse, provider: str, model: str
    ) -> None:
        """Persist usage to agent_llm_usage table and update Prometheus counters + Redis budget."""
        cost = response.estimated_cost_usd

        # Update Redis budget counters
        if cost > 0:
            self._increment_budget(agent_name, cost)

        # Prometheus token metrics
        agent_llm_tokens_total.labels(
            agent_name=agent_name, provider=provider, token_type="prompt"
        ).inc(response.prompt_tokens)
        agent_llm_tokens_total.labels(
            agent_name=agent_name, provider=provider, token_type="completion"
        ).inc(response.completion_tokens)
        agent_llm_cost_usd_total.labels(
            agent_name=agent_name, provider=provider
        ).inc(cost)

        # Persist to backend API (fire-and-forget)
        self._persist_usage_record(agent_name, response, provider, model)

    def _persist_usage_record(
        self, agent_name: str, response: LLMResponse, provider: str, model: str
    ) -> None:
        """POST usage record to backend API. Fire-and-forget — errors are logged."""
        try:
            import requests as http_requests

            record = LLMUsageRecord(
                agent_name=agent_name,
                provider=provider,
                model=model,
                prompt_tokens=response.prompt_tokens,
                completion_tokens=response.completion_tokens,
                estimated_cost_usd=response.estimated_cost_usd,
            )
            http_requests.post(
                f"{self._backend_url}/api/agents/llm-usage",
                json=record.model_dump(),
                timeout=5,
            )
        except Exception as e:
            logger.error("Failed to persist LLM usage record: %s", e)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _get_timeout(agent_name: str) -> int:
        """Get configurable llm_timeout_seconds (default 120) (Req 5.8)."""
        import os

        prefix = f"AGENT_{agent_name.upper()}"
        raw = os.environ.get(f"{prefix}_LLM_TIMEOUT_SECONDS")
        if raw:
            try:
                return int(raw)
            except (ValueError, TypeError):
                pass
        raw = os.environ.get("DEFAULT_LLM_TIMEOUT_SECONDS")
        if raw:
            try:
                return int(raw)
            except (ValueError, TypeError):
                pass
        return 120

    @staticmethod
    def _get_max_retries(agent_name: str) -> int:
        """Get configurable llm_max_retries (default 3) (Req 5.3)."""
        import os

        prefix = f"AGENT_{agent_name.upper()}"
        raw = os.environ.get(f"{prefix}_LLM_MAX_RETRIES")
        if raw:
            try:
                return int(raw)
            except (ValueError, TypeError):
                pass
        raw = os.environ.get("DEFAULT_LLM_MAX_RETRIES")
        if raw:
            try:
                return int(raw)
            except (ValueError, TypeError):
                pass
        return 3
