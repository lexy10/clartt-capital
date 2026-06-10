"""Research Agent — discovers and evaluates trading strategies using LLM reasoning.

Supports two task types:
- research_strategy: LLM reasoning → hypothesis formulation → duplicate check → publish
- market_analysis: query events/signals → LLM analysis → MarketAnalysisReport

Requirements: 10.1–10.7
"""

import json
import logging
import time
import traceback

from src.agents.base import Agent
from src.agents.llm_client import LLMClient
from src.agents.memory import AgentMemory
from src.agents.models import (
    AgentState,
    AgentTask,
    MarketAnalysisReport,
    StrategyHypothesis,
    TaskResult,
    TaskStatus,
)
from src.agents.state_machine import AgentStateMachine
from src.agents.streams import AgentStreamPublisher
from src.agents.tools.registry import ToolRegistry

logger = logging.getLogger("strategy_engine.agents.research")

RESEARCH_SYSTEM_PROMPT = """\
You are a senior quantitative trading researcher for Clartt Capital. Your role is to \
discover, evaluate, and formulate trading strategy hypotheses for automated execution.

Domain expertise:
- ICT (Inner Circle Trader) concepts: order blocks, fair value gaps, liquidity sweeps, \
market structure shifts, optimal trade entry
- Technical analysis: support/resistance, trend following, mean reversion, momentum, \
breakout strategies
- Indicators: RSI, MACD, Bollinger Bands, ATR, EMA/SMA crossovers, volume profile, \
ADX, stochastic oscillator
- Risk management: position sizing, stop-loss placement, risk-reward ratios
- Market microstructure: spread dynamics, volatility regimes, session timing

Supported instruments: US30 (Dow Jones), XAUUSD (Gold), Volatility 75 Index, \
Volatility 25 Index.

When formulating a strategy hypothesis, you MUST output valid JSON matching this schema:
{
  "name": "strategy_name_in_snake_case",
  "description": "Clear description of the strategy logic",
  "entry_rules": ["rule 1", "rule 2", ...],
  "exit_rules": ["rule 1", "rule 2", ...],
  "indicator_configurations": [{"name": "indicator", "params": {...}}, ...],
  "expected_market_conditions": ["trending", "high volatility", ...],
  "source_references": ["reference 1", ...],
  "confidence_estimate": 0.0 to 1.0,
  "target_instruments": ["US30", ...]
}

When performing market analysis, output valid JSON matching this schema:
{
  "instruments": ["US30", ...],
  "timeframe": "1h",
  "trend_assessment": {"US30": "bullish", ...},
  "volatility_regime": "high" | "medium" | "low",
  "key_levels": [{"instrument": "US30", "type": "support", "price": 39500.0}, ...],
  "recommended_strategy_types": ["momentum", "breakout", ...]
}

Be precise, data-driven, and conservative with confidence estimates. \
Always consider risk management in your analysis.\
"""


