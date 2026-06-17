"""End-to-end pilots (PR-7) — TS+vitest and JS+jest fixture projects.

Fast tier (always runs): language detection, registry resolution, real
in-process scanner runs, traceability, and D4 spec-coverage against
tests/fixtures/*_project copies.

Slow tier (@pytest.mark.slow): copies the TS fixture to tmp, installs the
pinned devDependencies (network), and runs the REAL external tools
(eslint / tsc / vitest coverage) through run_tool — skipped automatically
when npm/network is unavailable so CI without node stays green.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

pytest.importorskip("tree_sitter")

from harness.tool_runners import compute_tool_score, run_tool  # noqa: E402
from harness.toolchains import (  # noqa: E402
    detect_language,
    detect_test_runner,
    resolve_tool_id,
)

FIXTURES = Path(__file__).parent / "fixtures"
TS_PILOT = FIXTURES / "ts_vitest_project"
JS_PILOT = FIXTURES / "js_jest_project"


@pytest.fixture()
def ts_pilot(tmp_path):
    dst = tmp_path / "ts_pilot"
    shutil.copytree(TS_PILOT, dst)
    return dst


@pytest.fixture()
def js_pilot(tmp_path):
    dst = tmp_path / "js_pilot"
    shutil.copytree(JS_PILOT, dst)
    return dst


class TestDetectionAndResolution:
    def test_ts_pilot_detects_typescript_vitest(self, ts_pilot):
        assert detect_language(ts_pilot) == "typescript"
        assert detect_test_runner(ts_pilot) == "vitest"

    def test_js_pilot_detects_javascript_jest(self, js_pilot):
        assert detect_language(js_pilot) == "javascript"
        assert detect_test_runner(js_pilot) == "jest"

    def test_runner_variant_resolution(self):
        assert resolve_tool_id("test_coverage", "typescript",
                               test_runner="vitest") == "vitest-cov"
        assert resolve_tool_id("test_coverage", "javascript",
                               test_runner="jest") == "jest-cov"
        assert resolve_tool_id("type_safety", "javascript") == "tsc-checkjs"
        assert resolve_tool_id("type_safety", "typescript") == "tsc"


class TestTsPilotInProcessDimensions:
    """Real scanner runs — the deliberate defects must be detected."""

    def test_assertion_quality_catches_zero_assert_shell(self, ts_pilot):
        out, rc = run_tool("js-assertions", str(ts_pilot))
        data = json.loads(out)
        assert data["total"] == 4
        assert data["asserted"] == 3
        assert data["zero_assert"] == [
            "tests/test_fr02_map.test.ts::test_fr02_zero_assert_shell"
        ]
        assert compute_tool_score("js-assertions", out, rc) == 75.0

    def test_documentation_catches_missing_jsdoc(self, ts_pilot):
        out, rc = run_tool("js-doc-coverage", str(ts_pilot))
        data = json.loads(out)
        assert data["total"] == 2  # parse ✓, mapToken ✗
        assert data["missing"] == ["src/mapper.ts::mapToken"]
        assert compute_tool_score("js-doc-coverage", out, rc) == 50.0

    def test_error_handling_catches_unhandled_file(self, ts_pilot):
        out, rc = run_tool("js-error-handling", str(ts_pilot))
        data = json.loads(out)
        assert data["no_handler"] == ["src/mapper.ts"]
        assert compute_tool_score("js-error-handling", out, rc) == 50.0

    def test_mi_scores_both_files(self, ts_pilot):
        out, rc = run_tool("js-mi", str(ts_pilot))
        data = json.loads(out)
        assert set(data) == {"src/parser.ts", "src/mapper.ts"}
        score = compute_tool_score("js-mi", out, rc)
        assert score is not None and 0 < score <= 100


class TestTsPilotTraceabilityAndSpecCoverage:
    def test_scan_all_links_frs_to_code_and_tests(self, ts_pilot):
        from core.traceability.scanner import scan_all
        result = scan_all(ts_pilot)
        assert result["sad_frs"] == ["FR-01", "FR-02"]
        assert "src/parser.ts" in result["fr_to_code"]["FR-01"]  # type: ignore[reportIndexIssue]
        assert "src/mapper.ts" in result["fr_to_code"]["FR-02"]  # type: ignore[reportIndexIssue]
        assert result["fr_to_tests"]["FR-01"] == ["tests/test_fr01_parse.test.ts"]  # type: ignore[reportIndexIssue]
        assert result["fr_to_modules"]["FR-01"] == ["src/parser.ts"]  # type: ignore[reportIndexIssue]
        assert result["ghost_frs"] == []

    def test_spec_coverage_check_passes_at_100(self, ts_pilot):
        import harness_cli
        code, pct = harness_cli._run_spec_coverage_check(
            ts_pilot, threshold=80.0, verbose=False
        )
        assert (code, pct) == (0, 100.0)

    def test_spec_coverage_detects_missing_implementation(self, ts_pilot):
        import harness_cli
        spec = ts_pilot / "02-architecture" / "TEST_SPEC.md"
        spec.write_text(
            spec.read_text(encoding="utf-8")
            + "\n| 3 | `test_fr02_unimplemented_case` | token=\"z\" | validation | Q2 |\n",
            encoding="utf-8",
        )
        code, pct = harness_cli._run_spec_coverage_check(
            ts_pilot, threshold=90.0, verbose=False
        )
        assert code == 1
        assert pct == 80.0  # 4 of 5 spec items implemented


class TestJsPilotInProcess:
    def test_assertions_all_pass(self, js_pilot):
        out, _rc = run_tool("js-assertions", str(js_pilot))
        data = json.loads(out)
        assert data["total"] == 2 and data["asserted"] == 2

    def test_error_handling_full_coverage(self, js_pilot):
        out, rc = run_tool("js-error-handling", str(js_pilot))
        assert compute_tool_score("js-error-handling", out, rc) == 100.0

    def test_cjs_exports_not_measured_is_documented_contract(self, js_pilot):
        # Known v2.8 limitation (SOP §4): doc coverage measures ESM `export`
        # surface; CJS module.exports files contribute no public symbols →
        # total 0 → scorer returns 100 (nothing to document).
        out, rc = run_tool("js-doc-coverage", str(js_pilot))
        assert json.loads(out)["total"] == 0
        assert compute_tool_score("js-doc-coverage", out, rc) == 100.0


def _npm_install(project: Path) -> bool:
    """Install pinned devDeps; False when npm/network is unavailable."""
    if shutil.which("npm") is None:
        return False
    try:
        r = subprocess.run(
            ["npm", "install", "--no-audit", "--no-fund", "--silent"],
            cwd=project, capture_output=True, text=True, timeout=420,
        )
        return r.returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False


@pytest.mark.slow
class TestTsPilotExternalToolsE2E:
    """Real eslint/tsc/vitest runs through run_tool (network + node required)."""

    @pytest.fixture(scope="class")
    def installed_pilot(self, tmp_path_factory):
        dst = tmp_path_factory.mktemp("ts_pilot_e2e") / "proj"
        shutil.copytree(TS_PILOT, dst)
        if not _npm_install(dst):
            pytest.skip("npm install unavailable (offline or npm missing)")
        return dst

    def test_eslint_catches_unused_var_defect(self, installed_pilot):
        out, rc = run_tool("eslint", str(installed_pilot))
        score = compute_tool_score("eslint", out, rc)
        assert score is not None and score < 100.0  # unusedDefect in mapper.ts

    def test_tsc_clean_compile_scores_100(self, installed_pilot):
        out, rc = run_tool("tsc", str(installed_pilot))
        assert compute_tool_score("tsc", out, rc) == 100.0, out[-2000:]

    def test_vitest_coverage_artifact_scores(self, installed_pilot):
        out, rc = run_tool("vitest-cov", str(installed_pilot))
        score = compute_tool_score("vitest-cov", out, rc)
        assert score is not None and score > 80.0, out[-2000:]
