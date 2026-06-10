"""Backtest Agent — runs backtests, analyzes results, and iterates autonomously.

Supports the `run_backtest` task type:
- Run backtest → LLM analysis → optional optimization → optional walk-forward → optional re-run
- Walk-forward trigger: win_rate > 0.4 AND profit_factor > 1.0
- Iteration limit: max_backtest_iterations (default 5)
- Output BacktestAnalysis, publish to agents:backtest_output + knowledge base

Requirements: 12.1–12.10
"""

import json
import logging
import time
import traceback
from typing import Optional

from src.agents.base import Agent
from src.agents.llm_client import LLMClient
from src.agents.memory import AgentMemory
from src.agents.models import (
    AgentState,
    AgentTask,
    BacktestAnalysis,
    PerformanceGrade,
    TaskResult,
    TaskStatus,
)
from src.agents.state_machine import AgentStateMachine
from src.agents.streams import AgentStreamPublisher
from src.agents.tools.registry import ToolRegistry

logger = logging.getLogger("strategy_engine.agents.backtest")

DEFAULT_MAX_BACKTEST_ITERATIONS = 5

# Walk-forward trigger thresholds (Req 12.6)
WALK_FORWARD_WIN_RATE_THRESHOLD = 0.4
WALK_FORWARD_PROFIT_FACTOR_THRESHOLD = 1.0

BACKTEST_SYSTEM_PROMPT = """\
You are a senior quantitative analyst for Clartt Capital. Your role is to \
analyze backtest results, identify strengths and weaknesses, suggest parameter \
optimizations, and grade strategy performance.

Domain expertise:
- Performance metrics: win rate, profit factor, Sharpe ratio, Sortino ratio, \
max drawdown, expectancy, average win/loss ratio, recovery factor
- Risk assessment: drawdown analysis, tail risk, consecutive loss streaks, \
volatility-adjusted returns
- Parameter optimization: grid search, walk-forward validation, \
overfitting detection, out-of-sample testing
- Market regime analysis: trending vs ranging, high vs low volatility, \
session-specific performance (London, New York, Asian)

Supported instruments: US30 (Dow Jones), XAUUSD (Gold), Volatility 75 Index, \
Volatility 25 Index.

Performance grading scale:
- A: win_rate >= 60%, profit_factor >= 2.0, max_drawdown <= 10%
- B: win_rate >= 50%, profit_factor >= 1.5, max_drawdown <= 15%
- C: win_rate >= 40%, profit_factor >= 1.0, max_drawdown <= 20%
- D: win_rate >= 30%, profit_factor >= 0.8, max_drawdown <= 30%
- F: Below D thresholds

When analyzing backtest results, output valid JSON matching this schema:
{
  "performance_grade": "A" | "B" | "C" | "D" | "F",
  "strengths": ["strength 1", ...],
  "weaknesses": ["weakness 1", ...],
  "optimization_suggestions": [
    {"param": "param_name", "current": value, "suggested_range": [min, max]}, ...
  ],
  "recommended_next_steps": ["step 1", ...]
}

Be precise and data-driven. Flag overfitting risks when optimization results \
significantly outperform walk-forward results. Always consider the number of \
trades — small sample sizes reduce confidence in metrics.\
"""


