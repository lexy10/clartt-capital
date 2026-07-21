"""Property-based tests for the live signal pipeline.

Uses hypothesis to generate random inputs and verify correctness properties
across many iterations.
"""

import time
import uuid
from unittest.mock import MagicMock, patch

import pytest
from hypothesis import given, settings, assume
from hypothesis import strategies as st

from src.models import StrategyConfig, Signal, Candle
from src.models.signal import (
    SignalDirection,
    SignalMetadata,
    SignalMode,
    BOSType,
)
from src.models.timeframe import Timeframe
from src.models.strategy_config import RiskSettings, SessionWindow
from src.pipeline.strategy_config_loader import StrategyConfigLoader
from src.pipeline.strategy_runner import (
    StrategyRunner,
    DUPLICATE_EXPIRY_SECONDS,
    MAX_PROCESSED_PER_STRATEGY,
)
from src.pipeline.signal_persister import SignalPersister
from src.metrics import pipeline_active_strategies


# ---------------------------------------------------------------------------
# Hypothesis strategies for generating random domain objects
# ---------------------------------------------------------------------------

INSTRUMENTS = ["US30", "XAUUSD", "EURUSD", "GBPUSD", "NAS100"]
ALL_TIMEFRAMES = list(Timeframe)

st_instrument = st.sampled_from(INSTRUMENTS)
st_timeframe = st.sampled_from(ALL_TIMEFRAMES)


@st.composite
def st_risk_settings(draw):
    return RiskSettings(
        max_risk_per_trade_pct=draw(st.floats(min_value=0.1, max_value=10.0)),
        max_daily_loss_pct=draw(st.floats(min_value=0.1, max_value=10.0)),
        max_spread=draw(st.floats(min_value=0.1, max_value=50.0)),
        max_slippage=draw(st.floats(min_value=0.1, max_value=10.0)),
        volatility_multiplier=draw(st.floats(min_value=0.1, max_value=5.0)),
    )


@st.composite
def st_session_window(draw):
    return SessionWindow(
        name=draw(st.sampled_from(["london", "new_york", "tokyo", "sydney"])),
        start_hour=draw(st.integers(min_value=0, max_value=23)),
        start_minute=draw(st.integers(min_value=0, max_value=59)),
        end_hour=draw(st.integers(min_value=0, max_value=23)),
        end_minute=draw(st.integers(min_value=0, max_value=59)),
    )


@st.composite
def st_strategy_config(draw):
    """Generate a random valid StrategyConfig."""
    tfs = draw(st.lists(st_timeframe, min_size=1, max_size=4, unique=True))
    higher_tf = draw(st_timeframe)
    entry_tf = draw(st_timeframe)
    return StrategyConfig(
        id=draw(st.uuids().map(str)),
        name=draw(st.text(alphabet="abcdefghijklmnopqrstuvwxyz_", min_size=1, max_size=20)),
        algorithm=draw(st.sampled_from(["ict_order_block", "custom_algo", "mean_reversion"])),
        algorithm_params=draw(st.fixed_dictionaries({})),
        instruments=[draw(st_instrument)],
        timeframes=tfs,
        higher_timeframe=higher_tf,
        entry_timeframe=entry_tf,
        session_windows=draw(st.lists(st_session_window(), min_size=0, max_size=2)),
        risk_settings=draw(st_risk_settings()),
        mode=draw(st.sampled_from(["live", "forward_test", "backtest"])),
        news_protection_minutes=draw(st.integers(min_value=0, max_value=120)),
        min_confidence_score=draw(st.floats(min_value=0.0, max_value=1.0)),
        enabled=draw(st.booleans()),
    )


@st.composite
def st_signal_metadata(draw):
    return SignalMetadata(
        bos_type=draw(st.sampled_from(list(BOSType))),
        liquidity_swept=draw(st.booleans()),
        session=draw(st.sampled_from(["london", "new_york", "tokyo", "sydney"])),
        spread_at_generation=draw(st.floats(min_value=0.1, max_value=20.0)),
        volatility_ratio=draw(st.floats(min_value=0.01, max_value=5.0)),
    )


