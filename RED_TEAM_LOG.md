# Red-Team Log

Adversarial pass against the completed system (backend + frontend, both live), run against a
real running server and a real Postgres — not unit tests, not reasoning about the code. Every
finding below was reproduced live before being recorded, and every fix was re-verified live
after.

Severity scale used throughout:

| Severity | Meaning |
|---|---|
| **CRITICAL** | Violates a locked safety property (baseline §6's fail-closed invariant, or Decisions 3/5/14/15). Fixed immediately, committed individually. |
| **MODERATE** | Real bug, not a safety violation. Fixed only if time allows. |
| **COSMETIC** | Logged, not fixed. |

A deliberate distinction is drawn throughout between **fail-OPEN** (something that should
HOLD reached ALLOW — the actual danger) and **crash** (an unhandled exception surfacing as
HTTP 500). Both violate baseline §6's letter, which locks that *"any of {LLM timeout,
malformed/non-schema output, low confidence, unhandled pipeline exception} routes to HOLD,
never to silent ALLOW"*. Only one of the findings below was a true fail-open.

---

## Category 1 — Fail-closed integrity (COMPLETE, 2026-09-04)

### Summary

| ID | Finding | Severity | Status |
|---|---|---|---|
| RT-C1-001 | Future-dated `occurred_at` neutralises velocity → **silent ALLOW** | CRITICAL | Fixed `27a331d` |
| RT-C1-002 | 2nd transaction per mandate crashed (Decimal/float) | CRITICAL | Fixed `03600ae` |
| RT-C1-003 | Naive (tz-less) `occurred_at` crashed | CRITICAL | Fixed `e7f5b30` |
| RT-C1-004 | `Infinity` amount crashed | CRITICAL | Fixed `171ce63` |
| RT-C1-005 | `NaN` amount crashed | CRITICAL | Fixed `171ce63` |
| RT-C1-006 | NUL byte in merchant/category crashed | CRITICAL | Fixed `171ce63` |
| RT-C1-007 | The validation-error handler itself crashed on non-serializable input | CRITICAL | Fixed `171ce63` |
| RT-C1-008 | **No fail-closed exception boundary exists at all** | CRITICAL | **OPEN — needs human decision** |
| RT-C1-009 | Concurrent same-key crossing requests → 500 instead of idempotent replay | MODERATE | Fixed `49d24f0` |
| RT-C1-010 | No length cap on `merchant` / `category` | COSMETIC | Open |

---

### RT-C1-001 — Future-dated `occurred_at` produces a silent ALLOW — CRITICAL — FIXED

**The only true fail-open found.** `occurred_at` is attacker-controlled and flows into
`compute_velocity`'s `as_of = max(t.occurred_at for t in window)`, which sets
`days_elapsed = max(1, (as_of - mandate.created_at).days)` and
`expected_fraction = days_elapsed / period_days`. Future-dating inflates `expected_fraction`
without bound, so `ratio = actual_fraction / expected_fraction` collapses toward zero and the
velocity band reads `"normal"` no matter how large the spend.

For an **in-mandate** category (so `category_shift` reads `"none"`) on an unclustered day, all
three signals then read benign simultaneously. The threshold is never crossed, `decide()` is
never called, and the transaction takes `_persist_nominal_allow`: no LLM call, no evidence
packet, no case, no review.

Reproduced live — identical 7500 spend against an 8000-budget / 7-day mandate:

```
honest timestamp        -> state=held,    gate=hold, case opened
occurred_at 9999-01-01  -> state=allowed, gate=None, no case
occurred_at +1 year     -> state=allowed, gate=None, no case
```

It does not require an absurd date; **one year ahead is enough**. The effect also *persists*:
because `as_of` is a max over the whole window, a single future-dated row suppresses velocity
for every later evaluation of that mandate — one poisoned transaction permanently blinds one
signal.

**Fix** (`27a331d`): refused at the ingestion boundary, with the skew allowance in a new
versioned `IngestionConfig.max_future_skew_minutes` (5.0) rather than hardcoded.

**Why not fixed in the evidence engine.** Two reasons, both deliberate:
1. The engine is a pure function and takes no clock. Injecting wall-clock would make signal
   computation non-deterministic and break the reproducibility the eval harness and the
   **locked C13 test-set numbers** depend on.
2. The engine's unbounded `expected_fraction` growth is the *known* period-renewal gap,
   already flagged `[OPEN]` in baseline §18, carrying its own `TODO` in `velocity.py`, and
   deliberately scoped out by **Decision 16** (a generator-side constraint on dataset cases).
   Resolving that is a human decision, not one to take unilaterally mid-red-team.

What was genuinely unguarded — and what this fix closes — is that **the live API accepted
arbitrary timestamps at all**. Decision 16 bounded the *dataset* to a single period window;
nothing bounded live input.

**Residual risk, stated honestly:** the underlying engine behaviour is unchanged. Any future
caller that bypasses the API (a new ingestion path, a batch importer) reintroduces this
exposure. The real resolution is the period-renewal decision, still `[OPEN]`.

---

