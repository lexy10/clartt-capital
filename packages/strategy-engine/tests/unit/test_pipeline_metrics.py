"""Unit tests for pipeline Prometheus metrics integration in StrategyRunner."""

import logging
from unittest.mock import MagicMock

import pytest
from prometheus_client import REGISTRY

from src.metrics import (
    pipeline_active_strategies,
    pipeline_cycle_duration_seconds,
    pipeline_cycles_total,
    pipeline_errors_total,
    pipeline_signals_generated_total,
)
from src.models import Candle, StrategyConfig, Signal
from src.models.signal import SignalDirection, SignalMetadata, SignalMode, BOSType
from src.models.timeframe import Timeframe
from src.pipeline.strategy_runner import StrategyRunner


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_config(
    strategy_id="metrics-strat",
    name="Metrics Strategy",
    algorithm="ict_order_block",
    instruments=None,
    min_confidence_score=0.6,
) -> StrategyConfig:
    return StrategyConfig(
        id=strategy_id,
        name=name,
        algorithm=algorithm,
        instruments=instruments or ["US30"],
        timeframes=[Timeframe.ONE_MINUTE, Timeframe.FIFTEEN_MINUTES],
        higher_timeframe=Timeframe.FOUR_HOURS,
        entry_timeframe=Timeframe.FIFTEEN_MINUTES,
        risk_settings={
            "max_risk_per_trade_pct": 1.0,
            "max_daily_loss_pct": 3.0,
            "max_spread": 5.0,
            "max_slippage": 2.0,
            "volatility_multiplier": 1.5,
        },
        mode="live",
        min_confidence_score=min_confidence_score,
        enabled=True,
    )


def _make_signal(
    signal_id="sig-m1",
    ob_id="ob-m1",
    strategy_id="metrics-strat",
    confidence=0.82,
    direction=SignalDirection.BUY,
) -> Signal:
    return Signal(
        id=signal_id,
        instrument="US30",
        direction=direction,
        entry_price=38505.0,
        stop_loss=38490.0,
        take_profit=38535.0,
        position_size=0.05,
        confidence_score=confidence,
        timeframe=Timeframe.FIFTEEN_MINUTES,
        order_block_id=ob_id,
        strategy_id=strategy_id,
        mode=SignalMode.LIVE,
        metadata=SignalMetadata(
            bos_type=BOSType.BULLISH,
            liquidity_swept=True,
            session="new_york",
            spread_at_generation=2.5,
            volatility_ratio=0.65,
        ),
        created_at="2024-01-15T14:30:00+00:00",
    )


def _build_runner(
    redis_client=None,
    config_loader=None,
    registry=None,
    signal_publisher=None,
    signal_persister=None,
) -> StrategyRunner:
    return StrategyRunner(
        redis_client=redis_client or MagicMock(),
        config_loader=config_loader or MagicMock(),
        registry=registry or MagicMock(),
        signal_publisher=signal_publisher or MagicMock(),
        signal_persister=signal_persister or MagicMock(),
    )


def _get_counter_value(counter, labels: dict) -> float:
    """Get the current value of a prometheus Counter with given labels."""
    try:
        return counter.labels(**labels)._value.get()
    except Exception:
        return 0.0


def _get_gauge_value(gauge) -> float:
    """Get the current value of a prometheus Gauge."""
    try:
        return gauge._value.get()
    except Exception:
        return 0.0


def _get_histogram_count(histogram, labels: dict) -> float:
    """Get the sample count of a prometheus Histogram with given labels."""
    try:
        return histogram.labels(**labels)._sum.get()
    except Exception:
        return 0.0


# ---------------------------------------------------------------------------
# Test: counters increment after cycles and signals
# ---------------------------------------------------------------------------

class TestCycleAndSignalCounters:
    """Verify pipeline_cycles_total and pipeline_signals_generated_total increment."""

    def test_cycles_total_increments_after_cycle(self):
        config = _make_config()
        mock_algorithm = MagicMock()
        mock_algorithm.analyze.return_value = []

        mock_registry = MagicMock()
        mock_registry.get.return_value = mock_algorithm

        mock_redis = MagicMock()
        mock_redis.zrevrangebyscore.return_value = []

        runner = _build_runner(redis_client=mock_redis, registry=mock_registry)

        labels = {"strategy_name": config.name, "instrument": config.instruments[0]}
        before = _get_counter_value(pipeline_cycles_total, labels)

        runner._run_analysis_cycle(config, config.instruments[0])

        after = _get_counter_value(pipeline_cycles_total, labels)
        assert after - before == 1.0

    def test_signals_generated_total_increments_per_signal(self):
        config = _make_config()
        sig1 = _make_signal(signal_id="s1", ob_id="ob-1", confidence=0.9)
        sig2 = _make_signal(
            signal_id="s2", ob_id="ob-2", confidence=0.85,
            direction=SignalDirection.SELL,
        )

        mock_algorithm = MagicMock()
        mock_algorithm.analyze.return_value = [sig1, sig2]

        mock_registry = MagicMock()
        mock_registry.get.return_value = mock_algorithm

        mock_redis = MagicMock()
        mock_redis.zrevrangebyscore.return_value = []

        runner = _build_runner(redis_client=mock_redis, registry=mock_registry)

        buy_labels = {
            "strategy_name": config.name,
            "instrument": config.instruments[0],
            "direction": "BUY",
        }
        sell_labels = {
            "strategy_name": config.name,
            "instrument": config.instruments[0],
            "direction": "SELL",
        }
        buy_before = _get_counter_value(pipeline_signals_generated_total, buy_labels)
        sell_before = _get_counter_value(pipeline_signals_generated_total, sell_labels)

        runner._run_analysis_cycle(config, config.instruments[0])

        buy_after = _get_counter_value(pipeline_signals_generated_total, buy_labels)
        sell_after = _get_counter_value(pipeline_signals_generated_total, sell_labels)
        assert buy_after - buy_before == 1.0
        assert sell_after - sell_before == 1.0

    def test_cycles_total_increments_via_on_candle_update(self):
        config = _make_config()
        mock_algorithm = MagicMock()
        mock_algorithm.analyze.return_value = []

        mock_registry = MagicMock()
        mock_registry.get.return_value = mock_algorithm

        mock_config_loader = MagicMock()
        mock_config_loader.get_active_strategies.return_value = [config]

        mock_redis = MagicMock()
        mock_redis.zrevrangebyscore.return_value = []

        runner = _build_runner(
            redis_client=mock_redis,
            config_loader=mock_config_loader,
            registry=mock_registry,
        )

        labels = {"strategy_name": config.name, "instrument": config.instruments[0]}
        before = _get_counter_value(pipeline_cycles_total, labels)

        runner._on_candle_update("US30", "15m")

        after = _get_counter_value(pipeline_cycles_total, labels)
        assert after - before == 1.0