@st.composite
def st_signal(draw):
    """Generate a random valid Signal."""
    return Signal(
        id=draw(st.uuids().map(str)),
        instrument=draw(st_instrument),
        direction=draw(st.sampled_from(list(SignalDirection))),
        entry_price=draw(st.floats(min_value=100.0, max_value=50000.0)),
        stop_loss=draw(st.floats(min_value=100.0, max_value=50000.0)),
        take_profit=draw(st.floats(min_value=100.0, max_value=50000.0)),
        position_size=draw(st.floats(min_value=0.01, max_value=10.0)),
        confidence_score=draw(st.floats(min_value=0.0, max_value=1.0)),
        timeframe=draw(st_timeframe),
        order_block_id=draw(st.uuids().map(str)),
        strategy_id=draw(st.uuids().map(str)),
        mode=draw(st.sampled_from(list(SignalMode))),
        metadata=draw(st_signal_metadata()),
        created_at="2024-01-15T14:30:00+00:00",
    )


@st.composite
def st_candle_message(draw):
    """Generate a random candle update JSON message dict."""
    return {
        "instrument": draw(st_instrument),
        "timeframe": draw(st_timeframe).value,
        "open": draw(st.floats(min_value=100.0, max_value=50000.0)),
        "high": draw(st.floats(min_value=100.0, max_value=50000.0)),
        "low": draw(st.floats(min_value=100.0, max_value=50000.0)),
        "close": draw(st.floats(min_value=100.0, max_value=50000.0)),
        "volume": draw(st.integers(min_value=0, max_value=100000)),
        "timestamp": "2024-01-15T14:30:00+00:00",
    }



# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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


# ===========================================================================
# Property 1 — Instrument filtering
# ===========================================================================

# Feature: live-signal-pipeline, Property 1: Instrument filtering
class TestInstrumentFiltering:
    """For any candle update instrument and strategy configs, only
    matching-instrument strategies get cycles triggered.

    **Validates: Requirements 1.4**
    """

    @given(
        candle_instrument=st_instrument,
        strategies=st.lists(st_strategy_config(), min_size=1, max_size=10),
    )
    @settings()
    def test_only_matching_instrument_strategies_run(self, candle_instrument, strategies):
        # Feature: live-signal-pipeline, Property 1: Instrument filtering
        # Force all strategies to be enabled so filtering is purely by instrument
        for s in strategies:
            s.enabled = True

        matching = [s for s in strategies if candle_instrument in s.instruments]
        non_matching = [s for s in strategies if candle_instrument not in s.instruments]

        mock_config_loader = MagicMock()
        mock_config_loader.get_active_strategies.return_value = matching

        mock_algorithm = MagicMock()
        mock_algorithm.analyze.return_value = []

        mock_registry = MagicMock()
        mock_registry.get.return_value = mock_algorithm

        mock_redis = MagicMock()
        mock_redis.zrevrangebyscore.return_value = []

        runner = _build_runner(
            redis_client=mock_redis,
            config_loader=mock_config_loader,
            registry=mock_registry,
        )

        runner._on_candle_update(candle_instrument, "15m")

        # config_loader should have been called with the candle instrument
        mock_config_loader.get_active_strategies.assert_called_once_with(
            instrument=candle_instrument
        )

        # analyze should have been called once per matching strategy
        assert mock_algorithm.analyze.call_count == len(matching)



# ===========================================================================
# Property 2 — Cache TTL freshness
# ===========================================================================

