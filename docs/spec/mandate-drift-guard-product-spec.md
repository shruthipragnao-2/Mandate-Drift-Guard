# Product Specification — Mandate Drift Guard
*Razorpay AI Buildathon, Track 02 (AI Risk Manager) — Draft v1*

This document turns the locked concept in `mandate-drift-guard-brief.md` into a specification
precise enough to build, defend, and evaluate against. It does not reopen settled architecture —
three-layer pipeline, fail-closed gate, paired-scenario evaluation — but it does make several
decisions that the brief left implicit. Those are marked **[DECISION]** and need your sign-off,
not silent adoption.

---

## 1. Problem statement

Payment rails enforce **how much** an AI shopping agent can spend (via UPI Reserve Pay limits,
NPCI-level caps). They cannot enforce **what the spend was for**. A standing mandate like "spend
up to ₹8,000/week on household groceries" is a semantic boundary expressed in natural language,
but only its numeric ceiling is machine-enforced today. A sequence of individually reasonable
purchases can drift away from that semantic intent — with no single transaction crossing any
enforced limit, and no malicious actor involved — until the aggregate pattern no longer matches
what was authorized. Nothing in the current stack detects trajectory-level drift; every existing
control is transaction-local.

## 2. Target user

**[DECISION]** The brief's own "Validation & Open Items" section already forces this choice: the
Aug 28 fix reframes the project from "consumer protection tool" to "chargeback-prevention layer
for the agentic commerce rail." That reframe has a direct consequence for target user that the
brief doesn't spell out — I'm making it explicit now because section 7 (user journey) and section
20 (metrics) both depend on it.

Two candidate users exist and they are not the same product:

| | Risk/Trust Ops analyst (platform-side, B2B) | End consumer (mandate grantor) |
|---|---|---|
| Interacts with | A risk dashboard / API, reviewing flagged mandates | A HOLD confirmation prompt on their own purchase |
| Judged by Track 02 as | Primary — "AI Risk Manager" is explicitly platform-facing | Secondary — receives the *consequence* of a HOLD |
| What they need from the product | Explainable evidence, audit trail, tunable thresholds, false-positive cost visibility | A fast, low-friction yes/no on their own transaction |

**Recommendation:** primary target user = **Risk/Trust Ops analyst at the payment platform
(Razorpay or a PSP built on its rail)**, responsible for agentic-commerce loss prevention. The
end consumer is a secondary actor who appears only inside the HOLD escalation step, not as the
primary interface user. This matches Track 02's framing, matches who a judging panel represents,
and matches where the engineering/evaluation rigor this project leads with actually gets consumed.

If you intended the consumer to be the primary user (a personal spending-guardrail product), say
so now — it changes the UX in section 7 substantially, from a dashboard to a consumer-facing
confirmation flow, and shifts the metrics in section 20 from ops efficiency to consumer trust.

## 3. User's current workflow

Today, a Risk Ops analyst at a payment platform has no per-mandate semantic visibility. Their
current tools are aggregate and post-hoc:
- Cumulative spend and transaction-count dashboards (NPCI/bank-enforced limits already work here).
- Manual chargeback/dispute review, triggered *after* a consumer disputes a transaction — reactive,
  not preventive.
- Category-level fraud rules (velocity checks, merchant blacklists) tuned for outright fraud, not
  for legitimate-agent-drifting-from-intent, which looks nothing like fraud in any single event.

There is no existing step in this workflow where "does this transaction stream still match what
was authorized, in aggregate" gets asked before a dispute is filed.

## 4. Pain points

- **No semantic signal exists pre-dispute.** By the time drift becomes visible, it's already a
  chargeback, and the platform absorbs reputational/operational cost for the agentic-payments
  program itself.
- **Numeric-only limits create false confidence.** A mandate "working as intended" numerically can
  still be violating its purpose entirely.
- **No single transaction is the culprit**, so transaction-level fraud rules structurally cannot
  catch this class of problem — there's nothing anomalous to flag at the unit level.
- **Legitimate spikes and genuine drift look identical on deterministic signals alone** (this is
  the paired-scenario thesis) — so a rules-only system either over-blocks legitimate behavior
  change or under-catches real drift, and Ops has no tool to tell which case they're in.

