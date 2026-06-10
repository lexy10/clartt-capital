"""FastAPI router for the Autonomous Trading Agents framework.

Provides REST endpoints under ``/agents`` for agent management, task
management, pipeline management, approval management, configuration,
and a JSON metrics summary.

IMPORTANT: Fixed-path routes (tasks, pipelines, approvals, config, metrics)
are defined BEFORE the ``/{name}`` wildcard routes to prevent FastAPI from
matching them as agent names.

Requirements: 15.1–15.6, 8.6
"""

import logging
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from src.agents.models import (
    AgentTask,
    PipelineStatus,
    TaskPriority,
    TaskStatus,
)

logger = logging.getLogger("strategy_engine.agents.api")

# ---------------------------------------------------------------------------
# Pydantic request / response models
# ---------------------------------------------------------------------------


class SubmitTaskRequest(BaseModel):
    type: str
    agent_name: str
    priority: TaskPriority = TaskPriority.NORMAL
    payload: dict = Field(default_factory=dict)
    depends_on: list[str] = Field(default_factory=list)
    max_retries: int = 3
    task_timeout_seconds: int = 600


class StartPipelineRequest(BaseModel):
    name: str
    initial_payload: dict = Field(default_factory=dict)


class ApprovalActionRequest(BaseModel):
    resolved_by: str
    reason: str = ""


class UpdateConfigRequest(BaseModel):
    """Partial update — only supplied fields are applied."""
    default_llm_provider: Optional[str] = None
    default_llm_model: Optional[str] = None
    default_llm_temperature: Optional[float] = None
    default_llm_max_tokens: Optional[int] = None
    global_daily_budget_usd: Optional[float] = None
    global_requests_per_minute: Optional[int] = None
    task_timeout_seconds: Optional[int] = None
    heartbeat_interval_seconds: Optional[int] = None
    autonomy_mode: Optional[str] = None


class RefineRequest(BaseModel):
    """Request body for POST /agents/refine (Req 5.1, 5.5)."""
    algorithm_name: str
    strategy_id: str
    refinement_hints: list[str] = Field(default_factory=list)
    run_pipeline: bool = True


class RefineConfigRequest(BaseModel):
    """Request body for POST /agents/refine-config (Req 6.3)."""
    strategy_id: str
    refinement_hints: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Module-level dependency references set via ``setup_agents_router``
# ---------------------------------------------------------------------------

_registry: Any = None
_task_queue: Any = None
_pipeline: Any = None
_approval: Any = None
_llm_client: Any = None
_config: Any = None
_kill_switch: Any = None
_autonomy_manager: Any = None
_equity_monitor: Any = None
_correlation_guard: Any = None
_redis: Any = None
_tool_registry: Any = None

agents_router = APIRouter(prefix="/agents", tags=["agents"])


def setup_agents_router(
    *,
    registry: Any,
    task_queue: Any,
    pipeline_orchestrator: Any,
    approval_manager: Any,
    llm_client: Any = None,
    framework_config: Any = None,
    kill_switch: Any = None,
    autonomy_manager: Any = None,
    equity_monitor: Any = None,
    correlation_guard: Any = None,
    redis_client: Any = None,
    tool_registry: Any = None,
) -> None:
    """Inject dependencies into the agents router.

    Called once during application startup (e.g. from ``bootstrap.py``).
    """
    global _registry, _task_queue, _pipeline, _approval, _llm_client, _config
    global _kill_switch, _autonomy_manager, _equity_monitor, _correlation_guard
    global _redis, _tool_registry
    _registry = registry
    _task_queue = task_queue
    _pipeline = pipeline_orchestrator
    _approval = approval_manager
    _llm_client = llm_client
    _config = framework_config
    _kill_switch = kill_switch
    _autonomy_manager = autonomy_manager
    _equity_monitor = equity_monitor
    _correlation_guard = correlation_guard
    _redis = redis_client
    _tool_registry = tool_registry


def _require(dep: Any, name: str) -> Any:
    if dep is None:
        raise HTTPException(status_code=503, detail=f"{name} not initialized")
    return dep


