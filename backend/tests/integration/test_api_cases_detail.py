"""API tests for GET /cases/{case_id} (Checkpoint C14, full pipeline-story detail). Real
Postgres via the `api_client` fixture -- cases are built directly via conftest.py's factory
chain, wiring transaction -> evidence_packet -> semantic_assessment -> gate_decision -> case
through the SAME transaction, matching the real pipeline's actual data shape (domain.pipeline's
_persist_crossed_case), keeping these tests to routing/serialization/auth only.
"""

AUTH = {"Authorization": "Bearer test-bearer-token"}


def test_missing_token_returns_401(api_client, make_case):
    case = make_case()

    response = api_client.get(f"/cases/{case.id}")

    assert response.status_code == 401


def test_unknown_case_returns_404(api_client):
    response = api_client.get("/cases/00000000-0000-0000-0000-000000000000", headers=AUTH)

    assert response.status_code == 404


def test_full_pipeline_story_happy_path(
    api_client, make_mandate, make_transaction, make_evidence_packet, make_semantic_assessment, make_gate_decision, make_case
):
    mandate = make_mandate(
        purpose="weekly household groceries", budget=8000, period_days=7,
        allowed_categories=["groceries", "household essentials"],
    )
    transaction = make_transaction(
        mandate=mandate, merchant="Big Bazaar", category="groceries", amount=1200, state="held",
    )
    evidence_packet = make_evidence_packet(
        transaction=transaction,
        signals={"budget_utilization": 0.91, "spend_velocity": "elevated"},
        trajectory={"historical_distribution": {"groceries": 700.0}, "current_distribution": {"groceries": 700.0, "other": 300.0}},
    )
    semantic_assessment = make_semantic_assessment(
        evidence_packet=evidence_packet,
        mandate_alignment="low",
        risk_level="high",
        confidence=0.91,
        evidence=["spend has shifted away from allowed categories"],
    )
    gate_decision = make_gate_decision(
        transaction=transaction,
        semantic_assessment=semantic_assessment,
        decision="hold",
        rule_applied="threshold crossed, routed to HOLD",
    )
    case = make_case(transaction=transaction, gate_decision=gate_decision)

    response = api_client.get(f"/cases/{case.id}", headers=AUTH)

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == str(case.id)
    assert body["state"] == "hold"
    assert body["resolved_at"] is None
    assert body["resolved_by"] is None
    assert body["resolution_reason"] is None

    assert body["mandate"] == {
        "purpose": "weekly household groceries",
        "budget": 8000.0,
        "period_days": 7,
        "allowed_categories": ["groceries", "household essentials"],
    }

    assert body["transaction"]["merchant"] == "Big Bazaar"
    assert body["transaction"]["category"] == "groceries"
    assert body["transaction"]["amount"] == 1200.0
    assert body["transaction"]["state"] == "held"

    assert body["evidence_packet"]["signals"]["spend_velocity"] == "elevated"
    assert body["evidence_packet"]["trajectory"]["current_distribution"]["other"] == 300.0

    assert body["semantic_assessment"]["risk_level"] == "high"
    assert body["semantic_assessment"]["mandate_alignment"] == "low"
    assert body["semantic_assessment"]["confidence"] == 0.91
    assert body["semantic_assessment"]["evidence"] == ["spend has shifted away from allowed categories"]

    assert body["gate_decision"]["decision"] == "hold"
    assert body["gate_decision"]["rule_applied"] == "threshold crossed, routed to HOLD"
    assert body["gate_decision"]["rule_version"] == "v1"


def test_semantic_assessment_is_null_on_fail_closed_path(
    api_client, make_transaction, make_evidence_packet, make_gate_decision, make_case
):
    """Decision 5: a HOLD case can exist with an evidence_packet but no semantic_assessment
    (LLM timeout/malformed output, fail-closed) -- the detail response must report that
    honestly as null, not synthesize or omit the field."""
    transaction = make_transaction(state="held")
    evidence_packet = make_evidence_packet(transaction=transaction)
    gate_decision = make_gate_decision(transaction=transaction, semantic_assessment=None)
    case = make_case(transaction=transaction, gate_decision=gate_decision)

    response = api_client.get(f"/cases/{case.id}", headers=AUTH)

    assert response.status_code == 200
    body = response.json()
    assert body["semantic_assessment"] is None
    assert body["evidence_packet"]["signals"] is not None
    assert body["gate_decision"]["decision"] == "hold"
