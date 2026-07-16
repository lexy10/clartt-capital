"""Unit tests for StrategyConfigLoader."""

import time
from unittest.mock import patch, MagicMock

import pytest
import requests

from src.pipeline.strategy_config_loader import StrategyConfigLoader


def _make_api_strategy(
    strategy_id="strat-1",
    name="US30 London Session",
    algorithm="ict_order_block",
    enabled=True,
    instruments=None,
):
    """Build a backend API strategy response object."""
    return {
        "id": strategy_id,
        "name": name,
        "algorithm": algorithm,
        "enabled": enabled,
        "config": {
            "instruments": instruments or ["US30"],
            "timeframes": ["1m", "5m", "15m", "1h"],
            "higher_timeframe": "4h",
            "entry_timeframe": "15m",
            "session_windows": [
                {"name": "london", "start_hour": 8, "start_minute": 0, "end_hour": 16, "end_minute": 0}
            ],
            "risk_settings": {
                "max_risk_per_trade_pct": 1.0,
                "max_daily_loss_pct": 3.0,
                "max_spread": 5.0,
                "max_slippage": 2.0,
                "volatility_multiplier": 1.5,
            },
            "algorithm_params": {"reward_risk_ratio": 2.0},
            "mode": "live",
            "min_confidence_score": 0.6,
            "enabled": True,
        },
    }


class TestFirstCallFetchesFromAPI:
    """First call to get_active_strategies should hit the backend API."""

    @patch("src.pipeline.strategy_config_loader.requests.get")
    def test_first_call_fetches_from_api(self, mock_get):
        mock_response = MagicMock()
        mock_response.json.return_value = [_make_api_strategy()]
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        loader = StrategyConfigLoader(backend_url="http://backend:3000")
        strategies = loader.get_active_strategies()

        mock_get.assert_called_once_with("http://backend:3000/api/strategies", timeout=10)
        assert len(strategies) == 1
        assert strategies[0].id == "strat-1"
        assert strategies[0].name == "US30 London Session"

    @patch("src.pipeline.strategy_config_loader.requests.get")
    def test_second_call_within_ttl_uses_cache(self, mock_get):
        mock_response = MagicMock()
        mock_response.json.return_value = [_make_api_strategy()]
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        loader = StrategyConfigLoader(backend_url="http://backend:3000")
        loader.get_active_strategies()
        loader.get_active_strategies()

        # Only one HTTP call despite two get_active_strategies calls
        assert mock_get.call_count == 1


class TestStaleCacheFallback:
    """When API is unreachable, loader should fall back to stale cache."""

    @patch("src.pipeline.strategy_config_loader.requests.get")
    def test_stale_cache_fallback_when_api_unreachable(self, mock_get):
        # First call succeeds
        mock_response = MagicMock()
        mock_response.json.return_value = [_make_api_strategy()]
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        loader = StrategyConfigLoader(backend_url="http://backend:3000")
        strategies = loader.get_active_strategies()
        assert len(strategies) == 1

        # Expire the cache
        loader._cache_time = 0.0

        # Second call fails — API unreachable
        mock_get.side_effect = requests.ConnectionError("Connection refused")
        strategies = loader.get_active_strategies()

        # Should still return stale cached data
        assert len(strategies) == 1
        assert strategies[0].id == "strat-1"


class TestSkipsInvalidConfig:
    """Strategies with invalid config JSON should be skipped."""

    @patch("src.pipeline.strategy_config_loader.requests.get")
    def test_skips_invalid_config_json(self, mock_get):
        valid = _make_api_strategy(strategy_id="valid-1")
        invalid = {
            "id": "invalid-1",
            "name": "Bad Strategy",
            "algorithm": "ict_order_block",
            "enabled": True,
            "config": {
                # Missing required fields like timeframes, risk_settings
                "instruments": ["US30"],
            },
        }

        mock_response = MagicMock()
        mock_response.json.return_value = [valid, invalid]
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        loader = StrategyConfigLoader(backend_url="http://backend:3000")
        strategies = loader.get_active_strategies()

        # Only the valid strategy should be returned
        assert len(strategies) == 1
        assert strategies[0].id == "valid-1"


