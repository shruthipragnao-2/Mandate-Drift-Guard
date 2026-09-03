"""API tests for POST /cases/{case_id}/resolve (Checkpoint C12). Real Postgres via the
`api_client` fixture -- cases are built directly via conftest.py's `make_case` factory chain
(no pipeline run needed; the state machine itself is already covered by Checkpoint C11's
tests), keeping these tests to routing/serialization/auth only.
"""

from datetime import datetime, timedelta, timezone

AUTH = {"Authorization": "Bearer test-bearer-token"}

RESOLVE_PAYLOAD = {"resolution": "confirm", "resolved_by": "ops-analyst-1", "resolution_reason": "verified legitimate"}


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


def test_missing_token_returns_401(api_client, make_case):
    case = make_case()

    response = api_client.post(f"/cases/{case.id}/resolve", json=RESOLVE_PAYLOAD)

    assert response.status_code == 401


def test_invalid_token_returns_403(api_client, make_case):
    case = make_case()

    response = api_client.post(
        f"/cases/{case.id}/resolve", json=RESOLVE_PAYLOAD, headers={"Authorization": "Bearer wrong-token"}
    )

    assert response.status_code == 403


# ---------------------------------------------------------------------------
# Shape validation -> 400
# ---------------------------------------------------------------------------


def test_invalid_resolution_value_returns_400(api_client, make_case):
    case = make_case()
    payload = dict(RESOLVE_PAYLOAD, resolution="maybe")

    response = api_client.post(f"/cases/{case.id}/resolve", json=payload, headers=AUTH)

    assert response.status_code == 400


# ---------------------------------------------------------------------------
# 404
# ---------------------------------------------------------------------------


def test_unknown_case_returns_404(api_client):
    response = api_client.post(
        "/cases/00000000-0000-0000-0000-000000000000/resolve", json=RESOLVE_PAYLOAD, headers=AUTH
    )

    assert response.status_code == 404


# ---------------------------------------------------------------------------
# 200 -- confirm / deny
# ---------------------------------------------------------------------------


def test_confirm_resolves_to_resolved_allow(api_client, make_case):
    case = make_case()

    response = api_client.post(f"/cases/{case.id}/resolve", json=RESOLVE_PAYLOAD, headers=AUTH)

    assert response.status_code == 200
    body = response.json()
    assert body["case_id"] == str(case.id)
    assert body["new_state"] == "resolved_allow"
    assert body["resolved_at"] is not None


def test_deny_resolves_to_resolved_block(api_client, make_case):
    case = make_case()
    payload = dict(RESOLVE_PAYLOAD, resolution="deny")

    response = api_client.post(f"/cases/{case.id}/resolve", json=payload, headers=AUTH)

    assert response.status_code == 200
    assert response.json()["new_state"] == "resolved_block"


# ---------------------------------------------------------------------------
# 409 -- double-resolve
# ---------------------------------------------------------------------------


def test_double_resolve_returns_409(api_client, make_case):
    case = make_case()

    first = api_client.post(f"/cases/{case.id}/resolve", json=RESOLVE_PAYLOAD, headers=AUTH)
    second = api_client.post(f"/cases/{case.id}/resolve", json=RESOLVE_PAYLOAD, headers=AUTH)

    assert first.status_code == 200
    assert second.status_code == 409


# ---------------------------------------------------------------------------
# 409 -- Decision 18's lazy timeout, checked before the resolution request is processed
# ---------------------------------------------------------------------------


def test_timed_out_case_rejects_resolution_with_409_and_becomes_resolved_block(
    api_client, make_case, db_session
):
    opened_at = datetime.now(timezone.utc) - timedelta(hours=25)  # past the 24h default window
    case = make_case(opened_at=opened_at)

    response = api_client.post(f"/cases/{case.id}/resolve", json=RESOLVE_PAYLOAD, headers=AUTH)

    assert response.status_code == 409

    from app.db import models

    db_session.refresh(case)
    assert case.state == "resolved_block"
    assert case.resolved_by == "system:timeout"

    refreshed_transaction = db_session.get(models.Transaction, case.transaction_id)
    assert refreshed_transaction.state == "blocked"
