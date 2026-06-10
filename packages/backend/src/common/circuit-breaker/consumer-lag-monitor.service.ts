import {
  Injectable,
  Inject,
  Logger,
  OnModuleInit,
  OnModuleDestroy,
  Optional,
} from '@nestjs/common';
import Redis from 'ioredis';
import { Gauge, register } from 'prom-client';
import { REDIS_CLIENT } from '../modules/redis.module';
import { TradingGateway } from '../../modules/gateway/trading.gateway';

export interface StreamLagInfo {
  stream: string;
  group: string;
  lag: number;
  lastChecked: string;
}

const MONITORED_STREAMS = [
  'signals:stream',
  'backtest:requests',
  'backtest:results',
];

function getOrCreateLagGauge(): Gauge<'stream' | 'group'> {
  const existing = register.getSingleMetric('redis_stream_consumer_lag');
  if (existing) return existing as Gauge<'stream' | 'group'>;
  return new Gauge({
    name: 'redis_stream_consumer_lag',
    help: 'Consumer lag (pending message count) per Redis stream and consumer group',
    labelNames: ['stream', 'group'] as const,
  });
}

@Injectable()
export class ConsumerLagMonitor implements OnModuleInit, OnModuleDestroy {
  private readonly logger = new Logger(ConsumerLagMonitor.name);
  private readonly pollIntervalMs: number;
  private readonly alertThreshold: number;
  private timer: ReturnType<typeof setInterval> | null = null;
  private lagSnapshots: StreamLagInfo[] = [];

  private readonly lagGauge: Gauge<'stream' | 'group'>;

  constructor(
    @Inject(REDIS_CLIENT) private readonly redis: Redis,
    @Optional() @Inject(TradingGateway) private readonly gateway?: TradingGateway,
  ) {
    this.lagGauge = getOrCreateLagGauge();

    const envPoll = process.env.CONSUMER_LAG_POLL_INTERVAL_MS;
    const envThreshold = process.env.CONSUMER_LAG_ALERT_THRESHOLD;

    this.pollIntervalMs = envPoll ? parseInt(envPoll, 10) : 10000;
    this.alertThreshold = envThreshold ? parseInt(envThreshold, 10) : 50;

    this.logger.log(
      `Configured: pollInterval=${this.pollIntervalMs}ms, alertThreshold=${this.alertThreshold}`,
    );
  }

  onModuleInit(): void {
    this.start();
  }

  onModuleDestroy(): void {
    this.stop();
  }

  start(): void {
    if (this.timer) return;
    this.logger.log('Starting consumer lag polling');
    this.timer = setInterval(() => this.poll(), this.pollIntervalMs);
    // Run an initial poll immediately
    void this.poll();
  }

  stop(): void {
    if (this.timer) {
      clearInterval(this.timer);
      this.timer = null;
      this.logger.log('Stopped consumer lag polling');
    }
  }

  getLagInfo(): StreamLagInfo[] {
    return [...this.lagSnapshots];
  }

  private async poll(): Promise<void> {
    const snapshots: StreamLagInfo[] = [];
    const now = new Date().toISOString();

    for (const stream of MONITORED_STREAMS) {
      try {
        const groups = await this.redis.xinfo('GROUPS', stream) as unknown[][];

        if (!groups || !Array.isArray(groups)) continue;

        for (const groupData of groups) {
          const info = this.parseGroupInfo(groupData);
          if (!info) continue;

          const lag = info.pending;
          this.lagGauge.set({ stream, group: info.name }, lag);

          snapshots.push({
            stream,
            group: info.name,
            lag,
            lastChecked: now,
          });

          if (lag > this.alertThreshold) {
            this.logger.warn(
              `Consumer lag alert: stream=${stream} group=${info.name} lag=${lag} threshold=${this.alertThreshold}`,
            );
            this.emitLagAlert(stream, info.name, lag);
          }
        }
      } catch (error: unknown) {
        // Stream may not exist yet — that's fine, treat as zero lag
        const message = error instanceof Error ? error.message : String(error);
        if (!message.includes('no such key') && !message.includes('ERR no such key')) {
          this.logger.warn(`Failed to poll XINFO GROUPS for ${stream}: ${message}`);
        }
      }
    }

    this.lagSnapshots = snapshots;
  }

  private parseGroupInfo(
    groupData: unknown,
  ): { name: string; pending: number } | null {
    if (!Array.isArray(groupData)) return null;

    let name: string | undefined;
    let pending: number | undefined;

    // XINFO GROUPS returns flat key-value pairs: [key, value, key, value, ...]
    for (let i = 0; i < groupData.length - 1; i += 2) {
      const key = String(groupData[i]);
      const value = groupData[i + 1];
      if (key === 'name') {
        name = String(value);
      } else if (key === 'pel-count') {
        pending = parseInt(String(value), 10);
      }
    }

    if (name !== undefined && pending !== undefined) {
      return { name, pending };
    }
    return null;
  }

  private emitLagAlert(stream: string, group: string, lag: number): void {
    if (!this.gateway) return;

    try {
      this.gateway.server?.to('health').emit('consumerLag:alert', {
        stream,
        group,
        lag,
        threshold: this.alertThreshold,
        timestamp: new Date().toISOString(),
      });
    } catch (error: unknown) {
      const message = error instanceof Error ? error.message : String(error);
      this.logger.warn(`Failed to emit lag alert via WebSocket: ${message}`);
    }
  }
}
