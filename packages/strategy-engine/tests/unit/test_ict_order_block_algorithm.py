"""Unit tests for ICTOrderBlockAlgorithm plugin."""

import pytest

from src.models import (
    BOSDirection,
    Candle,
    OrderBlock,
    RiskSettings,
    SessionWindow,
    Signal,
    StrategyConfig,
    StructureType,
    Timeframe,
)
from src.strategy.algorithms.ict_order_block import ICTOrderBlockAlgorithm, OrderBlockDetector, ICTSignalGenerator


# ── Helpers ───────────────────────────────────────────────────────


def _candle(
    close: float,
    high: float | None = None,
    low: float | None = None,
    index: int = 0,
    timeframe: Timeframe = Timeframe.FIVE_MINUTES,
) -> Candle:
    h = high if high is not None else close + 2
    l = low if low is not None else close - 2
    return Candle(
        instrument="US30",
        timeframe=timeframe,
        open=close,
        high=h,
        low=l,
        close=close,
        volume=100.0,
        timestamp=f"2024-06-15T14:{index:02d}:00Z",
    )


def _config(**overrides) -> StrategyConfig:
    defaults = dict(
        id="strat-001",
        name="US30 OB Strategy",
        instruments=["US30"],
        timeframes=[Timeframe.ONE_HOUR, Timeframe.FIVE_MINUTES],
        higher_timeframe=Timeframe.ONE_HOUR,
        entry_timeframe=Timeframe.FIVE_MINUTES,
        session_windows=[
            SessionWindow(
                name="London", start_hour=8, start_minute=0,
                end_hour=16, end_minute=0,
            ),
            SessionWindow(
                name="New York", start_hour=13, start_minute=0,
                end_hour=21, end_minute=0,
            ),
        ],
        risk_settings=RiskSettings(
            max_risk_per_trade_pct=1.0,
            max_daily_loss_pct=5.0,
            max_spread=5.0,
            max_slippage=2.0,
            volatility_multiplier=3.0,
        ),
        mode="live",
    )
    defaults.update(overrides)
    return StrategyConfig(**defaults)


@pytest.fixture
def algorithm() -> ICTOrderBlockAlgorithm:
    return ICTOrderBlockAlgorithm()


# ── Static method tests ──────────────────────────────────────────


