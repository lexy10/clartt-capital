"""Framework initialization — wires all agent components together.

Instantiates all framework components, registers agents and tools,
creates Redis consumer groups, starts heartbeat loops, and integrates
with the FastAPI app lifecycle.

Requirements: 1.1, 4.5, 7.9, 14.3
"""

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI
from redis import Redis

from src.agents.api.router import setup_agents_router
from src.agents.approval import ApprovalGateManager
from src.agents.autonomy import AutonomyManager
from src.agents.config import load_framework_config
from src.agents.equity_monitor import EquityCurveMonitor
from src.agents.kill_switch import AgentKillSwitch
from src.agents.llm_client import LLMClient
from src.agents.memory import AgentMemory
from src.agents.performance_decay import PerformanceDecayDetector
from src.agents.pipeline import PipelineOrchestrator
from src.agents.registry import AgentRegistry
from src.agents.sandbox import CodeSandbox
from src.agents.streams import create_consumer_groups
from src.agents.task_queue import TaskQueue
from src.agents.tools.builtin import register_builtin_tools
from src.agents.tools.registry import ToolRegistry
from src.events.event_publisher import EventPublisher

logger = logging.getLogger("strategy_engine.agents.bootstrap")


class AgentFramework:
    """Container holding all instantiated agent framework components."""

    def __init__(self) -> None:
        self.config = None
        self.redis_client: Optional[Redis] = None
        self.event_publisher: Optional[EventPublisher] = None
        self.llm_client: Optional[LLMClient] = None
        self.memory: Optional[AgentMemory] = None
        self.tool_registry: Optional[ToolRegistry] = None
        self.sandbox: Optional[CodeSandbox] = None
        self.approval_manager: Optional[ApprovalGateManager] = None
        self.task_queue: Optional[TaskQueue] = None
        self.agent_registry: Optional[AgentRegistry] = None
        self.pipeline: Optional[PipelineOrchestrator] = None
        self.kill_switch: Optional[AgentKillSwitch] = None
        self.autonomy_manager: Optional[AutonomyManager] = None
        self.equity_monitor: Optional[EquityCurveMonitor] = None
        self.decay_detector: Optional[PerformanceDecayDetector] = None


def bootstrap_agent_framework(
    redis_client: Optional[Redis] = None,
    backend_url: Optional[str] = None,
    backtest_engine: object = None,
    strategy_registry: object = None,
    algorithm_manager: object = None,
) -> AgentFramework:
    """Instantiate and wire all agent framework components.

    Args:
        redis_client:      Existing Redis connection (creates one if None)
        backend_url:       Backend REST API base URL
        backtest_engine:   Optional BacktestEngine for local tool execution
        strategy_registry: Optional StrategyRegistry for algorithm listing
        algorithm_manager: Optional AlgorithmManager for validation
    """
    fw = AgentFramework()

    # 1. Load configuration
    fw.config = load_framework_config()
    logger.info("Agent framework config loaded")

    # 2. Redis client
    if redis_client is None:
        redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379")
        redis_client = Redis.from_url(redis_url, decode_responses=False)
    fw.redis_client = redis_client

    # 3. Resolve backend URL
    if backend_url is None:
        backend_url = os.environ.get("BACKEND_URL", "http://backend:3000")

    # 4. Event publisher
    fw.event_publisher = EventPublisher(
        redis_client=redis_client,
        source_service="strategy-engine",
    )

    # 5. LLM client (Req 5.1–5.9)
    fw.llm_client = LLMClient(
        redis_client=redis_client,
        backend_url=backend_url,
        global_daily_budget_usd=fw.config.global_daily_budget_usd,
        global_requests_per_minute=fw.config.global_requests_per_minute,
        event_publisher=fw.event_publisher,
    )

    # 6. Agent memory (Req 6.1–6.8)
    fw.memory = AgentMemory(
        backend_url=backend_url,
        llm_client=fw.llm_client,
    )

    # 7. Tool registry + built-in tools (Req 4.1–4.9)
    fw.tool_registry = ToolRegistry(event_publisher=fw.event_publisher)
    register_builtin_tools(
        registry=fw.tool_registry,
        backend_url=backend_url,
        backtest_engine=backtest_engine,
        strategy_registry=strategy_registry,
        algorithm_manager=algorithm_manager,
    )
    logger.info("Tool registry initialized with built-in tools")

    # 8. Code sandbox (Req 9.6–9.8)
    fw.sandbox = CodeSandbox(
        timeout_seconds=fw.config.sandbox_timeout_seconds,
        memory_mb=fw.config.sandbox_memory_mb,
    )

    # 9. Autonomy manager (Req 9.1, 9.2)
    fw.autonomy_manager = AutonomyManager(redis_client=redis_client)

    # 10. Approval gate manager (Req 9.1–9.5)
    fw.approval_manager = ApprovalGateManager(
        redis_client=redis_client,
        event_publisher=fw.event_publisher,
        backend_url=backend_url,
        timeout_hours=fw.config.approval_timeout_hours,
        autonomy_manager=fw.autonomy_manager,
    )

    # 11. Task queue (Req 2.1–2.11)
    fw.task_queue = TaskQueue(
        redis_client=redis_client,
        event_publisher=fw.event_publisher,
    )

    # 12. Agent registry (Req 1.1–1.10)
    fw.agent_registry = AgentRegistry(
        redis_client=redis_client,
        event_publisher=fw.event_publisher,
        memory=fw.memory,
        task_queue=fw.task_queue,
        heartbeat_interval=fw.config.heartbeat_interval_seconds,
        backend_url=backend_url,
    )

    # 13. Kill switch (Req 19.1–19.8)
    fw.kill_switch = AgentKillSwitch(
        redis_client=redis_client,
        event_publisher=fw.event_publisher,
        registry=fw.agent_registry,
    )

    # 14. Pipeline orchestrator (Req 7.1–7.10)
    fw.pipeline = PipelineOrchestrator(
        task_queue=fw.task_queue,
        agent_registry=fw.agent_registry,
        event_publisher=fw.event_publisher,
        backend_url=backend_url,
    )

    # 15. Equity monitor (Req 23.1–23.6)
    fw.equity_monitor = EquityCurveMonitor(
        redis_client=redis_client,
        event_publisher=fw.event_publisher,
        backend_url=backend_url,
    )

    # 16. Performance decay detector (Req 24.1–24.9, 4.1)
    fw.decay_detector = PerformanceDecayDetector(
        redis_client=redis_client,
        event_publisher=fw.event_publisher,
        backend_url=backend_url,
        autonomy_manager=fw.autonomy_manager,
        task_queue=fw.task_queue,
        approval_manager=fw.approval_manager,
    )

    # 17. Register all four agents (Req 1.1)
    _register_agents(fw, backend_url)

    # 18. Create Redis consumer groups (Req 14.3)
    create_consumer_groups(redis_client)
    logger.info("Redis consumer groups created")

    # 19. Wire router dependencies
    setup_agents_router(
        registry=fw.agent_registry,
        task_queue=fw.task_queue,
        pipeline_orchestrator=fw.pipeline,
        approval_manager=fw.approval_manager,
        llm_client=fw.llm_client,
        framework_config=fw.config,
        kill_switch=fw.kill_switch,
        autonomy_manager=fw.autonomy_manager,
        equity_monitor=fw.equity_monitor,
        correlation_guard=None,  # Correlation guard is wired into StrategyRunner, not router
        redis_client=fw.redis_client,
        tool_registry=fw.tool_registry,
    )
    logger.info("Agents router wired")

    logger.info("Agent framework bootstrap complete")
    return fw


