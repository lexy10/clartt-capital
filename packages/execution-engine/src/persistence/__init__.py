"""Direct database persistence for execution-engine artifacts.

Bypasses the trades:results pub/sub round-trip to write trade rows
directly to PostgreSQL the moment they're filled. Keeps the pub/sub
publish for dashboard WebSocket updates — but persistence no longer
depends on the backend subscriber being healthy.
"""

from src.persistence.trade_persister import TradePersister

__all__ = ["TradePersister"]
