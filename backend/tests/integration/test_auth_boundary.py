"""Red-team Category 2: the auth boundary, pinned.

Category 2's live probe found no bypass, but "we probed it once and it held" is not a
regression guard. These tests fix the properties that were actually verified against the
running server, so a future change to `app/auth.py` or to a router's `dependencies=[...]`
cannot quietly reopen them.

The endpoint coverage matters as much as the token cases: Decision 17 extended the single
bearer token to ingestion, and Checkpoint C14 later added two GET routes. The question the
probe asked -- and this file keeps asking -- is whether those two actually received the
dependency or were merely assumed to have it.
"""

import uuid

import pytest

GATED = [
    ("get", "/cases?state=hold", None),
    ("get", "/cases/{case_id}", None),
    ("post", "/transactions", {
        "mandate_id": "00000000-0000-4000-8000-000000000000",
        "merchant": "AuthProbe", "category": "bills", "amount": 1.0,
        "occurred_at": "2026-09-04T09:00:00Z", "idempotency_key": "auth-test",
    }),
    ("post", "/cases/{case_id}/resolve", {
        "resolution": "confirm", "resolved_by": "probe", "resolution_reason": "probe",
    }),
]

GOOD = "test-bearer-token"


def _call(client, method, path, body, headers):
    path = path.format(case_id="00000000-0000-4000-8000-000000000000")
    fn = getattr(client, method)
    return fn(path, json=body, headers=headers) if body else fn(path, headers=headers)


@pytest.mark.parametrize("method,path,body", GATED)
def test_every_gated_endpoint_401s_without_a_token(api_client, method, path, body):
    assert _call(api_client, method, path, body, {}).status_code == 401


@pytest.mark.parametrize("method,path,body", GATED)
def test_every_gated_endpoint_403s_on_a_wrong_token(api_client, method, path, body):
    r = _call(api_client, method, path, body, {"Authorization": "Bearer wrong-value"})
    assert r.status_code == 403


@pytest.mark.parametrize("header", [
    "",                       # empty Authorization
    GOOD,                     # bare token, no scheme
    f"Basic {GOOD}",          # wrong scheme
    f"Token {GOOD}",          # wrong scheme
    "Bearer",                 # scheme only
    "Bearer ",                # empty token after scheme
])
def test_malformed_authorization_headers_are_401(api_client, header):
    """No scheme, or a scheme that is not Bearer, is "who are you" (401) rather than
    "you are not allowed" (403) -- auth.py's documented status convention."""
    r = api_client.get("/cases?state=hold", headers={"Authorization": header})
    assert r.status_code == 401


@pytest.mark.parametrize("scheme", ["Bearer", "bearer", "BEARER", "BeArEr"])
def test_bearer_scheme_is_case_insensitive(api_client, scheme):
    """RFC 7235 §2.1: the auth scheme name is case-insensitive. Accepting these is CORRECT,
    not a bypass -- pinned so nobody "hardens" it into a spec violation later."""
    r = api_client.get("/cases?state=hold", headers={"Authorization": f"{scheme} {GOOD}"})
    assert r.status_code == 200


@pytest.mark.parametrize("token", [
    GOOD.upper(),
    GOOD + "x",
    "x" + GOOD,
    GOOD[:-1],
    GOOD[:-1] + "X",
])
def test_token_value_is_compared_exactly(api_client, token):
    """The scheme is case-insensitive; the token VALUE is not, and is not prefix-matched."""
    r = api_client.get("/cases?state=hold", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 403


def test_non_ascii_token_is_403_not_500(api_client):
    """RT-C2-002's fix encodes to UTF-8 before `secrets.compare_digest`, which raises
    TypeError on a non-ASCII `str`. Without that encode a hostile token would be a 500 rather
    than a 403 -- the exact crash shape Category 1 existed to remove.

    Sent as raw latin-1 BYTES, which is what actually reaches a server on the wire: HTTP header
    values are latin-1, and an httpx/urllib client refuses to ascii-encode a non-ASCII `str`
    before it ever leaves the process. Passing a `str` here would test the client, not us.
    """
    r = api_client.get(
        "/cases?state=hold", headers={b"Authorization": "Bearer éüß".encode("latin-1")}
    )
    assert r.status_code == 403


def test_tokens_match_is_total_over_non_ascii():
    """The same property at the function level, past any client-side encoding limits."""
    from app.auth import _tokens_match

    assert _tokens_match("abc", "abc") is True
    assert _tokens_match("你好", "abc") is False
    assert _tokens_match("café", "cafe") is False
    assert _tokens_match("", "abc") is False


def test_error_response_never_echoes_the_expected_token(api_client):
    r = api_client.get("/cases?state=hold", headers={"Authorization": "Bearer wrong"})
    assert GOOD not in r.text


def test_unauthenticated_caller_cannot_distinguish_real_from_fake_case(api_client, make_case):
    """Auth must run BEFORE the DB lookup, or 401-vs-404 becomes a case-existence oracle for
    an unauthenticated attacker."""
    real = make_case()
    fake = uuid.uuid4()

    r_real = api_client.get(f"/cases/{real.id}")
    r_fake = api_client.get(f"/cases/{fake}")
    assert r_real.status_code == r_fake.status_code == 401
    assert r_real.json() == r_fake.json()

    # With a valid token the two DO differ -- proving the test above is detecting auth
    # ordering, not merely that both ids happened to be unreachable.
    h = {"Authorization": f"Bearer {GOOD}"}
    assert api_client.get(f"/cases/{real.id}", headers=h).status_code == 200
    assert api_client.get(f"/cases/{fake}", headers=h).status_code == 404


def test_health_stays_unauthenticated(api_client):
    """The one deliberately open route. Pinned so it is a decision, not an accident."""
    assert api_client.get("/health").status_code == 200
