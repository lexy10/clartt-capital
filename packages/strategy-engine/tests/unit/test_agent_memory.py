"""Unit tests for AgentMemory."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from src.agents.memory import AgentMemory
from src.agents.models import LLMResponse


@pytest.fixture
def memory() -> AgentMemory:
    return AgentMemory(backend_url="http://localhost:3000")


@pytest.fixture
def mock_llm_client() -> MagicMock:
    client = MagicMock()
    client.complete = AsyncMock(
        return_value=LLMResponse(
            content="Summarized context: agent performed analysis and made decisions.",
            prompt_tokens=100,
            completion_tokens=50,
            total_tokens=150,
        )
    )
    return client


@pytest.fixture
def memory_with_llm(mock_llm_client: MagicMock) -> AgentMemory:
    return AgentMemory(backend_url="http://localhost:3000", llm_client=mock_llm_client)


class TestInit:
    def test_strips_trailing_slash(self):
        mem = AgentMemory(backend_url="http://localhost:3000/")
        assert mem._backend_url == "http://localhost:3000"

    def test_llm_client_optional(self, memory: AgentMemory):
        assert memory._llm_client is None

    def test_llm_client_set(self, memory_with_llm: AgentMemory):
        assert memory_with_llm._llm_client is not None
