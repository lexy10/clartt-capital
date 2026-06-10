import { MigrationInterface, QueryRunner } from 'typeorm';

export class AddReconciliationTables1774828800000 implements MigrationInterface {
  public async up(queryRunner: QueryRunner): Promise<void> {
    await queryRunner.query(`
      CREATE TABLE IF NOT EXISTS reconciliation_reports (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        account_id UUID NOT NULL REFERENCES trading_accounts(id) ON DELETE CASCADE,
        cycle_timestamp TIMESTAMPTZ NOT NULL,
        discrepancies JSONB NOT NULL,
        auto_corrections_applied JSONB NOT NULL,
        broker_state_snapshot JSONB NOT NULL,
        local_state_snapshot JSONB NOT NULL,
        duration_ms INTEGER NOT NULL,
        status VARCHAR(30) NOT NULL,
        error_message TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
      )
    `);

    await queryRunner.query(`
      CREATE INDEX idx_reconciliation_reports_account_id ON reconciliation_reports(account_id)
    `);

    await queryRunner.query(`
      CREATE INDEX idx_reconciliation_reports_cycle_timestamp ON reconciliation_reports(cycle_timestamp)
    `);

    await queryRunner.query(`
      CREATE INDEX idx_reconciliation_reports_status ON reconciliation_reports(status)
    `);

    await queryRunner.query(`
      CREATE TABLE IF NOT EXISTS reconciliation_configs (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        account_id UUID UNIQUE REFERENCES trading_accounts(id) ON DELETE CASCADE,
        reconciliation_interval_seconds INTEGER NOT NULL DEFAULT 60,
        balance_drift_threshold DECIMAL(18,2) NOT NULL DEFAULT 10.00,
        equity_drift_threshold DECIMAL(18,2) NOT NULL DEFAULT 50.00,
        position_size_drift_threshold DECIMAL(18,4) NOT NULL DEFAULT 0.01,
        auto_correct_phantom_positions BOOLEAN NOT NULL DEFAULT false,
        auto_correct_missing_positions BOOLEAN NOT NULL DEFAULT false,
        auto_correct_balance_drift BOOLEAN NOT NULL DEFAULT false,
        escalation_cycle_count INTEGER NOT NULL DEFAULT 3,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
      )
    `);
  }

  public async down(queryRunner: QueryRunner): Promise<void> {
    await queryRunner.query(`DROP TABLE IF EXISTS reconciliation_configs`);
    await queryRunner.query(`DROP TABLE IF EXISTS reconciliation_reports`);
  }
}
