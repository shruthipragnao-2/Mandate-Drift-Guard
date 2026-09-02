# Dataset Generation Log — M5 Pilot Batch

*2026-09-02. Records every Stage B (`eval/verify_pairs.py`) run against a candidate pair
during pilot construction, per eval-design.md §2's "log the rejection rate" requirement —
including the rejection, not just the final passing state.*

## Rejected candidate: pair 1, attempt 1

**Files** (kept under `eval/rejected_candidates/`, not shipped to `fixtures/`):
`pair_001_attempt1_legit.json`, `pair_001_attempt1_drift.json`.

**Design intent**: fast-spike pair, same mandate (weekly household groceries, budget 7000,
period 7 days), same bulk-purchase magnitude (spend=8000, count=3, days 1/3/5) — legitimate
member's spend entirely inside `allowed_categories` (`groceries`), drift member's spend
entirely in a category outside the mandate (`electronics`), per this checkpoint's literal
pilot-composition description ("spend allocated OUTSIDE allowed_categories instead").

**Stage B result: REJECTED.**
```
[REJECTED] eval/rejected_candidates/pair_001_attempt1_legit.json  <->  eval/rejected_candidates/pair_001_attempt1_drift.json
  velocity:        A=elevated   (ratio=1.5999999999999999)   B=elevated   (ratio=1.5999999999999999)   match=True
  category_shift:  A=none       (ratio=0.0)   B=severe     (ratio=1.0)   match=False
  clustering:      A=normal     (ratio=0.3333333333333333)   B=normal     (ratio=0.3333333333333333)   (not part of signal_match)
  spend:           A=8000.0   B=8000.0   diff=0.0000 <= 0.05   ok=True
  count:           A=3   B=3   diff=0 <= 1   ok=True

Stage B summary: 1 pair(s) checked, 1 rejected
Rejection rate: 100.0%
```

**Why, precisely**: this is not a numeric-tuning miss — it's a structural incompatibility. Any
pair where one member's spend is 100% inside `allowed_categories` and the other's is 100%
outside, at the same total spend, will always produce `category_shift` ratios of ~0.0 and ~1.0
respectively — opposite ends of the band range, never the same bucket, regardless of what
numbers are chosen. Full category-placement inversion and same-bucket `signal_match` are
mutually exclusive by construction for this signal.

**Resolution**: regenerated with the SAME category (`groceries`) for both members at the SAME
amounts/timing (so `category_shift` is identically `"none"` for both, trivially matching), and
moved the legitimate/drift distinction to the *narrative* layer instead — the merchant pattern
implies stockpiling-for-resale (drift) vs. a genuine one-time household event (legitimate),
something the raw category/amount signals cannot reveal by design. See
`fixtures/legitimate/pair_001_fast_spike_velocity_legit.json` /
`fixtures/drift/pair_001_fast_spike_velocity_drift.json` and their `rationale` fields for the
full reasoning, and the pilot report for the honest implication of this choice (the resulting
evidence packets for this specific pair are structurally near-identical, since the packet
schema bucket-collapses categories to `allowed ∪ {"other"}` — see the pilot report's
"observations" section).

## Final pilot batch — clean Stage B run

All three shipped pairs verified in one `--pilot` run, zero rejections:

```
[PASS] fixtures/legitimate/pair_001_fast_spike_velocity_legit.json  <->  fixtures/drift/pair_001_fast_spike_velocity_drift.json
[PASS] fixtures/legitimate/pair_002_slow_drift_category_legit.json  <->  fixtures/drift/pair_002_slow_drift_category_drift.json
[PASS] fixtures/legitimate/pair_003_combined_signals_legit.json  <->  fixtures/drift/pair_003_combined_signals_drift.json

Stage B summary: 3 pair(s) checked, 0 rejected
Rejection rate: 0.0%
```

## Overall pilot rejection rate

Counting the rejected first attempt: **1 rejected / 4 total candidate pairs generated = 25%**,
entirely concentrated in one structurally-impossible design (see above) — once corrected, 3/3
subsequent pairs passed on the first real attempt, because the backward-solved arithmetic
(`eval/generate_dataset.py`'s `velocity_target_spend`/`category_shift_target_out_of_mandate`
helpers) was checked against the real evidence engine, not trusted by hand.

## Fix, 2026-09-02: merchant-name-only distinction was structurally unwinnable

**Problem found**: in all three pairs, every field except merchant name was identical
between legitimate/drift twins. `packet_builder.py` has no merchant field in the evidence
packet schema at all (architecture §14, already locked) — so the LLM received literally
identical input for both members of every pair. Not a genuinely hard case; unwinnable by
construction.

**Fix applied**:
- **Pair 1** (no out-of-mandate category available — both members fully in-mandate):
  redesigned transaction timing so `clustering` genuinely differs between members (legit:
  one burst, `highly_clustered`; drift: spread evenly, `normal`) while `velocity` stays
  identical (elevated, ratio 1.6 for both) and `category_shift` stays identical (`none` for
  both). Re-verified: **PASS**, and confirmed via `packet_builder.build_evidence_packet`
  that `signals.clustering` now genuinely differs in the packet the LLM would see — the
  fix works as intended for this pair.