class TestStaticMethods:
    def test_name(self):
        assert ICTOrderBlockAlgorithm.name() == "ict_order_block"

    def test_description_is_nonempty_string(self):
        desc = ICTOrderBlockAlgorithm.description()
        assert isinstance(desc, str)
        assert len(desc) > 0

    def test_default_params(self):
        params = ICTOrderBlockAlgorithm.default_params()
        assert params == {
            "structure_lookback": 20,
            "trend_lookback": 50,
            "swing_length": 5,
            "max_rr_cap": 5.0,
            "cooldown_candles": 6,
            "max_candle_size_multiplier": 2.0,
            "kill_zone_mode": "disabled",
            "kill_zones": [],
            "kill_zone_confidence_penalty": 0.15,
            "choch_lookback": 3,
            "zone_filter_enabled": True,
            "breaker_blocks_enabled": True,
            "ob_max_age_candles": 500,
        }

    def test_param_schema_structure(self):
        schema = ICTOrderBlockAlgorithm.param_schema()
        assert schema["type"] == "object"
        props = schema["properties"]
        assert "structure_lookback" in props
        assert "trend_lookback" in props
        assert "swing_length" in props
        assert "max_rr_cap" in props
        assert "cooldown_candles" in props
        assert "max_candle_size_multiplier" in props
        assert "reward_risk_ratio" not in props
        assert props["structure_lookback"]["type"] == "integer"
        assert props["structure_lookback"]["minimum"] == 5
        assert props["structure_lookback"]["maximum"] == 100
        assert props["trend_lookback"]["type"] == "integer"
        assert props["trend_lookback"]["minimum"] == 10
        assert props["trend_lookback"]["maximum"] == 200
        assert props["swing_length"]["type"] == "integer"
        assert props["swing_length"]["minimum"] == 2
        assert props["swing_length"]["maximum"] == 20
        assert props["max_rr_cap"]["type"] == "number"
        assert props["max_rr_cap"]["minimum"] == 1.0
        assert props["max_rr_cap"]["maximum"] == 50.0
        assert props["cooldown_candles"]["type"] == "integer"
        assert props["cooldown_candles"]["minimum"] == 1
        assert props["cooldown_candles"]["maximum"] == 100
        assert props["max_candle_size_multiplier"]["type"] == "number"
        assert props["max_candle_size_multiplier"]["minimum"] == 0.5
        assert props["max_candle_size_multiplier"]["maximum"] == 10.0
        assert schema["additionalProperties"] is False

    def test_default_params_valid_against_schema(self):
        """default_params() keys should match param_schema() properties
        and values should satisfy the declared type constraints."""
        params = ICTOrderBlockAlgorithm.default_params()
        schema = ICTOrderBlockAlgorithm.param_schema()
        props = schema["properties"]

        # Every default param should be declared in the schema
        assert set(params.keys()) <= set(props.keys())

        # structure_lookback: integer within [5, 100]
        sl = params["structure_lookback"]
        assert isinstance(sl, int)
        assert props["structure_lookback"]["minimum"] <= sl <= props["structure_lookback"]["maximum"]

        # trend_lookback: integer within [10, 200]
        tl = params["trend_lookback"]
        assert isinstance(tl, int)
        assert props["trend_lookback"]["minimum"] <= tl <= props["trend_lookback"]["maximum"]

        # max_rr_cap: number within [1.0, 50.0]
        mrc = params["max_rr_cap"]
        assert isinstance(mrc, (int, float))
        assert props["max_rr_cap"]["minimum"] <= mrc <= props["max_rr_cap"]["maximum"]

        # swing_length: integer within [2, 20]
        swl = params["swing_length"]
        assert isinstance(swl, int)
        assert props["swing_length"]["minimum"] <= swl <= props["swing_length"]["maximum"]

        # cooldown_candles: integer within [1, 100]
        cc = params["cooldown_candles"]
        assert isinstance(cc, int)
        assert props["cooldown_candles"]["minimum"] <= cc <= props["cooldown_candles"]["maximum"]

        # max_candle_size_multiplier: number within [0.5, 10.0]
        mcsm = params["max_candle_size_multiplier"]
        assert isinstance(mcsm, (int, float))
        assert props["max_candle_size_multiplier"]["minimum"] <= mcsm <= props["max_candle_size_multiplier"]["maximum"]


# ── Empty input safety ───────────────────────────────────────────


class TestEmptyInputSafety:
    def test_empty_entry_candles(self, algorithm: ICTOrderBlockAlgorithm):
        higher = [_candle(100, index=i, timeframe=Timeframe.ONE_HOUR) for i in range(5)]
        trend = [_candle(100 + i * 2, index=i, timeframe=Timeframe.FOUR_HOURS) for i in range(10)]
        result = algorithm.analyze([], higher, trend, _config())
        assert result == []

    def test_empty_higher_tf_candles(self, algorithm: ICTOrderBlockAlgorithm):
        entry = [_candle(100, index=i) for i in range(5)]
        trend = [_candle(100 + i * 2, index=i, timeframe=Timeframe.FOUR_HOURS) for i in range(10)]
        result = algorithm.analyze(entry, [], trend, _config())
        assert result == []

    def test_both_empty(self, algorithm: ICTOrderBlockAlgorithm):
        trend = [_candle(100 + i * 2, index=i, timeframe=Timeframe.FOUR_HOURS) for i in range(10)]
        result = algorithm.analyze([], [], trend, _config())
        assert result == []

    def test_empty_trend_candles_returns_empty(self, algorithm: ICTOrderBlockAlgorithm):
        """With no trend candles, bias is neutral → no signals."""
        entry = [_candle(100, index=i) for i in range(5)]
        higher = [_candle(100, index=i, timeframe=Timeframe.ONE_HOUR) for i in range(5)]
        result = algorithm.analyze(entry, higher, [], _config())
        assert result == []


# ── Equivalence with direct detector/generator calls ─────────────


