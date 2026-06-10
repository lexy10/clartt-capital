import { Injectable, OnModuleInit, OnModuleDestroy, Logger } from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import { InjectRepository } from '@nestjs/typeorm';
import { Repository } from 'typeorm';
import { Worker, Job } from 'bullmq';
import Redis from 'ioredis';
import { Alert } from '../../modules/alerts/entities/alert.entity';

export interface AlertEvaluationJobData {
  instrument: string;
  currentPrice: number;
}

@Injectable()
export class AlertEvaluationWorker implements OnModuleInit, OnModuleDestroy {
  private readonly logger = new Logger(AlertEvaluationWorker.name);
  private worker: Worker;

  constructor(
    private readonly configService: ConfigService,
    @InjectRepository(Alert)
    private readonly alertsRepository: Repository<Alert>,
  ) {}

  onModuleInit() {
    const redisUrl = this.configService.get<string>('REDIS_URL') || 'redis://localhost:6379';
    const connection = new Redis(redisUrl, { maxRetriesPerRequest: null });

    this.worker = new Worker(
      'alert-evaluation',
      async (job: Job<AlertEvaluationJobData>) => {
        this.logger.log(`Processing alert evaluation job ${job.id}`);
        const { instrument, currentPrice } = job.data;
        await this.evaluateAlerts(instrument, currentPrice);
        this.logger.log(`Completed alert evaluation job ${job.id}`);
      },
      { connection: connection as any },
    );

    this.worker.on('failed', (job: Job | undefined, err: Error) => {
      this.logger.error(
        `Alert evaluation job ${job?.id} failed: ${err.message}`,
        err.stack,
      );
    });

    this.logger.log('Alert evaluation worker started');
  }

  private async evaluateAlerts(instrument: string, currentPrice: number): Promise<void> {
    const activeAlerts = await this.alertsRepository.find({
      where: { instrument, isActive: true },
    });

    for (const alert of activeAlerts) {
      const triggered = this.checkCondition(alert, currentPrice);
      if (triggered) {
        alert.isActive = false;
        alert.triggeredAt = new Date();
        await this.alertsRepository.save(alert);
        this.logger.log(`Alert ${alert.id} triggered for ${instrument} at ${currentPrice}`);
      }
    }
  }

  private checkCondition(alert: Alert, currentPrice: number): boolean {
    const value = alert.conditionValue as Record<string, unknown>;
    const targetPrice = Number(value.price);

    if (isNaN(targetPrice)) {
      return false;
    }

    switch (alert.conditionType) {
      case 'price_above':
        return currentPrice >= targetPrice;
      case 'price_below':
        return currentPrice <= targetPrice;
      case 'price_cross':
        return currentPrice === targetPrice;
      default:
        return false;
    }
  }

  async onModuleDestroy() {
    if (this.worker) {
      await this.worker.close();
      this.logger.log('Alert evaluation worker stopped');
    }
  }
}
