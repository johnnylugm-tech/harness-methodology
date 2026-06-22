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
from typing import Callable, Optional

from harness.lang_scanners import RUNNERS as _SCANNER_RUNNERS
from harness.toolchains import get_tool_spec

# Separator between subprocess stdout/stderr and an appended output_artifact
# file (ToolSpec.output_artifact) — scorers split on it to get the report JSON.
_ARTIFACT_MARKER = "\n=== TOOL_OUTPUT_ARTIFACT ===\n"


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
    spec = get_tool_spec(tool)
    if spec is None or spec.skip_inline:
        return "", -1  # Unknown or skip-list tool

    root = str(project_root)

    if spec.in_process:
        runner = _IN_PROCESS_RUNNERS.get(tool)
        if runner is None:
            return "", -1
        return runner(root)

    timeout = timeout_override if timeout_override is not None else spec.timeout

    import os

    if spec.required_config_file and not os.path.isfile(
        os.path.join(root, spec.required_config_file)
    ):
        return (f"Skipped: {spec.required_config_file} not found in project root", 0)

    test_target = root
    if os.path.isdir(os.path.join(root, "03-development", "tests")):
        test_target = os.path.join(root, "03-development", "tests")
    elif os.path.isdir(os.path.join(root, "tests")):
        test_target = os.path.join(root, "tests")

    if spec.cmd is None:
        return "", -1

    cmd = [part.format(root=root, test_target=test_target) for part in spec.cmd]

    # Clear a stale report file before the run: if the tool crashes before
    # rewriting it, the scorer must not read a previous run's artifact as if it
    # were current (coverage-summary.json staleness).
    artifact = os.path.join(root, spec.output_artifact) if spec.output_artifact else None
    if artifact and os.path.isfile(artifact):
        try:
            os.remove(artifact)
        except OSError:
            pass  # Best-effort; a non-removable stale file is a pre-existing problem.

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=root,
        )
        combined = (proc.stdout + proc.stderr).strip()
        if artifact and os.path.isfile(artifact):
            try:
                with open(artifact, encoding="utf-8", errors="replace") as fh:
                    combined += _ARTIFACT_MARKER + fh.read()
            except OSError:
                pass  # Artifact unreadable — scorer falls back to stdout
        return combined, proc.returncode
    except subprocess.TimeoutExpired:
        return f"TIMEOUT: {tool} exceeded {timeout}s", -2
    except FileNotFoundError:
        return f"Tool not found: {tool}", -3
    except Exception as exc:  # pylint: disable=broad-except
        return f"Error running {tool}: {exc}", -4


# In-process scanners (ToolSpec.in_process=True) live in
# harness/lang_scanners/ — python_ast.py (ast module) and
# treesitter_js.py (pinned tree-sitter grammars). Same output schema per
# dimension across languages, so the scorers below serve both.
_IN_PROCESS_RUNNERS: dict[str, Callable[[str], tuple[str, int]]] = _SCANNER_RUNNERS


def compute_tool_score(tool: str, output: str, returncode: int) -> Optional[float]:
    """Compute a 0-100 score from *tool* output.

    Returns None when the score cannot be determined (tool skipped / timed out /
    not found / unknown tool).
    """
    if returncode < 0:
        return None  # Harness-internal codes — cannot score

    spec = get_tool_spec(tool)
    fn = _SCORERS.get(spec.scorer) if spec and spec.scorer else None
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
        # No analysable file → None (NOT a free 100). A passing readability score
        # with nothing to analyse is unverifiable; cross-validation blocks it.
        return round(sum(mis) / len(mis), 1) if mis else None
    except (_json.JSONDecodeError, ValueError):
        return None  # Tool crash / non-JSON stderr — cannot score




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
    """Score ast-error-handling output.

    score = 100 × (files_with_handler / total_files) − 5 × anti_patterns

    anti_patterns (broad_swallow / except_base_exception / bare_except /
    empty_catch) are handlers that exist but undermine resilience — presence
    of a try/except is no longer automatically positive (a file catching
    BaseException previously scored as fully handled; tts-new shipped a
    Critical bug that way). −5 per finding matches the type-error curve.

    total == 0 (no source files with code) → 100.0 — nothing to handle is not
    a failure.  JSON parse failure → None.
    """
    import json as _json
    try:
        data = _json.loads(output)
        total = int(data.get("total", 0))
        with_handler = int(data.get("with_handler", 0))
        anti_count = len(data.get("anti_patterns", []) or [])
    except (_json.JSONDecodeError, ValueError, TypeError):
        return None
    if total == 0:
        return 100.0
    base = 100.0 * with_handler / total
    return round(max(0.0, base - 5.0 * anti_count), 1)


