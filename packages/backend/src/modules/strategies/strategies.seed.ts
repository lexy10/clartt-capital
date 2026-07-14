import { Repository } from 'typeorm';
import { Logger } from '@nestjs/common';
import { Strategy } from './entities/strategy.entity';
import { User } from '../auth/entities/user.entity';
import { STRATEGY_SEED_DATA } from './strategies.seed-data';

/**
 * Seed the global strategy catalogue on boot so a fresh deployment (no
 * full-dump restore) comes up with the real, tuned strategy configs.
 *
 * Idempotent by primary key: a strategy whose id already exists is left
 * untouched — so operator edits made through the dashboard survive restarts
 * and redeploys. Only genuinely-missing strategies are inserted.
 *
 * `created_by` is an audit field (strategies are global, not user-owned).
 * We attach the first admin if one exists; otherwise NULL (the column is
 * nullable). The admin bootstrap seed runs first, so on a fresh DB an admin
 * is normally present by the time this runs.
 */
export async function seedStrategies(
  strategyRepo: Repository<Strategy>,
  userRepo: Repository<User>,
): Promise<void> {
  const logger = new Logger('StrategiesSeed');

  // Prefer the superadmin (the platform owner); fall back to any admin.
  const admin =
    (await userRepo.findOne({ where: { role: 'superadmin' } })) ??
    (await userRepo.findOne({ where: { role: 'admin' } }));
  const createdBy = admin?.id ?? null;

  let created = 0;
  for (const seed of STRATEGY_SEED_DATA) {
    const exists = await strategyRepo.findOne({ where: { id: seed.id } });
    if (exists) continue;
    await strategyRepo.save(
      strategyRepo.create({
        id: seed.id,
        name: seed.name,
        algorithm: seed.algorithm,
        enabled: seed.enabled,
        config: seed.config,
        createdBy,
      }),
    );
    created++;
  }

  if (created) {
    logger.log(`Strategy seed: ${created} strategies created`);
  }
}
