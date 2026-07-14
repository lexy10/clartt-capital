import { Repository } from 'typeorm';
import { Logger } from '@nestjs/common';
import * as bcrypt from 'bcrypt';
import { User } from '../auth/entities/user.entity';

const BCRYPT_ROUNDS = 10;

/**
 * Bootstrap a single admin user on boot from environment variables.
 *
 * There is no self-service registration endpoint, so a fresh deployment
 * with no restored dump would otherwise have zero users and no way to log
 * in. This seeds exactly one admin from ADMIN_EMAIL / ADMIN_PASSWORD.
 *
 * Behaviour:
 *  - If ANY admin already exists, do nothing (never touch existing users).
 *  - If ADMIN_EMAIL / ADMIN_PASSWORD are unset, log a warning and skip —
 *    the operator must set them (or restore a dump) to get in.
 *  - The password is bcrypt-hashed; the plaintext never lands in the DB.
 *
 * After first login, change the password and rotate the env var.
 */
export async function seedAdminUser(userRepo: Repository<User>): Promise<void> {
  const logger = new Logger('UsersSeed');

  const existingAdmin = await userRepo.findOne({ where: { role: 'admin' } });
  if (existingAdmin) {
    return;
  }

  const email = process.env.ADMIN_EMAIL?.trim();
  const password = process.env.ADMIN_PASSWORD;
  if (!email || !password) {
    logger.warn(
      'No admin user exists and ADMIN_EMAIL / ADMIN_PASSWORD are not set — ' +
      'nobody can log in. Set them in .env (or restore a DB dump) and restart.',
    );
    return;
  }

  // Guard against a duplicate email that isn't an admin (shouldn't happen on
  // a fresh DB, but avoids a unique-constraint crash).
  const emailTaken = await userRepo.findOne({ where: { email } });
  if (emailTaken) {
    logger.warn(`ADMIN_EMAIL ${email} already exists (role=${emailTaken.role}); not modifying it.`);
    return;
  }

  const passwordHash = await bcrypt.hash(password, BCRYPT_ROUNDS);
  await userRepo.save(userRepo.create({ email, passwordHash, role: 'admin' }));
  logger.log(`Bootstrapped admin user ${email}. Change this password after first login.`);
}
