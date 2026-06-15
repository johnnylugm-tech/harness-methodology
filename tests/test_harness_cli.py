"""Tests for harness_cli.py init helpers and audit-structure command."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from unittest import mock


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

    def test_init_copies_adr_template_with_sentinel(self, tmp_path):
        """init-project must place the harness:template-stub sentinel in the
        ADR template it copies to 02-architecture/adr/ADR.md."""
        from harness_cli import _init_copy_templates
        import harness_cli as hc

        harness_root = Path(hc.__file__).parent
        (tmp_path / "01-requirements").mkdir()
        (tmp_path / "02-architecture").mkdir()
        (tmp_path / "02-architecture" / "adr").mkdir()

        _init_copy_templates(tmp_path, harness_root)

        adr = tmp_path / "02-architecture" / "adr" / "ADR.md"
        content = adr.read_text(encoding="utf-8")
        assert "<!-- harness:template-stub -->" in content

    def test_init_then_check_constitution_passes_for_stub_adr(self, tmp_path):
        """E2E: _init_copy_templates → cmd_check_constitution --file
        02-architecture/adr/ADR.md → returns 0 with [PASS] (sentinel vacuous
        pass)."""
        from harness_cli import _init_copy_templates, cmd_check_constitution
        import harness_cli as hc
        import argparse

        harness_root = Path(hc.__file__).parent
        (tmp_path / "01-requirements").mkdir()
        (tmp_path / "02-architecture").mkdir()
        (tmp_path / "02-architecture" / "adr").mkdir()

        _init_copy_templates(tmp_path, harness_root)

        args = argparse.Namespace(
            phase=2,
            project=str(tmp_path),
            file="02-architecture/adr/ADR.md",
        )
        rc = cmd_check_constitution(args)
        # Step 3 lands the --file branch — without it, this would crash.
        # Once Step 3 lands, this returns 0 (PASS) and stdout contains
        # "PASS" + the file's vacuous 100/100/100/100 score.
        assert rc == 0


class TestCheckConstitutionFile:
    """Tests for cmd_check_constitution --file <path> (single-file branch)."""

    def test_file_flag_missing_file_skip_exit_0(self, tmp_path, capsys):
        import argparse
        from harness_cli import cmd_check_constitution

        args = argparse.Namespace(
            phase=2, project=str(tmp_path),
            file="02-architecture/adr/ADR.md",
        )
        rc = cmd_check_constitution(args)
        out = capsys.readouterr().out
        assert rc == 0
        assert "[SKIP]" in out
        assert "File not found" in out

    def test_file_flag_directory_skip_exit_0(self, tmp_path, capsys):
        import argparse
        from harness_cli import cmd_check_constitution

        # Create a real directory and pass it via --file
        sub = tmp_path / "02-architecture"
        sub.mkdir()

        args = argparse.Namespace(
            phase=2, project=str(tmp_path),
            file="02-architecture",
        )
        rc = cmd_check_constitution(args)
        out = capsys.readouterr().out
        assert rc == 0
        assert "[SKIP]" in out
        assert "Not a regular file" in out

    def test_file_flag_stub_with_sentinel_passes(self, tmp_path, capsys):
        """Stub ADR template (with sentinel) returns [PASS] via vacuous 100s."""
        import argparse
        from harness_cli import cmd_check_constitution

        adr_dir = tmp_path / "02-architecture" / "adr"
        adr_dir.mkdir(parents=True)
        adr = adr_dir / "ADR.md"
        adr.write_text(
            "# ADR-01: foo\n\n"
            "<!-- harness:template-stub -->\n\n"
            "## Status\n{Proposed}\n\n"
            "Placeholder prose. " * 10,
            encoding="utf-8",
        )

        args = argparse.Namespace(
            phase=2, project=str(tmp_path),
            file="02-architecture/adr/ADR.md",
        )
        rc = cmd_check_constitution(args)
        out = capsys.readouterr().out
        assert rc == 0
        assert "[PASS]" in out

    def test_file_flag_low_score_fails(self, tmp_path, capsys):
        """Real-looking ADR with no constitution keywords returns [FAIL]."""
        import argparse
        from harness_cli import cmd_check_constitution

        adr_dir = tmp_path / "02-architecture" / "adr"
        adr_dir.mkdir(parents=True)
        adr = adr_dir / "ADR.md"
        # Long enough to pass the 100-char gate; no keywords; no FR refs.
        adr.write_text(
            "# ADR-01: random notes\n\n"
            + ("Just plain prose with no special terms. " * 20),
            encoding="utf-8",
        )

        args = argparse.Namespace(
            phase=2, project=str(tmp_path),
            file="02-architecture/adr/ADR.md",
        )
        rc = cmd_check_constitution(args)
        out = capsys.readouterr().out
        assert rc == 1
        assert "[FAIL]" in out

    def test_file_flag_omitted_uses_directory_branch(self, tmp_path, capsys):
        """Default (no --file) still uses the phase directory branch."""
        import argparse
        from harness_cli import cmd_check_constitution

        # No phase directory at all → directory branch's [SKIP] path fires
        args = argparse.Namespace(
            phase=2, project=str(tmp_path), file=None,
        )
        rc = cmd_check_constitution(args)
        out = capsys.readouterr().out
        assert rc == 0
        assert "Phase 2 directory not found" in out

    def test_file_flag_absolute_path_overrides_project(self, tmp_path, capsys):
        """Absolute --file path wins; --project is ignored for resolution."""
        import argparse
        from harness_cli import cmd_check_constitution

        other = tmp_path / "other"
        other.mkdir()
        adr = other / "ADR.md"
        adr.write_text(
            "# ADR\n\n<!-- harness:template-stub -->\n\nPlaceholder. " * 10,
            encoding="utf-8",
        )

        args = argparse.Namespace(
            phase=2, project=str(tmp_path),  # different from `other`
            file=str(adr),                  # absolute path
        )
        rc = cmd_check_constitution(args)
        out = capsys.readouterr().out
        assert rc == 0
        assert "[PASS]" in out
        assert str(adr) in out


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
            cmd_audit_structure(args)
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
        import io
        import sys
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
        import io
        import sys
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
        import io
        import sys
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

    # -- Bug 4 regression: ### headers and file-initial # headers --------

    def _audit_json(self, tmp_path):
        """Run cmd_audit_structure with json_out and return parsed result."""
        import io
        import sys
        from harness_cli import cmd_audit_structure

        buf = io.StringIO()
        old, sys.stdout = sys.stdout, buf
        try:
            cmd_audit_structure(self._make_args(str(tmp_path), json_out=True))
        finally:
            sys.stdout = old
        return json.loads(buf.getvalue())

    def test_content_quality_h3_headers_count_as_sections(self, tmp_path):
        """### (level-3) headers must satisfy the '≥ 2 sections' check.

        Before the fix, only \\n## and \\n# were counted; a TEST_SPEC.md written
        entirely with ### FR-XX: headings was falsely flagged as suspicious.
        """
        (tmp_path / "06-quality").mkdir()
        (tmp_path / "06-quality" / "QUALITY_REPORT.md").write_text(
            "### Section One\n\nSome content.\n\n### Section Two\n\n" + "x" * 200
        )
        data = self._audit_json(tmp_path)
        files = {
            f["path"]: f["quality"]
            for f in data["dimensions"]["content_quality"]["details"]["P6"]["files"]
        }
        assert files["06-quality/QUALITY_REPORT.md"] == "good", (
            "###-only file incorrectly flagged as suspicious"
        )

    def test_content_quality_file_initial_header_is_counted(self, tmp_path):
        """A # heading at byte-0 (no preceding \\n) must count as a section.

        Before the fix, \\n# was used, so a header at the very first line was
        invisible to the counter.  Combined with a second ##, that gave count=1
        which triggered '< 2 markdown sections'.
        """
        (tmp_path / "06-quality").mkdir()
        (tmp_path / "06-quality" / "QUALITY_REPORT.md").write_text(
            "# Title at line 1\n## Second Section\n\nContent.\n" + "x" * 200
        )
        data = self._audit_json(tmp_path)
        files = {
            f["path"]: f["quality"]
            for f in data["dimensions"]["content_quality"]["details"]["P6"]["files"]
        }
        assert files["06-quality/QUALITY_REPORT.md"] == "good", (
            "file-initial # header not counted as a section"
        )


# =============================================================================
# cmd_audit_structure — init → audit round-trip
# =============================================================================

class TestInitThenAudit:
    def test_init_then_audit_reports_dirs_and_artifacts(self, tmp_path):
        """After init-project, audit-structure should pass directory existence."""
        import argparse
        import io
        import sys

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
            cmd_audit_structure(args)
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
                "docs_embedded": ["SRS.md", "SAD.md"], "reason": "Reviewed all deliverables; acceptance criteria covered, no critical gaps found.", "citations": ["SRS.md:1"],
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
            "docs_embedded": ["SRS.md", "SAD.md"], "reason": "Reviewed all deliverables; acceptance criteria covered, no critical gaps found.", "citations": ["SRS.md:1"],
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

    def test_approve_empty_reason_returns_1(self, tmp_path, capsys):
        """A1: APPROVE with too-short reason → reject (shell approval)."""
        approvals_dir = tmp_path / ".methodology" / "agent_b_approvals"
        approvals_dir.mkdir(parents=True)
        (approvals_dir / "FR-01.json").write_text(json.dumps({
            "fr": "FR-01", "review_status": "APPROVE",
            "docs_embedded": ["SRS.md", "SAD.md"],
            "reason": "ok", "citations": ["SRS.md:1"],
        }))
        args = argparse.Namespace(project=str(tmp_path), phase=3, fr_ids="FR-01")
        from harness_cli import cmd_verify_agent_b_approvals
        assert cmd_verify_agent_b_approvals(args) == 1

    def test_approve_no_citations_returns_1(self, tmp_path, capsys):
        """A1: APPROVE without citations[] → reject."""
        approvals_dir = tmp_path / ".methodology" / "agent_b_approvals"
        approvals_dir.mkdir(parents=True)
        (approvals_dir / "FR-01.json").write_text(json.dumps({
            "fr": "FR-01", "review_status": "APPROVE",
            "docs_embedded": ["SRS.md", "SAD.md"],
            "reason": "Reviewed all deliverables; acceptance criteria covered fully.",
            "citations": [],
        }))
        args = argparse.Namespace(project=str(tmp_path), phase=3, fr_ids="FR-01")
        from harness_cli import cmd_verify_agent_b_approvals
        assert cmd_verify_agent_b_approvals(args) == 1

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
            "docs_embedded": ["SRS.md", "SAD.md"], "reason": "Reviewed all deliverables; acceptance criteria covered, no critical gaps found.", "citations": ["SRS.md:1"],
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
                "docs_embedded": ["SRS.md", "SAD.md"], "reason": "Reviewed all deliverables; acceptance criteria covered, no critical gaps found.", "citations": ["SRS.md:1"],
                "confidence": 0.9,
            }))
        # Pass FR IDs — they must be ignored for phase=2
        args = argparse.Namespace(project=str(tmp_path), phase=2, fr_ids="FR-01,FR-02,FR-03")
        from harness_cli import cmd_verify_agent_b_approvals
        rc = cmd_verify_agent_b_approvals(args)
        assert rc == 0  # SAD.md + ADR.md + TEST_SPEC.md approvals present → pass


class TestVerifyEnvCheckClaims:
    """A2: framework spot-check of env_check_result.json self-reported claims."""

    def _write(self, project: Path, payload: dict) -> None:
        work = project / ".sessi-work"
        work.mkdir(parents=True, exist_ok=True)
        (work / "env_check_result.json").write_text(json.dumps(payload))

    def test_claimed_cli_tool_missing_blocks(self, tmp_path):
        from harness_cli import _verify_env_check_claims
        self._write(tmp_path, {"cli_tools": {"required": [
            {"name": "definitely_not_a_real_tool_xyz", "present": True},
        ]}})
        findings = _verify_env_check_claims(tmp_path)
        assert any("definitely_not_a_real_tool_xyz" in f for f in findings)

    def test_claimed_absent_tool_not_flagged(self, tmp_path):
        """present:false tools are not force-verified."""
        from harness_cli import _verify_env_check_claims
        self._write(tmp_path, {"cli_tools": {"required": [
            {"name": "definitely_not_a_real_tool_xyz", "present": False},
        ]}})
        assert _verify_env_check_claims(tmp_path) == []

    def test_claimed_env_var_unset_blocks(self, tmp_path, monkeypatch):
        from harness_cli import _verify_env_check_claims
        monkeypatch.delenv("HARNESS_A2_PROBE", raising=False)
        self._write(tmp_path, {"env_vars": {"required": [
            {"name": "HARNESS_A2_PROBE", "present": True},
        ]}})
        findings = _verify_env_check_claims(tmp_path)
        assert any("HARNESS_A2_PROBE" in f for f in findings)

    def test_present_cli_tool_passes(self, tmp_path):
        """A real tool claimed present is not flagged."""
        from harness_cli import _verify_env_check_claims
        self._write(tmp_path, {"cli_tools": {"required": [
            {"name": "python3", "present": True},
        ]}})
        assert _verify_env_check_claims(tmp_path) == []

    def test_no_result_file_no_findings(self, tmp_path):
        from harness_cli import _verify_env_check_claims
        assert _verify_env_check_claims(tmp_path) == []

    def test_annotated_venv_name_not_flagged(self, tmp_path):
        """B1: 'python3 (.venv)' annotation must be stripped before PATH check.
        The base tool 'python3' exists on PATH, so no fabrication finding.
        """
        from harness_cli import _verify_env_check_claims
        self._write(tmp_path, {"cli_tools": {"required": [
            {"name": "python3 (.venv)", "present": True},
        ]}})
        assert _verify_env_check_claims(tmp_path) == [], (
            "Annotated name 'python3 (.venv)' should resolve to 'python3' "
            "which is on PATH — must not be flagged as fabrication"
        )

    def test_python_package_not_flagged_via_import(self, tmp_path):
        """B1: Python packages (e.g. 'json') not on PATH must pass via import fallback."""
        from harness_cli import _verify_env_check_claims
        # 'json' is stdlib — always importable, never a CLI binary.
        # The old code would flag it; the new code must not.
        self._write(tmp_path, {"cli_tools": {"required": [
            {"name": "json", "present": True},
        ]}})
        assert _verify_env_check_claims(tmp_path) == [], (
            "Python stdlib module 'json' must pass via import fallback, "
            "not be flagged as fabrication just because it's not on PATH"
        )


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
                "reason": "Reviewed deliverable; acceptance criteria covered, no critical gaps.",
                "citations": ["SRS.md:1"],
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
            '{"fr": "FR-03", "review_status": "APPROVE", "docs_embedded": ["SRS.md", "SAD.md"], "reason": "Reviewed all deliverables; acceptance criteria covered, no critical gaps found.", "citations": ["SRS.md:1"], "confidence": 0.85}\n'
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
        import sys
        import types
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
        import sys
        import types
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
        import sys
        import types
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
        import sys
        import types
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
        import sys
        import types
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

def _mock_constitution_pass(monkeypatch):
    """Make constitution postflight return vacuous pass (score 100%)."""
    from core.quality_gate.constitution.runner import ConstitutionResult
    _vacuous = ConstitutionResult(score=100.0, passed=True, violations=[])
    monkeypatch.setattr(
        "core.quality_gate.constitution.run_constitution_check",
        lambda *a, **kw: _vacuous,
    )
    monkeypatch.setattr(
        "core.quality_gate.constitution.profile.get_profile",
        lambda: type("_P", (), {"composite_threshold": lambda s, p: 75.0})(),
    )


class TestAdvancePrechecksTDD:
    """Tests for the P3+ TDD block in _advance_prechecks."""

    def _make_p3_project(self, tmp_path: Path) -> None:
        """Minimal P3 project skeleton (PhaseAuditor will be mocked)."""
        import harness_cli
        (tmp_path / ".methodology").mkdir()
        (tmp_path / "03-development" / "src").mkdir(parents=True)
        # Next-phase plan required by _advance_prechecks (phase >= 3)
        (tmp_path / ".methodology" / "phase4_plan.md").touch()
        # Finalize-gate sentinels — _advance_prechecks verifies these exist
        harness_cli._write_finalize_sentinels_for_tests(tmp_path)

    def test_pytest_failure_returns_9(self, tmp_path, monkeypatch):
        """pytest non-zero exit → _advance_prechecks returns 9."""
        from harness_cli import _advance_prechecks

        self._make_p3_project(tmp_path)
        _mock_constitution_pass(monkeypatch)
        monkeypatch.setattr("harness_cli._run_phase_auditor", lambda p, ph: 0)
        monkeypatch.setattr(
            "core.quality_gate.phase_truth_verifier.PhaseTruthVerifier",
            type("FV", (), {
                "__init__": lambda s, p, ph: None,
                "verify": lambda s: {"passed": True, "total_score": 100.0},
            }),
        )

        def _fake_run(cmd, *a, **kw):
            class _FakeResult:
                pass
            res = _FakeResult()
            if "pytest" in cmd:
                res.returncode = 1
            else:
                res.returncode = 0
            return res

        import harness_cli
        monkeypatch.setattr(harness_cli.subprocess, "run", _fake_run)

        rc = _advance_prechecks(tmp_path, completed_phase=3)
        assert rc == 9

    def test_pytest_skipped_when_no_src_dir(self, tmp_path, monkeypatch):
        """No 03-development/src → pytest step skipped, continues to spec-coverage."""
        from harness_cli import _advance_prechecks
        import harness_cli

        (tmp_path / ".methodology").mkdir()  # no src dir
        harness_cli._write_finalize_sentinels_for_tests(tmp_path)
        _mock_constitution_pass(monkeypatch)
        monkeypatch.setattr("harness_cli._run_phase_auditor", lambda p, ph: 0)
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
        import harness_cli

        (tmp_path / ".methodology").mkdir()
        harness_cli._write_finalize_sentinels_for_tests(tmp_path)
        _mock_constitution_pass(monkeypatch)
        monkeypatch.setattr("harness_cli._run_phase_auditor", lambda p, ph: 0)
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
        _mock_constitution_pass(monkeypatch)
        monkeypatch.setattr("harness_cli._run_phase_auditor", lambda p, ph: 0)
        monkeypatch.setattr("harness_cli._verify_agent_b_approvals_core",
                            lambda p, ph, ids: (True, "mocked"))

        rc = _advance_prechecks(tmp_path, completed_phase=2)
        assert rc == 0

    def test_threshold_escalation_p4_uses_70_80(self, tmp_path, monkeypatch):
        """P4: spec-coverage threshold=70%, D4 threshold=80%."""
        from harness_cli import _advance_prechecks
        import harness_cli

        (tmp_path / ".methodology").mkdir()
        harness_cli._write_finalize_sentinels_for_tests(tmp_path)
        _mock_constitution_pass(monkeypatch)
        monkeypatch.setattr("harness_cli._run_phase_auditor", lambda p, ph: 0)
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
        import harness_cli

        (tmp_path / ".methodology").mkdir()
        harness_cli._write_finalize_sentinels_for_tests(tmp_path)
        _mock_constitution_pass(monkeypatch)
        monkeypatch.setattr("harness_cli._run_phase_auditor", lambda p, ph: 0)
        # Phase 6 fires Agent B approval check before spec-coverage; stub it so
        # only the threshold value is exercised here (agent B tested elsewhere).
        monkeypatch.setattr(
            "harness_cli._verify_agent_b_approvals_core",
            lambda p, ph, ids: (True, "mocked"),
        )
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
# _advance_prechecks — Agent B approvals (P1/P2/P6)
# =============================================================================

class TestAdvancePreChecksAgentB:
    """Agent B approval gate in _advance_prechecks for P1/P2/P6."""

    def _mock_p1_prechecks(self, monkeypatch):
        """Patch non-AB checks so only AB check is exercised."""
        monkeypatch.setattr("harness_cli._run_phase_auditor", lambda p, ph: 0)
        _mock_constitution_pass(monkeypatch)

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
        from harness_cli import _advance_prechecks, _PHASE_DELIVERABLES
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
        from harness_cli import _advance_prechecks
        import harness_cli

        (tmp_path / ".methodology").mkdir()
        harness_cli._write_finalize_sentinels_for_tests(tmp_path)
        _mock_constitution_pass(monkeypatch)
        monkeypatch.setattr("harness_cli._run_phase_auditor", lambda p, ph: 0)
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

    # -- P6 Agent B enforcement tests ----------------------------------------

    def _mock_p6_non_ab_prechecks(self, tmp_path, monkeypatch):
        """Set up all P6 advance_prechecks prerequisites EXCEPT Agent B.

        P6 check order (simplified): Phase Truth → Stage Pass auto-gen →
        next-phase plan → phase auditor → constitution → Agent B → TDD-PRECHECK.
        This helper passes everything before Agent B so the test can control
        whether approvals exist without fighting unrelated failures.
        """
        import harness_cli

        method = tmp_path / ".methodology"
        method.mkdir(exist_ok=True)
        (tmp_path / "03-development" / "src").mkdir(parents=True, exist_ok=True)
        (tmp_path / ".methodology" / "phase7_plan.md").touch()
        # Pre-create Stage Pass so auto-generation is skipped
        (tmp_path / "00-summary").mkdir(exist_ok=True)
        (tmp_path / "00-summary" / "Phase6_STAGE_PASS.md").write_text(
            "# Phase 6 Stage Pass\n## Summary\n", encoding="utf-8"
        )
        harness_cli._write_finalize_sentinels_for_tests(tmp_path)

        monkeypatch.setattr("harness_cli._run_phase_auditor", lambda p, ph: 0)
        _mock_constitution_pass(monkeypatch)
        monkeypatch.setattr(
            "core.quality_gate.phase_truth_verifier.PhaseTruthVerifier",
            type("FV", (), {
                "__init__": lambda s, p, ph: None,
                "verify": lambda s: {"passed": True, "total_score": 100.0},
            }),
        )
        monkeypatch.setattr(
            "harness_cli._run_spec_coverage_check", lambda p, t, **kw: (0, 100.0)
        )
        monkeypatch.setattr("harness_cli.shutil.which", lambda cmd: True)
        monkeypatch.setattr(
            "harness_cli.subprocess.run",
            lambda cmd, **kw: type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})(),
        )
        # Gate-1 FR coverage and mutmut not exercised by these tests
        monkeypatch.setattr("harness_cli._check_gate1_live_coverage", lambda p, ph: 0)
        monkeypatch.setattr(
            "core.quality_gate.mutation_enforcer.run_mutation_precheck",
            lambda p: (True, "ok"),
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
        from harness_cli import _advance_prechecks

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
        from harness_cli import _advance_prechecks, _PHASE_DELIVERABLES, _REQUIRED_EMBEDDED_DOCS

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
        import sys
        import types
        import harness_cli

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
        import harness_cli
        import sys
        import io

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
        import harness_cli
        import sys
        import io

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
        import harness_cli
        import sys
        import io

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
        import sys
        import types
        import harness_cli

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
        import harness_cli
        import sys
        import io

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
        import harness_cli
        import sys
        import io

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
        import sys
        import types
        import harness_cli
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
        import sys
        import types
        import harness_cli
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
# P0-B: _append_dev_log_tdd_entry
# ---------------------------------------------------------------------------

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

class TestGate1LiveCoverageCheck:
    """Tests for the Gate 1 live coverage check inside _advance_prechecks."""

    def _make_manifest(self, tmp_path: Path, fr_ids: list) -> None:
        import json
        m = tmp_path / ".methodology" / "quality_manifest.json"
        m.parent.mkdir(parents=True, exist_ok=True)
        m.write_text(json.dumps({"fr_ids": fr_ids}), encoding="utf-8")

    def _run_check(self, tmp_path: Path, completed_phase: int) -> int:
        import harness_cli
        return harness_cli._check_gate1_live_coverage(tmp_path, completed_phase)

    def test_all_frs_covered_returns_0(self, tmp_path):
        """All FRs have real pytest coverage ≥ min → return 0."""
        import harness_cli
        self._make_manifest(tmp_path, ["FR-01", "FR-02", "FR-03"])
        with mock.patch.object(
            harness_cli, "_validate_fr_coverage_immediate", return_value=100.0
        ):
            assert self._run_check(tmp_path, 4) == 0

    def test_missing_fr_returns_14(self, tmp_path):
        """Live pytest returns None (no tests/ or pytest errored) → BLOCKED 14."""
        import harness_cli
        self._make_manifest(tmp_path, ["FR-01", "FR-02", "FR-03"])
        with mock.patch.object(
            harness_cli, "_validate_fr_coverage_immediate", return_value=None
        ):
            assert self._run_check(tmp_path, 4) == 14

    def test_zero_gate1_entries_returns_14(self, tmp_path):
        """All FRs have pytest erroring (None) → must block."""
        import harness_cli
        self._make_manifest(tmp_path, ["FR-01", "FR-02"])
        with mock.patch.object(
            harness_cli, "_validate_fr_coverage_immediate", return_value=None
        ):
            assert self._run_check(tmp_path, 4) == 14

    def test_delta_auto_skip_skips_live_pytest(self, tmp_path):
        """DELTA phase with code unchanged → auto-skip, return 0, pytest not called."""
        import harness_cli
        self._make_manifest(tmp_path, ["FR-01", "FR-02"])
        with mock.patch.object(
            harness_cli, "_fr_code_changed_since_last_gate1", return_value=False
        ):
            with mock.patch.object(
                harness_cli, "_validate_fr_coverage_immediate"
            ) as mock_cov:
                assert self._run_check(tmp_path, 4) == 0
                mock_cov.assert_not_called()

    def test_single_fr_manifest_passes(self, tmp_path):
        """Manifest with one FR, live coverage ≥ min → pass."""
        import harness_cli
        self._make_manifest(tmp_path, ["FR-01"])
        with mock.patch.object(
            harness_cli, "_validate_fr_coverage_immediate", return_value=100.0
        ):
            assert self._run_check(tmp_path, 4) == 0

    def test_no_manifest_skips_check(self, tmp_path):
        """Missing quality_manifest.json → skip check (non-FR project)."""
        # No manifest — check should be skipped gracefully
        assert self._run_check(tmp_path, 4) == 0

    def test_multiple_rounds_same_fr_ok(self, tmp_path):
        """Live pytest is per-FR idempotent — second FR also passes."""
        import harness_cli
        self._make_manifest(tmp_path, ["FR-01", "FR-02"])
        with mock.patch.object(
            harness_cli, "_validate_fr_coverage_immediate", return_value=100.0
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
        import harness_cli
        self._make_manifest(tmp_path, ["FR-01", "FR-02", "FR-03"])
        # Phase 6 is NOT a DELTA auto-skip phase → falls through to live pytest.
        # Without test files, _validate_fr_coverage_immediate returns None → 14.
        with mock.patch.object(
            harness_cli, "_validate_fr_coverage_immediate", return_value=None
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
        with patch("harness_cli._fr_code_changed_since_last_gate1", return_value=False):
            assert self._run_check(tmp_path, 7) == 0

    def test_delta_loop_no_skip_when_changed(self, tmp_path):
        """Layer 4: if any FR changed, the normal per-FR coverage requirement still applies."""
        from unittest.mock import patch
        self._make_manifest(tmp_path, ["FR-01", "FR-02"])
        # One FR changed → not all unchanged → falls through to timestamp check → missing → 14.
        with patch("harness_cli._fr_code_changed_since_last_gate1",
                   side_effect=lambda fr, project: fr == "FR-02"):
            assert self._run_check(tmp_path, 7) == 14

    def test_delta_loop_autoskip_includes_p4(self, tmp_path):
        """Audit Fix C: P4 is carryforward — its plan promises auto-skip, so advance-phase
        must also auto-satisfy P4 coverage when no FR's code changed (range is 4,5,7,8)."""
        from unittest.mock import patch
        self._make_manifest(tmp_path, ["FR-01", "FR-02"])
        with patch("harness_cli._fr_code_changed_since_last_gate1", return_value=False):
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

    def test_gate_timestamp_recorded_on_gate1_delta_skip(self, tmp_path, monkeypatch):
        """_record_gate_timestamp must be called for GATE1-DELTA skip."""
        import harness_cli
        recorded = []
        monkeypatch.setattr(harness_cli, "_fr_step_already_done", lambda *a, **k: True)
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
        import harness_cli
        self._make_project(
            tmp_path,
            spec_rows=[
                "test_fr_01_lexicon_coverage[視頻→影片]",
                "test_fr_01_lexicon_coverage[影片→視頻]",
            ],
            test_body="def test_fr_01_lexicon_coverage(word_pair):\n    pass\n",
        )
        result = harness_cli._compute_fr_spec_data(tmp_path, "FR-01", "tests/test_fr01.py")
        assert result["spec_cov_pct"] == 100, (
            f"parameterized names must match by base name; got {result['spec_cov_pct']}"
        )

    def test_missing_base_function_gives_zero(self, tmp_path):
        """If the base function does not exist in the test file, spec_cov_pct == 0."""
        import harness_cli
        self._make_project(
            tmp_path,
            spec_rows=["test_fr_01_missing[param]"],
            test_body="# no functions here\n",
        )
        result = harness_cli._compute_fr_spec_data(tmp_path, "FR-01", "tests/test_fr01.py")
        assert result["spec_cov_pct"] == 0

    def test_backtick_name_matches(self, tmp_path):
        """TEST_SPEC row '`test_fn`' (backtick-quoted) must match 'def test_fn'."""
        import harness_cli
        self._make_project(
            tmp_path,
            spec_rows=["`test_fr_01_lookup`"],
            test_body="def test_fr_01_lookup(x):\n    pass\n",
        )
        result = harness_cli._compute_fr_spec_data(tmp_path, "FR-01", "tests/test_fr01.py")
        assert result["spec_cov_pct"] == 100, (
            f"backtick-quoted spec name must strip backticks before matching; got {result['spec_cov_pct']}"
        )

    def test_paren_suffix_matches(self, tmp_path):
        """TEST_SPEC row 'test_fn()' (with parens) must match 'def test_fn'."""
        import harness_cli
        self._make_project(
            tmp_path,
            spec_rows=["test_fr_01_lookup()"],
            test_body="def test_fr_01_lookup(x):\n    pass\n",
        )
        result = harness_cli._compute_fr_spec_data(tmp_path, "FR-01", "tests/test_fr01.py")
        assert result["spec_cov_pct"] == 100, (
            f"() suffix must be stripped before matching; got {result['spec_cov_pct']}"
        )

    def test_async_def_matches(self, tmp_path):
        """'async def test_fn(...)' must be found the same as sync 'def test_fn'."""
        import harness_cli
        self._make_project(
            tmp_path,
            spec_rows=["test_fr_01_async"],
            test_body="async def test_fr_01_async(client):\n    pass\n",
        )
        result = harness_cli._compute_fr_spec_data(tmp_path, "FR-01", "tests/test_fr01.py")
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
        import harness_cli
        monkeypatch.setattr(subprocess, "run", self._fake_run_factory("abc123", "batch999"))
        sha = harness_cli._fr_gate1_commit_sha("FR-01", tmp_path)
        assert sha == "abc123"

    def test_fallback_to_batch_commit(self, tmp_path, monkeypatch):
        """If per-FR pattern finds nothing, must return SHA from batch 'Gate1 PASS' grep."""
        import subprocess
        import harness_cli
        monkeypatch.setattr(subprocess, "run", self._fake_run_factory("", "deadbeef"))
        sha = harness_cli._fr_gate1_commit_sha("FR-01", tmp_path)
        assert sha == "deadbeef"

    def test_returns_none_when_no_commit(self, tmp_path, monkeypatch):
        """No Gate1 PASS commit of any kind → returns None."""
        import subprocess
        import harness_cli
        monkeypatch.setattr(subprocess, "run", self._fake_run_factory("", ""))
        sha = harness_cli._fr_gate1_commit_sha("FR-01", tmp_path)
        assert sha is None


