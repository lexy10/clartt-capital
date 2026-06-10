import {
  Entity,
  PrimaryColumn,
  Column,
  UpdateDateColumn,
  ManyToOne,
  JoinColumn,
} from 'typeorm';
import { TradingAccount } from '../../trades/entities/trading-account.entity';

@Entity('autopilot_states')
export class AutopilotState {
  @PrimaryColumn({ type: 'uuid', name: 'account_id' })
  accountId: string;

  @ManyToOne(() => TradingAccount, { onDelete: 'CASCADE' })
  @JoinColumn({ name: 'account_id' })
  account: TradingAccount;

  @Column({ type: 'boolean', default: false })
  enabled: boolean;

  @UpdateDateColumn({ type: 'timestamptz', name: 'updated_at' })
  updatedAt: Date;
}
