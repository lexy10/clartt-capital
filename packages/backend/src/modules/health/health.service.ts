import { Injectable, Inject, Logger } from '@nestjs/common';
import { HttpService } from '@nestjs/axios';
import Redis from 'ioredis';
import { firstValueFrom } from 'rxjs';
import { REDIS_CLIENT } from '../../common/modules/redis.module';
import { CircuitBreaker, CircuitBreakerState } from '../../common/circuit-breaker/circuit-breaker';
import { EXECUTION_ENGINE_CIRCUIT_BREAKER } from '../../common/circuit-breaker/circuit-breaker.module';
import { ConsumerLagMonitor } from '../../common/circuit-breaker/consumer-lag-monitor.service';

export interface DependencyHealth {
  name: string;
  status: 'healthy' | 'degraded' | 'unhealthy';
  circuitBreakerState?: 'closed' | 'open' | 'half_open';
  lastSuccessfulContact: string | null;
}

export interface HealthResponse {
  service: string;
  status: 'healthy' | 'degraded' | 'unhealthy';
  timestamp: string;
  dependencies: DependencyHealth[];
}

export interface CircuitBreakerInfo {
  name: string;
  service: string;
  state: string;
  failureCount: number;
  lastStateChange: string;
  protectedDependency: string;
}

export interface CircuitBreakerAggregateResponse {
  circuitBreakers: CircuitBreakerInfo[];
  remoteBreakers: CircuitBreakerInfo[];
}

@Injectable()
export class HealthService {
  private readonly logger = new Logger(HealthService.name);

  private readonly strategyEngineUrl: string;
  private readonly executionEngineUrl: string;

  /** Cached remote breaker states with TTL */
  private remoteBreakersCache: CircuitBreakerInfo[] = [];
  private remoteBreakersCacheTime = 0;
  private readonly remoteBreakersCacheTtlMs = 15_000;

  /** Track last successful Redis/PostgreSQL operation timestamps */
  private lastRedisSuccess: number = Date.now();
  private lastPostgresSuccess: number = Date.now();
  private redisCheckInterval: ReturnType<typeof setInterval> | null = null;

  constructor(
    @Inject(EXECUTION_ENGINE_CIRCUIT_BREAKER)
    private readonly executionEngineBreaker: CircuitBreaker,
    private readonly consumerLagMonitor: ConsumerLagMonitor,
    @Inject(REDIS_CLIENT) private readonly redis: Redis,
    private readonly httpService: HttpService,
  ) {
    this.strategyEngineUrl =
      process.env.STRATEGY_ENGINE_URL || 'http://strategy-engine:8003';
    this.executionEngineUrl =
      process.env.EXECUTION_ENGINE_URL || 'http://execution-engine:8002';

    this.startRedisHealthTracking();
  }

  private startRedisHealthTracking(): void {
    // Ping Redis periodically to track last successful contact
    this.redisCheckInterval = setInterval(async () => {
      try {
        await this.redis.ping();
        this.lastRedisSuccess = Date.now();
      } catch {
        // Redis is down — lastRedisSuccess stays stale
      }
    }, 10_000);
  }

  stopRedisHealthTracking(): void {
    if (this.redisCheckInterval) {
      clearInterval(this.redisCheckInterval);
      this.redisCheckInterval = null;
    }
  }

  /** Mark PostgreSQL as healthy (called externally or via interceptor) */
  markPostgresSuccess(): void {
    this.lastPostgresSuccess = Date.now();
  }

  /** Mark Redis as healthy */
  markRedisSuccess(): void {
    this.lastRedisSuccess = Date.now();
  }

  getHealth(): HealthResponse {
    const dependencies = this.getDependencyHealthList();
    const overallStatus = this.aggregateStatus(dependencies);

    return {
      service: 'backend',
      status: overallStatus,
      timestamp: new Date().toISOString(),
      dependencies,
    };
  }

  async getCircuitBreakers(): Promise<CircuitBreakerAggregateResponse> {
    const localBreakers = this.getLocalBreakers();
    const remoteBreakers = await this.fetchRemoteBreakers();

    return {
      circuitBreakers: localBreakers,
      remoteBreakers,
    };
  }

  getDependencyHealthList(): DependencyHealth[] {
    return [
      this.getPostgresHealth(),
      this.getRedisHealth(),
      this.getExecutionEngineHealth(),
    ];
  }

  aggregateStatus(
    dependencies: DependencyHealth[],
  ): 'healthy' | 'degraded' | 'unhealthy' {
    const criticalDeps = ['postgresql', 'execution-engine'];
    const hasCriticalUnhealthy = dependencies.some(
      (d) => criticalDeps.includes(d.name) && d.status === 'unhealthy',
    );
    if (hasCriticalUnhealthy) return 'unhealthy';

    const allHealthy = dependencies.every((d) => d.status === 'healthy');
    if (allHealthy) return 'healthy';

    return 'degraded';
  }

