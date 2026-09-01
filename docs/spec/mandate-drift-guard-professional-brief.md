# Mandate Drift Guard
### A semantic authorization monitor for AI-agent payments
**Razorpay AI Buildathon — Track 02: AI Risk Manager**

---

## Executive Summary

Payment infrastructure enforces **how much** an AI shopping agent can spend. It does not enforce **what that spending was for**. A payment rail can validate a numeric limit; it cannot validate whether a sequence of individually-authorized purchases still serves the purpose for which spending authority was delegated.

**Mandate Drift Guard** is a merchant/platform-side risk system that monitors an AI agent's transaction trajectory against its original spending mandate, and detects when that trajectory has drifted from its authorized purpose — even when no single transaction, viewed in isolation, looks anomalous. It uses deterministic logic for everything that is arithmetically measurable, and AI for exactly the one thing arithmetic cannot do: judging whether an unusual pattern still matches a stated intent.

The system produces a fail-closed ALLOW / HOLD / BLOCK decision on each evaluated transaction stream, backed by a full audit trail, and is evaluated with precision/recall on a locked, held-out synthetic test set against an honest rules-only baseline.

---

## 1. Problem Taste — why this matters

### The circumstance
In February 2026, Razorpay and NPCI launched a live agentic-payments pilot enabling consumers to order from Zomato, Swiggy, and Zepto through conversational AI agents, using UPI Reserve Pay — a mechanism where a user pre-approves a spending limit for a merchant once, after which the agent can transact repeatedly without further authentication. This is confirmed, shipping infrastructure, not a hypothetical.

### The gap
Every confirmed source describes the underlying mandate as **merchant + spending limit** (and an implied period). This project's core proposal is that such a mandate can be enriched with a **semantic purpose** ("weekly household groceries") and **allowed categories** — and that this enrichment is necessary, because a numeric ceiling says nothing about whether spending *within* that ceiling still matches what was authorized. This enrichment is stated explicitly as our own proposed addition, not a documented Razorpay feature — the distinction matters, and we do not blur it.

### Why drift happens — without any actor behaving maliciously
1. **Legitimate behavior change** — a one-time genuine need (hosting an event) produces an unusual but entirely valid spending pattern.
2. **Underspecified intent** — a mandate like "buy groceries" does not define its own boundaries; reasonable interpretations can diverge over time with no bad intent anywhere.
3. **Compounding local decisions** — many individually reasonable purchases can, in aggregate, land somewhere the original mandate never intended, with no single transaction ever being the identifiable cause.

### Where the financial loss actually lands
This system is deliberately **not** framed as consumer protection. The loss it defends against is the **merchant's**: undetected mandate drift is a chargeback vector. If a consumer later disputes a purchase pattern they don't recognize or endorse, that dispute is filed against the merchant, who has already fulfilled the order. Razorpay and its merchant partners absorb that operational and reputational cost. This system exists to surface drift as a moment of confirmation, before it becomes a dispute weeks later — converting an eventual chargeback into a present-moment yes/no decision.

### Primary user
A Risk/Trust Ops analyst at the payment platform (or a merchant on its rail), responsible for agentic-commerce loss prevention — not the consumer. The consumer's only interaction with the system is answering a confirmation prompt during a rare HOLD.

---

## 2. Build Quality — architecture and engineering decisions

### Chosen architecture: synchronous monolith
A single backend service runs the full pipeline (evidence computation → semantic assessment → policy decision) in-process, in one request. An event-driven, message-queue architecture was evaluated and explicitly rejected: nothing about this problem's actual throughput or ordering requirements justifies the added failure surface (message loss, duplicate delivery, worker-crash recovery) or the evaluation-harness complexity a queue architecture would introduce. The synchronous design keeps every case's full decision trace in one linear, auditable call chain.

### Three-layer pipeline, three separated responsibilities

```
MANDATE (purpose, budget, period, allowed categories)
        │
        ▼
TRANSACTION STREAM
        │
        ▼
① DETERMINISTIC EVIDENCE ENGINE
   spend velocity · category-distribution shift · transaction clustering
   Pure functions. No AI. Outputs signals, not verdicts.
        │
        ▼
② SEMANTIC RISK ASSESSMENT
   One stateless LLM call. Receives a structured evidence packet only —
   never raw transactions. Returns mandate_alignment, risk_level,
   confidence, and evidence[] as a schema-validated structured object.
   Never returns ALLOW/HOLD/BLOCK directly.
        │
        ▼
③ DETERMINISTIC POLICY GATE
   Converts risk + confidence into ALLOW / HOLD / BLOCK via versioned
   threshold rules. Sole owner of the fail-closed invariant.
        │
        ▼
AUDIT LOG — append-only, every stage's output recorded, keyed to case_id
```

