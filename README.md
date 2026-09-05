# Mandate Drift Guard

**Razorpay AI Buildathon — Track 02: AI Risk Manager**

Detects when an AI shopping agent's *overall spending trajectory* has drifted from what it was
actually authorized to do — even when every individual transaction, looked at alone, seems fine.

## The problem

Razorpay's agentic-payment pilot lets a person grant an AI shopping agent a standing spending
mandate once (a budget, a period, a set of allowed categories), so the agent can transact
repeatedly without asking for approval every time. A payment rail can enforce *how much* an
agent spends. It cannot enforce *what the money was for*.

No single purchase in a drifting sequence has to look wrong. A grocery agent that quietly starts
buying electronics, ten transactions in a row, each individually plausible, is the failure mode
this system exists to catch — before the human ever notices, and before the eventual chargeback
dispute weeks later.

This is a **merchant/platform-side risk tool** — the same relationship as a bank's fraud-alert
system to an account holder. The person using this UI is a Risk/Trust Ops analyst, not the
consumer whose spending is being watched.

## How it works

A synchronous, three-layer pipeline runs on every transaction, in one HTTP request/response —
no queue, no background job, no agent framework:

```
Transaction ──▶ 1. Evidence Engine ──▶ 2. Semantic Risk Assessment ──▶ 3. Policy Gate ──▶ ALLOW / HOLD
                (deterministic)          (one bounded LLM call)          (deterministic)
```

1. **Evidence Engine** (`backend/app/domain/evidence_engine/`) — pure, deterministic functions
   compute three signals from the mandate's transaction history: spend **velocity**, **category
   shift** (spend moving outside the mandate's allowed categories), and **clustering** (spend
   concentrating in a narrow set of merchants/timing). No LLM involved; a threshold crossing on
   any signal is what triggers everything downstream. Most transactions never trigger anything.
2. **Semantic Risk Assessment** (`backend/app/domain/semantic_risk_client.py`) — exactly one
   bounded Claude call per triggered transaction, given only a structured evidence packet
   (signals + trajectory + the mandate's stated purpose — **no merchant name, ever**, and every
   out-of-mandate category collapsed to the literal string `"other"`; see *structural
   injection-resistance* below). The model returns structured output only — `risk_level`,
   `mandate_alignment`, `confidence`, human-readable evidence — and has **zero execution
   authority**. It cannot move money, resolve a case, or call a tool.
3. **Policy Gate** (`backend/app/domain/policy_gate.py`) — a deterministic rule table turns the
   evidence + assessment into `ALLOW` or `HOLD`. A held transaction opens a **Case** for a human
   Ops analyst to resolve (`resolved_allow` / `resolved_block`), with a full evidence trail —
   what was measured, what the model said, and why the gate decided what it did.

**Fail-closed on any uncertainty.** A malformed LLM response, a timeout, or an unhandled
exception anywhere in the pipeline routes to `HOLD`, never to a silent allow — including a
dedicated backstop around the entire pipeline that persists a held transaction and an audit
event even if the code throws somewhere nobody anticipated.

**Structural injection-resistance.** The evidence packet the LLM sees has no merchant field and
no free-text category outside the mandate's own allowed list — the one thing that reaches the
model as free text is the mandate's own `purpose`, set once at mandate creation, not attacker-
controlled per transaction. This was deliberately red-teamed (prompt injection, XSS, SQL
injection payloads fired at every text field) rather than assumed — see
[`RED_TEAM_LOG.md`](RED_TEAM_LOG.md).

## The frontend

A three-screen Ops-analyst tool (`frontend/`, Vite + React + TypeScript):

- **Case Queue** — every case that ever reached `HOLD`, across all three states (open, resolved
  allow, resolved block), with severity, merchant/category/amount, and the mandate it's held
  against.
- **Case Detail** — the full four-step pipeline story for one case (transaction received →
  deterministic signals → semantic assessment → gate decision), including a raw
  evidence-packet toggle showing exactly what layer 2 saw. An analyst resolves the case here.
- **Simulate Transaction** — submit a transaction against any seeded demo mandate and watch it
  move through the live pipeline in real time.

## Engineering rigor

