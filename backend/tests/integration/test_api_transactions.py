"""API tests for POST /transactions (Checkpoint C12). Real Postgres via the `api_client`
fixture (tests/integration/conftest.py), not mocks -- routing/serialization/auth only, so
every request here is deliberately kept on the nominal ALLOW path (small amount, in-mandate
category) to avoid triggering a real LLM call; pipeline correctness itself is already covered
by tests/integration/test_pipeline_orchestrator.py (Checkpoint C11).
"""

from datetime import datetime, timezone

AUTH = {"Authorization": "Bearer test-bearer-token"}


def _nominal_payload(mandate_id, idempotency_key="idem-1", **overrides):
    payload = {
        "mandate_id": str(mandate_id),
        "merchant": "Local Grocer",
        "category": "groceries",
        "amount": 100.0,
        "occurred_at": datetime.now(timezone.utc).isoformat(),
        "idempotency_key": idempotency_key,
    }
    payload.update(overrides)
    return payload


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


def test_missing_token_returns_401(api_client, make_mandate):
    mandate = make_mandate()

    response = api_client.post("/transactions", json=_nominal_payload(mandate.id))

    assert response.status_code == 401


def test_invalid_token_returns_403(api_client, make_mandate):
    mandate = make_mandate()

    response = api_client.post(
        "/transactions",
        json=_nominal_payload(mandate.id),
        headers={"Authorization": "Bearer wrong-token"},
    )

    assert response.status_code == 403


# ---------------------------------------------------------------------------
# Shape validation -> 400 (not FastAPI's default 422)
# ---------------------------------------------------------------------------


def test_missing_field_returns_400(api_client, make_mandate):
    mandate = make_mandate()
    payload = _nominal_payload(mandate.id)
    del payload["merchant"]

    response = api_client.post("/transactions", json=payload, headers=AUTH)

    assert response.status_code == 400


def test_non_positive_amount_returns_400(api_client, make_mandate):
    mandate = make_mandate()

    response = api_client.post(
        "/transactions", json=_nominal_payload(mandate.id, amount=0), headers=AUTH
    )

    assert response.status_code == 400


def test_malformed_timestamp_returns_400(api_client, make_mandate):
    mandate = make_mandate()

    response = api_client.post(
        "/transactions", json=_nominal_payload(mandate.id, occurred_at="not-a-timestamp"), headers=AUTH
    )

    assert response.status_code == 400


# ---------------------------------------------------------------------------
# 404
# ---------------------------------------------------------------------------


def test_unknown_mandate_returns_404(api_client):
    response = api_client.post(
        "/transactions",
        json=_nominal_payload("00000000-0000-0000-0000-000000000000"),
        headers=AUTH,
    )

    assert response.status_code == 404


# ---------------------------------------------------------------------------
# 200 -- nominal ALLOW path
# ---------------------------------------------------------------------------


def test_valid_request_returns_200_and_allows(api_client, make_mandate):
    mandate = make_mandate()

    response = api_client.post("/transactions", json=_nominal_payload(mandate.id), headers=AUTH)

    assert response.status_code == 200
    body = response.json()
    assert body["state"] == "allowed"
    assert body["decision"] == "allow"
    assert body["case_id"] is None
    assert body["gate_decision"] is None
    assert body["transaction_id"] is not None


# ---------------------------------------------------------------------------
# Idempotency (Decision 8)
# ---------------------------------------------------------------------------


def test_idempotent_replay_returns_same_transaction_id_and_does_not_duplicate(
    api_client, make_mandate, db_session
):
    mandate = make_mandate()
    payload = _nominal_payload(mandate.id, idempotency_key="idem-replay")

    first = api_client.post("/transactions", json=payload, headers=AUTH)
    second = api_client.post("/transactions", json=payload, headers=AUTH)

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["transaction_id"] == second.json()["transaction_id"]

    from app.db import models

    count = (
        db_session.query(models.Transaction)
        .filter_by(mandate_id=mandate.id, idempotency_key="idem-replay")
        .count()
    )
    assert count == 1


def test_idempotency_key_reuse_with_different_payload_returns_409(api_client, make_mandate):
    mandate = make_mandate()
    payload = _nominal_payload(mandate.id, idempotency_key="idem-conflict")

    first = api_client.post("/transactions", json=payload, headers=AUTH)
    assert first.status_code == 200

    conflicting = dict(payload)
    conflicting["amount"] = 999.0
    second = api_client.post("/transactions", json=conflicting, headers=AUTH)

    assert second.status_code == 409


# ---------------------------------------------------------------------------
# Regression (red-team Category 1, 2026-09-04): occurred_at timezone handling
# ---------------------------------------------------------------------------


def test_naive_occurred_at_is_rejected_as_400_not_500(api_client, make_mandate):
    """RT-C1-003. A naive timestamp used to reach compute_velocity and raise
    `TypeError: can't subtract offset-naive and offset-aware datetimes` -> HTTP 500, i.e. an
    unhandled pipeline exception, which baseline §6 forbids (it must route to HOLD, never a
    crash). It is now refused at the ingestion boundary with an actionable 400."""
    mandate = make_mandate()

    response = api_client.post(
        "/transactions",
        json={
            "mandate_id": str(mandate.id),
            "merchant": "Kirana",
            "category": "groceries",
            "amount": 50,
            "occurred_at": "2026-09-02T10:00:00",  # no offset
            "idempotency_key": "idem-naive-ts",
        },
        headers=AUTH,
    )

    assert response.status_code == 400


def test_offset_aware_occurred_at_is_accepted(api_client, make_mandate):
    """The complement of the above -- an explicit non-UTC offset must still be accepted, so
    the fix rejects only genuinely ambiguous input, not all non-Z timestamps."""
    mandate = make_mandate()

    response = api_client.post(
        "/transactions",
        json={
            "mandate_id": str(mandate.id),
            "merchant": "Kirana",
            "category": "groceries",
            "amount": 50,
            "occurred_at": "2026-09-02T10:00:00+05:30",
            "idempotency_key": "idem-offset-ts",
        },
        headers=AUTH,
    )

    assert response.status_code == 200
