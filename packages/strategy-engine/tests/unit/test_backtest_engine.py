"""Unit tests for BacktestEngine.

Tests cover:
- Performance statistics computation (win rate, max drawdown, Sharpe, profit factor, expectancy)
- run() with historical candle data
- optimize_parameters() grid search
- walk_forward() independent windows
- monte_carlo() distribution stats
"""

import math
from datetime import datetime, timezone

import pytest

from src.backtesting.backtest_engine import BacktestEngine
from src.models import (
    Candle,
    StrategyConfig,
    Timeframe,
)
from src.models.backtesting import (
    BacktestParams,
    BacktestResult,
    PerformanceStats,
    TimeWindow,
    TradeResult,
)
from src.models.strategy_config import RiskSettings, SessionWindow


# --- Helpers ---

def make_candle(
    open_: float,
    high: float,
    low: float,
    close: float,
    timestamp: str = "2024-01-01T10:00:00+00:00",
    instrument: str = "US30",
    timeframe: Timeframe = Timeframe.FIFTEEN_MINUTES,
    volume: float = 100.0,
) -> Candle:
    return Candle(
        instrument=instrument,
        timeframe=timeframe,
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
        timestamp=timestamp,
    )


def make_strategy_config(**overrides) -> StrategyConfig:
    defaults = {
        "id": "test-strategy",
        "name": "Test Strategy",
        "instruments": ["US30"],
        "timeframes": [Timeframe.FIFTEEN_MINUTES],
        "higher_timeframe": Timeframe.ONE_HOUR,
        "entry_timeframe": Timeframe.FIFTEEN_MINUTES,
        "session_windows": [
            SessionWindow(name="London", start_hour=8, start_minute=0, end_hour=16, end_minute=0),
        ],
        "risk_settings": RiskSettings(
            max_risk_per_trade_pct=2.0,
            max_daily_loss_pct=5.0,
            max_spread=5.0,
            max_slippage=3.0,
            volatility_multiplier=2.0,
        ),
        "mode": "backtest",
    }
    defaults.update(overrides)
    return StrategyConfig(**defaults)


# --- compute_stats tests ---