class TestExtractsAlgorithmField:
    """Algorithm field should be extracted from top-level response."""

    @patch("src.pipeline.strategy_config_loader.requests.get")
    def test_extracts_algorithm_from_top_level(self, mock_get):
        raw = _make_api_strategy(algorithm="custom_algo")

        mock_response = MagicMock()
        mock_response.json.return_value = [raw]
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        loader = StrategyConfigLoader(backend_url="http://backend:3000")
        strategies = loader.get_active_strategies()

        assert len(strategies) == 1
        assert strategies[0].algorithm == "custom_algo"

    @patch("src.pipeline.strategy_config_loader.requests.get")
    def test_defaults_algorithm_to_ict_order_block(self, mock_get):
        raw = _make_api_strategy()
        del raw["algorithm"]  # Remove algorithm field

        mock_response = MagicMock()
        mock_response.json.return_value = [raw]
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        loader = StrategyConfigLoader(backend_url="http://backend:3000")
        strategies = loader.get_active_strategies()

        assert len(strategies) == 1
        assert strategies[0].algorithm == "ict_order_block"

    @patch("src.pipeline.strategy_config_loader.requests.get")
    def test_filters_by_instrument(self, mock_get):
        us30 = _make_api_strategy(strategy_id="s1", instruments=["US30"])
        eurusd = _make_api_strategy(strategy_id="s2", instruments=["EURUSD"])

        mock_response = MagicMock()
        mock_response.json.return_value = [us30, eurusd]
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        loader = StrategyConfigLoader(backend_url="http://backend:3000")
        strategies = loader.get_active_strategies(instrument="US30")

        assert len(strategies) == 1
        assert strategies[0].id == "s1"

    @patch("src.pipeline.strategy_config_loader.requests.get")
    def test_filters_disabled_strategies(self, mock_get):
        enabled = _make_api_strategy(strategy_id="s1", enabled=True)
        disabled = _make_api_strategy(strategy_id="s2", enabled=False)

        mock_response = MagicMock()
        mock_response.json.return_value = [enabled, disabled]
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        loader = StrategyConfigLoader(backend_url="http://backend:3000")
        strategies = loader.get_active_strategies()

        assert len(strategies) == 1
        assert strategies[0].id == "s1"


class TestPeriodicRefreshRecovery:
    """The background keep-warm refresh must exercise the breaker and let a
    stuck-OPEN breaker recover WITHOUT any candle traffic (the 35h-stuck bug)."""

    def test_periodic_refresh_runs_without_candles(self):
        loader = StrategyConfigLoader(backend_url="http://backend:3000")
        loader._refresh_interval = 0.05

        resp = MagicMock()
        resp.json.return_value = [_make_api_strategy()]
        resp.raise_for_status.return_value = None
        with patch("requests.get", return_value=resp) as mock_get:
            loader.start()
            time.sleep(0.2)
            loader.stop()

        # Refreshed on its own timer — no get_active_strategies() from candles.
        assert mock_get.call_count >= 1

    def test_stuck_open_breaker_recovers_on_timer(self):
        loader = StrategyConfigLoader(backend_url="http://backend:3000")
        loader._refresh_interval = 0.05
        loader._cb._recovery_timeout_ms = 10  # recover fast for the test

        # Phase 1: backend down → breaker trips OPEN (threshold defaults to 5).
        with patch("requests.get", side_effect=requests.RequestException("down")):
            for _ in range(6):
                loader._cache_time = 0.0
                loader.get_active_strategies()
        assert loader.circuit_breaker.state.value == "open"

        # Phase 2: backend recovers. With NO candles, only the timer drives the
        # breaker — it must re-probe and close on its own.
        resp = MagicMock()
        resp.json.return_value = [_make_api_strategy()]
        resp.raise_for_status.return_value = None
        with patch("requests.get", return_value=resp):
            loader.start()
            time.sleep(0.3)
            loader.stop()
        assert loader.circuit_breaker.state.value == "closed"
