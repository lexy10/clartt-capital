"""Per-agent persistent memory with conversation history, decisions, and shared knowledge.

Persistence is handled via backend REST API (PostgreSQL tables:
agent_conversations, agent_decisions, agent_knowledge).

Requirements: 6.1–6.8
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

import requests

if TYPE_CHECKING:
    from src.agents.llm_client import LLMClient

logger = logging.getLogger(__name__)


class AgentMemory:
    """Per-agent persistent memory with conversation history, decisions, and shared knowledge."""

    def __init__(
        self,
        backend_url: str,
        llm_client: Optional[LLMClient] = None,
    ) -> None:
        self._backend_url = backend_url.rstrip("/")
        self._llm_client = llm_client

    # ------------------------------------------------------------------
    # Conversation History (Req 6.1, 6.3)
    # ------------------------------------------------------------------

    async def add_message(
        self,
        agent_name: str,
        task_id: str,
        role: str,
        content: str,
        tool_calls: Optional[list[dict]] = None,
    ) -> None:
        """Append a message to the agent's conversation history in agent_conversations."""
        payload = {
            "agent_name": agent_name,
            "task_id": task_id,
            "role": role,
            "content": content,
            "tool_calls": tool_calls,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        url = f"{self._backend_url}/api/agents/conversations"
        try:
            resp = requests.post(url, json=payload, timeout=10)
            resp.raise_for_status()
        except requests.RequestException:
            logger.warning(
                "Failed to persist conversation message for agent=%s task=%s",
                agent_name,
                task_id,
            )

    async def get_recent_context(
        self,
        agent_name: str,
        limit: int = 20,
    ) -> list[dict]:
        """Return last N conversation messages for the agent, ordered by created_at desc."""
        url = f"{self._backend_url}/api/agents/conversations"
        params = {"agent_name": agent_name, "limit": limit}
        try:
            resp = requests.get(url, params=params, timeout=10)
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException:
            logger.warning(
                "Failed to fetch recent context for agent=%s", agent_name
            )
            return []

    # ------------------------------------------------------------------
    # Decision Records (Req 6.2, 6.4)
    # ------------------------------------------------------------------

    async def record_decision(
        self,
        agent_name: str,
        task_id: str,
        decision_type: str,
        input_summary: str,
        output_summary: str,
        reasoning: str,
        outcome: str,
    ) -> None:
        """Store a decision record in agent_decisions."""
        payload = {
            "agent_name": agent_name,
            "task_id": task_id,
            "decision_type": decision_type,
            "input_summary": input_summary,
            "output_summary": output_summary,
            "reasoning": reasoning,
            "outcome": outcome,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        url = f"{self._backend_url}/api/agents/decisions"
        try:
            resp = requests.post(url, json=payload, timeout=10)
            resp.raise_for_status()
        except requests.RequestException:
            logger.warning(
                "Failed to persist decision for agent=%s task=%s",
                agent_name,
                task_id,
            )

    async def get_relevant_decisions(
        self,
        agent_name: str,
        decision_type: str,
        limit: int = 10,
    ) -> list[dict]:
        """Return past decisions filtered by agent and type, ordered by created_at desc."""
        url = f"{self._backend_url}/api/agents/decisions"
        params = {
            "agent_name": agent_name,
            "decision_type": decision_type,
            "limit": limit,
        }
        try:
            resp = requests.get(url, params=params, timeout=10)
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException:
            logger.warning(
                "Failed to fetch decisions for agent=%s type=%s",
                agent_name,
                decision_type,
            )
            return []

    # ------------------------------------------------------------------
    # Shared Knowledge Base (Req 6.5, 6.6)
    # ------------------------------------------------------------------

    async def store_knowledge(
        self,
        key: str,
        value: dict,
        source_agent: str,
        tags: list[str],
    ) -> None:
        """Store a finding in agent_knowledge, accessible by all agents."""
        payload = {
            "key": key,
            "value": value,
            "source_agent": source_agent,
            "tags": tags,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        url = f"{self._backend_url}/api/agents/knowledge"
        try:
            resp = requests.post(url, json=payload, timeout=10)
            resp.raise_for_status()
        except requests.RequestException:
            logger.warning(
                "Failed to store knowledge key=%s from agent=%s",
                key,
                source_agent,
            )

    async def query_knowledge(
        self,
        tags: Optional[list[str]] = None,
        source_agent: Optional[str] = None,
        limit: int = 20,
    ) -> list[dict]:
        """Query shared knowledge by tags or source agent."""
        url = f"{self._backend_url}/api/agents/knowledge"
        params: dict = {"limit": limit}
        if tags:
            params["tags"] = ",".join(tags)
        if source_agent:
            params["source_agent"] = source_agent
        try:
            resp = requests.get(url, params=params, timeout=10)
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException:
            logger.warning("Failed to query knowledge base")
            return []

    # ------------------------------------------------------------------
    # Context Management (Req 6.8)
    # ------------------------------------------------------------------

    async def summarize_context(
        self,
        agent_name: str,
        token_limit: int = 8000,
    ) -> str:
        """Use LLM to compress long conversation histories into a summary.

        If no LLMClient is configured, returns a simple truncated concatenation
        of the most recent messages.
        """
        messages = await self.get_recent_context(agent_name, limit=50)
        if not messages:
            return ""

        # Build raw context text from messages
        context_parts: list[str] = []
        for msg in reversed(messages):  # oldest first
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            context_parts.append(f"[{role}]: {content}")
        raw_context = "\n".join(context_parts)

        if self._llm_client is None:
            # Fallback: simple truncation when no LLM available
            if len(raw_context) > token_limit * 4:  # rough char estimate
                return raw_context[: token_limit * 4]
            return raw_context

        system_prompt = (
            "You are a context summarizer. Compress the following conversation "
            "history into a concise summary that preserves all key decisions, "
            "tool results, and action items. Keep the summary under "
            f"{token_limit} tokens."
        )
        llm_messages = [{"role": "user", "content": raw_context}]

        try:
            response = await self._llm_client.complete(
                agent_name=agent_name,
                system_prompt=system_prompt,
                messages=llm_messages,
                temperature=0.3,
                max_tokens=token_limit,
            )
            return response.content
        except Exception:
            logger.warning(
                "LLM summarization failed for agent=%s, falling back to truncation",
                agent_name,
            )
            if len(raw_context) > token_limit * 4:
                return raw_context[: token_limit * 4]
            return raw_context

    # ------------------------------------------------------------------
    # Retention / Cleanup (Req 6.7)
    # ------------------------------------------------------------------

    async def cleanup(self, retention_days: int = 90) -> int:
        """Delete conversation records older than retention_days. Return count deleted."""
        url = f"{self._backend_url}/api/agents/conversations/cleanup"
        payload = {"retention_days": retention_days}
        try:
            resp = requests.delete(url, json=payload, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            deleted = data.get("deleted_count", 0)
            logger.info(
                "Memory cleanup: deleted %d records older than %d days",
                deleted,
                retention_days,
            )
            return deleted
        except requests.RequestException:
            logger.warning("Failed to run memory cleanup")
            return 0
