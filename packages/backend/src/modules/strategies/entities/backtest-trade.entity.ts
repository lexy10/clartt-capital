import {
  Entity,
  PrimaryGeneratedColumn,
  Column,
  ManyToOne,
  JoinColumn,
  CreateDateColumn,
} from 'typeorm';
import { BacktestResult } from './backtest-result.entity';

@Entity('backtest_trades')
export class BacktestTrade {
  @PrimaryGeneratedColumn('uuid')
  id: string;

  @Column({ type: 'uuid', name: 'backtest_result_id' })
  backtestResultId: string;

  @ManyToOne(() => BacktestResult, { onDelete: 'CASCADE' })
  @JoinColumn({ name: 'backtest_result_id' })
  backtestResult: BacktestResult;

  @Column({ type: 'varchar', length: 255, name: 'signal_id' })
  signalId: string;

  @Column({ type: 'varchar', length: 10 })
  direction: string; // "BUY" or "SELL"

  @Column({ type: 'decimal', precision: 18, scale: 8, name: 'entry_price' })
  entryPrice: string;

  @Column({ type: 'decimal', precision: 18, scale: 8, name: 'exit_price' })
  exitPrice: string;

  @Column({ type: 'decimal', precision: 18, scale: 8, name: 'stop_loss', nullable: true })
  stopLoss: string | null;

  @Column({ type: 'decimal', precision: 18, scale: 8, name: 'take_profit', nullable: true })
  takeProfit: string | null;

  @Column({ type: 'decimal', precision: 30, scale: 8, name: 'position_size' })
  positionSize: string;

  @Column({ type: 'decimal', precision: 30, scale: 8, name: 'profit_loss' })
  profitLoss: string;

  @Column({ type: 'decimal', precision: 10, scale: 2, name: 'reward_risk', nullable: true })
  rewardRisk: string | null;

  @Column({ type: 'decimal', precision: 18, scale: 8, name: 'initial_stop_loss', nullable: true })
  initialStopLoss: string | null;

  @Column({ type: 'decimal', precision: 30, scale: 2, name: 'balance_before', nullable: true })
  balanceBefore: string | null;

  @Column({ type: 'decimal', precision: 30, scale: 2, name: 'balance_after', nullable: true })
  balanceAfter: string | null;

  @Column({ type: 'timestamptz', name: 'entry_time' })
  entryTime: Date;

  @Column({ type: 'timestamptz', name: 'exit_time' })
  exitTime: Date;

  @Column({ type: 'integer', name: 'trade_index' })
  tradeIndex: number;

  @CreateDateColumn({ type: 'timestamptz', name: 'created_at' })
  createdAt: Date;
}
