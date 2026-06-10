"""error_handling anti-pattern detection (v2.9 A1) — presence → quality.

Calibration source: tts-new Gate 4 scored error_handling 100 while shipping
`except BaseException` (Critical). These tests lock the detection of handlers
that exist but undermine resilience, in both languages, plus the scorer's
−5-per-finding deduction and backward compatibility with pre-v2.9 output.
"""

import json

import pytest

from harness.tool_runners import compute_tool_score, run_tool


def _write(root, rel, text):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class TestPythonAntiPatterns:
    def test_base_exception_without_reraise_flagged(self, tmp_path):
        _write(tmp_path, "src/breaker.py", """
async def call(coro):
    try:
        return await coro
    except BaseException:
        record_failure()
""")
        out, rc = run_tool("ast-error-handling", str(tmp_path))
        data = json.loads(out)
        assert data["total"] == 1 and data["with_handler"] == 1
        assert data["anti_patterns"] == ["src/breaker.py:5::except_base_exception"]
        # 100 (handled) − 5 (anti-pattern) = 95 — no longer a free 100
        assert compute_tool_score("ast-error-handling", out, rc) == 95.0

    def test_base_exception_with_bare_reraise_is_clean(self, tmp_path):
        _write(tmp_path, "src/cleanup.py", """
def run(job):
    try:
        return job()
    except BaseException:
        release_lock()
        raise
""")
        data = json.loads(run_tool("ast-error-handling", str(tmp_path))[0])
        assert data["anti_patterns"] == []

    def test_bare_except_flagged(self, tmp_path):
        _write(tmp_path, "src/loose.py", """
def f(x):
    try:
        return int(x)
    except:
        return 0
""")
        data = json.loads(run_tool("ast-error-handling", str(tmp_path))[0])
        assert data["anti_patterns"] == ["src/loose.py:5::bare_except"]

    def test_broad_swallow_flagged_once_not_twice(self, tmp_path):
        # `except Exception: pass` — broad type + swallow body → one entry,
        # classified as broad_swallow (severity order).
        _write(tmp_path, "src/silent.py", """
def f(x):
    try:
        do(x)
    except Exception:
        pass
""")
        data = json.loads(run_tool("ast-error-handling", str(tmp_path))[0])
        assert data["anti_patterns"] == ["src/silent.py:5::broad_swallow"]

    def test_narrow_except_pass_is_deliberate_idiom(self, tmp_path):
        _write(tmp_path, "src/narrow.py", """
import os

def cleanup(path):
    try:
        os.remove(path)
    except FileNotFoundError:
        pass
""")
        data = json.loads(run_tool("ast-error-handling", str(tmp_path))[0])
        assert data["anti_patterns"] == []

    def test_tuple_containing_base_exception_flagged(self, tmp_path):
        _write(tmp_path, "src/tup.py", """
def f(x):
    try:
        do(x)
    except (ValueError, BaseException):
        log(x)
""")
        data = json.loads(run_tool("ast-error-handling", str(tmp_path))[0])
        assert data["anti_patterns"] == ["src/tup.py:5::except_base_exception"]

    def test_pragma_exempt_file_contributes_no_anti_patterns(self, tmp_path):
        _write(tmp_path, "src/exempt.py", """
# pragma: no error-handling
def f(x):
    try:
        do(x)
    except BaseException:
        pass
""")
        data = json.loads(run_tool("ast-error-handling", str(tmp_path))[0])
        assert data["exempt_count"] == 1
        assert data["anti_patterns"] == []


class TestJsAntiPatterns:
    @pytest.fixture(autouse=True)
    def _need_tree_sitter(self):
        pytest.importorskip("tree_sitter")

    def test_empty_catch_flagged(self, tmp_path):
        _write(tmp_path, "src/swallow.ts", """
export function f(a: string): string {
  try { return JSON.parse(a); } catch (e) {}
  return "";
}
""")
        out, rc = run_tool("js-error-handling", str(tmp_path))
        data = json.loads(out)
        assert data["with_handler"] == 1
        assert data["anti_patterns"] == ["src/swallow.ts:3::empty_catch"]
        assert compute_tool_score("js-error-handling", out, rc) == 95.0

    def test_comment_only_catch_is_still_empty(self, tmp_path):
        _write(tmp_path, "src/comment.ts", """
export function f(a: string): string {
  try { return JSON.parse(a); } catch { /* ignore */ }
  return "";
}
""")
        data = json.loads(run_tool("js-error-handling", str(tmp_path))[0])
        assert data["anti_patterns"] == ["src/comment.ts:3::empty_catch"]

    def test_real_catch_body_is_clean(self, tmp_path):
        _write(tmp_path, "src/real.ts", """
export function f(a: string): string {
  try { return JSON.parse(a); } catch (e) { return ""; }
}
""")
        data = json.loads(run_tool("js-error-handling", str(tmp_path))[0])
        assert data["anti_patterns"] == []


class TestScorerBackwardCompat:
    def test_pre_v29_output_without_anti_patterns_field(self):
        # Old scanner output (no anti_patterns key) must score as before.
        old = json.dumps({"total": 2, "with_handler": 1, "no_handler": ["a.py"],
                          "exempt_count": 0, "exempt_files": []})
        assert compute_tool_score("ast-error-handling", old, 0) == 50.0

    def test_deduction_floors_at_zero(self):
        out = json.dumps({"total": 1, "with_handler": 1,
                          "anti_patterns": [f"f.py:{i}::bare_except" for i in range(30)]})
        assert compute_tool_score("ast-error-handling", out, 0) == 0.0
