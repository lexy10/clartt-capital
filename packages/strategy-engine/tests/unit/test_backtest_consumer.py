"""Unit tests for BacktestConsumer."""

import json
from unittest.mock import MagicMock, patch, call

import pytest

from src.backtesting.backtest_consumer import BacktestConsumer
from src.backtesting.backtest_engine import BacktestEngine
from src.models.backtesting import (
    BacktestParams,
    BacktestResult,
    PerformanceStats,
    TradeResult,
)
from src.models.candle import Candle


def _make_request_data(
    result_id="res-1",
    strategy_id="strat-1",
    start_date="2024-01-01T00:00:00Z",
    end_date="2024-06-01T00:00:00Z",
):
    return {
        "result_id": result_id,
        "strategy_id": strategy_id,
        "strategy_config": {
            "id": strategy_id,
            "name": "Test Strategy",
            "instruments": ["US30"],
            "timeframes": ["15m", "1h"],
            "higher_timeframe": "1h",
            "entry_timeframe": "15m",
            "session_windows": [],
            "risk_settings": {
                "max_risk_per_trade_pct": 1.0,
                "max_daily_loss_pct": 3.0,
                "max_spread": 5.0,
                "max_slippage": 2.0,
                "volatility_multiplier": 1.5,
            },
            "mode": "backtest",
            "news_protection_minutes": 30,
            "min_confidence_score": 0.6,
            "enabled": True,
        },
        "params": {
            "initial_capital": 10000.0,
            "commission_per_trade": 0.0,
            "slippage": 0.0,
            "spread": 0.0,
        },
        "start_date": start_date,
        "end_date": end_date,
    }


def _make_candle(timestamp="2024-01-15T14:30:00Z", close=38500.0):
    return Candle(
        instrument="US30",
        timeframe="15m",
        open=38450.0,
        high=38550.0,
        low=38400.0,
        close=close,
        volume=100.0,
        timestamp=timestamp,
    )


def _make_backtest_result(strategy_id="strat-1"):
    return BacktestResult(
        strategy_id=strategy_id,
        trades=[
            TradeResult(
                signal_id="sig-1",
                direction="BUY",
                entry_price=38500.0,
                exit_price=38650.0,
                position_size=1.0,
                profit_loss=150.0,
                entry_time="2024-01-15T14:30:00Z",
                exit_time="2024-01-15T16:00:00Z",
            )
        ],
        stats=PerformanceStats(
            total_trades=1,
            winning_trades=1,
            losing_trades=0,
            win_rate=1.0,
            max_drawdown=0.0,
            sharpe_ratio=0.0,
            profit_factor=0.0,
            expectancy=150.0,
            gross_profit=150.0,
            gross_loss=0.0,
            net_profit=150.0,
        ),
        equity_curve=[10000.0, 10150.0],
        initial_capital=10000.0,
    )


@pytest.fixture
def mock_redis():
    redis = MagicMock()
    # Default: xinfo_groups returns empty list (no consumer groups)
    redis.xinfo_groups.return_value = []
    return redis


@pytest.fixture
def mock_engine():
    return MagicMock(spec=BacktestEngine)


@pytest.fixture
def consumer(mock_redis, mock_engine):
    return BacktestConsumer(redis_client=mock_redis, backtest_engine=mock_engine)


class TestStart:
    def test_creates_consumer_group(self, consumer, mock_redis):
        consumer.start()
        mock_redis.xgroup_create.assert_called_once_with(
            name="backtest:requests",
            groupname="strategy-engine",
            id="0",
            mkstream=True,
        )
        consumer.stop()

    def test_handles_busygroup_error(self, consumer, mock_redis):
        mock_redis.xgroup_create.side_effect = Exception("BUSYGROUP Consumer Group name already exists")
        consumer.start()
        assert consumer.running is True
        consumer.stop()

    def test_raises_non_busygroup_error(self, consumer, mock_redis):
        mock_redis.xgroup_create.side_effect = Exception("Connection refused")
        with pytest.raises(Exception, match="Connection refused"):
            consumer.start()

    def test_sets_running_flag(self, consumer, mock_redis):
        consumer.start()
        assert consumer.running is True
        consumer.stop()


class TestStop:
    def test_clears_running_flag(self, consumer, mock_redis):
        consumer.start()
        consumer.stop()
        assert consumer.running is False

    def test_stop_without_start(self, consumer):
        consumer.stop()
        assert consumer.running is False


