# Implementation Baseline — Mandate Drift Guard

*Derived from `docs/spec/*.md` on 2026-08-30. This document is a synthesis, not a new design —
it does not resolve any contradiction or open decision found in the source specs. Every claim
below is tagged and traced to its source. `CLAUDE.md` was found empty at the time of writing, so
it imposes no additional constraints here.*

**Tags used throughout:**
- `[LOCKED]` — stated as settled/non-negotiable in the brief (Status: "Concept is locked,
  red-team tested") or repeated identically, unchallenged, across all docs that touch it.
- `[PROPOSED]` — a later document (architecture or eval design) offers a specific answer to a
  question the brief/product spec left open, but explicitly asks for sign-off before it's treated
  as decided.
- `[OPEN]` — no document proposes an answer, or the documents disagree; requires a human decision
  before implementation touches this area.
- `[CONTRADICTION]` — two or more source documents make claims that cannot both be implemented
  as written; flagged for explicit resolution, not resolved here.
- `[LOCKED — Decision N]` — resolved via explicit human sign-off during implementation-baseline
  review, distinct from items the original spec documents themselves called settled. Decisions
  1–3 (below) are dated 2026-08-30 and supersede any conflicting spec-document language noted at
  each site.

**Revision note (2026-08-30):** three previously-flagged items — HOLD resolution ownership (§7),
HOLD semantics (§7), and confidence treatment (§11) — have been resolved by explicit human
decision and are now `[LOCKED]`. All other `[OPEN]` items below are unchanged and remain open.

---

## 1. Project objective

A merchant/platform-side risk system that watches an AI shopping agent's *transaction trajectory*
against the natural-language *mandate* a consumer granted it, and detects when the aggregate
pattern — not any single purchase — has drifted from what was authorized. Deterministic logic
owns everything measurable; a single bounded AI reasoning step owns only the interpretation of
whether a pattern still matches a stated natural-language intent. `[LOCKED]`
(brief: "Core thesis", "One-line pitch"; product-spec §6 "Product thesis")

Reframed explicitly as a **chargeback-prevention layer for the agentic-commerce rail** (Track 02
alignment), not a consumer-protection tool: undetected drift becomes a dispute vector, and the
platform absorbs the reputational/operational cost. `[LOCKED]`
(brief: "Validation & Open Items" — "Structural fix"; product-spec §1, §4)

The system is for a Risk/Trust Ops analyst, not the consumer — the consumer's spending is the
*subject analyzed*, not who the tool serves. `[LOCKED, but see §12 contradiction below on the
consumer's role at HOLD resolution]`
(brief: "Who this is for"; product-spec §2, framed as `[DECISION]` there but treated as settled
recommendation carried into architecture §15)

---

## 2. Core system flow

```
MANDATE (purpose, budget, period, allowed categories)
        │
        ▼
TRANSACTION STREAM
        │
        ▼
① DETERMINISTIC EVIDENCE ENGINE   — signals only, no AI, no interpretation
        │
        ▼
② SEMANTIC RISK ASSESSMENT        — the only place AI is used; structured in, structured out
        │
        ▼
③ DETERMINISTIC POLICY GATE       — owns ALLOW / HOLD / BLOCK
        │
        ▼
AUDIT LOG (append-only, every layer's output, every stage)
```
`[LOCKED]` (brief "Architecture"; product-spec §8; architecture §2, explicitly stated as
not reopened by the architecture doc)

**Chosen execution model:** Architecture A — synchronous monolith. One backend service; an
ingestion request runs evidence engine → LLM call → gate inline, in-process, returns the decision
in the same HTTP response. No queue, no workers, no event bus. The evaluation harness imports the
same pipeline code in-process, not via HTTP. `[PROPOSED — engineering decision made by the
architecture doc, not on its own explicit sign-off checklist, but justified at length against a
rejected event-driven alternative (Architecture B) and a rejected autonomous-agent alternative
(Architecture C, rejected outright as violating product-spec §8 and the no-execution-authority
principle)]`
(architecture §1, §2)

---

## 3. Deterministic signal definitions

**Locked count and identity, with one explicitly unresolved architecture change flagged by the
brief itself:**

- Spend velocity classification `[LOCKED as a signal that must exist]`
- Category-distribution shift magnitude `[LOCKED as a signal that must exist]`
- Transaction clustering `[LOCKED as a signal that must exist]`

Brief and product-spec both describe this as "2–3 signals"; architecture §3/§5 describes exactly
these three with no hedging. No document gives exact formulas/thresholds for any of the three —
that is implementation-level detail not yet specified anywhere. `[OPEN — signal formulas/
thresholds not specified in any spec document]`

**`[OPEN — explicitly flagged, do not silently adopt]`**: a proposed architecture change (from an
external review dated Aug 29, referred to in the brief as "Dad's review") would split velocity
into a fast-spike-specific velocity signal and a separate, distinct "pattern-consistency" signal
for slow-drift cases (on the reasoning that velocity is "noisy and largely irrelevant" for slow
drift). The brief is explicit: *"This is a real architecture change, not an addition — needs
explicit sign-off before it's implemented."* No other document engages with this proposal one way
or the other. **This baseline does not adopt it.**
(brief: "Open item — NOT yet decided, do not silently adopt")

**`[LOCKED, separately]`**: a broadened mandate-category taxonomy (bills, fuel, house help,
telephone — not just groceries) was "separately adopted without objection" and "should be
reflected in the synthetic dataset design." Note: eval-design §2 Stage A's dataset methodology
text still only mentions "groceries, household essentials, etc." and does not explicitly enumerate
the broadened taxonomy — flagged as a consistency gap to close during dataset design, not a
contradiction requiring a decision (the taxonomy itself is settled; only its reflection in the
eval doc's prose is incomplete). `[OPEN — consistency gap, not a decision]`
(brief: "Open item"; evaluation-design §2)

---

## 4. Evidence packet definition

Structured object passed from layer ① to layer ②. Exact example schema, given in the brief and
unchanged since:

```json
{
  "mandate": {"purpose": "weekly household groceries", "budget": 8000, "period_days": 7, "allowed_categories": ["groceries", "household essentials"]},
  "signals": {"budget_utilization": 0.91, "spend_velocity": "elevated", "category_shift": "significant"},
  "trajectory": {"historical_distribution": "...", "current_distribution": "..."}
}
```
`[LOCKED]` (brief "Architecture" — "Evidence packet — what layer ② receives")

**Structural safety property, locked:** the evidence-packet schema must have **no field capable of
carrying arbitrary merchant-supplied free text** into the LLM's instruction context. Category and
merchant name are constrained to short structured values only. This is enforced as a schema
property, verified by a string-containment test — not a prompt-level defense, and explicitly not a
claim to solve prompt injection generally. `[LOCKED]`
(architecture §4 step 1, §14; product-spec §15, §16; eval-design failure case #4)

Only a rolling recent window (illustratively 6–8 weeks) of transaction history is used to compute
signals — not an unbounded history. `[LOCKED]` (brief "Data & privacy design principles")

Storage-level detail: one `evidence_packets` row per *triggered* evaluation (i.e., per case where a
deterministic threshold was crossed), not one per transaction — most transactions never generate
one. `[PROPOSED — data-model detail from architecture §5, not contested elsewhere]`

---

## 5. LLM responsibility ("Semantic Risk Assessment", layer ②)

- **Input boundary**: the evidence packet only. No raw transaction rows, no merchant free text, no
  live DB access, no ability to request more data. `[LOCKED]` (product-spec §9, §11, §16;
  architecture §4 step 1)
- **Call shape**: one stateless completion call per triggered case. No conversational memory
  across calls, no retrieval, no few-shot examples in MVP. `[LOCKED, mostly]` — few-shot addition
  during dev-set iteration is explicitly left open as a possibility, tracked via `prompt_version`
  if added. (product-spec §8; architecture §4 step 2)
- **Output**: fixed schema — `mandate_alignment` (low/medium/high), `risk_level`, `confidence`
  (0–1), `evidence[]` (short natural-language justifications). Obtained via a **forced single tool
  call** (`tool_choice` forcing one `emit_risk_assessment` call) as a structured-output mechanism,
  explicitly *not* agentic tool use — the "tool" has no side effects and executes nothing.
  `[PROPOSED — architecture §4 step 3 pre-empts a specific interviewer objection ("does forced
  tool-calling contradict 'no tools in the agentic sense'?") with a defensible answer; not
  contested by any other doc]`
- **Never returns a plain ALLOW/HOLD/BLOCK.** That mapping belongs entirely to the gate. `[LOCKED]`
  (brief; product-spec §12, §15)
- **Zero execution authority**, structurally enforced (validated Pydantic/typed return object, no
  downstream code path interprets LLM text as instructions or executable code). `[LOCKED]`
  (product-spec §13; architecture §4 step 6, §14)
- **Self-reported `confidence` is not trusted uncritically.** It is never treated as an
  automatically calibrated probability; missing, malformed, or otherwise unusable confidence is
  treated as uncertain and routes to HOLD via the fail-closed invariant (§6). Whether raw
  confidence correlates with actual correctness is a question the evaluation harness answers
  empirically on the dev/test datasets — not something the gate assumes or is defaulted around.
  `[LOCKED — Decision 3, see §11]`
  (brief "New risk... confidence calibration"; product-spec §16; eval-design §8)
- **Model pinning**: an exact model string in config, not resolved at request time, for run-over-
  run reproducibility (matters for the locked-test-set protocol). `[PROPOSED — architecture §8,
  uncontested]`

---

## 6. Policy gate responsibility (layer ③)

- Sole owner of the ALLOW / HOLD / BLOCK mapping. Never calls the LLM, never computes evidence.
  `[LOCKED]` (architecture §3)
- **Fail-closed invariant, hard, non-tunable**: any of {LLM timeout, malformed/non-schema output,
  low confidence, unhandled pipeline exception} routes to HOLD, never to silent ALLOW.
  `[LOCKED]` (product-spec §15, §16, §17; architecture §10, §17; eval-design §13 — "must equal
  100%, a hard invariant, not a metric with an acceptable range")
- Thresholds/rules stored as a versioned config object (`policy_version`); every gate decision
  records which version produced it. `[PROPOSED — architecture §9, uncontested, needed for
  eval-design §14's violation-count metric and the rules-only baseline's reproducibility]`
- **Disagreement-handling rule** (deterministic signals vs. LLM-reported risk pointing in
  different directions): `[OPEN — explicitly unresolved by product-spec §17 and blocks
  eval-design failure case #6 from being scored]`. Architecture §9 offers one specific candidate
  rule ("the gate never downgrades toward ALLOW based on the LLM's word alone; a low-risk LLM read
  on a signal-flagged case routes to HOLD, not ALLOW") but states plainly this **"needs your
  explicit sign-off... the answer needs to be yours, defensible on the stand, not something Claude
  Code inferred silently while building."** This baseline does not treat it as adopted.
- **Retry policy on malformed LLM output**: `[PROPOSED, not yet locked]` — architecture §10
  proposes no retry, straight to HOLD (matching eval-design §16's stated preference), with exactly
  one transport-level retry (5xx/connection error only) before HOLD-on-timeout. Eval-design's own
  open-items list (§ "Open items carried into this design", item 4) still lists this as needing an
  explicit decision, and architecture's own final sign-off checklist (item 3) repeats it as
  outstanding. **Two documents propose the same answer but neither claims it as locked.**

---

## 7. HOLD / ALLOW semantics

```
EVALUATING
   ├── low risk, high confidence ─────────────────────▶ ALLOW (terminal)
   └── medium/high risk, OR any uncertainty/
       timeout/malformed-output ──────────────────────▶ HOLD (triggering transaction held, does
                                                              not complete)
                                                            ├── Ops analyst confirms ─▶ ALLOW (terminal)
                                                            ├── Ops analyst denies ───▶ BLOCK (terminal)
                                                            └── resolution timeout ───▶ BLOCK (terminal)
```
`[LOCKED]` (brief "HOLD is a real state, never a dead end"; architecture §6, stated as reproduced
"exactly matching the brief's fail-closed diagram")

**BLOCK is never reached directly from EVALUATING — only through HOLD.** `[LOCKED]`
(architecture §6)

**`[LOCKED — Decision 1, human sign-off 2026-08-30]` HOLD resolution is Ops-analyst-only in the
MVP.** An authorized operations analyst resolves every HOLD via the case-detail UI
(`POST /cases/{id}/resolve`, bearer-token auth). There is **no consumer-facing HOLD confirmation
flow** in the MVP — do not design or implement one unless explicitly requested later. This
resolves the earlier `[CONTRADICTION]` between the brief's "the consumer's only interaction is
answering yes/no during a HOLD" framing and architecture §15's carried Ops-analyst-only decision,
**in favor of the architecture's version**: the product is a merchant/platform-side risk
*operations* system, so the primary — and, for MVP, only — human workflow is Ops review. The
brief's "consumer's only interaction" language is superseded by this decision and should not be
read as describing the MVP build.

**`[LOCKED — Decision 2, human sign-off 2026-08-30]` HOLD applies to the triggering transaction
attempt, not a future one.** The specific transaction whose ingestion crossed a deterministic
threshold and produced a HOLD does not complete while the case is open. For the synthetic
prototype this is represented explicitly as a **held transaction state** in the data model (see
§12) — not merely a UI flag layered on an otherwise-completed transaction. This resolves the
earlier phrasing mismatch with product-spec §7 step 5 ("the *next* agent transaction attempt is
paused"): that phrasing is superseded. Do not reinterpret HOLD as pausing a future transaction.

Resolution timeout implemented as a lightweight scheduled check; timeout → BLOCK, always. `[LOCKED
requirement, `[PROPOSED]` mechanism — architecture §15, "asyncio background task or simple cron-
triggered endpoint," no durable job scheduler needed at demo scale]`

---

## 8. Audit requirements

- **Append-only.** No deletion, no silent overwrite, ever. `[LOCKED]` (brief; product-spec §19)
- One audit record per case containing: mandate snapshot at evaluation time, computed signals,
  full evidence packet sent to layer ②, full raw semantic-layer response (including raw
  self-reported confidence, even when the gate doesn't trust it), gate decision and the exact rule
  version that produced it, human resolution if applicable, timestamps at every stage. `[LOCKED]`
  (product-spec §19; architecture §4 step 9)
- Must be sufficient **on its own** to reconstruct and defend any single decision in a panel
  interview. `[LOCKED]` (product-spec §19)
- DB-level (not just application-level) enforcement: the app's DB role has `INSERT, SELECT` only
  on `audit_events` — no `UPDATE`/`DELETE` grant. `[PROPOSED — architecture §14, a concrete,
  checkable enforcement mechanism uncontested elsewhere]`
- Audit completeness must be measured directly, not assumed: `Audit completeness rate` = fraction
  of cases with every required field populated; target 100%. `[LOCKED as an eval requirement]`
  (eval-design §15)

---

## 9. Evaluation requirements

Evaluation is stated as **"the actual center of the project"** — not a validation step tacked on
after building. `[LOCKED]` (brief; eval-design entire document, opening line: "This is designed
before any implementation code... It is the thing the product must be built *against*")

**Mandatory core claim structure, all `[LOCKED]`:**
- Paired scenarios: cases matched on every deterministic signal, opposite in ground-truth label
  (e.g., one-time legitimate spike vs. slow drift, built to near-identical total spend,
  transaction count, velocity classification, category-shift magnitude bucket).
- Mandatory rules-only-vs-hybrid baseline comparison on the same held-out set, **reported honestly
  even if the hybrid doesn't win outright.**
- Metrics computed **separately for fast-spike and slow-drift subsets, never blended** into one
  aggregate number.
- Dev set (visible, used for prompt/threshold iteration) vs. locked test set (touched exactly
  once, at the end) — split at the *pair* level, stratified by drift type and category.
- Anti-cherry-picking rule: every reported number comes from a single batch script run over the
  entire locked test set in one pass, written to a results file before any number reaches the
  pitch deck. The demo's side-by-side visual must use a case ID from that same batch run's actual
  recorded result.

**Exact formulas locked in eval-design.md, not to be reinvented during implementation:**
Precision, Recall, F1, FPR, FNR (§7); confidence–correctness point-biserial correlation and Brier
score (§8); cost-weighted outcome `Total_cost = FP_count × C_fp + FN_count × C_fn` and
`Drift_cases_caught_only_by_hybrid` (§9); abstention metrics on the ambiguous category (§12);
fail-closed compliance rate and silent-ALLOW-on-failure count, target 100% / 0 respectively (§13);
gate-rule violation count and mandate-mutation count, target 0 (§14); audit completeness rate,
target 100% (§15); schema-validation pass rate, retry rate, pipeline error rate (§16); p50/p95/p99
latency (§17); cost per case and total run cost (§18).

**Tiering** (from eval-design.md, load-bearing distinction — build Tier 1 first and completely,
Tier 2 only if time remains, Tier 3 optional/omit-honestly):
- Tier 1: primary precision/recall/FPR/FNR by drift type (§7); business/cost metrics (§9–11);
  abstention (§12); safety/fail-closed metrics (§13); policy-violation metrics (§14); audit
  completeness (§15).
- Tier 2: confidence calibration (§8); reliability metrics (§16); latency (§17); AI cost (§18).

**Seven deliberately injected failure fixtures** (separate from the main dataset, feed §13/§14
directly): simulated LLM timeout; malformed LLM output; low-confidence-despite-high-stated-risk;
adversarial instruction text embedded in a merchant/description field; cold-start (no historical
baseline); contradictory internal signals (**blocked pending the disagreement-handling rule
decision, §6 above**); ambiguous threshold-boundary case. `[LOCKED as required fixtures; fixture
#6 is explicitly non-scorable until §6's open decision is made]`

Ground truth is self-labeled by the builder with a written rationale per case — a stated
limitation, mitigated by a fixed labeling rubric written before labeling starts, not by using a
second LLM call as a "validator" (explicitly forbidden as a credibility risk). `[LOCKED]`
(eval-design §3)

---

## 10. Dataset requirements

- Three fixture categories on disk: `fixtures/legitimate/`, `fixtures/drift/`,
  `fixtures/ambiguous/` — static, labeled data, **not** attack payloads, **not** anything
  executable, **no generator/reusable-exploit tool** (Track 02's defense-only framing is a "hard
  rule, not a suggestion"). `[LOCKED]` (brief "Defense-only framing")
- Unit of analysis = one case = one mandate + its full transaction-stream trajectory, not one
  transaction. `[LOCKED]` (eval-design §1)
- Two-stage generation: Stage A generates candidate paired narratives (fast-spike-legitimate vs.
  slow-drift, built to land on the same deterministic-signal profile); Stage B **verifies** the
  pairing by running the real evidence engine and checking an exact quantitative match condition
  (`signal_match` formula, eval-design §2) — a pair failing this check is rejected and regenerated,
  not shipped as-is. Rejection rate itself is logged as a potential finding. `[LOCKED, formula
  given exactly]`
- **`[OPEN — needs sign-off against the 8-day clock]`**: proposed counts — 38 dev / 62 test / 100
  total, split 15/25 legitimate-paired, 15/25 drift-paired, 8/12 ambiguous (unpaired,
  abstention-only). Eval-design's own guidance: if the count must shrink, cut *count*, never the
  *pairing/verification discipline*. (eval-design §1)
- **`[OPEN]`**: exact ₹ values for `C_fp` (false-positive/customer-friction cost) and `C_fn`
  (false-negative/undetected-exposure cost), and the FN:FP ratio (recommended 5–10× but stated as
  an assumption needing a one-line sensitivity check against at least one alternative ratio).
  (eval-design §10, §11)

---

## 11. Confidence treatment and calibration

**`[LOCKED — Decision 3, human sign-off 2026-08-30]`** LLM-reported `confidence` is **not** treated
as an automatically calibrated probability. It is represented and stored as exactly what it is —
the model's raw self-reported value — kept structurally separate from any notion of "how
trustworthy this number actually is." Nothing in the pipeline is permitted to treat a high
self-reported confidence value as, by itself, evidence of correctness.

**If `confidence` is missing, malformed, or otherwise unusable** (fails schema validation, out of
range, absent from the tool-call arguments), the result is treated as **uncertain** — not repaired,
not defaulted, not best-effort parsed. Because the system is fail-closed (§6), uncertainty routes
to HOLD, exactly as timeout and malformed-output already do. This is a direct extension of the
fail-closed invariant already locked in §6, applied explicitly to the confidence field.

**Explicitly rejected**: the architecture doc's earlier proposal of a `confidence_source` config
flag that silently substitutes an evidence-packet-completeness proxy for raw confidence (defaulting
to the proxy) is **not adopted**. Do not introduce an evidence-completeness confidence proxy
anywhere in the gate logic.

**Calibration, if included, is evaluated — not assumed.** Whether self-reported confidence
correlates with actual correctness is a question for the evaluation harness to answer empirically
on the dev/test datasets (point-biserial correlation + Brier score, eval-design §8) — it is a
*measurement* to report honestly, not a mechanism the gate leans on by default or falls back to.
The gate's fail-closed behavior on missing/malformed/unusable confidence (above) does not depend
on the outcome of that measurement.

This decision supersedes architecture §8's `confidence_source` proposal and closes the item
previously flagged as "product-spec reserved this for human decision, architecture appears to have
silently resolved it" (product-spec open ambiguity #5) — Decision 3 is the human resolution.

---

## 12. Planned backend components

*(Traceable to architecture.md §2–§13; presented here as what the specs describe, not as approval
to build — architecture doc's own open-decisions items still apply.)*

| Component | Responsibility |
|---|---|
| Ingestion API | Accept + validate a transaction event, invoke pipeline synchronously, return decision. Idempotent via caller-supplied `idempotency_key`. |
| Evidence Engine (①) | Pure, unit-testable functions computing velocity, category-shift, clustering from transaction stream + historical baseline. No side effects. |
| Semantic Risk Client (②) | Builds prompt from evidence packet, one forced-tool-call Anthropic API call, validates response against schema. Never writes to DB directly. |
| Policy Gate (③) | ALLOW/HOLD/BLOCK mapping via versioned threshold config. Sole owner of fail-closed logic. |
| Case Store | Read-optimized current-state view; only cases that reached HOLD or later get a row. |
| Audit Event Log | Append-only, insert/select-only DB grant, one row per stage per case. |
| Evaluation Harness (`eval/run.py`, `eval/report.py`) | Imports pipeline modules directly (no HTTP), runs fixtures + failure-injection cases, writes results file; separate script recomputes metrics from that file. |
| Postgres schema | `mandates`, `transactions`, `evidence_packets`, `semantic_assessments`, `gate_decisions`, `cases`, `audit_events`, `dataset_cases` — full column lists in architecture §5. |
| API surface | `POST /mandates`, `POST /mandates/{id}/transactions` (auth status `[OPEN]`, see below), `GET /cases`, `GET /cases/{id}`, `POST /cases/{id}/resolve` (bearer-token auth, `[LOCKED]` as the one auth-gated endpoint whose *audience* is itself under `[CONTRADICTION]`, see §7), `GET /health`. |

**`[OPEN]`**: whether `POST /mandates/{id}/transactions` (ingestion) also requires auth, or stays
open for demo/seed convenience. (architecture §7, final sign-off item 4)

**`[LOCKED — Decision 2]`**: the transaction record that triggers a HOLD must carry an explicit
held state (e.g., a `held` value alongside whatever in-flight/completed states the `transactions`
table otherwise uses) — held-ness is a property of the transaction itself, not only of the
associated `case` row's state. The transaction does not transition to completed while its case is
open.

---

## 13. Planned frontend components

- A **minimal case view**, not a full Ops dashboard: case list (filterable by state) and case
  detail (mandate, evidence packet, full AI reasoning, gate rationale, resolve action). `[LOCKED
  as MVP scope ceiling]` (product-spec §22 — "likely a simple case view... Don't build dashboard
  polish before the pipeline and evaluation are solid")
- Exact interface fidelity (rendered JSON diff vs. an actual built UI) is `[OPEN]` — product-spec
  lists this explicitly as an unresolved time-budget decision. (product-spec open ambiguity #4)
- Stretch-promoted-to-first-priority-after-core: a side-by-side timeline visual (Case A vs. Case
  B, near-identical charts, one HOLD/one ALLOW, evidence packet shown) — explicitly the
  highest-leverage pitch moment, but still sequenced *after* core pipeline + eval are solid and
  honest. `[LOCKED priority, but still gated behind core completion]` (brief "Demo fix"; product-
  spec §23 item 1; architecture §18 phase 8)
- **`[LOCKED — Decision 1]`** Audience of the frontend is the Ops analyst only. No consumer-facing
  confirmation surface is in scope for MVP; do not build one unless explicitly requested later.

---

## 14. Explicit non-goals

`[LOCKED]` (product-spec §21; brief "Explicitly excluded scope"; "Things we are deliberately NOT
building" in explained-for-me.md):

- Detecting merchant-content manipulation or prompt injection into the agent — a different
  problem, deliberately excluded to avoid diluting focus.
- Enforcing numeric spending caps — that's NPCI/bank-level infrastructure, already solved, out of
  scope entirely.
- Solving cold-start (brand-new agent, no purchase history) — named limitation, not solved; MVP
  behavior is a conservative default, not a crash and not a silent skip.
- General-purpose fraud detection — this is narrowly a mandate-trajectory-vs-intent classifier.
- Real-time transaction-blocking infrastructure at payment-rail speed — a risk-assessment layer,
  not a claim to race NPCI-level authorization latency.
- Real Razorpay integration of any kind — synthetic-data prototype end to end, no live payment
  paths anywhere in the system.
- Any AI execution authority — the AI never ALLOWs, HOLDs, BLOCKs, touches the transaction stream,
  or modifies the mandate.
- Fine-tuning a model — an existing model is used as-is.
- An autonomous multi-tool agent architecture — considered and rejected outright (architecture
  §1, "Architecture C").
- An event-driven/queue-based pipeline — considered and rejected for this scale (architecture §1,
  "Architecture B"), named as a stated future-scaling non-goal with a reason, not built now.

---

## 15. Open implementation questions (consolidated)

Three items previously listed here — who resolves a HOLD, what HOLD pauses, and the
confidence-calibration fallback scope — are now resolved by Decisions 1–3 (§7, §11) and have been
removed from this list. Everything below is unchanged and remains open; organized by the
implementation phase (architecture §18) where each first becomes blocking, per instruction not to
resolve any of them silently.

**Phase 1 — Evidence Engine**
- **`[OPEN]`** Dad's-review proposal to split the velocity signal into fast-spike-specific
  velocity + a separate slow-drift pattern-consistency signal — brief explicitly says do not
  silently adopt. (§3 above)
- **`[OPEN, not specified anywhere]`** Exact numeric formulas/thresholds for the three
  deterministic signals themselves (velocity classification bands, category-shift magnitude
  buckets, clustering method) — no source document gives these; pure implementation detail still
  to be authored, respecting the eval-design §2 pairing-verification formula as a downstream
  constraint on whatever is chosen.

**Phase 2 — Semantic Risk Client + Policy Gate**
- **`[OPEN]`** Disagreement-handling rule between deterministic signals and LLM-reported risk —
  architecture §9 proposes one candidate, explicitly not yet signed off; blocks eval-design
  failure case #6 from being scored. (§6 above)
- **`[OPEN]`** Retry policy on malformed LLM output — no-retry-straight-to-HOLD (proposed by two
  docs) vs. retry-then-HOLD. (§6 above)

**Phase 4 — Ingestion + Case + Audit APIs**
- **`[OPEN]`** Whether ingestion (`POST /mandates/{id}/transactions`) requires auth. (§12 above)

**Phase 5 — Dataset generation + verification**
- **`[OPEN]`** Exact fixture counts (proposed 100 total: 38 dev / 62 test) against the 8-day
  clock. (§10 above)
- **`[OPEN]`** Exact `C_fp` / `C_fn` cost values and the FN:FP ratio. (§10 above)
- **`[OPEN, minor]`** Reflect the broadened mandate-category taxonomy explicitly in the
  eval-design dataset-generation prose (currently only in the brief). (§3 above)

**Phase 6 — Evaluation harness**
- **`[OPEN]`** Whether time-to-resolution / analyst-throughput secondary metrics are MVP-required
  or deferred (product-spec open ambiguity #6). No longer blocked on an undecided primary user —
  Decision 1 confirms Ops-analyst-primary — but the metric-scope question itself is still
  unanswered.

**Phase 7 — Frontend**
- **`[OPEN]`** MVP frontend fidelity — rendered JSON diff vs. an actual built UI. (§13 above)

---

## 16. Traceability index

| Baseline section | Primary source(s) |
|---|---|
| §1 Objective | brief (Core thesis, Who this is for); product-spec §1, §4, §6 |
| §2 Core flow | brief (Architecture); product-spec §8; architecture §1, §2 |
| §3 Signals | brief (Open item, Core MVP); product-spec §22; architecture §3, §5 |
| §4 Evidence packet | brief (Architecture, Data & privacy); product-spec §9, §11, §16; architecture §4, §5, §14; eval-design failure case #4 |
| §5 LLM responsibility | product-spec §8, §9, §11, §13, §15, §16; architecture §4, §8 |
| §6 Policy gate | architecture §3, §9, §10, §17; product-spec §15–17; eval-design §13, §14, §16, failure case #6 |
| §7 HOLD/ALLOW semantics | brief (HOLD is a real state, Who this is for); product-spec §2, §7, §14, §17, open ambiguities #1–2; architecture §6, §15; **Decisions 1–2 (human sign-off, 2026-08-30)** |
| §8 Audit | product-spec §19; architecture §4 step 9, §14; eval-design §15 |
| §9 Evaluation | brief (Evaluation methodology); eval-design entire document |
| §10 Dataset | brief (Defense-only framing); eval-design §1, §2 |
| §11 Confidence treatment and calibration | brief (New risk); product-spec §16, open ambiguity #5; architecture §8 (superseded); **Decision 3 (human sign-off, 2026-08-30)** |
| §12 Backend components | architecture §2–§7, §5, §13; Decision 2 |
| §13 Frontend components | product-spec §22, §23; brief (Demo fix); architecture §18; Decision 1 |
| §14 Non-goals | product-spec §21; brief (Explicitly excluded scope); explained-for-me.md |
| §15 Open questions | consolidated from all documents' explicit `[OPEN]`/`[DECISION]`/`[CARRIED DECISION]` markers |

---

## 17. Decisions 4–7 (Checkpoint C6 / milestone M1, human sign-off 2026-09-02)

Four items surfaced during the C6/M1 domain-model-and-schema planning pass (schema shape for
`transactions.state`, `semantic_assessments.confidence`/`risk_level`, and the `audit_events`
append-only grant) are resolved by explicit human sign-off and are now `[LOCKED]`, extending
the decision numbering from §7/§11 above. As with Decisions 1–3, these are recorded here as the
resolution, not re-derived from the original spec documents (none of the four originating
questions were settled by the brief/product-spec/architecture/eval-design docs themselves).

**`[LOCKED — Decision 4, human sign-off 2026-09-02]` `transactions.state`'s `pending_evaluation`
value is transient and in-pipeline only — never written to Postgres as a durable row state.**
The transactions row is inserted exactly once, already in its terminal-at-insert-time state
(`allowed` or `held`), after the synchronous pipeline (§2) completes. No code path may persist a
`pending_evaluation` row and later update it. This resolves the ambiguity the C6 planning pass
flagged about whether Architecture A's single-request pipeline ever needs an intermediate
durable transaction state — it does not.

**`[LOCKED — Decision 5, human sign-off 2026-09-02]` `semantic_assessments.confidence` is
NOT NULL.** A `semantic_assessments` row is written only when the full LLM response validates
cleanly, including `confidence`. Missing, malformed, or out-of-range confidence is treated
identically to any other malformed response (§5, §11 above): no `semantic_assessments` row is
written at all, matching `gate_decisions.semantic_assessment_id`'s existing nullable-on-
malformed-path design (architecture §5). The raw payload in that failure case lives only in
`audit_events.payload`. This resolves the ambiguity the C6 planning pass flagged about whether
Decision 3's "confidence missing/malformed/unusable" language implied a row with a null
`confidence` field — it does not; that case simply never produces a row.

**`[LOCKED — Decision 6, human sign-off 2026-09-02]` `semantic_assessments.risk_level` has
exactly three values: `low`, `medium`, `high`.** This resolves the `risk_level` enum gap
flagged in the C6 planning pass (no source document ever enumerated its value set; every
example payload showed only `"high"`) — by analogy with the already-`[LOCKED]`
`mandate_alignment` enum (brief, product-spec), not by independent textual evidence. The column
is stored as `TEXT` at the DB level, not a native Postgres `ENUM` type, so the value set stays
cheaply changeable without a migration if this needs revisiting; no DB-level `CHECK` constraint
is added for it in C6 — value validation happens at the Pydantic layer, in a later milestone,
not the schema layer.

**`[LOCKED — Decision 7, human sign-off 2026-09-02]` The `audit_events` DB-role grant
restriction (`INSERT`/`SELECT`-only, no `UPDATE`/`DELETE`) is explicitly deferred past C6.** No
second DB role is created and no `REVOKE` migration is written in this checkpoint. Architecture
§14's append-only claim for `audit_events` therefore remains an application-level convention
only (no `UPDATE`/`DELETE` code path is written against that table) until a later checkpoint
implements the DB-level enforcement — tracked via a `TODO` comment in the migration that creates
`audit_events` (`23ff2fa8647b_create_audit_events_table.py`), not silently dropped.

**Additionally approved 2026-09-02, as proposed in the C6 planning pass (not independently
re-derived from architecture §5, which omits all four columns from its literal schema text):**
`evidence_packets.transaction_id` (FK → `transactions.id`, NOT NULL),
`gate_decisions.transaction_id` (FK → `transactions.id`, NOT NULL),
`audit_events.mandate_id` (FK → `mandates.id`, NOT NULL), and `audit_events.transaction_id`
(FK → `transactions.id`, NULLABLE — covers the rare pre-persistence-failure case where an audit
event precedes even the transaction row existing). These close the traceability gap where a
gate decision, evidence packet, or audit event on the fail-closed or nominal-ALLOW path had no
durable link back to the specific transaction it concerned — necessary for the `[LOCKED]`
audit-reconstruction requirement (§8 above) to actually hold on those paths.

---

## 18. Decisions 8–11 (Checkpoint C7+C8 / evidence engine, human sign-off 2026-09-02)

One schema correction to C6 and three signal-formula decisions, resolving the numeric-formula
open items tracked in docs/IMPLEMENTATION-PLAN.md §S/§F. As with Decisions 1–7, these are the
resolution, not a re-derivation from the original spec documents — no source document ever gave
formulas or exact cutoffs for the three deterministic signals (baseline §3/§15 both flagged
this explicitly as `[OPEN, not specified anywhere]`).

**`[LOCKED — Decision 8, human sign-off 2026-09-02]` `transactions.idempotency_key` uniqueness
is scoped per mandate, not global.** The unique constraint is `(mandate_id, idempotency_key)`,
not `(idempotency_key)` alone. A global constraint risked two unrelated mandates' synthetic
dataset cases colliding on the same generated key string during the locked test-set batch run,
silently dropping a transaction and corrupting the pipeline-error-rate metric (eval-design §16,
target 0). Implemented as its own migration (`8e58ccd4981c`) on top of the C6 chain, not an
edit to the already-applied `d87892027663_create_transactions_table.py` in place.

**`[LOCKED — Decision 9, human sign-off 2026-09-02]` Spend velocity formula:**
```
expected_fraction = days_elapsed_in_period / period_days   (floor days_elapsed at 1)
actual_fraction   = spend_so_far_in_period / budget
velocity_ratio    = actual_fraction / expected_fraction
Bands: normal (ratio <= 1.3), elevated (1.3 < ratio <= 2.0), critical (ratio > 2.0)
```
Resolves the `[OPEN, not specified anywhere]` velocity-formula item (baseline §3/§15). The
Dad's-review velocity/pattern-consistency split proposal (baseline §3) is explicitly **not**
touched by this decision and remains not adopted.

**`[LOCKED — Decision 10, human sign-off 2026-09-02]` Category-shift formula:**
```
out_of_mandate_ratio = (sum of amount for transactions in the window whose category is
                         NOT IN mandate.allowed_categories)
                        / (sum of amount for all transactions in the window)
Bands: none (ratio <= 0.05), minor (0.05 < ratio <= 0.20),
       significant (0.20 < ratio <= 0.45), severe (ratio > 0.45)
```

**`[LOCKED — Decision 11, human sign-off 2026-09-02]` Clustering formula:**
```
burst_ratio = (max transaction count in any rolling 24-hour sub-window within the
               analysis window) / (total transaction count in the analysis window)
Bands: normal (ratio <= 0.4), clustered (0.4 < ratio <= 0.7), highly_clustered (ratio > 0.7)
```
Deliberately not a real clustering algorithm (no k-means/DBSCAN) — a pure, deterministic,
reproducible count, matching architecture's own framing of layer ① as side-effect-free and
trivially unit-testable.

All six numeric cutoffs above live in a versioned config object
(`app.config.EvidenceEngineThresholds`, `version: "v1"`), never hardcoded inline inside the
signal functions, so dev-set calibration (a later milestone) can tune them via a config edit,
not a code edit — mirroring the `policy_version` pattern architecture §9 already uses for gate
thresholds.

**Explicitly not touched by Decisions 9–11, still `[OPEN]`, not silently resolved:** the
cross-signal threshold-crossing rule (does ANY elevated signal trigger evaluation, or some
weighted combination?) is a distinct question these decisions don't answer — Checkpoint C7+C8
implements a specific `[INFERRED]` rule for it (any signal above its lowest band triggers),
documented and flagged as an inference in `app/domain/pipeline.py`, not claimed as a locked
decision here. Also untouched: the disagreement-handling rule (§6/§I), retry policy, ingestion
auth, fixture counts, `C_fp`/`C_fn` cost values, and frontend fidelity — all remain exactly as
listed in docs/IMPLEMENTATION-PLAN.md §S.

**`[LOCKED — Decision 12, human sign-off 2026-09-02]` Clustering's `N == 1` case is a
distinct, explicit `band="normal"` branch — not the cold-start branch, and not run through
the general Decision 11 formula.** As implemented, `burst_ratio` for a single-transaction
window is always `1.0` by construction (one data point trivially fills its own 24-hour
sub-window), which — combined with the `[INFERRED]` "any non-normal signal crosses the
threshold" rule above — meant every mandate's very first transaction forced a threshold
crossing and an LLM call, regardless of any real drift. This directly undercut eval-design
§18's stated expectation that most cases trigger zero LLM calls. `N == 1` is a genuine
boundary condition (clustering is undefined with nothing yet to cluster against), distinct
from `N == 0` (cold-start — no data at all): `ratio` is still computed and reported as `1.0`
for observability, but `band` is forced to `"normal"`. `N >= 2` continues to use the Decision
11 formula unchanged. This refines, not replaces, Decision 11 — recorded as its own decision
rather than silently folded into Decision 11's original text.

**`[OPEN — must be resolved before M5 dataset generation begins]` Velocity's period-anchoring
has no renewal logic.** `compute_velocity` (Decision 9) anchors `expected_fraction` against
`mandate.created_at` with no reset after the first `period_days` window elapses — there is no
period-start field in the schema distinct from mandate creation, and no source document
addresses period renewal/rollover semantics at all. For a mandate evaluated in its second,
third, or Nth cycle — and this project's own running example, "weekly household groceries", is
inherently recurring, not a one-time budget — `expected_fraction` grows unbounded the longer
the mandate has existed, and the velocity signal goes numb regardless of real spend. This
directly risks eval-design's slow-drift narrative, which is specifically about sustained drift
over time — the scenario most likely to span multiple periods and therefore most likely to hit
this gap. **Not fixed now** — it needs an actual design decision (rolling-window reset per
`period_days`? multiple explicit mandate-period instances? something else?) that has not been
made. `app/domain/evidence_engine/velocity.py` carries a one-line `TODO` at the relevant branch
pointing back here. Must be resolved before M5 (dataset generation) begins, since any
multi-period slow-drift fixture would otherwise silently exercise a known-numb signal.

---

## 19. Decisions 13–14 (Checkpoint C9 / Semantic Risk Client, human sign-off 2026-09-02)

Two decisions enabling the real Anthropic API integration for layer ②. As with Decisions
1–12, these are the resolution, not a re-derivation — architecture §8/§10 and eval-design §16
each independently *proposed* answers to both questions but explicitly stopped short of
locking them (baseline §6, §15; architecture's own final sign-off checklist items 2–3).

**`[LOCKED — Decision 13, human sign-off 2026-09-02]` Model pin: `claude-sonnet-5`.** Added to
`app.config.Settings.llm_model`, an exact string in config, never resolved at request time
("latest" or similar) — this is what makes the locked-test-set run-over-run reproducibility
protocol (eval-design's core methodology) meaningful: every reported number traces to one
fixed model string, not whatever "latest" happened to resolve to on the day the batch ran.

**`[LOCKED — Decision 14, human sign-off 2026-09-02]` Retry policy: no retry on malformed or
schema-invalid LLM output — straight to a failure state. Exactly one transport-level retry for
connection errors / 5xx responses only, before that also becomes a failure state.** Timeout is
its own third failure state, also with no retry. This formalizes, unchanged in substance, what
architecture §10 and eval-design §16 already independently proposed ("simpler and arguably
more defensible — fewer moving parts, same safety outcome") but neither document claimed as
locked. Implemented with `anthropic.Anthropic(max_retries=0)` at client construction, so the
SDK's own default retry behavior never competes with this module's explicit one-retry loop —
the retry count is owned entirely by `app.domain.semantic_risk_client`, not the SDK.

**Scope note, not a new decision:** `domain/semantic_risk_client.py`'s `assess()` returns a
structured `SemanticAssessmentOutcome` with one of exactly four statuses
(`success`/`timeout`/`malformed`/`transport_error`) and never raises for any of them. A 4xx
response (e.g. an invalid API key) is deliberately **not** one of the four — it's treated as a
genuinely unexpected/configuration-level failure and propagates uncaught, per baseline §6's
"any unhandled pipeline exception → HOLD" rule, which lives at the Policy Gate / pipeline
orchestrator milestone (not yet built) rather than being silently absorbed here.

**Explicitly not touched by Decisions 13–14, still exactly as listed in
docs/IMPLEMENTATION-PLAN.md §S:** the disagreement-handling rule, ingestion auth, fixture
counts, `C_fp`/`C_fn` cost values, and frontend fidelity. The Policy Gate itself
(`domain/policy_gate.py`) and the full pipeline orchestrator are explicitly out of Checkpoint
C9's scope — `assess()` is a self-contained layer ② call; nothing in this checkpoint decides
ALLOW/HOLD/BLOCK.

---

## 20. Decision 15 (Checkpoint C10 / Policy Gate, human sign-off 2026-09-02)

Resolves the one remaining open cell in Plan §I's decision table — "threshold crossed, LLM
valid, risk_level == low" — previously marked `[OPEN — this is precisely the open
disagreement-handling question]`. Architecture §9's own candidate rule ("the gate never
downgrades toward ALLOW based on the LLM's word alone") is superseded by this decision, which
permits a narrow, tightly-bounded downgrade rather than a blanket refusal — but the
fail-closed default for every other case is unchanged.

**`[LOCKED — Decision 15, human sign-off 2026-09-02]` A "low" `risk_level` MAY downgrade a
triggered case to ALLOW only if ALL three hold:**
1. **Exactly one signal triggered, and that signal is at its mildest triggering band only**
   (`velocity == "elevated"`, OR `category_shift == "minor"`, OR `clustering == "clustered"` —
   never `"critical"`/`"severe"`/`"highly_clustered"`, and never more than one signal
   triggered).
2. **`confidence >= 0.7`** (`app.config.GatePolicyConfig.confidence_floor`, versioned config,
   not hardcoded).
3. **`mandate_alignment != "low"`** — an internal LLM contradiction (`risk_level == "low"` but
   `mandate_alignment == "low"`) fails closed to HOLD rather than receiving the benefit of the
   doubt.

If any of the three fail, the case HOLDs even with `risk_level == "low"`. The LLM never
decides ALLOW/HOLD itself — it only supplies `mandate_alignment`/`risk_level`/`confidence`;
`domain/policy_gate.py`'s `decide()` is the sole, fixed, auditable mapping from those fields to
a decision. Zero execution authority (baseline §5) is unchanged by this decision.

**Full decision table, now fully resolved:**
| Condition | Outcome |
|---|---|
| Threshold not crossed | `decide()` must never be called — raises `ThresholdNotCrossedError` if it is |
| LLM status in `{timeout, malformed, transport_error}` | HOLD, unconditionally (fail-closed, takes precedence over everything below) |
| LLM status `success`, `risk_level` in `{medium, high}` | HOLD |
| LLM status `success`, `risk_level == low` | Decision 15's three conditions — ALLOW only if all three pass |

**This unblocks eval-design failure fixture #6** (contradictory internal signals), which was
explicitly non-scoreable until this exact cell was resolved (baseline §9, §15 Phase 2; Plan
§I, §L). Both directions are tested by name in `tests/unit/test_policy_gate.py`: a mild single
signal with high LLM-reported risk (routes through the ordinary medium/high row), and — the
direction actually blocked until now — a severe/critical signal with a confident, internally
consistent "low" LLM read, which still HOLDs because condition 1 requires the mildest band
specifically, proving the downgrade doesn't rubber-stamp "low".

**Gate-rule-violation invariant (eval-design §14, target 0):** proven by construction, not
sampled test data — `test_medium_or_high_risk_can_never_reach_allow_by_construction`
exhaustively sweeps every other input dimension (2,520 combinations) with `risk_level` fixed
at `medium`/`high`, exploiting the fact that `decide()`'s early return for that branch reads no
other parameter at all, so the sweep is a complete enumeration of that branch's reachable
states.

**Explicitly not touched by Decision 15:** the disagreement-handling rule as originally framed
in architecture §9 is superseded, not merely extended — but everything else in
docs/IMPLEMENTATION-PLAN.md §S remains untouched (ingestion auth, fixture counts,
`C_fp`/`C_fn` cost values, frontend fidelity). BLOCK, HOLD-resolution, and timeout state
transitions remain entirely out of scope — `decide()` only ever returns `allow` or `hold`;
milestone M4 is what wires this into a full pipeline with persistence.