class BacktestAgent(Agent):
    """Runs backtests, analyzes results, and iterates autonomously.

    Constructor args:
        llm_client: LLMClient for LLM completions.
        tool_registry: ToolRegistry for invoking platform tools.
        memory: AgentMemory for conversation history and knowledge base.
        stream_publisher: AgentStreamPublisher for publishing to Redis streams.
        backend_url: Backend API base URL (for state machine persistence).
        max_backtest_iterations: Max autonomous re-run iterations (default 5).
    """

    def __init__(
        self,
        llm_client: LLMClient,
        tool_registry: ToolRegistry,
        memory: AgentMemory,
        stream_publisher: AgentStreamPublisher,
        backend_url: str = "http://backend:3000",
        max_backtest_iterations: int = DEFAULT_MAX_BACKTEST_ITERATIONS,
    ) -> None:
        self._llm_client = llm_client
        self._tool_registry = tool_registry
        self._memory = memory
        self._stream_publisher = stream_publisher
        self._state_machine = AgentStateMachine("backtest", backend_url)
        self._max_iterations = max_backtest_iterations

    def name(self) -> str:
        return "backtest"

    def description(self) -> str:
        return (
            "Runs backtests via BacktestEngine, analyzes results with LLM reasoning, "
            "suggests parameter optimizations, runs walk-forward analysis, and iterates "
            "autonomously to improve strategy performance."
        )

    def supported_task_types(self) -> list[str]:
        return ["run_backtest"]

    def supported_tools(self) -> list[str]:
        return [
            "run_backtest",
            "optimize_parameters",
            "walk_forward_analysis",
            "get_strategy_config",
            "list_algorithms",
        ]

    def get_system_prompt(self) -> str:
        return BACKTEST_SYSTEM_PROMPT

    async def run(self, task: AgentTask) -> TaskResult:
        """Execute the backtest agent's reasoning loop.

        Handles `run_backtest` task type following
        PLANNING → EXECUTING → REVIEWING flow with optional iteration.
        """
        start_time = time.monotonic()
        try:
            if task.type != "run_backtest":
                return TaskResult(
                    task_id=task.id,
                    agent_name=self.name(),
                    status=TaskStatus.FAILED,
                    error=f"Unsupported task type: {task.type}",
                    duration_seconds=time.monotonic() - start_time,
                )
            return await self._run_backtest(task, start_time)
        except Exception as e:
            duration = time.monotonic() - start_time
            logger.error("Backtest agent failed on task %s: %s", task.id, e)
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
    # run_backtest flow (Req 12.1–12.10)
    # ------------------------------------------------------------------

    async def _run_backtest(
        self, task: AgentTask, start_time: float
    ) -> TaskResult:
        """Backtest flow:
        1. PLANNING: Parse payload, gather strategy config context.
        2. EXECUTING: Run backtest → LLM analysis → optional optimization
           → optional walk-forward → optional re-run (up to max iterations).
        3. REVIEWING: Produce BacktestAnalysis, publish + store.
        """
        payload = task.payload

        # ── PLANNING ── (Req 12.1)
        self._state_machine.transition(AgentState.PLANNING, reason="run_backtest")

        strategy_id = payload.get("strategy_id", "")
        algorithm_name = payload.get("algorithm_name", "")
        instruments = payload.get("instruments", [])
        start_date = payload.get("start_date", "")
        end_date = payload.get("end_date", "")
        backtest_params = payload.get("backtest_params", {})
        optimization_targets = payload.get("optimization_targets", [])

        # Gather context
        strategy_context = await self._query_strategy_context(strategy_id)
        algorithms_context = await self._query_algorithms_context()

        planning_prompt = self._build_planning_prompt(
            strategy_id=strategy_id,
            algorithm_name=algorithm_name,
            instruments=instruments,
            start_date=start_date,
            end_date=end_date,
            backtest_params=backtest_params,
            optimization_targets=optimization_targets,
            strategy_context=strategy_context,
            algorithms_context=algorithms_context,
        )

        await self._memory.add_message(
            self.name(), task.id, "user", planning_prompt
        )

        # ── EXECUTING ── (Req 12.2)
        self._state_machine.transition(AgentState.EXECUTING, reason="running backtest")

        # Run initial backtest
        backtest_input = {
            "instruments": instruments,
            "start_date": start_date,
            "end_date": end_date,
        }
        if strategy_id:
            backtest_input["strategy_id"] = strategy_id
        if algorithm_name:
            backtest_input["algorithm_name"] = algorithm_name
        if backtest_params:
            backtest_input["backtest_params"] = backtest_params

        backtest_result = await self._tool_registry.invoke(
            "run_backtest", self.name(), backtest_input
        )

        await self._memory.add_message(
            self.name(),
            task.id,
            "assistant",
            f"Initial backtest result:\n{json.dumps(backtest_result, default=str)[:3000]}",
        )

        # LLM analysis of results (Req 12.3)
        analysis = await self._analyze_results(task, backtest_result)

        # Optional optimization (Req 12.5)
        optimization_result: Optional[dict] = None
        if optimization_targets:
            optimization_result = await self._run_optimization(
                task, strategy_id, algorithm_name, optimization_targets, backtest_result
            )

        # Optional walk-forward analysis (Req 12.6)
        walk_forward_result: Optional[dict] = None
        win_rate = self._extract_metric(backtest_result, "win_rate", 0.0)
        profit_factor = self._extract_metric(backtest_result, "profit_factor", 0.0)

        if (
            win_rate > WALK_FORWARD_WIN_RATE_THRESHOLD
            and profit_factor > WALK_FORWARD_PROFIT_FACTOR_THRESHOLD
        ):
            walk_forward_result = await self._run_walk_forward(
                task, strategy_id, algorithm_name
            )

        # Optional re-run with adjusted params (Req 12.7, 12.8)
        iteration_count = 1
        comparison: Optional[dict] = None
        original_result = backtest_result

        if analysis.get("optimization_suggestions") and iteration_count < self._max_iterations:
            rerun_result = await self._iterate_backtest(
                task=task,
                original_result=original_result,
                analysis=analysis,
                backtest_input=backtest_input,
                iteration_count=iteration_count,
            )
            if rerun_result is not None:
                backtest_result = rerun_result["backtest_result"]
                iteration_count = rerun_result["iteration_count"]
                comparison = rerun_result.get("comparison")
                # Re-analyze final result
                analysis = await self._analyze_results(task, backtest_result)

        # ── REVIEWING ── (Req 12.4, 12.9, 12.10)
        self._state_machine.transition(AgentState.REVIEWING, reason="finalizing analysis")

        grade = self._parse_grade(analysis.get("performance_grade", "C"))

        backtest_analysis = BacktestAnalysis(
            backtest_result=backtest_result,
            optimization_result=optimization_result,
            walk_forward_result=walk_forward_result,
            performance_grade=grade,
            strengths=analysis.get("strengths", []),
            weaknesses=analysis.get("weaknesses", []),
            optimization_suggestions=analysis.get("optimization_suggestions", []),
            recommended_next_steps=analysis.get("recommended_next_steps", []),
            iteration_count=iteration_count,
            comparison=comparison,
        )

        # Publish to agents:backtest_output stream (Req 12.9)
        self._stream_publisher.publish("agents:backtest_output", backtest_analysis)

        # Store in shared knowledge base (Req 12.9)
        tags = ["backtest", "analysis"]
        if algorithm_name:
            tags.append(algorithm_name)
        tags.extend(instruments)

        await self._memory.store_knowledge(
            key=f"backtest:{algorithm_name or strategy_id}:{int(time.time())}",
            value=backtest_analysis.model_dump(),
            source_agent=self.name(),
            tags=tags,
        )

        await self._memory.record_decision(
            agent_name=self.name(),
            task_id=task.id,
            decision_type="run_backtest",
            input_summary=(
                f"Strategy: {algorithm_name or strategy_id}, "
                f"Instruments: {instruments}, Period: {start_date} to {end_date}"
            ),
            output_summary=(
                f"Grade: {grade.value}, Iterations: {iteration_count}, "
                f"Win rate: {win_rate:.2%}, Profit factor: {profit_factor:.2f}"
            ),
            reasoning=json.dumps(analysis, default=str)[:500],
            outcome="success",
        )

        self._state_machine.transition(AgentState.COMPLETED, reason="analysis complete")

        return TaskResult(
            task_id=task.id,
            agent_name=self.name(),
            status=TaskStatus.COMPLETED,
            output={"backtest_analysis": backtest_analysis.model_dump()},
            duration_seconds=time.monotonic() - start_time,
        )

    # ------------------------------------------------------------------
    # LLM analysis (Req 12.3)
    # ------------------------------------------------------------------

    async def _analyze_results(self, task: AgentTask, backtest_result: dict) -> dict:
        """Use LLM to analyze backtest results and produce structured assessment."""
        analysis_prompt = (
            "Analyze the following backtest results and produce a structured "
            "assessment as a JSON object.\n\n"
            f"Backtest Results:\n{json.dumps(backtest_result, default=str)[:4000]}\n\n"
            "Provide: performance_grade (A/B/C/D/F), strengths, weaknesses, "
            "optimization_suggestions (with param name, current value, suggested range), "
            "and recommended_next_steps."
        )

        await self._memory.add_message(
            self.name(), task.id, "user", analysis_prompt
        )

        response = await self._llm_client.complete(
            agent_name=self.name(),
            system_prompt=self.get_system_prompt(),
            messages=[{"role": "user", "content": analysis_prompt}],
            temperature=0.5,
        )

        await self._memory.add_message(
            self.name(), task.id, "assistant", response.content
        )

        return self._parse_analysis_json(response.content)

    # ------------------------------------------------------------------
    # Optimization (Req 12.5)
    # ------------------------------------------------------------------

    async def _run_optimization(
        self,
        task: AgentTask,
        strategy_id: str,
        algorithm_name: str,
        optimization_targets: list,
        backtest_result: dict,
    ) -> Optional[dict]:
        """Run parameter optimization via the optimize_parameters tool."""
        try:
            opt_input: dict = {
                "optimization_targets": optimization_targets,
            }
            if strategy_id:
                opt_input["strategy_id"] = strategy_id
            if algorithm_name:
                opt_input["algorithm_name"] = algorithm_name

            result = await self._tool_registry.invoke(
                "optimize_parameters", self.name(), opt_input
            )

            await self._memory.add_message(
                self.name(),
                task.id,
                "assistant",
                f"Optimization result:\n{json.dumps(result, default=str)[:2000]}",
            )

            return result
        except Exception as e:
            logger.warning("Parameter optimization failed: %s", e)
            await self._memory.add_message(
                self.name(),
                task.id,
                "assistant",
                f"Optimization failed: {e}",
            )
            return None

    # ------------------------------------------------------------------
    # Walk-forward analysis (Req 12.6)
    # ------------------------------------------------------------------

    async def _run_walk_forward(
        self,
        task: AgentTask,
        strategy_id: str,
        algorithm_name: str,
    ) -> Optional[dict]:
        """Run walk-forward analysis when results are promising."""
        try:
            wf_input: dict = {}
            if strategy_id:
                wf_input["strategy_id"] = strategy_id
            if algorithm_name:
                wf_input["algorithm_name"] = algorithm_name

            result = await self._tool_registry.invoke(
                "walk_forward_analysis", self.name(), wf_input
            )

            await self._memory.add_message(
                self.name(),
                task.id,
                "assistant",
                f"Walk-forward result:\n{json.dumps(result, default=str)[:2000]}",
            )

            return result
        except Exception as e:
            logger.warning("Walk-forward analysis failed: %s", e)
            await self._memory.add_message(
                self.name(),
                task.id,
                "assistant",
                f"Walk-forward analysis failed: {e}",
            )
            return None

    # ------------------------------------------------------------------
    # Iterative re-run (Req 12.7, 12.8)
    # ------------------------------------------------------------------

    async def _iterate_backtest(
        self,
        task: AgentTask,
        original_result: dict,
        analysis: dict,
        backtest_input: dict,
        iteration_count: int,
    ) -> Optional[dict]:
        """Re-run backtest with adjusted params based on LLM suggestions.

        Iterates up to max_backtest_iterations. Returns dict with
        backtest_result, iteration_count, and comparison on success.
        """
        suggestions = analysis.get("optimization_suggestions", [])
        if not suggestions:
            return None

        current_result = original_result
        current_params = dict(backtest_input.get("backtest_params", {}))

        for suggestion in suggestions:
            if iteration_count >= self._max_iterations:
                logger.info(
                    "Reached max backtest iterations (%d), stopping",
                    self._max_iterations,
                )
                break

            param_name = suggestion.get("param", "")
            suggested_range = suggestion.get("suggested_range", [])
            if not param_name or not suggested_range:
                continue

            # Use midpoint of suggested range
            if len(suggested_range) >= 2:
                new_value = (suggested_range[0] + suggested_range[1]) / 2
            else:
                new_value = suggested_range[0]

            current_params[param_name] = new_value
            iteration_count += 1

            adjusted_input = dict(backtest_input)
            adjusted_input["backtest_params"] = current_params

            try:
                new_result = await self._tool_registry.invoke(
                    "run_backtest", self.name(), adjusted_input
                )

                await self._memory.add_message(
                    self.name(),
                    task.id,
                    "assistant",
                    f"Re-run iteration {iteration_count} "
                    f"(adjusted {param_name}={new_value}):\n"
                    f"{json.dumps(new_result, default=str)[:2000]}",
                )

                current_result = new_result
            except Exception as e:
                logger.warning(
                    "Backtest re-run iteration %d failed: %s", iteration_count, e
                )
                break

        # Build comparison between original and final result
        comparison = self._build_comparison(original_result, current_result)

        return {
            "backtest_result": current_result,
            "iteration_count": iteration_count,
            "comparison": comparison,
        }

    # ------------------------------------------------------------------
    # Tool invocation helpers
    # ------------------------------------------------------------------

    async def _query_strategy_context(self, strategy_id: str) -> str:
        """Query strategy config via the get_strategy_config tool."""
        if not strategy_id:
            return "No strategy ID provided."
        try:
            result = await self._tool_registry.invoke(
                "get_strategy_config", self.name(), {"strategy_id": strategy_id}
            )
            return json.dumps(result, default=str)[:2000]
        except Exception as e:
            logger.warning("Failed to get strategy config: %s", e)
            return "Strategy config unavailable."

    async def _query_algorithms_context(self) -> str:
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
    def _build_planning_prompt(
        strategy_id: str,
        algorithm_name: str,
        instruments: list[str],
        start_date: str,
        end_date: str,
        backtest_params: dict,
        optimization_targets: list,
        strategy_context: str,
        algorithms_context: str,
    ) -> str:
        """Build the planning prompt for run_backtest tasks."""
        parts = [
            f"Run a comprehensive backtest analysis for strategy.",
        ]
        if strategy_id:
            parts.append(f"Strategy ID: {strategy_id}")
        if algorithm_name:
            parts.append(f"Algorithm: {algorithm_name}")
        if instruments:
            parts.append(f"Instruments: {', '.join(instruments)}")
        if start_date and end_date:
            parts.append(f"Period: {start_date} to {end_date}")
        if backtest_params:
            parts.append(
                f"Backtest parameters: {json.dumps(backtest_params, default=str)}"
            )
        if optimization_targets:
            parts.append(
                f"Optimization targets: {', '.join(optimization_targets)}"
            )

        parts.append(f"\nStrategy configuration:\n{strategy_context}")
        parts.append(f"\nAvailable algorithms:\n{algorithms_context}")
        parts.append(
            "\nAnalyze the backtest results thoroughly. Identify strengths, "
            "weaknesses, and suggest specific parameter optimizations."
        )
        return "\n".join(parts)

    # ------------------------------------------------------------------
    # Parsing helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_analysis_json(llm_content: str) -> dict:
        """Parse structured analysis JSON from LLM response."""
        raw = llm_content.strip()

        # Strip markdown code fences if present
        if "```json" in raw:
            raw = raw.split("```json", 1)[1]
            raw = raw.split("```", 1)[0]
        elif "```" in raw:
            raw = raw.split("```", 1)[1]
            raw = raw.split("```", 1)[0]

        try:
            return json.loads(raw.strip())
        except (json.JSONDecodeError, Exception) as e:
            logger.warning("Failed to parse analysis JSON: %s", e)
            return {
                "performance_grade": "C",
                "strengths": [],
                "weaknesses": ["Unable to parse LLM analysis"],
                "optimization_suggestions": [],
                "recommended_next_steps": ["Manual review recommended"],
            }

    @staticmethod
    def _parse_grade(grade_str: str) -> PerformanceGrade:
        """Parse a PerformanceGrade from a string value."""
        grade_str = grade_str.strip().upper()
        try:
            return PerformanceGrade(grade_str)
        except ValueError:
            return PerformanceGrade.C

    @staticmethod
    def _extract_metric(result: dict, metric_name: str, default: float) -> float:
        """Extract a numeric metric from a backtest result dict."""
        value = result.get(metric_name, default)
        if isinstance(value, (int, float)):
            return float(value)
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _build_comparison(original: dict, final: dict) -> dict:
        """Build a before/after comparison of key metrics."""
        metrics = ["win_rate", "profit_factor", "max_drawdown", "sharpe_ratio", "expectancy"]
        comparison: dict = {}
        for metric in metrics:
            orig_val = original.get(metric)
            final_val = final.get(metric)
            if orig_val is not None and final_val is not None:
                try:
                    comparison[metric] = {
                        "original": float(orig_val),
                        "adjusted": float(final_val),
                        "change": float(final_val) - float(orig_val),
                    }
                except (TypeError, ValueError):
                    pass
        return comparison
