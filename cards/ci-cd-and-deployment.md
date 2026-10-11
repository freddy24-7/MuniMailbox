# CI/CD, Docker Compose and systemd

**Status:** Stage 6 notes added, questions open · **Stages:** 1 (CI), 6 · **Requirements:** GR-11, NFR portability · **Code:** `.github/workflows/`, `deploy/`

## In one sentence
Every push is checked automatically, and a push to main that passes is built and rolled
out to the server without anyone logging in to do it by hand.

## Why it matters in this app
The pipeline is where the project's rules get teeth: the PII recall gate, the bypass
tests and the type checker all run here. And "one `docker compose up`" is what makes a
nine-part stack usable by one developer.

## Core ideas
- **Continuous integration** — lint, type-check and test on every push (ruff, mypy, pytest).
- **Continuous deployment** — a green main branch is deployed automatically.
- **Quality gate** — a check that blocks the merge or deploy when it fails.
- **Container and image** — a packaged process with its dependencies; an image is the
  recipe, a container is a running instance.
- **Docker Compose** — one file that describes all services and how they connect: app,
  PostgreSQL, Redis, ChromaDB, Prometheus, Loki, Grafana.
- **systemd unit** — tells the Linux server how to start a service, restart it on failure
  and start it at boot.
- **Reverse proxy and TLS** — one public entry point that terminates HTTPS; the database
  and model server listen only internally (GR-11).
- **Secrets in CI** — deploy keys and API keys are injected by the CI system, never stored in the repo.
- **Migrations on deploy** — the schema is upgraded as a step of the rollout.
- **Health check and rollback** — how the deploy knows it worked, and what happens if not.

## In this project
- CI order: ruff → mypy → unit + integration → PII recall gate → Playwright e2e → deploy.
- Acceptance criterion: a push to main runs tests and deploys to the VPS with no manual steps.
- The model server needs a GPU that the VPS probably lacks — where inference runs in the
  deployed demo is an open design point.

## Questions to be able to answer
- What is the difference between continuous delivery and continuous deployment?
- Why run systemd on the VPS when Docker Compose is used locally?
- Which services must never be reachable from the internet, and how is that enforced?
- What happens to an email being processed while a deploy restarts the app?
- How does the e2e test in CI get a model to talk to?

## Related cards
[testing-strategy](testing-strategy.md) · [config-and-secrets](config-and-secrets.md) · [observability](observability.md)

## Notes from the build
### Stage 1 — 2026-10-10 (CI and local Compose only)

**What was built**
- [.github/workflows/ci.yml](../.github/workflows/ci.yml), on every push to `main` and
  every pull request: install → ruff (lint and format check) → mypy → migrations → tests.
  The first run, on commit `e79a7cc`, passed.
- [deploy/docker-compose.yml](../deploy/docker-compose.yml) with one service so far,
  PostgreSQL. The root [compose.yaml](../compose.yaml) only includes it, so
  `docker compose up -d` works from the repository root.

**Decisions and why**
- CI starts its own PostgreSQL as a *service container*: created for the job, thrown away
  after. Tests therefore run against the same database engine as production.
- `uv sync --locked` fails if `uv.lock` does not match `pyproject.toml`, so CI installs
  exactly the versions that were tested locally.
- Order is cheapest first: a formatting error fails in seconds, before the database is used.
- The migration step runs `alembic upgrade head` on an empty database (proves a fresh
  install works) and `alembic check` (proves no migration is missing).
- Locally the database port is published as `127.0.0.1:5432`, not `5432`. The first form
  is reachable only from this machine (GR-11).
- The Compose file refuses to start without a password (`${POSTGRES_PASSWORD:?...}`)
  rather than falling back to a default.

**Try it**
- Open the Actions tab on GitHub and read the log of each step of the first run.
- Push a branch with a deliberately mis-formatted file and watch which step fails.
- `docker compose ps`, `docker compose logs postgres`, `docker volume ls`.
- `docker compose down` versus `docker compose down -v` — check what happens to the data.

**Not met yet**
- No deploy job, no VPS, no systemd, no HTTPS, no image for the app itself (Stage 6).
- The PII recall gate and the Playwright job (Stages 3 and 6).

### Stage 6 — 2026-10-11 (browser tests in CI, deployment written but not run)

**What was built**
- CI now also starts a Redis, enforces test coverage of at least 70%, and runs the
  browser tests ([tests/e2e/](../tests/e2e/)).
- A `deploy` job in the same workflow: after the tests pass on `main`, it logs in to a
  server over SSH and runs [deploy/deploy.sh](../deploy/deploy.sh).
- [deploy/systemd/](../deploy/systemd/): the app as a service, and two daily timers
  (retention purge, audit-chain check). [deploy/Caddyfile](../deploy/Caddyfile): HTTPS.
- [deploy/README.md](../deploy/README.md): the one-time server setup.

**Decisions and why**
- **Deployment is a job that needs the test job.** A commit that fails a test cannot
  reach the server; there is no separate button to forget.
- **The deploy job is off until a repository variable switches it on.** A copy of the
  repository without a server skips it instead of failing.
- **The server's identity is pinned** (`StrictHostKeyChecking=yes` with a stored key), so
  the job cannot be tricked into sending the code to another machine.
- **Deploy, check, roll back.** `deploy.sh` installs the commit, migrates, restarts, and
  asks `/readyz` whether the app answers. If not, it puts the previous commit back.
- **Migrations only add.** The previous version of the code keeps working with the new
  schema; that is what makes rolling back the code safe.
- **systemd restarts the app when it crashes,** and Redis redelivers the events that were
  not finished. The two together are what "reliable" means here.
- **The service can write its log file and nothing else** (`ProtectSystem=strict`).

**Seen in practice**
- CI failed twice on GitHub with a test that passed every time locally. The test assumed
  two emails finish in the order they arrived; on PostgreSQL they run in parallel and
  sometimes do not. Local runs used SQLite, where there is one writer at a time. The fix
  was in the test, and the lesson is older than this project: a test that only runs on a
  simpler setup than production proves less than it seems to.
- Reproducing it needed CI's exact conditions: PostgreSQL, Redis and no `.env` file.
- **Decided not to rent a server.** The app runs locally and is presented through the
  README. So the deploy job, the systemd units, the Caddy configuration and `deploy.sh`
  have never run. The acceptance criterion "a push to main deploys without manual steps"
  is built, not met.

**Try it**
- Open the Actions tab on GitHub: the `deploy` job is listed as skipped, with the reason.
- Read `deploy/deploy.sh` and find the two places where it can exit, and with what.

**Still open**
- Everything under "deployment" until it has run on a real server once.
- No container image of the app itself; on the server it runs from a checkout.
- A public address would need a lock first: the demo login has no passwords.
