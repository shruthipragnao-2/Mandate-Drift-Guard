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

---

# Prompt Calibration Verdict — v1 vs v2 (decided 2026-09-03, human-approved)

**Verdict: `prompt_version = "v1"` is retained, and is the prompt C13's locked test-set run will use.** v2 was tested against the full dev set and rejected. Reverted in commit `a0c6cbb` via a git-based restore from `07fedc8` (the commit preceding v2's `0c34a43`), so v1's `_SYSTEM_PROMPT` and `PROMPT_VERSION` are byte-identical to the originals rather than reconstructed by hand.

This section is hand-written and permanent. It deliberately does **not** replace the `SECTION:DEV_RUN_SUMMARY` block above, which stays as the raw machine-written v2 record. Note that that block is regenerated in place by `eval/report.py` on every run, so once C13 or any later v1 run executes, it will be overwritten with v1 numbers and will no longer show the v2 figures quoted below. The v2 numbers are therefore restated here in full, with provenance, so this verdict remains readable after that happens.

## Provenance of every number below

| Column | Source | How to retrieve |
|---|---|---|
| **v1** | `eval/results/dev_report.json` as committed at `07fedc8` — the post-`created_at`-fix, pre-v2 run | `git show 07fedc8:eval/results/dev_report.json` |
| **v2** | `eval/results/dev_report.json` as committed at `bef0bb7`; same figures as the DEV_RUN_SUMMARY block above | `git show bef0bb7:eval/results/dev_report.json` |

Both runs: 34 cases, dev split only (the `eval/dataset_loader.py` split guard was never bypassed; the locked test set remains untouched), `threshold_T = 0.05`, model `claude-sonnet-5`. Every figure in the table below was read directly out of those two committed JSON artifacts — none is quoted from memory or session recollection.

