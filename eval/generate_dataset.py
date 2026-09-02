"""Pilot dataset generator (Checkpoint M5, pilot batch only -- NOT the full dataset).

Signal-first methodology, per this checkpoint's instructions: pick the target band
combination FIRST, algorithmically solve backward for the numbers that produce those bands
(see `velocity_target_spend` / `category_shift_target_out_of_mandate` below), THEN wrap
merchant names / category framing around the numbers. Never the other way around.

This script is Stage A only (eval-design.md §2) -- it does not verify its own output.
`eval/verify_pairs.py` is Stage B, mandatory, against the REAL evidence engine, not this
script's arithmetic. "Generation is not verification."

Decision 16 (docs/IMPLEMENTATION-BASELINE.md §21, 2026-09-02): every transaction's
`occurred_at` must fall within `[mandate.created_at, mandate.created_at + period_days]` --
period-renewal semantics are an explicit, named non-goal of this prototype, not something this
generator (or any fixture) may exercise.

No DB writes -- `dataset_cases` table population is deferred to full-scale generation, a later
checkpoint. This script only writes fixture JSON files.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURES_DIR = REPO_ROOT / "fixtures"


# ---------------------------------------------------------------------------
# JSON helpers
# ---------------------------------------------------------------------------


def _iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def _mandate(purpose: str, budget: float, period_days: int, allowed_categories: list[str], created_at: datetime) -> dict:
    return {
        "purpose": purpose,
        "budget": budget,
        "period_days": period_days,
        "allowed_categories": allowed_categories,
        "created_at": _iso(created_at),
    }


def _txn(merchant: str, category: str, amount: float, occurred_at: datetime) -> dict:
    return {"merchant": merchant, "category": category, "amount": amount, "occurred_at": _iso(occurred_at)}


# ---------------------------------------------------------------------------
# Signal-first backward-solve helpers (the worked example from this checkpoint's
# instructions, made runnable rather than left as by-hand arithmetic).
# ---------------------------------------------------------------------------


def velocity_target_spend(budget: float, period_days: int, days_elapsed: int, target_ratio: float) -> float:
    """Solve for the total spend that produces `target_ratio` under Decision 9's formula.
    expected_fraction = days_elapsed / period_days
    actual_fraction    = target_ratio * expected_fraction
    spend              = actual_fraction * budget

    Rounded to 2dp (currency-precision) so the derived value is a clean number to build a
    transaction stream from, and so floating-point noise (e.g. 8000.000000000001) doesn't leak
    into fixture JSON or the by-hand cross-check assertions below.
    """
    expected_fraction = days_elapsed / period_days
    actual_fraction = target_ratio * expected_fraction
    return round(actual_fraction * budget, 2)


def category_shift_target_out_of_mandate(total_amount: float, target_ratio: float) -> float:
    """Solve for the out-of-mandate spend that produces `target_ratio` under Decision 10.
    Rounded to 2dp -- see `velocity_target_spend`'s docstring."""
    return round(target_ratio * total_amount, 2)


# ---------------------------------------------------------------------------
# Pilot case 1 -- fast-spike pair, single triggering signal: velocity=elevated only.
# ---------------------------------------------------------------------------