- **Pairs 2 and 3** (out-of-mandate spend already present): changed the out-of-mandate
  transaction *category tag* between members (pair 2: `subscriptions` vs `entertainment`;
  pair 3: `staff_welfare` vs `personal_grooming`), keeping amount and `category_shift`
  bucket identical. Re-verified: **PASS** on both, `category_shift` ratio identical (0.15
  and 0.12 respectively) between members, confirming the bucket depends only on the
  in-mandate/out-of-mandate split, not the specific tag — as expected, not assumed.

**Finding that still needs a decision before full-scale generation**: printing what
`packet_builder.build_evidence_packet` actually produces for all 6 corrected fixtures shows
pair 1's packets now differ (as intended), but **pairs 2 and 3's packets are still
byte-for-byte identical between legit and drift** — because `_bounded_category` in
`packet_builder.py` collapses every category not in `mandate.allowed_categories` to the
literal string `"other"`, regardless of what the raw tag actually is. `"subscriptions"` and
`"entertainment"` both become `{"other": 600.0}` in the trajectory; `"staff_welfare"` and
`"personal_grooming"` both become `{"other": 720.0}`. The category-tag fix makes the *raw
fixture data* honestly distinguishable (useful for human labeling, audit, and any future
system with finer-grained category visibility) but does **not** change what the currently
deployed evidence packet shows the LLM for pairs 2 and 3 — those two pairs remain unwinnable
by the LLM from packet content alone, exactly as before, just for a more specific reason
now identified. This is a structural consequence of the already-locked `"other"`-bucketing
design (architecture §14's merchant/category free-text exclusion), not a bug in this fix.
Flagged for a decision before generating 60-100 cases at this same shape, since every
category-shift-based pair in a larger batch would inherit the same gap.

## Fix, 2026-09-02 (second pass): timing/clustering distinction for pairs 2 and 3

**Decision, human-approved**: `packet_builder.py`'s category-to-`"other"` collapsing stays
exactly as locked (structural injection-resistance) — this is a fixture-content fix only.
The out-of-mandate category tags from the first-pass fix (`"subscriptions"`/`"entertainment"`
for pair 2, `"staff_welfare"`/`"personal_grooming"` for pair 3) are **kept in the fixture
JSON as human-readable rationale/audit context only** — both rationale texts now say this
explicitly, so a future reviewer isn't misled into thinking the LLM sees the tag. The actual
distinguishing signal is now `clustering`, using the same timing-based approach as pair 1's
original fix.

**Design constraint discovered**: the original in-mandate transaction schedules for pairs 2
and 3 (6 and 8 same-hour, multi-day-apart transactions respectively) made every consecutive
pair of in-mandate transactions land exactly on the 24-hour exclusion boundary — meaning no
two in-mandate transactions could ever share a clustering window with each other. That capped
the maximum reachable window (however the 2 movable out-of-mandate transactions were placed)
at 1 in-mandate + 2 out-of-mandate = 3 transactions — `3/8=0.375` for pair 2 and `3/10=0.3`
for pair 3, both short of the `>0.4` needed to cross into `"clustered"`. Reducing the
in-mandate transaction *count* (not the in-mandate *spend*, *category_shift ratio*, or
*velocity ratio*, all unchanged) — pair 2 from 6 to 3, pair 3 from 8 to 4 — made the same
3-transaction bunch cross the threshold cleanly: `3/5=0.6` and `3/6=0.5` respectively.

**Re-verification (`eval/verify_pairs.py --pilot`), all 3 pairs, 0 rejections**:
```
[PASS] pair_001: clustering A=highly_clustered(1.0)  B=normal(0.333)        (unchanged from prior fix)
[PASS] pair_002: clustering A=normal(0.2)             B=clustered(0.6)       velocity/category_shift/spend/count all match
[PASS] pair_003: clustering A=normal(0.167)           B=clustered(0.5)       velocity/category_shift/spend/count all match

Stage B summary: 3 pair(s) checked, 0 rejected
```

**Packet-diff re-check (`packet_builder.build_evidence_packet`, same proof standard as
pair 1)** — the ONLY difference in the LLM-visible packet for every pair is now
`signals.clustering`:
```
pair_001: legit.signals.clustering='highly_clustered'  vs  drift.signals.clustering='normal'   (all else identical)
pair_002: legit.signals.clustering='normal'             vs  drift.signals.clustering='clustered' (all else identical)
pair_003: legit.signals.clustering='normal'             vs  drift.signals.clustering='clustered' (all else identical)
```
Mandate, `budget_utilization`, `spend_velocity`, `category_shift`, and `trajectory` are
byte-identical between legit/drift for all three pairs — expected, since those are exactly
the fields `signal_match` requires to match. All three pilot pairs are now genuinely
distinguishable by the deployed evidence packet alone, not merely by fixture metadata a
human happens to read.

**Side effect, noted not hidden**: pair 3's drift member now triggers three signals
(velocity + category_shift + clustering) rather than two, since clustering crossed out of
`"normal"`. The pair's original purpose — proving a multi-signal case blocks Decision 15's
"exactly one mild signal" downgrade — is unaffected; three failing conditions block the
downgrade exactly as two would.