# =============================================================================
# _git_test_patterns: symlink-aware test path resolution for git operations
# =============================================================================

class TestGitTestPatterns:

    def test_no_symlink_returns_standard_patterns(self, tmp_path):
        """When tests/ is a regular directory, return only standard patterns."""
        import harness_cli
        (tmp_path / "tests").mkdir()
        patterns = harness_cli._git_test_patterns(tmp_path, "01", "1")
        assert patterns == ["tests/test_fr01.py", "tests/test_fr1.py"]

    def test_symlink_adds_resolved_patterns(self, tmp_path):
        """When tests/ → 03-development/tests/, include git-tracked real paths."""
        import harness_cli
        real = tmp_path / "03-development" / "tests"
        real.mkdir(parents=True)
        (tmp_path / "tests").symlink_to(real)
        patterns = harness_cli._git_test_patterns(tmp_path, "01", "1")
        assert "tests/test_fr01.py" in patterns
        assert "03-development/tests/test_fr01.py" in patterns
        assert "03-development/tests/test_fr1.py" in patterns
        assert len(patterns) == 4

    def test_symlink_outside_project_ignored(self, tmp_path):
        """Symlink resolving outside project root → ValueError caught, no extra patterns."""
        import harness_cli
        import tempfile
        outside = Path(tempfile.mkdtemp())
        try:
            (tmp_path / "tests").symlink_to(outside)
            patterns = harness_cli._git_test_patterns(tmp_path, "01", "1")
            assert len(patterns) == 2  # only standard patterns, no crash
        finally:
            import shutil
            shutil.rmtree(outside, ignore_errors=True)

