"""Instrument specifications for pip-aware calculations."""

from pydantic import BaseModel


class InstrumentSpecs(BaseModel):
    """Contract specifications for an instrument, fetched from backend."""
    symbol: str
    pip_size: float  # smallest price increment (e.g. 0.01 for XAUUSD, 1.0 for US30)
    pip_value: float  # value per pip per 1 lot in USD
    contract_size: float  # value of 1 standard lot
    min_lot: float = 0.01
    lot_step: float = 0.01
    leverage: int = 100

    def price_to_pips(self, price_distance: float) -> float:
        """Convert a price distance to pips using this instrument's pip_size."""
        if self.pip_size == 0:
            return 0.0
        return abs(price_distance) / self.pip_size

    def pips_to_price(self, pips: float) -> float:
        """Convert pips to a price distance using this instrument's pip_size."""
        return pips * self.pip_size
