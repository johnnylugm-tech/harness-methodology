"""
harness/tool_runners.py — Solution B: independent tool execution for cross-validation.

Each tool is run by the harness (not the agent) so scores cannot be fabricated by
writing stub files.  Results are written to .sessi-work/harness_verification/ as an
audit trail.

API
───
  run_tool(tool, project_root, *, timeout_override=None) → (output: str, returncode: int)
  compute_tool_score(tool, output, returncode) → float | None

Return-code conventions (negative = harness-internal, not tool exit codes):
  -1  tool is on the skip list (mutmut, scancode) — too slow / complex
  -2  subprocess timed out
  -3  tool executable not found
  -4  unexpected subprocess error
"""

from __future__ import annotations

import re
import subprocess
from typing import Optional

# Tools that are too slow or complex for inline cross-validation.
# They are still covered by Solution-A content validation.
_SKIP_TOOLS: frozenset[str] = frozenset({"mutmut", "scancode"})

# Default per-tool timeouts (seconds).
_DEFAULT_TIMEOUTS: dict[str, int] = {
    "ruff":             30,
    "mypy":             60,
    "pyright":          60,
    "pytest-cov":       120,
    "pytest":           120,
    "gitleaks":         30,
    "bandit":           60,
    "radon-cc":         30,
    "radon-cc-high":    30,
    "radon-mi":         30,
    "pydocstyle":       30,
    "grep-bare-except": 15,
    "pytest-benchmark": 180,
    "ast-assertions":   30,
    "ast-error-handling": 30,
    "pytest-cov-integration": 180,
}

# Source directories scanned for file-level error-handling coverage.
_SRC_DIRS: tuple[str, ...] = ("03-development/src", "src")


def run_tool(
    tool: str,
    project_root: str,
    *,
    timeout_override: Optional[int] = None,
) -> tuple[str, int]:
    """Execute *tool* against *project_root* and return (stdout+stderr, returncode).

    Returns (message, negative_code) when the tool is skipped/unavailable:
      ("", -1)  — skipped tool
      ("TIMEOUT…", -2) — subprocess timed out
      ("Tool not found…", -3) — executable missing
      ("Error: …", -4) — unexpected exception
    """
    if tool in _SKIP_TOOLS:
        return "", -1

    # ast-assertions / ast-error-handling are computed in-process (AST scan).
    if tool == "ast-assertions":
        return _run_ast_assertions(str(project_root))
    if tool == "ast-error-handling":
        return _run_ast_error_handling(str(project_root))

    timeout = timeout_override if timeout_override is not None else _DEFAULT_TIMEOUTS.get(tool, 30)
    root = str(project_root)

    # Build command per tool.
    cmds: dict[str, list[str]] = {
        "ruff": [
            "ruff", "check", root,
            "--output-format", "json",
            "--exit-zero",
        ],
        "mypy": [
            "mypy", root,
            "--ignore-missing-imports",
            "--no-color-output",
            "--no-error-summary",
        ],
        "pyright": [
            "pyright", root,
            "--outputjson",
        ],
        "pytest-cov": [
            "pytest", root,
            "--cov", "--cov-report=term-missing",
            "-q", "--tb=no", "--no-header",
        ],
        "pytest": [
            "pytest", root,
            "-q", "--tb=no", "--no-header",
        ],
        "gitleaks": [
            "gitleaks", "detect",
            "--source", root,
            # No --exit-code override: exit 0 = clean, exit 1 = leaks found.
            # Overriding to 0 would make the scorer always return 100.
        ],
        "bandit": [
            "bandit", "-r", root,
            "-f", "json",
            "--exit-zero",  # always exit 0 so returncode doesn't mask JSON output
        ],
        "radon-cc": [
            "radon", "cc", root,
            "-j",           # JSON output
            "--min", "A",   # include all grades (A-F)
        ],
        "radon-cc-high": [
            "radon", "cc", root,
            "-j",           # JSON output
            "--min", "D",   # grade D+ only (CC ≥ 16) — performance hot-path focus
        ],
        "radon-mi": [
            "radon", "mi", root,
            "-j",           # JSON output
        ],
        "pydocstyle": [
            "pydocstyle", root,
            "--count",
        ],
        "grep-bare-except": [
            "grep", "-rn", "--include=*.py",
            r"except\s*:",
            root,
        ],
        # --benchmark-only: run only tests using the `benchmark` fixture.
        # If none exist, pytest exits with code 5 (no tests collected) → scorer returns None.
        # Text output (not --benchmark-json) so results flow through stdout capture.
        "pytest-benchmark": [
            "pytest", root,
            "--benchmark-only",
            "--benchmark-disable-gc",
            "--benchmark-columns", "mean,max",
            "--tb", "no",
            "-q",
        ],
        # Integration coverage: run only the integration suite and measure real
        # line coverage of the source tree (NOT pass-rate).  Missing suite →
        # pytest exits 4/5 and the cov table is absent → _score_pytest returns 0
        # → cross-validation blocks (a passing agent score is then unverifiable).
        "pytest-cov-integration": [
            "pytest", "03-development/tests/integration",
            "--cov=03-development/src", "--cov-report=term-missing",
            "-q", "--tb=no", "--no-header",
        ],
    }

    cmd = cmds.get(tool)
    if not cmd:
        return "", -1  # Unknown tool — skip

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=root,
        )
        combined = (proc.stdout + proc.stderr).strip()
        return combined, proc.returncode
    except subprocess.TimeoutExpired:
        return f"TIMEOUT: {tool} exceeded {timeout}s", -2
    except FileNotFoundError:
        return f"Tool not found: {tool}", -3
    except Exception as exc:  # pylint: disable=broad-except
        return f"Error running {tool}: {exc}", -4


