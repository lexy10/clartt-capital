import {
  Entity,
  PrimaryGeneratedColumn,
  Column,
  UpdateDateColumn,
  ManyToOne,
  JoinColumn,
  Unique,
} from 'typeorm';
import { TradingAccount } from '../../trades/entities/trading-account.entity';

@Unique(['accountId'])
@Entity('reconciliation_configs')
export class ReconciliationConfig {
  @PrimaryGeneratedColumn('uuid')
  id: string;

  @Column({ type: 'uuid', name: 'account_id', nullable: true })
  accountId: string | null;

  @ManyToOne(() => TradingAccount, { nullable: true, onDelete: 'CASCADE' })
  @JoinColumn({ name: 'account_id' })
  account: TradingAccount | null;

  @Column({ type: 'integer', name: 'reconciliation_interval_seconds', default: 60 })
  reconciliationIntervalSeconds: number;

  @Column({ type: 'decimal', precision: 18, scale: 2, name: 'balance_drift_threshold', default: 10.0 })
  balanceDriftThreshold: string;

  @Column({ type: 'decimal', precision: 18, scale: 2, name: 'equity_drift_threshold', default: 50.0 })
  equityDriftThreshold: string;

  @Column({ type: 'decimal', precision: 18, scale: 4, name: 'position_size_drift_threshold', default: 0.01 })
  positionSizeDriftThreshold: string;

  @Column({ type: 'boolean', name: 'auto_correct_phantom_positions', default: false })
  autoCorrectPhantomPositions: boolean;

  @Column({ type: 'boolean', name: 'auto_correct_missing_positions', default: false })
  autoCorrectMissingPositions: boolean;

  @Column({ type: 'boolean', name: 'auto_correct_balance_drift', default: false })
  autoCorrectBalanceDrift: boolean;

  @Column({ type: 'integer', name: 'escalation_cycle_count', default: 3 })
  escalationCycleCount: number;

  @UpdateDateColumn({ type: 'timestamptz', name: 'updated_at' })
  updatedAt: Date;
}
