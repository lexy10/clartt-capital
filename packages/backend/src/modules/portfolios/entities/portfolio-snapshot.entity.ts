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

@Index('idx_portfolio_snapshots_account_id', ['accountId'])
@Index('idx_portfolio_snapshots_snapshot_at', ['snapshotAt'])
@Entity('portfolio_snapshots')
export class PortfolioSnapshot {
  @PrimaryGeneratedColumn('uuid')
  id: string;

  @Column({ type: 'uuid', name: 'account_id', nullable: true })
  accountId: string | null;

  @ManyToOne(() => TradingAccount, { nullable: true })
  @JoinColumn({ name: 'account_id' })
  account: TradingAccount;

  @Column({ type: 'decimal', precision: 18, scale: 8 })
  equity: string;

  @Column({ type: 'decimal', precision: 18, scale: 8 })
  balance: string;

  @Column({ type: 'decimal', precision: 18, scale: 8, name: 'unrealized_pnl' })
  unrealizedPnl: string;

  @Column({ type: 'integer', name: 'open_positions' })
  openPositions: number;

  @Column({ type: 'decimal', precision: 18, scale: 8, nullable: true, default: '0' })
  margin: string;

  @Column({ type: 'decimal', precision: 18, scale: 8, name: 'free_margin', nullable: true, default: '0' })
  freeMargin: string;

  @Column({ type: 'integer', nullable: true, default: 0 })
  leverage: number;

  @CreateDateColumn({ type: 'timestamptz', name: 'snapshot_at' })
  snapshotAt: Date;
}
