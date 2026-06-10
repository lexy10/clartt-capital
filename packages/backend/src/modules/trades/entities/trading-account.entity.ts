import {
  Entity,
  PrimaryGeneratedColumn,
  Column,
  CreateDateColumn,
  ManyToOne,
  JoinColumn,
  Unique,
} from 'typeorm';
import { User } from '../../auth/entities/user.entity';

@Entity('trading_accounts')
@Unique(['userId', 'mt5Login', 'mt5Server'])
export class TradingAccount {
  @PrimaryGeneratedColumn('uuid')
  id: string;

  @Column({ type: 'uuid', name: 'user_id' })
  userId: string;

  @ManyToOne(() => User, { onDelete: 'CASCADE' })
  @JoinColumn({ name: 'user_id' })
  user: User;

  /** MetaAPI account ID — nullable for Deriv-direct accounts that don't use MT5. */
  @Column({ type: 'varchar', length: 255, name: 'metaapi_account_id', nullable: true })
  metaapiAccountId: string | null;

  /** Deriv API token for direct WebSocket trading. Only set when brokerProvider='deriv'. */
  @Column({ type: 'varchar', length: 500, name: 'deriv_api_token', nullable: true })
  derivApiToken: string | null;

  /** Deriv login ID (e.g. "CR1234567"). */
  @Column({ type: 'varchar', length: 50, name: 'deriv_login_id', nullable: true })
  derivLoginId: string | null;

  @Column({ type: 'varchar', length: 255, nullable: true })
  label: string | null;

  @Column({ type: 'boolean', name: 'is_active', default: true })
  isActive: boolean;

  @Column({ type: 'varchar', length: 50, name: 'mt5_login', nullable: true })
  mt5Login: string | null;

  @Column({ type: 'varchar', length: 255, name: 'mt5_server', nullable: true })
  mt5Server: string | null;

  /** Account funding type — drives risk settings and prop-firm enforcement. */
  @Column({ type: 'varchar', length: 20, name: 'account_kind', default: 'personal' })
  accountKind: 'personal' | 'prop' | 'demo';

  /** Broker provider override — if set, all trades on this account route here
   *  regardless of instrument category. Leave null to use category routing. */
  @Column({ type: 'varchar', length: 20, name: 'broker_provider', nullable: true })
  brokerProvider:
    | 'deriv'
    | 'metaapi'
    | 'alpaca'
    | 'binance'
    | 'ibkr'
    | 'stub'
    | null;

  // ── Prop firm specific (null on personal/demo accounts) ───────────────

  @Column({ type: 'varchar', length: 100, name: 'prop_firm_name', nullable: true })
  propFirmName: string | null;

  /** Prop firm daily loss limit (e.g. 5.00 = 5%). */
  @Column({ type: 'decimal', precision: 5, scale: 2, name: 'prop_max_daily_loss_pct', nullable: true })
  propMaxDailyLossPct: number | null;

  /** Prop firm total drawdown limit (e.g. 10.00 = 10%). */
  @Column({ type: 'decimal', precision: 5, scale: 2, name: 'prop_max_total_drawdown_pct', nullable: true })
  propMaxTotalDrawdownPct: number | null;

  /** Prop firm profit target (e.g. 8.00 = 8% for phase 1). */
  @Column({ type: 'decimal', precision: 5, scale: 2, name: 'prop_profit_target_pct', nullable: true })
  propProfitTargetPct: number | null;

  @CreateDateColumn({ type: 'timestamptz', name: 'created_at' })
  createdAt: Date;
}
