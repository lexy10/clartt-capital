"""Base broker client protocol and category-based routing system.

This module defines the abstraction layer that lets the platform trade
different instrument categories through different broker APIs:

- Synthetic indices (R_25, R_75, BOOM, CRASH) -> Deriv direct API
- Forex / commodities / indices                -> MetaAPI (MT5)
- Stocks                                       -> Alpaca / IBKR (future)
- Crypto                                       -> Binance / Coinbase (future)
- Futures                                      -> CME / IBKR (future)

Each broker client implements the BrokerClient Protocol. The BrokerRouter
picks the correct client based on:
  1. Account override (if the account specifies a provider explicitly)
  2. Instrument category default (e.g. all synthetics -> Deriv)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# ExitDetails — broker-agnostic closed-position information
# ---------------------------------------------------------------------------


@dataclass
class ExitDetails:
    """Information about a closed/sold position, fetched from the broker.

    Used by the reconciler and the position monitor to write exit data back
    to the `trades` table when a position closes (either via our own
    sell call, the broker's SL/TP, or expiry).
    """
    broker_order_id: int
    exit_price: float
    profit_loss: float
    closed_at: datetime
    status: str = "closed"   # "closed" | "won" | "lost" | "expired"
    raw: Optional[dict] = None


# ---------------------------------------------------------------------------
# Categories
# ---------------------------------------------------------------------------


class InstrumentCategory(str, Enum):
    """Category of a tradable instrument — drives broker routing."""

    SYNTHETIC = "synthetic"   # Deriv R_*, BOOM_*, CRASH_*
    FOREX = "forex"           # EURUSD, GBPUSD, etc.
    COMMODITY = "commodity"   # XAUUSD, OIL, etc.
    INDEX = "index"           # US30, NAS100, SPX500
    STOCK = "stock"           # AAPL, TSLA, MSFT
    CRYPTO = "crypto"         # BTCUSD, ETHUSD
    FUTURE = "future"         # ES, NQ, CL


class AccountKind(str, Enum):
    """Account funding type — drives risk settings."""

    PERSONAL = "personal"     # Trader's own money
    PROP = "prop"             # Prop firm challenge / funded
    DEMO = "demo"             # Practice account


class BrokerProvider(str, Enum):
    """Specific broker / data provider."""

    DERIV = "deriv"           # Deriv direct WebSocket API
    METAAPI = "metaapi"       # MetaAPI cloud (MT5 wrapper)
    ALPACA = "alpaca"         # Alpaca Markets (stocks)
    BINANCE = "binance"       # Binance (crypto)
    IBKR = "ibkr"             # Interactive Brokers
    STUB = "stub"             # Demo / testing


# ---------------------------------------------------------------------------
# OrderResult — broker-agnostic result
# ---------------------------------------------------------------------------


class OrderResult:
    """Result returned by the broker for an order request."""

    def __init__(
        self,
        success: bool,
        order_id: int = 0,
        fill_price: float = 0.0,
        error_code: int = 0,
        error_message: str = "",
        volume: float = 0.0,
        bid: float = 0.0,
        ask: float = 0.0,
    ):
        self.success = success
        self.order_id = order_id
        self.fill_price = fill_price
        self.error_code = error_code
        self.error_message = error_message
        self.volume = volume
        self.bid = bid
        self.ask = ask


# ---------------------------------------------------------------------------
# BrokerClient Protocol — every concrete client must satisfy this
# ---------------------------------------------------------------------------


@runtime_checkable
class BrokerClient(Protocol):
    """Protocol all broker clients must implement.

    Implementations: DerivSyntheticClient, MetaApiForexClient,
    AlpacaStockClient, BinanceCryptoClient, StubBrokerClient.
    """

    #: The provider this client wraps — used for diagnostics
    provider: BrokerProvider

    def connect(self, account_id: str) -> bool:
        """Connect to a trading account. Idempotent."""
        ...

    def send_order(
        self,
        instrument: str,
        direction: str,
        volume: float,
        price: float,
        sl: float,
        tp: float,
    ) -> OrderResult:
        """Place a market order. direction in {'BUY','SELL'}."""
        ...

    def modify_position(
        self,
        order_id: int,
        sl: Optional[float] = None,
        tp: Optional[float] = None,
    ) -> OrderResult:
        """Modify SL/TP on an open position."""
        ...

    def close_position_by_id(self, position_id: int) -> OrderResult:
        """Close an open position by its broker ID."""
        ...

    def get_symbol_info_tick(self, instrument: str) -> Optional[dict]:
        """Return latest tick: {'bid': float, 'ask': float, 'time': int}."""
        ...

    def get_positions(self) -> list[dict]:
        """List all open positions on the connected account."""
        ...


# ---------------------------------------------------------------------------
# Category -> default provider mapping
# ---------------------------------------------------------------------------


CATEGORY_DEFAULT_PROVIDER: dict[InstrumentCategory, BrokerProvider] = {
    InstrumentCategory.SYNTHETIC: BrokerProvider.DERIV,
    InstrumentCategory.FOREX:     BrokerProvider.METAAPI,
    InstrumentCategory.COMMODITY: BrokerProvider.METAAPI,
    InstrumentCategory.INDEX:     BrokerProvider.METAAPI,
    InstrumentCategory.STOCK:     BrokerProvider.ALPACA,
    InstrumentCategory.CRYPTO:    BrokerProvider.BINANCE,
    InstrumentCategory.FUTURE:    BrokerProvider.IBKR,
}


# ---------------------------------------------------------------------------
# Instrument symbol -> category auto-detection
# ---------------------------------------------------------------------------


def detect_category(symbol: str) -> Optional[InstrumentCategory]:
    """Best-effort auto-detection of category from a symbol string.

    Used when the instrument row doesn't have a category set yet
    (migration of existing data, or convenience for new instruments).
    """
    s = symbol.upper()

    # Synthetic indices
    if s.startswith("R_") or s.startswith("BOOM") or s.startswith("CRASH") or s.startswith("STEP") or "VOLATILITY" in s:
        return InstrumentCategory.SYNTHETIC

    # Crypto pairs (common suffixes)
    if any(s.endswith(suffix) for suffix in ("USDT", "USDC", "BUSD")) or s.startswith(("BTC", "ETH", "XRP", "SOL", "DOGE")):
        return InstrumentCategory.CRYPTO

    # Common commodities
    if "XAU" in s or "XAG" in s or s in ("OIL", "WTI", "BRENT", "NATGAS"):
        return InstrumentCategory.COMMODITY

    # Common indices
    if s in ("US30", "US500", "NAS100", "SPX500", "GER40", "UK100", "JPN225", "AUS200"):
        return InstrumentCategory.INDEX

    # Forex (6-letter currency pair, all letters)
    if len(s) == 6 and s.isalpha():
        return InstrumentCategory.FOREX

    # Stock tickers — 1-5 uppercase letters with no special chars
    if 1 <= len(s) <= 5 and s.isalpha():
        return InstrumentCategory.STOCK

    return None  # Unknown — let the caller decide


# ---------------------------------------------------------------------------
# BrokerRouter — the central dispatcher
# ---------------------------------------------------------------------------


class BrokerRouter:
    """Routes trade operations to the right broker client based on
    instrument category and account preferences.

    Example:
        router = BrokerRouter()
        router.register(BrokerProvider.DERIV, DerivSyntheticClient(...))
        router.register(BrokerProvider.METAAPI, MetaApiForexClient(...))

        client = router.get_client_for(instrument="R_25", account=acct)
        client.send_order(...)
    """

    def __init__(self) -> None:
        self._clients: dict[BrokerProvider, BrokerClient] = {}

    def register(self, provider: BrokerProvider, client: BrokerClient) -> None:
        """Register a broker client for a given provider."""
        self._clients[provider] = client
        logger.info("BrokerRouter: registered %s client", provider.value)

    def get(self, provider: BrokerProvider) -> Optional[BrokerClient]:
        """Get a client by provider (returns None if not registered)."""
        return self._clients.get(provider)

    def resolve_provider(
        self,
        instrument_category: Optional[InstrumentCategory],
        account_provider_override: Optional[BrokerProvider] = None,
        instrument_provider_override: Optional[BrokerProvider] = None,
    ) -> Optional[BrokerProvider]:
        """Decide which provider should handle a given instrument+account.

        Priority:
        1. Account-level explicit provider (e.g., "this user wants all R_25 via MetaAPI")
        2. Instrument-level explicit provider (e.g., "this symbol uses Alpaca")
        3. Category default (synthetic -> Deriv, forex -> MetaAPI, etc.)
        """
        if account_provider_override is not None:
            return account_provider_override
        if instrument_provider_override is not None:
            return instrument_provider_override
        if instrument_category is not None:
            return CATEGORY_DEFAULT_PROVIDER.get(instrument_category)
        return None

    def get_client_for(
        self,
        instrument_category: Optional[InstrumentCategory],
        account_provider_override: Optional[BrokerProvider] = None,
        instrument_provider_override: Optional[BrokerProvider] = None,
    ) -> Optional[BrokerClient]:
        """Look up the right client for the given instrument+account context."""
        provider = self.resolve_provider(
            instrument_category=instrument_category,
            account_provider_override=account_provider_override,
            instrument_provider_override=instrument_provider_override,
        )
        if provider is None:
            return None
        client = self._clients.get(provider)
        if client is None:
            logger.warning(
                "BrokerRouter: no client registered for provider %s", provider.value
            )
        return client

    def list_registered(self) -> list[BrokerProvider]:
        """List which providers currently have a client registered."""
        return list(self._clients.keys())
