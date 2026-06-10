import {
  Injectable,
  Inject,
  Logger,
  OnModuleInit,
  OnModuleDestroy,
} from '@nestjs/common';
import { InjectRepository } from '@nestjs/typeorm';
import { Repository } from 'typeorm';
import Redis from 'ioredis';
import { validate } from 'class-validator';
import { plainToInstance } from 'class-transformer';
import { REDIS_CLIENT } from '@/common/modules/redis.module';
import { TradingEvent } from './entities/trading-event.entity';
import { TradingEventDto } from './dto/trading-event.dto';
import { TradingGateway } from '../gateway/trading.gateway';
import { EventStoreMetrics } from './event-store-metrics';

@Injectable()
export class EventIngestionService implements OnModuleInit, OnModuleDestroy {
  private readonly logger = new Logger(EventIngestionService.name);
  private readonly STREAM_KEY = 'events:stream';
  private readonly GROUP = 'event-store-writer';
  private readonly CONSUMER = 'backend-ingestion';
  private readonly BLOCK_MS = 5000;
  private running = false;
  private pollTimer: ReturnType<typeof setTimeout> | null = null;

  constructor(
    @Inject(REDIS_CLIENT) private readonly redis: Redis,
    @InjectRepository(TradingEvent)
    private readonly eventRepo: Repository<TradingEvent>,
    private readonly gateway: TradingGateway,
    private readonly metrics: EventStoreMetrics,
  ) {}

  async onModuleInit(): Promise<void> {
    await this.ensureConsumerGroup();
    await this.processPendingMessages();
    this.startPolling();
  }

  onModuleDestroy(): void {
    this.running = false;
    if (this.pollTimer) {
      clearTimeout(this.pollTimer);
      this.pollTimer = null;
    }
    this.logger.log('Event ingestion service stopped');
  }

  /**
   * Create the consumer group if it doesn't already exist.
   * Handles BUSYGROUP error gracefully (group already exists).
   */
  private async ensureConsumerGroup(): Promise<void> {
    try {
      await this.redis.xgroup(
        'CREATE',
        this.STREAM_KEY,
        this.GROUP,
        '0',
        'MKSTREAM',
      );
      this.logger.log(
        `Created consumer group "${this.GROUP}" on "${this.STREAM_KEY}"`,
      );
    } catch (err: any) {
      if (err?.message?.includes('BUSYGROUP')) {
        this.logger.debug(`Consumer group "${this.GROUP}" already exists`);
      } else {
        this.logger.error(
          `Failed to create consumer group: ${err?.message}`,
        );
        throw err;
      }
    }
  }

  /**
   * Process pending (unacknowledged) messages from a previous crash.
   * Uses id '0' to read all pending messages for this consumer.
   */
  private async processPendingMessages(): Promise<void> {
    this.logger.log('Processing pending messages...');
    try {
      const results = (await this.redis.xreadgroup(
        'GROUP',
        this.GROUP,
        this.CONSUMER,
        'COUNT',
        '100',
        'STREAMS',
        this.STREAM_KEY,
        '0',
      )) as [string, [string, string[]][]][] | null;

      if (!results) {
        this.logger.log('No pending messages to process');
        return;
      }

      for (const [, messages] of results) {
        for (const [messageId, fields] of messages) {
          await this.handleMessage(messageId, fields);
        }
      }

      this.logger.log('Pending messages processed');
    } catch (err: any) {
      this.logger.error(
        `Error processing pending messages: ${err?.message}`,
      );
    }
  }

  /**
   * Start the polling loop for new messages using XREADGROUP with BLOCK.
   */
  private startPolling(): void {
    this.running = true;
    this.logger.log('Event ingestion polling started');
    this.poll();
  }

  private async poll(): Promise<void> {
    if (!this.running) return;

    try {
      const results = (await this.redis.xreadgroup(
        'GROUP',
        this.GROUP,
        this.CONSUMER,
        'COUNT',
        '10',
        'BLOCK',
        String(this.BLOCK_MS),
        'STREAMS',
        this.STREAM_KEY,
        '>',
      )) as [string, [string, string[]][]][] | null;

      if (results) {
        for (const [, messages] of results) {
          for (const [messageId, fields] of messages) {
            await this.handleMessage(messageId, fields);
          }
        }
      }
    } catch (err: any) {
      if (this.running) {
        this.logger.error(`Error polling events stream: ${err?.message}`);
      }
    }

    if (this.running) {
      this.pollTimer = setTimeout(() => this.poll(), 50);
    }
  }

  /**
   * Handle a single message from the stream.
   * Parses the data field, validates the DTO, persists to PostgreSQL,
   * and emits via WebSocket.
   */
  private async handleMessage(
    messageId: string,
    fields: string[],
  ): Promise<void> {
    const dataIndex = fields.indexOf('data');
    if (dataIndex === -1 || dataIndex + 1 >= fields.length) {
      this.logger.warn(
        `Message ${messageId} missing "data" field, acknowledging to prevent reprocessing`,
      );
      await this.ack(messageId);
      return;
    }

    let parsed: Record<string, unknown>;
    try {
      parsed = JSON.parse(fields[dataIndex + 1]);
    } catch {
      this.logger.error(
        `Message ${messageId} has invalid JSON, acknowledging to prevent reprocessing`,
      );
      this.metrics.recordIngestionError('unknown', 'validation_failure');
      await this.ack(messageId);
      return;
    }

    const dto = plainToInstance(TradingEventDto, parsed);
    const errors = await validate(dto);

    if (errors.length > 0) {
      const errorMessages = errors
        .map((e) => Object.values(e.constraints ?? {}))
        .flat()
        .join(', ');
      this.logger.error(
        `Validation failed for message ${messageId}: ${errorMessages}`,
        JSON.stringify(parsed),
      );
      this.metrics.recordIngestionError(
        (parsed.event_type as string) || 'unknown',
        'validation_failure',
      );
      // XACK to prevent poison-message loop
      await this.ack(messageId);
      return;
    }

    try {
      const startTime = process.hrtime.bigint();
      const entity = await this.persistEvent(dto);
      const durationNs = Number(process.hrtime.bigint() - startTime);
      this.metrics.observeIngestionDuration(durationNs / 1e9);
      this.metrics.recordIngestion(dto.event_type, dto.source_service);
      await this.ack(messageId);
      this.gateway.emitNewEvent(entity);
    } catch (err: any) {
      // Persistence failure — do NOT XACK, message stays pending for retry
      this.logger.error(
        `Failed to persist event from message ${messageId}: ${err?.message}`,
        JSON.stringify(parsed),
      );
      this.metrics.recordIngestionError(
        dto.event_type || 'unknown',
        'persistence_failure',
      );
    }
  }

  /**
   * Persist a validated event DTO to PostgreSQL.
   */
  private async persistEvent(dto: TradingEventDto): Promise<TradingEvent> {
    const entity = this.eventRepo.create({
      eventType: dto.event_type,
      aggregateId: dto.aggregate_id,
      sequenceNumber: dto.sequence_number,
      correlationId: dto.correlation_id ?? null,
      payload: dto.payload,
      contextSnapshot: dto.context_snapshot ?? null,
      sourceService: dto.source_service,
      schemaVersion: dto.schema_version ?? 1,
    });

    return this.eventRepo.save(entity);
  }

  /**
   * Acknowledge a message in the consumer group.
   */
  private async ack(messageId: string): Promise<void> {
    try {
      await this.redis.xack(this.STREAM_KEY, this.GROUP, messageId);
    } catch (err: any) {
      this.logger.error(
        `Failed to XACK message ${messageId}: ${err?.message}`,
      );
    }
  }
}
