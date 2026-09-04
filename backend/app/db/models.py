"""SQLAlchemy domain models (Checkpoint C6 / milestone M1).

Table shapes trace to docs/spec/mandate-drift-guard-architecture.md §5, extended by:
  - Decision 2 (docs/IMPLEMENTATION-BASELINE.md §7): transactions.state and
    cases.transaction_id — the triggering transaction is held explicitly, not just the case.
  - Decisions 4-7 (docs/IMPLEMENTATION-BASELINE.md, appended 2026-09-02).
  - Four traceability additions approved 2026-09-02, closing gaps in architecture §5's literal
    column lists where a gate_decision/evidence_packet/audit_event on the fail-closed or
    nominal-ALLOW path had no durable link back to the transaction it concerned:
      evidence_packets.transaction_id, gate_decisions.transaction_id,
      audit_events.mandate_id, audit_events.transaction_id.

Deliberately NOT done here (still [OPEN] per docs/IMPLEMENTATION-PLAN.md §S, out of C6 scope):
  - No CHECK constraint on semantic_assessments.risk_level's value set (Decision 6 — Pydantic
    layer's job, not the schema's).
  - No second, grant-restricted DB role / REVOKE for audit_events (Decision 7 — deferred; see
    the TODO in the migration that creates audit_events).
  - No evidence-engine signal formulas or thresholds — this file only shapes storage.
  - No numeric precision/scale on budget/amount/confidence — not specified anywhere in the
    source docs; left as unconstrained NUMERIC rather than guessing a scale.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    ARRAY,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Numeric,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class Mandate(Base):
    """A consumer-granted mandate. Write-once after creation: the system never modifies a
    mandate's purpose/budget/categories once issued (product-spec §204)."""

    __tablename__ = "mandates"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    purpose: Mapped[str] = mapped_column(Text, nullable=False)
    budget: Mapped[float] = mapped_column(Numeric, nullable=False)
    period_days: Mapped[int] = mapped_column(nullable=False)
    allowed_categories: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    transactions: Mapped[list["Transaction"]] = relationship(back_populates="mandate")
    evidence_packets: Mapped[list["EvidencePacket"]] = relationship(back_populates="mandate")
    cases: Mapped[list["Case"]] = relationship(back_populates="mandate")


TRANSACTION_STATES = ("allowed", "held", "blocked")
# "pending_evaluation" deliberately excluded — Decision 4: it is a transient, in-pipeline-only
# concept and is never written to Postgres. The row is inserted exactly once, already in its
# terminal-at-insert-time state (allowed or held).


class Transaction(Base):
    """One transaction attempt against a mandate. `state` is set at insert time (Decision 4)
    to `allowed` or `held`, and only ever moves held -> allowed/blocked thereafter (Ops
    resolution or timeout, wired in a later milestone — this table only shapes storage).

    Decision 8 (2026-09-02): `idempotency_key` uniqueness is scoped per mandate, not global —
    a global constraint risked two unrelated mandates' synthetic dataset cases colliding on
    the same key string during the locked test-set batch run, silently dropping a transaction
    and corrupting the pipeline-error-rate metric (eval-design §16, target 0)."""

    __tablename__ = "transactions"
    __table_args__ = (
        UniqueConstraint(
            "mandate_id", "idempotency_key", name="uq_transactions_mandate_id_idempotency_key"
        ),
        Index("ix_transactions_mandate_id_occurred_at", "mandate_id", "occurred_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    mandate_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("mandates.id", ondelete="RESTRICT"), nullable=False
    )
    merchant: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(Text, nullable=False)
    amount: Mapped[float] = mapped_column(Numeric, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[str] = mapped_column(
        Enum(*TRANSACTION_STATES, name="transaction_state"), nullable=False
    )

    mandate: Mapped["Mandate"] = relationship(back_populates="transactions")
    evidence_packets: Mapped[list["EvidencePacket"]] = relationship(back_populates="transaction")
    gate_decisions: Mapped[list["GateDecision"]] = relationship(back_populates="transaction")
    case: Mapped["Case | None"] = relationship(back_populates="transaction")
    audit_events: Mapped[list["AuditEvent"]] = relationship(back_populates="transaction")


class EvidencePacket(Base):
    """One row per *triggered* evaluation (a deterministic threshold was crossed) — most
    transactions never generate one. `transaction_id` closes the traceability gap in
    architecture §5's original column list (approved 2026-09-02)."""

    __tablename__ = "evidence_packets"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    mandate_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("mandates.id", ondelete="RESTRICT"), nullable=False
    )
    transaction_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("transactions.id", ondelete="RESTRICT"), nullable=False
    )
    signals: Mapped[dict] = mapped_column(JSONB, nullable=False)
    trajectory: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    mandate: Mapped["Mandate"] = relationship(back_populates="evidence_packets")
    transaction: Mapped["Transaction"] = relationship(back_populates="evidence_packets")
    semantic_assessment: Mapped["SemanticAssessment | None"] = relationship(
        back_populates="evidence_packet"
    )


