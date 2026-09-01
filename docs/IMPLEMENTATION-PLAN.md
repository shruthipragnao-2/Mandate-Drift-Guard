# Implementation Plan — Mandate Drift Guard
*Checkpoint C4. Derived from `docs/IMPLEMENTATION-BASELINE.md` (engineering source of truth) and
`docs/spec/*.md` (original specifications) on 2026-08-30. No application code, dependencies, or
boilerplate are produced by this document.*

**Tags used throughout** (same convention as the baseline, plus one addition):
- `[LOCKED]` — settled in the brief/product-spec/architecture and unchanged by any later doc, or
  one of the three human-signed-off Decisions (1–3) from the baseline.
- `[PROPOSED]` — a specific answer offered by architecture.md or this plan, not yet a product
  decision requiring sign-off, but not simply "no spec preference" either.
- `[OPEN]` — unresolved; requires a human decision before the affected code can be finished.
- `[IMPL DETAIL]` — a low-stakes engineering choice (file layout, test framework, log format) that
  the specs explicitly delegate or don't address, and that does not need product sign-off per
  operating rule 7.

Where this plan and `architecture.md` disagree (e.g., §H/§I confidence handling superseding
architecture §8), the baseline's locked Decisions win — noted explicitly at each site.

---

## A. Repository structure

```
Mandate-Drift-Guard/
├── CLAUDE.md
├── README.md
├── docker-compose.yml                      [PROPOSED] local dev: backend + Postgres
├── docs/
│   ├── spec/                               source specs — read-only, unchanged
│   ├── IMPLEMENTATION-BASELINE.md          engineering source of truth
│   └── IMPLEMENTATION-PLAN.md              this document
├── backend/
│   ├── pyproject.toml (or requirements.txt)
│   ├── app/
│   │   ├── main.py                         FastAPI app + route registration only
│   │   ├── config.py                       model pin, policy_version, timeouts, thresholds
│   │   ├── api/                            route handlers — HTTP concerns only, no business logic
│   │   │   ├── mandates.py
│   │   │   ├── transactions.py
│   │   │   ├── cases.py
│   │   │   └── health.py
│   │   ├── domain/                         framework-agnostic pipeline logic (importable by eval/)
│   │   │   ├── models.py                   domain entities (§C)
│   │   │   ├── evidence_engine/            layer ① — pure functions, no DB, no AI
│   │   │   │   ├── velocity.py
│   │   │   │   ├── category_shift.py
│   │   │   │   ├── clustering.py
│   │   │   │   └── packet_builder.py       assembles the evidence packet from signal results
│   │   │   ├── semantic_risk_client.py     layer ② — Anthropic call, schema validation
│   │   │   ├── policy_gate.py              layer ③ — ALLOW/HOLD/BLOCK decision table
│   │   │   └── pipeline.py                 wires ①→②→③, writes audit events, the only orchestrator
│   │   ├── db/
│   │   │   ├── models.py                   SQLAlchemy table definitions
│   │   │   ├── session.py
│   │   │   └── migrations/                 Alembic
│   │   ├── schemas/                        Pydantic request/response + LLM I/O schemas
│   │   └── auth.py                         bearer-token check (resolve endpoint only, per §E)
│   └── tests/
│       ├── unit/                           evidence engine, gate, schema validation — no DB, no network
│       ├── integration/                    pipeline with mocked LLM, real DB
│       ├── api/                            FastAPI TestClient, endpoint-level
│       └── fixtures/                       test-only fixtures (distinct from eval fixtures below)
├── eval/
│   ├── run.py                              batch runner, in-process pipeline import, writes results/
│   ├── report.py                           computes eval-design.md §7–18 metrics from a results file
│   ├── results/                            committed results files (source of truth for pitch numbers)
│   └── tests/                              unit tests on metric-formula code itself
├── fixtures/
│   ├── legitimate/
│   ├── drift/
│   ├── ambiguous/
│   └── failure_cases/                      the 7 injected-failure fixtures (eval-design)
└── frontend/                                created only when milestone M7 starts (§Q)
```

**Responsibilities:**
- `backend/app/api/` — HTTP boundary only: parse request, call `domain/pipeline.py`, shape
  response. No signal math, no LLM calls, no gate logic lives here.
- `backend/app/domain/` — the three-layer pipeline (§I of the baseline) plus its orchestrator.
  This is what `eval/run.py` imports directly, in-process, per architecture §11's explicit
  requirement that the harness not depend on a running HTTP server.
- `backend/app/db/` — persistence only. No business rules.
- `backend/tests/` — code-correctness tests (unit/integration/API). Distinct from `eval/`, which
  tests *product* correctness against labeled data.
- `eval/` and `fixtures/` — the evaluation substrate described in baseline §9–10. `fixtures/`
  keeps the exact top-level names the brief already committed to
  (`fixtures/legitimate/`, `fixtures/drift/`, `fixtures/ambiguous/`) — not renamed here.
- `docs/` — `spec/` stays the immutable original source; `IMPLEMENTATION-BASELINE.md` is the
  synthesized engineering source of truth; this plan sits alongside it. Future architecture
  decision records (one file per resolved `[OPEN]` item) could live in `docs/decisions/` —
  `[PROPOSED, optional]`, not created until an open item is actually resolved.

`[IMPL DETAIL]` File layout above (exact filenames, `pyproject.toml` vs `requirements.txt`, whether
`eval/` scripts import `backend/app` as a package or a path-relative import) — none of this needs
product sign-off; architecture.md explicitly declines to have an opinion on several of these points
("No ORM opinion imposed here... not worth spending a decision-cycle on for a solo 8-day build").

---

## B. Technology stack

