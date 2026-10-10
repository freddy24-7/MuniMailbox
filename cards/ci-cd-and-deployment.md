# CI/CD, Docker Compose and systemd

**Status:** Overview · **Stages:** 1 (CI), 6 · **Requirements:** GR-11, NFR portability · **Code:** `.github/workflows/`, `deploy/`

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
_Empty — filled in during Stages 1 and 6._
