"""Tests for circuit breaker and backpressure Prometheus metrics."""

from src.metrics import (
    circuit_breaker_state_transitions_total,
    signal_backpressure_active,
    on_circuit_breaker_state_change,
    set_backpressure_active,
)


class TestCircuitBreakerStateTransitionsCounter:
    """circuit_breaker_state_transitions_total increments on state change callback."""

    def test_increments_on_state_change(self):
        before = circuit_breaker_state_transitions_total.labels(
            name="test-breaker", to_state="open"
        )._value.get()

        on_circuit_breaker_state_change("test-breaker", "closed", "open")

        after = circuit_breaker_state_transitions_total.labels(
            name="test-breaker", to_state="open"
        )._value.get()

        assert after == before + 1


class TestBackpressureGauge:
    """signal_backpressure_active gauge reflects backpressure state."""

    def test_set_to_1_when_paused(self):
        set_backpressure_active(True)
        assert signal_backpressure_active._value.get() == 1

    def test_set_to_0_when_active(self):
        set_backpressure_active(False)
        assert signal_backpressure_active._value.get() == 0
