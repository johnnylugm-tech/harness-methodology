"""PR-5 — truth verifier dispatch, Stryker precheck, JS auto-fix stubs."""

import json
import subprocess
from pathlib import Path

from core.quality_gate.mutation_enforcer import (
    run_mutation_precheck,
    run_stryker_precheck,
)
from core.quality_gate.phase_truth_verifier import PhaseTruthVerifier
from core.traceability.auto_fix_propose import propose_fixes


def _js_state(tmp_path: Path, runner: str = "vitest") -> None:
    meth = tmp_path / ".methodology"
    meth.mkdir(exist_ok=True)
    (meth / "state.json").write_text(
        json.dumps({"state": "RUNNING", "language": "typescript",
                    "test_runner": runner}),
        encoding="utf-8",
    )


def _fake_run(responses):
    """subprocess.run stub keyed on argv substrings; records calls."""
    calls = []

    def runner(cmd, **kwargs):
        calls.append(cmd)
        for key, (rc, out) in responses.items():
            if key in " ".join(map(str, cmd)):
                return subprocess.CompletedProcess(cmd, rc, stdout=out, stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    return runner, calls


class TestStrykerPrecheck:
    def _report(self, tmp_path, mutants):
        report_dir = tmp_path / "reports" / "mutation"
        report_dir.mkdir(parents=True)
        (report_dir / "mutation.json").write_text(json.dumps({
            "files": {"src/a.ts": {"mutants": mutants}},
        }), encoding="utf-8")

    def test_survivors_block_with_locations(self, tmp_path, monkeypatch):
        _js_state(tmp_path)
        self._report(tmp_path, [
            {"status": "Killed", "mutatorName": "BooleanLiteral"},
            {"status": "Survived", "mutatorName": "EqualityOperator",
             "location": {"start": {"line": 7}}},
        ])
        runner, _ = _fake_run({"stryker --version": (0, "9.6.1"),
                               "stryker run": (0, "done")})
        monkeypatch.setattr(
            "core.quality_gate.mutation_enforcer.run_isolated", runner)
        ok, msg = run_stryker_precheck(tmp_path)
        assert ok is False
        assert "1 surviving mutant(s)" in msg
        assert "src/a.ts:7 EqualityOperator" in msg

    def test_all_killed_passes(self, tmp_path, monkeypatch):
        _js_state(tmp_path)
        self._report(tmp_path, [{"status": "Killed", "mutatorName": "X"}])
        runner, _ = _fake_run({"stryker --version": (0, "9.6.1"),
                               "stryker run": (0, "done")})
        monkeypatch.setattr(
            "core.quality_gate.mutation_enforcer.run_isolated", runner)
        assert run_stryker_precheck(tmp_path) == (True, "")

    def test_missing_stryker_blocks_with_install_hint(self, tmp_path, monkeypatch):
        _js_state(tmp_path)
        runner, _ = _fake_run({"stryker --version": (1, "not found")})
        monkeypatch.setattr(
            "core.quality_gate.mutation_enforcer.run_isolated", runner)
        ok, msg = run_stryker_precheck(tmp_path)
        assert ok is False and "npm ci" in msg

    def test_missing_report_blocks(self, tmp_path, monkeypatch):
        _js_state(tmp_path)
        runner, _ = _fake_run({"stryker --version": (0, "9.6.1"),
                               "stryker run": (0, "done, but no reporter")})
        monkeypatch.setattr(
            "core.quality_gate.mutation_enforcer.run_isolated", runner)
        ok, msg = run_stryker_precheck(tmp_path)
        assert ok is False and "mutation.json" in msg

    def test_language_dispatch_from_state(self, tmp_path, monkeypatch):
        """run_mutation_precheck routes js/ts projects to stryker, not mutmut."""
        _js_state(tmp_path)
        runner, calls = _fake_run({"stryker --version": (1, "no")})
        monkeypatch.setattr(
            "core.quality_gate.mutation_enforcer.run_isolated", runner)
        ok, msg = run_mutation_precheck(tmp_path)
        assert ok is False and "StrykerJS" in msg
        assert any("stryker" in " ".join(map(str, c)) for c in calls)


class TestTruthVerifierJsDispatch:
    def test_check_pytest_runs_vitest_for_ts_project(self, tmp_path, monkeypatch):
        _js_state(tmp_path, runner="vitest")
        runner, calls = _fake_run({"vitest run": (0, "3 passed")})
        monkeypatch.setattr(subprocess, "run", runner)
        v = PhaseTruthVerifier(str(tmp_path), phase=3)
        passed, score, details = v.check_pytest()
        assert (passed, score) == (True, 100.0)
        assert any("vitest" in " ".join(map(str, c)) for c in calls)
        assert not any(c and c[0] == "pytest" for c in calls)

    def test_check_pytest_jest_failure_path(self, tmp_path, monkeypatch):
        _js_state(tmp_path, runner="jest")
        runner, calls = _fake_run({"jest": (1, "Tests: 2 failed, 1 passed")})
        monkeypatch.setattr(subprocess, "run", runner)
        v = PhaseTruthVerifier(str(tmp_path), phase=3)
        passed, score, details = v.check_pytest()
        assert passed is False and score == 0.0
        assert any("jest" in " ".join(map(str, c)) for c in calls)

    def test_check_coverage_reads_json_summary(self, tmp_path, monkeypatch):
        _js_state(tmp_path)
        cov_dir = tmp_path / "coverage"
        cov_dir.mkdir()
        (cov_dir / "coverage-summary.json").write_text(
            json.dumps({"total": {"lines": {"pct": 91.2}}}), encoding="utf-8"
        )
        runner, _ = _fake_run({"vitest run": (0, "ok")})
        monkeypatch.setattr(subprocess, "run", runner)
        v = PhaseTruthVerifier(str(tmp_path), phase=4)
        passed, score, details = v.check_coverage()
        assert passed is True
        assert score == 91.0
        assert "threshold 80%" in details

    def test_check_coverage_missing_summary_is_zero(self, tmp_path, monkeypatch):
        _js_state(tmp_path)
        runner, _ = _fake_run({"vitest run": (1, "FAIL")})
        monkeypatch.setattr(subprocess, "run", runner)
        v = PhaseTruthVerifier(str(tmp_path), phase=4)
        passed, score, details = v.check_coverage()
        assert passed is False and score == 0.0


class TestAutoFixJsStubs:
    def test_propose_fixes_emits_ts_stub_and_slash_comments(self, tmp_path):
        _js_state(tmp_path, runner="vitest")
        (tmp_path / "tests").mkdir()
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "parser.ts").write_text(
            "export const parse = (x: string) => x;\n", encoding="utf-8"
        )
        (tmp_path / "SAD.md").write_text(
            "## FR-01\nthe parser module handles tokenization\n", encoding="utf-8"
        )
        report = {"uncoded": ["FR-01"], "untested": ["FR-01"]}
        diff = propose_fixes(None, report, tmp_path)
        assert "tests/test_fr_01.test.ts" in diff
        assert 'it("test_fr_01_placeholder"' in diff
        assert 'import { it, expect } from "vitest";' in diff
        assert "+// Implements: FR-01" in diff
        assert ".py" not in diff

    def test_jest_stub_skips_vitest_import(self, tmp_path):
        _js_state(tmp_path, runner="jest")
        (tmp_path / "tests").mkdir()
        report = {"uncoded": [], "untested": ["FR-02"]}
        diff = propose_fixes(None, report, tmp_path)
        assert "tests/test_fr_02.test.js" not in diff  # typescript state → .ts
        assert "tests/test_fr_02.test.ts" in diff
        assert "vitest" not in diff

    def test_python_stub_unchanged(self, tmp_path):
        (tmp_path / "tests").mkdir()
        report = {"uncoded": [], "untested": ["FR-03"]}
        diff = propose_fixes(None, report, tmp_path)
        assert "tests/test_fr_03.py" in diff
        assert "def test_fr_03_placeholder():" in diff
