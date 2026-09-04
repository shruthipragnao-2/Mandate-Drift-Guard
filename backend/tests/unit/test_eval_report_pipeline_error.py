"""eval/report.py's pipeline-error predicate (eval-design §16), after Decision 20.

The metric's DEFINITION had to shift, not its meaning. Before Decision 20 an unanticipated
pipeline failure escaped `run_pipeline` as an exception and the runners caught it into
`pipeline_error`. `run_pipeline`'s fail-closed backstop now catches it first and routes it to
HOLD, so it never escapes -- and a harness still counting only caught exceptions would report
zero errors for a run in which the pipeline genuinely threw. That undercount would get quieter
the better the backstop works, which is exactly the kind of silent metric drift this project's
"generation is not verification" discipline exists to prevent.

Requires `pytest.ini`'s `pythonpath = . ../eval`, same as tests/unit/test_eval_split_guard.py.
"""

import report


def _case(**overrides):
    case = {
        "case_id": "c1",
        "ground_truth_label": "legitimate",
        "hybrid": {"threshold_crossed": True, "llm_status": "success", "audit_event_present": True},
        "rules_only": {"gate_decision": "allow"},
        "pipeline_error": None,
    }
    case.update(overrides)
    return case


def test_clean_case_is_not_an_error():
    assert report.is_pipeline_error(_case()) is False


def test_caught_exception_still_counts():
    """Source 1, unchanged: a runner that catches an exception around run_pipeline (the only
    source that existed before Decision 20) must keep counting."""
    assert report.is_pipeline_error(_case(pipeline_error="boom")) is True


def test_backstop_catch_counts_as_a_pipeline_error():
    """Source 2, the reason this predicate exists. The pipeline threw; the system failed closed
    and kept an audit record instead of 500ing with nothing persisted. It still threw."""
    case = _case()
    case["hybrid"] = {**case["hybrid"], "fail_closed_reason": "RuntimeError"}
    assert report.is_pipeline_error(case) is True


def test_explicit_none_fail_closed_reason_is_not_an_error():
    """Every ordinary result now carries this key set to None -- its presence must not be
    mistaken for its truth."""
    case = _case()
    case["hybrid"] = {**case["hybrid"], "fail_closed_reason": None}
    assert report.is_pipeline_error(case) is False


def test_missing_key_is_not_an_error():
    """Results written before Decision 20 have no `fail_closed_reason` key at all. They must
    read as clean rather than raising -- this is what lets the locked C13 report be recomputed
    with today's code and produce its recorded numbers unchanged."""
    assert report.is_pipeline_error(_case()) is False


def test_null_hybrid_does_not_raise():
    assert report.is_pipeline_error(_case(hybrid=None)) is False


def test_error_rate_counts_both_sources_together():
    cases = [
        _case(case_id="clean"),
        _case(case_id="caught", pipeline_error="boom"),
        _case(case_id="backstop", hybrid={"threshold_crossed": True, "llm_status": None, "fail_closed_reason": "ValueError"}),
    ]
    metrics = report.reliability_metrics(cases)
    assert metrics["pipeline_error_count"] == 2
    assert metrics["pipeline_error_rate"] == 2 / 3
    # Neither failed case may be counted as a completed LLM call: one never reached the LLM,
    # the other's outcome is unknown. Only the clean case is.
    assert metrics["llm_calls"] == 1


def test_backstop_case_is_not_counted_as_a_complete_audit_record():
    """The deliberate asymmetry. Decision 20 means a backstop case DOES have an audit event --
    that is precisely what the fix restored -- but "we recorded why it failed" is a different
    claim from "it did not fail". eval-design §15's completeness metric must not be quietly
    inflated by the very failures §16 is counting."""
    cases = [
        _case(case_id="backstop", hybrid={"threshold_crossed": False, "audit_event_present": True, "fail_closed_reason": "RuntimeError"}),
    ]
    assert report.audit_completeness_rate(cases)["complete"] == 0
