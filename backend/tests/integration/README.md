# Integration tests

Full pipeline tests against a mocked LLM client and a real database — including the
failure-injection fixtures (timeout, malformed output, low-confidence-high-risk, adversarial
text, cold-start). These land in milestone M2 per `docs/IMPLEMENTATION-PLAN.md` §Q/§M and do
not exist yet.

Checkpoint C6 / milestone M1 populated this directory with schema-only integration tests —
migration apply/rollback, NOT NULL/unique/FK/enum/ON DELETE RESTRICT constraint checks, and
model-construction/round-trip tests — run against the real Postgres service, no mocking. No
evidence-engine, LLM, or gate logic exists yet, so none of that is exercised here.

All tests in this directory assume Postgres is reachable at `DATABASE_URL` (see
`backend/README.md`) and require `alembic upgrade head` to have been run at least once —
`conftest.py`'s session-scoped autouse fixture does this automatically.
