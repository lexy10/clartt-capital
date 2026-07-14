#!/usr/bin/env bash
# Run this ONCE on the VPS before the first deploy fires.
#
#   bash vps-bootstrap.sh /opt/clartt [dashboard_port]
#
# What it does:
#   - Creates the deploy directory and locks down permissions
#   - Verifies docker + docker compose work
#   - Configures a host firewall (ufw): SSH + web ports only
#   - Stops here so you can scp the postgres backup over before the first
#     deploy. The DB restore step (vps-restore-db.sh) is a separate run.

set -euo pipefail

VPS_PATH="${1:-/opt/clartt}"
DASHBOARD_PORT="${2:-8080}"

echo "→ Creating deploy directory at ${VPS_PATH}"
sudo mkdir -p "${VPS_PATH}"
sudo chown "$(id -u):$(id -g)" "${VPS_PATH}"
chmod 750 "${VPS_PATH}"

echo "→ Ensuring docker is installed"
if ! command -v docker >/dev/null 2>&1; then
  echo "  docker not found — installing via get.docker.com (idempotent)"
  curl -fsSL https://get.docker.com | sudo sh
  sudo systemctl enable --now docker
else
  echo "  docker already present"
fi
docker --version
docker compose version

echo "→ Ensuring '$(whoami)' can talk to docker without sudo"
if ! docker ps >/dev/null 2>&1; then
  # Add to the docker group. Takes effect on next login; this script may need
  # a re-run (or `newgrp docker`) after this. Idempotent — usermod is a no-op
  # if already a member.
  sudo usermod -aG docker "$(whoami)"
  echo "  Added $(whoami) to the docker group. Log out/in (or run 'newgrp docker')"
  echo "  and re-run this script. Safe to re-run — every step is idempotent."
  exit 0
fi

echo "→ Configuring host firewall (ufw)"
# IMPORTANT — Docker vs ufw: Docker writes iptables rules directly and will
# expose any port it publishes to 0.0.0.0 REGARDLESS of ufw. Our
# docker-compose.prod.yml deliberately binds postgres/redis/backend/engines to
# 127.0.0.1, so they are never published to the outside world — that loopback
# bind, not ufw, is what protects the database. ufw here is defense-in-depth
# for the HOST itself (SSH and anything not docker-published). Only the
# dashboard is published on 0.0.0.0, on ${DASHBOARD_PORT}.
if command -v ufw >/dev/null 2>&1 || sudo apt-get install -y ufw >/dev/null 2>&1; then
  # Detect the port THIS ssh session is on so we never lock ourselves out,
  # even if SSH was moved off 22.
  SSH_PORT="$(echo "${SSH_CONNECTION:-}" | awk '{print $4}')"
  SSH_PORT="${SSH_PORT:-22}"

  sudo ufw --force reset >/dev/null
  sudo ufw default deny incoming
  sudo ufw default allow outgoing
  sudo ufw allow "${SSH_PORT}/tcp" comment 'SSH'
  sudo ufw allow 80/tcp   comment 'HTTP (reverse proxy / TLS redirect)'
  sudo ufw allow 443/tcp  comment 'HTTPS'
  sudo ufw allow "${DASHBOARD_PORT}/tcp" comment 'Clartt dashboard'
  sudo ufw --force enable
  echo "  ufw enabled — open: ${SSH_PORT} (ssh), 80, 443, ${DASHBOARD_PORT} (dashboard)"
  sudo ufw status verbose | sed 's/^/    /'
else
  echo "  WARNING: ufw not available; skipping host firewall. Configure Contabo's" >&2
  echo "  cloud firewall to allow only 22/80/443/${DASHBOARD_PORT} instead." >&2
fi

cat <<EOF

✓ Bootstrap complete. ${VPS_PATH} is ready.

NEXT STEPS:

1. From your laptop, copy the postgres backup to the VPS:
     scp ~/clartt-backups/postgres-*.sql.gz <vps-user>@<vps-host>:${VPS_PATH}/

2. Add the deploy key's PUBLIC half to ~/.ssh/authorized_keys on the VPS.

3. In GitHub repo settings → Secrets and variables → Actions, add:
     VPS_HOST       — the VPS IP or hostname
     VPS_USER       — '$(whoami)' (or your deploy user)
     VPS_PATH       — '${VPS_PATH}'
     VPS_SSH_KEY    — PRIVATE half of the deploy key (full key including BEGIN/END lines)
     VPS_ENV_FILE   — full contents of your .env file

4. Push to main. The workflow will:
   - Build and push four images to ghcr.io
   - SSH in, rsync compose files, write .env, pull images, restart
   - Smoke-test backend health

5. AFTER the first deploy lands, bootstrap the database (see deploy/README.md):
     Seed-based:    bash deploy/vps-init-schema.sh ${VPS_PATH}
     Restore-based: bash deploy/vps-restore-db.sh ${VPS_PATH}/postgres-*.sql.gz

6. RECOMMENDED before real use — put TLS in front of the dashboard.
   Right now it serves plain HTTP on port ${DASHBOARD_PORT}, and the login
   sends a JWT — that must not travel unencrypted over the internet. Point a
   domain at the VPS and run a reverse proxy (Caddy is easiest — automatic
   Let's Encrypt certs on 80/443 → proxy to localhost:${DASHBOARD_PORT}).
   Then you can even close ${DASHBOARD_PORT} in ufw and serve only 443.

EOF
