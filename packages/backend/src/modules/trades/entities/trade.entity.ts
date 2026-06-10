import {
  Entity,
  PrimaryGeneratedColumn,
  Column,
  CreateDateColumn,
  ManyToOne,
  JoinColumn,
  Index,
} from 'typeorm';
import { Signal } from '../../signals/entities/signal.entity';
import { TradingAccount } from './trading-account.entity';

@Index('idx_trades_account_id', ['accountId'])
@Index('idx_trades_signal_id', ['signalId'])
@Index('idx_trades_created_at', ['createdAt'])
@Entity('trades')
export class Trade {
  @PrimaryGeneratedColumn('uuid')
  id: string;

  @Column({ type: 'uuid', name: 'signal_id', nullable: true })
  signalId: string | null;

  @ManyToOne(() => Signal, { nullable: true })
  @JoinColumn({ name: 'signal_id' })
  signal: Signal;

  @Column({ type: 'uuid', name: 'account_id', nullable: true })
  accountId: string | null;

  @ManyToOne(() => TradingAccount, { nullable: true })
  @JoinColumn({ name: 'account_id' })
  account: TradingAccount;

  /** Broker order/contract ID. bigint because Deriv contract IDs (~315B) exceed
   *  the int32 max of 2.1B. TypeORM returns bigints as strings to avoid
   *  JS number precision loss past 2^53, so we store as string. */
  @Column({ type: 'bigint', name: 'broker_order_id', nullable: true })
  brokerOrderId: string | null;

  /** Instrument symbol this trade is on (e.g. 'R_25', 'XAUUSD', 'EURUSD').
   *  Stored on the trade itself so the dashboard doesn't have to join through
   *  the signals table (signal_id can be NULL for synthetic/manual trades). */
  @Column({ type: 'varchar', length: 50, nullable: true })
  instrument: string | null;

  @Column({ type: 'varchar', length: 10 })
  direction: string;

  @Column({ type: 'decimal', precision: 18, scale: 8, name: 'entry_price', nullable: true })
  entryPrice: string | null;

  @Column({ type: 'decimal', precision: 18, scale: 8, name: 'exit_price', nullable: true })
  exitPrice: string | null;

  @Column({ type: 'decimal', precision: 18, scale: 8, name: 'fill_price', nullable: true })
  fillPrice: string | null;

  @Column({ type: 'decimal', precision: 18, scale: 8, name: 'position_size' })
  positionSize: string;

  @Column({ type: 'decimal', precision: 18, scale: 8, name: 'profit_loss', nullable: true })
  profitLoss: string | null;

  @Column({ type: 'integer', name: 'execution_latency_ms', nullable: true })
  executionLatencyMs: number | null;

  @Column({ type: 'decimal', precision: 18, scale: 8, nullable: true })
  slippage: string | null;

  @Column({ type: 'decimal', precision: 18, scale: 8, name: 'spread_at_execution', nullable: true })
  spreadAtExecution: string | null;

  @Column({ type: 'varchar', length: 20 })
  status: string;

  @Column({ type: 'text', name: 'rejection_reason', nullable: true })
  rejectionReason: string | null;

  @Column({ type: 'timestamptz', name: 'opened_at', nullable: true })
  openedAt: Date | null;

  @Column({ type: 'timestamptz', name: 'closed_at', nullable: true })
  closedAt: Date | null;

  @CreateDateColumn({ type: 'timestamptz', name: 'created_at' })
  createdAt: Date;
}