class TestEquivalence:
    def test_produces_same_signals_as_direct_calls(
        self, algorithm: ICTOrderBlockAlgorithm
    ):
        """Plugin analyze() should produce identical signals to direct
        OrderBlockDetector + SignalGenerator usage with trend bias filtering."""
        config = _config()
        from src.strategy.algorithms.ict_order_block import TrendAnalyzer, TrendBias
        trend_analyzer = TrendAnalyzer()
        detector = OrderBlockDetector()
        generator = ICTSignalGenerator()

        # Build higher-TF candles with a clear swing pattern for BOS detection
        # Pattern: low → high → low → higher-high (bullish BOS)
        higher_candles = [
            _candle(100, high=102, low=98, index=0, timeframe=Timeframe.ONE_HOUR),
            _candle(105, high=110, low=103, index=1, timeframe=Timeframe.ONE_HOUR),
            _candle(100, high=103, low=97, index=2, timeframe=Timeframe.ONE_HOUR),
            _candle(108, high=115, low=106, index=3, timeframe=Timeframe.ONE_HOUR),
            _candle(110, high=112, low=107, index=4, timeframe=Timeframe.ONE_HOUR),
        ]

        # Trend candles with clear bullish structure (HH/HL)
        trend_candles = [
            _candle(90, high=92, low=88, index=0, timeframe=Timeframe.FOUR_HOURS),
            _candle(95, high=100, low=93, index=1, timeframe=Timeframe.FOUR_HOURS),
            _candle(93, high=96, low=91, index=2, timeframe=Timeframe.FOUR_HOURS),
            _candle(98, high=105, low=96, index=3, timeframe=Timeframe.FOUR_HOURS),
            _candle(96, high=99, low=94, index=4, timeframe=Timeframe.FOUR_HOURS),
            _candle(102, high=110, low=100, index=5, timeframe=Timeframe.FOUR_HOURS),
        ]

        # Entry candles that retest the OB zone
        entry_candles = [
            _candle(104, high=106, low=102, index=0),
            _candle(103, high=105, low=101, index=1),
            _candle(105, high=107, low=103, index=2),
        ]

        # Direct calls — replicate the same 3-TF logic as analyze()
        bias = trend_analyzer.determine_bias(trend_candles)
        structure = detector.detect_structure(higher_candles)
        bos_list = detector.detect_bos(structure)

        # Filter BOS by trend bias
        if bias == TrendBias.BULLISH:
            aligned_bos = [b for b in bos_list if b.direction == BOSDirection.BULLISH]
        elif bias == TrendBias.BEARISH:
            aligned_bos = [b for b in bos_list if b.direction == BOSDirection.BEARISH]
        else:
            aligned_bos = []

        ranges = [c.high - c.low for c in higher_candles if (c.high - c.low) > 0]
        avg_range = sum(ranges) / len(ranges) if ranges else 1.0
        swing_lows = [p.price for p in structure if p.type in (StructureType.HIGHER_LOW, StructureType.LOWER_LOW)]
        swing_highs = [p.price for p in structure if p.type in (StructureType.HIGHER_HIGH, StructureType.LOWER_HIGH)]

        direct_signals: list[Signal] = []
        for bos in aligned_bos:
            order_blocks = detector.identify_order_blocks(higher_candles, bos)
            for ob in order_blocks:
                liquidity_confirmed = False
                if ob.direction == BOSDirection.BULLISH and swing_lows:
                    relevant = [l for l in swing_lows if l <= ob.zone_high]
                    if relevant:
                        liquidity_confirmed = detector.detect_liquidity_sweep(
                            entry_candles, max(relevant), ob.direction,
                        )
                elif ob.direction == BOSDirection.BEARISH and swing_highs:
                    relevant = [h for h in swing_highs if h >= ob.zone_low]
                    if relevant:
                        liquidity_confirmed = detector.detect_liquidity_sweep(
                            entry_candles, min(relevant), ob.direction,
                        )

                fvg_confirmed = detector.detect_fvg(higher_candles, ob, avg_range)
                structural_tp = detector.find_structural_target(structure, ob)

                skip_tf = bool(entry_candles and entry_candles[0].timeframe != config.entry_timeframe)
                sig = generator.generate_signal(
                    ob=ob, candles=entry_candles, htf_candles=higher_candles,
                    config=config, structural_tp=structural_tp,
                    skip_timeframe_check=skip_tf,
                    liquidity_confirmed=liquidity_confirmed,
                    fvg_confirmed=fvg_confirmed,
                )
                if sig is not None:
                    direct_signals.append(sig)

        # Plugin call with 3 TFs
        plugin_signals = algorithm.analyze(entry_candles, higher_candles, trend_candles, config)

        # Compare signal count
        assert len(plugin_signals) == len(direct_signals)

        # Compare signal content (ignoring id and order_block_id which
        # are UUIDs generated fresh by each call)
        for ps, ds in zip(plugin_signals, direct_signals):
            assert ps.instrument == ds.instrument
            assert ps.direction == ds.direction
            assert ps.entry_price == ds.entry_price
            assert ps.stop_loss == ds.stop_loss
            assert ps.take_profit == ds.take_profit
            assert ps.strategy_id == ds.strategy_id
            assert ps.timeframe == ds.timeframe

    def test_insufficient_candles_for_structure(
        self, algorithm: ICTOrderBlockAlgorithm
    ):
        """With fewer than 3 higher-TF candles, no structure is detected,
        so no signals should be produced."""
        config = _config()
        higher = [
            _candle(100, index=0, timeframe=Timeframe.ONE_HOUR),
            _candle(105, index=1, timeframe=Timeframe.ONE_HOUR),
        ]
        entry = [_candle(100, index=i) for i in range(5)]
        trend = [
            _candle(90 + i * 3, index=i, timeframe=Timeframe.FOUR_HOURS)
            for i in range(10)
        ]
        result = algorithm.analyze(entry, higher, trend, config)
        assert result == []