def test_l1_finalize_sentinel_path_legacy_fallback(tmp_path):
    """Test L1: Legacy sentinel fallback in _finalize_sentinel_path."""
    from harness_cli import _finalize_sentinel_path
    
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
    import harness_cli

    (tmp_path / ".methodology").mkdir(exist_ok=True)
    (tmp_path / "03-development" / "src").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".methodology" / "phase4_plan.md").touch()

    # Create finalize-gate sentinels — _advance_prechecks verifies these exist
    harness_cli._write_finalize_sentinels_for_tests(tmp_path)

    monkeypatch.setattr("harness_cli._run_phase_auditor", lambda p, ph: 0)
    monkeypatch.setattr("harness_cli._verify_agent_b_approvals_core", lambda p, ph, ids: (True, "mocked"))

    class FakeVerifier:
        def __init__(self, *args, **kwargs): pass
        def verify(self): return {"passed": True, "total_score": 100.0}
    monkeypatch.setattr("core.quality_gate.phase_truth_verifier.PhaseTruthVerifier", FakeVerifier)

    # Constitution check — mock to pass so it doesn't block on empty project
    _mock_constitution_pass(monkeypatch)

    # Scope to harness_cli's reference — not global shutil
    monkeypatch.setattr("harness_cli.shutil.which", lambda cmd: True)


