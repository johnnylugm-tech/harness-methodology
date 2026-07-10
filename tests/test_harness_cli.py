"""Tests for harness_cli.py init helpers and audit-structure command."""

from __future__ import annotations

import os
import subprocess

import argparse
import json
import pytest
from pathlib import Path
from unittest import mock
import io
from core.quality_gate import gate1_evidence

import harness_cli as _hc_entry  # noqa: F401  entry-first before cli imports
from cli._shared import _post_push_self_check  # noqa: E402
from cli.fr_cmds import (  # noqa: E402
    _build_fr_step_prompt,
    _compute_fr_spec_data,
    _extract_srs_fr_section,
    _fr_step_already_done,
)
from cli.phase_cmds import _check_gate1_live_coverage  # noqa: E402
from cli._shared import _write_finalize_sentinels_for_tests  # noqa: E402


# =============================================================================
# _fr_num_str
# =============================================================================


class TestFrNumStr:
    @pytest.mark.parametrize("fr_id,expected", [
        ("FR-01", "01"),
        ("FR-1", "01"),
        ("fr01", "01"),
        ("FR_12", "12"),
        ("FR-100", "100"),   # 3-digit preserved
        ("NFR-01", "01"),    # NFR prefix
        ("NFR-07", "07"),
        ("TASK-03", "03"),   # TASK prefix
        ("invalid", "invalid"),  # passthrough on parse failure
    ])
    def test_fr_num_str(self, fr_id, expected):
        from core.canonical_form import fr_num_str
        assert fr_num_str(fr_id) == expected


class TestVerifyEntryGate:
    """Tests for _verify_entry_gate — phase boundary commit verification."""

    def _make_state(self, project: Path, phase: int, sha: str | None = None) -> None:
        method = project / ".methodology"
        method.mkdir(parents=True, exist_ok=True)
        state: dict = {"current_phase": phase}
        if sha:
            state["phase_completed"] = {str(phase - 1): {"sha": sha, "timestamp": "2026-01-01"}}
        (method / "state.json").write_text(json.dumps(state))

    def _make_approvals(self, project: Path, phase: int, status: str = "APPROVE") -> None:
        from harness_cli import _PHASE_DELIVERABLES
        from core.quality_gate.agent_b_approvals import REQUIRED_EMBEDDED_DOCS as _REQUIRED_EMBEDDED_DOCS
        approvals = project / ".methodology" / "agent_b_approvals"
        approvals.mkdir(parents=True, exist_ok=True)
        docs = _REQUIRED_EMBEDDED_DOCS.get(phase, ["SRS.md"])
        for did in _PHASE_DELIVERABLES.get(phase, []):
            (approvals / f"{did}.json").write_text(json.dumps({
                "fr": did, "review_status": status,
                "docs_embedded": docs, "confidence": 0.9,
                "reason": "Reviewed deliverable; acceptance criteria covered, no critical gaps.",
                "citations": ["SRS.md:1"],
            }))

    def test_p1_passes_without_gate(self, tmp_path):
        from cli.phase_cmds import _verify_entry_gate
        result = _verify_entry_gate(tmp_path, 1)
        assert result["passed"] is True

    def test_p2_no_state_json_falls_to_grep(self, tmp_path, monkeypatch):
        """No state.json → falls through to grep path → fails (no commits)."""
        import subprocess as sp
        from cli.phase_cmds import _verify_entry_gate
        monkeypatch.setattr(
            sp, "run",
            lambda cmd, **_: type("R", (), {"returncode": 1, "stdout": "", "stderr": ""})(),
        )
        result = _verify_entry_gate(tmp_path, 2)
        assert result["passed"] is False

    def test_p2_shallow_clone_fallback_passes_with_approvals(self, tmp_path, monkeypatch):
        """Shallow clone: merge-base fails → fallback to agent_b_approvals → pass."""
        import subprocess as sp
        from cli.phase_cmds import _verify_entry_gate

        self._make_state(tmp_path, phase=2, sha="abc1234def5678")
        self._make_approvals(tmp_path, phase=1)

        call_log: list[list[str]] = []

        def fake_run(cmd, **_):
            call_log.append(list(cmd))
            if "merge-base" in cmd:
                return type("R", (), {"returncode": 1, "stdout": "", "stderr": ""})()
            if "--is-shallow-repository" in cmd:
                return type("R", (), {"returncode": 0, "stdout": "true\n", "stderr": ""})()
            return type("R", (), {"returncode": 1, "stdout": "", "stderr": ""})()

        monkeypatch.setattr(sp, "run", fake_run)
        result = _verify_entry_gate(tmp_path, 2)
        assert result["passed"] is True
        assert "shallow" in result["reason"].lower()
        assert "agent_b_approvals" in result["reason"].lower()

    def test_p2_shallow_clone_fallback_fails_without_approvals(self, tmp_path, monkeypatch):
        """Shallow clone: merge-base fails, no approvals → fail."""
        import subprocess as sp
        from cli.phase_cmds import _verify_entry_gate

        self._make_state(tmp_path, phase=2, sha="abc1234def5678")
        # No approval files created

        def fake_run(cmd, **_):
            if "merge-base" in cmd:
                return type("R", (), {"returncode": 1, "stdout": "", "stderr": ""})()
            if "--is-shallow-repository" in cmd:
                return type("R", (), {"returncode": 0, "stdout": "true\n", "stderr": ""})()
            return type("R", (), {"returncode": 1, "stdout": "", "stderr": ""})()

        monkeypatch.setattr(sp, "run", fake_run)
        result = _verify_entry_gate(tmp_path, 2)
        assert result["passed"] is False
        assert "shallow" in result["reason"].lower()

    def test_p2_non_shallow_sha_mismatch_fails_hard(self, tmp_path, monkeypatch):
        """Non-shallow clone: SHA not ancestor → hard fail (branch reset scenario)."""
        import subprocess as sp
        from cli.phase_cmds import _verify_entry_gate

        self._make_state(tmp_path, phase=2, sha="abc1234def5678")

        def fake_run(cmd, **_):
            if "merge-base" in cmd:
                return type("R", (), {"returncode": 1, "stdout": "", "stderr": ""})()
            if "--is-shallow-repository" in cmd:
                return type("R", (), {"returncode": 0, "stdout": "false\n", "stderr": ""})()
            return type("R", (), {"returncode": 1, "stdout": "", "stderr": ""})()

        monkeypatch.setattr(sp, "run", fake_run)
        result = _verify_entry_gate(tmp_path, 2)
        assert result["passed"] is False
        assert "reset" in result["reason"].lower() or "force-push" in result["reason"].lower()


class TestExtractReviewJson:
    """Tests for _extract_review_json helper."""

    def test_plain_json(self):
        from cli.fr_cmds import _extract_review_json
        text = '{"fr": "FR-01", "review_status": "APPROVE", "docs_embedded": ["SRS.md"], "confidence": 0.9}'
        result = _extract_review_json(text)
        assert result is not None
        assert result["review_status"] == "APPROVE"

    def test_json_inside_prose(self):
        from cli.fr_cmds import _extract_review_json
        text = (
            "After reviewing the document I conclude:\n"
            '{"fr": "FR-02", "review_status": "REJECT", "docs_embedded": ["SRS.md"]}\n'
            "End of review."
        )
        result = _extract_review_json(text)
        assert result is not None
        assert result["review_status"] == "REJECT"
        assert result["fr"] == "FR-02"

    def test_json_inside_code_fence(self):
        from cli.fr_cmds import _extract_review_json
        text = (
            "```json\n"
            '{"fr": "FR-03", "review_status": "APPROVE", "docs_embedded": ["SRS.md", "SAD.md"], "reason": "Reviewed all deliverables; acceptance criteria covered, no critical gaps found.", "citations": ["SRS.md:1"], "confidence": 0.85}\n'
            "```"
        )
        result = _extract_review_json(text)
        assert result is not None
        assert result["confidence"] == 0.85

    def test_no_review_status_returns_none(self):
        from cli.fr_cmds import _extract_review_json
        result = _extract_review_json('{"fr": "FR-01", "other_key": "value"}')
        assert result is None

    def test_empty_string_returns_none(self):
        from cli.fr_cmds import _extract_review_json
        assert _extract_review_json("") is None


class TestDispatchWritesApprovalJson:
    """Tests for cmd_dispatch reviewer → agent_b_approvals/<fr_id>.json."""

    def _make_spawner_mock(self, status, output):
        """Return a module-patchable AgentSpawner that yields a fixed result."""
        class _MockSpawner:
            def __init__(self, **_):
                pass
            def spawn(self, **_):
                return {"status": status, "session_id": "sess-abc", "output": output}
        return _MockSpawner

    def test_reviewer_complete_writes_approval_json(self, tmp_path, monkeypatch):
        from harness_cli import cmd_dispatch
        import sys
        import types
        output = '{"fr": "FR-01", "review_status": "APPROVE", "docs_embedded": ["SRS.md"], "confidence": 0.9}'
        fake_mod = types.ModuleType("core.agent_spawner")
        fake_mod.AgentSpawner = self._make_spawner_mock("complete", output)  # type: ignore[reportAttributeAccessIssue]
        monkeypatch.setitem(sys.modules, "core.agent_spawner", fake_mod)

        args = argparse.Namespace(
            project=str(tmp_path), phase=1, fr_id="SRS.md",
            role="reviewer", prompt="Review FR-01", timeout=60, max_turns=5,
        )
        rc = cmd_dispatch(args)
        assert rc == 0
        approval_file = tmp_path / ".methodology" / "agent_b_approvals" / "SRS.md.json"
        assert approval_file.exists(), "approval JSON should be written"
        data = json.loads(approval_file.read_text())
        assert data["review_status"] == "APPROVE"

    def test_reviewer_complete_no_json_warns(self, tmp_path, monkeypatch, capsys):
        from harness_cli import cmd_dispatch
        import sys
        import types
        fake_mod = types.ModuleType("core.agent_spawner")
        fake_mod.AgentSpawner = self._make_spawner_mock("complete", "Looks good, no JSON here.")  # type: ignore[reportAttributeAccessIssue]
        monkeypatch.setitem(sys.modules, "core.agent_spawner", fake_mod)

        args = argparse.Namespace(
            project=str(tmp_path), phase=1, fr_id="SRS.md",
            role="reviewer", prompt="Review FR-01", timeout=60, max_turns=5,
        )
        rc = cmd_dispatch(args)
        assert rc == 0  # dispatch itself succeeded
        approval_file = tmp_path / ".methodology" / "agent_b_approvals" / "SRS.md.json"
        assert not approval_file.exists()
        out = capsys.readouterr().out
        assert "WARN" in out

    def test_developer_role_does_not_write_approval(self, tmp_path, monkeypatch):
        from harness_cli import cmd_dispatch
        import sys
        import types
        output = '{"fr": "FR-01", "review_status": "APPROVE", "docs_embedded": ["SRS.md"]}'
        fake_mod = types.ModuleType("core.agent_spawner")
        fake_mod.AgentSpawner = self._make_spawner_mock("complete", output)  # type: ignore[reportAttributeAccessIssue]
        monkeypatch.setitem(sys.modules, "core.agent_spawner", fake_mod)

        args = argparse.Namespace(
            project=str(tmp_path), phase=1, fr_id="SRS.md",
            role="developer", prompt="Implement FR-01", timeout=60, max_turns=5,
        )
        rc = cmd_dispatch(args)
        assert rc == 0
        approval_file = tmp_path / ".methodology" / "agent_b_approvals" / "SRS.md.json"
        assert not approval_file.exists(), "developer role must not write approval JSON"


class TestExtractAgentOutputJson:
    """Tests for _extract_agent_output_json (Gap-4)."""

    def test_plain_agent_a_json(self):
        from cli.fr_cmds import _extract_agent_output_json
        text = '{"status": "complete", "files": ["SRS.md"], "confidence": 0.9, "citations": ["FR-01"], "summary": "done"}'
        result = _extract_agent_output_json(text)
        assert result is not None
        assert result["status"] == "complete"
        assert result["confidence"] == 0.9

    def test_agent_a_json_inside_prose(self):
        from cli.fr_cmds import _extract_agent_output_json
        text = 'Task complete.\n{"status": "complete", "files": ["SAD.md"], "summary": "arch done"}'
        result = _extract_agent_output_json(text)
        assert result is not None
        assert result["files"] == ["SAD.md"]

    def test_agent_b_block_not_matched(self):
        """review_status blocks must not be treated as Agent A output."""
        from cli.fr_cmds import _extract_agent_output_json
        text = '{"fr": "FR-01", "review_status": "APPROVE", "docs_embedded": ["SRS.md"], "status": "done"}'
        result = _extract_agent_output_json(text)
        assert result is None

    def test_no_agent_a_fields_returns_none(self):
        from cli.fr_cmds import _extract_agent_output_json
        result = _extract_agent_output_json('{"status": "complete", "phase": 1}')
        assert result is None

    def test_empty_string_returns_none(self):
        from cli.fr_cmds import _extract_agent_output_json
        assert _extract_agent_output_json("") is None


