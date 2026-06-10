import {
  Entity,
  PrimaryGeneratedColumn,
  Column,
  CreateDateColumn,
  ManyToOne,
  JoinColumn,
} from 'typeorm';
import { Strategy } from './strategy.entity';
import { User } from '../../auth/entities/user.entity';

@Entity('backtest_results')
export class BacktestResult {
  @PrimaryGeneratedColumn('uuid')
  id: string;

  @Column({ type: 'uuid', name: 'strategy_id', nullable: true })
  strategyId: string | null;

  @ManyToOne(() => Strategy, { nullable: true })
  @JoinColumn({ name: 'strategy_id' })
  strategy: Strategy;

  @Column({ type: 'uuid', name: 'user_id', nullable: true })
  userId: string | null;

  @ManyToOne(() => User, { nullable: true })
  @JoinColumn({ name: 'user_id' })
  user: User;

  @Column({ type: 'jsonb' })
  config: Record<string, unknown>;

  @Column({ type: 'decimal', precision: 5, scale: 4, name: 'win_rate', nullable: true })
  winRate: string | null;

  @Column({ type: 'decimal', precision: 30, scale: 8, name: 'max_drawdown', nullable: true })
  maxDrawdown: string | null;

  @Column({ type: 'decimal', precision: 20, scale: 4, name: 'sharpe_ratio', nullable: true })
  sharpeRatio: string | null;

  @Column({ type: 'decimal', precision: 20, scale: 4, name: 'profit_factor', nullable: true })
  profitFactor: string | null;

  @Column({ type: 'decimal', precision: 30, scale: 8, nullable: true })
  expectancy: string | null;

  @Column({ type: 'integer', name: 'total_trades', nullable: true })
  totalTrades: number | null;

  @Column({ type: 'jsonb', name: 'trade_results', nullable: true })
  tradeResults: Record<string, unknown>[] | null;

  @Column({ type: 'varchar', length: 20, default: 'pending' })
  status: string;

  @Column({ type: 'text', name: 'error_message', nullable: true })
  errorMessage: string | null;

  @Column({ type: 'integer', name: 'winning_trades', nullable: true })
  winningTrades: number | null;

  @Column({ type: 'integer', name: 'losing_trades', nullable: true })
  losingTrades: number | null;

  @Column({ type: 'decimal', precision: 30, scale: 8, name: 'gross_profit', nullable: true })
  grossProfit: string | null;

  @Column({ type: 'decimal', precision: 30, scale: 8, name: 'gross_loss', nullable: true })
  grossLoss: string | null;

  @Column({ type: 'decimal', precision: 30, scale: 8, name: 'net_profit', nullable: true })
  netProfit: string | null;

  @Column({ type: 'decimal', precision: 10, scale: 2, name: 'average_rr', nullable: true })
  averageRr: string | null;

  @Column({ type: 'jsonb', name: 'equity_curve', nullable: true })
  equityCurve: number[] | null;

  @CreateDateColumn({ type: 'timestamptz', name: 'created_at' })
  createdAt: Date;
}
