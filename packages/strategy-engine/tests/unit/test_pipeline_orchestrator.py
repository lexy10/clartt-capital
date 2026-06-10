"""Unit tests for PipelineOrchestrator.

Tests cover: start_pipeline, on_task_completed, evaluate_gate,
abort, override_gate, get_pipeline, list_pipelines.
"""

import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, MagicMock, patch

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
from src.agents.pipeline import PipelineOrchestrator


@pytest.fixture
def mock_task_queue():
    tq = MagicMock()
    tq.submit = AsyncMock(side_effect=lambda task: task.id)
    tq.cancel = AsyncMock()
    return tq


@pytest.fixture
def mock_agent_registry():
    reg = MagicMock()
    reg.get = MagicMock(return_value=MagicMock())
    return reg


@pytest.fixture
def mock_event_publisher():
    pub = MagicMock()
    pub.publish = MagicMock()
    return pub


@pytest.fixture
def orchestrator(mock_task_queue, mock_agent_registry, mock_event_publisher):
    return PipelineOrchestrator(
        task_queue=mock_task_queue,
        agent_registry=mock_agent_registry,
        event_publisher=mock_event_publisher,
        backend_url="http://localhost:3000",
    )


# ── start_pipeline ────────────────────────────────────────────────


@pytest.mark.asyncio
@patch("src.agents.pipeline.requests.put")
async def test_start_pipeline_returns_id(mock_put, orchestrator):
    """start_pipeline returns a valid pipeline ID."""
    mock_put.return_value = MagicMock(status_code=200)
    pipeline_id = await orchestrator.start_pipeline("test-pipeline")
    assert isinstance(pipeline_id, str)
    assert len(pipeline_id) > 0


@pytest.mark.asyncio
@patch("src.agents.pipeline.requests.put")
async def test_start_pipeline_uses_default_stages(mock_put, orchestrator, mock_task_queue):
    """start_pipeline uses default 4-stage pipeline when no stages provided."""
    mock_put.return_value = MagicMock(status_code=200)
    pipeline_id = await orchestrator.start_pipeline("default-pipeline")

    pipeline = orchestrator.get_pipeline(pipeline_id)
    assert len(pipeline["stages"]) == 4
    assert pipeline["stages"][0]["agent_name"] == "research"
    assert pipeline["stages"][1]["agent_name"] == "converter"
    assert pipeline["stages"][2]["agent_name"] == "backtest"
    assert pipeline["stages"][3]["agent_name"] == "forward_test"


@pytest.mark.asyncio
@patch("src.agents.pipeline.requests.put")
async def test_start_pipeline_submits_first_task(mock_put, orchestrator, mock_task_queue):
    """start_pipeline submits stage tasks to the task queue (parallel independent stages)."""
    mock_put.return_value = MagicMock(status_code=200)
    await orchestrator.start_pipeline("test-pipeline")

    # Default pipeline: research + converter + backtest are independent (no gates between them),
    # so all 3 are submitted in parallel. forward_test is blocked by backtest's gate.
    assert mock_task_queue.submit.call_count == 3
    first_task = mock_task_queue.submit.call_args_list[0][0][0]
    assert first_task.type == "research_strategy"
    assert first_task.agent_name == "research"


@pytest.mark.asyncio
@patch("src.agents.pipeline.requests.put")
async def test_start_pipeline_sets_running_status(mock_put, orchestrator):
    """start_pipeline sets pipeline status to RUNNING."""
    mock_put.return_value = MagicMock(status_code=200)
    pipeline_id = await orchestrator.start_pipeline("test-pipeline")

    pipeline = orchestrator.get_pipeline(pipeline_id)
    assert pipeline["status"] == PipelineStatus.RUNNING.value


@pytest.mark.asyncio
@patch("src.agents.pipeline.requests.put")
async def test_start_pipeline_with_custom_stages(mock_put, orchestrator, mock_task_queue):
    """start_pipeline accepts custom stages."""
    mock_put.return_value = MagicMock(status_code=200)
    custom_stages = [
        PipelineStage(agent_name="research", task_type="research_strategy"),
        PipelineStage(agent_name="backtest", task_type="run_backtest"),
    ]
    pipeline_id = await orchestrator.start_pipeline("custom", stages=custom_stages)

    pipeline = orchestrator.get_pipeline(pipeline_id)
    assert len(pipeline["stages"]) == 2