## 5. Desired outcome

A Risk Ops analyst (or an automated policy layer acting on their behalf) can see, for any active
mandate, a running semantic-alignment assessment of the transaction trajectory against the
original mandate — with graded confidence, human-readable evidence, and a fail-closed HOLD when
uncertain — **before** a pattern becomes a dispute, not after.

## 6. Product thesis

Deterministic software should own everything measurable (velocity, category distribution,
clustering) — arithmetic is the correct tool there, and using an LLM would be strictly worse:
slower, less auditable, non-reproducible. AI should own exactly one thing deterministic logic
structurally cannot: interpreting whether a *pattern of transactions* still matches a
*natural-language statement of intent*. The paired-scenario evaluation exists to prove this
division of labor is necessary, not just architecturally tidy — i.e., to show that two cases
identical on every deterministic signal but opposite in ground truth cannot be separated without
the semantic layer.

## 7. Core user journey

*(Ops-analyst-primary framing per section 2 — revise if that decision changes.)*

1. A consumer's mandate is active; their AI shopping agent transacts against it over time.
2. The evidence engine continuously computes signals (velocity, category shift, clustering) per
   mandate; most mandates never leave a "nominal" state and never surface to a human.
3. When deterministic signals cross a threshold, an evidence packet is generated and sent to the
   semantic risk layer.
4. The semantic layer returns a structured risk assessment (alignment, risk level, confidence,
   evidence) — never a raw allow/block.
5. The policy gate converts this into ALLOW / HOLD / BLOCK. On HOLD, the *next* agent transaction
   attempt is paused pending confirmation (see open question in §17 on exactly what "HOLD" pauses).
6. The Ops analyst (or, in a HOLD-to-consumer variant, the consumer directly) sees the case: the
   evidence packet, the AI's reasoning, and the two possible resolutions.
7. Resolution is recorded: confirm → ALLOW, deny → BLOCK, timeout → BLOCK (fail-closed). Every
   step, every signal, every AI response, and every gate decision is appended to the audit log.
8. Over time, the paired-scenario dataset and the resolved-case history become the evaluation
   substrate: did the system's risk calls match ground truth, broken out by drift type.

## 8. Core agent workflow

This is not an autonomous "agent" in the tool-calling sense — it's a **pipeline with one bounded
AI reasoning step**, which is itself a defensible architectural choice worth stating plainly in
the pitch (principle 12: avoid unnecessary complexity; a multi-tool autonomous agent here would be
over-engineering for a task that is fundamentally "classify this evidence packet").

```
Mandate + Transaction stream
   → Deterministic Evidence Engine (signals only, no interpretation)
   → Semantic Risk Assessment (LLM; structured input, structured output, no execution authority)
   → Deterministic Policy Gate (owns the ALLOW/HOLD/BLOCK decision)
   → Audit Log (append-only, every layer's output recorded)
```

The LLM step is stateless per evidence packet — it does not retain conversational memory across
calls, does not call external tools, and does not see raw transaction data it wasn't given in the
packet. This bounding is a safety property, not an implementation detail, and should be named as
such in the architecture write-up.

## 9. Inputs