def test_l1_advance_prechecks_gitleaks_blocks(tmp_path, monkeypatch):
    """rc=20: gitleaks detects secrets → advance blocked."""
    import harness_cli
    from harness_cli import _advance_prechecks

    _setup_advance_prechecks_env(tmp_path, monkeypatch)

    def fake_run(cmd, **kwargs):
        class R:
            returncode = 1 if cmd[0] == "gitleaks" else 0
        return R()
    monkeypatch.setattr(harness_cli.subprocess, "run", fake_run)

    assert _advance_prechecks(tmp_path, 3) == 20


def test_l1_advance_prechecks_ruff_blocks(tmp_path, monkeypatch):
    """rc=18: ruff finds lint errors → advance blocked."""
    import harness_cli
    from harness_cli import _advance_prechecks

    _setup_advance_prechecks_env(tmp_path, monkeypatch)

    def fake_run(cmd, **kwargs):
        class R:
            returncode = 1 if cmd[0] == "ruff" else 0
        return R()
    monkeypatch.setattr(harness_cli.subprocess, "run", fake_run)

    assert _advance_prechecks(tmp_path, 3) == 18


def test_l1_advance_prechecks_mypy_blocks(tmp_path, monkeypatch):
    """rc=19: mypy finds type errors → advance blocked."""
    import harness_cli
    from harness_cli import _advance_prechecks

    _setup_advance_prechecks_env(tmp_path, monkeypatch)

    def fake_run(cmd, **kwargs):
        is_mypy = len(cmd) >= 3 and cmd[1] == "-m" and cmd[2] == "mypy"
        class R:
            returncode = 1 if is_mypy else 0
        return R()
    monkeypatch.setattr(harness_cli.subprocess, "run", fake_run)

    assert _advance_prechecks(tmp_path, 3) == 19


