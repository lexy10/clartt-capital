import {
  Entity,
  PrimaryGeneratedColumn,
  Column,
  CreateDateColumn,
  ManyToOne,
  JoinColumn,
  Index,
} from 'typeorm';
import { TradingAccount } from './trading-account.entity';
import { Trade } from './trade.entity';

@Index('idx_positions_account_id', ['accountId'])
@Entity('positions')
export class Position {
  @PrimaryGeneratedColumn('uuid')
  id: string;

  @Column({ type: 'uuid', name: 'account_id', nullable: true })
  accountId: string | null;

  @ManyToOne(() => TradingAccount, { nullable: true })
  @JoinColumn({ name: 'account_id' })
  account: TradingAccount;

  @Column({ type: 'uuid', name: 'trade_id', nullable: true })
  tradeId: string | null;

  @ManyToOne(() => Trade, { nullable: true })
  @JoinColumn({ name: 'trade_id' })
  trade: Trade;

  @Column({ type: 'varchar', length: 50 })
  instrument: string;

  @Column({ type: 'varchar', length: 10 })
  direction: string;

  @Column({ type: 'decimal', precision: 18, scale: 8, name: 'entry_price' })
  entryPrice: string;

  @Column({ type: 'decimal', precision: 18, scale: 8, name: 'current_price', nullable: true })
  currentPrice: string | null;

  @Column({ type: 'decimal', precision: 18, scale: 8, name: 'position_size' })
  positionSize: string;

  @Column({ type: 'decimal', precision: 18, scale: 8, name: 'unrealized_pnl', nullable: true })
  unrealizedPnl: string | null;

  @CreateDateColumn({ type: 'timestamptz', name: 'opened_at' })
  openedAt: Date;
}