# ═══════════════════════════════════════════════════════════════════════════
# Task management  (Req 15.2)
# MUST be defined before /{name} wildcard routes
# ═══════════════════════════════════════════════════════════════════════════


@agents_router.post("/tasks", status_code=201)
async def submit_task(body: SubmitTaskRequest) -> dict:
    """Submit a new task to the queue."""
    tq = _require(_task_queue, "TaskQueue")
    task = AgentTask(
        type=body.type,
        agent_name=body.agent_name,
        priority=body.priority,
        payload=body.payload,
        depends_on=body.depends_on,
        max_retries=body.max_retries,
        task_timeout_seconds=body.task_timeout_seconds,
    )
    task_id = await tq.submit(task)
    return {"task_id": task_id}


@agents_router.get("/tasks")
def list_tasks(
    status: Optional[str] = None,
    agent_name: Optional[str] = None,
) -> list[dict]:
    """List tasks with optional filters."""
    tq = _require(_task_queue, "TaskQueue")
    status_enum = None
    if status is not None:
        try:
            status_enum = TaskStatus(status)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid status '{status}'. Valid: {[s.value for s in TaskStatus]}",
            )
    tasks = tq.list_tasks(status=status_enum, agent_name=agent_name)
    return [t.model_dump(mode="json") for t in tasks]


@agents_router.get("/tasks/{task_id}")
def get_task(task_id: str) -> dict:
    """Get task status and result."""
    tq = _require(_task_queue, "TaskQueue")
    task = tq.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task '{task_id}' not found")
    return task.model_dump(mode="json")


@agents_router.delete("/tasks/{task_id}", status_code=200)
async def cancel_task(task_id: str) -> dict:
    """Cancel a task."""
    tq = _require(_task_queue, "TaskQueue")
    try:
        await tq.cancel(task_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Task '{task_id}' not found")
    return {"status": "cancelled", "task_id": task_id}


# ═══════════════════════════════════════════════════════════════════════════
# Pipeline management  (Req 15.3)
# ═══════════════════════════════════════════════════════════════════════════


@agents_router.post("/pipelines", status_code=201)
async def start_pipeline(body: StartPipelineRequest) -> dict:
    """Start a new pipeline."""
    po = _require(_pipeline, "PipelineOrchestrator")
    pipeline_id = await po.start_pipeline(
        name=body.name,
        initial_payload=body.initial_payload,
    )
    return {"pipeline_id": pipeline_id}


@agents_router.get("/pipelines")
def list_pipelines(status: Optional[str] = None) -> list[dict]:
    """List pipelines with optional status filter."""
    po = _require(_pipeline, "PipelineOrchestrator")
    status_enum = None
    if status is not None:
        try:
            status_enum = PipelineStatus(status)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid status '{status}'. Valid: {[s.value for s in PipelineStatus]}",
            )
    return po.list_pipelines(status=status_enum)


@agents_router.get("/pipelines/{pipeline_id}")
def get_pipeline(pipeline_id: str) -> dict:
    """Get pipeline status and stage results."""
    po = _require(_pipeline, "PipelineOrchestrator")
    try:
        return po.get_pipeline(pipeline_id)
    except KeyError:
        raise HTTPException(
            status_code=404, detail=f"Pipeline '{pipeline_id}' not found"
        )


@agents_router.post("/pipelines/{pipeline_id}/abort")
async def abort_pipeline(pipeline_id: str) -> dict:
    """Abort a running pipeline."""
    po = _require(_pipeline, "PipelineOrchestrator")
    try:
        await po.abort(pipeline_id)
    except KeyError:
        raise HTTPException(
            status_code=404, detail=f"Pipeline '{pipeline_id}' not found"
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"status": "aborted", "pipeline_id": pipeline_id}


# ═══════════════════════════════════════════════════════════════════════════
# Approval management  (Req 15.4)
# ═══════════════════════════════════════════════════════════════════════════


