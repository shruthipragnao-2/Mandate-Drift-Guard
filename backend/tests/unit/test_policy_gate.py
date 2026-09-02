"""Unit tests for the Policy Gate (Decision 15, Checkpoint C10). No DB, no network.

Includes the eval-design failure fixture #6 (contradictory internal signals) tests, which
this checkpoint's Decision 15 unblocks -- fixture #6 was explicitly non-scoreable until this
exact decision-table cell was resolved (docs/IMPLEMENTATION-PLAN.md §I).
"""

import pytest

from app.domain.evidence_engine.category_shift import CategoryShiftResult
from app.domain.evidence_engine.clustering import ClusteringResult
from app.domain.evidence_engine.velocity import VelocityResult
from app.domain.pipeline import ThresholdCheckResult
from app.domain.policy_gate import ThresholdNotCrossedError, decide
from app.domain.semantic_risk_client import SemanticAssessmentOutcome
from app.schemas.llm_output import LlmOutput

# ---------------------------------------------------------------------------
# Minimal factories -- no DB, no evidence-engine computation needed; these tests exercise
# decide() directly against hand-built results.
# ---------------------------------------------------------------------------


def _velocity(band="normal"):
    return VelocityResult(
        band=band, ratio=1.0, expected_fraction=0.5, actual_fraction=0.5, spend_so_far=100.0,
        days_elapsed=2, cold_start=False,
    )


def _category_shift(band="none"):
    return CategoryShiftResult(
        band=band, ratio=0.0, out_of_mandate_amount=0.0, total_amount=100.0, cold_start=False
    )


def _clustering(band="normal"):
    return ClusteringResult(band=band, ratio=0.2, max_window_count=1, total_count=5, cold_start=False)


def _threshold(*signals):
    return ThresholdCheckResult(crossed=bool(signals), triggering_signals=tuple(signals))


def _llm_outcome(status="success", **llm_output_overrides):
    llm_output = None
    if status == "success":
        defaults = dict(mandate_alignment="medium", risk_level="low", confidence=0.9, evidence=["..."])
        defaults.update(llm_output_overrides)
        llm_output = LlmOutput(**defaults)
    return SemanticAssessmentOutcome(
        status=status,
        llm_output=llm_output,
        raw_response={} if status == "success" else None,
        model_version="claude-sonnet-5",
        prompt_version="v1",
        latency_ms=500.0,
        error_detail=None if status == "success" else "simulated failure",
    )


# ---------------------------------------------------------------------------
# Structural guard: threshold not crossed
# ---------------------------------------------------------------------------


def test_threshold_not_crossed_raises():
    with pytest.raises(ThresholdNotCrossedError):
        decide(_threshold(), _velocity(), _category_shift(), _clustering(), _llm_outcome())


# ---------------------------------------------------------------------------
# Fail-closed: any non-success LLM status holds unconditionally
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("status", ["timeout", "malformed", "transport_error"])
def test_fail_closed_statuses_hold_unconditionally(status):
    threshold = _threshold("spend_velocity")

    result = decide(threshold, _velocity("elevated"), _category_shift(), _clustering(), _llm_outcome(status=status))

    assert result.decision == "hold"
    assert result.rule_applied == f"fail_closed: llm_status={status}"
    assert result.rule_version == "v1"


# ---------------------------------------------------------------------------
# success + risk_level in {medium, high} -> hold
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("risk_level", ["medium", "high"])
def test_success_medium_or_high_risk_holds(risk_level):
    threshold = _threshold("spend_velocity")
    outcome = _llm_outcome(risk_level=risk_level, confidence=0.99, mandate_alignment="high")

    result = decide(threshold, _velocity("elevated"), _category_shift(), _clustering(), outcome)

    assert result.decision == "hold"
    assert result.rule_applied == f"hold: risk_level={risk_level}"


