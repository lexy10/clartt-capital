import { Injectable, Logger, NotFoundException } from '@nestjs/common';
import { InjectRepository } from '@nestjs/typeorm';
import { Repository, LessThanOrEqual } from 'typeorm';
import { TradingEvent } from './entities/trading-event.entity';
import { EventStoreMetrics } from './event-store-metrics';

export interface ReconstructedState {
  state: Record<string, unknown>;
  events: TradingEvent[];
  event_count: number;
}

@Injectable()
export class StateReconstructorService {
  private readonly logger = new Logger(StateReconstructorService.name);

  constructor(
    @InjectRepository(TradingEvent)
    private readonly eventRepo: Repository<TradingEvent>,
    private readonly metrics: EventStoreMetrics,
  ) {}

  async reconstruct(
    aggregateId: string,
    timestamp: string,
  ): Promise<ReconstructedState> {
    const startTime = process.hrtime.bigint();

    try {
      const events = await this.eventRepo.find({
        where: {
          aggregateId,
          createdAt: LessThanOrEqual(new Date(timestamp)),
        },
        order: { sequenceNumber: 'ASC' },
      });

      if (events.length === 0) {
        throw new NotFoundException(
          `No events found for aggregate ${aggregateId} before ${timestamp}`,
        );
      }

      const state = events.reduce<Record<string, unknown>>(
        (acc, event) => this.applyEvent(acc, event),
        {},
      );

      return { state, events, event_count: events.length };
    } finally {
      const durationNs = Number(process.hrtime.bigint() - startTime);
      this.metrics.observeQueryDuration('reconstruct', durationNs / 1e9);
    }
  }

  private applyEvent(
    state: Record<string, unknown>,
    event: TradingEvent,
  ): Record<string, unknown> {
    return {
      ...state,
      ...(event.payload as Record<string, unknown>),
      event_type: event.eventType,
      source_service: event.sourceService,
      last_updated: event.createdAt,
    };
  }
}
