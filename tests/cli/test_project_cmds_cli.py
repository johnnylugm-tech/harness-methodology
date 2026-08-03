"""Tests for cli/project_cmds.py — init / audit-structure / audit-phase / load-context / read-file (split from tests/test_harness_cli.py, C1b)."""

from __future__ import annotations


import argparse
import json
from pathlib import Path
import io

import harness_cli as _hc_entry  # noqa: F401  entry-first before cli imports


# =============================================================================
# _init_phase_dirs
# =============================================================================

class TestInitPhaseDirs:
    def test_creates_all_directories(self, tmp_path):
        from cli.project_cmds import _init_phase_dirs

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
        from cli.project_cmds import _init_phase_dirs

        _init_phase_dirs(tmp_path)
        _init_phase_dirs(tmp_path)

        captured = capsys.readouterr().out
        assert "SKIP: all 12 directories already exist" in captured


# =============================================================================
# _init_copy_templates
# =============================================================================

class TestInitCopyTemplates:
    def test_copies_templates_to_correct_locations(self, tmp_path):
        from cli.project_cmds import _init_copy_templates
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
        from cli.project_cmds import _init_copy_templates
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

    def test_overwrite_protects_authored_deliverables(self, tmp_path, capsys):
        """Bug: init-project --overwrite clobbered authored SRS.md/SAD.md/... with
        template content (integration-test E2E, 2026-07-02). A deliverable whose
        content differs from its template is authored in-flight state — protected
        even with overwrite=True, mirroring the state.json never-reset rule."""
        from cli.project_cmds import _init_copy_templates
        import harness_cli as hc

        harness_root = Path(hc.__file__).parent
        (tmp_path / "01-requirements").mkdir()
        (tmp_path / "02-architecture").mkdir()
        (tmp_path / "02-architecture" / "adr").mkdir()
        srs = tmp_path / "01-requirements" / "SRS.md"
        srs.write_text("# Authored SRS — FR-01 taskq.models\n")

        _init_copy_templates(tmp_path, harness_root, overwrite=True)

        assert srs.read_text() == "# Authored SRS — FR-01 taskq.models\n"
        out = capsys.readouterr().out
        assert "PROTECTED" in out
        assert "SRS.md" in out

    def test_overwrite_refreshes_pristine_template_copies(self, tmp_path, capsys):
        """A deliverable byte-identical to its template is unauthored — overwrite=True
        may refresh it (no-op content-wise) and must NOT report PROTECTED."""
        from cli.project_cmds import _init_copy_templates
        import harness_cli as hc

        harness_root = Path(hc.__file__).parent
        (tmp_path / "01-requirements").mkdir()
        (tmp_path / "02-architecture").mkdir()
        (tmp_path / "02-architecture" / "adr").mkdir()

        _init_copy_templates(tmp_path, harness_root)
        capsys.readouterr()
        template_srs = (harness_root / "templates" / "SRS.md").read_text(encoding="utf-8")

        _init_copy_templates(tmp_path, harness_root, overwrite=True)

        srs = tmp_path / "01-requirements" / "SRS.md"
        assert srs.read_text(encoding="utf-8") == template_srs
        out = capsys.readouterr().out
        assert "PROTECTED" not in out

    def test_overwrite_never_replaces_existing_claude_md(self, tmp_path):
        """Existing CLAUDE.md is never re-copied wholesale (even with overwrite=True):
        _update_claude_md refreshes the auto block in place; a full re-copy only
        destroys user custom sections below the block."""
        from cli.project_cmds import _init_copy_templates
        import harness_cli as hc

        harness_root = Path(hc.__file__).parent
        (tmp_path / "01-requirements").mkdir()
        (tmp_path / "02-architecture").mkdir()
        (tmp_path / "02-architecture" / "adr").mkdir()
        claude = tmp_path / "CLAUDE.md"
        claude.write_text("# Project: x\n\n## My custom section\nkeep me\n")

        _init_copy_templates(tmp_path, harness_root, overwrite=True)

        assert "keep me" in claude.read_text()

    def test_handles_missing_template_source(self, tmp_path, capsys):
        """When a template file is removed, reports WARNING and continues."""
        from cli.project_cmds import _init_copy_templates

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
        from cli.project_cmds import _init_copy_templates
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
        from cli.project_cmds import _init_copy_templates
        from harness_cli import cmd_check_constitution
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

    def test_partial_project_passes_some_dims(self, tmp_path):
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

        # P4 TEST_PLAN has FR ref → good; TEST_RESULTS.md is exempt from the
        # FR-ref rule (Round 29: its canonical shape uses TC-XX test-case IDs,
        # never FR/TASK/NFR refs — harness's own template would itself fail
        # this check) and is otherwise well-formed → good.
        p4 = cq["details"]["P4"]
        p4_files = {f["path"]: f["quality"] for f in p4["files"]}
        assert p4_files["04-testing/TEST_PLAN.md"] == "good"
        assert p4_files["04-testing/TEST_RESULTS.md"] == "good"

        # P6 QUALITY_REPORT has no FR ref — but phase 6 is excluded from FR check
        p6 = cq["details"]["P6"]
        p6_files = {f["path"]: f["quality"] for f in p6["files"]}
        assert p6_files["06-quality/QUALITY_REPORT.md"] == "good"  # FR ref not required

    # -- Bug 4 regression: ### headers and file-initial # headers --------

    def _audit_json(self, tmp_path):
        """Run cmd_audit_structure with json_out and return parsed result."""
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

    # --- Bug 5 regression: P7 artifact list matches actual workflow output ---
    # Before the fix, audit-structure required 07-risk/RISK_ASSESSMENT.md which
    # is NEVER produced by the Phase 7 workflow — it outputs RISK_REGISTER.md,
    # RISK_MITIGATION_PLANS.md, RISK_STATUS_REPORT.md. The audit therefore
    # failed CI on every main push.

    def test_p7_artifact_list_uses_three_actual_files(self, tmp_path):

        (tmp_path / "07-risk").mkdir()
        (tmp_path / "07-risk" / "RISK_REGISTER.md").write_text(
            "# Risk Register\n\n## Section\n\n" + "x" * 200
        )
        (tmp_path / "07-risk" / "RISK_MITIGATION_PLANS.md").write_text(
            "# Mitigation Plans\n\n## Section\n\n" + "x" * 200
        )
        (tmp_path / "07-risk" / "RISK_STATUS_REPORT.md").write_text(
            "# Risk Status\n\n## Section\n\n" + "x" * 200
        )

        data = self._audit_json(tmp_path)
        p7 = data["dimensions"]["artifact_completeness"]["details"]["P7"]
        assert p7["all_present"] is True, (
            f"P7 artifacts should all exist; got {p7}"
        )
        expected = {
            "07-risk/RISK_REGISTER.md",
            "07-risk/RISK_MITIGATION_PLANS.md",
            "07-risk/RISK_STATUS_REPORT.md",
        }
        actual = {f["path"] for f in p7["files"]}
        assert actual == expected, f"P7 expected {expected}, got {actual}"

    def test_p7_artifact_list_does_not_require_risk_assessment(self, tmp_path):
        """RISK_ASSESSMENT.md must NOT be in the P7 required list."""

        (tmp_path / "07-risk").mkdir()
        # Provide only the 3 actual deliverables — no RISK_ASSESSMENT.md.
        (tmp_path / "07-risk" / "RISK_REGISTER.md").write_text("x" * 300)
        (tmp_path / "07-risk" / "RISK_MITIGATION_PLANS.md").write_text("x" * 300)
        (tmp_path / "07-risk" / "RISK_STATUS_REPORT.md").write_text("x" * 300)

        data = self._audit_json(tmp_path)
        p7_files = {
            f["path"]
            for f in data["dimensions"]["artifact_completeness"]["details"]["P7"]["files"]
        }
        assert "07-risk/RISK_ASSESSMENT.md" not in p7_files, (
            "RISK_ASSESSMENT.md is not produced by Phase 7 workflow; "
            "do not require it as a CI artifact"
        )

    # --- I: P4 FR-reference regex now STRICT CANONICAL ---
    # Previously accepted 4 variants (FR-01, FR01, fr_01, FR(01)). I improvement
    # narrows to canonical form (FR-NN / TASK-NN / NFR-NN, ≥2 digits). Tests
    # below verify:
    #   - canonical forms pass
    #   - non-canonical forms (e.g. test_fr01.py without hyphen) are flagged
    # Use `python3 -m core.canonical_lint <file>` to find/fix non-canonical.

    def test_p4_fr_reference_accepts_canonical(self, tmp_path):
        """Documents with CANONICAL FR-NN / NFR-NN references must pass."""

        (tmp_path / "04-testing").mkdir()
        (tmp_path / "04-testing" / "TEST_PLAN.md").write_text(
            "# Test Plan\n\n## Section\n\n"
            "Coverage for [FR-01], [FR-02], [NFR-03].\n" + "x" * 200
        )
        # Filename references are also canonical via FR- prefix in narrative
        (tmp_path / "04-testing" / "TEST_RESULTS.md").write_text(
            "# Test Results\n\n## Section\n\n"
            "Files for FR-01, FR-02, FR-03, FR-04, FR-05 all pass.\n" + "x" * 200
        )

        data = self._audit_json(tmp_path)
        files = {
            f["path"]: f["quality"]
            for f in data["dimensions"]["content_quality"]["details"]["P4"]["files"]
        }
        assert files["04-testing/TEST_PLAN.md"] == "good"
        assert files["04-testing/TEST_RESULTS.md"] == "good"

    def test_p4_fr_reference_flags_non_canonical(self, tmp_path):
        """Documents with non-canonical (e.g. FR01, fr_01) must be flagged."""

        (tmp_path / "04-testing").mkdir()
        # Non-canonical: FR01 without hyphen
        (tmp_path / "04-testing" / "TEST_PLAN.md").write_text(
            "# Test Plan\n\n## Section\n\n"
            "Coverage for FR01, FR02, NFR03.\n" + "x" * 200
        )

        data = self._audit_json(tmp_path)
        files = {
            f["path"]: f["quality"]
            for f in data["dimensions"]["content_quality"]["details"]["P4"]["files"]
        }
        assert files["04-testing/TEST_PLAN.md"] == "suspicious", (
            "non-canonical FR01 (without hyphen) should be flagged as suspicious"
        )

    def test_p4_fr_reference_still_flags_doc_with_no_reference(self, tmp_path):
        """A P4 doc with zero FR/NFR references must still be flagged — except
        TEST_RESULTS.md, which is exempt from this rule (Round 29: its
        canonical shape uses TC-XX test-case IDs, never FR/TASK/NFR refs)."""

        (tmp_path / "04-testing").mkdir()
        (tmp_path / "04-testing" / "TEST_PLAN.md").write_text(
            "# Test Plan\n\n## Section\n\nAll good.\n" + "x" * 200
        )
        (tmp_path / "04-testing" / "TEST_RESULTS.md").write_text(
            "# Test Results\n\n## Section\n\nAll good.\n" + "x" * 200
        )

        data = self._audit_json(tmp_path)
        files = {
            f["path"]: f["quality"]
            for f in data["dimensions"]["content_quality"]["details"]["P4"]["files"]
        }
        assert files["04-testing/TEST_PLAN.md"] == "suspicious"
        assert files["04-testing/TEST_RESULTS.md"] == "good"

    def test_test_results_still_flags_when_hollow(self, tmp_path):
        """TEST_RESULTS.md's FR-ref exemption must not become a blanket
        immunity — a genuinely thin/hollow file must still be flagged."""

        (tmp_path / "04-testing").mkdir()
        (tmp_path / "04-testing" / "TEST_RESULTS.md").write_text(
            "# Test Results\n\ntoo short.\n"
        )

        data = self._audit_json(tmp_path)
        files = {
            f["path"]: f["quality"]
            for f in data["dimensions"]["content_quality"]["details"]["P4"]["files"]
        }
        assert files["04-testing/TEST_RESULTS.md"] == "suspicious"

    def test_maintenance_log_fresh_cr_table_is_good(self, tmp_path):
        """A freshly-initialized MAINTENANCE_LOG.md (harness's own template
        shape: 1 H1 + an empty CR table header, zero CR rows yet) must not be
        flagged 'suspicious' — CR rows are appended by `cr-close`, not
        markdown sections, so this file legitimately never gains a 2nd
        heading regardless of how many CRs have been processed."""

        (tmp_path / ".methodology").mkdir()
        (tmp_path / ".methodology" / "state.json").write_text(
            json.dumps({"current_phase": 9})
        )
        (tmp_path / "09-maintenance").mkdir()
        (tmp_path / "09-maintenance" / "MAINTENANCE_LOG.md").write_text(
            "# MAINTENANCE_LOG — Phase 9 Change Request Index\n\n"
            "> ASPICE SUP.9 (problem resolution) / SUP.10 (change request management).\n"
            "> Machine state: `.methodology/change_requests/CR-NN.json` — "
            "this file is the human-readable index.\n"
            "> Rows are appended automatically by `harness_cli.py cr-close`; "
            "do not hand-edit closed rows.\n\n"
            "| CR | Type | Title | Status | FRs | Fix commit | Closed |\n"
            "|----|------|-------|--------|-----|------------|--------|\n"
        )

        data = self._audit_json(tmp_path)
        files = {
            f["path"]: f["quality"]
            for f in data["dimensions"]["content_quality"]["details"]["P9"]["files"]
        }
        assert files["09-maintenance/MAINTENANCE_LOG.md"] == "good"

    def test_maintenance_log_still_flags_when_hollow(self, tmp_path):
        """MAINTENANCE_LOG.md's section-count exemption must not become a
        blanket immunity — a genuinely thin/hollow file must still be
        flagged."""

        (tmp_path / ".methodology").mkdir()
        (tmp_path / ".methodology" / "state.json").write_text(
            json.dumps({"current_phase": 9})
        )
        (tmp_path / "09-maintenance").mkdir()
        (tmp_path / "09-maintenance" / "MAINTENANCE_LOG.md").write_text(
            "# MAINTENANCE_LOG\n\ntoo short.\n"
        )

        data = self._audit_json(tmp_path)
        files = {
            f["path"]: f["quality"]
            for f in data["dimensions"]["content_quality"]["details"]["P9"]["files"]
        }
        assert files["09-maintenance/MAINTENANCE_LOG.md"] == "suspicious"

    # --- Bug 7 regression: P7 artifact list single source of truth ---
    # Before the fix, the P7 list was duplicated in 8+ places (drift_detector,
    # framework_enforcer, harness_cli, etc.). Any
    # rename required editing all of them. Tests below verify the centralized
    # phase_artifacts() function and that all consumers agree.

    def test_phase_artifacts_p7_is_canonical_three_files(self):
        """phase_artifacts(7) must return the 3 Phase 7 deliverables."""
        from core.utils.project_layout import phase_artifacts
        assert phase_artifacts(7) == [
            "07-risk/RISK_REGISTER.md",
            "07-risk/RISK_MITIGATION_PLANS.md",
            "07-risk/RISK_STATUS_REPORT.md",
        ]

    def test_phase_artifacts_returns_copy(self):
        """Mutating the returned list must not affect the canonical map."""
        from core.utils.project_layout import phase_artifacts, PHASE_ARTIFACTS
        result = phase_artifacts(7)
        result.append("MUTATED.md")
        assert "MUTATED.md" not in PHASE_ARTIFACTS[7]

    def test_phase_artifacts_unknown_phase_returns_empty(self):
        from core.utils.project_layout import phase_artifacts
        assert phase_artifacts(0) == []
        assert phase_artifacts(99) == []

    def test_audit_structure_p7_matches_centralized_list(self, tmp_path):
        """cmd_audit_structure's P7 entry must equal phase_artifacts(7)."""
        # Set up the 3 P7 deliverables.
        (tmp_path / "07-risk").mkdir()
        for name in ("RISK_REGISTER.md", "RISK_MITIGATION_PLANS.md",
                     "RISK_STATUS_REPORT.md"):
            (tmp_path / "07-risk" / name).write_text("# H1\n\n## Section\n\n" + "x" * 200)
        data = self._audit_json(tmp_path)
        actual = {
            f["path"]
            for f in data["dimensions"]["artifact_completeness"]["details"]["P7"]["files"]
        }
        from core.utils.project_layout import phase_artifacts
        assert actual == set(phase_artifacts(7))


