"""Legacy shim — re-exports MetaApiForexClient as MetaApiClient.

The implementation moved to src.executor.clients.metaapi as part of the
broker-routing refactor. This module exists so any old imports keep working.
"""

from src.executor.clients.metaapi import MetaApiForexClient as MetaApiClient

__all__ = ["MetaApiClient"]
