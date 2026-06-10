import {
  Entity,
  PrimaryGeneratedColumn,
  Column,
  CreateDateColumn,
  ManyToOne,
  JoinColumn,
  Index,
} from 'typeorm';
import { Strategy } from '../../strategies/entities/strategy.entity';

@Index('idx_signals_created_at', ['createdAt'])
@Index('idx_signals_strategy_id', ['strategyId'])
@Entity('signals')
export class Signal {
  @PrimaryGeneratedColumn('uuid')
  id: string;

  @Column({ type: 'varchar', length: 50 })
  instrument: string;

  @Column({ type: 'varchar', length: 10 })
  direction: string;

  @Column({ type: 'decimal', precision: 18, scale: 8, name: 'entry_price' })
  entryPrice: string;

  @Column({ type: 'decimal', precision: 18, scale: 8, name: 'stop_loss' })
  stopLoss: string;

  @Column({ type: 'decimal', precision: 18, scale: 8, name: 'take_profit' })
  takeProfit: string;

  @Column({ type: 'decimal', precision: 18, scale: 8, name: 'position_size' })
  positionSize: string;

  @Column({ type: 'decimal', precision: 5, scale: 4, name: 'confidence_score' })
  confidenceScore: string;

  @Column({ type: 'varchar', length: 10 })
  timeframe: string;

  @Column({ type: 'varchar', length: 255, name: 'order_block_id', nullable: true })
  orderBlockId: string | null;

  @Column({ type: 'uuid', name: 'strategy_id', nullable: true })
  strategyId: string | null;

  @ManyToOne(() => Strategy, { nullable: true })
  @JoinColumn({ name: 'strategy_id' })
  strategy: Strategy;

  @Column({ type: 'varchar', length: 20 })
  mode: string;

  @Column({ type: 'jsonb', nullable: true })
  metadata: Record<string, unknown> | null;

  @CreateDateColumn({ type: 'timestamptz', name: 'created_at' })
  createdAt: Date;
}
