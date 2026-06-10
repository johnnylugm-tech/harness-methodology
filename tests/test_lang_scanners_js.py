"""tree-sitter JS/TS in-process scanners (PR-3) — fixture projects in tmp_path.

Each scanner must emit the same JSON schema as its Python counterpart so the
shared scorers apply unchanged; tests assert through run_tool/compute_tool_score
to cover the registry dispatch path end to end.
"""

import json

import pytest

pytest.importorskip("tree_sitter")
pytest.importorskip("tree_sitter_javascript")
pytest.importorskip("tree_sitter_typescript")

from harness.tool_runners import compute_tool_score, run_tool  # noqa: E402


def _write(root, rel, text):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


# ── js-assertions ────────────────────────────────────────────────────────────

class TestJsAssertions:
    def test_detects_zero_assert_and_real_assertions(self, tmp_path):
        _write(tmp_path, "tests/test_fr01_parse.test.ts", """
import { describe, it, expect } from "vitest";

describe("FR-01", () => {
  it("test_fr01_happy_path", () => {
    expect(parse("a")).toBe("b");
  });
  it("test_fr01_empty_shell", () => {
    const x = 1;  // no assertion — pass-and-still-green shell
  });
  test("test_fr01_node_assert", () => {
    assert.equal(1, 1);
  });
});
""")
        out, rc = run_tool("js-assertions", str(tmp_path))
        assert rc == 0
        data = json.loads(out)
        assert data["total"] == 3
        assert data["asserted"] == 2
        assert data["zero_assert"] == [
            "tests/test_fr01_parse.test.ts::test_fr01_empty_shell"
        ]
        # 2/3 → 66.7 via the shared ast-assertions scorer
        assert compute_tool_score("js-assertions", out, rc) == 66.7

    def test_it_each_and_skip_variants_count(self, tmp_path):
        _write(tmp_path, "tests/x.spec.js", """
it.each([[1], [2]])("test_each_%i", (n) => { expect(n).toBeGreaterThan(0); });
test.skip("test_skipped_shell", () => {});
""")
        data = json.loads(run_tool("js-assertions", str(tmp_path))[0])
        assert data["total"] == 2
        assert data["asserted"] == 1

    def test_no_tests_scores_zero(self, tmp_path):
        (tmp_path / "tests").mkdir()
        out, rc = run_tool("js-assertions", str(tmp_path))
        assert json.loads(out)["total"] == 0
        assert compute_tool_score("js-assertions", out, rc) == 0.0


# ── js-error-handling ────────────────────────────────────────────────────────

class TestJsErrorHandling:
    def test_try_catch_and_promise_catch_count_as_handled(self, tmp_path):
        _write(tmp_path, "src/with_try.ts", """
export function f(a: string): string {
  try { return JSON.parse(a); } catch (e) { return ""; }
}
""")
        _write(tmp_path, "src/with_promise.js", """
export function g(p) { return fetch(p).catch(() => null); }
""")
        _write(tmp_path, "src/naked.ts", """
export function h(a: number): number { return a + 1; }
""")
        _write(tmp_path, "src/exempt.ts", """
// pragma: no error-handling — pure data mapping, nothing can fail
export const m = (x: number) => x * 2;
""")
        out, rc = run_tool("js-error-handling", str(tmp_path))
        data = json.loads(out)
        assert data["total"] == 3
        assert data["with_handler"] == 2
        assert data["no_handler"] == ["src/naked.ts"]
        assert data["exempt_count"] == 1
        assert data["exempt_files"] == ["src/exempt.ts"]
        assert compute_tool_score("js-error-handling", out, rc) == 66.7

    def test_files_without_code_excluded(self, tmp_path):
        _write(tmp_path, "src/constants.ts", 'export const X = 1;\n')
        data = json.loads(run_tool("js-error-handling", str(tmp_path))[0])
        # const-only file has no function/class nodes → not in denominator
        assert data["total"] == 0

    def test_bare_catch_property_read_is_not_a_handler(self, tmp_path):
        # `obj.catch` as a property read (not `.catch(fn)`) must NOT count as
        # error handling — only a promise rejection call does.
        _write(tmp_path, "src/bare.ts", """
export function f(obj: { catch: boolean }): boolean { return obj.catch; }
""")
        _write(tmp_path, "src/real.ts", """
export function g(p: Promise<number>) { return p.catch(() => 0); }
""")
        out, rc = run_tool("js-error-handling", str(tmp_path))
        data = json.loads(out)
        assert data["total"] == 2
        assert data["with_handler"] == 1
        assert data["no_handler"] == ["src/bare.ts"]


