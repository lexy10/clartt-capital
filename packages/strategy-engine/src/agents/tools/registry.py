"""Tool Registry — typed tool system with JSON schema validation, timeout, retry, and metrics.

Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 4.7, 4.8, 4.9
"""

import asyncio
import logging
import time
from typing import Any, Callable, Protocol

import jsonschema
from prometheus_client import Counter, Histogram
from pydantic import BaseModel

logger = logging.getLogger(__name__)


# --- Pydantic model ---


class ToolDefinition(BaseModel):
    """Schema for a registered tool."""

    name: str
    description: str
    input_schema: dict
    output_schema: dict
    timeout_seconds: int = 30
    retryable: bool = True
    max_retries: int = 2


# --- Exceptions ---


class ToolInputValidationError(Exception):
    """Raised when tool input fails JSON schema validation."""

    def __init__(self, tool_name: str, message: str):
        self.tool_name = tool_name
        super().__init__(f"Tool '{tool_name}' input validation failed: {message}")


class ToolOutputValidationError(Exception):
    """Raised when tool output fails JSON schema validation."""

    def __init__(self, tool_name: str, message: str):
        self.tool_name = tool_name
        super().__init__(f"Tool '{tool_name}' output validation failed: {message}")


class ToolTimeoutError(Exception):
    """Raised when tool execution exceeds its configured timeout."""

    def __init__(self, tool_name: str, elapsed: float):
        self.tool_name = tool_name
        self.elapsed = elapsed
        super().__init__(
            f"Tool '{tool_name}' timed out after {elapsed:.1f}s"
        )


# --- Retryable error helpers ---

# Errors considered retryable (network, 5xx).
_RETRYABLE_EXCEPTIONS: tuple[type[BaseException], ...] = (
    ConnectionError,
    TimeoutError,
    OSError,
)


def _is_retryable(exc: BaseException) -> bool:
    """Return True if the error is retryable (network / 5xx)."""
    if isinstance(exc, _RETRYABLE_EXCEPTIONS):
        return True
    # requests.exceptions.ConnectionError / Timeout inherit from these,
    # but also check for HTTP 5xx via a `response` attribute.
    response = getattr(exc, "response", None)
    if response is not None:
        status = getattr(response, "status_code", None)
        if status is not None and 500 <= status < 600:
            return True
    return False


# --- Event publisher protocol ---


class EventPublisherProtocol(Protocol):
    """Minimal protocol for event publishing."""

    def publish(self, event: Any) -> None: ...


# --- Prometheus metrics ---

agent_tool_invocations_total = Counter(
    "agent_tool_invocations_total",
    "Total tool invocations by agents",
    labelnames=["tool_name", "agent_name", "status"],
)

agent_tool_duration_seconds = Histogram(
    "agent_tool_duration_seconds",
    "Duration of tool invocations in seconds",
    labelnames=["tool_name", "agent_name"],
)


# --- ToolRegistry ---


