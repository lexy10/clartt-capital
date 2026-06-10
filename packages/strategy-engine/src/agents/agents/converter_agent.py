"""Converter Agent — converts strategy descriptions into validated StrategyAlgorithm Python code.

Supports the `convert_strategy` task type:
- LLM code generation → write file → validate (syntax, import, interface) → smoke test
- Retry loop up to max_conversion_retries (default 5) on validation/smoke test failure
- All generated code executed through CodeSandbox

Requirements: 11.1–11.10
"""

import json
import logging
import time
import traceback
from datetime import datetime, timezone
from typing import Optional

from src.agents.base import Agent
from src.agents.llm_client import LLMClient
from src.agents.memory import AgentMemory
from src.agents.metrics import (
    agent_config_refinement_duration_seconds,
    agent_config_refinements_total,
    agent_refinements_total,
    agent_refinement_duration_seconds,
)
from src.agents.models import (
    AgentState,
    AgentTask,
    ConfigRefinementResult,
    ConversionResult,
    RefinementResult,
    TaskResult,
    TaskStatus,
)
from src.agents.sandbox import CodeSandbox
from src.agents.state_machine import AgentStateMachine
from src.agents.streams import AgentStreamPublisher
from src.agents.tools.registry import ToolRegistry

logger = logging.getLogger("strategy_engine.agents.converter")

DEFAULT_MAX_CONVERSION_RETRIES = 5

EVENTS_STREAM = "agents:events"
ACTIVITY_CHANNEL = "agents:activity"

# StrategyAlgorithm base class source included in the system prompt (Req 11.10)
_STRATEGY_ALGORITHM_SOURCE = '''\
from abc import ABC, abstractmethod
from src.models import Candle, Signal, StrategyConfig

class StrategyAlgorithm(ABC):
    """Base class for all strategy algorithm plugins."""

    @staticmethod
    @abstractmethod
    def name() -> str:
        """Unique identifier for this algorithm (e.g., 'my_strategy')."""

    @staticmethod
    @abstractmethod
    def description() -> str:
        """Human-readable description of the algorithm."""

    @staticmethod
    @abstractmethod
    def default_params() -> dict:
        """Default algorithm-specific parameters as a plain dict."""

    @staticmethod
    @abstractmethod
    def param_schema() -> dict:
        """JSON Schema describing the algorithm-specific parameters."""

    @abstractmethod
    def analyze(
        self,
        entry_candles: list[Candle],
        structure_candles: list[Candle],
        trend_candles: list[Candle],
        config: StrategyConfig,
    ) -> list[Signal]:
        """Run analysis and return generated signals (may be empty list)."""
'''

# Example algorithm structure included in the system prompt (Req 11.10)
_EXAMPLE_ALGORITHM = '''\
"""Example: Simple EMA crossover strategy algorithm."""

import uuid
from datetime import datetime, timezone

from src.models import Candle, Signal, SignalDirection, StrategyConfig, BOSType
from src.strategy.base import StrategyAlgorithm
from src.strategy.signal_helpers import build_signal


class EmaCrossoverAlgorithm(StrategyAlgorithm):
    """Simple EMA crossover strategy."""

    @staticmethod
    def name() -> str:
        return "ema_crossover"

    @staticmethod
    def description() -> str:
        return "Simple EMA crossover strategy"

    @staticmethod
    def default_params() -> dict:
        return {
            "fast_period": 9,
            "slow_period": 21,
            "atr_period": 14,
            "atr_sl_multiplier": 1.5,
            "reward_risk_ratio": 2.0,
        }

    @staticmethod
    def param_schema() -> dict:
        return {
            "fast_period": {"type": "integer", "minimum": 2, "maximum": 50},
            "slow_period": {"type": "integer", "minimum": 5, "maximum": 200},
            "atr_period": {"type": "integer", "minimum": 2, "maximum": 50},
            "atr_sl_multiplier": {"type": "number", "minimum": 0.5, "maximum": 5.0},
            "reward_risk_ratio": {"type": "number", "minimum": 1.0, "maximum": 10.0},
        }

    def analyze(
        self,
        entry_candles: list[Candle],
        structure_candles: list[Candle],
        trend_candles: list[Candle],
        config: StrategyConfig,
    ) -> list[Signal]:
        params = {**self.default_params(), **config.algorithm_params}
        fast_period = params["fast_period"]
        slow_period = params["slow_period"]

        if len(structure_candles) < slow_period + 1:
            return []

        closes = [c.close for c in structure_candles]

        # Compute EMAs
        fast_ema = _compute_ema(closes, fast_period)
        slow_ema = _compute_ema(closes, slow_period)

        n = len(closes)
        bullish = fast_ema[n - 1] > slow_ema[n - 1] and fast_ema[n - 2] <= slow_ema[n - 2]
        bearish = fast_ema[n - 1] < slow_ema[n - 1] and fast_ema[n - 2] >= slow_ema[n - 2]

        if not bullish and not bearish:
            return []

        direction = SignalDirection.BUY if bullish else SignalDirection.SELL
        entry_price = closes[-1]

        # ATR for SL/TP
        atr = _compute_atr(
            [c.high for c in structure_candles],
            [c.low for c in structure_candles],
            closes,
            params["atr_period"],
        )
        if not atr or atr[-1] == 0:
            return []

        sl_dist = params["atr_sl_multiplier"] * atr[-1]
        if direction == SignalDirection.BUY:
            stop_loss = entry_price - sl_dist
            take_profit = entry_price + params["reward_risk_ratio"] * sl_dist
        else:
            stop_loss = entry_price + sl_dist
            take_profit = entry_price - params["reward_risk_ratio"] * sl_dist

        signal = build_signal(
            instrument=config.instruments[0] if config.instruments else "",
            direction=direction,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            config=config,
            candles=structure_candles,
            timestamp=datetime.now(timezone.utc),
            order_block_id=str(uuid.uuid4()),
            extra_metadata={},
            confidence_score=0.6,
        )
        return [signal]


def _compute_ema(closes: list[float], period: int) -> list[float]:
    if not closes:
        return []
    mult = 2.0 / (period + 1)
    ema = [closes[0]]
    for i in range(1, len(closes)):
        ema.append(closes[i] * mult + ema[-1] * (1 - mult))
    return ema


def _compute_atr(highs, lows, closes, period):
    n = len(closes)
    if n < period + 1:
        return []
    tr_list = []
    for i in range(1, n):
        tr = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
        tr_list.append(tr)
    if len(tr_list) < period:
        return []
    atr = sum(tr_list[:period]) / period
    result = [atr]
    for i in range(period, len(tr_list)):
        atr = (atr * (period - 1) + tr_list[i]) / period
        result.append(atr)
    return result
'''


