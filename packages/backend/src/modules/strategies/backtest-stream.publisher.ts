import { Injectable, Inject, Logger, HttpException, HttpStatus } from '@nestjs/common';
import Redis from 'ioredis';
import { REDIS_CLIENT } from '../../common/modules/redis.module';

export interface BacktestRequestMessage {
  result_id: string;
  strategy_id: string;
  strategy_config: Record<string, unknown>;
  instrument: string;
  timeframe: string;
  params: Record<string, unknown>;
  start_date: string;
  end_date: string;
  instrument_specs?: {
    contract_size: number;
    pip_size: number;
    pip_value: number;
    min_lot: number;
    lot_step: number;
    leverage: number;
  };
}

@Injectable()
export class BacktestStreamPublisher {
  private readonly logger = new Logger(BacktestStreamPublisher.name);
  private readonly highWaterMark: number;

  constructor(@Inject(REDIS_CLIENT) private readonly redis: Redis) {
    this.highWaterMark = parseInt(
      process.env.BACKTEST_REQUEST_HIGH_WATER_MARK ?? '10',
      10,
    );
    this.logger.log(
      `Backtest request high-water mark: ${this.highWaterMark}`,
    );
  }

  async getConsumerLag(): Promise<number> {
    try {
      const groups = await this.redis.xinfo(
        'GROUPS',
        'backtest:requests',
      ) as unknown[][];

      for (const group of groups) {
        // XINFO GROUPS returns arrays of [field, value, field, value, ...]
        const fields = group as unknown[];
        const nameIdx = fields.indexOf('name');
        if (nameIdx !== -1 && fields[nameIdx + 1] === 'strategy-engine') {
          const pelIdx = fields.indexOf('pel-count');
          if (pelIdx !== -1) {
            return Number(fields[pelIdx + 1]);
          }
        }
      }

      // Consumer group not found — treat as lag 0 (group will be auto-created on first read)
      return 0;
    } catch {
      // Fail-open: if XINFO fails (e.g. stream doesn't exist yet), allow publishing
      this.logger.warn(
        'Failed to check consumer lag on backtest:requests, allowing publish (fail-open)',
      );
      return 0;
    }
  }

  async publishRequest(message: BacktestRequestMessage): Promise<string> {
    const lag = await this.getConsumerLag();

    if (lag > this.highWaterMark) {
      this.logger.warn(
        `Backtest queue backpressure: consumer lag ${lag} exceeds high-water mark ${this.highWaterMark}`,
      );
      throw new HttpException(
        'Backtest queue is full',
        HttpStatus.TOO_MANY_REQUESTS,
      );
    }

    const messageId = await this.redis.xadd(
      'backtest:requests',
      '*',
      'data',
      JSON.stringify(message),
    );
    if (!messageId) {
      throw new Error('Failed to publish backtest request to Redis stream');
    }
    return messageId;
  }
}