class ResearchAgent(Agent):
    """Discovers and evaluates trading strategies using LLM reasoning.

    Constructor args:
        llm_client: LLMClient for LLM completions.
        tool_registry: ToolRegistry for invoking platform tools.
        memory: AgentMemory for conversation history and knowledge base.
        stream_publisher: AgentStreamPublisher for publishing to Redis streams.
        backend_url: Backend API base URL (for state machine persistence).
    """

    def __init__(
        self,
        llm_client: LLMClient,
        tool_registry: ToolRegistry,
        memory: AgentMemory,
        stream_publisher: AgentStreamPublisher,
        backend_url: str = "http://backend:3000",
    ) -> None:
        self._llm_client = llm_client
        self._tool_registry = tool_registry
        self._memory = memory
        self._stream_publisher = stream_publisher
        self._state_machine = AgentStateMachine("research", backend_url)

    def name(self) -> str:
        return "research"

    def description(self) -> str:
        return (
            "Discovers and evaluates trading strategies using LLM reasoning, "
            "producing structured strategy hypotheses and market analysis reports."
        )

    def supported_task_types(self) -> list[str]:
        return ["research_strategy", "market_analysis"]

    def supported_tools(self) -> list[str]:
        return ["query_signals", "query_events", "list_algorithms"]

    def get_system_prompt(self) -> str:
        return RESEARCH_SYSTEM_PROMPT

    async def run(self, task: AgentTask) -> TaskResult:
        """Execute the research agent's reasoning loop.

        Dispatches to _run_research_strategy or _run_market_analysis
        based on task.type. Follows PLANNING → EXECUTING → REVIEWING flow.
        """
        start_time = time.monotonic()
        try:
            if task.type == "research_strategy":
                return await self._run_research_strategy(task, start_time)
            elif task.type == "market_analysis":
                return await self._run_market_analysis(task, start_time)
            else:
                return TaskResult(
                    task_id=task.id,
                    agent_name=self.name(),
                    status=TaskStatus.FAILED,
                    error=f"Unsupported task type: {task.type}",
                    duration_seconds=time.monotonic() - start_time,
                )
        except Exception as e:
            duration = time.monotonic() - start_time
            logger.error("Research agent failed on task %s: %s", task.id, e)
            self._state_machine.transition(AgentState.FAILED, reason=str(e))
            self._state_machine.record_failure(
                reason=str(e),
                stack_trace=traceback.format_exc(),
                task_id=task.id,
            )
            return TaskResult(
                task_id=task.id,
                agent_name=self.name(),
                status=TaskStatus.FAILED,
                error=str(e),
                duration_seconds=duration,
            )

    # ------------------------------------------------------------------
    # research_strategy flow (Req 10.1–10.4, 10.6, 10.7)
    # ------------------------------------------------------------------

    async def _run_research_strategy(
        self, task: AgentTask, start_time: float
    ) -> TaskResult:
        """Research strategy flow:
        1. PLANNING: Analyze focus_area, instruments, market_conditions.
           Query recent signals and events for context.
        2. EXECUTING: Formulate strategy hypothesis with entry/exit rules.
           Check knowledge base for duplicate hypotheses.
        3. REVIEWING: Validate hypothesis structure, assign confidence.
           Publish to agents:research_output + knowledge base.
        """
        payload = task.payload

        # ── PLANNING ──
        self._state_machine.transition(AgentState.PLANNING, reason="research_strategy")

        # Gather context from tools (Req 10.6)
        signals_context = await self._query_signals_context(task, payload)
        events_context = await self._query_events_context(task, payload)
        algorithms_context = await self._query_algorithms_context(task)

        # Build planning prompt
        planning_prompt = self._build_research_planning_prompt(
            payload, signals_context, events_context, algorithms_context
        )

        await self._memory.add_message(
            self.name(), task.id, "user", planning_prompt
        )

        # LLM planning call
        planning_response = await self._llm_client.complete(
            agent_name=self.name(),
            system_prompt=self.get_system_prompt(),
            messages=[{"role": "user", "content": planning_prompt}],
            temperature=0.7,
        )

        await self._memory.add_message(
            self.name(), task.id, "assistant", planning_response.content
        )

        # ── EXECUTING ──
        self._state_machine.transition(AgentState.EXECUTING, reason="formulating hypothesis")

        # Ask LLM to produce the structured hypothesis
        formulation_prompt = (
            "Based on your analysis above, produce a single StrategyHypothesis "
            "as a JSON object. Include concrete entry_rules, exit_rules, "
            "indicator_configurations, and a conservative confidence_estimate."
        )

        await self._memory.add_message(
            self.name(), task.id, "user", formulation_prompt
        )

        formulation_response = await self._llm_client.complete(
            agent_name=self.name(),
            system_prompt=self.get_system_prompt(),
            messages=[
                {"role": "user", "content": planning_prompt},
                {"role": "assistant", "content": planning_response.content},
                {"role": "user", "content": formulation_prompt},
            ],
            temperature=0.5,
        )

        await self._memory.add_message(
            self.name(), task.id, "assistant", formulation_response.content
        )

        # Parse hypothesis from LLM response
        hypothesis = self._parse_hypothesis(formulation_response.content)

        # ── REVIEWING ──
        self._state_machine.transition(AgentState.REVIEWING, reason="validating hypothesis")

        # Duplicate detection (Req 10.7)
        is_duplicate = await self._check_duplicate(hypothesis)

        if is_duplicate:
            logger.info(
                "Hypothesis '%s' flagged as duplicate — skipping publish",
                hypothesis.name,
            )
            await self._memory.record_decision(
                agent_name=self.name(),
                task_id=task.id,
                decision_type="research_strategy",
                input_summary=planning_prompt[:500],
                output_summary=f"Duplicate hypothesis: {hypothesis.name}",
                reasoning="Entry/exit rules and indicators match existing hypothesis",
                outcome="skipped_duplicate",
            )
            self._state_machine.transition(AgentState.COMPLETED, reason="duplicate hypothesis")
            return TaskResult(
                task_id=task.id,
                agent_name=self.name(),
                status=TaskStatus.COMPLETED,
                output={
                    "hypothesis": hypothesis.model_dump(),
                    "duplicate": True,
                },
                duration_seconds=time.monotonic() - start_time,
            )

        # Publish to agents:research_output stream (Req 10.4)
        self._stream_publisher.publish("agents:research_output", hypothesis)

        # Store in shared knowledge base (Req 10.4)
        tags = ["hypothesis", "research"]
        if payload.get("focus_area"):
            tags.append(payload["focus_area"])
        tags.extend(hypothesis.target_instruments)

        await self._memory.store_knowledge(
            key=f"hypothesis:{hypothesis.name}",
            value=hypothesis.model_dump(),
            source_agent=self.name(),
            tags=tags,
        )

        await self._memory.record_decision(
            agent_name=self.name(),
            task_id=task.id,
            decision_type="research_strategy",
            input_summary=planning_prompt[:500],
            output_summary=f"Hypothesis: {hypothesis.name} (confidence={hypothesis.confidence_estimate})",
            reasoning=planning_response.content[:500],
            outcome="success",
        )

        self._state_machine.transition(AgentState.COMPLETED, reason="hypothesis published")

        return TaskResult(
            task_id=task.id,
            agent_name=self.name(),
            status=TaskStatus.COMPLETED,
            output={
                "hypothesis": hypothesis.model_dump(),
                "duplicate": False,
            },
            duration_seconds=time.monotonic() - start_time,
        )

    # ------------------------------------------------------------------
    # market_analysis flow (Req 10.5)
    # ------------------------------------------------------------------

    async def _run_market_analysis(
        self, task: AgentTask, start_time: float
    ) -> TaskResult:
        """Market analysis flow:
        1. PLANNING: Analyze instruments and timeframe context.
        2. EXECUTING: Query events and signals, use LLM to assess trends/volatility.
        3. REVIEWING: Produce MarketAnalysisReport.
        """
        payload = task.payload

        # ── PLANNING ──
        self._state_machine.transition(AgentState.PLANNING, reason="market_analysis")

        instruments = payload.get("instruments", [])
        timeframe = payload.get("timeframe", "1h")

        # Gather context
        signals_context = await self._query_signals_context(task, payload)
        events_context = await self._query_events_context(task, payload)

        analysis_prompt = (
            f"Perform a market analysis for instruments: {instruments} "
            f"on timeframe: {timeframe}.\n\n"
            f"Recent signals data:\n{signals_context}\n\n"
            f"Recent events data:\n{events_context}\n\n"
            "Assess the current trend for each instrument, determine the "
            "volatility regime (high/medium/low), identify key support/resistance "
            "levels, and recommend suitable strategy types. "
            "Output your analysis as a JSON object matching the MarketAnalysisReport schema."
        )

        await self._memory.add_message(
            self.name(), task.id, "user", analysis_prompt
        )

        # ── EXECUTING ──
        self._state_machine.transition(AgentState.EXECUTING, reason="LLM analysis")

        analysis_response = await self._llm_client.complete(
            agent_name=self.name(),
            system_prompt=self.get_system_prompt(),
            messages=[{"role": "user", "content": analysis_prompt}],
            temperature=0.5,
        )

        await self._memory.add_message(
            self.name(), task.id, "assistant", analysis_response.content
        )

        # Parse the report
        report = self._parse_market_analysis(analysis_response.content, instruments, timeframe)

        # ── REVIEWING ──
        self._state_machine.transition(AgentState.REVIEWING, reason="validating report")

        await self._memory.record_decision(
            agent_name=self.name(),
            task_id=task.id,
            decision_type="market_analysis",
            input_summary=f"Instruments: {instruments}, Timeframe: {timeframe}",
            output_summary=f"Volatility: {report.volatility_regime}, Strategies: {report.recommended_strategy_types}",
            reasoning=analysis_response.content[:500],
            outcome="success",
        )

        self._state_machine.transition(AgentState.COMPLETED, reason="analysis complete")

        return TaskResult(
            task_id=task.id,
            agent_name=self.name(),
            status=TaskStatus.COMPLETED,
            output={"report": report.model_dump()},
            duration_seconds=time.monotonic() - start_time,
        )

    # ------------------------------------------------------------------
    # Tool invocation helpers
    # ------------------------------------------------------------------

    async def _query_signals_context(self, task: AgentTask, payload: dict) -> str:
        """Query recent signals via the query_signals tool (Req 10.6)."""
        try:
            instruments = payload.get("instruments", [])
            input_data: dict = {}
            if instruments:
                input_data["instrument"] = instruments[0] if len(instruments) == 1 else instruments[0]
            result = await self._tool_registry.invoke(
                "query_signals", self.name(), input_data
            )
            return json.dumps(result, default=str)[:2000]
        except Exception as e:
            logger.warning("Failed to query signals: %s", e)
            return "No signal data available."

    async def _query_events_context(self, task: AgentTask, payload: dict) -> str:
        """Query recent events via the query_events tool (Req 10.6)."""
        try:
            input_data: dict = {}
            if payload.get("instruments"):
                input_data["aggregate_id"] = payload["instruments"][0]
            result = await self._tool_registry.invoke(
                "query_events", self.name(), input_data
            )
            return json.dumps(result, default=str)[:2000]
        except Exception as e:
            logger.warning("Failed to query events: %s", e)
            return "No event data available."

    async def _query_algorithms_context(self, task: AgentTask) -> str:
        """Query existing algorithms via the list_algorithms tool."""
        try:
            result = await self._tool_registry.invoke(
                "list_algorithms", self.name(), {}
            )
            return json.dumps(result, default=str)[:2000]
        except Exception as e:
            logger.warning("Failed to list algorithms: %s", e)
            return "No algorithm data available."

    # ------------------------------------------------------------------
    # Prompt building
    # ------------------------------------------------------------------

    @staticmethod
    def _build_research_planning_prompt(
        payload: dict,
        signals_context: str,
        events_context: str,
        algorithms_context: str,
    ) -> str:
        """Build the planning prompt for research_strategy tasks."""
        focus_area = payload.get("focus_area", "general")
        instruments = payload.get("instruments", [])
        market_conditions = payload.get("market_conditions", "unknown")
        sources = payload.get("sources", [])

        parts = [
            f"Research a new trading strategy with focus area: {focus_area}.",
        ]
        if instruments:
            parts.append(f"Target instruments: {', '.join(instruments)}.")
        if market_conditions and market_conditions != "unknown":
            parts.append(f"Current market conditions: {market_conditions}.")
        if sources:
            parts.append(f"Preferred sources: {', '.join(sources)}.")

        parts.append(f"\nExisting algorithms on the platform:\n{algorithms_context}")
        parts.append(f"\nRecent signal performance:\n{signals_context}")
        parts.append(f"\nRecent trading events:\n{events_context}")
        parts.append(
            "\nAnalyze the above context and propose a novel strategy hypothesis. "
            "Avoid duplicating existing algorithms. Focus on strategies that "
            "complement the current portfolio."
        )
        return "\n".join(parts)

    # ------------------------------------------------------------------
    # Parsing helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_hypothesis(llm_content: str) -> StrategyHypothesis:
        """Parse a StrategyHypothesis from LLM response content.

        Attempts to extract JSON from the response, handling markdown
        code fences and partial JSON.
        """
        raw = llm_content.strip()

        # Strip markdown code fences if present
        if "```json" in raw:
            raw = raw.split("```json", 1)[1]
            raw = raw.split("```", 1)[0]
        elif "```" in raw:
            raw = raw.split("```", 1)[1]
            raw = raw.split("```", 1)[0]

        try:
            data = json.loads(raw.strip())
            return StrategyHypothesis.model_validate(data)
        except (json.JSONDecodeError, Exception) as e:
            logger.warning("Failed to parse hypothesis JSON: %s", e)
            # Fallback: create a minimal hypothesis from the raw content
            return StrategyHypothesis(
                name="unparsed_hypothesis",
                description=llm_content[:500],
                entry_rules=["See description"],
                exit_rules=["See description"],
                confidence_estimate=0.3,
            )

    @staticmethod
    def _parse_market_analysis(
        llm_content: str,
        instruments: list[str],
        timeframe: str,
    ) -> MarketAnalysisReport:
        """Parse a MarketAnalysisReport from LLM response content."""
        raw = llm_content.strip()

        # Strip markdown code fences if present
        if "```json" in raw:
            raw = raw.split("```json", 1)[1]
            raw = raw.split("```", 1)[0]
        elif "```" in raw:
            raw = raw.split("```", 1)[1]
            raw = raw.split("```", 1)[0]

        try:
            data = json.loads(raw.strip())
            return MarketAnalysisReport.model_validate(data)
        except (json.JSONDecodeError, Exception) as e:
            logger.warning("Failed to parse market analysis JSON: %s", e)
            # Fallback: create a minimal report
            return MarketAnalysisReport(
                instruments=instruments,
                timeframe=timeframe,
                volatility_regime="unknown",
                trend_assessment={},
                recommended_strategy_types=[],
            )

    # ------------------------------------------------------------------
    # Duplicate detection (Req 10.7)
    # ------------------------------------------------------------------

    async def _check_duplicate(self, hypothesis: StrategyHypothesis) -> bool:
        """Check if a substantially similar hypothesis exists in the knowledge base.

        A hypothesis is considered duplicate if an existing entry has matching
        entry_rules, exit_rules, AND indicators (indicator_configurations).
        """
        existing = await self._memory.query_knowledge(
            tags=["hypothesis"],
            source_agent=self.name(),
            limit=50,
        )

        entry_set = set(hypothesis.entry_rules)
        exit_set = set(hypothesis.exit_rules)
        indicator_names = {
            cfg.get("name", "") for cfg in hypothesis.indicator_configurations
        }

        for item in existing:
            value = item.get("value", {})
            if not isinstance(value, dict):
                continue

            existing_entry = set(value.get("entry_rules", []))
            existing_exit = set(value.get("exit_rules", []))
            existing_indicators = {
                cfg.get("name", "")
                for cfg in value.get("indicator_configurations", [])
            }

            # Match if all three components are identical
            if (
                entry_set == existing_entry
                and exit_set == existing_exit
                and indicator_names == existing_indicators
            ):
                return True

        return False
