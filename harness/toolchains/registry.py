"""harness/toolchains/registry.py — language → dimension → tool resolution tables.

Single source of truth for which tool scores which quality dimension in which
language, how that tool is invoked, and how its availability is probed.

The Python entries are a faithful extraction of the previously hardcoded
tables (harness/tool_runners.py cmds/_DEFAULT_TIMEOUTS/_SKIP_TOOLS and
harness_cli._TOOL_CHECK_COMMANDS): same commands, same timeouts, same check
probes. Resolution for language="python" passes the gate-YAML `tool:` field
through unchanged, so existing projects see zero behavioral difference.

Command templates use placeholders expanded by tool_runners.run_tool:
  {root}         absolute project root
  {test_target}  resolved test directory (03-development/tests > tests > root)
  {src_target}   the project's own source dir(s) — expands to N argv entries,
                 since a coverage source may name several. Source scanners take
                 this, not {root}: S4 compares its score against the agent's,
                 and evaluate_dimension.md points the agent at src/, so aiming
                 the harness at the whole repo compared two different
                 denominators — and on a real project timed out before it could
                 (Round 31 站6). {root} is still correct for gitleaks, which
                 must scan everything a commit could carry.

R8 contract (harness/ssi/scripts/score.py): every requires_tool_execution
dimension must produce a non-null tool_score. A language may therefore only be
registered in DIMENSION_TOOLS when EVERY gate dimension resolves to a ToolSpec
— tests/test_toolchain_registry.py enforces this invariant against the gate
YAML configs.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sys
from typing import Optional, Union

# Vendored semgrep ruleset (pinned content → reproducible security scores).
_SEMGREP_JS_RULES = str(Path(__file__).parent / "semgrep_rules" / "js_security.yaml")


@dataclass(frozen=True)
class ToolSpec:
    """One executable (or in-process) scoring tool.

    cmd=None means the tool is not run as a subprocess by run_tool: either it
    is computed in-process (in_process=True) or it is on the inline skip list
    (skip_inline=True — too slow/complex for inline cross-validation, e.g.
    mutmut/scancode, or owned by another pipeline, e.g. code-review-graph).

    output_artifact: project-relative file the tool writes its machine-readable
    report to (e.g. coverage/coverage-summary.json). run_tool appends its
    content to the captured output after the subprocess exits, so scorers can
    parse the JSON artifact instead of scraping human-oriented stdout.
    """

    tool_id: str
    timeout: int
    check_cmd: str          # shell probe; exit 0 = installed
    human_name: str         # diagnostic label for missing-tool messages
    # Round 47 站1: how the tool GETS here, stated beside how it is checked.
    # One of "requirements" | "gate-extras" | "external" | "npm" | "builtin";
    # harness/toolchains/bootstrap.py says what each means and why there are
    # five. No default: a new ToolSpec must say where it comes from, or the
    # repair path would silently guess (and the seven contradicting prose
    # statements this field replaced all began as a guess).
    install_step: str
    cmd: Optional[tuple[str, ...]] = None
    scorer: Optional[str] = None
    skip_inline: bool = False
    in_process: bool = False
    output_artifact: Optional[str] = None
    # If set, run_tool checks this file exists in the project root before running.
    # Missing file → exit code 0 (no config = no contracts defined = no violations).
    required_config_file: Optional[str] = None


# ──────────────────────────────────────────────────────────────────────────────
# Tool specs — flat, tool_id-keyed. Python entries extracted verbatim from
# tool_runners.py / harness_cli.py (v2.7.0).
# ──────────────────────────────────────────────────────────────────────────────

TOOL_SPECS: dict[str, ToolSpec] = {
    # ── Python toolchain ─────────────────────────────────────────────────────
    "ruff": ToolSpec(
        tool_id="ruff",
        cmd=("ruff", "check", "{src_target}", "--output-format", "json", "--exit-zero"),
        timeout=30,
        check_cmd=f"ruff --version 2>&1 || {sys.executable} -m ruff --version 2>&1",
        human_name="ruff",
        install_step="requirements",
        scorer="ruff",
    ),
    "mypy": ToolSpec(
        tool_id="mypy",
        cmd=("mypy", "{src_target}", "--ignore-missing-imports",
             "--no-color-output", "--no-error-summary"),
        timeout=60,
        check_cmd="mypy --version 2>&1",
        human_name="mypy",
        install_step="requirements",
        scorer="mypy",
    ),
    "pyright": ToolSpec(
        tool_id="pyright",
        cmd=("pyright", "{src_target}", "--outputjson"),
        timeout=60,
        check_cmd="pyright --version 2>&1",
        human_name="pyright",
        install_step="requirements",
        scorer="pyright",
    ),
    "pytest-cov": ToolSpec(
        tool_id="pytest-cov",
        cmd=("pytest", "{test_target}", "--cov={cov_target}", "--cov-report=term-missing",
             "-q", "--tb=no", "--no-header"),
        timeout=120,
        check_cmd="pytest --version 2>&1 && coverage --version 2>&1",
        human_name="pytest + coverage",
        install_step="requirements",
        scorer="pytest-cov",
    ),
    "pytest": ToolSpec(
        tool_id="pytest",
        cmd=("pytest", "{test_target}", "-q", "--tb=no", "--no-header"),
        timeout=120,
        check_cmd="pytest --version 2>&1",
        human_name="pytest",
        install_step="requirements",
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
        install_step="external",
        scorer="gitleaks",
    ),
    "bandit": ToolSpec(
        tool_id="bandit",
        # --exit-zero: always exit 0 so returncode doesn't mask JSON output.
        # -q: bandit 1.8.6 writes its own INFO-level startup log ("profile
        # include tests: None" etc.) to stderr on every run, unconditionally,
        # regardless of --exit-zero or -f json. run_tool() concatenates
        # stdout+stderr into one string (harness/tool_runners.py:258) for
        # audit-file completeness — correct for every OTHER tool here, whose
        # scorers tolerate or expect stderr content — but bandit's own JSON
        # is on stdout alone, so the appended stderr lines land AFTER the
        # closing `}` and break `_score_bandit`'s `json.loads`. Confirmed
        # 2026-08-31: reproducible on every invocation, `-q` fully silences
        # it (verified: stderr empty, stdout unchanged). Without -q,
        # `_score_bandit` silently scored 0.0 for a WORKING bandit run,
        # indistinguishable from "code has 100 findings" — this is what let
        # a real taskq-verify Gate 2 dimension read as a genuine harness
        # measurement of 0 when bandit had in fact scored 96.
        cmd=("bandit", "-r", "{src_target}", "-f", "json", "-q", "--exit-zero"),
        timeout=60,
        check_cmd="bandit --version 2>&1",
        human_name="bandit",
        install_step="requirements",
        scorer="bandit",
    ),
    "radon-cc": ToolSpec(
        tool_id="radon-cc",
        cmd=("radon", "cc", "{src_target}", "-j", "--min", "A"),
        timeout=30,
        check_cmd="radon --version 2>&1",
        human_name="radon (radon-cc)",
        install_step="requirements",
        scorer="radon-cc",
    ),
    "readability-v2": ToolSpec(
        tool_id="readability-v2",
        cmd=(sys.executable, "-m", "harness.toolchains.readability_v2", "{src_target}"),
        timeout=30,
        check_cmd="radon --version 2>&1",
        human_name="radon (readability-v2)",
        install_step="requirements",
        scorer="readability-v2",
    ),
    "radon-mi": ToolSpec(
        tool_id="radon-mi",
        cmd=(sys.executable, "-m", "harness.toolchains.radon_mi_ast_stripped", "{src_target}"),
        timeout=30,
        check_cmd="radon --version 2>&1",
        human_name="radon (radon-mi)",
        install_step="requirements",
        scorer="radon-mi",
    ),
    # --benchmark-only: run only tests using the `benchmark` fixture.
    # If none exist, pytest exits with code 5 (no tests collected) → scorer
    # returns None.
    #
    # --benchmark-json is what the score is read from (Round 50 站1). The
    # terminal table is for a human: pytest-benchmark renders a relative
    # multiplier after each value and thousands-separates at four digits, so
    # the shape of a row depends on the magnitude of the numbers in it. Six
    # rounds of a parser aimed at that table produced zero rows against real
    # output. The report is the same structured shape the JS toolchain's
    # coverage artifacts already use.
    #
    # The path must match output_artifact exactly — run_tool clears it before
    # the run and appends it after, and tests/test_benchmark_scoring.py holds
    # the two together. It is under .sessi-work/ because it is an input to a
    # score, not evidence a verdict cites: the audit copy the operator is sent
    # to read lives in gate_evidence/ (Round 50 站6).
    "pytest-benchmark": ToolSpec(
        tool_id="pytest-benchmark",
        cmd=("pytest", "{root}", "--benchmark-only", "--benchmark-disable-gc",
             "--benchmark-columns", "mean,max",
             "--benchmark-json=.sessi-work/benchmark_report.json",
             "--tb", "no", "-q"),
        timeout=180,
        check_cmd=f"pytest --version 2>&1 && {sys.executable} -c 'import pytest_benchmark' 2>&1",
        human_name="pytest-benchmark",
        install_step="requirements",
        scorer="pytest-benchmark",
        output_artifact=".sessi-work/benchmark_report.json",
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
        install_step="requirements",
        scorer="pytest-cov-integration",
    ),
    # In-process Python ast scanners (no external binary). Check probes verify
    # the interpreter can parse a minimal module — gives a clear diagnostic if
    # something is broken.
    "ast-assertions": ToolSpec(
        tool_id="ast-assertions",
        timeout=30,
        check_cmd=f"{sys.executable} -c 'import ast; ast.parse(\"x=1\")' 2>&1",
        human_name="ast (assertions)",
        install_step="builtin",
        scorer="ast-assertions",
        in_process=True,
    ),
    "ast-error-handling": ToolSpec(
        tool_id="ast-error-handling",
        timeout=30,
        check_cmd=f"{sys.executable} -c 'import ast; ast.parse(\"try:\\n pass\\nexcept: pass\")' 2>&1",
        human_name="ast (error-handling)",
        install_step="builtin",
        scorer="ast-error-handling",
        in_process=True,
    ),
    "ast-docstrings": ToolSpec(
        tool_id="ast-docstrings",
        timeout=30,
        check_cmd=f"{sys.executable} -c 'import ast; ast.parse(\"def f():\\n \\\"\\\"\\\"doc\\\"\\\"\\\"\\n pass\")' 2>&1",
        human_name="ast (docstrings)",
        install_step="builtin",
        scorer="ast-docstrings",
        in_process=True,
    ),
    # Inline skip list — too slow/complex for inline cross-validation
    # (Solution-A content validation still applies to their evidence files).
    # mutmut 2.5.x hardcodes `python` in time_test_suite() (__main__.py:527):
    # if only python3 exists the check passes but Phase 4+ needs `ln -s`.
    # Round 31 站2: skip_inline was False directly under the comment above
    # naming mutmut as skip-list, so S4 really did spawn a bare `mutmut run`
    # from the project root — the one invocation evaluate_dimension.md tells
    # agents never to issue, because outside the framework's temp workdir
    # mutmut 2.x's hardcoded `python` runner fails on any host without that
    # symlink. cmd/scorer are gone with it: mutation_testing's score comes from
    # .methodology/mutation_score.json, written by compute_mutation_score,
    # which owns the workdir isolation, the SAB-derived scope and the sqlite
    # read. check_cmd stays — S2 still verifies mutmut is installed.
    "mutmut": ToolSpec(
        tool_id="mutmut",
        timeout=1800,
        check_cmd="mutmut --help 2>&1",
        human_name="mutmut",
        install_step="requirements",
        skip_inline=True,
    ),
    "import-linter": ToolSpec(
        tool_id="import-linter",
        cmd=("lint-imports",),
        timeout=60,
        check_cmd="lint-imports --help 2>&1",
        human_name="import-linter",
        install_step="gate-extras",
        scorer="exit-code-binary",
        required_config_file=".importlinter",
    ),
    "system-verification": ToolSpec(
        tool_id="system-verification",
        cmd=("make", "verify-system"),
        timeout=300,
        check_cmd="make --version 2>&1",
        human_name="System Verification Target",
        install_step="external",
        scorer="exit-code-binary",
    ),
    "scancode": ToolSpec(
        tool_id="scancode",
        timeout=0,
        check_cmd="scancode --version 2>&1",
        human_name="scancode-toolkit",
        install_step="gate-extras",
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
        install_step="gate-extras",
        skip_inline=True,
    ),
    # ── JavaScript / TypeScript toolchain ────────────────────────────────────
    # All npx invocations use --no-install: tools must come from the project's
    # pinned devDependencies (templates/js_toolchain/package.json) — never
    # resolved from the network at scoring time (score reproducibility).
    "eslint": ToolSpec(
        tool_id="eslint",
        cmd=("npx", "--no-install", "eslint", ".", "-f", "json"),
        timeout=60,
        check_cmd="npx --no-install eslint --version 2>&1",
        human_name="eslint",
        install_step="npm",
        scorer="eslint",
    ),
    "tsc": ToolSpec(
        tool_id="tsc",
        cmd=("npx", "--no-install", "tsc", "--noEmit", "--pretty", "false"),
        timeout=120,
        check_cmd="npx --no-install tsc --version 2>&1",
        human_name="tsc (typescript)",
        install_step="npm",
        scorer="tsc",
    ),
    # Pure-JS type checking: JSDoc types via tsc --checkJs. The include/exclude
    # set lives in tsconfig.checkjs.json (template) — deliberately NOT
    # tsconfig.json, which would flip language detection to typescript.
    "tsc-checkjs": ToolSpec(
        tool_id="tsc-checkjs",
        cmd=("npx", "--no-install", "tsc", "-p", "tsconfig.checkjs.json",
             "--noEmit", "--pretty", "false"),
        timeout=120,
        # Project must carry tsconfig.checkjs.json (template, generated by
        # _init_js_toolchain). Without it, tsc emits TS5083 which the scorer
        # would misread as a real type error; gate S2 must block instead.
        check_cmd="test -f tsconfig.checkjs.json && npx --no-install tsc --version 2>&1",
        human_name="tsc --checkJs (typescript)",
        install_step="npm",
        scorer="tsc",
    ),
    # No --reporter flag: vitest 4 removed the "basic" reporter; the default
    # reporter is fine because the score comes from the json-summary artifact.
    "vitest-cov": ToolSpec(
        tool_id="vitest-cov",
        cmd=("npx", "--no-install", "vitest", "run", "--coverage",
             "--coverage.reporter=json-summary", "--coverage.reporter=text"),
        timeout=240,
        check_cmd="npx --no-install vitest --version 2>&1",
        human_name="vitest + coverage-v8",
        install_step="npm",
        scorer="coverage-summary",
        output_artifact="coverage/coverage-summary.json",
    ),
    "jest-cov": ToolSpec(
        tool_id="jest-cov",
        cmd=("npx", "--no-install", "jest", "--coverage", "--ci",
             "--coverageReporters=json-summary", "--coverageReporters=text"),
        timeout=240,
        check_cmd="npx --no-install jest --version 2>&1",
        human_name="jest + coverage",
        install_step="npm",
        scorer="coverage-summary",
        output_artifact="coverage/coverage-summary.json",
    ),
    "vitest-cov-integration": ToolSpec(
        tool_id="vitest-cov-integration",
        cmd=("npx", "--no-install", "vitest", "run", "{test_target}/integration",
             "--coverage", "--coverage.reporter=json-summary",
             "--coverage.reporter=text"),
        timeout=240,
        check_cmd="npx --no-install vitest --version 2>&1",
        human_name="vitest + coverage (integration)",
        install_step="npm",
        scorer="coverage-summary",
        output_artifact="coverage/coverage-summary.json",
    ),
    "jest-cov-integration": ToolSpec(
        tool_id="jest-cov-integration",
        cmd=("npx", "--no-install", "jest", "{test_target}/integration",
             "--coverage", "--ci", "--coverageReporters=json-summary",
             "--coverageReporters=text"),
        timeout=240,
        check_cmd="npx --no-install jest --version 2>&1",
        human_name="jest + coverage (integration)",
        install_step="npm",
        scorer="coverage-summary",
        output_artifact="coverage/coverage-summary.json",
    ),
    "semgrep-js": ToolSpec(
        tool_id="semgrep-js",
        cmd=("semgrep", "scan", "--config", _SEMGREP_JS_RULES,
             "--json", "--metrics=off", "--quiet"),
        timeout=120,
        check_cmd="semgrep --version 2>&1",
        human_name="semgrep (vendored JS ruleset)",
        install_step="requirements",
        scorer="semgrep",
    ),
    # Runner-agnostic benchmark convention: `node benchmarks/run.mjs` emits
    # {"benchmarks": [{"name": ..., "mean_ms": ...}]} (tinybench template in
    # templates/js_toolchain/benchmarks/). Missing benchmarks/ → scorer None
    # (dimension not yet applicable — same semantics as pytest-benchmark
    # exit 5).
    "js-bench": ToolSpec(
        tool_id="js-bench",
        cmd=("node", "benchmarks/run.mjs"),
        timeout=180,
        check_cmd="node --version 2>&1",
        human_name="node benchmarks (tinybench)",
        install_step="external",
        scorer="js-bench",
    ),
    # StrykerJS — skip-list like mutmut (full mutation runs are minutes-long);
    # TDD-PRECHECK and gate evidence validation consume its JSON report.
    "stryker": ToolSpec(
        tool_id="stryker",
        timeout=0,
        check_cmd="npx --no-install stryker --version 2>&1",
        human_name="StrykerJS",
        install_step="npm",
        skip_inline=True,
    ),
    # In-process tree-sitter scanners (shared by javascript and typescript;
    # grammar chosen per file extension). Runners live in
    # harness/lang_scanners/treesitter_js.py.
    "js-assertions": ToolSpec(
        tool_id="js-assertions",
        timeout=30,
        check_cmd=(f"{sys.executable} -c 'import tree_sitter, tree_sitter_javascript, "
                   "tree_sitter_typescript' 2>&1"),
        human_name="tree-sitter (assertions)",
        install_step="requirements",
        scorer="ast-assertions",
        in_process=True,
    ),
    "js-error-handling": ToolSpec(
        tool_id="js-error-handling",
        timeout=30,
        check_cmd=(f"{sys.executable} -c 'import tree_sitter, tree_sitter_javascript, "
                   "tree_sitter_typescript' 2>&1"),
        human_name="tree-sitter (error-handling)",
        install_step="requirements",
        scorer="ast-error-handling",
        in_process=True,
    ),
    "js-doc-coverage": ToolSpec(
        tool_id="js-doc-coverage",
        timeout=30,
        check_cmd=(f"{sys.executable} -c 'import tree_sitter, tree_sitter_javascript, "
                   "tree_sitter_typescript' 2>&1"),
        human_name="tree-sitter (JSDoc coverage)",
        install_step="requirements",
        scorer="ast-docstrings",
        in_process=True,
    ),
    # Emits radon-mi-compatible JSON ({"file": {"mi": 78.2}}) so the existing
    # radon-mi scorer (average MI) applies unchanged.
    "js-mi": ToolSpec(
        tool_id="js-mi",
        timeout=60,
        check_cmd=(f"{sys.executable} -c 'import tree_sitter, tree_sitter_javascript, "
                   "tree_sitter_typescript' 2>&1"),
        human_name="tree-sitter (maintainability index)",
        install_step="requirements",
        scorer="radon-mi",
        in_process=True,
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

_JS_COMMON: dict[str, DimensionTool] = {
    "linting":                "eslint",
    "test_coverage":          {"vitest": "vitest-cov", "jest": "jest-cov",
                               "default": "vitest-cov"},
    "security":               "semgrep-js",
    "secrets_scanning":       "gitleaks",
    "license_compliance":     "scancode",
    "mutation_testing":       "stryker",
    "architecture":           "code-review-graph",
    "readability":            "js-mi",
    "error_handling":         "js-error-handling",
    "documentation":          "js-doc-coverage",
    "performance":            "js-bench",
    "integration_coverage":   {"vitest": "vitest-cov-integration",
                               "jest": "jest-cov-integration",
                               "default": "vitest-cov-integration"},
    "test_assertion_quality": "js-assertions",
    # eslint enforces import boundaries when configured with eslint-plugin-import;
    # no Python-native import-linter equivalent exists for JS/TS.
    "architecture_constraints": "eslint",
    # system-verification (make verify-system) is language-agnostic.
    "execute_verification_target": "system-verification",
}

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
        "readability":            "readability-v2",
        "error_handling":         "ast-error-handling",
        "documentation":          "ast-docstrings",
        "performance":            "pytest-benchmark",
        "integration_coverage":   "pytest-cov-integration",
        "test_assertion_quality": "ast-assertions",
        "architecture_constraints": "import-linter",
        "execute_verification_target": "system-verification",
    },
    # type_safety is the only JS/TS divergence: TS type-checks natively
    # (tsc --noEmit); pure JS enforces JSDoc types via tsc --checkJs
    # (tsconfig.checkjs.json template) — R8 forbids skipping the dimension.
    "javascript": {**_JS_COMMON, "type_safety": "tsc-checkjs"},
    "typescript": {**_JS_COMMON, "type_safety": "tsc"},
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
