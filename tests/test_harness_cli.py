"""Tests for harness_cli.py init helpers and audit-structure command."""

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

    def test_missing_archive_returns_error(self, tmp_path):
        from harness_cli import _validate_p8_completion
        errors = _validate_p8_completion(tmp_path)
        assert any(".methodology-archive" in e for e in errors)

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
            project=str(tmp_path), phase=1, fr_id="FR-01",
            role="reviewer", prompt="Review FR-01", timeout=60, max_turns=5,
        )
        rc = cmd_dispatch(args)
        assert rc == 0
        approval_file = tmp_path / ".methodology" / "agent_b_approvals" / "FR-01.json"
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
            project=str(tmp_path), phase=1, fr_id="FR-01",
            role="reviewer", prompt="Review FR-01", timeout=60, max_turns=5,
        )
        rc = cmd_dispatch(args)
        assert rc == 0  # dispatch itself succeeded
        approval_file = tmp_path / ".methodology" / "agent_b_approvals" / "FR-01.json"
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
            project=str(tmp_path), phase=1, fr_id="FR-01",
            role="developer", prompt="Implement FR-01", timeout=60, max_turns=5,
        )
        rc = cmd_dispatch(args)
        assert rc == 0
        approval_file = tmp_path / ".methodology" / "agent_b_approvals" / "FR-01.json"
        assert not approval_file.exists(), "developer role must not write approval JSON"


