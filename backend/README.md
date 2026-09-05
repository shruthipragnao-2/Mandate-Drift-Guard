# Backend — development setup

FastAPI + Postgres + Alembic backend for Mandate Drift Guard's three-layer pipeline (deterministic
evidence engine → bounded LLM risk assessment → deterministic policy gate). See the
[root README](../README.md) for the full project overview and architecture; this file is local
dev-setup instructions only.

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

## Where things live

- `app/domain/evidence_engine/` — the deterministic signal functions (velocity, category_shift,
  clustering)
- `app/domain/pipeline.py`, `policy_gate.py`, `semantic_risk_client.py` — orchestration, the
  ALLOW/HOLD rule table, and the one bounded Claude call
- `app/api/` — `transactions.py`, `cases.py`, `health.py` (routing/serialization/auth only)
- `app/db/migrations/` — the Alembic chain

Run the tests: `pytest` (225 passing as of the last full run, against a real Postgres instance).

See the [root README](../README.md) for the full architecture and project status, and
`docs/IMPLEMENTATION-BASELINE.md` for every locked engineering decision.
