"""Pydantic models for the Autonomous Trading Agents framework."""

from datetime import datetime
from enum import Enum
from typing import Optional
from uuid import uuid4

from pydantic import BaseModel, Field


# --- Enums ---


class AgentState(str, Enum):
    IDLE = "IDLE"
    PLANNING = "PLANNING"
    EXECUTING = "EXECUTING"
    WAITING_FOR_INPUT = "WAITING_FOR_INPUT"
    REVIEWING = "REVIEWING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    PAUSED = "PAUSED"


class TaskPriority(int, Enum):
    CRITICAL = 0
    HIGH = 1
    NORMAL = 2
    LOW = 3


class TaskStatus(str, Enum):
    PENDING = "PENDING"
    QUEUED = "QUEUED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    DEAD_LETTERED = "DEAD_LETTERED"
    CANCELLED = "CANCELLED"


class PipelineStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    ABORTED = "ABORTED"


class GateType(str, Enum):
    METRIC_THRESHOLD = "metric_threshold"
    HUMAN_APPROVAL = "human_approval"
    AUTO_PASS = "auto_pass"


class ApprovalStatus(str, Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    DENIED = "DENIED"
    EXPIRED = "EXPIRED"


class PerformanceGrade(str, Enum):
    A = "A"
    B = "B"
    C = "C"
    D = "D"
    F = "F"


class PromotionRecommendation(str, Enum):
    PROMOTE = "PROMOTE"
    DEMOTE = "DEMOTE"
    EXTEND = "EXTEND"


# --- Task Models ---


class AgentTask(BaseModel):
    """A unit of work submitted to the TaskQueue."""

    id: str = Field(default_factory=lambda: str(uuid4()))
    type: str
    agent_name: str
    priority: TaskPriority = TaskPriority.NORMAL
    payload: dict = Field(default_factory=dict)
    result: Optional[dict] = None
    status: TaskStatus = TaskStatus.PENDING
    depends_on: list[str] = Field(default_factory=list)
    retry_count: int = 0
    max_retries: int = 3
    error_message: Optional[str] = None
    task_timeout_seconds: int = 600
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    started_at: Optional[str] = None
    completed_at: Optional[str] = None


class TaskResult(BaseModel):
    """Result of a completed agent task."""

    task_id: str
    agent_name: str
    status: TaskStatus
    output: dict = Field(default_factory=dict)
    error: Optional[str] = None
    duration_seconds: float = 0.0


# --- Research Agent Models ---


class StrategyHypothesis(BaseModel):
    """Structured output from the Research Agent."""

    name: str
    description: str
    entry_rules: list[str]
    exit_rules: list[str]
    indicator_configurations: list[dict] = Field(default_factory=list)
    expected_market_conditions: list[str] = Field(default_factory=list)
    source_references: list[str] = Field(default_factory=list)
    confidence_estimate: float = Field(ge=0.0, le=1.0)
    target_instruments: list[str] = Field(default_factory=list)


class MarketAnalysisReport(BaseModel):
    """Structured output from the Research Agent's market_analysis task."""

    instruments: list[str]
    timeframe: str
    trend_assessment: dict = Field(default_factory=dict)
    volatility_regime: str
    key_levels: list[dict] = Field(default_factory=list)
    recommended_strategy_types: list[str] = Field(default_factory=list)
    analysis_timestamp: str = Field(default_factory=lambda: datetime.now().isoformat())


# --- Converter Agent Models ---


class ConversionResult(BaseModel):
    """Structured output from the Converter Agent."""

    algorithm_name: str
    file_path: str
    validation_passed: bool
    smoke_test_passed: bool
    retry_count: int = 0
    warnings: list[str] = Field(default_factory=list)


class RefinementResult(BaseModel):
    """Structured output from the Converter Agent's refine_strategy task."""

    algorithm_name: str
    file_path: str
    validation_passed: bool
    smoke_test_passed: bool
    retry_count: int = 0
    changes_summary: str = ""
    original_source_backup_key: str = ""
    warnings: list[str] = Field(default_factory=list)


class ConfigRefinementResult(BaseModel):
    """Structured output from the Converter Agent's refine_strategy_config task."""

    strategy_id: str
    algorithm_name: str
    original_params: dict = Field(default_factory=dict)
    updated_params: dict = Field(default_factory=dict)
    param_changes_summary: str = ""
    backtest_improvement: dict = Field(default_factory=dict)  # {before: {}, after: {}}
    config_backup_key: str = ""
    warnings: list[str] = Field(default_factory=list)


# --- Backtest Agent Models ---


class BacktestAnalysis(BaseModel):
    """Structured output from the Backtest Agent."""

    backtest_result: dict = Field(default_factory=dict)
    optimization_result: Optional[dict] = None
    walk_forward_result: Optional[dict] = None
    performance_grade: PerformanceGrade
    strengths: list[str] = Field(default_factory=list)
    weaknesses: list[str] = Field(default_factory=list)
    optimization_suggestions: list[dict] = Field(default_factory=list)
    recommended_next_steps: list[str] = Field(default_factory=list)
    iteration_count: int = 1
    comparison: Optional[dict] = None


# --- Forward Test Agent Models ---


class ForwardTestReport(BaseModel):
    """Structured output from the Forward Test Agent."""

    strategy_id: str
    algorithm_name: str
    evaluation_period_days: int
    total_signals_generated: int
    total_trades_simulated: int
    performance_metrics: dict = Field(default_factory=dict)
    backtest_comparison: dict = Field(default_factory=dict)
    market_conditions_during_test: str
    promotion_recommendation: PromotionRecommendation
    recommendation_reasoning: str
    extensions_used: int = 0


# --- Pipeline Models ---


class PipelineGate(BaseModel):
    """Gate between pipeline stages."""

    gate_type: GateType
    config: dict = Field(default_factory=dict)


class PipelineStage(BaseModel):
    """A single stage in a pipeline."""

    agent_name: str
    task_type: str
    task_payload_template: dict = Field(default_factory=dict)
    gate: Optional[PipelineGate] = None
    task_id: Optional[str] = None
    result: Optional[dict] = None
    status: TaskStatus = TaskStatus.PENDING


class Pipeline(BaseModel):
    """A multi-agent workflow."""

    id: str = Field(default_factory=lambda: str(uuid4()))
    name: str
    stages: list[PipelineStage]
    current_stage_index: int = 0
    status: PipelineStatus = PipelineStatus.PENDING
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now().isoformat())


