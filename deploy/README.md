# Deployment

Everything needed to run the app outside a laptop. **None of the server parts have been
run on a real server yet**; treat the first deployment as a test.

| File | What it is |
| --- | --- |
| `docker-compose.yml` | PostgreSQL, ChromaDB and Redis. With `--profile monitoring` also Prometheus, Loki, Alloy and Grafana. |
| `prometheus/` | Scrape configuration and alert rules. |
| `alloy/config.alloy` | Ships the app's JSON log file to Loki. |
| `grafana/` | Data sources and three dashboards, loaded at start-up. |
| `systemd/` | The app as a service, plus two daily timers: retention purge and audit-chain check. |
| `Caddyfile` | HTTPS in front of the app. |
| `deploy.sh` | Runs on the server: install a commit, migrate, restart, check, roll back if unhealthy. |

## Locally

```bash
docker compose up -d                          # database, vector store, event bus
docker compose --profile monitoring up -d     # plus monitoring; Grafana on http://localhost:3000
```

The monitoring containers are capped at about 1 GB together and used about 170 MB when
measured. Set `LOG_FILE=logs/app.log` in `.env`, or Loki has nothing to read.

## One-time server setup

1. A Linux server with Docker, [uv](https://docs.astral.sh/uv/), Caddy and git.
2. `useradd --system --home /opt/munimailbox munimailbox`, clone the repository to
   `/opt/munimailbox`, owned by that user. Create `/opt/munimailbox/logs`.
3. `/etc/munimailbox/env`: copy `.env.example`, fill it in, `chmod 600`. Use
   `ENVIRONMENT=production`, `LOG_FORMAT=json`, `LOG_FILE=/opt/munimailbox/logs/app.log`,
   `EVENT_BUS=redis` and a `METRICS_TOKEN`.
4. `docker compose --env-file /etc/munimailbox/env up -d` in `/opt/munimailbox`.
5. `uv sync --locked --no-dev`, `uv run alembic upgrade head`, then ingest the knowledge
   base (`scripts/fetch_documents.py`, `scripts/ingest_documents.py`).
6. Copy `systemd/*` to `/etc/systemd/system/`, then
   `systemctl enable --now munimailbox munimailbox-purge.timer munimailbox-verify-audit.timer`.
7. Put your domain in `Caddyfile`, copy it to `/etc/caddy/Caddyfile`, reload Caddy.
8. Allow the deploy user to restart the service without a password:
   `munimailbox ALL=(root) NOPASSWD: /usr/bin/systemctl restart munimailbox` in sudoers.

A server without a GPU cannot run the local chat model at a useful speed. Use the cloud
route there (`CLOUD_LLM_ENABLED=true`); embeddings still need a local embedding server.

## Deploying on every push to main

The `deploy` job in `.github/workflows/ci.yml` runs after the tests pass on `main`. It
does nothing until these are set in the GitHub repository:

- variable `DEPLOY_ENABLED` = `true`
- secrets `DEPLOY_HOST`, `DEPLOY_USER`, `DEPLOY_SSH_KEY` (a key that may log in as the
  `munimailbox` user) and `DEPLOY_KNOWN_HOSTS` (the server's line from `ssh-keyscan`)
