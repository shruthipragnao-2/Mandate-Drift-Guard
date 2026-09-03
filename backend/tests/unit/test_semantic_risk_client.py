"""Unit tests for the Semantic Risk Client (Checkpoint C9). Fully mocked -- no real network
call, no ANTHROPIC_API_KEY required to run this file. The real-API integration proof lives in
tests/unit/test_semantic_risk_client_smoke.py (network-dependent, separately marked).
"""

from unittest.mock import MagicMock

import anthropic
import httpx
import pytest

from app.config import Settings, SemanticRiskClientConfig
from app.domain.semantic_risk_client import assess

_REQUEST = httpx.Request("POST", "https://api.anthropic.com/v1/messages")


class _FakeToolUseBlock:
    def __init__(self, name, input_):
        self.type = "tool_use"
        self.name = name
        self.input = input_


class _FakeMessage:
    def __init__(self, content):
        self.content = content

    def model_dump(self, mode="json"):
        return {
            "content": [
                {"type": b.type, "name": getattr(b, "name", None), "input": getattr(b, "input", None)}
                for b in self.content
            ]
        }


def _valid_tool_input(**overrides):
    defaults = dict(
        mandate_alignment="low",
        risk_level="high",
        confidence=0.91,
        evidence=["spend has shifted away from allowed categories"],
    )
    defaults.update(overrides)
    return defaults


def _make_client(**create_kwargs):
    client = MagicMock(spec=anthropic.Anthropic)
    client.messages.create = MagicMock(**create_kwargs)
    return client


def _test_settings() -> Settings:
    return Settings(anthropic_api_key="unused-in-mocked-tests")


def _fast_config() -> SemanticRiskClientConfig:
    return SemanticRiskClientConfig()


# ---------------------------------------------------------------------------
# Success path
# ---------------------------------------------------------------------------


def test_success_path_populates_every_field(evidence_packet_factory):
    response = _FakeMessage([_FakeToolUseBlock("emit_risk_assessment", _valid_tool_input())])
    client = _make_client(return_value=response)

    outcome = assess(
        evidence_packet_factory(), client=client, app_settings=_test_settings(), config=_fast_config()
    )

    assert outcome.status == "success"
    assert outcome.llm_output.mandate_alignment == "low"
    assert outcome.llm_output.risk_level == "high"
    assert outcome.llm_output.confidence == 0.91
    assert outcome.llm_output.evidence == ["spend has shifted away from allowed categories"]
    assert outcome.raw_response is not None
    assert outcome.model_version == "claude-sonnet-5"
    assert outcome.prompt_version == "v2"
    assert outcome.latency_ms is not None and outcome.latency_ms >= 0
    assert outcome.error_detail is None
    assert client.messages.create.call_count == 1


def test_success_path_calls_with_forced_tool_choice_and_pinned_model(evidence_packet_factory):
    response = _FakeMessage([_FakeToolUseBlock("emit_risk_assessment", _valid_tool_input())])
    client = _make_client(return_value=response)

    assess(evidence_packet_factory(), client=client, app_settings=_test_settings(), config=_fast_config())

    _, kwargs = client.messages.create.call_args
    assert kwargs["model"] == "claude-sonnet-5"
    assert kwargs["tool_choice"] == {"type": "tool", "name": "emit_risk_assessment"}
    assert len(kwargs["tools"]) == 1
    assert kwargs["tools"][0]["name"] == "emit_risk_assessment"
    assert kwargs["messages"][0]["role"] == "user"


# ---------------------------------------------------------------------------
# Failure fixture #1 -- simulated timeout
# ---------------------------------------------------------------------------


def test_failure_fixture_1_timeout(evidence_packet_factory):
    client = _make_client(side_effect=anthropic.APITimeoutError(request=_REQUEST))

    outcome = assess(
        evidence_packet_factory(), client=client, app_settings=_test_settings(), config=_fast_config()
    )

    assert outcome.status == "timeout"
    assert outcome.llm_output is None
    assert outcome.raw_response is None
    assert client.messages.create.call_count == 1  # no retry on timeout


# ---------------------------------------------------------------------------
# Failure fixture #2 -- simulated malformed output
# ---------------------------------------------------------------------------


def test_failure_fixture_2_missing_required_field_is_malformed(evidence_packet_factory):
    bad_input = _valid_tool_input()
    del bad_input["evidence"]
    response = _FakeMessage([_FakeToolUseBlock("emit_risk_assessment", bad_input)])
    client = _make_client(return_value=response)

    outcome = assess(
        evidence_packet_factory(), client=client, app_settings=_test_settings(), config=_fast_config()
    )

    assert outcome.status == "malformed"
    assert outcome.llm_output is None
    assert outcome.raw_response is not None  # raw payload preserved for the audit trail
    assert client.messages.create.call_count == 1  # no retry on malformed


def test_failure_fixture_2_wrong_tool_name_is_malformed(evidence_packet_factory):
    response = _FakeMessage([_FakeToolUseBlock("some_other_tool", _valid_tool_input())])
    client = _make_client(return_value=response)

    outcome = assess(
        evidence_packet_factory(), client=client, app_settings=_test_settings(), config=_fast_config()
    )

    assert outcome.status == "malformed"


def test_failure_fixture_2_no_tool_use_block_is_malformed(evidence_packet_factory):
    response = _FakeMessage([])
    client = _make_client(return_value=response)

    outcome = assess(
        evidence_packet_factory(), client=client, app_settings=_test_settings(), config=_fast_config()
    )

    assert outcome.status == "malformed"


