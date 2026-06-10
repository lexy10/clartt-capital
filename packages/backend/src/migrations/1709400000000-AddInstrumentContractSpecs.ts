import { MigrationInterface, QueryRunner } from 'typeorm';

export class AddInstrumentContractSpecs1709400000000 implements MigrationInterface {
  public async up(queryRunner: QueryRunner): Promise<void> {
    await queryRunner.query(`
      ALTER TABLE instruments
        ADD COLUMN IF NOT EXISTS contract_size DECIMAL(16,6) NOT NULL DEFAULT 1,
        ADD COLUMN IF NOT EXISTS pip_size DECIMAL(16,8) NOT NULL DEFAULT 0.01,
        ADD COLUMN IF NOT EXISTS pip_value DECIMAL(16,6) NOT NULL DEFAULT 1,
        ADD COLUMN IF NOT EXISTS min_lot DECIMAL(10,4) NOT NULL DEFAULT 0.01,
        ADD COLUMN IF NOT EXISTS lot_step DECIMAL(10,4) NOT NULL DEFAULT 0.01,
        ADD COLUMN IF NOT EXISTS leverage INT NOT NULL DEFAULT 100
    `);

    // Set sensible defaults per instrument
    // XAUUSD: 100oz contract, pip=0.01, pip_value=$1/pip/lot, min_lot=0.01, leverage 100:1
    await queryRunner.query(`
      UPDATE instruments SET
        contract_size = 100, pip_size = 0.01, pip_value = 1.0,
        min_lot = 0.01, lot_step = 0.01, leverage = 100
      WHERE symbol = 'XAUUSD'
    `);

    // US30: 1 contract, pip=1.0, pip_value=$1/pip/lot, min_lot=0.01, leverage 100:1
    await queryRunner.query(`
      UPDATE instruments SET
        contract_size = 1, pip_size = 1.0, pip_value = 1.0,
        min_lot = 0.01, lot_step = 0.01, leverage = 100
      WHERE symbol = 'US30'
    `);

    // R_75: 1 contract, pip=0.001, pip_value=$1/pip/lot, min_lot=0.01, leverage 500:1
    await queryRunner.query(`
      UPDATE instruments SET
        contract_size = 1, pip_size = 0.001, pip_value = 1.0,
        min_lot = 0.01, lot_step = 0.01, leverage = 500
      WHERE symbol = 'R_75'
    `);

    // R_25: 1 contract, pip=0.001, pip_value=$1/pip/lot, min_lot=0.01, leverage 500:1
    await queryRunner.query(`
      UPDATE instruments SET
        contract_size = 1, pip_size = 0.001, pip_value = 1.0,
        min_lot = 0.01, lot_step = 0.01, leverage = 500
      WHERE symbol = 'R_25'
    `);
  }

  public async down(queryRunner: QueryRunner): Promise<void> {
    await queryRunner.query(`
      ALTER TABLE instruments
        DROP COLUMN IF EXISTS contract_size,
        DROP COLUMN IF EXISTS pip_size,
        DROP COLUMN IF EXISTS pip_value,
        DROP COLUMN IF EXISTS min_lot,
        DROP COLUMN IF EXISTS lot_step,
        DROP COLUMN IF EXISTS leverage
    `);
  }
}
