import { Injectable } from '@nestjs/common';
import { Counter, Gauge, Histogram } from 'prom-client';

@Injectable()
export class CircuitBreakerMetrics {
  readonly stateTransitionsTotal = new Counter({
    name: 'circuit_breaker_state_transitions_total',
    help: 'Total number of circuit breaker state transitions',
    labelNames: ['name', 'to_state'] as const,
  });

  readonly callsTotal = new Counter({
    name: 'circuit_breaker_calls_total',
    help: 'Total number of calls through circuit breakers',
    labelNames: ['name', 'outcome'] as const,
  });

  readonly state = new Gauge({
    name: 'circuit_breaker_state',
    help: 'Current circuit breaker state (0=closed, 1=open, 2=half_open)',
    labelNames: ['name'] as const,
  });

  readonly recoveryDuration = new Histogram({
    name: 'circuit_breaker_recovery_duration_seconds',
    help: 'Duration from Open state entry to Closed state recovery in seconds',
    labelNames: ['name'] as const,
    buckets: [1, 5, 10, 30, 60, 120, 300],
  });

  onStateTransition(name: string, toState: string): void {
    this.stateTransitionsTotal.inc({ name, to_state: toState });

    const stateValue =
      toState === 'closed' ? 0 : toState === 'open' ? 1 : 2;
    this.state.set({ name }, stateValue);
  }

  onCallComplete(
    name: string,
    outcome: 'success' | 'failure' | 'rejected',
  ): void {
    this.callsTotal.inc({ name, outcome });
  }

  setCurrentState(name: string, toState: string): void {
    const stateValue =
      toState === 'closed' ? 0 : toState === 'open' ? 1 : 2;
    this.state.set({ name }, stateValue);
  }

  recordRecoveryDuration(name: string, durationSeconds: number): void {
    this.recoveryDuration.observe({ name }, durationSeconds);
  }
}