### Key engineering decisions and their rationale
- **Structured output via a forced tool call**, not free-text parsing — guarantees schema-valid JSON from the LLM layer without agentic tool-use; this is a structured-output mechanism, not tool-calling in the execution sense.
- **DB-level append-only enforcement** on the audit table (`INSERT, SELECT` grants only, no `UPDATE`/`DELETE`) — immutability enforced at the database layer, not merely by application convention.
- **Idempotent ingestion**, keyed on a caller-supplied idempotency key — protects signal computation from corruption by duplicate transaction submission.
- **No agent-orchestration framework** — the AI step is one bounded classification call, not a multi-step autonomous loop; introducing a framework here would be complexity without a matching requirement.

### Data & privacy design
- **Bounded retention** — signal computation uses a rolling 6–8 week window, not indefinite history.
- **Structured-only exposure** — the evidence packet sent to the AI layer contains signals and distribution summaries, never itemized raw purchase data.
- **Scoped consent** — monitoring is scoped to the specific mandate the consumer authorized, not general financial surveillance. The payment processor already observes every transaction by necessity; this system performs analysis on data that must already exist for the payment to occur, rather than introducing a new collection point.

---

## 3. AI Judgment — the right tool, and where we chose not to use one

### The explicit division of labor
Deterministic software owns everything numerically measurable: velocity, category-distribution shift, clustering. Using an LLM for arithmetic that a rule can compute exactly would be strictly worse — slower, less reproducible, harder to audit. AI is used for exactly one task deterministic logic structurally cannot perform: **interpreting whether an observed pattern of transactions still matches a natural-language statement of intent.**

### Proof, not assertion, that AI is structurally necessary
The core experiment of this project's evaluation is a **paired-scenario methodology**: constructing case pairs matched on every deterministic signal a rule could compute (total spend, transaction count, spend velocity, category-shift magnitude) but with opposite ground-truth labels — for example, a one-time legitimate spending spike (hosting an event) versus a genuine slow drift away from the mandate's purpose, engineered to produce near-identical numeric signatures. If such pairs exist and only the semantic layer can separate them correctly, that is a demonstration by construction that the selected deterministic features are insufficient — not a broader, unsupportable claim that no statistical method anywhere could ever solve this problem.

### Alternatives considered and rejected, with reasons stated
- **Small Language Models** — cheaper and faster, but currently weaker at the kind of nuanced, context-dependent judgment this task requires; not selected given the compressed build timeline.
- **Classical ML / fine-tuned classifier** — would require labeled training data and fine-tuning infrastructure disproportionate to an 8-day solo build; an off-the-shelf LLM used via prompting is the deliberate, stated choice, not a default.
- **Pure rules-only system** — evaluated directly as the mandatory baseline (see §5); reported honestly even where it performs competitively, rather than assumed inferior.

### What the AI layer is structurally prevented from doing
No execution authority of any kind. It cannot ALLOW, HOLD, or BLOCK a transaction directly, cannot touch the transaction stream, cannot modify the mandate, and never receives merchant-supplied free text as part of its instruction context. Its entire output surface is a validated structured object consumed by a separate, deterministic policy engine.

---

## 4. Failure Recovery — what breaks, and what happens

### The fail-closed invariant
Uncertainty, timeout, or malformed output never resolves toward ALLOW. It always resolves toward HOLD, and HOLD that times out resolves to BLOCK. This is a hard architectural invariant, not a tunable default — the gate never trusts an LLM's word alone to move money.

| Failure condition | System behavior |
|---|---|
| Semantic layer call times out | → HOLD |
| Semantic layer returns malformed/non-schema output | → HOLD (no retry, straight to HOLD — fewer moving parts, same safety outcome) |
| Semantic layer returns low self-reported confidence | → HOLD, regardless of stated risk level |
| Deterministic signals and semantic assessment disagree | Gate never downgrades toward ALLOW on the LLM's word alone (explicit versioned rule) |
| Evidence engine has no historical baseline (cold start) | Conservative default threshold applied; must not crash; named as a scoped limitation |
| HOLD exceeds resolution timeout | → BLOCK (fail-closed, never left unresolved) |
| Unhandled pipeline exception | → HOLD, exception recorded in the audit event |