# =============================================================================
# cmd_audit_structure — init → audit round-trip
# =============================================================================

class TestInitThenAudit:
    def test_init_then_audit_reports_dirs_and_artifacts(self, tmp_path):
        """After init-project, audit-structure should pass directory existence."""
        import argparse
        import sys

        from cli.project_cmds import _init_phase_dirs, cmd_audit_structure

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
        # S4: init helpers live in cli/project_cmds — patch that namespace.
        from cli import project_cmds as _projc

        # Stub out all the heavyweight steps
        monkeypatch.setattr(_projc, "_init_phase_dirs", lambda _p: None)
        monkeypatch.setattr(_projc, "_init_copy_templates", lambda _p, _h, **_: None)
        monkeypatch.setattr("core.claude_md.update_claude_md", lambda _p: None)
        monkeypatch.setattr(_projc, "_check_and_offer_ecc_hooks", lambda _h: None)
        monkeypatch.setattr(_projc, "_auto_offer_branch_protection", lambda _p: None)
        # S2: verify_gate_tools moved to harness/tool_checks.py; all callers
        # (incl. cli/project_cmds) resolve it via that module's namespace.
        from harness import tool_checks as _tc
        monkeypatch.setattr(_tc, "verify_gate_tools", lambda _g, _h, **_: ({}, []))
        monkeypatch.setattr(_projc, "_check_crg_available", lambda: True)
        monkeypatch.setattr(_projc, "_harness_workflow_template", lambda: "# ci\n")
        # S1: cmd_init_project (cli/project_cmds) binds atomic_write_json
        # directly from core.atomic_io — patch its namespace, not just hc's.
        monkeypatch.setattr(_projc, "atomic_write_json", lambda _p, _d: None)

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