def build_pair_1_fast_spike_velocity() -> tuple[dict, dict]:
    """Target bands: BOTH members velocity=elevated, category_shift=none (fully in-mandate --
    there is no out-of-mandate category to lean on for this pair). Clustering now DIFFERS
    between members: signal_match (eval-design.md §2) does not require it to match, and it is
    what actually distinguishes this pair in the LLM-visible evidence packet.

    Fix, 2026-09-02: the original design had every field but merchant name identical between
    twins, and merchant name never reaches the evidence packet (packet_builder.py has no
    merchant field at all, architecture §14) -- making the pair unwinnable by construction,
    not a genuinely hard case. Redesigned so the SAME total spend/count/velocity is
    distributed differently in time: legit bunches all three purchases into one short burst
    (single-event framing); drift spreads them evenly across the window (systematic,
    recurring framing). Category placement (all "groceries", in-mandate) is unchanged and
    identical for both, as originally designed.

    Solve: period_days=7, target velocity_ratio=1.6 (elevated: 1.3 < r <= 2.0), days_elapsed=5
    for both members (same max occurred_at -> same velocity ratio exactly).
    """
    created_at = datetime(2026, 8, 1, tzinfo=timezone.utc)
    budget = 7000.0
    period_days = 7
    days_elapsed = 5
    target_velocity_ratio = 1.6

    spend = velocity_target_spend(budget, period_days, days_elapsed, target_velocity_ratio)
    assert spend == 8000.0, f"derivation check failed: {spend}"  # by-hand cross-check

    mandate = _mandate(
        purpose="weekly household groceries",
        budget=budget,
        period_days=period_days,
        allowed_categories=["groceries", "household essentials"],
        created_at=created_at,
    )

    amounts = [3000.0, 3000.0, 2000.0]
    assert sum(amounts) == spend

    # Legit: all three transactions within hours of each other on day 5 -- a single burst.
    # max_window_count=3 (all fall in one 24h window) -> ratio=3/3=1.0 -> highly_clustered.
    legit_offsets = [timedelta(days=5, hours=8), timedelta(days=5, hours=14), timedelta(days=5, hours=20)]
    # Drift: spread across days 1/3/5, >24h apart pairwise -- same max occurred_at (day 5),
    # so days_elapsed and velocity_ratio are IDENTICAL to legit's, but clustering differs:
    # max_window_count=1 -> ratio=1/3=0.333 -> normal.
    drift_offsets = [timedelta(days=1, hours=10), timedelta(days=3, hours=10), timedelta(days=5, hours=10)]

    legit_merchants = ["Big Bazaar Bulk Order", "Local Wholesale Grocers", "Supermart Stock-up"]
    drift_merchants = ["Metro Cash & Carry Wholesale", "Bulk Grocery Distributors Ltd", "Wholesale Mart Resale Unit"]

    legit_txns = [
        _txn(m, "groceries", a, created_at + o) for m, a, o in zip(legit_merchants, amounts, legit_offsets)
    ]
    drift_txns = [
        _txn(m, "groceries", a, created_at + o) for m, a, o in zip(drift_merchants, amounts, drift_offsets)
    ]

    legit = {
        "mandate": mandate,
        "transactions": legit_txns,
        "ground_truth_label": "legitimate",
        "drift_type": "fast_spike",
        "rationale": (
            "All three bulk purchases land within hours of each other on a single day (day 5 "
            "of 7) -- a tight burst consistent with last-minute preparation for a one-time "
            "household event, not an ongoing pattern. Clustering reads 'highly_clustered' "
            "(3/3 transactions inside one 24h window); spend stays entirely within the "
            "mandate's allowed categories. The single-burst-then-nothing shape is what makes "
            "this plausibly legitimate (LABELING_RUBRIC.md: a one-time deviation that still "
            "serves the stated purpose) -- and, after the 2026-09-02 fix, this is a real, "
            "packet-visible signal (the clustering band), not something only a merchant name "
            "could reveal."
        ),
        "paired_with": "fixtures/drift/pair_001_fast_spike_velocity_drift.json",
    }
    drift = {
        "mandate": mandate,
        "transactions": drift_txns,
        "ground_truth_label": "drift",
        "drift_type": "fast_spike",
        "rationale": (
            "Same total spend, transaction count, and elevated velocity as the legitimate "
            "twin -- deliberately, per eval-design.md §2's paired-scenario methodology -- but "
            "spread evenly across days 1/3/5 instead of bunched into one burst. Clustering "
            "reads 'normal' (1/3, no 24h window holds more than one transaction) -- the one "
            "signal eval-design.md §2 does not require to match between paired members. An "
            "evenly-paced, systematic restocking rhythm across the window reads as the start "
            "of a sustained elevated rate rather than a one-off event, which is a more "
            "concerning pattern than a single burst (LABELING_RUBRIC.md: the trajectory shape "
            "itself, not a merchant name, is what a reasonable person would read this from)."
        ),
        "paired_with": "fixtures/legitimate/pair_001_fast_spike_velocity_legit.json",
    }
    return legit, drift