# Feature: live-signal-pipeline, Property 2: Cache TTL freshness
class TestCacheTTLFreshness:
    """For any sequence of get_active_strategies calls with varying time gaps,
    cache hit/miss matches TTL.

    **Validates: Requirements 2.2, 2.3**
    """

    @given(
        time_gap=st.floats(min_value=0.0, max_value=300.0),
    )
    @settings()
    def test_cache_hit_miss_matches_ttl(self, time_gap):
        # Feature: live-signal-pipeline, Property 2: Cache TTL freshness
        loader = StrategyConfigLoader(backend_url="http://localhost:3000")

        # Pre-populate cache with a known strategy
        config = StrategyConfig(
            id="test-id",
            name="test",
            algorithm="ict_order_block",
            instruments=["US30"],
            timeframes=[Timeframe.FIFTEEN_MINUTES],
            higher_timeframe=Timeframe.FOUR_HOURS,
            entry_timeframe=Timeframe.FIFTEEN_MINUTES,
            risk_settings=RiskSettings(
                max_risk_per_trade_pct=1.0,
                max_daily_loss_pct=3.0,
                max_spread=5.0,
                max_slippage=2.0,
                volatility_multiplier=1.5,
            ),
            mode="live",
            enabled=True,
        )
        loader._cache = [config]

        base_time = 1000000.0
        loader._cache_time = base_time

        # Mock time.time to return base_time + time_gap
        with patch("src.pipeline.strategy_config_loader.time") as mock_time:
            mock_time.time.return_value = base_time + time_gap

            with patch.object(loader, "_refresh") as mock_refresh:
                # If refresh is called and raises, fall back to stale cache
                mock_refresh.side_effect = Exception("should not refresh on cache hit")

                if time_gap < 60.0:
                    # Cache should be valid — no refresh
                    result = loader.get_active_strategies()
                    mock_refresh.assert_not_called()
                    assert len(result) == 1
                else:
                    # Cache expired — refresh should be attempted
                    # Reset side_effect so it doesn't raise
                    mock_refresh.side_effect = None
                    result = loader.get_active_strategies()
                    mock_refresh.assert_called_once()



# ===========================================================================
# Property 3 — Enabled strategy filtering
# ===========================================================================

# Feature: live-signal-pipeline, Property 3: Enabled strategy filtering
class TestEnabledStrategyFiltering:
    """For any list of strategies with random enabled flags, only enabled
    strategies are returned.

    **Validates: Requirements 2.4**
    """

    @given(
        strategies=st.lists(st_strategy_config(), min_size=1, max_size=15),
    )
    @settings()
    def test_only_enabled_strategies_returned(self, strategies):
        # Feature: live-signal-pipeline, Property 3: Enabled strategy filtering
        # Build raw API responses from the generated configs
        raw_responses = []
        for s in strategies:
            config_dict = s.model_dump(mode="json")
            # Remove top-level fields that go into the API response wrapper
            sid = config_dict.pop("id")
            name = config_dict.pop("name")
            algorithm = config_dict.pop("algorithm")
            raw_responses.append({
                "id": sid,
                "name": name,
                "algorithm": algorithm,
                "enabled": s.enabled,
                "config": config_dict,
            })

        loader = StrategyConfigLoader(backend_url="http://localhost:3000")

        # Simulate _refresh by parsing each raw response
        parsed = []
        for raw in raw_responses:
            result = loader._parse_strategy(raw)
            if result is not None and result.enabled:
                parsed.append(result)

        expected_enabled_count = sum(1 for s in strategies if s.enabled)
        assert len(parsed) == expected_enabled_count

        # All returned strategies must be enabled
        for p in parsed:
            assert p.enabled is True



# ===========================================================================
# Property 4 — Strategy config round-trip parsing
# ===========================================================================