class TestCmdReadFile:
    def test_read_file_ok(self, tmp_path):
        import harness_cli
        f = tmp_path / "test.txt"
        f.write_text("Hello World", encoding="utf-8")
        out_json = tmp_path / "out.json"
        
        args = argparse.Namespace(
            file=str(f),
            expect_prefix=None,
            min_length=0,
            max_length=None,
            content=True,
            content_out=None,
            json_out=str(out_json),
            quiet=True
        )
        rc = harness_cli.cmd_read_file(args)
        assert rc == 0
        data = json.loads(out_json.read_text(encoding="utf-8"))
        assert data["status"] == "OK"
        assert data["content"] == "Hello World"
        
    def test_read_file_missing(self, tmp_path):
        import harness_cli
        f = tmp_path / "missing.txt"
        
        args = argparse.Namespace(
            file=str(f),
            expect_prefix=None,
            min_length=0,
            max_length=None,
            content=False,
            content_out=None,
            json_out=None,
            quiet=True
        )
        rc = harness_cli.cmd_read_file(args)
        assert rc == 1

    def test_read_file_content_out(self, tmp_path):
        import harness_cli
        f = tmp_path / "test.txt"
        f.write_text("Hello Content", encoding="utf-8")
        out_content = tmp_path / "content.txt"
        
        args = argparse.Namespace(
            file=str(f),
            expect_prefix=None,
            min_length=0,
            max_length=None,
            content=True,
            content_out=str(out_content),
            json_out=None,
            quiet=True
        )
        rc = harness_cli.cmd_read_file(args)
        assert rc == 0
        assert out_content.read_text(encoding="utf-8") == "Hello Content"