def build_pair_1_rejected_first_attempt() -> tuple[dict, dict]:
    """The FIRST, naive attempt at pair 1 -- opposite category placement (all in-mandate vs.
    all out-of-mandate), as this checkpoint's pilot-composition instructions literally
    describe. Kept here, not shipped to fixtures/, specifically to demonstrate a real Stage B
    rejection: opposite category placement at the same total spend is structurally
    incompatible with signal_match's category_shift_bucket_A==category_shift_bucket_B clause
    (one bucket will read "none", the other "severe") -- no amount of numeric tuning fixes
    that, because it's not a numeric error, it's the two conditions being mutually exclusive
    by construction. See the pilot report for the actual verify_pairs.py output on this
    candidate.
    """
    legit, _ = build_pair_1_fast_spike_velocity()
    created_at = datetime.fromisoformat(legit["mandate"]["created_at"].replace("Z", "+00:00"))
    offsets = [timedelta(days=1, hours=10), timedelta(days=3, hours=10), timedelta(days=5, hours=10)]
    amounts = [3000.0, 3000.0, 2000.0]
    drift_merchants = ["Consumer Electronics Superstore", "Gadget World Flagship Store", "Premium Electronics Outlet"]

    drift_attempt = {
        "mandate": legit["mandate"],
        "transactions": [
            _txn(m, "electronics", a, created_at + o) for m, a, o in zip(drift_merchants, amounts, offsets)
        ],
        "ground_truth_label": "drift",
        "drift_type": "fast_spike",
        "rationale": "REJECTED CANDIDATE -- see pilot report; kept for the Stage B rejection log only.",
        "paired_with": None,
    }
    return legit, drift_attempt


# ---------------------------------------------------------------------------
# Pilot case 2 -- slow-drift pair, single triggering signal: category_shift=minor only.
# ---------------------------------------------------------------------------