@agents_router.get("/approvals")
def list_approvals() -> list[dict]:
    """List pending approval requests."""
    am = _require(_approval, "ApprovalGateManager")
    return am.list_pending()


@agents_router.post("/approvals/{approval_id}/approve")
async def approve_action(approval_id: str, body: ApprovalActionRequest) -> dict:
    """Approve a pending action."""
    am = _require(_approval, "ApprovalGateManager")
    try:
        await am.approve(approval_id, resolved_by=body.resolved_by)
    except KeyError:
        raise HTTPException(
            status_code=404, detail=f"Approval '{approval_id}' not found"
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"status": "approved", "approval_id": approval_id}


@agents_router.post("/approvals/{approval_id}/deny")
async def deny_action(approval_id: str, body: ApprovalActionRequest) -> dict:
    """Deny a pending action."""
    am = _require(_approval, "ApprovalGateManager")
    try:
        await am.deny(
            approval_id,
            resolved_by=body.resolved_by,
            reason=body.reason,
        )
    except KeyError:
        raise HTTPException(
            status_code=404, detail=f"Approval '{approval_id}' not found"
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"status": "denied", "approval_id": approval_id}


# ═══════════════════════════════════════════════════════════════════════════
# Configuration  (Req 15.5)
# ═══════════════════════════════════════════════════════════════════════════


@agents_router.get("/config")
def get_config() -> dict:
    """Return current agent framework configuration."""
    from src.agents.config import load_framework_config

    cfg = _config if _config is not None else load_framework_config()
    result = cfg.model_dump(mode="json")

    # Include autonomy mode (Req 9.2, 9.14)
    if _autonomy_manager is not None:
        result["autonomy_mode"] = _autonomy_manager.get_mode().value
    else:
        result["autonomy_mode"] = "approval"

    return result


@agents_router.put("/config")
def update_config(body: UpdateConfigRequest) -> dict:
    """Update agent framework configuration (partial update).

    Only fields present in the request body are applied.
    """
    global _config
    from src.agents.config import load_framework_config

    cfg = _config if _config is not None else load_framework_config()
    updates = body.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")

    # Handle autonomy mode separately (Req 9.2)
    if "autonomy_mode" in updates and _autonomy_manager is not None:
        from src.agents.autonomy import AutonomyMode

        try:
            mode = AutonomyMode(updates.pop("autonomy_mode"))
            _autonomy_manager.set_mode(mode)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail="Invalid autonomy_mode. Valid: 'approval', 'full_autonomy'",
            )

    for key, value in updates.items():
        if hasattr(cfg, key):
            setattr(cfg, key, value)

    _config = cfg
    result = cfg.model_dump(mode="json")
    if _autonomy_manager is not None:
        result["autonomy_mode"] = _autonomy_manager.get_mode().value
    return result


# ═══════════════════════════════════════════════════════════════════════════
# Kill Switch  (Req 19.5–19.7)
# MUST be defined before /{name} wildcard routes
# ═══════════════════════════════════════════════════════════════════════════


@agents_router.post("/kill-switch/activate")
async def activate_kill_switch() -> dict:
    """Activate the global agent kill switch."""
    ks = _require(_kill_switch, "AgentKillSwitch")
    await ks.activate()
    return {"status": "active"}


@agents_router.post("/kill-switch/deactivate")
async def deactivate_kill_switch() -> dict:
    """Deactivate the global agent kill switch."""
    ks = _require(_kill_switch, "AgentKillSwitch")
    await ks.deactivate()
    return {"status": "inactive"}


@agents_router.get("/kill-switch")
def get_kill_switch_status() -> dict:
    """Return current kill switch state."""
    ks = _require(_kill_switch, "AgentKillSwitch")
    return {"active": ks.is_active()}


# ═══════════════════════════════════════════════════════════════════════════
# Equity Pause  (Req 23.7)
# ═══════════════════════════════════════════════════════════════════════════


@agents_router.get("/equity-pause")
def list_equity_pause() -> list[dict]:
    """List all accounts with equity pause status."""
    em = _require(_equity_monitor, "EquityCurveMonitor")
    return em.list_accounts_status()


