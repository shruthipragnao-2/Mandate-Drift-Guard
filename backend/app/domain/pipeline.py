"""Pipeline orchestration (Checkpoint C11: full orchestrator, per docs/IMPLEMENTATION-PLAN.md
§D, extending the Checkpoint C7+C8 threshold-check-only scope).

`check_threshold` (unchanged since C7+C8) answers Plan §D step 3's question in isolation:
given the three computed signal results, does this transaction's evaluation cross the
deterministic threshold that requires building an evidence packet and invoking layer ②?

`run_pipeline` is the full orchestrator (Plan §D steps 1-8): computes all three signals, calls
`check_threshold`, and -- if crossed -- builds the evidence packet, calls the (unmodified)
semantic risk client and policy gate, then persists every resulting row in ONE atomic DB
transaction (a single `session.commit()`), per Plan §D step 7/9's atomicity requirement.
`domain/evidence_engine/*.py`, `semantic_risk_client.py`, and `policy_gate.py` are called here,
never rewritten -- this module owns orchestration and persistence only.

`resolve_hold` and `check_and_apply_timeout` implement Plan §J's HOLD-resolution state
machine (confirm/deny/timeout), built now for C12's future `/cases/{id}/resolve` endpoint to
call, per Decision 18's lazy-timeout-on-read design (docs/IMPLEMENTATION-BASELINE.md §22): no
background job or scheduler -- a case is only checked for timeout when something reads it.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal, Sequence

import anthropic
from sqlalchemy.orm import Session

from app.config import (
    EVIDENCE_ENGINE_THRESHOLDS,
    GATE_POLICY_CONFIG,
    HOLD_RESOLUTION_CONFIG,
    SEMANTIC_RISK_CLIENT_CONFIG,
    EvidenceEngineThresholds,
    GatePolicyConfig,
    HoldResolutionConfig,
    Settings,
    SemanticRiskClientConfig,
    settings,
)
from app.db import models
from app.domain.evidence_engine.category_shift import CategoryShiftResult, compute_category_shift
from app.domain.evidence_engine.clustering import ClusteringResult, compute_clustering
from app.domain.evidence_engine.packet_builder import build_evidence_packet
from app.domain.evidence_engine.types import MandateLike, TransactionLike
from app.domain.evidence_engine.velocity import VelocityResult, compute_velocity
from app.domain.semantic_risk_client import assess


@dataclass(frozen=True)
class ThresholdCheckResult:
    crossed: bool
    triggering_signals: tuple[str, ...]


def check_threshold(
    velocity_result: VelocityResult,
    category_shift_result: CategoryShiftResult,
    clustering_result: ClusteringResult,
) -> ThresholdCheckResult:
    """[INFERRED -- not settled by Decisions 9-11 or any prior spec document]: those decisions
    fix each signal's own banding, but not a cross-signal trigger rule for "does this warrant
    a second look at all". This implements the simplest, most conservative rule available: ANY
    signal reading above its own lowest ("no drift") band is enough to cross the threshold --
    velocity != "normal", category_shift != "none", or clustering != "normal". No weighted or
    combined score is attempted; nothing in the source docs calls for one, and a single
    elevated signal warranting review is consistent with the fail-closed philosophy already
    locked elsewhere (baseline §6). Revisit if dev-set calibration (a later milestone) shows
    this over- or under-triggers -- that calibration is explicitly out of this checkpoint's
    scope.
    """
    triggering: list[str] = []
    if velocity_result.band != "normal":
        triggering.append("spend_velocity")
    if category_shift_result.band != "none":
        triggering.append("category_shift")
    if clustering_result.band != "normal":
        triggering.append("clustering")

    return ThresholdCheckResult(crossed=bool(triggering), triggering_signals=tuple(triggering))


# ---------------------------------------------------------------------------
# Full orchestrator (Checkpoint C11)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class IncomingTransaction:
    """The not-yet-persisted transaction attempt being evaluated. `merchant`/`category`/etc.
    match `transactions` table columns exactly -- this is Plan §E's ingestion request shape,
    independent of any API-layer request model (C12, out of scope here)."""

    merchant: str
    category: str
    amount: float
    occurred_at: datetime
    idempotency_key: str


@dataclass(frozen=True)
class _WindowTransaction:
    """Adapts both the incoming transaction and already-persisted history to
    `TransactionLike` for signal computation -- the evidence engine's pure functions don't
    need (and the incoming transaction doesn't yet have) a DB-assigned id.

    `amount` is normalized to `float` here on purpose. `TransactionLike.amount` is declared
    `float`, but a `models.Transaction` loaded back from Postgres carries a
    `decimal.Decimal` (NUMERIC column), while the incoming transaction carries a real
    `float` from the API request model. Mixing the two in one window made
    `sum(t.amount for ...)` raise `TypeError: unsupported operand type(s) for +:
    'decimal.Decimal' and 'float'` inside compute_velocity/compute_category_shift -- so the
    live API 500'd on the *second* transaction against any mandate (the first has empty
    history, hence no Decimal to mix). The eval harness never hit it because it passes
    freshly-flushed ORM rows whose `amount` is still the Python float that was assigned,
    never round-tripped through the DB. Normalizing at this boundary keeps the engine
    receiving exactly what its Protocol declares, and makes the live and eval paths use one
    identical representation rather than two that only coincidentally agree.
    """

    amount: float
    category: str
    occurred_at: datetime

    @classmethod
    def of(cls, txn: TransactionLike) -> "_WindowTransaction":
        return cls(amount=float(txn.amount), category=txn.category, occurred_at=txn.occurred_at)


@dataclass(frozen=True)
class PipelineResult:
    transaction_id: uuid.UUID
    state: Literal["allowed", "held"]
    threshold_crossed: bool
    triggering_signals: tuple[str, ...]
    gate_decision: Literal["allow", "hold"] | None
    case_id: uuid.UUID | None
    llm_status: str | None


def run_pipeline(
    session: Session,
    mandate: models.Mandate,
    historical_transactions: Sequence[TransactionLike],
    incoming: IncomingTransaction,
    *,
    llm_client: anthropic.Anthropic | None = None,
    evidence_config: EvidenceEngineThresholds = EVIDENCE_ENGINE_THRESHOLDS,
    llm_config: SemanticRiskClientConfig = SEMANTIC_RISK_CLIENT_CONFIG,
    gate_config: GatePolicyConfig = GATE_POLICY_CONFIG,
    app_settings: Settings = settings,
) -> PipelineResult:
    """The one place that calls all three layers in sequence (Plan §D's own requirement) --
    API handlers (C12) and the eval harness (`eval/run.py`) both call this, never the
    individual layers directly, so live traffic and evaluation share exactly one orchestration
    path. `mandate` and `historical_transactions` are assumed already persisted; this function
    persists exactly one new `transactions` row (plus whatever else the crossed path requires)
    and returns.

    Ordering is deliberate: every signal/packet/LLM/gate computation happens BEFORE this
    function touches `session` at all, so the (possibly slow) network call to layer ② never
    happens with a DB transaction open. All resulting rows are then added and committed
    together in one `session.commit()` -- Plan §D step 7/9's atomicity requirement.
    """
    incoming_as_window_txn = _WindowTransaction(
        amount=float(incoming.amount), category=incoming.category, occurred_at=incoming.occurred_at
    )
    # Historical rows are normalized too, not passed through raw -- see _WindowTransaction's
    # docstring for why a DB-loaded Decimal amount alongside a float one used to crash the
    # signal computation outright.
    transactions_in_window: list[TransactionLike] = [
        *(_WindowTransaction.of(t) for t in historical_transactions),
        incoming_as_window_txn,
    ]

    velocity_result = compute_velocity(mandate, transactions_in_window, config=evidence_config)
    category_shift_result = compute_category_shift(mandate, transactions_in_window, config=evidence_config)
    clustering_result = compute_clustering(mandate, transactions_in_window, config=evidence_config)
    threshold_check = check_threshold(velocity_result, category_shift_result, clustering_result)

    if not threshold_check.crossed:
        return _persist_nominal_allow(session, mandate, incoming, velocity_result, category_shift_result, clustering_result)

    evidence_packet = build_evidence_packet(
        mandate, transactions_in_window, velocity_result, category_shift_result, clustering_result
    )
    llm_outcome = assess(evidence_packet, client=llm_client, app_settings=app_settings, config=llm_config)

    # Deferred import: policy_gate imports ThresholdCheckResult from this module, so a
    # module-level import here would be circular. By the time run_pipeline is actually
    # called, both modules are fully loaded -- this import is cheap and safe at call time.
    from app.domain.policy_gate import decide

    gate_result = decide(
        threshold_check, velocity_result, category_shift_result, clustering_result, llm_outcome, config=gate_config
    )

    return _persist_crossed_case(
        session, mandate, incoming, threshold_check, velocity_result, category_shift_result,
        clustering_result, evidence_packet, llm_outcome, gate_result,
    )


def _persist_nominal_allow(
    session: Session,
    mandate: models.Mandate,
    incoming: IncomingTransaction,
    velocity_result: VelocityResult,
    category_shift_result: CategoryShiftResult,
    clustering_result: ClusteringResult,
) -> PipelineResult:
    """Plan §D step 3's "not crossed" branch: terminal `allowed` state at insert time
    (Decision 4), one lightweight audit event (Plan §K's nominal-path completeness note), no
    evidence packet, no LLM call, no case row."""
    txn_row = models.Transaction(
        mandate_id=mandate.id,
        merchant=incoming.merchant,
        category=incoming.category,
        amount=incoming.amount,
        occurred_at=incoming.occurred_at,
        idempotency_key=incoming.idempotency_key,
        state="allowed",
    )
    session.add(txn_row)
    session.flush()

    session.add(
        models.AuditEvent(
            case_id=None,
            mandate_id=mandate.id,
            transaction_id=txn_row.id,
            event_type="evaluated_threshold_not_crossed",
            payload={
                "decision": "allow",
                "signals": {
                    "spend_velocity": velocity_result.band,
                    "category_shift": category_shift_result.band,
                    "clustering": clustering_result.band,
                },
            },
        )
    )
    session.commit()

    return PipelineResult(
        transaction_id=txn_row.id,
        state="allowed",
        threshold_crossed=False,
        triggering_signals=(),
        gate_decision=None,
        case_id=None,
        llm_status=None,
    )


def _persist_crossed_case(
    session: Session,
    mandate: models.Mandate,
    incoming: IncomingTransaction,
    threshold_check: ThresholdCheckResult,
    velocity_result: VelocityResult,
    category_shift_result: CategoryShiftResult,
    clustering_result: ClusteringResult,
    evidence_packet,
    llm_outcome,
    gate_result,
) -> PipelineResult:
    """Plan §D steps 4-8, one atomic transaction: transactions row (terminal state per the
    gate decision) -> evidence_packets row -> semantic_assessments row (success only, per
    Decision 5) -> gate_decisions row (always) -> cases row (hold only) -> one audit event
    sufficient, given a transaction_id alone, to answer "why" without re-running anything."""
    txn_row = models.Transaction(
        mandate_id=mandate.id,
        merchant=incoming.merchant,
        category=incoming.category,
        amount=incoming.amount,
        occurred_at=incoming.occurred_at,
        idempotency_key=incoming.idempotency_key,
        state="allowed" if gate_result.decision == "allow" else "held",
    )
    session.add(txn_row)
    session.flush()

    evidence_packet_row = models.EvidencePacket(
        mandate_id=mandate.id,
        transaction_id=txn_row.id,
        signals=evidence_packet.signals.model_dump(),
        trajectory=evidence_packet.trajectory.model_dump(),
    )
    session.add(evidence_packet_row)
    session.flush()

    semantic_assessment_row = None
    if llm_outcome.status == "success":
        semantic_assessment_row = models.SemanticAssessment(
            evidence_packet_id=evidence_packet_row.id,
            mandate_alignment=llm_outcome.llm_output.mandate_alignment,
            risk_level=llm_outcome.llm_output.risk_level,
            confidence=llm_outcome.llm_output.confidence,
            evidence=llm_outcome.llm_output.evidence,
            raw_response=llm_outcome.raw_response,
            model_version=llm_outcome.model_version,
            prompt_version=llm_outcome.prompt_version,
            latency_ms=round(llm_outcome.latency_ms) if llm_outcome.latency_ms is not None else 0,
        )
        session.add(semantic_assessment_row)
        session.flush()

    gate_decision_row = models.GateDecision(
        semantic_assessment_id=semantic_assessment_row.id if semantic_assessment_row else None,
        transaction_id=txn_row.id,
        decision=gate_result.decision,
        rule_version=gate_result.rule_version,
        rule_applied=gate_result.rule_applied,
    )
    session.add(gate_decision_row)
    session.flush()

    case_row = None
    if gate_result.decision == "hold":
        case_row = models.Case(
            mandate_id=mandate.id,
            transaction_id=txn_row.id,
            gate_decision_id=gate_decision_row.id,
            state="hold",
        )
        session.add(case_row)
        session.flush()

    session.add(
        models.AuditEvent(
            case_id=case_row.id if case_row else None,
            mandate_id=mandate.id,
            transaction_id=txn_row.id,
            event_type="evaluated_threshold_crossed",
            payload={
                "triggering_signals": list(threshold_check.triggering_signals),
                "signals": {
                    "spend_velocity": velocity_result.band,
                    "category_shift": category_shift_result.band,
                    "clustering": clustering_result.band,
                },
                "llm_status": llm_outcome.status,
                "gate_decision": gate_result.decision,
                "rule_applied": gate_result.rule_applied,
            },
        )
    )
    session.commit()

    return PipelineResult(
        transaction_id=txn_row.id,
        state=txn_row.state,
        threshold_crossed=True,
        triggering_signals=threshold_check.triggering_signals,
        gate_decision=gate_result.decision,
        case_id=case_row.id if case_row else None,
        llm_status=llm_outcome.status,
    )


# ---------------------------------------------------------------------------
# HOLD-resolution state machine (Plan §J), built now for C12's future
# POST /cases/{id}/resolve endpoint to call.
# ---------------------------------------------------------------------------


class InvalidCaseTransitionError(ValueError):
    """Raised on any transition not enumerated in docs/IMPLEMENTATION-PLAN.md §J's state
    machine -- resolving a case not currently in `hold` state (already resolved: no
    double-write; per architecture §7's 409-on-double-resolve). Terminal states
    (`resolved_allow`/`resolved_block`) never transition again."""


def resolve_hold(
    session: Session,
    case: models.Case,
    *,
    resolution: Literal["confirm", "deny"],
    resolved_by: str,
    resolution_reason: str,
) -> models.Case:
    """Ops-analyst resolution (Decision 1): confirm -> resolved_allow, deny ->
    resolved_block. Updates `cases.state` and `transactions.state` together, atomically (one
    `session.commit()`), per Plan §J's requirement that the two never diverge."""
    if case.state != "hold":
        raise InvalidCaseTransitionError(
            f"cannot resolve case {case.id}: state is {case.state!r}, not 'hold' -- no "
            "double-resolve, and resolved_allow/resolved_block are terminal."
        )

    new_case_state = "resolved_allow" if resolution == "confirm" else "resolved_block"
    new_txn_state = "allowed" if resolution == "confirm" else "blocked"

    case.state = new_case_state
    case.resolved_at = datetime.now(timezone.utc)
    case.resolved_by = resolved_by
    case.resolution_reason = resolution_reason
    case.transaction.state = new_txn_state
    session.flush()

    session.add(
        models.AuditEvent(
            case_id=case.id,
            mandate_id=case.mandate_id,
            transaction_id=case.transaction_id,
            event_type=f"case_resolved_{resolution}",
            payload={
                "resolution": resolution,
                "resolved_by": resolved_by,
                "resolution_reason": resolution_reason,
            },
        )
    )
    session.commit()
    return case


def check_and_apply_timeout(
    session: Session,
    case: models.Case,
    *,
    config: HoldResolutionConfig = HOLD_RESOLUTION_CONFIG,
) -> models.Case:
    """Decision 18: lazy, on-read timeout check -- no background job. Called whenever a case
    is read (a future C12 `GET /cases/{id}` handler, and directly in this checkpoint's tests).
    A case not in `hold`, or in `hold` but not yet past `timeout_window_hours`, is returned
    unchanged. BLOCK remains reachable only via HOLD (baseline §7), never directly."""
    if case.state != "hold":
        return case

    elapsed = datetime.now(timezone.utc) - case.opened_at
    if elapsed <= timedelta(hours=config.timeout_window_hours):
        return case

    case.state = "resolved_block"
    case.resolved_at = datetime.now(timezone.utc)
    case.resolved_by = "system:timeout"
    case.resolution_reason = f"resolution timeout exceeded ({config.timeout_window_hours}h)"
    case.transaction.state = "blocked"
    session.flush()

    session.add(
        models.AuditEvent(
            case_id=case.id,
            mandate_id=case.mandate_id,
            transaction_id=case.transaction_id,
            event_type="case_resolved_timeout",
            payload={"resolution": "timeout", "timeout_window_hours": config.timeout_window_hours},
        )
    )
    session.commit()
    return case
