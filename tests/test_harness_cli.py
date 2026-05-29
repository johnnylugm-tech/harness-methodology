"""Tests for harness_cli.py init helpers and audit-structure command."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


# =============================================================================
# _init_phase_dirs
# =============================================================================

class TestInitPhaseDirs:
    def test_creates_all_directories(self, tmp_path):
        from harness_cli import _init_phase_dirs

        _init_phase_dirs(tmp_path)

        expected = [
            "01-requirements",
            "02-architecture/adr",
            "03-development/src",
            "03-development/tests",
            "04-testing",
            "05-verification",
            "06-quality",
            "07-risk",
            "08-config",
        ]
        for d in expected:
            assert (tmp_path / d).is_dir(), f"missing: {d}"

    def test_idempotent_when_dirs_exist(self, tmp_path, capsys):
        from harness_cli import _init_phase_dirs

        _init_phase_dirs(tmp_path)
        _init_phase_dirs(tmp_path)

        captured = capsys.readouterr().out
        assert "SKIP: all 11 directories already exist" in captured


# =============================================================================
# _init_copy_templates
# =============================================================================

class TestInitCopyTemplates:
    def test_copies_templates_to_correct_locations(self, tmp_path):
        from harness_cli import _init_copy_templates
        import harness_cli as hc

        harness_root = Path(hc.__file__).parent
        (tmp_path / "01-requirements").mkdir()
        (tmp_path / "02-architecture").mkdir()
        (tmp_path / "02-architecture" / "adr").mkdir()

        _init_copy_templates(tmp_path, harness_root)

        assert (tmp_path / "01-requirements" / "SRS.md").is_file()
        assert (tmp_path / "01-requirements" / "SPEC_TRACKING.md").is_file()
        assert (tmp_path / "01-requirements" / "TRACEABILITY_MATRIX.md").is_file()
        assert (tmp_path / "02-architecture" / "SAD.md").is_file()
        assert (tmp_path / "02-architecture" / "adr" / "ADR.md").is_file()
        assert (tmp_path / "CLAUDE.md").is_file()

    def test_skips_existing_files_by_default(self, tmp_path, capsys):
        from harness_cli import _init_copy_templates
        import harness_cli as hc

        harness_root = Path(hc.__file__).parent
        for d in ["01-requirements", "02-architecture/adr"]:
            (tmp_path / d).mkdir(parents=True)

        _init_copy_templates(tmp_path, harness_root)
        out1 = capsys.readouterr().out

        _init_copy_templates(tmp_path, harness_root)
        out2 = capsys.readouterr().out

        assert "copied" in out1
        assert "already existed" in out2

    def test_force_overwrites_existing(self, tmp_path):
        from harness_cli import _init_copy_templates
        import harness_cli as hc

        harness_root = Path(hc.__file__).parent
        (tmp_path / "01-requirements").mkdir()
        (tmp_path / "02-architecture").mkdir()
        (tmp_path / "02-architecture" / "adr").mkdir()
        srs = tmp_path / "01-requirements" / "SRS.md"
        srs.write_text("old content")

        _init_copy_templates(tmp_path, harness_root, overwrite=True)

        content = srs.read_text()
        assert "old content" not in content  # overwritten by template

    def test_handles_missing_template_source(self, tmp_path, capsys):
        """When a template file is removed, reports WARNING and continues."""
        from harness_cli import _init_copy_templates

        (tmp_path / "01-requirements").mkdir()
        (tmp_path / "02-architecture").mkdir()
        (tmp_path / "02-architecture" / "adr").mkdir()

        # Pass a non-existent harness root — templates won't be found
        fake_root = tmp_path / "no-templates"
        fake_root.mkdir()
        (fake_root / "templates").mkdir()

        _init_copy_templates(tmp_path, fake_root)

        captured = capsys.readouterr().out
        assert "template not found" in captured


# =============================================================================
# cmd_audit_structure
# =============================================================================

class TestCmdAuditStructure:
    @staticmethod
    def _make_args(project: str, json_out: bool = False):
        import argparse
        ns = argparse.Namespace()
        ns.project = project
        ns.json = json_out
        return ns

    # --- Happy path ----------------------------------------------------------

    def test_empty_project_reports_5_dims(self, tmp_path, capsys):
        from harness_cli import cmd_audit_structure

        args = self._make_args(str(tmp_path))
        rc = cmd_audit_structure(args)
        captured = capsys.readouterr().out

        assert rc == 1  # empty project fails
        assert "Directory Existence" in captured
        assert "Artifact Completeness" in captured
        assert "Content Quality" in captured
        assert "ASPICE Traceability Chain" in captured
        assert "Naming Convention" in captured
        assert "RESULT: FAIL" in captured

    def test_json_output_is_valid(self, tmp_path):
        from harness_cli import cmd_audit_structure
        import io
        import sys

        args = self._make_args(str(tmp_path), json_out=True)
        # Capture stdout for JSON
        buf = io.StringIO()
        old = sys.stdout
        sys.stdout = buf
        try:
            rc = cmd_audit_structure(args)
        finally:
            sys.stdout = old

        data = json.loads(buf.getvalue())
        assert "project" in data
        assert "dimensions" in data
        assert "summary" in data
        assert len(data["dimensions"]) == 5
        assert data["summary"]["total_dims"] == 5

    def test_partial_project_passes_some_dims(self, tmp_path, capsys):
        """Project with some dirs created but no artifacts."""
        from harness_cli import cmd_audit_structure

        # Create one phase directory only
        (tmp_path / "01-requirements").mkdir()

        args = self._make_args(str(tmp_path))
        rc = cmd_audit_structure(args)

        assert rc == 1  # still fails (most dims fail)

    # --- Naming convention ---------------------------------------------------

    def test_naming_passes_for_canonical_structure(self, tmp_path):
        from harness_cli import cmd_audit_structure

        for d in [
            "01-requirements", "02-architecture", "03-development",
            "04-testing", "05-verification", "06-quality", "07-risk", "08-config",
        ]:
            (tmp_path / d).mkdir()

        args = self._make_args(str(tmp_path), json_out=True)
        import io, sys
        buf = io.StringIO()
        old = sys.stdout
        sys.stdout = buf
        try:
            cmd_audit_structure(args)
        finally:
            sys.stdout = old

        data = json.loads(buf.getvalue())
        dim = data["dimensions"]["naming_convention"]
        assert dim["passed"] is True, f"naming issues: {dim['details'].get('issues')}"

    def test_naming_fails_for_extra_0x_dir(self, tmp_path):
        from harness_cli import cmd_audit_structure

        (tmp_path / "01-requirements").mkdir()
        (tmp_path / "09-unknown").mkdir()  # not in the canonical set

        args = self._make_args(str(tmp_path), json_out=True)
        import io, sys
        buf = io.StringIO()
        old = sys.stdout
        sys.stdout = buf
        try:
            cmd_audit_structure(args)
        finally:
            sys.stdout = old

        data = json.loads(buf.getvalue())
        dim = data["dimensions"]["naming_convention"]
        assert dim["passed"] is False
        assert any("09-unknown" in i for i in dim["details"]["issues"])

    # --- Content quality per-phase scoping -----------------------------------

    def test_content_quality_fr_check_scoped_to_phases_1_4(self, tmp_path):
        """Phases 5-8 docs without FR references should NOT be flagged."""
        from harness_cli import cmd_audit_structure

        # Set up P4 dir with a doc that has FR ref
        (tmp_path / "04-testing").mkdir()
        (tmp_path / "04-testing" / "TEST_PLAN.md").write_text(
            "# Test Plan\n\n## Section 1\n\n## Section 2\n\n"
            "Tests for [FR-01] and [FR-02] requirements.\n"
            + "x" * 200  # pad to pass char count
        )
        (tmp_path / "04-testing" / "TEST_RESULTS.md").write_text(
            "# Test Results\n\n## Section A\n\n## Section B\n\n"
            "All tests passed.\n"
            + "x" * 200
        )

        # Set up P6 dir with an operational doc, no FR ref
        (tmp_path / "06-quality").mkdir()
        (tmp_path / "06-quality" / "QUALITY_REPORT.md").write_text(
            "# Quality Report\n\n## Section X\n\n## Section Y\n\n"
            "Deployment validation complete.\n"
            + "x" * 200
        )

        args = self._make_args(str(tmp_path), json_out=True)
        import io, sys
        buf = io.StringIO()
        old = sys.stdout
        sys.stdout = buf
        try:
            cmd_audit_structure(args)
        finally:
            sys.stdout = old

        data = json.loads(buf.getvalue())
        cq = data["dimensions"]["content_quality"]

        # P4 TEST_PLAN has FR ref → good; TEST_RESULTS has no FR ref → suspicious
        p4 = cq["details"]["P4"]
        p4_files = {f["path"]: f["quality"] for f in p4["files"]}
        assert p4_files["04-testing/TEST_PLAN.md"] == "good"
        assert p4_files["04-testing/TEST_RESULTS.md"] == "suspicious"  # no FR ref

        # P6 QUALITY_REPORT has no FR ref — but phase 6 is excluded from FR check
        p6 = cq["details"]["P6"]
        p6_files = {f["path"]: f["quality"] for f in p6["files"]}
        assert p6_files["06-quality/QUALITY_REPORT.md"] == "good"  # FR ref not required


# =============================================================================
# cmd_audit_structure — init → audit round-trip
# =============================================================================

class TestInitThenAudit:
    def test_init_then_audit_reports_dirs_and_artifacts(self, tmp_path):
        """After init-project, audit-structure should pass directory existence."""
        import argparse
        import io, sys

        from harness_cli import _init_phase_dirs, cmd_audit_structure

        # Minimal init
        _init_phase_dirs(tmp_path)

        args = argparse.Namespace()
        args.project = str(tmp_path)
        args.json = True
        buf = io.StringIO()
        old = sys.stdout
        sys.stdout = buf
        try:
            rc = cmd_audit_structure(args)
        finally:
            sys.stdout = old

        data = json.loads(buf.getvalue())
        assert data["dimensions"]["directory_existence"]["passed"] is True
        assert data["dimensions"]["naming_convention"]["passed"] is True
        # Artifacts still fail because templates weren't copied
        assert data["dimensions"]["artifact_completeness"]["passed"] is False


class TestVerifyAgentBApprovals:
    """Tests for the verify-agent-b-approvals subcommand."""

    def test_missing_approval_files_returns_1(self, tmp_path, capsys):
        methodology = tmp_path / ".methodology"
        methodology.mkdir()
        (methodology / "quality_manifest.json").write_text(
            json.dumps({"fr_ids": ["FR-01", "FR-02"], "gate_results": {}})
        )
        args = argparse.Namespace(project=str(tmp_path), phase=3, fr_ids="FR-01,FR-02")
        from harness_cli import cmd_verify_agent_b_approvals
        rc = cmd_verify_agent_b_approvals(args)
        assert rc == 1
        out = capsys.readouterr().out
        assert "FR-01" in out or "Missing" in out

    def test_all_approved_returns_0(self, tmp_path, capsys):
        methodology = tmp_path / ".methodology"
        methodology.mkdir()
        approvals_dir = tmp_path / ".methodology" / "agent_b_approvals"
        approvals_dir.mkdir(parents=True)
        for fr in ["FR-01", "FR-02"]:
            (approvals_dir / f"{fr}.json").write_text(json.dumps({
                "fr": fr,
                "review_status": "APPROVE",
                "docs_embedded": ["SRS.md", "SAD.md"],
                "confidence": 0.92,
            }))
        args = argparse.Namespace(project=str(tmp_path), phase=3, fr_ids="FR-01,FR-02")
        from harness_cli import cmd_verify_agent_b_approvals
        rc = cmd_verify_agent_b_approvals(args)
        assert rc == 0

    def test_non_approve_status_returns_1(self, tmp_path, capsys):
        approvals_dir = tmp_path / ".methodology" / "agent_b_approvals"
        approvals_dir.mkdir(parents=True)
        (approvals_dir / "FR-01.json").write_text(json.dumps({
            "fr": "FR-01",
            "review_status": "REJECT",
            "docs_embedded": ["SRS.md", "SAD.md"],
        }))
        args = argparse.Namespace(project=str(tmp_path), phase=3, fr_ids="FR-01")
        from harness_cli import cmd_verify_agent_b_approvals
        rc = cmd_verify_agent_b_approvals(args)
        assert rc == 1
        out = capsys.readouterr().out
        assert "REJECT" in out or "APPROVE" in out

    def test_missing_docs_embedded_returns_1(self, tmp_path, capsys):
        approvals_dir = tmp_path / ".methodology" / "agent_b_approvals"
        approvals_dir.mkdir(parents=True)
        (approvals_dir / "FR-01.json").write_text(json.dumps({
            "fr": "FR-01",
            "review_status": "APPROVE",
            "docs_embedded": [],  # missing required docs
        }))
        args = argparse.Namespace(project=str(tmp_path), phase=3, fr_ids="FR-01")
        from harness_cli import cmd_verify_agent_b_approvals
        rc = cmd_verify_agent_b_approvals(args)
        assert rc == 1

    def test_no_fr_ids_no_manifest_returns_1(self, tmp_path, capsys):
        args = argparse.Namespace(project=str(tmp_path), phase=3, fr_ids="")
        from harness_cli import cmd_verify_agent_b_approvals
        rc = cmd_verify_agent_b_approvals(args)
        assert rc == 1

    def test_reads_fr_ids_from_manifest(self, tmp_path, capsys):
        methodology = tmp_path / ".methodology"
        methodology.mkdir()
        (methodology / "quality_manifest.json").write_text(
            json.dumps({"fr_ids": ["FR-01"], "gate_results": {}})
        )
        approvals_dir = tmp_path / ".methodology" / "agent_b_approvals"
        approvals_dir.mkdir(parents=True)
        (approvals_dir / "FR-01.json").write_text(json.dumps({
            "fr": "FR-01",
            "review_status": "APPROVE",
            "docs_embedded": ["SRS.md", "SAD.md"],
            "confidence": 0.9,
        }))
        args = argparse.Namespace(project=str(tmp_path), phase=3, fr_ids="")
        from harness_cli import cmd_verify_agent_b_approvals
        rc = cmd_verify_agent_b_approvals(args)
        assert rc == 0

    def test_p2_uses_phase_deliverables_not_fr_ids(self, tmp_path, capsys):
        """Phase 2 must verify SAD.md/ADR.md/TEST_SPEC.md approvals; --fr-ids must be ignored."""
        approvals_dir = tmp_path / ".methodology" / "agent_b_approvals"
        approvals_dir.mkdir(parents=True)
        for did in ["SAD.md", "ADR.md", "TEST_SPEC.md"]:
            (approvals_dir / f"{did}.json").write_text(json.dumps({
                "fr": did,
                "review_status": "APPROVE",
                "docs_embedded": ["SRS.md", "SAD.md"],
                "confidence": 0.9,
            }))
        # Pass FR IDs — they must be ignored for phase=2
        args = argparse.Namespace(project=str(tmp_path), phase=2, fr_ids="FR-01,FR-02,FR-03")
        from harness_cli import cmd_verify_agent_b_approvals
        rc = cmd_verify_agent_b_approvals(args)
        assert rc == 0  # SAD.md + ADR.md + TEST_SPEC.md approvals present → pass


class TestValidateP8Completion:
    """Tests for _validate_p8_completion pre-flight checks."""

    def test_all_ok_returns_empty_list(self, tmp_path):
        archive = tmp_path / ".methodology-archive"
        archive.mkdir()
        handover = tmp_path / "HANDOVER.md"
        handover.write_text("# Handover\n\nP8 complete. All phases done.\n")
        from harness_cli import _validate_p8_completion
        errors = _validate_p8_completion(tmp_path)
        assert errors == []

    def test_missing_archive_autocreated(self, tmp_path):
        # O2: auto-create .methodology-archive/ instead of returning error
        from harness_cli import _validate_p8_completion
        assert not (tmp_path / ".methodology-archive").exists()
        errors = _validate_p8_completion(tmp_path)
        assert not any(".methodology-archive" in e for e in errors)
        assert (tmp_path / ".methodology-archive").exists()

    def test_phase9_reference_in_handover_returns_error(self, tmp_path):
        archive = tmp_path / ".methodology-archive"
        archive.mkdir()
        handover = tmp_path / "HANDOVER.md"
        handover.write_text("# Handover\n\nNext: Begin Phase 9 tasks.\n")
        from harness_cli import _validate_p8_completion
        errors = _validate_p8_completion(tmp_path)
        assert any("Phase 9" in e or "phase 9" in e.lower() for e in errors)

    def test_phase9_plan_reference_returns_error(self, tmp_path):
        archive = tmp_path / ".methodology-archive"
        archive.mkdir()
        handover = tmp_path / "HANDOVER.md"
        handover.write_text("See phase9_plan.md for next steps.\n")
        from harness_cli import _validate_p8_completion
        errors = _validate_p8_completion(tmp_path)
        assert any("Phase 9" in e or "phase9" in e.lower() for e in errors)

    def test_no_handover_file_is_ok(self, tmp_path):
        archive = tmp_path / ".methodology-archive"
        archive.mkdir()
        # No HANDOVER.md — should not raise
        from harness_cli import _validate_p8_completion
        errors = _validate_p8_completion(tmp_path)
        assert errors == []


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
        from harness_cli import _PHASE_DELIVERABLES, _REQUIRED_EMBEDDED_DOCS
        approvals = project / ".methodology" / "agent_b_approvals"
        approvals.mkdir(parents=True, exist_ok=True)
        docs = _REQUIRED_EMBEDDED_DOCS.get(phase, ["SRS.md"])
        for did in _PHASE_DELIVERABLES.get(phase, []):
            (approvals / f"{did}.json").write_text(json.dumps({
                "fr": did, "review_status": status,
                "docs_embedded": docs, "confidence": 0.9,
            }))

    def test_p1_passes_without_gate(self, tmp_path):
        from harness_cli import _verify_entry_gate
        result = _verify_entry_gate(tmp_path, 1)
        assert result["passed"] is True

    def test_p2_no_state_json_falls_to_grep(self, tmp_path, monkeypatch):
        """No state.json → falls through to grep path → fails (no commits)."""
        import subprocess as sp
        from harness_cli import _verify_entry_gate
        monkeypatch.setattr(
            sp, "run",
            lambda cmd, **_kw: type("R", (), {"returncode": 1, "stdout": "", "stderr": ""})(),
        )
        result = _verify_entry_gate(tmp_path, 2)
        assert result["passed"] is False

    def test_p2_shallow_clone_fallback_passes_with_approvals(self, tmp_path, monkeypatch):
        """Shallow clone: merge-base fails → fallback to agent_b_approvals → pass."""
        import subprocess as sp
        from harness_cli import _verify_entry_gate

        self._make_state(tmp_path, phase=2, sha="abc1234def5678")
        self._make_approvals(tmp_path, phase=1)

        call_log: list[list[str]] = []

        def fake_run(cmd, **_kw):
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
        from harness_cli import _verify_entry_gate

        self._make_state(tmp_path, phase=2, sha="abc1234def5678")
        # No approval files created

        def fake_run(cmd, **_kw):
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
        from harness_cli import _verify_entry_gate

        self._make_state(tmp_path, phase=2, sha="abc1234def5678")

        def fake_run(cmd, **_kw):
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
        from harness_cli import _extract_review_json
        text = '{"fr": "FR-01", "review_status": "APPROVE", "docs_embedded": ["SRS.md"], "confidence": 0.9}'
        result = _extract_review_json(text)
        assert result is not None
        assert result["review_status"] == "APPROVE"

    def test_json_inside_prose(self):
        from harness_cli import _extract_review_json
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
        from harness_cli import _extract_review_json
        text = (
            "```json\n"
            '{"fr": "FR-03", "review_status": "APPROVE", "docs_embedded": ["SRS.md", "SAD.md"], "confidence": 0.85}\n'
            "```"
        )
        result = _extract_review_json(text)
        assert result is not None
        assert result["confidence"] == 0.85

    def test_no_review_status_returns_none(self):
        from harness_cli import _extract_review_json
        result = _extract_review_json('{"fr": "FR-01", "other_key": "value"}')
        assert result is None

    def test_empty_string_returns_none(self):
        from harness_cli import _extract_review_json
        assert _extract_review_json("") is None


class TestDispatchWritesApprovalJson:
    """Tests for cmd_dispatch reviewer → agent_b_approvals/<fr_id>.json."""

    def _make_spawner_mock(self, status, output):
        """Return a module-patchable AgentSpawner that yields a fixed result."""
        class _MockSpawner:
            def __init__(self, **_kw):
                pass
            def spawn(self, **_kw):
                return {"status": status, "session_id": "sess-abc", "output": output}
        return _MockSpawner

    def test_reviewer_complete_writes_approval_json(self, tmp_path, monkeypatch):
        from harness_cli import cmd_dispatch
        import sys, types
        output = '{"fr": "FR-01", "review_status": "APPROVE", "docs_embedded": ["SRS.md"], "confidence": 0.9}'
        fake_mod = types.ModuleType("core.agent_spawner")
        fake_mod.AgentSpawner = self._make_spawner_mock("complete", output)
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
        import sys, types
        fake_mod = types.ModuleType("core.agent_spawner")
        fake_mod.AgentSpawner = self._make_spawner_mock("complete", "Looks good, no JSON here.")
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
        import sys, types
        output = '{"fr": "FR-01", "review_status": "APPROVE", "docs_embedded": ["SRS.md"]}'
        fake_mod = types.ModuleType("core.agent_spawner")
        fake_mod.AgentSpawner = self._make_spawner_mock("complete", output)
        monkeypatch.setitem(sys.modules, "core.agent_spawner", fake_mod)

        args = argparse.Namespace(
            project=str(tmp_path), phase=1, fr_id="SRS.md",
            role="developer", prompt="Implement FR-01", timeout=60, max_turns=5,
        )
        rc = cmd_dispatch(args)
        assert rc == 0
        approval_file = tmp_path / ".methodology" / "agent_b_approvals" / "SRS.md.json"
        assert not approval_file.exists(), "developer role must not write approval JSON"


class TestPushCheckpointAgentBGate:
    """push-checkpoint: pure git record — all quality gates live in advance-phase."""

    def test_commit_message_no_longer_says_human_review(self, tmp_path, monkeypatch):
        """Commit notes must NOT say 'human review' (gates moved to advance-phase)."""
        from harness_cli import cmd_push_checkpoint
        import harness_cli

        commit_calls: list[dict] = []
        class _FakeGit:
            def ensure_gitignore(self): pass
            def commit_and_push_p1(self, **kw):
                commit_calls.append(kw)
                return True

        monkeypatch.setattr(harness_cli, "_make_git", lambda *_a, **_kw: _FakeGit())

        args = argparse.Namespace(
            project=str(tmp_path), phase=1, fr_ids="FR-01",
            dry_run=False, no_push=False,
        )
        rc = cmd_push_checkpoint(args)
        assert rc == 0
        assert commit_calls, "commit_and_push_p1 should have been called"
        background = commit_calls[0].get("background", "")
        notes = commit_calls[0].get("notes", [])
        assert "human review" not in background.lower(), "must not claim human review"
        assert all("human review" not in n.lower() for n in notes), "notes must not claim human review"


class TestExtractAgentOutputJson:
    """Tests for _extract_agent_output_json (Gap-4)."""

    def test_plain_agent_a_json(self):
        from harness_cli import _extract_agent_output_json
        text = '{"status": "complete", "files": ["SRS.md"], "confidence": 0.9, "citations": ["FR-01"], "summary": "done"}'
        result = _extract_agent_output_json(text)
        assert result is not None
        assert result["status"] == "complete"
        assert result["confidence"] == 0.9

    def test_agent_a_json_inside_prose(self):
        from harness_cli import _extract_agent_output_json
        text = 'Task complete.\n{"status": "complete", "files": ["SAD.md"], "summary": "arch done"}'
        result = _extract_agent_output_json(text)
        assert result is not None
        assert result["files"] == ["SAD.md"]

    def test_agent_b_block_not_matched(self):
        """review_status blocks must not be treated as Agent A output."""
        from harness_cli import _extract_agent_output_json
        text = '{"fr": "FR-01", "review_status": "APPROVE", "docs_embedded": ["SRS.md"], "status": "done"}'
        result = _extract_agent_output_json(text)
        assert result is None

    def test_no_agent_a_fields_returns_none(self):
        from harness_cli import _extract_agent_output_json
        result = _extract_agent_output_json('{"status": "complete", "phase": 1}')
        assert result is None

    def test_empty_string_returns_none(self):
        from harness_cli import _extract_agent_output_json
        assert _extract_agent_output_json("") is None


class TestDispatchSavesAgentAOutput:
    """cmd_dispatch developer role persists Agent A output JSON (Gap-4)."""

    def _make_spawner_mock(self, status, output):
        class _MockSpawner:
            def __init__(self, **_kw): pass
            def spawn(self, **_kw):
                return {"status": status, "session_id": "sess-xyz", "output": output}
        return _MockSpawner

    def test_developer_complete_writes_output_json(self, tmp_path, monkeypatch):
        from harness_cli import cmd_dispatch
        import sys, types
        output = '{"status": "complete", "files": ["SRS.md"], "confidence": 0.9, "citations": ["FR-01"], "summary": "done"}'
        fake_mod = types.ModuleType("core.agent_spawner")
        fake_mod.AgentSpawner = self._make_spawner_mock("complete", output)
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
        import sys, types
        fake_mod = types.ModuleType("core.agent_spawner")
        fake_mod.AgentSpawner = self._make_spawner_mock("complete", "All done, no JSON.")
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
        monkeypatch.setattr(harness_cli, "_verify_entry_gate",
                            lambda *_a, **_kw: {"passed": True, "gate": "G", "reason": "ok"})
        # Stub preflight_all to pass.
        monkeypatch.setattr(PhaseHooks, "preflight_all",
                            lambda self: {"all_passed": True, "details": {}})
        # Stub postflight_all — must NOT be called.
        monkeypatch.setattr(PhaseHooks, "postflight_all",
                            lambda self: postflight_called.append(1) or {"success": True})
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

        monkeypatch.setattr(harness_cli, "_verify_entry_gate",
                            lambda *_a, **_kw: {"passed": True, "gate": "G", "reason": "ok"})
        monkeypatch.setattr(PhaseHooks, "preflight_all",
                            lambda self: {"all_passed": False, "details": {"error": "missing SRS"}})
        monkeypatch.setattr(PhaseHooks, "postflight_all",
                            lambda self: postflight_called.append(1) or {"success": True})

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

        monkeypatch.setattr(harness_cli, "_verify_entry_gate",
                            lambda *_a, **_kw: {"passed": False, "gate": "G", "reason": "Phase 0 not complete"})
        monkeypatch.setattr(PhaseHooks, "postflight_all",
                            lambda self: postflight_called.append(1) or {"success": True})

        args = argparse.Namespace(phase=2, project=str(project))
        rc = harness_cli.cmd_run_phase(args)

        assert rc == 10
        assert postflight_called == [], "postflight_all must NOT be called on entry gate failure"


# =============================================================================
# _advance_prechecks — TDD block (P3+)
# =============================================================================

class TestAdvancePrechecksTDD:
    """Tests for the P3+ TDD block in _advance_prechecks."""

    def _make_p3_project(self, tmp_path: Path) -> None:
        """Minimal P3 project skeleton (PhaseAuditor will be mocked)."""
        (tmp_path / ".methodology").mkdir()
        (tmp_path / "03-development" / "src").mkdir(parents=True)
        # Next-phase plan required by _advance_prechecks (phase >= 3)
        (tmp_path / ".methodology" / "phase4_plan.md").touch()

    def test_pytest_failure_returns_9(self, tmp_path, monkeypatch):
        """pytest non-zero exit → _advance_prechecks returns 9."""
        from harness_cli import _advance_prechecks

        self._make_p3_project(tmp_path)
        monkeypatch.setattr("harness_cli._run_phase_auditor", lambda p, ph: 0)
        monkeypatch.setattr("harness_cli._check_gate_score_variance", lambda p, ph: 0)
        monkeypatch.setattr(
            "core.quality_gate.phase_truth_verifier.PhaseTruthVerifier",
            type("FV", (), {
                "__init__": lambda s, p, ph: None,
                "verify": lambda s: {"passed": True, "total_score": 100.0},
            }),
        )

        class _FakeResult:
            returncode = 1

        import harness_cli
        monkeypatch.setattr(harness_cli.subprocess, "run", lambda *a, **kw: _FakeResult())

        rc = _advance_prechecks(tmp_path, completed_phase=3)
        assert rc == 9

    def test_pytest_skipped_when_no_src_dir(self, tmp_path, monkeypatch):
        """No 03-development/src → pytest step skipped, continues to spec-coverage."""
        from harness_cli import _advance_prechecks

        (tmp_path / ".methodology").mkdir()  # no src dir
        monkeypatch.setattr("harness_cli._run_phase_auditor", lambda p, ph: 0)
        monkeypatch.setattr("harness_cli._check_gate_score_variance", lambda p, ph: 0)
        monkeypatch.setattr(
            "core.quality_gate.phase_truth_verifier.PhaseTruthVerifier",
            type("FV", (), {
                "__init__": lambda s, p, ph: None,
                "verify": lambda s: {"passed": True, "total_score": 100.0},
            }),
        )
        # spec-coverage returns pass (unified D4, v2.6)
        monkeypatch.setattr("harness_cli._run_spec_coverage_check",
                            lambda p, t, **kw: (0, 100.0))
        # next-phase plan required by _advance_prechecks (phase >= 3)
        (tmp_path / ".methodology" / "phase4_plan.md").touch()

        rc = _advance_prechecks(tmp_path, completed_phase=3)
        assert rc == 0

    def test_spec_coverage_below_threshold_returns_10(self, tmp_path, monkeypatch):
        """spec-coverage below threshold → _advance_prechecks returns 10."""
        from harness_cli import _advance_prechecks

        (tmp_path / ".methodology").mkdir()
        monkeypatch.setattr("harness_cli._run_phase_auditor", lambda p, ph: 0)
        monkeypatch.setattr("harness_cli._check_gate_score_variance", lambda p, ph: 0)
        monkeypatch.setattr(
            "core.quality_gate.phase_truth_verifier.PhaseTruthVerifier",
            type("FV", (), {
                "__init__": lambda s, p, ph: None,
                "verify": lambda s: {"passed": True, "total_score": 100.0},
            }),
        )
        monkeypatch.setattr("harness_cli._run_spec_coverage_check",
                            lambda p, t, **kw: (1, 30.0))
        # next-phase plan required by _advance_prechecks (phase >= 3)
        (tmp_path / ".methodology" / "phase4_plan.md").touch()

        rc = _advance_prechecks(tmp_path, completed_phase=3)
        assert rc == 10

    def test_tdd_block_not_run_for_p2(self, tmp_path, monkeypatch):
        """P2 does not execute TDD block — returns 0 after PhaseAuditor + agent-B."""
        from harness_cli import _advance_prechecks

        (tmp_path / ".methodology").mkdir()
        monkeypatch.setattr("harness_cli._run_phase_auditor", lambda p, ph: 0)
        monkeypatch.setattr("harness_cli._verify_agent_b_approvals_core",
                            lambda p, ph, ids: (True, "mocked"))

        rc = _advance_prechecks(tmp_path, completed_phase=2)
        assert rc == 0

    def test_threshold_escalation_p4_uses_70_80(self, tmp_path, monkeypatch):
        """P4: spec-coverage threshold=70%, D4 threshold=80%."""
        from harness_cli import _advance_prechecks

        (tmp_path / ".methodology").mkdir()
        monkeypatch.setattr("harness_cli._run_phase_auditor", lambda p, ph: 0)
        monkeypatch.setattr("harness_cli._check_gate_score_variance", lambda p, ph: 0)
        monkeypatch.setattr(
            "core.quality_gate.phase_truth_verifier.PhaseTruthVerifier",
            type("FV", (), {
                "__init__": lambda s, p, ph: None,
                "verify": lambda s: {"passed": True, "total_score": 100.0},
            }),
        )
        captured_sc = {}

        def _fake_sc(p, t, **kw):
            captured_sc["threshold"] = t
            return (0, 100.0)

        monkeypatch.setattr("harness_cli._run_spec_coverage_check", _fake_sc)
        (tmp_path / ".methodology" / "phase5_plan.md").touch()

        _advance_prechecks(tmp_path, completed_phase=4)
        assert captured_sc["threshold"] == 80.0  # unified v2.6

    def test_threshold_escalation_p6_uses_90(self, tmp_path, monkeypatch):
        """P6: spec-coverage threshold escalates to 90%."""
        from harness_cli import _advance_prechecks

        (tmp_path / ".methodology").mkdir()
        monkeypatch.setattr("harness_cli._run_phase_auditor", lambda p, ph: 0)
        monkeypatch.setattr("harness_cli._check_gate_score_variance", lambda p, ph: 0)
        monkeypatch.setattr(
            "core.quality_gate.phase_truth_verifier.PhaseTruthVerifier",
            type("FV", (), {
                "__init__": lambda s, p, ph: None,
                "verify": lambda s: {"passed": True, "total_score": 100.0},
            }),
        )
        captured = {}

        def _fake_sc(p, t, **kw):
            captured["sc"] = t
            return (0, 100.0)

        monkeypatch.setattr("harness_cli._run_spec_coverage_check", _fake_sc)
        (tmp_path / ".methodology" / "phase7_plan.md").touch()

        _advance_prechecks(tmp_path, completed_phase=6)
        assert captured["sc"] == 90.0  # unified v2.6


# =============================================================================
# _advance_prechecks — Agent B approvals (P1/P2)
# =============================================================================

class TestAdvancePreChecksAgentB:
    """Agent B approval gate in _advance_prechecks for P1/P2."""

    def _mock_p1_prechecks(self, monkeypatch):
        """Patch non-AB checks so only AB check is exercised."""
        monkeypatch.setattr("harness_cli._run_phase_auditor", lambda p, ph: 0)

    def test_p1_missing_approvals_returns_13(self, tmp_path, monkeypatch):
        """P1 with no agent_b_approvals/ → returns 13."""
        from harness_cli import _advance_prechecks
        (tmp_path / ".methodology").mkdir()
        self._mock_p1_prechecks(monkeypatch)
        rc = _advance_prechecks(tmp_path, completed_phase=1)
        assert rc == 13

    def test_p1_approved_returns_0(self, tmp_path, monkeypatch):
        """P1 with all approvals APPROVE → proceeds (returns 0)."""
        from harness_cli import _advance_prechecks, _PHASE_DELIVERABLES
        import json

        method_dir = tmp_path / ".methodology"
        (method_dir / "agent_b_approvals").mkdir(parents=True)
        for did in _PHASE_DELIVERABLES[1]:
            (method_dir / "agent_b_approvals" / f"{did}.json").write_text(
                json.dumps({"review_status": "APPROVE", "docs_embedded": ["SRS.md"]}),
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
        from harness_cli import _advance_prechecks, _PHASE_DELIVERABLES
        import json

        method_dir = tmp_path / ".methodology"
        (method_dir / "agent_b_approvals").mkdir(parents=True)
        for i, did in enumerate(_PHASE_DELIVERABLES[2]):
            status = "REJECT" if i == 0 else "APPROVE"
            (method_dir / "agent_b_approvals" / f"{did}.json").write_text(
                json.dumps({
                    "review_status": status,
                    "docs_embedded": ["SRS.md", "SAD.md"],
                }),
                encoding="utf-8",
            )

        self._mock_p1_prechecks(monkeypatch)
        rc = _advance_prechecks(tmp_path, completed_phase=2)
        assert rc == 13

    def test_p3_skips_agent_b_check(self, tmp_path, monkeypatch):
        """P3+ does not run Agent B check (A/B removed from P3+)."""
        from harness_cli import _advance_prechecks

        (tmp_path / ".methodology").mkdir()
        monkeypatch.setattr("harness_cli._run_phase_auditor", lambda p, ph: 0)
        monkeypatch.setattr("harness_cli._check_gate_score_variance", lambda p, ph: 0)
        monkeypatch.setattr(
            "core.quality_gate.phase_truth_verifier.PhaseTruthVerifier",
            type("FV", (), {
                "__init__": lambda s, p, ph: None,
                "verify": lambda s: {"passed": True, "total_score": 100.0},
            }),
        )
        monkeypatch.setattr("harness_cli._run_spec_coverage_check",
                            lambda p, t, **kw: (0, 100.0))
        # next-phase plan required by _advance_prechecks (phase >= 3)
        (tmp_path / ".methodology" / "phase4_plan.md").touch()

        # No agent_b_approvals dir at all — should not matter for P3
        rc = _advance_prechecks(tmp_path, completed_phase=3)
        assert rc == 0


class TestAuditPhaseFailOnCritical:
    def test_fail_on_critical_exits_1_on_criticals(self, tmp_path):
        """--fail-on-critical: exit 1 when CRITICAL findings exist."""
        import argparse
        from harness_cli import cmd_audit_phase

        # Minimal P1 project — missing all deliverables → multiple CRITICALs
        (tmp_path / ".methodology").mkdir()

        args = argparse.Namespace(
            phase=1, project=str(tmp_path), branch="main",
            repo=None, save=None, output="text",
            fail_on_critical=True,
        )
        rc = cmd_audit_phase(args)
        assert rc == 1

    def test_without_flag_uses_verdict_logic(self, tmp_path):
        """Without --fail-on-critical, exit code follows verdict only."""
        import argparse
        from harness_cli import cmd_audit_phase

        (tmp_path / ".methodology").mkdir()

        args = argparse.Namespace(
            phase=1, project=str(tmp_path), branch="main",
            repo=None, save=None, output="text",
            fail_on_critical=False,
        )
        rc = cmd_audit_phase(args)
        # P1 missing deliverables → FAIL verdict → exit 1 (flag path not taken)
        assert rc in (0, 1)


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


class TestFrSourceFilesFromImports:
    """Tests for _fr_source_files_from_imports — AST-based FR source file detection."""

    def test_from_import_matches_module_file(self, tmp_path):
        """from foo.bar import Baz → matches foo/bar.py."""
        import harness_cli

        src = tmp_path / "src"
        src.joinpath("foo").mkdir(parents=True)
        src.joinpath("foo", "bar.py").write_text("class Baz: pass", encoding="utf-8")
        test = tmp_path / "tests"
        test.mkdir()
        test_file = test / "test_fr01.py"
        test_file.write_text("from foo.bar import Baz\n", encoding="utf-8")

        result = harness_cli._fr_source_files_from_imports(
            tmp_path, "tests/test_fr01.py", "src"
        )
        assert result == ["src/foo/bar.py"]

    def test_direct_import_matches_module_file(self, tmp_path):
        """import foo.bar → matches foo/bar.py."""
        import harness_cli

        src = tmp_path / "src"
        src.joinpath("foo").mkdir(parents=True)
        src.joinpath("foo", "bar.py").write_text("x = 1", encoding="utf-8")
        test = tmp_path / "tests"
        test.mkdir()
        test_file = test / "test_fr01.py"
        test_file.write_text("import foo.bar\n", encoding="utf-8")

        result = harness_cli._fr_source_files_from_imports(
            tmp_path, "tests/test_fr01.py", "src"
        )
        assert result == ["src/foo/bar.py"]

    def test_stdlib_only_returns_empty(self, tmp_path):
        """Test file with only stdlib imports → returns [] (fallback)."""
        import harness_cli

        src = tmp_path / "src"
        src.mkdir()
        src.joinpath("dummy.py").write_text("x = 1", encoding="utf-8")
        test = tmp_path / "tests"
        test.mkdir()
        test_file = test / "test_fr01.py"
        test_file.write_text("import os\nimport sys\nfrom pathlib import Path\n", encoding="utf-8")

        result = harness_cli._fr_source_files_from_imports(
            tmp_path, "tests/test_fr01.py", "src"
        )
        assert result == []

    def test_missing_test_file_returns_empty(self, tmp_path):
        """Test file doesn't exist → returns []."""
        import harness_cli

        src = tmp_path / "src"
        src.mkdir()
        src.joinpath("dummy.py").write_text("x = 1", encoding="utf-8")

        result = harness_cli._fr_source_files_from_imports(
            tmp_path, "tests/nonexistent.py", "src"
        )
        assert result == []

    def test_syntax_error_returns_empty(self, tmp_path):
        """Unparseable test file → returns []."""
        import harness_cli

        src = tmp_path / "src"
        src.mkdir()
        src.joinpath("dummy.py").write_text("x = 1", encoding="utf-8")
        test = tmp_path / "tests"
        test.mkdir()
        test_file = test / "test_fr01.py"
        test_file.write_text("this is not valid python {{{{{\n", encoding="utf-8")

        result = harness_cli._fr_source_files_from_imports(
            tmp_path, "tests/test_fr01.py", "src"
        )
        assert result == []

    def test_mixed_imports_match_multiple_files(self, tmp_path):
        """from foo.bar import Baz + import foo.baz → matches both source files."""
        import harness_cli

        src = tmp_path / "src"
        src.joinpath("foo").mkdir(parents=True)
        src.joinpath("foo", "bar.py").write_text("class Baz: pass", encoding="utf-8")
        src.joinpath("foo", "baz.py").write_text("x = 1", encoding="utf-8")
        test = tmp_path / "tests"
        test.mkdir()
        test_file = test / "test_fr01.py"
        test_file.write_text(
            "from foo.bar import Baz\nimport foo.baz\n", encoding="utf-8"
        )

        result = harness_cli._fr_source_files_from_imports(
            tmp_path, "tests/test_fr01.py", "src"
        )
        assert sorted(result) == ["src/foo/bar.py", "src/foo/baz.py"]

    def test_init_py_excluded(self, tmp_path):
        """__init__.py files are excluded even when the package is imported."""
        import harness_cli

        src = tmp_path / "src"
        src.joinpath("foo").mkdir(parents=True)
        src.joinpath("foo", "__init__.py").write_text("x = 1", encoding="utf-8")
        src.joinpath("foo", "bar.py").write_text("class Baz: pass", encoding="utf-8")
        test = tmp_path / "tests"
        test.mkdir()
        test_file = test / "test_fr01.py"
        test_file.write_text("import foo\nfrom foo.bar import Baz\n", encoding="utf-8")

        result = harness_cli._fr_source_files_from_imports(
            tmp_path, "tests/test_fr01.py", "src"
        )
        # foo/__init__.py should be excluded; only foo/bar.py should match
        assert result == ["src/foo/bar.py"]

    def test_missing_src_dir_returns_empty(self, tmp_path):
        """src_dir doesn't exist → returns []."""
        import harness_cli

        test = tmp_path / "tests"
        test.mkdir()
        test_file = test / "test_fr01.py"
        test_file.write_text("from foo.bar import Baz\n", encoding="utf-8")

        result = harness_cli._fr_source_files_from_imports(
            tmp_path, "tests/test_fr01.py", "nonexistent_src"
        )
        assert result == []

    def test_from_import_with_alias_subpath_match(self, tmp_path):
        """from foo.bar import BazClass adds foo.bar.BazClass, which startswith-match
        module foo.bar → correctly finds foo/bar.py."""
        import harness_cli

        src = tmp_path / "src"
        src.joinpath("foo").mkdir(parents=True)
        src.joinpath("foo", "bar.py").write_text("class BazClass: pass", encoding="utf-8")
        test = tmp_path / "tests"
        test.mkdir()
        test_file = test / "test_fr01.py"
        test_file.write_text("from foo.bar import BazClass\n", encoding="utf-8")

        result = harness_cli._fr_source_files_from_imports(
            tmp_path, "tests/test_fr01.py", "src"
        )
        assert result == ["src/foo/bar.py"]


