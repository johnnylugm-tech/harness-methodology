"""harness/toolchains/registry.py — language → dimension → tool resolution tables.

Single source of truth for which tool scores which quality dimension in which
language, how that tool is invoked, and how its availability is probed.

The Python entries are a faithful extraction of the previously hardcoded
tables (harness/tool_runners.py cmds/_DEFAULT_TIMEOUTS/_SKIP_TOOLS and
harness_cli._TOOL_CHECK_COMMANDS): same commands, same timeouts, same check
probes. Resolution for language="python" passes the gate-YAML `tool:` field
through unchanged, so existing projects see zero behavioral difference.

Command templates use two placeholders, expanded by tool_runners.run_tool:
  {root}         absolute project root
  {test_target}  resolved test directory (03-development/tests > tests > root)

R8 contract (harness/ssi/scripts/score.py): every requires_tool_execution
dimension must produce a non-null tool_score. A language may therefore only be
registered in DIMENSION_TOOLS when EVERY gate dimension resolves to a ToolSpec
— tests/test_toolchain_registry.py enforces this invariant against the gate
YAML configs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Union


@dataclass(frozen=True)
class ToolSpec:
    """One executable (or in-process) scoring tool.

    cmd=None means the tool is not run as a subprocess by run_tool: either it
    is computed in-process (in_process=True) or it is on the inline skip list
    (skip_inline=True — too slow/complex for inline cross-validation, e.g.
    mutmut/scancode, or owned by another pipeline, e.g. code-review-graph).
    """

    tool_id: str
    timeout: int
    check_cmd: str          # shell probe; exit 0 = installed
    human_name: str         # diagnostic label for missing-tool messages
    cmd: Optional[tuple[str, ...]] = None
    scorer: Optional[str] = None
    skip_inline: bool = False
    in_process: bool = False


# ──────────────────────────────────────────────────────────────────────────────
# Tool specs — flat, tool_id-keyed. Python entries extracted verbatim from
# tool_runners.py / harness_cli.py (v2.7.0).
# ──────────────────────────────────────────────────────────────────────────────

TOOL_SPECS: dict[str, ToolSpec] = {
    # ── Python toolchain ─────────────────────────────────────────────────────
    "ruff": ToolSpec(
        tool_id="ruff",
        cmd=("ruff", "check", "{root}", "--output-format", "json", "--exit-zero"),
        timeout=30,
        check_cmd="ruff --version 2>&1 || python3 -m ruff --version 2>&1",
        human_name="ruff",
        scorer="ruff",
    ),
    "mypy": ToolSpec(
        tool_id="mypy",
        cmd=("mypy", "{root}", "--ignore-missing-imports",
             "--no-color-output", "--no-error-summary"),
        timeout=60,
        check_cmd="mypy --version 2>&1",
        human_name="mypy",
        scorer="mypy",
    ),
    "pyright": ToolSpec(
        tool_id="pyright",
        cmd=("pyright", "{root}", "--outputjson"),
        timeout=60,
        check_cmd="pyright --version 2>&1",
        human_name="pyright",
        scorer="pyright",
    ),
    "pytest-cov": ToolSpec(
        tool_id="pytest-cov",
        cmd=("pytest", "{test_target}", "--cov", "--cov-report=term-missing",
             "-q", "--tb=no", "--no-header"),
        timeout=120,
        check_cmd="pytest --version 2>&1 && coverage --version 2>&1",
        human_name="pytest + coverage",
        scorer="pytest-cov",
    ),
    "pytest": ToolSpec(
        tool_id="pytest",
        cmd=("pytest", "{test_target}", "-q", "--tb=no", "--no-header"),
        timeout=120,
        check_cmd="pytest --version 2>&1",
        human_name="pytest",
        scorer="pytest",
    ),
    "gitleaks": ToolSpec(
        tool_id="gitleaks",
        # No --exit-code override: exit 0 = clean, exit 1 = leaks found.
        # Overriding to 0 would make the scorer always return 100.
        cmd=("gitleaks", "detect", "--source", "{root}"),
        timeout=30,
        check_cmd="gitleaks version 2>&1",
        human_name="gitleaks",
        scorer="gitleaks",
    ),
    "bandit": ToolSpec(
        tool_id="bandit",
        # --exit-zero: always exit 0 so returncode doesn't mask JSON output
        cmd=("bandit", "-r", "{root}", "-f", "json", "--exit-zero"),
        timeout=60,
        check_cmd="bandit --version 2>&1",
        human_name="bandit",
        scorer="bandit",
    ),
    "radon-cc": ToolSpec(
        tool_id="radon-cc",
        cmd=("radon", "cc", "{root}", "-j", "--min", "A"),
        timeout=30,
        check_cmd="radon --version 2>&1",
        human_name="radon (radon-cc)",
        scorer="radon-cc",
    ),
    "radon-mi": ToolSpec(
        tool_id="radon-mi",
        cmd=("radon", "mi", "{root}", "-j"),
        timeout=30,
        check_cmd="radon --version 2>&1",
        human_name="radon (radon-mi)",
        scorer="radon-mi",
    ),
    # --benchmark-only: run only tests using the `benchmark` fixture.
    # If none exist, pytest exits with code 5 (no tests collected) → scorer
    # returns None. Text output so results flow through stdout capture.
    "pytest-benchmark": ToolSpec(
        tool_id="pytest-benchmark",
        cmd=("pytest", "{root}", "--benchmark-only", "--benchmark-disable-gc",
             "--benchmark-columns", "mean,max", "--tb", "no", "-q"),
        timeout=180,
        check_cmd="pytest --version 2>&1 && python3 -c 'import pytest_benchmark' 2>&1",
        human_name="pytest-benchmark",
        scorer="pytest-benchmark",
    ),
    # Integration coverage: run only the integration suite and measure real
    # line coverage of the source tree (NOT pass-rate). Missing suite →
    # pytest exits 4/5 and the cov table is absent → scorer returns 0 →
    # cross-validation blocks (a passing agent score is then unverifiable).
    "pytest-cov-integration": ToolSpec(
        tool_id="pytest-cov-integration",
        cmd=("pytest", "03-development/tests/integration",
             "--cov=03-development/src", "--cov-report=term-missing",
             "-q", "--tb=no", "--no-header"),
        timeout=180,
        check_cmd="pytest --version 2>&1 && coverage --version 2>&1",
        human_name="pytest + coverage (integration)",
        scorer="pytest-cov-integration",
    ),
    # In-process Python ast scanners (no external binary). Check probes verify
    # the interpreter can parse a minimal module — gives a clear diagnostic if
    # something is broken.
    "ast-assertions": ToolSpec(
        tool_id="ast-assertions",
        timeout=30,
        check_cmd="python3 -c 'import ast; ast.parse(\"x=1\")' 2>&1",
        human_name="ast (assertions)",
        scorer="ast-assertions",
        in_process=True,
    ),
    "ast-error-handling": ToolSpec(
        tool_id="ast-error-handling",
        timeout=30,
        check_cmd="python3 -c 'import ast; ast.parse(\"try:\\n pass\\nexcept: pass\")' 2>&1",
        human_name="ast (error-handling)",
        scorer="ast-error-handling",
        in_process=True,
    ),
    "ast-docstrings": ToolSpec(
        tool_id="ast-docstrings",
        timeout=30,
        check_cmd="python3 -c 'import ast; ast.parse(\"def f():\\n \\\"\\\"\\\"doc\\\"\\\"\\\"\\n pass\")' 2>&1",
        human_name="ast (docstrings)",
        scorer="ast-docstrings",
        in_process=True,
    ),
    # Inline skip list — too slow/complex for inline cross-validation
    # (Solution-A content validation still applies to their evidence files).
    # mutmut 2.5.x hardcodes `python` in time_test_suite() (__main__.py:527):
    # if only python3 exists the check passes but Phase 4+ needs `ln -s`.
    "mutmut": ToolSpec(
        tool_id="mutmut",
        timeout=0,
        check_cmd="mutmut --help 2>&1",
        human_name="mutmut",
        skip_inline=True,
    ),
    "scancode": ToolSpec(
        tool_id="scancode",
        timeout=0,
        check_cmd="scancode --version 2>&1",
        human_name="scancode-toolkit",
        skip_inline=True,
    ),
    # ── Language-agnostic ────────────────────────────────────────────────────
    # architecture is scored by the framework's independent CRG run
    # (community_cohesion) inside finalize_gate, never by run_tool.
    "code-review-graph": ToolSpec(
        tool_id="code-review-graph",
        timeout=0,
        check_cmd="code-review-graph status 2>&1",
        human_name="code-review-graph",
        skip_inline=True,
    ),
}


# ──────────────────────────────────────────────────────────────────────────────
# Dimension → tool_id per language.
#
# Values are either a tool_id string, or a {test_runner: tool_id} dict for
# dimensions whose tool depends on the project's test runner (with key
# "default" as the fallback when the runner is undetected).
#
# Registering a language here asserts FULL 14-dimension coverage (R8) — the
# registry completeness test fails otherwise.
# ──────────────────────────────────────────────────────────────────────────────

DimensionTool = Union[str, dict[str, str]]

DIMENSION_TOOLS: dict[str, dict[str, DimensionTool]] = {
    "python": {
        "linting":                "ruff",
        "type_safety":            "pyright",
        "test_coverage":          "pytest-cov",
        "security":               "bandit",
        "secrets_scanning":       "gitleaks",
        "license_compliance":     "scancode",
        "mutation_testing":       "mutmut",
        "architecture":           "code-review-graph",
        "readability":            "radon-mi",
        "error_handling":         "ast-error-handling",
        "documentation":          "ast-docstrings",
        "performance":            "pytest-benchmark",
        "integration_coverage":   "pytest-cov-integration",
        "test_assertion_quality": "ast-assertions",
    },
}


def get_tool_spec(tool_id: str) -> Optional[ToolSpec]:
    """Return the ToolSpec for *tool_id*, or None if unregistered."""
    return TOOL_SPECS.get(tool_id)


def resolve_tool_id(
    dimension: str,
    language: str,
    yaml_tool: Optional[str] = None,
    test_runner: Optional[str] = None,
) -> Optional[str]:
    """Resolve the tool that scores *dimension* for *language*.

    python      → the gate-YAML `tool:` field passes through unchanged (legacy
                  contract: YAML is authoritative, including its absence —
                  callers keep their historical None-handling).
    other langs → DIMENSION_TOOLS lookup; runner-variant dimensions select by
                  *test_runner* with "default" fallback.

    Returns None when the dimension has no tool requirement for the language.
    """
    if language == "python":
        return yaml_tool

    entry = DIMENSION_TOOLS.get(language, {}).get(dimension)
    if entry is None:
        return None
    if isinstance(entry, dict):
        if test_runner and test_runner in entry:
            return entry[test_runner]
        return entry.get("default")
    return entry
