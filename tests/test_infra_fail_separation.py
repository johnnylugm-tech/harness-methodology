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
             "tool_evidence":
             "Architecture Amendment Protocol violation. "
             "Unregistered modules detected: {'taskq.arch.phantom'}"},
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

    def test_partial_infra_pollution_passes_through(self):
        """Round N: if at least ONE dimension PASSed cleanly while another is
        INFRA-failed, the run-gate DID execute end-to-end. Blanket-rejecting
        would discard a real PASS verdict (taskq-plus FR-05 P3 2026-07:
        GATE1 hit `[BLOCKED] run-gate` for the SAB phantom dimension while
        7/8 others scored normally; the blanked rejection escalated to human
        on a false positive). Expect a diagnostic-prefixed marker so
        operators still see the partial-pollution info, but no whole-gate
        rejection (the verdict is accepted)."""
        raw = _result(breakdown={
            "linting": {"score": 95.0, "tool_evidence": "ruff clean"},
            "type_safety": {"score": 0, "tool_evidence":
                            "[BLOCKED] run-gate: Architecture Amendment Protocol violation."},
        })
        violations = _check_infra_fail_pollution(raw)
        # Partial pollution: at least one clean PASS → accept verdict, surface
        # diagnostic via the [partial-pollution] marker.
        assert len(violations) == 1
        assert violations[0].startswith("[partial-pollution]")
        assert "type_safety" in violations[0]

    def test_all_dims_infra_failed_whole_pollution_kept(self):
        """Sanity check the partial-pollution carve-out does NOT weaken the
        whole-gate INFRA-pollution rejection: when EVERY dimension is INFRA-
        failed with no clean PASS counterpart, the original behaviour is
        preserved (violations list reflects every affected dimension)."""
        raw = _result(breakdown={
            "linting": {"score": 0, "tool_evidence":
                        "[BLOCKED] run-gate: Architecture Amendment Protocol "
                        "violation. Unregistered modules detected: {'taskq.foo'}"},
            "type_safety": {"score": 0, "tool_evidence":
                            "[BLOCKED] run-gate: Architecture Amendment Protocol violation."},
            "test_coverage": {"score": 0, "tool_evidence":
                              "[BLOCKED] run-gate: SAB phantom module 'taskq.bar'"},
        })
        violations = _check_infra_fail_pollution(raw)
        # 3 dims, 0 clean PASS → whole-gate pollution preserved
        assert len(violations) == 3
        assert not any(v.startswith("[partial-pollution]") for v in violations)

    def test_partial_with_multiple_infra_dims(self):
        """Multiple INFRA-failed dimensions + at least one clean PASS → still
        accepted as partial pollution; the [partial-pollution] marker names
        every affected dimension for operator visibility."""
        raw = _result(breakdown={
            "linting": {"score": 95.0, "tool_evidence": "ruff clean"},
            "type_safety": {"score": 0, "tool_evidence":
                            "[BLOCKED] run-gate: Architecture Amendment Protocol violation."},
            "architecture": {"score": 0, "tool_evidence":
                             "[BLOCKED] run-gate: phantom module 'taskq.baz'"},
        })
        violations = _check_infra_fail_pollution(raw)
        assert len(violations) == 1
        marker = violations[0]
        assert marker.startswith("[partial-pollution]")
        assert "type_safety" in marker
        assert "architecture" in marker


