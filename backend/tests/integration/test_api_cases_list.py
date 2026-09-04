"""API tests for GET /cases (Checkpoint C14, Ops-analyst case queue). Real Postgres via the
`api_client` fixture -- cases are built directly via conftest.py's `make_case` factory chain,
keeping these tests to routing/serialization/auth only.
"""

AUTH = {"Authorization": "Bearer test-bearer-token"}


def test_missing_token_returns_401(api_client):
    response = api_client.get("/cases")

    assert response.status_code == 401


def test_default_filter_returns_only_hold_cases(api_client, make_case, make_mandate, make_transaction, make_gate_decision):
    mandate = make_mandate(purpose="weekly household groceries")
    transaction = make_transaction(mandate=mandate, state="held")
    gate_decision = make_gate_decision(transaction=transaction)
    hold_case = make_case(transaction=transaction, gate_decision=gate_decision)

    resolved_case = make_case(state="resolved_allow")

    response = api_client.get("/cases", headers=AUTH)

    assert response.status_code == 200
    body = response.json()
    ids = {c["id"] for c in body["cases"]}
    assert str(hold_case.id) in ids
    assert str(resolved_case.id) not in ids

    row = next(c for c in body["cases"] if c["id"] == str(hold_case.id))
    assert row["mandate_id"] == str(mandate.id)
    assert row["transaction_id"] == str(hold_case.transaction_id)
    assert row["state"] == "hold"
    assert row["opened_at"] is not None
    assert row["mandate_purpose"] == "weekly household groceries"


def test_explicit_state_filter_returns_resolved_cases(api_client, make_case):
    case = make_case(state="resolved_block")

    response = api_client.get("/cases", params={"state": "resolved_block"}, headers=AUTH)

    assert response.status_code == 200
    ids = {c["id"] for c in response.json()["cases"]}
    assert str(case.id) in ids
