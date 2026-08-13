"""
harness/tool_runners.py — Solution B: independent tool execution for cross-validation.

Each tool is run by the harness (not the agent) so scores cannot be fabricated by
writing stub files.  Results are written to the audit directory
core.evidence_retention.cited_evidence_dir names, under
.methodology/gate_evidence/, so the audit trail outlives the phase that
produced it (Round 50 站6).

API
───
  run_tool(tool, project_root, *, timeout_override=None) → (output: str, returncode: int)
  compute_tool_score(tool, output, returncode) → float | None

Return-code conventions (negative = harness-internal, not tool exit codes):
  -1  tool is on the skip list (mutmut, scancode) — too slow / complex
  -2  subprocess timed out
  -3  tool executable not found
  -4  unexpected subprocess error
  -5  required config file missing — tool did not run, NOT a pass
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


def _resolve_src_targets(root: str, cov_target: str) -> list:
    """The directories a source scanner should be pointed at.

    Round 31 站6. S4 exists to re-run the tool the agent ran and compare the
    two scores — which only means anything if both sides look at the same
    files. The specs said ``{root}``. Measured on the project this round came
    from: 4917 .py files under the root (4344 of them a committed .venv, 537
    the vendored harness) against 21 files of actual source, while
    evaluate_dimension.md tells the agent to scan ``src/``. pyright timed out
    at 60s and S4 reported the timeout as tool_score_fabrication with "Install
    'pyright'" — for a tool that was installed. The harness's own inability to
    measure became an accusation against the measured party.

    Falls back to the project root when nothing resolves, and records that in
    the degradation ledger. A silent fallback would leave the fix looking
    applied on exactly the layouts it does not work for — the shape Round 29's
    station 2 shipped.
    """
    import os as _os

    candidates = [c for c in (cov_target or "").split() if c not in ("", ".")]
    resolved = [
        _os.path.join(root, c) for c in candidates
        if _os.path.isdir(_os.path.join(root, c))
    ]
    if resolved:
        return resolved

    from core.degradation_ledger import record_degradation
    record_degradation(
        root, "gate:scan-scope",
        f"no source directory resolved (coverage target {cov_target!r}); "
        f"source scanners fall back to the whole project root",
        why=("the harness then scans dependencies and any vendored harness "
             "alongside the project's own code, which is neither what the "
             "agent scanned nor what the score is supposed to describe"), owner="harness"
    )
    return [root]


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
      ("Skipped: …", -5) — required config file missing, tool did not run
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
        # Bug fix: this used to return rc=0, which `exit-code-binary` (and any
        # other returncode-based scorer) reads as a full pass — a missing
        # required config silently scored 100 instead of being unscoreable.
        # Negative code routes through compute_tool_score's existing
        # `returncode < 0 → None` guard, consistent with -1/-2/-3/-4 above.
        return (f"Skipped: {spec.required_config_file} not found in project root", -5)

    if spec.cmd is None:
        return "", -1

    # cov_target: reuse the same resolution core.quality_gate.test_suite_run
    # already applies for gate1_evidence / FrameworkEnforcer /
    # PhaseTruthVerifier / cmd_advance_phase's TDD-PRECHECK — a bare `--cov`
    # (coverage's own "." default) pulls test files and any tmp fixtures the
    # test run creates into the denominator alongside real source, exactly
    # the bug test_suite_run.resolve_targets()'s own docstring documents
    # fixing once before ("--cov=. pulled harness_cli.py ... reported 95.98%
    # where the project's source is 100%"). Fall back to "." (today's
    # behavior, unchanged) when the resolved dir doesn't actually exist —
    # ToolSpec.cmd entries without a {cov_target} token are unaffected either
    # way.
    #
    # Round 32 站3: the test target comes from the same call. It used to be
    # re-derived immediately above by a hardcoded `03-development/tests` then
    # `tests` probe — a verbatim copy of ProjectLayout.active_test_dir's own
    # logic, ten lines from a call that had already computed it. Round 25's
    # module docstring names "four call sites each hand-rolling the argv" as
    # the defect it was written to end; this was the fifth.
    from core.quality_gate.test_suite_run import resolve_targets
    _test_candidate, _cov_candidate = resolve_targets(root)
    cov_target = _cov_candidate if os.path.isdir(os.path.join(root, _cov_candidate)) else "."
    test_target = (
        os.path.join(root, _test_candidate)
        if _test_candidate and os.path.isdir(os.path.join(root, _test_candidate))
        else root
    )

    # {src_target} expands to N argv entries, not one token (Round 31 站6).
    # resolve_targets' cov_target is a coverage SOURCE, which .coveragerc may
    # declare as several directories on separate lines; a single str.format
    # substitution would hand the tool one path made of all of them.
    src_targets = _resolve_src_targets(root, cov_target)

    cmd: list[str] = []
    for part in spec.cmd:
        if part == "{src_target}":
            cmd.extend(src_targets)
            continue
        cmd.append(
            part.format(root=root, test_target=test_target, cov_target=cov_target)
        )

    # Round 16: tools run with cwd=root but no PYTHONPATH, so `import
    # <package>` fails for any src-layout project (03-development/src/ or
    # src/) unless the calling shell happened to already have PYTHONPATH set —
    # a non-reproducible, environment-dependent gate score. That round fixed
    # it for pytest alone, with the condition `cmd[0] == "pytest"`.
    #
    # Round 32 站3: PYTHONPATH is not pytest's requirement, it is "where this
    # project's package lives". Binding the injection to one tool declared
    # that only pytest needs to import the project. import-linter does too,
    # and reproduced on a src-layout fixture:
    #
    #   no PYTHONPATH   Could not find package 'probeapp'   rc=1  ->   0.0
    #   with PYTHONPATH Contracts: 1 kept, 0 broken         rc=0  -> 100.0
    #
    # S4 reported that 0.0 as the agent fabricating architecture_constraints
    # on a live P4 Gate 1. Every subprocess now gets the same env, resolved
    # once from ProjectLayout.active_src_dir (the same source
    # test_suite_run.resolve_targets uses).
    #
    # Measured per tool before/after on a fixture with a real intra-package
    # import: ruff / mypy / bandit / radon-cc / radon-mi / readability-v2 /
    # gitleaks unchanged; pyright 95.0 -> 100.0 (an unresolved-import
    # diagnostic disappears); import-linter and pytest-cov 0.0 -> 100.0.
    # Every score that moves, moves UP, and each rise removes a false negative
    # the harness itself manufactured. Nothing is relaxed.
    #
    # An existing PYTHONPATH is extended, never replaced: a caller who set one
    # meant it.
    #
    # Bug #129/#128/#123 class, this call site: env used to start from
    # os.environ.copy() (or None — fully inherited — when the project had no
    # src dir), so a bare tool name like "pytest" resolved via whatever the
    # calling shell's ambient PATH said, not the target project's own venv.
    # On one machine that picked a stray Python-3.9-associated pytest ahead of
    # the project's 3.11 .venv/bin/pytest, which then failed to even collect
    # test files using PEP 604 `X | Y` syntax — S4 read that as "tool ran but
    # produced no readable score" (core.agent_spawner._child_env solved the
    # identical class of bug for spawned sub-agents; this reuses that pattern
    # via core.utils.venv_env instead of a third inline .venv probe).
    from pathlib import Path as _Path
    from core.utils.venv_env import venv_scoped_env
    env = venv_scoped_env(_Path(root))
    from core.utils.project_layout import ProjectLayout
    src_dir = ProjectLayout(root).active_src_dir
    if src_dir.is_dir():
        existing = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = (
            str(src_dir) if not existing
            else str(src_dir) + os.pathsep + existing
        )

    # Clear a stale report file before the run: if the tool crashes before
    # rewriting it, the scorer must not read a previous run's artifact as if it
    # were current (coverage-summary.json staleness).
    artifact = os.path.join(root, spec.output_artifact) if spec.output_artifact else None
    if artifact and os.path.isfile(artifact):
        try:
            os.remove(artifact)
        except OSError:
            pass  # Best-effort; a non-removable stale file is a pre-existing problem.
    if artifact:
        # The tool writes this file itself, but not all of them create the
        # directory first: pytest-benchmark's --benchmark-json opens the path
        # during argument parsing and exits 4 with a usage error if the parent
        # is missing (measured 2026-08-13). A tool that never ran looks exactly
        # like a tool that measured nothing, so the directory is made here
        # rather than left to each tool's own habits.
        try:
            os.makedirs(os.path.dirname(artifact), exist_ok=True)
        except OSError:
            pass  # Unwritable parent surfaces as the tool's own failure below.

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=root,
            env=env,
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


def _score_pytest(output: str, *, coverage: bool) -> Optional[float]:
    """Score pytest (and pytest-cov) output, or None when it carries no score.

    With coverage=True: return total line-coverage percentage.
    With coverage=False: return 100 × (passed / total).

    Round 32 站4: `total == 0` used to return 0.0, so a pytest run that never
    collected a test — exit 2 on a collection error, an import failure, a
    conftest that raised — was recorded as 0% coverage. On a live P4 Gate 1
    that produced

        test_coverage: fabrication detected — harness ran 'pytest-cov' and
        scored 0.0 (below threshold 80.0), but agent reported 100.0

    None is the value that already means "the harness could not re-score"
    everywhere else in this module (see the returncode < 0 guard in
    compute_tool_score, and _score_pytest_benchmark's exit-5 branch), and S4
    handles it correctly. A parse failure is not a measurement of zero
    (docs/ERROR_HANDLING.md, Round 31).
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
        return None
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


def _score_readability_v2(output: str, _returncode: int) -> Optional[float]:
    """Score readability_v2 (LLOC-weighted CC)."""
    import json as _json
    try:
        data = _json.loads(output)
        if "project_score" in data:
            return float(data["project_score"])
        return None
    except (_json.JSONDecodeError, ValueError):
        return None




def _score_pytest_benchmark(output: str, returncode: int) -> Optional[float]:
    """Score pytest-benchmark's --benchmark-json report.

    Returns None whenever nothing was measured. Otherwise read each
    benchmark's mean latency from the report and penalise the slow ones.

    Thresholds (cross-validation heuristics, not NFR targets):
      mean > 3000 ms → -50 pts per benchmark  (hard fail, clearly exceeds NFR-01 target)
      mean > 1000 ms → -25 pts per benchmark  (warning zone)
      All within 1000 ms → 100

    Round 50 站1: this used to parse the table pytest-benchmark prints for a
    human reader, and that parser only worked in the narrowest case. Measured
    against real runs of the exact command the registry issues:

        1 benchmark, values < 1000     parses
        2 benchmarks                   zero rows  (`4.7578 (1.0)` — a
                                       relative-multiplier column appears
                                       once there is something to compare to)
        any count, values >= 1000      zero rows  (`1,050.7090` — thousands
                                       separator)

    `--benchmark-columns` affects neither. So whether the framework could
    score this dimension depended on how many benchmarks the project had
    written: adding a second one took the ability away. Zero rows then took
    the correct Round 46 站3 branch ("a suite that measured nothing has not
    earned 100") for an incorrect reason, and the verdict fell back to the
    agent's own unverified number.

    The rendering is not the measurement. pytest-benchmark writes a
    machine-readable report on request, and `_score_coverage_summary` /
    `_score_js_bench` already read that shape for the JavaScript toolchain;
    this is the third consumer, not a new mechanism. There is deliberately no
    table fallback: a run whose stdout has no report attached means the
    framework did not ask for one (registry.py's `--benchmark-json` and
    `output_artifact` are checked against each other by
    tests/test_benchmark_scoring.py), and inventing a number from the
    rendering is what this round removed.

    `stats.mean` is in SECONDS — measured, not assumed: a row rendered as
    "4.7578" under a `(time in us)` header is 4.757764248017881e-06 here.
    """
    import json as _json

    if returncode == 5:
        return None  # No benchmark tests exist yet — dimension not yet applicable

    # Round 32 站4: on exit 2 (a collection error — the suite never ran) the
    # old loop awarded full marks to a crashed benchmark suite, because it
    # only ever SUBTRACTED. Every non-zero code reads as "no measurement".
    if returncode != 0:
        return None

    report = output.rsplit(_ARTIFACT_MARKER, 1)[1] if _ARTIFACT_MARKER in output else output
    try:
        benches = _json.loads(report).get("benchmarks", [])
    except (_json.JSONDecodeError, ValueError, AttributeError, TypeError):
        return None
    if not benches:
        return None  # Ran clean and measured nothing — N/A, not a free 100.

    score = 100.0
    for bench in benches:
        try:
            mean_ms = float(bench["stats"]["mean"]) * 1000.0
        except (KeyError, TypeError, ValueError):
            # A report entry with no readable mean is not a fast benchmark.
            # Skipping it silently would let a malformed report score 100, so
            # the whole report is refused instead.
            return None
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


# A tool saying, in its own words, that it never got as far as judging
# anything. Round 32 站4: exit-code-binary treated every non-zero exit as "the
# contract is broken", which is the same value it gives a tool that could not
# start. Measured: `lint-imports` on a src-layout project without PYTHONPATH
# prints "Could not find package 'X' in your Python path" and exits 1 — the
# harness scored that 0.0 and S4 reported it as the agent fabricating a 100.
# Each pattern is a literal the tool prints on its own could-not-run path; a
# genuinely broken contract prints its verdict instead ("Contracts: 0 kept,
# 1 broken"), which still scores 0.0.
_TOOL_DID_NOT_RUN_PATTERNS: tuple[str, ...] = (
    r"Could not find package",              # import-linter: unresolvable root_package
    r"No such file or directory",           # a target path that does not exist
    r"command not found",
    r"ModuleNotFoundError",
    r"ImportError",
)


def _score_exit_code_binary(output: str, returncode: int) -> Optional[float]:
    """Score based entirely on exit code (0 -> 100), or None when the tool
    reports that it could not run at all.

    The distinction matters because the two land in opposite places: a broken
    contract is the project's defect, while a tool that could not start is the
    framework's, and S4 routes them differently (Round 13 辨源).
    """
    if returncode == 0:
        return 100.0
    for pattern in _TOOL_DID_NOT_RUN_PATTERNS:
        if re.search(pattern, output, re.IGNORECASE):
            return None
    return 0.0


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
    "readability-v2":   _score_readability_v2,
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
}
