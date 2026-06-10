"""JS/TS toolchain scorers — captured-output fixtures (PR-2).

Each scorer is fed a realistic captured tool output and must produce the
documented score. Network/subprocess is never touched except the artifact-
append test, which monkeypatches subprocess.run.
"""

import json
import subprocess

from harness.tool_runners import (
    _ARTIFACT_MARKER,
    compute_tool_score,
    run_tool,
)

# ── eslint ───────────────────────────────────────────────────────────────────

ESLINT_VIOLATIONS = json.dumps([
    {"filePath": "src/a.ts", "errorCount": 2, "warningCount": 1,
     "messages": [{"ruleId": "no-unused-vars", "severity": 2},
                  {"ruleId": "no-undef", "severity": 2},
                  {"ruleId": "complexity", "severity": 1}]},
    {"filePath": "src/b.ts", "errorCount": 0, "warningCount": 0, "messages": []},
])


class TestScoreEslint:
    def test_violations_cost_two_points_each(self):
        assert compute_tool_score("eslint", ESLINT_VIOLATIONS, 1) == 94.0

    def test_clean_run_scores_100(self):
        assert compute_tool_score("eslint", "[]", 0) == 100.0

    def test_non_json_returns_none(self):
        assert compute_tool_score("eslint", "Oops! Something went wrong!", 2) is None

    def test_json_object_not_list_returns_none(self):
        assert compute_tool_score("eslint", "{}", 0) is None


# ── tsc ──────────────────────────────────────────────────────────────────────

TSC_ERRORS = (
    "src/a.ts(3,1): error TS2322: Type 'string' is not assignable to type 'number'.\n"
    "src/b.ts(10,5): error TS2304: Cannot find name 'foo'.\n"
)


class TestScoreTsc:
    def test_errors_cost_five_points_each(self):
        assert compute_tool_score("tsc", TSC_ERRORS, 2) == 90.0

    def test_clean_compile_scores_100(self):
        assert compute_tool_score("tsc", "tsc exit=0", 0) == 100.0

    def test_checkjs_uses_same_scorer(self):
        assert compute_tool_score("tsc-checkjs", TSC_ERRORS, 2) == 90.0

    def test_config_error_counts(self):
        out = "error TS5083: Cannot read file 'tsconfig.checkjs.json'."
        assert compute_tool_score("tsc-checkjs", out, 1) == 95.0


# ── semgrep ──────────────────────────────────────────────────────────────────

SEMGREP_FINDINGS = json.dumps({
    "results": [
        {"check_id": "js-eval-usage", "extra": {"severity": "ERROR"}},
        {"check_id": "js-innerhtml-assignment", "extra": {"severity": "WARNING"}},
        {"check_id": "js-document-write", "extra": {"severity": "INFO"}},
    ],
    "errors": [],
})


class TestScoreSemgrep:
    def test_severity_weights_match_bandit(self):
        # ERROR −10, WARNING −3, INFO −1 → 86
        assert compute_tool_score("semgrep-js", SEMGREP_FINDINGS, 0) == 86.0

    def test_clean_scan_scores_100(self):
        assert compute_tool_score("semgrep-js", '{"results": [], "errors": []}', 0) == 100.0

    def test_non_json_returns_none(self):
        assert compute_tool_score("semgrep-js", "semgrep crashed", 2) is None


# ── coverage-summary (vitest/jest json-summary artifact) ─────────────────────

COVERAGE_ARTIFACT = json.dumps({
    "total": {
        "lines": {"total": 200, "covered": 171, "pct": 85.5},
        "branches": {"pct": 70.0},
    }
})


