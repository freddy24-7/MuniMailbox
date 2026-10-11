# Configuration and secrets

**Status:** Stage 6 notes added, questions open · **Stages:** 1, 6 · **Requirements:** GR-10 · **Code:** settings module, `.env`, `.env.example`

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
### Stage 1 — 2026-10-10

**What was built**
- [app/config.py](../app/config.py): one `Settings` class with four values so far —
  `environment`, `database_url`, `log_level`, `log_format`.
- [.env.example](../.env.example) is committed with placeholders; `.env` holds the real
  values and is listed in [.gitignore](../.gitignore).
- `get_settings()` is wrapped in `lru_cache`, so the environment is read once per process.

**Decisions and why**
- `database_url` is a `SecretStr`, because the URL contains the password. Printing the
  settings shows `database_url=SecretStr('**********')`; code that needs the value must
  ask for it explicitly with `.get_secret_value()`.
- `log_level` and `log_format` are `Literal[...]` types, so a typo is rejected at startup
  rather than silently ignored.
- `extra="ignore"`: the same `.env` also carries `POSTGRES_*` variables that only Docker
  Compose reads. Without this, pydantic would reject them as unknown.
- Tests build `Settings(_env_file=None)` so a developer's local `.env` cannot change a test result.

**Seen in practice**
- `LOG_LEVEL=CHATTY` stops the app before it serves anything, with a message naming the
  field and the allowed values.
- The database password exists in two places in `.env`: `POSTGRES_PASSWORD` (for the
  container) and inside `DATABASE_URL` (for the app). They must be kept in sync by hand.
- [.github/workflows/ci.yml](../.github/workflows/ci.yml) contains the password `ci-only`
  in plain text. Worth being able to explain why that one is acceptable.

**Try it**
- `LOG_LEVEL=CHATTY uv run python -c "import app.main"`
- `uv run python -c "from app.config import get_settings; print(get_settings())"`
- `git check-ignore -v .env` — shows which rule keeps it out of the repository.

**Not met yet**
- Encryption key and cloud API key (Stage 2 and 4), and where secrets live on the VPS (Stage 6).

### Stage 6 — 2026-10-11 (what the later stages added)

**What changed since Stage 1**
- The settings grew with every stage: models, retrieval, retries, the event bus,
  monitoring. All still one class, [app/config.py](../app/config.py), and every variable
  is listed in `.env.example` with a comment.
- Four secrets now: `ENCRYPTION_KEY`, `SESSION_SECRET`, `CLOUD_LLM_API_KEY` and
  (optional) `METRICS_TOKEN`. All are `SecretStr`: printing the settings shows `**********`.

**Decisions and why**
- **Risky things are off by default.** The cloud model needs `CLOUD_LLM_ENABLED=true`;
  the deploy job needs a repository variable; monitoring needs a Compose profile. Doing
  nothing gives the safe behaviour.
- **Thresholds are settings, not numbers in the code:** the redaction score, the recall
  gate, the similarity floor, the retry limit. Each has a comment saying what moves when
  it changes; the similarity floor's comment carries the measured ranges.
- **Tests never read `.env`.** They build `Settings(_env_file=None)` with their own
  values. A test that passes only because of what is in your `.env` is the kind of
  difference that made CI fail in Stage 6.
- **On a server, secrets live in `/etc/munimailbox/env`,** readable by root and the
  service only, loaded by systemd. In CI, throwaway keys are generated per run; the
  deploy key and server address are GitHub secrets.

**Seen in practice**
- A model on Mistral's free plan was listed but allowed zero requests per minute. The fix
  was one line in `.env`, with no code change: that is what "configuration" is for.
- Development on an 8 GB laptop was made possible by settings alone: a smaller model, one
  model call at a time, the cloud route for chat.

**Still open**
- No rotation of the encryption key: replacing it means re-encrypting what is stored.
