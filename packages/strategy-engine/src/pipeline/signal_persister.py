import logging
import os
from collections import deque
from concurrent.futures import ThreadPoolExecutor

import requests

from ..circuit_breaker import CircuitBreaker, CircuitBreakerState
from ..metrics import on_circuit_breaker_state_change
from ..models.signal import Signal

logger = logging.getLogger(__name__)


class SignalPersister:
    """Sends generated signals to the backend API asynchronously.

    Uses a circuit breaker to protect POST /api/signals calls.
    When the breaker is open, signals are buffered in memory and
    flushed oldest-first on recovery.
    """

    def __init__(self, backend_url: str, max_workers: int = 2) -> None:
        self._backend_url = backend_url
        self._executor = ThreadPoolExecutor(max_workers=max_workers)

        buffer_max = int(os.environ.get("SIGNAL_BUFFER_MAX_SIZE", "500"))
        self._buffer: deque[Signal] = deque(maxlen=buffer_max)
        self._overflow_count: int = 0

        self._cb = CircuitBreaker(
            name="strategy-to-backend-signals",
            on_state_change=self._on_state_change,
        )

    def persist(self, signal: Signal) -> None:
        """Submit async POST /signals request through circuit breaker."""
        self._executor.submit(self._persist_with_cb, signal)

    @property
    def circuit_breaker(self) -> CircuitBreaker:
        """Expose circuit breaker for health reporting."""
        return self._cb

    @property
    def overflow_count(self) -> int:
        """Number of signals dropped due to buffer overflow."""
        return self._overflow_count

    @property
    def buffer_size(self) -> int:
        """Current number of buffered signals."""
        return len(self._buffer)

    def _persist_with_cb(self, signal: Signal) -> None:
        """Execute send through circuit breaker with buffer fallback."""
        def send_fn() -> None:
            self._send(signal)

        def buffer_fn() -> None:
            self._buffer_signal(signal)

        try:
            self._cb.execute(fn=send_fn, fallback=buffer_fn)
        except Exception:
            # _send raised while breaker was closed — buffer as fallback
            self._buffer_signal(signal)

    def _buffer_signal(self, signal: Signal) -> None:
        """Buffer a signal in memory. Track overflow when buffer is full."""
        if len(self._buffer) >= (self._buffer.maxlen or 500):
            self._overflow_count += 1
            logger.warning(
                "Signal buffer full (%d), overflow_count=%d — dropping oldest signal",
                len(self._buffer),
                self._overflow_count,
            )
        self._buffer.append(signal)
        logger.info(
            "Buffered signal %s (buffer_size=%d)", signal.id, len(self._buffer)
        )

    def _on_state_change(self, name: str, old_state: str, new_state: str) -> None:
        """On recovery (transition to Closed): flush buffered signals oldest-first."""
        on_circuit_breaker_state_change(name, old_state, new_state)
        if new_state == CircuitBreakerState.CLOSED.value:
            self._executor.submit(self._flush_buffer)

    def _flush_buffer(self) -> None:
        """Flush buffered signals oldest-first (FIFO)."""
        flushed = 0
        while self._buffer:
            signal = self._buffer.popleft()
            try:
                self._send(signal)
                flushed += 1
            except Exception:
                # Re-buffer the signal and stop flushing — breaker may re-open
                self._buffer.appendleft(signal)
                logger.warning(
                    "Flush interrupted after %d signals, %d remaining",
                    flushed,
                    len(self._buffer),
                )
                return
        if flushed > 0:
            logger.info("Flushed %d buffered signals after recovery", flushed)

    def _send(self, signal: Signal) -> None:
        """POST {backend_url}/api/signals with signal payload. Log errors without raising."""
        url = f"{self._backend_url}/api/signals"
        payload = {
            "instrument": signal.instrument,
            "direction": signal.direction.value,
            "entryPrice": signal.entry_price,
            "stopLoss": signal.stop_loss,
            "takeProfit": signal.take_profit,
            "positionSize": signal.position_size,
            "confidenceScore": signal.confidence_score,
            "timeframe": signal.timeframe.value,
            "orderBlockId": signal.order_block_id,
            "strategyId": signal.strategy_id,
            "mode": signal.mode.value,
            "metadata": signal.metadata.model_dump(),
        }
        resp = requests.post(url, json=payload, timeout=10)
        resp.raise_for_status()

    def shutdown(self) -> None:
        """Shutdown the thread pool executor."""
        self._executor.shutdown(wait=True)