class TestScoreCoverageSummary:
    def test_reads_total_lines_pct_from_artifact(self):
        out = "Test Files  3 passed (3)" + _ARTIFACT_MARKER + COVERAGE_ARTIFACT
        assert compute_tool_score("vitest-cov", out, 0) == 85.5
        assert compute_tool_score("jest-cov", out, 0) == 85.5
        assert compute_tool_score("vitest-cov-integration", out, 0) == 85.5

    def test_missing_artifact_scores_zero(self):
        # Suite failed before writing coverage — unverifiable claim blocks
        assert compute_tool_score("vitest-cov", "FAIL src/a.test.ts", 1) == 0.0

    def test_corrupt_artifact_returns_none(self):
        out = "ok" + _ARTIFACT_MARKER + "{not json"
        assert compute_tool_score("vitest-cov", out, 0) is None


# ── js-bench ─────────────────────────────────────────────────────────────────

BENCH_OUTPUT = json.dumps({"benchmarks": [
    {"name": "FR-01 fast path", "mean_ms": 50.0},
    {"name": "FR-02 warning zone", "mean_ms": 1500.0},
    {"name": "FR-03 hard fail", "mean_ms": 4000.0},
]})


class TestScoreJsBench:
    def test_threshold_penalties_match_pytest_benchmark(self):
        # 100 − 25 (>1000ms) − 50 (>3000ms) = 25
        assert compute_tool_score("js-bench", BENCH_OUTPUT, 0) == 25.0

    def test_all_fast_scores_100(self):
        out = json.dumps({"benchmarks": [{"name": "x", "mean_ms": 10}]})
        assert compute_tool_score("js-bench", out, 0) == 100.0

    def test_empty_bench_set_is_na_not_a_pass(self):
        # No benchmarks registered → None (dimension N/A), parity with
        # pytest-benchmark exit 5. An empty stub must NOT grant a free 100.
        assert compute_tool_score("js-bench", '{"benchmarks": []}', 0) is None

    def test_missing_bench_script_returns_none(self):
        out = "node: Cannot find module '/proj/benchmarks/run.mjs'"
        assert compute_tool_score("js-bench", out, 1) is None


# ── run_tool artifact append ─────────────────────────────────────────────────

class TestArtifactAppend:
    def test_output_artifact_is_appended_after_marker(self, tmp_path, monkeypatch):
        # A real run writes coverage-summary.json itself; the fake run does the
        # same so the freshly-written artifact is appended.
        def fake_run(cmd, **kwargs):
            cov = tmp_path / "coverage"
            cov.mkdir(exist_ok=True)
            (cov / "coverage-summary.json").write_text(COVERAGE_ARTIFACT, encoding="utf-8")
            return subprocess.CompletedProcess(cmd, 0, stdout="3 passed", stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)
        out, rc = run_tool("vitest-cov", str(tmp_path))
        assert rc == 0
        assert _ARTIFACT_MARKER in out
        assert compute_tool_score("vitest-cov", out, rc) == 85.5

    def test_stale_artifact_deleted_when_run_writes_nothing(self, tmp_path, monkeypatch):
        # A prior run left coverage-summary.json; this run crashes before
        # rewriting it. The stale file must be deleted, not scored as current.
        cov = tmp_path / "coverage"
        cov.mkdir()
        (cov / "coverage-summary.json").write_text(COVERAGE_ARTIFACT, encoding="utf-8")

        def fake_run(cmd, **kwargs):
            return subprocess.CompletedProcess(cmd, 1, stdout="FAIL", stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)
        out, rc = run_tool("vitest-cov", str(tmp_path))
        assert _ARTIFACT_MARKER not in out
        assert not (cov / "coverage-summary.json").exists()
        assert compute_tool_score("vitest-cov", out, rc) == 0.0

    def test_missing_artifact_keeps_plain_output(self, tmp_path, monkeypatch):
        def fake_run(cmd, **kwargs):
            return subprocess.CompletedProcess(cmd, 1, stdout="FAIL", stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)
        out, rc = run_tool("vitest-cov", str(tmp_path))
        assert _ARTIFACT_MARKER not in out
        assert compute_tool_score("vitest-cov", out, rc) == 0.0
