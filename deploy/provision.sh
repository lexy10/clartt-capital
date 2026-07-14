#!/usr/bin/env bash
# One-command, idempotent provisioning of a FRESH VPS — run from your laptop.
#
#   bash deploy/provision.sh <host> [ssh_user] [deploy_path]
#   e.g. bash deploy/provision.sh 203.0.113.10 root /opt/clartt
#
# What it does (all idempotent — safe to re-run):
#   1. Generates a dedicated deploy SSH key (~/.ssh/clartt_deploy) if missing
#   2. Authorizes that key on the VPS (so GitHub Actions can SSH in later)
#   3. Copies vps-bootstrap.sh + schema.sql to the VPS and runs bootstrap
#      (installs Docker, creates the deploy dir, configures the firewall)
#
# PREREQUISITES you must have done:
#   - Ordered the VPS and can SSH in as <ssh_user> (password or Contabo's
#     initial root key). This script uses your CURRENT ssh access to install
#     the dedicated deploy key.
#
# AFTER this: run deploy/setup-secrets.sh to push the GitHub secrets, then
# push to main.

set -euo pipefail

HOST="${1:?usage: provision.sh <host> [ssh_user] [deploy_path]}"
SSH_USER="${2:-root}"
DEPLOY_PATH="${3:-/opt/clartt}"
KEY="${HOME}/.ssh/clartt_deploy"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET="${SSH_USER}@${HOST}"

echo "→ [1/3] Deploy key"
if [[ -f "${KEY}" ]]; then
  echo "  ${KEY} already exists — reusing"
else
  ssh-keygen -t ed25519 -f "${KEY}" -N "" -C "clartt github-actions deploy"
  echo "  generated ${KEY}"
fi

echo "→ [2/3] Authorizing deploy key on ${TARGET}"
# ssh-copy-id is idempotent (won't duplicate the key). Falls back to manual
# append if ssh-copy-id isn't installed.
if command -v ssh-copy-id >/dev/null 2>&1; then
  ssh-copy-id -i "${KEY}.pub" "${TARGET}"
else
  ssh "${TARGET}" "mkdir -p ~/.ssh && chmod 700 ~/.ssh && grep -qxF '$(cat "${KEY}.pub")' ~/.ssh/authorized_keys 2>/dev/null || echo '$(cat "${KEY}.pub")' >> ~/.ssh/authorized_keys"
fi

echo "→ [3/3] Bootstrapping the VPS (Docker + dir + firewall)"
# Copy the scripts the VPS needs, then run bootstrap. schema.sql goes too so
# the deploy can self-init the DB later.
ssh "${TARGET}" "mkdir -p '${DEPLOY_PATH}/deploy'"
scp "${SCRIPT_DIR}/vps-bootstrap.sh" "${SCRIPT_DIR}/schema.sql" \
    "${SCRIPT_DIR}/vps-init-schema.sh" "${SCRIPT_DIR}/vps-restore-db.sh" \
    "${SCRIPT_DIR}/vps-backup-db.sh" \
    "${TARGET}:${DEPLOY_PATH}/deploy/" 2>/dev/null || \
  scp "${SCRIPT_DIR}/vps-bootstrap.sh" "${TARGET}:${DEPLOY_PATH}/deploy/"
# Run bootstrap using the dedicated key (proves it works end-to-end).
ssh -i "${KEY}" "${TARGET}" "bash '${DEPLOY_PATH}/deploy/vps-bootstrap.sh' '${DEPLOY_PATH}'"

cat <<EOF

✓ VPS provisioned (idempotent — re-run any time).

NEXT:
  1. Push the GitHub secrets:
       bash deploy/setup-secrets.sh ${HOST} ${SSH_USER} ${DEPLOY_PATH}
  2. Commit + push to main → the deploy runs and self-bootstraps the DB.
  3. Point DNS (A record for CLARTT_DOMAIN → ${HOST}) and bring up TLS.

If step 3 of bootstrap said to re-run after adding the docker group, do that:
  bash deploy/provision.sh ${HOST} ${SSH_USER} ${DEPLOY_PATH}
EOF
