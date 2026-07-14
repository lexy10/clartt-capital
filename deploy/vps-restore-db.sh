#!/usr/bin/env bash
# Restore a pg_dump backup into the running postgres container.
#
#   bash vps-restore-db.sh /opt/clartt/postgres-20260610-192538.sql.gz
#
# Run this ONCE on the VPS, after the first deploy has brought postgres
# up but before you trust the platform with live trading.

set -euo pipefail

BACKUP_FILE="${1:?usage: vps-restore-db.sh <path-to-postgres-*.sql.gz>}"

if [[ ! -f "${BACKUP_FILE}" ]]; then
  echo "ERROR: backup file not found: ${BACKUP_FILE}" >&2
  exit 1
fi

# Read postgres creds from the deployed .env. Don't bake them into the
# script — they should live in exactly one place.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}/.."
set -a; source ./.env; set +a

CONTAINER="clartt-capital-platform-postgres-1"

echo "→ Checking postgres container is running"
if ! docker ps --format '{{.Names}}' | grep -q "^${CONTAINER}$"; then
  echo "ERROR: ${CONTAINER} is not running. Start the stack first." >&2
  exit 1
fi

echo "→ Sanity check: current row counts (pre-restore)"
docker exec -i "${CONTAINER}" psql -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" -c \
  "SELECT 'users' AS table, COUNT(*) FROM users UNION ALL SELECT 'trading_accounts', COUNT(*) FROM trading_accounts;" 2>/dev/null || \
  echo "(tables don't exist yet — fresh DB, expected on first restore)"

echo "→ Restoring from ${BACKUP_FILE}"
echo "  (this includes DROP+CREATE; existing rows in matching tables will be wiped)"
read -rp "Continue? [y/N] " confirm
[[ "${confirm}" =~ ^[Yy]$ ]] || { echo "Aborted."; exit 1; }

gunzip -c "${BACKUP_FILE}" | docker exec -i "${CONTAINER}" psql -U "${POSTGRES_USER}" -d "${POSTGRES_DB}"

echo "→ Row counts after restore"
docker exec -i "${CONTAINER}" psql -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" -c \
  "SELECT 'users' AS table, COUNT(*) FROM users UNION ALL SELECT 'trading_accounts', COUNT(*) FROM trading_accounts;"

echo "→ Baselining TypeORM migrations history"
# The dump comes from a dev DB built by synchronize:true — its schema already
# contains everything these migrations create, but the migrations table is
# missing/empty. Without a baseline, the backend's migrationsRun would try to
# re-run all of them at boot and crash on duplicate objects. Mark the known
# migrations as applied; anything added later runs normally.
docker exec -i "${CONTAINER}" psql -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" <<'SQL'
CREATE TABLE IF NOT EXISTS migrations (
  id SERIAL PRIMARY KEY,
  timestamp BIGINT NOT NULL,
  name VARCHAR NOT NULL
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

echo "→ Restarting backend so migrationsRun sees the baselined history"
docker compose -f docker-compose.yml -f docker-compose.prod.yml restart backend 2>/dev/null || \
  docker restart clartt-capital-platform-backend-1

echo "✓ Restore complete."
