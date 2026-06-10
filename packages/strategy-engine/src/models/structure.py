from enum import Enum
from typing import Optional

from pydantic import BaseModel


class StructureType(str, Enum):
    HIGHER_HIGH = "higher_high"
    HIGHER_LOW = "higher_low"
    LOWER_HIGH = "lower_high"
    LOWER_LOW = "lower_low"


class StructurePoint(BaseModel):
    type: StructureType
    price: float
    timestamp: str  # ISO 8601
    candle_index: int


class BOSDirection(str, Enum):
    BULLISH = "bullish"
    BEARISH = "bearish"


class BOS(BaseModel):
    direction: BOSDirection
    break_price: float
    break_timestamp: str  # ISO 8601
    from_point: StructurePoint
    to_point: StructurePoint


class OrderBlock(BaseModel):
    id: str  # UUID
    instrument: str
    direction: BOSDirection
    zone_high: float
    zone_low: float
    formation_timestamp: str  # ISO 8601
    bos_id: Optional[str] = None
    is_valid: bool
    partial_mitigation_count: int = 0
    formation_candle_index: int | None = None
