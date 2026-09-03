"""Semantic Risk Client — layer ② (Checkpoint C9).

One stateless, forced-tool-call completion per triggered evidence packet (baseline §5,
`[LOCKED]`): the evidence packet only as input, no raw transaction rows, no retrieval, no
chat history, no few-shot examples in MVP. Never returns ALLOW/HOLD/BLOCK -- that mapping
belongs entirely to the Policy Gate, a later milestone NOT touched here.

Decision 13 (docs/IMPLEMENTATION-BASELINE.md, 2026-09-02): model pinned to
`app.config.settings.llm_model` ("claude-sonnet-5"), an exact string in config, never
resolved at request time -- matters for the locked-test-set run-over-run reproducibility
protocol (eval-design's core methodology).

Decision 14 (docs/IMPLEMENTATION-BASELINE.md, 2026-09-02): no retry on malformed or
schema-invalid output (straight to a failure state). Exactly one transport-level retry for
connection errors / 5xx responses only, before that also becomes a failure state.

Scope note: this module does NOT decide what happens next -- `assess()` returns a structured
outcome, never raises for any of its four expected statuses, and the Policy Gate (a later
checkpoint) is what turns that outcome into ALLOW/HOLD/BLOCK.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Literal

import anthropic

from app.config import Settings, SemanticRiskClientConfig, SEMANTIC_RISK_CLIENT_CONFIG, settings
from app.domain.evidence_engine.packet_builder import EvidencePacket
from app.schemas.llm_output import LlmOutput

TOOL_NAME = "emit_risk_assessment"

# [IMPL DETAIL]: only the version TAG below is meaningful right now. The prompt WORDING is
# expected to be iterated during dev-set calibration (a later milestone, not this checkpoint)
# -- changing the wording must bump this constant, per baseline §5's prompt_version tracking
# requirement, so a reported result can always be traced to the exact prompt that produced it.
# See eval/calibration_log.md for the v2 calibration attempt (tested, then reverted).
PROMPT_VERSION = "v1"

_SYSTEM_PROMPT = """\
You are the semantic risk assessment layer of a merchant-side risk system that watches an AI \
shopping agent's transaction trajectory against the natural-language mandate a consumer \
granted it.

You receive a structured evidence packet: the consumer's mandate (purpose, budget, period, \
allowed categories), deterministic signal readings already computed by an upstream system \
(spend velocity, category shift, clustering), and a category-level spend trajectory. You do \
not see raw transactions, merchant names, or anything not already in this packet.

Your only job is to judge whether the aggregate spending pattern still matches the mandate's \
stated purpose. You must call the emit_risk_assessment tool exactly once with your \
assessment. You never decide ALLOW, HOLD, or BLOCK -- a separate system owns that decision. \
You have no tool other than emit_risk_assessment, and it has no side effects: it is a \
structured answer format, not an action, and nothing you output is executed.