def _register_agents(fw: AgentFramework, backend_url: str) -> None:
    """Register all four agent implementations."""
    try:
        from src.agents.agents.research_agent import ResearchAgent

        fw.agent_registry.register(
            ResearchAgent(
                llm_client=fw.llm_client,
                tool_registry=fw.tool_registry,
                memory=fw.memory,
                redis_client=fw.redis_client,
            )
        )
        logger.info("Registered ResearchAgent")
    except Exception as exc:
        logger.warning("Failed to register ResearchAgent: %s", exc)

    try:
        from src.agents.agents.converter_agent import ConverterAgent

        fw.agent_registry.register(
            ConverterAgent(
                llm_client=fw.llm_client,
                tool_registry=fw.tool_registry,
                memory=fw.memory,
                sandbox=fw.sandbox,
            )
        )
        logger.info("Registered ConverterAgent")
    except Exception as exc:
        logger.warning("Failed to register ConverterAgent: %s", exc)

    try:
        from src.agents.agents.backtest_agent import BacktestAgent

        fw.agent_registry.register(
            BacktestAgent(
                llm_client=fw.llm_client,
                tool_registry=fw.tool_registry,
                memory=fw.memory,
                redis_client=fw.redis_client,
            )
        )
        logger.info("Registered BacktestAgent")
    except Exception as exc:
        logger.warning("Failed to register BacktestAgent: %s", exc)

    try:
        from src.agents.agents.forward_test_agent import ForwardTestAgent

        fw.agent_registry.register(
            ForwardTestAgent(
                llm_client=fw.llm_client,
                tool_registry=fw.tool_registry,
                memory=fw.memory,
                redis_client=fw.redis_client,
                approval_manager=fw.approval_manager,
            )
        )
        logger.info("Registered ForwardTestAgent")
    except Exception as exc:
        logger.warning("Failed to register ForwardTestAgent: %s", exc)


@asynccontextmanager
async def agent_framework_lifespan(app: FastAPI):
    """FastAPI lifespan context manager for agent framework startup/shutdown.

    Usage in main.py:
        app = FastAPI(lifespan=agent_framework_lifespan)
    """
    redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379")
    redis_client = Redis.from_url(redis_url, decode_responses=False)
    backend_url = os.environ.get("BACKEND_URL", "http://backend:3000")

    fw = bootstrap_agent_framework(
        redis_client=redis_client,
        backend_url=backend_url,
    )

    # Store framework on app state for access in endpoints
    app.state.agent_framework = fw

    logger.info("Agent framework started via lifespan")
    yield

    # Shutdown
    logger.info("Agent framework shutting down")