- **Mandate object**: purpose (free text), budget, period, allowed categories. *(Per the brief's
  own honesty fix: the semantic-purpose and allowed-categories fields are this project's proposed
  enrichment of Razorpay's mandate concept — Razorpay's confirmed pilot mandate is merchant +
  amount limit only. State this distinction in the spec's data model, not just the pitch.)*
- **Transaction stream**: timestamped purchases (amount, merchant, category, item-level detail if
  available) against a given mandate.
- **Historical distribution baseline**: prior transactions under the same mandate, used to compute
  category-shift and velocity signals.
- **Evidence packet** (system-internal, not external input): the structured object layer ① passes
  to layer ②, per the schema already defined in the brief.

## 10. Outputs

- **Deterministic signals** (layer ①): spend velocity classification, category-shift magnitude,
  clustering result — numeric/categorical, not natural language.
- **Semantic risk assessment** (layer ②): `mandate_alignment` (low/medium/high), `risk_level`,
  `confidence` (0–1), `evidence[]` (short natural-language justifications).
- **Gate decision** (layer ③): ALLOW / HOLD / BLOCK, plus the reason chain that produced it.
- **Audit record**: append-only entry per case containing all of the above plus final resolution
  and timestamp — this is a product output in its own right, not just logging (principle 15,
  traceability).
- **(If Ops-dashboard framing holds)** a case view: mandate, evidence packet, AI reasoning, current
  state, resolution controls.

## 11. Tools available to the AI

Deliberately minimal, by design (principle 3 — don't use AI where deterministic logic is
superior):
- **Input**: the evidence packet only. No live database access, no tool-calling, no web access, no
  ability to pull additional transactions beyond what layer ① already selected.
- **Output**: a fixed structured schema (JSON), validated on receipt. No free-form executable
  instructions of any kind are accepted as valid output.

The AI has **no tools** in the agentic sense — no function-calling capability, no ability to query
for more data if uncertain (uncertainty is instead handled by the gate defaulting to HOLD, per
§13–15). This is a direct, defensible answer to "what happens when the AI is wrong" and "what
prevents unsafe actions": the AI's blast radius is bounded to producing a JSON opinion that a
separate deterministic component decides whether to trust.

## 12. Actions the AI may propose

- A `mandate_alignment` classification (low/medium/high) with supporting evidence text.
- A `risk_level` and `confidence` score.
- Nothing else. The semantic layer does not propose ALLOW/HOLD/BLOCK directly — that mapping is
  owned entirely by the deterministic gate, which is the architecture's core safety property
  (brief: *"never returns a plain allow/block — that's not its job"*).

## 13. Actions the AI may execute

**None.** This is worth stating as its own section rather than folding into §12, because it's the
single most defensible line in a Track-02 submission: the AI layer has zero execution authority.
It emits a structured assessment; it cannot ALLOW, HOLD, or BLOCK anything itself, cannot touch the
transaction stream, and cannot write to the audit log except as the object being logged.

## 14. Actions that require human approval

- Resolving any case that reaches **HOLD** (confirm → ALLOW, deny → BLOCK) — this is the one
  human-in-the-loop point in the entire pipeline, and it's mandatory by design, not optional.
- Any change to policy-gate thresholds (what triggers layer ② at all, what confidence/risk
  combinations map to which gate outcome) — this is a system-configuration change, not a
  transaction decision, but principle 8 ("treat financial actions as high-risk operations
  requiring explicit boundaries") argues for treating threshold changes with the same rigor.
- **[OPEN]** Whether the human resolving a HOLD is the Ops analyst, the consumer, or both in
  sequence is unresolved — see §2 decision and the ambiguity list at the end.

## 15. Actions the AI must never perform

- Never outputs a direct ALLOW/HOLD/BLOCK verdict — always routed through the deterministic gate.
- Never executes, initiates, reverses, or modifies a transaction.
- Never modifies the mandate itself (budget, categories, purpose).
- Never receives or acts on merchant-supplied content as instructions — this is the brief's
  explicitly excluded scope (prompt-injection defense is a *different* problem from drift
  detection, and conflating them would dilute the pitch's focus). The evidence packet must be
  structurally incapable of carrying merchant-originated free text into the AI's instruction
  context.
- Never causes a silent ALLOW on timeout, malformed output, schema-validation failure, or
  low-confidence output — all of these default to HOLD (or BLOCK on HOLD-timeout), never ALLOW.
  This fail-closed default is a hard architectural invariant, not a tunable default.
- Never has its self-reported `confidence` trusted uncritically — per the brief's own Aug 28
  addition, this must be empirically checked against actual correctness before the gate logic
  leans on it (see §17).

## 16. Safety boundaries

- **Bounded input**: AI sees only the evidence packet, never raw transaction PII beyond what's
  needed for the assessment, never merchant-supplied free text.
- **Bounded output**: schema-validated structured JSON only; any deviation is treated as a failure
  case (→ HOLD), not repaired or best-effort parsed.
- **No execution authority**: enforced structurally (AI has no write access to transaction state,
  mandate state, or the gate's decision path) — not just by prompt instruction, since prompt-only
  boundaries are not a safety boundary.
- **Fail-closed default**: uncertainty, timeout, or malformed output routes to HOLD; HOLD-timeout
  routes to BLOCK. Never silent ALLOW under any failure condition.
- **Confidence calibration check**: before the gate trusts self-reported confidence, it must be
  validated against held-out ground truth (per brief §"New risk... confidence calibration"). Until
  validated, the honest position is that raw self-reported confidence is *not yet trustworthy* and
  the gate should either use a conservative recalibration or an evidence-completeness proxy
  instead.
- **Explicit scope boundary**: prompt-injection / merchant-content-manipulation defense is out of
  scope. This must be stated in the product, not just the deck, so a reviewer doesn't assume the
  system claims coverage it doesn't have.

## 17. Failure states

| Failure | Handling |
|---|---|
| Semantic layer times out | Gate treats as unresolved → HOLD |
| Semantic layer returns malformed/non-schema output | Treated as failure → HOLD |
| Semantic layer returns low confidence | Gate routes to HOLD regardless of stated risk_level, until calibration validated |
| Evidence engine signal computation fails (e.g., missing historical baseline — cold start) | Explicitly out of scope per brief; production note: default to conservative thresholds. For MVP, state this limitation rather than silently degrading. |
| HOLD case exceeds resolution timeout | → BLOCK (fail-closed, never left open) |
| Deterministic signals disagree sharply with semantic assessment (e.g., signals mild, AI reports high risk, or vice versa) | **[OPEN]** — not yet specified whether the gate has an explicit disagreement-handling rule beyond the standard risk/confidence→state mapping. Worth deciding before build, since it's a natural interview question. |

## 18. Human escalation

- **HOLD → the resolving human** (Ops analyst and/or consumer, per §2/§14 open decision). This is
  the only escalation path in the MVP — deliberately singular, per principle 12 (avoid
  unnecessary complexity). No secondary escalation tier (e.g., "escalate to senior analyst") is in
  scope for MVP; note it as a natural post-MVP extension only if useful for the pitch narrative.
- Every escalation is logged with full context (evidence packet, AI reasoning, gate rationale) —
  the human should never have to reconstruct *why* a case was flagged.

## 19. Audit requirements

- **Append-only** log, no deletion or silent overwrite of any record.
- One audit record per case, containing: mandate snapshot at time of evaluation, computed signals,
  full evidence packet sent to the semantic layer, full semantic-layer response (including raw
  confidence), gate decision and the rule that produced it, human resolution (if HOLD), and
  timestamps at every stage.
- Audit log must be sufficient on its own to reconstruct and defend any single decision in a panel
  interview — this is a direct requirement, not a nice-to-have, given the "must be able to defend
  every decision" constraint in the brief's context section.

## 20. Success metrics

Per the brief's mandatory methodology — reported **by drift type (fast-spike vs. slow-drift),
never blended**:
- Precision, recall, false-positive rate, false-negative rate — hybrid system vs. rules-only
  baseline, same held-out (locked) test set.
- Confidence-correctness correlation on the held-out set (new metric, per §16/§17 calibration
  requirement) — this needs to exist as a named metric, not just a mentioned concern.
- Illustrative cost-weighted outcome (false positive = friction cost, false negative = exposure
  cost) — explicitly labeled as assumed/illustrative, not sourced from real loss data.
- **[OPEN — depends on §2]** If Ops-analyst-primary: time-to-resolution on HOLD cases, or
  case volume an analyst can review, are natural secondary metrics but are not yet specified as
  MVP-required. Decide whether these belong in MVP scope or are explicitly out (§23).

## 21. Non-goals

- Detecting merchant-content manipulation or prompt injection into the agent (explicitly excluded
  in the brief; a different problem from drift detection).
- Enforcing numeric spending caps — that's NPCI/bank-level infrastructure, already solved,
  confirmed, and out of this project's scope entirely.
- Solving cold-start (new agent, no purchase history) — named limitation, not solved in MVP.
- General-purpose fraud detection — this system is not a fraud classifier; it is narrowly a
  mandate-trajectory-vs-intent classifier.
- Real-time transaction blocking infrastructure at payment-rail speed — this is a risk-assessment
  layer, not a claim to replace or race NPCI-level authorization latency.

## 22. MVP scope — one complete end-to-end workflow

**The single workflow that must work, fully and honestly evaluated, end to end:**

> A synthetic mandate + transaction stream is fed in → deterministic evidence engine computes 2–3
> signals (velocity, category drift, clustering) → evidence packet generated → semantic risk layer
> returns a structured assessment → policy gate converts to ALLOW/HOLD/BLOCK with fail-closed
> defaults → decision and full reasoning chain are written to an append-only audit log → this
> entire path is run against the paired-scenario dataset (dev set for tuning, locked test set for
> final numbers) → rules-only-vs-hybrid comparison is computed and reported per drift type.

Everything in this list is required; nothing here is optional:
- Mandate schema (including the project's proposed semantic-purpose field, labeled as our own
  enrichment, not Razorpay's confirmed feature).
- 2–3 deterministic signals.
- Evidence packet construction.
- LLM semantic assessment with schema-validated output.
- Deterministic policy gate with fail-closed HOLD/BLOCK.
- Append-only audit log.
- Paired-scenario dataset with dev/test split and written rationale per case.
- Rules-only vs. hybrid evaluation, metrics broken out by drift type.
- Confidence-correctness calibration check (§16/§20) — this is now core, not stretch, since it's a
  correctness property of the fail-closed design, not a nice-to-have measurement.

**[DECISION carried over from §2]:** MVP interface is the minimum needed to demonstrate the
workflow above — likely a simple case view (evidence packet, AI reasoning, gate decision) rather
than a full Ops dashboard. Don't build dashboard polish before the pipeline and evaluation are
solid; this is a Track-02 engineering/evaluation submission, not a UI showcase.

## 23. Post-MVP — explicitly will NOT build unless time permits

In priority order, per the brief's own stretch list, plus one addition from this spec pass:
1. **Side-by-side visual timeline** (Case A vs. Case B, near-identical charts, one HOLD/one ALLOW)
   — brief already promotes this from stretch to first-priority-after-core, because it's the
   single highest-leverage 15-second pitch moment. Build only after the core pipeline and eval are
   done and honest.
2. LLM-only baseline (comparison point, not required for the core hybrid-vs-rules claim).
3. Cost-model threshold curve (illustrative visualization of the FP/FN tradeoff).
4. Merchant-distribution as a fourth deterministic signal.
5. **(New, from this pass)** Ops-dashboard polish beyond a minimal case view — analyst
   time-to-resolution tracking, case-volume views, threshold-tuning UI. These are real product
   value but not required to prove the core thesis, and building them early risks principle 14
   (optimize for engineering/hiring signal, not feature count).
6. **(New, from this pass)** Secondary escalation tier beyond single HOLD-to-human.

---

## Open ambiguities — need your decision before this spec is final

1. **Primary user (§2, §14, §18, §22 UI).** Is this an Ops-analyst-facing risk tool (my
   recommendation, matches Track 02 framing) or a consumer-facing spending guardrail? This choice
   changes the interface, the escalation flow, and which metrics matter — it isn't cosmetic.
2. **Who resolves a HOLD, concretely?** Ops analyst, consumer, or a two-step flow (e.g., consumer
   confirms first, Ops sees it only if consumer denies or times out)? Not specified anywhere yet.
3. **Disagreement handling (§17).** When deterministic signals and the semantic layer's risk
   assessment point in different directions, is there an explicit gate rule beyond the standard
   risk/confidence mapping, or does the existing mapping already implicitly cover this? Worth a
   one-line answer before build, since it's a likely interview question.
4. **MVP interface fidelity.** How minimal is "minimal case view" allowed to be — a rendered JSON
   diff, or an actual UI? Affects time budget against the 8-day clock.
5. **Confidence calibration fallback.** If, on the held-out set, self-reported confidence turns out
   *not* to correlate with correctness (a real possibility the brief itself flags), do you want to
   (a) build the evidence-completeness proxy as a fallback within MVP scope, or (b) report the
   miscalibration honestly as a named limitation and leave the fix for post-MVP? This has a real
   time cost either way and should be decided now, not discovered on day 6.
6. **Secondary metrics scope (§20).** Time-to-resolution / analyst throughput — MVP-required or
   explicitly deferred? Depends on the §2/§1 decision.
