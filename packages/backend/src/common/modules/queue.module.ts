import { Module, Global, OnModuleDestroy, Inject, Logger } from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import { Queue, Worker, Job } from 'bullmq';
import Redis from 'ioredis';
import { REDIS_CLIENT } from './redis.module';

export const PORTFOLIO_SNAPSHOT_QUEUE = 'PORTFOLIO_SNAPSHOT_QUEUE';
export const ALERT_EVALUATION_QUEUE = 'ALERT_EVALUATION_QUEUE';

@Global()
@Module({
  providers: [
    {
      provide: PORTFOLIO_SNAPSHOT_QUEUE,
      useFactory: (config: ConfigService) => {
        const redisUrl = config.get<string>('REDIS_URL') || 'redis://localhost:6379';
        return new Queue('portfolio-snapshots', {
          connection: new Redis(redisUrl, { maxRetriesPerRequest: null }) as any,
        });
      },
      inject: [ConfigService],
    },
    {
      provide: ALERT_EVALUATION_QUEUE,
      useFactory: (config: ConfigService) => {
        const redisUrl = config.get<string>('REDIS_URL') || 'redis://localhost:6379';
        return new Queue('alert-evaluation', {
          connection: new Redis(redisUrl, { maxRetriesPerRequest: null }) as any,
        });
      },
      inject: [ConfigService],
    },
  ],
  exports: [PORTFOLIO_SNAPSHOT_QUEUE, ALERT_EVALUATION_QUEUE],
})
export class QueueModule implements OnModuleDestroy {
  private readonly logger = new Logger(QueueModule.name);

  constructor(
    @Inject(PORTFOLIO_SNAPSHOT_QUEUE) private readonly portfolioQueue: Queue,
    @Inject(ALERT_EVALUATION_QUEUE) private readonly alertQueue: Queue,
  ) {}

  async onModuleDestroy() {
    this.logger.log('Closing BullMQ queues');
    await this.portfolioQueue.close();
    await this.alertQueue.close();
  }
}
