"""Forward Test Agent — deploys strategies in forward-test mode and monitors performance.

Supports the `forward_test` task type:
- Deploy in forward_test mode → monitoring loop → LLM evaluation
  → promotion/demotion/extension decision
- Drift detection: flag when win_rate deviation > drift_threshold_pct (default 15%)
- Extension limit: max_extensions (default 3)
- Trigger ApprovalGate on PROMOTE recommendation
- Output ForwardTestReport, publish to agents:forward_test_output + knowledge base

Requirements: 13.1–13.9
"""

import asyncio
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
    ForwardTestReport,
    PromotionRecommendation,
    TaskResult,
    TaskStatus,
)
from src.agents.state_machine import AgentStateMachine
from src.agents.streams import AgentStreamPublisher
from src.agents.tools.registry import ToolRegistry

logger = logging.getLogger("strategy_engine.agents.forward_test")

DEFAULT_EVALUATION_PERIOD_DAYS = 14
DEFAULT_MONITORING_INTERVAL_MINUTES = 60
DEFAULT_EXTENSION_PERIOD_DAYS = 7
DEFAULT_MAX_EXTENSIONS = 3
DEFAULT_DRIFT_THRESHOLD_PCT = 15.0

FORWARD_TEST_SYSTEM_PROMPT = """\
You are a senior quantitative analyst for Clartt Capital. Your role is to \
evaluate forward-test (paper trading) results, compare them against backtest \
baselines, detect performance drift, and make data-driven promotion or \
demotion decisions.

Domain expertise:
- Forward testing methodology: out-of-sample validation, live market conditions, \
slippage and spread impact, execution quality assessment
- Performance drift detection: comparing forward-test metrics against backtest \
baselines, identifying regime changes, distinguishing noise from signal
- Promotion criteria: consistent performance across market conditions, \
acceptable drawdown, sufficient sample size, stable win rate
- Demotion triggers: significant performance degradation, excessive drawdown, \
regime mismatch, insufficient trade volume

Supported instruments: US30 (Dow Jones), XAUUSD (Gold), Volatility 75 Index, \
Volatility 25 Index.

When evaluating forward-test results, output valid JSON matching this schema:
{
  "total_signals_generated": integer,
  "total_trades_simulated": integer,
  "performance_metrics": {
    "win_rate": float,
    "profit_factor": float,
    "max_drawdown": float,
    "total_pnl": float
  },
  "backtest_comparison": {
    "win_rate_drift": float,
    "profit_factor_drift": float,
    "max_drawdown_drift": float
  },
  "market_conditions_during_test": "description string",
  "promotion_recommendation": "PROMOTE" | "DEMOTE" | "EXTEND",
  "recommendation_reasoning": "detailed reasoning string"
}

Decision framework:
- PROMOTE: Forward-test metrics within acceptable drift of backtest, \
sufficient sample size (>= 20 trades), stable performance across the period
- DEMOTE: Significant negative drift (win_rate drop > 15%), profit_factor < 1.0, \
or max_drawdown exceeds backtest by > 50%
- EXTEND: Inconclusive results due to insufficient trades, mixed signals, \
or moderate drift that needs more data

Be conservative with PROMOTE recommendations. Prefer EXTEND over PROMOTE \
when in doubt. Always flag drift concerns explicitly.\
"""


