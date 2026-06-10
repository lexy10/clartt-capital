"""Pydantic models for candle endpoints."""

from typing import Optional
from pydantic import BaseModel


class HistoricalCandleRequest(BaseModel):
    broker_symbol: str
    account_id: Optional[str] = None  # Not needed for Deriv, kept for MetaAPI fallback
    timeframe: str  # 1m, 5m, 15m, 30m, 1h, 4h, 1d
    start_date: str  # ISO 8601
    end_date: str  # ISO 8601


class CandleResponse(BaseModel):
    instrument: str
    timeframe: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    timestamp: str  # ISO 8601


class StreamStartRequest(BaseModel):
    account_id: Optional[str] = None  # Not needed for Deriv
    symbols: list[str]  # deriv symbols
    symbol_map: dict[str, str] | None = None  # deriv_symbol -> instrument_symbol


class StreamStopRequest(BaseModel):
    account_id: Optional[str] = None  # Not needed for Deriv
