"""Shared test fixtures for the Execution Engine test suite."""

import pytest
from hypothesis import settings

from src.models import (
    Signal,
    SignalDirection,
    SignalMetadata,
    SignalMode,
    BOSType,
    Timeframe,
    TradingAccount,
    TradeExecutionResult,
    TradeExecutionStatus,
    RiskValidationResult,
)


# Configure Hypothesis default settings
settings.register_profile("default", max_examples=100)
settings.register_profile("ci", max_examples=200)
settings.load_profile("default")


@pytest.fixture
def sample_signal() -> Signal:
    """Create a sample signal for testing."""
    return Signal(
        id="550e8400-e29b-41d4-a716-446655440000",
        instrument="US30",
        direction=SignalDirection.BUY,
        entry_price=34500.50,
        stop_loss=34450.00,
        take_profit=34600.00,
        position_size=0.1,
        confidence_score=0.85,
        timeframe=Timeframe.FIFTEEN_MINUTES,
        order_block_id="ob-001",
        strategy_id="strat-001",
        mode=SignalMode.LIVE,
        metadata=SignalMetadata(
            bos_type=BOSType.BULLISH,
            liquidity_swept=True,
            session="new_york",
            spread_at_generation=2.5,
            volatility_ratio=1.2,
        ),
        created_at="2024-01-15T10:30:00Z",
    )


@pytest.fixture
def sample_trading_account() -> TradingAccount:
    """Create a sample trading account for testing."""
    return TradingAccount(
        id="660e8400-e29b-41d4-a716-446655440000",
        user_id="770e8400-e29b-41d4-a716-446655440000",
        metaapi_account_id="test-account-id",
        label="Demo Account",
        is_active=True,
        equity=10000.0,
        balance=10000.0,
        open_positions=2,
        daily_loss=50.0,
        total_lot_exposure=0.5,
    )


@pytest.fixture
def sample_trade_execution_result() -> TradeExecutionResult:
    """Create a sample trade execution result for testing."""
    return TradeExecutionResult(
        id="880e8400-e29b-41d4-a716-446655440000",
        signal_id="550e8400-e29b-41d4-a716-446655440000",
        account_id="660e8400-e29b-41d4-a716-446655440000",
        order_id=100001,
        fill_price=34501.00,
        execution_latency_ms=45.0,
        status=TradeExecutionStatus.FILLED,
        slippage=0.5,
        spread_at_execution=2.8,
        created_at="2024-01-15T10:30:01Z",
    )