def _score_docstring_coverage(output: str, _returncode: int) -> Optional[float]:
    """Score ast-docstrings output.  100 × (public_with_docstring / total_public).

    total == 0 (no public API) → 100.0 — nothing to document is not a failure.
    JSON parse failure → None.
    """
    import json as _json
    try:
        data = _json.loads(output)
        total = int(data.get("total", 0))
        with_doc = int(data.get("with_doc", 0))
    except (_json.JSONDecodeError, ValueError, TypeError):
        return None
    if total == 0:
        return 100.0
    return round(100.0 * with_doc / total, 1)


def _score_eslint(output: str, _returncode: int) -> Optional[float]:
    """Score eslint -f json output.  Each error/warning costs 2 pts (ruff parity).

    eslint JSON is a list of per-file results carrying errorCount/warningCount.
    Parse failure → None (tool crash — never a silent 100).
    """
    import json as _json
    try:
        results = _json.loads(output)
        if not isinstance(results, list):
            return None
        count = sum(
            int(r.get("errorCount", 0)) + int(r.get("warningCount", 0))
            for r in results if isinstance(r, dict)
        )
    except (_json.JSONDecodeError, ValueError, TypeError):
        return None
    return max(0.0, 100.0 - count * 2.0)


def _score_tsc(output: str, _returncode: int) -> float:
    """Score tsc --noEmit --pretty false output.  Each `error TSxxxx` costs 5 pts.

    Clean compile emits nothing → 100. Config failures (e.g. missing
    tsconfig.checkjs.json) also print `error TSxxxx` lines and are counted —
    mypy/pyright parity.
    """
    errors = len(re.findall(r"\berror TS\d+:", output))
    return max(0.0, 100.0 - errors * 5.0)


def _score_semgrep(output: str, _returncode: int) -> Optional[float]:
    """Score semgrep --json output.  ERROR=−10, WARNING=−3, INFO=−1 (bandit parity).

    Parse failure → None.
    """
    import json as _json
    try:
        data = _json.loads(output)
        results = data.get("results", [])
        sev = [str(r.get("extra", {}).get("severity", "")).upper() for r in results]
        high   = sum(1 for s in sev if s == "ERROR")
        medium = sum(1 for s in sev if s == "WARNING")
        low    = sum(1 for s in sev if s == "INFO")
        return max(0.0, 100.0 - high * 10.0 - medium * 3.0 - low * 1.0)
    except (_json.JSONDecodeError, ValueError, AttributeError):
        return None


def _score_coverage_summary(output: str, _returncode: int) -> Optional[float]:
    """Score istanbul/v8 json-summary coverage (vitest/jest).

    The coverage-summary.json artifact is appended to the output after the
    marker (ToolSpec.output_artifact); return total.lines.pct from it.
    Artifact absent → 0.0: the suite failed before writing coverage, so a
    passing coverage claim is unverifiable (blocks, pytest-cov-integration
    parity). Artifact unparseable → None (tool crash).
    """
    import json as _json
    if _ARTIFACT_MARKER not in output:
        return 0.0
    artifact = output.rsplit(_ARTIFACT_MARKER, 1)[1]
    try:
        data = _json.loads(artifact)
        pct = data["total"]["lines"]["pct"]
        return round(float(pct), 1)
    except (_json.JSONDecodeError, ValueError, TypeError, KeyError):
        return None