| Layer | Choice | Status | Why |
|---|---|---|---|
| Backend framework | FastAPI (Python) | `[PROPOSED]` architecture §16 | Native async; Pydantic doubles as both the API-request validator and the LLM-output schema validator (one library, two jobs — avoids a second validation dependency); matches the Python-native Anthropic SDK. |
| Database | Postgres | `[PROPOSED]` architecture §16 | The `audit_events` append-only guarantee (§O) needs a real `INSERT/SELECT`-only DB role grant — a concrete, checkable security-boundary claim. SQLite has no equivalent role-level grant model. |
| ORM / data access | SQLAlchemy | `[IMPL DETAIL]` | Architecture explicitly leaves this open ("SQLAlchemy or a lighter query layer are both fine"). SQLAlchemy chosen only because it pairs with Alembic migrations and is the most common FastAPI+Postgres combination — not a claim that a lighter layer would be wrong. |
| Migrations | Alembic | `[IMPL DETAIL]` | Standard SQLAlchemy companion; no spec preference stated. |
| Validation / schema | Pydantic | `[PROPOSED]` architecture §4, §16 | Same library validates API request/response shapes *and* the forced-tool-call LLM output — this dual use is what architecture explicitly calls out as the reason to pick FastAPI in the first place. Not adding a second schema library for one of these two jobs. |
| LLM integration | Anthropic Python SDK, single forced-tool-call per triggered case | `[PROPOSED]` architecture §4 step 3, §8 | Matches the locked "structured output, not agentic tool use" requirement (§H). No wrapper/agent framework needed for one bounded call. |
| Agent orchestration framework | **None** | `[LOCKED — rejected]` architecture §16 | Explicitly rejected: "The AI step is one bounded classification call, not a multi-step agent loop... A framework here is complexity with no matching requirement." Do not add LangChain/LangGraph/CrewAI/etc. |
| Message queue | **None** | `[LOCKED — rejected]` architecture §1, §16 | Architecture A (synchronous monolith) was chosen over the event-driven alternative specifically to avoid this. Do not add Redis Streams/Kafka/etc. |
| Testing framework | pytest | `[IMPL DETAIL]` | No spec preference; standard Python default, works with FastAPI's `TestClient`. |
| Frontend framework | Vite + React + TypeScript SPA | `[PROPOSED]` architecture §16 | "No SSR/routing complexity needed for a single case-list + case-detail view" — deferred to milestone M7 (§Q); recorded here only because architecture already made the call. |
| Config/secrets | `pydantic-settings` or plain `.env` + `python-dotenv` | `[IMPL DETAIL]` | Needed to load the pinned model string, bearer token, DB URL, thresholds — no spec preference on mechanism. |
| Rate limiting (ingestion) | `slowapi`, optional | `[PROPOSED, explicitly stretch]` architecture §10 | "Not a hard requirement for Track 02 scoring, worth adding only if time permits." Not part of MVP core. |