# Test directories scanned for assertion-quality analysis (first existing wins is
# NOT used — all are scanned and merged).
_TEST_DIRS: tuple[str, ...] = ("tests", "03-development/tests")


def _function_has_assertion(node: "object") -> bool:
    """True if a (async)FunctionDef body contains a substantive assertion.

    Recognises: bare `assert`, unittest `self.assertXxx(...)` / `self.fail()`,
    `pytest.raises`/`pytest.warns` (call or `with` context manager), numpy
    `np.testing.assert_*`, and bare `raises(...)`/`warns(...)` imports.
    """
    import ast as _ast

    for sub in _ast.walk(node):  # type: ignore[arg-type]
        if isinstance(sub, _ast.Assert):
            return True
        if isinstance(sub, _ast.Call):
            fn = sub.func
            if isinstance(fn, _ast.Attribute):
                name = fn.attr
                if name.startswith("assert") or name in ("fail", "raises", "warns"):
                    return True
            elif isinstance(fn, _ast.Name):
                if fn.id in ("raises", "warns"):
                    return True
    return False


def _run_ast_assertions(project_root: str) -> tuple[str, int]:
    """Scan test files and report assertion coverage of test functions.

    Returns (json_summary, 0) where summary = {total, asserted, zero_assert:[...]}.
    A test function with NO substantive assertion (a "pass-and-still-green" shell)
    is counted as zero_assert.  This is what test_assertion_quality means —
    pytest pass-rate cannot detect it.
    """
    import ast as _ast
    import json as _json
    from pathlib import Path as _Path

    root = _Path(project_root)
    total = 0
    asserted = 0
    zero_assert: list[str] = []

    seen: set[str] = set()
    for rel in _TEST_DIRS:
        base = root / rel
        if not base.is_dir():
            continue
        for path in base.rglob("test_*.py"):
            key = str(path.resolve())
            if key in seen:
                continue
            seen.add(key)
            try:
                tree = _ast.parse(path.read_text(encoding="utf-8", errors="replace"))
            except (SyntaxError, ValueError, OSError):
                continue
            for fn in _ast.walk(tree):
                if isinstance(fn, (_ast.FunctionDef, _ast.AsyncFunctionDef)) and fn.name.startswith("test"):
                    total += 1
                    if _function_has_assertion(fn):
                        asserted += 1
                    else:
                        zero_assert.append(f"{path.relative_to(root)}::{fn.name}")

    summary = {"total": total, "asserted": asserted, "zero_assert": zero_assert[:50]}
    return _json.dumps(summary), 0


