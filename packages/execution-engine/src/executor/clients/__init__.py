"""Broker client implementations organized by instrument category.

Public surface:
- BrokerRouter, BrokerProvider, InstrumentCategory, AccountKind from .base
- Concrete clients: DerivSyntheticClient, MetaApiForexClient,
  AlpacaStockClient, BinanceCryptoClient, IBKRFutureClient

Adding a new broker:
1. Create a new module under this package
2. Implement the BrokerClient Protocol (see base.py)
3. Set the `provider` class attribute to a BrokerProvider value
4. Register it with the BrokerRouter at startup (main.py)
"""

from src.executor.clients.base import (
    AccountKind,
    BrokerClient,
    BrokerProvider,
    BrokerRouter,
    CATEGORY_DEFAULT_PROVIDER,
    InstrumentCategory,
    OrderResult,
    detect_category,
)
from src.executor.clients.deriv import DerivSyntheticClient
from src.executor.clients.metaapi import MetaApiForexClient
from src.executor.clients.stubs import (
    AlpacaStockClient,
    BinanceCryptoClient,
    IBKRFutureClient,
)

__all__ = [
    "AccountKind",
    "AlpacaStockClient",
    "BinanceCryptoClient",
    "BrokerClient",
    "BrokerProvider",
    "BrokerRouter",
    "CATEGORY_DEFAULT_PROVIDER",
    "DerivSyntheticClient",
    "IBKRFutureClient",
    "InstrumentCategory",
    "MetaApiForexClient",
    "OrderResult",
    "detect_category",
]