class TestComputeStats:
    """Tests for BacktestEngine.compute_stats()."""

    def test_empty_trades_returns_zero_stats(self):
        stats = BacktestEngine.compute_stats([])
        assert stats.total_trades == 0
        assert stats.win_rate == 0.0
        assert stats.max_drawdown == 0.0
        assert stats.sharpe_ratio == 0.0
        assert stats.profit_factor == 0.0
        assert stats.expectancy == 0.0

    def test_all_winning_trades(self):
        trades = [
            TradeResult(signal_id="1", direction="BUY", entry_price=100, exit_price=110,
                        position_size=1.0, profit_loss=10.0, entry_time="t1", exit_time="t2"),
            TradeResult(signal_id="2", direction="BUY", entry_price=100, exit_price=120,
                        position_size=1.0, profit_loss=20.0, entry_time="t3", exit_time="t4"),
        ]
        stats = BacktestEngine.compute_stats(trades)
        assert stats.total_trades == 2
        assert stats.winning_trades == 2
        assert stats.losing_trades == 0
        assert stats.win_rate == 1.0
        assert stats.gross_profit == 30.0
        assert stats.gross_loss == 0.0
        assert stats.net_profit == 30.0
        assert stats.expectancy == 15.0
        assert stats.max_drawdown == 0.0
        assert stats.profit_factor == float("inf")

    def test_all_losing_trades(self):
        trades = [
            TradeResult(signal_id="1", direction="BUY", entry_price=100, exit_price=90,
                        position_size=1.0, profit_loss=-10.0, entry_time="t1", exit_time="t2"),
            TradeResult(signal_id="2", direction="BUY", entry_price=100, exit_price=95,
                        position_size=1.0, profit_loss=-5.0, entry_time="t3", exit_time="t4"),
        ]
        stats = BacktestEngine.compute_stats(trades)
        assert stats.total_trades == 2
        assert stats.winning_trades == 0
        assert stats.losing_trades == 2
        assert stats.win_rate == 0.0
        assert stats.gross_profit == 0.0
        assert stats.gross_loss == 15.0
        assert stats.net_profit == -15.0
        assert stats.profit_factor == 0.0

    def test_mixed_trades_win_rate(self):
        trades = [
            TradeResult(signal_id="1", direction="BUY", entry_price=100, exit_price=110,
                        position_size=1.0, profit_loss=10.0, entry_time="t1", exit_time="t2"),
            TradeResult(signal_id="2", direction="BUY", entry_price=100, exit_price=95,
                        position_size=1.0, profit_loss=-5.0, entry_time="t3", exit_time="t4"),
            TradeResult(signal_id="3", direction="BUY", entry_price=100, exit_price=108,
                        position_size=1.0, profit_loss=8.0, entry_time="t5", exit_time="t6"),
            TradeResult(signal_id="4", direction="BUY", entry_price=100, exit_price=97,
                        position_size=1.0, profit_loss=-3.0, entry_time="t7", exit_time="t8"),
        ]
        stats = BacktestEngine.compute_stats(trades)
        assert stats.total_trades == 4
        assert stats.winning_trades == 2
        assert stats.losing_trades == 2
        assert stats.win_rate == 0.5
        assert stats.gross_profit == 18.0
        assert stats.gross_loss == 8.0
        assert stats.net_profit == 10.0
        assert stats.profit_factor == pytest.approx(2.25, abs=0.01)
        assert stats.expectancy == pytest.approx(2.5, abs=0.01)

    def test_max_drawdown_computation(self):
        # Sequence: +10, -20, +5
        # equity: 10000 → 10010 → 9990 → 9995
        # peak=10010, trough=9990, dd=(10010-9990)/10010 ≈ 0.001998
        trades = [
            TradeResult(signal_id="1", direction="BUY", entry_price=100, exit_price=110,
                        position_size=1.0, profit_loss=10.0, entry_time="t1", exit_time="t2"),
            TradeResult(signal_id="2", direction="BUY", entry_price=100, exit_price=80,
                        position_size=1.0, profit_loss=-20.0, entry_time="t3", exit_time="t4"),
            TradeResult(signal_id="3", direction="BUY", entry_price=100, exit_price=105,
                        position_size=1.0, profit_loss=5.0, entry_time="t5", exit_time="t6"),
        ]
        stats = BacktestEngine.compute_stats(trades)
        # max_drawdown is a ratio (0.0–1.0): (10010 - 9990) / 10010 ≈ 0.002
        assert 0.0 < stats.max_drawdown < 1.0
        assert stats.max_drawdown == pytest.approx(20.0 / 10010.0, abs=0.0001)

    def test_sharpe_ratio_single_trade(self):
        trades = [
            TradeResult(signal_id="1", direction="BUY", entry_price=100, exit_price=110,
                        position_size=1.0, profit_loss=10.0, entry_time="t1", exit_time="t2"),
        ]
        stats = BacktestEngine.compute_stats(trades)
        # With a single trade, stdev is undefined, so sharpe should be 0
        assert stats.sharpe_ratio == 0.0


# --- run() tests ---

class TestBacktestRun:
    """Tests for BacktestEngine.run()."""

    def test_run_with_insufficient_data(self):
        engine = BacktestEngine()
        strategy = make_strategy_config()
        candles = [make_candle(100, 105, 95, 102)]
        params = BacktestParams()

        result = engine.run(strategy, candles, params)
        assert result.strategy_id == "test-strategy"
        assert len(result.trades) == 0
        assert result.stats.total_trades == 0
        assert result.initial_capital == 10000.0

    def test_run_with_empty_data(self):
        engine = BacktestEngine()
        strategy = make_strategy_config()
        result = engine.run(strategy, [], BacktestParams())
        assert len(result.trades) == 0

    def test_run_returns_backtest_result(self):
        engine = BacktestEngine()
        strategy = make_strategy_config()
        # Generate enough candles to potentially trigger signals
        candles = _generate_trending_candles(50, start_price=35000.0)
        params = BacktestParams(initial_capital=50000.0)

        result = engine.run(strategy, candles, params)
        assert isinstance(result, BacktestResult)
        assert result.strategy_id == "test-strategy"
        assert result.initial_capital == 50000.0
        assert len(result.equity_curve) >= 1

    def test_run_equity_curve_starts_with_initial_capital(self):
        engine = BacktestEngine()
        strategy = make_strategy_config()
        candles = _generate_trending_candles(30, start_price=35000.0)
        params = BacktestParams(initial_capital=25000.0)

        result = engine.run(strategy, candles, params)
        assert result.equity_curve[0] == 25000.0


