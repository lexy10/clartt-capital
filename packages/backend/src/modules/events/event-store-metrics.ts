import { Injectable } from '@nestjs/common';
import { Counter, Gauge, Histogram } from 'prom-client';

@Injectable()
export class EventStoreMetrics {
  readonly eventsIngestedTotal = new Counter({
    name: 'event_store_events_ingested_total',
    help: 'Total number of events successfully ingested into the event store',
    labelNames: ['event_type', 'source_service'] as const,
  });

  readonly ingestionErrorsTotal = new Counter({
    name: 'event_store_ingestion_errors_total',
    help: 'Total number of event ingestion errors',
    labelNames: ['event_type', 'error_type'] as const,
  });

  readonly ingestionDurationSeconds = new Histogram({
    name: 'event_store_ingestion_duration_seconds',
    help: 'Duration of event persistence in seconds',
    buckets: [0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1],
  });

  readonly activeEventCount = new Gauge({
    name: 'event_store_active_event_count',
    help: 'Total number of events in the active trading_events table',
  });

  readonly queryDurationSeconds = new Histogram({
    name: 'event_store_query_duration_seconds',
    help: 'Duration of event store queries in seconds',
    labelNames: ['query_type'] as const,
    buckets: [0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5],
  });

  recordIngestion(eventType: string, sourceService: string): void {
    this.eventsIngestedTotal.inc({ event_type: eventType, source_service: sourceService });
  }

  recordIngestionError(eventType: string, errorType: string): void {
    this.ingestionErrorsTotal.inc({ event_type: eventType, error_type: errorType });
  }

  observeIngestionDuration(durationSeconds: number): void {
    this.ingestionDurationSeconds.observe(durationSeconds);
  }

  setActiveEventCount(count: number): void {
    this.activeEventCount.set(count);
  }

  observeQueryDuration(queryType: string, durationSeconds: number): void {
    this.queryDurationSeconds.observe({ query_type: queryType }, durationSeconds);
  }
}
