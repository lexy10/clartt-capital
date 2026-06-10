"""WorkerSupervisor — manages AccountWorker threads with crash recovery.

Maintains a dict of account_id → (AccountWorker, Thread). Provides methods
to start, stop, and restart individual workers. Crashed workers can be
restarted without affecting others.
"""

import logging
import threading
from typing import Callable, Optional

from src.models import TradingAccount

from .account_worker import AccountWorker

logger = logging.getLogger(__name__)


class WorkerSupervisor:
    """Manages a pool of AccountWorker threads with per-account isolation.

    Each worker runs in its own daemon thread. The supervisor tracks worker
    state and provides restart capability for crashed workers.
    """

    def __init__(
        self,
        worker_factory: Callable[[TradingAccount], AccountWorker],
    ) -> None:
        """
        Args:
            worker_factory: A callable that creates an AccountWorker for a given
                TradingAccount. This allows the supervisor to be decoupled from
                the specific dependencies (Redis, RiskManager, etc.).
        """
        self._worker_factory = worker_factory
        self._workers: dict[str, AccountWorker] = {}
        self._threads: dict[str, threading.Thread] = {}
        self._accounts: dict[str, TradingAccount] = {}
        self._lock = threading.Lock()

    @property
    def active_workers(self) -> list[str]:
        """Return list of account IDs with running workers."""
        with self._lock:
            return [
                aid for aid, worker in self._workers.items() if worker.is_running
            ]

    @property
    def all_workers(self) -> list[str]:
        """Return list of all managed account IDs."""
        with self._lock:
            return list(self._workers.keys())

    def start_worker(self, account: TradingAccount) -> None:
        """Create and start a worker thread for the given account.

        If a worker already exists for this account, it is stopped first.
        """
        with self._lock:
            account_id = account.id

            # Stop existing worker if present
            if account_id in self._workers:
                logger.info("Stopping existing worker for account %s before restart", account_id)
                self._stop_worker_unlocked(account_id)

            worker = self._worker_factory(account)
            thread = threading.Thread(
                target=self._run_worker,
                args=(worker,),
                daemon=True,
                name=f"worker-{account_id}",
            )

            self._workers[account_id] = worker
            self._threads[account_id] = thread
            self._accounts[account_id] = account

            thread.start()
            logger.info("Started worker thread for account %s", account_id)

    def _run_worker(self, worker: AccountWorker) -> None:
        """Wrapper that runs the worker and logs crashes."""
        try:
            worker.run()
        except Exception:
            logger.exception(
                "[account:%s] Worker crashed unexpectedly", worker.account_id
            )

    def stop_worker(self, account_id: str) -> None:
        """Stop a worker for the given account ID."""
        with self._lock:
            self._stop_worker_unlocked(account_id)

    def _stop_worker_unlocked(self, account_id: str) -> None:
        """Internal stop — must be called with self._lock held."""
        worker = self._workers.get(account_id)
        thread = self._threads.get(account_id)

        if worker is None:
            logger.warning("No worker found for account %s", account_id)
            return

        worker.stop()

        if thread and thread.is_alive():
            thread.join(timeout=5.0)
            if thread.is_alive():
                logger.warning(
                    "Worker thread for account %s did not stop within timeout",
                    account_id,
                )

        del self._workers[account_id]
        del self._threads[account_id]
        logger.info("Stopped worker for account %s", account_id)

    def restart_worker(self, account_id: str) -> None:
        """Restart a crashed or stopped worker without affecting others."""
        with self._lock:
            account = self._accounts.get(account_id)
            if account is None:
                logger.error("Cannot restart worker — unknown account %s", account_id)
                return

        logger.info("Restarting worker for account %s", account_id)
        self.start_worker(account)

    def stop_all(self) -> None:
        """Stop all managed workers."""
        with self._lock:
            account_ids = list(self._workers.keys())

        logger.info("Stopping all workers (%d total)", len(account_ids))
        for account_id in account_ids:
            self.stop_worker(account_id)

        logger.info("All workers stopped")

    def is_worker_alive(self, account_id: str) -> bool:
        """Check if a worker's thread is still alive."""
        with self._lock:
            thread = self._threads.get(account_id)
            return thread is not None and thread.is_alive()
