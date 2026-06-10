"""Equity Curve Monitor — automated drawdown detection and signal pause.

Tracks peak equity (high-water mark) per account and pauses signal
generation when drawdown exceeds a configurable threshold. Auto-deactivates
on recovery.

Requirements: 23.1–23.6
"""

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Optional, Protocol

import requests
from prometheus_client import Counter, Gauge
from redis import Redis

logger = logging.getLogger("strategy_engine.agents.equity_monitor")

HWM_KEY_PREFIX = "equity:hwm"
PAUSE_KEY_PREFIX = "equity:paused"
EVENTS_STREAM = "agents:events"
ACTIVITY_CHANNEL = "agents:activity"

# Prometheus metrics (Req 23.9)
equity_pause_activations_total = Counter(
    "equity_pause_activations_total",
    "Total equity pause activations",
    labelnames=["account_id"],
)
equity_pause_active = Gauge(
    "equity_pause_active",
    "Whether equity pause is active for an account (0 or 1)",
    labelnames=["account_id"],
)
equity_current_drawdown_pct = Gauge(
    "equity_current_drawdown_pct",
    "Current drawdown percentage from peak for an account",
    labelnames=["account_id"],
)


class EventPublisherProtocol(Protocol):
    def publish(self, event: Any) -> None: ...


