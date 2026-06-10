from datetime import datetime, timezone
from enum import Enum
from typing import Optional
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


class SignalGeneratedPayload(BaseModel):
    signal_id: str
    instrument: str
    direction: str
    entry_price: float
    stop_loss: float
    take_profit: float
    position_size: float
    confidence_score: float
    timeframe: str
    strategy_id: str
    algorithm_name: str
    order_block_id: Optional[str] = None


class SignalPublishedPayload(BaseModel):
    signal_id: str
    instrument: str
    direction: str
    publish_timestamp: str
