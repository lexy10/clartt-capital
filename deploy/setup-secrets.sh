#!/usr/bin/env bash
# Push the GitHub Actions secrets the deploy workflow needs — run from your
# laptop, from inside the repo. Idempotent (gh secret set overwrites).
#
#   bash deploy/setup-secrets.sh <host> [ssh_user] [deploy_path]
#   e.g. bash deploy/setup-secrets.sh 203.0.113.10 root /opt/clartt
#
# Sets: VPS_HOST, VPS_USER, VPS_PATH, VPS_SSH_KEY (private deploy key),
#       VPS_ENV_FILE (contents of your local .env).
#
# PREREQUISITES:
#   - GitHub CLI installed and authenticated:  gh auth status
#   - deploy/provision.sh already run (so ~/.ssh/clartt_deploy exists)
#   - a local .env with the real production values

set -euo pipefail

HOST="${1:?usage: setup-secrets.sh <host> [ssh_user] [deploy_path]}"
SSH_USER="${2:-root}"
DEPLOY_PATH="${3:-/opt/clartt}"
KEY="${HOME}/.ssh/clartt_deploy"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${REPO_ROOT}/.env"

command -v gh >/dev/null 2>&1 || { echo "ERROR: GitHub CLI (gh) not installed." >&2; exit 1; }
gh auth status >/dev/null 2>&1 || { echo "ERROR: run 'gh auth login' first." >&2; exit 1; }
[[ -f "${KEY}" ]] || { echo "ERROR: ${KEY} missing — run deploy/provision.sh first." >&2; exit 1; }
[[ -f "${ENV_FILE}" ]] || { echo "ERROR: ${ENV_FILE} missing." >&2; exit 1; }

# Warn if the .env still has obvious placeholders — deploying these would break.
if grep -qE 'change-me|change-in-production|yourdomain\.com' "${ENV_FILE}"; then
  echo "WARNING: ${ENV_FILE} still contains placeholder values (change-me / yourdomain.com)." >&2
  echo "         Fix them before the secrets go live, or the deploy will ship them." >&2
  read -rp "Continue anyway? [y/N] " ok; [[ "${ok}" =~ ^[Yy]$ ]] || exit 1
fi

echo "→ Setting GitHub Actions secrets on $(gh repo view --json nameWithOwner -q .nameWithOwner)"
gh secret set VPS_HOST      --body "${HOST}"
gh secret set VPS_USER      --body "${SSH_USER}"
gh secret set VPS_PATH      --body "${DEPLOY_PATH}"
gh secret set VPS_SSH_KEY   < "${KEY}"
gh secret set VPS_ENV_FILE  < "${ENV_FILE}"

echo "✓ Secrets set (idempotent). Verify with: gh secret list"
echo "  Next: git commit + push to main → the workflow builds, deploys, and"
echo "        self-bootstraps the DB on the VPS."
