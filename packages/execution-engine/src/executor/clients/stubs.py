"""Stub broker clients — placeholders for stocks, crypto, futures.

These return NotImplementedError on actual order placement but satisfy the
BrokerClient Protocol so the router can be wired up immediately. When a real
integration is added, replace each class with the real implementation.

Each stub still implements connect / get_symbol_info_tick / get_positions
as no-ops so the platform doesn't crash if it routes a tick query through
them before the real client is built.
"""

import logging
from typing import Optional

from src.executor.clients.base import BrokerProvider, OrderResult

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Base — shared NotImplementedError behaviour
# ---------------------------------------------------------------------------


class _NotYetImplementedClient:
    """Base class for category clients that aren't built yet.

    Subclasses set `provider` and `category_name`. All trading methods return
    a clean failure OrderResult so the platform can keep running.
    """

    provider: BrokerProvider = BrokerProvider.STUB
    category_name: str = "unknown"

    def connect(self, account_id: str) -> bool:
        logger.warning(
            "%s client: connect() called but %s trading is not yet implemented",
            self.provider.value, self.category_name,
        )
        return False

    def _not_implemented(self, op: str) -> OrderResult:
        msg = f"{self.category_name} trading via {self.provider.value} is not yet implemented"
        logger.error("%s.%s: %s", self.__class__.__name__, op, msg)
        return OrderResult(success=False, error_message=msg)

    def send_order(self, instrument, direction, volume, price, sl, tp) -> OrderResult:
        return self._not_implemented("send_order")

    def modify_position(self, order_id, sl=None, tp=None) -> OrderResult:
        return self._not_implemented("modify_position")

    def close_position_by_id(self, position_id) -> OrderResult:
        return self._not_implemented("close_position_by_id")

    def get_symbol_info_tick(self, instrument: str) -> Optional[dict]:
        return None

    def get_positions(self) -> list[dict]:
        return []


# ---------------------------------------------------------------------------
# Per-category stubs
# ---------------------------------------------------------------------------


class AlpacaStockClient(_NotYetImplementedClient):
    """Future: Alpaca Markets REST API for US stocks (AAPL, TSLA, etc.).

    When implementing:
    - Auth via API key + secret in env (ALPACA_API_KEY, ALPACA_API_SECRET)
    - Endpoint: https://api.alpaca.markets/v2/orders
    - Type: market, time_in_force: day, side: buy/sell, qty: shares
    - SL/TP via bracket orders (order_class=bracket)
    """
    provider = BrokerProvider.ALPACA
    category_name = "stock"


class BinanceCryptoClient(_NotYetImplementedClient):
    """Future: Binance Spot/Futures API for crypto (BTCUSDT, ETHUSDT, etc.).

    When implementing:
    - Auth via API key + secret (BINANCE_API_KEY, BINANCE_API_SECRET)
    - Endpoint: https://api.binance.com/api/v3/order (spot) or fapi.binance.com (futures)
    - SL/TP via STOP_MARKET + TAKE_PROFIT_MARKET secondary orders
    """
    provider = BrokerProvider.BINANCE
    category_name = "crypto"


class IBKRFutureClient(_NotYetImplementedClient):
    """Future: Interactive Brokers TWS / Gateway API for futures (ES, NQ, CL).

    When implementing:
    - Auth via IB Gateway running locally + ib_insync library
    - SL/TP via bracket orders (parent + 2 children)
    """
    provider = BrokerProvider.IBKR
    category_name = "future"