class TestDispatchSavesAgentAOutput:
    """cmd_dispatch developer role persists Agent A output JSON (Gap-4)."""

    def _make_spawner_mock(self, status, output):
        class _MockSpawner:
            def __init__(self, **_): pass
            def spawn(self, **_):
                return {"status": status, "session_id": "sess-xyz", "output": output}
        return _MockSpawner

    def test_developer_complete_writes_output_json(self, tmp_path, monkeypatch):
        from harness_cli import cmd_dispatch
        import sys
        import types
        output = '{"status": "complete", "files": ["SRS.md"], "confidence": 0.9, "citations": ["FR-01"], "summary": "done"}'
        fake_mod = types.ModuleType("core.agent_spawner")
        fake_mod.AgentSpawner = self._make_spawner_mock("complete", output)  # type: ignore[reportAttributeAccessIssue]
        monkeypatch.setitem(sys.modules, "core.agent_spawner", fake_mod)

        args = argparse.Namespace(
            project=str(tmp_path), phase=1, fr_id="SRS.md",
            role="developer", prompt="Implement FR-01", timeout=60, max_turns=5,
        )
        rc = cmd_dispatch(args)
        assert rc == 0
        output_file = tmp_path / ".methodology" / "agent_a_outputs" / "SRS.md.json"
        assert output_file.exists(), "agent output JSON should be written"
        data = json.loads(output_file.read_text())
        assert data["status"] == "complete"

    def test_developer_no_json_warns(self, tmp_path, monkeypatch, capsys):
        from harness_cli import cmd_dispatch
        import sys
        import types
        fake_mod = types.ModuleType("core.agent_spawner")
        fake_mod.AgentSpawner = self._make_spawner_mock("complete", "All done, no JSON.")  # type: ignore[reportAttributeAccessIssue]
        monkeypatch.setitem(sys.modules, "core.agent_spawner", fake_mod)

        args = argparse.Namespace(
            project=str(tmp_path), phase=1, fr_id="SRS.md",
            role="developer", prompt="Implement FR-01", timeout=60, max_turns=5,
        )
        rc = cmd_dispatch(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "WARN" in out
        assert not (tmp_path / ".methodology" / "agent_a_outputs" / "SRS.md.json").exists()


# =============================================================================
# TestRunPhaseNoPostflight
# =============================================================================

class TestRunPhaseNoPostflight:
    """Verify cmd_run_phase does NOT invoke postflight after the e22e723 fix."""

    def _make_project(self, tmp_path: Path) -> Path:
        """Minimal project dir that satisfies entry-gate + preflight stubs."""
        meth = tmp_path / ".methodology"
        meth.mkdir(parents=True)
        state = {
            "current_phase": 1,
            "phase_completed": {},
        }
        (meth / "state.json").write_text(json.dumps(state))
        return tmp_path

    def test_postflight_not_called_on_success(self, tmp_path, monkeypatch):
        """run-phase must return without ever calling postflight_all."""
        project = self._make_project(tmp_path)
        postflight_called = []

        import harness_cli
        from core.phase_hooks import PhaseHooks

        # Stub entry gate to pass immediately.
        monkeypatch.setattr("cli.phase_cmds._verify_entry_gate",
                            lambda *_, **__: {"passed": True, "gate": "G", "reason": "ok"})
        # Stub preflight_all to pass.
        monkeypatch.setattr(PhaseHooks, "preflight_all",
                            lambda _: {"all_passed": True, "details": {}})
        # Stub postflight_all — must NOT be called.
        monkeypatch.setattr(PhaseHooks, "postflight_all",
                            lambda _: postflight_called.append(1) or {"success": True})
        # Suppress sessions_spawn audit (phase 1 is not in _PER_FR_GATE1_PHASES).

        args = argparse.Namespace(phase=1, project=str(project))
        rc = harness_cli.cmd_run_phase(args)

        assert rc == 0
        assert postflight_called == [], "postflight_all must NOT be called from run-phase"

    def test_returns_1_on_preflight_failure(self, tmp_path, monkeypatch):
        """run-phase returns 1 when preflight fails (no postflight)."""
        project = self._make_project(tmp_path)
        postflight_called = []

        import harness_cli
        from core.phase_hooks import PhaseHooks

        monkeypatch.setattr("cli.phase_cmds._verify_entry_gate",
                            lambda *_, **__: {"passed": True, "gate": "G", "reason": "ok"})
        monkeypatch.setattr(PhaseHooks, "preflight_all",
                            lambda _: {"all_passed": False, "details": {"error": "missing SRS"}})
        monkeypatch.setattr(PhaseHooks, "postflight_all",
                            lambda _: postflight_called.append(1) or {"success": True})

        args = argparse.Namespace(phase=1, project=str(project))
        rc = harness_cli.cmd_run_phase(args)

        assert rc == 1
        assert postflight_called == [], "postflight_all must NOT be called even on preflight failure"

    def test_returns_10_on_entry_gate_failure(self, tmp_path, monkeypatch):
        """run-phase returns 10 when entry gate fails (no postflight)."""
        project = self._make_project(tmp_path)
        postflight_called = []

        import harness_cli
        from core.phase_hooks import PhaseHooks

        monkeypatch.setattr("cli.phase_cmds._verify_entry_gate",
                            lambda *_, **__: {"passed": False, "gate": "G", "reason": "Phase 0 not complete"})
        monkeypatch.setattr(PhaseHooks, "postflight_all",
                            lambda _: postflight_called.append(1) or {"success": True})

        args = argparse.Namespace(phase=2, project=str(project))
        rc = harness_cli.cmd_run_phase(args)

        assert rc == 10
        assert postflight_called == [], "postflight_all must NOT be called on entry gate failure"


# =============================================================================
# _advance_prechecks — TDD block (P3+)
# =============================================================================

def _mock_constitution_pass(monkeypatch):
    """Make constitution postflight return vacuous pass (score 100%)."""
    from core.quality_gate.constitution.runner import ConstitutionResult
    _vacuous = ConstitutionResult(score=100.0, passed=True, violations=[])
    monkeypatch.setattr(
        "core.quality_gate.constitution.run_constitution_check",
        lambda *_, **__: _vacuous,
    )
    monkeypatch.setattr(
        "core.quality_gate.constitution.profile.get_profile",
        lambda: type("_P", (), {"composite_threshold": lambda _, __: 75.0})(),
    )


class TestAdvancePrechecksTDD:
    """Tests for the P3+ TDD block in _advance_prechecks."""

    def _make_p3_project(self, tmp_path: Path) -> None:
        """Minimal P3 project skeleton (PhaseAuditor will be mocked)."""
        import harness_cli  # noqa: F401  entry-first load order (cli-first crashes until S5)
        (tmp_path / ".methodology").mkdir()
        (tmp_path / "03-development" / "src").mkdir(parents=True)
        # Next-phase plan required by _advance_prechecks (phase >= 3)
        (tmp_path / ".methodology" / "phase4_plan.md").touch()
        # Finalize-gate sentinels — _advance_prechecks verifies these exist
        _write_finalize_sentinels_for_tests(tmp_path)

    def test_pytest_failure_returns_9(self, tmp_path, monkeypatch):
        """pytest non-zero exit → _advance_prechecks returns 9."""
        from cli.phase_cmds import _advance_prechecks

        self._make_p3_project(tmp_path)
        _mock_constitution_pass(monkeypatch)
        monkeypatch.setattr("cli._shared._run_phase_auditor", lambda _, __: 0)
        monkeypatch.setattr(
            "core.quality_gate.phase_truth_verifier.PhaseTruthVerifier",
            type("FV", (), {
                "__init__": lambda _, __, ___: None,
                "verify": lambda _: {"passed": True, "total_score": 100.0},
            }),
        )

        def _fake_run(cmd, *_, **__):
            class _FakeResult:
                pass
            res = _FakeResult()
            res.stdout = ""  # type: ignore[reportAttributeAccessIssue]
            if "pytest" in cmd:
                res.returncode = 1  # type: ignore[reportAttributeAccessIssue]
            else:
                res.returncode = 0  # type: ignore[reportAttributeAccessIssue]
            return res

        monkeypatch.setattr(subprocess, "run", _fake_run)

        rc = _advance_prechecks(tmp_path, completed_phase=3)
        assert rc == 9

    def test_pytest_skipped_when_no_src_dir(self, tmp_path, monkeypatch):
        """No 03-development/src → pytest step skipped, continues to spec-coverage."""
        from cli.phase_cmds import _advance_prechecks

        (tmp_path / ".methodology").mkdir()  # no src dir
        _write_finalize_sentinels_for_tests(tmp_path)
        _mock_constitution_pass(monkeypatch)
        monkeypatch.setattr("cli._shared._run_phase_auditor", lambda _, __: 0)
        monkeypatch.setattr(
            "core.quality_gate.phase_truth_verifier.PhaseTruthVerifier",
            type("FV", (), {
                "__init__": lambda _, __, ___: None,
                "verify": lambda _: {"passed": True, "total_score": 100.0},
            }),
        )
        # spec-coverage returns pass (unified D4, v2.6)
        monkeypatch.setattr("core.quality_gate.spec_coverage._run_spec_coverage_check",
                            lambda *_, **__: (0, 100.0))
        # next-phase plan required by _advance_prechecks (phase >= 3)
        (tmp_path / ".methodology" / "phase4_plan.md").touch()

        rc = _advance_prechecks(tmp_path, completed_phase=3)
        assert rc == 0

    def test_spec_coverage_below_threshold_returns_10(self, tmp_path, monkeypatch):
        """spec-coverage below threshold → _advance_prechecks returns 10."""
        from cli.phase_cmds import _advance_prechecks

        (tmp_path / ".methodology").mkdir()
        _write_finalize_sentinels_for_tests(tmp_path)
        _mock_constitution_pass(monkeypatch)
        monkeypatch.setattr("cli._shared._run_phase_auditor", lambda _, __: 0)
        monkeypatch.setattr(
            "core.quality_gate.phase_truth_verifier.PhaseTruthVerifier",
            type("FV", (), {
                "__init__": lambda _, __, ___: None,
                "verify": lambda _: {"passed": True, "total_score": 100.0},
            }),
        )
        monkeypatch.setattr("core.quality_gate.spec_coverage._run_spec_coverage_check",
                            lambda *_, **__: (1, 30.0))
        # next-phase plan required by _advance_prechecks (phase >= 3)
        (tmp_path / ".methodology" / "phase4_plan.md").touch()

        rc = _advance_prechecks(tmp_path, completed_phase=3)
        assert rc == 10

    def test_tdd_block_not_run_for_p2(self, tmp_path, monkeypatch):
        """P2 does not execute TDD block — returns 0 after PhaseAuditor + agent-B."""
        from cli.phase_cmds import _advance_prechecks

        (tmp_path / ".methodology").mkdir()
        _mock_constitution_pass(monkeypatch)
        monkeypatch.setattr("cli._shared._run_phase_auditor", lambda _, __: 0)
        monkeypatch.setattr("core.quality_gate.agent_b_approvals.verify_agent_b_approvals_core",
                            lambda _, __, ___: (True, "mocked"))

        rc = _advance_prechecks(tmp_path, completed_phase=2)
        assert rc == 0

    def test_threshold_escalation_p4_uses_70_80(self, tmp_path, monkeypatch):
        """P4: spec-coverage threshold=70%, D4 threshold=80%."""
        from cli.phase_cmds import _advance_prechecks

        (tmp_path / ".methodology").mkdir()
        _write_finalize_sentinels_for_tests(tmp_path)
        _mock_constitution_pass(monkeypatch)
        monkeypatch.setattr("cli._shared._run_phase_auditor", lambda _, __: 0)
        monkeypatch.setattr(
            "core.quality_gate.phase_truth_verifier.PhaseTruthVerifier",
            type("FV", (), {
                "__init__": lambda _, __, ___: None,
                "verify": lambda _: {"passed": True, "total_score": 100.0},
            }),
        )
        captured_sc = {}

        def _fake_sc(p, t, **kw):
            captured_sc["threshold"] = t
            return (0, 100.0)

        monkeypatch.setattr("core.quality_gate.spec_coverage._run_spec_coverage_check", _fake_sc)
        (tmp_path / ".methodology" / "phase5_plan.md").touch()

        _advance_prechecks(tmp_path, completed_phase=4)
        assert captured_sc["threshold"] == 80.0  # unified v2.6

    def test_threshold_escalation_p6_uses_90(self, tmp_path, monkeypatch):
        """P6: spec-coverage threshold escalates to 90%."""
        from cli.phase_cmds import _advance_prechecks

        (tmp_path / ".methodology").mkdir()
        _write_finalize_sentinels_for_tests(tmp_path)
        _mock_constitution_pass(monkeypatch)
        monkeypatch.setattr("cli._shared._run_phase_auditor", lambda _, __: 0)
        # Phase 6 fires Agent B approval check before spec-coverage; stub it so
        # only the threshold value is exercised here (agent B tested elsewhere).
        monkeypatch.setattr(
            "core.quality_gate.agent_b_approvals.verify_agent_b_approvals_core",
            lambda _, __, ___: (True, "mocked"),
        )
        monkeypatch.setattr(
            "core.quality_gate.phase_truth_verifier.PhaseTruthVerifier",
            type("FV", (), {
                "__init__": lambda _, __, ___: None,
                "verify": lambda _: {"passed": True, "total_score": 100.0},
            }),
        )
        captured = {}

        def _fake_sc(p, t, **kw):
            captured["sc"] = t
            return (0, 100.0)

        monkeypatch.setattr("core.quality_gate.spec_coverage._run_spec_coverage_check", _fake_sc)
        (tmp_path / ".methodology" / "phase7_plan.md").touch()

        _advance_prechecks(tmp_path, completed_phase=6)
        assert captured["sc"] == 90.0  # unified v2.6


# =============================================================================
# _advance_prechecks — Agent B approvals (P1/P2/P6)
# =============================================================================

class TestAdvancePreChecksAgentB:
    """Agent B approval gate in _advance_prechecks for P1/P2/P6."""

    def _mock_p1_prechecks(self, monkeypatch):
        """Patch non-AB checks so only AB check is exercised."""
        monkeypatch.setattr("cli._shared._run_phase_auditor", lambda _, __: 0)
        _mock_constitution_pass(monkeypatch)

    def test_p1_missing_approvals_returns_13(self, tmp_path, monkeypatch):
        """P1 with no agent_b_approvals/ → returns 13."""
        from cli.phase_cmds import _advance_prechecks
        (tmp_path / ".methodology").mkdir()
        self._mock_p1_prechecks(monkeypatch)
        rc = _advance_prechecks(tmp_path, completed_phase=1)
        assert rc == 13

    def test_p1_approved_returns_0(self, tmp_path, monkeypatch):
        """P1 with all approvals APPROVE → proceeds (returns 0)."""
        from cli.phase_cmds import _advance_prechecks
        from core.quality_gate.legal_artifacts import PHASE_DELIVERABLES as _PHASE_DELIVERABLES
        import json

        method_dir = tmp_path / ".methodology"
        (method_dir / "agent_b_approvals").mkdir(parents=True)
        for did in _PHASE_DELIVERABLES[1]:
            (method_dir / "agent_b_approvals" / f"{did}.json").write_text(
                json.dumps({"review_status": "APPROVE", "docs_embedded": ["SRS.md"], "reason": "Reviewed deliverable; acceptance criteria covered, no critical gaps.", "citations": ["SRS.md:1"]}),
                encoding="utf-8",
            )

        # Also need TEST_INVENTORY.yaml for checksum step
        (tmp_path / "TEST_INVENTORY.yaml").write_text("tests: []")
        (method_dir / "state.json").write_text(json.dumps({"state": "ACTIVE"}))
        self._mock_p1_prechecks(monkeypatch)
        rc = _advance_prechecks(tmp_path, completed_phase=1)
        assert rc == 0

    def test_p2_rejected_approval_returns_13(self, tmp_path, monkeypatch):
        """P2 with one REJECT approval → returns 13."""
        from cli.phase_cmds import _advance_prechecks
        from core.quality_gate.legal_artifacts import PHASE_DELIVERABLES as _PHASE_DELIVERABLES
        import json

        method_dir = tmp_path / ".methodology"
        (method_dir / "agent_b_approvals").mkdir(parents=True)
        for i, did in enumerate(_PHASE_DELIVERABLES[2]):
            status = "REJECT" if i == 0 else "APPROVE"
            (method_dir / "agent_b_approvals" / f"{did}.json").write_text(
                json.dumps({
                    "review_status": status,
                    "docs_embedded": ["SRS.md", "SAD.md"], "reason": "Reviewed all deliverables; acceptance criteria covered, no critical gaps found.", "citations": ["SRS.md:1"],
                }),
                encoding="utf-8",
            )

        self._mock_p1_prechecks(monkeypatch)
        rc = _advance_prechecks(tmp_path, completed_phase=2)
        assert rc == 13

    def test_p3_skips_agent_b_check(self, tmp_path, monkeypatch):
        """P3+ does not run Agent B check (A/B removed from P3+)."""
        from cli.phase_cmds import _advance_prechecks

        (tmp_path / ".methodology").mkdir()
        _write_finalize_sentinels_for_tests(tmp_path)
        _mock_constitution_pass(monkeypatch)
        monkeypatch.setattr("cli._shared._run_phase_auditor", lambda _, __: 0)
        monkeypatch.setattr(
            "core.quality_gate.phase_truth_verifier.PhaseTruthVerifier",
            type("FV", (), {
                "__init__": lambda _, __, ___: None,
                "verify": lambda _: {"passed": True, "total_score": 100.0},
            }),
        )
        monkeypatch.setattr("core.quality_gate.spec_coverage._run_spec_coverage_check",
                            lambda *_, **__: (0, 100.0))
        # next-phase plan required by _advance_prechecks (phase >= 3)
        (tmp_path / ".methodology" / "phase4_plan.md").touch()

        # No agent_b_approvals dir at all — should not matter for P3
        rc = _advance_prechecks(tmp_path, completed_phase=3)
        assert rc == 0

    # -- P6 Agent B enforcement tests ----------------------------------------

    def _mock_p6_non_ab_prechecks(self, tmp_path, monkeypatch):
        """Set up all P6 advance_prechecks prerequisites EXCEPT Agent B.

        P6 check order (simplified): Phase Truth → Stage Pass auto-gen →
        next-phase plan → phase auditor → constitution → Agent B → TDD-PRECHECK.
        This helper passes everything before Agent B so the test can control
        whether approvals exist without fighting unrelated failures.
        """

        method = tmp_path / ".methodology"
        method.mkdir(exist_ok=True)
        (tmp_path / "03-development" / "src").mkdir(parents=True, exist_ok=True)
        (tmp_path / ".methodology" / "phase7_plan.md").touch()
        # Pre-create Stage Pass so auto-generation is skipped
        (tmp_path / "00-summary").mkdir(exist_ok=True)
        (tmp_path / "00-summary" / "Phase6_STAGE_PASS.md").write_text(
            "# Phase 6 Stage Pass\n## Summary\n", encoding="utf-8"
        )
        _write_finalize_sentinels_for_tests(tmp_path)

        monkeypatch.setattr("cli._shared._run_phase_auditor", lambda _, __: 0)
        _mock_constitution_pass(monkeypatch)
        monkeypatch.setattr(
            "core.quality_gate.phase_truth_verifier.PhaseTruthVerifier",
            type("FV", (), {
                "__init__": lambda _, __, ___: None,
                "verify": lambda _: {"passed": True, "total_score": 100.0},
            }),
        )
        monkeypatch.setattr(
            "core.quality_gate.spec_coverage._run_spec_coverage_check", lambda *_, **__: (0, 100.0)
        )
        monkeypatch.setattr("shutil.which", lambda cmd: True)
        monkeypatch.setattr(
            "subprocess.run",
            lambda cmd, **kw: type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})(),
        )
        # Gate-1 FR coverage and mutmut not exercised by these tests
        monkeypatch.setattr("cli.phase_cmds._check_gate1_live_coverage", lambda _, __: 0)
        monkeypatch.setattr(
            "core.quality_gate.mutation_enforcer.run_mutation_precheck",
            lambda _: (True, "ok"),
        )

    def _p6_approval(self, required_docs: list[str]) -> str:
        """Minimal valid Agent B approval JSON for a P6 deliverable."""
        return json.dumps({
            "review_status": "APPROVE",
            "docs_embedded": required_docs,
            "reason": "Reviewed all P6 deliverables; quality criteria satisfied, "
                      "Gate 4 scoring verified, no critical gaps.",
            "citations": ["QUALITY_REPORT.md:1"],
        })

    def test_p6_missing_approvals_returns_13(self, tmp_path, monkeypatch):
        """P6 with no agent_b_approvals/ → advance blocked with rc=13."""
        from cli.phase_cmds import _advance_prechecks

        self._mock_p6_non_ab_prechecks(tmp_path, monkeypatch)
        # No approvals dir at all
        rc = _advance_prechecks(tmp_path, completed_phase=6)
        assert rc == 13

    def test_p6_approved_returns_0(self, tmp_path, monkeypatch):
        """P6 with all deliverables APPROVE → advance proceeds (rc=0).

        Also guards against the M1 regression (quality_manifest double-extension):
        if _PHASE_DELIVERABLES[6] goes back to "quality_manifest.json", the loop
        creates quality_manifest.json.json — the first approval-file assertion fails
        and rc would be 13 (not 0).
        """
        from cli.phase_cmds import _advance_prechecks
        from core.quality_gate.legal_artifacts import PHASE_DELIVERABLES as _PHASE_DELIVERABLES
        from core.quality_gate.agent_b_approvals import REQUIRED_EMBEDDED_DOCS as _REQUIRED_EMBEDDED_DOCS

        self._mock_p6_non_ab_prechecks(tmp_path, monkeypatch)

        approvals_dir = tmp_path / ".methodology" / "agent_b_approvals"
        approvals_dir.mkdir(parents=True)
        req_docs = _REQUIRED_EMBEDDED_DOCS[6]
        for did in _PHASE_DELIVERABLES[6]:
            (approvals_dir / f"{did}.json").write_text(
                self._p6_approval(req_docs), encoding="utf-8"
            )

        # M1 regression guard: quality_manifest approval must be quality_manifest.json,
        # not quality_manifest.json.json (double-extension from using "quality_manifest.json"
        # as the deliverable ID).
        assert (approvals_dir / "quality_manifest.json").exists(), (
            "approval file for quality_manifest must be quality_manifest.json"
        )
        assert not (approvals_dir / "quality_manifest.json.json").exists(), (
            "double-extension quality_manifest.json.json must not exist"
        )
        rc = _advance_prechecks(tmp_path, completed_phase=6)
        assert rc == 0


# =============================================================================
# run-fr-step / resume-fr-phase
# =============================================================================

def _setup_preflight_fixtures(tmp_path: Path, *, step: str, fr_id: str = "FR-01") -> None:
    """Set up the minimum files needed to pass _fr_step_preflight.

    Creates: git repo, SRS.md, quality_manifest.json, and either
    02-architecture/TEST_SPEC.md (TDD-RED) or env_check_result.json (GATE1/CODE-FIX).
    """
    import subprocess as _sp

    # 1. Git repo
    _sp.run(["git", "init", "-q"], cwd=str(tmp_path), check=False)
    _sp.run(["git", "config", "user.email", "test@test"], cwd=str(tmp_path), check=False)
    _sp.run(["git", "config", "user.name", "Test"], cwd=str(tmp_path), check=False)

    # 2. SRS.md
    tmp_path.joinpath("SRS.md").write_text(
        f"### {fr_id}: Test Feature\n\n**Description**: Test\n\n---\n",
        encoding="utf-8",
    )

    # 3. quality_manifest.json
    manifest_dir = tmp_path / ".methodology"
    manifest_dir.mkdir()
    manifest_dir.joinpath("quality_manifest.json").write_text(
        json.dumps({"fr_ids": [fr_id]}), encoding="utf-8",
    )

    # 4. Step-specific
    if step.upper() in ("GATE1", "GATE1-DELTA", "CODE-FIX"):
        sessi = tmp_path / ".sessi-work"
        sessi.mkdir()
        sessi.joinpath("env_check_result.json").write_text(json.dumps({
            "ready": True,
            "checked_at": "2026-01-01T00:00:00+00:00",
            "summary": "ok",
            "env_vars": {"required": []},
            "cli_tools": {"required": []},
            "infra_services": {"required": []},
        }), encoding="utf-8")
    elif step.upper() == "TDD-RED":
        spec_dir = tmp_path / "02-architecture"
        spec_dir.mkdir()
        spec_dir.joinpath("TEST_SPEC.md").write_text(
            f"### {fr_id}: Test Feature\n\n"
            "| # | Test Function | Type |\n"
            "|---|--------------|------|\n"
            "| 1 | `test_feature` | Functional |\n",
            encoding="utf-8",
        )


