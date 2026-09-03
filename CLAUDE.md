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

## Checkpoint status (as of 2026-09-03, evening)
DONE: C5, C6 (Decisions 4-8), C7+C8 (Decisions 9-12), C9 (Decisions 13-14), C10 (Decision 15),
M5 dataset (100 cases + archived pilot, Decision 16), C11 (orchestrator + eval harness,
Decisions 17-18), C12 (API layer).
IN PROGRESS: prompt calibration. Found and fixed a real bug -- eval/run.py wasn't passing
fixture created_at to the DB Mandate row, corrupting velocity signals dev-set-wide. Fixed via
eval/dataset_loader.py's persist_case_mandate() (single source of truth now). Tested
prompt_version "v2" against the anchoring bias found in v1 (LLM rarely reports risk_level=
"low"); v2 showed mixed results on the full dev set (marginal slow_drift gain, no fast_spike
gain, one new false negative, real regression in ambiguous-case abstention) -- decision: v1
retained, v2 documented as tested-and-rejected in eval/calibration_log.md.
NEXT ACTION: revert semantic_risk_client.py's prompt to v1 exactly, confirm tests pass,
commit. Then C13 (locked test-set run) is unblocked. Full history: eval/calibration_log.md.
NOT STARTED: C13, frontend, e2e testing, breaking/fixing pass, demo/video prep.

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
backend/app/domain/pipeline.py — check_threshold only (NOT the full orchestrator yet)
backend/app/domain/policy_gate.py — decide() (NOT the full orchestrator yet)
backend/app/domain/semantic_risk_client.py — assess() (real Anthropic API calls)
backend/app/config.py — all versioned thresholds/config, nothing hardcoded elsewhere
eval/generate_dataset.py, eval/verify_pairs.py, eval/generation_log.md
fixtures/legitimate/, fixtures/drift/, fixtures/ambiguous/
LABELING_RUBRIC.md

## Model pin
claude-sonnet-5 (Decision 13). Retry policy: no retry on malformed output, exactly one
retry on transport/5xx errors (Decision 14).