**Comparability check.** `eval/report.py` did change between the two runs (`bef0bb7`'s log-writer split), so this was verified rather than assumed: the diff touches only imports, additive fields inside `reliability_metrics` (`llm_status_counts`, `timeout_rate`), a new Decision 15 clearance diagnostic, and `main()`. The bodies of `primary_metrics`, `abstention_metrics`, `drift_cases_caught_only_by_hybrid`, and `gate_decision_distribution` are unmodified. Corroborating this, the `rules_only` rows are identical across both reports (fast_spike and slow_drift alike: TP=2 FP=2 FN=5 TN=5, F1=0.3636) — exactly as expected, since the prompt cannot affect the deterministic layer. The hybrid deltas below are attributable to the prompt change.

## Comparison (hybrid layer; `rules_only` identical in both runs)

| Metric | v1 | v2 | Direction |
|---|---|---|---|
| fast_spike TP/FP/FN/TN | 7 / 7 / 0 / 0 | 6 / 7 / 1 / 0 | worse |
| fast_spike P / R / F1 | 0.5000 / 1.0000 / 0.6667 | 0.4615 / 0.8571 / 0.6000 | worse |
| fast_spike FPR | 1.0000 | 1.0000 | unchanged |
| slow_drift TP/FP/FN/TN | 7 / 7 / 0 / 0 | 7 / 6 / 0 / 1 | better |
| slow_drift P / R / F1 | 0.5000 / 1.0000 / 0.6667 | 0.5385 / 1.0000 / 0.7000 | better |
| slow_drift FPR | 1.0000 | 0.8571 | better |
| §9 drift caught only by hybrid | 10 | 9 | worse |
| §12 ambiguous `correct_abstention_rate` | 0.6667 (4/6) | 0.1667 (1/6) | **much worse** |
| §12 ambiguous `overconfidence_on_ambiguous_rate` | 0.1667 (1/6) | 0.6667 (4/6) | **much worse** |
| §12 legitimate `unnecessary_hold_rate` (n=14) | 1.0000 | 0.9286 | better |
| §8 gate distribution allow / hold / none | 0.029 / 0.941 / 0.029 | 0.176 / 0.794 / 0.029 | (see below) |
| §16 `schema_validation_pass_rate` | 0.8485 (28/33) | 1.0000 (33/33) | better |
| §14 gate-rule violations | 0 | 0 | unchanged |
| §15 audit completeness | 34/34 | 34/34 | unchanged |

The single new false negative under v2 is `fixtures/drift/pair_013_fast_spike_single_telephone_drift.json`, which v2 gate-decided `allow`. Verified directly from `eval/results/dev_run_results.json`: it is the only `ground_truth_label == "drift"` case whose hybrid `gate_decision != "hold"`, and v1's report records `fn = 0` for fast_spike, so this case was held under v1 and is genuinely new under v2.

## What v2 did as designed

v2's stated goal was to counter the medium-risk anchoring diagnosed in the postmortem above, and directionally it worked: allow-decisions rose from 1/34 to 6/34, holds fell from 32/34 to 27/34, `unnecessary_hold_rate` on legitimate cases improved, and slow_drift picked up a true negative (FPR 1.0 → 0.857, F1 0.6667 → 0.7000). The 5-case spot-check recorded in `0c34a43` (`eval/results/prompt_v2_validation_5case.json`, retained as a real result) pointed the same way. The mechanism was sound.

## Why it was rejected anyway

The loosening was indiscriminate. It did not selectively relax the cases that deserved relaxing — it moved the model's whole risk posture, and the cases it wrongly relaxed cost more than the ones it rightly relaxed gained:

- **Ambiguous-case abstention collapsed.** This is the sharpest result in the table: `correct_abstention_rate` fell 4/6 → 1/6 while `overconfidence_on_ambiguous_rate` rose 1/6 → 4/6. v2 made the model *confidently wrong* on exactly the cases the design wants it to decline to call. For a fail-closed system whose entire premise is bounded, honest uncertainty, this is the most expensive thing v2 could have broken.
- **A new false negative**, `pair_013_fast_spike_single_telephone_drift` — a drift case released as `allow`. Under a fail-closed mandate, a missed drift is a categorically worse error than an unnecessary hold.
- **Net detection went down**, not up: §9 fell 10 → 9.
- **The gain was one-sided.** slow_drift improved; fast_spike regressed (F1 0.6667 → 0.6000). Not a wash across subsets — a trade, and not a favourable one.

An encouraging n=5 spot-check did not survive n=34. That is the reusable lesson here: the 5-case sample was drawn from the single-signal/legitimate subset — precisely the cases v2 was built to relax — so it could only ever have confirmed the hypothesis. It measured the intended effect while being structurally blind to the collateral cost. Any future prompt iteration gets validated on the full dev set, including ambiguous and drift cases, before it is considered.

## Caveats, stated rather than glossed

- **Single run per prompt version, n=34, nondeterministic LLM.** No repeated runs, no confidence intervals. The 1-case deltas (§9's 10 → 9, the fast_spike FN) are individually within plausible run-to-run sampling noise and should not be over-read on their own. The abstention collapse (4/6 → 1/6, with the mirrored overconfidence rise) is a much larger swing and is the finding this verdict actually rests on.
- **`schema_validation_pass_rate` favours v2** (0.8485 → 1.0000) and is reported here rather than omitted, since it cuts against the verdict. The formula is unchanged between runs, so it is a real observation; but with one run each it may be sampling variance rather than a property of the prompt. It was not treated as decisive either way.
- **v1's reliability block lacks `llm_status_counts` and `timeout_rate`** — those fields did not exist at `07fedc8`. Those two v2 figures have no v1 counterpart and are therefore excluded from the table rather than compared against an assumed value.
- **The prior dev-set summary in this log was overwritten** by the v2 run before this section existed (the DEV_RUN_SUMMARY block is regenerable). The v1 column above is reconstructed from the committed `07fedc8` JSON artifact, which is why provenance is spelled out above: it is recoverable from git, not from this log's own history.

<!-- SECTION:LOCKED_TEST_SET_RUN:START -->
# LOCKED Test-Set Run — Checkpoint C13 / Milestone M8 (2026-09-04)

**This is the final, one-time locked-test-set run. These are the numbers that get reported.** No further threshold or prompt change may follow this run without invalidating it -- per docs/IMPLEMENTATION-BASELINE.md's "touched exactly once, at the end" policy, this section is not expected to ever be regenerated. If it ever is, that is itself a finding to report, not a routine rerun.

*eval/run_locked_test.py, run against the LOCKED TEST SET ONLY (eval/dataset_loader.py's `load_test_cases(confirm=True)`, the sole sanctioned reader of split='test' rows). 66 cases, prompt_version="v1", rules-only threshold_T=0.05 (hardcoded, not recalibrated against this data).*

## §7 Primary metrics (precision/recall/F1/FPR), by drift_type

**fast_spike**
- rules_only: TP=6 FP=6 FN=8 TN=8 P=0.5000 R=0.4286 F1=0.4615 FPR=0.4286
- hybrid: TP=14 FP=14 FN=0 TN=0 P=0.5000 R=1.0000 F1=0.6667 FPR=1.0000

**slow_drift**
- rules_only: TP=5 FP=5 FN=9 TN=9 P=0.5000 R=0.3571 F1=0.4167 FPR=0.3571
- hybrid: TP=12 FP=13 FN=2 TN=1 P=0.4800 R=0.8571 F1=0.6154 FPR=0.9286

## §8 Gate-decision distribution (hybrid): {'allow': 0.07575757575757576, 'hold': 0.8333333333333334, 'none': 0.09090909090909091}

## §9 Drift_cases_caught_only_by_hybrid: 15

## §12 Abstention metrics: {'n_ambiguous': 10, 'correct_abstention_rate': 0.2, 'overconfidence_on_ambiguous_rate': 0.2, 'n_legitimate': 28, 'unnecessary_hold_rate': 0.9642857142857143}

## §14 Gate-rule-violation count (measured, target 0): 0

## §15 Audit completeness: {'total': 66, 'complete': 66, 'rate': 1.0}

## §16 Reliability: {'llm_calls': 60, 'llm_status_counts': {'success': 60}, 'schema_validation_pass_rate': 1.0, 'timeout_rate': 0.0, 'pipeline_error_rate': 0.0, 'pipeline_error_count': 0}

## Decision 15 clearance, single-signal/legitimate subset: {'n_subset': 7, 'n_cleared': 1, 'cleared_case_ids': ['0eedabac-7201-4d5e-ab9c-eefe59a63ec0']}
<!-- SECTION:LOCKED_TEST_SET_RUN:END -->


<!-- SECTION:LOCKED_TEST_SET_RUN_ATTEMPT_1_VOID:START -->
# LOCKED Test-Set Run — ATTEMPT 1 (VOID, 2026-09-04)

**VOID.** This attempt failed due to a working-directory error during invocation, not a system defect: `eval/run_locked_test.py` was run with cwd=repo root instead of cwd=backend/ (this project's established convention for every other eval/test invocation). `app.config.Settings` resolves its `env_file` relative to the process's current working directory (confirmed by reading `DotEnvSettingsSource._read_env_files`), so `backend/.env` was never found, `anthropic_api_key` silently defaulted to `None`, and every case whose deterministic signals crossed the threshold failed with an Anthropic SDK authentication error (`Could not resolve authentication method...`) instead of producing a real assessment -- 60 of 66 cases errored this way, and the remaining 6 simply never triggered an LLM call. Zero cases produced real LLM output; the hybrid-column metrics below are consequently degenerate (all-zero confusion matrices, 100% "none" gate decisions) and must not be read as a real result. `app/config.py` has since been fixed to anchor `.env` resolution to its own file location rather than the caller's cwd. This attempt is void and superseded by the immediate re-run recorded in the "LOCKED_TEST_SET_RUN" section above/below -- retained here, unaltered, for transparency per this project's documentation discipline, not deleted or hidden.

---

# LOCKED Test-Set Run — Checkpoint C13 / Milestone M8 (2026-09-04)

**This is the final, one-time locked-test-set run. These are the numbers that get reported.** No further threshold or prompt change may follow this run without invalidating it -- per docs/IMPLEMENTATION-BASELINE.md's "touched exactly once, at the end" policy, this section is not expected to ever be regenerated. If it ever is, that is itself a finding to report, not a routine rerun.

*eval/run_locked_test.py, run against the LOCKED TEST SET ONLY (eval/dataset_loader.py's `load_test_cases(confirm=True)`, the sole sanctioned reader of split='test' rows). 66 cases, prompt_version="v1", rules-only threshold_T=0.05 (hardcoded, not recalibrated against this data).*

## §7 Primary metrics (precision/recall/F1/FPR), by drift_type

**fast_spike**
- rules_only: TP=6 FP=6 FN=8 TN=8 P=0.5000 R=0.4286 F1=0.4615 FPR=0.4286
- hybrid: TP=0 FP=0 FN=14 TN=14 P=0.0000 R=0.0000 F1=0.0000 FPR=0.0000

**slow_drift**
- rules_only: TP=5 FP=5 FN=9 TN=9 P=0.5000 R=0.3571 F1=0.4167 FPR=0.3571
- hybrid: TP=0 FP=0 FN=14 TN=14 P=0.0000 R=0.0000 F1=0.0000 FPR=0.0000

## §8 Gate-decision distribution (hybrid): {'allow': 0.0, 'hold': 0.0, 'none': 1.0}

## §9 Drift_cases_caught_only_by_hybrid: 0

## §12 Abstention metrics: {'n_ambiguous': 10, 'correct_abstention_rate': 0.0, 'overconfidence_on_ambiguous_rate': 0.0, 'n_legitimate': 28, 'unnecessary_hold_rate': 0.0}

## §14 Gate-rule-violation count (measured, target 0): 0

## §15 Audit completeness: {'total': 66, 'complete': 6, 'rate': 0.09090909090909091}

## §16 Reliability: {'llm_calls': 0, 'llm_status_counts': {}, 'schema_validation_pass_rate': None, 'timeout_rate': None, 'pipeline_error_rate': 0.9090909090909091, 'pipeline_error_count': 60}

## Decision 15 clearance, single-signal/legitimate subset: {'n_subset': 0, 'n_cleared': 0, 'cleared_case_ids': []}
<!-- SECTION:LOCKED_TEST_SET_RUN_ATTEMPT_1_VOID:END -->
