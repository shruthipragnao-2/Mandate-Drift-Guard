# Rules-Only Baseline Calibration Log — Checkpoint C11

*eval/calibrate_baseline.py, run against the DEV SET ONLY (eval/dataset_loader.py's hard split guard) -- the locked test set was never touched. Per eval-design.md §5: "`threshold_T` is chosen by sweeping candidate values on the dev set and picking the value that maximizes dev-set F1 ... recorded explicitly so the choice is reproducible, not eyeballed."*

Dev-set legitimate/drift cases scored: 14 legitimate, 14 drift (ambiguous cases excluded from F1, per eval-design §7/§12).

Rule swept (eval-design §5, exact): `IF velocity == elevated AND category_shift_ratio >= threshold_T THEN HOLD ELSE ALLOW`.

## Full sweep table

| threshold_T | TP | FP | FN | TN | Precision | Recall | F1 |
|---|---|---|---|---|---|---|---|
| 0.05 **(chosen)** | 4 | 4 | 10 | 10 | 0.5000 | 0.2857 | 0.3636 |
| 0.10 | 2 | 2 | 12 | 12 | 0.5000 | 0.1429 | 0.2222 |
| 0.15 | 2 | 2 | 12 | 12 | 0.5000 | 0.1429 | 0.2222 |
| 0.20 | 2 | 2 | 12 | 12 | 0.5000 | 0.1429 | 0.2222 |
| 0.25 | 2 | 2 | 12 | 12 | 0.5000 | 0.1429 | 0.2222 |
| 0.30 | 2 | 2 | 12 | 12 | 0.5000 | 0.1429 | 0.2222 |
| 0.35 | 2 | 2 | 12 | 12 | 0.5000 | 0.1429 | 0.2222 |
| 0.40 | 2 | 2 | 12 | 12 | 0.5000 | 0.1429 | 0.2222 |
| 0.45 | 2 | 2 | 12 | 12 | 0.5000 | 0.1429 | 0.2222 |
| 0.50 | 2 | 2 | 12 | 12 | 0.5000 | 0.1429 | 0.2222 |

## Chosen value: `threshold_T = 0.05` (dev-set F1 = 0.3636)

TP=4, FP=4, FN=10, TN=10, Precision=0.5000, Recall=0.2857.

This value is read by `eval/run.py` when scoring the rules-only baseline -- not re-swept or hardcoded a second time.

---

## Postmortem: `eval/run.py`'s `mandate.created_at` bug (found 2026-09-03, fixed same day)

**The bug.** `eval/run.py`'s `_run_hybrid()` constructed the DB `Mandate` row inline
(`models.Mandate(purpose=..., budget=..., period_days=..., allowed_categories=...)`) and
never passed `created_at`. Since `Mandate.created_at` has `server_default=func.now()`, every
mandate silently got the real wall-clock time the script happened to run at (e.g.
2026-09-03T13:22Z) instead of the fixture's intended `2026-08-01T00:00Z`. Every fixture's
transactions occur *before* that real insert time, so `compute_velocity`'s
`days_elapsed = max(1, (as_of - mandate.created_at).days)` floored to 1 for every single dev
case — inflating `expected_fraction` (and therefore the velocity ratio) far beyond what the
dataset was designed to produce.

**How it was found.** A read-only diagnostic request (print per-case LLM output, sorted by
triggering-signal count) cross-checked the stored `triggering_signals` against a direct
recomputation from the fixture files and found a mismatch on the very first case compared.
Widening the check confirmed it was systematic, not isolated: all 10 of the dataset's
`slow_drift`-single-signal pairs — deliberately built with `velocity="normal"` so that
`category_shift` was the *only* intended trigger — showed `spend_velocity` triggering anyway
in the stored C11 run.

**Scope confirmed by audit** (human-requested, 2026-09-03): of the four sites that construct a
Mandate object from fixture JSON, only `eval/run.py` had this bug. `eval/calibrate_baseline.py`
never constructs a DB row at all — it only ever consumed `CaseRecord.mandate`
(`eval/dataset_loader.py`'s `_Mandate` dataclass), whose `created_at` was always correctly
parsed from the fixture. `eval/populate_dataset_cases.py` never constructs a Mandate object at
all (it only writes `dataset_cases` rows). `backend/tests/integration/conftest.py`'s
`make_mandate` fixture doesn't load fixture JSON at all — a different, legitimate
explicit-override pattern for hand-built test data, not a bug.

**The fix — structural, not a one-line patch.** `eval/dataset_loader.py` gained
`persist_case_mandate(session, case)`, the single sanctioned way to materialize a
`CaseRecord`'s mandate as a real DB row (`created_at=m.created_at` included, by construction).
`eval/run.py` now calls it instead of constructing `models.Mandate(...)` inline — the
duplication between "the loader parses fixtures correctly" and "`eval/run.py` re-does that
parsing by hand, differently" is what let this bug exist in the first place, so the fix
removes the second code path entirely rather than patching its missing field.

**Effect on results, confirmed by a fresh dev-set run post-fix.** The 10 `slow_drift`-single
pairs now show ONLY `category_shift` (+ `clustering` for some members) triggering —
`spend_velocity` no longer appears in any of them, matching the dataset's intended design
exactly. A direct recomputation from every one of the 34 dev fixtures now matches the stored
run's `triggering_signals` with **0 mismatches** (previously mismatched on every case with a
`slow_drift`-single or ambiguous-category_shift-boundary shape). One case
(`ambiguous_009_category_shift_boundary_household_essentials`, ratio exactly 0.05, the
none/minor boundary's "none" side) now correctly never crosses the threshold at all, and — for
the first time across the pilot and this dev-set run — Decision 15's bounded-downgrade path was
genuinely exercised and produced an ALLOW
(`ambiguous_011_category_shift_boundary_telephone`: single mild `category_shift` signal,
`risk_level=low`, `confidence=0.72`, `mandate_alignment=medium`).

**The C11 report's headline finding is retracted.** "The hybrid system HOLDs on all 34 dev
cases (FPR=1.0 on both drift-type subsets)" was reported as a genuine LLM-calibration finding.
It was at least partly an artifact of this bug corrupting the evidence packets before they
ever reached the LLM, not solely a measurement of LLM behavior. The corrected numbers are
reported in this same log's next dev-set run entry (or the accompanying report — see
`eval/results/dev_report.json` for the machine-readable version of the corrected metrics).

**Rules-only baseline unaffected.** `eval/calibrate_baseline.py`'s sweep table above is
byte-for-byte identical before and after this fix (confirmed by re-running it), exactly as
expected given it never had the bug.

<!-- SECTION:DEV_RUN_SUMMARY:START -->
# Dev-Set Run Summary — prompt_version="v2"

*eval/run.py + eval/report.py, run against the DEV SET ONLY (eval/dataset_loader.py's hard split guard) -- the locked test set was never touched. 34 cases, rules-only threshold_T=0.05.*

## §7 Primary metrics (precision/recall/F1/FPR), by drift_type

**fast_spike**
- rules_only: TP=2 FP=2 FN=5 TN=5 P=0.5000 R=0.2857 F1=0.3636 FPR=0.2857
- hybrid: TP=6 FP=7 FN=1 TN=0 P=0.4615 R=0.8571 F1=0.6000 FPR=1.0000

**slow_drift**
- rules_only: TP=2 FP=2 FN=5 TN=5 P=0.5000 R=0.2857 F1=0.3636 FPR=0.2857
- hybrid: TP=7 FP=6 FN=0 TN=1 P=0.5385 R=1.0000 F1=0.7000 FPR=0.8571

## §8 Gate-decision distribution (hybrid): {'allow': 0.17647058823529413, 'hold': 0.7941176470588235, 'none': 0.029411764705882353}

## §9 Drift_cases_caught_only_by_hybrid: 9

## §12 Abstention metrics: {'n_ambiguous': 6, 'correct_abstention_rate': 0.16666666666666666, 'overconfidence_on_ambiguous_rate': 0.6666666666666666, 'n_legitimate': 14, 'unnecessary_hold_rate': 0.9285714285714286}

## §14 Gate-rule-violation count (measured, target 0): 0

## §15 Audit completeness: {'total': 34, 'complete': 34, 'rate': 1.0}

## §16 Reliability: {'llm_calls': 33, 'llm_status_counts': {'success': 33}, 'schema_validation_pass_rate': 1.0, 'timeout_rate': 0.0, 'pipeline_error_rate': 0.0, 'pipeline_error_count': 0}

## Decision 15 clearance, single-signal/legitimate subset: {'n_subset': 5, 'n_cleared': 1, 'cleared_case_ids': ['c145ecee-52a3-4a8f-89cf-dba926328fd8']}
<!-- SECTION:DEV_RUN_SUMMARY:END -->