def test_failure_fixture_2_invalid_risk_level_value_is_malformed(evidence_packet_factory):
    """Decision 6: risk_level's three-value set is validated at this layer -- an unrecognized
    value must fail here, not silently pass through."""
    response = _FakeMessage(
        [_FakeToolUseBlock("emit_risk_assessment", _valid_tool_input(risk_level="extreme"))]
    )
    client = _make_client(return_value=response)

    outcome = assess(
        evidence_packet_factory(), client=client, app_settings=_test_settings(), config=_fast_config()
    )

    assert outcome.status == "malformed"


# ---------------------------------------------------------------------------
# Failure fixture #3 -- Decision 5's unusable-confidence collapse
# ---------------------------------------------------------------------------


def test_failure_fixture_3_out_of_range_confidence_with_high_risk_is_malformed(evidence_packet_factory):
    """eval-design's original fixture #3 ("low-confidence-despite-high-stated-risk") expects
    a *validly low but in-range* confidence (e.g. 0.2) paired with risk_level="high" to reach
    the Policy Gate and route to HOLD there -- that's ordinary "success" output as far as THIS
    layer is concerned, and is out of scope until the gate exists (a later checkpoint).

    What this test exercises instead, per Decision 5 (docs/IMPLEMENTATION-BASELINE.md,
    2026-09-02): an *unusable* confidence -- out of the [0, 1] range, not merely numerically
    low -- must collapse into the same malformed/no-row path as any other schema violation,
    at THIS layer, via LlmOutput's `Field(ge=0, le=1)` constraint. Decision 5 is explicit that
    "missing, malformed, or out-of-range confidence" is treated identically to any other
    malformed response: no semantic_assessments row is ever written for it. This is that
    collapse, not a separate code path -- do not mistake it for one later.
    """
    response = _FakeMessage(
        [_FakeToolUseBlock("emit_risk_assessment", _valid_tool_input(confidence=-0.1, risk_level="high"))]
    )
    client = _make_client(return_value=response)

    outcome = assess(
        evidence_packet_factory(), client=client, app_settings=_test_settings(), config=_fast_config()
    )

    assert outcome.status == "malformed"
    assert client.messages.create.call_count == 1  # no retry on malformed


def test_ordinary_low_but_in_range_confidence_is_still_a_success(evidence_packet_factory):
    """Contrast case for the test above: a merely LOW (not unusable) confidence is a
    perfectly valid LlmOutput and must NOT be treated as malformed at this layer -- whether
    the gate trusts it is that later milestone's decision, not this one's."""
    response = _FakeMessage(
        [_FakeToolUseBlock("emit_risk_assessment", _valid_tool_input(confidence=0.2, risk_level="high"))]
    )
    client = _make_client(return_value=response)

    outcome = assess(
        evidence_packet_factory(), client=client, app_settings=_test_settings(), config=_fast_config()
    )

    assert outcome.status == "success"
    assert outcome.llm_output.confidence == 0.2
    assert outcome.llm_output.risk_level == "high"


# ---------------------------------------------------------------------------
# Decision 14 -- transport-level retry
# ---------------------------------------------------------------------------


def test_retry_then_success_on_connection_error(evidence_packet_factory):
    response = _FakeMessage([_FakeToolUseBlock("emit_risk_assessment", _valid_tool_input())])
    client = _make_client(
        side_effect=[anthropic.APIConnectionError(request=_REQUEST), response]
    )

    outcome = assess(
        evidence_packet_factory(), client=client, app_settings=_test_settings(), config=_fast_config()
    )

    assert outcome.status == "success"
    assert client.messages.create.call_count == 2


def test_retry_then_transport_error_on_connection_error(evidence_packet_factory):
    client = _make_client(
        side_effect=[
            anthropic.APIConnectionError(request=_REQUEST),
            anthropic.APIConnectionError(request=_REQUEST),
        ]
    )

    outcome = assess(
        evidence_packet_factory(), client=client, app_settings=_test_settings(), config=_fast_config()
    )

    assert outcome.status == "transport_error"
    assert client.messages.create.call_count == 2  # initial + exactly one retry, per Decision 14


def test_retry_then_success_on_5xx(evidence_packet_factory):
    response = _FakeMessage([_FakeToolUseBlock("emit_risk_assessment", _valid_tool_input())])
    server_error = anthropic.InternalServerError(
        "server error", response=httpx.Response(status_code=500, request=_REQUEST), body=None
    )
    client = _make_client(side_effect=[server_error, response])

    outcome = assess(
        evidence_packet_factory(), client=client, app_settings=_test_settings(), config=_fast_config()
    )

    assert outcome.status == "success"
    assert client.messages.create.call_count == 2


def test_4xx_is_not_retried_and_propagates_uncaught(evidence_packet_factory):
    """A 4xx (e.g. bad API key, malformed request) is not one of the four documented
    outcomes -- it's a genuinely unexpected/configuration-level failure and must propagate,
    not be silently absorbed into transport_error or any other status."""
    auth_error = anthropic.AuthenticationError(
        "invalid api key", response=httpx.Response(status_code=401, request=_REQUEST), body=None
    )
    client = _make_client(side_effect=auth_error)

    with pytest.raises(anthropic.AuthenticationError):
        assess(evidence_packet_factory(), client=client, app_settings=_test_settings(), config=_fast_config())

    assert client.messages.create.call_count == 1  # not retried