# ── js-doc-coverage ──────────────────────────────────────────────────────────

class TestJsDocCoverage:
    def test_exported_surface_jsdoc_ratio(self, tmp_path):
        _write(tmp_path, "src/api.ts", """
/** Parses the input. */
export function parse(a: string): string { return a; }

export function undocumented(a: string): string { return a; }

/** Service wrapper. */
export class Service {
  /** Runs the request. */
  run(): void {}
  helper(): void {}
  _internal(): void {}
  constructor() {}
}

const local = () => 1;  // not exported — not part of the public surface
""")
        out, rc = run_tool("js-doc-coverage", str(tmp_path))
        data = json.loads(out)
        # parse ✓, undocumented ✗, Service ✓, Service.run ✓, Service.helper ✗
        assert data["total"] == 5
        assert data["with_doc"] == 3
        assert set(data["missing"]) == {
            "src/api.ts::undocumented", "src/api.ts::Service.helper",
        }
        assert compute_tool_score("js-doc-coverage", out, rc) == 60.0

    def test_exported_arrow_const_counts(self, tmp_path):
        _write(tmp_path, "src/fn.mjs", """
/** Doubles. */
export const double = (x) => x * 2;
export const triple = (x) => x * 3;
""")
        data = json.loads(run_tool("js-doc-coverage", str(tmp_path))[0])
        assert data["total"] == 2
        assert data["with_doc"] == 1

    def test_exported_value_const_not_counted(self, tmp_path):
        # Parity with Python def/class: a value export is not a documentable
        # callable and must not inflate the denominator. The arrow export is
        # the only counted symbol.
        _write(tmp_path, "src/data.ts", """
export const TABLE = { a: "alpha", b: "beta" };
export const COUNT = 42;
/** Looks a token up. */
export const lookup = (k: string) => TABLE[k];
""")
        data = json.loads(run_tool("js-doc-coverage", str(tmp_path))[0])
        assert data["total"] == 1            # only `lookup`
        assert data["with_doc"] == 1
        assert data["missing"] == []

    def test_no_code_returns_empty_summary_scores_100(self, tmp_path):
        (tmp_path / "src").mkdir()
        out, rc = run_tool("js-doc-coverage", str(tmp_path))
        assert json.loads(out) == {}
        assert compute_tool_score("js-doc-coverage", out, rc) == 100.0


# ── js-mi ────────────────────────────────────────────────────────────────────

class TestJsMi:
    def test_simple_file_ranks_a_and_scorer_averages(self, tmp_path):
        _write(tmp_path, "src/simple.ts", """
export function add(a: number, b: number): number { return a + b; }
""")
        out, rc = run_tool("js-mi", str(tmp_path))
        data = json.loads(out)
        assert "src/simple.ts" in data
        assert data["src/simple.ts"]["mi"] > 60  # tiny file → high MI
        assert data["src/simple.ts"]["rank"] == "A"
        score = compute_tool_score("js-mi", out, rc)
        # radon-mi scorer averages to 1 decimal place
        assert score == round(data["src/simple.ts"]["mi"], 1)

    def test_complex_file_scores_lower(self, tmp_path):
        branches = "\n".join(
            f"  if (x === {i}) {{ out = out + {i}; }} else if (x > {i * 7}) "
            f"{{ out = out - {i}; }}"
            for i in range(1, 30)
        )
        body = branches + "\n  return out ? out : (x && f(x)) || g(x);"
        _write(tmp_path, "src/tangled.js",
               f"export function tangle(x) {{\n  let out = 0;\n{body}\n}}\n"
               f"function f(v) {{ return v; }}\nfunction g(v) {{ return v; }}\n")
        _write(tmp_path, "src/simple.js", "export const id = (x) => x;\n")
        out, _rc = run_tool("js-mi", str(tmp_path))
        data = json.loads(out)
        assert data["src/tangled.js"]["mi"] < data["src/simple.js"]["mi"]

    def test_no_source_returns_none(self, tmp_path):
        (tmp_path / "src").mkdir()
        out, rc = run_tool("js-mi", str(tmp_path))
        # Empty result set → radon-mi scorer returns None (nothing analysable
        # is never a free 100)
        assert compute_tool_score("js-mi", out, rc) is None