### The confidence-calibration risk, addressed directly
The policy gate's design initially risked trusting the semantic layer's self-reported `confidence` field uncritically — a real risk, since LLM self-reported confidence is frequently miscalibrated, and a confidently wrong output could otherwise slip past the fail-closed design. The evaluation plan includes a dedicated calibration check: confidence is validated for correlation with actual correctness on the held-out set before the gate trusts it; if calibration fails, the gate falls back to a deterministic confidence proxy derived from evidence-packet completeness rather than the model's raw self-report.

### Deliberately injected failure cases
A dedicated fixture set — separate from the main evaluation dataset — exists specifically to test whether the system fails safely: simulated timeouts, simulated malformed output, simulated low-confidence-but-high-stated-risk cases, and adversarial text embedded in transaction fields. Each has a required system behavior (route to HOLD; never let adversarial content reach the LLM's instruction context) and a corresponding safety metric, reported at 100% compliance as a hard bar, not a target range.

---

## 5. Evaluation & Honest Metrics

### Dataset design
Synthetic, paired cases spanning two drift types — **fast-spike** (a short burst of high, off-category spend) and **slow-drift** (a gradual, sustained category shift with no single anomalous transaction) — plus an unpaired **ambiguous** category built deliberately at the threshold boundary to test appropriate abstention. Every paired case is verified, not just generated: the real deterministic evidence engine is run on both members of a pair, and the pair is rejected and regenerated if their signal profiles don't match within a stated tolerance.

### Dev / locked-test split
The dataset is split at the pair level, stratified by drift type and category. The dev set is used to iterate the semantic-layer prompt and calibrate the rules-only baseline's thresholds. The locked test set is touched exactly once, at the end of the build, to produce every number reported in the final submission — enforced procedurally, not just as a stated intention.

### Metrics
Precision, recall, F1, false-positive rate, and false-negative rate — computed on the locked test set, reported separately for fast-spike and slow-drift cases, never blended into one aggregate number. Reported for both the rules-only baseline and the full hybrid system, side by side, so the incremental contribution of the AI layer is a measured result, not an assumption.

### Cost-weighted business framing
An explicitly labeled, illustrative cost model — not sourced from real Razorpay loss data — assigns a cost to false positives (customer friction from an unnecessary HOLD) and false negatives (undetected drift that proceeds to become chargeback exposure). The single most direct proof of business value: the count of drift cases the rules-only baseline structurally cannot catch but the hybrid system does — cases where the AI layer's contribution is not incremental, but the entire difference between catching the case and missing it.

---

## 6. Defense-Only Compliance

Track 02's stated bar requires the submission to be strictly defense-only. This system's only executable capabilities are ALLOW, HOLD, or BLOCK — it has no capability to execute, optimize, or deliver anything resembling an attack. All synthetic "drift" and "ambiguous" test cases exist as static, labeled data fixtures, checked into the repository as fixed test data — never a live generator, never exposed as a reusable capability in the delivered system.

---

## 7. Scope

### Core (must work, fully, honestly evaluated)
Mandate schema · 2–3 deterministic signals · evidence packet construction · LLM semantic assessment with schema-validated output · fail-closed policy gate · append-only audit log · paired-scenario dataset with verified pairing · rules-only vs. hybrid comparison, reported by drift type · confidence-correctness calibration check.

### Stretch (only if core finishes with time remaining)
LLM-only baseline for comparison · cost-model threshold sensitivity curve · side-by-side visual timeline of a matched case pair · a fourth deterministic signal (merchant distribution).

### Named limitations, stated up front
- Ground truth is synthetic and self-labeled by the builder, with a written rationale attached to every case as the primary mitigation — no independent adjudicator exists.
- Cold-start (an agent with no purchase history) is explicitly out of scope; a production system would require conservative default thresholds until sufficient history accrues.
- The semantic-purpose mandate field is this project's proposed enrichment of Razorpay's mandate concept, not a confirmed feature of their production system.