def _run_ast_error_handling(project_root: str) -> tuple[str, int]:
    """Scan source files and report file-level error-handling coverage.

    Returns (json_summary, 0) where summary = {total, with_handler, no_handler:[...]}.
    A source file counts as "handled" if it contains at least one try/except block
    with a real handler.  This is a framework-owned, independently reproducible
    measure of error_handling — it replaces the CRG flow path whose has_error_handler
    field does not exist in the installed code-review-graph package.
    """
    import ast as _ast
    import json as _json
    from pathlib import Path as _Path

    root = _Path(project_root)
    total = 0
    with_handler = 0
    no_handler: list[str] = []

    seen: set[str] = set()
    for rel in _SRC_DIRS:
        base = root / rel
        if not base.is_dir():
            continue
        for path in base.rglob("*.py"):
            key = str(path.resolve())
            if key in seen:
                continue
            seen.add(key)
            try:
                tree = _ast.parse(path.read_text(encoding="utf-8", errors="replace"))
            except (SyntaxError, ValueError, OSError):
                continue
            # Skip files with no functions/classes (e.g. empty __init__.py) — they
            # have nothing to handle and would unfairly dilute the ratio.
            has_code = any(
                isinstance(n, (_ast.FunctionDef, _ast.AsyncFunctionDef, _ast.ClassDef))
                for n in _ast.walk(tree)
            )
            if not has_code:
                continue
            total += 1
            handled = any(
                isinstance(n, _ast.Try) and n.handlers
                for n in _ast.walk(tree)
            )
            if handled:
                with_handler += 1
            else:
                no_handler.append(str(path.relative_to(root)))

    summary = {"total": total, "with_handler": with_handler, "no_handler": no_handler[:50]}
    return _json.dumps(summary), 0


def compute_tool_score(tool: str, output: str, returncode: int) -> Optional[float]:
    """Compute a 0-100 score from *tool* output.

    Returns None when the score cannot be determined (tool skipped / timed out /
    not found / unknown tool).
    """
    if returncode < 0:
        return None  # Harness-internal codes — cannot score

    scorers = {
        "ruff":             _score_ruff,
        "mypy":             _score_mypy,
        "pyright":          _score_pyright,
        "pytest-cov":       lambda o, _rc: _score_pytest(o, coverage=True),
        "pytest":           lambda o, _rc: _score_pytest(o, coverage=False),
        "gitleaks":         _score_gitleaks,
        "bandit":           _score_bandit,
        "radon-cc":         _score_radon_cc,
        "radon-cc-high":    _score_radon_cc_high,
        "radon-mi":         _score_radon_mi,
        "pydocstyle":       _score_pydocstyle,
        "grep-bare-except": _score_grep_bare_except,
        "pytest-benchmark": _score_pytest_benchmark,
        "ast-assertions":   _score_assertion_quality,
        "ast-error-handling": _score_error_handling_coverage,
        "pytest-cov-integration": lambda o, _rc: _score_pytest(o, coverage=True),
    }
    fn = scorers.get(tool)
    return fn(output, returncode) if fn else None


# ---------------------------------------------------------------------------
# Per-tool scoring helpers
# ---------------------------------------------------------------------------

def _score_ruff(output: str, _returncode: int) -> float:
    """Score ruff JSON output.  0 violations → 100; each violation costs 2 pts."""
    import json as _json
    try:
        violations = _json.loads(output)
        count = len(violations) if isinstance(violations, list) else 0
    except (_json.JSONDecodeError, ValueError):
        # Fall back to counting text-format lines (file:line:col:)
        count = len(re.findall(r"^\S+\.(py|pyi):\d+:\d+:", output, re.MULTILINE))

    if count == 0:
        return 100.0
    return max(0.0, 100.0 - count * 2.0)


def _score_mypy(output: str, _returncode: int) -> float:
    """Score mypy text output.  0 errors → 100; each error costs 5 pts."""
    if "Success: no issues found" in output:
        return 100.0
    errors = len(re.findall(r":\s*error:", output))
    if errors == 0:
        return 100.0
    return max(0.0, 100.0 - errors * 5.0)


