"""Unit tests for ToolRegistry — Requirements 4.1–4.9."""

import asyncio

import pytest

from src.agents.tools.registry import (
    ToolDefinition,
    ToolInputValidationError,
    ToolOutputValidationError,
    ToolRegistry,
    ToolTimeoutError,
)

# --- Fixtures ---

SIMPLE_INPUT_SCHEMA = {
    "type": "object",
    "properties": {"x": {"type": "integer"}},
    "required": ["x"],
}

SIMPLE_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {"result": {"type": "integer"}},
    "required": ["result"],
}


def _make_tool(
    name: str = "test_tool",
    retryable: bool = True,
    max_retries: int = 2,
    timeout_seconds: int = 30,
) -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description="A test tool",
        input_schema=SIMPLE_INPUT_SCHEMA,
        output_schema=SIMPLE_OUTPUT_SCHEMA,
        timeout_seconds=timeout_seconds,
        retryable=retryable,
        max_retries=max_retries,
    )


async def _sync_executor(data: dict) -> dict:
    return {"result": data["x"] * 2}


# --- Registration tests (Req 4.1, 4.9) ---


class TestRegistration:
    def test_register_and_list(self):
        reg = ToolRegistry()
        tool = _make_tool()
        reg.register(tool, _sync_executor)
        assert len(reg.list_tools()) == 1
        assert reg.list_tools()[0].name == "test_tool"

    def test_deregister(self):
        reg = ToolRegistry()
        reg.register(_make_tool(), _sync_executor)
        reg.deregister("test_tool")
        assert len(reg.list_tools()) == 0

    def test_dynamic_registration_at_runtime(self):
        """Req 4.9 — tools can be added/removed at runtime."""
        reg = ToolRegistry()
        assert len(reg.list_tools()) == 0
        reg.register(_make_tool("a"), _sync_executor)
        reg.register(_make_tool("b"), _sync_executor)
        assert len(reg.list_tools()) == 2
        reg.deregister("a")
        assert len(reg.list_tools()) == 1

    def test_get_tool_schemas(self):
        reg = ToolRegistry()
        reg.register(_make_tool("alpha"), _sync_executor)
        reg.register(_make_tool("beta"), _sync_executor)
        schemas = reg.get_tool_schemas(["alpha", "missing"])
        assert len(schemas) == 1
        assert schemas[0]["function"]["name"] == "alpha"
        assert schemas[0]["type"] == "function"


# --- Invocation tests ---


class TestInvoke:
    @pytest.mark.asyncio
    async def test_successful_invocation(self):
        reg = ToolRegistry()
        reg.register(_make_tool(), _sync_executor)
        result = await reg.invoke("test_tool", "agent_a", {"x": 5})
        assert result == {"result": 10}

    @pytest.mark.asyncio
    async def test_input_validation_error(self):
        """Req 4.2 — invalid input raises ToolInputValidationError."""
        reg = ToolRegistry()
        reg.register(_make_tool(), _sync_executor)
        with pytest.raises(ToolInputValidationError):
            await reg.invoke("test_tool", "agent_a", {"x": "not_an_int"})

    @pytest.mark.asyncio
    async def test_output_validation_error(self):
        """Req 4.3 — invalid output raises ToolOutputValidationError."""

        async def bad_output(data: dict) -> dict:
            return {"result": "not_an_int"}

        reg = ToolRegistry()
        reg.register(_make_tool(retryable=False), bad_output)
        with pytest.raises(ToolOutputValidationError):
            await reg.invoke("test_tool", "agent_a", {"x": 1})

    @pytest.mark.asyncio
    async def test_timeout_error(self):
        """Req 4.4 — timeout raises ToolTimeoutError."""

        async def slow_fn(data: dict) -> dict:
            await asyncio.sleep(10)
            return {"result": 0}

        reg = ToolRegistry()
        reg.register(_make_tool(timeout_seconds=1), slow_fn)
        with pytest.raises(ToolTimeoutError):
            await reg.invoke("test_tool", "agent_a", {"x": 1})

    @pytest.mark.asyncio
    async def test_retryable_error_retries(self):
        """Req 4.6 — retryable errors are retried up to max_retries."""
        call_count = 0

        async def flaky_fn(data: dict) -> dict:
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ConnectionError("network failure")
            return {"result": data["x"]}

        reg = ToolRegistry()
        reg.register(_make_tool(max_retries=2), flaky_fn)
        result = await reg.invoke("test_tool", "agent_a", {"x": 7})
        assert result == {"result": 7}
        assert call_count == 3  # 1 initial + 2 retries

    @pytest.mark.asyncio
    async def test_non_retryable_error_no_retry(self):
        """Req 4.7 — non-retryable errors are returned immediately."""
        call_count = 0

        async def bad_fn(data: dict) -> dict:
            nonlocal call_count
            call_count += 1
            raise ValueError("business logic error")

        reg = ToolRegistry()
        reg.register(_make_tool(retryable=True, max_retries=2), bad_fn)
        with pytest.raises(ValueError, match="business logic"):
            await reg.invoke("test_tool", "agent_a", {"x": 1})
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_unknown_tool_raises_key_error(self):
        reg = ToolRegistry()
        with pytest.raises(KeyError):
            await reg.invoke("nonexistent", "agent_a", {})

    @pytest.mark.asyncio
    async def test_sync_executor_supported(self):
        """Sync callables should work via run_in_executor."""

        def sync_fn(data: dict) -> dict:
            return {"result": data["x"] + 1}

        reg = ToolRegistry()
        reg.register(_make_tool(), sync_fn)
        result = await reg.invoke("test_tool", "agent_a", {"x": 4})
        assert result == {"result": 5}

    @pytest.mark.asyncio
    async def test_retries_exhausted_raises(self):
        """When all retries are exhausted, the last error is raised."""

        async def always_fail(data: dict) -> dict:
            raise ConnectionError("down")

        reg = ToolRegistry()
        reg.register(_make_tool(max_retries=2), always_fail)
        with pytest.raises(ConnectionError, match="down"):
            await reg.invoke("test_tool", "agent_a", {"x": 1})
