import { MigrationInterface, QueryRunner } from 'typeorm';

/**
 * Introduce the 'superadmin' role. Existing 'admin' users become 'superadmin'
 * so whoever ran the platform before (e.g. trader1) keeps user-management and
 * impersonation powers. New 'admin' users created afterwards are ops-level
 * only. Idempotent: safe if there are no admins.
 */
export class PromoteAdminToSuperadmin1785100000000 implements MigrationInterface {
  public async up(queryRunner: QueryRunner): Promise<void> {
    await queryRunner.query(
      `UPDATE "users" SET "role" = 'superadmin' WHERE "role" = 'admin'`,
    );
  }

  public async down(queryRunner: QueryRunner): Promise<void> {
    await queryRunner.query(
      `UPDATE "users" SET "role" = 'admin' WHERE "role" = 'superadmin'`,
    );
  }
}
