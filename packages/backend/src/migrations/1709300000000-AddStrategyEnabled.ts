import { MigrationInterface, QueryRunner } from 'typeorm';

export class AddStrategyEnabled1709300000000 implements MigrationInterface {
  public async up(queryRunner: QueryRunner): Promise<void> {
    await queryRunner.query(
      `ALTER TABLE "strategies" ADD COLUMN IF NOT EXISTS "enabled" boolean NOT NULL DEFAULT true`,
    );
  }

  public async down(queryRunner: QueryRunner): Promise<void> {
    await queryRunner.query(
      `ALTER TABLE "strategies" DROP COLUMN IF EXISTS "enabled"`,
    );
  }
}
