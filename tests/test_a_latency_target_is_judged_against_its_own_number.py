"""Round 115 站8 — a declared NFR latency target is judged against its own number.

See core/quality_gate/latency_targets.py for the measured defect: the
`performance` dimension reported a framework formula (mean > 1000 ms) as the
verdict on taskq-open's NFR-01 (p95 < 30 ms / < 80 ms), and taskq-open's own
SRS says the framework does not check p95.
"""

from __future__ import annotations

import json
from pathlib import Path

from core.quality_gate.latency_targets import target_findings, target_verdicts

_SPEC = "# Spec\n\n## NFR\n\n- `GET /v1/tasks/{id}` p95 < 30ms at 10,000 rows\n"


def _srs(targets: list) -> str:
    block = {"functional_requirements": [{"id": "FR-01", "title": "x"}],
             "non_functional_requirements": [{"id": "NFR-01", "type": "performance",
                                              "description": "p95 < 30ms", "test_method": "bench",
                                              "targets": targets}]}
    return ("# SRS\n\n### FR-01: x\n\n- **AC-N1.1** p95 < 30ms (SPEC.md:5)\n\n"
            "```json\n" + json.dumps(block, indent=2) + "\n```\n")


_TARGET = {"ac": "AC-N1.1", "statistic": "p95", "op": "<", "value": 30, "unit": "ms",
           "spec_ref": "SPEC.md:5"}


def _project(tmp_path: Path, targets: list) -> Path:
    (tmp_path / "SPEC.md").write_text(_SPEC, encoding="utf-8")
    (tmp_path / "01-requirements").mkdir()
    (tmp_path / "01-requirements" / "SRS.md").write_text(_srs(targets), encoding="utf-8")
    (tmp_path / "TEST_INVENTORY.yaml").write_text(
        "nfr_tests:\n  NFR-01:\n    - tc_id: TC-N01-01\n      ac: AC-N1.1\n"
        "      test_function: test_nfr01_get_p95\n", encoding="utf-8")
    return tmp_path


# ── the transcription, checked at the Phase 1 exit ──────────────────────────

def test_a_target_the_spec_line_states_is_clean(tmp_path):
    assert target_findings(_project(tmp_path, [_TARGET])) == []


def test_a_number_that_drifted_from_the_spec_is_named(tmp_path):
    findings = target_findings(_project(tmp_path, [dict(_TARGET, value=80)]))
    assert any("does not state 80ms" in f for f in findings)


def test_vocabulary_ac_and_ref_are_checked(tmp_path):
    bad = [dict(_TARGET, statistic="p97"), dict(_TARGET, ac="AC-N9.9"),
           dict(_TARGET, spec_ref="SPEC.md"), {"ac": "AC-N1.1"}]
    text = " | ".join(target_findings(_project(tmp_path, bad)))
    assert "statistic 'p97'" in text and "AC-N9.9 is not an acceptance criterion" in text
    assert "must name a project file and line" in text and "missing" in text


def test_the_phase_1_vocabulary_check_reports_them(tmp_path):
    from core.quality_gate.srs_nfr_validate import illegal_nfr_vocabulary

    assert any("80ms" in f for f in illegal_nfr_vocabulary(_project(tmp_path, [dict(_TARGET, value=80)])))


# ── the verdict, at the gate ────────────────────────────────────────────────

_OUT_KEY = "03-development/tests/test_nfr.py::test_nfr01_get_p95"


def test_a_benchmark_with_raw_rounds_is_measured_by_the_framework(tmp_path):
    proj = _project(tmp_path, [_TARGET])
    fast = {"benchmarks": [{"name": "test_nfr01_get_p95", "stats": {"data": [0.010] * 19 + [0.020]}}]}
    slow = {"benchmarks": [{"name": "test_nfr01_get_p95", "stats": {"data": [0.010] * 18 + [0.050] * 2}}]}
    [ok] = target_verdicts(proj, {_OUT_KEY: "passed"}, fast)
    [bad] = target_verdicts(proj, {_OUT_KEY: "passed"}, slow)
    assert (ok["basis"], ok["passed"]) == ("tool-measured", True)
    assert (bad["basis"], bad["passed"], bad["measured_ms"]) == ("tool-measured", False, 50.0)


def test_without_a_benchmark_the_declared_tests_verdict_is_the_basis(tmp_path):
    proj = _project(tmp_path, [_TARGET])
    [ok] = target_verdicts(proj, {_OUT_KEY: "passed"}, None)
    [bad] = target_verdicts(proj, {_OUT_KEY: "failed"}, None)
    assert (ok["basis"], ok["passed"]) == ("test-asserted", True)
    assert (bad["basis"], bad["passed"]) == ("test-asserted", False)


def test_unmeasured_is_stated_not_scored(tmp_path):
    proj = _project(tmp_path, [_TARGET])
    [v] = target_verdicts(proj, None, None)
    assert (v["basis"], v["passed"]) == ("unmeasured", None)


def test_the_gate_sets_performance_to_zero_on_a_measured_violation_and_records_the_basis(tmp_path):
    from core.quality_gate.latency_targets import judge_performance_by_latency_targets
    from harness.harness_bridge import DimResult

    proj = _project(tmp_path, [_TARGET])
    (proj / ".sessi-work").mkdir()
    (proj / ".sessi-work" / "benchmark_report.json").write_text(json.dumps(
        {"benchmarks": [{"name": "test_nfr01_get_p95", "stats": {"data": [0.050] * 20}}]}))
    raw = {"breakdown": {"performance": {"score": 100.0}}}
    dims = [DimResult(name="performance", score=100.0, threshold=75.0)]
    new, changed = judge_performance_by_latency_targets(
        dims, str(proj), raw, test_outcomes={_OUT_KEY: "passed"})
    assert changed and new[0].score == 0.0
    assert raw["breakdown"]["performance"]["latency_targets"][0]["basis"] == "tool-measured"


def test_a_project_without_targets_keeps_the_generic_score_and_says_so(tmp_path):
    from core.quality_gate.latency_targets import judge_performance_by_latency_targets
    from harness.harness_bridge import DimResult

    proj = _project(tmp_path, [])
    raw = {"breakdown": {"performance": {"score": 100.0}}}
    dims = [DimResult(name="performance", score=100.0, threshold=75.0)]
    new, changed = judge_performance_by_latency_targets(dims, str(proj), raw)
    assert not changed and new[0].score == 100.0
    assert raw["breakdown"]["performance"]["latency_basis"] == "generic"


def test_the_benchmark_run_keeps_its_raw_rounds():
    from harness.toolchains.registry import TOOL_SPECS

    assert "--benchmark-save-data" in TOOL_SPECS["pytest-benchmark"].cmd


def test_the_p1_author_is_told_how_to_state_a_target():
    from scripts.workflowgen.generate_workflows import generate

    js = generate(1)
    assert '"spec_ref": "SPEC.md:<line>"' in js and "statistic: mean|median|max|p90|p95|p99" in js
