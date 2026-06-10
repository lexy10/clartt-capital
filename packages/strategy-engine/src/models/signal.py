from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, Field

from .timeframe import Timeframe


class SignalDirection(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class SignalMode(str, Enum):
    BACKTEST = "backtest"
    FORWARD_TEST = "forward_test"
    LIVE = "live"


class BOSType(str, Enum):
    BULLISH = "bullish"
    BEARISH = "bearish"


class EntryZone(BaseModel):
    price_high: float
    price_low: float
    timestamp: str  # ISO 8601


class ExitZone(BaseModel):
    type: Literal["stop_loss", "take_profit"]
    price: float
    timestamp: str  # ISO 8601


class SignalMetadata(BaseModel):
    bos_type: BOSType
    liquidity_swept: bool
    session: str
    spread_at_generation: float
    volatility_ratio: float
    entry_zone: Optional[EntryZone] = None
    exit_zone_sl: Optional[ExitZone] = None
    exit_zone_tp: Optional[ExitZone] = None


class Signal(BaseModel):
    id: str  # UUID
    instrument: str  # e.g. "US30"
    direction: SignalDirection
    entry_price: float
    stop_loss: float
    take_profit: float
    position_size: float  # lot size
    confidence_score: float = Field(ge=0.0, le=1.0)
    timeframe: Timeframe
    order_block_id: str
    strategy_id: str
    mode: SignalMode
    metadata: SignalMetadata
    exit_rules: Optional[dict] = None  # ExitRules config from strategy, passed to execution engine
    created_at: str  # ISO 8601
