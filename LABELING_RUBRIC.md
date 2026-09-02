# Ground-Truth Labeling Rubric

*Written before any fixture rationale text, per eval-design.md §3: "a fixed labeling rubric,
written before labeling starts and applied identically across all cases." This is what makes
labels reproducible against a stated standard, even without a second rater — self-labeling by
the builder is a real limitation (§3), and this rubric is the honest mitigation, not a way to
pretend the limitation away.*

## The question every case answers

For a given mandate + its full transaction-stream trajectory, one question only:

> **Does the aggregate spending pattern still serve the mandate's stated purpose, or has it
> drifted away from it?**

Not: "is any single transaction suspicious?" Not: "does this transaction match its category
tag?" The unit of judgment is the *trajectory as a whole* (eval-design §1) — a mandate for
"weekly household groceries" can survive one unusual purchase and still clearly serve its
purpose; it can also have every individual transaction look mundane and still, in aggregate,
no longer resemble what was authorized.

## The three labels

### `legitimate`
The aggregate pattern still plausibly serves the mandate's stated purpose, even if it isn't
perfectly steady-state. A one-time deviation (a bulk purchase, a seasonal spike, a purchase in
an adjacent category) counts as legitimate when a reasonable person reading the mandate's
purpose text would say "yes, that's still what this money is for" — even if a deterministic
threshold was crossed along the way. Legitimate does not mean "boring" or "exactly matches
budget pace" — it means the *intent* the mandate describes is still what the spend reflects.

### `drift`
The aggregate pattern no longer plausibly serves the mandate's stated purpose, even if every
individual transaction is unremarkable and even if the category tags attached to those
transactions are not obviously wrong. Drift is a judgment about the *underlying reality* the
transactions represent, not just their surface labels — a transaction correctly tagged
"groceries" can still represent drift if what's actually being purchased, at that volume, for
that recipient, no longer serves "weekly household groceries" as a reasonable person would read
it. Conversely, a transaction that touches a category outside the mandate's explicit allow-list
is not automatically drift if it's a one-off that a reasonable person would still call "close
enough to the stated purpose."

### `abstain_expected`
The case was constructed specifically because a reasonable, careful human reviewer — with the
full transaction detail in front of them, not just a deterministic signal summary — could not
confidently pick `legitimate` or `drift`. This is not a third bucket for "cases I didn't feel
like labeling" — it is reserved for cases deliberately built at a genuine boundary (e.g. a
category-shift ratio sitting exactly on a threshold edge), where the honest answer is "this
could reasonably go either way." `abstain_expected` cases are not scored for precision/recall
(eval-design §12) — they test whether the system also recognizes genuine ambiguity rather than
forcing a confident answer it hasn't earned.

## What this rubric explicitly does NOT use as the deciding factor

- **A crossed deterministic threshold, by itself.** Crossing a threshold is what triggers a
  *second look* (layer ①→② handoff) — it is not itself evidence of drift. A legitimate case can
  and often does cross a threshold (that's the whole point of the fast-spike pair type).
- **Which discrete band a signal landed in.** Two cases can share the exact same
  velocity/category-shift bucket and still deserve opposite labels — that is precisely what the
  paired-scenario methodology (eval-design §2) is built to test. The bucket describes the
  *magnitude* of deviation, not its *legitimacy*.
- **Whether the deployed system (evidence packet → LLM → gate) could actually detect the
  difference.** That is an empirical question the evaluation harness answers later (a later
  milestone), not something the label is contingent on. A case can be legitimately labeled
  `drift` even if today's evidence-packet design (which bucket-collapses out-of-mandate
  categories to a single generic label) might struggle to distinguish it from its paired
  `legitimate` twin — that gap, if it exists, is a finding to report honestly, not a reason to
  avoid constructing the case.

## Boundary cases specifically

A case built deliberately at or within one step of a documented band cutoff
(`app.config.EvidenceEngineThresholds`) is a strong candidate for `abstain_expected` — the
closer the constructed ratio sits to a cutoff, the less a hand-labeled `legitimate`/`drift`
call should be trusted, and the rubric's own honesty requires saying so rather than picking a
side to make the dataset look more decisive than the underlying signal actually is.

## Rationale text requirement

Every case, regardless of label, gets one written sentence (or short paragraph) stating the
specific reason this label was chosen, applying the standard above — not a restatement of the
computed signal bands (those are already in the evidence available to whoever reviews the
label later), and not a post-hoc justification for an arbitrary choice made first and
rationalized after.
