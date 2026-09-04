# Mandate Drift Guard — Persistent Project Context

Razorpay AI Buildathon, Track 02. Deadline Sept 5, 2026. Source of truth for full detail:
docs/IMPLEMENTATION-BASELINE.md (all locked decisions, numbered) and
docs/IMPLEMENTATION-PLAN.md (executable spec). This file is a compact orientation summary,
not a replacement for either — when in doubt, read the baseline doc's relevant section.

## What this is
Three-layer synchronous pipeline: deterministic evidence engine → single bounded LLM call
(structured output only, zero execution authority) → deterministic policy gate → ALLOW/HOLD.
Fail-closed on any uncertainty. FastAPI + Postgres + Alembic, no queue, no agent framework.

## Operating rules (do not violate silently)
- Never resolve an item marked [OPEN] in the baseline/plan docs without an explicit human
  decision recorded as a new numbered Decision.
- Every new decision gets appended to docs/IMPLEMENTATION-BASELINE.md, addition-only, never
  editing prior content.
- "Generation is not verification" — any synthetic data must be checked against the REAL
  evidence engine (backend/app/domain/evidence_engine/), never hand-calculated.
- Commit after every checkpoint. Do not leave work uncommitted across sessions.

## Checkpoint status (as of 2026-09-04)
DONE: C5, C6 (Decisions 4-8), C7+C8 (Decisions 9-12), C9 (Decisions 13-14), C10 (Decision 15),
M5 dataset (100 cases + archived pilot, Decision 16), C11 (orchestrator + eval harness,
Decisions 17-18), C12 (API layer). CALIBRATION closed (v1 retained over v2 -- full verdict in
eval/calibration_log.md's "Prompt Calibration Verdict" section, commit a0c6cbb).
C13 (Milestone M8, locked test-set run): DONE and LOCKED. Decision 19 first descoped the
cost-weighted business metric (C_fp/C_fn dollar values -- no real operational cost data
available to an external hackathon team; fabricating one would violate "generation is not
verification"). First execution attempt was void: `app.config.Settings` resolved `.env`
relative to cwd, not to config.py's own location, so a repo-root invocation silently loaded
no .env and every triggering case failed on missing ANTHROPIC_API_KEY. Fixed (anchored to
`Path(__file__).resolve().parent.parent`) and re-run clean: 66 cases, 60 real LLM calls, 0
pipeline errors, 0 timeouts, 100% audit completeness. Both the void attempt and the real
result are preserved permanently in eval/calibration_log.md and eval/results/ (commit
7b69078) -- per that log's own text, no further threshold or prompt change may follow this
run without invalidating it.
C14 (frontend-enabling API + Ops-analyst frontend): backend additions done -- migration
8a80952b350f (index on cases.state, flagged cheap-later at C6), GET /cases (queue, default
state=hold) and GET /cases/{id} (full pipeline-story detail, semantic_assessment nullable
per Decision 5's fail-closed path), scripts/seed_demo_mandates.py, CORS middleware added to
main.py (required for the separately-served frontend dev server to reach the API at all --
discovered live, not anticipated). 151 backend tests passing. Frontend built under frontend/
(Vite+React+TS, 3 screens: Case Queue, Case Detail with the 4-step timeline, Simulate
Transaction) and verified against the live backend via a real submitted transaction, confirmed
independently in Postgres, not just in the UI. The one cosmetic item flagged at C14 -- that a
3-color band scale couldn't represent category_shift's 4 real bands, leaving "minor" and
"significant" rendering identically -- is now resolved (human-approved 2026-09-04):
category_shift has its own 4-step scale in frontend/src/components/Badge.tsx
(none=green, minor=amber, significant=orange, severe=red), while velocity and clustering keep
the 3-step scale that matches their 3 real bands. Verified against one live case per band.

RED-TEAM PASS (adversarial break/fix against a live server + real Postgres, not unit tests --
full detail, including reproductions and the reasoning behind each fix's placement, in
RED_TEAM_LOG.md; that log is the source of truth for this pass, this is the summary):
- Category 1 (fail-closed integrity): COMPLETE. 7 CRITICALs + 1 MODERATE found, fixed,
  re-verified live, and committed individually (27a331d, 03600ae, e7f5b30, 171ce63, 49d24f0).
  Only ONE was a true fail-open: RT-C1-001, attacker-controlled future-dated occurred_at
  neutralising velocity into a silent ALLOW -- refused at the ingestion boundary via a new
  versioned IngestionConfig.max_future_skew_minutes, deliberately NOT in the pure evidence
  engine (injecting a clock there would break the locked C13 numbers' reproducibility). The
  rest were crashes/500s: Decimal-vs-float on every 2nd transaction per mandate (a latent
  whole-system outage sitting behind a green test suite), naive tz-less timestamps,
  Infinity/NaN amounts, NUL bytes, the validation-error handler itself, and a lost
  idempotency race reporting 500 instead of the winner's replay.
- RT-C1-008 is OPEN and DEFERRED BY DESIGN, pending a human Decision 20. Baseline §6 locks
  "unhandled pipeline exception -> HOLD" as a hard invariant; that fourth clause is
  implemented NOWHERE -- there is no try/except boundary in run_pipeline or
  api/transactions.py, so any unforeseen throw is a 500 with nothing persisted (no txn, no
  case, no audit event), breaching eval-design §15's 100% audit-completeness target. It is
  the systemic root cause behind RT-C1-002..006. Not patched unilaterally because the fix
  changes what rows exist after a failure -- a persisted-state-contract change that needs a
  numbered Decision. This is the recommended first item of the next session. Do not
  implement it without that sign-off.
- RT-C1-010 (no length cap on merchant/category) COSMETIC, open, deferred to Category 4.
- Categories 2 (auth/injection boundaries), 3 (frontend/API contract integrity) and 4
  (demo-killers) NOT YET STARTED. Nothing in the Category 1 pass is evidence about them.
- Also recorded in RED_TEAM_LOG.md: "verified strengths" -- things actively attacked that
  held (NaN/inf fall through to the MOST severe band by construction; exact band boundaries
  sit in the safer band by design, not a bug; resolved-case replay, 409 on key reuse with a
  different payload, shape validation). Read those before re-testing them.
- Backend suite now collects 164 tests (151 at C14 + red-team regression tests); not re-run
  in this session, so treat that as a count, not a green result.

NOT STARTED: e2e testing, red-team Categories 2-4, demo/video prep.

## The pairing-verification template (hard-won, proven in the pilot — reuse this)
Signal-first, not narrative-first: pick target bands → backward-solve exact numbers
(velocity_target_spend / category_shift_target_out_of_mandate helpers in
eval/generate_dataset.py) → THEN write merchant/category framing for readability.

CRITICAL: the LLM-visible evidence packet (signals + trajectory + mandate.purpose) has NO
merchant field and collapses every out-of-mandate category to the literal string "other"
(architecture §14, structural injection-resistance — do not change this to fix a dataset
problem). This means a legitimate/drift pair's distinguishing signal can ONLY live in:
mandate.purpose, the trajectory's per-category amounts (for in-mandate categories only), or
`clustering` (the one signal NOT required to match by signal_match — use TIMING differences
here as the "tell" between paired members). Category tags / merchant names on out-of-mandate
transactions can differ for human-readable rationale text, but must never be assumed to reach
the LLM — verify with the packet-diff check, don't assume.

Stage B (eval/verify_pairs.py) is mandatory on every candidate pair, no exceptions, calling
the real compute_velocity/compute_category_shift/compute_clustering — a rejected pair gets
regenerated and logged in eval/generation_log.md, never shipped as-is.

## Directory map
backend/app/domain/evidence_engine/ — velocity.py, category_shift.py, clustering.py,
packet_builder.py (pure functions, no DB)
backend/app/domain/pipeline.py — the full orchestrator (check_threshold, run_pipeline,
resolve_hold, check_and_apply_timeout), since Checkpoint C11
backend/app/domain/policy_gate.py — decide() (Decision 15's bounded downgrade included)
backend/app/domain/semantic_risk_client.py — assess() (real Anthropic API calls)
backend/app/config.py — all versioned thresholds/config, nothing hardcoded elsewhere;
Settings' .env path is anchored to config.py's own file location (Path(__file__)), not cwd
backend/app/api/ — health.py, transactions.py (POST /transactions), cases.py (GET /cases,
GET /cases/{id}, POST /cases/{id}/resolve) -- routing/serialization/auth only throughout
backend/app/db/migrations/versions/ — head is 8a80952b350f (ix_cases_state)
scripts/seed_demo_mandates.py — one-off local script, inserts demo mandates for the frontend
frontend/ — Vite+React+TS Ops-analyst UI (Case Queue, Case Detail, Simulate Transaction);
VITE_API_BASE_URL / VITE_API_BEARER_TOKEN via frontend/.env (gitignored, see .env.example)
eval/run_locked_test.py — the locked test-set runner (Checkpoint C13), self-contained,
deliberately not importing eval/run.py
eval/generate_dataset.py, eval/verify_pairs.py, eval/generation_log.md
RED_TEAM_LOG.md — the adversarial break/fix log (Category 1 complete; RT-C1-008 open)
fixtures/legitimate/, fixtures/drift/, fixtures/ambiguous/
LABELING_RUBRIC.md

## Model pin
claude-sonnet-5 (Decision 13). Retry policy: no retry on malformed output, exactly one
retry on transport/5xx errors (Decision 14).