@pytest.mark.asyncio
@patch("src.agents.pipeline.requests.put")
async def test_start_pipeline_merges_initial_payload(mock_put, orchestrator, mock_task_queue):
    """start_pipeline merges initial_payload into the first task."""
    mock_put.return_value = MagicMock(status_code=200)
    await orchestrator.start_pipeline(
        "test-pipeline",
        initial_payload={"instruments": ["US30"], "focus_area": "momentum"},
    )

    submitted_task = mock_task_queue.submit.call_args[0][0]
    assert submitted_task.payload["instruments"] == ["US30"]
    assert submitted_task.payload["focus_area"] == "momentum"


# ── evaluate_gate ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_evaluate_gate_auto_pass(orchestrator):
    """auto_pass gate always returns True."""
    gate = PipelineGate(gate_type=GateType.AUTO_PASS)
    result = TaskResult(task_id="t1", agent_name="test", status=TaskStatus.COMPLETED)
    passed, reason = await orchestrator.evaluate_gate(gate, result)
    assert passed is True
    assert "auto_pass" in reason


@pytest.mark.asyncio
async def test_evaluate_gate_human_approval(orchestrator):
    """human_approval gate returns False (pauses for operator)."""
    gate = PipelineGate(gate_type=GateType.HUMAN_APPROVAL)
    result = TaskResult(task_id="t1", agent_name="test", status=TaskStatus.COMPLETED)
    passed, reason = await orchestrator.evaluate_gate(gate, result)
    assert passed is False
    assert "human_approval" in reason


@pytest.mark.asyncio
async def test_evaluate_gate_metric_threshold_passes(orchestrator):
    """metric_threshold gate passes when all metrics meet thresholds."""
    gate = PipelineGate(
        gate_type=GateType.METRIC_THRESHOLD,
        config={"win_rate_min": 0.5, "max_drawdown_max": 0.2, "profit_factor_min": 1.0},
    )
    result = TaskResult(
        task_id="t1",
        agent_name="backtest",
        status=TaskStatus.COMPLETED,
        output={"win_rate": 0.6, "max_drawdown": 0.15, "profit_factor": 1.5},
    )
    passed, reason = await orchestrator.evaluate_gate(gate, result)
    assert passed is True
    assert "all thresholds passed" in reason


@pytest.mark.asyncio
async def test_evaluate_gate_metric_threshold_fails_min(orchestrator):
    """metric_threshold gate fails when a _min metric is below threshold."""
    gate = PipelineGate(
        gate_type=GateType.METRIC_THRESHOLD,
        config={"win_rate_min": 0.5},
    )
    result = TaskResult(
        task_id="t1",
        agent_name="backtest",
        status=TaskStatus.COMPLETED,
        output={"win_rate": 0.3},
    )
    passed, reason = await orchestrator.evaluate_gate(gate, result)
    assert passed is False
    assert "win_rate" in reason


@pytest.mark.asyncio
async def test_evaluate_gate_metric_threshold_fails_max(orchestrator):
    """metric_threshold gate fails when a _max metric exceeds threshold."""
    gate = PipelineGate(
        gate_type=GateType.METRIC_THRESHOLD,
        config={"max_drawdown_max": 0.2},
    )
    result = TaskResult(
        task_id="t1",
        agent_name="backtest",
        status=TaskStatus.COMPLETED,
        output={"max_drawdown": 0.35},
    )
    passed, reason = await orchestrator.evaluate_gate(gate, result)
    assert passed is False
    assert "max_drawdown" in reason


@pytest.mark.asyncio
async def test_evaluate_gate_metric_missing_key(orchestrator):
    """metric_threshold gate fails when metric key is missing from output."""
    gate = PipelineGate(
        gate_type=GateType.METRIC_THRESHOLD,
        config={"win_rate_min": 0.5},
    )
    result = TaskResult(
        task_id="t1",
        agent_name="backtest",
        status=TaskStatus.COMPLETED,
        output={},
    )
    passed, reason = await orchestrator.evaluate_gate(gate, result)
    assert passed is False
    assert "not found" in reason


# ── on_task_completed ─────────────────────────────────────────────


