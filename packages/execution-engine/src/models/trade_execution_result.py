from enum import Enum
from typing import Optional

from pydantic import BaseModel


class TradeExecutionStatus(str, Enum):
    FILLED = "filled"
    REJECTED = "rejected"
    PARTIAL = "partial"
    ERROR = "error"


class TradeExecutionResult(BaseModel):
    id: str  # UUID
    signal_id: str
    account_id: str
    order_id: int  # Broker order ID
    fill_price: float
    execution_latency_ms: float
    status: TradeExecutionStatus
    rejection_reason: Optional[str] = None
    slippage: float
    spread_at_execution: float
    created_at: str  # ISO 8601