# --- optimize_parameters() tests ---

class TestOptimizeParameters:
    """Tests for BacktestEngine.optimize_parameters()."""

    def test_empty_param_space(self):
        engine = BacktestEngine()
        strategy = make_strategy_config()
        candles = _generate_trending_candles(30, start_price=35000.0)

        result = engine.optimize_parameters(strategy, candles, {})
        assert result.best_params == {}
        assert len(result.all_results) == 1

    def test_single_param_optimization(self):
        engine = BacktestEngine()
        strategy = make_strategy_config()
        candles = _generate_trending_candles(30, start_price=35000.0)

        param_space = {"max_risk_per_trade_pct": [1.0, 2.0, 3.0]}
        result = engine.optimize_parameters(strategy, candles, param_space)

        assert len(result.all_results) == 3
        assert "max_risk_per_trade_pct" in result.best_params
        assert result.metric == "net_profit"
        # Best score should be the max among all results
        scores = [r["score"] for r in result.all_results]
        assert result.best_score == max(scores)

    def test_multi_param_grid_search(self):
        engine = BacktestEngine()
        strategy = make_strategy_config()
        candles = _generate_trending_candles(30, start_price=35000.0)

        param_space = {
            "max_risk_per_trade_pct": [1.0, 2.0],
            "volatility_multiplier": [1.5, 2.5],
        }
        result = engine.optimize_parameters(strategy, candles, param_space)

        # 2 x 2 = 4 combinations
        assert len(result.all_results) == 4


# --- walk_forward() tests ---

class TestWalkForward:
    """Tests for BacktestEngine.walk_forward()."""

    def test_walk_forward_with_two_windows(self):
        engine = BacktestEngine()
        strategy = make_strategy_config()
        candles = _generate_trending_candles(60, start_price=35000.0)

        # Define in-sample and out-of-sample windows
        windows = [
            TimeWindow(start=candles[0].timestamp, end=candles[29].timestamp),
            TimeWindow(start=candles[30].timestamp, end=candles[59].timestamp),
        ]

        result = engine.walk_forward(strategy, candles, windows)
        assert len(result.windows) == 1  # 1 pair of IS/OOS
        assert result.windows[0].in_sample.start == candles[0].timestamp
        assert result.windows[0].out_of_sample.start == candles[30].timestamp

    def test_walk_forward_windows_are_independent(self):
        engine = BacktestEngine()
        strategy = make_strategy_config()
        candles = _generate_trending_candles(120, start_price=35000.0)

        windows = [
            TimeWindow(start=candles[0].timestamp, end=candles[29].timestamp),
            TimeWindow(start=candles[30].timestamp, end=candles[59].timestamp),
            TimeWindow(start=candles[60].timestamp, end=candles[89].timestamp),
            TimeWindow(start=candles[90].timestamp, end=candles[119].timestamp),
        ]

        result = engine.walk_forward(strategy, candles, windows)
        assert len(result.windows) == 2  # 2 pairs

    def test_walk_forward_empty_windows(self):
        engine = BacktestEngine()
        strategy = make_strategy_config()
        candles = _generate_trending_candles(30, start_price=35000.0)

        result = engine.walk_forward(strategy, candles, [])
        assert len(result.windows) == 0
        assert result.combined_stats.total_trades == 0


# --- monte_carlo() tests ---

