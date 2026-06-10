import {
  Entity,
  PrimaryGeneratedColumn,
  Column,
  Index,
  Unique,
} from 'typeorm';

@Index('idx_trading_events_aggregate_id', ['aggregateId'])
@Index('idx_trading_events_correlation_id', ['correlationId'])
@Index('idx_trading_events_event_type', ['eventType'])
@Index('idx_trading_events_source_service', ['sourceService'])
@Index('idx_trading_events_created_at', ['createdAt'])
@Unique('uq_aggregate_sequence', ['aggregateId', 'sequenceNumber'])
@Entity('trading_events')
export class TradingEvent {
  @PrimaryGeneratedColumn('uuid')
  id: string;

  @Column({ type: 'varchar', length: 50, name: 'event_type' })
  eventType: string;

  @Column({ type: 'varchar', length: 255, name: 'aggregate_id' })
  aggregateId: string;

  @Column({ type: 'int', name: 'sequence_number' })
  sequenceNumber: number;

  @Column({ type: 'varchar', length: 255, nullable: true, name: 'correlation_id' })
  correlationId: string | null;

  @Column({ type: 'jsonb' })
  payload: Record<string, unknown>;

  @Column({ type: 'jsonb', nullable: true, name: 'context_snapshot' })
  contextSnapshot: Record<string, unknown> | null;

  @Column({ type: 'varchar', length: 50, name: 'source_service' })
  sourceService: string;

  @Column({ type: 'timestamptz', name: 'created_at', default: () => 'NOW()' })
  createdAt: Date;

  @Column({ type: 'int', name: 'schema_version', default: 1 })
  schemaVersion: number;
}
