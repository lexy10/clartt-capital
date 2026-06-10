import { Injectable, Logger } from '@nestjs/common';
import { Cron } from '@nestjs/schedule';
import { ConfigService } from '@nestjs/config';
import { InjectRepository } from '@nestjs/typeorm';
import { Repository } from 'typeorm';
import { TradingEvent } from './entities/trading-event.entity';
import { EventStoreMetrics } from './event-store-metrics';

@Injectable()
export class ArchivalService {
  private readonly logger = new Logger(ArchivalService.name);

  constructor(
    private readonly configService: ConfigService,
    @InjectRepository(TradingEvent)
    private readonly eventRepo: Repository<TradingEvent>,
    private readonly metrics: EventStoreMetrics,
  ) {}

  /**
   * Scheduled archival job — runs daily at 02:00 UTC.
   * 1. Moves events older than EVENT_RETENTION_DAYS from trading_events to trading_events_archive
   * 2. Deletes archived events older than EVENT_ARCHIVE_RETENTION_DAYS
   * 3. Updates the active event count gauge
   */
  @Cron('0 2 * * *')
  async handleArchival(): Promise<void> {
    this.logger.log('Starting event archival job');

    const retentionDays = parseInt(
      this.configService.get<string>('EVENT_RETENTION_DAYS') || '90',
      10,
    );
    const archiveRetentionDays = parseInt(
      this.configService.get<string>('EVENT_ARCHIVE_RETENTION_DAYS') || '365',
      10,
    );

    const manager = this.eventRepo.manager;

    let archivedCount = 0;
    let deletedExpiredCount = 0;

    try {
      // Step 1: Move expired events from trading_events to trading_events_archive
      const archiveResult = await manager.query(
        `INSERT INTO trading_events_archive (id, event_type, aggregate_id, sequence_number, correlation_id, payload, context_snapshot, source_service, created_at, schema_version)
         SELECT id, event_type, aggregate_id, sequence_number, correlation_id, payload, context_snapshot, source_service, created_at, schema_version
         FROM trading_events
         WHERE created_at < NOW() - INTERVAL '1 day' * $1
         ON CONFLICT (id) DO NOTHING`,
        [retentionDays],
      );
      archivedCount = archiveResult?.[1] ?? 0;

      // Step 2: Disable append-only trigger, delete archived events from active table, re-enable trigger
      await manager.query(
        `ALTER TABLE trading_events DISABLE TRIGGER trg_prevent_event_mutation`,
      );

      try {
        const deleteResult = await manager.query(
          `DELETE FROM trading_events WHERE created_at < NOW() - INTERVAL '1 day' * $1`,
          [retentionDays],
        );
        archivedCount = deleteResult?.[1] ?? archivedCount;
      } finally {
        await manager.query(
          `ALTER TABLE trading_events ENABLE TRIGGER trg_prevent_event_mutation`,
        );
      }

      // Step 3: Delete expired archived events
      const expiredResult = await manager.query(
        `DELETE FROM trading_events_archive WHERE created_at < NOW() - INTERVAL '1 day' * $1`,
        [archiveRetentionDays],
      );
      deletedExpiredCount = expiredResult?.[1] ?? 0;

      // Step 4: Update active event count gauge
      const countResult = await manager.query(
        `SELECT COUNT(*) as count FROM trading_events`,
      );
      const activeCount = parseInt(countResult?.[0]?.count ?? '0', 10);
      this.metrics.setActiveEventCount(activeCount);

      this.logger.log(
        `Archival complete: ${archivedCount} events archived, ${deletedExpiredCount} expired archive events deleted, ${activeCount} active events remaining`,
      );
    } catch (err: any) {
      this.logger.error(`Archival job failed: ${err?.message}`, err?.stack);
    }
  }
}
