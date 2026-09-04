"""Application configuration.

Fields belonging to later milestones — the resolve-endpoint bearer token (M4) — are still NOT
defined here. Adding a placeholder value for it now would mean inventing an answer to
decisions docs/IMPLEMENTATION-BASELINE.md §15 and docs/IMPLEMENTATION-PLAN.md §S explicitly
leave open (disagreement-handling rule, ingestion auth, etc.). It will be added in the
milestone that actually needs it.

`EvidenceEngineThresholds` (Checkpoint C7+C8), `SemanticRiskClientConfig` (Checkpoint C9), and
`GatePolicyConfig` (Checkpoint C10) are the three pieces of pipeline config that ARE defined
now — Decisions 9-11, 13-14, and 15 (docs/IMPLEMENTATION-BASELINE.md, 2026-09-02) lock their
exact values, and architecture's own framing of thresholds as a versioned config object (not
hardcoded) applies to all three.
"""

from pathlib import Path

from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict

# Anchored to this file's own location (backend/app/config.py -> backend/.env), NOT left as a
# bare relative ".env" -- pydantic-settings resolves a relative env_file against the invoking
# process's current working directory (confirmed 2026-09-04 by reading
# DotEnvSettingsSource._read_env_files: `Path(env_file).expanduser()` then `.is_file()`, with
# no fallback or warning if that check fails -- Settings() just silently falls through to
# field defaults). A script invoked from any directory other than backend/ (the project's own
# convention, but not something Python enforces) would load no .env at all and get
# `anthropic_api_key=None` with no error until the first real API call -- exactly what
# happened during Checkpoint C13's first, void locked-test-set attempt (see
# eval/calibration_log.md's "ATTEMPT 1 (VOID)" section). Resolving here, once, removes the
# cwd dependency for every future invocation regardless of what directory it's run from.
_ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=_ENV_FILE, env_file_encoding="utf-8", extra="ignore")

    app_env: str = "development"
    log_level: str = "INFO"

    # Default matches docker-compose.yml's `db` service. Override via .env for local (non-Docker)
    # Postgres, or via the DATABASE_URL environment variable directly.
    database_url: str = "postgresql+psycopg2://mandate_guard:mandate_guard@localhost:5432/mandate_guard"

    # Decision 13 (2026-09-02): exact model string, pinned in config, never resolved at
    # request time (a bare "latest" alias would break run-over-run reproducibility for the
    # locked-test-set protocol, eval-design's core methodology).
    llm_model: str = "claude-sonnet-5"

    # Read from the ANTHROPIC_API_KEY environment variable (.env, gitignored). None by
    # default so importing this module never requires the key to be set -- only actually
    # calling the semantic risk client does. Never logged, printed, or otherwise written out
    # anywhere by this codebase.
    anthropic_api_key: str | None = None

    # Decision 17 (2026-09-02, docs/IMPLEMENTATION-BASELINE.md §22): the single shared static
    # bearer-token secret (architecture's already-locked single-bearer-token mechanism -- no
    # per-user auth, no user table) now gates BOTH POST /transactions and
    # POST /cases/{id}/resolve, not just resolution. Read from the API_BEARER_TOKEN
    # environment variable (.env, gitignored); None by default so importing this module never
    # requires it -- only `app.auth.require_bearer_token` actually checks it, and treats an
    # unconfigured token as "auth can never succeed" (fail-closed), not "auth is skipped".
    # This is a credential, not a tunable threshold, so it lives here in `Settings` (the
    # existing pattern for env-sourced secrets, matching `anthropic_api_key` exactly) rather
    # than as a separate versioned `BaseModel` like `EvidenceEngineThresholds` and friends --
    # there is no "rule_version" concept for a static secret. Never logged, printed, or
    # otherwise written out anywhere by this codebase.
    api_bearer_token: str | None = None


settings = Settings()


