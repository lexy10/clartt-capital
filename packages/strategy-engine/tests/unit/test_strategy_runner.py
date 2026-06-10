"""Unit tests for StrategyRunner."""

import json
import logging
import threading
import time
from unittest.mock import MagicMock, patch, call

import pytest

from src.models import Candle, StrategyConfig, Signal
from src.models.signal import SignalDirection, SignalMetadata, SignalMode, BOSType
from src.models.timeframe import Timeframe
from src.pipeline.strategy_runner import (
    StrategyRunner,
    CANDLE_CHANNEL,
    FAILURE_WARNING_THRESHOLD,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_config(
    strategy_id="strat-1",
    name="Test Strategy",
    algorithm="ict_order_block",
    instruments=None,
    min_confidence_score=0.6,
    enabled=True,
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
        enabled=enabled,
    )


def _make_signal(
    signal_id="sig-001",
    ob_id="ob-001",
    strategy_id="strat-1",
    confidence=0.82,
) -> Signal:
    return Signal(
        id=signal_id,
        instrument="US30",
        direction=SignalDirection.BUY,
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


def _make_candle_json(instrument="US30", timeframe="15m") -> bytes:
    return json.dumps({
        "instrument": instrument,
        "timeframe": timeframe,
        "open": 38500.0,
        "high": 38520.0,
        "low": 38490.0,
        "close": 38510.0,
        "volume": 1234,
        "timestamp": "2024-01-15T14:30:00+00:00",
    }).encode()


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


# ---------------------------------------------------------------------------
# Test: subscribes/unsubscribes on start/stop
# ---------------------------------------------------------------------------

class TestSubscribesOnStartStop:
    """StrategyRunner subscribes to candles:updates on start and unsubscribes on stop."""

    def test_subscribes_and_unsubscribes(self):
        mock_redis = MagicMock()
        mock_pubsub = MagicMock()
        mock_redis.pubsub.return_value = mock_pubsub

        # get_message returns None once then we stop
        call_count = 0

        def _get_message(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                runner._stop_event.set()
            return None

        mock_pubsub.get_message.side_effect = _get_message

        runner = _build_runner(redis_client=mock_redis)
        runner.start()
        # Wait for the thread to finish
        runner._thread.join(timeout=3.0)

        mock_pubsub.subscribe.assert_called_with(CANDLE_CHANNEL)
        mock_pubsub.unsubscribe.assert_called_with(CANDLE_CHANNEL)
        mock_pubsub.close.assert_called_once()


# ---------------------------------------------------------------------------
# Test: completes in-progress cycle before shutdown
# ---------------------------------------------------------------------------

class TestCompletesInProgressCycleBeforeShutdown:
    """stop() waits for an in-progress analysis cycle to complete."""

    def test_waits_for_in_progress_cycle(self):
        cycle_started = threading.Event()
        cycle_can_finish = threading.Event()

        config = _make_config()
        mock_config_loader = MagicMock()
        mock_config_loader.get_active_strategies.return_value = [config]

        mock_algorithm = MagicMock()
        signal = _make_signal()

        def slow_analyze(entry, structure, trend, cfg, **kwargs):
            cycle_started.set()
            cycle_can_finish.wait(timeout=5.0)
            return [signal]

        mock_algorithm.analyze.side_effect = slow_analyze

        mock_registry = MagicMock()
        mock_registry.get.return_value = mock_algorithm

        mock_redis = MagicMock()

        candle = Candle(
            instrument="US30",
            timeframe=Timeframe.FIFTEEN_MINUTES,
            open=38500.0, high=38520.0, low=38490.0, close=38510.0,
            volume=1234, timestamp="2024-01-15T14:30:00+00:00",
        )

        runner = _build_runner(
            redis_client=mock_redis,
            config_loader=mock_config_loader,
            registry=mock_registry,
        )
        runner._fetch_candles_from_backend = MagicMock(return_value=[candle])

        # Directly call _on_candle_update in a thread to simulate in-progress cycle
        t = threading.Thread(target=runner._on_candle_update, args=("US30", "15m"))
        t.start()

        cycle_started.wait(timeout=3.0)
        assert runner._in_progress.is_set()

        # Now let the cycle finish
        cycle_can_finish.set()
        t.join(timeout=3.0)

        assert not runner._in_progress.is_set()


# ---------------------------------------------------------------------------
# Test: reconnects after connection drop
# ---------------------------------------------------------------------------

class TestReconnectsAfterConnectionDrop:
    """StrategyRunner reconnects pub/sub after a connection error."""

    def test_reconnects_on_connection_error(self):
        mock_redis = MagicMock()
        attempt = {"count": 0}

        def _pubsub_factory():
            attempt["count"] += 1
            ps = MagicMock()
            if attempt["count"] == 1:
                # First connection drops immediately
                ps.subscribe.side_effect = ConnectionError("Connection lost")
            else:
                # Second connection works, then we stop
                call_count = {"n": 0}

                def _get_msg(**kwargs):
                    call_count["n"] += 1
                    if call_count["n"] >= 2:
                        runner._stop_event.set()
                    return None

                ps.get_message.side_effect = _get_msg
            return ps

        mock_redis.pubsub.side_effect = _pubsub_factory

        runner = _build_runner(redis_client=mock_redis)

        with patch("src.pipeline.strategy_runner.INITIAL_BACKOFF", 0.01), \
             patch("src.pipeline.strategy_runner.MAX_BACKOFF", 0.02):
            runner.start()
            runner._thread.join(timeout=5.0)

        # Should have created pubsub at least twice (reconnect)
        assert attempt["count"] >= 2


# ---------------------------------------------------------------------------
# Test: dispatches to correct algorithm via registry
# ---------------------------------------------------------------------------

class TestDispatchesToCorrectAlgorithm:
    """StrategyRunner looks up the algorithm from the registry and calls analyze."""

    def test_dispatches_via_registry(self):
        config = _make_config(algorithm="custom_algo")
        signal = _make_signal()

        mock_algorithm = MagicMock()
        mock_algorithm.analyze.return_value = [signal]

        mock_registry = MagicMock()
        mock_registry.get.return_value = mock_algorithm

        mock_redis = MagicMock()
        mock_redis.zrevrangebyscore.return_value = []

        runner = _build_runner(redis_client=mock_redis, registry=mock_registry)
        count = runner._run_analysis_cycle(config, config.instruments[0])

        mock_registry.get.assert_called_once_with("custom_algo")
        mock_algorithm.analyze.assert_called_once()
        assert count == 1


# ---------------------------------------------------------------------------
# Test: handles unknown algorithm (KeyError)
# ---------------------------------------------------------------------------

class TestHandlesUnknownAlgorithm:
    """StrategyRunner handles KeyError when algorithm is not in registry."""

    def test_unknown_algorithm_raises_key_error(self):
        config = _make_config(algorithm="nonexistent_algo")

        mock_registry = MagicMock()
        mock_registry.get.side_effect = KeyError("Unknown algorithm 'nonexistent_algo'")

        mock_redis = MagicMock()
        mock_redis.zrevrangebyscore.return_value = []

        runner = _build_runner(redis_client=mock_redis, registry=mock_registry)

        with pytest.raises(KeyError):
            runner._run_analysis_cycle(config, config.instruments[0])

    def test_unknown_algorithm_isolated_in_on_candle_update(self, caplog):
        """KeyError from registry.get is caught in _on_candle_update."""
        config = _make_config(algorithm="nonexistent_algo")

        mock_config_loader = MagicMock()
        mock_config_loader.get_active_strategies.return_value = [config]

        mock_registry = MagicMock()
        mock_registry.get.side_effect = KeyError("Unknown algorithm 'nonexistent_algo'")

        mock_redis = MagicMock()
        mock_redis.zrevrangebyscore.return_value = []

        runner = _build_runner(
            redis_client=mock_redis,
            config_loader=mock_config_loader,
            registry=mock_registry,
        )

        with caplog.at_level(logging.ERROR):
            runner._on_candle_update("US30", "15m")

        assert runner._failure_counts[config.id] == 1
        assert "Analysis cycle failed" in caplog.text


# ---------------------------------------------------------------------------
# Test: passes StrategyConfig to algorithm.analyze
# ---------------------------------------------------------------------------

class TestPassesStrategyConfigToAnalyze:
    """StrategyRunner passes the StrategyConfig to algorithm.analyze."""

    def test_passes_config_to_analyze(self):
        config = _make_config()

        mock_algorithm = MagicMock()
        mock_algorithm.analyze.return_value = []

        mock_registry = MagicMock()
        mock_registry.get.return_value = mock_algorithm

        mock_redis = MagicMock()

        runner = _build_runner(redis_client=mock_redis, registry=mock_registry)

        # Mock _fetch_candles_from_backend to return a single candle
        candle = Candle(
            instrument="US30",
            timeframe=Timeframe.FIFTEEN_MINUTES,
            open=38500.0,
            high=38520.0,
            low=38490.0,
            close=38510.0,
            volume=1234,
            timestamp="2024-01-15T14:30:00+00:00",
        )
        runner._fetch_candles_from_backend = MagicMock(return_value=[candle])

        runner._run_analysis_cycle(config, config.instruments[0])

        call_args = mock_algorithm.analyze.call_args
        # Fourth argument should be the config (entry, structure, trend, config)
        assert call_args[0][3] is config
        # First three arguments should be candle lists
        assert len(call_args[0][0]) == 1  # entry candles
        assert len(call_args[0][1]) == 1  # structure candles
        assert len(call_args[0][2]) == 1  # trend candles


# ---------------------------------------------------------------------------
# Test: error isolation across strategies
# ---------------------------------------------------------------------------

class TestErrorIsolationAcrossStrategies:
    """Failure in one strategy does not affect other strategies."""

    def test_error_isolation(self, caplog):
        config_ok = _make_config(strategy_id="ok-strat", name="OK Strategy", algorithm="algo_ok")
        config_bad = _make_config(strategy_id="bad-strat", name="Bad Strategy", algorithm="algo_bad")

        signal = _make_signal(strategy_id="ok-strat")

        mock_algo_ok = MagicMock()
        mock_algo_ok.analyze.return_value = [signal]

        mock_algo_bad = MagicMock()
        mock_algo_bad.analyze.side_effect = RuntimeError("boom")

        def registry_get(name):
            if name == "algo_ok":
                return mock_algo_ok
            if name == "algo_bad":
                return mock_algo_bad
            raise KeyError(name)

        mock_registry = MagicMock()
        mock_registry.get.side_effect = registry_get

        mock_config_loader = MagicMock()
        mock_config_loader.get_active_strategies.return_value = [config_bad, config_ok]

        mock_redis = MagicMock()
        mock_redis.zrevrangebyscore.return_value = []

        mock_publisher = MagicMock()
        mock_persister = MagicMock()

        runner = _build_runner(
            redis_client=mock_redis,
            config_loader=mock_config_loader,
            registry=mock_registry,
            signal_publisher=mock_publisher,
            signal_persister=mock_persister,
        )

        with caplog.at_level(logging.ERROR):
            runner._on_candle_update("US30", "15m")

        # Bad strategy should have failed
        assert runner._failure_counts["bad-strat"] == 1
        # OK strategy should have succeeded
        assert runner._failure_counts["ok-strat"] == 0
        # OK strategy's signal should have been published
        mock_publisher.publish.assert_called_once_with(signal)
        mock_persister.persist.assert_called_once_with(signal)

    def test_consecutive_failure_warning(self, caplog):
        """Log warning when a strategy fails 5 consecutive cycles."""
        config = _make_config(strategy_id="failing-strat", name="Failing Strategy")

        mock_config_loader = MagicMock()
        mock_config_loader.get_active_strategies.return_value = [config]

        mock_registry = MagicMock()
        mock_registry.get.side_effect = RuntimeError("always fails")

        mock_redis = MagicMock()
        mock_redis.zrevrangebyscore.return_value = []

        runner = _build_runner(
            redis_client=mock_redis,
            config_loader=mock_config_loader,
            registry=mock_registry,
        )

        with caplog.at_level(logging.WARNING):
            for _ in range(FAILURE_WARNING_THRESHOLD):
                runner._on_candle_update("US30", "15m")

        assert runner._failure_counts["failing-strat"] == FAILURE_WARNING_THRESHOLD
        assert "has failed 5 consecutive cycles" in caplog.text
