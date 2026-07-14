import { Module, OnApplicationBootstrap, Logger } from '@nestjs/common';
import { TypeOrmModule, InjectRepository } from '@nestjs/typeorm';
import { Repository } from 'typeorm';
import { User } from '../../modules/auth/entities/user.entity';
import { Instrument } from '../../modules/instruments/entities/instrument.entity';
import { Strategy } from '../../modules/strategies/entities/strategy.entity';
import { seedAdminUser } from '../../modules/users/users.seed';
import { seedInstruments } from '../../modules/instruments/instruments.seed';
import { seedStrategies } from '../../modules/strategies/strategies.seed';

/**
 * Central boot-time seeder. Runs on onApplicationBootstrap — AFTER every
 * module's onModuleInit and after TypeORM migrations — so the schema is in
 * place and ordering across seeds is deterministic:
 *
 *   1. admin user   (so strategies can attach created_by)
 *   2. instruments  (so candle backfill has derivSymbol-mapped instruments)
 *   3. strategies   (the tuned catalogue)
 *
 * Every seed is idempotent, so this is safe on every boot — a fresh DB gets
 * populated, an existing one is left alone. This is what lets a remote deploy
 * come up WITHOUT restoring a full dump: migrations build the schema, these
 * seeds add users/instruments/strategies, and the candle backfill
 * (backfill.service.ts, 10s after boot) pulls 12 months of history from Deriv.
 */
@Module({
  imports: [TypeOrmModule.forFeature([User, Instrument, Strategy])],
})
export class SeedModule implements OnApplicationBootstrap {
  private readonly logger = new Logger('SeedModule');

  constructor(
    @InjectRepository(User) private readonly userRepo: Repository<User>,
    @InjectRepository(Instrument) private readonly instrumentRepo: Repository<Instrument>,
    @InjectRepository(Strategy) private readonly strategyRepo: Repository<Strategy>,
  ) {}

  async onApplicationBootstrap(): Promise<void> {
    try {
      await seedAdminUser(this.userRepo);
      await seedInstruments(this.instrumentRepo);
      await seedStrategies(this.strategyRepo, this.userRepo);
    } catch (err) {
      // A seed failure shouldn't crash the whole app — log loudly and carry
      // on. A half-seeded DB is recoverable; a crash-loop on boot is worse.
      this.logger.error(`Seeding failed: ${(err as Error).message}`, (err as Error).stack);
    }
  }
}
