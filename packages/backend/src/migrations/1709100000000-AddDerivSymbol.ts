import { MigrationInterface, QueryRunner } from 'typeorm';

export class AddDerivSymbol1709100000000 implements MigrationInterface {
  public async up(queryRunner: QueryRunner): Promise<void> {
    // Add deriv_symbol column to instruments table
    await queryRunner.query(`
      ALTER TABLE instruments
      ADD COLUMN IF NOT EXISTS deriv_symbol VARCHAR(50) DEFAULT NULL
    `);

    // Populate known Deriv symbols for existing instruments
    await queryRunner.query(`UPDATE instruments SET deriv_symbol = 'OTC_DJI' WHERE symbol = 'US30'`);
    await queryRunner.query(`UPDATE instruments SET deriv_symbol = 'frxXAUUSD' WHERE symbol = 'XAUUSD'`);
    await queryRunner.query(`UPDATE instruments SET deriv_symbol = 'R_75' WHERE symbol = 'R_75'`);
    await queryRunner.query(`UPDATE instruments SET deriv_symbol = 'R_25' WHERE symbol = 'R_25'`);
  }

  public async down(queryRunner: QueryRunner): Promise<void> {
    await queryRunner.query(`ALTER TABLE instruments DROP COLUMN IF EXISTS deriv_symbol`);
  }
}