def _score_pytest(output: str, *, coverage: bool) -> float:
    """Score pytest (and pytest-cov) output.

    With coverage=True: return total line-coverage percentage.
    With coverage=False: return 100 × (passed / total).
    """
    if coverage:
        m = re.search(r"TOTAL\s+(?:\d+\s+){2,}(\d+)%", output)
        if m:
            return float(m.group(1))
        # Fallback to pass-rate if coverage table absent
    passed_m = re.search(r"(\d+) passed", output)
    failed_m = re.search(r"(\d+) failed", output)
    passed = int(passed_m.group(1)) if passed_m else 0
    failed = int(failed_m.group(1)) if failed_m else 0
    total = passed + failed
    if total == 0:
        return 0.0
    return round(100.0 * passed / total, 1)


def _score_gitleaks(output: str, returncode: int) -> float:
    """Score gitleaks output.  No leaks → 100; any leaks → 0."""
    if "No leaks found" in output or returncode == 0:
        return 100.0
    return 0.0


def _score_pyright(output: str, _returncode: int) -> float:
    """Score pyright --outputjson.  0 errors → 100; each error costs 5 pts."""
    import json as _json
    try:
        data = _json.loads(output)
        errors = data.get("summary", {}).get("errorCount", 0)
    except (_json.JSONDecodeError, ValueError):
        # Fall back to counting text-format "error:" lines.
        errors = len(re.findall(r"\berror:", output))
    return max(0.0, 100.0 - errors * 5.0)


def _score_bandit(output: str, _returncode: int) -> float:
    """Score bandit -f json.  HIGH=−10, MEDIUM=−3, LOW=−1 per issue."""
    import json as _json
    try:
        data = _json.loads(output)
        results = data.get("results", [])
        high   = sum(1 for r in results if r.get("issue_severity") == "HIGH")
        medium = sum(1 for r in results if r.get("issue_severity") == "MEDIUM")
        low    = sum(1 for r in results if r.get("issue_severity") == "LOW")
        return max(0.0, 100.0 - high * 10.0 - medium * 3.0 - low * 1.0)
    except (_json.JSONDecodeError, ValueError):
        return 0.0


def _score_radon_cc(output: str, _returncode: int) -> Optional[float]:
    """Score radon cc -j.  Functions with CC > 10 (grade C+) each cost 5 pts.

    Returns None on JSON parse failure so compute_tool_score propagates None
    rather than silently awarding 100 for a tool crash.
    """
    import json as _json
    try:
        data = _json.loads(output)
        # radon cc -j: {"file.py": [{"complexity": N, "type": "function", ...}, ...]}
        complex_count = sum(
            1
            for entries in data.values() if isinstance(entries, list)
            for entry in entries
            if isinstance(entry, dict) and entry.get("complexity", 0) > 10
        )
        return max(0.0, 100.0 - complex_count * 5.0)
    except (_json.JSONDecodeError, ValueError):
        return None  # Tool crash / non-JSON stderr — cannot score


def _score_radon_cc_high(output: str, _returncode: int) -> Optional[float]:
    """Score radon cc -j --min D.  Functions with CC > 15 (grade D+) each cost 10 pts.

    Used for the *performance* dimension to focus on severe hot-path complexity.
    Returns None on JSON parse failure.
    """
    import json as _json
    try:
        data = _json.loads(output)
        # radon cc -j --min D: {"file.py": [{"complexity": N, ...}, ...]}
        # All returned entries have CC ≥ 16; filter defensively.
        complex_count = sum(
            1
            for entries in data.values() if isinstance(entries, list)
            for entry in entries
            if isinstance(entry, dict) and entry.get("complexity", 0) > 15
        )
        return max(0.0, 100.0 - complex_count * 10.0)
    except (_json.JSONDecodeError, ValueError):
        return None  # Tool crash / non-JSON stderr — cannot score