- **225 backend tests**, run against a real Postgres instance (not mocked, not just collected).
- **A locked, non-repeatable evaluation run** (Milestone M8) against a held-out test set of 66
  paired legitimate/drift/ambiguous cases, generated with a signal-first "backward-solve the
  exact numbers, then verify against the real evidence engine" methodology — never hand-labeled
  or hand-calculated. 100% audit completeness, 0 pipeline errors, 0 gate-rule violations. Full
  numbers and methodology in [`eval/calibration_log.md`](eval/calibration_log.md) and
  [`eval/results/`](eval/results/).
- **A full adversarial (red-team) pass against a live server and real database** — four
  categories covering fail-closed integrity, auth/injection boundaries, frontend/API contract
  integrity, and demo-killer polish. Every finding, reproduction, and fix is logged in
  [`RED_TEAM_LOG.md`](RED_TEAM_LOG.md), including the one true fail-open bug it found (an
  attacker-controlled future-dated timestamp that could neutralize the velocity signal) and the
  fixes for the eight other issues it turned up.
- **Every non-obvious engineering decision is numbered, dated, and traceable** in
  [`docs/IMPLEMENTATION-BASELINE.md`](docs/IMPLEMENTATION-BASELINE.md) — nothing marked `[OPEN]`
  in the original spec was resolved without an explicit, recorded decision.

## Getting started

**Backend** (FastAPI + Postgres + Alembic) — see [`backend/README.md`](backend/README.md) for
full detail:

```bash
cd backend
python -m venv .venv && source .venv/Scripts/activate   # Windows: .venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
cp .env.example .env   # fill in ANTHROPIC_API_KEY and your local Postgres URL
alembic upgrade head
uvicorn app.main:app --reload
```

Or via Docker Compose (Postgres + backend): `docker compose up --build`.

**Frontend** (Vite + React + TypeScript) — see [`frontend/README.md`](frontend/README.md):

```bash
cd frontend
npm install
cp .env.example .env   # point VITE_API_BASE_URL / VITE_API_BEARER_TOKEN at the backend above
npm run dev
```

Seed demo mandates for the frontend to simulate against: `python scripts/seed_demo_mandates.py`
(run from `backend/`, so its `.env`/`DATABASE_URL` resolution applies).

**Tests:** `cd backend && pytest`

## Repository layout

```
backend/app/domain/evidence_engine/   pure signal functions (velocity, category_shift, clustering)
backend/app/domain/pipeline.py        the orchestrator — run_pipeline, resolve_hold, timeout check
backend/app/domain/policy_gate.py     the deterministic ALLOW/HOLD rule table
backend/app/domain/semantic_risk_client.py   the one bounded Claude call
backend/app/api/                      transactions.py, cases.py, health.py — routing/auth only
backend/app/db/migrations/            Alembic migration chain
frontend/src/                         Case Queue, Case Detail, Simulate Transaction screens
eval/                                 dataset generation/verification, calibration log, locked results
fixtures/                             the paired legitimate/drift/ambiguous evaluation dataset
scripts/seed_demo_mandates.py         one-off local script to seed demo mandates for the frontend
docs/IMPLEMENTATION-BASELINE.md       every locked engineering decision, numbered and dated
docs/IMPLEMENTATION-PLAN.md           the executable build plan and milestone sequence
docs/spec/                            the original problem brief, product spec, and architecture docs
RED_TEAM_LOG.md                       the full adversarial break/fix log
LABELING_RUBRIC.md                    the fixed ground-truth labeling rubric for the eval dataset
```

## Known, deliberate limitations

- No length cap on transaction `merchant`/`category` fields (`RT-C1-010`, cosmetic — logged, not
  yet fixed).
- Ingestion (`POST /transactions`) shares the demo's single bearer token rather than a separate
  per-agent credential — acceptable for a synthetic-data prototype with no real payment rail
  behind it; flagged as forward-looking in `RED_TEAM_LOG.md` (`RT-C2-003`).
- This is a synthetic-data prototype end to end — no real Razorpay integration exists or is
  claimed.

## Model

`claude-sonnet-5`, one bounded structured-output call per triggered transaction. No retry on
malformed output (routes straight to fail-closed `HOLD`); exactly one retry on transport/5xx
errors.