def build_pair_2_slow_drift_category_shift() -> tuple[dict, dict]:
    """Target bands (both members): velocity=normal, category_shift=minor, clustering=normal.

    Solve: total_amount=4000, target category_shift ratio=0.15 (minor: 0.05 < r <= 0.20)
    -> out_of_mandate=600. period_days=14, days_elapsed=10 -> velocity ratio ~0.933 (normal).
    8 transactions on 8 distinct days -> clustering ratio=1/8=0.125 (normal).

    Fix, 2026-09-02: the out-of-mandate CATEGORY TAG now differs between members -- the
    amount (300+300=600) and therefore the category_shift ratio/bucket are identical for
    both (category_shift depends only on in-mandate-vs-not amounts, never on which specific
    out-of-mandate label is used; confirmed against the real compute_category_shift in
    eval/verify_pairs.py, not assumed). Legit keeps "subscriptions" (bill-adjacent, matches
    its rationale); drift uses "entertainment" (clearly unrelated to bills/telephone).
    """
    created_at = datetime(2026, 8, 1, tzinfo=timezone.utc)
    budget = 6000.0
    period_days = 14
    total_amount = 4000.0
    target_category_shift_ratio = 0.15

    out_of_mandate = category_shift_target_out_of_mandate(total_amount, target_category_shift_ratio)
    assert out_of_mandate == 600.0, out_of_mandate
    in_mandate = total_amount - out_of_mandate
    assert in_mandate == 3400.0

    mandate = _mandate(
        purpose="monthly bills and telephone payments",
        budget=budget,
        period_days=period_days,
        allowed_categories=["bills", "telephone"],
        created_at=created_at,
    )

    in_mandate_plan = [
        ("bills", 600.0, 1), ("telephone", 400.0, 2), ("bills", 600.0, 4),
        ("telephone", 400.0, 6), ("bills", 700.0, 7), ("bills", 700.0, 8),
    ]
    assert sum(a for _, a, _ in in_mandate_plan) == in_mandate

    out_of_mandate_amounts_days = [(300.0, 5), (300.0, 10)]
    assert sum(a for a, _ in out_of_mandate_amounts_days) == out_of_mandate

    legit_out_of_mandate_category = "subscriptions"
    drift_out_of_mandate_category = "entertainment"

    days_elapsed = max([d for *_, d in in_mandate_plan] + [d for _, d in out_of_mandate_amounts_days])
    assert days_elapsed == 10

    legit_merchants = {
        "bills": ["City Power & Water Board", "Municipal Bills Portal", "Home Bills Autopay", "Gas Utility Payments"],
        "telephone": ["National Telecom Ltd", "Household Broadband Co"],
        "subscriptions": ["FamilyStream Household Bundle", "HomeBills OTT Add-on"],
    }
    drift_merchants = {
        "bills": ["City Power & Water Board", "Municipal Bills Portal", "Home Bills Autopay", "Gas Utility Payments"],
        "telephone": ["National Telecom Ltd", "Household Broadband Co"],
        "entertainment": ["Personal Gaming Pass", "Solo Streaming Premium"],
    }

    def _build_txns(plan, merchant_map, merchant_index):
        txns = []
        for category, amount, day in plan:
            merchant = merchant_map[category][merchant_index[category]]
            merchant_index[category] += 1
            txns.append(_txn(merchant, category, amount, created_at + timedelta(days=day, hours=9)))
        return txns

    legit_full_plan = in_mandate_plan + [
        (legit_out_of_mandate_category, a, d) for a, d in out_of_mandate_amounts_days
    ]
    drift_full_plan = in_mandate_plan + [
        (drift_out_of_mandate_category, a, d) for a, d in out_of_mandate_amounts_days
    ]
    legit_txns = _build_txns(
        legit_full_plan, legit_merchants, {"bills": 0, "telephone": 0, "subscriptions": 0}
    )
    drift_txns = _build_txns(
        drift_full_plan, drift_merchants, {"bills": 0, "telephone": 0, "entertainment": 0}
    )

    legit = {
        "mandate": mandate,
        "transactions": legit_txns,
        "ground_truth_label": "legitimate",
        "drift_type": "slow_drift",
        "rationale": (
            "A gradual, 15% category-shift toward 'subscriptions' charges bundled alongside "
            "monthly bill payments plausibly represents household streaming/utility-bundle "
            "services commonly grouped with bills; the shift is mild, no single transaction is "
            "anomalous, and the bulk of spend (85%) remains on core bills/telephone categories "
            "(LABELING_RUBRIC.md: mild deviation still plausibly serving the stated purpose)."
        ),
        "paired_with": "fixtures/drift/pair_002_slow_drift_category_drift.json",
    }
    drift = {
        "mandate": mandate,
        "transactions": drift_txns,
        "ground_truth_label": "drift",
        "drift_type": "slow_drift",
        "rationale": (
            "Same 15% category-shift magnitude, spend profile, and gradual (no single "
            "anomalous transaction) pattern as the legitimate twin -- deliberately, per "
            "eval-design.md §2. The recurring charges are tagged 'entertainment' here, not "
            "'subscriptions' -- individual gaming/streaming spend clearly unrelated to the "
            "mandate's stated purpose of household bills and telephone payments, rather than "
            "the bill-adjacent framing of the legitimate twin. A genuine, if modest, drift "
            "(LABELING_RUBRIC.md: underlying reality, not just magnitude, decides the label) "
            "that is now visible in the raw transaction category itself, not only inferable "
            "from a merchant name."
        ),
        "paired_with": "fixtures/legitimate/pair_002_slow_drift_category_legit.json",
    }
    return legit, drift


