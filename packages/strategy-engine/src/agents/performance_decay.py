"""Performance Decay Detector — rolling performance monitoring for live strategies.

Detects when a live strategy's metrics deviate significantly from its
backtest baseline and flags or auto-disables the strategy.

Requirements: 24.1–24.9
"""

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Optional, Protocol

import requests
from prometheus_client import Counter, Gauge
from redis import Redis

from src.agents.models import AgentTask, TaskPriority

logger = logging.getLogger("strategy_engine.agents.performance_decay")

ACTIVITY_CHANNEL = "agents:activity"

# Prometheus metrics (Req 24.11)
strategy_decay_detections_total = Counter(
    "strategy_decay_detections_total",
    "Total decay detections",
    labelnames=["strategy_id", "metric"],
)
strategy_decay_auto_disables_total = Counter(
    "strategy_decay_auto_disables_total",
    "Total strategies auto-disabled due to decay",
    labelnames=["strategy_id"],
)
strategy_rolling_win_rate = Gauge(
    "strategy_rolling_win_rate",
    "Rolling win rate for a strategy",
    labelnames=["strategy_id"],
)
strategy_rolling_profit_factor = Gauge(
    "strategy_rolling_profit_factor",
    "Rolling profit factor for a strategy",
    labelnames=["strategy_id"],
)


class EventPublisherProtocol(Protocol):
    def publish(self, event: Any) -> None: ...


class AutonomyManagerProtocol(Protocol):
    def get_mode(self) -> Any: ...


