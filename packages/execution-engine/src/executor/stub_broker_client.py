"""Stub broker client for development/demo mode when no real broker is available."""

import logging
from typing import Optional

from src.executor.clients.base import BrokerProvider
from src.executor.trade_executor import OrderResult

logger = logging.getLogger("execution_engine.stub_broker")


class StubBrokerClient:
    """A stub broker client that satisfies the BrokerClient protocol without a real connection."""

    provider = BrokerProvider.STUB

    def connect(self, account_id: str) -> bool:
        logger.info("StubBrokerClient: simulated connect to account %s", account_id)
        return True

    def send_order(
        self,
        instrument: str,
        direction: str,
        volume: float,
        price: float,
        sl: float,
        tp: float,
    ) -> OrderResult:
        logger.info(
            "StubBrokerClient: simulated %s %s %.2f lots @ %.2f (SL=%.2f, TP=%.2f)",
            direction, instrument, volume, price, sl, tp,
        )
        return OrderResult(success=True, order_id=0, fill_price=price, volume=volume)

    def modify_position(
        self,
        order_id: int,
        sl: Optional[float] = None,
        tp: Optional[float] = None,
    ) -> OrderResult:
        logger.info("StubBrokerClient: simulated modify position %d (SL=%s, TP=%s)", order_id, sl, tp)
        return OrderResult(success=True, order_id=order_id)

    def close_position_by_id(self, position_id: int) -> OrderResult:
        logger.info("StubBrokerClient: simulated close position %d", position_id)
        return OrderResult(success=True, order_id=position_id)

    def get_symbol_info_tick(self, instrument: str) -> Optional[dict]:
        return {"bid": 42000.0, "ask": 42002.0, "time": 0}
    def get_positions(self) -> list[dict]:
        logger.info("StubBrokerClient: simulated get_positions — returning empty")
        return []