class TestMonteCarlo:
    """Tests for BacktestEngine.monte_carlo()."""

    def test_monte_carlo_basic(self):
        engine = BacktestEngine()
        trades = [
            TradeResult(signal_id=str(i), direction="BUY", entry_price=100,
                        exit_price=110 if i % 2 == 0 else 95, position_size=1.0,
                        profit_loss=10.0 if i % 2 == 0 else -5.0,
                        entry_time=f"t{i}", exit_time=f"t{i+1}")
            for i in range(20)
        ]
        bt_result = BacktestResult(
            strategy_id="test",
            trades=trades,
            stats=BacktestEngine.compute_stats(trades),
            initial_capital=10000.0,
        )

        mc_result = engine.monte_carlo(bt_result, iterations=100)
        assert mc_result.iterations == 100
        assert len(mc_result.samples) == 100
        assert len(mc_result.drawdown_samples) == 100
        # All shuffles of the same trades should have the same net profit
        expected_net = sum(t.profit_loss for t in trades)
        for sample in mc_result.samples:
            assert sample == pytest.approx(expected_net, abs=0.01)

    def test_monte_carlo_empty_trades(self):
        engine = BacktestEngine()
        bt_result = BacktestResult(strategy_id="test", initial_capital=10000.0)

        mc_result = engine.monte_carlo(bt_result, iterations=50)
        assert mc_result.iterations == 50
        assert len(mc_result.samples) == 0

    def test_monte_carlo_distribution_stats(self):
        engine = BacktestEngine()
        trades = [
            TradeResult(signal_id=str(i), direction="BUY", entry_price=100,
                        exit_price=110 if i % 3 != 0 else 90, position_size=1.0,
                        profit_loss=10.0 if i % 3 != 0 else -10.0,
                        entry_time=f"t{i}", exit_time=f"t{i+1}")
            for i in range(30)
        ]
        bt_result = BacktestResult(
            strategy_id="test",
            trades=trades,
            stats=BacktestEngine.compute_stats(trades),
            initial_capital=10000.0,
        )

        mc_result = engine.monte_carlo(bt_result, iterations=200)
        assert mc_result.stats.probability_of_profit >= 0.0
        assert mc_result.stats.probability_of_profit <= 1.0
        assert mc_result.stats.mean_max_drawdown >= 0.0

    def test_monte_carlo_zero_iterations(self):
        engine = BacktestEngine()
        trades = [
            TradeResult(signal_id="1", direction="BUY", entry_price=100,
                        exit_price=110, position_size=1.0, profit_loss=10.0,
                        entry_time="t1", exit_time="t2"),
        ]
        bt_result = BacktestResult(
            strategy_id="test",
            trades=trades,
            stats=BacktestEngine.compute_stats(trades),
            initial_capital=10000.0,
        )

        mc_result = engine.monte_carlo(bt_result, iterations=0)
        assert mc_result.iterations == 0
        assert len(mc_result.samples) == 0


# --- Helper to generate candle data ---

def _generate_trending_candles(
    count: int,
    start_price: float = 35000.0,
    instrument: str = "US30",
    timeframe: Timeframe = Timeframe.FIFTEEN_MINUTES,
) -> list[Candle]:
    """Generate a series of candles with a trending pattern that creates swing points."""
    candles = []
    price = start_price
    base_time = datetime(2024, 1, 2, 10, 0, 0, tzinfo=timezone.utc)

    for i in range(count):
        # Create a zigzag pattern to generate swing highs/lows
        if i % 6 < 3:
            # Upward movement
            delta = 50.0 + (i % 3) * 20
            open_ = price
            close = price + delta
            high = close + 10
            low = open_ - 5
        else:
            # Downward movement
            delta = 30.0 + (i % 3) * 15
            open_ = price
            close = price - delta
            high = open_ + 5
            low = close - 10

        from datetime import timedelta
        ts = base_time + timedelta(minutes=i * 15)
        candles.append(Candle(
            instrument=instrument,
            timeframe=timeframe,
            open=round(open_, 2),
            high=round(high, 2),
            low=round(low, 2),
            close=round(close, 2),
            volume=100.0,
            timestamp=ts.isoformat(),
        ))
        price = close

    return candles
