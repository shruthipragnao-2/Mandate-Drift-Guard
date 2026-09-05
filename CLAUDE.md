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
- RT-C1-008 (the missing fail-closed exception boundary -- baseline §6's fourth clause,
  previously implemented NOWHERE, and the systemic root cause behind RT-C1-002..006) is now
  FIXED under Decision 20 (baseline §24, commit 1c07c60, human sign-off 2026-09-04).
  run_pipeline's body is wrapped: on an otherwise-unhandled exception it rolls back the failed
  attempt's partial writes, then persists a held transaction + open hold case + one audit
  event carrying the exception type and a message with every request-supplied value actively
  redacted (no traceback). No semantic_assessment and no gate_decision row -- Decision 5's "no
  row when nothing validated", applied one layer earlier. It is a BACKSTOP, not a replacement:
  the four structured fail-closed paths return outcomes rather than raising, so they never
  reach it and keep their richer records. Three ripples worth knowing before touching this
  area: cases.gate_decision_id is now NULLABLE (migration c4f1b7e2d9a3 -- a real weakening,
  reasoned through in baseline §24); POST /transactions derives `decision` from transaction
  state, not `gate_decision or "allow"`, which would have reported a held transaction as
  allowed; and GET /cases/{id} returns evidence_packet/gate_decision as nullable plus a new
  fail_closed_reason field. IntegrityError is deliberately re-raised (RT-C1-009's fix in
  api/transactions.py depends on it).
- eval-design §16's pipeline-error metric was updated to match (commit d451182): a backstop
  catch no longer raises, so eval/report.py's is_pipeline_error() counts BOTH a caught
  exception and PipelineResult.fail_closed_reason. C13's locked numbers are unaffected and
  verifiably so -- recomputing the locked report's own 66 embedded case records through the
  new code returns byte-identical metrics, with 0 cases carrying either error signal.
- RT-C1-010 (no length cap on merchant/category) COSMETIC, still open at the API layer --
  its user-visible symptom (broken Case Detail layout on an unbroken long string) was fixed in
  Category 4 as RT-C4-002; the underlying missing length cap itself was not.
- Category 2 (auth/injection boundaries): COMPLETE, 2026-09-04. No CRITICAL findings, no auth
  bypass found anywhere. RT-C2-002 (bearer token compared with `!=`, not constant-time) fixed
  `4cdbe25` with `secrets.compare_digest`; measured honestly, the timing side-channel did not
  reproduce as exploitable (0.006ms delta against a 0.88ms baseline). RT-C2-001 (OpenAPI
  docs served unauthenticated) and RT-C2-003 (`mandate.purpose` is the one free-text field
  that reaches the LLM) logged as COSMETIC/forward-looking, not fixed. A parallel run with a
  recording stand-in at the LLM boundary verified the structural-exclusion property (no
  merchant field, no free-text category) against the literal bytes sent to Anthropic, not a
  rebuilt packet. 28 new auth-boundary tests.
- Categories 3 (frontend/API contract integrity) and 4 (demo-killers): COMPLETE, 2026-09-04,
  run against the real backend and real Vite dev server with headless-Chrome screenshots. No
  CRITICAL findings. Two MODERATE issues found and fixed (`8d4c407`): RT-C4-001 (Vite silently
  moving off port 5173 when occupied silently CORS-blocked every API call from the new origin)
  and RT-C4-002 (an unbroken long merchant/category/purpose string breaking the Case Detail
  layout). Three COSMETIC items logged, not fixed: RT-C4-003 (browser Back exits the app --
  no history integration), RT-C3-001 (backend-unreachable banner shows a raw `TypeError`), and
  RT-C3-002 (a 400 validation error renders as raw JSON).
- Also recorded in RED_TEAM_LOG.md: "verified strengths" -- things actively attacked that
  held (NaN/inf fall through to the MOST severe band by construction; exact band boundaries
  sit in the safer band by design, not a bug; resolved-case replay, 409 on key reuse with a
  different payload, shape validation, SQL injection blocked before the ORM is reached with
  row counts verified unchanged, no case-existence oracle pre-auth, CORS preflight leaks
  nothing to a disallowed origin). Read those before re-testing them.
- **All four red-team categories are now COMPLETE.** Backend suite: 225 tests passing as of
  2026-09-05, run against real Postgres, not collected -- this is a green result.

## Submission readiness (2026-09-05)
The adversarial pass surfaced one more class of issue worth recording here rather than in
RED_TEAM_LOG.md, since it's about the repository itself, not the running system: this repo's
GitHub default branch was `main`, a 3-commit skeleton (`Initial commit` -> `C5: establish
repository foundation` -> one stray `frontend commits` commit that had accidentally checked in
`node_modules`), completely diverged from `c6-domain-model`, which carries all 35 real commits.
Fixed by flipping GitHub's default branch to `c6-domain-model` via the API and deleting `main`
entirely, both on origin and locally -- `c6-domain-model` is now the repo's only branch.
Verified separately: no `.env` file or secret was ever committed on any branch, in any commit,
at any point in history. Root `README.md`, `backend/README.md`, and `frontend/README.md` were
all still describing Checkpoint-C5-era or template-default scope; rewritten to reflect current
state. The live demo Postgres database also had ~17 red-team probe transactions (prompt
injection, XSS, SQLi, oversized strings, `AuthProbe`/`LiveProbeMerchant`) left over from the
Category 2-4 live-fire pass, sitting in the same tables the Case Queue reads from -- identified
by exact signature match (not a blanket delete) and removed in FK-safe order; the ~405
legitimate eval-dataset transactions and mandates were left untouched.

NOT STARTED: automated e2e test suite (the red-team Category 3/4 screenshots were a one-off
audit pass, not a regression suite); the actual demo video recording itself (the UI/latency-
label/evidence-toggle polish it depends on is done, per C14 and Category 4 above).

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
backend/app/db/migrations/versions/ — head is c4f1b7e2d9a3 (cases.gate_decision_id nullable,
Decision 20); prior head 8a80952b350f (ix_cases_state)
scripts/seed_demo_mandates.py — one-off local script, inserts demo mandates for the frontend
frontend/ — Vite+React+TS Ops-analyst UI (Case Queue, Case Detail, Simulate Transaction);
VITE_API_BASE_URL / VITE_API_BEARER_TOKEN via frontend/.env (gitignored, see .env.example)
eval/run_locked_test.py — the locked test-set runner (Checkpoint C13), self-contained,
deliberately not importing eval/run.py
eval/generate_dataset.py, eval/verify_pairs.py, eval/generation_log.md
RED_TEAM_LOG.md — the adversarial break/fix log (all four categories complete and closed;
RT-C1-010, RT-C4-003, RT-C3-001, RT-C3-002 remain open, all COSMETIC)
fixtures/legitimate/, fixtures/drift/, fixtures/ambiguous/
LABELING_RUBRIC.md

## Model pin
claude-sonnet-5 (Decision 13). Retry policy: no retry on malformed output, exactly one
retry on transport/5xx errors (Decision 14).