def _score_js_bench(output: str, _returncode: int) -> Optional[float]:
    """Score `node benchmarks/run.mjs` normalized output.

    Expects {"benchmarks": [{"name": ..., "mean_ms": ...}]} on stdout
    (templates/js_toolchain/benchmarks/run.mjs). Thresholds match
    pytest-benchmark: mean > 3000 ms → −50/benchmark, > 1000 ms → −25.

    No benchmarks registered (empty list, or run.mjs absent →
    Cannot find module / ENOENT) → None — the dimension is not yet applicable,
    exactly like pytest-benchmark collecting nothing (exit 5). This denies the
    free 100 a stub/empty benchmark file would otherwise grant.
    """
    import json as _json
    if re.search(r"Cannot find module|ENOENT", output):
        return None
    try:
        data = _json.loads(output)
        benches = data.get("benchmarks", [])
    except (_json.JSONDecodeError, ValueError, AttributeError):
        return None
    if not benches:
        return None  # No benchmarks registered — N/A, not a pass.
    score = 100.0
    for b in benches:
        try:
            mean_ms = float(b.get("mean_ms", 0))
        except (TypeError, ValueError):
            continue
        if mean_ms > 3000.0:
            score -= 50.0
        elif mean_ms > 1000.0:
            score -= 25.0
    return max(0.0, score)


def _score_exit_code_binary(output: str, returncode: int) -> float:
    """Score based entirely on exit code (0 -> 100, else 0)."""
    if returncode == 0:
        return 100.0
    return 0.0

def _score_mutmut(output: str, returncode: int) -> float:
    """Score mutmut run output."""
    killed_m = re.search(r"Killed: (\d+)", output)
    survived_m = re.search(r"Survived: (\d+)", output)
    timeout_m = re.search(r"Timeout: (\d+)", output)
    killed = int(killed_m.group(1)) if killed_m else 0
    survived = int(survived_m.group(1)) if survived_m else 0
    # Timed-out mutants were not killed; count them as survived so the
    # score reflects the true kill rate (excluding them inflates the score).
    timeout = int(timeout_m.group(1)) if timeout_m else 0
    total = killed + survived + timeout
    if total == 0:
        return 0.0
    return round(100.0 * killed / total, 1)


# Scorer functions keyed by ToolSpec.scorer id (toolchain registry). Multiple
# tools may share one scorer (e.g. eslint-family tools added per language).
_SCORERS: dict[str, Callable[[str, int], Optional[float]]] = {
    "ruff":             _score_ruff,
    "mypy":             _score_mypy,
    "pyright":          _score_pyright,
    "pytest-cov":       lambda o, _rc: _score_pytest(o, coverage=True),
    "pytest":           lambda o, _rc: _score_pytest(o, coverage=False),
    "gitleaks":         _score_gitleaks,
    "bandit":           _score_bandit,
    "radon-cc":         _score_radon_cc,
    "radon-mi":         _score_radon_mi,
    "pytest-benchmark": _score_pytest_benchmark,
    "ast-assertions":   _score_assertion_quality,
    "ast-error-handling": _score_error_handling_coverage,
    "ast-docstrings":   _score_docstring_coverage,
    "pytest-cov-integration": lambda o, _rc: _score_pytest(o, coverage=True),
    # JS/TS toolchain
    "eslint":           _score_eslint,
    "tsc":              _score_tsc,
    "semgrep":          _score_semgrep,
    "coverage-summary": _score_coverage_summary,
    "js-bench":         _score_js_bench,
    "exit-code-binary": _score_exit_code_binary,
    "mutmut":           _score_mutmut,
}
