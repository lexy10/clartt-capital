import { Logger } from '@nestjs/common';

export enum CircuitBreakerState {
  CLOSED = 'closed',
  OPEN = 'open',
  HALF_OPEN = 'half_open',
}

export type OnStateChangeCallback = (
  name: string,
  previousState: CircuitBreakerState,
  newState: CircuitBreakerState,
) => void;

export interface CircuitBreakerOptions {
  name: string;
  failureThreshold?: number;
  recoveryTimeoutMs?: number;
  probeIntervalMs?: number;
  onStateChange?: OnStateChangeCallback;
}

export interface CircuitBreakerStatus {
  name: string;
  state: CircuitBreakerState;
  failureCount: number;
  lastStateChange: string;
  lastSuccessfulContact: string;
}

export class CircuitBreaker {
  private readonly logger: Logger;
  private readonly name: string;
  private readonly failureThreshold: number;
  private readonly recoveryTimeoutMs: number;
  private readonly probeIntervalMs: number;
  private readonly onStateChangeCallback?: OnStateChangeCallback;

  private state: CircuitBreakerState = CircuitBreakerState.CLOSED;
  private failureCount = 0;
  private lastStateChangeTime: number = Date.now();
  private lastSuccessTime: number = Date.now();
  private openedAt = 0;
  private probeInFlight = false;

  constructor(options: CircuitBreakerOptions) {
    this.name = options.name;
    this.logger = new Logger(`CircuitBreaker:${this.name}`);
    this.onStateChangeCallback = options.onStateChange;

    const envThreshold = process.env.CB_FAILURE_THRESHOLD;
    const envRecovery = process.env.CB_RECOVERY_TIMEOUT_MS;
    const envProbe = process.env.CB_PROBE_INTERVAL_MS;

    this.failureThreshold =
      options.failureThreshold ??
      (envThreshold ? parseInt(envThreshold, 10) : 5);
    this.recoveryTimeoutMs =
      options.recoveryTimeoutMs ??
      (envRecovery ? parseInt(envRecovery, 10) : 30000);
    this.probeIntervalMs =
      options.probeIntervalMs ??
      (envProbe ? parseInt(envProbe, 10) : 10000);
  }

  async execute<T>(fn: () => Promise<T>, fallback: () => T): Promise<T> {
    switch (this.state) {
      case CircuitBreakerState.CLOSED:
        return this.executeClosed(fn);

      case CircuitBreakerState.OPEN:
        return this.executeOpen(fn, fallback);

      case CircuitBreakerState.HALF_OPEN:
        return this.executeHalfOpen(fn, fallback);
    }
  }

  getStatus(): CircuitBreakerStatus {
    return {
      name: this.name,
      state: this.state,
      failureCount: this.failureCount,
      lastStateChange: new Date(this.lastStateChangeTime).toISOString(),
      lastSuccessfulContact: new Date(this.lastSuccessTime).toISOString(),
    };
  }

  get currentState(): CircuitBreakerState {
    return this.state;
  }

  private async executeClosed<T>(fn: () => Promise<T>): Promise<T> {
    try {
      const result = await fn();
      this.onSuccess();
      return result;
    } catch (error) {
      this.onFailure();
      throw error;
    }
  }

  private executeOpen<T>(
    fn: () => Promise<T>,
    fallback: () => T,
  ): Promise<T> | T {
    const now = Date.now();
    if (now - this.openedAt >= this.recoveryTimeoutMs) {
      this.transitionTo(CircuitBreakerState.HALF_OPEN);
      return this.executeHalfOpen(fn, fallback);
    }
    return fallback();
  }

  private async executeHalfOpen<T>(
    fn: () => Promise<T>,
    fallback: () => T,
  ): Promise<T> {
    if (this.probeInFlight) {
      return fallback();
    }

    this.probeInFlight = true;
    try {
      const result = await fn();
      this.probeInFlight = false;
      this.onProbeSuccess();
      return result;
    } catch {
      this.probeInFlight = false;
      this.onProbeFailure();
      return fallback();
    }
  }

  private onSuccess(): void {
    this.failureCount = 0;
    this.lastSuccessTime = Date.now();
  }

  private onFailure(): void {
    this.failureCount++;
    if (this.failureCount >= this.failureThreshold) {
      this.transitionTo(CircuitBreakerState.OPEN);
    }
  }

  private onProbeSuccess(): void {
    this.failureCount = 0;
    this.lastSuccessTime = Date.now();
    this.transitionTo(CircuitBreakerState.CLOSED);
  }

  private onProbeFailure(): void {
    this.transitionTo(CircuitBreakerState.OPEN);
  }

  private transitionTo(newState: CircuitBreakerState): void {
    const previousState = this.state;
    if (previousState === newState) return;

    this.state = newState;
    this.lastStateChangeTime = Date.now();

    if (newState === CircuitBreakerState.OPEN) {
      this.openedAt = Date.now();
    }

    this.logger.warn(
      `State transition: ${previousState} → ${newState} [breaker=${this.name}]`,
    );

    if (this.onStateChangeCallback) {
      try {
        this.onStateChangeCallback(this.name, previousState, newState);
      } catch (error) {
        this.logger.warn(
          `State change callback error: ${error instanceof Error ? error.message : String(error)}`,
        );
      }
    }
  }
}