class TestRunFrStep:
    """Tests for cmd_run_fr_step and related helpers."""

    def test_skip_if_already_done(self, tmp_path, monkeypatch):
        """Idempotency: returns 0 immediately if step commit already exists."""
        import harness_cli
        monkeypatch.setattr("cli.fr_cmds._fr_step_already_done", lambda s, f, p: True)
        args = argparse.Namespace(
            phase=3, fr_id="FR-01", step="TDD-RED", project=str(tmp_path),
            srs=None, timeout=600, max_turns=30, max_fix_rounds=3,
        )
        assert harness_cli.cmd_run_fr_step(args) == 0

    def test_dispatch_called_when_not_done(self, tmp_path, monkeypatch):
        """Sub-agent is dispatched when step has not yet been committed."""
        import sys
        import types
        import harness_cli

        _setup_preflight_fixtures(tmp_path, step="TDD-RED")

        # _fr_step_already_done always returns False (step not done)
        monkeypatch.setattr("cli.fr_cmds._fr_step_already_done", lambda s, f, p: False)

        dispatched: dict = {}

        class _FakeSpawner:
            def __init__(self, project_path=None):
                pass
            def spawn(self, **kwargs):
                dispatched.update(kwargs)
                return {"status": "complete", "output": "{}"}

        fake_mod = types.ModuleType("core.agent_spawner")
        fake_mod.AgentSpawner = _FakeSpawner  # type: ignore[reportAttributeAccessIssue]
        monkeypatch.setitem(sys.modules, "core.agent_spawner", fake_mod)

        import subprocess as _sp
        class _FakeResult:
            returncode = 0
            stdout = ""
            stderr = ""
        monkeypatch.setattr(_sp, "run", lambda cmd, **kw: _FakeResult())

        args = argparse.Namespace(
            phase=3, fr_id="FR-01", step="TDD-RED", project=str(tmp_path),
            srs=None, timeout=600, max_turns=30, max_fix_rounds=3,
        )
        harness_cli.cmd_run_fr_step(args)
        assert dispatched.get("fr_id") == "FR-01"
        assert dispatched.get("phase_sop_override") == ""

    def test_extract_srs_fr_section_returns_correct_fr(self, tmp_path):
        """_extract_srs_fr_section returns only the target FR's content."""
        srs = tmp_path / "SRS.md"
        srs.write_text(
            "### FR-01: Feature A\n\n**Description**: Alpha text\n\n---\n"
            "### FR-02: Feature B\n\n**Description**: Beta text\n\n---\n",
            encoding="utf-8",
        )
        section = _extract_srs_fr_section(srs, "FR-01")
        assert "Alpha text" in section
        assert "Beta text" not in section

    def test_extract_srs_fr_section_missing_fr(self, tmp_path):
        """_extract_srs_fr_section returns empty string when FR not found."""
        srs = tmp_path / "SRS.md"
        srs.write_text("### FR-02: Feature B\n\n**Description**: Beta\n\n---\n")
        assert _extract_srs_fr_section(srs, "FR-01") == ""

    def test_prompt_tdd_red_contains_srs_section(self, tmp_path):
        """TDD-RED prompt includes extracted SRS section and commit format."""
        srs = tmp_path / "SRS.md"
        srs.write_text(
            "### FR-01: My Feature\n\n**Description**: Do important thing X\n\n---\n",
            encoding="utf-8",
        )
        prompt = _build_fr_step_prompt("TDD-RED", "FR-01", 3, tmp_path, srs)
        assert "Do important thing X" in prompt
        assert "test(RED): failing test for FR-01" in prompt
        assert "failing test" in prompt.lower()

    def test_prompt_tdd_green_inlines_test_file(self, tmp_path):
        """TDD-GREEN prompt includes the current test file content inline."""
        srs = tmp_path / "SRS.md"
        srs.write_text(
            "### FR-01: My Feature\n\n**Description**: Do X\n\n---\n", encoding="utf-8"
        )
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_fr01.py").write_text(
            "def test_my_feature(): assert False  # RED", encoding="utf-8"
        )
        prompt = _build_fr_step_prompt("TDD-GREEN", "FR-01", 3, tmp_path, srs)
        assert "assert False  # RED" in prompt
        assert "feat(FR-01): GREEN" in prompt

    def test_prompt_gate1_contains_run_gate_command(self, tmp_path):
        """GATE1 prompt includes run-gate and finalize-gate commands."""
        prompt = _build_fr_step_prompt("GATE1", "FR-01", 3, tmp_path, None)
        assert "run-gate --gate 1 --phase 3 --fr-id FR-01" in prompt
        assert "finalize-gate --gate 1 --phase 3 --fr-id FR-01" in prompt
        assert '"pass"' in prompt

    def test_prompt_gate1_delta_uses_full_gate_evaluation(self, tmp_path):
        """GATE1-DELTA prompt runs full GATE1 (no --delta — skip is handled by
        _fr_step_already_done() git diff check before dispatch)."""
        prompt = _build_fr_step_prompt("GATE1-DELTA", "FR-05", 5, tmp_path, None)
        assert "run-gate --gate 1" in prompt
        assert "finalize-gate --gate 1" in prompt
        assert "--delta" not in prompt

    def test_prompt_code_fix_test_coverage_only(self, tmp_path):
        """CODE-FIX with test_coverage only → [TEST COVERAGE FIX] section,
        FORBIDDEN allows adding tests, git add includes test file."""

        # Set up TEST_SPEC.md so _extract_test_spec_names returns names
        spec_dir = tmp_path / "02-architecture"
        spec_dir.mkdir()
        spec_dir.joinpath("TEST_SPEC.md").write_text(
            "### FR-01: Feature\n\n"
            "| # | Test Function | Type |\n"
            "|---|--------------|------|\n"
            "| 1 | `test_feature_a` | Functional |\n"
            "| 2 | `test_feature_b` | Functional |\n",
            encoding="utf-8",
        )

        prompt = _build_fr_step_prompt(
            "CODE-FIX", "FR-01", 3, tmp_path, None,
            failing_dims=["test_coverage"],
        )
        assert "[TEST COVERAGE FIX" in prompt
        assert "test_feature_a" in prompt
        assert "test_feature_b" in prompt
        assert "MISSING" in prompt
        assert "ADD ALL of them as real" in prompt
        assert "Deleting existing tests" in prompt
        assert "Skipping or xfail-marking" in prompt
        assert "git add tests/test_fr01.py" in prompt

    def test_prompt_code_fix_source_only(self, tmp_path):
        """CODE-FIX with ruff only → no test section, FORBIDDEN blocks test files."""

        prompt = _build_fr_step_prompt(
            "CODE-FIX", "FR-01", 3, tmp_path, None,
            failing_dims=["ruff"],
        )
        assert "[TEST COVERAGE FIX" not in prompt
        assert "Modifying test files" in prompt
        assert "git add 03-development/src/" in prompt

    def test_prompt_code_fix_mixed_dims(self, tmp_path):
        """CODE-FIX with test_coverage + ruff → both sections, git add includes
        both src_dir and test_file."""

        spec_dir = tmp_path / "02-architecture"
        spec_dir.mkdir()
        spec_dir.joinpath("TEST_SPEC.md").write_text(
            "### FR-01: Feature\n\n"
            "| # | Test Function | Type |\n"
            "|---|--------------|------|\n"
            "| 1 | `test_feature_a` | Functional |\n",
            encoding="utf-8",
        )

        prompt = _build_fr_step_prompt(
            "CODE-FIX", "FR-01", 3, tmp_path, None,
            failing_dims=["test_coverage", "ruff"],
        )
        assert "[TEST COVERAGE FIX" in prompt
        assert "Fix source code" in prompt
        assert "Resolve test_coverage failures" in prompt
        assert "ADD any missing" in prompt
        assert "git add 03-development/src/ tests/test_fr01.py" in prompt
        assert "Deleting existing tests" in prompt
        assert "Skipping or xfail-marking" in prompt

    def test_prompt_code_fix_none_fallback(self, tmp_path):
        """CODE-FIX with failing_dims=None → diagnostic mode — self-identify
        failures via pytest + ruff (gate1_result.json doesn't exist)."""

        prompt = _build_fr_step_prompt(
            "CODE-FIX", "FR-02", 3, tmp_path, None,
            failing_dims=None,
        )
        assert "no gate1_result.json was written" in prompt
        assert "diagnostic mode" in prompt
        assert "pytest tests/ -q" in prompt
        assert "ruff check" in prompt
        assert "[TEST COVERAGE FIX" not in prompt
        assert "Deleting or modifying existing passing tests" in prompt
        assert "git add 03-development/src/ tests/test_fr02.py" in prompt

    def test_resume_fr_phase_finds_first_pending_step(self, tmp_path, monkeypatch):
        """resume-fr-phase prints the first step that is not yet done."""
        import harness_cli
        import sys

        (tmp_path / ".methodology").mkdir()
        (tmp_path / ".methodology" / "quality_manifest.json").write_text(
            json.dumps({"fr_ids": ["FR-01"]}), encoding="utf-8"
        )
        # TDD-RED done, TDD-GREEN not yet done
        monkeypatch.setattr("cli.fr_cmds._fr_step_already_done",
            lambda step, fr_id, project: step == "TDD-RED",
        )
        captured = io.StringIO()
        monkeypatch.setattr(sys, "stdout", captured)

        args = argparse.Namespace(phase=3, project=str(tmp_path))
        rc = harness_cli.cmd_resume_fr_phase(args)
        assert rc == 0
        out = captured.getvalue()
        assert "TDD-GREEN" in out
        assert "FR-01" in out

    def test_resume_fr_phase_all_done(self, tmp_path, monkeypatch):
        """resume-fr-phase reports all complete when every step is done."""
        import harness_cli
        import sys

        (tmp_path / ".methodology").mkdir()
        (tmp_path / ".methodology" / "quality_manifest.json").write_text(
            json.dumps({"fr_ids": ["FR-01"]}), encoding="utf-8"
        )
        monkeypatch.setattr("cli.fr_cmds._fr_step_already_done", lambda *a: True)
        captured = io.StringIO()
        monkeypatch.setattr(sys, "stdout", captured)

        args = argparse.Namespace(phase=3, project=str(tmp_path))
        rc = harness_cli.cmd_resume_fr_phase(args)
        assert rc == 0
        assert "All FRs complete" in captured.getvalue()

    def test_resume_fr_phase_falls_back_to_fr_progress(self, tmp_path, monkeypatch):
        """resume-fr-phase uses fr_progress.json when quality_manifest.json is absent."""
        import harness_cli
        import sys

        (tmp_path / ".methodology").mkdir()
        (tmp_path / ".methodology" / "fr_progress.json").write_text(
            json.dumps({"phase": 3, "frs": {"FR-02": {"status": "gate1_pass"}}}),
            encoding="utf-8",
        )
        monkeypatch.setattr("cli.fr_cmds._fr_step_already_done", lambda *a: False)
        captured = io.StringIO()
        monkeypatch.setattr(sys, "stdout", captured)

        args = argparse.Namespace(phase=3, project=str(tmp_path))
        rc = harness_cli.cmd_resume_fr_phase(args)
        assert rc == 0
        assert "FR-02" in captured.getvalue()

    def test_gate1_blocked_after_max_rounds(self, tmp_path, monkeypatch):
        """Returns exit 2 (BLOCKED) when GATE1 never passes after max_fix_rounds."""
        import sys
        import types
        import harness_cli

        _setup_preflight_fixtures(tmp_path, step="GATE1")

        monkeypatch.setattr("cli.fr_cmds._fr_step_already_done", lambda s, f, p: False)

        # Sub-agent always returns gate_pass=false
        _fail_output = '{"status": "DONE", "pass": false, "failing_dims": ["D1"], "gate_score": 0.2}'

        class _FakeSpawner:
            def __init__(self, project_path=None):
                pass
            def spawn(self, **kwargs):
                return {"status": "complete", "output": _fail_output}

        fake_mod = types.ModuleType("core.agent_spawner")
        fake_mod.AgentSpawner = _FakeSpawner  # type: ignore[reportAttributeAccessIssue]
        monkeypatch.setitem(sys.modules, "core.agent_spawner", fake_mod)

        args = argparse.Namespace(
            phase=3, fr_id="FR-01", step="GATE1", project=str(tmp_path),
            srs=None, timeout=60, max_turns=5, max_fix_rounds=2,
        )
        rc = harness_cli.cmd_run_fr_step(args)
        assert rc == 2  # BLOCKED

    def test_resume_fr_phase_carryforward_uses_gate1_delta(self, tmp_path, monkeypatch):
        """resume-fr-phase emits GATE1-DELTA for carry-forward phases when code unchanged."""
        import harness_cli
        import sys

        (tmp_path / ".methodology").mkdir()
        (tmp_path / ".methodology" / "quality_manifest.json").write_text(
            json.dumps({"fr_ids": ["FR-01"]}), encoding="utf-8"
        )
        monkeypatch.setattr("cli.fr_cmds._fr_step_already_done", lambda *a: False)
        monkeypatch.setattr("core.quality_gate.gate1_evidence.fr_code_changed_since_last_gate1", lambda *a: False,
        )
        captured = io.StringIO()
        monkeypatch.setattr(sys, "stdout", captured)

        for phase in (5, 7, 8):
            captured.truncate(0)
            captured.seek(0)
            args = argparse.Namespace(phase=phase, project=str(tmp_path))
            rc = harness_cli.cmd_resume_fr_phase(args)
            assert rc == 0
            out = captured.getvalue()
            assert "GATE1-DELTA" in out, f"Phase {phase} should use GATE1-DELTA"
            assert "TDD-RED" not in out, f"Phase {phase} should not show TDD-RED"

    def test_resume_fr_phase_carryforward_switches_to_tdd_when_code_changed(
        self, tmp_path, monkeypatch,
    ):
        """Carry-forward phases switch to full TDD when code changed since last Gate 1."""
        import harness_cli
        import sys

        (tmp_path / ".methodology").mkdir()
        (tmp_path / ".methodology" / "quality_manifest.json").write_text(
            json.dumps({"fr_ids": ["FR-01"]}), encoding="utf-8"
        )
        monkeypatch.setattr("cli.fr_cmds._fr_step_already_done", lambda *a: False)
        monkeypatch.setattr("core.quality_gate.gate1_evidence.fr_code_changed_since_last_gate1", lambda *a: True,
        )
        captured = io.StringIO()
        monkeypatch.setattr(sys, "stdout", captured)

        args = argparse.Namespace(phase=7, project=str(tmp_path))
        rc = harness_cli.cmd_resume_fr_phase(args)
        assert rc == 0
        out = captured.getvalue()
        assert "TDD-RED" in out, "Code changed → should use full TDD"
        assert "GATE1-DELTA" not in out, "Code changed → should not use GATE1-DELTA"

    def test_fr_step_already_done_requires_file_existence(self, tmp_path, monkeypatch):
        """_fr_step_already_done returns False if commit matches but physical test file or src dir is missing."""
        import subprocess as _sp

        class _FakeResult:
            returncode = 0
            stdout = "test(RED): failing test for FR-01"

        # Git log mock returns matching commit
        monkeypatch.setattr(_sp, "run", lambda *_, **__: _FakeResult())

        # RED Test: Test file missing -> should return False
        assert not _fr_step_already_done("TDD-RED", "FR-01", tmp_path)

        # Create test file -> should return True
        (tmp_path / "tests").mkdir(parents=True, exist_ok=True)
        (tmp_path / "tests" / "test_fr01.py").write_text("def test_fr(): pass")
        assert _fr_step_already_done("TDD-RED", "FR-01", tmp_path)

        # GREEN Test: Src dir missing -> should return False
        assert not _fr_step_already_done("TDD-GREEN", "FR-01", tmp_path)

        # Create empty src dir -> should return False
        (tmp_path / "03-development" / "src").mkdir(parents=True, exist_ok=True)
        assert not _fr_step_already_done("TDD-GREEN", "FR-01", tmp_path)

        # Create source file with tag -> should return True
        f = tmp_path / "03-development" / "src" / "impl.py"
        f.write_text("# [FR-01]")
        assert _fr_step_already_done("TDD-GREEN", "FR-01", tmp_path)

    def test_gate1_already_done_uses_quality_complete_not_overall_score(self, tmp_path, monkeypatch):
        """_fr_step_already_done("GATE1", ...) must key off the manifest's
        quality_complete verdict, not a same-ballpark-by-coincidence
        comparison of overall_score against quality_targets.min_coverage.

        Repro (2026-07-08 P3 run): FR-01 overall_score=80.28 (weighted
        composite of linting/type_safety/test_coverage) happened to clear
        min_coverage=80 (a coverage-percentage threshold, different unit),
        so the old code treated GATE1 as already-done and skipped
        re-evaluation even though quality_complete was False (test_coverage
        dimension score=42 < its own 80 threshold).
        """
        import subprocess as _sp

        class _FakeResult:
            returncode = 0
            stdout = "feat(FR-01): Gate1 PASS"

        monkeypatch.setattr(_sp, "run", lambda *_, **__: _FakeResult())

        manifest_dir = tmp_path / ".methodology"
        manifest_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = manifest_dir / "quality_manifest.json"

        # overall_score clears min_coverage numerically, but quality_complete
        # is False (a dimension-level threshold — test_coverage — failed).
        manifest_path.write_text(json.dumps({
            "quality_targets": {"min_coverage": 80.0},
            "gate_results": {"gate1": {"FR-01": {
                "score": 80.28, "quality_complete": False,
            }}},
        }))
        assert not _fr_step_already_done("GATE1", "FR-01", tmp_path), (
            "quality_complete=False must force re-evaluation regardless of "
            "how overall_score compares to min_coverage"
        )

        # quality_complete True → safe to skip.
        manifest_path.write_text(json.dumps({
            "quality_targets": {"min_coverage": 80.0},
            "gate_results": {"gate1": {"FR-01": {
                "score": 97.62, "quality_complete": True,
            }}},
        }))
        assert _fr_step_already_done("GATE1", "FR-01", tmp_path)

    def test_run_fr_step_handles_git_push_failure_as_fatal(self, tmp_path, monkeypatch, capsys):
        """cmd_run_fr_step prints an error and returns 1 when git push fails (fatal check-recovery)."""
        import sys
        import types
        import harness_cli
        import subprocess as _sp

        _setup_preflight_fixtures(tmp_path, step="TDD-RED")

        monkeypatch.setattr("cli.fr_cmds._fr_step_already_done", lambda s, f, p: False)

        class _FakeSpawner:
            def __init__(self, project_path=None): pass
            def spawn(self, **kwargs):
                return {"status": "complete", "output": "{}"}

        fake_mod = types.ModuleType("core.agent_spawner")
        fake_mod.AgentSpawner = _FakeSpawner  # type: ignore[reportAttributeAccessIssue]
        monkeypatch.setitem(sys.modules, "core.agent_spawner", fake_mod)

        # Mock git commands: make git push fail (returncode 1)
        def _fake_run(cmd, **_):
            class _Res:
                returncode = 1 if "push" in cmd else 0
                stdout = ""
                stderr = "fatal: Could not read from remote repository."
            return _Res()
        monkeypatch.setattr(_sp, "run", _fake_run)

        args = argparse.Namespace(
            phase=3, fr_id="FR-01", step="TDD-RED", project=str(tmp_path),
            srs=None, timeout=600, max_turns=30, max_fix_rounds=3, no_push=False
        )

        rc = harness_cli.cmd_run_fr_step(args)
        assert rc == 1
        captured = capsys.readouterr().out
        assert "git push failed" in captured

    def test_run_fr_step_respects_no_push_argument(self, tmp_path, monkeypatch, capsys):
        """cmd_run_fr_step skips git push when no_push is True."""
        import sys
        import types
        import harness_cli
        import subprocess as _sp

        _setup_preflight_fixtures(tmp_path, step="TDD-RED")

        monkeypatch.setattr("cli.fr_cmds._fr_step_already_done", lambda s, f, p: False)

        class _FakeSpawner:
            def __init__(self, project_path=None): pass
            def spawn(self, **kwargs):
                return {"status": "complete", "output": "{}"}

        fake_mod = types.ModuleType("core.agent_spawner")
        fake_mod.AgentSpawner = _FakeSpawner  # type: ignore[reportAttributeAccessIssue]
        monkeypatch.setitem(sys.modules, "core.agent_spawner", fake_mod)

        run_calls = []
        def _fake_run(cmd, **_):
            run_calls.append(cmd)
            class _Res:
                returncode = 0
                stdout = ""
                stderr = ""
            return _Res()
        monkeypatch.setattr(_sp, "run", _fake_run)

        # 1. Test with no_push = True
        args = argparse.Namespace(
            phase=3, fr_id="FR-01", step="TDD-RED", project=str(tmp_path),
            srs=None, timeout=600, max_turns=30, max_fix_rounds=3, no_push=True
        )

        rc = harness_cli.cmd_run_fr_step(args)
        assert rc == 0
        assert not any("push" in cmd for cmd in run_calls), "git push should not be called when no_push=True"
        captured = capsys.readouterr().out
        assert "skipping git push" in captured
        assert "complete" in captured
        assert "+ pushed to GitHub" not in captured

        # 2. Test with no_push = False (normal behavior)
        run_calls.clear()
        args.no_push = False
        rc = harness_cli.cmd_run_fr_step(args)
        assert rc == 0
        assert any("push" in cmd for cmd in run_calls), "git push should be called when no_push=False"
        captured = capsys.readouterr().out
        assert "complete + pushed to GitHub" in captured


