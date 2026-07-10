"""Tests for cli/check_cmds.py — constitution / agent-B approvals / test-mirrors-spec / verification-report / SAB generation (split from tests/test_harness_cli.py, C1b)."""

from __future__ import annotations


import argparse
import json
from pathlib import Path
from unittest import mock
import io

import harness_cli as _hc_entry  # noqa: F401  entry-first before cli imports
from cli.phase_cmds import _validate_handoff_p4_to_p5  # noqa: E402


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

    def test_file_flag_fail_prints_missing_keywords(self, tmp_path, capsys):
        """FAIL output must enumerate the absent per-dimension keywords (actionable
        gap) — not only the vague 'Fix document gaps' line — so a fixing agent
        knows what to add. Mirrors the advance-postflight idiom."""
        import argparse
        from harness_cli import cmd_check_constitution
        from core.quality_gate.constitution.profile import get_profile

        kws = get_profile().dimension_keywords_for_phase("correctness", 2)
        assert kws and len(kws) >= 2

        adr_dir = tmp_path / "02-architecture" / "adr"
        adr_dir.mkdir(parents=True)
        adr = adr_dir / "ADR.md"
        # Include ONLY the first correctness keyword; the rest are genuinely absent.
        adr.write_text(
            "# ADR-01: decisions\n\n"
            + f"This record references {kws[0]} extensively. "
            + ("Plain prose with no other special terms. " * 20),
            encoding="utf-8",
        )
        args = argparse.Namespace(
            phase=2, project=str(tmp_path),
            file="02-architecture/adr/ADR.md",
        )
        rc = cmd_check_constitution(args)
        out = capsys.readouterr().out.lower()
        assert rc == 1
        assert "[fail]" in out
        assert "missing:" in out
        absent = [k for k in kws if k.lower() not in adr.read_text(encoding="utf-8").lower()]
        assert absent, "test setup must leave at least one keyword absent"
        assert any(k.lower() in out for k in absent)

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

    def test_all_approved_returns_0(self, tmp_path):
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

    def test_missing_docs_embedded_returns_1(self, tmp_path):
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

    def test_approve_empty_reason_returns_1(self, tmp_path):
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

    def test_approve_no_citations_returns_1(self, tmp_path):
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

    def test_no_fr_ids_no_manifest_returns_1(self, tmp_path):
        args = argparse.Namespace(project=str(tmp_path), phase=3, fr_ids="")
        from harness_cli import cmd_verify_agent_b_approvals
        rc = cmd_verify_agent_b_approvals(args)
        assert rc == 1

    def test_reads_fr_ids_from_manifest(self, tmp_path):
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

    def test_p2_uses_phase_deliverables_not_fr_ids(self, tmp_path):
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
            project=str(project), fr_id="FR-01", test_files=[str(test_file)],
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

    def test_handoff_validator_passes_when_test_results_present(self, tmp_path):
        """P4→P5 validator passes when TEST_RESULTS.md exists at 04-testing/."""
        import json

        self._setup_project(tmp_path)
        (tmp_path / "04-testing").mkdir(parents=True, exist_ok=True)
        (tmp_path / "04-testing" / "TEST_RESULTS.md").write_text("A" * 200)

        # Gate 3 pass
        manifest = tmp_path / ".methodology" / "quality_manifest.json"
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text(json.dumps({"gate_results": {"gate3": {"quality_complete": True}}}))

        errors = _validate_handoff_p4_to_p5(tmp_path)
        assert not errors, (
            f"Validator should pass when TEST_RESULTS.md exists; got: {errors}"
        )

    def test_handoff_validator_gives_actionable_error(self, tmp_path):
        """P4→P5 validator gives actionable remediation when TEST_RESULTS.md missing."""

        self._setup_project(tmp_path)
        # Do NOT create TEST_RESULTS.md
        errors = _validate_handoff_p4_to_p5(tmp_path)
        assert errors, "Validator should error when report missing"
        assert "TEST_RESULTS.md" in errors[0]
        assert "Phase 4 orchestrator" in errors[0], (
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




class TestCheckTestMirrorsSpecMultiFile:
    """Bug #26 regression: --test-file accepts nargs='+' so callers can
    pass multiple test files (e.g. test_fr01_inputs.py + test_fr01_edge.py
    for a per-FR split)."""

    def test_argparse_accepts_multiple_test_files(self):
        """Direct argparse check: --test-file must accept nargs='+'."""
        import argparse
        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers()
        ctms = sub.add_parser("check-test-mirrors-spec")
        ctms.add_argument(
            "--test-file", dest="test_files", nargs="+", required=True,
            help="Path(s) to the RED test file(s)",
        )
        args = parser.parse_args(
            ["check-test-mirrors-spec", "--test-file",
             "tests/test_fr01.py", "tests/test_fr01_extra.py"]
        )
        assert args.test_files == ["tests/test_fr01.py", "tests/test_fr01_extra.py"]
        assert len(args.test_files) == 2


    def test_handler_iterates_test_files(self, tmp_path, monkeypatch):
        from core.quality_gate import red_assertion_check as rac
        called_with: list = []

        def fake_python(src, cases, assertions):
            called_with.append(src)
            return []

        monkeypatch.setattr(rac, "check_test_mirrors_spec", fake_python)
        monkeypatch.setattr(rac, "check_test_mirrors_spec_js", fake_python)

        from harness_cli import cmd_check_test_mirrors_spec
        spec = (tmp_path / "02-architecture" / "TEST_SPEC.md")
        spec.parent.mkdir(parents=True)
        # SpecAssertionParser requires ### FR-NN heading, a case table with
        # `Inputs` columns, and a sub-assertion table with `predicate` +
        # `applies_to` columns.
        spec.write_text(
            "### FR-01\n\n"
            "| # | Inputs | Expected |\n| --- | --- | --- |\n"
            "| 1 | x=\"1\" | y=1 |\n\n"
            "| rule_id | predicate | applies_to |\n"
            "| --- | --- | --- |\n"
            "| A1 | `result == 1` | 1 |\n",
            encoding="utf-8",
        )
        (tmp_path / "tests").mkdir()
        f1 = tmp_path / "tests" / "test_fr01_inputs.py"
        f1.write_text("# inputs file\n", encoding="utf-8")
        f2 = tmp_path / "tests" / "test_fr01_edge.py"
        f2.write_text("# edge file\n", encoding="utf-8")

        args = argparse.Namespace(
            project=str(tmp_path), fr_id="FR-01", test_files=[str(f1), str(f2)],
        )
        result = cmd_check_test_mirrors_spec(args)
        assert result == 0
        assert len(called_with) == 2
        assert "# inputs file" in called_with[0]
        assert "# edge file" in called_with[1]


# ---------------------------------------------------------------------------
# Bug #109 / #110 / #112 — plan/CLI signature drift fixes
# ---------------------------------------------------------------------------


class TestBuildTraceAttestationWriteFlag:
    """Bug #109: build-trace-attestation must accept --write / --no-write so
    plan template text matches the live CLI signature."""

    def test_help_lists_write_and_no_write(self):
        from harness_cli import build_parser
        parser = build_parser()
        # Subparser action holds per-command subparser instances; search them too.
        # --write and --no-write share dest="write" but each is its own argparse action.
        option_strings: list[str] = []
        for action in parser._actions:
            sub_parsers = getattr(action, "choices", None) or {}
            if not sub_parsers:
                continue
            for sub_parser in sub_parsers.values():
                for sub_action in sub_parser._actions:
                    if sub_action.dest == "write":
                        option_strings.extend(sub_action.option_strings)
        assert "--write" in option_strings, option_strings
        assert "--no-write" in option_strings, option_strings

    def test_no_write_runs_without_writing_attestation(self, tmp_path, monkeypatch):
        from harness_cli import cmd_build_trace_attestation
        # Stub build_attestation so we don't need a real project.
        def fake_build(project, overlay_path=None):
            return {"git_sha": "abc123", "content_sha256": "deadbeef", "overlay_errors": []}
        write_called = {"count": 0}
        def fake_write(project, attestation, trace_dir):
            write_called["count"] += 1
            return str(tmp_path / "attestation.json"), str(tmp_path / "latest.json")
        monkeypatch.setattr("scripts.build_trace_attestation.build_attestation", fake_build)
        monkeypatch.setattr("scripts.build_trace_attestation.write_attestation", fake_write)
        args = argparse.Namespace(
            project=str(tmp_path), overlay=None, trace_dir=tmp_path / "trace",
            write=False,  # --no-write path
        )
        result = cmd_build_trace_attestation(args)
        assert result == 0
        assert write_called["count"] == 0  # NO write when --no-write

    def test_default_write_calls_write_attestation(self, tmp_path, monkeypatch):
        from harness_cli import cmd_build_trace_attestation
        def fake_build(project, overlay_path=None):
            return {"git_sha": "abc123", "content_sha256": "deadbeef", "overlay_errors": []}
        write_called = {"count": 0}
        def fake_write(project, attestation, trace_dir):
            write_called["count"] += 1
            return str(tmp_path / "att.json"), str(tmp_path / "latest.json")
        monkeypatch.setattr("scripts.build_trace_attestation.build_attestation", fake_build)
        monkeypatch.setattr("scripts.build_trace_attestation.write_attestation", fake_write)
        args = argparse.Namespace(
            project=str(tmp_path), overlay=None, trace_dir=tmp_path / "trace",
            write=True,  # default
        )
        result = cmd_build_trace_attestation(args)
        assert result == 0
        assert write_called["count"] == 1  # write IS called when --write or default