# ---------------------------------------------------------------------------
# Pilot case 3 -- combined-signal pair: velocity=elevated AND category_shift=minor together.
# Tests that Decision 15's "exactly one signal" downgrade condition correctly blocks a
# downgrade even though both individual bands are mild.
# ---------------------------------------------------------------------------


def build_pair_3_combined_signals() -> tuple[dict, dict]:
    """Target bands (both members): velocity=elevated AND category_shift=minor
    simultaneously, clustering=normal (exactly two signals trigger, not three).

    Solve: period_days=30, days_elapsed=20, target velocity_ratio=1.5 (elevated)
    -> spend=6000 (== budget). target category_shift ratio=0.12 (minor) -> out_of_mandate=720.
    10 transactions on 10 distinct days -> clustering ratio=1/10=0.1 (normal).

    Fix, 2026-09-02: the out-of-mandate CATEGORY TAG now differs between members (amount and
    bucket stay identical -- confirmed via the real compute_category_shift in
    eval/verify_pairs.py, not assumed). Legit uses "staff_welfare" (plausibly adjacent to
    "house help"); drift uses "personal_grooming" (clearly the account holder's own personal
    spend, not the staff's).
    """
    created_at = datetime(2026, 8, 1, tzinfo=timezone.utc)
    budget = 6000.0
    period_days = 30
    days_elapsed = 20
    target_velocity_ratio = 1.5

    spend = velocity_target_spend(budget, period_days, days_elapsed, target_velocity_ratio)
    assert spend == 6000.0, spend

    target_category_shift_ratio = 0.12
    out_of_mandate = category_shift_target_out_of_mandate(spend, target_category_shift_ratio)
    assert out_of_mandate == 720.0, out_of_mandate
    in_mandate = spend - out_of_mandate
    assert in_mandate == 5280.0

    mandate = _mandate(
        purpose="monthly house help and domestic staff payments",
        budget=budget,
        period_days=period_days,
        allowed_categories=["house help"],
        created_at=created_at,
    )

    in_mandate_plan = [
        ("house help", 600.0, 1), ("house help", 600.0, 3), ("house help", 700.0, 5),
        ("house help", 700.0, 7), ("house help", 660.0, 9), ("house help", 660.0, 12),
        ("house help", 680.0, 15), ("house help", 680.0, 18),
    ]
    assert sum(a for _, a, _ in in_mandate_plan) == in_mandate

    out_of_mandate_amounts_days = [(360.0, 10), (360.0, 20)]
    assert sum(a for a, _ in out_of_mandate_amounts_days) == out_of_mandate

    legit_out_of_mandate_category = "staff_welfare"
    drift_out_of_mandate_category = "personal_grooming"

    full_days = [d for *_, d in in_mandate_plan] + [d for _, d in out_of_mandate_amounts_days]
    assert max(full_days) == days_elapsed
    assert len(set(full_days)) == len(full_days)  # every txn on a distinct day

    legit_merchants = {
        "house help": [f"Household Staff Payroll {i + 1}" for i in range(8)],
        "staff_welfare": ["Staff Uniform & Hygiene Supplies", "Staff Welfare Top-up"],
    }
    drift_merchants = {
        "house help": [f"Household Staff Payroll {i + 1}" for i in range(8)],
        "personal_grooming": ["Personal Grooming Salon", "Individual Wellness Spa"],
    }

    def _build_txns(plan, merchant_map, merchant_index):
        txns = []
        for category, amount, day in plan:
            merchant = merchant_map[category][merchant_index[category]]
            merchant_index[category] += 1
            txns.append(_txn(merchant, category, amount, created_at + timedelta(days=day, hours=9)))
        return txns

    legit_full_plan = in_mandate_plan + [
        (legit_out_of_mandate_category, a, d) for a, d in out_of_mandate_amounts_days
    ]
    drift_full_plan = in_mandate_plan + [
        (drift_out_of_mandate_category, a, d) for a, d in out_of_mandate_amounts_days
    ]
    legit_txns = _build_txns(legit_full_plan, legit_merchants, {"house help": 0, "staff_welfare": 0})
    drift_txns = _build_txns(drift_full_plan, drift_merchants, {"house help": 0, "personal_grooming": 0})

    legit = {
        "mandate": mandate,
        "transactions": legit_txns,
        "ground_truth_label": "legitimate",
        "drift_type": "slow_drift",
        "rationale": (
            "Elevated velocity (bulk of the month's staff-payment budget spent within two "
            "thirds of the period) combined with a minor 12% category shift toward "
            "'staff_welfare' plausibly reflects occasional hygiene/uniform provisioning for "
            "the household staff, bundled into the same payment run -- a defensible extension "
            "of 'house help' spend, not a departure from it. Deliberately constructed with "
            "TWO triggering signals at once (not one), to exercise Decision 15's requirement "
            "that a downgrade needs exactly one mild signal, not two."
        ),
        "paired_with": "fixtures/drift/pair_003_combined_signals_drift.json",
    }
    drift = {
        "mandate": mandate,
        "transactions": drift_txns,
        "ground_truth_label": "drift",
        "drift_type": "slow_drift",
        "rationale": (
            "Same elevated-velocity and 12% category-shift magnitude as the legitimate twin, "
            "same two-signals-at-once profile -- deliberately, per eval-design.md §2. Here "
            "the out-of-mandate charges are tagged 'personal_grooming' rather than "
            "'staff_welfare' -- the account holder's own personal grooming/wellness spend, "
            "unrelated to the household staff the mandate pays for, not a similar-sounding "
            "adjacent category (LABELING_RUBRIC.md: underlying reality decides the label, and "
            "here the raw category tag itself now reflects that reality). Also exercises "
            "Decision 15: two signals triggered means no downgrade is available regardless of "
            "how the LLM reads it, which this pair's twin (and any future gate run against "
            "it) should reflect."
        ),
        "paired_with": "fixtures/legitimate/pair_003_combined_signals_legit.json",
    }
    return legit, drift