### RT-C1-002 — Second transaction against any mandate crashed — CRITICAL — FIXED

```
TypeError: unsupported operand type(s) for +: 'decimal.Decimal' and 'float'
  at compute_velocity -> sum(t.amount for t in transactions_in_window)
```

History loaded back from Postgres carries `decimal.Decimal` amounts (NUMERIC column); the
incoming transaction carries a `float`. `run_pipeline` put both in one window and summing it
raised. The first transaction against a mandate has empty history, so nothing to mix.

**This broke the system for all realistic use** — two honest 100/120-rupee in-mandate
transactions were enough:

```
txn 1 (amount=100): HTTP 200
txn 2 (amount=120): HTTP 500
```

**Why it was never caught:** the frontend verification submitted exactly one transaction per
mandate, and the eval harness passes freshly-flushed ORM rows whose `amount` is still the
assigned Python float, never round-tripped through the DB. Every existing test passed history
as `[]` or as flushed-but-not-reloaded rows. A latent whole-system outage sitting behind a
green 151-test suite.

**Fix** (`03600ae`): normalised at the boundary that creates the mismatch, not in the engine —
`TransactionLike` already declares `amount: float`, so passing Decimals violated its stated
contract. `_WindowTransaction.of()` now normalises history too. Regression test round-trips
through Postgres via `expire_all()` and asserts the reloaded amount really *is* `Decimal`, so
the test cannot silently stop testing anything; verified to fail without the fix.

---

### RT-C1-003 — Naive `occurred_at` crashed — CRITICAL — FIXED

`"2026-09-02T10:00:00"` (no offset — an entirely ordinary ISO spelling) reached
`compute_velocity` and raised `TypeError: can't subtract offset-naive and offset-aware
datetimes` against the mandate's tz-aware `created_at`. HTTP 500, nothing persisted.

**Fix** (`e7f5b30`): 400 at the boundary with an actionable message, rather than silently
assuming UTC. Guessing an offset would shift a transaction by hours and can change its
clustering band, and silent repair of ambiguous input is what this project refuses elsewhere
(Decision 3: *"not repaired, not defaulted, not best-effort parsed"*). Explicit non-UTC
offsets (`+05:30`) still accepted, pinned by its own test.

**Noted design choice:** assume-UTC was the alternative. Rejecting is stricter than most APIs;
flip it if demo ergonomics matter more than strictness.

---

### RT-C1-004 / 005 / 006 — Unrepresentable values crashed — CRITICAL — FIXED

| Input | Exception |
|---|---|
| `Infinity` amount | `psycopg2.errors.InvalidTextRepresentation: invalid input syntax for type json` |
| `NaN` amount | `ValueError: Out of range float values are not JSON compliant: nan` |
| NUL (`0x00`) in merchant/category | `ValueError: A string literal cannot contain NUL (0x00) characters` |

JSON's spec has no `Infinity`/`NaN`, but Python's `json` module emits and accepts both, so a
client can send them. `Field(gt=0)` stops neither — `inf > 0` is True, and NaN slips through
pydantic's float handling.

**Fix** (`171ce63`): refused at the ingestion boundary. NUL is rejected rather than stripped —
quietly rewriting a merchant name would corrupt the audit record the system exists to produce.
**Only** NUL is rejected; emoji, RTL marks and combining characters are legitimate in real
merchant names, store fine, and are pinned as still-accepted by a test.

---

### RT-C1-007 — The validation-error handler itself crashed — CRITICAL — FIXED

Found only because the RT-C1-004 fix *didn't work*: with the amount validator in place and
firing correctly, `Infinity`/`NaN` **still** returned 500.

`main.py`'s `RequestValidationError` handler did `jsonable_encoder(exc.errors())`, and each
error entry echoes the rejected value back under `input`. `jsonable_encoder` cannot serialize
a non-finite float — so the handler raised *while reporting* a correctly-detected 400.

No amount of upstream validation could fix this; the **reporting path** was the bug. It would
have turned *any* validation failure carrying a non-serializable input into a 500.

**Fix** (`171ce63`): `input` is dropped rather than coerced. It is attacker-controlled content
being reflected straight back into a response, `loc`/`msg` already identify the bad field, and
echoing it was the only reason the handler could fail. `ctx` is stringified (it can hold a
live exception object).

---

### RT-C1-008 — No fail-closed exception boundary exists — CRITICAL — **OPEN, NEEDS DECISION**

**This is the systemic root cause behind RT-C1-002 through 006, and the most important
unresolved item in this log.**

Baseline §6 locks, as a *hard, non-tunable* invariant:

> any of {LLM timeout, malformed/non-schema output, low confidence, **unhandled pipeline
> exception**} routes to HOLD, never to silent ALLOW.

The first three are implemented, in `policy_gate.decide()`. **The fourth is not implemented
anywhere.** There is no `try`/`except` around the pipeline in `run_pipeline` or in
`api/transactions.py`. An unhandled exception propagates to FastAPI and becomes a 500 with
**nothing persisted at all** — no transaction row, no case, no audit event.