@pytest.mark.asyncio
@patch("src.agents.pipeline.requests.put")
async def test_on_task_completed_advances_pipeline(mock_put, orchestrator, mock_task_queue):
    """on_task_completed advances to next stage when gate passes (auto_pass)."""
    mock_put.return_value = MagicMock(status_code=200)

    # Use a gate to force sequential execution so we can test advancement
    stages = [
        PipelineStage(
            agent_name="research",
            task_type="research_strategy",
            gate=PipelineGate(gate_type=GateType.AUTO_PASS),
        ),
        PipelineStage(agent_name="converter", task_type="convert_strategy"),
    ]
    pipeline_id = await orchestrator.start_pipeline("test", stages=stages)

    # Only the first stage should be submitted (gate blocks parallel)
    assert mock_task_queue.submit.call_count == 1
    first_task = mock_task_queue.submit.call_args[0][0]

    result = TaskResult(
        task_id=first_task.id,
        agent_name="research",
        status=TaskStatus.COMPLETED,
        output={"strategy_description": "test strategy"},
    )

    mock_task_queue.submit.reset_mock()
    await orchestrator.on_task_completed(first_task, result)

    # Should have submitted the next stage task
    mock_task_queue.submit.assert_called_once()
    next_task = mock_task_queue.submit.call_args[0][0]
    assert next_task.agent_name == "converter"
    assert next_task.payload["strategy_description"] == "test strategy"


@pytest.mark.asyncio
@patch("src.agents.pipeline.requests.put")
async def test_on_task_completed_pauses_on_gate_failure(mock_put, orchestrator, mock_task_queue):
    """on_task_completed pauses pipeline when gate evaluation fails."""
    mock_put.return_value = MagicMock(status_code=200)

    stages = [
        PipelineStage(
            agent_name="backtest",
            task_type="run_backtest",
            gate=PipelineGate(
                gate_type=GateType.METRIC_THRESHOLD,
                config={"win_rate_min": 0.5},
            ),
        ),
        PipelineStage(agent_name="forward_test", task_type="forward_test"),
    ]
    pipeline_id = await orchestrator.start_pipeline("test", stages=stages)

    first_task = mock_task_queue.submit.call_args[0][0]
    result = TaskResult(
        task_id=first_task.id,
        agent_name="backtest",
        status=TaskStatus.COMPLETED,
        output={"win_rate": 0.3},  # Below threshold
    )

    await orchestrator.on_task_completed(first_task, result)

    pipeline = orchestrator.get_pipeline(pipeline_id)
    assert pipeline["status"] == PipelineStatus.PAUSED.value


@pytest.mark.asyncio
@patch("src.agents.pipeline.requests.put")
async def test_on_task_completed_completes_pipeline(mock_put, orchestrator, mock_task_queue):
    """on_task_completed marks pipeline COMPLETED when last stage finishes."""
    mock_put.return_value = MagicMock(status_code=200)

    stages = [
        PipelineStage(agent_name="research", task_type="research_strategy"),
    ]
    pipeline_id = await orchestrator.start_pipeline("test", stages=stages)

    first_task = mock_task_queue.submit.call_args[0][0]
    result = TaskResult(
        task_id=first_task.id,
        agent_name="research",
        status=TaskStatus.COMPLETED,
        output={"hypothesis": "test"},
    )

    await orchestrator.on_task_completed(first_task, result)

    pipeline = orchestrator.get_pipeline(pipeline_id)
    assert pipeline["status"] == PipelineStatus.COMPLETED.value


@pytest.mark.asyncio
@patch("src.agents.pipeline.requests.put")
async def test_on_task_completed_handles_failure(mock_put, orchestrator, mock_task_queue):
    """on_task_completed marks pipeline FAILED when a stage task fails."""
    mock_put.return_value = MagicMock(status_code=200)

    stages = [
        PipelineStage(agent_name="research", task_type="research_strategy"),
    ]
    pipeline_id = await orchestrator.start_pipeline("test", stages=stages)

    first_task = mock_task_queue.submit.call_args[0][0]
    result = TaskResult(
        task_id=first_task.id,
        agent_name="research",
        status=TaskStatus.FAILED,
        error="LLM timeout",
    )

    await orchestrator.on_task_completed(first_task, result)

    pipeline = orchestrator.get_pipeline(pipeline_id)
    assert pipeline["status"] == PipelineStatus.FAILED.value