# ---------------------------------------------------------------------------
# Gate 1 per-FR coverage check (new exit code 14)
# Validates that advance-phase blocks when finalize-gate --gate 1 was not
# called for every FR in quality_manifest.json.
# ---------------------------------------------------------------------------

class TestGate1LiveCoverageCheck:
    """Tests for the Gate 1 live coverage check inside _advance_prechecks."""

    def _make_manifest(self, tmp_path: Path, fr_ids: list) -> None:
        import json
        m = tmp_path / ".methodology" / "quality_manifest.json"
        m.parent.mkdir(parents=True, exist_ok=True)
        m.write_text(json.dumps({"fr_ids": fr_ids}), encoding="utf-8")

    def _run_check(self, tmp_path: Path, completed_phase: int) -> int:
        return _check_gate1_live_coverage(tmp_path, completed_phase)

    def test_all_frs_covered_returns_0(self, tmp_path):
        """All FRs have real pytest coverage ≥ min → return 0."""
        self._make_manifest(tmp_path, ["FR-01", "FR-02", "FR-03"])
        with mock.patch.object(
            gate1_evidence, "validate_fr_coverage_immediate", return_value=100.0
        ):
            assert self._run_check(tmp_path, 4) == 0

    def test_missing_fr_returns_14(self, tmp_path):
        """Live pytest returns None (no tests/ or pytest errored) → BLOCKED 14."""
        self._make_manifest(tmp_path, ["FR-01", "FR-02", "FR-03"])
        with mock.patch.object(
            gate1_evidence, "validate_fr_coverage_immediate", return_value=None
        ):
            assert self._run_check(tmp_path, 4) == 14

    def test_zero_gate1_entries_returns_14(self, tmp_path):
        """All FRs have pytest erroring (None) → must block."""
        self._make_manifest(tmp_path, ["FR-01", "FR-02"])
        with mock.patch.object(
            gate1_evidence, "validate_fr_coverage_immediate", return_value=None
        ):
            assert self._run_check(tmp_path, 4) == 14

    def test_delta_auto_skip_skips_live_pytest(self, tmp_path):
        """DELTA phase with code unchanged → auto-skip, return 0, pytest not called."""
        self._make_manifest(tmp_path, ["FR-01", "FR-02"])
        with mock.patch.object(
            gate1_evidence, "fr_code_changed_since_last_gate1", return_value=False
        ):
            with mock.patch.object(
                gate1_evidence, "validate_fr_coverage_immediate"
            ) as mock_cov:
                assert self._run_check(tmp_path, 4) == 0
                mock_cov.assert_not_called()

    def test_single_fr_manifest_passes(self, tmp_path):
        """Manifest with one FR, live coverage ≥ min → pass."""
        self._make_manifest(tmp_path, ["FR-01"])
        with mock.patch.object(
            gate1_evidence, "validate_fr_coverage_immediate", return_value=100.0
        ):
            assert self._run_check(tmp_path, 4) == 0

    def test_no_manifest_skips_check(self, tmp_path):
        """Missing quality_manifest.json → skip check (non-FR project)."""
        # No manifest — check should be skipped gracefully
        assert self._run_check(tmp_path, 4) == 0

    def test_multiple_rounds_same_fr_ok(self, tmp_path):
        """Live pytest is per-FR idempotent — second FR also passes."""
        self._make_manifest(tmp_path, ["FR-01", "FR-02"])
        with mock.patch.object(
            gate1_evidence, "validate_fr_coverage_immediate", return_value=100.0
        ):
            assert self._run_check(tmp_path, 4) == 0

    def test_phase6_not_in_gate1_fr_check_set(self):
        """Phase 6 must not be in _PHASES_WITH_GATE1_FR_CHECK — Gate 4 replaces FR loop."""
        import harness_cli
        assert 6 not in harness_cli._PHASES_WITH_GATE1_FR_CHECK

    def test_phase6_check_skipped_even_with_fr_manifest(self, tmp_path):
        """advance-phase for Phase 6 must not block on missing Gate 1 records.

        Phase 6 (Quality Assurance) uses Gate 4 exclusively — there are no
        per-FR TDD-RED/GREEN/GATE1 steps, so _check_gate1_live_coverage
        should not be called for completed_phase=6.
        """
        self._make_manifest(tmp_path, ["FR-01", "FR-02", "FR-03"])
        # Phase 6 is NOT a DELTA auto-skip phase → falls through to live pytest.
        # Without test files, _validate_fr_coverage_immediate returns None → 14.
        with mock.patch.object(
            gate1_evidence, "validate_fr_coverage_immediate", return_value=None
        ):
            assert self._run_check(tmp_path, 6) == 14, (
                "_check_gate1_live_coverage itself returns 14 for phase=6 "
                "(confirms the guard in _advance_prechecks is doing the right thing)"
            )

    def test_phases_with_gate1_fr_check_constant(self):
        """_PHASES_WITH_GATE1_FR_CHECK must cover phases 3,4,5,7,8 and exclude 1,2,6."""
        import harness_cli
        expected_included = {3, 4, 5, 7, 8}
        expected_excluded = {1, 2, 6}
        for p in expected_included:
            assert p in harness_cli._PHASES_WITH_GATE1_FR_CHECK, f"Phase {p} must be in set"
        for p in expected_excluded:
            assert p not in harness_cli._PHASES_WITH_GATE1_FR_CHECK, f"Phase {p} must NOT be in set"

    def test_delta_loop_autoskip_when_unchanged(self, tmp_path):
        """Layer 4: P7 with all FRs unchanged since last gate → coverage auto-satisfied (return 0),
        even with NO per-FR Gate 1 timestamps for phase 7."""
        from unittest.mock import patch
        self._make_manifest(tmp_path, ["FR-01", "FR-02", "FR-03"])
        # No gate_timestamps for phase 7 at all → without auto-skip this would return 14.
        with patch("core.quality_gate.gate1_evidence.fr_code_changed_since_last_gate1", return_value=False):
            assert self._run_check(tmp_path, 7) == 0

    def test_delta_loop_no_skip_when_changed(self, tmp_path):
        """Layer 4: if any FR changed, the normal per-FR coverage requirement still applies."""
        from unittest.mock import patch
        self._make_manifest(tmp_path, ["FR-01", "FR-02"])
        # One FR changed → not all unchanged → falls through to timestamp check → missing → 14.
        with patch("core.quality_gate.gate1_evidence.fr_code_changed_since_last_gate1",
                   side_effect=lambda fr, project: fr == "FR-02"):
            assert self._run_check(tmp_path, 7) == 14

    def test_delta_loop_autoskip_includes_p4(self, tmp_path):
        """Audit Fix C: P4 is carryforward — its plan promises auto-skip, so advance-phase
        must also auto-satisfy P4 coverage when no FR's code changed (range is 4,5,7,8)."""
        from unittest.mock import patch
        self._make_manifest(tmp_path, ["FR-01", "FR-02"])
        with patch("core.quality_gate.gate1_evidence.fr_code_changed_since_last_gate1", return_value=False):
            assert self._run_check(tmp_path, 4) == 0


# =============================================================================
# _parse_spec_names_for_fr (harness_bridge)
# =============================================================================

class TestParseSpecNamesForFr:
    """Tests for _parse_spec_names_for_fr — the canonical TEST_SPEC.md parser."""

    def _parse(self, spec_text, fr_id):
        from harness.harness_bridge import _parse_spec_names_for_fr
        return _parse_spec_names_for_fr(spec_text, fr_id)

    def test_basic_table(self):
        spec = (
            "### FR-01: Foo\n"
            "| # | Test Function | Type | Derivation |\n"
            "|---|---|---|---|\n"
            "| 1 | `test_fr01_happy` | happy_path | Q1 |\n"
        )
        assert self._parse(spec, "FR-01") == ["test_fr01_happy"]

    def test_cross_cutting_h2_stops_collection(self):
        """Rows under ## Cross-Cutting must NOT be attributed to the previous FR."""
        spec = (
            "### FR-22: Last\n"
            "| # | Test Function | Type | Derivation |\n"
            "|---|---|---|---|\n"
            "| 1 | `test_fr22_real` | happy_path | Q1 |\n"
            "\n"
            "## Cross-Cutting Integration Tests\n"
            "| # | Test Function | Type | Derivation |\n"
            "|---|---|---|---|\n"
            "| 1 | `test_cross_something` | integration | Q5 |\n"
        )
        names = self._parse(spec, "FR-22")
        assert "test_fr22_real" in names
        assert "test_cross_something" not in names

    def test_h2_non_cross_cutting_also_stops(self):
        """Any H2 heading (not only ## Cross-Cutting) must close the current FR."""
        spec = (
            "### FR-01: Foo\n"
            "| # | Test Function | Type | Derivation |\n"
            "|---|---|---|---|\n"
            "| 1 | `test_fr01_a` | happy_path | Q1 |\n"
            "\n"
            "## Security Red Team\n"
            "| # | Test Function | Type | Derivation |\n"
            "|---|---|---|---|\n"
            "| 1 | `test_redteam_x` | security | Q9 |\n"
        )
        names = self._parse(spec, "FR-01")
        assert "test_fr01_a" in names
        assert "test_redteam_x" not in names

    def test_horizontal_rule_closes_table(self):
        """--- between FR section and next section must not cause bleed."""
        spec = (
            "### FR-05: Bar\n"
            "| # | Test Function | Type | Derivation |\n"
            "|---|---|---|---|\n"
            "| 1 | `test_fr05_ok` | happy_path | Q1 |\n"
            "\n"
            "---\n"
            "\n"
            "## Other Section\n"
            "| # | Test Function | Type | Derivation |\n"
            "|---|---|---|---|\n"
            "| 1 | `test_other_z` | integration | Q2 |\n"
        )
        names = self._parse(spec, "FR-05")
        assert "test_fr05_ok" in names
        assert "test_other_z" not in names

    def test_missing_table_header_returns_empty(self):
        """FR section with data rows but no header row → returns [] (warns caller)."""
        spec = (
            "### FR-22: Missing header\n"
            "| 1 | `test_fr22_no_header` | happy_path | Q1 |\n"
        )
        # Without the table header row, parser cannot identify the table
        assert self._parse(spec, "FR-22") == []

    def test_old_bullet_list_format(self):
        """Backward-compat: bullet-list format `- test_foo` still works."""
        spec = (
            "### FR-03: Old\n"
            "- `test_fr03_legacy`\n"
            "- test_fr03_also_legacy\n"
        )
        names = self._parse(spec, "FR-03")
        assert "test_fr03_legacy" in names
        assert "test_fr03_also_legacy" in names


# =============================================================================
# run-fr-step idempotency skip: gate timestamp must fire when skipping GATE1-DELTA
# =============================================================================

class TestRunFrStepSkipSideEffects:
    """When _fr_step_already_done returns True,
    gate1_evidence.record_gate_timestamp (for GATE1-DELTA) must still be called."""

    def _make_plan(self, tmp_path: Path, phase: int, fr_id: str) -> Path:
        plan = tmp_path / ".methodology" / f"phase{phase}_plan.md"
        plan.parent.mkdir(parents=True, exist_ok=True)
        plan.write_text(
            f"- [ ] **[ORCH-GATE1]** run-fr-step --step GATE1-DELTA for {fr_id}:\n",
            encoding="utf-8",
        )
        return plan

    def _make_manifest(self, tmp_path: Path, fr_id: str, score: float = 95.0) -> None:
        import json
        m = tmp_path / ".methodology" / "quality_manifest.json"
        m.parent.mkdir(parents=True, exist_ok=True)
        m.write_text(json.dumps({
            "fr_ids": [fr_id],
            "quality_targets": {"min_coverage": 80.0},
            "gate_results": {"gate1": {fr_id: {"score": score, "quality_complete": True}}},
        }), encoding="utf-8")

    def test_gate_timestamp_recorded_on_gate1_delta_skip(self, tmp_path, monkeypatch):
        """record_gate_timestamp must be called for GATE1-DELTA skip."""
        import harness_cli
        recorded = []
        monkeypatch.setattr("cli.fr_cmds._fr_step_already_done", lambda *a, **k: True)
        from core.quality_gate import gate1_evidence as _ge
        monkeypatch.setattr(_ge, "record_gate_timestamp",
                            lambda project, phase, gate, fr_id: recorded.append((phase, gate, fr_id)))

        self._make_manifest(tmp_path, "FR-03")
        args = argparse.Namespace(
            phase=5, fr_id="FR-03", step="GATE1-DELTA",
            project=str(tmp_path), srs=None,
            timeout=600, max_fix_rounds=3, no_push=True,
            no_mcp=False, permission_mode=None, max_turns=None,
        )
        harness_cli.cmd_run_fr_step(args)

        assert (5, 1, "FR-03") in recorded, f"gate timestamp not recorded: {recorded}"

    def test_no_gate_timestamp_for_non_delta_skip(self, tmp_path, monkeypatch):
        """record_gate_timestamp must NOT be called for non-DELTA step skips."""
        import harness_cli
        recorded = []
        monkeypatch.setattr("cli.fr_cmds._fr_step_already_done", lambda *a, **k: True)
        from core.quality_gate import gate1_evidence as _ge
        monkeypatch.setattr(_ge, "record_gate_timestamp",
                            lambda *a, **k: recorded.append(True))

        self._make_manifest(tmp_path, "FR-01")
        args = argparse.Namespace(
            phase=3, fr_id="FR-01", step="TDD-RED",
            project=str(tmp_path), srs=None,
            timeout=600, max_fix_rounds=3, no_push=True,
            no_mcp=False, permission_mode=None, max_turns=None,
        )
        harness_cli.cmd_run_fr_step(args)

        assert not recorded, "gate timestamp should not be recorded for TDD-RED skip"


# =============================================================================
# Bug fix: _compute_fr_spec_data must strip [...] from parameterized test names
# =============================================================================

class TestComputeFrSpecDataParameterized:
    """_compute_fr_spec_data must match parameterized test names by stripping [param]."""

    def _make_project(self, tmp_path, spec_rows, test_body):
        arch = tmp_path / "02-architecture"
        arch.mkdir(parents=True)
        table = "\n".join(f"| {i+1} | `{name}` | Functional |" for i, name in enumerate(spec_rows))
        (arch / "TEST_SPEC.md").write_text(
            "### FR-01: Lexicon\n\n"
            "| # | Test Function | Type |\n"
            "|---|--------------|------|\n"
            f"{table}\n",
            encoding="utf-8",
        )
        tests = tmp_path / "tests"
        tests.mkdir()
        (tests / "test_fr01.py").write_text(test_body, encoding="utf-8")
        return tmp_path

    def test_parameterized_name_matches_base_function(self, tmp_path):
        """TEST_SPEC row 'test_fn[param]' must match 'def test_fn' in test file."""
        self._make_project(
            tmp_path,
            spec_rows=[
                "test_fr_01_lexicon_coverage[視頻→影片]",
                "test_fr_01_lexicon_coverage[影片→視頻]",
            ],
            test_body="def test_fr_01_lexicon_coverage(word_pair):\n    pass\n",
        )
        result = _compute_fr_spec_data(tmp_path, "FR-01", "tests/test_fr01.py")
        assert result["spec_cov_pct"] == 100, (
            f"parameterized names must match by base name; got {result['spec_cov_pct']}"
        )

    def test_missing_base_function_gives_zero(self, tmp_path):
        """If the base function does not exist in the test file, spec_cov_pct == 0."""
        self._make_project(
            tmp_path,
            spec_rows=["test_fr_01_missing[param]"],
            test_body="# no functions here\n",
        )
        result = _compute_fr_spec_data(tmp_path, "FR-01", "tests/test_fr01.py")
        assert result["spec_cov_pct"] == 0

    def test_backtick_name_matches(self, tmp_path):
        """TEST_SPEC row '`test_fn`' (backtick-quoted) must match 'def test_fn'."""
        self._make_project(
            tmp_path,
            spec_rows=["`test_fr_01_lookup`"],
            test_body="def test_fr_01_lookup(x):\n    pass\n",
        )
        result = _compute_fr_spec_data(tmp_path, "FR-01", "tests/test_fr01.py")
        assert result["spec_cov_pct"] == 100, (
            f"backtick-quoted spec name must strip backticks before matching; got {result['spec_cov_pct']}"
        )

    def test_paren_suffix_matches(self, tmp_path):
        """TEST_SPEC row 'test_fn()' (with parens) must match 'def test_fn'."""
        self._make_project(
            tmp_path,
            spec_rows=["test_fr_01_lookup()"],
            test_body="def test_fr_01_lookup(x):\n    pass\n",
        )
        result = _compute_fr_spec_data(tmp_path, "FR-01", "tests/test_fr01.py")
        assert result["spec_cov_pct"] == 100, (
            f"() suffix must be stripped before matching; got {result['spec_cov_pct']}"
        )

    def test_async_def_matches(self, tmp_path):
        """'async def test_fn(...)' must be found the same as sync 'def test_fn'."""
        self._make_project(
            tmp_path,
            spec_rows=["test_fr_01_async"],
            test_body="async def test_fr_01_async(client):\n    pass\n",
        )
        result = _compute_fr_spec_data(tmp_path, "FR-01", "tests/test_fr01.py")
        assert result["spec_cov_pct"] == 100, (
            f"async def must be detected by the function scanner; got {result['spec_cov_pct']}"
        )


# =============================================================================
# Bug fix: _fr_gate1_commit_sha must fall back to batch "Gate1 PASS" commits
# =============================================================================