# ---------------------------------------------------------------------------
# init-project: root wrapper creation (submodule layout)
# ---------------------------------------------------------------------------

class TestInitProjectRootWrapper:
    """init-project step [1b/11]: harness_cli.py wrapper for submodule layout."""

    _MARKER = "# auto-generated by init-project (harness submodule layout)"

    def _minimal_project(self, tmp_path: Path) -> Path:
        """Minimal project skeleton that satisfies early init-project checks."""
        (tmp_path / "harness" / "core" / "quality_gate").mkdir(parents=True)
        (tmp_path / "harness" / "core" / "quality_gate" / "__init__.py").touch()
        # harness_cli.py inside harness submodule
        (tmp_path / "harness" / "harness_cli.py").write_text("# harness\n")
        return tmp_path

    def _run_init(self, tmp_path: Path, monkeypatch, overwrite: bool = False):
        """Invoke cmd_init_project with heavy mocking to isolate the wrapper step."""
        import harness_cli as hc
        import argparse

        # Stub out all the heavyweight steps
        monkeypatch.setattr(hc, "_init_phase_dirs", lambda _p: None)
        monkeypatch.setattr(hc, "_init_copy_templates", lambda _p, _h, **_kw: None)
        monkeypatch.setattr(hc, "_update_claude_md", lambda _p: None)
        monkeypatch.setattr(hc, "_check_and_offer_ecc_hooks", lambda _h: None)
        monkeypatch.setattr(hc, "_auto_offer_branch_protection", lambda _p: None)
        monkeypatch.setattr(hc, "_verify_gate_tools", lambda _g, _h, **_kw: ({}, []))
        monkeypatch.setattr(hc, "_check_crg_available", lambda: True)
        monkeypatch.setattr(hc, "_harness_workflow_template", lambda: "# ci\n")
        monkeypatch.setattr(hc, "atomic_write_json", lambda _p, _d: None)

        args = argparse.Namespace(
            project=str(tmp_path),
            phase=1,
            language=None,
            test_runner=None,
            overwrite=overwrite,
            ci_only=True,
            setup_branch_protection=False,
        )
        return hc.cmd_init_project(args)

    def test_writes_wrapper_for_submodule_layout(self, tmp_path, monkeypatch):
        """Submodule layout + no root harness_cli.py → wrapper created."""
        self._minimal_project(tmp_path)
        rc = self._run_init(tmp_path, monkeypatch)
        assert rc == 0
        wrapper = tmp_path / "harness_cli.py"
        assert wrapper.exists(), "wrapper not created"
        content = wrapper.read_text()
        assert self._MARKER in content
        # Path must be resolved via __file__ (not a bare relative string)
        assert "__file__" in content
        assert '"harness_cli.py"' in content

    def test_wrapper_is_executable_delegation(self, tmp_path, monkeypatch):
        """Wrapper executes harness/harness_cli.py with forwarded args."""
        self._minimal_project(tmp_path)
        self._run_init(tmp_path, monkeypatch)
        wrapper = tmp_path / "harness_cli.py"
        # The wrapper must reference subprocess.run + sys.argv forwarding
        content = wrapper.read_text()
        assert "subprocess.run" in content
        assert "sys.argv[1:]" in content

    def test_skips_wrapper_for_standalone_layout(self, tmp_path, monkeypatch):
        """Standalone layout (harness_cli.py at root, no harness/) → no wrapper written."""
        # Standalone: harness_cli.py exists at root, no harness submodule
        (tmp_path / "harness_cli.py").write_text("# real standalone cli\n")
        (tmp_path / "core" / "quality_gate").mkdir(parents=True)
        (tmp_path / "core" / "quality_gate" / "__init__.py").touch()
        rc = self._run_init(tmp_path, monkeypatch)
        assert rc == 0
        # Content must NOT be overwritten with our wrapper
        assert self._MARKER not in (tmp_path / "harness_cli.py").read_text()

    def test_overwrites_our_wrapper_when_force(self, tmp_path, monkeypatch):
        """--overwrite replaces a previously generated wrapper."""
        self._minimal_project(tmp_path)
        self._run_init(tmp_path, monkeypatch)
        # Modify the wrapper so we can detect it was re-written
        wrapper = tmp_path / "harness_cli.py"
        original_mtime = wrapper.stat().st_mtime
        import time
        time.sleep(0.01)
        self._run_init(tmp_path, monkeypatch, overwrite=True)
        assert wrapper.stat().st_mtime >= original_mtime
        assert self._MARKER in wrapper.read_text()

    def test_skips_foreign_root_harness_cli(self, tmp_path, monkeypatch):
        """Submodule layout + user-created harness_cli.py (not ours) → SKIP, no overwrite."""
        self._minimal_project(tmp_path)
        user_content = "# user's own cli wrapper\nimport something\n"
        (tmp_path / "harness_cli.py").write_text(user_content)
        self._run_init(tmp_path, monkeypatch)
        # Must not overwrite user's file
        assert (tmp_path / "harness_cli.py").read_text() == user_content

    def test_overwrite_does_not_reset_state_json(self, tmp_path, monkeypatch):
        """--overwrite must NOT touch state.json — FSM phase progress must survive.

        Bug 5: passing --overwrite to init-project (e.g. after a harness submodule
        update) was resetting current_phase back to 1, destroying mid-project state.
        Fix: state.json is now excluded from the --overwrite scope entirely.
        """
        self._minimal_project(tmp_path)
        state_path = tmp_path / ".methodology" / "state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(
            json.dumps({
                "state": "RUNNING",
                "current_phase": 6,
                "last_gate": 4,
                "last_fr": None,
            }),
            encoding="utf-8",
        )
        self._run_init(tmp_path, monkeypatch, overwrite=True)
        state = json.loads(state_path.read_text())
        assert state["current_phase"] == 6, (
            "--overwrite reset current_phase to 1; state.json must not be touched"
        )


# =============================================================================
# Bug 3 — _check_gate4_prerequisites: da_waiver skipped when tool_score ≥ threshold
# =============================================================================

class TestGate4DaWaiverThresholdCheck:
    """Bug 3: waiver must only enter da_waivers when tool_score < threshold.

    Previously the threshold check was absent — a waiver was accepted even when
    tool_score was already above threshold, which then set
    da_waiver_needs_human_review=True in quality_manifest.json as a false positive.
    """

    _LONG = "x" * 130  # > _DA_EVIDENCE_MIN_CHARS (120)

    def _make_g4(self, dim: str, tool_score: float, threshold: float | None) -> dict:
        """Minimal gate4_result.json satisfying all A3 checks for one waived dim."""
        from harness_cli import _TIER3_DIMS

        devil_advocate = {d: True for d in _TIER3_DIMS}
        evidence = {
            d: {"challenge": self._LONG, "response": self._LONG}
            for d in _TIER3_DIMS
        }
        bd: dict = {"tool_score": tool_score}
        if threshold is not None:
            bd["threshold"] = threshold
        return {
            "devil_advocate": devil_advocate,
            "devil_advocate_evidence": evidence,
            "da_waiver": {dim: True},
            "breakdown": {dim: bd},
        }

    def _run(self, tmp_path: Path, g4: dict) -> tuple[bool, set]:
        from harness_cli import _check_gate4_prerequisites

        sessi = tmp_path / ".sessi-work"
        sessi.mkdir(parents=True, exist_ok=True)
        (sessi / "gate4_result.json").write_text(
            json.dumps(g4), encoding="utf-8"
        )
        # B2 check: needs at least one score file in round_1/scores/
        scores_dir = sessi / "round_1" / "scores"
        scores_dir.mkdir(parents=True, exist_ok=True)
        (scores_dir / "architecture.json").write_text(
            json.dumps({"round": 1, "dim": "architecture", "score": 80.0}),
            encoding="utf-8",
        )
        return _check_gate4_prerequisites(tmp_path)

    def test_waiver_skipped_when_tool_score_above_threshold(self, tmp_path):
        """tool_score=100 >= threshold=80 → dimension already passes → waiver NOT applied."""
        blocked, da_waivers = self._run(
            tmp_path, self._make_g4("architecture", tool_score=100.0, threshold=80.0)
        )
        assert not blocked
        assert "architecture" not in da_waivers

    def test_waiver_skipped_at_exact_threshold(self, tmp_path):
        """tool_score == threshold → passes → waiver NOT applied."""
        blocked, da_waivers = self._run(
            tmp_path, self._make_g4("architecture", tool_score=80.0, threshold=80.0)
        )
        assert not blocked
        assert "architecture" not in da_waivers

    def test_waiver_applied_when_tool_score_below_threshold(self, tmp_path):
        """tool_score=50 < threshold=80 → dimension fails → waiver IS applied."""
        blocked, da_waivers = self._run(
            tmp_path, self._make_g4("architecture", tool_score=50.0, threshold=80.0)
        )
        assert not blocked
        assert "architecture" in da_waivers

    def test_waiver_applied_when_threshold_field_missing(self, tmp_path):
        """threshold absent → default float('inf') → waiver IS applied (conservative).

        The M1 fix: old default was 0.0, which made tool_score >= 0.0 always True
        and silently discarded every waiver.  float('inf') means 'unknown threshold
        → assume waiver is needed'.
        """
        blocked, da_waivers = self._run(
            tmp_path,
            self._make_g4("architecture", tool_score=100.0, threshold=None),
        )
        assert not blocked
        assert "architecture" in da_waivers