def _score_radon_mi(output: str, _returncode: int) -> Optional[float]:
    """Score radon mi -j.  Average Maintainability Index across all files (0-100).

    Returns None on JSON parse failure so compute_tool_score propagates None
    rather than silently awarding 100 for a tool crash.
    """
    import json as _json
    try:
        data = _json.loads(output)
        # radon mi -j: {"file.py": {"mi": 80.5, "rank": "A"}}
        mis = [
            v["mi"]
            for v in data.values()
            if isinstance(v, dict) and isinstance(v.get("mi"), (int, float))
        ]
        return round(sum(mis) / len(mis), 1) if mis else 100.0
    except (_json.JSONDecodeError, ValueError):
        return None  # Tool crash / non-JSON stderr — cannot score


def _score_pydocstyle(output: str, _returncode: int) -> float:
    """Score pydocstyle --count.  Each violation costs 2 pts."""
    # --count appends a final line like "42 violations found"
    m = re.search(r"(\d+)\s+violation", output)
    count = int(m.group(1)) if m else len(re.findall(r":\s*D\d{3}", output))
    return max(0.0, 100.0 - count * 2.0)


def _score_grep_bare_except(output: str, _returncode: int) -> float:
    """Score grep-bare-except.  Each matching line costs 5 pts."""
    count = len(output.strip().splitlines()) if output.strip() else 0
    return max(0.0, 100.0 - count * 5.0)


def _score_pytest_benchmark(output: str, returncode: int) -> Optional[float]:
    """Score pytest-benchmark text output.

    Exit code 5 means no benchmark tests collected → return None (dimension skipped).
    Otherwise parse the benchmark table for mean latencies and penalise slow benchmarks.

    Thresholds (cross-validation heuristics, not NFR targets):
      mean > 3000 ms → -50 pts per benchmark  (hard fail, clearly exceeds NFR-01 target)
      mean > 1000 ms → -25 pts per benchmark  (warning zone)
      All within 1000 ms → 100
    """
    if returncode == 5:
        return None  # No benchmark tests exist yet — dimension not yet applicable

    # Parse the unit multiplier from the header line:  "Name (time in ms)"
    unit_m = re.search(r"Name\s+\(time\s+in\s+(ms|us|ns|s)\)", output, re.IGNORECASE)
    if unit_m:
        unit = unit_m.group(1).lower()
        to_ms = {"ms": 1.0, "us": 0.001, "ns": 0.000001, "s": 1000.0}.get(unit, 1.0)
    else:
        to_ms = 1.0  # Assume ms if header not found

    # Each data row: "  test_name   <mean_val>   <max_val>"
    # --benchmark-columns mean,max produces exactly those two numeric columns.
    row_re = re.compile(
        r"^\s*(test_\S+)\s+([\d.]+(?:e[+-]?\d+)?)\s+([\d.]+(?:e[+-]?\d+)?)\s*$",
        re.MULTILINE,
    )
    score = 100.0
    for m in row_re.finditer(output):
        mean_ms = float(m.group(2)) * to_ms
        if mean_ms > 3000.0:
            score -= 50.0
        elif mean_ms > 1000.0:
            score -= 25.0
    return max(0.0, score)


def _score_assertion_quality(output: str, _returncode: int) -> Optional[float]:
    """Score ast-assertions output.  100 × (asserted / total).

    total == 0 (no test functions at all) → 0.0 — a project claiming a passing
    assertion-quality score with zero tests is a fabrication.  JSON parse failure
    → None (treat as tool error, do not silently award a score).
    """
    import json as _json
    try:
        data = _json.loads(output)
        total = int(data.get("total", 0))
        asserted = int(data.get("asserted", 0))
    except (_json.JSONDecodeError, ValueError, TypeError):
        return None
    if total == 0:
        return 0.0
    return round(100.0 * asserted / total, 1)


def _score_error_handling_coverage(output: str, _returncode: int) -> Optional[float]:
    """Score ast-error-handling output.  100 × (files_with_handler / total_files).

    total == 0 (no source files with code) → 100.0 — nothing to handle is not a
    failure.  JSON parse failure → None.
    """
    import json as _json
    try:
        data = _json.loads(output)
        total = int(data.get("total", 0))
        with_handler = int(data.get("with_handler", 0))
    except (_json.JSONDecodeError, ValueError, TypeError):
        return None
    if total == 0:
        return 100.0
    return round(100.0 * with_handler / total, 1)