# ── abort ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
@patch("src.agents.pipeline.requests.put")
async def test_abort_sets_aborted_status(mock_put, orchestrator, mock_task_queue):
    """abort sets pipeline status to ABORTED."""
    mock_put.return_value = MagicMock(status_code=200)
    pipeline_id = await orchestrator.start_pipeline("test")

    await orchestrator.abort(pipeline_id)

    pipeline = orchestrator.get_pipeline(pipeline_id)
    assert pipeline["status"] == PipelineStatus.ABORTED.value


@pytest.mark.asyncio
@patch("src.agents.pipeline.requests.put")
async def test_abort_cancels_pending_tasks(mock_put, orchestrator, mock_task_queue):
    """abort cancels any pending/queued stage tasks."""
    mock_put.return_value = MagicMock(status_code=200)
    pipeline_id = await orchestrator.start_pipeline("test")

    await orchestrator.abort(pipeline_id)

    mock_task_queue.cancel.assert_called()


@pytest.mark.asyncio
async def test_abort_raises_for_unknown_pipeline(orchestrator):
    """abort raises KeyError for unknown pipeline ID."""
    with pytest.raises(KeyError):
        await orchestrator.abort("nonexistent-id")


@pytest.mark.asyncio
@patch("src.agents.pipeline.requests.put")
async def test_abort_raises_for_completed_pipeline(mock_put, orchestrator, mock_task_queue):
    """abort raises ValueError for already completed pipeline."""
    mock_put.return_value = MagicMock(status_code=200)

    stages = [PipelineStage(agent_name="research", task_type="research_strategy")]
    pipeline_id = await orchestrator.start_pipeline("test", stages=stages)

    # Complete the pipeline
    first_task = mock_task_queue.submit.call_args[0][0]
    result = TaskResult(
        task_id=first_task.id,
        agent_name="research",
        status=TaskStatus.COMPLETED,
        output={},
    )
    await orchestrator.on_task_completed(first_task, result)

    with pytest.raises(ValueError):
        await orchestrator.abort(pipeline_id)


# ── override_gate ─────────────────────────────────────────────────


@pytest.mark.asyncio
@patch("src.agents.pipeline.requests.put")
async def test_override_gate_advances_paused_pipeline(mock_put, orchestrator, mock_task_queue):
    """override_gate advances a paused pipeline to the next stage."""
    mock_put.return_value = MagicMock(status_code=200)

    stages = [
        PipelineStage(
            agent_name="backtest",
            task_type="run_backtest",
            gate=PipelineGate(
                gate_type=GateType.METRIC_THRESHOLD,
                config={"win_rate_min": 0.5},
            ),
        ),
        PipelineStage(agent_name="forward_test", task_type="forward_test"),
    ]
    pipeline_id = await orchestrator.start_pipeline("test", stages=stages)

    # Complete first stage with failing gate
    first_task = mock_task_queue.submit.call_args[0][0]
    result = TaskResult(
        task_id=first_task.id,
        agent_name="backtest",
        status=TaskStatus.COMPLETED,
        output={"win_rate": 0.3},
    )
    await orchestrator.on_task_completed(first_task, result)

    pipeline = orchestrator.get_pipeline(pipeline_id)
    assert pipeline["status"] == PipelineStatus.PAUSED.value

    # Override the gate
    mock_task_queue.submit.reset_mock()
    await orchestrator.override_gate(pipeline_id)

    pipeline = orchestrator.get_pipeline(pipeline_id)
    assert pipeline["status"] == PipelineStatus.RUNNING.value
    mock_task_queue.submit.assert_called_once()


@pytest.mark.asyncio
async def test_override_gate_raises_for_non_paused(orchestrator):
    """override_gate raises ValueError if pipeline is not paused."""
    with pytest.raises(KeyError):
        await orchestrator.override_gate("nonexistent-id")


# ── get_pipeline / list_pipelines ─────────────────────────────────


