"""Round 115 站5 — QUALITY_REPORT.md states the gate's own verdict and where its defect counts come from.

Two defects in the human-facing Gate 4 deliverable, both measured:

1. Status. `_build_dimension_table` printed `✓ PASS` for any score >= 70 —
   a number of its own. The gate judges each dimension against
   `harness_bridge._effective_threshold` (the gate config's value first), and
   that number was written nowhere: the breakdown's `threshold` is the
   agent's. A dimension at 85 under a threshold of 100 read PASS in the report
   beside a FAIL verdict, and the independent verifier compared scores only.
   finalize-gate now records each dimension's `effective_threshold` and
   `passed` where it computes them, and the report prints exactly that.

2. Defects. `_build_defect_summary` counted `gate_result["issues"]`, a field
   no producer writes: 13 of 13 corpus gate-4 results lack it, so every
   delivered report said Critical 0 / High 0 / Medium 0 / Low 0 — including
   taskq-new's, whose hunt holds a confirmed CRITICAL. The framework's defect
   ledger is the bug hunt report; the summary is read from it, and with no
   report the section says the count was not measured.
"""

from __future__ import annotations

import json
from pathlib import Path

from core.quality_gate.quality_report_verify import verify_quality_report
from scripts.generate_quality_report import _build_dimension_table, generate_quality_report


def _row(text: str, label: str) -> str:
    return next(line for line in text.splitlines() if line.startswith(f"| {label} |"))


def test_the_status_is_the_recorded_verdict_not_a_threshold_of_its_own():
    text = "\n".join(_build_dimension_table({"breakdown": {
        "secrets_scanning": {"score": 85.0, "threshold": 0, "effective_threshold": 100.0, "passed": False},
        "readability": {"score": 72.0, "effective_threshold": 70.0, "passed": True},
    }}))
    assert "✗ FAIL" in _row(text, "Secrets Scanning") and "100" in _row(text, "Secrets Scanning")
    assert "✓ PASS" in _row(text, "Readability")


def test_a_dimension_with_no_recorded_verdict_is_not_given_one():
    text = "\n".join(_build_dimension_table({"breakdown": {"security": {"score": 98.0}}}))
    assert "PASS" not in _row(text, "Security") and "FAIL" not in _row(text, "Security")
    assert "UNKNOWN" in _row(text, "Security")


def test_finalize_records_the_threshold_and_verdict_it_used():
    from harness.gate_stages import _FinalizeStages
    from harness.harness_bridge import DimResult

    raw = {"breakdown": {"security": {"score": 85.0, "threshold": 80}}}
    dims = [DimResult(name="security", score=85.0, threshold=80.0)]
    _FinalizeStages._stage_record_dimension_verdicts(
        raw, dims, lambda d: d.score >= 100.0, lambda d: 100.0)
    assert raw["breakdown"]["security"]["effective_threshold"] == 100.0
    assert raw["breakdown"]["security"]["passed"] is False


def _project(tmp_path: Path, breakdown: dict, hunt: "dict | None") -> Path:
    (tmp_path / ".methodology").mkdir()
    (tmp_path / ".methodology" / "gate4_result.json").write_text(json.dumps(
        {"composite_score": 90.0, "breakdown": breakdown}))
    (tmp_path / ".methodology" / "quality_manifest.json").write_text(json.dumps({"gate_results": {}}))
    if hunt is not None:
        (tmp_path / ".methodology" / "bug_hunt_report.json").write_text(json.dumps(hunt))
    generate_quality_report(str(tmp_path))
    return tmp_path


def _finding(fid: str, severity: str, confirmed: bool, status: str) -> dict:
    return {"id": fid, "severity": severity, "confirmed": confirmed, "resolution": {"status": status}}


def _report(proj: Path) -> str:
    return (proj / "06-quality" / "QUALITY_REPORT.md").read_text(encoding="utf-8")


def test_the_defect_counts_come_from_the_hunt_report(tmp_path):
    hunt = {"findings": [
        _finding("a#1", "critical", True, "refuted"),
        _finding("b#1", "high", True, "resolved"),
        _finding("b#2", "high", True, "open"),
        _finding("c#1", "low", False, "refuted"),
    ]}
    proj = _project(tmp_path, {"security": {"score": 98.0, "effective_threshold": 80.0, "passed": True}}, hunt)
    text = _report(proj)
    assert "bug_hunt_report.json" in text
    assert "**Critical**: 1 (open 0, resolved 0, refuted 1)" in text
    assert "**High**: 2 (open 1, resolved 1, refuted 0)" in text
    assert "**Low**: 0" in text
    assert "Unconfirmed findings" in text and ": 1" in text.split("Unconfirmed findings")[1].splitlines()[0]
    assert verify_quality_report(proj) == []


def test_no_hunt_report_is_not_zero_defects(tmp_path):
    proj = _project(tmp_path, {"security": {"score": 98.0, "effective_threshold": 80.0, "passed": True}}, None)
    text = _report(proj)
    section = text.split("## Defect / Issue Summary")[1].split("---")[0]
    assert "Not measured" in section
    assert "**Critical**: 0" not in section


def test_the_verifier_catches_a_status_that_disagrees_with_the_verdict(tmp_path):
    proj = _project(tmp_path, {"secrets_scanning": {"score": 85.0, "effective_threshold": 100.0, "passed": False}}, None)
    path = proj / "06-quality" / "QUALITY_REPORT.md"
    path.write_text(path.read_text(encoding="utf-8").replace("✗ FAIL", "✓ PASS"), encoding="utf-8")
    assert any("Secrets Scanning" in v and "FAIL" in v for v in verify_quality_report(proj))


def test_the_verifier_catches_a_defect_count_that_disagrees_with_the_hunt(tmp_path):
    hunt = {"findings": [_finding("a#1", "critical", True, "open")]}
    proj = _project(tmp_path, {"security": {"score": 98.0, "effective_threshold": 80.0, "passed": True}}, hunt)
    path = proj / "06-quality" / "QUALITY_REPORT.md"
    path.write_text(path.read_text(encoding="utf-8").replace("**Critical**: 1", "**Critical**: 0"), encoding="utf-8")
    assert any("Critical" in v for v in verify_quality_report(proj))