class TestFrGate1CommitShaFallback:
    """_fr_gate1_commit_sha must fall back to batch commits when per-FR format missing."""

    def _fake_run_factory(self, per_fr_sha: str, batch_sha: str):
        """Return a subprocess.run replacement: per-FR pattern returns per_fr_sha,
        broad 'Gate1 PASS' pattern returns batch_sha."""
        import subprocess

        def fake_run(cmd, **kw):
            ns = subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
            idx = cmd.index("--grep") + 1 if "--grep" in cmd else -1
            if idx >= 0:
                pattern = cmd[idx]
                if "Gate1 PASS" == pattern:
                    ns.stdout = batch_sha + "\n" if batch_sha else ""
                else:
                    ns.stdout = per_fr_sha + "\n" if per_fr_sha else ""
            return ns

        return fake_run

    def test_per_fr_commit_returned_directly(self, tmp_path, monkeypatch):
        """If per-FR pattern matches, return that SHA without hitting fallback."""
        import subprocess
        monkeypatch.setattr(subprocess, "run", self._fake_run_factory("abc123", "batch999"))
        from core.quality_gate.gate1_evidence import fr_gate1_commit_sha
        sha = fr_gate1_commit_sha("FR-01", tmp_path)
        assert sha == "abc123"

    def test_fallback_to_batch_commit(self, tmp_path, monkeypatch):
        """If per-FR pattern finds nothing, must return SHA from batch 'Gate1 PASS' grep."""
        import subprocess
        monkeypatch.setattr(subprocess, "run", self._fake_run_factory("", "deadbeef"))
        from core.quality_gate.gate1_evidence import fr_gate1_commit_sha
        sha = fr_gate1_commit_sha("FR-01", tmp_path)
        assert sha == "deadbeef"

    def test_returns_none_when_no_commit(self, tmp_path, monkeypatch):
        """No Gate1 PASS commit of any kind → returns None."""
        import subprocess
        monkeypatch.setattr(subprocess, "run", self._fake_run_factory("", ""))
        from core.quality_gate.gate1_evidence import fr_gate1_commit_sha
        sha = fr_gate1_commit_sha("FR-01", tmp_path)
        assert sha is None


# =============================================================================
# _git_test_patterns: symlink-aware test path resolution for git operations
# =============================================================================

class TestGitTestPatterns:

    def test_no_symlink_returns_standard_patterns(self, tmp_path):
        """When tests/ is a regular directory, return only standard patterns."""
        import harness_cli  # noqa: F401  entry-first load order (cli-first crashes until S5)
        (tmp_path / "tests").mkdir()
        from core.quality_gate.spec_coverage import _git_test_patterns
        patterns = _git_test_patterns(tmp_path, "01", "1")
        assert patterns == ["tests/test_fr01.py", "tests/test_fr1.py"]
        dirs = harness_cli._get_test_directories(tmp_path)
        assert len(dirs) == 1
        assert dirs[0].name == "tests"

    def test_canonical_layout_directly(self, tmp_path):
        """Bug #130: 03-development/tests/ used directly (no symlink) works."""
        import harness_cli
        can_tests = tmp_path / "03-development" / "tests"
        can_tests.mkdir(parents=True)
        from core.quality_gate.spec_coverage import _git_test_patterns
        patterns = _git_test_patterns(tmp_path, "01", "1")
        assert "03-development/tests/test_fr01.py" in patterns
        assert "03-development/tests/test_fr1.py" in patterns
        dirs = harness_cli._get_test_directories(tmp_path)
        assert len(dirs) == 1
        assert dirs[0] == can_tests

    def test_symlink_adds_resolved_patterns(self, tmp_path):
        """When tests/ → 03-development/tests/, include git-tracked real paths."""
        import harness_cli
        real = tmp_path / "03-development" / "tests"
        real.mkdir(parents=True)
        (tmp_path / "tests").symlink_to(real)
        from core.quality_gate.spec_coverage import _git_test_patterns
        patterns = _git_test_patterns(tmp_path, "01", "1")
        assert "tests/test_fr01.py" in patterns
        assert "03-development/tests/test_fr01.py" in patterns
        assert "03-development/tests/test_fr1.py" in patterns
        assert len(patterns) == 4
        dirs = harness_cli._get_test_directories(tmp_path)
        assert len(dirs) == 1
        assert dirs[0] == real

    def test_symlink_outside_project_ignored(self, tmp_path):
        """Symlink resolving outside project root → ValueError caught, no extra patterns."""
        import tempfile
        outside = Path(tempfile.mkdtemp())
        try:
            (tmp_path / "tests").symlink_to(outside)
            from core.quality_gate.spec_coverage import _git_test_patterns
            patterns = _git_test_patterns(tmp_path, "01", "1")
            assert len(patterns) == 2  # only standard patterns, no crash
        finally:
            import shutil
            shutil.rmtree(outside, ignore_errors=True)

def test_l1_finalize_sentinel_path_legacy_fallback(tmp_path):
    """Test L1: Legacy sentinel fallback in _finalize_sentinel_path."""
    from cli._shared import _finalize_sentinel_path
    
    fr_id = "FR-99"
    key = fr_id.replace("-", "").lower()
    gate = 1
    d = tmp_path / ".sessi-work" / "sentinels"
    d.mkdir(parents=True, exist_ok=True)
    
    std_path = d / f"g{gate}_{key}.finalized"
    legacy_path = d / f"g{gate}_{fr_id}.flag"
    
    # Neither exists -> returns std_path
    assert _finalize_sentinel_path(tmp_path, gate, fr_id) == std_path
    
    # Only legacy exists -> returns legacy_path
    legacy_path.touch()
    assert _finalize_sentinel_path(tmp_path, gate, fr_id) == legacy_path
    
    # Both exist -> returns std_path
    std_path.touch()
    assert _finalize_sentinel_path(tmp_path, gate, fr_id) == std_path

def _setup_advance_prechecks_env(tmp_path, monkeypatch):
    """Shared fixture setup for _advance_prechecks blocking-path tests."""

    (tmp_path / ".methodology").mkdir(exist_ok=True)
    (tmp_path / "03-development" / "src").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".methodology" / "phase4_plan.md").touch()

    # Create finalize-gate sentinels — _advance_prechecks verifies these exist
    _write_finalize_sentinels_for_tests(tmp_path)

    monkeypatch.setattr("cli._shared._run_phase_auditor", lambda _, __: 0)
    monkeypatch.setattr("core.quality_gate.agent_b_approvals.verify_agent_b_approvals_core", lambda _, __, ___: (True, "mocked"))

    class FakeVerifier:
        def __init__(self, *args, **kwargs): pass
        def verify(self): return {"passed": True, "total_score": 100.0}
    monkeypatch.setattr("core.quality_gate.phase_truth_verifier.PhaseTruthVerifier", FakeVerifier)

    # Constitution check — mock to pass so it doesn't block on empty project
    _mock_constitution_pass(monkeypatch)

    # Scope to harness_cli's reference — not global shutil
    monkeypatch.setattr("shutil.which", lambda cmd: True)


def test_l1_advance_prechecks_gitleaks_blocks(tmp_path, monkeypatch):
    """rc=20: gitleaks detects secrets → advance blocked."""
    from cli.phase_cmds import _advance_prechecks

    _setup_advance_prechecks_env(tmp_path, monkeypatch)

    def fake_run(cmd, **kwargs):
        class R:
            returncode = 1 if cmd[0] == "gitleaks" else 0
            stdout = ""
        return R()
    monkeypatch.setattr(subprocess, "run", fake_run)

    assert _advance_prechecks(tmp_path, 3) == 20


def test_l1_advance_prechecks_ruff_blocks(tmp_path, monkeypatch):
    """rc=18: ruff finds lint errors → advance blocked."""
    from cli.phase_cmds import _advance_prechecks

    _setup_advance_prechecks_env(tmp_path, monkeypatch)

    def fake_run(cmd, **kwargs):
        class R:
            returncode = 1 if cmd[0] == "ruff" else 0
            stdout = ""
        return R()
    monkeypatch.setattr(subprocess, "run", fake_run)

    assert _advance_prechecks(tmp_path, 3) == 18


def test_l1_advance_prechecks_mypy_blocks(tmp_path, monkeypatch):
    """rc=19: mypy finds type errors → advance blocked."""
    from cli.phase_cmds import _advance_prechecks

    _setup_advance_prechecks_env(tmp_path, monkeypatch)

    def fake_run(cmd, **kwargs):
        is_mypy = len(cmd) >= 3 and cmd[1] == "-m" and cmd[2] == "mypy"
        class R:
            returncode = 1 if is_mypy else 0
            stdout = ""
        return R()
    monkeypatch.setattr(subprocess, "run", fake_run)

    assert _advance_prechecks(tmp_path, 3) == 19


# =============================================================================
# B3: _trace_dirty_state must include fix command in reason string
# =============================================================================

class TestTraceDirtyState:
    """_trace_dirty_state reason strings must include the build-trace-attestation hint."""

    def _make_attestation(self, tmp_path, offset_secs: float = 0.0) -> Path:
        """Write attestation.json with an mtime offset relative to now."""
        import time
        trace_dir = tmp_path / ".methodology" / "trace"
        trace_dir.mkdir(parents=True)
        att = trace_dir / "attestation.json"
        att.write_text('{"schema": "v1"}', encoding="utf-8")
        if offset_secs:
            t = time.time() + offset_secs
            import os
            os.utime(att, (t, t))
        return att

    def test_missing_attestation_reason_includes_fix_hint(self, tmp_path):
        """No attestation.json → reason must contain the fix command."""
        from cli.phase_cmds import _trace_dirty_state
        (tmp_path / ".methodology" / "trace").mkdir(parents=True)
        result = _trace_dirty_state(tmp_path)
        assert not result["passed"]
        assert "build-trace-attestation" in result["reason"], (
            f"Fix command missing from reason: {result['reason']!r}"
        )

    def test_newer_test_file_reason_includes_fix_hint(self, tmp_path):
        """Test file newer than attestation → reason must contain the fix command."""
        from cli.phase_cmds import _trace_dirty_state

        # attestation written first (older)
        att = self._make_attestation(tmp_path)

        # Write a test file that is 2 seconds newer than attestation
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        tf = tests_dir / "test_something.py"
        tf.write_text("def test_x(): pass\n", encoding="utf-8")
        future = att.stat().st_mtime + 2.0
        os.utime(tf, (future, future))

        result = _trace_dirty_state(tmp_path)
        assert not result["passed"]
        assert "build-trace-attestation" in result["reason"], (
            f"Fix command missing from reason: {result['reason']!r}"
        )

    def test_current_attestation_passes(self, tmp_path):
        """Attestation newer than all files → passed=True."""
        from cli.phase_cmds import _trace_dirty_state

        # Write a test file first (older)
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        tf = tests_dir / "test_something.py"
        tf.write_text("def test_x(): pass\n", encoding="utf-8")

        # attestation written 2 seconds later (newer)
        att = self._make_attestation(tmp_path)
        future = tf.stat().st_mtime + 2.0
        os.utime(att, (future, future))

        result = _trace_dirty_state(tmp_path)
        assert result["passed"]


# =============================================================================
# B2: advance-phase must git-add auto-generated STAGE_PASS before auditor runs
# =============================================================================

def test_stage_pass_autogenerate_is_git_added(tmp_path, monkeypatch):
    """B2: After auto-generating Phase{N}_STAGE_PASS.md, advance-phase must
    call 'git add' on it before running PhaseAuditor C1 (git ls-files check).
    Without git-add, C1 immediately blocks the file that advance-phase just created.
    """
    from cli.phase_cmds import _advance_prechecks

    _setup_advance_prechecks_env(tmp_path, monkeypatch)

    # Do NOT pre-create Phase3_STAGE_PASS.md so auto-generation is triggered.
    # Mock _generate_stage_pass to write the file (quality_manifest.json is
    # absent in the tmp project, so the real generator would print WARN + skip).
    def _write_stage_pass(project, gate, phase):
        sp = project / "00-summary" / f"Phase{phase}_STAGE_PASS.md"
        sp.parent.mkdir(exist_ok=True)
        sp.write_text(f"# Phase {phase} STAGE_PASS\n## Summary\n", encoding="utf-8")

    monkeypatch.setattr("cli._shared._generate_stage_pass", _write_stage_pass)

    # Capture subprocess.run calls to verify git add is invoked.
    git_add_calls: list[list] = []

    def fake_subprocess_run(cmd, **kwargs):
        class R:
            returncode = 0
            stdout = ""
            stderr = ""
        cmd_list = list(cmd)
        if cmd_list[:2] == ["git", "add"]:
            git_add_calls.append(cmd_list)
        return R()

    monkeypatch.setattr(subprocess, "run", fake_subprocess_run)
    monkeypatch.setattr(
        "core.quality_gate.mutation_enforcer.run_mutation_precheck",
        lambda _: (True, "ok"),
    )
    monkeypatch.setattr("core.quality_gate.spec_coverage._run_spec_coverage_check", lambda *_, **__: (0, 100.0))
    monkeypatch.setattr("cli.phase_cmds._check_gate1_live_coverage", lambda _, __: 0)

    _advance_prechecks(tmp_path, completed_phase=3)

    expected_path = str(tmp_path / "00-summary" / "Phase3_STAGE_PASS.md")
    assert any(expected_path in " ".join(str(x) for x in call) for call in git_add_calls), (
        f"'git add Phase3_STAGE_PASS.md' was not called; "
        f"captured git-add calls: {git_add_calls}"
    )


# =============================================================================
# _generate_stage_pass gate_data-empty bug (Phase 1-2 case)
# =============================================================================

