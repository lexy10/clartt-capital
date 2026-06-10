import { MigrationInterface, QueryRunner } from 'typeorm';

export class AddAgentTables1774968678384 implements MigrationInterface {
  public async up(queryRunner: QueryRunner): Promise<void> {
    // agent_states: persists agent lifecycle state
    await queryRunner.query(`
      CREATE TABLE IF NOT EXISTS agent_states (
        id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        agent_name      VARCHAR(100) NOT NULL UNIQUE,
        current_state   VARCHAR(30) NOT NULL DEFAULT 'IDLE',
        state_history   JSONB NOT NULL DEFAULT '[]',
        last_heartbeat  TIMESTAMPTZ,
        current_task_id VARCHAR(255),
        error_count     INTEGER NOT NULL DEFAULT 0,
        failure_reason  TEXT,
        failure_stack_trace TEXT,
        created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
      )
    `);

    // agent_tasks: task queue history and results
    await queryRunner.query(`
      CREATE TABLE IF NOT EXISTS agent_tasks (
        id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        type            VARCHAR(100) NOT NULL,
        agent_name      VARCHAR(100) NOT NULL,
        priority        INTEGER NOT NULL DEFAULT 2,
        payload         JSONB NOT NULL DEFAULT '{}',
        result          JSONB,
        status          VARCHAR(30) NOT NULL DEFAULT 'PENDING',
        depends_on      JSONB NOT NULL DEFAULT '[]',
        retry_count     INTEGER NOT NULL DEFAULT 0,
        max_retries     INTEGER NOT NULL DEFAULT 3,
        error_message   TEXT,
        task_timeout_seconds INTEGER NOT NULL DEFAULT 600,
        created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        started_at      TIMESTAMPTZ,
        completed_at    TIMESTAMPTZ
      )
    `);

    // agent_conversations: per-agent conversation history
    await queryRunner.query(`
      CREATE TABLE IF NOT EXISTS agent_conversations (
        id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        agent_name      VARCHAR(100) NOT NULL,
        task_id         VARCHAR(255),
        role            VARCHAR(20) NOT NULL,
        content         TEXT,
        tool_calls      JSONB,
        created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
      )
    `);

    // agent_decisions: per-agent decision records
    await queryRunner.query(`
      CREATE TABLE IF NOT EXISTS agent_decisions (
        id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        agent_name      VARCHAR(100) NOT NULL,
        task_id         VARCHAR(255),
        decision_type   VARCHAR(100) NOT NULL,
        input_summary   TEXT,
        output_summary  TEXT,
        reasoning       TEXT,
        outcome         VARCHAR(20),
        created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
      )
    `);

    // agent_knowledge: shared knowledge base
    await queryRunner.query(`
      CREATE TABLE IF NOT EXISTS agent_knowledge (
        id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        key             VARCHAR(255) NOT NULL,
        value           JSONB NOT NULL,
        source_agent    VARCHAR(100) NOT NULL,
        tags            TEXT[] DEFAULT '{}',
        created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
      )
    `);

    // agent_pipelines: pipeline state
    await queryRunner.query(`
      CREATE TABLE IF NOT EXISTS agent_pipelines (
        id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        name            VARCHAR(255) NOT NULL,
        stages          JSONB NOT NULL,
        current_stage_index INTEGER NOT NULL DEFAULT 0,
        status          VARCHAR(30) NOT NULL DEFAULT 'PENDING',
        created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
      )
    `);

    // agent_llm_usage: LLM cost tracking
    await queryRunner.query(`
      CREATE TABLE IF NOT EXISTS agent_llm_usage (
        id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        agent_name      VARCHAR(100) NOT NULL,
        provider        VARCHAR(50) NOT NULL,
        model           VARCHAR(100) NOT NULL,
        prompt_tokens   INTEGER NOT NULL,
        completion_tokens INTEGER NOT NULL,
        estimated_cost_usd NUMERIC(10, 6) NOT NULL,
        task_id         VARCHAR(255),
        created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
      )
    `);

    // agent_approvals: human-in-the-loop approvals
    await queryRunner.query(`
      CREATE TABLE IF NOT EXISTS agent_approvals (
        id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        agent_name      VARCHAR(100) NOT NULL,
        task_id         VARCHAR(255),
        action_description TEXT NOT NULL,
        status          VARCHAR(20) NOT NULL DEFAULT 'PENDING',
        resolved_by     VARCHAR(255),
        resolution_reason TEXT,
        created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        resolved_at     TIMESTAMPTZ
      )
    `);

    // --- Indexes ---
    await queryRunner.query(`CREATE INDEX idx_agent_tasks_agent_name ON agent_tasks(agent_name)`);
    await queryRunner.query(`CREATE INDEX idx_agent_tasks_status ON agent_tasks(status)`);
    await queryRunner.query(`CREATE INDEX idx_agent_tasks_created_at ON agent_tasks(created_at)`);
    await queryRunner.query(`CREATE INDEX idx_agent_conversations_agent_task ON agent_conversations(agent_name, task_id)`);
    await queryRunner.query(`CREATE INDEX idx_agent_decisions_agent_type ON agent_decisions(agent_name, decision_type)`);
    await queryRunner.query(`CREATE INDEX idx_agent_knowledge_source ON agent_knowledge(source_agent)`);
    await queryRunner.query(`CREATE INDEX idx_agent_knowledge_tags ON agent_knowledge USING GIN(tags)`);
    await queryRunner.query(`CREATE INDEX idx_agent_pipelines_status ON agent_pipelines(status)`);
    await queryRunner.query(`CREATE INDEX idx_agent_llm_usage_agent_created ON agent_llm_usage(agent_name, created_at)`);
    await queryRunner.query(`CREATE INDEX idx_agent_approvals_status ON agent_approvals(status)`);
  }

  public async down(queryRunner: QueryRunner): Promise<void> {
    // Drop indexes (automatically dropped with tables, but explicit for clarity)
    await queryRunner.query(`DROP INDEX IF EXISTS idx_agent_approvals_status`);
    await queryRunner.query(`DROP INDEX IF EXISTS idx_agent_llm_usage_agent_created`);
    await queryRunner.query(`DROP INDEX IF EXISTS idx_agent_pipelines_status`);
    await queryRunner.query(`DROP INDEX IF EXISTS idx_agent_knowledge_tags`);
    await queryRunner.query(`DROP INDEX IF EXISTS idx_agent_knowledge_source`);
    await queryRunner.query(`DROP INDEX IF EXISTS idx_agent_decisions_agent_type`);
    await queryRunner.query(`DROP INDEX IF EXISTS idx_agent_conversations_agent_task`);
    await queryRunner.query(`DROP INDEX IF EXISTS idx_agent_tasks_created_at`);
    await queryRunner.query(`DROP INDEX IF EXISTS idx_agent_tasks_status`);
    await queryRunner.query(`DROP INDEX IF EXISTS idx_agent_tasks_agent_name`);

    // Drop tables in reverse order
    await queryRunner.query(`DROP TABLE IF EXISTS agent_approvals`);
    await queryRunner.query(`DROP TABLE IF EXISTS agent_llm_usage`);
    await queryRunner.query(`DROP TABLE IF EXISTS agent_pipelines`);
    await queryRunner.query(`DROP TABLE IF EXISTS agent_knowledge`);
    await queryRunner.query(`DROP TABLE IF EXISTS agent_decisions`);
    await queryRunner.query(`DROP TABLE IF EXISTS agent_conversations`);
    await queryRunner.query(`DROP TABLE IF EXISTS agent_tasks`);
    await queryRunner.query(`DROP TABLE IF EXISTS agent_states`);
  }
}