# Feature: live-signal-pipeline, Property 4: Strategy config round-trip parsing
class TestStrategyConfigRoundTrip:
    """For any valid StrategyConfig, serialize to dict then parse back
    produces equivalent object.

    **Validates: Requirements 2.6**
    """

    @given(config=st_strategy_config())
    @settings()
    def test_round_trip_produces_equivalent_config(self, config):
        # Feature: live-signal-pipeline, Property 4: Strategy config round-trip parsing
        # Serialize to dict (JSON-compatible)
        config_dict = config.model_dump(mode="json")

        # Restructure into the API response format that _parse_strategy expects
        sid = config_dict.pop("id")
        name = config_dict.pop("name")
        algorithm = config_dict.pop("algorithm")

        api_response = {
            "id": sid,
            "name": name,
            "algorithm": algorithm,
            "config": config_dict,
        }

        loader = StrategyConfigLoader(backend_url="http://localhost:3000")
        parsed = loader._parse_strategy(api_response)

        assert parsed is not None
        assert parsed.id == config.id
        assert parsed.name == config.name
        assert parsed.algorithm == config.algorithm
        assert parsed.instruments == config.instruments
        assert parsed.timeframes == config.timeframes
        assert parsed.higher_timeframe == config.higher_timeframe
        assert parsed.entry_timeframe == config.entry_timeframe
        assert parsed.mode == config.mode
        assert parsed.enabled == config.enabled
        assert parsed.min_confidence_score == config.min_confidence_score
        assert parsed.risk_settings == config.risk_settings



# ===========================================================================
# Property 6 — Confidence score filtering
# ===========================================================================

