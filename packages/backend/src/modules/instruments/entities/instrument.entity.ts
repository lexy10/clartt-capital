import {
  Entity,
  PrimaryGeneratedColumn,
  Column,
  CreateDateColumn,
  UpdateDateColumn,
} from 'typeorm';

@Entity('instruments')
export class Instrument {
  @PrimaryGeneratedColumn('uuid')
  id: string;

  @Column({ type: 'varchar', length: 50, unique: true })
  symbol: string;

  @Column({ type: 'varchar', length: 100, name: 'display_name' })
  displayName: string;

  @Column({ type: 'varchar', length: 20 })
  type: 'index' | 'commodity' | 'synthetic';

  /** Broker routing category — drives which BrokerClient handles this instrument. */
  @Column({ type: 'varchar', length: 20, nullable: true })
  category:
    | 'synthetic'
    | 'forex'
    | 'commodity'
    | 'index'
    | 'stock'
    | 'crypto'
    | 'future'
    | null;

  /** Explicit broker provider override for this instrument (rare — overrides category default). */
  @Column({ type: 'varchar', length: 20, nullable: true, name: 'preferred_provider' })
  preferredProvider:
    | 'deriv'
    | 'metaapi'
    | 'alpaca'
    | 'binance'
    | 'ibkr'
    | 'stub'
    | null;

  @Column({ type: 'boolean', name: 'is_active', default: true })
  isActive: boolean;

  @Column({ type: 'varchar', length: 50, name: 'deriv_symbol', nullable: true })
  derivSymbol: string | null;

  /** Value of 1 standard lot (e.g. 100 for XAUUSD = 100oz, 1 for synthetics) */
  @Column({ type: 'decimal', precision: 16, scale: 6, name: 'contract_size', default: 1 })
  contractSize: number;

  /** Smallest price increment (e.g. 0.01 for XAUUSD, 0.001 for R_75) */
  @Column({ type: 'decimal', precision: 16, scale: 8, name: 'pip_size', default: 0.01 })
  pipSize: number;

  /** Value per pip per 1 lot in USD (e.g. 1.0 for XAUUSD, 1.0 for synthetics) */
  @Column({ type: 'decimal', precision: 16, scale: 6, name: 'pip_value', default: 1 })
  pipValue: number;

  /** Minimum tradeable lot size */
  @Column({ type: 'decimal', precision: 10, scale: 4, name: 'min_lot', default: 0.01 })
  minLot: number;

  /** Lot size increment */
  @Column({ type: 'decimal', precision: 10, scale: 4, name: 'lot_step', default: 0.01 })
  lotStep: number;

  /** Default leverage ratio (e.g. 500 for 500:1) */
  @Column({ type: 'int', name: 'leverage', default: 100 })
  leverage: number;

  @CreateDateColumn({ type: 'timestamptz', name: 'created_at' })
  createdAt: Date;

  @UpdateDateColumn({ type: 'timestamptz', name: 'updated_at' })
  updatedAt: Date;
}
