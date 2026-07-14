#!/usr/bin/env bash
# Nightly postgres backup with rotation. Install on the VPS via cron:
#
#   crontab -e
#   # nightly at 03:15, keep logs
#   15 3 * * * /opt/clartt/deploy/vps-backup-db.sh /opt/clartt >> /opt/clartt/backups/backup.log 2>&1
#
# Keeps the last 14 daily dumps locally. Copy them off-box too — a backup
# that lives only on the VPS dies with the VPS:
#   rsync -az <vps>:/opt/clartt/backups/ ~/clartt-backups/vps/   (from laptop)

set -euo pipefail

DEPLOY_PATH="${1:-/opt/clartt}"
BACKUP_DIR="${DEPLOY_PATH}/backups"
KEEP_DAYS=14
CONTAINER="clartt-capital-platform-postgres-1"

mkdir -p "${BACKUP_DIR}"

# Postgres creds come from the deployed .env — single source of truth.
set -a; source "${DEPLOY_PATH}/.env"; set +a

STAMP="$(date +%Y%m%d-%H%M%S)"
OUT="${BACKUP_DIR}/postgres-${STAMP}.sql.gz"

echo "[$(date -Is)] dumping ${POSTGRES_DB} → ${OUT}"
docker exec -i "${CONTAINER}" pg_dump -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" --clean --if-exists \
  | gzip > "${OUT}.tmp"
mv "${OUT}.tmp" "${OUT}"   # atomic — a killed dump never leaves a half-written "backup"

SIZE=$(du -h "${OUT}" | cut -f1)
echo "[$(date -Is)] done (${SIZE})"

# Rotate: delete dumps older than KEEP_DAYS
find "${BACKUP_DIR}" -name "postgres-*.sql.gz" -mtime +${KEEP_DAYS} -delete

# Sanity: a dump under 1KB almost certainly means pg_dump failed silently
if [[ $(stat -f%z "${OUT}" 2>/dev/null || stat -c%s "${OUT}") -lt 1024 ]]; then
  echo "[$(date -Is)] WARNING: dump is suspiciously small (${SIZE}) — investigate" >&2
  exit 1
fi
