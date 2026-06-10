import { Injectable, OnModuleInit, OnModuleDestroy, Logger } from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import { Worker, Job } from 'bullmq';
import Redis from 'ioredis';
import { PortfoliosService } from '../../modules/portfolios/portfolios.service';

@Injectable()
export class PortfolioSnapshotWorker implements OnModuleInit, OnModuleDestroy {
  private readonly logger = new Logger(PortfolioSnapshotWorker.name);
  private worker: Worker;

  constructor(
    private readonly configService: ConfigService,
    private readonly portfoliosService: PortfoliosService,
  ) {}

  onModuleInit() {
    const redisUrl = this.configService.get<string>('REDIS_URL') || 'redis://localhost:6379';
    const connection = new Redis(redisUrl, { maxRetriesPerRequest: null });

    this.worker = new Worker(
      'portfolio-snapshots',
      async (job: Job) => {
        this.logger.log(`Processing portfolio snapshot job ${job.id}`);
        await this.portfoliosService.syncAccounts();
        this.logger.log(`Completed portfolio snapshot job ${job.id}`);
      },
      { connection: connection as any },
    );

    this.worker.on('failed', (job: Job | undefined, err: Error) => {
      this.logger.error(
        `Portfolio snapshot job ${job?.id} failed: ${err.message}`,
        err.stack,
      );
    });

    this.logger.log('Portfolio snapshot worker started');
  }

  async onModuleDestroy() {
    if (this.worker) {
      await this.worker.close();
      this.logger.log('Portfolio snapshot worker stopped');
    }
  }
}
