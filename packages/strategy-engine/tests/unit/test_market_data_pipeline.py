"""Unit tests for MarketDataPipeline."""

import pytest
from datetime import datetime, timezone

from src.models.tick import Tick
from src.models.candle import Candle
from src.models.timeframe import Timeframe
from src.pipeline.market_data_pipeline import MarketDataPipeline


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _tick(price: float, volume: float = 1.0, ts: str = "2024-01-15T10:00:00+00:00", instrument: str = "US30") -> Tick:
    return Tick(instrument=instrument, price=price, volume=volume, timestamp=ts)


# ---------------------------------------------------------------------------
# validate_tick
# ---------------------------------------------------------------------------

class TestValidateTick:
    def setup_method(self):
        self.pipeline = MarketDataPipeline()

    def test_valid_tick(self):
        tick = _tick(35000.0)
        assert self.pipeline.validate_tick(tick) is True

    def test_rejects_zero_price(self):
        tick = _tick(0.0)
        assert self.pipeline.validate_tick(tick) is False

    def test_rejects_negative_price(self):
        tick = _tick(-100.0)
        assert self.pipeline.validate_tick(tick) is False

    def test_rejects_negative_volume(self):
        tick = _tick(35000.0, volume=-1.0)
        assert self.pipeline.validate_tick(tick) is False

    def test_allows_zero_volume(self):
        tick = _tick(35000.0, volume=0.0)
        assert self.pipeline.validate_tick(tick) is True

    def test_rejects_empty_instrument(self):
        tick = Tick(instrument="", price=100.0, volume=1.0, timestamp="2024-01-15T10:00:00+00:00")
        assert self.pipeline.validate_tick(tick) is False

    def test_rejects_whitespace_instrument(self):
        tick = Tick(instrument="   ", price=100.0, volume=1.0, timestamp="2024-01-15T10:00:00+00:00")
        assert self.pipeline.validate_tick(tick) is False

    def test_rejects_empty_timestamp(self):
        tick = Tick(instrument="US30", price=100.0, volume=1.0, timestamp="")
        assert self.pipeline.validate_tick(tick) is False

    def test_rejects_unparseable_timestamp(self):
        tick = Tick(instrument="US30", price=100.0, volume=1.0, timestamp="not-a-date")
        assert self.pipeline.validate_tick(tick) is False


# ---------------------------------------------------------------------------
# aggregate_candle
# ---------------------------------------------------------------------------

class TestAggregateCandle:
    def setup_method(self):
        self.pipeline = MarketDataPipeline()

    def test_single_tick(self):
        ticks = [_tick(35000.0, volume=10.0)]
        candle = self.pipeline.aggregate_candle(ticks, Timeframe.ONE_MINUTE)
        assert candle.open == 35000.0
        assert candle.high == 35000.0
        assert candle.low == 35000.0
        assert candle.close == 35000.0
        assert candle.volume == 10.0
        assert candle.instrument == "US30"
        assert candle.timeframe == Timeframe.ONE_MINUTE

    def test_multiple_ticks_ohlcv(self):
        ticks = [
            _tick(100.0, volume=1.0, ts="2024-01-15T10:00:01+00:00"),
            _tick(110.0, volume=2.0, ts="2024-01-15T10:00:02+00:00"),
            _tick(90.0, volume=3.0, ts="2024-01-15T10:00:03+00:00"),
            _tick(105.0, volume=4.0, ts="2024-01-15T10:00:04+00:00"),
        ]
        candle = self.pipeline.aggregate_candle(ticks, Timeframe.ONE_MINUTE)
        assert candle.open == 100.0   # first
        assert candle.high == 110.0   # max
        assert candle.low == 90.0     # min
        assert candle.close == 105.0  # last
        assert candle.volume == 10.0  # sum

    def test_empty_ticks_raises(self):
        with pytest.raises(ValueError, match="empty"):
            self.pipeline.aggregate_candle([], Timeframe.ONE_MINUTE)


# ---------------------------------------------------------------------------
# ingest_tick + flush + get_candles (integration-style unit tests)
# ---------------------------------------------------------------------------