Every crash in this log is a symptom of that single missing guarantee. The individual input
fixes above close the *known* inputs; they do not restore the invariant. Anything unforeseen —
an Anthropic 4xx/429 (Decision 14's scope note explicitly says a 4xx "propagates uncaught"), a
transient DB error, a future code change — still produces a 500 with no audit record, which
also breaches eval-design §15's 100%-audit-completeness target.

Mitigating context, stated fairly: a 500 is **not** a fail-open. No authorization is granted
and no money moves. The practical danger is lower than RT-C1-001. But it is a locked invariant
that is currently unimplemented, and the audit trail is silently incomplete when it fires.

**Not fixed here deliberately.** Implementing it means persisting a `held` transaction + case
+ audit event when the pipeline throws — a change to what rows exist after a failure, i.e. a
change to the system's persisted-state contract. That warrants a numbered Decision and human
sign-off, not a unilateral 2 a.m. patch during a red-team pass. Recommended as the first item
for the next session.

---

### RT-C1-009 — Concurrent same-key submissions return 500 — MODERATE — FIXED

Four concurrent identical threshold-crossing requests sharing one idempotency key:

```
#1 HTTP 500   #2 HTTP 500   #3 HTTP 200 (state=held)   #4 HTTP 500
-> transactions persisted for that key: 1     cases: 1
```

Cause: the read-then-insert in `create_transaction` is not atomic, so losers of the race hit
`uq_transactions_mandate_id_idempotency_key` and raise `IntegrityError` uncaught.

**Data integrity held perfectly** — exactly one transaction and one case, no duplicate
evaluation, no double-charge, no silent allow. This is why it is MODERATE and not CRITICAL:
the idempotency guarantee (Decision 8) is intact; only the *reporting* is wrong. A client that
retries after the 500 gets the correct `held` result.

The non-crossing equivalent (6 concurrent, no case opened) returned **200 six times with
exactly 1 row persisted** — correct.

**Fix:** `IntegrityError` is now caught around `run_pipeline`; the session is rolled back and
the key re-read. If a row is now present the race was lost, and the request returns the same
replay a serial duplicate gets — because the winner has already persisted the identical
request. If **no** matching row is found, the error was some *other* constraint failure and is
re-raised rather than masked: quietly reporting success for an unrelated integrity error would
be exactly the silent repair this project refuses elsewhere. Both directions are pinned by
tests, the race simulated deterministically rather than with threads.

Re-verified live with the same 4-way concurrent probe that produced the 3×500:

```
#1..#4: HTTP 200, state=held, all four returning the SAME transaction id
DB: transactions for key=1, cases=1
```

**Related observation, not fixed:** each racing request makes its own real Anthropic call
before losing at commit, since `assess()` runs before persistence. Four concurrent duplicates
cost four LLM calls to produce one case. Harmless to correctness and to the locked metrics,
but it is real spend, and worth knowing before any load demo.

---

### RT-C1-010 — No length cap on `merchant` / `category` — COSMETIC — open

100,000-character merchant and category strings are accepted and stored (TEXT columns, no
constraint). No crash, no safety impact. Relevant only to frontend layout — deferred to the
Category 4 pass.

---

## Verified strengths (probed, held up)

Recorded because "we tried to break this and could not" is worth as much as a finding, and
these were actively attacked, not assumed:

- **Banding is fail-closed by construction.** Every band function is
  `if ratio <= safe_max: return "<safer band>"`, and *every* comparison against NaN is False,
  so a NaN ratio falls through to the most severe band. Confirmed directly:

  | signal | NaN → | inf → |
  |---|---|---|
  | velocity | `critical` | `critical` |
  | category_shift | `severe` | `severe` |
  | clustering | `highly_clustered` | `highly_clustered` |

- **Exact-boundary values sit in the safer band, consistently.** At `velocity=1.3`,
  `category_shift=0.05/0.20/0.45`, `clustering=0.4/0.7`, the boundary itself reads as the
  *lower* band and one epsilon above flips it. This is the defined semantics of Decisions
  9–11's band maxima, applied uniformly — **surprising-looking but correct, not a finding**. A
  transaction landing simultaneously on all three safe maxima is ALLOWed by design.

- **Already-resolved cases behave correctly.** Resubmitting a resolved transaction's
  idempotency key returns the **current** state (`blocked`) rather than a stale `held`, does
  not re-run the pipeline, and creates no second row. Re-resolving returns 409. DB confirmed:
  1 transaction, 1 case, states consistent.

- **Same key + different payload → 409**, per Decision 8 / Plan §E.

- **Input shape validation is correct**: missing field → 400, wrong type → 400, malformed UUID
  → 400, unknown mandate → 404.

- **Extreme but representable values are handled**: `1e308`, subnormal `1e-320`, 100k-char
  strings, emoji/RTL/combining marks — all processed without crashing, reaching HOLD or ALLOW
  per the actual signal readings.

---

## Not yet run

Categories 2 (auth/injection boundaries), 3 (frontend/API contract integrity) and 4
(demo-killers) were deliberately deferred to a fresh session. Nothing in this pass should be
read as evidence about them.
