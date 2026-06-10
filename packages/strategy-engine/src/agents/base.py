"""Abstract base class for all trading agents."""

from abc import ABC, abstractmethod

from src.agents.models import AgentTask, TaskResult


class Agent(ABC):
    """Abstract base class for all trading agents."""

    @abstractmethod
    def name(self) -> str:
        """Unique agent identifier (e.g., 'research', 'converter')."""

    @abstractmethod
    def description(self) -> str:
        """Human-readable description of the agent's purpose."""

    @abstractmethod
    def supported_task_types(self) -> list[str]:
        """List of task types this agent can handle."""

    @abstractmethod
    def supported_tools(self) -> list[str]:
        """List of tool names this agent is allowed to invoke."""

    @abstractmethod
    async def run(self, task: AgentTask) -> TaskResult:
        """Execute the agent's reasoning loop for the given task.

        The agent should:
        1. Enter PLANNING state — use LLM to analyze the task and plan actions
        2. Enter EXECUTING state — invoke tools to carry out the plan
        3. Enter REVIEWING state — evaluate results and decide next steps
        4. Return COMPLETED with a TaskResult, or loop back to PLANNING

        The agent MUST check for cancellation between steps.
        The agent MUST persist conversation history to AgentMemory.
        """

    @abstractmethod
    def get_system_prompt(self) -> str:
        """Return the system prompt for this agent's LLM interactions."""