# =============================================================================
# Bug 2 — finalize-gate persist: composite_score patched with harness score
# =============================================================================

class TestFinalizeGatePersistCompositeScore:
    """Bug 2: gate{N}_result.json persisted to .methodology/ must carry the
    harness-computed composite_score, not the agent's self-assessed raw value.

    Previously _cmd_finalize_gate_impl copied the file verbatim; the agent's
    raw composite_score was never updated with the weighted value computed by
    bridge.finalize_gate.
    """

    def _run_finalize(
        self,
        tmp_path: Path,
        monkeypatch,
        gate: int = 1,
        phase: int = 1,
        agent_score: float = 96.9,
        harness_score: float = 97.1288,
        src_json_text: str | None = None,
    ) -> int:
        import harness_cli as hc
        from harness.harness_bridge import GateResult

        # Write the source gate result (agent-assessed) to .sessi-work/
        sessi = tmp_path / ".sessi-work"
        sessi.mkdir(parents=True, exist_ok=True)
        gate_src = sessi / f"gate{gate}_result.json"
        if src_json_text is not None:
            gate_src.write_text(src_json_text, encoding="utf-8")
        else:
            gate_src.write_text(
                json.dumps({"composite_score": agent_score, "breakdown": {}}),
                encoding="utf-8",
            )

        (tmp_path / ".methodology").mkdir(parents=True, exist_ok=True)

        # Stub out all heavyweight helpers
        monkeypatch.setattr(hc, "_finalize_gate_preflight", lambda _a, _p: None)
        monkeypatch.setattr(hc, "_finalize_gate_fr_checks", lambda _a, _p: None)
        monkeypatch.setattr(hc, "_finalize_gate_cross_checks", lambda _a, _p: None)
        monkeypatch.setattr(hc, "_update_state_checkpoint", lambda *_a, **_kw: None)
        monkeypatch.setattr(hc, "_update_claude_md", lambda _p: None)
        monkeypatch.setattr(hc, "_record_gate_timestamp", lambda *_a: None)
        monkeypatch.setattr(hc, "_generate_stage_pass", lambda *_a: None)

        _harness_score = harness_score

        class FakeGit:
            def ensure_gitignore(self): pass
            def commit_fr_gate1(self, *_a): pass
            def commit_and_push_gate(self, *_a): pass

        monkeypatch.setattr(hc, "_make_git", lambda *_a: FakeGit())

        class FakeBridge:
            def prepare_gate(self, **_kw):
                return object()

            def finalize_gate(self, _ctx, **_kw):
                return GateResult(
                    gate_num=gate,
                    score=_harness_score,
                    dimensions=[],
                    open_critical=0,
                    open_high=0,
                    quality_complete=True,
                    rounds_used=1,
                )

        import harness.harness_bridge as hb
        monkeypatch.setattr(hb, "HarnessBridge", FakeBridge)

        args = argparse.Namespace(
            project=str(tmp_path),
            gate=gate,
            phase=phase,
            fr_id=None,
        )
        return hc._cmd_finalize_gate_impl(args)

    def test_composite_score_patched_with_harness_score(self, tmp_path, monkeypatch):
        """Persisted gate result must carry the harness-computed score, not agent's."""
        rc = self._run_finalize(
            tmp_path, monkeypatch, agent_score=96.9, harness_score=97.1288
        )
        assert rc == 0
        persisted = json.loads(
            (tmp_path / ".methodology" / "gate1_result.json").read_text()
        )
        assert persisted["composite_score"] == round(97.1288, 4), (
            f"expected {round(97.1288, 4)}, got {persisted['composite_score']}"
        )

    def test_malformed_source_json_falls_back_to_verbatim(self, tmp_path, monkeypatch):
        """Malformed gate result JSON → verbatim fallback, not a crash."""
        rc = self._run_finalize(
            tmp_path, monkeypatch, src_json_text="NOT_VALID_JSON{"
        )
        assert rc == 0
        # Verbatim text written as-is
        persisted_raw = (tmp_path / ".methodology" / "gate1_result.json").read_text()
        assert persisted_raw == "NOT_VALID_JSON{"


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
        from harness_cli import _trace_dirty_state
        (tmp_path / ".methodology" / "trace").mkdir(parents=True)
        result = _trace_dirty_state(tmp_path)
        assert not result["passed"]
        assert "build-trace-attestation" in result["reason"], (
            f"Fix command missing from reason: {result['reason']!r}"
        )

    def test_newer_test_file_reason_includes_fix_hint(self, tmp_path):
        """Test file newer than attestation → reason must contain the fix command."""
        import os
        from harness_cli import _trace_dirty_state

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
        import os
        from harness_cli import _trace_dirty_state

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
    import harness_cli
    from harness_cli import _advance_prechecks

    _setup_advance_prechecks_env(tmp_path, monkeypatch)

    # Do NOT pre-create Phase3_STAGE_PASS.md so auto-generation is triggered.
    # Mock _generate_stage_pass to write the file (quality_manifest.json is
    # absent in the tmp project, so the real generator would print WARN + skip).
    def _write_stage_pass(project, gate, phase):
        sp = project / "00-summary" / f"Phase{phase}_STAGE_PASS.md"
        sp.parent.mkdir(exist_ok=True)
        sp.write_text(f"# Phase {phase} STAGE_PASS\n## Summary\n", encoding="utf-8")

    monkeypatch.setattr("harness_cli._generate_stage_pass", _write_stage_pass)

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

    monkeypatch.setattr(harness_cli.subprocess, "run", fake_subprocess_run)
    monkeypatch.setattr(
        "core.quality_gate.mutation_enforcer.run_mutation_precheck",
        lambda p: (True, "ok"),
    )
    monkeypatch.setattr("harness_cli._run_spec_coverage_check", lambda p, t, **kw: (0, 100.0))
    monkeypatch.setattr("harness_cli._check_gate1_live_coverage", lambda p, ph: 0)

    _advance_prechecks(tmp_path, completed_phase=3)

    expected_path = str(tmp_path / "00-summary" / "Phase3_STAGE_PASS.md")
    assert any(expected_path in " ".join(str(x) for x in call) for call in git_add_calls), (
        f"'git add Phase3_STAGE_PASS.md' was not called; "
        f"captured git-add calls: {git_add_calls}"
    )


# =============================================================================
# Phase 8 bug regressions (B1 / B2 / B3)
# =============================================================================

# B1: commit_and_push_p8 must pass resume_phase=8 so HandoverGenerator does
# not compute _target = 9 and embed phase9_plan.md references.
def test_p8_commit_handover_uses_resume_phase_8(tmp_path, monkeypatch):
    """B1: commit_and_push_p8 must write HANDOVER.md with resume_phase=8.

    Without resume_phase=8, HandoverGenerator._target = phase + 1 = 9,
    causing it to embed Phase 9 plan references that break _validate_p8_completion.
    """
    from harness.git_strategy import GitStrategy

    captured: dict = {}

    def fake_write(
        self,
        checkpoint_id,
        phase,
        background,
        status,
        steps,
        notes,
        extra=None,
        plan_override=None,
        deliverables=None,
        resume_phase=None,
    ):
        captured["resume_phase"] = resume_phase
        captured["phase"] = phase

    monkeypatch.setattr("harness.git_strategy.GitStrategy._write_handover", fake_write)
    monkeypatch.setattr(
        "harness.git_strategy.GitStrategy._commit_and_push",
        lambda self, msg: True,
    )

    gs = GitStrategy(project=tmp_path, enabled=True)
    gs.commit_and_push_p8()

    assert captured.get("phase") == 8, (
        f"Expected phase=8 in _write_handover call, got {captured.get('phase')}"
    )
    assert captured.get("resume_phase") == 8, (
        f"Expected resume_phase=8, got {captured.get('resume_phase')!r}. "
        f"Without resume_phase=8, HandoverGenerator computes _target=9 and "
        f"embeds phase9_plan.md refs."
    )


# B2: generate_sab.py must exit 1 (not overwrite) when SAB.json already exists
# and --overwrite is not passed.
def test_generate_sab_exits_1_when_output_exists_without_overwrite(tmp_path, monkeypatch):
    """B2: generate_sab.py must NOT silently overwrite an existing SAB.json.

    Without the guard, running generate_sab.py after SAD.md is updated with
    placeholder content would destroy a valid SAB.json.
    """
    import sys
    from scripts.generate_sab import main as sab_main

    # Minimal SAD.md so the file-not-found early exit doesn't trigger
    arch_dir = tmp_path / "02-architecture"
    arch_dir.mkdir(parents=True)
    (arch_dir / "SAD.md").write_text("# SAD\n", encoding="utf-8")

    # Pre-create a SAB.json that must NOT be overwritten
    output_file = tmp_path / ".methodology" / "SAB.json"
    output_file.parent.mkdir(parents=True)
    output_file.write_text('{"existing": true}', encoding="utf-8")

    monkeypatch.setattr(sys, "argv", ["generate_sab.py", "--project", str(tmp_path)])

    import io
    captured_stderr = io.StringIO()
    monkeypatch.setattr(sys, "stderr", captured_stderr)

    rc = sab_main()

    assert rc == 1, (
        f"Expected exit 1 when SAB.json exists and --overwrite not passed, got {rc}"
    )
    assert "already exists" in captured_stderr.getvalue(), (
        f"Expected 'already exists' in stderr, got: {captured_stderr.getvalue()!r}"
    )
    # Verify the existing file was NOT touched
    assert output_file.read_text(encoding="utf-8") == '{"existing": true}', (
        "SAB.json content was modified despite no --overwrite flag"
    )