# ---------------------------------------------------------------------------
# Pilot case 4 -- ambiguous, unpaired: category_shift ratio exactly at the minor/significant
# boundary (0.20).
# ---------------------------------------------------------------------------


def build_ambiguous_category_boundary() -> dict:
    """Target: category_shift ratio == 0.20 exactly -- the minor/significant boundary
    (minor: 0.05 < r <= 0.20; significant: 0.20 < r <= 0.45). At exactly 0.20 the code
    classifies this "minor" (inclusive upper bound), but it sits directly on the edge --
    genuinely too close to confidently call legitimate or drift by a human standard
    (LABELING_RUBRIC.md's boundary-case guidance).

    Solve: total_amount=4000, target ratio=0.20 -> out_of_mandate=800. velocity and
    clustering both kept "normal" (not part of the deliberate ambiguity).
    """
    created_at = datetime(2026, 8, 1, tzinfo=timezone.utc)
    budget = 5000.0
    period_days = 14
    total_amount = 4000.0
    target_category_shift_ratio = 0.20

    out_of_mandate = category_shift_target_out_of_mandate(total_amount, target_category_shift_ratio)
    assert out_of_mandate == 800.0, out_of_mandate
    in_mandate = total_amount - out_of_mandate
    assert in_mandate == 3200.0

    days_elapsed = 10
    # velocity is deliberately kept "normal" here (not part of the ambiguity):
    # actual_fraction = 4000/5000 = 0.8; expected_fraction = 10/14; ratio = 0.8/(10/14) = 1.12
    # (<= 1.3, normal) -- checked against the real evidence engine in Stage B regardless.

    mandate = _mandate(
        purpose="household essentials replenishment",
        budget=budget,
        period_days=period_days,
        allowed_categories=["household essentials"],
        created_at=created_at,
    )

    in_mandate_plan = [
        ("household essentials", 500.0, 1), ("household essentials", 500.0, 2),
        ("household essentials", 600.0, 4), ("household essentials", 600.0, 5),
        ("household essentials", 500.0, 7), ("household essentials", 500.0, 8),
    ]
    assert sum(a for _, a, _ in in_mandate_plan) == in_mandate

    out_of_mandate_plan = [("personal care", 400.0, 9), ("personal care", 400.0, 10)]
    assert sum(a for _, a, _ in out_of_mandate_plan) == out_of_mandate

    full_plan = in_mandate_plan + out_of_mandate_plan
    assert max(day for *_, day in full_plan) == days_elapsed

    merchants = {
        "household essentials": [f"Home Essentials Store {i + 1}" for i in range(6)],
        "personal care": ["Personal Care Pharmacy", "Wellness & Toiletries Shop"],
    }
    merchant_index = {"household essentials": 0, "personal care": 0}
    txns = []
    for category, amount, day in full_plan:
        merchant = merchants[category][merchant_index[category]]
        merchant_index[category] += 1
        txns.append(_txn(merchant, category, amount, created_at + timedelta(days=day, hours=9)))

    return {
        "mandate": mandate,
        "transactions": txns,
        "ground_truth_label": "abstain_expected",
        "drift_type": "n_a",
        "rationale": (
            "Category-shift ratio lands exactly at the minor/significant boundary (0.20, "
            "800 of 4000 total spend on 'personal care') -- one additional out-of-mandate "
            "transaction would tip this into 'significant', while removing one would drop it "
            "comfortably into 'minor'. A human reviewer could reasonably read this as a mild, "
            "tolerable extension of 'household essentials' or as the early edge of a real "
            "drift; genuinely too close to the line to confidently assign legitimate or drift "
            "(LABELING_RUBRIC.md: boundary cases are strong abstain_expected candidates)."
        ),
        "paired_with": None,
    }