class TestProcessRequest:
    @patch("src.backtesting.backtest_consumer.requests.get")
    def test_successful_backtest(self, mock_get, consumer, mock_redis, mock_engine):
        data = _make_request_data()
        fields = {b"data": json.dumps(data).encode()}
        candles = [_make_candle() for _ in range(10)]
        mock_response = MagicMock()
        mock_response.json.return_value = [json.loads(c.model_dump_json()) for c in candles]
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response
        mock_engine.run.return_value = _make_backtest_result()

        consumer._process_request(b"msg-1", fields)

        # Should publish running status
        publish_calls = mock_redis.publish.call_args_list
        assert len(publish_calls) == 2
        running_msg = json.loads(publish_calls[0][0][1])
        assert running_msg["status"] == "running"
        assert running_msg["result_id"] == "res-1"

        # Should publish completed status
        completed_msg = json.loads(publish_calls[1][0][1])
        assert completed_msg["status"] == "completed"

        # Should publish result to stream
        mock_redis.xadd.assert_called_once()
        xadd_call = mock_redis.xadd.call_args
        assert xadd_call[0][0] == "backtest:results"
        result_data = json.loads(xadd_call[0][1]["data"])
        assert result_data["status"] == "completed"
        assert result_data["result_id"] == "res-1"
        assert result_data["stats"]["total_trades"] == 1

    def test_missing_data_field(self, consumer, mock_redis):
        consumer._process_request(b"msg-1", {})
        mock_redis.publish.assert_not_called()
        mock_redis.xadd.assert_not_called()

    def test_invalid_json(self, consumer, mock_redis):
        fields = {b"data": b"not-json"}
        consumer._process_request(b"msg-1", fields)
        mock_redis.publish.assert_not_called()

    @patch("src.backtesting.backtest_consumer.requests.get")
    def test_empty_candle_data_publishes_failed(self, mock_get, consumer, mock_redis, mock_engine):
        data = _make_request_data()
        fields = {b"data": json.dumps(data).encode()}
        mock_response = MagicMock()
        mock_response.json.return_value = []
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        consumer._process_request(b"msg-1", fields)

        # Should publish running then failed
        publish_calls = mock_redis.publish.call_args_list
        assert len(publish_calls) == 2
        assert json.loads(publish_calls[0][0][1])["status"] == "running"
        failed_msg = json.loads(publish_calls[1][0][1])
        assert failed_msg["status"] == "failed"
        assert "No candle data" in failed_msg["error"]

        # Should publish failure result to stream
        xadd_call = mock_redis.xadd.call_args
        result_data = json.loads(xadd_call[0][1]["data"])
        assert result_data["status"] == "failed"

    @patch("src.backtesting.backtest_consumer.requests.get")
    def test_engine_exception_publishes_failed(self, mock_get, consumer, mock_redis, mock_engine):
        data = _make_request_data()
        fields = {b"data": json.dumps(data).encode()}
        candles = [_make_candle() for _ in range(10)]
        mock_response = MagicMock()
        mock_response.json.return_value = [json.loads(c.model_dump_json()) for c in candles]
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response
        mock_engine.run.side_effect = RuntimeError("Engine crashed")

        consumer._process_request(b"msg-1", fields)

        publish_calls = mock_redis.publish.call_args_list
        failed_msg = json.loads(publish_calls[1][0][1])
        assert failed_msg["status"] == "failed"
        assert "Engine crashed" in failed_msg["error"]


class TestFetchCandleData:
    @patch("src.backtesting.backtest_consumer.requests.get")
    def test_fetches_from_correct_url(self, mock_get, consumer):
        mock_response = MagicMock()
        mock_response.json.return_value = []
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response
        consumer._fetch_candle_data("US30", "15m", "2024-01-01T00:00:00Z", "2024-06-01T00:00:00Z")
        mock_get.assert_called_once()
        call_kwargs = mock_get.call_args
        assert call_kwargs[1]["params"]["instrument"] == "US30"
        assert call_kwargs[1]["params"]["timeframe"] == "15m"

    @patch("src.backtesting.backtest_consumer.requests.get")
    def test_deserializes_candles(self, mock_get, consumer):
        candle = _make_candle()
        mock_response = MagicMock()
        mock_response.json.return_value = [json.loads(candle.model_dump_json())]
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response
        result = consumer._fetch_candle_data("US30", "15m", "2024-01-01T00:00:00Z", "2024-06-01T00:00:00Z")
        assert len(result) == 1
        assert result[0].instrument == "US30"
        assert result[0].close == 38500.0

    @patch("src.backtesting.backtest_consumer.requests.get")
    def test_empty_result(self, mock_get, consumer):
        mock_response = MagicMock()
        mock_response.json.return_value = []
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response
        result = consumer._fetch_candle_data("US30", "15m", "2024-01-01T00:00:00Z", "2024-06-01T00:00:00Z")
        assert result == []
