import { MigrationInterface, QueryRunner } from 'typeorm';

export class AddCandleCompleted1709200000000 implements MigrationInterface {
  public async up(queryRunner: QueryRunner): Promise<void> {
    await queryRunner.query(
      `ALTER TABLE candles ADD COLUMN IF NOT EXISTS completed boolean NOT NULL DEFAULT false`,
    );
  }

  public async down(queryRunner: QueryRunner): Promise<void> {
    await queryRunner.query(`ALTER TABLE candles DROP COLUMN IF EXISTS completed`);
  }
}
