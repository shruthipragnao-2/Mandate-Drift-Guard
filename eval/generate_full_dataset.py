"""Full-scale dataset generator (Checkpoint M5, full-scale batch -- extends the pilot).

Generalizes the pilot's (eval/generate_dataset.py) hand-written builder functions into a
parameterized, seeded generator, per this checkpoint's instructions: "extend the proven pilot
template, do not redesign it." Reuses `velocity_target_spend` / `category_shift_target_out_of_
mandate` from generate_dataset.py unmodified (proven, not to be rewritten).

Composition (human-approved 2026-09-02): 42 paired cases (21 fast_spike, 21 slow_drift) = 84
fixture files, + 16 unpaired ambiguous cases = 100 total. This is a FRESH, self-contained batch
-- the pilot's 3 pairs + 1 ambiguous case (already on disk under fixtures/, never added to
dataset_cases per the pilot's own deferral note) are left untouched and are not part of this
batch's 100-row count; this batch's fixtures are numbered starting after the pilot's existing
pair_001-003 / ambiguous_001 to avoid filename collision.

Signal-first methodology (unchanged from the pilot): pick target bands -> backward-solve exact
numbers -> assign timing. The legitimate/drift "tell" for every pair is `clustering` timing
(the one signal signal_match does NOT require to match) -- category tags on out-of-mandate
transactions are narrative/audit context only and never reach the evidence packet
(packet_builder.py collapses them to "other"), exactly as the pilot's post-fix design
established.

This script is Stage A + an inline Stage B pre-check (importing the real evidence engine
directly, so generation never ships an unverified pair) -- eval/verify_pairs.py is still run
separately afterward as the mandatory, independently-invoked Stage B gate of record.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURES_DIR = REPO_ROOT / "fixtures"
BACKEND_DIR = REPO_ROOT / "backend"
sys.path.insert(0, str(BACKEND_DIR))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from generate_dataset import (  # noqa: E402  (proven pilot helpers, reused unmodified)
    _iso,
    _mandate,
    _txn,
    category_shift_target_out_of_mandate,
    velocity_target_spend,
)

from app.domain.evidence_engine.category_shift import compute_category_shift  # noqa: E402
from app.domain.evidence_engine.clustering import compute_clustering  # noqa: E402
from app.domain.evidence_engine.velocity import compute_velocity  # noqa: E402

SEED = 42
DEV_FRACTION = 0.35

CATEGORIES = ["groceries", "household essentials", "bills", "telephone", "fuel", "house help"]

CATEGORY_PURPOSE = {
    "groceries": "weekly household groceries",
    "household essentials": "household essentials replenishment",
    "bills": "monthly utility bills payments",
    "telephone": "monthly telephone recharge and bills",
    "fuel": "monthly fuel and vehicle top-up budget",
    "house help": "monthly house help and domestic staff payments",
}

CATEGORY_BUDGET = {
    "groceries": 8000.0,
    "household essentials": 5000.0,
    "bills": 6000.0,
    "telephone": 2000.0,
    "fuel": 4000.0,
    "house help": 6000.0,
}

# (legit_tag, drift_tag) -- narrative/audit context ONLY, per the pilot's proven finding that
# packet_builder.py collapses any non-allowed category to the literal "other" regardless of tag.
OUT_OF_MANDATE_TAGS = {
    "groceries": ("bulk_resale_stock", "wholesale_flip_stock"),
    "household essentials": ("subscriptions", "entertainment"),
    "bills": ("streaming_addons", "gaming_subscriptions"),
    "telephone": ("gadget_accessories", "electronics_upgrade"),
    "fuel": ("vehicle_addons", "vehicle_resale_parts"),
    "house help": ("staff_welfare", "personal_grooming"),
}

CATEGORY_MERCHANT_WORD = {
    "groceries": "Grocers",
    "household essentials": "Home Essentials Store",
    "bills": "Utility Board",
    "telephone": "Telecom Ltd",
    "fuel": "Fuel Station",
    "house help": "Household Staff Payroll",
}

ELEVATED_RATIOS = [1.4, 1.5, 1.6, 1.7, 1.8, 1.9]
CRITICAL_RATIOS = [2.2, 2.5, 2.8, 3.0]
NORMAL_RATIOS = [0.8, 0.9, 1.0, 1.1, 1.2]

MINOR_RATIOS = [0.08, 0.10, 0.12, 0.15, 0.18]
SIGNIFICANT_RATIOS = [0.25, 0.30, 0.35, 0.40]
SEVERE_RATIOS = [0.50, 0.55, 0.60]


# ---------------------------------------------------------------------------
# Timing helpers -- generalize the pilot's per-pair hand-crafted "spread" (steady, low
# clustering) vs "burst" (bunched, high clustering) offset patterns.
# ---------------------------------------------------------------------------


def _spread_days(n: int, last_day: int) -> list[int]:
    """n distinct days ending exactly at `last_day`, each pairwise >=1 day apart (>=24h at a
    fixed hour-of-day) -- generalizes the pilot's evenly-spread offset lists."""
    if n == 1:
        return [last_day]
    gap = max(1, (last_day - 1) // (n - 1))
    return [last_day - (n - 1 - i) * gap for i in range(n)]


def _burst_hours(k: int) -> list[int]:
    """k hours-of-day, strictly increasing, spanning less than 24h total -- so any 24h sliding
    window containing the first also contains all k (max_window_count == k for this cluster)."""
    if k == 1:
        return [12]
    return [round(24 * (i + 1) / (k + 1)) for i in range(k)]


def _build_offsets(
    n: int, days_elapsed: int, style: str, burst_count: int = 0, spread_hour: int = 9, buffer_days: int = 2
) -> list[timedelta]:
    if style == "spread":
        days = _spread_days(n, days_elapsed)
        return [timedelta(days=d, hours=spread_hour) for d in days]

    if style == "burst":
        assert 1 <= burst_count <= n, (n, burst_count)
        spread_n = n - burst_count
        offsets: list[timedelta] = []
        if spread_n > 0:
            spread_last_day = max(0, days_elapsed - buffer_days)
            offsets += [
                timedelta(days=d, hours=spread_hour) for d in _spread_days(spread_n, spread_last_day)
            ]
        offsets += [timedelta(days=days_elapsed, hours=h) for h in _burst_hours(burst_count)]
        return offsets

    raise ValueError(style)


def _split_amount(total: float, count: int) -> list[float]:
    """count amounts summing exactly to `total` (2dp) -- last absorbs the rounding residual."""
    if count == 0:
        return []
    base = round(total / count, 2)
    amounts = [base] * count
    residual = round(total - base * count, 2)
    amounts[-1] = round(amounts[-1] + residual, 2)
    return amounts


# ---------------------------------------------------------------------------
# Pair spec + builder
# ---------------------------------------------------------------------------


@dataclass
class PairSpec:
    index: int
    drift_type: str  # "fast_spike" | "slow_drift"
    signal_mode: str  # "single" | "combined"
    category: str
    velocity_band: str  # "normal" | "elevated" | "critical"
    velocity_ratio: float
    category_shift_band: str  # "none" | "minor" | "significant" | "severe"
    category_shift_ratio: float
    n: int
    out_count: int
    burst_count: int
    burst_on_drift: bool
    period_days: int
    days_elapsed: int
    budget: float


def _fast_spike_spec(i: int) -> PairSpec:
    category = CATEGORIES[i % 6]
    combined = (i % 5) < 2  # ~9/21 combined, ~12/21 single -- roughly 40/60 combined/single

    velocity_band = "critical" if i % 7 == 0 else "elevated"
    velocity_pool = CRITICAL_RATIOS if velocity_band == "critical" else ELEVATED_RATIOS
    velocity_ratio = velocity_pool[i % len(velocity_pool)]

    if combined:
        n, out_count = 6, 2
        cs_choice = i % 3
        category_shift_band = ["minor", "significant", "severe"][cs_choice]
        cs_pool = [MINOR_RATIOS, SIGNIFICANT_RATIOS, SEVERE_RATIOS][cs_choice]
        category_shift_ratio = cs_pool[i % len(cs_pool)]
        period_days = [10, 14, 18][i % 3]
        burst_count = [5, 4, 3][i % 3]
    else:
        n, out_count = 3, 0
        category_shift_band = "none"
        category_shift_ratio = 0.0
        period_days = [7, 10, 14][i % 3]
        burst_count = [3, 2][i % 2]

    days_elapsed = period_days - 2
    budget = CATEGORY_BUDGET[category]

    return PairSpec(
        index=i,
        drift_type="fast_spike",
        signal_mode="combined" if combined else "single",
        category=category,
        velocity_band=velocity_band,
        velocity_ratio=velocity_ratio,
        category_shift_band=category_shift_band,
        category_shift_ratio=category_shift_ratio,
        n=n,
        out_count=out_count,
        burst_count=burst_count,
        burst_on_drift=(i % 2 == 0),
        period_days=period_days,
        days_elapsed=days_elapsed,
        budget=budget,
    )


def _slow_drift_spec(i: int) -> PairSpec:
    category = CATEGORIES[(i + 3) % 6]  # offset from fast_spike's cycle to balance category counts
    combined = (i % 5) < 2

    cs_choice = i % 3
    category_shift_band = ["minor", "significant", "severe"][cs_choice]
    cs_pool = [MINOR_RATIOS, SIGNIFICANT_RATIOS, SEVERE_RATIOS][cs_choice]
    category_shift_ratio = cs_pool[i % len(cs_pool)]

    if combined:
        n, out_count = 6, 2
        velocity_band = "critical" if i % 6 == 0 else "elevated"
        velocity_pool = CRITICAL_RATIOS if velocity_band == "critical" else ELEVATED_RATIOS
        velocity_ratio = velocity_pool[i % len(velocity_pool)]
        period_days = [14, 21, 30][i % 3]
        burst_count = [5, 4, 3][i % 3]
    else:
        n, out_count = 5, 2
        velocity_band = "normal"
        velocity_ratio = NORMAL_RATIOS[i % 5]
        period_days = [14, 21, 28][i % 3]
        burst_count = [4, 3][i % 2]

    days_elapsed = round(period_days * 0.7)
    budget = CATEGORY_BUDGET[category]

    return PairSpec(
        index=i,
        drift_type="slow_drift",
        signal_mode="combined" if combined else "single",
        category=category,
        velocity_band=velocity_band,
        velocity_ratio=velocity_ratio,
        category_shift_band=category_shift_band,
        category_shift_ratio=category_shift_ratio,
        n=n,
        out_count=out_count,
        burst_count=burst_count,
        burst_on_drift=(i % 2 == 1),
        period_days=period_days,
        days_elapsed=days_elapsed,
        budget=budget,
    )


def build_pair(spec: PairSpec, pair_number: int) -> tuple[dict, dict]:
    created_at = datetime(2026, 8, 1, tzinfo=timezone.utc)
    in_count = spec.n - spec.out_count

    spend = velocity_target_spend(spec.budget, spec.period_days, spec.days_elapsed, spec.velocity_ratio)
    out_of_mandate = category_shift_target_out_of_mandate(spend, spec.category_shift_ratio)
    in_mandate = round(spend - out_of_mandate, 2)

    in_amounts = _split_amount(in_mandate, in_count)
    out_amounts = _split_amount(out_of_mandate, spec.out_count)
    amounts = in_amounts + out_amounts
    assert len(amounts) == spec.n
    assert abs(sum(amounts) - spend) < 0.01, (sum(amounts), spend)

    legit_offsets = _build_offsets(
        spec.n, spec.days_elapsed, "burst" if not spec.burst_on_drift else "spread", spec.burst_count
    )
    drift_offsets = _build_offsets(
        spec.n, spec.days_elapsed, "burst" if spec.burst_on_drift else "spread", spec.burst_count
    )

    mandate = _mandate(
        purpose=CATEGORY_PURPOSE[spec.category],
        budget=spec.budget,
        period_days=spec.period_days,
        allowed_categories=[spec.category],
        created_at=created_at,
    )

    legit_out_tag, drift_out_tag = OUT_OF_MANDATE_TAGS[spec.category]
    merchant_word = CATEGORY_MERCHANT_WORD[spec.category]

    def _merchants(style_word: str, out_tag: str) -> list[str]:
        names = [f"{merchant_word} {style_word} {j + 1}" for j in range(in_count)]
        names += [f"{out_tag.replace('_', ' ').title()} Vendor {j + 1}" for j in range(spec.out_count)]
        return names

    legit_merchants = _merchants("Regular", legit_out_tag)
    drift_merchants = _merchants("Regular", drift_out_tag)
    in_cat = spec.category
    categories = [in_cat] * in_count + [legit_out_tag] * spec.out_count  # legit fixture tags
    drift_categories = [in_cat] * in_count + [drift_out_tag] * spec.out_count

    legit_txns = [
        _txn(m, c, a, created_at + o)
        for m, c, a, o in zip(legit_merchants, categories, amounts, legit_offsets)
    ]
    drift_txns = [
        _txn(m, c, a, created_at + o)
        for m, c, a, o in zip(drift_merchants, drift_categories, amounts, drift_offsets)
    ]

    legit_style = "spread" if spec.burst_on_drift else "burst"
    drift_style = "burst" if spec.burst_on_drift else "spread"

    def _style_phrase(style: str) -> str:
        return (
            "spread across well-separated days (each pairwise >=24h apart)"
            if style == "spread"
            else "bunched into a single tight burst, hours apart, on the trajectory's final day"
        )

    signal_desc = (
        f"velocity={spec.velocity_band} (ratio {spec.velocity_ratio})"
        if spec.signal_mode == "single" and spec.category_shift_band == "none"
        else (
            f"category_shift={spec.category_shift_band} (ratio {spec.category_shift_ratio})"
            if spec.signal_mode == "single"
            else f"velocity={spec.velocity_band} (ratio {spec.velocity_ratio}) AND "
            f"category_shift={spec.category_shift_band} (ratio {spec.category_shift_ratio})"
        )
    )

    base_id = f"pair_{pair_number:03d}_{spec.drift_type}_{spec.signal_mode}_{spec.category.replace(' ', '_')}"

    legit_rationale = (
        f"Triggering signal(s): {signal_desc}, identical between both members of this pair "
        f"(eval-design.md §2's paired-scenario methodology). The distinguishing 'tell' is "
        f"clustering timing: this (legitimate) member's transactions are {_style_phrase(legit_style)} "
        f"-- reading as a plausible {'steady, spaced-out' if legit_style == 'spread' else 'one-off, single-event'} "
        f"pattern that still serves '{CATEGORY_PURPOSE[spec.category]}' (LABELING_RUBRIC.md). NOTE ON "
        f"WHAT THE LLM ACTUALLY SEES: any out-of-mandate category tag in this fixture "
        f"('{legit_out_tag}') is human-readable audit context only -- packet_builder.py collapses it "
        f"to the literal 'other', identical to the drift twin's tag. The clustering-band difference "
        f"is the real, packet-visible signal distinguishing this pair."
    )
    drift_rationale = (
        f"Same triggering signal(s) as the legitimate twin -- {signal_desc} -- deliberately, per "
        f"eval-design.md §2, but this (drift) member's transactions are {_style_phrase(drift_style)} "
        f"-- reading as a more concerning "
        f"{'systematic, recurring' if drift_style == 'spread' else 'late-bunched, coordinated'} "
        f"trajectory that no longer plausibly serves '{CATEGORY_PURPOSE[spec.category]}' as a "
        f"reasonable person would read it (LABELING_RUBRIC.md). NOTE ON WHAT THE LLM ACTUALLY SEES: "
        f"the out-of-mandate tag ('{drift_out_tag}') is audit context only -- packet_builder.py "
        f"collapses it to 'other' identically to the legitimate twin's tag; the clustering-band "
        f"difference above is the real signal."
    )

    legit = {
        "mandate": mandate,
        "transactions": legit_txns,
        "ground_truth_label": "legitimate",
        "drift_type": spec.drift_type,
        "rationale": legit_rationale,
        "paired_with": f"fixtures/drift/{base_id}_drift.json",
    }
    drift = {
        "mandate": mandate,
        "transactions": drift_txns,
        "ground_truth_label": "drift",
        "drift_type": spec.drift_type,
        "rationale": drift_rationale,
        "paired_with": f"fixtures/legitimate/{base_id}_legit.json",
    }
    return legit, drift


# ---------------------------------------------------------------------------
# Ambiguous singles
# ---------------------------------------------------------------------------


@dataclass
class AmbiguousSpec:
    index: int
    category: str
    boundary_type: str  # "velocity" | "category_shift"
    boundary_ratio: float
    band_at_boundary: str


def _ambiguous_spec(i: int) -> AmbiguousSpec:
    category = CATEGORIES[i % 6]
    boundary_type = "velocity" if i % 2 == 0 else "category_shift"
    if boundary_type == "velocity":
        # normal/elevated boundary (1.3) or elevated/critical boundary (2.0), alternating.
        boundary_ratio = [1.3, 2.0][(i // 2) % 2]
        band_at_boundary = "normal" if boundary_ratio == 1.3 else "elevated"
    else:
        boundary_ratio = [0.05, 0.20, 0.45][(i // 2) % 3]
        band_at_boundary = {0.05: "none", 0.20: "minor", 0.45: "significant"}[boundary_ratio]
    return AmbiguousSpec(
        index=i,
        category=category,
        boundary_type=boundary_type,
        boundary_ratio=boundary_ratio,
        band_at_boundary=band_at_boundary,
    )


def build_ambiguous(spec: AmbiguousSpec, case_number: int) -> dict:
    created_at = datetime(2026, 8, 1, tzinfo=timezone.utc)
    budget = CATEGORY_BUDGET[spec.category]

    if spec.boundary_type == "velocity":
        # expected_fraction = days_elapsed/period_days = 0.5 EXACTLY (10/20) so that
        # spend = ratio * 0.5 * budget rounds to a clean cent value with zero drift from the
        # 2dp currency rounding in velocity_target_spend -- avoids the sub-cent floating noise
        # that would otherwise tip the computed ratio to the wrong side of the cutoff.
        period_days = 20
        days_elapsed = 10
        n = 3
        spend = velocity_target_spend(budget, period_days, days_elapsed, spec.boundary_ratio)
        amounts = _split_amount(spend, n)
        offsets = _build_offsets(n, days_elapsed, "spread")
        categories = [spec.category] * n
        merchants = [f"{CATEGORY_MERCHANT_WORD[spec.category]} Regular {j + 1}" for j in range(n)]
        lower_band, upper_band = {1.3: ("normal", "elevated"), 2.0: ("elevated", "critical")}[
            spec.boundary_ratio
        ]
        rationale = (
            f"Velocity ratio is constructed to land exactly at the {lower_band}/{upper_band} "
            f"boundary ({spec.boundary_ratio}) -- one slightly larger purchase would tip this into "
            f"'{upper_band}', while a slightly smaller one would drop it comfortably into "
            f"'{lower_band}'. Category shift is not part of the ambiguity here (all spend stays "
            f"in-mandate). Genuinely too close to a threshold edge to confidently call legitimate "
            f"or drift by a human standard (LABELING_RUBRIC.md: boundary cases are strong "
            f"abstain_expected candidates)."
        )
    else:
        period_days = 14
        days_elapsed = 10
        in_count, out_count = 6, 2
        n = in_count + out_count
        # total_amount = budget * 0.5 -- keeps velocity comfortably "normal" (not part of the
        # deliberate ambiguity) regardless of category, and stays a clean cent value (budgets
        # are round thousands) so the category_shift ratio below has zero rounding drift.
        total_amount = round(budget * 0.5, 2)
        out_of_mandate = category_shift_target_out_of_mandate(total_amount, spec.boundary_ratio)
        in_mandate = round(total_amount - out_of_mandate, 2)
        amounts = _split_amount(in_mandate, in_count) + _split_amount(out_of_mandate, out_count)
        offsets = _build_offsets(n, days_elapsed, "spread")
        out_tag = OUT_OF_MANDATE_TAGS[spec.category][0]
        categories = [spec.category] * in_count + [out_tag] * out_count
        merchants = [f"{CATEGORY_MERCHANT_WORD[spec.category]} Regular {j + 1}" for j in range(in_count)]
        merchants += [f"{out_tag.replace('_', ' ').title()} Vendor {j + 1}" for j in range(out_count)]
        lower_band, upper_band = {0.05: ("none", "minor"), 0.20: ("minor", "significant"), 0.45: ("significant", "severe")}[
            spec.boundary_ratio
        ]
        rationale = (
            f"Category-shift ratio is constructed to land exactly at the {lower_band}/{upper_band} "
            f"boundary ({spec.boundary_ratio}) -- one additional out-of-mandate transaction would "
            f"tip this into '{upper_band}', while removing one would drop it back into "
            f"'{lower_band}'. Velocity is kept normal, not part of the deliberate ambiguity. A "
            f"human reviewer could reasonably read this as a mild, tolerable extension of "
            f"'{CATEGORY_PURPOSE[spec.category]}' or as the early edge of real drift -- genuinely "
            f"too close to the line to confidently assign legitimate or drift "
            f"(LABELING_RUBRIC.md)."
        )

    mandate = _mandate(
        purpose=CATEGORY_PURPOSE[spec.category],
        budget=budget,
        period_days=period_days,
        allowed_categories=[spec.category],
        created_at=created_at,
    )
    txns = [_txn(m, c, a, created_at + o) for m, c, a, o in zip(merchants, categories, amounts, offsets)]

    return {
        "mandate": mandate,
        "transactions": txns,
        "ground_truth_label": "abstain_expected",
        "drift_type": "n_a",
        "rationale": rationale,
        "paired_with": None,
        "_boundary_type": spec.boundary_type,
        "_boundary_ratio": spec.boundary_ratio,
    }


# ---------------------------------------------------------------------------
# Stage B pre-check (mirrors eval/verify_pairs.py's signal_match exactly)
# ---------------------------------------------------------------------------


@dataclass
class _Mandate:
    purpose: str
    budget: float
    period_days: int
    allowed_categories: list
    created_at: datetime


@dataclass
class _Transaction:
    amount: float
    category: str
    occurred_at: datetime


def _parse_dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _to_domain(case: dict) -> tuple[_Mandate, list[_Transaction]]:
    m = case["mandate"]
    mandate = _Mandate(
        purpose=m["purpose"],
        budget=m["budget"],
        period_days=m["period_days"],
        allowed_categories=m["allowed_categories"],
        created_at=_parse_dt(m["created_at"]),
    )
    txns = [
        _Transaction(amount=t["amount"], category=t["category"], occurred_at=_parse_dt(t["occurred_at"]))
        for t in case["transactions"]
    ]
    return mandate, txns


def _compute_signals(case: dict) -> dict:
    mandate, txns = _to_domain(case)
    velocity = compute_velocity(mandate, txns)
    category_shift = compute_category_shift(mandate, txns)
    clustering = compute_clustering(mandate, txns)
    return {
        "velocity_band": velocity.band,
        "velocity_ratio": velocity.ratio,
        "category_shift_band": category_shift.band,
        "category_shift_ratio": category_shift.ratio,
        "clustering_band": clustering.band,
        "clustering_ratio": clustering.ratio,
        "spend": sum(t.amount for t in txns),
        "count": len(txns),
    }


SPEND_TOLERANCE = 0.05
COUNT_TOLERANCE = 1


def _signal_match(a: dict, b: dict) -> tuple[bool, dict]:
    velocity_match = a["velocity_band"] == b["velocity_band"]
    category_shift_match = a["category_shift_band"] == b["category_shift_band"]
    spend_ok = a["spend"] != 0 and abs(a["spend"] - b["spend"]) / a["spend"] <= SPEND_TOLERANCE
    count_ok = abs(a["count"] - b["count"]) <= COUNT_TOLERANCE
    detail = {
        "velocity_match": velocity_match,
        "category_shift_match": category_shift_match,
        "spend_tolerance_ok": spend_ok,
        "count_tolerance_ok": count_ok,
    }
    return all(detail.values()), detail


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def generate_all() -> tuple[list[tuple[str, str, dict, dict, PairSpec]], list[tuple[str, dict, AmbiguousSpec]]]:
    """Returns (pairs, ambiguous) where pairs is a list of
    (legit_relpath, drift_relpath, legit_case, drift_case, spec) and ambiguous is a list of
    (relpath, case, spec)."""
    pairs = []
    pair_number = 4  # pilot already used pair_001-003
    for i in range(21):
        spec = _fast_spike_spec(i)
        legit, drift = build_pair(spec, pair_number)
        base_id = f"pair_{pair_number:03d}_{spec.drift_type}_{spec.signal_mode}_{spec.category.replace(' ', '_')}"
        pairs.append((f"fixtures/legitimate/{base_id}_legit.json", f"fixtures/drift/{base_id}_drift.json", legit, drift, spec))
        pair_number += 1
    for i in range(21):
        spec = _slow_drift_spec(i)
        legit, drift = build_pair(spec, pair_number)
        base_id = f"pair_{pair_number:03d}_{spec.drift_type}_{spec.signal_mode}_{spec.category.replace(' ', '_')}"
        pairs.append((f"fixtures/legitimate/{base_id}_legit.json", f"fixtures/drift/{base_id}_drift.json", legit, drift, spec))
        pair_number += 1

    ambiguous = []
    case_number = 2  # pilot already used ambiguous_001
    for i in range(16):
        spec = _ambiguous_spec(i)
        case = build_ambiguous(spec, case_number)
        relpath = f"fixtures/ambiguous/ambiguous_{case_number:03d}_{spec.boundary_type}_boundary_{spec.category.replace(' ', '_')}.json"
        ambiguous.append((relpath, case, spec))
        case_number += 1

    return pairs, ambiguous


def verify_all(pairs) -> tuple[list[dict], list[dict]]:
    """Returns (passed, rejected) verification result dicts."""
    passed, rejected = [], []
    for legit_path, drift_path, legit, drift, spec in pairs:
        sig_a = _compute_signals(legit)
        sig_b = _compute_signals(drift)
        matched, detail = _signal_match(sig_a, sig_b)
        result = {
            "legit_path": legit_path,
            "drift_path": drift_path,
            "spec": spec,
            "signals_legit": sig_a,
            "signals_drift": sig_b,
            "matched": matched,
            "detail": detail,
        }
        (passed if matched else rejected).append(result)
    return passed, rejected


def seeded_split(units: list, seed: int = SEED, dev_fraction: float = DEV_FRACTION) -> tuple[list, list]:
    shuffled = list(units)
    random.Random(seed).shuffle(shuffled)
    dev_count = round(len(shuffled) * dev_fraction)
    return shuffled[:dev_count], shuffled[dev_count:]


def write_fixtures(cases: dict[str, dict]) -> None:
    for relative_path, case in cases.items():
        path = REPO_ROOT / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        clean = {k: v for k, v in case.items() if not k.startswith("_")}
        path.write_text(json.dumps(clean, indent=2) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--generate", action="store_true", required=True)
    parser.add_argument("--dry-run", action="store_true", help="Verify only, do not write files.")
    args = parser.parse_args()

    if not args.generate:
        return

    pairs, ambiguous = generate_all()
    passed, rejected = verify_all(pairs)

    print(f"Stage B pre-check: {len(pairs)} pairs checked, {len(rejected)} rejected")
    for r in rejected:
        print(f"  REJECTED: {r['legit_path']} <-> {r['drift_path']} -- {r['detail']}")
    for r in passed[:3]:
        a, b = r["signals_legit"], r["signals_drift"]
        print(
            f"  PASS sample: {r['legit_path']}: velocity={a['velocity_band']}/{b['velocity_band']} "
            f"category_shift={a['category_shift_band']}/{b['category_shift_band']} "
            f"clustering={a['clustering_band']}/{b['clustering_band']}"
        )

    if rejected:
        print("\nRejections found -- fix generator logic before writing fixtures. Aborting write.")
        sys.exit(1)

    if args.dry_run:
        print("\nDry run -- not writing fixtures.")
        return

    fixture_map: dict[str, dict] = {}
    for legit_path, drift_path, legit, drift, spec in pairs:
        fixture_map[legit_path] = legit
        fixture_map[drift_path] = drift
    for relpath, case, spec in ambiguous:
        fixture_map[relpath] = case

    write_fixtures(fixture_map)
    print(f"\nwrote {len(fixture_map)} fixture files ({len(pairs)} pairs + {len(ambiguous)} ambiguous)")


if __name__ == "__main__":
    main()