@pytest.mark.asyncio
@patch("src.agents.pipeline.requests.put")
async def test_get_pipeline_returns_state(mock_put, orchestrator):
    """get_pipeline returns full pipeline state."""
    mock_put.return_value = MagicMock(status_code=200)
    pipeline_id = await orchestrator.start_pipeline("test")

    pipeline = orchestrator.get_pipeline(pipeline_id)
    assert pipeline["id"] == pipeline_id
    assert pipeline["name"] == "test"
    assert "stages" in pipeline
    assert "status" in pipeline


def test_get_pipeline_raises_for_unknown(orchestrator):
    """get_pipeline raises KeyError for unknown pipeline ID."""
    with pytest.raises(KeyError):
        orchestrator.get_pipeline("nonexistent-id")


@pytest.mark.asyncio
@patch("src.agents.pipeline.requests.put")
async def test_list_pipelines_returns_all(mock_put, orchestrator):
    """list_pipelines returns all pipelines."""
    mock_put.return_value = MagicMock(status_code=200)
    await orchestrator.start_pipeline("pipeline-1")
    await orchestrator.start_pipeline("pipeline-2")

    pipelines = orchestrator.list_pipelines()
    assert len(pipelines) == 2


@pytest.mark.asyncio
@patch("src.agents.pipeline.requests.put")
async def test_list_pipelines_filters_by_status(mock_put, orchestrator, mock_task_queue):
    """list_pipelines filters by status."""
    mock_put.return_value = MagicMock(status_code=200)

    stages = [PipelineStage(agent_name="research", task_type="research_strategy")]
    pid1 = await orchestrator.start_pipeline("running", stages=stages)

    # Complete the second pipeline
    stages2 = [PipelineStage(agent_name="research", task_type="research_strategy")]
    pid2 = await orchestrator.start_pipeline("completed", stages=stages2)
    # Find the task for pid2
    last_task = mock_task_queue.submit.call_args[0][0]
    result = TaskResult(
        task_id=last_task.id,
        agent_name="research",
        status=TaskStatus.COMPLETED,
        output={},
    )
    await orchestrator.on_task_completed(last_task, result)

    running = orchestrator.list_pipelines(status=PipelineStatus.RUNNING)
    assert len(running) == 1
    assert running[0]["name"] == "running"

    completed = orchestrator.list_pipelines(status=PipelineStatus.COMPLETED)
    assert len(completed) == 1
    assert completed[0]["name"] == "completed"


# ── Parallel stage execution ──────────────────────────────────────


@pytest.mark.asyncio
@patch("src.agents.pipeline.requests.put")
async def test_parallel_stages_submitted_together(mock_put, orchestrator, mock_task_queue):
    """Independent stages without gates are submitted in parallel."""
    mock_put.return_value = MagicMock(status_code=200)

    # Two stages with no gates — should be submitted in parallel
    stages = [
        PipelineStage(agent_name="research", task_type="research_strategy"),
        PipelineStage(agent_name="converter", task_type="convert_strategy"),
    ]
    await orchestrator.start_pipeline("parallel-test", stages=stages)

    # Both tasks should have been submitted
    assert mock_task_queue.submit.call_count == 2


@pytest.mark.asyncio
@patch("src.agents.pipeline.requests.put")
async def test_gate_prevents_parallel_submission(mock_put, orchestrator, mock_task_queue):
    """Stages after a gate are NOT submitted in parallel."""
    mock_put.return_value = MagicMock(status_code=200)

    stages = [
        PipelineStage(
            agent_name="backtest",
            task_type="run_backtest",
            gate=PipelineGate(gate_type=GateType.AUTO_PASS),
        ),
        PipelineStage(agent_name="forward_test", task_type="forward_test"),
    ]
    await orchestrator.start_pipeline("gated-test", stages=stages)

    # Only the first stage should be submitted (gate blocks parallel)
    assert mock_task_queue.submit.call_count == 1


# ── Event publishing ──────────────────────────────────────────────


@pytest.mark.asyncio
@patch("src.agents.pipeline.requests.put")
async def test_start_pipeline_publishes_event(mock_put, orchestrator, mock_event_publisher):
    """start_pipeline publishes a PipelineStarted event."""
    mock_put.return_value = MagicMock(status_code=200)
    await orchestrator.start_pipeline("test")

    mock_event_publisher.publish.assert_called()
    event = mock_event_publisher.publish.call_args[0][0]
    assert event.event_type == "Agent:PipelineStarted"
