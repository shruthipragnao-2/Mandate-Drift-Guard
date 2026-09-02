"""Pydantic schema for the forced tool-call output of layer ② (Checkpoint C9).

Matches the locked output fields (docs/IMPLEMENTATION-BASELINE.md §5): mandate_alignment,
risk_level, confidence, evidence[]. `risk_level`'s three-value set is Decision 6 -- value-set
validation lives HERE, at the Pydantic layer, not as a DB-level CHECK constraint
(`semantic_assessments.risk_level` is plain TEXT in backend/app/db/models.py, deliberately
left unconstrained at the schema layer for exactly this reason).

Any Pydantic `ValidationError` constructing this model (missing field, wrong type, an
out-of-range confidence, an unrecognized mandate_alignment/risk_level value, or an
unexpected extra field) is treated identically to a malformed response by
`app.domain.semantic_risk_client` -- no repair, no best-effort parsing (baseline §5/§11).
`extra="forbid"` operationalizes baseline §5's "nothing beyond the four fields above" as a
schema-level check, not just a prose description.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class LlmOutput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    mandate_alignment: Literal["low", "medium", "high"]
    risk_level: Literal["low", "medium", "high"]
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: list[str]
