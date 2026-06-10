from datetime import datetime, timezone
from enum import Enum
from typing import List, Optional
from uuid import uuid4

from pydantic import BaseModel, Field


class TradingEventType(str, Enum):
    SignalGenerated = "SignalGenerated"
    SignalPublished = "SignalPublished"
    RiskEvaluated = "RiskEvaluated"
    TradeRequested = "TradeRequested"
    TradeExecuted = "TradeExecuted"
    TradeFailed = "TradeFailed"
    PositionOpened = "PositionOpened"
    PositionUpdated = "PositionUpdated"
    PositionClosed = "PositionClosed"
    AutopilotStateChanged = "AutopilotStateChanged"
    KillSwitchActivated = "KillSwitchActivated"
    KillSwitchDeactivated = "KillSwitchDeactivated"


class TradingEvent(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    event_type: str
    aggregate_id: str
    sequence_number: int
    correlation_id: Optional[str] = None
    payload: dict
    context_snapshot: Optional[dict] = None
    source_service: str
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    schema_version: int = 1


class RiskRuleEvaluation(BaseModel):
    rule: str
    result: bool
    threshold: float


class RiskEvaluatedPayload(BaseModel):
    signal_id: str
    account_id: str
    passed: bool
    rules_evaluated: List[RiskRuleEvaluation]
    rejection_reason: Optional[str] = None


class TradeRequestedPayload(BaseModel):
    signal_id: str
    account_id: str
    instrument: str
    direction: str
    requested_size: float
    broker_order_id: Optional[str] = None


class TradeExecutedPayload(BaseModel):
    signal_id: str
    account_id: str
    trade_id: str
    fill_price: float
    position_size: float
    execution_latency_ms: float
    slippage: float
    spread_at_execution: float


class TradeFailedPayload(BaseModel):
    signal_id: str
    account_id: str
    failure_reason: str
    error_code: str
    retry_count: int


class PositionOpenedPayload(BaseModel):
    position_id: str
    account_id: str
    trade_id: str
    instrument: str
    direction: str
    entry_price: float
    position_size: float


class PositionUpdatedPayload(BaseModel):
    position_id: str
    account_id: str
    current_price: float
    unrealized_pnl: float
    update_reason: str


class PositionClosedPayload(BaseModel):
    position_id: str
    account_id: str
    exit_price: float
    realized_pnl: float
    close_reason: str
    duration_seconds: float
