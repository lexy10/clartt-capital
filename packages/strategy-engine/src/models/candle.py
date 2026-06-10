from pydantic import BaseModel

from .timeframe import Timeframe


class Candle(BaseModel):
    instrument: str
    timeframe: Timeframe
    open: float
    high: float
    low: float
    close: float
    volume: float
    timestamp: str  # ISO 8601
