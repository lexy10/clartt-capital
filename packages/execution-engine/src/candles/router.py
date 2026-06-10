"""FastAPI router for candle data endpoints."""

import logging

from fastapi import APIRouter, HTTPException

from .models import (
    CandleResponse,
    HistoricalCandleRequest,
    StreamStartRequest,
    StreamStopRequest,
)

logger = logging.getLogger("execution_engine.candles.router")

router = APIRouter(prefix="/api/candles", tags=["candles"])

# Set at startup via configure_candle_service()
_candle_service = None
_candle_streamer = None


def configure_candle_service(service) -> None:
    global _candle_service
    _candle_service = service


def configure_candle_streamer(streamer) -> None:
    global _candle_streamer
    _candle_streamer = streamer


@router.post("/historical", response_model=list[CandleResponse])
async def get_historical_candles(request: HistoricalCandleRequest) -> list[CandleResponse]:
    """Fetch historical candles (Deriv API, MetaAPI fallback, or stub in demo mode)."""
    if _candle_service is None:
        raise HTTPException(status_code=503, detail="Candle service not initialized")

    try:
        return await _candle_service.get_historical(request)
    except Exception as exc:
        logger.exception("Failed to fetch historical candles")
        raise HTTPException(status_code=500, detail=f"Failed to fetch candles: {exc}")


@router.post("/stream/start")
async def start_stream(request: StreamStartRequest):
    """Start streaming candles for an account's symbols."""
    if _candle_streamer is None:
        raise HTTPException(status_code=503, detail="Candle streamer not initialized")
    try:
        stream_key = request.account_id or "global"
        await _candle_streamer.start_stream(stream_key, request.symbols, request.symbol_map)
        return {"status": "streaming"}
    except Exception as exc:
        logger.exception("Failed to start candle stream")
        raise HTTPException(status_code=500, detail=f"Failed to start stream: {exc}")


@router.post("/stream/stop")
async def stop_stream(request: StreamStopRequest):
    """Stop streaming candles for an account."""
    if _candle_streamer is None:
        raise HTTPException(status_code=503, detail="Candle streamer not initialized")
    try:
        stream_key = request.account_id or "global"
        await _candle_streamer.stop_stream(stream_key)
        return {"status": "stopped"}
    except Exception as exc:
        logger.exception("Failed to stop candle stream")
        raise HTTPException(status_code=500, detail=f"Failed to stop stream: {exc}")


@router.get("/stream/status")
async def get_stream_status():
    """Get current streaming status for health checks."""
    if _candle_streamer is None:
        return {"active": False, "subscription_count": 0, "symbols": [], "accounts": []}
    try:
        return _candle_streamer.get_status()
    except Exception as exc:
        logger.exception("Failed to get stream status")
        raise HTTPException(status_code=500, detail=f"Failed to get status: {exc}")