class EquityCurveMonitor:
    """Monitors equity curves and pauses signal generation on excessive drawdown.

    Constructor:
        redis_client:     Redis connection for HWM and pause state
        event_publisher:  EventPublisher for equity pause events
        backend_url:      Backend REST API base URL for portfolio queries
    """

    def __init__(
        self,
        redis_client: Redis,
        event_publisher: Optional[EventPublisherProtocol] = None,
        backend_url: str = "http://backend:3000",
    ) -> None:
        self._redis = redis_client
        self._event_publisher = event_publisher
        self._backend_url = backend_url.rstrip("/")

        # Configuration from env vars (Req 23.8)
        self._max_drawdown_pct = float(
            os.environ.get("EQUITY_MAX_DRAWDOWN_PCT", "10.0")
        )
        self._recovery_pct = float(
            os.environ.get("EQUITY_RECOVERY_PCT", "5.0")
        )
        self._check_interval = int(
            os.environ.get("EQUITY_CHECK_INTERVAL_SECONDS", "60")
        )

    def check(self, account_id: str) -> dict:
        """Check equity for a single account and activate/deactivate pause.

        Returns a status dict with drawdown_pct, hwm, current_equity, paused.
        """
        equity = self._fetch_equity(account_id)
        if equity is None:
            return {
                "account_id": account_id,
                "paused": self.is_paused(account_id),
                "error": "could not fetch equity",
            }

        hwm = self._get_hwm(account_id)

        # Update HWM if current equity is a new peak (Req 23.2)
        if equity > hwm:
            hwm = equity
            self._set_hwm(account_id, hwm)

        # Calculate drawdown
        if hwm <= 0:
            drawdown_pct = 0.0
        else:
            drawdown_pct = ((hwm - equity) / hwm) * 100.0

        equity_current_drawdown_pct.labels(account_id=account_id).set(drawdown_pct)

        currently_paused = self.is_paused(account_id)

        # Activate pause if drawdown exceeds threshold (Req 23.3)
        if not currently_paused and drawdown_pct >= self._max_drawdown_pct:
            self._activate_pause(account_id, hwm, equity, drawdown_pct)
            currently_paused = True

        # Auto-deactivate if drawdown recovers below recovery threshold (Req 23.6)
        elif currently_paused and drawdown_pct < self._recovery_pct:
            self._deactivate_pause(account_id, hwm, equity, drawdown_pct)
            currently_paused = False

        return {
            "account_id": account_id,
            "paused": currently_paused,
            "drawdown_pct": round(drawdown_pct, 2),
            "hwm": hwm,
            "current_equity": equity,
        }

    def is_paused(self, account_id: str) -> bool:
        """Return True if signal generation is paused for this account."""
        key = f"{PAUSE_KEY_PREFIX}:{account_id}"
        val = self._redis.get(key)
        if val is None:
            return False
        decoded = val if isinstance(val, str) else val.decode("utf-8")
        return decoded == "paused"

    def manual_deactivate(self, account_id: str) -> None:
        """Manually deactivate equity pause for an account (Req 23.7)."""
        key = f"{PAUSE_KEY_PREFIX}:{account_id}"
        self._redis.delete(key)
        equity_pause_active.labels(account_id=account_id).set(0)
        logger.info("Equity pause manually deactivated for account %s", account_id)

    def list_accounts_status(self) -> list[dict]:
        """List all accounts with their pause status."""
        results: list[dict] = []
        cursor = 0
        while True:
            cursor, keys = self._redis.scan(
                cursor, match=f"{HWM_KEY_PREFIX}:*", count=100
            )
            for key in keys:
                key_str = key if isinstance(key, str) else key.decode("utf-8")
                account_id = key_str.split(":")[-1]
                hwm = self._get_hwm(account_id)
                paused = self.is_paused(account_id)
                results.append({
                    "account_id": account_id,
                    "hwm": hwm,
                    "paused": paused,
                })
            if cursor == 0:
                break
        return results

    # ── Private Helpers ───────────────────────────────────────────────

    def _get_hwm(self, account_id: str) -> float:
        key = f"{HWM_KEY_PREFIX}:{account_id}"
        val = self._redis.get(key)
        if val is None:
            return 0.0
        decoded = val if isinstance(val, str) else val.decode("utf-8")
        try:
            return float(decoded)
        except (ValueError, TypeError):
            return 0.0

    def _set_hwm(self, account_id: str, value: float) -> None:
        key = f"{HWM_KEY_PREFIX}:{account_id}"
        self._redis.set(key, str(value))

    def _fetch_equity(self, account_id: str) -> Optional[float]:
        """Fetch current equity from backend portfolio summary (Req 23.1)."""
        try:
            resp = requests.get(
                f"{self._backend_url}/api/portfolios/summary",
                params={"account_id": account_id},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            # Handle both single-account and multi-account responses
            if isinstance(data, dict):
                return float(data.get("equity", 0))
            return None
        except Exception as exc:
            logger.warning(
                "Failed to fetch equity for account %s: %s", account_id, exc
            )
            return None

    def _activate_pause(
        self,
        account_id: str,
        hwm: float,
        equity: float,
        drawdown_pct: float,
    ) -> None:
        """Activate equity pause for an account (Req 23.3, 23.4)."""
        key = f"{PAUSE_KEY_PREFIX}:{account_id}"
        self._redis.set(key, "paused")
        equity_pause_active.labels(account_id=account_id).set(1)
        equity_pause_activations_total.labels(account_id=account_id).inc()

        now = datetime.now(timezone.utc).isoformat()

        # Publish event (Req 23.4)
        self._publish_event(
            event_type="Agent:EquityPauseActivated",
            aggregate_id=account_id,
            payload={
                "account_id": account_id,
                "peak_equity": hwm,
                "current_equity": equity,
                "drawdown_pct": round(drawdown_pct, 2),
                "threshold_pct": self._max_drawdown_pct,
                "timestamp": now,
            },
        )

        self._broadcast_activity(
            event_type="EquityPauseActivated",
            account_id=account_id,
            drawdown_pct=round(drawdown_pct, 2),
        )

        logger.warning(
            "Equity pause ACTIVATED for account %s: drawdown %.2f%% (threshold %.1f%%)",
            account_id,
            drawdown_pct,
            self._max_drawdown_pct,
        )

    def _deactivate_pause(
        self,
        account_id: str,
        hwm: float,
        equity: float,
        drawdown_pct: float,
    ) -> None:
        """Auto-deactivate equity pause on recovery (Req 23.6)."""
        key = f"{PAUSE_KEY_PREFIX}:{account_id}"
        self._redis.delete(key)
        equity_pause_active.labels(account_id=account_id).set(0)

        now = datetime.now(timezone.utc).isoformat()

        self._publish_event(
            event_type="Agent:EquityPauseDeactivated",
            aggregate_id=account_id,
            payload={
                "account_id": account_id,
                "peak_equity": hwm,
                "current_equity": equity,
                "drawdown_pct": round(drawdown_pct, 2),
                "recovery_pct": self._recovery_pct,
                "timestamp": now,
            },
        )

        self._broadcast_activity(
            event_type="EquityPauseDeactivated",
            account_id=account_id,
            drawdown_pct=round(drawdown_pct, 2),
        )

        logger.info(
            "Equity pause DEACTIVATED for account %s: drawdown recovered to %.2f%%",
            account_id,
            drawdown_pct,
        )

    def _publish_event(
        self, event_type: str, aggregate_id: str, payload: dict
    ) -> None:
        if self._event_publisher is None:
            return
        from src.models.trading_event import TradingEvent

        event = TradingEvent(
            event_type=event_type,
            aggregate_id=aggregate_id,
            sequence_number=0,
            payload=payload,
            source_service="strategy-engine",
        )
        try:
            self._event_publisher.publish(event)
        except Exception as exc:
            logger.warning("Failed to publish %s: %s", event_type, exc)

    def _broadcast_activity(self, event_type: str, **fields: Any) -> None:
        try:
            message = {"type": event_type, **{k: str(v) for k, v in fields.items()}}
            self._redis.publish(ACTIVITY_CHANNEL, json.dumps(message))
        except Exception as exc:
            logger.warning("Failed to broadcast %s: %s", event_type, exc)
