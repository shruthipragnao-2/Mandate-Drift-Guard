"""API tests for GET /cases (Checkpoint C14, queue redesigned 2026-09-05: all three states by
default, merchant/category/amount/severity added, hold-first-by-severity-then-recency sort).
Real Postgres via the `api_client` fixture -- cases are built directly via conftest.py's
`make_case` factory chain, keeping these tests to routing/serialization/auth only.
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest

AUTH = {"Authorization": "Bearer test-bearer-token"}


def test_missing_token_returns_401(api_client):
    response = api_client.get("/cases")

    assert response.status_code == 401


def test_default_returns_cases_in_all_three_states(
    api_client, make_case, make_mandate, make_transaction, make_gate_decision
):
    """Queue redesign: the previous default (`state="hold"` only) is gone. This is the direct
    inverse of what this test used to assert -- a resolved case disappearing from the default
    view was exactly the usability problem the redesign fixes, so a resolved case must now be
    present, not absent.
    """
    mandate = make_mandate(purpose="weekly household groceries")
    transaction = make_transaction(
        mandate=mandate, state="held", merchant="Big Bazaar", category="groceries", amount=1200.0
    )
    gate_decision = make_gate_decision(transaction=transaction)
    hold_case = make_case(transaction=transaction, gate_decision=gate_decision)

    resolved_allow_case = make_case(state="resolved_allow")
    resolved_block_case = make_case(state="resolved_block")

    response = api_client.get("/cases", headers=AUTH)

    assert response.status_code == 200
    body = response.json()
    ids = {c["id"] for c in body["cases"]}
    assert str(hold_case.id) in ids
    assert str(resolved_allow_case.id) in ids
    assert str(resolved_block_case.id) in ids

    row = next(c for c in body["cases"] if c["id"] == str(hold_case.id))
    assert row["mandate_id"] == str(mandate.id)
    assert row["transaction_id"] == str(hold_case.transaction_id)
    assert row["state"] == "hold"
    assert row["opened_at"] is not None
    assert row["mandate_purpose"] == "weekly household groceries"
    # Queue redesign additions: merchant/category/amount were already stored, just not
    # serialized by this endpoint before.
    assert row["merchant"] == "Big Bazaar"
    assert row["category"] == "groceries"
    assert row["amount"] == 1200.0
    assert row["severity"] in ("high", "medium", "low")


def test_explicit_state_filter_still_narrows(api_client, make_case):
    """The `?state=` filter is preserved for narrowing -- only the default changed."""
    hold_case = make_case(state="hold")
    resolved_case = make_case(state="resolved_block")

    response = api_client.get("/cases", params={"state": "resolved_block"}, headers=AUTH)

    assert response.status_code == 200
    ids = {c["id"] for c in response.json()["cases"]}
    assert str(resolved_case.id) in ids
    assert str(hold_case.id) not in ids


# ---------------------------------------------------------------------------
# Severity computation -- three separate cases, matching the redesign's three-way fallback
# exactly. Deliberately three distinct test functions, not one parametrized/combined test:
# each exercises a structurally different DB shape (semantic_assessment present; evidence
# packet only; neither), not just a different input value to the same code path.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "risk_level,expected", [("low", "low"), ("medium", "medium"), ("high", "high")]
)
def test_severity_from_semantic_assessment_risk_level(
    api_client,
    make_case,
    make_transaction,
    make_evidence_packet,
    make_semantic_assessment,
    make_gate_decision,
    risk_level,
    expected,
):
    """Branch 1: a semantic_assessment exists -> severity is its risk_level, directly (Decision
    6 already guarantees the value set). Signals are deliberately left at the SAFEST possible
    reading, so a pass here proves severity is reading risk_level, not silently falling through
    to the deterministic-band branch."""
    transaction = make_transaction(state="held")
    evidence_packet = make_evidence_packet(
        transaction=transaction,
        signals={"spend_velocity": "normal", "category_shift": "none", "clustering": "normal"},
    )
    semantic_assessment = make_semantic_assessment(
        evidence_packet=evidence_packet, risk_level=risk_level
    )
    gate_decision = make_gate_decision(transaction=transaction, semantic_assessment=semantic_assessment)
    case = make_case(transaction=transaction, gate_decision=gate_decision)

    response = api_client.get("/cases", headers=AUTH)

    row = next(c for c in response.json()["cases"] if c["id"] == str(case.id))
    assert row["severity"] == expected


def test_severity_from_worst_band_when_no_semantic_assessment(
    api_client, make_case, make_transaction, make_evidence_packet, make_gate_decision
):
    """Branch 2: an evidence_packet exists but no semantic_assessment (the LLM leg failed
    closed via Decision 14's malformed/timeout/transport_error paths -- a real HOLD, just
    without an LLM read). Severity falls back to the WORST of the three deterministic bands.
    The three bands here are deliberately different tiers (normal=low, minor=medium,
    highly_clustered=high) so a pass actually proves "worst of three", not a single-signal
    lookup that happens to match."""
    transaction = make_transaction(state="held")
    evidence_packet = make_evidence_packet(
        transaction=transaction,
        signals={
            "spend_velocity": "normal",
            "category_shift": "minor",
            "clustering": "highly_clustered",
        },
    )
    gate_decision = make_gate_decision(transaction=transaction)  # semantic_assessment=None
    case = make_case(transaction=transaction, gate_decision=gate_decision)

    response = api_client.get("/cases", headers=AUTH)

    row = next(c for c in response.json()["cases"] if c["id"] == str(case.id))
    assert row["severity"] == "high"


def test_severity_is_high_for_decision_20_backstop_case(api_client, make_case, make_transaction):
    """Branch 3: Decision 20's fail-closed exception backstop -- `cases.gate_decision_id` is
    NULL (migration c4f1b7e2d9a3) and no evidence_packets row exists either, because the
    pipeline threw before either was built. Severity is "high" unconditionally, per explicit
    instruction: an unexplained pipeline failure must not quietly sort to the bottom of the
    queue as if it were unknown or low-priority."""
    transaction = make_transaction(state="held")
    case = make_case(transaction=transaction, gate_decision_id=None)

    response = api_client.get("/cases", headers=AUTH)

    row = next(c for c in response.json()["cases"] if c["id"] == str(case.id))
    assert row["severity"] == "high"


# ---------------------------------------------------------------------------
# Sort order: hold cases first (grouped above all resolved cases), by severity descending
# then opened_at descending within the same severity; resolved cases (both states, combined)
# after, by resolved_at descending.
# ---------------------------------------------------------------------------


def test_sort_order_hold_grouped_by_severity_then_recency_resolved_by_recency(
    api_client,
    make_case,
    make_transaction,
    make_evidence_packet,
    make_semantic_assessment,
    make_gate_decision,
):
    now = datetime.now(timezone.utc)

    def _hold_case(risk_level: str, opened_at: datetime):
        transaction = make_transaction(state="held")
        evidence_packet = make_evidence_packet(transaction=transaction)
        semantic_assessment = make_semantic_assessment(
            evidence_packet=evidence_packet, risk_level=risk_level
        )
        gate_decision = make_gate_decision(
            transaction=transaction, semantic_assessment=semantic_assessment
        )
        return make_case(transaction=transaction, gate_decision=gate_decision, opened_at=opened_at)

    # Two "high" cases at different ages, to prove the opened_at-descending tiebreak actually
    # applies WITHIN a severity tier, not just that severity tiers are grouped correctly.
    hold_high_newer = _hold_case("high", now - timedelta(hours=1))
    hold_high_older = _hold_case("high", now - timedelta(hours=3))
    # Opened more recently than either "high" case, but lower severity -- must still sort
    # AFTER both, proving severity dominates opened_at rather than the reverse.
    hold_low = _hold_case("low", now - timedelta(minutes=30))
    hold_medium = _hold_case("medium", now - timedelta(hours=2))

    # Resolved more recently than either resolution below sorts first among resolved cases,
    # regardless of state -- both states are combined into one group.
    resolved_allow = make_case(state="resolved_allow", resolved_at=now - timedelta(minutes=10))
    resolved_block = make_case(state="resolved_block", resolved_at=now - timedelta(minutes=20))

    response = api_client.get("/cases", headers=AUTH)

    assert response.status_code == 200
    expected_order = [
        hold_high_newer.id,
        hold_high_older.id,
        hold_medium.id,
        hold_low.id,
        resolved_allow.id,
        resolved_block.id,
    ]
    ids_in_order = [uuid.UUID(c["id"]) for c in response.json()["cases"]]
    # Filter to just this test's own ids -- db_session's per-test rollback should already
    # isolate this from other tests' data, but asserting relative order among a known subset
    # is more robust than exact-list equality against the whole (possibly larger) response.
    relevant = [i for i in ids_in_order if i in expected_order]
    assert relevant == expected_order
