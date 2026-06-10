import { MigrationInterface, QueryRunner } from 'typeorm';

export class AddBacktestTrades1709500000000 implements MigrationInterface {
  public async up(queryRunner: QueryRunner): Promise<void> {
    await queryRunner.query(`
      CREATE TABLE IF NOT EXISTS backtest_trades (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        backtest_result_id UUID NOT NULL REFERENCES backtest_results(id) ON DELETE CASCADE,
        signal_id VARCHAR(255) NOT NULL,
        direction VARCHAR(10) NOT NULL,
        entry_price DECIMAL(18,8) NOT NULL,
        exit_price DECIMAL(18,8) NOT NULL,
        stop_loss DECIMAL(18,8),
        take_profit DECIMAL(18,8),
        position_size DECIMAL(18,8) NOT NULL,
        profit_loss DECIMAL(18,8) NOT NULL,
        entry_time TIMESTAMPTZ NOT NULL,
        exit_time TIMESTAMPTZ NOT NULL,
        trade_index INTEGER NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
      )
    `);

    await queryRunner.query(`
      CREATE INDEX idx_backtest_trades_result_id ON backtest_trades(backtest_result_id)
    `);

    await queryRunner.query(`
      CREATE INDEX idx_backtest_trades_entry_time ON backtest_trades(entry_time)
    `);
  }

  public async down(queryRunner: QueryRunner): Promise<void> {
    await queryRunner.query(`DROP TABLE IF EXISTS backtest_trades`);
  }
}