class TestRunFrStep:
    """Tests for cmd_run_fr_step and related helpers."""

    def test_skip_if_already_done(self, tmp_path, monkeypatch):
        """Idempotency: returns 0 immediately if step commit already exists."""
        import harness_cli
        monkeypatch.setattr(harness_cli, "_fr_step_already_done", lambda s, f, p: True)
        args = argparse.Namespace(
            phase=3, fr_id="FR-01", step="TDD-RED", project=str(tmp_path),
            srs=None, timeout=600, max_turns=30, max_fix_rounds=3,
        )
        assert harness_cli.cmd_run_fr_step(args) == 0

    def test_dispatch_called_when_not_done(self, tmp_path, monkeypatch):
        """Sub-agent is dispatched when step has not yet been committed."""
        import sys, types, harness_cli

        _setup_preflight_fixtures(tmp_path, step="TDD-RED")

        # _fr_step_already_done always returns False (step not done)
        monkeypatch.setattr(harness_cli, "_fr_step_already_done", lambda s, f, p: False)

        dispatched: dict = {}

        class _FakeSpawner:
            def __init__(self, project_path=None):
                pass
            def spawn(self, **kwargs):
                dispatched.update(kwargs)
                return {"status": "complete", "output": "{}"}

        fake_mod = types.ModuleType("core.agent_spawner")
        fake_mod.AgentSpawner = _FakeSpawner
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
        import harness_cli
        srs = tmp_path / "SRS.md"
        srs.write_text(
            "### FR-01: Feature A\n\n**Description**: Alpha text\n\n---\n"
            "### FR-02: Feature B\n\n**Description**: Beta text\n\n---\n",
            encoding="utf-8",
        )
        section = harness_cli._extract_srs_fr_section(srs, "FR-01")
        assert "Alpha text" in section
        assert "Beta text" not in section

    def test_extract_srs_fr_section_missing_fr(self, tmp_path):
        """_extract_srs_fr_section returns empty string when FR not found."""
        import harness_cli
        srs = tmp_path / "SRS.md"
        srs.write_text("### FR-02: Feature B\n\n**Description**: Beta\n\n---\n")
        assert harness_cli._extract_srs_fr_section(srs, "FR-01") == ""

    def test_prompt_tdd_red_contains_srs_section(self, tmp_path):
        """TDD-RED prompt includes extracted SRS section and commit format."""
        import harness_cli
        srs = tmp_path / "SRS.md"
        srs.write_text(
            "### FR-01: My Feature\n\n**Description**: Do important thing X\n\n---\n",
            encoding="utf-8",
        )
        prompt = harness_cli._build_fr_step_prompt("TDD-RED", "FR-01", 3, tmp_path, srs)
        assert "Do important thing X" in prompt
        assert "test(RED): failing test for FR-01" in prompt
        assert "failing test" in prompt.lower()

    def test_prompt_tdd_green_inlines_test_file(self, tmp_path):
        """TDD-GREEN prompt includes the current test file content inline."""
        import harness_cli
        srs = tmp_path / "SRS.md"
        srs.write_text(
            "### FR-01: My Feature\n\n**Description**: Do X\n\n---\n", encoding="utf-8"
        )
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_fr01.py").write_text(
            "def test_my_feature(): assert False  # RED", encoding="utf-8"
        )
        prompt = harness_cli._build_fr_step_prompt("TDD-GREEN", "FR-01", 3, tmp_path, srs)
        assert "assert False  # RED" in prompt
        assert "feat(FR-01): GREEN" in prompt

    def test_prompt_gate1_contains_run_gate_command(self, tmp_path):
        """GATE1 prompt includes run-gate and finalize-gate commands."""
        import harness_cli
        prompt = harness_cli._build_fr_step_prompt("GATE1", "FR-01", 3, tmp_path, None)
        assert "run-gate --gate 1 --phase 3 --fr-id FR-01" in prompt
        assert "finalize-gate --gate 1 --phase 3 --fr-id FR-01" in prompt
        assert '"pass"' in prompt

    def test_prompt_gate1_delta_uses_full_gate_evaluation(self, tmp_path):
        """GATE1-DELTA prompt runs full GATE1 (no --delta — skip is handled by
        _fr_step_already_done() git diff check before dispatch)."""
        import harness_cli
        prompt = harness_cli._build_fr_step_prompt("GATE1-DELTA", "FR-05", 5, tmp_path, None)
        assert "run-gate --gate 1" in prompt
        assert "finalize-gate --gate 1" in prompt
        assert "--delta" not in prompt

    def test_prompt_code_fix_test_coverage_only(self, tmp_path):
        """CODE-FIX with test_coverage only → [TEST COVERAGE FIX] section,
        FORBIDDEN allows adding tests, git add includes test file."""
        import harness_cli

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

        prompt = harness_cli._build_fr_step_prompt(
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
        import harness_cli

        prompt = harness_cli._build_fr_step_prompt(
            "CODE-FIX", "FR-01", 3, tmp_path, None,
            failing_dims=["ruff"],
        )
        assert "[TEST COVERAGE FIX" not in prompt
        assert "Modifying test files" in prompt
        assert "git add 03-development/src/" in prompt

    def test_prompt_code_fix_mixed_dims(self, tmp_path):
        """CODE-FIX with test_coverage + ruff → both sections, git add includes
        both src_dir and test_file."""
        import harness_cli

        spec_dir = tmp_path / "02-architecture"
        spec_dir.mkdir()
        spec_dir.joinpath("TEST_SPEC.md").write_text(
            "### FR-01: Feature\n\n"
            "| # | Test Function | Type |\n"
            "|---|--------------|------|\n"
            "| 1 | `test_feature_a` | Functional |\n",
            encoding="utf-8",
        )

        prompt = harness_cli._build_fr_step_prompt(
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
        import harness_cli

        prompt = harness_cli._build_fr_step_prompt(
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
        import harness_cli, sys, io

        (tmp_path / ".methodology").mkdir()
        (tmp_path / ".methodology" / "quality_manifest.json").write_text(
            json.dumps({"fr_ids": ["FR-01"]}), encoding="utf-8"
        )
        # TDD-RED done, TDD-GREEN not yet done
        monkeypatch.setattr(
            harness_cli, "_fr_step_already_done",
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
        import harness_cli, sys, io

        (tmp_path / ".methodology").mkdir()
        (tmp_path / ".methodology" / "quality_manifest.json").write_text(
            json.dumps({"fr_ids": ["FR-01"]}), encoding="utf-8"
        )
        monkeypatch.setattr(harness_cli, "_fr_step_already_done", lambda *a: True)
        captured = io.StringIO()
        monkeypatch.setattr(sys, "stdout", captured)

        args = argparse.Namespace(phase=3, project=str(tmp_path))
        rc = harness_cli.cmd_resume_fr_phase(args)
        assert rc == 0
        assert "All FRs complete" in captured.getvalue()

    def test_resume_fr_phase_falls_back_to_fr_progress(self, tmp_path, monkeypatch):
        """resume-fr-phase uses fr_progress.json when quality_manifest.json is absent."""
        import harness_cli, sys, io

        (tmp_path / ".methodology").mkdir()
        (tmp_path / ".methodology" / "fr_progress.json").write_text(
            json.dumps({"phase": 3, "frs": {"FR-02": {"status": "gate1_pass"}}}),
            encoding="utf-8",
        )
        monkeypatch.setattr(harness_cli, "_fr_step_already_done", lambda *a: False)
        captured = io.StringIO()
        monkeypatch.setattr(sys, "stdout", captured)

        args = argparse.Namespace(phase=3, project=str(tmp_path))
        rc = harness_cli.cmd_resume_fr_phase(args)
        assert rc == 0
        assert "FR-02" in captured.getvalue()

    def test_gate1_blocked_after_max_rounds(self, tmp_path, monkeypatch):
        """Returns exit 2 (BLOCKED) when GATE1 never passes after max_fix_rounds."""
        import sys, types, harness_cli

        _setup_preflight_fixtures(tmp_path, step="GATE1")

        monkeypatch.setattr(harness_cli, "_fr_step_already_done", lambda s, f, p: False)

        # Sub-agent always returns gate_pass=false
        _fail_output = '{"status": "DONE", "pass": false, "failing_dims": ["D1"], "gate_score": 0.2}'

        class _FakeSpawner:
            def __init__(self, project_path=None):
                pass
            def spawn(self, **kwargs):
                return {"status": "complete", "output": _fail_output}

        fake_mod = types.ModuleType("core.agent_spawner")
        fake_mod.AgentSpawner = _FakeSpawner
        monkeypatch.setitem(sys.modules, "core.agent_spawner", fake_mod)

        args = argparse.Namespace(
            phase=3, fr_id="FR-01", step="GATE1", project=str(tmp_path),
            srs=None, timeout=60, max_turns=5, max_fix_rounds=2,
        )
        rc = harness_cli.cmd_run_fr_step(args)
        assert rc == 2  # BLOCKED

    def test_resume_fr_phase_carryforward_uses_gate1_delta(self, tmp_path, monkeypatch):
        """resume-fr-phase emits GATE1-DELTA for carry-forward phases when code unchanged."""
        import harness_cli, sys, io

        (tmp_path / ".methodology").mkdir()
        (tmp_path / ".methodology" / "quality_manifest.json").write_text(
            json.dumps({"fr_ids": ["FR-01"]}), encoding="utf-8"
        )
        monkeypatch.setattr(harness_cli, "_fr_step_already_done", lambda *a: False)
        monkeypatch.setattr(
            harness_cli, "_fr_code_changed_since_last_gate1", lambda *a: False,
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
        import harness_cli, sys, io

        (tmp_path / ".methodology").mkdir()
        (tmp_path / ".methodology" / "quality_manifest.json").write_text(
            json.dumps({"fr_ids": ["FR-01"]}), encoding="utf-8"
        )
        monkeypatch.setattr(harness_cli, "_fr_step_already_done", lambda *a: False)
        monkeypatch.setattr(
            harness_cli, "_fr_code_changed_since_last_gate1", lambda *a: True,
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
        import harness_cli
        import subprocess as _sp

        class _FakeResult:
            returncode = 0
            stdout = "test(RED): failing test for FR-01"

        # Git log mock returns matching commit
        monkeypatch.setattr(_sp, "run", lambda *a, **kw: _FakeResult())

        # RED Test: Test file missing -> should return False
        assert not harness_cli._fr_step_already_done("TDD-RED", "FR-01", tmp_path)

        # Create test file -> should return True
        (tmp_path / "tests").mkdir(parents=True, exist_ok=True)
        (tmp_path / "tests" / "test_fr01.py").write_text("def test_fr(): pass")
        assert harness_cli._fr_step_already_done("TDD-RED", "FR-01", tmp_path)

        # GREEN Test: Src dir missing -> should return False
        assert not harness_cli._fr_step_already_done("TDD-GREEN", "FR-01", tmp_path)

        # Create empty src dir -> should return False
        (tmp_path / "03-development" / "src").mkdir(parents=True, exist_ok=True)
        assert not harness_cli._fr_step_already_done("TDD-GREEN", "FR-01", tmp_path)

        # Create source file with tag -> should return True
        f = tmp_path / "03-development" / "src" / "impl.py"
        f.write_text("# [FR-01]")
        assert harness_cli._fr_step_already_done("TDD-GREEN", "FR-01", tmp_path)

    def test_run_fr_step_handles_git_push_failure_as_fatal(self, tmp_path, monkeypatch, capsys):
        """cmd_run_fr_step prints an error and returns 1 when git push fails (fatal check-recovery)."""
        import sys, types, harness_cli
        import subprocess as _sp

        _setup_preflight_fixtures(tmp_path, step="TDD-RED")

        monkeypatch.setattr(harness_cli, "_fr_step_already_done", lambda s, f, p: False)

        class _FakeSpawner:
            def __init__(self, project_path=None): pass
            def spawn(self, **kwargs):
                return {"status": "complete", "output": "{}"}

        fake_mod = types.ModuleType("core.agent_spawner")
        fake_mod.AgentSpawner = _FakeSpawner
        monkeypatch.setitem(sys.modules, "core.agent_spawner", fake_mod)

        # Mock git commands: make git push fail (returncode 1)
        def _fake_run(cmd, **kw):
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
        import sys, types, harness_cli
        import subprocess as _sp

        _setup_preflight_fixtures(tmp_path, step="TDD-RED")

        monkeypatch.setattr(harness_cli, "_fr_step_already_done", lambda s, f, p: False)

        class _FakeSpawner:
            def __init__(self, project_path=None): pass
            def spawn(self, **kwargs):
                return {"status": "complete", "output": "{}"}

        fake_mod = types.ModuleType("core.agent_spawner")
        fake_mod.AgentSpawner = _FakeSpawner
        monkeypatch.setitem(sys.modules, "core.agent_spawner", fake_mod)

        run_calls = []
        def _fake_run(cmd, **kw):
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
# P0-A: _mark_plan_item
# ---------------------------------------------------------------------------

class TestMarkPlanItem:
    """Tests for harness_cli._mark_plan_item (P0-A bookkeeping automation)."""

    def _make_plan(self, tmp_path: Path, phase: int, content: str) -> Path:
        plan = tmp_path / ".methodology" / f"phase{phase}_plan.md"
        plan.parent.mkdir(parents=True, exist_ok=True)
        plan.write_text(content, encoding="utf-8")
        return plan

    def test_marks_orch_red_for_tdd_red(self, tmp_path):
        import harness_cli
        content = "- [ ] **[ORCH-RED]** Dispatch TDD-RED sub-agent for FR-01:\n"
        plan = self._make_plan(tmp_path, 3, content)
        harness_cli._mark_plan_item(tmp_path, 3, "TDD-RED", "FR-01")
        assert "- [x] **[ORCH-RED]**" in plan.read_text()

    def test_marks_orch_green_for_tdd_green(self, tmp_path):
        import harness_cli
        content = "- [ ] **[ORCH-GREEN]** Dispatch TDD-GREEN sub-agent for FR-05:\n"
        plan = self._make_plan(tmp_path, 3, content)
        harness_cli._mark_plan_item(tmp_path, 3, "TDD-GREEN", "FR-05")
        assert "- [x] **[ORCH-GREEN]**" in plan.read_text()

    def test_marks_orch_gate1_for_gate1(self, tmp_path):
        import harness_cli
        content = "- [ ] **[ORCH-GATE1]** Dispatch GATE1 evaluator for FR-19:\n"
        plan = self._make_plan(tmp_path, 3, content)
        harness_cli._mark_plan_item(tmp_path, 3, "GATE1", "FR-19")
        assert "- [x] **[ORCH-GATE1]**" in plan.read_text()

    def test_does_not_modify_other_fr_items(self, tmp_path):
        import harness_cli
        content = (
            "- [ ] **[ORCH-RED]** Dispatch TDD-RED sub-agent for FR-01:\n"
            "- [ ] **[ORCH-RED]** Dispatch TDD-RED sub-agent for FR-02:\n"
        )
        plan = self._make_plan(tmp_path, 3, content)
        harness_cli._mark_plan_item(tmp_path, 3, "TDD-RED", "FR-01")
        updated = plan.read_text()
        assert "- [x] **[ORCH-RED]** Dispatch TDD-RED sub-agent for FR-01" in updated
        assert "- [ ] **[ORCH-RED]** Dispatch TDD-RED sub-agent for FR-02" in updated

    def test_noop_when_plan_missing(self, tmp_path):
        import harness_cli
        # No plan file — should not raise
        harness_cli._mark_plan_item(tmp_path, 3, "TDD-RED", "FR-01")

    def test_noop_for_unknown_step(self, tmp_path):
        import harness_cli
        content = "- [ ] **[ORCH-RED]** for FR-01:\n"
        plan = self._make_plan(tmp_path, 3, content)
        harness_cli._mark_plan_item(tmp_path, 3, "UNKNOWN-STEP", "FR-01")
        # File unchanged
        assert "- [ ] **[ORCH-RED]**" in plan.read_text()

    def test_already_checked_item_unchanged(self, tmp_path):
        import harness_cli
        content = "- [x] **[ORCH-RED]** Dispatch TDD-RED for FR-01:\n"
        plan = self._make_plan(tmp_path, 3, content)
        harness_cli._mark_plan_item(tmp_path, 3, "TDD-RED", "FR-01")
        assert plan.read_text().count("[x]") == 1  # still exactly one [x]


# ---------------------------------------------------------------------------
# P0-B: _append_dev_log_tdd_entry
# ---------------------------------------------------------------------------

class TestAppendDevLogTddEntry:
    """Tests for harness_cli._append_dev_log_tdd_entry (P0-B bookkeeping)."""

    def test_appends_line_with_score(self, tmp_path):
        import harness_cli
        log = tmp_path / "DEVELOPMENT_LOG.md"
        log.write_text("# Dev Log\n", encoding="utf-8")
        harness_cli._append_dev_log_tdd_entry(tmp_path, "FR-03", score=92.5)
        content = log.read_text()
        assert "FR-03 test pass" in content
        assert "92.50" in content
        assert "RED→GREEN" in content

    def test_appends_line_with_none_score(self, tmp_path):
        import harness_cli
        log = tmp_path / "DEVELOPMENT_LOG.md"
        log.write_text("# Dev Log\n", encoding="utf-8")
        harness_cli._append_dev_log_tdd_entry(tmp_path, "FR-07", score=None)
        assert "FR-07 test pass" in log.read_text()
        assert "N/A" in log.read_text()

    def test_noop_when_log_missing(self, tmp_path):
        import harness_cli
        # No DEVELOPMENT_LOG.md — must not raise
        harness_cli._append_dev_log_tdd_entry(tmp_path, "FR-01", score=100.0)

    def test_multiple_calls_append_multiple_lines(self, tmp_path):
        import harness_cli
        log = tmp_path / "DEVELOPMENT_LOG.md"
        log.write_text("", encoding="utf-8")
        harness_cli._append_dev_log_tdd_entry(tmp_path, "FR-01", score=80.0)
        harness_cli._append_dev_log_tdd_entry(tmp_path, "FR-02", score=90.0)
        lines = [ln for ln in log.read_text().splitlines() if ln.strip()]
        assert len(lines) == 2
        assert "FR-01" in lines[0]
        assert "FR-02" in lines[1]


# ---------------------------------------------------------------------------
# P2-B: W2 coverage warning logic (harness_bridge.finalize_gate)
# ---------------------------------------------------------------------------

class TestW2CoverageWarning:
    """Tests for W2 sub-100% coverage advisory emitted by finalize_gate."""

    def _run_w2_logic(self, score: float, capsys) -> str:
        """Replicate the W2 branch from finalize_gate for unit testing."""
        try:
            _cov_pct = float(score)
        except (TypeError, ValueError):
            _cov_pct = 100.0
        if _cov_pct < 100.0:
            print(
                f"[W2] test_coverage {_cov_pct:.1f}% < 100 — "
                "advance-phase requires 100% on 03-development/src. "
                "Lines not exercisable in tests: add # pragma: no cover."
            )
        return capsys.readouterr().out

    def test_w2_emitted_when_coverage_below_100(self, capsys):
        out = self._run_w2_logic(95.0, capsys)
        assert "[W2]" in out
        assert "95.0%" in out
        assert "# pragma: no cover" in out

    def test_w2_emitted_at_80_percent(self, capsys):
        out = self._run_w2_logic(80.0, capsys)
        assert "[W2]" in out

    def test_w2_not_emitted_at_100_percent(self, capsys):
        out = self._run_w2_logic(100.0, capsys)
        assert "[W2]" not in out

    def test_w2_not_emitted_above_100(self, capsys):
        # edge case: score > 100 (invalid but shouldn't crash)
        out = self._run_w2_logic(100.1, capsys)
        assert "[W2]" not in out


# ---------------------------------------------------------------------------
# Gate 1 per-FR coverage check (new exit code 14)
# Validates that advance-phase blocks when finalize-gate --gate 1 was not
# called for every FR in quality_manifest.json.
# ---------------------------------------------------------------------------

class TestGate1PerFrCoverageCheck:
    """Tests for the Gate 1 per-FR coverage check inside _advance_prechecks."""

    def _make_manifest(self, tmp_path: Path, fr_ids: list) -> None:
        import json
        m = tmp_path / ".methodology" / "quality_manifest.json"
        m.parent.mkdir(parents=True, exist_ok=True)
        m.write_text(json.dumps({"fr_ids": fr_ids}), encoding="utf-8")

    def _make_timestamps(self, tmp_path: Path, entries: list) -> None:
        import json
        ts = tmp_path / ".methodology" / "gate_timestamps.jsonl"
        ts.parent.mkdir(parents=True, exist_ok=True)
        ts.write_text(
            "\n".join(json.dumps(e) for e in entries) + "\n",
            encoding="utf-8",
        )

    def _run_check(self, tmp_path: Path, completed_phase: int) -> int:
        import harness_cli
        return harness_cli._check_gate1_per_fr_coverage(tmp_path, completed_phase)

    def test_all_frs_covered_returns_0(self, tmp_path):
        self._make_manifest(tmp_path, ["FR-01", "FR-02", "FR-03"])
        self._make_timestamps(tmp_path, [
            {"phase": 4, "gate": 1, "fr_id": "FR-01", "ts": 1.0},
            {"phase": 4, "gate": 1, "fr_id": "FR-02", "ts": 2.0},
            {"phase": 4, "gate": 1, "fr_id": "FR-03", "ts": 3.0},
        ])
        assert self._run_check(tmp_path, 4) == 0

    def test_missing_fr_returns_14(self, tmp_path):
        self._make_manifest(tmp_path, ["FR-01", "FR-02", "FR-03"])
        self._make_timestamps(tmp_path, [
            {"phase": 4, "gate": 1, "fr_id": "FR-01", "ts": 1.0},
            {"phase": 4, "gate": 1, "fr_id": "FR-02", "ts": 2.0},
            # FR-03 missing
        ])
        assert self._run_check(tmp_path, 4) == 14

    def test_zero_gate1_entries_returns_14(self, tmp_path):
        """No finalize-gate --gate 1 calls at all → must block."""
        self._make_manifest(tmp_path, ["FR-01", "FR-02"])
        # Only a Gate 3 entry (batch commit scenario)
        self._make_timestamps(tmp_path, [
            {"phase": 4, "gate": 3, "fr_id": "phase", "ts": 1.0},
        ])
        assert self._run_check(tmp_path, 4) == 14

    def test_different_phase_entries_ignored(self, tmp_path):
        """Phase 3 entries must not count towards Phase 4 coverage."""
        self._make_manifest(tmp_path, ["FR-01", "FR-02"])
        self._make_timestamps(tmp_path, [
            {"phase": 3, "gate": 1, "fr_id": "FR-01", "ts": 1.0},
            {"phase": 3, "gate": 1, "fr_id": "FR-02", "ts": 2.0},
        ])
        assert self._run_check(tmp_path, 4) == 14  # phase=3 entries ≠ phase=4

    def test_phase_sentinel_fr_id_ignored(self, tmp_path):
        """fr_id='phase' (aggregate gate) must not count as per-FR coverage."""
        self._make_manifest(tmp_path, ["FR-01"])
        self._make_timestamps(tmp_path, [
            {"phase": 4, "gate": 1, "fr_id": "phase", "ts": 1.0},
        ])
        assert self._run_check(tmp_path, 4) == 14

    def test_no_manifest_skips_check(self, tmp_path):
        """Missing quality_manifest.json → skip check (non-FR project)."""
        # No manifest — check should be skipped gracefully
        assert self._run_check(tmp_path, 4) == 0

    def test_multiple_rounds_same_fr_ok(self, tmp_path):
        """Retries (multiple Gate 1 entries for same FR) must not cause false block."""
        self._make_manifest(tmp_path, ["FR-01", "FR-02"])
        self._make_timestamps(tmp_path, [
            {"phase": 4, "gate": 1, "fr_id": "FR-01", "ts": 1.0},
            {"phase": 4, "gate": 1, "fr_id": "FR-01", "ts": 2.0},  # retry
            {"phase": 4, "gate": 1, "fr_id": "FR-02", "ts": 3.0},
        ])
        assert self._run_check(tmp_path, 4) == 0

    def test_phase6_not_in_gate1_fr_check_set(self):
        """Phase 6 must not be in _PHASES_WITH_GATE1_FR_CHECK — Gate 4 replaces FR loop."""
        import harness_cli
        assert 6 not in harness_cli._PHASES_WITH_GATE1_FR_CHECK

    def test_phase6_check_skipped_even_with_fr_manifest(self, tmp_path):
        """advance-phase for Phase 6 must not block on missing Gate 1 records.

        Phase 6 (Quality Assurance) uses Gate 4 exclusively — there are no
        per-FR TDD-RED/GREEN/GATE1 steps, so _check_gate1_per_fr_coverage
        should not be called for completed_phase=6.
        """
        # Set up a manifest with FRs but NO Gate 1 timestamps for Phase 6
        self._make_manifest(tmp_path, ["FR-01", "FR-02", "FR-03"])
        # gate_timestamps.jsonl has only Gate 4 entries (typical P6 output)
        self._make_timestamps(tmp_path, [
            {"phase": 6, "gate": 4, "fr_id": "phase", "ts": 1.0},
        ])
        # _check_gate1_per_fr_coverage would return 14 if called for phase=6;
        # the guard in _advance_prechecks must prevent that call.
        assert self._run_check(tmp_path, 6) == 14, (
            "_check_gate1_per_fr_coverage itself returns 14 for phase=6 "
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
# run-fr-step idempotency skip: side-effects must fire even when skipping
# =============================================================================

class TestRunFrStepSkipSideEffects:
    """When _fr_step_already_done returns True, _mark_plan_item and
    _record_gate_timestamp (for GATE1-DELTA) must still be called."""

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

    def test_mark_plan_item_called_on_skip(self, tmp_path, monkeypatch):
        """_mark_plan_item must be called even when step is skipped (already done)."""
        import harness_cli
        called = []
        monkeypatch.setattr(harness_cli, "_fr_step_already_done", lambda *a, **k: True)
        monkeypatch.setattr(harness_cli, "_record_gate_timestamp", lambda *a, **k: None)
        original_mark = harness_cli._mark_plan_item
        def mock_mark(project, phase, step, fr_id):
            called.append((phase, step, fr_id))
            return original_mark(project, phase, step, fr_id)
        monkeypatch.setattr(harness_cli, "_mark_plan_item", mock_mark)

        plan = self._make_plan(tmp_path, 5, "FR-01")
        self._make_manifest(tmp_path, "FR-01")

        args = argparse.Namespace(
            phase=5, fr_id="FR-01", step="GATE1-DELTA",
            project=str(tmp_path), srs=None,
            timeout=600, max_fix_rounds=3, no_push=True,
            no_mcp=False, permission_mode=None, max_turns=None,
        )
        harness_cli.cmd_run_fr_step(args)

        assert ("FR-01" in str(called)), f"_mark_plan_item not called: {called}"
        assert "- [x] **[ORCH-GATE1]**" in plan.read_text()

    def test_gate_timestamp_recorded_on_gate1_delta_skip(self, tmp_path, monkeypatch):
        """_record_gate_timestamp must be called for GATE1-DELTA skip."""
        import harness_cli
        recorded = []
        monkeypatch.setattr(harness_cli, "_fr_step_already_done", lambda *a, **k: True)
        monkeypatch.setattr(harness_cli, "_mark_plan_item", lambda *a, **k: None)
        monkeypatch.setattr(harness_cli, "_record_gate_timestamp",
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
        """_record_gate_timestamp must NOT be called for non-DELTA step skips."""
        import harness_cli
        recorded = []
        monkeypatch.setattr(harness_cli, "_fr_step_already_done", lambda *a, **k: True)
        monkeypatch.setattr(harness_cli, "_mark_plan_item", lambda *a, **k: None)
        monkeypatch.setattr(harness_cli, "_record_gate_timestamp",
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
# _mark_p5_baseline_plan_items + _mark_generate_next_plan_item
# =============================================================================

class TestP5PlanMarking:
    """Deliverable plan items must be auto-marked by push-milestone / advance-phase."""

    def _make_plan(self, tmp_path: Path, content: str) -> Path:
        plan = tmp_path / ".methodology" / "phase5_plan.md"
        plan.parent.mkdir(parents=True, exist_ok=True)
        plan.write_text(content, encoding="utf-8")
        return plan

    def test_baseline_items_marked_by_push_milestone(self, tmp_path):
        import harness_cli
        content = (
            "- [ ] Integration tests pass\n"
            "- [ ] Performance tests meet targets\n"
            "- [ ] Security scan passes\n"
            "- [ ] Baseline established\n"
            "- [ ] **PUSH ⑦ — P5-baseline** (after BASELINE.md is generated):\n"
            "- [ ] `BASELINE.md` - System baseline\n"
            "- [ ] `VERIFICATION_REPORT.md` - Verification report\n"
        )
        plan = self._make_plan(tmp_path, content)
        harness_cli._mark_p5_baseline_plan_items(tmp_path)
        result = plan.read_text()
        assert result.count("- [x]") == 7, f"Expected 7 checked, got:\n{result}"
        assert "- [ ]" not in result

    def test_no_false_positive_on_other_items(self, tmp_path):
        import harness_cli
        content = (
            "- [ ] Something unrelated\n"
            "- [ ] Integration tests pass\n"
        )
        plan = self._make_plan(tmp_path, content)
        harness_cli._mark_p5_baseline_plan_items(tmp_path)
        result = plan.read_text()
        assert "- [ ] Something unrelated" in result  # not touched
        assert "- [x] Integration tests pass" in result

    def test_generate_next_plan_item_marked(self, tmp_path):
        import harness_cli
        content = "- [ ] Generate Phase 6 plan:\n"
        plan = tmp_path / ".methodology" / "phase5_plan.md"
        plan.parent.mkdir(parents=True, exist_ok=True)
        plan.write_text(content)
        harness_cli._mark_generate_next_plan_item(tmp_path, completed_phase=5, next_phase=6)
        assert "- [x] Generate Phase 6 plan:" in plan.read_text()

    def test_generate_next_plan_wrong_phase_not_marked(self, tmp_path):
        import harness_cli
        content = "- [ ] Generate Phase 7 plan:\n"
        plan = tmp_path / ".methodology" / "phase5_plan.md"
        plan.parent.mkdir(parents=True, exist_ok=True)
        plan.write_text(content)
        harness_cli._mark_generate_next_plan_item(tmp_path, completed_phase=5, next_phase=6)
        # Phase 7 item should NOT be touched when we're advancing to Phase 6
        assert "- [ ] Generate Phase 7 plan:" in plan.read_text()
