"""Application configuration (Checkpoint C5 / milestone M0 scope only).

Intentionally minimal. Fields belonging to later milestones — the pinned LLM model string
(M3), policy-gate threshold/version config (M2), and the resolve-endpoint bearer token (M4) —
are NOT defined here yet. Adding placeholder values for them now would mean inventing answers
to decisions docs/IMPLEMENTATION-BASELINE.md §15 and docs/IMPLEMENTATION-PLAN.md §S explicitly
leave open (disagreement-handling rule, retry policy, ingestion auth, signal thresholds, etc.).
They will be added in the milestone that actually needs them.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = "development"
    log_level: str = "INFO"

    # Default matches docker-compose.yml's `db` service. Override via .env for local (non-Docker)
    # Postgres, or via the DATABASE_URL environment variable directly.
    database_url: str = "postgresql+psycopg2://mandate_guard:mandate_guard@localhost:5432/mandate_guard"


settings = Settings()
