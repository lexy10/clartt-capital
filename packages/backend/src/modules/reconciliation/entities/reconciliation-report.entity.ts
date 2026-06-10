import {
  Entity,
  PrimaryGeneratedColumn,
  Column,
  CreateDateColumn,
  ManyToOne,
  JoinColumn,
  Index,
} from 'typeorm';
import { TradingAccount } from '../../trades/entities/trading-account.entity';

@Index('idx_reconciliation_reports_account_id', ['accountId'])
@Index('idx_reconciliation_reports_cycle_timestamp', ['cycleTimestamp'])
@Index('idx_reconciliation_reports_status', ['status'])
@Entity('reconciliation_reports')
export class ReconciliationReport {
  @PrimaryGeneratedColumn('uuid')
  id: string;

  @Column({ type: 'uuid', name: 'account_id' })
  accountId: string;

  @ManyToOne(() => TradingAccount, { onDelete: 'CASCADE' })
  @JoinColumn({ name: 'account_id' })
  account: TradingAccount;

  @Column({ type: 'timestamptz', name: 'cycle_timestamp' })
  cycleTimestamp: Date;

  @Column({ type: 'jsonb' })
  discrepancies: Record<string, unknown>[];

  @Column({ type: 'jsonb', name: 'auto_corrections_applied' })
  autoCorrectionsApplied: Record<string, unknown>[];

  @Column({ type: 'jsonb', name: 'broker_state_snapshot' })
  brokerStateSnapshot: Record<string, unknown>;

  @Column({ type: 'jsonb', name: 'local_state_snapshot' })
  localStateSnapshot: Record<string, unknown>;

  @Column({ type: 'integer', name: 'duration_ms' })
  durationMs: number;

  @Column({ type: 'varchar', length: 30 })
  status: string;

  @Column({ type: 'text', name: 'error_message', nullable: true })
  errorMessage: string | null;

  @CreateDateColumn({ type: 'timestamptz', name: 'created_at' })
  createdAt: Date;
}