class TestBlockedReportSurvivesToTheGuard:
    """Round 26 — replay of taskq-plus's own log entry, end to end.

    Round 13 站2a added `_classify_infra_or_harness_bug` so that an INFRA
    precondition failure aborts the fix loop instead of dispatching CODE-FIX at
    healthy code. It works by string-matching the sub-agent's dispatch output
    against harness_bridge._INFRA_FAIL_EVIDENCE_SIGNATURES. Those signatures live
    in the verbatim [BLOCKED] quote the GATE1 prompt orders the agent to include.

    `_validate_inner_json` REPLACED that output with a one-line synthetic
    diagnostic, so the guard was matching a string from which every signature had
    already been removed. Measured on the real entry (taskq-plus
    .methodology/sessions_spawn.log, 2026-07-29T21:41:57, exit_code 0,
    error_class EXECUTION_ERROR, inner_status INFRA_BLOCKED): the guard saw None,
    and the next dispatch was a CODE-FIX told "sub-agent timeout or error" that
    burned 51 turns against an unresolvable SAB phantom.

    These pin both halves: the evidence survives, and the classification no longer
    depends on whether the agent volunteered a `"pass": false` key.
    """

    # The reply as the FR-05 Gate 1 evaluator actually returned it, trimmed.
    AGENT_REPLY = (
        '```json\n'
        '{\n'
        '  "status": "INFRA_BLOCKED",\n'
        '  "gate_score": null,\n'
        '  "summary": "[BLOCKED] run-gate: SAB phantom module \'taskq_plus.cli.main\'",\n'
        '  "blocker": "[BLOCKED] run-gate: Architecture Amendment Protocol violation.\\n'
        'Phantom modules declared in SAB.json but not implemented in codebase: '
        "['taskq_plus.cli.main']\"\n"
        '}\n'
        '```'
    )

    def _validated(self, reply: str, step: str = "GATE1") -> dict:
        from core.agent_spawner import _validate_inner_json
        err = _validate_inner_json({"result": reply}, step)
        assert err is not None, "a reported blocker must not read as legitimate progress"
        return err

    def test_the_blocked_quote_reaches_the_round13_guard(self):
        from cli.fr_cmds import _classify_infra_or_harness_bug

        err = self._validated(self.AGENT_REPLY)
        verdict = _classify_infra_or_harness_bug(err["output"])
        assert verdict is not None, (
            "the INFRA guard saw no signature — the sub-agent's [BLOCKED] quote was "
            "dropped from `output` again, which routes an unmet precondition into "
            "CODE-FIX at healthy code"
        )
        assert verdict[0] == "INFRA"

    def test_the_synthetic_diagnostic_is_still_there_for_the_reader(self):
        err = self._validated(self.AGENT_REPLY)
        assert err["output"].startswith("Sub-agent reported inner status")
        assert "INFRA_BLOCKED" in err["output"]

    def test_classification_does_not_depend_on_a_volunteered_pass_key(self):
        """The old coin flip: with `pass: false` the blocker was waved through as
        progress; without it, it became an EXECUTION_ERROR. Same blocker."""
        with_pass = self.AGENT_REPLY.replace(
            '"gate_score": null,', '"gate_score": null,\n  "pass": false,'
        )
        assert '"pass": false' in with_pass
        for reply in (self.AGENT_REPLY, with_pass):
            err = self._validated(reply)
            assert err["error_class"] == "INFRA"
            assert err["inner_status"] == "INFRA_BLOCKED"

    def test_mast_files_it_as_infra_not_specification(self):
        from core.failure_modes import classify_entry

        err = self._validated(self.AGENT_REPLY)
        # sessions_spawn.log shape: the spawner's `output` lands in error_output.
        entry = {"status": "ERROR", "error_output": err["output"],
                 "regression_flags": {}}
        verdict = classify_entry(entry)
        assert verdict["mode_id"] == "infra_precondition_blocked", verdict
        assert verdict["mast_category"] == "infra"

    def test_a_genuine_missing_commit_is_untouched(self):
        """The new branch must not swallow the failure mode it sits in front of."""
        from core.failure_modes import classify_entry

        err = self._validated('{"status": "DONE", "commit": null}', step="TDD-GREEN")
        assert err["error_class"] == "EXECUTION_ERROR"
        entry = {"status": "ERROR", "error_output": err["output"],
                 "regression_flags": {}}
        assert classify_entry(entry)["mode_id"] == "commit_required_step_no_commit"


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
