"""Real-API smoke test for the Semantic Risk Client (Checkpoint C9).

Network-dependent -- makes real calls against the Anthropic API using the pinned model
(Decision 13, `settings.llm_model`). Requires `ANTHROPIC_API_KEY` to be set (via `.env` or the
environment) and costs real API credits, so it's excluded from the default `pytest` run via
the `smoke` marker (see pytest.ini's `addopts = -m "not smoke"`). Run explicitly with:

    pytest -m smoke tests/unit/test_semantic_risk_client_smoke.py -v -s

This is the actual "does the real integration work" proof for Checkpoint C9 -- the mocked
tests in test_semantic_risk_client.py prove this module's own control flow is correct, but
only a real call proves the forced-tool-call schema, the pinned model string, and the prompt
actually produce a response `schemas/llm_output.py` validates.
"""

import pytest

from app.config import settings
from app.domain.semantic_risk_client import assess

pytestmark = pytest.mark.smoke


def _skip_reason() -> str | None:
    if not settings.anthropic_api_key:
        return "ANTHROPIC_API_KEY not set -- skipping real-API smoke test"
    return None


@pytest.mark.skipif(_skip_reason() is not None, reason=_skip_reason() or "")
class TestRealApiSmoke:
    def test_clear_drift_case_validates(self, evidence_packet_factory):
        packet = evidence_packet_factory(
            mandate={
                "purpose": "weekly household groceries",
                "budget": 8000.0,
                "period_days": 7,
                "allowed_categories": ["groceries", "household essentials"],
            },
            signals={
                "budget_utilization": 0.91,
                "spend_velocity": "critical",
                "category_shift": "severe",
                "clustering": "highly_clustered",
            },
            trajectory={
                "historical_distribution": {"groceries": 700.0, "household essentials": 200.0},
                "current_distribution": {"other": 7100.0, "groceries": 100.0},
            },
        )

        outcome = assess(packet)

        assert outcome.status == "success", outcome.error_detail
        assert outcome.llm_output is not None
        assert outcome.llm_output.mandate_alignment in ("low", "medium", "high")
        assert outcome.llm_output.risk_level in ("low", "medium", "high")
        assert 0.0 <= outcome.llm_output.confidence <= 1.0
        assert isinstance(outcome.llm_output.evidence, list) and outcome.llm_output.evidence
        assert outcome.model_version == "claude-sonnet-5"
        assert outcome.prompt_version == "v1"
        print("\n--- clear drift case ---")
        print(outcome.llm_output.model_dump_json(indent=2))
        print(f"latency_ms={outcome.latency_ms:.1f}")

    def test_clearly_nominal_case_validates(self, evidence_packet_factory):
        packet = evidence_packet_factory(
            mandate={
                "purpose": "weekly household groceries",
                "budget": 8000.0,
                "period_days": 7,
                "allowed_categories": ["groceries", "household essentials"],
            },
            signals={
                "budget_utilization": 0.55,
                "spend_velocity": "elevated",
                "category_shift": "minor",
                "clustering": "normal",
            },
            trajectory={
                "historical_distribution": {"groceries": 600.0, "household essentials": 150.0},
                "current_distribution": {"groceries": 650.0, "household essentials": 180.0, "other": 50.0},
            },
        )

        outcome = assess(packet)

        assert outcome.status == "success", outcome.error_detail
        assert outcome.llm_output is not None
        print("\n--- clearly nominal case ---")
        print(outcome.llm_output.model_dump_json(indent=2))
        print(f"latency_ms={outcome.latency_ms:.1f}")

    def test_ambiguous_boundary_case_validates(self, evidence_packet_factory):
        packet = evidence_packet_factory(
            mandate={
                "purpose": "weekly household groceries",
                "budget": 8000.0,
                "period_days": 7,
                "allowed_categories": ["groceries", "household essentials"],
            },
            signals={
                "budget_utilization": 0.78,
                "spend_velocity": "elevated",
                "category_shift": "minor",
                "clustering": "clustered",
            },
            trajectory={
                "historical_distribution": {"groceries": 500.0, "household essentials": 100.0},
                "current_distribution": {"groceries": 400.0, "household essentials": 80.0, "other": 150.0},
            },
        )

        outcome = assess(packet)

        assert outcome.status == "success", outcome.error_detail
        assert outcome.llm_output is not None
        print("\n--- ambiguous boundary case ---")
        print(outcome.llm_output.model_dump_json(indent=2))
        print(f"latency_ms={outcome.latency_ms:.1f}")