  private getPostgresHealth(): DependencyHealth {
    const ageMs = Date.now() - this.lastPostgresSuccess;
    return {
      name: 'postgresql',
      status: this.deriveTimestampStatus(ageMs),
      lastSuccessfulContact: new Date(this.lastPostgresSuccess).toISOString(),
    };
  }

  private getRedisHealth(): DependencyHealth {
    const ageMs = Date.now() - this.lastRedisSuccess;
    return {
      name: 'redis',
      status: this.deriveTimestampStatus(ageMs),
      lastSuccessfulContact: new Date(this.lastRedisSuccess).toISOString(),
    };
  }

  private getExecutionEngineHealth(): DependencyHealth {
    const status = this.executionEngineBreaker.getStatus();
    return {
      name: 'execution-engine',
      status: this.deriveBreakerStatus(status.state),
      circuitBreakerState: status.state,
      lastSuccessfulContact: status.lastSuccessfulContact,
    };
  }

  private deriveBreakerStatus(
    state: CircuitBreakerState,
  ): 'healthy' | 'degraded' | 'unhealthy' {
    switch (state) {
      case CircuitBreakerState.CLOSED:
        return 'healthy';
      case CircuitBreakerState.HALF_OPEN:
        return 'degraded';
      case CircuitBreakerState.OPEN:
        return 'unhealthy';
    }
  }

  private deriveTimestampStatus(
    ageMs: number,
  ): 'healthy' | 'degraded' | 'unhealthy' {
    if (ageMs < 30_000) return 'healthy';
    if (ageMs < 60_000) return 'degraded';
    return 'unhealthy';
  }

  private getLocalBreakers(): CircuitBreakerInfo[] {
    const status = this.executionEngineBreaker.getStatus();
    return [
      {
        name: status.name,
        service: 'backend',
        state: status.state,
        failureCount: status.failureCount,
        lastStateChange: status.lastStateChange,
        protectedDependency: 'execution-engine',
      },
    ];
  }

  private async fetchRemoteBreakers(): Promise<CircuitBreakerInfo[]> {
    const now = Date.now();
    if (
      now - this.remoteBreakersCacheTime < this.remoteBreakersCacheTtlMs &&
      this.remoteBreakersCache.length > 0
    ) {
      return this.remoteBreakersCache;
    }

    const results: CircuitBreakerInfo[] = [];

    // Fetch from Strategy Engine
    const strategyBreakers = await this.fetchServiceHealth(
      this.strategyEngineUrl,
      'strategy-engine',
    );
    results.push(...strategyBreakers);

    // Fetch from Execution Engine
    const executionBreakers = await this.fetchServiceHealth(
      this.executionEngineUrl,
      'execution-engine',
    );
    results.push(...executionBreakers);

    this.remoteBreakersCache = results;
    this.remoteBreakersCacheTime = now;

    return results;
  }

  async fetchServiceHealthProxy(serviceName: string): Promise<any> {
    const baseUrl = serviceName === 'strategy-engine'
      ? this.strategyEngineUrl
      : this.executionEngineUrl;
    try {
      const response = await firstValueFrom(
        this.httpService.get(`${baseUrl}/health`, { timeout: 5000 }),
      );
      return response.data;
    } catch (error) {
      this.logger.warn(
        `Failed to proxy health for ${serviceName}: ${error instanceof Error ? error.message : String(error)}`,
      );
      return {
        service: serviceName,
        status: 'unhealthy',
        timestamp: new Date().toISOString(),
        dependencies: [],
      };
    }
  }

  private async fetchServiceHealth(
    baseUrl: string,
    serviceName: string,
  ): Promise<CircuitBreakerInfo[]> {
    try {
      const response = await firstValueFrom(
        this.httpService.get(`${baseUrl}/health`, { timeout: 3000 }),
      );
      const data = response.data;
      if (!data?.dependencies || !Array.isArray(data.dependencies)) {
        return [];
      }

      return data.dependencies
        .filter((dep: any) => dep.circuitBreakerState)
        .map((dep: any) => ({
          name: `${serviceName}-to-${dep.name}`,
          service: serviceName,
          state: dep.circuitBreakerState,
          failureCount: 0,
          lastStateChange: dep.lastSuccessfulContact || new Date().toISOString(),
          protectedDependency: dep.name,
        }));
    } catch (error) {
      this.logger.warn(
        `Failed to fetch health from ${serviceName}: ${error instanceof Error ? error.message : String(error)}`,
      );
      // Return cached data if available (already handled by caller)
      return [];
    }
  }
}
