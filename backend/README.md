# Backend — development setup

Checkpoint C5 (repository foundation) scope only: app startup, config, DB connectivity/migration
scaffolding, and a health check. No business logic (evidence engine, LLM layer, policy gate,
mandates/transactions/cases APIs) exists yet — see `docs/IMPLEMENTATION-PLAN.md` §Q for the
milestone plan.

## Prerequisites

- Python 3.12+ (tested with 3.13)
- Postgres 16 (via Docker, or a local install) — not required just to run tests or boot the app
  and hit `/health`, but required for anything that touches the database.

## Local setup (no Docker)

```bash
cd backend
python -m venv .venv
# Windows (Git Bash): source .venv/Scripts/activate
# Windows (PowerShell): .venv\Scripts\Activate.ps1
# macOS/Linux: source .venv/bin/activate

pip install -r requirements-dev.txt
cp .env.example .env   # edit if your local Postgres differs from the defaults
```

Run the app:

```bash
uvicorn app.main:app --reload
```

Then `curl http://127.0.0.1:8000/health` should return `{"status": "ok"}`.

Run the tests:

```bash
pytest
```

## Database migrations

Alembic is configured (`alembic.ini`, `app/db/migrations/`) and reads `DATABASE_URL` from the
same `app.config.settings` object the app uses — one source of truth for the connection string.

```bash
alembic upgrade head    # apply migrations
alembic downgrade base  # roll back everything
alembic current          # show the applied revision
```

As of Checkpoint C5 there are no domain tables yet (`app/db/base.py`'s `Base.metadata` is empty),
so the single initial revision is a deliberate no-op — it only establishes the migration chain and
the `alembic_version` tracking table. Domain tables (mandates, transactions, evidence_packets,
etc.) are added in milestone M1.

## Docker Compose (backend + Postgres)

```bash
docker compose up --build
```

This starts Postgres (`db`) and the backend (`backend`), the latter depending on the former's
healthcheck. The backend does not run migrations automatically on startup in C5 — run
`alembic upgrade head` against the running `db` service manually (e.g. via
`docker compose exec backend alembic upgrade head`) once you need the schema applied.

> **Note:** Docker was not available in the environment this checkpoint was implemented in, so
> the Compose path above is written to match `docker-compose.yml` and `Dockerfile` but has not
> been execution-verified here. The Alembic migration chain itself *was* verified mechanically
> (apply/rollback) against a throwaway local SQLite file, and the Postgres engine/driver
> (`psycopg2`) was verified to construct correctly from `DATABASE_URL` — but no live Postgres
> connection has actually been exercised yet. Please verify `docker compose up` and
> `alembic upgrade head` against the real `db` service before relying on this in C6.

## What's deliberately not here yet

- `app/db/models.py` (domain tables) — M1
- `app/domain/*` (evidence engine, semantic risk client, policy gate, pipeline) — M1–M3
- `app/api/mandates.py`, `transactions.py`, `cases.py`, `auth.py` — M4
- `eval/`, root `fixtures/` — M5–M6
- `frontend/` — M7

See `docs/IMPLEMENTATION-PLAN.md` for the full milestone breakdown and `docs/
IMPLEMENTATION-BASELINE.md` §15 / plan §S for decisions still open (disagreement-handling rule,
retry policy, ingestion auth, signal thresholds, etc.) that later milestones depend on.