class ForwardTestAgent(Agent):
    """Deploys strategies in forward-test mode and monitors real-time performance.

    Constructor args:
        llm_client: LLMClient for LLM completions.
        tool_registry: ToolRegistry for invoking platform tools.
        memory: AgentMemory for conversation history and knowledge base.
        stream_publisher: AgentStreamPublisher for publishing to Redis streams.
        approval_manager: Optional ApprovalGateManager for PROMOTE approval gates.
        backend_url: Backend API base URL (for state machine persistence).
        max_extensions: Max evaluation period extensions (default 3).
        drift_threshold_pct: Win rate drift threshold percentage (default 15%).
        monitoring_interval_minutes: Monitoring check interval (default 60).
    """

    def __init__(
        self,
        llm_client: LLMClient,
        tool_registry: ToolRegistry,
        memory: AgentMemory,
        stream_publisher: AgentStreamPublisher,
        approval_manager: Optional[object] = None,
        backend_url: str = "http://backend:3000",
        max_extensions: int = DEFAULT_MAX_EXTENSIONS,
        drift_threshold_pct: float = DEFAULT_DRIFT_THRESHOLD_PCT,
        monitoring_interval_minutes: int = DEFAULT_MONITORING_INTERVAL_MINUTES,
    ) -> None:
        self._llm_client = llm_client
        self._tool_registry = tool_registry
        self._memory = memory
        self._stream_publisher = stream_publisher
        self._approval_manager = approval_manager
        self._state_machine = AgentStateMachine("forward_test", backend_url)
        self._max_extensions = max_extensions
        self._drift_threshold_pct = drift_threshold_pct
        self._monitoring_interval_minutes = monitoring_interval_minutes

    def name(self) -> str:
        return "forward_test"

    def description(self) -> str:
        return (
            "Deploys strategies in forward-test mode, monitors real-time performance, "
            "detects drift against backtest baselines, and makes promotion/demotion "
            "decisions with human approval gates."
        )

    def supported_task_types(self) -> list[str]:
        return ["forward_test"]

    def supported_tools(self) -> list[str]:
        return ["get_strategy_config", "create_strategy", "query_events", "query_signals"]

    def get_system_prompt(self) -> str:
        return FORWARD_TEST_SYSTEM_PROMPT

    async def run(self, task: AgentTask) -> TaskResult:
        """Execute the forward test agent's reasoning loop.

        Handles `forward_test` task type following
        PLANNING → EXECUTING → REVIEWING flow.
        """
        start_time = time.monotonic()
        try:
            if task.type != "forward_test":
                return TaskResult(
                    task_id=task.id,
                    agent_name=self.name(),
                    status=TaskStatus.FAILED,
                    error=f"Unsupported task type: {task.type}",
                    duration_seconds=time.monotonic() - start_time,
                )
            return await self._run_forward_test(task, start_time)
        except Exception as e:
            duration = time.monotonic() - start_time
            logger.error("Forward test agent failed on task %s: %s", task.id, e)
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
    # forward_test flow (Req 13.1–13.9)
    # ------------------------------------------------------------------

    async def _run_forward_test(
        self, task: AgentTask, start_time: float
    ) -> TaskResult:
        """Forward test flow:
        1. PLANNING: Parse payload, deploy strategy in forward_test mode.
        2. EXECUTING: Monitoring loop — query events, compute metrics,
           check for drift.
        3. REVIEWING: LLM evaluation → promotion/demotion/extension decision.
           Trigger ApprovalGate on PROMOTE. Publish ForwardTestReport.
        """
        payload = task.payload

        # ── PLANNING ── (Req 13.1, 13.2)
        self._state_machine.transition(AgentState.PLANNING, reason="forward_test")

        strategy_id = payload.get("strategy_id", "")
        algorithm_name = payload.get("algorithm_name", "")
        instruments = payload.get("instruments", [])
        evaluation_period_days = payload.get(
            "evaluation_period_days", DEFAULT_EVALUATION_PERIOD_DAYS
        )
        promotion_criteria = payload.get("promotion_criteria", {})
        backtest_analysis = payload.get("backtest_analysis", {})

        # Deploy strategy in forward_test mode (Req 13.2)
        deployed_strategy = await self._deploy_forward_test(
            task, strategy_id, algorithm_name, instruments
        )

        if deployed_strategy:
            strategy_id = deployed_strategy.get("id", strategy_id)

        await self._memory.add_message(
            self.name(),
            task.id,
            "user",
            f"Forward test started for strategy '{algorithm_name}' "
            f"(id={strategy_id}) on instruments {instruments}. "
            f"Evaluation period: {evaluation_period_days} days.",
        )

        # ── EXECUTING ── (Req 13.3)
        self._state_machine.transition(
            AgentState.EXECUTING, reason="monitoring forward test"
        )

        # Monitoring loop: collect performance data over the evaluation period
        # In production this would run over days; here we simulate by querying
        # accumulated events/signals for the strategy.
        monitoring_data = await self._run_monitoring_loop(
            task=task,
            strategy_id=strategy_id,
            algorithm_name=algorithm_name,
            instruments=instruments,
            evaluation_period_days=evaluation_period_days,
        )

        # Extract backtest baseline metrics for drift comparison
        backtest_metrics = self._extract_backtest_metrics(backtest_analysis)

        # ── REVIEWING ── (Req 13.4, 13.5, 13.6, 13.7, 13.8)
        self._state_machine.transition(AgentState.REVIEWING, reason="evaluating results")

        # LLM evaluation (Req 13.4)
        evaluation = await self._evaluate_results(
            task=task,
            monitoring_data=monitoring_data,
            backtest_metrics=backtest_metrics,
            promotion_criteria=promotion_criteria,
            evaluation_period_days=evaluation_period_days,
        )

        # Detect drift (Req 13.8)
        drift_detected = self._detect_drift(
            evaluation.get("performance_metrics", {}),
            backtest_metrics,
        )

        # Override recommendation if drift detected
        recommendation_str = evaluation.get(
            "promotion_recommendation", "EXTEND"
        )
        if drift_detected and recommendation_str == "PROMOTE":
            recommendation_str = "EXTEND"
            evaluation["recommendation_reasoning"] = (
                f"Drift detected (threshold {self._drift_threshold_pct}%). "
                + evaluation.get("recommendation_reasoning", "")
            )

        recommendation = self._parse_recommendation(recommendation_str)
        extensions_used = payload.get("extensions_used", 0)

        # Handle recommendation (Req 13.5, 13.6, 13.7)
        if recommendation == PromotionRecommendation.EXTEND:
            if extensions_used >= self._max_extensions:
                recommendation = PromotionRecommendation.DEMOTE
                evaluation["recommendation_reasoning"] = (
                    f"Max extensions ({self._max_extensions}) reached. "
                    + evaluation.get("recommendation_reasoning", "")
                )
            else:
                extensions_used += 1

        # Trigger ApprovalGate on PROMOTE (Req 13.5)
        if recommendation == PromotionRecommendation.PROMOTE:
            await self._request_promotion_approval(task, algorithm_name, strategy_id)

        # Handle DEMOTE (Req 13.6)
        if recommendation == PromotionRecommendation.DEMOTE:
            await self._handle_demotion(
                task, strategy_id, algorithm_name,
                evaluation.get("recommendation_reasoning", "Performance below threshold"),
            )

        # Build ForwardTestReport (Req 13.4)
        report = ForwardTestReport(
            strategy_id=strategy_id,
            algorithm_name=algorithm_name,
            evaluation_period_days=evaluation_period_days,
            total_signals_generated=evaluation.get("total_signals_generated", 0),
            total_trades_simulated=evaluation.get("total_trades_simulated", 0),
            performance_metrics=evaluation.get("performance_metrics", {}),
            backtest_comparison=evaluation.get("backtest_comparison", {}),
            market_conditions_during_test=evaluation.get(
                "market_conditions_during_test", "unknown"
            ),
            promotion_recommendation=recommendation,
            recommendation_reasoning=evaluation.get(
                "recommendation_reasoning", ""
            ),
            extensions_used=extensions_used,
        )

        # Publish to agents:forward_test_output stream (Req 13.9)
        self._stream_publisher.publish("agents:forward_test_output", report)

        # Store in shared knowledge base (Req 13.9)
        tags = ["forward_test", "report"]
        if algorithm_name:
            tags.append(algorithm_name)
        tags.extend(instruments)

        await self._memory.store_knowledge(
            key=f"forward_test:{algorithm_name or strategy_id}:{int(time.time())}",
            value=report.model_dump(),
            source_agent=self.name(),
            tags=tags,
        )

        await self._memory.record_decision(
            agent_name=self.name(),
            task_id=task.id,
            decision_type="forward_test",
            input_summary=(
                f"Strategy: {algorithm_name} (id={strategy_id}), "
                f"Period: {evaluation_period_days}d, Instruments: {instruments}"
            ),
            output_summary=(
                f"Recommendation: {recommendation.value}, "
                f"Extensions: {extensions_used}/{self._max_extensions}, "
                f"Drift detected: {drift_detected}"
            ),
            reasoning=evaluation.get("recommendation_reasoning", "")[:500],
            outcome="success",
        )

        self._state_machine.transition(AgentState.COMPLETED, reason="evaluation complete")

        return TaskResult(
            task_id=task.id,
            agent_name=self.name(),
            status=TaskStatus.COMPLETED,
            output={"forward_test_report": report.model_dump()},
            duration_seconds=time.monotonic() - start_time,
        )

    # ------------------------------------------------------------------
    # Deploy forward test (Req 13.2)
    # ------------------------------------------------------------------

    async def _deploy_forward_test(
        self,
        task: AgentTask,
        strategy_id: str,
        algorithm_name: str,
        instruments: list[str],
    ) -> Optional[dict]:
        """Create or update strategy in forward_test mode via create_strategy tool."""
        try:
            strategy_payload: dict = {
                "algorithm": algorithm_name,
                "mode": "forward_test",
                "instruments": instruments,
                "name": f"FT_{algorithm_name}",
            }
            if strategy_id:
                strategy_payload["id"] = strategy_id

            result = await self._tool_registry.invoke(
                "create_strategy", self.name(), strategy_payload
            )

            await self._memory.add_message(
                self.name(),
                task.id,
                "assistant",
                f"Deployed strategy in forward_test mode: "
                f"{json.dumps(result, default=str)[:1000]}",
            )

            return result
        except Exception as e:
            logger.warning("Failed to deploy forward test strategy: %s", e)
            await self._memory.add_message(
                self.name(),
                task.id,
                "assistant",
                f"Failed to deploy forward test: {e}",
            )
            return None

    # ------------------------------------------------------------------
    # Monitoring loop (Req 13.3)
    # ------------------------------------------------------------------

    async def _run_monitoring_loop(
        self,
        task: AgentTask,
        strategy_id: str,
        algorithm_name: str,
        instruments: list[str],
        evaluation_period_days: int,
    ) -> dict:
        """Run the monitoring loop, querying events and signals periodically.

        In a production deployment this would run over the full evaluation
        period. For the agent execution context, we perform a single
        comprehensive query of accumulated data and simulate the monitoring
        interval pattern.
        """
        all_events: list = []
        all_signals: list = []

        # Query events for the strategy (Req 13.3)
        try:
            events_input: dict = {}
            if strategy_id:
                events_input["aggregate_id"] = strategy_id
            events_result = await self._tool_registry.invoke(
                "query_events", self.name(), events_input
            )
            if isinstance(events_result, list):
                all_events = events_result
            elif isinstance(events_result, dict):
                all_events = events_result.get("events", [])
        except Exception as e:
            logger.warning("Failed to query events: %s", e)

        # Query signals for the strategy
        try:
            signals_input: dict = {}
            if strategy_id:
                signals_input["strategy_id"] = strategy_id
            if instruments:
                signals_input["instrument"] = instruments[0]
            signals_result = await self._tool_registry.invoke(
                "query_signals", self.name(), signals_input
            )
            if isinstance(signals_result, list):
                all_signals = signals_result
            elif isinstance(signals_result, dict):
                all_signals = signals_result.get("signals", [])
        except Exception as e:
            logger.warning("Failed to query signals: %s", e)

        # Compute running metrics from collected data
        metrics = self._compute_metrics(all_events, all_signals)

        await self._memory.add_message(
            self.name(),
            task.id,
            "assistant",
            f"Monitoring data collected: {len(all_events)} events, "
            f"{len(all_signals)} signals. Metrics: {json.dumps(metrics, default=str)}",
        )

        return {
            "events": all_events,
            "signals": all_signals,
            "metrics": metrics,
            "total_events": len(all_events),
            "total_signals": len(all_signals),
        }

    # ------------------------------------------------------------------
    # LLM evaluation (Req 13.4)
    # ------------------------------------------------------------------

    async def _evaluate_results(
        self,
        task: AgentTask,
        monitoring_data: dict,
        backtest_metrics: dict,
        promotion_criteria: dict,
        evaluation_period_days: int,
    ) -> dict:
        """Use LLM to evaluate forward test results and produce recommendation."""
        eval_prompt = (
            "Evaluate the following forward-test results and produce a "
            "ForwardTestReport as a JSON object.\n\n"
            f"Monitoring Data Summary:\n"
            f"- Total events: {monitoring_data.get('total_events', 0)}\n"
            f"- Total signals: {monitoring_data.get('total_signals', 0)}\n"
            f"- Running metrics: {json.dumps(monitoring_data.get('metrics', {}), default=str)}\n\n"
            f"Backtest Baseline Metrics:\n{json.dumps(backtest_metrics, default=str)}\n\n"
            f"Promotion Criteria:\n{json.dumps(promotion_criteria, default=str)}\n\n"
            f"Evaluation Period: {evaluation_period_days} days\n\n"
            f"Drift Threshold: {self._drift_threshold_pct}%\n\n"
            "Assess performance, compare against backtest baseline, detect any "
            "drift, and recommend PROMOTE, DEMOTE, or EXTEND with detailed reasoning."
        )

        await self._memory.add_message(
            self.name(), task.id, "user", eval_prompt
        )

        response = await self._llm_client.complete(
            agent_name=self.name(),
            system_prompt=self.get_system_prompt(),
            messages=[{"role": "user", "content": eval_prompt}],
            temperature=0.5,
        )

        await self._memory.add_message(
            self.name(), task.id, "assistant", response.content
        )

        return self._parse_evaluation_json(response.content)

    # ------------------------------------------------------------------
    # Drift detection (Req 13.8)
    # ------------------------------------------------------------------

    def _detect_drift(
        self,
        forward_metrics: dict,
        backtest_metrics: dict,
    ) -> bool:
        """Detect significant performance drift between forward and backtest results.

        Returns True if win_rate deviation exceeds drift_threshold_pct.
        """
        forward_wr = forward_metrics.get("win_rate")
        backtest_wr = backtest_metrics.get("win_rate")

        if forward_wr is None or backtest_wr is None:
            return False

        try:
            deviation = abs(float(forward_wr) - float(backtest_wr)) * 100
            threshold = self._drift_threshold_pct
            if deviation > threshold:
                logger.info(
                    "Drift detected: forward win_rate=%.2f, backtest win_rate=%.2f, "
                    "deviation=%.1f%% > threshold=%.1f%%",
                    float(forward_wr),
                    float(backtest_wr),
                    deviation,
                    threshold,
                )
                return True
        except (TypeError, ValueError):
            pass

        return False

    # ------------------------------------------------------------------
    # Promotion approval (Req 13.5)
    # ------------------------------------------------------------------

    async def _request_promotion_approval(
        self,
        task: AgentTask,
        algorithm_name: str,
        strategy_id: str,
    ) -> None:
        """Trigger ApprovalGate for PROMOTE recommendation."""
        if self._approval_manager is None:
            logger.info(
                "No approval manager configured — PROMOTE for '%s' logged only",
                algorithm_name,
            )
            return

        try:
            action_description = (
                f"Promote strategy '{algorithm_name}' (id={strategy_id}) "
                f"from forward_test to LIVE mode"
            )
            await self._approval_manager.request_approval(
                agent_name=self.name(),
                task_id=task.id,
                action_description=action_description,
                state_machine=self._state_machine,
            )
            logger.info(
                "Approval requested for promoting '%s' to LIVE", algorithm_name
            )
        except Exception as e:
            logger.warning("Failed to request promotion approval: %s", e)

    # ------------------------------------------------------------------
    # Demotion handling (Req 13.6)
    # ------------------------------------------------------------------

    async def _handle_demotion(
        self,
        task: AgentTask,
        strategy_id: str,
        algorithm_name: str,
        reason: str,
    ) -> None:
        """Handle DEMOTE recommendation — store demotion reason in knowledge base."""
        await self._memory.store_knowledge(
            key=f"demotion:{algorithm_name or strategy_id}:{int(time.time())}",
            value={
                "strategy_id": strategy_id,
                "algorithm_name": algorithm_name,
                "reason": reason,
                "action": "demoted",
            },
            source_agent=self.name(),
            tags=["demotion", "forward_test", algorithm_name],
        )

        logger.info(
            "Strategy '%s' (id=%s) demoted: %s",
            algorithm_name,
            strategy_id,
            reason,
        )

    # ------------------------------------------------------------------
    # Metric computation helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_metrics(events: list, signals: list) -> dict:
        """Compute running performance metrics from events and signals."""
        total_trades = 0
        winning_trades = 0
        total_pnl = 0.0
        max_drawdown = 0.0
        peak_pnl = 0.0

        for event in events:
            if not isinstance(event, dict):
                continue
            event_type = event.get("event_type", "")
            event_payload = event.get("payload", {})
            if isinstance(event_payload, str):
                try:
                    event_payload = json.loads(event_payload)
                except (json.JSONDecodeError, TypeError):
                    event_payload = {}

            if "Trade" in event_type or "trade" in event_type:
                total_trades += 1
                pnl = event_payload.get("profit_loss", 0)
                try:
                    pnl = float(pnl)
                except (TypeError, ValueError):
                    pnl = 0.0

                total_pnl += pnl
                if pnl > 0:
                    winning_trades += 1

                if total_pnl > peak_pnl:
                    peak_pnl = total_pnl
                drawdown = peak_pnl - total_pnl
                if drawdown > max_drawdown:
                    max_drawdown = drawdown

        win_rate = winning_trades / total_trades if total_trades > 0 else 0.0

        return {
            "win_rate": round(win_rate, 4),
            "total_trades": total_trades,
            "winning_trades": winning_trades,
            "total_pnl": round(total_pnl, 2),
            "max_drawdown": round(max_drawdown, 2),
            "total_signals": len(signals),
        }

    @staticmethod
    def _extract_backtest_metrics(backtest_analysis: dict) -> dict:
        """Extract baseline metrics from a BacktestAnalysis payload."""
        if not backtest_analysis:
            return {}

        # BacktestAnalysis may contain backtest_result with metrics
        bt_result = backtest_analysis.get("backtest_result", {})
        if not bt_result:
            bt_result = backtest_analysis

        return {
            "win_rate": bt_result.get("win_rate"),
            "profit_factor": bt_result.get("profit_factor"),
            "max_drawdown": bt_result.get("max_drawdown"),
            "sharpe_ratio": bt_result.get("sharpe_ratio"),
            "expectancy": bt_result.get("expectancy"),
            "total_trades": bt_result.get("total_trades"),
        }

    # ------------------------------------------------------------------
    # Parsing helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_evaluation_json(llm_content: str) -> dict:
        """Parse structured evaluation JSON from LLM response."""
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
            logger.warning("Failed to parse evaluation JSON: %s", e)
            return {
                "total_signals_generated": 0,
                "total_trades_simulated": 0,
                "performance_metrics": {},
                "backtest_comparison": {},
                "market_conditions_during_test": "unknown",
                "promotion_recommendation": "EXTEND",
                "recommendation_reasoning": "Unable to parse LLM evaluation",
            }

    @staticmethod
    def _parse_recommendation(rec_str: str) -> PromotionRecommendation:
        """Parse a PromotionRecommendation from a string value."""
        rec_str = rec_str.strip().upper()
        try:
            return PromotionRecommendation(rec_str)
        except ValueError:
            return PromotionRecommendation.EXTEND