# =============================================================================
# cmd_amend_sab (Bug Fix R3: phantom_modules integration)
# =============================================================================

class TestCmdAmendSabPhantom:
    """Bug Fix R3 (2026-07-15): amend-sab must surface phantom modules.

    Previously, `cmd_amend_sab` only handled the FORWARD direction
    (src → SAB). The reverse direction (SAB → src: phantom modules
    registered in SAB but missing from implementation) was not surfaced
    until Phase 4 preflight — too late to amend.

    Fix: every amend-sab invocation now also runs `phantom_modules()`
    and prints a `[amend-sab] PHANTOM:` block listing them. `--strict`
    flag makes it exit non-zero so pipelines can fail-fast.
    """

    def _make_project(self, tmp_path: Path, sab_modules: list[dict], src_files: list[str]):
        """Set up a tmp project with SAB + src tree."""
        methodology = tmp_path / ".methodology"
        methodology.mkdir()
        src_dir = tmp_path / "03-development" / "src"
        src_dir.mkdir(parents=True)
        for f in src_files:
            target = src_dir / f
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("# stub\n", encoding="utf-8")
        sab = {
            "version": "1.0",
            "phase": 2,
            "project": "test",
            "layers": [
                {"name": "engine", "modules": sab_modules, "allowed_dependencies": []},
            ],
            "dependencies": {},
            "fr_module_traceability": {},
        }
        (methodology / "SAB.json").write_text(json.dumps(sab, indent=2), encoding="utf-8")
        return tmp_path

    def test_phantom_block_printed_when_src_missing_module(self, tmp_path, capsys):
        from cli.project_cmds import cmd_amend_sab

        # SAB registers `taskq.breaker` but src only has `taskq.cli`
        self._make_project(
            tmp_path,
            sab_modules=[
                {"name": "taskq.cli", "implemented_in": "src/taskq/cli.py"},
                {"name": "taskq.breaker", "implemented_in": "src/taskq/breaker.py"},
            ],
            src_files=["taskq/cli.py"],
        )
        args = argparse.Namespace(
            project=str(tmp_path), src_dir="03-development/src",
            dry_run=False, strict=False,
        )
        rc = cmd_amend_sab(args)
        # Without --strict: exit 0 (informational), but PHANTOM block printed
        assert rc == 0
        captured = capsys.readouterr()
        assert "PHANTOM" in captured.out
        assert "taskq.breaker" in captured.out

    def test_strict_flag_exits_nonzero_on_phantom(self, tmp_path, capsys):
        from cli.project_cmds import cmd_amend_sab

        self._make_project(
            tmp_path,
            sab_modules=[
                {"name": "taskq.cli", "implemented_in": "src/taskq/cli.py"},
                {"name": "taskq.breaker", "implemented_in": "src/taskq/breaker.py"},
            ],
            src_files=["taskq/cli.py"],
        )
        args = argparse.Namespace(
            project=str(tmp_path), src_dir="03-development/src",
            dry_run=False, strict=True,
        )
        rc = cmd_amend_sab(args)
        assert rc == 1
        captured = capsys.readouterr()
        assert "PHANTOM" in captured.out
        assert "--strict" in captured.err

    def test_no_phantom_block_when_all_modules_implemented(self, tmp_path, capsys):
        from cli.project_cmds import cmd_amend_sab

        self._make_project(
            tmp_path,
            sab_modules=[
                {"name": "taskq.cli", "implemented_in": "src/taskq/cli.py"},
            ],
            src_files=["taskq/cli.py"],
        )
        args = argparse.Namespace(
            project=str(tmp_path), src_dir="03-development/src",
            dry_run=False, strict=False,
        )
        rc = cmd_amend_sab(args)
        assert rc == 0
        captured = capsys.readouterr()
        assert "PHANTOM" not in captured.out

    def test_dry_run_reports_phantom_too(self, tmp_path, capsys):
        """Bug Fix R3: phantom check runs on dry-run too — operators need
        to see drift without writing SAB.json."""
        from cli.project_cmds import cmd_amend_sab

        self._make_project(
            tmp_path,
            sab_modules=[
                {"name": "taskq.cli", "implemented_in": "src/taskq/cli.py"},
                {"name": "taskq.breaker", "implemented_in": "src/taskq/breaker.py"},
            ],
            src_files=["taskq/cli.py"],
        )
        args = argparse.Namespace(
            project=str(tmp_path), src_dir="03-development/src",
            dry_run=True, strict=False,
        )
        rc = cmd_amend_sab(args)
        assert rc == 0
        captured = capsys.readouterr()
        assert "PHANTOM" in captured.out
        assert "taskq.breaker" in captured.out


def test_issue_26_gate_check_hard_deps_and_mutmut_probe():
    """ID: test_issue_26_gate_check_hard_deps_and_mutmut_probe
    Verifies Issue #26 fixes:
    1. harness_quality_gate.yml gate-check job installs 4 required gate hard dependencies
    2. verify_tools.py probes mutmut with --help instead of --version
    """
    from cli.project_cmds import _harness_workflow_template
    from harness.ssi.scripts.verify_tools import EXTENDED_TOOLS

    workflow_content = _harness_workflow_template()
    assert "import-linter==2.5.2" in workflow_content
    assert "scancode-toolkit==32.4.1" in workflow_content
    assert "code-review-graph==2.3.6" in workflow_content
    assert "gitleaks" in workflow_content

    assert EXTENDED_TOOLS["mutmut"][0] == "mutmut --help"

