import { Injectable, Logger } from '@nestjs/common';
import { Cron } from '@nestjs/schedule';
import { ConfigService } from '@nestjs/config';
import { InjectRepository } from '@nestjs/typeorm';
import { Repository } from 'typeorm';
import { Candle } from './entities/candle.entity';

/**
 * Rolling candle retention. Candles are ~99% of the database and grow ~200 MB
 * per active instrument per year with no built-in bound. This job keeps the
 * table flat by deleting candles older than CANDLE_RETENTION_DAYS.
 *
 *   CANDLE_RETENTION_DAYS=365   (default) keep one year
 *   CANDLE_RETENTION_DAYS=0     disable — keep everything forever
 *
 * Runs daily at 03:30 UTC (after the 02:00 event archival, so the two heavy
 * cleanups don't overlap). The (instrument, timeframe, timestamp) index makes
 * the timestamp filter cheap. After the first run the daily delta is tiny
 * (~one day of candles), because retention is aligned with the 12-month
 * backfill window — so a single DELETE is all that's needed.
 *
 * NOTE: keep CANDLE_RETENTION_DAYS >= the backfill window (12 months) —
 * otherwise the backfill re-fetches what this just deleted and the two fight
 * each other. If you lower it, lower BACKFILL_MONTHS in backfill.service.ts too.
 */
@Injectable()
export class CandleRetentionService {
  private readonly logger = new Logger(CandleRetentionService.name);

  constructor(
    private readonly configService: ConfigService,
    @InjectRepository(Candle)
    private readonly candleRepo: Repository<Candle>,
  ) {}

  @Cron('30 3 * * *')
  async handleRetention(): Promise<void> {
    const retentionDays = parseInt(
      this.configService.get<string>('CANDLE_RETENTION_DAYS') ?? '365',
      10,
    );

    if (!Number.isFinite(retentionDays) || retentionDays <= 0) {
      this.logger.log('Candle retention disabled (CANDLE_RETENTION_DAYS <= 0)');
      return;
    }

    const cutoff = new Date(Date.now() - retentionDays * 24 * 60 * 60 * 1000);
    this.logger.log(
      `Candle retention: deleting candles older than ${cutoff.toISOString()} (${retentionDays}d)`,
    );

    try {
      const result = await this.candleRepo
        .createQueryBuilder()
        .delete()
        .from(Candle)
        .where('timestamp < :cutoff', { cutoff })
        .execute();
      this.logger.log(`Candle retention complete: ${result.affected ?? 0} candles deleted`);
    } catch (err) {
      this.logger.error(`Candle retention failed: ${(err as Error).message}`);
    }
  }
}