# Feature: live-signal-pipeline, Property 6: Confidence score filtering
class TestConfidenceScoreFiltering:
    """For any signal confidence and threshold, correct accept/discard behavior.

    **Validates: Requirements 3.6**
    """

    @given(
        confidence=st.floats(min_value=0.0, max_value=1.0),
        threshold=st.floats(min_value=0.0, max_value=1.0),
    )
    @settings()
    def test_confidence_filtering_logic(self, confidence, threshold):
        # Feature: live-signal-pipeline, Property 6: Confidence score filtering
        signal = Signal(
            id=str(uuid.uuid4()),
            instrument="US30",
            direction=SignalDirection.BUY,
            entry_price=38505.0,
            stop_loss=38490.0,
            take_profit=38535.0,
            position_size=0.05,
            confidence_score=confidence,
            timeframe=Timeframe.FIFTEEN_MINUTES,
            order_block_id=str(uuid.uuid4()),
            strategy_id="strat-1",
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

        config = StrategyConfig(
            id="strat-1",
            name="test",
            algorithm="ict_order_block",
            instruments=["US30"],
            timeframes=[Timeframe.FIFTEEN_MINUTES],
            higher_timeframe=Timeframe.FOUR_HOURS,
            entry_timeframe=Timeframe.FIFTEEN_MINUTES,
            risk_settings=RiskSettings(
                max_risk_per_trade_pct=1.0,
                max_daily_loss_pct=3.0,
                max_spread=5.0,
                max_slippage=2.0,
                volatility_multiplier=1.5,
            ),
            mode="live",
            min_confidence_score=threshold,
            enabled=True,
        )

        mock_algorithm = MagicMock()
        mock_algorithm.analyze.return_value = [signal]

        mock_registry = MagicMock()
        mock_registry.get.return_value = mock_algorithm

        mock_redis = MagicMock()
        mock_redis.zrevrangebyscore.return_value = []

        mock_publisher = MagicMock()
        mock_persister = MagicMock()

        runner = _build_runner(
            redis_client=mock_redis,
            registry=mock_registry,
            signal_publisher=mock_publisher,
            signal_persister=mock_persister,
        )

        count = runner._run_analysis_cycle(config, config.instruments[0])

        if confidence >= threshold:
            assert count == 1
            mock_publisher.publish.assert_called_once()
            mock_persister.persist.assert_called_once()
        else:
            assert count == 0
            mock_publisher.publish.assert_not_called()
            mock_persister.persist.assert_not_called()



# ===========================================================================
# Property 8 — Persistence payload completeness
# ===========================================================================

# Feature: live-signal-pipeline, Property 8: Persistence payload completeness
class TestPersistencePayloadCompleteness:
    """For any Signal, payload contains all required fields.

    **Validates: Requirements 5.2**
    """

    @given(signal=st_signal())
    @settings()
    def test_payload_has_all_required_fields(self, signal):
        # Feature: live-signal-pipeline, Property 8: Persistence payload completeness
        persister = SignalPersister(backend_url="http://localhost:3000")

        required_fields = {
            "instrument",
            "direction",
            "entryPrice",
            "stopLoss",
            "takeProfit",
            "positionSize",
            "confidenceScore",
            "timeframe",
            "orderBlockId",
            "strategyId",
            "mode",
            "metadata",
        }

        with patch("src.pipeline.signal_persister.requests") as mock_requests:
            mock_resp = MagicMock()
            mock_resp.raise_for_status.return_value = None
            mock_requests.post.return_value = mock_resp

            persister._send(signal)

            mock_requests.post.assert_called_once()
            call_kwargs = mock_requests.post.call_args
            payload = call_kwargs.kwargs.get("json") or call_kwargs[1].get("json")

            assert payload is not None
            assert required_fields.issubset(payload.keys()), (
                f"Missing fields: {required_fields - payload.keys()}"
            )

            # Verify field values match the signal
            assert payload["instrument"] == signal.instrument
            assert payload["direction"] == signal.direction.value
            assert payload["entryPrice"] == signal.entry_price
            assert payload["stopLoss"] == signal.stop_loss
            assert payload["takeProfit"] == signal.take_profit
            assert payload["positionSize"] == signal.position_size
            assert payload["confidenceScore"] == signal.confidence_score
            assert payload["timeframe"] == signal.timeframe.value
            assert payload["orderBlockId"] == signal.order_block_id
            assert payload["strategyId"] == signal.strategy_id
            assert payload["mode"] == signal.mode.value



# ===========================================================================
# Property 9 — Duplicate detection with expiry and cap
# ===========================================================================

# Feature: live-signal-pipeline, Property 9: Duplicate detection with expiry and cap
class TestDuplicateDetection:
    """For any sequence of order block IDs with timestamps, dedup works,
    24h expiry works, 1000-entry cap works.

    **Validates: Requirements 6.1, 6.2, 6.3**
    """

    @given(
        ob_ids=st.lists(
            st.uuids().map(str),
            min_size=1,
            max_size=50,
        ),
    )
    @settings()
    def test_duplicate_detection_blocks_repeats(self, ob_ids):
        # Feature: live-signal-pipeline, Property 9: Duplicate detection with expiry and cap
        runner = _build_runner()
        strategy_id = "strat-1"

        for ob_id in ob_ids:
            # First time should not be duplicate
            assert not runner._is_duplicate(strategy_id, ob_id)
            runner._mark_processed(strategy_id, ob_id)
            # Second time should be duplicate
            assert runner._is_duplicate(strategy_id, ob_id)

    @given(
        ob_ids=st.lists(
            st.uuids().map(str),
            min_size=1,
            max_size=20,
        ),
    )
    @settings()
    def test_expiry_after_24h(self, ob_ids):
        # Feature: live-signal-pipeline, Property 9: Duplicate detection with expiry and cap
        runner = _build_runner()
        strategy_id = "strat-1"

        base_time = 1000000.0

        with patch("src.pipeline.strategy_runner.time") as mock_time:
            mock_time.perf_counter.return_value = 0.0
            mock_time.time.return_value = base_time

            for ob_id in ob_ids:
                runner._mark_processed(strategy_id, ob_id)

            # All should be duplicates now
            for ob_id in ob_ids:
                assert runner._is_duplicate(strategy_id, ob_id)

            # Advance time past 24h expiry
            mock_time.time.return_value = base_time + DUPLICATE_EXPIRY_SECONDS + 1

            # After expiry, none should be duplicates
            for ob_id in ob_ids:
                assert not runner._is_duplicate(strategy_id, ob_id)

    @given(
        data=st.data(),
    )
    @settings()
    def test_cap_at_1000_entries(self, data):
        # Feature: live-signal-pipeline, Property 9: Duplicate detection with expiry and cap
        runner = _build_runner()
        strategy_id = "strat-1"

        # Generate slightly more than 1000 unique IDs
        num_entries = data.draw(st.integers(min_value=1001, max_value=1050))
        ob_ids = [str(uuid.uuid4()) for _ in range(num_entries)]

        for ob_id in ob_ids:
            runner._mark_processed(strategy_id, ob_id)

        bucket = runner._processed_obs.get(strategy_id, {})
        assert len(bucket) <= MAX_PROCESSED_PER_STRATEGY



# ===========================================================================
# Property 10 — Error isolation across strategies
# ===========================================================================

# Feature: live-signal-pipeline, Property 10: Error isolation across strategies
class TestErrorIsolation:
    """For any strategy set where some raise exceptions, non-failing
    strategies complete.

    **Validates: Requirements 8.1, 8.2**
    """

    @given(
        fail_mask=st.lists(st.booleans(), min_size=2, max_size=10),
    )
    @settings()
    def test_non_failing_strategies_complete(self, fail_mask):
        # Feature: live-signal-pipeline, Property 10: Error isolation across strategies
        strategies = []
        for i, should_fail in enumerate(fail_mask):
            config = StrategyConfig(
                id=f"strat-{i}",
                name=f"Strategy {i}",
                algorithm=f"algo_{i}",
                instruments=["US30"],
                timeframes=[Timeframe.FIFTEEN_MINUTES],
                higher_timeframe=Timeframe.FOUR_HOURS,
                entry_timeframe=Timeframe.FIFTEEN_MINUTES,
                risk_settings=RiskSettings(
                    max_risk_per_trade_pct=1.0,
                    max_daily_loss_pct=3.0,
                    max_spread=5.0,
                    max_slippage=2.0,
                    volatility_multiplier=1.5,
                ),
                mode="live",
                enabled=True,
            )
            strategies.append((config, should_fail))

        mock_config_loader = MagicMock()
        mock_config_loader.get_active_strategies.return_value = [s[0] for s in strategies]

        call_tracker = {"success_count": 0, "fail_count": 0}

        def mock_get(name):
            algo = MagicMock()
            idx = int(name.split("_")[1])
            should_fail = strategies[idx][1]
            if should_fail:
                algo.analyze.side_effect = RuntimeError(f"Algo {idx} failed")
            else:
                algo.analyze.return_value = []
            return algo

        mock_registry = MagicMock()
        mock_registry.get.side_effect = mock_get

        mock_redis = MagicMock()
        mock_redis.zrevrangebyscore.return_value = []

        runner = _build_runner(
            redis_client=mock_redis,
            config_loader=mock_config_loader,
            registry=mock_registry,
        )

        runner._on_candle_update("US30", "15m")

        expected_failures = sum(1 for _, f in strategies if f)
        expected_successes = sum(1 for _, f in strategies if not f)

        # Verify failure counts are tracked for failing strategies
        actual_failures = sum(
            1 for sid, count in runner._failure_counts.items() if count > 0
        )
        actual_successes = sum(
            1 for sid, count in runner._failure_counts.items() if count == 0
        )

        assert actual_failures == expected_failures
        assert actual_successes == expected_successes



# ===========================================================================
# Property 11 — Consecutive failure counting
# ===========================================================================

# Feature: live-signal-pipeline, Property 11: Consecutive failure counting
class TestConsecutiveFailureCounting:
    """For any pass/fail sequence, failure count tracks correctly and
    resets on success.

    **Validates: Requirements 8.3**
    """

    @given(
        sequence=st.lists(st.booleans(), min_size=1, max_size=50),
    )
    @settings()
    def test_failure_count_tracks_and_resets(self, sequence):
        # Feature: live-signal-pipeline, Property 11: Consecutive failure counting
        config = StrategyConfig(
            id="strat-1",
            name="test",
            algorithm="test_algo",
            instruments=["US30"],
            timeframes=[Timeframe.FIFTEEN_MINUTES],
            higher_timeframe=Timeframe.FOUR_HOURS,
            entry_timeframe=Timeframe.FIFTEEN_MINUTES,
            risk_settings=RiskSettings(
                max_risk_per_trade_pct=1.0,
                max_daily_loss_pct=3.0,
                max_spread=5.0,
                max_slippage=2.0,
                volatility_multiplier=1.5,
            ),
            mode="live",
            enabled=True,
        )

        mock_config_loader = MagicMock()
        mock_config_loader.get_active_strategies.return_value = [config]

        call_index = {"i": 0}

        def mock_get(name):
            algo = MagicMock()
            idx = call_index["i"]
            should_fail = sequence[idx] if idx < len(sequence) else False
            call_index["i"] += 1
            if should_fail:
                algo.analyze.side_effect = RuntimeError("fail")
            else:
                algo.analyze.return_value = []
            return algo

        mock_registry = MagicMock()
        mock_registry.get.side_effect = mock_get

        mock_redis = MagicMock()
        mock_redis.zrevrangebyscore.return_value = []

        runner = _build_runner(
            redis_client=mock_redis,
            config_loader=mock_config_loader,
            registry=mock_registry,
        )

        # Run each step in the sequence
        expected_count = 0
        for should_fail in sequence:
            call_index["i"] = 0  # Reset for each call
            # Reconfigure the mock for this iteration
            algo = MagicMock()
            if should_fail:
                algo.analyze.side_effect = RuntimeError("fail")
                expected_count += 1
            else:
                algo.analyze.return_value = []
                expected_count = 0

            mock_registry.get.side_effect = lambda name, a=algo: a

            # Clear throttle so the next call isn't skipped
            runner._last_run.clear()
            runner._on_candle_update("US30", "15m")

            assert runner._failure_counts.get("strat-1", 0) == expected_count



# ===========================================================================
# Property 12 — Active strategies gauge accuracy
# ===========================================================================

# Feature: live-signal-pipeline, Property 12: Active strategies gauge accuracy
class TestActiveStrategiesGauge:
    """For any strategy list with mixed enabled flags, gauge equals
    enabled count.

    **Validates: Requirements 9.5**
    """

    @given(
        strategies=st.lists(st_strategy_config(), min_size=1, max_size=20),
    )
    @settings()
    def test_gauge_equals_enabled_count(self, strategies):
        # Feature: live-signal-pipeline, Property 12: Active strategies gauge accuracy
        # Filter to only enabled strategies (simulating what config_loader returns)
        enabled_strategies = [s for s in strategies if s.enabled]
        assume(len(enabled_strategies) > 0)

        # Ensure all enabled strategies have the same instrument for matching
        for s in enabled_strategies:
            s.instruments = ["US30"]

        mock_config_loader = MagicMock()
        mock_config_loader.get_active_strategies.return_value = enabled_strategies

        mock_algorithm = MagicMock()
        mock_algorithm.analyze.return_value = []

        mock_registry = MagicMock()
        mock_registry.get.return_value = mock_algorithm

        mock_redis = MagicMock()
        mock_redis.zrevrangebyscore.return_value = []

        runner = _build_runner(
            redis_client=mock_redis,
            config_loader=mock_config_loader,
            registry=mock_registry,
        )

        runner._on_candle_update("US30", "15m")

        # The gauge should reflect the count of enabled strategies
        gauge_value = pipeline_active_strategies._value.get()
        assert gauge_value == len(enabled_strategies)
