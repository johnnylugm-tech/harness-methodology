"""Round 12 站3b — INFRA_FAIL ≠ quality failure.

2026-07-16 P3 incident: run-gate's SAB phantom-module PRECONDITION block
fired before any dimension tool ran; the gate evaluator followed its STOP
RULE and recorded score=0 for three dimensions with the BLOCK text as
tool_evidence. The zeros entered the manifest as quality verdicts and
CODE-FIX agents were dispatched at healthy code.

_check_infra_fail_pollution rejects such results at finalize-gate: a zero
score whose evidence carries a precondition-block signature is not a
measurement. These tests pin the detector and its non-goals (real quality
zeros must pass through untouched).
"""
from __future__ import annotations

from harness.harness_bridge import _check_infra_fail_pollution


def _result(breakdown=None, dimensions=None):
    raw: dict = {"gate": 1, "overall_score": 0.0}
    if breakdown is not None:
        raw["breakdown"] = breakdown
    if dimensions is not None:
        raw["dimensions"] = dimensions
    return raw


class TestInfraFailPollution:
    def test_phantom_block_zero_is_rejected(self):
        """The incident shape: zeros whose evidence is run-gate's SAB
        phantom BLOCK output."""
        raw = _result(breakdown={
            "linting": {"score": 0, "tool_evidence":
                        "[BLOCKED] run-gate: Architecture Amendment Protocol "
                        "violation. Unregistered modules detected: {'taskq.storage.store'}"},
            "type_safety": {"score": 0, "tool_evidence":
                            "[BLOCKED] run-gate: Architecture Amendment Protocol violation."},
        })
        violations = _check_infra_fail_pollution(raw)
        assert len(violations) == 2
        assert "INFRA failure, not a quality" in violations[0]
        assert "Do NOT dispatch code fixes" in violations[0]

    def test_genuine_quality_zero_passes_through(self):
        """A real measurement of 0 (ruff exploded with violations) is a
        quality verdict and must NOT be intercepted."""
        raw = _result(breakdown={
            "linting": {"score": 0, "tool_evidence":
                        "ruff check: 214 violations (E501 x80, F401 x134)"},
        })
        assert _check_infra_fail_pollution(raw) == []

    def test_nonzero_score_with_block_mention_passes(self):
        """Evidence that merely MENTIONS a past block alongside a real
        passing score is not pollution (the tool demonstrably ran)."""
        raw = _result(breakdown={
            "linting": {"score": 95.0, "tool_evidence":
                        "ruff clean after resolving the earlier "
                        "[BLOCKED] run-gate precondition"},
        })
        assert _check_infra_fail_pollution(raw) == []

    def test_dimensions_list_shape_supported(self):
        """Gate 2+ results use a dimensions list, not a breakdown dict."""
        raw = _result(dimensions=[
            {"name": "architecture", "score": 0,
             "tool_evidence": "[BLOCKED] run-gate: manifest corrupted"},
        ])
        violations = _check_infra_fail_pollution(raw)
        assert len(violations) == 1
        assert "architecture" in violations[0]

    def test_missing_evidence_is_not_this_checkers_job(self):
        """Zero with NO evidence at all is S3's territory (evidence
        enforcement), not infra-pollution detection."""
        raw = _result(breakdown={"linting": {"score": 0}})
        assert _check_infra_fail_pollution(raw) == []

    def test_malformed_rows_are_ignored(self):
        raw = _result(breakdown={"linting": "not-a-dict"},
                      dimensions=["also-not-a-dict"])
        assert _check_infra_fail_pollution(raw) == []


class TestCheckerEnforcementConfig:
    """Round 12 站3c — per-checker enforcement overlay."""

    def test_default_is_warn(self, tmp_path):
        from core.harness_config import get_checker_enforcement
        assert get_checker_enforcement(tmp_path, "spec_unsatisfiable") == "warn"

    def test_overlay_promotes_to_block(self, tmp_path):
        import json
        from core.harness_config import get_checker_enforcement
        meth = tmp_path / ".methodology"
        meth.mkdir()
        (meth / "harness_config.json").write_text(json.dumps(
            {"values": {"checker_enforcement": {"spec_unsatisfiable": "block"}}}
        ))
        assert get_checker_enforcement(tmp_path, "spec_unsatisfiable") == "block"

    def test_invalid_level_falls_back_to_default(self, tmp_path):
        import json
        from core.harness_config import get_checker_enforcement
        meth = tmp_path / ".methodology"
        meth.mkdir()
        (meth / "harness_config.json").write_text(json.dumps(
            {"values": {"checker_enforcement": {"spec_unsatisfiable": "annihilate"}}}
        ))
        # whole-dict validation rejects the invalid level → registry default {}
        assert get_checker_enforcement(tmp_path, "spec_unsatisfiable") == "warn"

    def test_unlisted_checker_uses_default(self, tmp_path):
        import json
        from core.harness_config import get_checker_enforcement
        meth = tmp_path / ".methodology"
        meth.mkdir()
        (meth / "harness_config.json").write_text(json.dumps(
            {"values": {"checker_enforcement": {"other_checker": "block"}}}
        ))
        assert get_checker_enforcement(tmp_path, "spec_unsatisfiable") == "warn"
