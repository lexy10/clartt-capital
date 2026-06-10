import {
  Entity,
  PrimaryGeneratedColumn,
  Column,
  CreateDateColumn,
  UpdateDateColumn,
  Unique,
  Index,
} from 'typeorm';

@Entity('candles')
@Unique(['instrument', 'timeframe', 'timestamp'])
@Index('idx_candles_instrument_timeframe_timestamp', ['instrument', 'timeframe', 'timestamp'])
export class Candle {
  @PrimaryGeneratedColumn('uuid')
  id: string;

  @Column({ type: 'varchar', length: 50 })
  instrument: string;

  @Column({ type: 'varchar', length: 10 })
  timeframe: string;

  @Column({ type: 'double precision' })
  open: number;

  @Column({ type: 'double precision' })
  high: number;

  @Column({ type: 'double precision' })
  low: number;

  @Column({ type: 'double precision' })
  close: number;

  @Column({ type: 'double precision', default: 0 })
  volume: number;

  @Column({ type: 'timestamptz' })
  timestamp: Date;

  @Column({ type: 'boolean', default: false })
  completed: boolean;

  @CreateDateColumn({ type: 'timestamptz', name: 'created_at' })
  createdAt: Date;

  @UpdateDateColumn({ type: 'timestamptz', name: 'updated_at' })
  updatedAt: Date;
}