MANDATE_ALIGNMENT_VALUES = ("low", "medium", "high")


class SemanticAssessment(Base):
    """The LLM's structured output for one evidence packet. Decision 5: a row here is written
    ONLY when the full response — including `confidence` — validates cleanly. Any missing,
    malformed, or out-of-range confidence is treated identically to any other malformed
    response: no row is written at all; the raw payload lives solely in audit_events.payload.

    `risk_level` is TEXT, not a native enum, per Decision 6 — its three-value set
    (low/medium/high) is enforced at the Pydantic layer only, kept cheaply changeable without
    a migration; no DB-level CHECK constraint is added for it in C6.
    """

    __tablename__ = "semantic_assessments"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    evidence_packet_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("evidence_packets.id", ondelete="RESTRICT"), nullable=False, unique=True
    )
    mandate_alignment: Mapped[str] = mapped_column(
        Enum(*MANDATE_ALIGNMENT_VALUES, name="mandate_alignment"), nullable=False
    )
    risk_level: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float] = mapped_column(Numeric, nullable=False)
    evidence: Mapped[list] = mapped_column(JSONB, nullable=False)
    raw_response: Mapped[dict] = mapped_column(JSONB, nullable=False)
    model_version: Mapped[str] = mapped_column(Text, nullable=False)
    prompt_version: Mapped[str] = mapped_column(Text, nullable=False)
    latency_ms: Mapped[int] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    evidence_packet: Mapped["EvidencePacket"] = relationship(back_populates="semantic_assessment")
    gate_decision: Mapped["GateDecision | None"] = relationship(back_populates="semantic_assessment")


GATE_DECISION_VALUES = ("allow", "hold")
# No "block" — BLOCK is never a direct gate output, only reachable via case resolution/timeout
# (architecture §5 exact parenthetical: "decision (allow/hold)"; §6 state machine).


class GateDecision(Base):
    """The Policy Gate's ALLOW/HOLD output. `semantic_assessment_id` is nullable on the
    fail-closed path (timeout/malformed output — no semantic_assessments row exists there,
    per Decision 5). `transaction_id` is NOT NULL and independent of that FK specifically so
    every gate decision remains traceable to its transaction even when the LLM leg failed
    entirely (approved 2026-09-02 traceability addition)."""

    __tablename__ = "gate_decisions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    semantic_assessment_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("semantic_assessments.id", ondelete="RESTRICT"), nullable=True
    )
    transaction_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("transactions.id", ondelete="RESTRICT"), nullable=False
    )
    decision: Mapped[str] = mapped_column(
        Enum(*GATE_DECISION_VALUES, name="gate_decision_value"), nullable=False
    )
    rule_version: Mapped[str] = mapped_column(Text, nullable=False)
    rule_applied: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    semantic_assessment: Mapped["SemanticAssessment | None"] = relationship(
        back_populates="gate_decision"
    )
    transaction: Mapped["Transaction"] = relationship(back_populates="gate_decisions")
    case: Mapped["Case | None"] = relationship(back_populates="gate_decision")


CASE_STATES = ("hold", "resolved_allow", "resolved_block")
# Exact literal values per architecture §5: "state (hold / resolved_allow / resolved_block)".


