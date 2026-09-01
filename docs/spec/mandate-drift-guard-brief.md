# Project Brief — Mandate Drift Guard
*(Razorpay AI Buildathon — Track 02: AI Risk Manager)*

## Status
Concept is locked, red-team tested, and considered stable. Not yet built. This is the spec to build against — not an open brainstorm anymore.

## Context for whoever's reading this (including Claude, in a fresh chat)
Solo student builder, submitting to Razorpay's AI Buildathon (deadline Sept 5, in-person Bangalore internship at stake). No prior fintech/payments knowledge. Strong in backend/API design, LLM prompting & agent orchestration, evaluation rigor, data pipelines. No prior solo end-to-end shipped project — Claude Code will implement, the builder owns direction, architecture decisions, and must be able to defend every decision in a panel interview. This idea went through multiple rounds of pressure-testing (including cross-checking with another AI and a deliberate red-team pass trying to find fatal objections) before being locked.

---

## Who this is for (read this first — recurring point of confusion, resolve it here)
**This is a merchant/platform-side risk tool, not a consumer app.** The user who operates this system is a Risk/Trust Ops analyst working for Razorpay or a merchant on its rail — the same relationship as a bank's fraud-alert system, which watches *your* spending but is unmistakably *the bank's* tool, not yours.

The consumer's spending data is the **subject being analyzed** — that is not the same question as **who the tool is for**. A supermarket's loss-prevention camera watches customers; nobody calls it a customer service tool. Same shape here: the customer's mandate and transaction stream get analyzed *because that's the only available definition of "normal" for their specific agent* — but the system exists to protect the **merchant** from the financial loss (chargebacks) that undetected drift eventually causes, weeks later, when the consumer disputes a purchase they don't recognize.

The consumer's only interaction with this system, ever, is answering a yes/no confirmation during a rare HOLD. They never see a dashboard, a risk score, or an evidence packet — that's the Ops analyst's view, not theirs.

## Core thesis
A payment rail can enforce *how much* an AI agent spends. It cannot enforce *what the money was for*. When does a sequence of individually-reasonable agent purchases stop matching what the user actually authorized — and can that be detected when no single purchase looks wrong on its own?

## One-line pitch
A system that watches an AI shopping agent's transaction history against its original spending mandate, and flags when the overall *trajectory* — not any single purchase — has drifted from what was authorized. Deterministic checks handle what's measurable; AI handles only what requires interpreting meaning.

## Why this is real (and what we're NOT claiming)
Razorpay's live agentic-payment pilot (Feb 2026, via Claude, with Zomato/Swiggy/Zepto) lets a person grant an AI agent a standing spending mandate once, so it can transact repeatedly without re-approval each time.

**We do not claim Razorpay's payment infrastructure has an exploitable hole.** NPCI/bank-level UPI limits already enforce cumulative daily totals and transaction counts — that's confirmed, hardened, decades-old infrastructure. The gap we address sits one level up: the **mandate itself** ("spend up to ₹8,000/week on household groceries") is a semantic boundary, not just a number. Enforcing the number does nothing to enforce the meaning.

## Three real reasons drift happens — none require a malicious or "attacked" agent
1. **Legitimate behavior change** — a one-time genuine need (hosting a family event). High spend, zero problem.
2. **Underspecified intent** — "buy groceries" doesn't define its own edges. Reasonable interpretations can diverge over time with nobody doing anything wrong.
3. **Compounding local decisions** — many individually sensible choices that collectively land somewhere the mandate never intended. No single bad decision to point at.

**Explicitly excluded scope:** merchant-content manipulation / prompt injection into the agent. That's a different, related problem (catching a *trick*, not catching *drift*) and deliberately kept out to avoid diluting this project's focus.

---

## Architecture — three separated responsibilities

```
MANDATE (purpose, budget, period, allowed categories)
        │
        ▼
TRANSACTION STREAM
        │
        ▼
① DETERMINISTIC EVIDENCE ENGINE
   — spend velocity, category-distribution shift, transaction clustering
   — outputs signals, not verdicts. No AI here — arithmetic is the right tool.
        │
        ▼
② SEMANTIC RISK ASSESSMENT  ← the only place AI is used
   — receives a structured "evidence packet," not raw data
   — returns: mandate_alignment (low/medium/high), risk_level, confidence, evidence[]
   — never returns a plain allow/block — that's not its job
        │
        ▼
③ DETERMINISTIC POLICY GATE
   — converts risk + confidence into ALLOW / HOLD / BLOCK
   — uncertain, timed-out, or malformed AI output → always HOLD, never silent ALLOW
        │
        ▼
AUDIT LOG (every signal, every AI response, every gate decision — permanent, append-only)
```

**Evidence packet — what layer ② receives (example):**
```json
{
  "mandate": {"purpose": "weekly household groceries", "budget": 8000, "period_days": 7, "allowed_categories": ["groceries", "household essentials"]},
  "signals": {"budget_utilization": 0.91, "spend_velocity": "elevated", "category_shift": "significant"},
  "trajectory": {"historical_distribution": "...", "current_distribution": "..."}
}
```

