"""Per-dimension tables the Gate-evidence checks read. Data, no logic.

Round 80 站12. These five tables and the checks that read them change for
different reasons and are reviewed by different questions. A table changes when
a DIMENSION or a TOOL changes — a new dimension, a tool whose output no longer
contains the string it used to, a file that stops being evidence. A check
changes when the RULE changes. Keeping them in one file meant every review of
one had to page past the other, and 172 of the 945 lines that file carried were
a single regex table.

Every entry here is a literal: five tables, zero references to anything, so the
import goes one way and can never come back. Split out of harness/gate_checks.py
byte-identical, comments included; gate_checks re-exports them, which is how
`harness_bridge` and everything downstream keep reaching them by their existing
names.
"""

from __future__ import annotations


# ---------------------------------------------------------------------------
# S3-A: Tool-output content patterns (Solution A)
# ---------------------------------------------------------------------------
# For each tool name, at least one pattern must match the file/inline content.
# Patterns use re.IGNORECASE | re.MULTILINE.
_TOOL_CONTENT_PATTERNS: dict[str, list[str]] = {
    "ruff": [
        r"All checks passed",          # clean run
        r"\S+\.pyi?:\d+:\d+:",         # file:line:col violation line
        r"Found \d+ error",            # summary
        r"\[[\w-]+\]",                 # rule code like [E501] or [ruff]
    ],
    "mypy": [
        r"Success: no issues found",
        r"Found \d+ error",
        r"\.pyi?:\d+: (error|note):",
    ],
    "pytest-cov": [
        r"\d+ passed",
        r"TOTAL\s+\d+",
        r"coverage:",
        r"Coverage report",
    ],
    "pytest": [
        r"\d+ passed",
        r"\d+ failed",
        r"no tests ran",
        r"={3,}",                      # pytest separator bars
    ],
    "gitleaks": [
        r"No leaks found",
        r"Secret",
        r"leaks?\s+found",
        r"gitleaks",
        r"WRN\[",                      # gitleaks warning format
        r"INF\[",                      # gitleaks info format
    ],
    "mutmut": [
        r"Killed",
        r"Survived",
        r"mutation score",
        r"mutmut",
    ],
    "scancode": [
        r"license",
        r"SPDX",
        r"copyright",
        r"scan:",
    ],
    # ── JS/TS toolchain (resolved tool ids) ─────────────────────────────────
    "eslint": [
        r'"filePath"',                 # -f json per-file result objects
        r'"errorCount"',
        r'"messages"',
    ],
    # Clean tsc compiles emit nothing — evaluate_dimension.md instructs agents
    # to append `echo "tsc exit=$?"` so clean evidence still carries a marker.
    "tsc": [
        r"error TS\d+:",               # diagnostic lines
        r"tsc exit=\d",
    ],
    "tsc-checkjs": [
        r"error TS\d+:",
        r"tsc exit=\d",
    ],
    "semgrep-js": [
        r'"results"',                  # --json envelope
        r'"check_id"',
        r"semgrep",
    ],
    "vitest-cov": [
        r'"total"',                    # coverage-summary.json artifact
        r"%\s*(Stmts|Lines)",          # text reporter table header
        r"Coverage report",
        r"\d+ passed",
    ],
    "jest-cov": [
        r'"total"',
        r"%\s*(Stmts|Lines)",
        r"Tests:\s+\d+",
    ],
    "vitest-cov-integration": [
        r'"total"',
        r"%\s*(Stmts|Lines)",
        r"\d+ passed",
    ],
    "jest-cov-integration": [
        r'"total"',
        r"%\s*(Stmts|Lines)",
        r"Tests:\s+\d+",
    ],
    "js-bench": [
        r'"benchmarks"',               # normalized run.mjs JSON
        r'"mean_ms"',
    ],
    "stryker": [
        r"mutation score",
        r'"mutationScore"',            # mutation.json report
        r"Killed",
        r"Survived",
        r"stryker",
    ],
    # ── Round 27 站1: the 15 tools that had no pattern at all ────────────────
    # Without an entry here _validate_tool_content skips check 3 entirely, so any
    # prose ≥ _TOOL_OUTPUT_MIN_BYTES that does not start with '#' passed as tool
    # evidence. taskq-plus's Gate 4 shipped "NFR-08 satisfied contractually
    # (harness surface exists)" and "dimension N/A per protocol" as the evidence
    # for two dimensions on exactly this gap. `code-review-graph` is deliberately
    # NOT listed: architecture is framework-owned (crg_independent computes it in
    # finalize_gate), so a framework sentence IS its legitimate evidence.
    "bandit": [
        r'"results"',                  # -f json envelope
        r'"issue_severity"',
        r'"metrics"',
        r"No issues identified",
    ],
    "pyright": [
        r'"generalDiagnostics"',       # --outputjson envelope
        r'"summary"',
        r'"errorCount"',
        r"\d+ errors?, \d+ warnings?",
    ],
    "pytest-benchmark": [
        r"Name\s+\(time in ",          # benchmark table header
        r"-{3,}\s*benchmark:",         # "----------- benchmark: 2 tests -----------"
        r"no tests ran",
        r"\d+ passed",
        # Two drafts were rejected here by test_prose_is_not_tool_evidence, both
        # against taskq-plus's actual N/A sentence: a bare r"benchmark" (the word
        # appears in "pytest-benchmark" and "--benchmark-only") and then
        # r"-+\s*benchmark[:\s]", which still matched the "-benchmark " inside
        # "pytest-benchmark tests". Only the real separator rule — three or more
        # dashes AND the colon — describes output the tool alone produces.
        #
        # tool_runners.py::_score_pytest_benchmark (Round 50 站1) reads ONLY the
        # --benchmark-json report ("There is deliberately no table fallback") —
        # the four patterns above describe the console rendering that scorer
        # stopped parsing. Evidence captured the way the registry actually
        # invokes this tool (--benchmark-json + output_artifact,
        # tests/test_benchmark_scoring.py) is therefore the JSON envelope, and
        # it matched none of the four patterns above, so genuine evidence for
        # every project using the scorer's own expected format failed S3-A.
        # These three describe the JSON shape _score_pytest_benchmark parses
        # (`json.loads(report)["benchmarks"][i]["stats"]["mean"]`).
        r'"benchmarks"\s*:',
        r'"machine_info"',
        r'"stats"\s*:\s*\{',
    ],
    "pytest-cov-integration": [
        r"\d+ passed",
        r"TOTAL\s+\d+",
        r"coverage:",
        r"no tests ran",
    ],
    "import-linter": [
        r"Contracts?:",                # "Contracts: 3 kept, 0 broken."
        r"\d+ (kept|broken)",
        r"lint-imports",
        r"not found in project root",  # tool_runners' required_config_file message
    ],
    "system-verification": [
        r"verify-system",              # the target's own name in make output
        r"make(\[\d+\])?:",
        r"exit=?\s*\d",
    ],
    # In-process AST/tree-sitter scanners (harness/lang_scanners/) emit a JSON
    # object per dimension — same schema across languages, hence one pattern list
    # shared by the python and js tool ids.
    "ast-assertions": [r'"total"', r'"asserted"', r'"zero_assert"'],
    "js-assertions": [r'"total"', r'"asserted"', r'"zero_assert"'],
    "ast-error-handling": [r'"with_handler"', r'"no_handler"', r'"anti_patterns"'],
    "js-error-handling": [r'"with_handler"', r'"no_handler"', r'"anti_patterns"'],
    "ast-docstrings": [r'"documented"', r'"total"', r'"missing"', r"^\{\}$"],
    "js-doc-coverage": [r'"documented"', r'"total"', r'"missing"', r"^\{\}$"],
    "readability-v2": [r'"project_score"', r'"files"', r'"lloc"'],
    "js-mi": [r'"project_score"', r'"files"', r'"lloc"'],
}