class TestPushCheckpointAgentBGate:
    """push-checkpoint must enforce CI, Agent B, and checklist gates before committing."""

    def _write_approval(self, project: Path, fr_id: str, phase: int, status="APPROVE"):
        approvals_dir = project / ".methodology" / "agent_b_approvals"
        approvals_dir.mkdir(parents=True, exist_ok=True)
        docs = ["SRS.md"] if phase == 1 else ["SRS.md", "SAD.md"]
        (approvals_dir / f"{fr_id}.json").write_text(json.dumps({
            "fr": fr_id, "review_status": status,
            "docs_embedded": docs, "confidence": 0.9,
        }))

    def _write_ci_files(self, project: Path):
        """Create the CI workflow + git hook files required by the CI gate."""
        wf = project / ".github" / "workflows"
        wf.mkdir(parents=True, exist_ok=True)
        (wf / "harness_quality_gate.yml").write_text("# stub")
        hooks = project / ".git" / "hooks"
        hooks.mkdir(parents=True, exist_ok=True)
        (hooks / "prepare-commit-msg").write_text("#!/bin/sh")

    def _patch_conf(self, monkeypatch, sys, types, harness_cli):
        conf_mod = types.ModuleType("core.quality_gate.confidence_scorer")
        conf_mod.compute_confidence = lambda *_: {"composite": 95.0}
        conf_mod.should_auto_approve_p1p2 = lambda _: True
        conf_mod.format_confidence_report = lambda _: ""
        conf_mod.AUTO_APPROVE_P1P2_THRESHOLD = 80.0
        monkeypatch.setitem(sys.modules, "core.quality_gate.confidence_scorer", conf_mod)

    def test_missing_ci_wiring_blocks(self, tmp_path, monkeypatch, capsys):
        """push-checkpoint returns 5 when CI workflow / git hooks are absent."""
        from harness_cli import cmd_push_checkpoint
        import harness_cli, types, sys

        class _FakeGit:
            def ensure_gitignore(self): pass
            def commit_and_push_p1(self, **_kw): return True

        monkeypatch.setattr(harness_cli, "_make_git", lambda *_a, **_kw: _FakeGit())
        self._patch_conf(monkeypatch, sys, types, harness_cli)

        args = argparse.Namespace(
            project=str(tmp_path), phase=1, fr_ids="FR-01",
            skip_confidence=False, dry_run=False, no_push=False,
        )
        rc = cmd_push_checkpoint(args)
        assert rc == 5
        out = capsys.readouterr().out
        assert "BLOCKED" in out and "CI wiring" in out

    def test_missing_agent_b_approvals_blocks(self, tmp_path, monkeypatch, capsys):
        """push-checkpoint returns 5 when Agent B approvals are missing."""
        from harness_cli import cmd_push_checkpoint
        import harness_cli, types, sys

        self._write_ci_files(tmp_path)

        class _FakeGit:
            def ensure_gitignore(self): pass
            def commit_and_push_p1(self, **_kw): return True
            def commit_and_push_p2(self, **_kw): return True

        monkeypatch.setattr(harness_cli, "_make_git", lambda *_a, **_kw: _FakeGit())
        self._patch_conf(monkeypatch, sys, types, harness_cli)

        # Phase 1 uses phase-level deliverables (SRS.md etc.), not FR IDs.
        # fr_ids arg is irrelevant for P1/P2 — no approval files are written here.
        args = argparse.Namespace(
            project=str(tmp_path), phase=1, fr_ids="FR-01,FR-02,FR-03",
            skip_confidence=False, dry_run=False, no_push=False,
        )
        rc = cmd_push_checkpoint(args)
        assert rc == 5, "missing approvals should return 5"
        out = capsys.readouterr().out
        assert "BLOCKED" in out or "Missing" in out

    def test_commit_message_no_longer_says_human_review(self, tmp_path, monkeypatch):
        """Commit notes must NOT say 'human review'."""
        from harness_cli import cmd_push_checkpoint
        import harness_cli, types, sys

        self._write_ci_files(tmp_path)
        # P1 uses phase-level deliverables (SKILL.md §0.4), not FR IDs.
        for did in ["SRS.md", "SPEC_TRACKING.md", "TRACEABILITY_MATRIX.md", "TEST_INVENTORY.yaml"]:
            self._write_approval(tmp_path, did, phase=1)

        commit_calls: list[dict] = []
        class _FakeGit:
            def ensure_gitignore(self): pass
            def commit_and_push_p1(self, **kw):
                commit_calls.append(kw)
                return True
            def commit_and_push_p2(self, **kw):
                commit_calls.append(kw)
                return True

        monkeypatch.setattr(harness_cli, "_make_git", lambda *_a, **_kw: _FakeGit())
        self._patch_conf(monkeypatch, sys, types, harness_cli)

        args = argparse.Namespace(
            project=str(tmp_path), phase=1, fr_ids="FR-01",
            skip_confidence=False, dry_run=False, no_push=False,
        )
        rc = cmd_push_checkpoint(args)
        assert rc == 0
        assert commit_calls, "commit_and_push_p1 should have been called"
        background = commit_calls[0].get("background", "")
        notes = commit_calls[0].get("notes", [])
        assert "human review" not in background.lower(), "must not claim human review"
        assert all("human review" not in n.lower() for n in notes), "notes must not claim human review"
        assert "auto-approved" in background.lower() or "confidence" in background.lower()


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
            project=str(tmp_path), phase=1, fr_id="FR-01",
            role="developer", prompt="Implement FR-01", timeout=60, max_turns=5,
        )
        rc = cmd_dispatch(args)
        assert rc == 0
        output_file = tmp_path / ".methodology" / "agent_a_outputs" / "FR-01.json"
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
            project=str(tmp_path), phase=1, fr_id="FR-01",
            role="developer", prompt="Implement FR-01", timeout=60, max_turns=5,
        )
        rc = cmd_dispatch(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "WARN" in out
        assert not (tmp_path / ".methodology" / "agent_a_outputs" / "FR-01.json").exists()


class TestCheckChecklist:
    """Tests for cmd_check_checklist and _parse_plan_unchecked (Gap-7)."""

    def _write_plan(self, path: Path, content: str):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)

    def test_all_checked_returns_0(self, tmp_path, capsys):
        plan = tmp_path / ".methodology" / "phase1_plan.md"
        self._write_plan(plan, "- [x] **[A-2]** done\n- [x] **[B-2]** done\n")
        args = argparse.Namespace(project=str(tmp_path), phase=1)
        from harness_cli import cmd_check_checklist
        rc = cmd_check_checklist(args)
        assert rc == 0
        assert "✓" in capsys.readouterr().out

    def test_unchecked_mandatory_returns_1(self, tmp_path, capsys):
        plan = tmp_path / ".methodology" / "phase1_plan.md"
        self._write_plan(plan, "- [ ] **[A-2]** Agent A output\n- [x] **[B-2]** done\n")
        args = argparse.Namespace(project=str(tmp_path), phase=1)
        from harness_cli import cmd_check_checklist
        rc = cmd_check_checklist(args)
        assert rc == 1
        out = capsys.readouterr().out
        assert "BLOCKED" in out
        assert "A-2" in out

    def test_unchecked_advisory_only_returns_0(self, tmp_path, capsys):
        plan = tmp_path / ".methodology" / "phase1_plan.md"
        self._write_plan(plan, "- [x] **[A-2]** done\n- [ ] **[PREFLIGHT-CI]** ci check\n")
        args = argparse.Namespace(project=str(tmp_path), phase=1)
        from harness_cli import cmd_check_checklist
        rc = cmd_check_checklist(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "Advisory" in out or "advisory" in out or "PREFLIGHT-CI" in out

    def test_missing_plan_returns_1(self, tmp_path, capsys):
        args = argparse.Namespace(project=str(tmp_path), phase=1)
        from harness_cli import cmd_check_checklist
        rc = cmd_check_checklist(args)
        assert rc == 1
        out = capsys.readouterr().out
        # f-string fix: phase and project must be interpolated, not printed literally
        assert "{phase}" not in out
        assert "{project}" not in out
        assert "1" in out  # phase number should appear

    def test_phase_truth_is_mandatory(self, tmp_path):
        plan = tmp_path / ".methodology" / "phase3_plan.md"
        self._write_plan(plan, "- [ ] **[PHASE-TRUTH]** verify truth\n")
        from harness_cli import _parse_plan_unchecked
        mandatory, advisory = _parse_plan_unchecked(plan)
        assert any("PHASE-TRUTH" in m for m in mandatory)


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