# ---------------------------------------------------------------------------
# Decision 15: bounded downgrade to ALLOW, one scenario per signal
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "signal_name, velocity_band, category_band, clustering_band",
    [
        ("spend_velocity", "elevated", "none", "normal"),
        ("category_shift", "normal", "minor", "normal"),
        ("clustering", "normal", "none", "clustered"),
    ],
)
def test_decision_15_downgrades_to_allow_for_each_mild_single_signal(
    signal_name, velocity_band, category_band, clustering_band
):
    threshold = _threshold(signal_name)
    outcome = _llm_outcome(risk_level="low", confidence=0.7, mandate_alignment="medium")

    result = decide(
        threshold, _velocity(velocity_band), _category_shift(category_band), _clustering(clustering_band), outcome
    )

    assert result.decision == "allow"
    assert result.rule_applied.startswith("bounded_downgrade:")
    assert signal_name in result.rule_applied
    assert "confidence=0.7" in result.rule_applied
    assert "mandate_alignment=medium" in result.rule_applied


def test_confidence_exactly_at_floor_is_inclusive():
    """>= 0.7, not > 0.7 -- the floor value itself must qualify."""
    threshold = _threshold("spend_velocity")
    outcome = _llm_outcome(risk_level="low", confidence=0.7, mandate_alignment="medium")

    result = decide(threshold, _velocity("elevated"), _category_shift(), _clustering(), outcome)

    assert result.decision == "allow"


# ---------------------------------------------------------------------------
# Decision 15's three conditions, each tested as an INDEPENDENT failure case (item 4) --
# an otherwise-qualifying case that fails on exactly one condition.
# ---------------------------------------------------------------------------


def test_downgrade_blocked_by_multiple_triggered_signals():
    """Condition 1 fails: two signals triggered, even though each individually is at its
    mildest band."""
    threshold = _threshold("spend_velocity", "category_shift")
    outcome = _llm_outcome(risk_level="low", confidence=0.9, mandate_alignment="medium")

    result = decide(threshold, _velocity("elevated"), _category_shift("minor"), _clustering(), outcome)

    assert result.decision == "hold"
    assert "multiple_signals_triggered" in result.rule_applied


def test_downgrade_blocked_by_non_mildest_band():
    """Condition 1 fails: exactly one signal triggered, but not at its mildest band."""
    threshold = _threshold("spend_velocity")
    outcome = _llm_outcome(risk_level="low", confidence=0.9, mandate_alignment="medium")

    result = decide(threshold, _velocity("critical"), _category_shift(), _clustering(), outcome)

    assert result.decision == "hold"
    assert "signal_not_mildest_band: spend_velocity=critical" in result.rule_applied


def test_downgrade_blocked_by_confidence_below_floor():
    """Condition 2 fails: confidence just under the 0.7 floor, everything else qualifying."""
    threshold = _threshold("spend_velocity")
    outcome = _llm_outcome(risk_level="low", confidence=0.69, mandate_alignment="medium")

    result = decide(threshold, _velocity("elevated"), _category_shift(), _clustering(), outcome)

    assert result.decision == "hold"
    assert "confidence_below_floor: 0.69<0.7" in result.rule_applied


def test_downgrade_blocked_by_mandate_alignment_low():
    """Condition 3 fails: internal LLM contradiction -- risk_level=low but
    mandate_alignment=low -- fails closed rather than getting the benefit of the doubt."""
    threshold = _threshold("spend_velocity")
    outcome = _llm_outcome(risk_level="low", confidence=0.9, mandate_alignment="low")

    result = decide(threshold, _velocity("elevated"), _category_shift(), _clustering(), outcome)

    assert result.decision == "hold"
    assert "mandate_alignment=low" in result.rule_applied


# ---------------------------------------------------------------------------
# eval-design failure fixture #6 (contradictory internal signals) -- unblocked by Decision 15
# ---------------------------------------------------------------------------


def test_failure_fixture_6_mild_signal_high_risk_holds():
    """Fixture #6, direction 1: a mild single deterministic signal, but the LLM reports high
    risk. Already covered by the generic risk_level-in-{medium,high} row above -- named
    explicitly here for fixture traceability, since fixture #6 was the item explicitly
    blocked pending this checkpoint's Decision 15 (docs/IMPLEMENTATION-PLAN.md §I, §L)."""
    threshold = _threshold("spend_velocity")
    outcome = _llm_outcome(risk_level="high", confidence=0.9, mandate_alignment="medium")

    result = decide(threshold, _velocity("elevated"), _category_shift(), _clustering(), outcome)

    assert result.decision == "hold"