# Minimum byte size for a tool_output file to be considered non-stub.
# Real tool output is always larger than this; pure comment lines are typically
# under 80 bytes.
#
# Round 27 站1 considered raising this and did NOT: the shortest real output a
# registered tool produces is `{}` / `[]` (2 bytes) — ast-docstrings with nothing
# to document, ruff on a clean run — so any floor high enough to reject a prose
# stub also rejects those. The check that actually rejects a stub is check 3, the
# per-tool content pattern, which this round extended from 17 tools to 31 (every
# tool except framework-owned code-review-graph).
_TOOL_OUTPUT_MIN_BYTES: int = 5


# Round 67 站3. The patterns above answer "is this output from that tool?"
# — an OR, deliberately, so a clean run and a failing run both satisfy it.
# For a dimension whose SCORE IS READ OUT OF THE OUTPUT, that is not enough:
# it also has to answer "is the quantity in here?".
#
# Measured on taskq-cc's committed Gate 4:
# .methodology/gate_evidence/gate4/test_coverage.txt is 205 bytes —
#
#     Outliers: 1 Standard Deviation from Mean; 1.5 IQR ...
#     OPS: Operations Per Second, computed as 1 / Mean
#     287 passed, 12 warnings in 88.80s (0:01:28)
#
# the tail of a pytest-benchmark run, cited as the evidence for
# test_coverage = 100.0. It satisfied `\d+ passed` and nothing else was
# required. Round 45 closed "the cited file is gone"; Round 32 closed "the
# cited file is a stub"; this is the third shape — a real file, real tool
# output, and the output of a different run than the number beside it.
#
# Only the two coverage dimensions are listed. The rule that a score must be
# derivable from its evidence is general, but these are the dimensions whose
# score is a number lifted out of the text; ruff's `All checks passed!`
# already proves everything ruff's score needs, and requiring more of thirty
# tools is how a guard starts manufacturing the fabrication it exists to
# prevent (Round 27's note, ten lines below).
_TOOL_REQUIRED_PATTERNS: dict[str, "tuple[str, list[str]]"] = {
    "pytest-cov": ("a coverage measurement", [r"TOTAL\s+\d+", r"\d+%", r"coverage:"]),
    "pytest-cov-integration": (
        "a coverage measurement", [r"TOTAL\s+\d+", r"\d+%", r"coverage:"],
    ),
}