**Layer ② output — structured reasoning, never an executable instruction:**
```json
{"mandate_alignment": "low", "risk_level": "high", "confidence": 0.91, "evidence": ["spend has shifted away from allowed categories", "pattern persists across multiple transactions, not one outlier"]}
```

## HOLD is a real state, never a dead end
```
risk = HIGH or MEDIUM → HOLD → user confirmation requested
   ├── user confirms  → ALLOW
   ├── user denies    → BLOCK
   └── timeout        → BLOCK  (fail-closed default, never left unresolved)
```

---

## Evaluation methodology — the actual center of the project

**Core experiment: paired scenarios, matched on every deterministic signal, opposite in meaning.**
Example — Case A (legitimate one-time spike: hosting an event) and Case B (gradual drift toward unrelated purchases) are constructed with near-identical total spend, transaction count, velocity, and category-shift magnitude — yet opposite correct labels. If the hybrid system tells these apart while a rules-only baseline can't, that's a defensible, narrow claim: *these specific deterministic features are insufficient to distinguish these cases* — not an overclaim that no statistical method anywhere ever could solve it.

**Mandatory baseline:** rules-only vs. hybrid system, same held-out dataset, reported honestly even if the hybrid doesn't win outright. (LLM-only baseline is a stretch addition, not required.)

**Metrics — broken down by drift type (fast-split vs. slow-drift), never blended into one number:** precision, recall, false-positive rate, false-negative rate.

**Cost model:** explicitly-labeled-as-assumed illustrative costs for false positives (customer friction) vs. false negatives (unauthorized spend exposure), showing how the chosen threshold trades one against the other. Stated plainly as a demonstration, not sourced from real Razorpay loss data.

## Defense-only framing (Track 02 requires this — hard rule, not a suggestion)
Synthetic "drift" and "legitimate spike" cases live as static, labeled data fixtures (`fixtures/legitimate/`, `fixtures/drift/`, `fixtures/ambiguous/`) — transaction-pattern data, not attack payloads or anything executable. No generator tool, no reusable exploit capability. Stated explicitly in the repo.

## Data & privacy design principles (say these before a judge asks)
The payment processor already sees every transaction necessarily — this system adds *analysis*, not a new data-collection point. Even so, three deliberate design rules limit what's actually exposed:
- **Bounded retention** — only a rolling recent window (e.g., 6–8 weeks) is used to compute drift signals, not an indefinite spending history.
- **Structured-only data reaches the AI layer** — the evidence packet carries signals (`category_shift: significant`) never raw item-level purchase descriptions or a running diary of purchases.
- **Consent is scoped, not general** — the user delegated spending authority to *this agent* for *this stated mandate*; monitoring stays scoped to that relationship, not their broader financial life.

## Anticipated panel questions (pre-written answers — don't improvise these live)

**"Why not just use rules/statistics instead of an LLM? Banks already do fraud pattern detection."**
Existing tools answer "is this unusual compared to history" — a pure numbers question, and rules are genuinely the right tool for that (that's Layer 1, unchanged). Nothing existing answers "is this unusual spending still okay, given what the person said they wanted" — that requires checking behavior against a stated purpose in natural language, which didn't exist for fraud tools to check against until agent delegation existed. Proven, not just claimed, by the paired-scenario methodology: two cases with *identical* numbers, opposite correct answers — by construction, no rule operating on those numbers alone can get both right.

**"Isn't monitoring customer spend patterns an invasion of privacy?"**
Correction to an earlier oversimplification, worth stating honestly rather than smoothing over: checking compliance needs the **mandate text itself** ("groceries, ₹8,000/week"), not just transaction logs — that genuinely is more data than pure transaction monitoring. What makes this not spying is not the quantity of data, it's that (1) the person authored the mandate themselves at setup — nothing is inferred or extracted by watching them; (2) delegating spending authority to an agent requires *some* instruction to exist in the first place — the mandate isn't added on top of the real product, it's the precondition for delegation working at all, and Razorpay's actual pilot already requires a mandate (currently merchant + amount) for this reason; (3) a stated purpose like "groceries" is low-sensitivity — closer to a budget label than a behavioral profile. Note: don't cite RBI purpose-code requirements as precedent — those apply specifically to international remittances under FEMA, not domestic UPI, and citing them would be a wrong-category comparison that a sharp panelist could catch. See data/privacy principles above for the additional concrete limits (bounded retention, structured-only evidence packets).

**"Who is this actually for — the customer or the merchant?"**
Merchant/platform-side, unambiguously. See "Who this is for" at the top of this document — the customer's data is the subject analyzed; the merchant is who the tool serves and who loses money if drift goes undetected.

## Open item — NOT yet decided, do not silently adopt
Dad's review (Aug 29) suggested splitting the deterministic signal design: use spend-velocity specifically for fast-split drift (where it's genuinely the right signal) and a separate spend-**pattern-consistency** signal for slow-drift (where velocity is noisy and largely irrelevant), rather than one signal set serving both drift types. This is a real architecture change, not an addition — **needs explicit sign-off before it's implemented**, not folded in automatically. Broadened mandate-category taxonomy (bills, fuel, house help, telephone — not just groceries) was separately adopted without objection and should be reflected in the synthetic dataset design.