class EvidenceEngineThresholds(BaseModel):
    """Versioned band cutoffs for the three deterministic signals (Decisions 9-11). Read by
    `app.domain.evidence_engine.*`'s signal functions as a `config` keyword argument, never
    hardcoded inline — so dev-set calibration (a later milestone) can tune these via a config
    edit, not a code edit.
    """

    version: str = "v1"

    # Decision 9 — spend velocity: ratio = actual_fraction / expected_fraction.
    velocity_normal_max: float = 1.3
    velocity_elevated_max: float = 2.0

    # Decision 10 — category shift: out-of-mandate spend / total spend in the window.
    category_shift_none_max: float = 0.05
    category_shift_minor_max: float = 0.20
    category_shift_significant_max: float = 0.45

    # Decision 11 — clustering: max 24h-sub-window transaction count / total count.
    clustering_normal_max: float = 0.4
    clustering_clustered_max: float = 0.7


EVIDENCE_ENGINE_THRESHOLDS = EvidenceEngineThresholds()


class SemanticRiskClientConfig(BaseModel):
    """Versioned config for layer ② (Checkpoint C9). Read by
    `app.domain.semantic_risk_client.assess` as a `config` keyword argument, never hardcoded
    inline — mirrors `EvidenceEngineThresholds`'s pattern.
    """

    version: str = "v1"

    # [IMPL DETAIL] architecture's own proposed starting value, tunable via config edit.
    timeout_seconds: float = 10.0

    # Decision 14 (2026-09-02): exactly one transport-level retry (connection errors / 5xx
    # only) before falling to status="transport_error". No retry on malformed/schema-invalid
    # output, and none on timeout either -- both are their own distinct failure statuses.
    transport_retry_attempts: int = 1


SEMANTIC_RISK_CLIENT_CONFIG = SemanticRiskClientConfig()


class GatePolicyConfig(BaseModel):
    """Versioned config for layer ③ (Checkpoint C10). Read by
    `app.domain.policy_gate.decide` as a `config` keyword argument, never hardcoded inline —
    mirrors `EvidenceEngineThresholds`/`SemanticRiskClientConfig`'s pattern. `rule_version` is
    what `gate_decisions.rule_version` records per decision (architecture §5/§9), so every
    gate decision is traceable to the exact rule set that produced it.
    """

    rule_version: str = "v1"

    # Decision 15 (2026-09-02): the minimum self-reported confidence for a "low" risk_level to
    # be eligible for a bounded downgrade to ALLOW. Below this floor, HOLD regardless of the
    # other two conditions.
    confidence_floor: float = 0.7


GATE_POLICY_CONFIG = GatePolicyConfig()


class IngestionConfig(BaseModel):
    """Versioned config for ingestion-boundary input validation (red-team Category 1,
    2026-09-04), mirroring the `EvidenceEngineThresholds`/`GatePolicyConfig` pattern rather
    than hardcoding a constant inside the API module.
    """

    version: str = "v1"

    # Red-team finding RT-C1-001: `occurred_at` is attacker-controlled and feeds
    # compute_velocity's `as_of = max(t.occurred_at ...)`, which sets
    # `expected_fraction = days_elapsed / period_days`. A future-dated transaction inflates
    # expected_fraction without bound, driving the velocity ratio toward zero and reading
    # "normal" no matter how large the spend -- a silent ALLOW. Transactions are reports of
    # spend that has ALREADY happened, so anything beyond a small clock-skew allowance is
    # refused. Deliberately small: this is skew tolerance, not a business window.
    max_future_skew_minutes: float = 5.0


INGESTION_CONFIG = IngestionConfig()


class HoldResolutionConfig(BaseModel):
    """Versioned config for HOLD-timeout handling (Checkpoint C11). Read by
    `app.domain.pipeline.check_and_apply_timeout` as a `config` keyword argument, mirroring
    the `EvidenceEngineThresholds`/`SemanticRiskClientConfig`/`GatePolicyConfig` pattern.
    """

    version: str = "v1"

    # Decision 18 (2026-09-02): checked lazily on read (no background job/scheduler, per the
    # already-locked no-queue architecture), not enforced at any fixed wall-clock moment.
    # Explicitly an arbitrary starting default, not a deeply-deliberated product decision.
    timeout_window_hours: float = 24.0


HOLD_RESOLUTION_CONFIG = HoldResolutionConfig()