class TestIngestAndRetrieve:
    def setup_method(self):
        self.pipeline = MarketDataPipeline()

    def test_ingest_valid_tick_and_flush(self):
        self.pipeline.ingest_tick(_tick(100.0, volume=5.0, ts="2024-01-15T10:00:05+00:00"))
        self.pipeline.ingest_tick(_tick(110.0, volume=3.0, ts="2024-01-15T10:00:10+00:00"))
        self.pipeline.ingest_tick(_tick(95.0, volume=2.0, ts="2024-01-15T10:00:15+00:00"))

        candles = self.pipeline.flush_candles("US30", Timeframe.ONE_MINUTE)
        assert len(candles) == 1
        c = candles[0]
        assert c.open == 100.0
        assert c.high == 110.0
        assert c.low == 95.0
        assert c.close == 95.0
        assert c.volume == 10.0

    def test_invalid_tick_does_not_halt_pipeline(self):
        """Invalid ticks are skipped; valid ticks still aggregate correctly."""
        self.pipeline.ingest_tick(_tick(-5.0, ts="2024-01-15T10:00:01+00:00"))  # invalid
        self.pipeline.ingest_tick(_tick(100.0, ts="2024-01-15T10:00:02+00:00"))  # valid
        self.pipeline.ingest_tick(_tick(0.0, ts="2024-01-15T10:00:03+00:00"))    # invalid
        self.pipeline.ingest_tick(_tick(200.0, ts="2024-01-15T10:00:04+00:00"))  # valid

        candles = self.pipeline.flush_candles("US30", Timeframe.ONE_MINUTE)
        assert len(candles) == 1
        assert candles[0].open == 100.0
        assert candles[0].close == 200.0
        assert candles[0].volume == 2.0

    def test_get_candles_returns_most_recent(self):
        # Ingest ticks across two different 1-minute windows
        self.pipeline.ingest_tick(_tick(100.0, ts="2024-01-15T10:00:05+00:00"))
        self.pipeline.ingest_tick(_tick(200.0, ts="2024-01-15T10:01:05+00:00"))
        self.pipeline.ingest_tick(_tick(300.0, ts="2024-01-15T10:02:05+00:00"))

        self.pipeline.flush_candles("US30", Timeframe.ONE_MINUTE)

        # Get last 2 candles
        candles = self.pipeline.get_candles("US30", Timeframe.ONE_MINUTE, 2)
        assert len(candles) == 2
        assert candles[0].open == 200.0
        assert candles[1].open == 300.0

    def test_get_candles_count_exceeds_available(self):
        self.pipeline.ingest_tick(_tick(100.0, ts="2024-01-15T10:00:05+00:00"))
        self.pipeline.flush_candles("US30", Timeframe.ONE_MINUTE)

        candles = self.pipeline.get_candles("US30", Timeframe.ONE_MINUTE, 100)
        assert len(candles) == 1

    def test_get_candles_empty(self):
        candles = self.pipeline.get_candles("US30", Timeframe.ONE_MINUTE, 10)
        assert candles == []

    def test_multiple_instruments(self):
        self.pipeline.ingest_tick(_tick(100.0, ts="2024-01-15T10:00:05+00:00", instrument="US30"))
        self.pipeline.ingest_tick(_tick(5000.0, ts="2024-01-15T10:00:05+00:00", instrument="NAS100"))

        self.pipeline.flush_candles("US30", Timeframe.ONE_MINUTE)
        self.pipeline.flush_candles("NAS100", Timeframe.ONE_MINUTE)

        us30 = self.pipeline.get_candles("US30", Timeframe.ONE_MINUTE, 10)
        nas100 = self.pipeline.get_candles("NAS100", Timeframe.ONE_MINUTE, 10)

        assert len(us30) == 1
        assert us30[0].open == 100.0
        assert len(nas100) == 1
        assert nas100[0].open == 5000.0

    def test_multiple_timeframes(self):
        """A single tick should be buffered into all timeframe windows."""
        self.pipeline.ingest_tick(_tick(100.0, ts="2024-01-15T10:00:05+00:00"))

        for tf in Timeframe:
            candles = self.pipeline.flush_candles("US30", tf)
            assert len(candles) >= 1, f"Expected candle for timeframe {tf.value}"


# ---------------------------------------------------------------------------
# Time manipulation for replay/backtesting
# ---------------------------------------------------------------------------

class TestTimeManipulation:
    def test_custom_time_fn(self):
        """Pipeline accepts a custom time function for replay/backtesting."""
        fixed_time = datetime(2024, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
        pipeline = MarketDataPipeline(time_fn=lambda: fixed_time)
        # The pipeline should be constructable and functional with a custom clock
        pipeline.ingest_tick(_tick(100.0, ts="2024-06-01T12:00:00+00:00"))
        candles = pipeline.flush_candles("US30", Timeframe.ONE_MINUTE)
        assert len(candles) == 1


# ---------------------------------------------------------------------------
# flush_all
# ---------------------------------------------------------------------------

class TestFlushAll:
    def test_flush_all_aggregates_everything(self):
        pipeline = MarketDataPipeline()
        pipeline.ingest_tick(_tick(100.0, ts="2024-01-15T10:00:05+00:00", instrument="US30"))
        pipeline.ingest_tick(_tick(200.0, ts="2024-01-15T10:00:05+00:00", instrument="NAS100"))

        all_candles = pipeline.flush_all()
        # Each tick goes into all 7 timeframes, 2 instruments = 14 candles
        assert len(all_candles) == 14