# ── Signal attribution ───────────────────────────────────────────


class TestSignalAttribution:
    def _make_candles_with_signal(self):
        """Create candle sets that produce at least one signal."""
        higher_candles = [
            _candle(100, high=102, low=98, index=0, timeframe=Timeframe.ONE_HOUR),
            _candle(105, high=110, low=103, index=1, timeframe=Timeframe.ONE_HOUR),
            _candle(100, high=103, low=97, index=2, timeframe=Timeframe.ONE_HOUR),
            _candle(108, high=115, low=106, index=3, timeframe=Timeframe.ONE_HOUR),
            _candle(110, high=112, low=107, index=4, timeframe=Timeframe.ONE_HOUR),
        ]
        entry_candles = [
            _candle(104, high=106, low=102, index=0),
            _candle(103, high=105, low=101, index=1),
            _candle(105, high=107, low=103, index=2),
        ]
        # Bullish trend candles (HH/HL pattern)
        trend_candles = [
            _candle(90, high=92, low=88, index=0, timeframe=Timeframe.FOUR_HOURS),
            _candle(95, high=100, low=93, index=1, timeframe=Timeframe.FOUR_HOURS),
            _candle(93, high=96, low=91, index=2, timeframe=Timeframe.FOUR_HOURS),
            _candle(98, high=105, low=96, index=3, timeframe=Timeframe.FOUR_HOURS),
            _candle(96, high=99, low=94, index=4, timeframe=Timeframe.FOUR_HOURS),
            _candle(102, high=110, low=100, index=5, timeframe=Timeframe.FOUR_HOURS),
        ]
        return entry_candles, higher_candles, trend_candles

    def test_signals_have_correct_strategy_id(
        self, algorithm: ICTOrderBlockAlgorithm
    ):
        entry, higher, trend = self._make_candles_with_signal()
        config = _config(id="my-strategy-123")
        signals = algorithm.analyze(entry, higher, trend, config)
        for sig in signals:
            assert sig.strategy_id == "my-strategy-123"

    def test_signals_have_correct_instrument(
        self, algorithm: ICTOrderBlockAlgorithm
    ):
        entry, higher, trend = self._make_candles_with_signal()
        config = _config(instruments=["US30"])
        signals = algorithm.analyze(entry, higher, trend, config)
        for sig in signals:
            assert sig.instrument == "US30"