class Case(Base):
    """Read-optimized current-state view. Only rows that reached HOLD or later get a case —
    a nominal ALLOW transaction never has one. `transaction_id` is Decision 2's required
    addition: HOLD applies to the specific triggering transaction, not just the mandate.

    `gate_decision_id` is NULLABLE as of Decision 20 (migration c4f1b7e2d9a3) — and ONLY for
    the one path that decision creates: the fail-closed backstop in
    `domain.pipeline.run_pipeline`, which opens a case when the pipeline throws an unforeseen
    exception. The gate is never reached on that path, so no `gate_decisions` row exists to
    point at, and writing one would record a decision that was never made (Decision 5 applies
    the same reasoning one layer up, to `semantic_assessments`). Every other path still writes
    a gate decision first and links it here; that is now an application-level invariant rather
    than a schema-level one."""

    __tablename__ = "cases"
    __table_args__ = (
        UniqueConstraint("transaction_id", name="uq_cases_transaction_id"),
        Index("ix_cases_state", "state"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    mandate_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("mandates.id", ondelete="RESTRICT"), nullable=False
    )
    transaction_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("transactions.id", ondelete="RESTRICT"), nullable=False
    )
    gate_decision_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("gate_decisions.id", ondelete="RESTRICT"), nullable=True, unique=True
    )
    state: Mapped[str] = mapped_column(Enum(*CASE_STATES, name="case_state"), nullable=False)
    opened_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_by: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolution_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    mandate: Mapped["Mandate"] = relationship(back_populates="cases")
    transaction: Mapped["Transaction"] = relationship(back_populates="case")
    gate_decision: Mapped["GateDecision | None"] = relationship(back_populates="case")
    audit_events: Mapped[list["AuditEvent"]] = relationship(back_populates="case")


class AuditEvent(Base):
    """Append-only log — every stage's output, one row per stage per case (or per nominal
    pipeline pass, for transactions that never open a case). `mandate_id` is NOT NULL and
    `transaction_id` is nullable (a rare pre-persistence failure could precede even the
    transaction row existing) — both approved 2026-09-02 to close the same traceability gap
    as evidence_packets/gate_decisions above, since `case_id` alone is null on the nominal-
    ALLOW path where no case is ever opened.

    DB-level append-only enforcement (INSERT/SELECT-only grant, no UPDATE/DELETE) is
    Decision 7: explicitly DEFERRED past C6. See the TODO in the migration that creates this
    table — no second DB role or REVOKE statement exists yet.
    """

    __tablename__ = "audit_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    case_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("cases.id", ondelete="RESTRICT"), nullable=True
    )
    mandate_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("mandates.id", ondelete="RESTRICT"), nullable=False
    )
    transaction_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("transactions.id", ondelete="RESTRICT"), nullable=True
    )
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    case: Mapped["Case | None"] = relationship(back_populates="audit_events")
    mandate: Mapped["Mandate"] = relationship()
    transaction: Mapped["Transaction | None"] = relationship(back_populates="audit_events")


DATASET_SPLIT_VALUES = ("dev", "test")
DATASET_CATEGORY_VALUES = ("legitimate", "drift", "ambiguous")
DATASET_DRIFT_TYPE_VALUES = ("fast_spike", "slow_drift", "n_a")


class DatasetCase(Base):
    """Evaluation-harness fixture metadata (milestone M5 population; the table itself ships
    with the rest of the M1 schema). Independent of the live pipeline tables above."""

    __tablename__ = "dataset_cases"
    __table_args__ = (
        CheckConstraint(
            "paired_with_id IS NULL OR paired_with_id != id", name="ck_dataset_cases_no_self_pair"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    split: Mapped[str] = mapped_column(Enum(*DATASET_SPLIT_VALUES, name="dataset_split"), nullable=False)
    category: Mapped[str] = mapped_column(
        Enum(*DATASET_CATEGORY_VALUES, name="dataset_category"), nullable=False
    )
    drift_type: Mapped[str] = mapped_column(
        Enum(*DATASET_DRIFT_TYPE_VALUES, name="dataset_drift_type"), nullable=False
    )
    paired_with_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("dataset_cases.id", ondelete="RESTRICT"), nullable=True
    )
    ground_truth_label: Mapped[str] = mapped_column(Text, nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    fixture_path: Mapped[str] = mapped_column(Text, nullable=False)