# Round 12 站3b — infra-failure signatures inside a dimension's evidence.
# When run-gate's PRECONDITIONS block (SAB phantom/unregistered modules,
# manifest corruption), the gate evaluator agent used to follow its STOP
# Round N (2026-07): tighten the INFRA-fail signature registry. The old
# list contained `[BLOCKED] run-gate` — a generic run-gate prefix that
# appears not only on real INFRA failures (SAB phantom blocks) but also
# in any context where a sub-agent mentions or quotes gate1 output that
# contained that string. The classic false positive (taskq-plus FR-05 P3
# 2026-07): a workflow sub-agent reading its own GATE1 log and quoting
# the `[BLOCKED] run-gate` line in its report caused
# `_classify_infra_or_harness_bug` to mark the dispatch as INFRA and
# `_abort_dispatch_infra_or_harness_bug` to escalate to human — discarding
# a real Gate 1 PASS verdict (8/8 dimensions had evaluated and PASSed
# via the direct CLI). The remaining four signatures are specific to the
# Architecture Amendment Protocol pathway — they cannot appear in
# incidental context because the wording is framework-internal to
# harness-methodology. The original 2026-07-16 incident (three dimensions
# uniformly zeroed by a taskq.storage.store phantom block) is still
# caught: the dimensions' evidence carried both
# "Architecture Amendment Protocol violation" AND
# "Unregistered modules detected" — both retained signatures.
_INFRA_FAIL_EVIDENCE_SIGNATURES = (
    "Architecture Amendment Protocol violation",
    "Unregistered modules detected",
    "phantom module",
    "Phantom modules",
)


# Round 30 站6 — dimensions whose score can be moved by an exclusion file, and
# the file that moves it. `None` means "this dimension has no exclusion channel"
# and is a POSITIVE statement, not an omission: scancode (license_compliance)
# takes its exclusions on the command line, so there is no file to fingerprint,
# and recording that here is what stops the next reader assuming it was
# forgotten. A new scored dimension belongs in this table with one or the other.
DIMENSION_EXCLUSION_FILES: "dict[str, str | tuple[str, ...] | None]" = {
    # Round 92: `.gitleaks.toml` moves this dimension's score the same way
    # `.gitleaksignore` does, and more completely — a config can disable
    # whole rules, not just fingerprint one finding at a time. Measured on
    # three corpus projects carrying their own `.gitleaks.toml`, none of it
    # digested into any verdict before this round.
    "secrets_scanning": (".gitleaksignore", ".gitleaks.toml"),
    "license_compliance": None,
    # Round 31 站4: setup.cfg's [mutmut] section carries BOTH halves of the
    # mutation denominator — paths_to_mutate (what is in scope) and
    # paths_to_exclude (basenames dropped from the mutant pool, written by the
    # party being scored). Digesting the whole file covers both, which is the
    # right granularity: a verdict has to be readable back to the scope it was
    # taken on. Not a dot-file, so the entry is a project-root-relative path
    # rather than a hidden one — the registry's contract is "which file can
    # move this dimension's score", not "which dotfile".
    "mutation_testing": "setup.cfg",
    # Round 32 站5: the file that declares which tests count moves
    # test_coverage the same way an ignore-list moves secrets_scanning.
    # Measured: a project whose [tool:pytest] testpaths named nine entries
    # while the directory held sixteen test files — two of them the FR tests
    # for FR-02 and FR-07. The agent's bare `pytest` measured nine, the
    # framework's explicit path measured sixteen, and neither denominator was
    # recorded. Three files can carry the declaration, so this entry is a
    # tuple: whichever ones exist are digested.
    "test_coverage": ("pytest.ini", "pyproject.toml", "setup.cfg"),
}