Fields you must provide:
- mandate_alignment: "low", "medium", or "high" -- how well the current spending trajectory \
still matches the mandate's stated purpose ("low" = poor match / likely drift).
- risk_level: "low", "medium", or "high" -- your overall assessment of how risky this pattern \
is relative to the mandate.
- confidence: a number from 0 to 1 -- your genuine self-assessed confidence in this judgment, \
not a rounded or reflexively high value.
- evidence: a short list of natural-language justifications, grounded only in the evidence \
packet you were given.
"""

_TOOL_SCHEMA = LlmOutput.model_json_schema()


@dataclass(frozen=True)
class SemanticAssessmentOutcome:
    status: Literal["success", "timeout", "malformed", "transport_error"]
    llm_output: LlmOutput | None
    raw_response: dict[str, Any] | None
    model_version: str
    prompt_version: str
    latency_ms: float | None
    error_detail: str | None


class _TransportRetryExhausted(Exception):
    """Internal signal only -- raised after Decision 14's one retry is used up on a
    connection/5xx error. Never escapes this module; `assess()` always converts it to a
    status="transport_error" outcome."""


def _default_client(app_settings: Settings) -> anthropic.Anthropic:
    # max_retries=0: Decision 14's retry count is owned entirely by this module, not the
    # SDK's own built-in retry behavior (which defaults to 2) -- exactly one retry, not
    # however many the SDK would otherwise choose on its own.
    return anthropic.Anthropic(api_key=app_settings.anthropic_api_key, max_retries=0)


def _is_transport_retryable(exc: Exception) -> bool:
    if isinstance(exc, anthropic.APITimeoutError):
        return False  # timeout is its own status (below), not a transport-retry case
    if isinstance(exc, anthropic.APIConnectionError):
        return True
    if isinstance(exc, anthropic.APIStatusError):
        return exc.status_code >= 500
    return False


def _call_llm_with_retry(
    client: anthropic.Anthropic,
    *,
    config: SemanticRiskClientConfig,
    **kwargs: Any,
) -> Any:
    """Decision 14: exactly one retry, connection/5xx only, no backoff (not specified by the
    decision). `anthropic.APITimeoutError` and anything not classified as transport-retryable
    (including a genuinely unexpected exception) propagate immediately -- only a retryable
    transport error is ever caught and retried here."""
    last_exc: Exception | None = None
    for _attempt in range(1 + config.transport_retry_attempts):
        try:
            return client.messages.create(**kwargs)
        except anthropic.APITimeoutError:
            raise
        except Exception as exc:
            if _is_transport_retryable(exc):
                last_exc = exc
                continue
            raise
    raise _TransportRetryExhausted(str(last_exc)) from last_exc


def _parse_tool_response(response: Any) -> LlmOutput:
    """Raises `ValueError` (pydantic's `ValidationError` is a `ValueError` subclass) on any
    shape the caller should treat as malformed -- wrong block count, wrong tool name, or a
    schema-invalid `input`. No repair, no best-effort parsing."""
    tool_use_blocks = [block for block in response.content if getattr(block, "type", None) == "tool_use"]
    if len(tool_use_blocks) != 1:
        raise ValueError(f"expected exactly one tool_use block, got {len(tool_use_blocks)}")
    block = tool_use_blocks[0]
    if block.name != TOOL_NAME:
        raise ValueError(f"unexpected tool name: {block.name!r}")
    return LlmOutput.model_validate(block.input)


def assess(
    evidence_packet: EvidencePacket,
    *,
    client: anthropic.Anthropic | None = None,
    app_settings: Settings = settings,
    config: SemanticRiskClientConfig = SEMANTIC_RISK_CLIENT_CONFIG,
) -> SemanticAssessmentOutcome:
    """Never raises for its four expected outcomes (success/timeout/malformed/
    transport_error) -- a genuinely unexpected exception (e.g. a 4xx from a bad API key, or
    an SDK/programming error) is deliberately NOT caught here and propagates uncaught, per
    baseline §6's "any unhandled pipeline exception -> HOLD" rule living at the Policy Gate /
    pipeline orchestrator milestone, not swallowed at this layer."""
    client = client or _default_client(app_settings)
    start = time.monotonic()

    try:
        response = _call_llm_with_retry(
            client,
            config=config,
            model=app_settings.llm_model,
            max_tokens=1024,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": evidence_packet.model_dump_json()}],
            tools=[
                {
                    "name": TOOL_NAME,
                    "description": "Emit the structured semantic risk assessment for this evidence packet.",
                    "input_schema": _TOOL_SCHEMA,
                }
            ],
            tool_choice={"type": "tool", "name": TOOL_NAME},
            timeout=config.timeout_seconds,
        )
    except anthropic.APITimeoutError as exc:
        return SemanticAssessmentOutcome(
            status="timeout",
            llm_output=None,
            raw_response=None,
            model_version=app_settings.llm_model,
            prompt_version=PROMPT_VERSION,
            latency_ms=(time.monotonic() - start) * 1000,
            error_detail=str(exc),
        )
    except _TransportRetryExhausted as exc:
        return SemanticAssessmentOutcome(
            status="transport_error",
            llm_output=None,
            raw_response=None,
            model_version=app_settings.llm_model,
            prompt_version=PROMPT_VERSION,
            latency_ms=(time.monotonic() - start) * 1000,
            error_detail=str(exc),
        )

    latency_ms = (time.monotonic() - start) * 1000
    raw_response = response.model_dump(mode="json")

    try:
        llm_output = _parse_tool_response(response)
    except ValueError as exc:
        return SemanticAssessmentOutcome(
            status="malformed",
            llm_output=None,
            raw_response=raw_response,
            model_version=app_settings.llm_model,
            prompt_version=PROMPT_VERSION,
            latency_ms=latency_ms,
            error_detail=str(exc),
        )

    return SemanticAssessmentOutcome(
        status="success",
        llm_output=llm_output,
        raw_response=raw_response,
        model_version=app_settings.llm_model,
        prompt_version=PROMPT_VERSION,
        latency_ms=latency_ms,
        error_detail=None,
    )
