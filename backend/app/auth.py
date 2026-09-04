"""Bearer-token authentication (Checkpoint C12, Decision 17).

A single shared static secret, per architecture's already-locked single-bearer-token
mechanism -- no per-user auth, no user table exists (baseline §12: "Authorization: no RBAC --
a single Ops-analyst role is assumed, named openly ... as a demo-scale simplification").
Decision 17 (docs/IMPLEMENTATION-BASELINE.md §22) extends this token's scope from
HOLD-resolution only to also cover ingestion (`POST /transactions`).

The token value is read from `app.config.settings.api_bearer_token` (environment-sourced,
`.env`, gitignored) -- never hardcoded, never logged, never included in any exception detail.

Status code convention (this checkpoint's own instruction: "401/403 on missing/invalid bearer
token"): no `Authorization` header at all -> 401 ("who are you"); a header present but the
wrong token -> 403 ("I know who's asking, and it's not authorized").
"""

from __future__ import annotations

import secrets

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import settings

_bearer_scheme = HTTPBearer(auto_error=False)


def require_bearer_token(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> None:
    if settings.api_bearer_token is None:
        # Fail closed: an unconfigured token means auth can never succeed, not that it's
        # silently skipped -- consistent with the fail-closed philosophy applied everywhere
        # else in this project (baseline §6).
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="bearer token not configured")
    if credentials is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="missing bearer token")
    if not _tokens_match(credentials.credentials, settings.api_bearer_token):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="invalid bearer token")


def _tokens_match(presented: str, expected: str) -> bool:
    """Red-team finding RT-C2-002. The comparison this replaces was `!=` on `str`, which
    short-circuits at the first differing byte, so how long a rejection takes depends on how
    many leading characters the presented token got right -- the classic side channel that
    lets a secret be recovered one character at a time rather than by brute force.

    Stated honestly about severity, because the log should not overclaim: this was NOT shown
    to be exploitable here. Measured over loopback, a first-character-wrong token and a
    last-character-wrong token differed by 0.006 ms against a ~0.88 ms baseline -- the signal
    is far below ASGI and Postgres scheduling noise, and the token is a static demo secret, not
    a per-user credential. It is fixed because the fix is one line with no behavioural
    downside, not because an attack was demonstrated.

    Encoded to bytes rather than passed as `str`: `secrets.compare_digest` on `str` raises
    TypeError if either side contains a non-ASCII character, and `credentials` is
    attacker-controlled, so comparing strings directly would turn a hostile token into a 500
    instead of a 403 -- reintroducing exactly the shape of crash Category 1 spent its time
    removing. UTF-8 both sides and the comparison is total.

    Note what is deliberately NOT hidden: token LENGTH still leaks, because compare_digest
    returns False immediately for unequal-length inputs. That is inherent to the primitive and
    not worth working around for a static shared secret.
    """
    return secrets.compare_digest(presented.encode("utf-8"), expected.encode("utf-8"))
