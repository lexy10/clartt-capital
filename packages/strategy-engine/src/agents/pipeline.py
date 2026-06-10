"""Pipeline Orchestrator — multi-agent workflow orchestration with stage gates.

Coordinates the Research → Converter → Backtest → Forward Test pipeline
with configurable gates between stages. Persists pipeline state to
PostgreSQL via the backend REST API.

Requirements: 7.1–7.10
"""

import json
import logging
from datetime import datetime, timezone
from typing import Any, Optional, Protocol

import requests

from src.agents.metrics import agent_refinement_improvement_score
from src.agents.models import (
    AgentTask,
    GateType,
    Pipeline,
    PipelineGate,
    PipelineStage,
    PipelineStatus,
    TaskPriority,
    TaskResult,
    TaskStatus,
)

logger = logging.getLogger(__name__)

EVENTS_STREAM = "agents:events"
ACTIVITY_CHANNEL = "agents:activity"

# 2-stage refinement pipeline: Converter (refine) → Backtest (compare)
# Used when start_pipeline(name="refinement") is called without explicit stages.
# Requirements: 3.1, 3.2, 3.4, 3.8
REFINEMENT_PIPELINE_STAGES = [
    PipelineStage(
        agent_name="converter",
        task_type="refine_strategy",
    ),
    PipelineStage(
        agent_name="backtest",
        task_type="run_backtest",
        gate=PipelineGate(
            gate_type=GateType.METRIC_THRESHOLD,
            config={
                "win_rate_min": 0.5,
                "max_drawdown_max": 0.2,
                "profit_factor_min": 1.0,
            },
        ),
    ),
]


class EventPublisherProtocol(Protocol):
    """Minimal protocol — any object with a publish() method."""

    def publish(self, event: Any) -> None: ...


class TaskQueueProtocol(Protocol):
    """Minimal protocol for the TaskQueue dependency."""

    async def submit(self, task: AgentTask) -> str: ...

    async def cancel(self, task_id: str) -> None: ...


class AgentRegistryProtocol(Protocol):
    """Minimal protocol for the AgentRegistry dependency."""

    def get(self, name: str) -> Any: ...


