# Configuration and secrets

**Status:** Overview · **Stages:** 1, 6 · **Requirements:** GR-10 · **Code:** settings module, `.env`, `.env.example`

## In one sentence
Everything that differs between machines (URLs, model names, thresholds, keys) is read
from the environment into one typed settings object, and secrets never enter the repository.

## Why it matters in this app
The same code must run on a laptop with Ollama and SQLite, in CI with mocked models, and
on a VPS with PostgreSQL and vLLM. The encryption key for the PII mapping and the cloud
API key are the two most sensitive values in the project.

## Core ideas
- **Twelve-factor config** — configuration lives in the environment, not in code.
- **`pydantic-settings`** — a `Settings` class that reads env vars, validates types and
  fails at startup if something required is missing.
- **Config vs secret** — a retry limit is config; an API key is a secret. Both come from
  the environment, only one is dangerous to leak.
- **`.env` and `.env.example`** — the real file is git-ignored; the example lists every
  variable with a harmless placeholder.
- **Fail fast** — a wrong setting should stop the app at boot, not at the first email.
- **Safe defaults** — the cloud route is off unless explicitly enabled (PRD risk table).

## In this project
- No hardcoded URLs, model names or thresholds (CLAUDE.md).
- Configurable values named in the PRD: retry limit (FR-3), size/complexity threshold for
  cloud routing (FR-13), mapping retention period (GR-2), PII recall threshold (GR-4).

## Questions to be able to answer
- What happens if a secret is committed once and then deleted in the next commit?
- Why validate settings at startup instead of where they are used?
- How do tests get different settings than the running app?
- Where does the encryption key live on the VPS, and who can read it?

## Related cards
[encryption-and-retention](encryption-and-retention.md) · [ci-cd-and-deployment](ci-cd-and-deployment.md) · [inference-routing](inference-routing.md)

## Notes from the build
_Empty — filled in during Stage 1._