@pytest.mark.parametrize(
    "signal_name, velocity_band, category_band, clustering_band",
    [
        ("spend_velocity", "critical", "none", "normal"),
        ("category_shift", "normal", "severe", "normal"),
        ("clustering", "normal", "none", "highly_clustered"),
    ],
)
def test_failure_fixture_6_severe_signal_low_risk_still_holds(
    signal_name, velocity_band, category_band, clustering_band
):
    """Fixture #6, direction 2 -- the direction that was actually blocked until this
    checkpoint, and the more interesting proof: deterministic signals scream severe, but the
    LLM reports "low" risk with high confidence and no internal contradiction
    (mandate_alignment != "low"). Decision 15 condition 1 still fails here, because a
    severe/critical/highly_clustered band never qualifies as the "mildest triggering band"
    regardless of how confidently the LLM disagrees -- proving the downgrade rule doesn't
    just rubber-stamp "low"."""
    threshold = _threshold(signal_name)
    outcome = _llm_outcome(risk_level="low", confidence=0.99, mandate_alignment="high")

    result = decide(
        threshold, _velocity(velocity_band), _category_shift(category_band), _clustering(clustering_band), outcome
    )

    assert result.decision == "hold"
    assert "signal_not_mildest_band" in result.rule_applied


# ---------------------------------------------------------------------------
# Gate-rule-violation invariant (eval-design §14): count(risk_level=="high" AND
# confidence<floor AND gate_output=="ALLOW") must be 0 -- proven by construction via an
# exhaustive sweep, not sampled test data.
# ---------------------------------------------------------------------------


def test_medium_or_high_risk_can_never_reach_allow_by_construction():
    """decide()'s implementation returns "hold" immediately upon seeing
    `risk_level in ("medium", "high")` (see domain/policy_gate.py), BEFORE ever reading
    confidence, mandate_alignment, or the triggering signals -- that branch's output cannot
    depend on any of those parameters. This means an exhaustive sweep over every other input
    dimension, with risk_level fixed at "medium" or "high", is a COMPLETE enumeration of every
    state that branch can ever produce -- not a sample of a much larger space. If even one
    combination below produced "allow", it would mean the code had changed to route
    confidence/alignment/signal information into that branch, which is exactly the class of
    regression this test exists to catch -- the eval-design §14 gate-rule-violation count is
    therefore 0 by construction, not by coincidence of what the test data happened to cover.
    """
    triggering_shapes = [
        ("spend_velocity",),
        ("category_shift",),
        ("clustering",),
        ("spend_velocity", "category_shift"),
        ("spend_velocity", "category_shift", "clustering"),
    ]
    confidence_values = [0.0, 0.3, 0.69, 0.7, 0.71, 0.9, 1.0]
    mandate_alignment_values = ["low", "medium", "high"]
    velocity_bands = ["elevated", "critical"]
    category_bands = ["minor", "significant", "severe"]
    clustering_bands = ["clustered", "highly_clustered"]
    risk_levels = ["medium", "high"]

    checked = 0
    for risk_level in risk_levels:
        for signals in triggering_shapes:
            for confidence in confidence_values:
                for alignment in mandate_alignment_values:
                    for v_band in velocity_bands:
                        for c_band in category_bands:
                            for cl_band in clustering_bands:
                                threshold = _threshold(*signals)
                                outcome = _llm_outcome(
                                    risk_level=risk_level, confidence=confidence, mandate_alignment=alignment
                                )
                                result = decide(
                                    threshold,
                                    _velocity(v_band),
                                    _category_shift(c_band),
                                    _clustering(cl_band),
                                    outcome,
                                )
                                assert result.decision == "hold", (
                                    f"risk_level={risk_level} produced ALLOW for "
                                    f"signals={signals}, confidence={confidence}, "
                                    f"alignment={alignment} -- gate-rule-violation invariant broken"
                                )
                                checked += 1

    expected = (
        len(risk_levels)
        * len(triggering_shapes)
        * len(confidence_values)
        * len(mandate_alignment_values)
        * len(velocity_bands)
        * len(category_bands)
        * len(clustering_bands)
    )
    assert checked == expected  # sanity check the sweep actually ran every combination