**Deliberately not added:** any ORM-adjacent GraphQL layer, any admin-panel generator, any
background-job framework beyond a single scheduled check for HOLD-resolution timeout (architecture
§15 — "an `asyncio` background task or a simple cron-triggered endpoint is enough... no durable job
scheduler needed"), any observability/APM SaaS (architecture §12 names this a stated non-goal), and
any second LLM-calling library — one bounded call does not need an abstraction layer over the
Anthropic SDK.

---

## C. Domain model

The baseline (and architecture §5) already names the persisted tables; this section maps the nine
concepts requested onto them and flags one addition the locked Decisions require.

| Requested concept | Realized as | Notes |
|---|---|---|
| mandate | `mandates` table | purpose, budget, period_days, allowed_categories — includes the project's proposed semantic-purpose field, labeled in code/comments as "our enrichment, not Razorpay's confirmed schema" per the brief's honesty fix. |
| transaction | `transactions` table | id, mandate_id (FK), merchant, category, amount, occurred_at, ingested_at, idempotency_key (unique), **plus a `state` field** — see below, required by Decision 2. |
| mandate/transaction history or events | **Not a separate table.** Realized as a windowed query over `transactions` (the "rolling recent window, e.g. 6–8 weeks" from the brief's bounded-retention principle) used as input to the Evidence Engine. `audit_events` separately serves as the permanent event history for everything *downstream* of a triggered evaluation. | `[IMPL DETAIL]` — no spec calls for a persisted "history" entity distinct from the transaction stream itself. |
| deterministic signal result | **Not independently persisted.** It's the typed return value of each Evidence Engine function (§F), captured into the `signals` (and `trajectory`) JSONB fields of `evidence_packets` once a threshold is crossed. | Whether to *also* persist a normalized `signal_results` table (for easier ad-hoc querying) is `[IMPL DETAIL]`, not required by any spec. |
| evidence packet | `evidence_packets` table | One row per *triggered* evaluation, not per transaction — most transactions never cross a threshold and never get one (architecture §5). |
| LLM judgment | `semantic_assessments` table | mandate_alignment, risk_level, confidence, evidence[], raw_response, model_version, prompt_version, latency_ms. |
| policy decision | `gate_decisions` table | decision (allow/hold), rule_version, rule_applied, nullable FK to `semantic_assessments` (null on timeout/malformed path). |
| audit event | `audit_events` table | append-only, `INSERT`/`SELECT`-only DB grant (§O). |
| Ops HOLD resolution | `cases` table (state: hold / resolved_allow / resolved_block, resolved_by, resolved_at, resolution_reason) + one `audit_events` row of `event_type = resolution` | Not a separate table from `cases` — resolution is a state transition on the case, audited like every other stage. |

**Required schema addition (a direct consequence of Decision 2, not a new open decision):**
architecture §5's original `cases` table has no explicit FK to the transaction it holds.
Decision 2 ("HOLD applies to the triggering transaction attempt... represented explicitly as a
held transaction state") requires:
- `transactions.state` — enum, see §J for the full state machine.
- `cases.transaction_id` (FK, not null) — the specific transaction a case is holding.

This is flagged here so it isn't silently dropped when the schema is actually written — it's a
structural consequence of an already-`[LOCKED]` decision, not a new product decision (§S does not
list it).

**Relationships** (unchanged from architecture §5's ERD, plus the addition above):
`mandate 1—* transactions`; `mandate 1—* evidence_packets` (triggered cases only);
`evidence_packet 1—0..1 semantic_assessment`; `semantic_assessment 1—0..1 gate_decision`
(nullable FK); `gate_decision 1—0..1 case`; `case 1—1 transaction` (new, above);
`case 1—* audit_events`; `audit_events.case_id` nullable — pre-case-creation pipeline events
(e.g. a nominal ALLOW with no case) still get audited (§K).

---

## D. Pipeline architecture — request lifecycle

```
1. Transaction attempt received          api/transactions.py
   → validate shape, idempotency check
2. Evidence Engine                       domain/evidence_engine/*.py
   → compute velocity, category-shift, clustering against rolling window
3. Threshold check                       domain/pipeline.py
   → not crossed: ALLOW immediately, no case, one audit_event, done
   → crossed: continue
4. Evidence packet built + persisted     domain/evidence_engine/packet_builder.py → evidence_packets row
5. Bounded LLM assessment                domain/semantic_risk_client.py
   → forced tool call, timeout/schema validation → semantic_assessments row (or failure marker)
6. Deterministic Policy Gate             domain/policy_gate.py
   → ALLOW / HOLD / BLOCK per §I's decision table → gate_decisions row
7. Transaction state update              domain/pipeline.py (single DB transaction with step 8)
   → ALLOW: transactions.state = allowed
   → HOLD: transactions.state = held; cases row created, referencing the transaction (§C)
8. Audit record(s) written               every one of steps 2–7 appends to audit_events
9. [If HOLD] Ops resolution, later       api/cases.py POST /cases/{id}/resolve
   → confirm → transactions.state = allowed, cases.state = resolved_allow
   → deny     → transactions.state = blocked, cases.state = resolved_block
   → timeout  → same as deny, triggered by a scheduled check, resolved_by = "system:timeout"
```

`domain/pipeline.py` is the **only** place that calls all three layers in sequence — API handlers
and the eval harness both call into it, never into the individual layers directly, so there is
exactly one orchestration path to keep consistent between live traffic and evaluation (matching
architecture §11's reproducibility requirement).

---

## E. API contracts

No implementation in this section — shapes and requirements only.

### `POST /mandates`
- **Purpose:** create a mandate (demo/fixture/seed loading).
- **Auth:** none `[PROPOSED]` (architecture §7 — demo/seed use).
- **Request:** `{ purpose: string, budget: number, period_days: int, allowed_categories: string[] }`
- **Response:** `201 { id, purpose, budget, period_days, allowed_categories, created_at }`
- **Errors:** `400` invalid shape (missing field, non-positive budget, empty `allowed_categories`).

### `POST /mandates/{id}/transactions`
- **Purpose:** ingest a transaction; runs the full pipeline (§D) synchronously.
- **Auth:** `[OPEN — architecture §7 flags this explicitly; not resolved here]`.
- **Request:** `{ merchant: string, category: string, amount: number, occurred_at: datetime, idempotency_key: string }`
- **Response:** `200 { transaction_id, state: "allowed" | "held", case_id?: uuid, gate_decision: "allow" | "hold" }` — deliberately minimal; the full evidence packet and raw LLM response are fetched via `GET /cases/{id}`, not inlined here. `[IMPL DETAIL, proposed convention]`
- **Errors:** `404` mandate not found; `400` invalid transaction shape; `409` idempotency-key reuse with a *different* payload (same key + same payload → `200` idempotent replay of the original decision, per architecture §7). `[IMPL DETAIL — the exact same-key/different-payload conflict rule is not spelled out in any spec document and is proposed here for concreteness.]`

### `GET /cases`
- **Purpose:** list cases, filterable by state.
- **Auth:** none `[PROPOSED]`.
- **Query params:** `state`, `mandate_id`, `limit`/`offset` `[IMPL DETAIL — pagination isn't spec'd, needed to avoid unbounded scans]`.
- **Response:** `200 [{ id, mandate_id, transaction_id, state, opened_at, risk_level, mandate_alignment }]`
- **Errors:** `400` invalid filter value.

### `GET /cases/{id}`
- **Purpose:** full case detail for Ops review.
- **Auth:** none `[PROPOSED]`.
- **Response:** `200 { case, mandate_snapshot, transaction, evidence_packet, semantic_assessment, gate_decision, audit_events[] }`
- **Errors:** `404` not found.

### `POST /cases/{id}/resolve`
- **Purpose:** the sole human-in-the-loop action — Ops analyst resolves a HOLD.
- **Auth:** **bearer token required** `[LOCKED]` — the one consequential write endpoint (architecture §7), and per Decision 1, the analyst is the only party who can call it (no consumer-facing equivalent exists).
- **Request:** `{ resolution: "confirm" | "deny", resolved_by: string, resolution_reason: string }`
- **Response:** `200 { case_id, new_state: "resolved_allow" | "resolved_block", resolved_at }`
- **Errors:** `404` case not found; `409` case already resolved (no double-write, per architecture §7); `401`/`403` missing/invalid token; `400` invalid `resolution` value.

### `GET /health`
- **Purpose:** liveness check. **Auth:** none. **Response:** `200 { status: "ok" }`.

No consumer-facing endpoint exists anywhere in this list, consistent with Decision 1/operating
rule 13.

---

## F. Deterministic signal engine

Represented as **three independent, pure-function components** under
`domain/evidence_engine/` — `velocity.py`, `category_shift.py`, `clustering.py` — each with the
shape:

```
def compute_<signal>(mandate, transactions_in_window) -> <Signal>Result
```

No shared mutable state; each is independently unit-testable with no DB dependency. This
composability is a deliberate property, not incidental: it means the still-open velocity-split
proposal (below) would be **additive** — a fourth component, or a swap of one component's
internals — rather than a rewrite of the engine's structure, *if and when it is adopted*. It is
**not adopted here.**

**`[LOCKED]`** — three signal identities must exist: spend velocity classification,
category-shift magnitude, clustering. (brief; product-spec §22; architecture §3, §5)

**`[OPEN — must remain explicitly open, not silently adopted]`** — the proposal (brief, "Open
item," attributed to an Aug 29 external review) to split velocity into a fast-spike-specific
velocity signal plus a separate, distinct slow-drift "pattern-consistency" signal. No document
resolves this. This plan does not implement it; the three-component shape above is deliberately
generic enough to accommodate it later without a redesign, but that is a structural convenience,
not a decision to adopt it.

**`[OPEN — not specified anywhere]`** — exact numeric formulas/thresholds:
- Velocity classification bands (what counts as "elevated," "nominal," etc.)
- Category-shift magnitude buckets (what counts as "significant" vs. "mild")
- Clustering method (algorithm, distance metric, or rule-based grouping — undecided)

**Structural requirement carried from eval-design §2 (not a new decision, a consequence of an
already-locked formula):** whatever the above thresholds/methods turn out to be, each signal
result must expose a **comparable categorical value** (not just a raw float), because the
paired-scenario verification formula (`signal_match`) checks discrete equality
(`velocity_A == velocity_B`, `category_shift_bucket_A == category_shift_bucket_B`) between
candidate pairs. This constrains the *shape* of the return type; it does not resolve the two
`[OPEN]` items above.

---

## G. Evidence packet

**What enters** (matches the brief's exact example schema, reproduced in baseline §4):
- `mandate`: purpose, budget, period_days, allowed_categories
- `signals`: structured categorical/numeric values only (e.g. `spend_velocity: "elevated"`,
  `category_shift: "significant"`, `budget_utilization: 0.91`)
- `trajectory`: aggregated historical-vs-current distribution — structured, not raw transaction
  rows

**What is excluded, structurally:** raw transaction rows; merchant free text; any field type
capable of holding arbitrary text. `[LOCKED]` (architecture §14; product-spec §16)

**How deterministic evidence is represented:** the exact typed outputs of §F's three signal
functions, serialized into the `signals`/`trajectory` sub-objects — no natural-language
summarization step between the Evidence Engine and the packet.

**Validation before LLM submission:** a Pydantic model for the evidence-packet schema is
constructed and validated *before* serialization to the LLM call. This is the runtime
instantiation of the locked structural-exclusion requirement above.

**How merchant free text is prevented from entering — precisely:** this is enforced by **field
type, not by runtime filtering**. The packet's `merchant`/`category` fields are typed as short,
constrained values (e.g., a bounded string or an enum drawn from the allowed-categories set) —
there is no `description`/`notes`/free-text field in the schema at all, so an adversarial string
in a transaction's raw merchant-text field (eval-design failure case #4) has no field to occupy in
a valid packet in the first place. Architecture §14 is explicit that this is "checked by a schema
test, not a prompt-injection defense" — the schema-boundary integrity test (§M) proves the field
doesn't exist, it doesn't prove a filter works.

---

## H. LLM layer

- **Input contract:** the evidence packet only, serialized as the user message; a fixed system
  prompt (task instructions + exact output schema); no retrieval, no chat history, no few-shot
  examples in MVP. `[LOCKED]` (product-spec §8; architecture §4 step 2)
- **Output contract:** one forced tool call, `emit_risk_assessment`, arguments validated against:
  `mandate_alignment` (enum: low/medium/high — `[LOCKED]`), `risk_level` (`[OPEN, unspecified]` —
  no source document enumerates `risk_level`'s exact value set; every example shows only
  `"high"`. Presumed low/medium/high by analogy with `mandate_alignment`, but no document states
  this explicitly — flagged rather than assumed), `confidence` (float, `[LOCKED]` — but see
  confidence handling below), `evidence[]` (short natural-language strings, `[LOCKED]`).
- **Structured-output validation:** any validation failure (missing field, wrong type, malformed
  JSON, out-of-range confidence) is treated **identically** to a malformed response. `[LOCKED]`
  (architecture §4 step 4)
- **Timeout behavior:** a hard timeout (`[PROPOSED]` starting value 10s, tuned against observed
  p95 latency — architecture §8) → HOLD. `[LOCKED behavior; the exact seconds value is `[IMPL
  DETAIL]`, tunable via config]`
- **Malformed-output behavior:** → HOLD, no repair, no best-effort parsing. `[LOCKED]`
- **Retry behavior:** `[OPEN — explicitly not settled]`. Architecture §10 and eval-design §16 both
  *lean toward* no application-level retry on malformed output (straight to HOLD), with exactly
  one transport-level retry reserved for connection errors/5xx only — but eval-design's own open
  items list and architecture's own final sign-off checklist both still carry this as undecided.
  This plan does not treat it as locked.
- **Confidence handling:** `[LOCKED — Decision 3]`. Raw self-reported confidence is stored exactly
  as returned, never treated as a calibrated probability. If `confidence` is missing, malformed,
  or otherwise unusable, the result is treated as **uncertain** and routes to HOLD via the
  fail-closed invariant (§I) — not repaired, not defaulted, not passed through an
  evidence-completeness proxy (that proposal is explicitly rejected). Whether raw confidence
  correlates with correctness is measured by the evaluation harness (point-biserial correlation +
  Brier score, eval-design §8) as a reported finding, not consulted by the gate at runtime.
- **What the LLM is explicitly forbidden from deciding:** ALLOW/HOLD/BLOCK directly; anything with
  execution/side effects; mandate mutation; anything beyond the four fields above. `[LOCKED]`
  (product-spec §12, §13, §15)

---

## I. Policy gate

**Decision table** — every row is `[LOCKED]` except the one marked otherwise:

| Condition | Outcome | Status |
|---|---|---|
| Threshold not crossed (layer ② never invoked) | ALLOW, no case created | `[LOCKED]` |
| Threshold crossed, LLM valid, risk = medium/high | HOLD | `[LOCKED]` |
| Threshold crossed, LLM valid, risk = low, **confidence usable** | **UNDECIDED — this is precisely the open disagreement-handling question** | `[OPEN]` |
| Threshold crossed, LLM timeout | HOLD | `[LOCKED]` |
| Threshold crossed, LLM malformed/schema-invalid | HOLD | `[LOCKED]` |
| Threshold crossed, confidence missing/malformed/unusable | HOLD (uncertain, §H) | `[LOCKED — Decision 3]` |
| Any unhandled pipeline exception | HOLD, exception logged to audit event | `[LOCKED]` (architecture §10) |
| HOLD, resolution timeout | BLOCK | `[LOCKED]` |
| HOLD, Ops analyst confirms | ALLOW | `[LOCKED — Decision 1]` |
| HOLD, Ops analyst denies | BLOCK | `[LOCKED — Decision 1]` |

**Precedence rule, locked:** any failure signal (timeout, malformed output, unusable confidence,
unhandled exception) short-circuits directly to HOLD regardless of what `risk_level` says — fail-
closed always wins over any risk-level reading. This is not in tension with the open row above;
it's the opposite situation (the LLM answered cleanly, but with a *low* risk reading on a
signal-flagged case).

**The one open cell, stated precisely:** architecture §9 proposes a candidate rule for that row —
"the gate never downgrades toward ALLOW based on the LLM's word alone... a low-risk LLM read on a
signal-flagged case is treated as insufficient to override the signal... routes to HOLD" — but
states explicitly this "needs your explicit sign-off... not something Claude Code inferred
silently while building." **This plan does not implement or assume that rule.** The policy-gate
code (§Q, milestone M2) must make this gap visible — e.g., raise on that branch — rather than
default to either ALLOW or HOLD silently.

---

## J. Transaction state machine

**States:** `pending_evaluation` (transient, in-pipeline) → `allowed` (terminal) **or**
`held` (non-terminal) → [Ops resolves] → `allowed` (terminal) or `blocked` (terminal).

```
pending_evaluation ──gate: ALLOW───────────────▶ allowed        (terminal)
pending_evaluation ──gate: HOLD────────────────▶ held
held ──Ops confirms────────────────────────────▶ allowed        (terminal)
held ──Ops denies──────────────────────────────▶ blocked        (terminal)
held ──resolution timeout (automated)──────────▶ blocked        (terminal)
```

`transactions.state` and `cases.state` are updated together, atomically, in one DB transaction
(§C, §D step 7/9) — `held`/`allowed`/`blocked` on the transaction; `hold`/`resolved_allow`/
`resolved_block` on the case. They are kept as two fields rather than one because a transaction
can be `allowed` with no case ever existing (nominal path), while every `case` always maps to
exactly one transaction (§C).

**Invalid transitions, must be rejected:**
- `allowed → held` — a completed/allowed transaction can never be retroactively held.
- `blocked → *` (any) — BLOCK is terminal.
- `allowed → blocked` — terminal states don't transition to each other.
- `held → held` — resolving an already-resolved case returns `409`, no double-write (architecture
  §7); this also means a second `held` write is rejected, not silently accepted.
- Any direct external write to `transactions.state` outside of `domain/pipeline.py`'s
  orchestration — the state machine has exactly one writer.

---

## K. Auditability

Per case (`[LOCKED]`, product-spec §19, baseline §8): mandate snapshot at evaluation time,
computed signals, full evidence packet, full raw semantic-assessment response (including raw
confidence, per Decision 3 — stored even though the gate doesn't trust it uncritically), gate
decision + exact rule version, human resolution if applicable, timestamps at every stage.

**Nominal-path completeness, proposed rather than found explicit in any single spec sentence:**
architecture's audit-events table description says events precede case creation
(`case_id` nullable) and that "every stage writes to the Audit Event Log" — read together, this
implies a transaction that never crosses a threshold (the common case — "most mandates never
leave nominal") should still get **one** lightweight audit event (evaluated, threshold not
crossed, ALLOW), not zero. `[PROPOSED — a reading of architecture §3/§5 rather than a single
explicit sentence; flagged so the nominal path isn't under-audited by omission.]`

**Reconstruction requirement, locked:** given a `transaction_id`, the audit trail alone must let a
reviewer answer "why did this transaction receive its decision" without re-running the pipeline or
querying live LLM state — this is what audit-completeness (eval-design §15, target 100%) actually
measures.

---

## L. Evaluation architecture

- **Fixture structure:** `fixtures/{legitimate,drift,ambiguous}/` (brief's own naming, unchanged)
  plus `fixtures/failure_cases/` for the 7 injected fixtures. One file per case
  `[IMPL DETAIL — JSON vs YAML not spec'd]`, containing mandate + transaction stream +
  `ground_truth_label` + `drift_type` + `paired_with_id` + written `rationale` — mirroring the
  `dataset_cases` table columns (architecture §5).
- **Ground truth:** self-labeled by the builder, written rationale per case, fixed labeling rubric
  written before labeling starts; ambiguous cases labeled `abstain_expected` rather than forced
  binary; a second LLM pass may be used only as a labeling *sanity flag*, never cited as
  validation. `[LOCKED]` (eval-design §3)
- **Deterministic baseline:** the same Evidence Engine feeding a simpler fixed-threshold gate with
  **no LLM layer**, threshold swept and chosen on the dev set only (never the test set).
  `[LOCKED methodology; exact threshold value(s) depend on the still-`[OPEN]` signal formulas]`
- **Hybrid evaluation:** the full pipeline, same held-out test set, same scoring, same input
  format as the baseline — no architectural or prompt change between the two runs other than the
  presence of layer ②. `[LOCKED]`
- **Paired-scenario methodology:** Stage A generates candidate pairs; Stage B verifies via the
  exact `signal_match` formula (eval-design §2) — a pair failing the check is rejected and
  regenerated, never shipped as-is; rejection rate is logged as a finding in itself. `[LOCKED,
  formula given exactly in eval-design §2]`
- **Dev/test split:** split at the *pair* level (never splitting a pair across dev/test),
  stratified by drift type and category, target ratio ~38/62 dev/test. `[LOCKED split rules;
  `[OPEN]` exact counts — proposed 100 total, not signed off]`
- **Metrics:** the full formula set from eval-design §7–18, tiered 1/2/3 as specified there —
  reproduced by reference, not restated here to avoid drift between two copies of the same
  formulas. `[LOCKED]`
- **Failure-case testing:** the 7 injected fixtures (baseline §9); fixture #6 (contradictory
  internal signals) is explicitly **not scoreable** until §I's open disagreement-handling cell is
  resolved. `[LOCKED as a blocking dependency, not resolved here]`

**Kept explicitly `[OPEN]`, not decided by this plan:** exact fixture counts; `C_fp`/`C_fn` cost
values and the FN:FP ratio.

---

## M. Testing strategy

| Layer | What it covers | Depends on |
|---|---|---|
| Unit | Each evidence-engine signal function (pure, no DB) — golden-value tests once formulas are decided (§F); policy-gate decision-table tests for every `[LOCKED]` branch, with the open disagreement branch explicitly `xfail`/skipped with a comment linking to §I, never silently assumed; evidence-packet and LLM-output Pydantic schema tests, including the schema-boundary string-containment test (eval-design failure case #4). | M1/M2 (§Q) |
| Integration | Full pipeline with a mocked LLM client (deterministic canned responses) — every applicable failure-injection fixture (#1 timeout, #2 malformed, #3 low-confidence-high-risk, #4 adversarial text, #5 cold-start) must be green **before** real LLM integration begins, per architecture §18 phase 2. Fixture #6 stays out of scope until §I is resolved. | M2 |
| API | FastAPI `TestClient` against every endpoint in §E — bearer-auth enforcement on `/resolve`, idempotency replay, `409` on double-resolve, `404`s, invalid-input `400`s. | M4 |
| Policy | Automated check that gate-rule violation count = 0 (eval-design §14) over a representative synthetic input set, not eyeballed. | M2 |
| State-transition | Every invalid transition in §J is asserted rejected (resolve-an-already-blocked case, hold-an-already-allowed transaction, double-resolve). | M4 |
| LLM failure-path | Mocked-failure-path tests run in normal CI; real-API smoke tests (network-dependent) run separately/manually or in a gated CI job, not blocking every commit. | M3 |
| Evaluation | Unit tests on `eval/report.py`'s metric formulas against synthetic confusion-matrix inputs (precision/recall/F1/FPR/FNR); the `signal_match` pairing-verification formula tested in isolation. | M5/M6 |
| End-to-end | One full happy-path run (mandate → nominal transaction → ALLOW) and one full HOLD-to-resolution run (mandate → triggering transaction → HOLD → Ops resolve → BLOCK/ALLOW) through the real API + real DB with a mocked LLM, asserting the complete audit trail is reconstructable afterward. | M4 |

---

## N. Observability

- Structured (JSON) logs per pipeline stage, correlated by `case_id`/`mandate_id`/
  `transaction_id`. `[PROPOSED]` (architecture §12) — a debugging convenience, explicitly not the
  audit surface (audit is a product output, §K; logs are not required to be permanent).
- Per-stage latency captured inside the audit-event payload itself (`*_ms` fields), directly
  feeding eval-design §17's p50/p95/p99 metrics without separate observability infra.
  `[PROPOSED]`
- **What not to log:** avoid duplicating full mandate purpose text or full evidence-packet
  payloads into general-purpose application logs by default — keep verbose payloads in
  `audit_events` (append-only, grant-restricted) rather than a less access-controlled log
  aggregator. `[PROPOSED — a prudent extension of the brief's bounded-data principles to logging
  specifically; no spec sentence mandates this exact line, flagged as such rather than presented
  as settled.]`
- No production APM/metrics backend (Prometheus/Grafana) — a stated non-goal at this scale.
  `[LOCKED]` (architecture §12)

---

## O. Security

- **Authentication:** single static bearer token gates only `POST /cases/{id}/resolve`.
  `[LOCKED]` as the one consequential endpoint; ingestion auth remains `[OPEN]`.
- **Authorization:** no RBAC — a single Ops-analyst role is assumed, named openly in the repo and
  pitch as a demo-scale simplification, not hidden. `[LOCKED as a stated simplification]`
- **Input validation:** Pydantic schema validation at every API boundary; malformed input is
  rejected with `400`, never silently coerced.
- **Idempotency:** `idempotency_key` required on ingestion; a repeat submission with the same key
  returns the original decision without recompute. `[LOCKED]`
- **Injection risks:** the one adversarial-input class in scope is merchant/description free text
  attempting to inject instructions into the LLM's context — mitigated structurally, per §G, by
  the evidence packet having no field capable of holding such text. `[LOCKED]` General SQL
  injection is mitigated by standard parameterized-query/ORM practice — not a special project
  concern, not elaborated further here per operating rule not to expand scope.
- **Model-output validation:** strict schema validation on every LLM response; any deviation
  → HOLD, never partially trusted or best-effort parsed. `[LOCKED]`
- **Audit integrity:** DB-level `INSERT`/`SELECT`-only grant on `audit_events` (no `UPDATE`/
  `DELETE`) — an enforced mechanism, not just application-code discipline. `[PROPOSED]`
  (architecture §14)
- **Failure behavior:** every failure path (exception, timeout, malformed output, unusable
  confidence) → HOLD, never silent ALLOW. `[LOCKED — the core invariant]`

**Explicitly out of scope**, per operating rule 15 / product-spec §21: general auth/RBAC systems,
production-grade rate limiting, encryption-at-rest specifics, or any other security feature not
already named in the specification documents. Not elaborated here.

---

## P. Frontend boundary

*(No visual design performed here — data/contract needs only, per instruction.)*

- **Backend data needed:** exactly the responses already defined in §E for `GET /cases`,
  `GET /cases/{id}`, and `POST /cases/{id}/resolve`. No new endpoints are anticipated for the MVP
  frontend.
- **Required API contracts:** the three endpoints above, as specified — nothing additional.
- **Major user roles:** one — the Ops analyst. `[LOCKED — Decision 1]` No consumer role or screen
  exists in MVP.
- **Core screens implied by the locked Ops-only workflow:**
  1. Case list/queue (filterable by state) — reads `GET /cases`.
  2. Case detail (mandate, evidence packet, AI reasoning, gate rationale, resolve controls) —
     reads `GET /cases/{id}`, writes via `POST /cases/{id}/resolve`.
  3. The side-by-side Case-A/Case-B timeline visual (product-spec §23 item 1) — a specific demo
     artifact built from one committed eval results run, not a general application screen.

No wireframes, mockups, component trees, or visual fidelity decisions are made here — see §S for
the still-open frontend-fidelity decision.

---

## Q. Implementation order — dependency-aware milestones

| # | Objective | Files/components | Prerequisites | Tests required | Exit criteria |
|---|---|---|---|---|---|
| **M0** | Minimal runnable foundation | `main.py`, `config.py`, `db/session.py`, `api/health.py`, `docker-compose.yml` | none | `GET /health` test | `pytest` green; service boots via Docker Compose; empty-schema migration applies. |
| **M1** | Data model + Evidence Engine | `db/models.py`, `db/migrations/*`, `domain/evidence_engine/*.py` (incl. the `transactions.state`/`cases.transaction_id` addition from §C) | M0 | Unit tests per signal function; migration apply/rollback test | Schema migrates cleanly; each signal function has ≥1 passing test; **no DB dependency** in evidence-engine tests. Note: proceeds with placeholder/config-driven thresholds — exact formulas remain `[OPEN]` (§F). |
| **M2** | Semantic Risk Client + Policy Gate, mocked LLM | `domain/semantic_risk_client.py`, `domain/policy_gate.py`, `schemas/llm_output.py` | M1 | All applicable failure-injection fixtures (1–5) green against mock; decision-table unit tests; the open disagreement branch (§I) explicitly `xfail`/raises | Fixtures 1–5 pass; fixture #6 visibly blocked, not silently resolved; every `[LOCKED]` gate branch implemented. |
| **M3** | Real LLM integration | `domain/semantic_risk_client.py` (real call), `config.py` (model pin) | M2 | Schema-validation pass-rate check on hand-written cases; separate network-dependent smoke tests | Schema-validation passes on hand-written cases (no numeric bar specified in any spec — `[IMPL DETAIL]` acceptance threshold). |
| **M4** | Ingestion + Case + Audit APIs | `api/mandates.py`, `api/transactions.py`, `api/cases.py`, `auth.py`, `domain/pipeline.py` | M2 (real LLM from M3 not a hard blocker for API wiring) | API tests (§E errors, idempotency, 409 double-resolve, 401); state-transition tests (§J); end-to-end tests (§M) | Backend fully demoable via curl/Postman with no frontend — matches architecture §18 phase 4 bar. |
| **M5** | Paired-scenario dataset generation + verification | `fixtures/` population, generation helper (location `[IMPL DETAIL]`) | M1 (needs real Evidence Engine for Stage B) | `signal_match` formula unit test; rejection-rate logging check | A small verified paired batch exists; full-scale generation stays blocked on the fixture-count sign-off (`[OPEN]`). |
| **M6** | Evaluation harness | `eval/run.py`, `eval/report.py` | M4 (in-process pipeline import), M5 (fixtures) | Metric-formula unit tests; dry run on dev set | Dev-set run produces a results file with every Tier 1 metric populated; `--confirm-locked-test-set` path exists but is not yet invoked. |
| **M7** | Minimal frontend | `frontend/` (new) | M4 (stable API contracts) | Manual/smoke only — no automated frontend suite mandated by spec | An Ops analyst can view and resolve a HOLD case end-to-end through the UI. Fidelity level (`[OPEN]`, §P) decided before this milestone starts. |
| **M8** | Locked test-set run + demo assembly | none new | M6, M7, **and** every scoring-relevant `[OPEN]` item in §S resolved | none new — terminal, non-repeatable step | Results file committed as source of truth; pitch numbers traceable to it; Case-A/Case-B visual uses that same run's actual result. |

---

## R. Risks — 10 highest-risk implementation assumptions

1. **Placeholder signal thresholds (M1) get treated as final** if not loudly marked TODO/config-
   driven — risk of quietly anchoring on values nobody actually signed off on.
2. **The open disagreement-handling cell (§I) gets a silent default** under deadline pressure
   instead of staying visibly blocked — mitigate by making the gate raise/fail on that branch, not
   fall through to a default.
3. **Confidence handling regresses toward architecture §8's superseded proposal** if a future
   contributor reads `architecture.md` directly instead of the baseline's Decision 3 — the two
   documents now disagree by design, and only the baseline is current.
4. **Ingestion staying unauthenticated past a public demo deployment** — low stakes given
   synthetic data, but worth resolving before any public link ships, not left open indefinitely.
5. **Retry-policy drift between test suite and later "fix flakiness" changes** — if someone adds
   retry logic to the real LLM client later without revisiting the still-open decision, the mocked
   test suite (built assuming no retry) and production behavior could silently diverge.
6. **Evidence-packet schema creep** — adding a `notes`/`description` free-text field for debugging
   convenience would silently reopen the merchant-content-injection surface architecture
   structurally closed. Mitigate with the schema-boundary integrity test running in CI on every
   change, not just once at build time.
7. **Bulk dataset generation (M5) before fixture-count sign-off** wastes labeling effort if counts
   change later — generate a small illustrative batch first, defer bulk generation.
8. **The `cases.transaction_id`/`transactions.state` addition (§C) gets missed** because it isn't
   explicit in architecture §5's original ERD — it's a consequence of Decision 2, not an optional
   extra; flagged here so it isn't dropped during schema authoring.
9. **Nominal-path (non-triggering) transactions under-audited** if audit-event writing is wired
   only into the HOLD path during early development — test the nominal path's audit trail
   explicitly (§K), not just the HOLD path.
10. **Frontend scope creep (M7)** — because fidelity is `[OPEN]`, there's real risk of building
    dashboard polish explicitly named as a non-goal (product-spec §23) before core pipeline/eval
    work is solid. Mitigate by sequencing frontend strictly after M4–M6, not in parallel.

---

## S. Open decisions

| Decision | Why it matters | When it must be decided | Current status |
|---|---|---|---|
| Disagreement-handling rule (signals vs. LLM risk) | Fills the one undecided row of the policy-gate decision table (§I); unblocks failure fixture #6 | Before M2 exit | `[OPEN]` — architecture §9 proposes a candidate, not signed off |
| Retry policy on malformed LLM output | Determines Semantic Risk Client behavior and fixture #2/#3 determinism | Before M2/M3 | `[OPEN]` — two docs lean no-retry, neither locks it |
| Ingestion authentication | API auth model; demo-link exposure | Before M4, and before any public deployment | `[OPEN]` |
| Velocity/pattern-consistency signal split | Would change Evidence Engine component boundaries | Before M1 finalizes signal contracts (current 3-signal shape can proceed either way) | `[OPEN]` — must not be silently adopted |
| Exact signal formulas/thresholds | Needed for real (non-placeholder) M1 values and the baseline's threshold sweep | Before M1 exit / M6 | `[OPEN]` — unspecified in any doc |
| `risk_level` exact enum values | Needed to finalize the LLM output schema and gate matching logic | Before M2/M3 | `[OPEN, unspecified]` — presumed low/medium/high by analogy, never stated explicitly |
| Exact fixture counts | Labeling effort, M5 scope | Before M5 bulk generation | `[OPEN]` — 100 proposed, not signed off |
| `C_fp` / `C_fn` cost values and ratio | Needed for the business-metric calculation (eval-design §9) | Before M6/M8 | `[OPEN]` |
| MVP frontend fidelity | M7 scope/time budget | Before M7 starts | `[OPEN]` |
| Secondary metrics scope (time-to-resolution, etc.) | Affects M6 metric set | Before M6 exit | `[OPEN]` |
| Taxonomy reflection in eval-doc prose | Minor consistency fix, not a real decision | Before M5 | `[OPEN, minor]` |

---

## Proposed C4 implementation roadmap (for review before any code is touched)

```
M0 Foundation ──▶ M1 Data model + Evidence Engine ──▶ M2 Gate + mocked LLM ──▶ M3 Real LLM
                                                              │
                                                              ▼
                                            M4 Ingestion/Case/Audit APIs (demoable via curl)
                                                        │              │
                                                        ▼              ▼
                                          M5 Dataset gen/verify   M6 Evaluation harness
                                                        └──────┬───────┘
                                                                ▼
                                                        M7 Minimal frontend
                                                                ▼
                                              M8 Locked test-set run + demo assembly
                                              (blocked until every scoring-relevant
                                               OPEN item in §S is resolved)
```

**Gating rule proposed for this roadmap:** M1 and M2 can start now with placeholder
thresholds/config-driven stubs, since their *shape* doesn't depend on the open decisions — but
neither milestone's tests should assert a specific threshold value or a specific answer to the
disagreement-handling question until §S's relevant rows are resolved. M8 is explicitly gated on
every scoring-relevant open item, matching the "locked test set touched exactly once" requirement.

This plan makes no code changes. Awaiting review before M0 begins.