class PipelineOrchestrator:
    """Orchestrates multi-agent pipelines with stage gates.

    Default pipeline: Research → Converter → Backtest → Forward Test.

    Constructor:
        task_queue:       TaskQueue for submitting stage tasks
        agent_registry:   AgentRegistry for agent lookups
        event_publisher:  EventPublisher for TradingEvent publishing
        backend_url:      Backend REST API base URL for pipeline persistence
    """

    DEFAULT_PIPELINE_STAGES = [
        PipelineStage(
            agent_name="research",
            task_type="research_strategy",
        ),
        PipelineStage(
            agent_name="converter",
            task_type="convert_strategy",
        ),
        PipelineStage(
            agent_name="backtest",
            task_type="run_backtest",
            gate=PipelineGate(
                gate_type=GateType.METRIC_THRESHOLD,
                config={
                    "win_rate_min": 0.5,
                    "max_drawdown_max": 0.2,
                    "profit_factor_min": 1.0,
                },
            ),
        ),
        PipelineStage(
            agent_name="forward_test",
            task_type="forward_test",
            gate=PipelineGate(gate_type=GateType.HUMAN_APPROVAL),
        ),
    ]

    def __init__(
        self,
        task_queue: TaskQueueProtocol,
        agent_registry: AgentRegistryProtocol,
        event_publisher: EventPublisherProtocol,
        backend_url: str,
    ) -> None:
        self._task_queue = task_queue
        self._agent_registry = agent_registry
        self._event_publisher = event_publisher
        self._backend_url = backend_url.rstrip("/")

        # In-memory pipeline cache: pipeline_id → Pipeline
        self._pipelines: dict[str, Pipeline] = {}
        # Map task_id → pipeline_id for fast lookup on task completion
        self._task_to_pipeline: dict[str, str] = {}

    # ── Start Pipeline ────────────────────────────────────────────────

    async def start_pipeline(
        self,
        name: str,
        stages: Optional[list[PipelineStage]] = None,
        initial_payload: Optional[dict] = None,
    ) -> str:
        """Create a pipeline, submit the first stage task, persist to PostgreSQL.

        Returns the pipeline_id.

        Requirement 7.1, 7.2, 7.8, 7.9
        """
        if name == "refinement" and stages is None:
            stages = REFINEMENT_PIPELINE_STAGES

        pipeline_stages = (
            [s.model_copy(deep=True) for s in stages]
            if stages
            else [s.model_copy(deep=True) for s in self.DEFAULT_PIPELINE_STAGES]
        )

        pipeline = Pipeline(
            name=name,
            stages=pipeline_stages,
            current_stage_index=0,
            status=PipelineStatus.RUNNING,
        )

        self._pipelines[pipeline.id] = pipeline

        # Submit first stage task(s) — supports parallel independent stages
        await self._submit_stage_tasks(pipeline, initial_payload or {})

        # Persist to PostgreSQL
        self._persist_pipeline(pipeline)

        self._publish_event(
            event_type="Agent:PipelineStarted",
            aggregate_id=pipeline.id,
            payload={
                "pipeline_id": pipeline.id,
                "pipeline_name": name,
                "total_stages": len(pipeline_stages),
                "first_stage_agent": pipeline_stages[0].agent_name,
            },
        )

        logger.info(
            "Started pipeline '%s' (id=%s, stages=%d)",
            name,
            pipeline.id,
            len(pipeline_stages),
        )
        return pipeline.id

    # ── On Task Completed ─────────────────────────────────────────────

    async def on_task_completed(self, task: AgentTask, result: TaskResult) -> None:
        """Called when a stage task completes. Evaluate gate, advance or pause pipeline.

        Requirement 7.3, 7.4, 7.5, 7.7
        """
        pipeline_id = self._task_to_pipeline.get(task.id)
        if pipeline_id is None:
            return

        pipeline = self._pipelines.get(pipeline_id)
        if pipeline is None:
            return

        if pipeline.status not in (PipelineStatus.RUNNING, PipelineStatus.PAUSED):
            return

        # Find the stage that owns this task
        stage_index = self._find_stage_by_task_id(pipeline, task.id)
        if stage_index is None:
            return

        stage = pipeline.stages[stage_index]

        # Handle task failure
        if result.status == TaskStatus.FAILED:
            stage.status = TaskStatus.FAILED
            stage.result = result.output
            pipeline.status = PipelineStatus.FAILED
            pipeline.updated_at = self._now_iso()
            self._persist_pipeline(pipeline)

            self._publish_event(
                event_type="Agent:PipelineFailed",
                aggregate_id=pipeline.id,
                payload={
                    "pipeline_id": pipeline.id,
                    "pipeline_name": pipeline.name,
                    "failed_stage_index": stage_index,
                    "failed_agent": stage.agent_name,
                    "error": result.error or "unknown",
                },
            )
            logger.warning(
                "Pipeline '%s' failed at stage %d (%s): %s",
                pipeline.id,
                stage_index,
                stage.agent_name,
                result.error,
            )
            return

        # Mark stage completed
        stage.status = TaskStatus.COMPLETED
        stage.result = result.output

        self._publish_event(
            event_type="Agent:PipelineStageCompleted",
            aggregate_id=pipeline.id,
            payload={
                "pipeline_id": pipeline.id,
                "stage_index": stage_index,
                "agent_name": stage.agent_name,
                "status": TaskStatus.COMPLETED.value,
            },
        )

        # Evaluate gate if present
        if stage.gate is not None:
            passed, reason = await self.evaluate_gate(stage.gate, result)

            self._publish_event(
                event_type="Agent:PipelineGateEvaluated",
                aggregate_id=pipeline.id,
                payload={
                    "pipeline_id": pipeline.id,
                    "stage_index": stage_index,
                    "gate_type": stage.gate.gate_type.value,
                    "passed": passed,
                    "reason": reason,
                },
            )

            if not passed:
                pipeline.status = PipelineStatus.PAUSED
                pipeline.updated_at = self._now_iso()
                self._persist_pipeline(pipeline)

                self._publish_event(
                    event_type="Agent:PipelineGateRejected",
                    aggregate_id=pipeline.id,
                    payload={
                        "pipeline_id": pipeline.id,
                        "pipeline_name": pipeline.name,
                        "stage_index": stage_index,
                        "gate_type": stage.gate.gate_type.value,
                        "rejection_reason": reason,
                        "agent_name": stage.agent_name,
                    },
                )

                # Refinement pipeline: if gate fails and all key metrics
                # degraded, restore original algorithm and publish
                # RefinementRejected event (Req 3.6)
                if pipeline.name == "refinement" and stage.gate.gate_type == GateType.METRIC_THRESHOLD:
                    await self._handle_refinement_gate_failure(
                        pipeline, result,
                    )

                logger.info(
                    "Pipeline '%s' paused at stage %d gate (%s): %s",
                    pipeline.id,
                    stage_index,
                    stage.gate.gate_type.value,
                    reason,
                )
                return

        # Advance to next stage
        next_index = stage_index + 1
        if next_index >= len(pipeline.stages):
            # All stages complete
            pipeline.status = PipelineStatus.COMPLETED
            pipeline.current_stage_index = stage_index
            pipeline.updated_at = self._now_iso()
            self._persist_pipeline(pipeline)

            self._publish_event(
                event_type="Agent:PipelineCompleted",
                aggregate_id=pipeline.id,
                payload={
                    "pipeline_id": pipeline.id,
                    "pipeline_name": pipeline.name,
                    "total_stages": len(pipeline.stages),
                },
            )

            # Refinement pipeline success: publish RefinementCompleted event
            # with before/after metrics and update improvement score gauge
            # (Req 3.5, 3.7)
            if pipeline.name == "refinement":
                self._handle_refinement_pipeline_completed(pipeline, result)

            logger.info("Pipeline '%s' completed all stages", pipeline.id)
            return

        # Submit next stage task with previous output merged into payload
        pipeline.current_stage_index = next_index
        pipeline.updated_at = self._now_iso()

        await self._submit_next_stage(pipeline, next_index, result)
        self._persist_pipeline(pipeline)

    # ── Evaluate Gate ─────────────────────────────────────────────────

    async def evaluate_gate(
        self, gate: PipelineGate, stage_result: TaskResult
    ) -> tuple[bool, str]:
        """Evaluate a pipeline gate. Returns (passed, reason).

        Gate types:
          - metric_threshold: check result output dict for metric keys
          - human_approval: trigger approval gate manager (pauses pipeline)
          - auto_pass: always returns True

        Requirement 7.3, 7.6
        """
        if gate.gate_type == GateType.AUTO_PASS:
            return True, "auto_pass: gate always passes"

        if gate.gate_type == GateType.HUMAN_APPROVAL:
            # Human approval gates pause the pipeline — the operator must
            # call override_gate() to advance.
            return False, "human_approval: awaiting operator approval"

        if gate.gate_type == GateType.METRIC_THRESHOLD:
            return self._evaluate_metric_threshold(gate.config, stage_result)

        return False, f"unknown gate type: {gate.gate_type}"

    def _evaluate_metric_threshold(
        self, config: dict, stage_result: TaskResult
    ) -> tuple[bool, str]:
        """Check task result output against metric threshold config.

        Config keys follow the pattern ``{metric_name}_min`` or ``{metric_name}_max``.
        For example: ``win_rate_min: 0.5``, ``max_drawdown_max: 0.2``, ``profit_factor_min: 1.0``.

        The result output dict is checked for matching metric keys (e.g. ``win_rate``,
        ``max_drawdown``, ``profit_factor``).
        """
        output = stage_result.output
        failures: list[str] = []

        for key, threshold in config.items():
            if key.endswith("_min"):
                metric_name = key[: -len("_min")]
                actual = output.get(metric_name)
                if actual is None:
                    failures.append(f"{metric_name}: not found in result output")
                elif float(actual) < float(threshold):
                    failures.append(
                        f"{metric_name}={actual} < min threshold {threshold}"
                    )
            elif key.endswith("_max"):
                metric_name = key[: -len("_max")]
                actual = output.get(metric_name)
                if actual is None:
                    failures.append(f"{metric_name}: not found in result output")
                elif float(actual) > float(threshold):
                    failures.append(
                        f"{metric_name}={actual} > max threshold {threshold}"
                    )

        if failures:
            return False, f"metric_threshold failed: {'; '.join(failures)}"

        return True, "metric_threshold: all thresholds passed"

    # ── Abort ─────────────────────────────────────────────────────────

    async def abort(self, pipeline_id: str) -> None:
        """Abort a running pipeline. Cancel pending tasks.

        Requirement 7.7
        """
        pipeline = self._pipelines.get(pipeline_id)
        if pipeline is None:
            raise KeyError(f"Pipeline '{pipeline_id}' not found")

        if pipeline.status not in (PipelineStatus.RUNNING, PipelineStatus.PAUSED):
            raise ValueError(
                f"Pipeline '{pipeline_id}' cannot be aborted (status={pipeline.status.value})"
            )

        # Cancel any pending/in-progress stage tasks
        for stage in pipeline.stages:
            if stage.task_id and stage.status in (
                TaskStatus.PENDING,
                TaskStatus.QUEUED,
                TaskStatus.IN_PROGRESS,
            ):
                try:
                    await self._task_queue.cancel(stage.task_id)
                except Exception as exc:
                    logger.warning(
                        "Failed to cancel task %s during pipeline abort: %s",
                        stage.task_id,
                        exc,
                    )
                stage.status = TaskStatus.CANCELLED

        pipeline.status = PipelineStatus.ABORTED
        pipeline.updated_at = self._now_iso()
        self._persist_pipeline(pipeline)

        self._publish_event(
            event_type="Agent:PipelineAborted",
            aggregate_id=pipeline_id,
            payload={
                "pipeline_id": pipeline_id,
                "pipeline_name": pipeline.name,
                "aborted_at_stage": pipeline.current_stage_index,
            },
        )

        logger.info("Pipeline '%s' aborted", pipeline_id)

    # ── Override Gate ─────────────────────────────────────────────────

    async def override_gate(self, pipeline_id: str) -> None:
        """Manually override a failed/paused gate and advance the pipeline.

        Requirement 7.5
        """
        pipeline = self._pipelines.get(pipeline_id)
        if pipeline is None:
            raise KeyError(f"Pipeline '{pipeline_id}' not found")

        if pipeline.status != PipelineStatus.PAUSED:
            raise ValueError(
                f"Pipeline '{pipeline_id}' is not paused (status={pipeline.status.value})"
            )

        current_stage = pipeline.stages[pipeline.current_stage_index]

        # Build a synthetic result from the current stage's stored result
        result = TaskResult(
            task_id=current_stage.task_id or "",
            agent_name=current_stage.agent_name,
            status=TaskStatus.COMPLETED,
            output=current_stage.result or {},
        )

        next_index = pipeline.current_stage_index + 1
        if next_index >= len(pipeline.stages):
            pipeline.status = PipelineStatus.COMPLETED
            pipeline.updated_at = self._now_iso()
            self._persist_pipeline(pipeline)

            self._publish_event(
                event_type="Agent:PipelineCompleted",
                aggregate_id=pipeline.id,
                payload={
                    "pipeline_id": pipeline.id,
                    "pipeline_name": pipeline.name,
                    "total_stages": len(pipeline.stages),
                },
            )
            logger.info("Pipeline '%s' completed (gate overridden at final stage)", pipeline_id)
            return

        pipeline.status = PipelineStatus.RUNNING
        pipeline.current_stage_index = next_index
        pipeline.updated_at = self._now_iso()

        self._publish_event(
            event_type="Agent:PipelineGateOverridden",
            aggregate_id=pipeline_id,
            payload={
                "pipeline_id": pipeline_id,
                "pipeline_name": pipeline.name,
                "overridden_stage_index": pipeline.current_stage_index - 1,
            },
        )

        await self._submit_next_stage(pipeline, next_index, result)
        self._persist_pipeline(pipeline)

        logger.info("Pipeline '%s' gate overridden, advancing to stage %d", pipeline_id, next_index)

    # ── Query Methods ─────────────────────────────────────────────────

    def get_pipeline(self, pipeline_id: str) -> dict:
        """Return pipeline state including current stage, status, and stage results.

        Requirement 7.8
        """
        pipeline = self._pipelines.get(pipeline_id)
        if pipeline is None:
            raise KeyError(f"Pipeline '{pipeline_id}' not found")

        return pipeline.model_dump(mode="json")

    def list_pipelines(self, status: Optional[PipelineStatus] = None) -> list[dict]:
        """List pipelines with optional status filter.

        Requirement 7.8
        """
        results: list[dict] = []
        for pipeline in self._pipelines.values():
            if status is not None and pipeline.status != status:
                continue
            results.append(pipeline.model_dump(mode="json"))

        # Sort by created_at descending
        results.sort(key=lambda p: p.get("created_at", ""), reverse=True)
        return results

    # ── Refinement Pipeline Helpers ──────────────────────────────────

    async def _handle_refinement_gate_failure(
        self,
        pipeline: Pipeline,
        backtest_result: TaskResult,
    ) -> None:
        """Handle gate failure for a refinement pipeline.

        When all key metrics (win_rate, profit_factor, max_drawdown) degraded,
        restore the original algorithm from backup and publish a
        ``RefinementRejected`` event.

        Requirement 3.6
        """
        output = backtest_result.output
        comparison = output.get("before_after_comparison", {})
        delta = comparison.get("delta", {})

        # Check if ALL key metrics degraded:
        #   win_rate delta < 0, profit_factor delta < 0, max_drawdown delta > 0
        #   (higher drawdown is worse)
        win_rate_degraded = delta.get("win_rate", 0) < 0
        profit_factor_degraded = delta.get("profit_factor", 0) < 0
        max_drawdown_degraded = delta.get("max_drawdown", 0) > 0
        all_degraded = win_rate_degraded and profit_factor_degraded and max_drawdown_degraded

        # Extract algorithm_name from the converter stage output
        converter_stage = pipeline.stages[0] if pipeline.stages else None
        converter_result = converter_stage.result if converter_stage else {}
        refinement_result = converter_result.get("refinement_result", {}) if converter_result else {}
        algorithm_name = refinement_result.get("algorithm_name", "")
        backup_key = refinement_result.get("original_source_backup_key", "")
        file_path = refinement_result.get("file_path", "")

        if all_degraded and backup_key:
            # Restore original algorithm from backup via tool registry
            try:
                await self._restore_algorithm_from_backup(backup_key, file_path)
                logger.info(
                    "Restored original algorithm '%s' from backup '%s' after gate failure",
                    algorithm_name,
                    backup_key,
                )
            except Exception as exc:
                logger.error(
                    "Failed to restore algorithm '%s' from backup '%s': %s",
                    algorithm_name,
                    backup_key,
                    exc,
                )

        # Publish RefinementRejected event
        self._publish_event(
            event_type="Agent:RefinementRejected",
            aggregate_id=pipeline.id,
            payload={
                "pipeline_id": pipeline.id,
                "algorithm_name": algorithm_name,
                "before_metrics": comparison.get("original", {}),
                "after_metrics": comparison.get("refined", {}),
                "all_metrics_degraded": all_degraded,
                "backup_restored": all_degraded and bool(backup_key),
            },
        )

    async def _restore_algorithm_from_backup(
        self, backup_key: str, file_path: str
    ) -> None:
        """Restore an algorithm file from an AgentMemory backup.

        Queries the agent registry for the converter agent's memory to
        retrieve the backup, then writes the original source via the tool
        registry.
        """
        from pathlib import Path

        converter = self._agent_registry.get("converter")
        if converter is None:
            raise RuntimeError("Converter agent not found in registry")

        memory = getattr(converter, "_memory", None)
        if memory is None:
            raise RuntimeError("Converter agent has no _memory attribute")

        results = await memory.query_knowledge(tags=["backup", "refinement"])
        for entry in results:
            if entry.get("key") == backup_key:
                original_code = entry["value"]["source_code"]
                filename = Path(file_path).name
                tool_registry = getattr(converter, "_tool_registry", None)
                if tool_registry:
                    await tool_registry.invoke(
                        "write_algorithm_file",
                        "pipeline_orchestrator",
                        {"filename": filename, "code": original_code},
                    )
                return

        raise RuntimeError(f"Backup key '{backup_key}' not found in agent memory")

    def _handle_refinement_pipeline_completed(
        self,
        pipeline: Pipeline,
        backtest_result: TaskResult,
    ) -> None:
        """Publish RefinementCompleted event and update improvement score gauge.

        Requirement 3.5, 3.7
        """
        output = backtest_result.output
        comparison = output.get("before_after_comparison", {})
        improvement_score = comparison.get("improvement_score", 0.0)

        # Extract algorithm_name from the converter stage output
        converter_stage = pipeline.stages[0] if pipeline.stages else None
        converter_result = converter_stage.result if converter_stage else {}
        refinement_result = converter_result.get("refinement_result", {}) if converter_result else {}
        algorithm_name = refinement_result.get("algorithm_name", "")

        self._publish_event(
            event_type="Agent:RefinementCompleted",
            aggregate_id=pipeline.id,
            payload={
                "pipeline_id": pipeline.id,
                "algorithm_name": algorithm_name,
                "before_metrics": comparison.get("original", {}),
                "after_metrics": comparison.get("refined", {}),
                "improvement_score": improvement_score,
            },
        )

        # Update Prometheus gauge (Req 3.7)
        if algorithm_name:
            agent_refinement_improvement_score.labels(
                algorithm_name=algorithm_name,
            ).set(improvement_score)

        logger.info(
            "Refinement pipeline '%s' completed for '%s' (improvement_score=%.3f)",
            pipeline.id,
            algorithm_name,
            improvement_score,
        )

    # ── Private Helpers ───────────────────────────────────────────────

    async def _submit_stage_tasks(
        self, pipeline: Pipeline, initial_payload: dict
    ) -> None:
        """Submit task(s) for the current pipeline stage(s).

        Supports parallel stage execution for independent stages (Requirement 7.10).
        Independent stages are consecutive stages starting from current_stage_index
        that share no data dependencies (i.e., stages without gates on the previous stage).
        For the default sequential pipeline, this submits one task at a time.
        """
        idx = pipeline.current_stage_index
        stages_to_submit = self._get_parallel_stages(pipeline, idx)

        for stage_idx in stages_to_submit:
            stage = pipeline.stages[stage_idx]
            payload = {**stage.task_payload_template, **initial_payload}

            task = AgentTask(
                type=stage.task_type,
                agent_name=stage.agent_name,
                priority=TaskPriority.NORMAL,
                payload=payload,
            )

            task_id = await self._task_queue.submit(task)
            stage.task_id = task_id
            stage.status = TaskStatus.QUEUED
            self._task_to_pipeline[task_id] = pipeline.id

    async def _submit_next_stage(
        self, pipeline: Pipeline, next_index: int, prev_result: TaskResult
    ) -> None:
        """Submit the next stage task with previous output merged into payload.

        Requirement 7.4
        """
        stages_to_submit = self._get_parallel_stages(pipeline, next_index)

        for stage_idx in stages_to_submit:
            stage = pipeline.stages[stage_idx]
            # Merge previous stage output into the task payload
            payload = {
                **stage.task_payload_template,
                **prev_result.output,
            }

            # Find the previous completed task for depends_on
            prev_task_id = prev_result.task_id

            task = AgentTask(
                type=stage.task_type,
                agent_name=stage.agent_name,
                priority=TaskPriority.NORMAL,
                payload=payload,
                depends_on=[prev_task_id] if prev_task_id else [],
            )

            task_id = await self._task_queue.submit(task)
            stage.task_id = task_id
            stage.status = TaskStatus.QUEUED
            self._task_to_pipeline[task_id] = pipeline.id

    def _get_parallel_stages(
        self, pipeline: Pipeline, start_index: int
    ) -> list[int]:
        """Determine which stages starting from start_index can run in parallel.

        Stages are considered independent (parallelizable) if they don't have
        a gate on the immediately preceding stage. In the default sequential
        pipeline, each stage depends on the previous, so this returns [start_index].

        Requirement 7.10
        """
        if start_index >= len(pipeline.stages):
            return []

        parallel = [start_index]

        # Check subsequent stages — if a stage has no gate and the previous
        # stage also has no gate, they can potentially run in parallel
        for i in range(start_index + 1, len(pipeline.stages)):
            prev_stage = pipeline.stages[i - 1]
            # If the previous stage has a gate, the current stage depends on
            # the gate evaluation, so it cannot run in parallel
            if prev_stage.gate is not None:
                break
            # If the current stage has already been submitted, skip
            if pipeline.stages[i].status != TaskStatus.PENDING:
                break
            parallel.append(i)

        return parallel

    def _find_stage_by_task_id(
        self, pipeline: Pipeline, task_id: str
    ) -> Optional[int]:
        """Find the stage index that owns the given task_id."""
        for i, stage in enumerate(pipeline.stages):
            if stage.task_id == task_id:
                return i
        return None

    def _now_iso(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    # ── Persistence ───────────────────────────────────────────────────

    def _persist_pipeline(self, pipeline: Pipeline) -> None:
        """Persist pipeline state to PostgreSQL via backend REST API.

        Requirement 7.8
        """
        url = f"{self._backend_url}/api/agents/pipelines"
        try:
            data = pipeline.model_dump(mode="json")
            requests.put(
                f"{url}/{pipeline.id}",
                json=data,
                timeout=5,
            )
        except requests.RequestException as exc:
            logger.warning(
                "Failed to persist pipeline '%s' to backend: %s",
                pipeline.id,
                exc,
            )

    # ── Event Publishing ──────────────────────────────────────────────

    def _publish_event(
        self,
        event_type: str,
        aggregate_id: str,
        payload: dict,
        correlation_id: Optional[str] = None,
    ) -> None:
        """Publish a TradingEvent to events:stream via EventPublisher."""
        from src.models.trading_event import TradingEvent

        event = TradingEvent(
            event_type=event_type,
            aggregate_id=aggregate_id,
            sequence_number=0,
            correlation_id=correlation_id or aggregate_id,
            payload=payload,
            source_service="strategy-engine",
        )
        try:
            self._event_publisher.publish(event)
        except Exception as exc:
            logger.warning("Failed to publish %s event: %s", event_type, exc)