## Known, named limitations (stated up front, not left for a judge to find)
- Ground truth is synthetic and self-labeled — mitigated by a written rationale attached to every case, not silent labels.
- Cold-start (a brand-new agent with no purchase history) isn't solved — scoped out explicitly, with a one-line note on what production would need instead (conservative default thresholds until history accrues).

## Core MVP vs. Stretch
**Core (must work, fully, honestly evaluated):** mandate schema + 2–3 signals (spend velocity, category drift, clustering) → evidence packet → LLM semantic assessment → policy gate with fail-closed HOLD → audit log → paired-scenario dataset → rules-only-vs-hybrid comparison → per-drift-type metrics.

**Stretch (only if core finishes early):** LLM-only baseline, cost-model threshold curve, simple visual timeline UI, merchant-distribution as a fourth signal.

## Validation & Open Items (added after external reverse-engineering pass, Aug 28)

**Verified:** Razorpay + NPCI's Agentic Payments pilot on Claude (Zomato/Swiggy/Zepto) launched Feb 20, 2026. Confirmed mechanism: UPI Reserve Pay lets a user approve a predefined spending limit *for a merchant*, then complete multiple purchases without repeated PIN authentication.

**Honesty fix — mandate schema:** every confirmed source describes the mandate as **merchant + spending limit** (+ implied period). No source confirms a semantic-purpose field ("household groceries only"). The brief's opening framing implied this field already exists in Razorpay's system — it does not, as far as anything public confirms. **The semantic-purpose field is this project's own proposed enrichment of the mandate concept, not a documented Razorpay feature.** State this plainly in the pitch. It's still defensible — quick-commerce catalogs (Zepto etc.) span far beyond groceries, so drift *within a single merchant relationship* is real even under an amount-only mandate — but the richer mandate object is our design proposal, not observed fact.

**Structural fix — tie this to a merchant loss, explicitly:** Track 02 is scoped around merchant losses (fraud, returns, chargebacks). As written, this project protects the *consumer* who granted the mandate, with no stated link to merchant loss — a real gap against the track's own framing. **Bridge, one paragraph, no architecture change:** undetected mandate drift becomes a chargeback/dispute vector — if a user later claims "my agent wasn't authorized to spend this," that's a dispute against the merchant, and Razorpay/NPCI absorb reputational and operational risk for the agentic-payments program itself. This reframes the project from "consumer protection tool" to "chargeback-prevention layer for the agentic commerce rail" — squarely inside Track 02, no redesign required.

**Eval protocol fix — dev/test split, not yet specified:** build the paired-scenario dataset in two batches: a small **dev set** you're allowed to look at and tune the layer-② prompt against, and a **locked test set** touched exactly once, at the end, to produce the numbers reported in the pitch. Without this split, metrics are optimistic by construction — tuning against your own eventual test set is the same "cherry-picked" failure mode Razorpay explicitly warns against elsewhere in the brief.

**New risk, not previously addressed — confidence calibration:** the policy gate leans on layer ②'s self-reported `confidence` field. LLM self-reported confidence is frequently poorly calibrated — high confidence on wrong answers is a known failure mode, and it would quietly defeat the fail-closed design (a wrong-but-confident output slides to ALLOW instead of HOLD). **Add an explicit eval step:** check whether `confidence` actually correlates with correctness on the held-out set. If it doesn't, either empirically recalibrate the ALLOW/HOLD/BLOCK thresholds against observed reliability, or stop trusting the raw self-reported number and derive a confidence proxy from evidence-packet completeness instead.

**Demo fix — promote the visual out of stretch:** a 5-minute pitch with zero visual moment is a real liability for an inherently less-flashy Track 02 project. The paired-scenario methodology is the strongest asset in this brief and it's currently invisible — a simple side-by-side timeline (Case A vs. Case B, near-identical charts, one HOLD and one ALLOW, evidence packet shown) makes the entire "trajectory, not any single purchase" thesis visible in about 15 seconds of pitch video. **This moves from stretch to first-priority-after-core-logic-works, not true stretch.**

**Timeline correction — this changes the plan below, not the architecture:** as of Aug 28, the deadline (Sept 5) is 8 days out. Four separate pre-code documents is too much process ceremony against that clock for a solo, first-time-shipping builder. Collapsing to one document, below.

## Next planned step (revised, replaces the four-document plan)
Write **one combined build-plan document**, timeboxed to half a day max — it should pull together: the architecture (already written above, don't re-derive it), the drift-mechanism/threat-model points (already written above), the paired-scenario dataset spec (needs to go from concept to a concrete list of cases, including the dev/test split), and a day-by-day plan through Sept 5 matching the core/stretch split. Code starts the day after this is done, not later.