CONVERTER_SYSTEM_PROMPT = f"""\
You are a senior Python developer for Clartt Capital. Your role is to convert \
trading strategy descriptions (PineScript, natural language, pseudocode) into \
production-quality Python code that implements the StrategyAlgorithm interface.

## StrategyAlgorithm Base Class

```python
{_STRATEGY_ALGORITHM_SOURCE}
```

## Example Algorithm

```python
{_EXAMPLE_ALGORITHM}
```

## Key Models

- `Candle`: has fields `open`, `high`, `low`, `close`, `volume`, `timestamp`, \
`instrument`, `timeframe`
- `Signal`: trading signal with `instrument`, `direction`, `entry_price`, \
`stop_loss`, `take_profit`, `confidence_score`, etc.
- `SignalDirection`: enum with `BUY` and `SELL`
- `StrategyConfig`: has `instruments`, `algorithm_params`, `min_confidence_score`, \
`timeframes`, `mode`, `risk_settings`, etc.
- `BOSType`: enum with `BULLISH` and `BEARISH`

## Imports Available

```python
from src.models import Candle, Signal, SignalDirection, StrategyConfig, BOSType, Timeframe
from src.strategy.base import StrategyAlgorithm
from src.strategy.signal_helpers import build_signal
```

## Rules

1. The algorithm class MUST subclass `StrategyAlgorithm` from `src.strategy.base`.
2. Implement ALL five abstract methods: `name()`, `description()`, `default_params()`, \
`param_schema()`, `analyze()`.
3. `name()` and `description()` are `@staticmethod` returning `str`.
4. `default_params()` is `@staticmethod` returning `dict`.
5. `param_schema()` is `@staticmethod` returning `dict` (JSON Schema).
6. `analyze()` receives `entry_candles`, `structure_candles`, `trend_candles`, \
`config` and returns `list[Signal]` (may be empty).
7. Use `build_signal()` from `src.strategy.signal_helpers` to construct signals.
8. Include all indicator computation as module-level helper functions.
9. Use only standard library + numpy/pandas if needed. Do NOT import external packages.
10. Handle edge cases: insufficient candles, zero ATR, division by zero.
11. Output ONLY the complete Python file content — no markdown fences, no explanation.
12. The algorithm name in `name()` MUST be in snake_case.
"""


REFINEMENT_SYSTEM_PROMPT_ADDITION = """\

## Refinement Mode

You are refining an EXISTING algorithm, not creating one from scratch.
You will receive:
- The current source code of the algorithm
- Backtest performance metrics (win rate, profit factor, max drawdown, Sharpe ratio)
- Optional refinement hints describing specific areas to improve

Your goal is to make targeted, surgical improvements to the algorithm code.
Do NOT rewrite the entire algorithm from scratch. Focus on:
1. Improving entry/exit logic based on performance weaknesses
2. Adjusting indicator parameters or adding complementary indicators
3. Improving edge case handling (insufficient data, zero ATR, etc.)
4. Tightening risk management (SL/TP placement)

Preserve the algorithm's class name, `name()`, and overall structure.
Output ONLY the complete updated Python file.
"""


CONFIG_REFINEMENT_SYSTEM_PROMPT_ADDITION = """\

## Config Refinement Mode

You are optimizing the CONFIGURATION PARAMETERS of an existing algorithm, not modifying its code.
You will receive:
- The current algorithm_params, risk_settings, instruments, and timeframes
- The algorithm source code (for understanding how parameters are used)
- Backtest performance metrics and optional live performance data
- Optional refinement hints describing specific areas to optimize

Your goal is to suggest improved parameter values that will improve the algorithm's performance.
Focus on:
1. Indicator periods (e.g., EMA periods, ATR periods, RSI periods)
2. ATR multipliers for SL/TP placement
3. Reward/risk ratios
4. Confidence thresholds
5. Confluence filter settings
6. Session window adjustments

Output a JSON object with ONLY the config fields you want to change.
Do NOT include unchanged fields. Example:
{
  "algorithm_params": {
    "atr_sl_multiplier": 2.0,
    "reward_risk_ratio": 2.5
  },
  "risk_settings": {
    "max_risk_per_trade": 0.015
  }
}
"""


