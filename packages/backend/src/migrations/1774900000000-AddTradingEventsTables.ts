import { MigrationInterface, QueryRunner } from 'typeorm';

export class AddTradingEventsTables1774900000000 implements MigrationInterface {
  public async up(queryRunner: QueryRunner): Promise<void> {
    // Create trading_events table
    await queryRunner.query(`
      CREATE TABLE IF NOT EXISTS trading_events (
        id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        event_type      VARCHAR(50) NOT NULL,
        aggregate_id    VARCHAR(255) NOT NULL,
        sequence_number INTEGER NOT NULL,
        correlation_id  VARCHAR(255),
        payload         JSONB NOT NULL,
        context_snapshot JSONB,
        source_service  VARCHAR(50) NOT NULL,
        created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        schema_version  INTEGER NOT NULL DEFAULT 1,
        CONSTRAINT uq_aggregate_sequence UNIQUE (aggregate_id, sequence_number)
      )
    `);

    // Indexes on trading_events
    await queryRunner.query(
      `CREATE INDEX idx_trading_events_aggregate_id ON trading_events (aggregate_id)`,
    );
    await queryRunner.query(
      `CREATE INDEX idx_trading_events_correlation_id ON trading_events (correlation_id)`,
    );
    await queryRunner.query(
      `CREATE INDEX idx_trading_events_event_type ON trading_events (event_type)`,
    );
    await queryRunner.query(
      `CREATE INDEX idx_trading_events_source_service ON trading_events (source_service)`,
    );
    await queryRunner.query(
      `CREATE INDEX idx_trading_events_created_at ON trading_events (created_at)`,
    );

    // Append-only trigger: reject UPDATE and DELETE
    await queryRunner.query(
      'CREATE OR REPLACE FUNCTION prevent_event_mutation() RETURNS TRIGGER AS $func$ ' +
      'BEGIN ' +
        "RAISE EXCEPTION 'trading_events is append-only: % operations are not allowed', TG_OP; " +
        'RETURN NULL; ' +
      'END; ' +
      '$func$ LANGUAGE plpgsql',
    );

    await queryRunner.query(
      `CREATE TRIGGER trg_prevent_event_mutation
        BEFORE UPDATE OR DELETE ON trading_events
        FOR EACH ROW EXECUTE FUNCTION prevent_event_mutation()`,
    );

    // Create trading_events_archive table
    await queryRunner.query(`
      CREATE TABLE IF NOT EXISTS trading_events_archive (
        id              UUID PRIMARY KEY,
        event_type      VARCHAR(50) NOT NULL,
        aggregate_id    VARCHAR(255) NOT NULL,
        sequence_number INTEGER NOT NULL,
        correlation_id  VARCHAR(255),
        payload         JSONB NOT NULL,
        context_snapshot JSONB,
        source_service  VARCHAR(50) NOT NULL,
        created_at      TIMESTAMPTZ NOT NULL,
        schema_version  INTEGER NOT NULL DEFAULT 1,
        CONSTRAINT uq_archive_aggregate_sequence UNIQUE (aggregate_id, sequence_number)
      )
    `);

    // Indexes on trading_events_archive
    await queryRunner.query(
      `CREATE INDEX idx_archive_events_aggregate_id ON trading_events_archive (aggregate_id)`,
    );
    await queryRunner.query(
      `CREATE INDEX idx_archive_events_created_at ON trading_events_archive (created_at)`,
    );
    await queryRunner.query(
      `CREATE INDEX idx_archive_events_event_type ON trading_events_archive (event_type)`,
    );
  }

  public async down(queryRunner: QueryRunner): Promise<void> {
    await queryRunner.query(`DROP TABLE IF EXISTS trading_events_archive`);
    await queryRunner.query(`DROP TRIGGER IF EXISTS trg_prevent_event_mutation ON trading_events`);
    await queryRunner.query(`DROP FUNCTION IF EXISTS prevent_event_mutation`);
    await queryRunner.query(`DROP TABLE IF EXISTS trading_events`);
  }
}