# ---------------------------------------------------------------------------
# Writer / CLI
# ---------------------------------------------------------------------------

PILOT_FILES = {
    "fixtures/legitimate/pair_001_fast_spike_velocity_legit.json": None,
    "fixtures/drift/pair_001_fast_spike_velocity_drift.json": None,
    "fixtures/legitimate/pair_002_slow_drift_category_legit.json": None,
    "fixtures/drift/pair_002_slow_drift_category_drift.json": None,
    "fixtures/legitimate/pair_003_combined_signals_legit.json": None,
    "fixtures/drift/pair_003_combined_signals_drift.json": None,
    "fixtures/ambiguous/ambiguous_001_category_boundary.json": None,
}


def generate_pilot() -> dict[str, dict]:
    pair1_legit, pair1_drift = build_pair_1_fast_spike_velocity()
    pair2_legit, pair2_drift = build_pair_2_slow_drift_category_shift()
    pair3_legit, pair3_drift = build_pair_3_combined_signals()
    ambiguous = build_ambiguous_category_boundary()

    return {
        "fixtures/legitimate/pair_001_fast_spike_velocity_legit.json": pair1_legit,
        "fixtures/drift/pair_001_fast_spike_velocity_drift.json": pair1_drift,
        "fixtures/legitimate/pair_002_slow_drift_category_legit.json": pair2_legit,
        "fixtures/drift/pair_002_slow_drift_category_drift.json": pair2_drift,
        "fixtures/legitimate/pair_003_combined_signals_legit.json": pair3_legit,
        "fixtures/drift/pair_003_combined_signals_drift.json": pair3_drift,
        "fixtures/ambiguous/ambiguous_001_category_boundary.json": ambiguous,
    }


def write_fixtures(cases: dict[str, dict]) -> None:
    for relative_path, case in cases.items():
        path = REPO_ROOT / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(case, indent=2) + "\n")
        print(f"wrote {relative_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--pilot", action="store_true", required=True,
        help="Generate the pilot batch only (this checkpoint's scope -- full-scale generation is a later checkpoint).",
    )
    args = parser.parse_args()

    if args.pilot:
        cases = generate_pilot()
        write_fixtures(cases)
        print(f"\npilot batch: {len(cases)} fixture files written under {FIXTURES_DIR}")


if __name__ == "__main__":
    main()
