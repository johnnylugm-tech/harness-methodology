"""Traceability / spec-coverage / mirror-gate on a JS/TS fixture project (PR-4)."""

import json
from pathlib import Path

import pytest

pytest.importorskip("tree_sitter")

from core.traceability.scanner import (  # noqa: E402
    SAD_ROW_PATTERN,
    scan_fr_annotations,
    scan_python_fr_annotations,
    scan_test_fr_coverage,
)
import harness_cli  # noqa: E402


def _ts_project(tmp_path: Path) -> Path:
    """Minimal TS project: state.json, [FR-XX] sources, test_fr tests."""
    meth = tmp_path / ".methodology"
    meth.mkdir()
    (meth / "state.json").write_text(
        json.dumps({"state": "RUNNING", "current_phase": 3,
                    "language": "typescript", "test_runner": "vitest"}),
        encoding="utf-8",
    )
    src = tmp_path / "src"
    src.mkdir()
    (src / "parser.ts").write_text(
        "// [FR-01] tokenizer entry point\n"
        "export function parse(a: string): string { return a; }\n",
        encoding="utf-8",
    )
    (src / "mapper.ts").write_text(
        "/* [FR-02] mapping table */\n"
        "export const map = (x: string) => x;\n",
        encoding="utf-8",
    )
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_fr01_parse.test.ts").write_text(
        'import { it, expect } from "vitest";\n'
        "// [FR-01]\n"
        'it("test_fr01_happy_path", () => { expect(1).toBe(1); });\n'
        'it("test_fr01_validation", () => { expect(2).toBe(2); });\n',
        encoding="utf-8",
    )
    return tmp_path


class TestFrAnnotationScan:
    def test_ts_sources_scanned_via_state_language(self, tmp_path):
        project = _ts_project(tmp_path)
        fr_map = scan_fr_annotations(project)
        # Python-parity: annotation scan covers the whole tree, tests included
        # (scan_all unions code/tests separately).
        assert fr_map["FR-01"] == ["src/parser.ts", "tests/test_fr01_parse.test.ts"]
        assert fr_map["FR-02"] == ["src/mapper.ts"]

    def test_python_alias_still_works(self, tmp_path):
        (tmp_path / "mod.py").write_text("# [FR-03]\n", encoding="utf-8")
        fr_map = scan_python_fr_annotations(tmp_path, "python")
        assert fr_map["FR-03"] == ["mod.py"]

    def test_test_coverage_scan_finds_js_tests(self, tmp_path):
        project = _ts_project(tmp_path)
        cov = scan_test_fr_coverage(project / "tests")
        assert cov["FR-01"] == ["tests/test_fr01_parse.test.ts"]


class TestSadRowPattern:
    def test_matches_all_supported_extensions(self):
        line = "| FR-01 | `src/parser.ts` | done |"
        m = SAD_ROW_PATTERN.search(line)
        assert m and m.group(2) == "src/parser.ts"
        for fname in ("a.py", "b.jsx", "c.tsx", "d.mjs", "e.cjs", "f.js"):
            assert SAD_ROW_PATTERN.search(f"FR-02 → `{fname}`"), fname

    def test_ignores_non_source_files(self):
        assert SAD_ROW_PATTERN.search("FR-03 `notes.md`") is None


class TestScanTestFunctionsJs:
    def test_extracts_titles_from_it_and_test_calls(self, tmp_path):
        project = _ts_project(tmp_path)
        (project / "tests" / "more.spec.ts").write_text(
            'test("test_fr02_roundtrip", () => { expect(1).toBe(1); });\n'
            "it.each([[1]])('test_fr02_each_case', (n) => { expect(n).toBe(1); });\n"
            'it(`test_fr02_backtick`, () => { expect(1).toBe(1); });\n',
            encoding="utf-8",
        )
        fns = harness_cli._scan_test_functions(project / "tests", "typescript")
        assert fns == {
            "test_fr01_happy_path", "test_fr01_validation",
            "test_fr02_roundtrip", "test_fr02_each_case", "test_fr02_backtick",
        }

    def test_python_mode_unchanged(self, tmp_path):
        tests = tmp_path / "tests"
        tests.mkdir()
        (tests / "test_x.py").write_text(
            "def test_alpha():\n    assert True\n", encoding="utf-8"
        )
        assert harness_cli._scan_test_functions(tests) == {"test_alpha"}


class TestMirrorGateJs:
    def _assertions(self):
        from core.quality_gate.red_assertion_check import SubAssertion
        return [SubAssertion(rule_id="A1", predicate="len(result) == 4",
                             applies_to=[1])]

    def test_parseable_file_with_tests_yields_review_items(self):
        from core.quality_gate.red_assertion_check import check_test_mirrors_spec_js
        src = 'it("test_fr01_x", () => { expect(r.length).toBe(4); });\n'
        violations = check_test_mirrors_spec_js(src, [], self._assertions())
        assert [v.severity for v in violations] == ["info"]
        assert violations[0].check_type == "js_predicate_review"

    def test_no_tests_is_an_error(self):
        from core.quality_gate.red_assertion_check import check_test_mirrors_spec_js
        violations = check_test_mirrors_spec_js("const x = 1;\n", [], [])
        assert violations[0].check_type == "no_test_cases"
        assert violations[0].severity == "error"

    def test_unparseable_is_an_error(self):
        from core.quality_gate.red_assertion_check import check_test_mirrors_spec_js
        violations = check_test_mirrors_spec_js("it('x', () => {", [], [])
        assert violations[0].check_type == "test_unparseable"

    def test_tsx_dialect_parses_jsx_test(self):
        from core.quality_gate.red_assertion_check import check_test_mirrors_spec_js
        src = (
            'it("test_fr01_renders", () => { '
            'const el = <Foo />; expect(el).toBeTruthy(); });\n'
        )
        violations = check_test_mirrors_spec_js(src, [], self._assertions(),
                                                dialect="tsx")
        # No parse error AND it() is present → only the review-info items.
        assert [v.severity for v in violations] == ["info"]
        assert violations[0].check_type == "js_predicate_review"

    def test_javascript_dialect_routes_to_js_grammar(self):
        from core.quality_gate.red_assertion_check import check_test_mirrors_spec_js
        # Pure JS — no TS-only syntax. Must parse under the JS grammar and
        # NOT raise. (Previously the test suite only covered typescript.)
        src = 'it("test_fr01_x", () => { expect(1).toBe(1); });\n'
        violations = check_test_mirrors_spec_js(src, [], self._assertions(),
                                                dialect="javascript")
        assert [v.severity for v in violations] == ["info"]


class TestTraceDirtyStateJs:
    def test_js_test_mtime_triggers_staleness(self, tmp_path):
        import os
        import time
        import json
        project = _ts_project(tmp_path)
        # Mock Phase 2 so that staleness is treated as a hard block, as expected by the test
        (project / ".methodology" / "state.json").write_text(json.dumps({
            "current_phase": 2, "language": "typescript", "test_runner": "vitest"
        }), encoding="utf-8")
        trace_dir = project / ".methodology" / "trace"
        trace_dir.mkdir(parents=True, exist_ok=True)
        att = trace_dir / "attestation.json"
        att.write_text("{}", encoding="utf-8")
        old = time.time() - 3600
        os.utime(att, (old, old))
        from cli.phase_cmds import _trace_dirty_state
        result = _trace_dirty_state(project)
        assert result["passed"] is False
        assert "test_fr01_parse.test.ts" in result["reason"]
