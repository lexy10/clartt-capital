import {
  Entity,
  PrimaryGeneratedColumn,
  Column,
  CreateDateColumn,
  ManyToOne,
  JoinColumn,
  Unique,
} from 'typeorm';
import { TradingAccount } from '../../trades/entities/trading-account.entity';
import { Instrument } from './instrument.entity';

@Entity('account_instruments')
@Unique(['accountId', 'instrumentId'])
export class AccountInstrument {
  @PrimaryGeneratedColumn('uuid')
  id: string;

  @Column({ type: 'uuid', name: 'account_id' })
  accountId: string;

  @ManyToOne(() => TradingAccount, { onDelete: 'CASCADE' })
  @JoinColumn({ name: 'account_id' })
  account: TradingAccount;

  @Column({ type: 'uuid', name: 'instrument_id' })
  instrumentId: string;

  @ManyToOne(() => Instrument, { onDelete: 'CASCADE' })
  @JoinColumn({ name: 'instrument_id' })
  instrument: Instrument;

  @Column({ type: 'varchar', length: 100, name: 'broker_symbol' })
  brokerSymbol: string;

  @CreateDateColumn({ type: 'timestamptz', name: 'created_at' })
  createdAt: Date;
}