@agents_router.post("/equity-pause/{account_id}/deactivate")
def deactivate_equity_pause(account_id: str) -> dict:
    """Manually deactivate equity pause for an account."""
    em = _require(_equity_monitor, "EquityCurveMonitor")
    em.manual_deactivate(account_id)
    return {"status": "deactivated", "account_id": account_id}


# ═══════════════════════════════════════════════════════════════════════════
# Metrics summary  (Req 8.6)
# ═══════════════════════════════════════════════════════════════════════════


@agents_router.get("/metrics")
def metrics_summary() -> dict:
    """JSON summary of agent statuses, queue depth, active pipelines, LLM usage."""
    result: dict = {
        "agents": [],
        "queue_depth": {},
        "active_pipelines": 0,
        "llm_usage": {},
    }

    if _registry is not None:
        result["agents"] = _registry.list_agents()

    if _task_queue is not None:
        try:
            result["queue_depth"] = _task_queue.get_queue_depth()
        except Exception:
            result["queue_depth"] = {}

    if _pipeline is not None:
        try:
            running = _pipeline.list_pipelines(status=PipelineStatus.RUNNING)
            result["active_pipelines"] = len(running)
        except Exception:
            result["active_pipelines"] = 0

    if _llm_client is not None and _registry is not None:
        usage: dict[str, dict] = {}
        try:
            for info in result["agents"]:
                name = info.get("name", "")
                if name:
                    usage[name] = _llm_client.get_usage(name)
        except Exception:
            pass
        result["llm_usage"] = usage

    return result


# ═══════════════════════════════════════════════════════════════════════════
# Strategy Refinement  (Req 5.1–5.5)
# MUST be defined before /{name} wildcard routes
# ═══════════════════════════════════════════════════════════════════════════


@agents_router.post("/refine", status_code=201)
async def refine_strategy(body: RefineRequest) -> dict:
    """Trigger strategy refinement via pipeline or standalone task."""
    tq = _require(_task_queue, "TaskQueue")
    po = _require(_pipeline, "PipelineOrchestrator")
    tr = _require(_tool_registry, "ToolRegistry")

    # Validate algorithm exists via read_algorithm_source tool (Req 5.3)
    try:
        await tr.invoke(
            "read_algorithm_source",
            "api",
            {"algorithm_name": body.algorithm_name},
        )
    except Exception:
        raise HTTPException(
            status_code=404,
            detail=f"Algorithm '{body.algorithm_name}' not found",
        )

    # Check for duplicate in-progress refinement via Redis key (Req 5.4)
    if _redis is not None:
        refining_key = f"agents:refining:{body.algorithm_name}"
        if _redis.exists(refining_key):
            raise HTTPException(
                status_code=409,
                detail=f"Refinement already in progress for '{body.algorithm_name}'",
            )

    if body.run_pipeline:
        # Start full refinement pipeline: refine + backtest comparison (Req 5.5)
        pipeline_id = await po.start_pipeline(
            name="refinement",
            initial_payload={
                "algorithm_name": body.algorithm_name,
                "strategy_id": body.strategy_id,
                "refinement_hints": body.refinement_hints,
            },
        )
        return {"pipeline_id": pipeline_id}
    else:
        # Submit standalone refine_strategy task (Req 5.2)
        task = AgentTask(
            type="refine_strategy",
            agent_name="converter",
            payload={
                "algorithm_name": body.algorithm_name,
                "strategy_id": body.strategy_id,
                "refinement_hints": body.refinement_hints,
            },
        )
        task_id = await tq.submit(task)
        return {"task_id": task_id}


# ═══════════════════════════════════════════════════════════════════════════
# Strategy Config Refinement  (Req 6.3–6.6)
# MUST be defined before /{name} wildcard routes
# ═══════════════════════════════════════════════════════════════════════════


