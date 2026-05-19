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
    "ruff":       30,
    "mypy":       60,
    "pytest-cov": 120,
    "pytest":     120,
    "gitleaks":   30,
}


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


def compute_tool_score(tool: str, output: str, returncode: int) -> Optional[float]:
    """Compute a 0-100 score from *tool* output.

    Returns None when the score cannot be determined (tool skipped / timed out /
    not found / unknown tool).
    """
    if returncode < 0:
        return None  # Harness-internal codes — cannot score

    scorers = {
        "ruff":       _score_ruff,
        "mypy":       _score_mypy,
        "pytest-cov": lambda o, _rc: _score_pytest(o, coverage=True),
        "pytest":     lambda o, _rc: _score_pytest(o, coverage=False),
        "gitleaks":   _score_gitleaks,
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