class ConverterAgent(Agent):
    """Converts strategy descriptions into validated StrategyAlgorithm Python code.

    Constructor args:
        llm_client: LLMClient for LLM completions.
        tool_registry: ToolRegistry for invoking platform tools.
        memory: AgentMemory for conversation history and knowledge base.
        sandbox: CodeSandbox for isolated code execution and validation.
        stream_publisher: AgentStreamPublisher for publishing to Redis streams.
        backend_url: Backend API base URL (for state machine persistence).
        max_conversion_retries: Max retry attempts for validation/smoke test failures.
    """

    def __init__(
        self,
        llm_client: LLMClient,
        tool_registry: ToolRegistry,
        memory: AgentMemory,
        sandbox: CodeSandbox,
        stream_publisher: Optional[AgentStreamPublisher] = None,
        backend_url: str = "http://backend:3000",
        max_conversion_retries: int = DEFAULT_MAX_CONVERSION_RETRIES,
    ) -> None:
        self._llm_client = llm_client
        self._tool_registry = tool_registry
        self._memory = memory
        self._sandbox = sandbox
        self._stream_publisher = stream_publisher
        self._state_machine = AgentStateMachine("converter", backend_url)
        self._max_retries = max_conversion_retries

    def name(self) -> str:
        return "converter"

    def description(self) -> str:
        return (
            "Converts strategy descriptions (PineScript, natural language, pseudocode) "
            "into validated Python code conforming to the StrategyAlgorithm interface."
        )

    def supported_task_types(self) -> list[str]:
        return ["convert_strategy", "refine_strategy", "refine_strategy_config"]

    def supported_tools(self) -> list[str]:
        return [
            "write_algorithm_file", "validate_algorithm", "list_algorithms",
            "read_algorithm_source", "read_strategy_config", "update_strategy_config",
        ]

    def get_system_prompt(self) -> str:
        return CONVERTER_SYSTEM_PROMPT

    async def run(self, task: AgentTask) -> TaskResult:
        """Execute the converter agent's reasoning loop.

        Handles `convert_strategy` task type following
        PLANNING → EXECUTING → REVIEWING flow with retry loop.
        """
        start_time = time.monotonic()
        try:
            if task.type == "convert_strategy":
                return await self._run_convert_strategy(task, start_time)
            if task.type == "refine_strategy":
                return await self._run_refine_strategy(task, start_time)
            if task.type == "refine_strategy_config":
                return await self._run_refine_strategy_config(task, start_time)
            return TaskResult(
                task_id=task.id,
                agent_name=self.name(),
                status=TaskStatus.FAILED,
                error=f"Unsupported task type: {task.type}",
                duration_seconds=time.monotonic() - start_time,
            )
        except Exception as e:
            duration = time.monotonic() - start_time
            logger.error("Converter agent failed on task %s: %s", task.id, e)
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
    # convert_strategy flow (Req 11.1–11.10)
    # ------------------------------------------------------------------

    async def _run_convert_strategy(
        self, task: AgentTask, start_time: float
    ) -> TaskResult:
        """Convert strategy flow:
        1. PLANNING: Analyze strategy_description, gather existing algorithms context.
        2. EXECUTING: LLM code generation → write file → validate → smoke test.
           Retry loop up to max_conversion_retries on failure.
        3. REVIEWING: Confirm all validations pass. Output ConversionResult.
        """
        payload = task.payload
        strategy_description = payload.get("strategy_description", "")
        strategy_name = payload.get("strategy_name", "generated_strategy")
        target_instruments = payload.get("target_instruments", [])
        strategy_hypothesis = payload.get("strategy_hypothesis", None)

        file_path = f"src/strategy/algorithms/{strategy_name}.py"
        warnings: list[str] = []

        # ── PLANNING ──
        self._state_machine.transition(AgentState.PLANNING, reason="convert_strategy")

        # Gather context: existing algorithms (Req 11.10)
        algorithms_context = await self._query_algorithms_context(task)

        planning_prompt = self._build_planning_prompt(
            strategy_description,
            strategy_name,
            target_instruments,
            strategy_hypothesis,
            algorithms_context,
        )

        await self._memory.add_message(
            self.name(), task.id, "user", planning_prompt
        )

        # ── EXECUTING ──
        self._state_machine.transition(AgentState.EXECUTING, reason="generating code")

        # Initial code generation via LLM (Req 11.2)
        generation_prompt = (
            f"{planning_prompt}\n\n"
            "Generate the complete Python file implementing this strategy as a "
            "StrategyAlgorithm subclass. Output ONLY the Python code, no markdown "
            "fences or explanations."
        )

        code_response = await self._llm_client.complete(
            agent_name=self.name(),
            system_prompt=self.get_system_prompt(),
            messages=[{"role": "user", "content": generation_prompt}],
            temperature=0.5,
        )

        await self._memory.add_message(
            self.name(), task.id, "assistant", code_response.content
        )

        generated_code = self._extract_code(code_response.content)

        # Retry loop: write → validate → smoke test (Req 11.5, 11.7)
        retry_count = 0
        validation_passed = False
        smoke_test_passed = False
        algorithm_class: Optional[str] = None

        while retry_count <= self._max_retries:
            # Write file via tool through sandbox (Req 11.3, 11.9)
            try:
                await self._tool_registry.invoke(
                    "write_algorithm_file",
                    self.name(),
                    {"filename": f"{strategy_name}.py", "code": generated_code},
                )
            except Exception as e:
                logger.warning("write_algorithm_file failed: %s", e)
                warnings.append(f"Write attempt {retry_count}: {e}")
                # Fallback: write directly if tool fails
                try:
                    import os
                    os.makedirs(os.path.dirname(file_path), exist_ok=True)
                    with open(file_path, "w") as f:
                        f.write(generated_code)
                except Exception as write_err:
                    logger.error("Direct file write also failed: %s", write_err)
                    if retry_count >= self._max_retries:
                        break
                    retry_count += 1
                    continue

            # Validate: syntax, import, interface (Req 11.4)
            validation_result = await self._sandbox.validate_file(file_path)

            if not validation_result.success:
                validation_passed = False
                error_msg = validation_result.stderr or validation_result.error or "Unknown validation error"
                logger.info(
                    "Validation failed (attempt %d/%d): %s",
                    retry_count + 1,
                    self._max_retries + 1,
                    error_msg,
                )

                if retry_count >= self._max_retries:
                    warnings.append(f"Validation failed after {retry_count + 1} attempts: {error_msg}")
                    break

                # Feed error back to LLM for correction (Req 11.5)
                generated_code = await self._fix_code_with_llm(
                    task, generated_code, error_msg, "validation"
                )
                retry_count += 1
                continue

            validation_passed = True
            algorithm_class = (
                validation_result.return_value.get("algorithm_class")
                if validation_result.return_value
                else None
            )

            if not algorithm_class:
                warnings.append("Could not determine algorithm class name from validation")
                if retry_count >= self._max_retries:
                    break
                retry_count += 1
                continue

            # Smoke test: instantiate + call analyze() (Req 11.6)
            smoke_result = await self._sandbox.smoke_test(file_path, algorithm_class)

            if not smoke_result.success:
                smoke_test_passed = False
                error_msg = smoke_result.stderr or smoke_result.error or "Unknown smoke test error"
                logger.info(
                    "Smoke test failed (attempt %d/%d): %s",
                    retry_count + 1,
                    self._max_retries + 1,
                    error_msg,
                )

                if retry_count >= self._max_retries:
                    warnings.append(f"Smoke test failed after {retry_count + 1} attempts: {error_msg}")
                    break

                # Feed error back to LLM for correction (Req 11.7)
                generated_code = await self._fix_code_with_llm(
                    task, generated_code, error_msg, "smoke_test"
                )
                retry_count += 1
                continue

            # Both validation and smoke test passed
            smoke_test_passed = True
            break

        # ── REVIEWING ──
        self._state_machine.transition(AgentState.REVIEWING, reason="finalizing result")

        # Build ConversionResult (Req 11.8)
        conversion_result = ConversionResult(
            algorithm_name=strategy_name,
            file_path=file_path,
            validation_passed=validation_passed,
            smoke_test_passed=smoke_test_passed,
            retry_count=retry_count,
            warnings=warnings,
        )

        overall_status = (
            TaskStatus.COMPLETED
            if validation_passed and smoke_test_passed
            else TaskStatus.FAILED
        )

        # Record decision
        await self._memory.record_decision(
            agent_name=self.name(),
            task_id=task.id,
            decision_type="convert_strategy",
            input_summary=f"Strategy: {strategy_name}, Description: {strategy_description[:300]}",
            output_summary=(
                f"validation={validation_passed}, smoke_test={smoke_test_passed}, "
                f"retries={retry_count}"
            ),
            reasoning=f"Generated code for {strategy_name} targeting {target_instruments}",
            outcome="success" if overall_status == TaskStatus.COMPLETED else "failed",
        )

        # Store in knowledge base on success
        if overall_status == TaskStatus.COMPLETED:
            tags = ["algorithm", "converter", strategy_name]
            tags.extend(target_instruments)

            await self._memory.store_knowledge(
                key=f"algorithm:{strategy_name}",
                value=conversion_result.model_dump(),
                source_agent=self.name(),
                tags=tags,
            )

        self._state_machine.transition(
            AgentState.COMPLETED if overall_status == TaskStatus.COMPLETED else AgentState.FAILED,
            reason="conversion complete" if overall_status == TaskStatus.COMPLETED else "conversion failed",
        )

        if overall_status == TaskStatus.FAILED:
            self._state_machine.record_failure(
                reason=f"Conversion failed after {retry_count} retries",
                stack_trace="",
                task_id=task.id,
            )

        return TaskResult(
            task_id=task.id,
            agent_name=self.name(),
            status=overall_status,
            output={"conversion_result": conversion_result.model_dump()},
            duration_seconds=time.monotonic() - start_time,
        )

    # ------------------------------------------------------------------
    # refine_strategy flow (Req 2.1–2.10) — stub, implemented in tasks 4.2–4.4
    # ------------------------------------------------------------------

    async def _run_refine_strategy(
        self, task: AgentTask, start_time: float
    ) -> TaskResult:
        """Refine an existing algorithm based on performance data and LLM suggestions.

        Follows the same PLANNING → EXECUTING → REVIEWING pattern as
        _run_convert_strategy().
        """
        payload = task.payload
        algorithm_name = payload.get("algorithm_name", "")
        strategy_id = payload.get("strategy_id", "")
        refinement_hints: list[str] = payload.get("refinement_hints", [])
        performance_data: dict = payload.get("performance_data", {})

        # ── PLANNING ──
        self._state_machine.transition(AgentState.PLANNING, reason="refine_strategy")

        # Publish RefinementStarted event (Req 6.1)
        self._publish_refinement_event(
            "Agent:RefinementStarted",
            algorithm_name,
            {
                "algorithm_name": algorithm_name,
                "strategy_id": strategy_id,
                "refinement_hints": refinement_hints,
            },
        )
        self._broadcast_activity(
            "RefinementStarted",
            algorithm_name=algorithm_name,
            strategy_id=strategy_id,
        )

        # 1. Fetch current source code via read_algorithm_source tool (Req 2.2)
        source_result = await self._tool_registry.invoke(
            "read_algorithm_source",
            self.name(),
            {"algorithm_name": algorithm_name},
        )
        original_source: str = source_result.get("source_code", "")
        file_path: str = source_result.get(
            "file_path", f"src/strategy/algorithms/{algorithm_name}.py"
        )

        # Fetch current strategy config for LLM context (Req 3.1)
        strategy_config: dict = {}
        try:
            config_result = await self._tool_registry.invoke(
                "read_strategy_config",
                self.name(),
                {"strategy_id": strategy_id},
            )
            strategy_config = config_result.get("config", {})
        except Exception as cfg_err:
            logger.warning("Failed to fetch strategy config: %s", cfg_err)

        # 2. Fetch recent backtest metrics (optional, Req 2.3)
        backtest_metrics: dict = {}
        try:
            backtest_result = await self._tool_registry.invoke(
                "run_backtest",
                self.name(),
                {"strategy_id": strategy_id},
            )
            backtest_metrics = backtest_result if isinstance(backtest_result, dict) else {}
        except Exception as bt_err:
            logger.warning(
                "Failed to fetch backtest metrics for strategy_id=%s: %s (proceeding without)",
                strategy_id,
                bt_err,
            )

        # 3. Store backup in AgentMemory before any modification (Req 2.4)
        backup_key = f"algorithm_backup:{algorithm_name}:{datetime.now(timezone.utc).isoformat()}"
        await self._memory.store_knowledge(
            key=backup_key,
            value={"source_code": original_source, "file_path": file_path},
            source_agent=self.name(),
            tags=["backup", "refinement", algorithm_name],
        )

        # 4. Build refinement LLM prompt (Req 2.5, 3.1, 3.2)
        refinement_prompt = self._build_refinement_prompt(
            algorithm_name=algorithm_name,
            original_source=original_source,
            backtest_metrics=backtest_metrics,
            performance_data=performance_data,
            refinement_hints=refinement_hints,
            strategy_config=strategy_config,
        )

        # Record the planning conversation in memory
        await self._memory.add_message(
            self.name(), task.id, "user", refinement_prompt
        )

        # ── EXECUTING ── (Req 2.5, 2.6, 2.7)
        self._state_machine.transition(AgentState.EXECUTING, reason="refining code")

        refinement_system_prompt = CONVERTER_SYSTEM_PROMPT + REFINEMENT_SYSTEM_PROMPT_ADDITION

        # Initial LLM call to generate refined code
        code_response = await self._llm_client.complete(
            agent_name=self.name(),
            system_prompt=refinement_system_prompt,
            messages=[{"role": "user", "content": refinement_prompt}],
            temperature=0.5,
        )

        await self._memory.add_message(
            self.name(), task.id, "assistant", code_response.content
        )

        # Store raw LLM content before _extract_code strips ---CONFIG--- section
        raw_llm_content = code_response.content

        refined_code = self._extract_code(code_response.content)

        # Publish RefinementCodeGenerated event (Req 6.1)
        self._publish_refinement_event(
            "Agent:RefinementCodeGenerated",
            algorithm_name,
            {"algorithm_name": algorithm_name, "retry_count": 0},
        )

        # Retry loop: write → validate → smoke test (Req 2.6, 2.7)
        retry_count = 0
        validation_passed = False
        smoke_test_passed = False
        last_error: str = ""
        warnings: list[str] = []
        filename = file_path.rsplit("/", 1)[-1] if "/" in file_path else f"{algorithm_name}.py"

        while retry_count <= self._max_retries:
            # Write file via tool (Req 2.6)
            try:
                await self._tool_registry.invoke(
                    "write_algorithm_file",
                    self.name(),
                    {"filename": filename, "code": refined_code},
                )
            except Exception as e:
                logger.warning("write_algorithm_file failed: %s", e)
                warnings.append(f"Write attempt {retry_count}: {e}")
                try:
                    import os
                    os.makedirs(os.path.dirname(file_path), exist_ok=True)
                    with open(file_path, "w") as f:
                        f.write(refined_code)
                except Exception as write_err:
                    logger.error("Direct file write also failed: %s", write_err)
                    if retry_count >= self._max_retries:
                        last_error = f"File write failed: {write_err}"
                        break
                    retry_count += 1
                    continue

            # Validate: syntax, import, interface (Req 2.6)
            validation_result = await self._sandbox.validate_file(file_path)

            if not validation_result.success:
                validation_passed = False
                error_msg = validation_result.stderr or validation_result.error or "Unknown validation error"
                logger.info(
                    "Refinement validation failed (attempt %d/%d): %s",
                    retry_count + 1,
                    self._max_retries + 1,
                    error_msg,
                )

                if retry_count >= self._max_retries:
                    last_error = f"Validation failed: {error_msg}"
                    warnings.append(f"Validation failed after {retry_count + 1} attempts: {error_msg}")
                    break

                # Feed error back to LLM for correction (Req 2.7)
                refined_code = await self._fix_code_with_llm(
                    task, refined_code, error_msg, "validation"
                )
                retry_count += 1
                continue

            validation_passed = True

            # Smoke test: instantiate + call analyze() (Req 2.6)
            algorithm_class = (
                validation_result.return_value.get("algorithm_class")
                if validation_result.return_value
                else None
            )

            if not algorithm_class:
                warnings.append("Could not determine algorithm class name from validation")
                if retry_count >= self._max_retries:
                    last_error = "Could not determine algorithm class name"
                    break
                retry_count += 1
                continue

            smoke_result = await self._sandbox.smoke_test(file_path, algorithm_class)

            if not smoke_result.success:
                smoke_test_passed = False
                error_msg = smoke_result.stderr or smoke_result.error or "Unknown smoke test error"
                logger.info(
                    "Refinement smoke test failed (attempt %d/%d): %s",
                    retry_count + 1,
                    self._max_retries + 1,
                    error_msg,
                )

                if retry_count >= self._max_retries:
                    last_error = f"Smoke test failed: {error_msg}"
                    warnings.append(f"Smoke test failed after {retry_count + 1} attempts: {error_msg}")
                    break

                # Feed error back to LLM for correction (Req 2.7)
                refined_code = await self._fix_code_with_llm(
                    task, refined_code, error_msg, "smoke_test"
                )
                retry_count += 1
                continue

            # Both validation and smoke test passed
            smoke_test_passed = True
            break

        # Determine if all retries were exhausted (for REVIEWING phase)
        all_retries_exhausted = not (validation_passed and smoke_test_passed)
        duration = time.monotonic() - start_time

        # ── REVIEWING ── (Req 2.8, 2.9, 6.1, 6.2, 6.4, 6.5)

        if all_retries_exhausted:
            # All retries exhausted — restore original code (Req 2.9)
            await self._restore_from_backup(backup_key, file_path)

            # Publish RefinementFailed event (Req 6.1)
            self._publish_refinement_event(
                "Agent:RefinementFailed",
                algorithm_name,
                {
                    "algorithm_name": algorithm_name,
                    "error": last_error or "All retries exhausted",
                    "retry_count": retry_count,
                    "backup_key": backup_key,
                },
            )

            # Publish RefinementRolledBack event (Req 6.1)
            self._publish_refinement_event(
                "Agent:RefinementRolledBack",
                algorithm_name,
                {
                    "algorithm_name": algorithm_name,
                    "backup_key": backup_key,
                    "reason": last_error or "All retries exhausted",
                },
            )

            # Broadcast activity (Req 6.4)
            self._broadcast_activity(
                "RefinementFailed",
                algorithm_name=algorithm_name,
                error=last_error or "All retries exhausted",
                retry_count=retry_count,
            )

            # Metrics (Req 6.3)
            agent_refinements_total.labels(
                algorithm_name=algorithm_name, status="failed"
            ).inc()
            agent_refinement_duration_seconds.labels(
                algorithm_name=algorithm_name
            ).observe(duration)

            self._state_machine.transition(
                AgentState.FAILED, reason="refinement failed after retries"
            )
            return TaskResult(
                task_id=task.id,
                agent_name=self.name(),
                status=TaskStatus.FAILED,
                error=last_error or "Refinement failed after all retries",
                output={
                    "backup_key": backup_key,
                    "file_path": file_path,
                    "algorithm_name": algorithm_name,
                    "retry_count": retry_count,
                    "warnings": warnings,
                },
                duration_seconds=duration,
            )

        # ── Success path ──
        self._state_machine.transition(AgentState.REVIEWING, reason="finalizing refinement")

        # Publish RefinementValidated event (Req 6.1)
        self._publish_refinement_event(
            "Agent:RefinementValidated",
            algorithm_name,
            {"algorithm_name": algorithm_name, "file_path": file_path},
        )

        # Parse optional config suggestions from LLM output (Req 3.2)
        config_updates = self._extract_config_suggestions(raw_llm_content)
        config_changes = None

        if config_updates and strategy_config:
            # Store config backup (Req 3.3)
            config_backup_key = f"config_backup:{strategy_id}:{datetime.now(timezone.utc).isoformat()}"
            await self._memory.store_knowledge(
                key=config_backup_key,
                value={"config": strategy_config, "strategy_id": strategy_id},
                source_agent=self.name(),
                tags=["backup", "config_refinement", strategy_id],
            )

            # Apply config updates (Req 3.4)
            try:
                await self._tool_registry.invoke(
                    "update_strategy_config", self.name(),
                    {"strategy_id": strategy_id, "config": config_updates},
                )

                # Backtest with new config (Req 3.5)
                config_backtest = await self._tool_registry.invoke(
                    "run_backtest", self.name(), {"strategy_id": strategy_id},
                )
                config_backtest = config_backtest if isinstance(config_backtest, dict) else {}

                # Compare metrics — rollback if all worse (Req 3.6)
                if self._all_metrics_worse(backtest_metrics, config_backtest):
                    await self._tool_registry.invoke(
                        "update_strategy_config", self.name(),
                        {"strategy_id": strategy_id, "config": strategy_config},
                    )
                    logger.info("Config rollback for strategy %s — all metrics worse", strategy_id)
                else:
                    config_changes = {
                        "original_params": strategy_config,
                        "updated_params": config_updates,
                        "config_backup_key": config_backup_key,
                    }
            except Exception as cfg_apply_err:
                logger.warning("Failed to apply config updates: %s", cfg_apply_err)
                # Restore original config on any error
                try:
                    await self._tool_registry.invoke(
                        "update_strategy_config", self.name(),
                        {"strategy_id": strategy_id, "config": strategy_config},
                    )
                except Exception:
                    pass

        # Ask LLM for changes_summary (Req 2.8)
        summary_prompt = (
            "Briefly describe the changes you made to the algorithm and why. "
            "Focus on what was modified (entry/exit logic, indicators, risk management) "
            "and the expected impact. Keep it under 200 words."
        )
        try:
            summary_response = await self._llm_client.complete(
                agent_name=self.name(),
                system_prompt=CONVERTER_SYSTEM_PROMPT + REFINEMENT_SYSTEM_PROMPT_ADDITION,
                messages=[{"role": "user", "content": summary_prompt}],
                temperature=0.3,
            )
            changes_summary = summary_response.content.strip()
        except Exception as summary_err:
            logger.warning("Failed to get changes_summary from LLM: %s", summary_err)
            changes_summary = "Changes summary unavailable."

        # Build RefinementResult (Req 2.8)
        refinement_result = RefinementResult(
            algorithm_name=algorithm_name,
            file_path=file_path,
            validation_passed=validation_passed,
            smoke_test_passed=smoke_test_passed,
            retry_count=retry_count,
            changes_summary=changes_summary,
            original_source_backup_key=backup_key,
            warnings=warnings,
        )

        # Record decision in AgentMemory (Req 2.8)
        await self._memory.record_decision(
            agent_name=self.name(),
            task_id=task.id,
            decision_type="refine_strategy",
            input_summary=(
                f"Algorithm: {algorithm_name}, Strategy: {strategy_id}, "
                f"Hints: {refinement_hints[:3]}"
            ),
            output_summary=(
                f"validation={validation_passed}, smoke_test={smoke_test_passed}, "
                f"retries={retry_count}, changes_summary={changes_summary[:200]}"
            ),
            reasoning=f"Refined algorithm {algorithm_name} based on performance data",
            outcome="success",
        )

        # Store refinement outcome in knowledge base (Req 6.5)
        outcome_timestamp = datetime.now(timezone.utc).isoformat()
        await self._memory.store_knowledge(
            key=f"refinement:{algorithm_name}:{outcome_timestamp}",
            value={
                "before_metrics": {**backtest_metrics, **performance_data},
                "after_metrics": {},
                "changes_summary": changes_summary,
                "improvement_score": 0.0,
            },
            source_agent=self.name(),
            tags=["refinement", "outcome", algorithm_name],
        )

        # Publish RefinementCompleted event (Req 6.1, 6.2)
        self._publish_refinement_event(
            "Agent:RefinementCompleted",
            algorithm_name,
            {
                "algorithm_name": algorithm_name,
                "changes_summary": changes_summary,
                "retry_count": retry_count,
                "backup_key": backup_key,
            },
        )

        # Broadcast activity (Req 6.4)
        self._broadcast_activity(
            "RefinementCompleted",
            algorithm_name=algorithm_name,
            changes_summary=changes_summary[:200],
            retry_count=retry_count,
        )

        # Metrics (Req 6.3)
        agent_refinements_total.labels(
            algorithm_name=algorithm_name, status="success"
        ).inc()
        agent_refinement_duration_seconds.labels(
            algorithm_name=algorithm_name
        ).observe(duration)

        self._state_machine.transition(AgentState.IDLE, reason="refinement complete")

        output = {"refinement_result": refinement_result.model_dump()}
        if config_changes:
            output["refinement_result"]["config_changes"] = config_changes

        return TaskResult(
            task_id=task.id,
            agent_name=self.name(),
            status=TaskStatus.COMPLETED,
            output=output,
            duration_seconds=duration,
        )

    # ------------------------------------------------------------------
    # refine_strategy_config flow (Req 4.1–4.10) — stub, implemented in tasks 4.2–4.4
    # ------------------------------------------------------------------

    async def _run_refine_strategy_config(self, task: AgentTask, start_time: float) -> TaskResult:
        """Config-only refinement: optimise strategy parameters without touching algorithm code.

        Follows PLANNING → EXECUTING → REVIEWING pattern.
        Requirements: 4.1–4.10, 7.1–7.5
        """
        payload = task.payload
        strategy_id: str = payload.get("strategy_id", "")
        refinement_hints: list[str] = payload.get("refinement_hints", [])
        performance_data: dict = payload.get("performance_data", {})

        # ── PLANNING ── (Req 4.2, 4.3, 4.4, 7.1)
        self._state_machine.transition(AgentState.PLANNING, reason="refine_strategy_config")

        # Publish ConfigRefinementStarted event (Req 7.1)
        self._publish_refinement_event(
            "Agent:ConfigRefinementStarted",
            strategy_id,
            {
                "strategy_id": strategy_id,
                "refinement_hints": refinement_hints,
            },
        )
        self._broadcast_activity(
            "ConfigRefinementStarted",
            strategy_id=strategy_id,
        )

        # 1. Fetch current strategy config via read_strategy_config tool (Req 4.2)
        config_result = await self._tool_registry.invoke(
            "read_strategy_config",
            self.name(),
            {"strategy_id": strategy_id},
        )
        strategy_config: dict = config_result.get("config", {})
        algorithm_name: str = config_result.get("algorithm", "")

        # 2. Fetch algorithm source code for context (Req 4.2)
        source_result = await self._tool_registry.invoke(
            "read_algorithm_source",
            self.name(),
            {"algorithm_name": algorithm_name},
        )
        algorithm_source: str = source_result.get("source_code", "")

        # 3. Store Config_Backup in AgentMemory before any modification (Req 4.4)
        timestamp = datetime.now(timezone.utc).isoformat()
        config_backup_key = f"config_backup:{strategy_id}:{timestamp}"
        await self._memory.store_knowledge(
            key=config_backup_key,
            value={"config": strategy_config, "strategy_id": strategy_id},
            source_agent=self.name(),
            tags=["backup", "config_refinement", strategy_id],
        )

        # 4. Build config refinement LLM prompt (Req 4.3)
        config_refinement_prompt = self._build_config_refinement_prompt(
            strategy_config=strategy_config,
            algorithm_source=algorithm_source,
            performance_data=performance_data,
            refinement_hints=refinement_hints,
        )

        await self._memory.add_message(
            self.name(), task.id, "user", config_refinement_prompt
        )

        # ── EXECUTING ── (Req 4.5, 4.6, 4.7, 4.10, 7.1)
        self._state_machine.transition(AgentState.EXECUTING, reason="generating config updates")

        config_refinement_system_prompt = CONVERTER_SYSTEM_PROMPT + CONFIG_REFINEMENT_SYSTEM_PROMPT_ADDITION

        # Get baseline backtest metrics before any changes
        before_backtest: dict = {}
        try:
            before_backtest = await self._tool_registry.invoke(
                "run_backtest",
                self.name(),
                {"strategy_id": strategy_id},
            )
            if not isinstance(before_backtest, dict):
                before_backtest = {}
        except Exception as bt_err:
            logger.warning(
                "Failed to get baseline backtest for strategy_id=%s: %s",
                strategy_id,
                bt_err,
            )
            # Fall back to performance_data from payload if available
            before_backtest = performance_data.copy() if performance_data else {}

        retry_count = 0
        config_updates: dict = {}
        after_backtest: dict = {}
        llm_messages = [{"role": "user", "content": config_refinement_prompt}]

        while retry_count <= self._max_retries:
            # 1. LLM generates JSON config update
            llm_response = await self._llm_client.complete(
                agent_name=self.name(),
                system_prompt=config_refinement_system_prompt,
                messages=llm_messages,
                temperature=0.5,
            )

            await self._memory.add_message(
                self.name(), task.id, "assistant", llm_response.content
            )

            # 2. Parse and validate JSON structure
            try:
                config_updates = self._extract_json(llm_response.content)
            except (json.JSONDecodeError, ValueError) as parse_err:
                error_msg = f"Failed to parse JSON from LLM response: {parse_err}"
                logger.info(
                    "Config refinement JSON parse failed (attempt %d/%d): %s",
                    retry_count + 1,
                    self._max_retries + 1,
                    error_msg,
                )
                if retry_count >= self._max_retries:
                    break

                # Feed error back to LLM and retry
                fix_msg = (
                    f"Your response could not be parsed as valid JSON: {parse_err}\n\n"
                    "Please output ONLY a valid JSON object with the config fields to change."
                )
                llm_messages = [{"role": "user", "content": fix_msg}]
                await self._memory.add_message(
                    self.name(), task.id, "user", fix_msg
                )
                retry_count += 1
                continue

            if not isinstance(config_updates, dict) or not config_updates:
                error_msg = "LLM returned empty or non-dict config updates"
                logger.info(
                    "Config refinement invalid structure (attempt %d/%d): %s",
                    retry_count + 1,
                    self._max_retries + 1,
                    error_msg,
                )
                if retry_count >= self._max_retries:
                    break

                fix_msg = (
                    "Your response was not a non-empty JSON object. "
                    "Please output a JSON object with ONLY the config fields to change."
                )
                llm_messages = [{"role": "user", "content": fix_msg}]
                await self._memory.add_message(
                    self.name(), task.id, "user", fix_msg
                )
                retry_count += 1
                continue

            # 3. Apply config via update_strategy_config tool (Req 4.5)
            try:
                await self._tool_registry.invoke(
                    "update_strategy_config",
                    self.name(),
                    {"strategy_id": strategy_id, "config": config_updates},
                )
            except Exception as apply_err:
                error_msg = f"Failed to apply config update: {apply_err}"
                logger.info(
                    "Config apply failed (attempt %d/%d): %s",
                    retry_count + 1,
                    self._max_retries + 1,
                    error_msg,
                )
                if retry_count >= self._max_retries:
                    break

                fix_msg = (
                    f"The config update failed to apply: {apply_err}\n\n"
                    "Please suggest different config parameter values as a valid JSON object."
                )
                llm_messages = [{"role": "user", "content": fix_msg}]
                await self._memory.add_message(
                    self.name(), task.id, "user", fix_msg
                )
                retry_count += 1
                continue

            # Publish ConfigRefinementApplied event (Req 7.1)
            self._publish_refinement_event(
                "Agent:ConfigRefinementApplied",
                strategy_id,
                {
                    "strategy_id": strategy_id,
                    "algorithm_name": algorithm_name,
                    "updated_params": str(config_updates),
                },
            )

            # 4. Run backtest with updated config (Req 4.6)
            backtest_ok = True
            try:
                after_backtest = await self._tool_registry.invoke(
                    "run_backtest",
                    self.name(),
                    {"strategy_id": strategy_id},
                )
                if not isinstance(after_backtest, dict):
                    after_backtest = {}
                    backtest_ok = False
            except Exception as bt_err:
                error_msg = f"Backtest failed with updated config: {bt_err}"
                logger.info(
                    "Backtest failed (attempt %d/%d): %s",
                    retry_count + 1,
                    self._max_retries + 1,
                    error_msg,
                )
                backtest_ok = False

            # 5. Compare before/after metrics (Req 4.7)
            if not backtest_ok or self._all_metrics_worse(before_backtest, after_backtest):
                reason = (
                    "all metrics worse after config update"
                    if backtest_ok
                    else (error_msg if not backtest_ok else "backtest failed")
                )
                logger.info(
                    "Config refinement metrics check failed (attempt %d/%d): %s",
                    retry_count + 1,
                    self._max_retries + 1,
                    reason,
                )

                # Restore original config from backup
                try:
                    await self._tool_registry.invoke(
                        "update_strategy_config",
                        self.name(),
                        {"strategy_id": strategy_id, "config": strategy_config},
                    )
                except Exception as restore_err:
                    logger.error("Failed to restore config from backup: %s", restore_err)

                if retry_count >= self._max_retries:
                    break

                # Feed error back to LLM and retry
                fix_msg = (
                    f"The config update did not improve performance: {reason}\n\n"
                    f"Before metrics: {json.dumps(before_backtest, indent=2, default=str)}\n"
                    f"After metrics: {json.dumps(after_backtest, indent=2, default=str)}\n\n"
                    "Please suggest different parameter values that would improve performance. "
                    "Output ONLY a valid JSON object."
                )
                llm_messages = [{"role": "user", "content": fix_msg}]
                await self._memory.add_message(
                    self.name(), task.id, "user", fix_msg
                )
                retry_count += 1
                continue

            # Metrics improved or mixed — success!
            # Publish ConfigRefinementValidated event (Req 7.1)
            self._publish_refinement_event(
                "Agent:ConfigRefinementValidated",
                strategy_id,
                {
                    "strategy_id": strategy_id,
                    "algorithm_name": algorithm_name,
                    "backtest_metrics": str(after_backtest),
                },
            )
            break

        # Check if all retries exhausted (Req 4.10)
        all_retries_exhausted = retry_count > self._max_retries
        if all_retries_exhausted:
            # Restore original config from backup
            try:
                await self._tool_registry.invoke(
                    "update_strategy_config",
                    self.name(),
                    {"strategy_id": strategy_id, "config": strategy_config},
                )
            except Exception as restore_err:
                logger.error("Failed to restore config on exhaustion: %s", restore_err)

            # Publish ConfigRefinementRolledBack event (Req 7.1)
            self._publish_refinement_event(
                "Agent:ConfigRefinementRolledBack",
                strategy_id,
                {
                    "strategy_id": strategy_id,
                    "algorithm_name": algorithm_name,
                    "config_backup_key": config_backup_key,
                    "reason": "All retries exhausted",
                },
            )
            self._broadcast_activity(
                "ConfigRefinementRolledBack",
                strategy_id=strategy_id,
                algorithm_name=algorithm_name,
                reason="All retries exhausted",
            )

            duration = time.monotonic() - start_time

            # Metrics (Req 7.3)
            agent_config_refinements_total.labels(
                strategy_id=strategy_id, status="failed"
            ).inc()
            agent_config_refinement_duration_seconds.labels(
                strategy_id=strategy_id
            ).observe(duration)

            self._state_machine.transition(
                AgentState.FAILED, reason="config refinement failed after retries"
            )
            return TaskResult(
                task_id=task.id,
                agent_name=self.name(),
                status=TaskStatus.FAILED,
                error="Config refinement failed after all retries",
                output={
                    "strategy_id": strategy_id,
                    "algorithm_name": algorithm_name,
                    "config_backup_key": config_backup_key,
                    "retry_count": retry_count,
                },
                duration_seconds=duration,
            )

        # ── REVIEWING ── (Req 4.8, 7.1, 7.2, 7.3, 7.4, 7.5)
        self._state_machine.transition(AgentState.REVIEWING, reason="finalizing config refinement")

        # Ask LLM for param_changes_summary
        summary_prompt = (
            "Briefly describe the config parameter changes you suggested and why. "
            "Focus on what parameters were changed (indicator periods, ATR multipliers, "
            "risk settings, etc.) and the expected impact on performance. "
            "Keep it under 200 words."
        )
        try:
            summary_response = await self._llm_client.complete(
                agent_name=self.name(),
                system_prompt=CONVERTER_SYSTEM_PROMPT + CONFIG_REFINEMENT_SYSTEM_PROMPT_ADDITION,
                messages=[{"role": "user", "content": summary_prompt}],
                temperature=0.3,
            )
            param_changes_summary = summary_response.content.strip()
        except Exception as summary_err:
            logger.warning("Failed to get param_changes_summary from LLM: %s", summary_err)
            param_changes_summary = "Changes summary unavailable."

        # Build ConfigRefinementResult (Req 4.8)
        config_refinement_result = ConfigRefinementResult(
            strategy_id=strategy_id,
            algorithm_name=algorithm_name,
            original_params=strategy_config,
            updated_params=config_updates,
            param_changes_summary=param_changes_summary,
            backtest_improvement={"before": before_backtest, "after": after_backtest},
            config_backup_key=config_backup_key,
            warnings=[],
        )

        # Record decision in AgentMemory
        await self._memory.record_decision(
            agent_name=self.name(),
            task_id=task.id,
            decision_type="refine_strategy_config",
            input_summary=(
                f"Strategy: {strategy_id}, Algorithm: {algorithm_name}, "
                f"Hints: {refinement_hints[:3]}"
            ),
            output_summary=(
                f"updated_params={config_updates}, "
                f"retries={retry_count}, summary={param_changes_summary[:200]}"
            ),
            reasoning=f"Refined config for strategy {strategy_id} ({algorithm_name})",
            outcome="success",
        )

        # Store config refinement outcome in knowledge base (Req 7.5)
        await self._memory.store_knowledge(
            key=f"config_refinement:{strategy_id}:{timestamp}",
            value={
                "original_params": strategy_config,
                "updated_params": config_updates,
                "changes_summary": param_changes_summary,
                "before_metrics": before_backtest,
                "after_metrics": after_backtest,
            },
            source_agent=self.name(),
            tags=["config_refinement", "outcome", strategy_id],
        )

        # Publish ConfigRefinementCompleted event (Req 7.1, 7.2)
        self._publish_refinement_event(
            "Agent:ConfigRefinementCompleted",
            strategy_id,
            {
                "strategy_id": strategy_id,
                "algorithm_name": algorithm_name,
                "param_changes_summary": param_changes_summary,
                "config_backup_key": config_backup_key,
                "before_metrics": str(before_backtest),
                "after_metrics": str(after_backtest),
            },
        )

        # Broadcast activity to agents:activity channel (Req 7.4)
        self._broadcast_activity(
            "ConfigRefinementCompleted",
            strategy_id=strategy_id,
            algorithm_name=algorithm_name,
            param_changes_summary=param_changes_summary[:200],
            retry_count=retry_count,
        )

        # Metrics (Req 7.3)
        duration = time.monotonic() - start_time
        agent_config_refinements_total.labels(
            strategy_id=strategy_id, status="success"
        ).inc()
        agent_config_refinement_duration_seconds.labels(
            strategy_id=strategy_id
        ).observe(duration)

        # Transition to IDLE
        self._state_machine.transition(AgentState.IDLE, reason="config refinement complete")

        return TaskResult(
            task_id=task.id,
            agent_name=self.name(),
            status=TaskStatus.COMPLETED,
            output={"config_refinement_result": config_refinement_result.model_dump()},
            duration_seconds=duration,
        )

    # ------------------------------------------------------------------
    # JSON extraction and metric comparison helpers (Req 4.5, 4.7)
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_config_suggestions(llm_content: str) -> dict:
        """Extract optional config suggestions from LLM output separated by ---CONFIG--- marker."""
        if "---CONFIG---" not in llm_content:
            return {}
        try:
            config_part = llm_content.split("---CONFIG---", 1)[1].strip()
            # Strip markdown fences if present
            if "```json" in config_part:
                config_part = config_part.split("```json", 1)[1]
                config_part = config_part.split("```", 1)[0]
            elif "```" in config_part:
                config_part = config_part.split("```", 1)[1]
                config_part = config_part.split("```", 1)[0]
            return json.loads(config_part.strip())
        except (json.JSONDecodeError, IndexError, ValueError):
            return {}

    @staticmethod
    def _extract_json(llm_content: str) -> dict:
        """Extract JSON object from LLM response, stripping markdown fences if present."""
        raw = llm_content.strip()
        if "```json" in raw:
            raw = raw.split("```json", 1)[1]
            raw = raw.split("```", 1)[0]
        elif "```" in raw:
            raw = raw.split("```", 1)[1]
            raw = raw.split("```", 1)[0]
        return json.loads(raw.strip())

    @staticmethod
    def _all_metrics_worse(before: dict, after: dict) -> bool:
        """Return True if ALL key metrics are worse in 'after' compared to 'before'."""
        b_wr = before.get("win_rate", 0)
        a_wr = after.get("win_rate", 0)
        b_pf = before.get("profit_factor", 0)
        a_pf = after.get("profit_factor", 0)
        b_dd = before.get("max_drawdown", 0)
        a_dd = after.get("max_drawdown", 0)
        # All three must be worse (lower win_rate, lower profit_factor, higher drawdown)
        return a_wr < b_wr and a_pf < b_pf and a_dd > b_dd

    # ------------------------------------------------------------------
    # LLM error correction (Req 11.5, 11.7)
    # ------------------------------------------------------------------

    async def _fix_code_with_llm(
        self,
        task: AgentTask,
        current_code: str,
        error_message: str,
        error_type: str,
    ) -> str:
        """Feed validation/smoke test error back to LLM for code correction."""
        fix_prompt = (
            f"The generated code has a {error_type} error:\n\n"
            f"Error:\n{error_message}\n\n"
            f"Current code:\n```python\n{current_code}\n```\n\n"
            "Fix the error and output the COMPLETE corrected Python file. "
            "Output ONLY the Python code, no markdown fences or explanations."
        )

        await self._memory.add_message(
            self.name(), task.id, "user", fix_prompt
        )

        fix_response = await self._llm_client.complete(
            agent_name=self.name(),
            system_prompt=self.get_system_prompt(),
            messages=[{"role": "user", "content": fix_prompt}],
            temperature=0.3,
        )

        await self._memory.add_message(
            self.name(), task.id, "assistant", fix_response.content
        )

        return self._extract_code(fix_response.content)

    # ------------------------------------------------------------------
    # Backup restore (Req 2.9)
    # ------------------------------------------------------------------

    async def _restore_from_backup(self, backup_key: str, file_path: str) -> None:
        """Restore original algorithm source code from AgentMemory backup."""
        try:
            results = await self._memory.query_knowledge(
                tags=["backup", "refinement"]
            )
            for entry in results:
                if entry.get("key") == backup_key:
                    original_code = entry["value"]["source_code"]
                    filename = (
                        file_path.rsplit("/", 1)[-1]
                        if "/" in file_path
                        else file_path
                    )
                    await self._tool_registry.invoke(
                        "write_algorithm_file",
                        self.name(),
                        {"filename": filename, "code": original_code},
                    )
                    logger.info(
                        "Restored original code for '%s' from backup '%s'",
                        file_path,
                        backup_key,
                    )
                    return
            logger.error(
                "Backup not found for key '%s' — algorithm may be in broken state",
                backup_key,
            )
        except Exception as exc:
            logger.error("Failed to restore from backup '%s': %s", backup_key, exc)

    # ------------------------------------------------------------------
    # Event publishing helpers (Req 6.1, 6.2, 6.4)
    # ------------------------------------------------------------------

    def _publish_refinement_event(
        self, event_type: str, algorithm_name: str, payload: dict
    ) -> None:
        """Publish a refinement TradingEvent to agents:events stream."""
        if self._stream_publisher is None:
            return
        try:
            data = {"type": event_type}
            for k, v in payload.items():
                data[k] = str(v) if v is not None else ""
            self._stream_publisher._redis.xadd(
                EVENTS_STREAM, data, maxlen=10000, approximate=True
            )
        except Exception as exc:
            logger.warning("Failed to publish %s event: %s", event_type, exc)

    def _broadcast_activity(self, event_type: str, **fields) -> None:
        """Broadcast refinement activity to agents:activity pub/sub channel."""
        if self._stream_publisher is None:
            return
        try:
            message = {"type": event_type, "agent": self.name(), **fields}
            self._stream_publisher._redis.publish(
                ACTIVITY_CHANNEL, json.dumps(message, default=str)
            )
        except Exception as exc:
            logger.warning("Failed to broadcast activity %s: %s", event_type, exc)

    # ------------------------------------------------------------------
    # Tool invocation helpers
    # ------------------------------------------------------------------

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
    def _build_planning_prompt(
        strategy_description: str,
        strategy_name: str,
        target_instruments: list[str],
        strategy_hypothesis: Optional[dict],
        algorithms_context: str,
    ) -> str:
        """Build the planning prompt for convert_strategy tasks."""
        parts = [
            f"Convert the following strategy description into a Python "
            f"StrategyAlgorithm implementation named '{strategy_name}'.",
            f"\nStrategy Description:\n{strategy_description}",
        ]

        if target_instruments:
            parts.append(f"\nTarget Instruments: {', '.join(target_instruments)}")

        if strategy_hypothesis:
            parts.append(
                f"\nStrategy Hypothesis (from Research Agent):\n"
                f"{json.dumps(strategy_hypothesis, indent=2, default=str)}"
            )

        parts.append(f"\nExisting algorithms on the platform:\n{algorithms_context}")
        parts.append(
            "\nEnsure the generated algorithm follows the same patterns as "
            "existing algorithms. The class name should be in PascalCase "
            f"(e.g., for '{strategy_name}' use an appropriate PascalCase name)."
        )

        return "\n".join(parts)

    @staticmethod
    def _build_refinement_prompt(
        algorithm_name: str,
        original_source: str,
        backtest_metrics: dict,
        performance_data: dict,
        refinement_hints: list[str],
        strategy_config: Optional[dict] = None,
    ) -> str:
        """Build the LLM prompt for refine_strategy tasks (Req 2.5, 3.1, 3.2)."""
        parts = [
            f"Refine the existing algorithm '{algorithm_name}'.",
            f"\n## Current Source Code\n\n```python\n{original_source}\n```",
        ]

        # Include strategy config context when available (Req 3.1, 3.2)
        if strategy_config:
            parts.append(
                f"\n## Current Strategy Config\n\n"
                f"```json\n{json.dumps(strategy_config, indent=2, default=str)}\n```\n\n"
                "If your code changes affect how parameters are used, also suggest "
                "updated algorithm_params as a JSON object. Output the JSON after "
                "the Python code, separated by a line containing only '---CONFIG---'."
            )

        # Merge backtest metrics and live performance data
        metrics_section_parts: list[str] = []
        if backtest_metrics:
            metrics_section_parts.append(
                f"Backtest Metrics:\n{json.dumps(backtest_metrics, indent=2, default=str)}"
            )
        if performance_data:
            metrics_section_parts.append(
                f"Live Performance Data:\n{json.dumps(performance_data, indent=2, default=str)}"
            )
        if metrics_section_parts:
            parts.append("\n## Performance Metrics\n\n" + "\n\n".join(metrics_section_parts))

        if refinement_hints:
            hints_text = "\n".join(f"- {hint}" for hint in refinement_hints)
            parts.append(f"\n## Refinement Hints\n\n{hints_text}")

        parts.append(
            "\nApply targeted improvements based on the metrics and hints above. "
            "Preserve the algorithm's class name, `name()`, and overall structure. "
            "Output ONLY the complete updated Python file."
        )

        return "\n".join(parts)

    @staticmethod
    def _build_config_refinement_prompt(
        strategy_config: dict,
        algorithm_source: str,
        performance_data: dict,
        refinement_hints: list[str],
    ) -> str:
        """Build the LLM prompt for refine_strategy_config tasks (Req 4.3)."""
        parts = [
            "Optimize the configuration parameters for this algorithm.",
            f"\n## Current Config\n\n```json\n{json.dumps(strategy_config, indent=2, default=str)}\n```",
            f"\n## Algorithm Source Code\n\n```python\n{algorithm_source}\n```",
        ]
        if performance_data:
            parts.append(
                f"\n## Performance Data\n\n```json\n{json.dumps(performance_data, indent=2, default=str)}\n```"
            )
        if refinement_hints:
            hints_text = "\n".join(f"- {hint}" for hint in refinement_hints)
            parts.append(f"\n## Refinement Hints\n\n{hints_text}")
        parts.append(
            "\nOutput a JSON object with ONLY the config fields you want to change."
        )
        return "\n".join(parts)

    # ------------------------------------------------------------------
    # Code extraction
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_code(llm_content: str) -> str:
        """Extract Python code from LLM response, stripping markdown fences if present."""
        raw = llm_content.strip()

        # Strip markdown code fences
        if "```python" in raw:
            raw = raw.split("```python", 1)[1]
            raw = raw.split("```", 1)[0]
        elif "```" in raw:
            raw = raw.split("```", 1)[1]
            raw = raw.split("```", 1)[0]

        return raw.strip()