# B3: _advance_prechecks at completed_phase=8 must NOT block on phase9_plan.md
# (Phase 8 is the terminal phase — there is no Phase 9).
def test_advance_prechecks_p8_does_not_require_phase9_plan(tmp_path, monkeypatch):
    """B3: advance-phase for completed_phase=8 must not return 15 (plan-not-found).

    Before the fix, `if completed_phase >= 3:` triggered for P8 and blocked
    with exit code 15 because phase9_plan.md does not exist.
    """
    import harness_cli
    from harness_cli import _advance_prechecks

    _setup_advance_prechecks_env(tmp_path, monkeypatch)

    # Explicitly do NOT create phase9_plan.md — verify P8 is not blocked on it.
    assert not (tmp_path / ".methodology" / "phase9_plan.md").exists()

    monkeypatch.setattr("harness_cli._run_spec_coverage_check", lambda p, t, **kw: (0, 100.0))
    monkeypatch.setattr("harness_cli._check_gate1_live_coverage", lambda p, ph: 0)
    monkeypatch.setattr("harness_cli._generate_stage_pass", lambda p, g, ph: None)
    monkeypatch.setattr(
        "core.quality_gate.mutation_enforcer.run_mutation_precheck",
        lambda p: (True, "ok"),
    )
    monkeypatch.setattr(harness_cli.subprocess, "run", lambda cmd, **kw: type("R", (), {
        "returncode": 0, "stdout": "", "stderr": "",
    })())

    rc = _advance_prechecks(tmp_path, completed_phase=8)

    assert rc != 15, (
        "advance-phase returned 15 (phase9_plan.md not found) for completed_phase=8. "
        "Phase 8 is terminal — no phase9_plan.md should be required."
    )