class PerformanceDecayDetector:
    """Detects performance decay in live strategies.

    Constructor:
        redis_client:       Redis connection
        event_publisher:    EventPublisher for decay events
        backend_url:        Backend REST API base URL
        autonomy_manager:   AutonomyManager for checking autonomy mode
        task_queue:         TaskQueue for submitting refinement tasks
        approval_manager:   ApprovalGateManager for requesting human approval
    """

    def __init__(
        self,
        redis_client: Redis,
        event_publisher: Optional[EventPublisherProtocol] = None,
        backend_url: str = "http://backend:3000",
        autonomy_manager: Optional[AutonomyManagerProtocol] = None,
        task_queue: Any = None,
        approval_manager: Any = None,
    ) -> None:
        self._redis = redis_client
        self._event_publisher = event_publisher
        self._backend_url = backend_url.rstrip("/")
        self._autonomy_manager = autonomy_manager

        # Refinement support — wired in bootstrap.py
        self._task_queue = task_queue
        self._approval_manager = approval_manager

        # Configuration from env vars (Req 24.10)
        self._check_interval_minutes = int(
            os.environ.get("DECAY_CHECK_INTERVAL_MINUTES", "60")
        )
        self._lookback_trades = int(
            os.environ.get("DECAY_LOOKBACK_TRADES", "50")
        )
        self._win_rate_decay_threshold_pct = float(
            os.environ.get("WIN_RATE_DECAY_THRESHOLD_PCT", "15.0")
        )
        self._min_profit_factor = float(
            os.environ.get("MIN_PROFIT_FACTOR", "0.8")
        )
        self._decay_action = os.environ.get("DECAY_ACTION", "flag")

    def check_all(self) -> list[dict]:
        """Check all active live strategies for performance decay.

        Returns a list of assessment dicts for strategies that are decaying.
        """
        strategies = self._fetch_active_strategies()
        results: list[dict] = []

        for strategy in strategies:
            strategy_id = strategy.get("id", "")
            assessment = self.check_strategy(strategy_id, strategy)
            if assessment and assessment.get("decaying"):
                results.append(assessment)

        return results

    def check_strategy(
        self, strategy_id: str, strategy_info: Optional[dict] = None
    ) -> Optional[dict]:
        """Check a single strategy for performance decay.

        Returns an assessment dict or None if insufficient data.
        """
        # Fetch recent trades (Req 24.2)
        trades = self._fetch_recent_trades(strategy_id)

        # Skip if insufficient data (Req 24.9)
        if len(trades) < self._lookback_trades:
            return {
                "strategy_id": strategy_id,
                "decaying": False,
                "reason": f"insufficient trades ({len(trades)}/{self._lookback_trades})",
            }

        # Compute rolling metrics
        recent = trades[: self._lookback_trades]
        rolling_wr = self._compute_win_rate(recent)
        rolling_pf = self._compute_profit_factor(recent)

        strategy_rolling_win_rate.labels(strategy_id=strategy_id).set(rolling_wr)
        strategy_rolling_profit_factor.labels(strategy_id=strategy_id).set(rolling_pf)

        # Fetch backtest baseline (Req 24.3)
        baseline = self._fetch_backtest_baseline(strategy_id)
        baseline_wr = baseline.get("win_rate", 0.0) if baseline else 0.0

        decay_reasons: list[str] = []

        # Check win rate decay (Req 24.4)
        if baseline_wr > 0:
            wr_drop = (baseline_wr - rolling_wr) * 100  # percentage points
            if wr_drop > self._win_rate_decay_threshold_pct:
                decay_reasons.append(
                    f"win_rate dropped {wr_drop:.1f}pp "
                    f"(rolling={rolling_wr:.2%} vs baseline={baseline_wr:.2%})"
                )
                strategy_decay_detections_total.labels(
                    strategy_id=strategy_id, metric="win_rate"
                ).inc()

        # Check profit factor (Req 24.5)
        if rolling_pf < self._min_profit_factor:
            decay_reasons.append(
                f"profit_factor {rolling_pf:.2f} below minimum {self._min_profit_factor}"
            )
            strategy_decay_detections_total.labels(
                strategy_id=strategy_id, metric="profit_factor"
            ).inc()

        if not decay_reasons:
            return {
                "strategy_id": strategy_id,
                "decaying": False,
                "rolling_win_rate": rolling_wr,
                "rolling_profit_factor": rolling_pf,
                "baseline_win_rate": baseline_wr,
            }

        # Strategy is decaying — publish event (Req 24.6)
        assessment = {
            "strategy_id": strategy_id,
            "decaying": True,
            "reasons": decay_reasons,
            "rolling_win_rate": rolling_wr,
            "rolling_profit_factor": rolling_pf,
            "baseline_win_rate": baseline_wr,
            "action": self._decay_action,
        }

        self._publish_decay_event(strategy_id, assessment)

        # Execute action (Req 24.7, 24.8, 4.1–4.7, 6.1, 6.2)
        if self._decay_action == "refine":
            asyncio.ensure_future(self._handle_refine(strategy_id, assessment))
        elif self._decay_action == "refine_config":
            asyncio.ensure_future(self._handle_refine_config(strategy_id, assessment))
        elif self._decay_action == "disable":
            self._handle_disable(strategy_id, assessment)

        return assessment

    # ── Action Handlers ───────────────────────────────────────────────

    def _handle_disable(self, strategy_id: str, assessment: dict) -> None:
        """Disable a decaying strategy, respecting autonomy mode (Req 24.7, 24.8)."""
        from src.agents.autonomy import AutonomyMode

        mode = AutonomyMode.APPROVAL
        if self._autonomy_manager is not None:
            mode = self._autonomy_manager.get_mode()

        if mode == AutonomyMode.FULL_AUTONOMY:
            # Disable immediately (Req 24.8)
            self._disable_strategy(strategy_id)
            strategy_decay_auto_disables_total.labels(
                strategy_id=strategy_id
            ).inc()
            logger.warning(
                "Strategy '%s' auto-disabled due to decay (full_autonomy mode)",
                strategy_id,
            )
        else:
            # In approval mode, just flag — approval gate would be triggered
            # by the agent framework if an agent is handling this
            logger.info(
                "Strategy '%s' flagged for decay (approval mode — manual action required)",
                strategy_id,
            )

    async def _handle_refine(self, strategy_id: str, assessment: dict) -> None:
        """Trigger strategy refinement, respecting autonomy mode (Req 4.1–4.7)."""
        from src.agents.autonomy import AutonomyMode

        algorithm_name = self._resolve_algorithm_name(strategy_id)

        # Duplicate check: skip if refinement already queued/in-progress (Req 4.6)
        if self._is_refinement_in_progress(algorithm_name):
            logger.warning(
                "Refinement already in progress for '%s', skipping",
                algorithm_name,
            )
            return

        # Autonomy mode check (Req 4.3, 4.4)
        if (
            self._autonomy_manager
            and self._autonomy_manager.get_mode() == AutonomyMode.APPROVAL
        ):
            if self._approval_manager is not None:
                await self._approval_manager.request_approval(
                    agent_name="converter",
                    task_id="",
                    action_description=(
                        f"Refine strategy '{algorithm_name}': "
                        f"{assessment.get('reasons', [])}"
                    ),
                )
            # Task submission deferred until approval granted
            return

        # Fetch current config for refinement context (Req 5.1, 5.2)
        current_config = {}
        try:
            resp = requests.get(
                f"{self._backend_url}/api/strategies/{strategy_id}",
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            current_config = data.get("config", {})
        except Exception as exc:
            logger.warning(
                "Failed to fetch strategy config for '%s': %s (proceeding without)",
                strategy_id,
                exc,
            )

        # Build performance_data with optional config context (Req 5.1, 5.2)
        performance_data = {
            "rolling_win_rate": assessment.get("rolling_win_rate"),
            "rolling_profit_factor": assessment.get("rolling_profit_factor"),
            "baseline_win_rate": assessment.get("baseline_win_rate"),
        }
        if current_config:
            performance_data["current_config"] = current_config

        # Submit refinement task (Req 4.1, 4.2)
        if self._task_queue is not None:
            task = AgentTask(
                type="refine_strategy",
                agent_name="converter",
                priority=TaskPriority.NORMAL,
                payload={
                    "algorithm_name": algorithm_name,
                    "strategy_id": strategy_id,
                    "refinement_hints": assessment.get("reasons", []),
                    "performance_data": performance_data,
                },
            )
            await self._task_queue.submit(task)

        # Publish trigger event (Req 4.7)
        self._publish_refinement_triggered_event(
            strategy_id, algorithm_name, assessment
        )

    async def _handle_refine_config(self, strategy_id: str, assessment: dict) -> None:
        """Trigger config-only refinement (Req 6.1, 6.2)."""
        from src.agents.autonomy import AutonomyMode

        algorithm_name = self._resolve_algorithm_name(strategy_id)

        # Duplicate check: skip if refinement already queued/in-progress
        if self._is_refinement_in_progress(algorithm_name):
            logger.warning(
                "Config refinement already in progress for '%s'",
                algorithm_name,
            )
            return

        # Autonomy mode check
        if (
            self._autonomy_manager
            and self._autonomy_manager.get_mode() == AutonomyMode.APPROVAL
        ):
            if self._approval_manager is not None:
                await self._approval_manager.request_approval(
                    agent_name="converter",
                    task_id="",
                    action_description=(
                        f"Refine config for strategy '{algorithm_name}': "
                        f"{assessment.get('reasons', [])}"
                    ),
                )
            return

        # Submit config-only refinement task (Req 6.1, 6.2)
        if self._task_queue is not None:
            task = AgentTask(
                type="refine_strategy_config",
                agent_name="converter",
                priority=TaskPriority.NORMAL,
                payload={
                    "strategy_id": strategy_id,
                    "refinement_hints": assessment.get("reasons", []),
                    "performance_data": {
                        "rolling_win_rate": assessment.get("rolling_win_rate"),
                        "rolling_profit_factor": assessment.get("rolling_profit_factor"),
                        "baseline_win_rate": assessment.get("baseline_win_rate"),
                    },
                },
            )
            await self._task_queue.submit(task)

        # Publish trigger event
        self._publish_config_refinement_triggered_event(
            strategy_id, algorithm_name, assessment
        )

    def _resolve_algorithm_name(self, strategy_id: str) -> str:
        """Resolve the algorithm name for a strategy from the backend."""
        try:
            resp = requests.get(
                f"{self._backend_url}/api/strategies/{strategy_id}",
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get("algorithm", strategy_id)
        except Exception as exc:
            logger.warning(
                "Failed to resolve algorithm name for '%s': %s",
                strategy_id,
                exc,
            )
            return strategy_id

    def _is_refinement_in_progress(self, algorithm_name: str) -> bool:
        """Check Redis for an in-progress refinement lock (Req 4.6)."""
        key = f"agents:refining:{algorithm_name}"
        return bool(self._redis.exists(key))

    def _publish_refinement_triggered_event(
        self,
        strategy_id: str,
        algorithm_name: str,
        assessment: dict,
    ) -> None:
        """Publish StrategyRefinementTriggered event (Req 4.7)."""
        if self._event_publisher is None:
            return
        from src.agents.autonomy import AutonomyMode
        from src.models.trading_event import TradingEvent

        autonomy_mode = "approval"
        if self._autonomy_manager is not None:
            autonomy_mode = self._autonomy_manager.get_mode().value

        event = TradingEvent(
            event_type="Agent:StrategyRefinementTriggered",
            aggregate_id=strategy_id,
            sequence_number=0,
            payload={
                "strategy_id": strategy_id,
                "algorithm_name": algorithm_name,
                "decay_reasons": assessment.get("reasons", []),
                "autonomy_mode": autonomy_mode,
            },
            source_service="strategy-engine",
        )
        try:
            self._event_publisher.publish(event)
        except Exception as exc:
            logger.warning(
                "Failed to publish refinement triggered event: %s", exc
            )

        # Broadcast to activity channel
        try:
            message = {
                "type": "StrategyRefinementTriggered",
                "strategy_id": strategy_id,
                "algorithm_name": algorithm_name,
            }
            self._redis.publish(ACTIVITY_CHANNEL, json.dumps(message))
        except Exception as exc:
            logger.warning(
                "Failed to broadcast refinement triggered activity: %s", exc
            )

    def _publish_config_refinement_triggered_event(
        self,
        strategy_id: str,
        algorithm_name: str,
        assessment: dict,
    ) -> None:
        """Publish ConfigRefinementTriggered event (Req 6.1, 6.2)."""
        if self._event_publisher is None:
            return
        from src.agents.autonomy import AutonomyMode
        from src.models.trading_event import TradingEvent

        autonomy_mode = "approval"
        if self._autonomy_manager is not None:
            autonomy_mode = self._autonomy_manager.get_mode().value

        event = TradingEvent(
            event_type="Agent:ConfigRefinementTriggered",
            aggregate_id=strategy_id,
            sequence_number=0,
            payload={
                "strategy_id": strategy_id,
                "algorithm_name": algorithm_name,
                "decay_reasons": assessment.get("reasons", []),
                "autonomy_mode": autonomy_mode,
            },
            source_service="strategy-engine",
        )
        try:
            self._event_publisher.publish(event)
        except Exception as exc:
            logger.warning(
                "Failed to publish config refinement triggered event: %s", exc
            )

        # Broadcast to activity channel
        try:
            message = {
                "type": "ConfigRefinementTriggered",
                "strategy_id": strategy_id,
                "algorithm_name": algorithm_name,
            }
            self._redis.publish(ACTIVITY_CHANNEL, json.dumps(message))
        except Exception as exc:
            logger.warning(
                "Failed to broadcast config refinement triggered activity: %s", exc
            )

    def _disable_strategy(self, strategy_id: str) -> None:
        """Disable a strategy via the backend API."""
        try:
            resp = requests.put(
                f"{self._backend_url}/api/strategies/{strategy_id}",
                json={"enabled": False},
                timeout=10,
            )
            resp.raise_for_status()
        except Exception as exc:
            logger.error(
                "Failed to disable strategy '%s': %s", strategy_id, exc
            )

    # ── Metric Computation ────────────────────────────────────────────

    @staticmethod
    def _compute_win_rate(trades: list[dict]) -> float:
        """Compute win rate from a list of trade dicts."""
        if not trades:
            return 0.0
        wins = sum(
            1 for t in trades if float(t.get("profit_loss", 0)) > 0
        )
        return wins / len(trades)

    @staticmethod
    def _compute_profit_factor(trades: list[dict]) -> float:
        """Compute profit factor (gross profit / gross loss)."""
        gross_profit = sum(
            float(t.get("profit_loss", 0))
            for t in trades
            if float(t.get("profit_loss", 0)) > 0
        )
        gross_loss = abs(
            sum(
                float(t.get("profit_loss", 0))
                for t in trades
                if float(t.get("profit_loss", 0)) < 0
            )
        )
        if gross_loss == 0:
            return float("inf") if gross_profit > 0 else 0.0
        return gross_profit / gross_loss

    # ── Data Fetching ─────────────────────────────────────────────────

    def _fetch_active_strategies(self) -> list[dict]:
        """Fetch active live strategies from the backend."""
        try:
            resp = requests.get(
                f"{self._backend_url}/api/strategies",
                params={"mode": "live", "enabled": "true"},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            return data if isinstance(data, list) else data.get("strategies", [])
        except Exception as exc:
            logger.warning("Failed to fetch active strategies: %s", exc)
            return []

    def _fetch_recent_trades(self, strategy_id: str) -> list[dict]:
        """Fetch recent trades for a strategy, ordered by most recent first."""
        try:
            resp = requests.get(
                f"{self._backend_url}/api/trades",
                params={
                    "strategy_id": strategy_id,
                    "limit": self._lookback_trades,
                    "sort": "desc",
                },
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            return data if isinstance(data, list) else data.get("trades", [])
        except Exception as exc:
            logger.warning(
                "Failed to fetch trades for strategy '%s': %s",
                strategy_id,
                exc,
            )
            return []

    def _fetch_backtest_baseline(self, strategy_id: str) -> Optional[dict]:
        """Fetch the most recent backtest result for a strategy (Req 24.3)."""
        try:
            resp = requests.get(
                f"{self._backend_url}/api/strategies/{strategy_id}/backtest",
                params={"limit": 1, "sort": "desc"},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            if isinstance(data, list) and data:
                return data[0]
            if isinstance(data, dict):
                return data
            return None
        except Exception as exc:
            logger.warning(
                "Failed to fetch backtest baseline for '%s': %s",
                strategy_id,
                exc,
            )
            return None

    # ── Event Publishing ──────────────────────────────────────────────

    def _publish_decay_event(self, strategy_id: str, assessment: dict) -> None:
        """Publish StrategyPerformanceDecay event (Req 24.6)."""
        if self._event_publisher is None:
            return
        from src.models.trading_event import TradingEvent

        event = TradingEvent(
            event_type="Agent:StrategyPerformanceDecay",
            aggregate_id=strategy_id,
            sequence_number=0,
            payload={
                "strategy_id": strategy_id,
                "reasons": assessment.get("reasons", []),
                "rolling_win_rate": assessment.get("rolling_win_rate", 0),
                "rolling_profit_factor": assessment.get("rolling_profit_factor", 0),
                "baseline_win_rate": assessment.get("baseline_win_rate", 0),
                "action": assessment.get("action", "flag"),
            },
            source_service="strategy-engine",
        )
        try:
            self._event_publisher.publish(event)
        except Exception as exc:
            logger.warning("Failed to publish decay event: %s", exc)

        # Broadcast to activity channel
        try:
            message = {
                "type": "StrategyPerformanceDecay",
                "strategy_id": strategy_id,
                "action": assessment.get("action", "flag"),
            }
            self._redis.publish(ACTIVITY_CHANNEL, json.dumps(message))
        except Exception as exc:
            logger.warning("Failed to broadcast decay activity: %s", exc)
