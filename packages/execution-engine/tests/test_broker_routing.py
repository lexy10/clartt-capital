"""Unit tests for the BrokerRouter, InstrumentRegistry, and TradeExecutor routing.

Verifies that:
- The router resolves to the correct provider based on category
- Account-level provider overrides win over category defaults
- Instrument-level overrides win over category defaults but lose to account
- TradeExecutor picks the right client per call (execute, modify, close)
- Stub clients return clean failure results without crashing
"""

from unittest.mock import MagicMock

import pytest

from src.executor.clients.base import (
    BrokerProvider,
    BrokerRouter,
    InstrumentCategory,
    OrderResult,
    detect_category,
)
from src.executor.clients.stubs import AlpacaStockClient, BinanceCryptoClient
from src.executor.instrument_registry import InstrumentInfo, InstrumentRegistry
from src.executor.trade_executor import TradeExecutor
from src.models import (
    BOSType,
    Signal,
    SignalDirection,
    SignalMetadata,
    SignalMode,
    Timeframe,
    TradingAccount,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_mock_client(order_id: int, provider: BrokerProvider) -> MagicMock:
    c = MagicMock()
    c.connect.return_value = True
    c.get_symbol_info_tick.return_value = {"bid": 100.0, "ask": 100.1, "time": 0}
    c.send_order.return_value = OrderResult(
        success=True, order_id=order_id, fill_price=100.05, volume=0.01,
    )
    c.modify_position.return_value = OrderResult(success=True, order_id=order_id)
    c.close_position_by_id.return_value = OrderResult(
        success=True, order_id=order_id, fill_price=100.0,
    )
    c.provider = provider
    return c


@pytest.fixture
def deriv_client():
    return _make_mock_client(order_id=111, provider=BrokerProvider.DERIV)


@pytest.fixture
def metaapi_client():
    return _make_mock_client(order_id=222, provider=BrokerProvider.METAAPI)


@pytest.fixture
def router(deriv_client, metaapi_client):
    r = BrokerRouter()
    r.register(BrokerProvider.DERIV, deriv_client)
    r.register(BrokerProvider.METAAPI, metaapi_client)
    return r


@pytest.fixture
def registry():
    r = InstrumentRegistry(backend_url="http://nowhere", ttl_seconds=99999)
    r._cache = {
        "R_25":   InstrumentInfo("R_25",   category=InstrumentCategory.SYNTHETIC),
        "XAUUSD": InstrumentInfo("XAUUSD", category=InstrumentCategory.COMMODITY),
        "US30":   InstrumentInfo("US30",   category=InstrumentCategory.INDEX),
    }
    r._last_refresh = 9_999_999_999
    return r


def _signal(instrument: str) -> Signal:
    return Signal(
        id=f"sig-{instrument}",
        instrument=instrument,
        direction=SignalDirection.BUY,
        entry_price=100.0,
        stop_loss=99.0,
        take_profit=103.0,
        position_size=0.01,
        confidence_score=0.7,
        timeframe=Timeframe.FIVE_MINUTES,
        order_block_id="ob-1",
        strategy_id="strat-1",
        mode=SignalMode.LIVE,
        metadata=SignalMetadata(
            bos_type=BOSType.BULLISH, liquidity_swept=False, session="London",
            spread_at_generation=0.1, volatility_ratio=0.5,
        ),
        exit_rules=None,
        broker_symbol=None,
        created_at="2026-06-06T00:00:00Z",
    )


def _account(broker_override=None) -> TradingAccount:
    return TradingAccount(
        id="acct-1",
        user_id="user-1",
        metaapi_account_id="mt5-1",
        label="Test",
        is_active=True,
        account_kind="personal",
        broker_provider=broker_override,
    )


# ---------------------------------------------------------------------------
# Router resolution tests
# ---------------------------------------------------------------------------


class TestBrokerRouter:
    def test_synthetic_routes_to_deriv(self, router):
        assert router.resolve_provider(InstrumentCategory.SYNTHETIC) == BrokerProvider.DERIV

    def test_index_routes_to_metaapi(self, router):
        assert router.resolve_provider(InstrumentCategory.INDEX) == BrokerProvider.METAAPI

    def test_commodity_routes_to_metaapi(self, router):
        assert router.resolve_provider(InstrumentCategory.COMMODITY) == BrokerProvider.METAAPI

    def test_account_override_wins_over_category(self, router):
        # Synthetic normally -> Deriv, but account override forces MetaAPI
        provider = router.resolve_provider(
            instrument_category=InstrumentCategory.SYNTHETIC,
            account_provider_override=BrokerProvider.METAAPI,
        )
        assert provider == BrokerProvider.METAAPI

    def test_instrument_override_wins_over_category(self, router):
        provider = router.resolve_provider(
            instrument_category=InstrumentCategory.SYNTHETIC,
            instrument_provider_override=BrokerProvider.METAAPI,
        )
        assert provider == BrokerProvider.METAAPI

    def test_account_override_wins_over_instrument_override(self, router):
        provider = router.resolve_provider(
            instrument_category=InstrumentCategory.SYNTHETIC,
            account_provider_override=BrokerProvider.DERIV,
            instrument_provider_override=BrokerProvider.METAAPI,
        )
        assert provider == BrokerProvider.DERIV

    def test_unknown_category_returns_none(self, router):
        assert router.resolve_provider(None) is None

    def test_get_client_for_returns_registered_client(self, router, deriv_client):
        client = router.get_client_for(InstrumentCategory.SYNTHETIC)
        assert client is deriv_client


# ---------------------------------------------------------------------------
# Symbol auto-detection
# ---------------------------------------------------------------------------


class TestSymbolDetection:
    def test_synthetic_prefix(self):
        assert detect_category("R_25") == InstrumentCategory.SYNTHETIC
        assert detect_category("R_75") == InstrumentCategory.SYNTHETIC
        assert detect_category("BOOM_1000") == InstrumentCategory.SYNTHETIC
        assert detect_category("CRASH_500") == InstrumentCategory.SYNTHETIC

    def test_commodity(self):
        assert detect_category("XAUUSD") == InstrumentCategory.COMMODITY
        assert detect_category("OIL") == InstrumentCategory.COMMODITY

    def test_index(self):
        assert detect_category("US30") == InstrumentCategory.INDEX
        assert detect_category("NAS100") == InstrumentCategory.INDEX

    def test_forex(self):
        assert detect_category("EURUSD") == InstrumentCategory.FOREX
        assert detect_category("GBPJPY") == InstrumentCategory.FOREX

    def test_crypto(self):
        assert detect_category("BTCUSDT") == InstrumentCategory.CRYPTO


# ---------------------------------------------------------------------------
# TradeExecutor routing
# ---------------------------------------------------------------------------


class TestTradeExecutorRouting:
    def test_synthetic_signal_executes_on_deriv(
        self, router, registry, deriv_client, metaapi_client,
    ):
        executor = TradeExecutor(broker_router=router, instrument_registry=registry)
        result = executor.execute(_signal("R_25"), _account())
        assert result.order_id == 111  # Deriv mock
        assert deriv_client.send_order.call_count == 1
        assert metaapi_client.send_order.call_count == 0

    def test_commodity_signal_executes_on_metaapi(
        self, router, registry, deriv_client, metaapi_client,
    ):
        executor = TradeExecutor(broker_router=router, instrument_registry=registry)
        result = executor.execute(_signal("XAUUSD"), _account())
        assert result.order_id == 222  # MetaAPI mock
        assert metaapi_client.send_order.call_count == 1
        assert deriv_client.send_order.call_count == 0

    def test_account_override_forces_different_broker(
        self, router, registry, deriv_client, metaapi_client,
    ):
        executor = TradeExecutor(broker_router=router, instrument_registry=registry)
        # R_25 normally -> Deriv, but force MetaAPI via account override
        result = executor.execute(_signal("R_25"), _account(broker_override="metaapi"))
        assert result.order_id == 222
        assert metaapi_client.send_order.call_count == 1
        assert deriv_client.send_order.call_count == 0

    def test_modify_order_routes_to_right_broker(
        self, router, registry, deriv_client, metaapi_client,
    ):
        executor = TradeExecutor(broker_router=router, instrument_registry=registry)
        # Modify an R_25 position -> Deriv
        ok = executor.modify_order(
            order_id=111, account=_account(),
            modifications={"stop_loss": 99.5, "take_profit": 105.0},
            instrument="R_25",
        )
        assert ok is True
        assert deriv_client.modify_position.call_count == 1
        assert metaapi_client.modify_position.call_count == 0

    def test_close_position_routes_to_right_broker(
        self, router, registry, deriv_client, metaapi_client,
    ):
        executor = TradeExecutor(broker_router=router, instrument_registry=registry)
        # Close an XAUUSD position -> MetaAPI
        result = executor.close_position(
            position_id=222, account=_account(), instrument="XAUUSD",
        )
        assert result.order_id == 222
        assert metaapi_client.close_position_by_id.call_count == 1
        assert deriv_client.close_position_by_id.call_count == 0

    def test_legacy_single_client_still_works(self, deriv_client):
        """Backwards compat: passing broker_client= alone should still work."""
        executor = TradeExecutor(broker_client=deriv_client)
        result = executor.execute(_signal("R_25"), _account())
        assert result.order_id == 111
        assert deriv_client.send_order.call_count == 1

    def test_unregistered_provider_falls_back_to_legacy_client(
        self, deriv_client, registry,
    ):
        """If a category has no registered provider in the router,
        executor should fall back to the legacy client rather than crash."""
        empty_router = BrokerRouter()
        # No clients registered at all
        executor = TradeExecutor(
            broker_client=deriv_client,
            broker_router=empty_router,
            instrument_registry=registry,
        )
        result = executor.execute(_signal("R_25"), _account())
        # Falls back to deriv_client (the legacy single client)
        assert result.order_id == 111


# ---------------------------------------------------------------------------
# Stub clients
# ---------------------------------------------------------------------------


class TestStubClients:
    def test_alpaca_stub_returns_clean_failure(self):
        client = AlpacaStockClient()
        result = client.send_order("AAPL", "BUY", 1.0, 200.0, 195.0, 210.0)
        assert result.success is False
        assert "stock" in result.error_message.lower()
        assert "alpaca" in result.error_message.lower()

    def test_binance_stub_returns_clean_failure(self):
        client = BinanceCryptoClient()
        result = client.send_order("BTCUSDT", "BUY", 0.1, 50000.0, 49000.0, 52000.0)
        assert result.success is False
        assert "crypto" in result.error_message.lower()

    def test_stub_get_positions_returns_empty_list(self):
        assert AlpacaStockClient().get_positions() == []
        assert BinanceCryptoClient().get_positions() == []

    def test_stub_get_tick_returns_none(self):
        assert AlpacaStockClient().get_symbol_info_tick("AAPL") is None


# ---------------------------------------------------------------------------
# InstrumentRegistry
# ---------------------------------------------------------------------------


class TestInstrumentRegistry:
    def test_get_known_symbol_returns_cached_info(self, registry):
        info = registry.get("R_25")
        assert info.category == InstrumentCategory.SYNTHETIC

    def test_get_unknown_symbol_auto_detects(self, registry):
        info = registry.get("BTCUSDT")  # not in cache
        assert info.category == InstrumentCategory.CRYPTO

    def test_get_unrecognized_symbol_returns_none_category(self, registry):
        info = registry.get("???")
        assert info.category is None
