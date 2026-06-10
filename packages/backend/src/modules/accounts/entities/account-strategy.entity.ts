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
import { Strategy } from '../../strategies/entities/strategy.entity';

@Entity('account_strategies')
@Unique(['accountId', 'strategyId'])
export class AccountStrategy {
  @PrimaryGeneratedColumn('uuid')
  id: string;

  @Column({ type: 'uuid', name: 'account_id' })
  accountId: string;

  @Column({ type: 'uuid', name: 'strategy_id' })
  strategyId: string;

  @ManyToOne(() => TradingAccount, { onDelete: 'CASCADE' })
  @JoinColumn({ name: 'account_id' })
  account: TradingAccount;

  @ManyToOne(() => Strategy, { onDelete: 'CASCADE' })
  @JoinColumn({ name: 'strategy_id' })
  strategy: Strategy;

  @CreateDateColumn({ type: 'timestamptz', name: 'created_at' })
  createdAt: Date;
}
