import {
  Inject,
  Injectable,
  Logger,
  OnModuleInit,
  OnModuleDestroy,
} from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import { InjectRepository } from '@nestjs/typeorm';
import { Repository } from 'typeorm';
import { Worker, Queue, Job } from 'bullmq';
import Redis from 'ioredis';

import { REDIS_CLIENT } from '../../common/modules/redis.module';
import { TradingAccount } from '../trades/entities/trading-account.entity';
import { ReconciliationService } from './reconciliation.service';
import { ReconciliationConfigService } from './reconciliation-config.service';

const BATCH_SIZE = 5;
const REDIS_STATE_PREFIX = 'reconciliation:state:';

@Injectable()
export class ReconciliationWorker implements OnModuleInit, OnModuleDestroy {
  private readonly logger = new Logger(ReconciliationWorker.name);
  private worker: Worker;
  private queue: Queue;

  constructor(
    private readonly configService: ConfigService,
    private readonly reconciliationService: ReconciliationService,
    private readonly reconciliationConfigService: ReconciliationConfigService,
    @InjectRepository(TradingAccount)
    private readonly tradingAccountRepo: Repository<TradingAccount>,
    @Inject(REDIS_CLIENT) private readonly redis: Redis,
  ) {}

  async onModuleInit() {
    const redisUrl =
      this.configService.get<string>('REDIS_URL') || 'redis://localhost:6379';
    const connection = new Redis(redisUrl, { maxRetriesPerRequest: null });

    // Create queue and add repeatable job
    this.queue = new Queue('reconciliation', {
      connection: connection.duplicate() as any,
    });

    const effectiveConfig =
      await this.reconciliationConfigService.getEffectiveConfig('global');
    const intervalMs = effectiveConfig.reconciliationIntervalSeconds * 1000;

    await this.queue.add(
      'reconciliation-cycle',
      {},
      { repeat: { every: intervalMs } },
    );

    // Create worker to process jobs
    this.worker = new Worker(
      'reconciliation',
      async (job: Job) => {
        this.logger.log(`Processing reconciliation cycle job ${job.id}`);
        await this.processReconciliationCycle();
        this.logger.log(`Completed reconciliation cycle job ${job.id}`);
      },
      { connection: connection as any, concurrency: 1 },
    );

    this.worker.on('failed', (job: Job | undefined, err: Error) => {
      this.logger.error(
        `Reconciliation job ${job?.id} failed: ${err.message}`,
        err.stack,
      );
    });

    this.logger.log('Reconciliation worker started');
  }

  async onModuleDestroy() {
    if (this.worker) {
      await this.worker.close();
      this.logger.log('Reconciliation worker stopped');
    }
    if (this.queue) {
      await this.queue.close();
      this.logger.log('Reconciliation queue closed');
    }
  }

  /**
   * Main reconciliation cycle: fetch active accounts, clean up deactivated,
   * and process active accounts in batches of 5.
   */
  async processReconciliationCycle(): Promise<void> {
    // Fetch all accounts (active and inactive for cleanup)
    const allAccounts = await this.tradingAccountRepo.find();

    const activeAccounts = allAccounts.filter((a) => a.isActive);
    const deactivatedAccounts = allAccounts.filter((a) => !a.isActive);

    // Clean up Redis state for deactivated accounts
    await this.cleanupDeactivatedAccounts(deactivatedAccounts);

    if (activeAccounts.length === 0) {
      this.logger.log('No active accounts to reconcile');
      return;
    }

    this.logger.log(
      `Reconciling ${activeAccounts.length} active accounts in batches of ${BATCH_SIZE}`,
    );

    // Process in batches of 5
    const batches = this.chunk(activeAccounts, BATCH_SIZE);
    for (const batch of batches) {
      const results = await Promise.allSettled(
        batch.map((account) =>
          this.reconciliationService.reconcileAccount(account),
        ),
      );

      // Log any rejected promises
      for (let i = 0; i < results.length; i++) {
        const result = results[i];
        if (result.status === 'rejected') {
          this.logger.error(
            `Reconciliation failed for account ${batch[i].id}: ${result.reason}`,
          );
        }
      }
    }

    // Purge expired reports at the end of each cycle
    try {
      const purged = await this.reconciliationService.purgeExpiredReports();
      if (purged > 0) {
        this.logger.log(`Purged ${purged} expired reconciliation reports`);
      }
    } catch (err) {
      this.logger.warn(`Failed to purge expired reports: ${err}`);
    }
  }

  /**
   * Remove Redis state keys for deactivated accounts.
   */
  private async cleanupDeactivatedAccounts(
    accounts: TradingAccount[],
  ): Promise<void> {
    for (const account of accounts) {
      try {
        const stateKey = `${REDIS_STATE_PREFIX}${account.id}`;
        await this.redis.del(stateKey);
      } catch (err) {
        this.logger.warn(
          `Failed to clean up Redis state for deactivated account ${account.id}: ${err}`,
        );
      }
    }
  }

  /**
   * Split an array into chunks of the given size.
   */
  chunk<T>(array: T[], size: number): T[][] {
    const chunks: T[][] = [];
    for (let i = 0; i < array.length; i += size) {
      chunks.push(array.slice(i, i + size));
    }
    return chunks;
  }
}