# --- Approval Models ---


class ApprovalRequest(BaseModel):
    """A pending human approval request."""

    id: str = Field(default_factory=lambda: str(uuid4()))
    agent_name: str
    task_id: str
    action_description: str
    status: ApprovalStatus = ApprovalStatus.PENDING
    resolved_by: Optional[str] = None
    resolution_reason: Optional[str] = None
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    resolved_at: Optional[str] = None


# --- State Models ---


class StateTransition(BaseModel):
    """A single state transition record."""

    from_state: AgentState
    to_state: AgentState
    timestamp: str
    reason: Optional[str] = None


class AgentStateRecord(BaseModel):
    """Persisted agent state for PostgreSQL."""

    agent_name: str
    current_state: AgentState = AgentState.IDLE
    state_history: list[StateTransition] = Field(default_factory=list)
    last_heartbeat: Optional[str] = None
    current_task_id: Optional[str] = None
    error_count: int = 0
    failure_reason: Optional[str] = None
    failure_stack_trace: Optional[str] = None


# --- LLM Models ---


class LLMResponse(BaseModel):
    """Structured response from an LLM call."""

    content: str
    tool_calls: list[dict] = Field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    estimated_cost_usd: float = 0.0


class LLMUsageRecord(BaseModel):
    """Record of a single LLM API call for cost tracking."""

    id: str = Field(default_factory=lambda: str(uuid4()))
    agent_name: str
    provider: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    estimated_cost_usd: float
    task_id: Optional[str] = None
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())


# --- Config Models ---


class AgentConfig(BaseModel):
    """Per-agent configuration loaded from environment variables."""

    llm_provider: str = "openai"
    llm_model: str = "gpt-4o"
    llm_temperature: float = 0.7
    llm_max_tokens: int = 4096
    daily_budget_usd: float = 10.0
    requests_per_minute: int = 30


class AgentFrameworkConfig(BaseModel):
    """Global agent framework configuration."""

    default_llm_provider: str = "openai"
    default_llm_model: str = "gpt-4o"
    default_llm_temperature: float = 0.7
    default_llm_max_tokens: int = 4096
    global_daily_budget_usd: float = 50.0
    global_requests_per_minute: int = 100
    task_timeout_seconds: int = 600
    heartbeat_interval_seconds: int = 30
    memory_retention_days: int = 90
    sandbox_timeout_seconds: int = 30
    sandbox_memory_mb: int = 512
    pipeline_backtest_win_rate_threshold: float = 0.5
    pipeline_max_drawdown_threshold: float = 0.2
    pipeline_profit_factor_threshold: float = 1.0
    approval_timeout_hours: float = 24.0


# --- Sandbox Models ---


class SandboxResult(BaseModel):
    """Result of sandbox code execution."""

    success: bool
    stdout: str = ""
    stderr: str = ""
    return_value: Optional[dict] = None
    execution_time_seconds: float = 0.0
    error: Optional[str] = None