# ---------------------------------------------------------------------------
# Test: errors_total incremented on failure with correct labels
# ---------------------------------------------------------------------------

class TestErrorsCounter:
    """Verify pipeline_errors_total incremented on failure with correct labels."""

    def test_errors_total_incremented_on_failure(self):
        config = _make_config(name="Failing Metrics Strategy")

        mock_registry = MagicMock()
        mock_registry.get.side_effect = RuntimeError("analysis boom")

        mock_config_loader = MagicMock()
        mock_config_loader.get_active_strategies.return_value = [config]

        mock_redis = MagicMock()
        mock_redis.zrevrangebyscore.return_value = []

        runner = _build_runner(
            redis_client=mock_redis,
            config_loader=mock_config_loader,
            registry=mock_registry,
        )

        labels = {
            "strategy_name": "Failing Metrics Strategy",
            "error_type": "RuntimeError",
        }
        before = _get_counter_value(pipeline_errors_total, labels)

        runner._on_candle_update("US30", "15m")

        after = _get_counter_value(pipeline_errors_total, labels)
        assert after - before == 1.0

    def test_errors_total_uses_correct_exception_type(self):
        config = _make_config(name="ValueError Strategy")

        mock_registry = MagicMock()
        mock_registry.get.side_effect = ValueError("bad value")

        mock_config_loader = MagicMock()
        mock_config_loader.get_active_strategies.return_value = [config]

        mock_redis = MagicMock()
        mock_redis.zrevrangebyscore.return_value = []

        runner = _build_runner(
            redis_client=mock_redis,
            config_loader=mock_config_loader,
            registry=mock_registry,
        )

        labels = {
            "strategy_name": "ValueError Strategy",
            "error_type": "ValueError",
        }
        before = _get_counter_value(pipeline_errors_total, labels)

        runner._on_candle_update("US30", "15m")

        after = _get_counter_value(pipeline_errors_total, labels)
        assert after - before == 1.0


# ---------------------------------------------------------------------------
# Test: cycle_duration_seconds records timing
# ---------------------------------------------------------------------------

class TestCycleDurationHistogram:
    """Verify pipeline_cycle_duration_seconds records timing for each cycle."""

    def test_duration_recorded_after_cycle(self):
        config = _make_config(name="Duration Strategy")
        mock_algorithm = MagicMock()
        mock_algorithm.analyze.return_value = []

        mock_registry = MagicMock()
        mock_registry.get.return_value = mock_algorithm

        mock_redis = MagicMock()
        mock_redis.zrevrangebyscore.return_value = []

        runner = _build_runner(redis_client=mock_redis, registry=mock_registry)

        labels = {"strategy_name": "Duration Strategy"}
        sum_before = pipeline_cycle_duration_seconds.labels(**labels)._sum.get()

        runner._run_analysis_cycle(config, config.instruments[0])

        sum_after = pipeline_cycle_duration_seconds.labels(**labels)._sum.get()
        # Duration should have increased (cycle took some time > 0)
        assert sum_after > sum_before


# ---------------------------------------------------------------------------
# Test: active_strategies gauge set correctly
# ---------------------------------------------------------------------------

class TestActiveStrategiesGauge:
    """Verify pipeline_active_strategies gauge reflects loaded strategy count."""

    def test_gauge_set_after_config_refresh(self):
        configs = [
            _make_config(strategy_id=f"strat-{i}", name=f"Strategy {i}")
            for i in range(3)
        ]

        mock_algorithm = MagicMock()
        mock_algorithm.analyze.return_value = []

        mock_registry = MagicMock()
        mock_registry.get.return_value = mock_algorithm

        mock_config_loader = MagicMock()
        mock_config_loader.get_active_strategies.return_value = configs

        mock_redis = MagicMock()
        mock_redis.zrevrangebyscore.return_value = []

        runner = _build_runner(
            redis_client=mock_redis,
            config_loader=mock_config_loader,
            registry=mock_registry,
        )

        runner._on_candle_update("US30", "15m")

        assert _get_gauge_value(pipeline_active_strategies) == 3.0