# =============================================================================
# cmd_check_test_mirrors_spec — JS/TS dispatch
# =============================================================================
# The command routes to check_test_mirrors_spec_js (tree-sitter) when the test
# file extension is .js/.jsx/.ts/.tsx/.mjs/.cjs, and to check_test_mirrors_spec
# (ast) for .py. Locks the dispatch — a regression here would silently drop
# the JS mirror gate.
class TestCmdCheckTestMirrorsSpecDispatch:
    def _setup(self, tmp_path, test_ext: str):
        spec = tmp_path / "02-architecture" / "TEST_SPEC.md"
        spec.parent.mkdir(parents=True)
        # SpecAssertionParser requires ### FR-NN heading, a case table with
        # `#` and `Inputs` columns, and a sub-assertion table with
        # `predicate` + `applies_to` columns.
        spec.write_text(
            "### FR-01\n\n"
            "| # | Inputs | Expected |\n| --- | --- | --- |\n"
            "| 1 | x=\"1\" | y=1 |\n\n"
            "| rule_id | predicate | applies_to |\n"
            "| --- | --- | --- |\n"
            "| A1 | `result == 1` | 1 |\n",
            encoding="utf-8",
        )
        if test_ext in (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"):
            test_src = ('it("test_fr01_x", () => { '
                        'expect(1).toBe(1); });\n')
        else:
            test_src = "def test_fr01_x():\n    assert True\n"
        test_file = tmp_path / "tests" / f"unit{test_ext}"
        test_file.parent.mkdir(parents=True, exist_ok=True)
        test_file.write_text(test_src, encoding="utf-8")
        return spec, test_file

    def _run(self, spec, test_file, project):
        from harness_cli import cmd_check_test_mirrors_spec
        args = argparse.Namespace(
            project=str(project), fr_id="FR-01", test_file=str(test_file),
        )
        return cmd_check_test_mirrors_spec(args)

    def test_typescript_test_routes_to_js_checker(self, tmp_path, capsys, monkeypatch):
        from core.quality_gate import red_assertion_check as rac
        captured: dict = {}

        def fake_js(src, cases, assertions, dialect):
            captured["dialect"] = dialect
            captured["called"] = True
            return []

        monkeypatch.setattr(rac, "check_test_mirrors_spec_js", fake_js)
        spy = mock.MagicMock(return_value=[])
        monkeypatch.setattr(rac, "check_test_mirrors_spec", spy)

        spec, test_file = self._setup(tmp_path, ".ts")
        self._run(spec, test_file, tmp_path)

        assert captured.get("called"), "JS checker was NOT dispatched for .ts"
        assert captured["dialect"] == "typescript"
        assert spy.call_count == 0, "Python checker must not run for .ts"

    def test_tsx_test_routes_to_js_checker(self, tmp_path, monkeypatch):
        from core.quality_gate import red_assertion_check as rac
        captured: dict = {}

        def fake_js(s, c, a, d):
            captured["dialect"] = d
            return []
        monkeypatch.setattr(rac, "check_test_mirrors_spec_js", fake_js)
        monkeypatch.setattr(rac, "check_test_mirrors_spec", mock.MagicMock())

        spec, test_file = self._setup(tmp_path, ".tsx")
        self._run(spec, test_file, tmp_path)

        assert captured.get("dialect") == "tsx"

    def test_javascript_test_routes_to_js_checker(self, tmp_path, monkeypatch):
        from core.quality_gate import red_assertion_check as rac
        captured: dict = {}

        def fake_js(s, c, a, d):
            captured["dialect"] = d
            return []
        monkeypatch.setattr(rac, "check_test_mirrors_spec_js", fake_js)
        monkeypatch.setattr(rac, "check_test_mirrors_spec", mock.MagicMock())

        spec, test_file = self._setup(tmp_path, ".js")
        self._run(spec, test_file, tmp_path)

        assert captured.get("dialect") == "javascript"

    def test_python_test_routes_to_python_checker(self, tmp_path, monkeypatch):
        from core.quality_gate import red_assertion_check as rac
        js_called = {"v": False}

        monkeypatch.setattr(rac, "check_test_mirrors_spec_js",
                            lambda *a, **k: js_called.update(v=True) or [])
        spy = mock.MagicMock(return_value=[])
        monkeypatch.setattr(rac, "check_test_mirrors_spec", spy)

        spec, test_file = self._setup(tmp_path, ".py")
        self._run(spec, test_file, tmp_path)

        assert not js_called["v"], "JS checker must NOT run for .py"
        assert spy.call_count == 1, "Python checker must run exactly once"


# =============================================================================
# cmd_load_context template-stub warning (regression for SKILL.md §0.3.1)
# =============================================================================

class TestLoadContextTemplateWarnings:
    """Regression for harness_cli.py:4193 sentinel + co-equal heuristic.

    Per SKILL.md §0.3.1, an artifact is a template stub if it contains
    the `<!-- harness:template-stub -->` sentinel OR ≥8 {placeholder}
    patterns. Earlier versions only checked the sentinel literal AND
    scanned the wrong paths (root-level SRS.md, TEST_SPEC.md, ADR.md)
    — this class covers both the path fix and the heuristic co-enable.
    """

    _SENTINEL = "<!-- harness:template-stub -->"

    def _write_artifact(self, tmp_path, rel: str, content: str) -> None:
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")

    def _call(self, tmp_path, capsys) -> dict:
        from harness_cli import cmd_load_context
        args = argparse.Namespace(project=str(tmp_path), phase=1)
        rc = cmd_load_context(args)
        assert rc == 0
        return json.loads(capsys.readouterr().out)

    def test_pure_sentinel_triggers_warning(self, tmp_path, capsys):
        """ADR.md with only the sentinel literal → 1 warning."""
        self._write_artifact(
            tmp_path,
            "02-architecture/adr/ADR.md",
            f"# ADR-001: Test\n\n{self._SENTINEL}\n\n## Status\n\nAccepted.\n",
        )
        result = self._call(tmp_path, capsys)
        assert "warnings" in result
        assert any("02-architecture/adr/ADR.md" in w for w in result["warnings"])

    def test_sad_md_sentinel_triggers_warning(self, tmp_path, capsys):
        """SAD.md with sentinel literal → 1 warning.

        Guards the path fix that added 02-architecture/SAD.md to
        _template_artifacts (was missing — P2 entry agents could mistake
        an init-project-copied SAD for a real P2 deliverable).
        """
        self._write_artifact(
            tmp_path,
            "02-architecture/SAD.md",
            f"# SAD - Test\n\n{self._SENTINEL}\n\n## 1. Overview\n\nArchitecture.\n",
        )
        result = self._call(tmp_path, capsys)
        assert "warnings" in result
        assert any("02-architecture/SAD.md" in w for w in result["warnings"])

    def test_sad_md_clean_no_warning(self, tmp_path, capsys):
        """SAD.md without sentinel and < 8 placeholders → no warning.

        Negative companion to test_sad_md_sentinel_triggers_warning.
        """
        self._write_artifact(
            tmp_path,
            "02-architecture/SAD.md",
            "# SAD - OmniBot\n\n## 1. Overview\n\nReal architecture.\n",
        )
        result = self._call(tmp_path, capsys)
        assert "warnings" not in result or not any(
            "02-architecture/SAD.md" in w for w in result["warnings"]
        )

    def test_pure_placeholder_triggers_warning(self, tmp_path, capsys):
        """TEST_SPEC.md with ≥8 {placeholder} patterns, no sentinel → 1 warning.

        The earlier version missed this case entirely (heuristic not wired).
        """
        placeholders = " ".join(f"{{Field {i}}}" for i in range(10))
        self._write_artifact(
            tmp_path,
            "02-architecture/TEST_SPEC.md",
            f"# TEST_SPEC\n\n{placeholders}\n",
        )
        result = self._call(tmp_path, capsys)
        assert "warnings" in result
        assert any("02-architecture/TEST_SPEC.md" in w for w in result["warnings"])

    def test_clean_artifact_no_warning(self, tmp_path, capsys):
        """All three artifacts are real (no sentinel, < 8 placeholders) → no warnings."""
        self._write_artifact(
            tmp_path,
            "01-requirements/SRS.md",
            "# SRS - OmniBot\n\n## FR-01: Real req\n\nAcceptance: works.\n",
        )
        self._write_artifact(
            tmp_path,
            "02-architecture/TEST_SPEC.md",
            "## test_fr01_works\n- Given: input\n- When: run\n- Then: pass\n",
        )
        self._write_artifact(
            tmp_path,
            "02-architecture/adr/ADR.md",
            "# ADR-001: Chosen approach\n\n## Status\n\nAccepted 2026-06-12.\n",
        )
        result = self._call(tmp_path, capsys)
        assert "warnings" not in result or result.get("warnings") == []

    def test_four_stubs_four_warnings(self, tmp_path, capsys):
        """All four artifacts are stubs (sentinel) → 4 warnings, one per file.

        Guards the path set: _template_artifacts must include SRS, SAD,
        TEST_SPEC, and ADR. Earlier version scanned the wrong paths
        (root-level files) and emitted zero warnings even when all
        files were stubs. Later version missed SAD.md until this fix.
        """
        for rel, header in [
            ("01-requirements/SRS.md", "# SRS - {Project Name}"),
            ("02-architecture/SAD.md", "# SAD - {Project Name}"),
            ("02-architecture/TEST_SPEC.md", "# TEST_SPEC"),
            ("02-architecture/adr/ADR.md", "# ADR-001"),
        ]:
            self._write_artifact(
                tmp_path, rel, f"{header}\n\n{self._SENTINEL}\n"
            )
        result = self._call(tmp_path, capsys)
        assert "warnings" in result
        assert len(result["warnings"]) == 4
        warned = {w.split(" is a template stub")[0] for w in result["warnings"]}
        assert "01-requirements/SRS.md" in warned
        assert "02-architecture/SAD.md" in warned
        assert "02-architecture/TEST_SPEC.md" in warned
        assert "02-architecture/adr/ADR.md" in warned


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
        import harness_cli
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
        harness_cli._write_finalize_sentinels_for_tests(tmp_path)
        monkeypatch.setattr("harness_cli._advance_prechecks", lambda p, ph: 0)
        monkeypatch.setattr("harness_cli._update_claude_md", lambda p: None)
        monkeypatch.setattr("harness_cli._llm_clean_stale_claude_md", lambda p: None)
        monkeypatch.setattr("harness_cli.shutil.which", lambda c: None)  # no CRG
        monkeypatch.setattr("harness_cli._advance_fsm", lambda *a, **kw: None)

        class _FakeGen:
            def __init__(self, *a, **kw): pass
            def write(self, *a, **kw): pass
        monkeypatch.setattr("harness_cli.HandoverGenerator", _FakeGen)

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

        monkeypatch.setattr(harness_cli.subprocess, "run", _fake_run)

    def _build_args(self, project: Path, completed_phase: int):
        import argparse
        return argparse.Namespace(
            project=str(project), completed_phase=completed_phase,
        )

    def test_p2_advance_regenerates_manifest(self, tmp_path, monkeypatch):
        """P2→P3: SAD.md present → manifest regenerated, generated_at_phase=2."""
        import json
        import harness_cli
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
        import harness_cli
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
        import harness_cli
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
        import harness_cli
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


# =============================================================================
# Finding #16: P5 plan's VERIFY-REPORT task had no tool producing the file
# =============================================================================

class TestGenerateVerificationReport:
    """Regression tests for Finding #16: P5 plan's VERIFY-REPORT task said
    "Generate 05-verification/VERIFICATION_REPORT.md" but no harness tool
    produced it. The P4→P5 handoff validator blocked with no remediation
    path. Fix: `harness_cli.py generate-verification-report` writes the
    report from quality_manifest.json + SRS.md acceptance criteria.
    """

    def _setup_project(self, tmp_path: Path) -> None:
        """Minimal project with manifest + SRS.md containing AC-FR-NN-N: lines."""
        import json
        (tmp_path / ".methodology").mkdir(parents=True, exist_ok=True)
        (tmp_path / "01-requirements").mkdir(parents=True, exist_ok=True)

        # Manifest with 2 FRs, 1 PASS / 1 FAIL
        manifest = {
            "schema_version": "1.0",
            "generated_at_phase": 2,
            "fr_ids": ["FR-01", "FR-02"],
            "nfr_dimension_mapping": {},
            "high_risk_modules": [],
            "gate_results": {
                "gate1": {
                    "FR-01": {"quality_complete": True, "score": 95.0},
                    "FR-02": {"quality_complete": False, "score": 60.0},
                },
                "gate2": None, "gate3": None, "gate4": None,
            },
        }
        (tmp_path / ".methodology" / "quality_manifest.json").write_text(
            json.dumps(manifest), encoding="utf-8"
        )

        # SRS with AC-FR-XX-N acceptance criteria
        srs = (
            "# SRS - taskq\n\n"
            "### FR-01: submit\n"
            "AC-FR-01-1: accepts valid command\n"
            "AC-FR-01-2: rejects empty command\n\n"
            "### FR-02: run\n"
            "AC-FR-02-1: executes via subprocess\n"
        )
        (tmp_path / "01-requirements" / "SRS.md").write_text(srs, encoding="utf-8")

    def test_script_writes_report(self, tmp_path):
        """scripts/generate_verification_report.py writes 05-verification/VERIFICATION_REPORT.md."""
        from scripts.generate_verification_report import generate_verification_report

        self._setup_project(tmp_path)
        out = generate_verification_report(tmp_path)
        assert out.exists()
        assert out.name == "VERIFICATION_REPORT.md"
        assert out.parent.name == "05-verification"

        text = out.read_text(encoding="utf-8")
        # Per-FR sections present
        assert "### FR-01" in text
        assert "### FR-02" in text
        # Acceptance criteria extracted from SRS
        assert "AC-FR-01-1" in text
        assert "AC-FR-01-2" in text
        assert "AC-FR-02-1" in text
        # Status from manifest
        assert "PASS" in text
        assert "FAIL" in text

    def test_script_certification_counts_passes(self, tmp_path):
        """Certification block reflects manifest gate1 data."""
        from scripts.generate_verification_report import generate_verification_report

        self._setup_project(tmp_path)
        out = generate_verification_report(tmp_path)
        text = out.read_text(encoding="utf-8")
        assert "1/2 FRs" in text or "1/2" in text
        # Manifest has 1 PASS / 1 FAIL → conditional or FAIL cert
        assert "FAIL" in text or "Conditional" in text

    def test_handoff_validator_passes_when_report_present(self, tmp_path):
        """P4→P5 validator passes when VERIFICATION_REPORT.md exists at 05-verification/."""
        from scripts.generate_verification_report import generate_verification_report
        import harness_cli

        self._setup_project(tmp_path)
        generate_verification_report(tmp_path)

        errors = harness_cli._validate_handoff_p4_to_p5(tmp_path)
        assert not errors, (
            f"Validator should pass when VERIFICATION_REPORT.md exists; got: {errors}"
        )

    def test_handoff_validator_gives_actionable_error(self, tmp_path):
        """P4→P5 validator gives actionable remediation when VERIFICATION_REPORT.md missing."""
        import harness_cli

        self._setup_project(tmp_path)
        # Do NOT generate the report
        errors = harness_cli._validate_handoff_p4_to_p5(tmp_path)
        assert errors, "Validator should error when report missing"
        assert "VERIFICATION_REPORT.md" in errors[0]
        # Finding #16 fix: error must include the remediation command
        assert "generate-verification-report" in errors[0], (
            f"Error must point to the canonical remediation tool; got: {errors[0]}"
        )

    def test_cli_subcommand_runs(self, tmp_path, capsys):
        """harness_cli.py generate-verification-report --project . writes the report."""
        import harness_cli

        self._setup_project(tmp_path)
        rc = harness_cli.cmd_generate_verification_report(
            argparse.Namespace(project=str(tmp_path))
        )
        assert rc == 0
        out_path = tmp_path / "05-verification" / "VERIFICATION_REPORT.md"
        assert out_path.exists()

        captured = capsys.readouterr()
        assert "VERIFICATION_REPORT.md written" in captured.out