@agents_router.post("/refine-config", status_code=201)
async def refine_strategy_config(body: RefineConfigRequest) -> dict:
    """Trigger config-only refinement for a strategy."""
    tq = _require(_task_queue, "TaskQueue")
    tr = _require(_tool_registry, "ToolRegistry")

    # Validate strategy exists (Req 6.5)
    try:
        await tr.invoke(
            "read_strategy_config", "api",
            {"strategy_id": body.strategy_id},
        )
    except Exception:
        raise HTTPException(
            status_code=404,
            detail=f"Strategy '{body.strategy_id}' not found",
        )

    # Check for duplicate in-progress config refinement (Req 6.6)
    if _redis is not None:
        refining_key = f"agents:refining:config:{body.strategy_id}"
        if _redis.exists(refining_key):
            raise HTTPException(
                status_code=409,
                detail=f"Config refinement already in progress for strategy '{body.strategy_id}'",
            )

    task = AgentTask(
        type="refine_strategy_config",
        agent_name="converter",
        payload={
            "strategy_id": body.strategy_id,
            "refinement_hints": body.refinement_hints,
        },
    )
    task_id = await tq.submit(task)
    return {"task_id": task_id}


# ═══════════════════════════════════════════════════════════════════════════
# Agent management  (Req 15.1)
# These /{name} routes MUST come after all fixed-path routes above
# ═══════════════════════════════════════════════════════════════════════════


@agents_router.get("")
def list_agents() -> list[dict]:
    """List all registered agents with status."""
    reg = _require(_registry, "AgentRegistry")
    return reg.list_agents()


@agents_router.get("/{name}")
def get_agent(name: str) -> dict:
    """Get agent details and current state."""
    reg = _require(_registry, "AgentRegistry")
    try:
        agent = reg.get(name)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Agent '{name}' not found")
    sm = reg.get_state_machine(name)
    return {
        "name": agent.name(),
        "description": agent.description(),
        "state": sm.current_state.value,
        "supported_task_types": agent.supported_task_types(),
        "supported_tools": agent.supported_tools(),
    }


@agents_router.post("/{name}/start")
async def start_agent(name: str) -> dict:
    """Start an agent — transition IDLE → PLANNING."""
    reg = _require(_registry, "AgentRegistry")
    try:
        await reg.start(name)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Agent '{name}' not found")
    except Exception as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"status": "started", "agent": name}


@agents_router.post("/{name}/stop")
async def stop_agent(name: str) -> dict:
    """Stop an agent — transition to IDLE."""
    reg = _require(_registry, "AgentRegistry")
    try:
        await reg.stop(name)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Agent '{name}' not found")
    except Exception as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"status": "stopped", "agent": name}


@agents_router.post("/{name}/pause")
async def pause_agent(name: str) -> dict:
    """Pause an agent."""
    reg = _require(_registry, "AgentRegistry")
    try:
        await reg.pause(name)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Agent '{name}' not found")
    except Exception as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"status": "paused", "agent": name}


@agents_router.post("/{name}/resume")
async def resume_agent(name: str) -> dict:
    """Resume a paused agent."""
    reg = _require(_registry, "AgentRegistry")
    try:
        await reg.resume(name)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Agent '{name}' not found")
    except Exception as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"status": "resumed", "agent": name}


@agents_router.get("/{name}/health")
def agent_health(name: str) -> dict:
    """Health check for a specific agent."""
    reg = _require(_registry, "AgentRegistry")
    try:
        return reg.health_check(name)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Agent '{name}' not found")


@agents_router.post("/{name}/disable")
async def disable_agent(name: str) -> dict:
    """Disable a specific agent (Req 19.6)."""
    ks = _require(_kill_switch, "AgentKillSwitch")
    await ks.disable_agent(name)
    return {"status": "disabled", "agent": name}


@agents_router.post("/{name}/enable")
async def enable_agent(name: str) -> dict:
    """Re-enable a specific agent (Req 19.7)."""
    ks = _require(_kill_switch, "AgentKillSwitch")
    await ks.enable_agent(name)
    return {"status": "enabled", "agent": name}
