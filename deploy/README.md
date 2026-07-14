# Deploying Clartt to a VPS

This directory holds everything the GitHub Actions workflow rsyncs to the VPS plus the one-off bootstrap/restore scripts.

## Fast path (automated)

Once you have a VPS you can SSH into and `gh` installed locally, three commands
get you from nothing to deployed — all idempotent (safe to re-run):

```bash
# 1. Provision the box: deploy key + Docker + firewall + dirs
bash deploy/provision.sh <vps-ip> root /opt/clartt

# 2. Push the GitHub Actions secrets from your local .env + key
bash deploy/setup-secrets.sh <vps-ip> root /opt/clartt

# 3. Deploy
git push origin main
```

The push triggers: tests → build images → push to GHCR → SSH to the VPS →
**self-bootstrap the DB if empty** → seed users/instruments/strategies →
start candle backfill → smoke-test. Later pushes skip the DB bootstrap (it's
only done when the DB is empty).

Still manual (can't be automated): **buying the VPS** + first SSH access, and
the **DNS A record** for your domain. TLS turns on automatically once
`CLARTT_DOMAIN` is in the deployed `.env` and DNS resolves.

The step-by-step below explains what each command does if you'd rather run it
by hand.

---

## First-time setup — checklist (manual equivalent)

Do these once, in order. Everything after is just `git push origin main`.

### 1. Generate the deploy key (on your laptop)

```bash
ssh-keygen -t ed25519 -f ~/.ssh/clartt_deploy -N "" -C "github-actions deploy"
```

Two files appear:
- `~/.ssh/clartt_deploy` — private half. Goes into GitHub Actions as a secret. **Never commit.**
- `~/.ssh/clartt_deploy.pub` — public half. Goes on the VPS.

### 2. Authorize the key on the VPS

```bash
ssh-copy-id -i ~/.ssh/clartt_deploy.pub <vps-user>@<vps-host>
```

If `ssh-copy-id` isn't available, paste the contents of `~/.ssh/clartt_deploy.pub` into `~/.ssh/authorized_keys` on the VPS manually.

Test it:
```bash
ssh -i ~/.ssh/clartt_deploy <vps-user>@<vps-host> "echo ok"
```

### 3. Bootstrap the VPS

```bash
scp deploy/vps-bootstrap.sh <vps-user>@<vps-host>:~/
ssh <vps-user>@<vps-host> "bash ~/vps-bootstrap.sh /opt/clartt"
```

This also configures a `ufw` host firewall (opens only SSH + 80/443 + the
dashboard port). Two things to know:

- **Docker bypasses ufw.** Anything Docker publishes to `0.0.0.0` is exposed
  regardless of firewall rules. That's why `docker-compose.prod.yml` binds
  Postgres/Redis/backend/engines to `127.0.0.1` — the loopback bind (not ufw)
  is what keeps the database off the internet. ufw is defense-in-depth for the
  host. If Contabo also offers a cloud firewall, enable it too.
- **Add TLS before real use** — see **Enabling TLS** below. Until then the
  dashboard serves plain HTTP and the login sends a JWT unencrypted.

### 4. Choose how the database gets bootstrapped

Two paths — pick one (details in **Database bootstrap** below):

- **Seed-based (recommended for a clean prod):** nothing to copy now. The DB
  builds itself from `deploy/schema.sql` + boot seeds + candle backfill. Just
  make sure `ADMIN_EMAIL` / `ADMIN_PASSWORD` are in the `.env` you put in the
  `VPS_ENV_FILE` secret.
- **Restore-based (carry over everything):** copy your full backup now:
  ```bash
  scp ~/clartt-backups/postgres-*.sql.gz <vps-user>@<vps-host>:/opt/clartt/
  ```

### 5. Add GitHub secrets

GitHub repo → Settings → Secrets and variables → Actions → **New repository secret**. Add all five:

| Secret | Value |
|---|---|
| `VPS_HOST` | The IP or hostname of your Contabo VPS |
| `VPS_USER` | The SSH user (e.g. `root` or `deploy`) |
| `VPS_PATH` | `/opt/clartt` (or wherever bootstrap ran) |
| `VPS_SSH_KEY` | **Full contents** of `~/.ssh/clartt_deploy` — from `-----BEGIN OPENSSH PRIVATE KEY-----` to the matching END line, including the END line |
| `VPS_ENV_FILE` | **Full contents** of your local `.env` |

### 6. First deploy

Push to `main`. The workflow runs:
1. Builds 4 images, pushes to `ghcr.io/<you>/clartt-{dashboard,backend,strategy-engine,execution-engine}`
2. rsyncs compose files + this `deploy/` directory to the VPS
3. Writes `.env` from secret
4. `docker compose pull && docker compose up -d`
5. Smoke-tests the backend health endpoint

The first deploy's smoke test may fail because the DB is empty (no schema yet) — that's expected. Bootstrap the DB (step 7), then the next boot is healthy.

### 7. Bootstrap the database

See **Database bootstrap** below for the two options. Short version:

**Seed-based (recommended):**
```bash
ssh <vps-user>@<vps-host>
cd /opt/clartt
bash deploy/vps-init-schema.sh /opt/clartt          # loads schema.sql + baselines migrations
docker compose -f docker-compose.yml -f docker-compose.prod.yml restart backend
```
On restart the backend seeds the admin user, instruments, and strategies, then
starts pulling candles from Deriv. Log in with `ADMIN_EMAIL`/`ADMIN_PASSWORD`
and add your trading account through the dashboard.

**Restore-based:**
```bash
ssh <vps-user>@<vps-host>
cd /opt/clartt
bash deploy/vps-restore-db.sh /opt/clartt/postgres-*.sql.gz
```

You're live. Subsequent pushes redeploy automatically.

---

## Database bootstrap

The incremental TypeORM migrations assume a schema that `synchronize` first
built (the earliest one `ALTER`s the instruments table), so they can't build a
DB from empty on their own. Schema therefore comes from one of:

### Option A — Seed-based (recommended for clean prod)

Ships **no data** — the DB self-populates:

1. `deploy/vps-init-schema.sh` loads `deploy/schema.sql` (~32 KB, DDL only) and
   marks all current migrations as applied (baseline).
2. On backend boot: migrations no-op → **boot seeds run** (admin user from
   `ADMIN_EMAIL`/`ADMIN_PASSWORD`, all instruments **with their `derivSymbol`**,
   the tuned strategy catalogue).
3. The candle backfill auto-starts ~10 s later and pulls **12 months** of
   history per instrument from Deriv (a few minutes; this is what rebuilds the
   bulk of the DB).
4. You add your trading account through the dashboard (its API token is
   encrypted at rest via `TOKEN_ENCRYPTION_KEY`).

Regenerate `deploy/schema.sql` whenever entities change:
```bash
docker compose exec -T postgres pg_dump -U trading -d us30_trading \
  --schema-only --no-owner --no-privileges > deploy/schema.sql
```

### Option B — Restore a full dump

`deploy/vps-restore-db.sh` loads a full `pg_dump` (schema + all data, incl.
~437 MB of candles) and baselines migrations. Use this to carry an existing
environment over wholesale. The seeds still run on boot but find everything
already present, so they're no-ops.

---

## Day-to-day

### Deploy
Just push to `main`. The workflow takes ~3–6 minutes (the first run is slower because the GHA cache is cold).

### Manual deploy / re-run
GitHub repo → Actions → "Build & Deploy" → "Run workflow".

### Rollback to a previous commit's images
```bash
ssh <vps-user>@<vps-host>
cd /opt/clartt
export IMAGE_REPO_OWNER=<lowercase-github-username>
export IMAGE_TAG=<previous-commit-sha>
docker compose -f docker-compose.yml -f docker-compose.prod.yml pull
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
```
GHCR keeps every commit's image, so any past SHA works.

### Where to look when something breaks
```bash
ssh <vps-user>@<vps-host>
cd /opt/clartt
docker compose ps                              # what's up
docker compose logs --tail=100 backend         # any service name
docker compose logs -f strategy-engine         # follow live
```

### Update `.env` on the VPS
Update the `VPS_ENV_FILE` secret in GitHub and re-run the workflow. The next deploy will write the new `.env` atomically.

---

## Enabling TLS (do this before real use)

The dashboard serves plain HTTP; the login sends a JWT. Put Caddy in front for
automatic HTTPS. Everything's already in the repo — you just need a domain.

1. **Point a domain at the VPS.** Create a DNS `A` record
   (e.g. `trade.yourdomain.com`) → your VPS IP. Wait for it to resolve.

2. **Add three lines to `VPS_ENV_FILE`** (the GitHub secret) and re-deploy so
   the VPS `.env` picks them up:
   ```
   CLARTT_DOMAIN=trade.yourdomain.com
   CLARTT_ACME_EMAIL=you@yourdomain.com
   CORS_ORIGIN=https://trade.yourdomain.com
   ```

3. **Bring the stack up with the TLS override** (adds the Caddy container):
   ```bash
   ssh <vps-user>@<vps-host>
   cd /opt/clartt
   docker compose -f docker-compose.yml -f docker-compose.prod.yml \
                  -f docker-compose.tls.yml up -d
   ```
   Caddy fetches a Let's Encrypt cert on first start (a few seconds). The
   dashboard drops to a loopback-only bind; Caddy is the only public service.

4. **Tighten the firewall** — the dashboard port is no longer public:
   ```bash
   sudo ufw delete allow 8080
   ```

5. Visit `https://trade.yourdomain.com`. HTTP auto-redirects to HTTPS.

**To make it permanent in CI**, add `-f docker-compose.tls.yml` to the `pull`
and `up -d` commands in `.github/workflows/deploy.yml` (the "Pull images &
restart stack" step). Otherwise re-run the compose command above after each
deploy, or keep TLS deploys manual.

> The Caddy cert store lives in the `caddy-data` docker volume — it persists
> across restarts, so you won't re-issue certs (and hit Let's Encrypt rate
> limits) on every redeploy.

### Backup the production DB

**Automated (set up once):** install the nightly backup cron on the VPS:
```bash
ssh <vps-user>@<vps-host>
crontab -e
# add this line — nightly at 03:15, keeps 14 days of dumps in /opt/clartt/backups
15 3 * * * /opt/clartt/deploy/vps-backup-db.sh /opt/clartt >> /opt/clartt/backups/backup.log 2>&1
```

Pull the dumps off-box regularly (a backup that lives only on the VPS dies with the VPS):
```bash
# from your laptop
rsync -az <vps-user>@<vps-host>:/opt/clartt/backups/ ~/clartt-backups/vps/
```

**Manual one-off:**
```bash
ssh <vps-user>@<vps-host> "bash /opt/clartt/deploy/vps-backup-db.sh /opt/clartt"
```

---

## Files in this directory

| File | Purpose |
|---|---|
| `provision.sh` | **(laptop)** one-command idempotent VPS setup — deploy key + Docker + bootstrap |
| `setup-secrets.sh` | **(laptop)** set the 5 GitHub Actions secrets via `gh` (idempotent) |
| `vps-bootstrap.sh` | **(on VPS)** installs Docker, creates the deploy dir, configures ufw |
| `vps-init-schema.sh` | Seed-based bootstrap: load `schema.sql` + baseline migrations (Option A) |
| `schema.sql` | Schema-only DDL snapshot (~32 KB) used by `vps-init-schema.sh` |
| `vps-restore-db.sh` | Restore a full `pg_dump` backup + baseline migrations (Option B) |
| `vps-backup-db.sh` | Nightly dump with 14-day rotation — install via cron (see above) |
| `Caddyfile` | Caddy reverse-proxy config for TLS (used by `docker-compose.tls.yml`) |
| `README.md` | This file |

The TLS override itself lives at the repo root: `docker-compose.tls.yml`.