class ToolRegistry:
    """Registry of typed tools with validation, timeout, retry, and metrics.

    Supports dynamic registration/deregistration at runtime (Req 4.9).
    """

    def __init__(self, event_publisher: EventPublisherProtocol | None = None) -> None:
        self._tools: dict[str, ToolDefinition] = {}
        self._executors: dict[str, Callable] = {}
        self._event_publisher = event_publisher

    # --- Registration (Req 4.1, 4.9) ---

    def register(self, tool: ToolDefinition, execute_fn: Callable) -> None:
        """Register a tool with its execution function."""
        self._tools[tool.name] = tool
        self._executors[tool.name] = execute_fn
        logger.info("Registered tool '%s'", tool.name)

    def deregister(self, name: str) -> None:
        """Remove a tool at runtime."""
        self._tools.pop(name, None)
        self._executors.pop(name, None)
        logger.info("Deregistered tool '%s'", name)

    # --- Query ---

    def list_tools(self) -> list[ToolDefinition]:
        """Return all registered tool definitions."""
        return list(self._tools.values())

    def get_tool_schemas(self, tool_names: list[str]) -> list[dict]:
        """Return JSON schema definitions for specified tools (for LLM tool_use)."""
        schemas: list[dict] = []
        for name in tool_names:
            tool = self._tools.get(name)
            if tool is None:
                continue
            schemas.append(
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.input_schema,
                    },
                }
            )
        return schemas

    # --- Invocation (Req 4.2, 4.3, 4.4, 4.6, 4.7, 4.8) ---

    async def invoke(
        self, tool_name: str, agent_name: str, input_data: dict
    ) -> dict:
        """Validate input → execute with timeout → validate output → return result.

        Retries retryable errors up to max_retries with 1s delay.
        Records Prometheus metrics for every invocation.
        """
        tool = self._tools.get(tool_name)
        if tool is None:
            raise KeyError(f"Tool '{tool_name}' not registered")

        execute_fn = self._executors[tool_name]

        # --- Validate input (Req 4.2) ---
        try:
            jsonschema.validate(instance=input_data, schema=tool.input_schema)
        except jsonschema.ValidationError as exc:
            agent_tool_invocations_total.labels(
                tool_name=tool_name, agent_name=agent_name, status="input_error"
            ).inc()
            raise ToolInputValidationError(tool_name, str(exc.message)) from exc

        # --- Execute with retry + timeout ---
        max_attempts = (tool.max_retries + 1) if tool.retryable else 1
        last_exc: BaseException | None = None

        for attempt in range(max_attempts):
            start = time.time()
            try:
                result = await self._execute_with_timeout(
                    execute_fn, input_data, tool.timeout_seconds, tool_name
                )
                elapsed = time.time() - start

                # --- Validate output (Req 4.3) ---
                try:
                    jsonschema.validate(
                        instance=result, schema=tool.output_schema
                    )
                except jsonschema.ValidationError as exc:
                    agent_tool_invocations_total.labels(
                        tool_name=tool_name,
                        agent_name=agent_name,
                        status="output_error",
                    ).inc()
                    agent_tool_duration_seconds.labels(
                        tool_name=tool_name, agent_name=agent_name
                    ).observe(elapsed)
                    raise ToolOutputValidationError(
                        tool_name, str(exc.message)
                    ) from exc

                # Success
                agent_tool_invocations_total.labels(
                    tool_name=tool_name, agent_name=agent_name, status="success"
                ).inc()
                agent_tool_duration_seconds.labels(
                    tool_name=tool_name, agent_name=agent_name
                ).observe(elapsed)
                return result

            except ToolTimeoutError:
                elapsed = time.time() - start
                agent_tool_invocations_total.labels(
                    tool_name=tool_name, agent_name=agent_name, status="timeout"
                ).inc()
                agent_tool_duration_seconds.labels(
                    tool_name=tool_name, agent_name=agent_name
                ).observe(elapsed)
                raise

            except (ToolInputValidationError, ToolOutputValidationError):
                raise

            except Exception as exc:
                elapsed = time.time() - start
                last_exc = exc

                if not _is_retryable(exc) or not tool.retryable:
                    # Non-retryable → return immediately (Req 4.7)
                    agent_tool_invocations_total.labels(
                        tool_name=tool_name,
                        agent_name=agent_name,
                        status="error",
                    ).inc()
                    agent_tool_duration_seconds.labels(
                        tool_name=tool_name, agent_name=agent_name
                    ).observe(elapsed)
                    raise

                # Retryable — log and retry after 1s (Req 4.6)
                if attempt < max_attempts - 1:
                    logger.warning(
                        "Tool '%s' attempt %d failed (retryable): %s",
                        tool_name,
                        attempt + 1,
                        exc,
                    )
                    await asyncio.sleep(1)

        # All retries exhausted
        agent_tool_invocations_total.labels(
            tool_name=tool_name, agent_name=agent_name, status="error"
        ).inc()
        raise last_exc  # type: ignore[misc]

    # --- Internal helpers ---

    @staticmethod
    async def _execute_with_timeout(
        execute_fn: Callable,
        input_data: dict,
        timeout_seconds: int,
        tool_name: str,
    ) -> dict:
        """Run execute_fn with asyncio timeout (Req 4.4)."""
        # Support both sync and async callables
        if asyncio.iscoroutinefunction(execute_fn):
            coro = execute_fn(input_data)
        else:
            loop = asyncio.get_running_loop()
            coro = loop.run_in_executor(None, execute_fn, input_data)

        try:
            return await asyncio.wait_for(coro, timeout=timeout_seconds)
        except asyncio.TimeoutError as exc:
            raise ToolTimeoutError(tool_name, float(timeout_seconds)) from exc
