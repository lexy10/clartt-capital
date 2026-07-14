#!/usr/bin/env bash
# Bootstrap a FRESH database schema (no data) on the VPS, then baseline the
# TypeORM migrations so the backend's migrationsRun is a no-op on first boot.
#
#   bash vps-init-schema.sh /opt/clartt
#
# Use this for a clean seed-based deploy (recommended) INSTEAD of restoring a
# full dump. After this runs and the backend boots, the boot seeds populate
# users / instruments / strategies, and the candle backfill pulls history
# from Deriv. See README.md "Fresh deploy (seed-based)".
#
# WHY a schema dump and not migrations: the incremental migrations assume a
# schema that `synchronize` originally built — the first one ALTERs the
# instruments table. They are not a from-scratch DDL, so a truly empty DB
# can't be built by migrations alone. deploy/schema.sql is the full DDL
# snapshot; migrations then handle future increments.

set -euo pipefail

DEPLOY_PATH="${1:-/opt/clartt}"
SCHEMA_FILE="${DEPLOY_PATH}/deploy/schema.sql"
CONTAINER="clartt-capital-platform-postgres-1"

set -a; source "${DEPLOY_PATH}/.env"; set +a

if [[ ! -f "${SCHEMA_FILE}" ]]; then
  echo "ERROR: ${SCHEMA_FILE} not found (did the deploy rsync deploy/?)." >&2
  exit 1
fi

echo "→ Checking the DB is empty (refuse to clobber existing data)"
TABLE_COUNT=$(docker exec -i "${CONTAINER}" psql -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" -t -A -c \
  "SELECT count(*) FROM information_schema.tables WHERE table_schema='public';" 2>/dev/null || echo "0")
if [[ "${TABLE_COUNT}" != "0" ]]; then
  echo "ERROR: database already has ${TABLE_COUNT} tables. This script is for a FRESH DB only." >&2
  echo "       If you meant to restore data, use vps-restore-db.sh instead." >&2
  exit 1
fi

echo "→ Loading schema (${SCHEMA_FILE})"
docker exec -i "${CONTAINER}" psql -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" < "${SCHEMA_FILE}"

echo "→ Baselining TypeORM migrations (mark all current migrations applied)"
docker exec -i "${CONTAINER}" psql -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" <<'SQL'
CREATE TABLE IF NOT EXISTS migrations (
  id SERIAL PRIMARY KEY, timestamp BIGINT NOT NULL, name VARCHAR NOT NULL
);
INSERT INTO migrations (timestamp, name)
SELECT v.ts, v.name FROM (VALUES
  (1709000000000::bigint, 'DropBrokerAliases1709000000000'),
  (1709100000000::bigint, 'AddDerivSymbol1709100000000'),
  (1709200000000::bigint, 'AddCandleCompleted1709200000000'),
  (1709300000000::bigint, 'AddStrategyEnabled1709300000000'),
  (1709400000000::bigint, 'AddInstrumentContractSpecs1709400000000'),
  (1709500000000::bigint, 'AddBacktestTrades1709500000000'),
  (1774828800000::bigint, 'AddReconciliationTables1774828800000'),
  (1774900000000::bigint, 'AddTradingEventsTables1774900000000'),
  (1774968678384::bigint, 'AddAgentTables1774968678384')
) AS v(ts, name)
WHERE NOT EXISTS (SELECT 1 FROM migrations m WHERE m.name = v.name);
SQL

cat <<EOF

✓ Schema bootstrapped and migrations baselined.

NEXT: make sure ADMIN_EMAIL / ADMIN_PASSWORD are set in ${DEPLOY_PATH}/.env,
then (re)start the backend. On boot it will:
  - run migrations (no-op — baselined)
  - seed the admin user, instruments (with derivSymbol), and strategies
  - start the candle backfill (12 months from Deriv, ~a few minutes)

  docker compose -f docker-compose.yml -f docker-compose.prod.yml restart backend
EOF