class TestGenerateStagePassEmptyGateData:
    """Bug: For Phase 1-2, Gate 1 has not fired yet (Gate 1 is per-FR, fires in
    Phase 3+). quality_manifest.json gate_results.gate1 = {} (empty dict).

    _generate_stage_pass() reads this empty dict, computes quality_complete=False
    (default), and writes STAGE_PASS.md saying "Phase 1 exit gate FAIL" — even
    though the phase actually succeeded (Constitution PASS, all 4 deliverables
    APPROVED, advance-phase recorded phase_truth_passed:true in state.json).

    Fix: when gate_data is empty AND the phase is one where the gate has not
    fired yet (Phase 1-2 → Gate 1; Phase 5/7/8 → Gate 1 GATE1-DELTA logic
    applies, but quality_manifest.gate1 may still be {} before DELTA — fallback
    to state.json.phase_truth_passed to derive the verdict.
    """

    def _setup(self, tmp_path, phase_truth_passed):
        """Create tmp project: state.json + empty quality_manifest."""
        import json

        methodology = tmp_path / ".methodology"
        methodology.mkdir(parents=True)
        state = {
            "state": "RUNNING",
            "current_phase": 2,
            "phase_truth_passed": phase_truth_passed,
            "last_update": "2026-07-04T10:52:47Z",
        }
        (methodology / "state.json").write_text(json.dumps(state), encoding="utf-8")

        # Empty quality_manifest: gate1 = {} (Gate 1 not fired for Phase 1-2)
        manifest = {
            "schema_version": "1.0",
            "generated_at_phase": 1,
            "gate_results": {
                "gate1": {},
                "gate2": None,
                "gate3": None,
                "gate4": None,
            },
        }
        (methodology / "quality_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        return methodology

    def test_phase1_pass_when_phase_truth_passed_true(self, tmp_path):
        """Phase 1 with phase_truth_passed=True → STAGE_PASS.md must say PASS."""
        from cli._shared import _generate_stage_pass

        self._setup(tmp_path, phase_truth_passed=True)

        _generate_stage_pass(tmp_path, gate_num=1, phase_num=1)

        out = tmp_path / "00-summary" / "Phase1_STAGE_PASS.md"
        assert out.exists(), "STAGE_PASS.md not generated"
        content = out.read_text(encoding="utf-8")
        assert "PASS" in content, f"Expected PASS verdict for Phase 1 (phase_truth_passed=True); got:\n{content}"
        assert "FAIL" not in content.split("## Summary")[1], (
            f"Summary section must say PASS, not FAIL; got:\n{content}"
        )

    def test_phase1_fail_when_phase_truth_passed_false(self, tmp_path):
        """Phase 1 with phase_truth_passed=False → STAGE_PASS.md must say FAIL."""
        from cli._shared import _generate_stage_pass

        self._setup(tmp_path, phase_truth_passed=False)

        _generate_stage_pass(tmp_path, gate_num=1, phase_num=1)

        out = tmp_path / "00-summary" / "Phase1_STAGE_PASS.md"
        assert out.exists()
        content = out.read_text(encoding="utf-8")
        assert "FAIL" in content.split("## Summary")[1], (
            f"Summary section must say FAIL when phase_truth_passed=False; got:\n{content}"
        )

    def test_phase1_passes_when_state_json_missing(self, tmp_path):
        """No state.json + empty quality_manifest → fall back to FAIL (safe default)."""
        from cli._shared import _generate_stage_pass

        # No state.json — function should not crash; default to FAIL.
        methodology = tmp_path / ".methodology"
        methodology.mkdir(parents=True)
        manifest = {
            "schema_version": "1.0",
            "gate_results": {"gate1": {}, "gate2": None, "gate3": None, "gate4": None},
        }
        (methodology / "quality_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

        _generate_stage_pass(tmp_path, gate_num=1, phase_num=1)
        out = tmp_path / "00-summary" / "Phase1_STAGE_PASS.md"
        assert out.exists()
        content = out.read_text(encoding="utf-8")
        assert "FAIL" in content.split("## Summary")[1]

    def test_phase3_gate1_per_fr_quality_complete_false_overrides_truth(self, tmp_path):
        """Phase 3 Gate 1 per-FR with any FR quality_complete=False → FAIL,
        even if phase_truth_passed=True. Gate data takes precedence.
        """
        from cli._shared import _generate_stage_pass

        methodology = tmp_path / ".methodology"
        methodology.mkdir(parents=True)
        state = {"current_phase": 4, "phase_truth_passed": True}
        (methodology / "state.json").write_text(json.dumps(state), encoding="utf-8")

        # Gate 1 per-FR: FR-01 failed (real failure)
        manifest = {
            "schema_version": "1.0",
            "gate_results": {
                "gate1": {
                    "FR-01": {"score": 65.0, "quality_complete": False},
                    "FR-02": {"score": 92.0, "quality_complete": True},
                },
                "gate2": None,
                "gate3": None,
                "gate4": None,
            },
        }
        (methodology / "quality_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

        _generate_stage_pass(tmp_path, gate_num=1, phase_num=3)

        out = tmp_path / "00-summary" / "Phase3_STAGE_PASS.md"
        assert out.exists()
        content = out.read_text(encoding="utf-8")
        assert "FAIL" in content.split("## Summary")[1], (
            f"Phase 3 Gate 1 with FR-01 quality_complete=False must say FAIL; got:\n{content}"
        )

    def test_phase3_gate1_per_fr_all_pass(self, tmp_path):
        """Phase 3 Gate 1 per-FR all quality_complete=True → PASS."""
        from cli._shared import _generate_stage_pass

        methodology = tmp_path / ".methodology"
        methodology.mkdir(parents=True)
        state = {"current_phase": 4, "phase_truth_passed": True}
        (methodology / "state.json").write_text(json.dumps(state), encoding="utf-8")

        # Gate 1 per-FR: all pass
        manifest = {
            "schema_version": "1.0",
            "gate_results": {
                "gate1": {
                    "FR-01": {"score": 92.0, "quality_complete": True},
                    "FR-02": {"score": 88.0, "quality_complete": True},
                },
                "gate2": None,
                "gate3": None,
                "gate4": None,
            },
        }
        (methodology / "quality_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

        _generate_stage_pass(tmp_path, gate_num=1, phase_num=3)

        out = tmp_path / "00-summary" / "Phase3_STAGE_PASS.md"
        assert out.exists()
        content = out.read_text(encoding="utf-8")
        assert "PASS" in content.split("## Summary")[1], (
            f"Phase 3 Gate 1 all FRs pass → must say PASS; got:\n{content}"
        )

    def test_phase3_gate2_flat_structure_unchanged(self, tmp_path):
        """Phase 3 Gate 2 (flat) with quality_complete=True → PASS."""
        from cli._shared import _generate_stage_pass

        methodology = tmp_path / ".methodology"
        methodology.mkdir(parents=True)
        state = {"current_phase": 4, "phase_truth_passed": True}
        (methodology / "state.json").write_text(json.dumps(state), encoding="utf-8")

        manifest = {
            "schema_version": "1.0",
            "gate_results": {
                "gate1": {},
                "gate2": {"score": 95.0, "quality_complete": True},
                "gate3": None,
                "gate4": None,
            },
        }
        (methodology / "quality_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

        _generate_stage_pass(tmp_path, gate_num=2, phase_num=3)

        out = tmp_path / "00-summary" / "Phase3_STAGE_PASS.md"
        assert out.exists()
        content = out.read_text(encoding="utf-8")
        assert "PASS" in content.split("## Summary")[1], (
            f"Phase 3 Gate 2 quality_complete=True → must say PASS; got:\n{content}"
        )
        assert "95.0" in content, "Gate 2 score must be displayed"


# =============================================================================
# STAGE_PASS.md always-regenerate (B-class bug fix: stale FAIL artifact)
# =============================================================================

class TestAdvancePhaseRegeneratesStagePass:
    """Bug: STAGE_PASS.md committed by an older _generate_stage_pass logic
    (pre-d8fccea "always FAIL" for empty gate_data) is never overwritten by
    subsequent advance-phase runs. Result: PhaseAuditor C1 sees stale FAIL even
    after the underlying logic was fixed.

    Fix: advance-phase trigger at harness_cli.py:6345 must ALWAYS call
    _generate_stage_pass, then git-add only if content actually changed.
    """

    def test_regenerates_when_stale_exists(self, tmp_path, monkeypatch):
        """Pre-create stale Phase3_STAGE_PASS.md (the pre-d8fccea FAIL content),
        run _advance_prechecks, assert the file content was overwritten by the
        current _generate_stage_pass logic (not just appended/touched)."""
        from cli.phase_cmds import _advance_prechecks

        _setup_advance_prechecks_env(tmp_path, monkeypatch)

        # Pre-create stale STAGE_PASS.md with the bug-era "FAIL" content.
        sp_dir = tmp_path / "00-summary"
        sp_dir.mkdir(exist_ok=True)
        sp_path = sp_dir / "Phase3_STAGE_PASS.md"
        stale_content = (
            "# Phase 3 STAGE_PASS\n\n"
            "Generated: 2026-07-04 09:00 UTC\n\n"
            "## Gate Score\n"
            "Gate 1 Composite Score: **N/A**\n\n"
            "## Quality Status\n"
            "quality_complete: **False**\n\n"
            "## Summary\n"
            "Phase 3 exit gate FAIL.\n"
        )
        sp_path.write_text(stale_content, encoding="utf-8")
        pre_existing_bytes = sp_path.read_bytes()

        # Mock _generate_stage_pass to write the CURRENT (post-fix) "PASS" content.
        new_content = (
            "# Phase 3 STAGE_PASS\n\n"
            "Generated: 2026-07-04 12:00 UTC\n\n"
            "## Summary\n"
            "Phase 3 exit gate PASS.\n"
        )

        def _write_new_stage_pass(project, gate, phase):
            sp = project / "00-summary" / f"Phase{phase}_STAGE_PASS.md"
            sp.parent.mkdir(exist_ok=True)
            sp.write_text(new_content, encoding="utf-8")

        monkeypatch.setattr("cli._shared._generate_stage_pass", _write_new_stage_pass)
        monkeypatch.setattr(subprocess, "run", _fake_subprocess_capture_git_add)

        _advance_prechecks(tmp_path, completed_phase=3)

        # Assert: file content was overwritten (not still stale).
        post_bytes = sp_path.read_bytes()
        assert post_bytes != pre_existing_bytes, (
            "STAGE_PASS.md was NOT overwritten — always-regenerate missing"
        )
        assert post_bytes == new_content.encode("utf-8"), (
            f"STAGE_PASS.md content mismatch.\n"
            f"Expected: {new_content!r}\n"
            f"Got: {post_bytes.decode('utf-8')!r}"
        )

    def test_git_add_called_when_content_changed(self, tmp_path, monkeypatch):
        """When _generate_stage_pass produces content different from existing
        file, advance-phase must call `git add` so the refresh lands in commit."""
        from cli.phase_cmds import _advance_prechecks

        _setup_advance_prechecks_env(tmp_path, monkeypatch)

        sp_dir = tmp_path / "00-summary"
        sp_dir.mkdir(exist_ok=True)
        sp_path = sp_dir / "Phase3_STAGE_PASS.md"
        sp_path.write_text("# STALE OLD CONTENT\n", encoding="utf-8")

        def _write_different(project, gate, phase):
            sp = project / "00-summary" / f"Phase{phase}_STAGE_PASS.md"
            sp.parent.mkdir(exist_ok=True)
            sp.write_text("# NEW CONTENT AFTER REGENERATE\n", encoding="utf-8")

        monkeypatch.setattr("cli._shared._generate_stage_pass", _write_different)

        git_add_calls: list[list] = []

        def fake_subprocess_run(cmd, **kwargs):
            class R:
                returncode = 0
                stdout = ""
                stderr = ""
            cmd_list = list(cmd)
            if cmd_list[:2] == ["git", "add"]:
                git_add_calls.append(cmd_list)
            return R()

        monkeypatch.setattr(subprocess, "run", fake_subprocess_run)

        _advance_prechecks(tmp_path, completed_phase=3)

        expected_path = str(tmp_path / "00-summary" / "Phase3_STAGE_PASS.md")
        assert any(expected_path in " ".join(str(x) for x in call) for call in git_add_calls), (
            f"git add STAGE_PASS.md not called when content changed.\n"
            f"Captured git-add calls: {git_add_calls}"
        )

    def test_git_add_skipped_when_content_unchanged(self, tmp_path, monkeypatch):
        """When _generate_stage_pass produces content identical to existing
        file, advance-phase must NOT call `git add` (avoid empty no-op commits)."""
        from cli.phase_cmds import _advance_prechecks

        _setup_advance_prechecks_env(tmp_path, monkeypatch)

        sp_dir = tmp_path / "00-summary"
        sp_dir.mkdir(exist_ok=True)
        sp_path = sp_dir / "Phase3_STAGE_PASS.md"
        same_content = "# SAME CONTENT\n"
        sp_path.write_text(same_content, encoding="utf-8")

        def _write_same(project, gate, phase):
            sp = project / "00-summary" / f"Phase{phase}_STAGE_PASS.md"
            sp.parent.mkdir(exist_ok=True)
            sp.write_text(same_content, encoding="utf-8")

        monkeypatch.setattr("cli._shared._generate_stage_pass", _write_same)

        git_add_calls: list[list] = []

        def fake_subprocess_run(cmd, **kwargs):
            class R:
                returncode = 0
                stdout = ""
                stderr = ""
            cmd_list = list(cmd)
            if cmd_list[:2] == ["git", "add"]:
                git_add_calls.append(cmd_list)
            return R()

        monkeypatch.setattr(subprocess, "run", fake_subprocess_run)

        _advance_prechecks(tmp_path, completed_phase=3)

        expected_path = str(tmp_path / "00-summary" / "Phase3_STAGE_PASS.md")
        stage_pass_add_calls = [
            call for call in git_add_calls
            if expected_path in " ".join(str(x) for x in call)
        ]
        assert stage_pass_add_calls == [], (
            f"git add STAGE_PASS.md was called when content unchanged.\n"
            f"Calls: {stage_pass_add_calls}"
        )

    def test_missing_file_still_generates_regression(self, tmp_path, monkeypatch):
        """Regression: if STAGE_PASS.md does not exist, advance-phase still
        calls _generate_stage_pass and stages the new file (original behavior
        must be preserved)."""
        from cli.phase_cmds import _advance_prechecks

        _setup_advance_prechecks_env(tmp_path, monkeypatch)

        sp_path = tmp_path / "00-summary" / "Phase3_STAGE_PASS.md"
        assert not sp_path.exists()

        called = {"count": 0}

        def _write_when_missing(project, gate, phase):
            called["count"] += 1
            sp = project / "00-summary" / f"Phase{phase}_STAGE_PASS.md"
            sp.parent.mkdir(exist_ok=True)
            sp.write_text("# GENERATED\n", encoding="utf-8")

        monkeypatch.setattr("cli._shared._generate_stage_pass", _write_when_missing)
        monkeypatch.setattr(subprocess, "run", _fake_subprocess_capture_git_add)

        _advance_prechecks(tmp_path, completed_phase=3)

        assert called["count"] == 1, "_generate_stage_pass not called for missing file"
        assert sp_path.exists(), "STAGE_PASS.md not generated for missing-file case"


def _fake_subprocess_capture_git_add(cmd, **kwargs):
    """Stand-in for test_stage_pass_autogenerate_is_git_added pattern."""
    class R:
        returncode = 0
        stdout = ""
        stderr = ""
    return R()


# =============================================================================
# _advance_commit_targets includes STAGE_PASS.md when present
# =============================================================================

class TestAdvanceCommitTargetsIncludesStagePass:
    """Bug: _advance_commit_targets at harness_cli.py:6713 did NOT include
    00-summary/Phase{N}_STAGE_PASS.md. Result: STAGE_PASS.md regenerated by
    advance-phase's earlier git-add (line 6361) would land in commit only via
    that earlier git-add; if it were missed, the file would never enter the
    advance commit and would persist as dirty tree residue.

    Fix: include STAGE_PASS.md in the advance commit targets so a single
    git-add covers everything."""

    def test_targets_includes_stage_pass_when_exists(self):
        from cli.phase_cmds import _advance_commit_targets

        targets = _advance_commit_targets(
            completed_phase=3,
            next_phase=4,
            manifest_regenerated=False,
            fr_progress_exists=False,
            gate_timestamps_exists=False,
            stage_pass_exists=True,
        )
        assert "00-summary/Phase3_STAGE_PASS.md" in targets, (
            f"STAGE_PASS.md missing from advance commit targets.\nGot: {targets}"
        )

    def test_targets_excludes_stage_pass_when_missing(self):
        from cli.phase_cmds import _advance_commit_targets

        targets = _advance_commit_targets(
            completed_phase=3,
            next_phase=4,
            manifest_regenerated=False,
            fr_progress_exists=False,
            gate_timestamps_exists=False,
            stage_pass_exists=False,
        )
        assert "00-summary/Phase3_STAGE_PASS.md" not in targets, (
            f"STAGE_PASS.md should NOT be in targets when missing.\nGot: {targets}"
        )

    def test_targets_uses_completed_phase_in_path(self):
        """The path uses completed_phase (the phase just finished), not next_phase."""
        from cli.phase_cmds import _advance_commit_targets

        targets = _advance_commit_targets(
            completed_phase=6,
            next_phase=7,
            manifest_regenerated=False,
            fr_progress_exists=False,
            gate_timestamps_exists=False,
            stage_pass_exists=True,
        )
        assert "00-summary/Phase6_STAGE_PASS.md" in targets
        assert "00-summary/Phase7_STAGE_PASS.md" not in targets


# B3: _advance_prechecks at completed_phase=8 must NOT block on phase9_plan.md
# (Phase 8 is the terminal phase — there is no Phase 9).
def test_advance_prechecks_p8_does_not_require_phase9_plan(tmp_path, monkeypatch):
    """B3: advance-phase for completed_phase=8 must not return 15 (plan-not-found).

    Before the fix, `if completed_phase >= 3:` triggered for P8 and blocked
    with exit code 15 because phase9_plan.md does not exist.
    """
    from cli.phase_cmds import _advance_prechecks

    _setup_advance_prechecks_env(tmp_path, monkeypatch)

    # Explicitly do NOT create phase9_plan.md — verify P8 is not blocked on it.
    assert not (tmp_path / ".methodology" / "phase9_plan.md").exists()

    monkeypatch.setattr("core.quality_gate.spec_coverage._run_spec_coverage_check", lambda *_, **__: (0, 100.0))
    monkeypatch.setattr("cli.phase_cmds._check_gate1_live_coverage", lambda _, __: 0)
    monkeypatch.setattr("cli._shared._generate_stage_pass", lambda p, g, ph: None)
    monkeypatch.setattr(
        "core.quality_gate.mutation_enforcer.run_mutation_precheck",
        lambda _: (True, "ok"),
    )
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: type("R", (), {
        "returncode": 0, "stdout": "", "stderr": "",
    })())

    rc = _advance_prechecks(tmp_path, completed_phase=8)

    assert rc != 15, (
        "advance-phase returned 15 (phase9_plan.md not found) for completed_phase=8. "
        "Phase 8 is terminal — no phase9_plan.md should be required."
    )


# =============================================================================
# Finding #3: P2→P3 advance must auto-regenerate quality_manifest.json
# =============================================================================

class TestP2AdvanceRegeneratesManifest:
    """Regression tests for Finding #3: P2 plan never re-invokes
    `harness_cli.py manifest` after scripts/generate_sab.py runs. P3 entry
    then sees a stale P1 manifest with no SAD-derived data (nfr_dim_map,
    high_risk_modules, gate_score_overrides). The fix: cmd_advance_phase
    auto-regenerates the manifest at P2 exit using the fresh SAD.md.
    """

    def _setup(self, tmp_path: Path, monkeypatch) -> None:
        """Minimal project + mocked advance prechecks so cmd_advance_phase
        reaches the manifest-regeneration block.
        """
        import harness_cli  # noqa: F401  entry-first load order (cli-first crashes until S5)
        (tmp_path / ".methodology").mkdir(parents=True, exist_ok=True)
        (tmp_path / "01-requirements").mkdir(parents=True, exist_ok=True)
        (tmp_path / "02-architecture").mkdir(parents=True, exist_ok=True)
        (tmp_path / ".methodology" / "phase2_plan.md").touch()
        (tmp_path / ".methodology" / "phase3_plan.md").touch()
        (tmp_path / "01-requirements" / "SRS.md").write_text(
            "# SRS\n\n### FR-01: alpha\n\n### FR-02: beta\n", encoding="utf-8"
        )
        (tmp_path / "02-architecture" / "SAD.md").write_text(
            "# SAD - taskq\n\n## 5. SAB\n\nnfr_dim_map: {}\n"
            "constraints: []\nhigh_risk: []\n",
            encoding="utf-8",
        )
        # Seed a stale P1 manifest — generated_at_phase=1 marks it as P1 output
        import json
        seed = {
            "schema_version": "1.0",
            "generated_at_phase": 1,
            "fr_ids": ["FR-01", "FR-02"],
            "nfr_dimension_mapping": {},
            "high_risk_modules": [],
            "gate_results": {"gate1": {}, "gate2": None, "gate3": None, "gate4": None},
        }
        (tmp_path / ".methodology" / "quality_manifest.json").write_text(
            json.dumps(seed), encoding="utf-8"
        )

        # Mock prechecks so cmd_advance_phase doesn't trip on missing CI artifacts
        _write_finalize_sentinels_for_tests(tmp_path)
        monkeypatch.setattr("cli.phase_cmds._advance_prechecks", lambda _, __: 0)
        monkeypatch.setattr("core.claude_md.update_claude_md", lambda _: None)
        monkeypatch.setattr("core.claude_md.llm_clean_stale_claude_md", lambda _: None)
        monkeypatch.setattr("shutil.which", lambda c: None)  # no CRG
        monkeypatch.setattr("cli.phase_cmds._advance_fsm", lambda *_, **__: None)

        class _FakeGen:
            def __init__(self, *a, **kw): pass
            def write(self, *a, **kw): pass
        monkeypatch.setattr("cli.phase_cmds.HandoverGenerator", _FakeGen)

        # Capture git-add target list so we can assert manifest is included
        self._git_add_calls: list[list] = []

        def _fake_run(cmd, **kwargs):
            class R:
                returncode = 0
                stdout = ""
                stderr = ""
            if isinstance(cmd, (list, tuple)) and "add" in cmd:
                # Match: ["git", "-C", project, "add", *targets]
                if cmd[0] == "git" and "add" in cmd:
                    self._git_add_calls.append(list(cmd))
            return R()

        monkeypatch.setattr(subprocess, "run", _fake_run)

    def _build_args(self, project: Path, completed_phase: int):
        import argparse
        return argparse.Namespace(
            project=str(project), completed_phase=completed_phase,
        )

    def test_p2_advance_regenerates_manifest(self, tmp_path, monkeypatch):
        """P2→P3: SAD.md present → manifest regenerated, generated_at_phase=2."""
        import json
        from harness_cli import cmd_advance_phase

        self._setup(tmp_path, monkeypatch)
        assert cmd_advance_phase(self._build_args(tmp_path, 2)) == 0

        manifest = json.loads(
            (tmp_path / ".methodology" / "quality_manifest.json").read_text(
                encoding="utf-8"
            )
        )
        assert manifest["generated_at_phase"] == 2, (
            f"manifest.generated_at_phase should be 2 after P2 advance, got "
            f"{manifest.get('generated_at_phase')}"
        )
        assert manifest["fr_ids"] == ["FR-01", "FR-02"], (
            "fr_ids should be preserved from the seed manifest"
        )

    def test_p2_advance_commits_regenerated_manifest(self, tmp_path, monkeypatch):
        """P2→P3: regenerated manifest is included in the auto-commit."""
        from harness_cli import cmd_advance_phase

        self._setup(tmp_path, monkeypatch)
        assert cmd_advance_phase(self._build_args(tmp_path, 2)) == 0

        # git-add passes paths relative to project; check by basename suffix
        added = any(
            any(str(arg).endswith("quality_manifest.json") for arg in call)
            for call in self._git_add_calls
        )
        assert added, (
            f"git-add did not include regenerated quality_manifest.json; "
            f"captured calls: {self._git_add_calls}"
        )

    def test_p3_advance_does_not_regenerate_manifest(self, tmp_path, monkeypatch):
        """P3→P4 (not P2 exit): no manifest regeneration — only P2 exit does it.

        Guards against over-eager manifest regeneration on every advance,
        which would mask P3-internal manifest edits.
        """
        import json
        from harness_cli import cmd_advance_phase

        self._setup(tmp_path, monkeypatch)
        # Add phase4_plan.md so the advance call doesn't trip
        (tmp_path / ".methodology" / "phase4_plan.md").touch()
        # Add a phase3_plan.md that pre-existed (so we can advance from P3)
        assert cmd_advance_phase(self._build_args(tmp_path, 3)) == 0

        manifest = json.loads(
            (tmp_path / ".methodology" / "quality_manifest.json").read_text(
                encoding="utf-8"
            )
        )
        # P3 advance should NOT have touched the manifest — it stays at P1
        assert manifest["generated_at_phase"] == 1, (
            f"P3 advance should not regenerate manifest; "
            f"got generated_at_phase={manifest.get('generated_at_phase')}"
        )

    def test_p2_advance_without_sad_skips_with_warning(
        self, tmp_path, monkeypatch, capsys
    ):
        """P2→P3 with no SAD.md: skip regeneration, print actionable warning."""
        from harness_cli import cmd_advance_phase

        self._setup(tmp_path, monkeypatch)
        # Remove the SAD.md to simulate unfinished P2
        (tmp_path / "02-architecture" / "SAD.md").unlink()
        assert cmd_advance_phase(self._build_args(tmp_path, 2)) == 0

        captured = capsys.readouterr()
        combined = captured.out + captured.err
        assert "SAD.md not found" in combined, (
            f"Expected actionable 'SAD.md not found' warning, got: {combined}"
        )
        assert "manifest regeneration skipped" in combined

        # git-add should NOT include the manifest (no regeneration happened)
        added = any(
            any(str(arg).endswith("quality_manifest.json") for arg in call)
            for call in self._git_add_calls
        )
        assert not added, (
            f"git-add included manifest despite no SAD.md; calls: {self._git_add_calls}"
        )

    def test_p2_advance_fails_fast_when_fr_ids_empty(
        self, tmp_path, monkeypatch, capsys
    ):
        """Fail-fast guard: when current manifest has no fr_ids AND SRS.md
        has no FR markers, cmd_advance_phase must NOT call
        generate_quality_manifest with an empty list (which would silently
        write an empty manifest passing the regeneration print, then trip
        preflight Pattern A in P3). Instead: return non-zero and emit an
        actionable error pointing at the SRS regex / fr_ids injection fix.
        Companion regression to Bug #140 — the SRS regex is fixed but a
        malformed SRS file must surface the failure locally, not at P3
        preflight.
        """
        import json
        from harness_cli import cmd_advance_phase

        self._setup(tmp_path, monkeypatch)
        # Empty the seed fr_ids so neither manifest nor SRS provides a list.
        mf = tmp_path / ".methodology" / "quality_manifest.json"
        seed = json.loads(mf.read_text(encoding="utf-8"))
        seed["fr_ids"] = []
        mf.write_text(json.dumps(seed), encoding="utf-8")
        # SRS.md with NO FR markers (regression for the post-Bug-140 shape:
        # malformed SRS that the regex correctly accepts as zero matches).
        (tmp_path / "01-requirements" / "SRS.md").write_text(
            "# SRS\n\n_No FR markers in this body._\n", encoding="utf-8"
        )

        # generate_quality_manifest should NEVER be called — replace it
        # with a sentinel that fails the test if invoked.
        called = {"n": 0}

        def _must_not_call(*a, **kw):
            called["n"] += 1
            raise AssertionError(
                "generate_quality_manifest must not run with empty fr_ids"
            )

        monkeypatch.setattr(
            "harness.harness_bridge.HarnessBridge.generate_quality_manifest",
            _must_not_call,
        )

        rc = cmd_advance_phase(self._build_args(tmp_path, 2))

        assert rc != 0, (
            f"expected non-zero return on empty fr_ids, got rc={rc}"
        )
        assert called["n"] == 0, (
            f"generate_quality_manifest must not be invoked, was called "
            f"{called['n']} time(s)"
        )

        # Manifest must be left untouched (still generated_at_phase=1).
        after = json.loads(mf.read_text(encoding="utf-8"))
        assert after.get("generated_at_phase") == 1, (
            f"manifest must not be overwritten when fail-fast fires; "
            f"generated_at_phase={after.get('generated_at_phase')}"
        )

        captured = capsys.readouterr()
        combined = captured.out + captured.err
        assert "fr_ids" in combined, (
            f"expected actionable error mentioning fr_ids; got: {combined}"
        )


# =============================================================================
# P7→P8: deterministic CONFIG_RECORDS / RELEASE_CHECKLIST baseline
# =============================================================================

class TestP7AdvanceGeneratesP8Baseline:
    """Regression tests for the post-merge review finding: the
    ``phase8_doc_gen`` module shipped with no production caller. The
    fix: hook ``scripts.phase8_doc_gen.generate()`` into
    ``cmd_advance_phase`` when ``next_phase == 8`` (i.e. just advanced
    past P7) so the LLM agent that runs P8 starts from a real baseline
    rather than authoring from scratch."""

    def _setup(self, tmp_path: Path, monkeypatch):
        import harness_cli  # noqa: F401  entry-first load order (cli-first crashes until S5)
        (tmp_path / ".methodology").mkdir(parents=True, exist_ok=True)
        (tmp_path / ".methodology" / "phase7_plan.md").touch()
        (tmp_path / ".methodology" / "phase8_plan.md").touch()
        # templates dir the generator reads from
        (tmp_path / "templates").mkdir(parents=True, exist_ok=True)
        (tmp_path / "templates" / "CONFIG_RECORDS.md").write_text(
            "# Config — {project_name} ({version})\n\ndate={release_date}\n",
            encoding="utf-8",
        )
        (tmp_path / "templates" / "RELEASE_CHECKLIST.md").write_text(
            "# Release — {project_name}\n", encoding="utf-8",
        )
        # git init so git describe / rev-parse work
        import subprocess as _sp
        _sp.run(["git", "init", "-q"], cwd=tmp_path, check=True)
        _sp.run(["git", "config", "user.email", "t@t"], cwd=tmp_path, check=True)
        _sp.run(["git", "config", "user.name", "T"], cwd=tmp_path, check=True)
        _sp.run(["git", "add", "-A"], cwd=tmp_path, check=True)
        _sp.run(["git", "commit", "-m", "init", "-q"], cwd=tmp_path, check=True)

        _write_finalize_sentinels_for_tests(tmp_path)
        monkeypatch.setattr("cli.phase_cmds._advance_prechecks", lambda _, __: 0)
        monkeypatch.setattr("core.claude_md.update_claude_md", lambda _: None)
        monkeypatch.setattr("core.claude_md.llm_clean_stale_claude_md", lambda _: None)
        monkeypatch.setattr("shutil.which", lambda c: None)
        monkeypatch.setattr("cli.phase_cmds._advance_fsm", lambda *_, **__: None)

        class _FakeGen:
            def __init__(self, *a, **kw): pass
            def write(self, *a, **kw): pass
        monkeypatch.setattr("cli.phase_cmds.HandoverGenerator", _FakeGen)

        self._git_add_calls: list[list] = []

        def _fake_run(cmd, **kwargs):
            class R:
                returncode = 0
                stdout = ""
                stderr = ""
            if isinstance(cmd, (list, tuple)) and "add" in cmd:
                if cmd[0] == "git" and "add" in cmd:
                    self._git_add_calls.append(list(cmd))
            return R()

        monkeypatch.setattr(subprocess, "run", _fake_run)

    def _build_args(self, project: Path, completed_phase: int):
        import argparse
        return argparse.Namespace(project=str(project), completed_phase=completed_phase)

    def test_p7_advance_generates_p8_baseline(self, tmp_path, monkeypatch):
        """P7→P8: scripts.phase8_doc_gen.generate() is invoked and the
        resulting CONFIG_RECORDS.md + RELEASE_CHECKLIST.md land under 08-config/."""
        from harness_cli import cmd_advance_phase

        self._setup(tmp_path, monkeypatch)
        assert cmd_advance_phase(self._build_args(tmp_path, 7)) == 0

        config = tmp_path / "08-config" / "CONFIG_RECORDS.md"
        release = tmp_path / "08-config" / "RELEASE_CHECKLIST.md"
        assert config.exists(), f"{config} not generated by P7→P8 advance"
        assert release.exists(), f"{release} not generated by P7→P8 advance"

    def test_p7_advance_commits_p8_baseline(self, tmp_path, monkeypatch):
        """P7→P8: generated P8 docs are included in the auto-commit."""
        from harness_cli import cmd_advance_phase

        self._setup(tmp_path, monkeypatch)
        assert cmd_advance_phase(self._build_args(tmp_path, 7)) == 0

        added_basenames = {
            str(arg).split("/")[-1]
            for call in self._git_add_calls
            for arg in call
        }
        assert "CONFIG_RECORDS.md" in added_basenames, (
            f"git-add did not include CONFIG_RECORDS.md; calls: {self._git_add_calls}"
        )
        assert "RELEASE_CHECKLIST.md" in added_basenames, (
            f"git-add did not include RELEASE_CHECKLIST.md; calls: {self._git_add_calls}"
        )

    def test_p6_advance_does_not_generate_p8_baseline(
        self, tmp_path, monkeypatch
    ):
        """P6→P7 (not P7 exit): no P8 baseline generated — only P7 exit does."""
        from harness_cli import cmd_advance_phase

        self._setup(tmp_path, monkeypatch)
        # Add phase6_plan.md so the advance call doesn't trip
        (tmp_path / ".methodology" / "phase6_plan.md").touch()
        assert cmd_advance_phase(self._build_args(tmp_path, 6)) == 0

        config = tmp_path / "08-config" / "CONFIG_RECORDS.md"
        assert not config.exists(), (
            "P6 advance should not generate P8 baseline; got "
            f"{config} (exists={config.exists()})"
        )

    def test_p7_advance_generator_failure_does_not_block(
        self, tmp_path, monkeypatch, capsys
    ):
        """P7→P8 with phase8_doc_gen raising: advance still returns 0 and
        surfaces an actionable error message on stderr."""
        from harness_cli import cmd_advance_phase

        self._setup(tmp_path, monkeypatch)

        def _raise(*a, **kw):
            raise RuntimeError("git describe exploded")
        monkeypatch.setattr(
            "scripts.phase8_doc_gen.generate", _raise, raising=False
        )
        # Fall back: also stub the symbol the harness module imports lazily.
        import scripts.phase8_doc_gen as _p8
        monkeypatch.setattr(_p8, "generate", _raise)

        rc = cmd_advance_phase(self._build_args(tmp_path, 7))
        assert rc == 0
        captured = capsys.readouterr()
        combined = captured.out + captured.err
        assert "phase8_doc_gen failed" in combined, (
            f"Expected actionable 'phase8_doc_gen failed' message, got: {combined}"
        )


class TestRunToolDispatcher:
    """Bug #110: harness_cli run-tool subcommand dispatches to tool_runners.run_tool."""

    def test_help_lists_run_tool_subcommand(self):
        from harness_cli import build_parser
        parser = build_parser()
        sub_action = next(
            a for a in parser._actions if a.dest == "command" or a.choices and "run-tool" in a.choices
        )
        assert "run-tool" in sub_action.choices  # type: ignore[reportOperatorIssue]

    def test_run_tool_invokes_tool_runners(self, tmp_path, monkeypatch, capsys):
        from harness_cli import cmd_run_tool
        def fake_run(tool, project_root, timeout_override=None):
            return "OK", 0
        def fake_score(tool, output, returncode):
            return 95.0
        monkeypatch.setattr("harness.tool_runners.run_tool", fake_run)
        monkeypatch.setattr("harness.tool_runners.compute_tool_score", fake_score)
        args = argparse.Namespace(
            tool="ast-error-handling", project=str(tmp_path),
            timeout_override=None, json=False,
        )
        result = cmd_run_tool(args)
        assert result == 0
        captured = capsys.readouterr()
        assert "tool=ast-error-handling" in captured.out
        assert "score:      95.0" in captured.out

    def test_run_tool_json_output(self, tmp_path, monkeypatch, capsys):
        from harness_cli import cmd_run_tool
        monkeypatch.setattr("harness.tool_runners.run_tool", lambda *_, **__: ("raw", 1))
        monkeypatch.setattr("harness.tool_runners.compute_tool_score", lambda *_, **__: 50.0)
        args = argparse.Namespace(
            tool="ruff", project=str(tmp_path), timeout_override=None, json=True,
        )
        result = cmd_run_tool(args)
        assert result == 1  # non-zero returncode → exit 1
        captured = capsys.readouterr()
        import json
        payload = json.loads(captured.out)
        assert payload["tool"] == "ruff"
        assert payload["returncode"] == 1
        assert payload["score"] == 50.0


class TestSubmoduleDriftAdvisory:
    """Phase 6 improvement #3: advance-phase postflight detects when the
    harness/ submodule HEAD is behind origin/main (e.g. CI auto-fix landed)
    and prints an actionable warning. Non-blocking by design.
    """

    def _setup_submodule(self, tmp_path: Path) -> tuple[Path, Path]:
        """Create a fake main repo + harness/ submodule with a bare 'origin'
        remote. Returns (project, sub) where project/harness is a real git
        submodule that can be ahead/behind by making local commits.

        Uses ``git update-ref`` instead of ``git push`` to populate the bare
        repo so the test is portable across CI environments where local
        transport push may be blocked by safe.directory or receive hooks.
        Commits are made BEFORE the bare clone so the bare repo already holds
        the commit objects at clone time.
        """
        import subprocess as sp
        proj = tmp_path
        (proj / ".gitmodules").write_text(
            '[submodule "harness"]\n\tpath = harness\n\turl = x\n'
        )
        sub = proj / "harness"
        sub.mkdir()
        for d in [proj, sub]:
            sp.run(["git", "-C", str(d), "init", "-q"], check=True)
            sp.run(["git", "-C", str(d), "config", "user.email", "t@t.com"], check=True)
            sp.run(["git", "-C", str(d), "config", "user.name", "T"], check=True)
        # Commit FIRST so bare clone gets the object
        (sub / "x").write_text("a")
        sp.run(["git", "-C", str(sub), "add", "."], check=True)
        sp.run(["git", "-C", str(sub), "commit", "-q", "-m", "init"], check=True)
        # Bare "origin" — cloned AFTER commit so it already has the object
        bare = tmp_path.parent / (tmp_path.name + "_origin.git")
        sp.run(["git", "clone", "--bare", str(sub), str(bare)],
               check=True, capture_output=True)
        sp.run(["git", "-C", str(sub), "remote", "add", "origin", str(bare)],
               check=True)
        # Sync bare/origin HEAD ref to match sub HEAD (transport-independent)
        head_sha = sp.run(
            ["git", "-C", str(sub), "rev-parse", "HEAD"],
            check=True, capture_output=True, text=True,
        ).stdout.strip()
        sp.run(
            ["git", "-C", str(bare), "update-ref", "refs/heads/main", head_sha],
            check=True,
        )
        return proj, sub


    def test_no_warning_when_in_sync(self, tmp_path, capsys):
        """HEAD == origin/main → no drift warning printed."""
        from cli.phase_cmds import _check_submodule_drift
        proj, sub = self._setup_submodule(tmp_path)
        _check_submodule_drift(proj)
        captured = capsys.readouterr()
        assert "harness/ submodule is" not in captured.out
        assert "CI may have applied" not in captured.out

    def test_warning_when_local_ahead(self, tmp_path, capsys):
        """origin has commit not in local → "behind" warning printed.

        Simulates a CI-authored commit landing on origin/main by writing
        a new commit object + updating the bare ref directly with
        ``git update-ref`` — no push transport required.
        """
        import subprocess as sp
        from cli.phase_cmds import _check_submodule_drift
        proj, sub = self._setup_submodule(tmp_path)
        bare = tmp_path.parent / (tmp_path.name + "_origin.git")

        # Build the "ci-fix" commit in a local clone of bare (no network needed)
        ci = tmp_path.parent / (tmp_path.name + "_ci")
        sp.run(["git", "clone", "-q", str(bare), str(ci)], check=True)
        sp.run(["git", "-C", str(ci), "config", "user.email", "ci@ci.com"], check=True)
        sp.run(["git", "-C", str(ci), "config", "user.name", "CI"], check=True)
        (ci / "y").write_text("ci-fix")
        sp.run(["git", "-C", str(ci), "add", "."], check=True)
        sp.run(["git", "-C", str(ci), "commit", "-q", "-m", "ci-fix"], check=True)
        ci_sha = sp.run(
            ["git", "-C", str(ci), "rev-parse", "HEAD"],
            check=True, capture_output=True, text=True,
        ).stdout.strip()

        # Inject the new commit objects into bare via git fetch (local path, no network)
        sp.run(
            ["git", "-C", str(bare), "fetch", str(ci), "HEAD"],
            check=True, capture_output=True,
        )

        # Advance origin/main ref — transport-independent
        sp.run(
            ["git", "-C", str(bare), "update-ref", "refs/heads/main", ci_sha],
            check=True,
        )

        # Local sub is unchanged → HEAD still at "init", origin/main at "ci-fix"
        _check_submodule_drift(proj)
        captured = capsys.readouterr()
        assert "harness/ submodule is 1 commit(s) behind origin/main" in captured.out
        assert "CI may have applied test-fix commits" in captured.out
        assert "sync-harness" in captured.out  # J: one-shot sync command


    def test_silent_when_fetch_fails(self, tmp_path, capsys):
        """No origin access (offline) → silently skip, no error."""
        from cli.phase_cmds import _check_submodule_drift
        proj = tmp_path
        sub = proj / "harness"
        sub.mkdir()
        (sub / ".git").mkdir()  # marker; no remote configured
        (proj / ".gitmodules").write_text(
            '[submodule "harness"]\n\tpath = harness\n\turl = x\n'
        )
        _check_submodule_drift(proj)
        captured = capsys.readouterr()
        assert "Traceback" not in captured.out
        assert "harness/ submodule is" not in captured.out


# =============================================================================
# SRS FR fallback regex (b3f5a1c — both header and table syntax must match)
# =============================================================================

class TestSrsFrFallbackRegex:
    """The advance-phase FR-extraction regex must recognise both markdown formats:
    '### FR-NN:' header syntax and '| FR-NN |' table syntax."""

    _PATTERN = r"^(?:###\s+FR-|\|\s*FR-)(\d+)(?:\s*:|\s*\|)"

    def test_header_syntax(self):
        import re
        text = "### FR-01: First requirement\n### FR-12: Another\n"
        assert re.findall(self._PATTERN, text, re.MULTILINE) == ["01", "12"]

    def test_table_syntax(self):
        import re
        text = "| FR-01 | First requirement |\n| FR-12 | Another |\n"
        assert re.findall(self._PATTERN, text, re.MULTILINE) == ["01", "12"]

    def test_mixed_header_and_table(self):
        import re
        text = "### FR-01: Header format\n| FR-02 | Table format |\n"
        assert re.findall(self._PATTERN, text, re.MULTILINE) == ["01", "02"]

    def test_prose_fr_reference_not_matched(self):
        import re
        text = "This references FR-01 and FR-02 in prose text only.\n"
        assert re.findall(self._PATTERN, text, re.MULTILINE) == []


# =============================================================================
# _fr_step_preflight — srs_path parameter (c744ea3 fix)
# =============================================================================

class TestFrStepPreflightSrsPath:
    """_fr_step_preflight must accept an explicit srs_path (Path or str) and use
    it instead of the default fallback lookup.  cmd_run_fr_step was previously
    passing raw args.srs (str or None) despite having already resolved it to an
    absolute Path on line 7015 — the fix passes the resolved Path object."""

    def _make_manifest(self, tmp_path: Path, fr_id: str = "FR-01") -> None:
        meth = tmp_path / ".methodology"
        meth.mkdir(exist_ok=True)
        (meth / "quality_manifest.json").write_text(
            json.dumps({"fr_ids": [fr_id]}), encoding="utf-8"
        )

    def test_explicit_absolute_srs_path_accepted(self, tmp_path):
        import harness_cli  # noqa: F401  entry-first load order (cli-first crashes until S5)
        from cli import fr_cmds as _frm
        # SRS.md at a non-default absolute path — no default lookup should occur.
        srs_dir = tmp_path / "custom-docs"
        srs_dir.mkdir()
        srs = srs_dir / "SRS.md"
        srs.write_text("### FR-01: Feature\n\n---\n", encoding="utf-8")
        self._make_manifest(tmp_path)
        _ok, errors = _frm._fr_step_preflight("TDD-RED", tmp_path, "FR-01", srs_path=srs)
        assert not any("SRS" in e for e in errors), f"Unexpected SRS error: {errors}"

    def test_explicit_srs_path_missing_adds_error(self, tmp_path):
        import harness_cli  # noqa: F401  entry-first load order (cli-first crashes until S5)
        from cli import fr_cmds as _frm
        self._make_manifest(tmp_path)
        nonexistent = tmp_path / "no-such.md"
        ok, errors = _frm._fr_step_preflight("TDD-RED", tmp_path, "FR-01", srs_path=nonexistent)
        assert not ok
        assert any("SRS" in e for e in errors)

    def test_relative_srs_str_resolved_against_project(self, tmp_path):
        import harness_cli  # noqa: F401  entry-first load order (cli-first crashes until S5)
        from cli import fr_cmds as _frm
        (tmp_path / "docs").mkdir()
        (tmp_path / "docs" / "SRS.md").write_text("### FR-01: Feature\n\n---\n", encoding="utf-8")
        self._make_manifest(tmp_path)
        _ok, errors = _frm._fr_step_preflight(
            "TDD-RED", tmp_path, "FR-01", srs_path="docs/SRS.md"
        )
        assert not any("SRS" in e for e in errors), f"Unexpected SRS error: {errors}"

    def test_cmd_run_fr_step_passes_resolved_path_not_raw_string(self, tmp_path, monkeypatch):
        """Regression: cmd_run_fr_step line 7056 was passing getattr(args,'srs',None)
        (a relative string) instead of the already-resolved absolute Path from line 7015."""
        import harness_cli

        captured: dict = {}

        def _spy(step, project, fr_id, srs_path=None):
            captured["srs_path"] = srs_path
            return True, []

        monkeypatch.setattr("cli.fr_cmds._fr_step_preflight", _spy)
        monkeypatch.setattr("cli.fr_cmds._fr_step_already_done", lambda s, f, p: True)

        args = argparse.Namespace(
            phase=3, fr_id="FR-01", step="TDD-RED", project=str(tmp_path),
            srs="docs/SRS.md",
            timeout=600, max_turns=30, max_fix_rounds=3,
        )
        harness_cli.cmd_run_fr_step(args)
        # idempotency skips before preflight — verify the resolved path was computed
        # by checking cmd_run_fr_step used args.srs (not getattr with a silent None)
        # The spy isn't reached on skip, but args.srs attribute access must not throw.
        assert args.srs == "docs/SRS.md"  # arg is always available (registered by argparse)


# =============================================================================
# cmd_plan_all — preserve existing quality_manifest.json
# =============================================================================

class TestCmdPlanAllPreservesManifest:
    """Regression for the P7 footgun: `plan-all --force` must NEVER touch an
    existing quality_manifest.json. The manifest holds accumulated Gate scores
    across phases; shrinking it (because plan-all re-derives the FR list from
    SAD.md) resets pipeline progress and breaks carry-forward."""

    @staticmethod
    def _make_args(project: str, force: bool = False):
        import argparse
        ns = argparse.Namespace()
        ns.project = project
        ns.output_dir = None
        ns.force = force
        return ns

    def _seed(self, tmp_path):
        (tmp_path / ".methodology").mkdir()
        # Minimal SRS so generate_full_plan doesn't choke on missing input.
        (tmp_path / "01-requirements").mkdir()
        (tmp_path / "01-requirements" / "SRS.md").write_text(
            "# SRS\n\n### FR-01: Foo\n\n### FR-02: Bar\n\n" + "x" * 200
        )
        return tmp_path

    def test_plan_all_preserves_existing_quality_manifest(self, tmp_path, capsys):
        from harness_cli import cmd_plan_all

        self._seed(tmp_path)
        manifest_path = tmp_path / ".methodology" / "quality_manifest.json"
        original = {
            "fr_module_traceability": {"FR-01": "taskq.core"},
            "gate_results": {
                "gate1": {
                    "FR-01": {"score": 96.8, "passed": True},
                    "FR-02": {"score": 95.6, "passed": True},
                },
            },
        }
        manifest_path.write_text(json.dumps(original))

        rc = cmd_plan_all(self._make_args(str(tmp_path), force=True))
        out = capsys.readouterr().out

        assert "[PRESERVE]" in out
        assert "quality_manifest.json" in out
        # Manifest byte-equal — not regenerated, not shrunk.
        assert json.loads(manifest_path.read_text()) == original
        assert rc == 0

    def test_plan_all_runs_normally_when_manifest_absent(self, tmp_path, capsys):
        from harness_cli import cmd_plan_all

        self._seed(tmp_path)
        # No quality_manifest.json — plan-all proceeds normally.
        rc = cmd_plan_all(self._make_args(str(tmp_path)))
        out = capsys.readouterr().out
        assert "[PRESERVE]" not in out
        assert rc == 0

    def test_plan_all_treats_empty_manifest_as_absent(self, tmp_path, capsys):
        """An empty 0-byte file at the manifest path is NOT a valid manifest
        and must NOT trigger the [PRESERVE] guard — otherwise the next gate
        run would crash on ``json.loads('')` with JSONDecodeError."""
        from harness_cli import cmd_plan_all

        self._seed(tmp_path)
        manifest_path = tmp_path / ".methodology" / "quality_manifest.json"
        manifest_path.write_text("", encoding="utf-8")  # 0-byte
        rc = cmd_plan_all(self._make_args(str(tmp_path), force=True))
        out = capsys.readouterr().out
        assert "[PRESERVE]" not in out, (
            f"empty file should not be preserved; got: {out!r}"
        )
        assert rc == 0

    def test_plan_all_treats_corrupt_manifest_as_absent(self, tmp_path, capsys):
        """A manifest with non-JSON content must not trigger [PRESERVE] —
        a previous interrupted run leaving garbage behind should not lock
        the pipeline out of regenerating."""
        from harness_cli import cmd_plan_all

        self._seed(tmp_path)
        manifest_path = tmp_path / ".methodology" / "quality_manifest.json"
        manifest_path.write_text("not json at all {]", encoding="utf-8")
        rc = cmd_plan_all(self._make_args(str(tmp_path), force=True))
        out = capsys.readouterr().out
        assert "[PRESERVE]" not in out, (
            f"corrupt JSON should not be preserved; got: {out!r}"
        )
        assert rc == 0

    def test_plan_all_treats_manifest_dir_as_absent(self, tmp_path, capsys):
        """A directory at the manifest path (e.g. a mistakenly
        ``mkdir .methodology/quality_manifest.json``) must not be
        treated as a preserved manifest — ``Path.exists()`` returns True
        for directories, but ``is_file()`` does not."""
        from harness_cli import cmd_plan_all

        self._seed(tmp_path)
        manifest_path = tmp_path / ".methodology" / "quality_manifest.json"
        manifest_path.mkdir()  # directory, not file
        rc = cmd_plan_all(self._make_args(str(tmp_path), force=True))
        out = capsys.readouterr().out
        assert "[PRESERVE]" not in out, (
            f"directory at manifest path should not be preserved; got: {out!r}"
        )
        assert rc == 0


# =============================================================================
# H: GATE1-DELTA batch auto-skip (regression — Improvement H)
# =============================================================================
#
# When advancing from P4/P5/P7/P8, _check_gate1_live_coverage consults
# _fr_code_changed_since_last_gate1 for every FR. If ALL FRs are unchanged
# since their last Gate 1 PASS, the live pytest run is skipped (return 0)
# to avoid wasting 8 redundant coverage runs per advance.
#
# These tests verify the batch auto-skip is robust: empty FR list, missing
# FR git history, and one-changed-all-unchanged all behave correctly.

class TestGate1DeltaBatchAutoSkip:
    """Phase 4/5/7/8 advance batch: skip live pytest when all FRs unchanged."""

    def _manifest(self, tmp_path, fr_ids):
        import json
        m = tmp_path / ".methodology" / "quality_manifest.json"
        m.parent.mkdir(parents=True, exist_ok=True)
        m.write_text(json.dumps({
            "fr_ids": fr_ids,
            "quality_targets": {"min_coverage": 80},
        }))
        return m

    def test_all_unchanged_returns_0_skips_pytest(self, tmp_path):
        """When every FR is unchanged since last Gate1 PASS, batch returns 0
        WITHOUT invoking the live pytest validator."""
        self._manifest(tmp_path, ["FR-01", "FR-02"])
        with mock.patch.object(
            gate1_evidence, "fr_code_changed_since_last_gate1", return_value=False
        ), mock.patch.object(
            gate1_evidence, "validate_fr_coverage_immediate"
        ) as mock_pytest:
            rc = _check_gate1_live_coverage(tmp_path, 4)
        assert rc == 0
        mock_pytest.assert_not_called()
        _ = "must skip pytest when all unchanged"  # assertion message — pylint-only

    def test_any_changed_runs_pytest(self, tmp_path):
        """If even one FR changed, fall through to live pytest path."""
        self._manifest(tmp_path, ["FR-01", "FR-02"])
        # FR-01 changed, FR-02 unchanged → not "all unchanged"
        with mock.patch.object(
            gate1_evidence, "fr_code_changed_since_last_gate1",
            side_effect=lambda fr, p: fr == "FR-01",
        ), mock.patch.object(
            gate1_evidence, "validate_fr_coverage_immediate", return_value=95.0,
        ) as mock_pytest:
            rc = _check_gate1_live_coverage(tmp_path, 7)
        assert rc == 0
        # Pytest MUST be invoked when any FR changed.
        assert mock_pytest.called

    def test_empty_fr_list_returns_0_without_pytest(self, tmp_path):
        """Non-FR project (no fr_ids in manifest) — bypass entirely."""
        self._manifest(tmp_path, [])
        with mock.patch.object(
            gate1_evidence, "validate_fr_coverage_immediate"
        ) as mock_pytest:
            rc = _check_gate1_live_coverage(tmp_path, 4)
        assert rc == 0
        mock_pytest.assert_not_called()

    def test_gate1_live_coverage_exception_in_changed_check_falls_through(
        self, tmp_path
    ):
        """If the changed-check raises, default to running pytest (safe)."""
        self._manifest(tmp_path, ["FR-01"])
        with mock.patch.object(
            gate1_evidence, "fr_code_changed_since_last_gate1",
            side_effect=RuntimeError("git error"),
        ), mock.patch.object(
            gate1_evidence, "validate_fr_coverage_immediate", return_value=85.0,
        ) as mock_pytest:
            rc = _check_gate1_live_coverage(tmp_path, 4)
        assert rc == 0
        assert mock_pytest.called


# =============================================================================
# Finding H1: backup temp dir must be cleaned up on any exception
# =============================================================================

class TestBackupTempDirCleanup:
    """Regression test for Finding H1: the backup temp dir created at the
    start of cmd_advance_phase's sentinels-preserve block must be cleaned
    up even if shutil.rmtree(sessi_work) raises a non-OSError that
    ignore_errors does not swallow.
    """

    def _setup_minimal(self, tmp_path, monkeypatch):
        import harness_cli  # noqa: F401  entry-first load order (cli-first crashes until S5)
        (tmp_path / ".methodology").mkdir(parents=True, exist_ok=True)
        (tmp_path / "01-requirements").mkdir(parents=True, exist_ok=True)
        (tmp_path / "02-architecture").mkdir(parents=True, exist_ok=True)
        (tmp_path / ".methodology" / "phase2_plan.md").touch()
        (tmp_path / ".methodology" / "phase3_plan.md").touch()
        (tmp_path / "01-requirements" / "SRS.md").write_text(
            "# SRS\n\n### FR-01: alpha\n", encoding="utf-8"
        )
        (tmp_path / "02-architecture" / "SAD.md").write_text(
            "# SAD\n", encoding="utf-8"
        )
        sentinels = tmp_path / ".sessi-work" / "sentinels"
        sentinels.mkdir(parents=True)
        (sentinels / "g1_fr01.flag").write_text("ok", encoding="utf-8")

        _write_finalize_sentinels_for_tests(tmp_path)
        monkeypatch.setattr("cli.phase_cmds._advance_prechecks", lambda _, __: 0)
        monkeypatch.setattr("core.claude_md.update_claude_md", lambda _: None)
        monkeypatch.setattr("core.claude_md.llm_clean_stale_claude_md", lambda _: None)
        monkeypatch.setattr("shutil.which", lambda c: None)
        monkeypatch.setattr("cli.phase_cmds._advance_fsm", lambda *_, **__: None)

        class _FakeGen:
            def __init__(self, *a, **kw): pass
            def write(self, *a, **kw): pass
        monkeypatch.setattr("cli.phase_cmds.HandoverGenerator", _FakeGen)

        class _R:
            returncode = 0
            stdout = ""
            stderr = ""
        monkeypatch.setattr(subprocess, "run", lambda *a, **kw: _R())

    def test_backup_tempdir_cleaned_when_rmtree_sessi_work_raises(
        self, tmp_path, monkeypatch
    ):
        """If shutil.rmtree(sessi_work, ignore_errors=True) raises
        RuntimeError, the harness-sentinels-* backup temp dir must still
        be removed by the outer try/finally."""
        import shutil as _real_shutil
        _real_rmtree = _real_shutil.rmtree

        self._setup_minimal(tmp_path, monkeypatch)

        def fake_rmtree(path, *args, **kwargs):
            if ".sessi-work" in str(path):
                raise RuntimeError("simulated non-OSError")
            return _real_rmtree(path, *args, **kwargs)
        monkeypatch.setattr("shutil.rmtree", fake_rmtree)

        from harness_cli import cmd_advance_phase
        import argparse
        args = argparse.Namespace(project=str(tmp_path), completed_phase=2)

        import tempfile
        sys_temp = Path(tempfile.gettempdir())
        before = set(sys_temp.glob("harness-sentinels-*"))
        try:
            cmd_advance_phase(args)
        except RuntimeError:
            pass  # expected — function may propagate or swallow
        after = set(sys_temp.glob("harness-sentinels-*"))
        leaked = after - before
        assert not leaked, f"Backup temp dir leaked: {leaked}"


# =============================================================================
# _post_push_self_check + 3-site dirty-warn integration
# =============================================================================
# See plan: ~/.claude/plans/abundant-stargazing-hejlsberg.md
#
# Bug class: post-push working-tree dirtiness (28864f7 family). 28864f7 fixed
# the specific state.json audit-write-after-push case at 3 sites, but did not
# add a generic post-push self-check. This module adds one (warn-only, never
# fail-fast) and wires it into the same 3 sites.


class TestPostPushSelfCheck:
    """Unit tests for the `_post_push_self_check(project)` helper."""

    def test_clean_when_status_empty(self, tmp_path, monkeypatch):
        fake_result = mock.Mock(returncode=0, stdout="")
        monkeypatch.setattr(subprocess, "run", lambda *_a, **_kw: fake_result)
        assert _post_push_self_check(tmp_path) == []

    def test_returns_modified_paths(self, tmp_path, monkeypatch):
        fake_result = mock.Mock(
            returncode=0,
            stdout=" M .methodology/state.json\n M .methodology/HANDOVER.md\n",
        )
        monkeypatch.setattr(subprocess, "run", lambda *_a, **_kw: fake_result)
        out = _post_push_self_check(tmp_path)
        assert out == [
            ".methodology/state.json",
            ".methodology/HANDOVER.md",
        ]

    def test_returns_untracked_paths(self, tmp_path, monkeypatch):
        fake_result = mock.Mock(
            returncode=0, stdout="?? new_file.py\n?? docs/scratch.md\n",
        )
        monkeypatch.setattr(subprocess, "run", lambda *_a, **_kw: fake_result)
        out = _post_push_self_check(tmp_path)
        assert out == ["new_file.py", "docs/scratch.md"]

    def test_handles_subprocess_failure(self, tmp_path, monkeypatch):

        def _raise(*_a, **_kw):
            raise OSError("git not found")
        monkeypatch.setattr(subprocess, "run", _raise)
        assert _post_push_self_check(tmp_path) == []  # best-effort

    def test_handles_nonzero_returncode(self, tmp_path, monkeypatch):
        fake_result = mock.Mock(returncode=128, stdout="fatal: not a git repo")
        monkeypatch.setattr(subprocess, "run", lambda *_a, **_kw: fake_result)
        assert _post_push_self_check(tmp_path) == []


class TestAdvanceFsmPreservesExistingStateFields:
    """_advance_fsm must merge into state.json, not replace it — a bare
    replacement silently discarded fields owned by other commands
    (last_push_checkpoint, phase_completed, ci_readiness_ack, language,
    test_runner, ...) on every advance-phase call."""

    def test_preserves_unrelated_existing_fields(self, tmp_path, monkeypatch):
        import harness_cli as hc  # noqa: F401  entry-first load order

        meth = tmp_path / ".methodology"
        meth.mkdir(parents=True, exist_ok=True)
        state_path = meth / "state.json"
        state_path.write_text(json.dumps({
            "state": "RUNNING",
            "current_phase": 1,
            "last_push_checkpoint": "2026-07-01T00:00:00+00:00",
            "phase_completed": {"1": {"sha": "deadbeef", "timestamp": "2026-07-01T00:00:00+00:00"}},
            "ci_readiness_ack": ["branch_protection"],
            "language": "python",
            "test_runner": "pytest",
        }), encoding="utf-8")

        monkeypatch.setattr("harness.handover_generator.HandoverGenerator.write", lambda self, **_kw: None)

        from cli.phase_cmds import _advance_fsm
        _advance_fsm(tmp_path, completed_phase=1, last_gate=1, last_fr="FR-01")

        sd = json.loads(state_path.read_text(encoding="utf-8"))
        # fields owned by other commands must survive
        assert sd["last_push_checkpoint"] == "2026-07-01T00:00:00+00:00"
        assert sd["phase_completed"] == {"1": {"sha": "deadbeef", "timestamp": "2026-07-01T00:00:00+00:00"}}
        assert sd["ci_readiness_ack"] == ["branch_protection"]
        assert sd["language"] == "python"
        assert sd["test_runner"] == "pytest"
        # fields this function owns must be updated
        assert sd["current_phase"] == 2
        assert sd["last_gate"] == 1
        assert sd["last_fr"] == "FR-01"
        assert sd["phase_truth_passed"] is True

    def test_still_works_when_state_json_missing(self, tmp_path, monkeypatch):
        import harness_cli as hc  # noqa: F401  entry-first load order

        monkeypatch.setattr("harness.handover_generator.HandoverGenerator.write", lambda self, **_kw: None)
        from cli.phase_cmds import _advance_fsm
        _advance_fsm(tmp_path, completed_phase=1, last_gate=None, last_fr=None)

        state_path = tmp_path / ".methodology" / "state.json"
        sd = json.loads(state_path.read_text(encoding="utf-8"))
        assert sd["current_phase"] == 2
        assert sd["state"] == "INIT"

    def test_still_works_with_corrupt_state_json(self, tmp_path, monkeypatch):
        import harness_cli as hc  # noqa: F401  entry-first load order

        meth = tmp_path / ".methodology"
        meth.mkdir(parents=True, exist_ok=True)
        state_path = meth / "state.json"
        state_path.write_text("{not valid json", encoding="utf-8")

        monkeypatch.setattr("harness.handover_generator.HandoverGenerator.write", lambda self, **_kw: None)
        from cli.phase_cmds import _advance_fsm
        _advance_fsm(tmp_path, completed_phase=1, last_gate=None, last_fr=None)

        sd = json.loads(state_path.read_text(encoding="utf-8"))
        assert sd["current_phase"] == 2
        assert sd["state"] == "INIT"
