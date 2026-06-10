import { MigrationInterface, QueryRunner } from 'typeorm';

export class DropBrokerAliases1709000000000 implements MigrationInterface {
  public async up(queryRunner: QueryRunner): Promise<void> {
    await queryRunner.query(
      `ALTER TABLE instruments DROP COLUMN IF EXISTS broker_aliases`,
    );
  }

  public async down(queryRunner: QueryRunner): Promise<void> {
    await queryRunner.query(
      `ALTER TABLE instruments ADD COLUMN broker_aliases jsonb NOT NULL DEFAULT '[]'`,
    );
  }
}
