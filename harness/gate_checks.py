"""What Gate evidence has to contain before a dimension may be scored.

Round 80 站8. Moved out of harness/harness_bridge.py verbatim; the bodies here
are byte-identical to the ones that were there, which
tests/test_god_file_split_safety.py asserts by AST source segment.

Seven checks and the five tables they read. Each answers the same kind of
question — is what the sub-agent supplied for this dimension actually evidence?
— against a different fact: the tool output's content (`_validate_tool_content`
and the `_TOOL_*` tables), whether an infrastructure failure is being reported
as a finding (`_check_infra_fail_pollution`), whether the evidence was executed
at all (`_check_tool_evidence`), what the declared test outcome was
(`_check_tests_failed`, `_parse_skip_counts`, `_check_test_skip_ratio`) and
whether the verification target reaches the delivered system
(`_verify_system_reach_block`).

`path_escapes_root` and `_gate_dimension_names` travel with them because they
are inside this set's dependency closure; harness_bridge re-exports both, along
with everything else here, so every existing caller and test keeps its name.
The closure references nothing defined in harness_bridge and no class there, so
the import goes one way only.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - annotations only
    # The three signatures below name GateContext, which lives in
    # harness_bridge and imports this module. Guarded so the annotation
    # stays honest without creating a runtime cycle; `from __future__
    # import annotations` above means it is never evaluated anyway.
    from harness.harness_bridge import GateContext


def path_escapes_root(candidate: Path, root: Path) -> bool:
    """True if `candidate` resolves to a location outside `root`.

    Shared containment check for agent-controlled path fields (tool_output,
    issue_registry_path, ...) so an agent writing `../../etc/passwd` (or an
    absolute path, or a symlink to outside) into a gate result JSON can't be
    silently read. May raise OSError/RuntimeError if resolution fails (e.g.
    a symlink loop) — callers catch those themselves since the message they
    surface differs per call site.
    """
    return not candidate.resolve().is_relative_to(root.resolve())


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


def _validate_tool_content(
    content: str,
    tool: str | None,
    dim_name: str,
    *,
    inline: bool,
) -> list[str]:
    """S3-A: Verify that *content* looks like genuine tool output.

    Checks (in order):
      1. Minimum size (file only — inline snippets are expected to be short)
      2. Comment-header stub detection (applies to both file and inline)
      3. Tool-specific structural pattern match (applies to both)
      4. For tools whose dimension score is read out of the output: the
         quantity itself is present (Round 67 站3)

    Returns list of violation messages (empty = OK).
    """
    violations: list[str] = []

    # 1. Minimum size (file only)
    if not inline:
        size = len(content.encode("utf-8"))
        if size < _TOOL_OUTPUT_MIN_BYTES:
            violations.append(
                f"{dim_name}: tool_output file is too small ({size} bytes) — "
                f"likely a stub; real tool output is at least {_TOOL_OUTPUT_MIN_BYTES} bytes"
            )
            return violations  # Early exit — no point checking further

    # 2. Comment-header stub detection
    first_nonblank = next((ln for ln in content.splitlines() if ln.strip()), "")
    if first_nonblank.strip().startswith("#"):
        kind = "tool_evidence" if inline else "tool_output"
        violations.append(
            f"{dim_name}: {kind} starts with '#' comment — "
            f"this is a stub marker, not genuine tool output"
        )
        return violations  # Early exit

    # 3. Tool-specific structural pattern
    if tool and tool in _TOOL_CONTENT_PATTERNS:
        patterns = _TOOL_CONTENT_PATTERNS[tool]
        if not any(
            re.search(p, content, re.IGNORECASE | re.MULTILINE)
            for p in patterns
        ):
            kind = "tool_evidence" if inline else "tool_output"
            violations.append(
                f"{dim_name}: {kind} does not match any expected output pattern for "
                f"'{tool}' — content may not be genuine {tool} output"
            )

    # 4. The quantity the score was read from has to be in there.
    if tool and tool in _TOOL_REQUIRED_PATTERNS:
        what, required = _TOOL_REQUIRED_PATTERNS[tool]
        if not any(
            re.search(p, content, re.IGNORECASE | re.MULTILINE) for p in required
        ):
            kind = "tool_evidence" if inline else "tool_output"
            violations.append(
                f"{dim_name}: {kind} contains no {what} — no TOTAL row, no "
                f"percentage, no coverage header. The score cited against this "
                f"evidence cannot have been read out of it; re-run the tool "
                f"with coverage enabled and cite that run"
            )

    return violations


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


def _check_infra_fail_pollution(raw: dict) -> list[str]:
    """Round 12 站3b: INFRA_FAIL ≠ quality failure.

    A zero score whose evidence carries a run-gate PRECONDITION-block
    signature is not a measurement — the tool never ran. Writing it into
    the manifest as a quality zero poisons scoring history and dispatches
    code fixers at a non-code problem. Detect and reject the result
    outright so finalize-gate FATALs with an infra diagnosis instead.

    Round N: partial-pollution carve-out. If at least ONE evaluated dimension
    produced a real (non-zero) score with non-INFRA-pollution evidence, the
    run-gate DID execute end-to-end; the other dimensions' INFRA-block
    zeros are partial pollution (one SAB-phantom dimension aborts the run
    while the rest still score normally) and the whole verdict must NOT
    be blanket-rejected. The per-dim diagnostic message still surfaces via
    the partial-pollution diagnostics list so operators see the affected
    dimensions, but `finalize-gate` proceeds with the real PASS record for
    the cleanly-evaluated dimensions. Incident: taskq-plus FR-05 P3 (2026-07)
    — GATE1 hit `[BLOCKED] run-gate` for the SAB phantom dimension while
    7/8 other dimensions evaluated normally; blanket rejection discarded
    a real Gate 1 PASS verdict and the workflow escalated to human on
    false-positive grounds.
    """
    entries: list[tuple[str, float | None, str]] = []
    breakdown = raw.get("breakdown")
    if isinstance(breakdown, dict):
        for dim, row in breakdown.items():
            if isinstance(row, dict):
                _ev = " ".join(str(row.get(k, "")) for k in ("tool_evidence", "evidence"))
                entries.append((str(dim), row.get("score"), _ev))
    for row in raw.get("dimensions", []) or []:
        if isinstance(row, dict):
            _ev = " ".join(str(row.get(k, "")) for k in ("tool_evidence", "evidence"))
            entries.append((str(row.get("name", "?")), row.get("score"), _ev))
    # Partial-pollution carve-out: at least one dimension passed cleanly
    # (non-zero score AND its evidence contains no INFRA-fail signature).
    # When present, the gate DID run end-to-end — accept the verdict and
    # surface partial-pollution info via diagnostics rather than rejecting.
    has_real_pass = any(
        (score not in (0, 0.0, None))
        and not any(sig in (evidence or "") for sig in _INFRA_FAIL_EVIDENCE_SIGNATURES)
        for _, score, evidence in entries
    )
    violations: list[str] = []
    partial_diagnostics: list[str] = []
    for dim, score, evidence in entries:
        if not evidence:
            continue
        matched = [sig for sig in _INFRA_FAIL_EVIDENCE_SIGNATURES if sig in evidence]
        if matched and (score in (0, 0.0, None)):
            msg = (
                f"dimension {dim!r}: score={score} with run-gate PRECONDITION-block "
                f"evidence ({matched[0]!r}) — this is an INFRA failure, not a quality "
                f"measurement. Do NOT dispatch code fixes for it. Fix the precondition "
                f"run-gate reported (SAB phantom/unregistered module, manifest state), "
                f"re-run run-gate until its preconditions pass, then re-evaluate."
            )
            if has_real_pass:
                # Partial pollution — surface per-dim info but accept the whole verdict.
                partial_diagnostics.append(msg)
            else:
                # Whole-gate pollution — reject so finalize-gate FATALs with infra dx.
                violations.append(msg)
    # Attach partial diagnostics as a marker suffix so callers can still surface
    # them without treating them as blockers. The first violation (if any) carries
    # the diagnostics block; if no violations, append a synthetic diagnostic-only
    # entry prefixed with "[partial-pollution]" so it's distinguishable from the
    # whole-gate rejections (operators looking at finalize-gate output).
    if partial_diagnostics and not violations:
        violations.append(
            "[partial-pollution] " + " | ".join(partial_diagnostics)
            + " — accepted (at least one dimension PASSed cleanly); fix the "
            "SAB/manifest preconditions and re-run to clear the partial-pollution marker."
        )
    return violations


# Round 30 站6 — dimensions whose score can be moved by an exclusion file, and
# the file that moves it. `None` means "this dimension has no exclusion channel"
# and is a POSITIVE statement, not an omission: scancode (license_compliance)
# takes its exclusions on the command line, so there is no file to fingerprint,
# and recording that here is what stops the next reader assuming it was
# forgotten. A new scored dimension belongs in this table with one or the other.
DIMENSION_EXCLUSION_FILES: "dict[str, str | tuple[str, ...] | None]" = {
    "secrets_scanning": ".gitleaksignore",
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


def _check_tool_evidence(ctx: "GateContext", raw: dict,
                         digests: "dict | None" = None) -> list[str]:
    """S3: Verify tool execution evidence in gate result JSON.

    When *digests* is supplied, every piece of evidence that PASSES验证 is
    fingerprinted into it (Round 27 站3). The digest is taken here rather than
    later because here is the only moment the evidence is known to exist and to
    be genuine — taskq-plus's Gate 4 cites 13 tool_output paths under the
    gitignored .sessi-work/, all of them gone now, while the verdict that read
    them is committed and permanent.

    For dimensions with requires_tool_execution:true in the gate YAML config,
    the result JSON breakdown entry MUST include either:
      - tool_output: path to a file containing raw tool stdout/stderr
      - tool_evidence: inline string of tool output snippet

    Additionally (S3-A), the content of tool_output files and tool_evidence
    strings is validated for structural authenticity — stub files and comment
    placeholders are rejected.

    Returns list of violation messages (empty = all good).
    """
    import yaml as _yaml
    from pathlib import Path as _Path

    # Round 29 Station 1: use the single-source-of-truth resolver instead of
    # project_root-relative globbing.  The old path (project/harness/gate_configs)
    # was one level too high when the harness is checked out as a git submodule
    # (the actual path is project/harness/harness/gate_configs).  SSOT resolver:
    # core.quality_gate.gate_thresholds.gate_config_path() — uses __file__ so it
    # always lands on the framework's own shipped configs.
    from core.quality_gate.gate_thresholds import gate_config_path as _gcp

    # Round 30 站3: gate_num comes from GateContext, which the framework builds
    # — a value outside 1-4 is a caller-contract violation, and Round 29 caught
    # the ValueError and returned `[]`, i.e. "no evidence violations found". The
    # raise now reaches the Round 28 crash boundary, which names the caller.
    cfg_path = _gcp(ctx.gate_num)

    if not cfg_path.exists():
        # Round 29 Station 1: gate configs are framework-owned assets tracked by
        # git ls-files.  Missing → checkout is corrupt.  Return a blocking
        # violation instead of silently returning [] (which the old code did
        # and was indistinguishable from "no violations").
        return [
            f"S3 gate config not found: {cfg_path} "
            f"(gate {ctx.gate_num}). Expected framework-owned asset — "
            f"is the harness checkout intact?"
        ]

    try:
        cfg = _yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    except (_yaml.YAMLError, OSError) as exc:
        return [
            f"S3 gate config unreadable: {cfg_path} ({exc})"
        ]



    violations: list[str] = []
    breakdown = raw.get("breakdown", {})

    # Round 29 Station 6: exclusion files that alter a dimension's score
    # (e.g. .gitleaksignore for secrets_scanning) must themselves be in
    # version control.  An untracked exclusion file means the score on a
    # fresh clone would be different — the denominator is in the scorer's
    # hands, not the framework's.
    #
    # Round 30 站6: fingerprinted as well as tracked. `.gitleaksignore` is
    # committed and still the score moves when a line is added to it — the file
    # being in git says nothing about which version of it produced this verdict.
    # The digest goes into evidence_digest beside the tool outputs (Round 27
    # 站3's channel), so two verdicts scored under different exemption lists are
    # distinguishable from the artifacts alone.
    _excl_pairs: "list[tuple[str, str]]" = [
        (_dim, str(_f))
        for _dim, _spec in DIMENSION_EXCLUSION_FILES.items()
        if _spec is not None
        for _f in ((_spec,) if isinstance(_spec, str) else _spec)
    ]
    for _dim_name, _excl_file in _excl_pairs:
        _excl_path = _Path(ctx.project_root) / _excl_file
        if not _excl_path.is_file():
            continue
        if digests is not None:
            from core.quality_gate.evidence_digest import digest_of_file
            digests[f"{_dim_name}::{_excl_file}"] = digest_of_file(
                _excl_path, source=f"{_excl_file} (score-altering exclusions)"
            )
        _project_root_path = _Path(ctx.project_root)
        import subprocess as _sp  # bound before the try: the except reads it
        try:
            _tracked = _sp.run(
                ["git", "ls-files", "--error-unmatch", _excl_file],
                cwd=str(_project_root_path),
                capture_output=True, text=True, timeout=10,
            )
            if _tracked.returncode != 0:
                violations.append(
                    f"S6 {_excl_file} exists but is not tracked by git — "
                    f"the {_dim_name} score depends on an exclusion file "
                    f"that is absent on a fresh clone. "
                    f"Either commit it or remove the exclusion entries."
                )
        except (OSError, _sp.SubprocessError) as _git_exc:
            # Round 30 站3: git is a HARD dependency of this framework —
            # enforcer_sha, state.json's phase_completed[].sha and every hook
            # need it. Round 29 wrote this as `except Exception` into a
            # logging.debug nobody reads, so a check that could not run was
            # indistinguishable from a check that found nothing. It still must
            # not block a gate on a provenance-adjacent failure, so it records
            # and continues — the ledger is where "we could not check" lives.
            from core.degradation_ledger import record_degradation
            record_degradation(
                str(_project_root_path), "gate:S6-exclusion-vcs",
                f"could not verify {_excl_file} is tracked by git ({_git_exc})",
                why=f"the {_dim_name} score was accepted without its exclusion "
                    f"file being checked into version control", owner="harness"
            )

    # Evidence format patterns are keyed by the RESOLVED tool id — a TS
    # project's linting evidence is eslint JSON, not ruff output.
    from harness.toolchains import (
        get_project_language,
        get_project_test_runner,
        resolve_tool_id,
    )
    _language = get_project_language(ctx.project_root)
    _test_runner = get_project_test_runner(ctx.project_root)

    for dim in cfg.get("dimensions", []):
        dim_name = dim.get("name", "")
        requires_tool = dim.get("requires_tool_execution", False)
        if not requires_tool:
            continue

        tool = dim.get("tool")
        if _language != "python" and tool:
            tool = resolve_tool_id(
                dim_name, _language, yaml_tool=tool, test_runner=_test_runner
            ) or tool
        dim_data = breakdown.get(dim_name, {})
        tool_output = dim_data.get("tool_output")
        tool_evidence = dim_data.get("tool_evidence")

        if tool_output:
            out_path = _Path(ctx.project_root) / tool_output
            # Containment check: refuse to read any tool_output that
            # resolves outside project_root. An agent writing
            # `../../etc/passwd` (or an absolute path, or a symlink to
            # outside) into the gate result JSON must not be silently
            # read by the audit cross-check.
            try:
                if path_escapes_root(out_path, _Path(ctx.project_root)):
                    violations.append(
                        f"{dim_name}: tool_output path '{tool_output}' "
                        f"escapes project root — refusing to read"
                    )
                    continue
            except (OSError, RuntimeError) as exc:
                violations.append(
                    f"{dim_name}: tool_output path '{tool_output}' "
                    f"cannot be resolved: {exc}"
                )
                continue
            if not out_path.exists():
                violations.append(
                    f"{dim_name}: tool_output path '{tool_output}' does not exist"
                )
            else:
                try:
                    content = out_path.read_text(encoding="utf-8", errors="replace")
                except OSError as exc:
                    violations.append(f"{dim_name}: cannot read tool_output file: {exc}")
                    continue
                _content_problems = _validate_tool_content(
                    content, tool, dim_name, inline=False
                )
                violations.extend(_content_problems)
                if digests is not None and not _content_problems:
                    from core.quality_gate.evidence_digest import digest_of_file
                    digests[dim_name] = digest_of_file(out_path, source=str(tool_output))
        elif tool_evidence:
            evidence_str = str(tool_evidence).strip()
            if len(evidence_str) < 10:
                violations.append(
                    f"{dim_name}: tool_evidence too short "
                    f"({len(evidence_str)} chars) — must be real tool output snippet"
                )
            else:
                _content_problems = _validate_tool_content(
                    evidence_str, tool, dim_name, inline=True
                )
                violations.extend(_content_problems)
                if digests is not None and not _content_problems:
                    from core.quality_gate.evidence_digest import digest_of_text
                    digests[dim_name] = digest_of_text(
                        evidence_str, source="tool_evidence (inline)"
                    )
        else:
            violations.append(
                f"{dim_name}: requires tool execution but result JSON has neither "
                f"tool_output nor tool_evidence — scores must come from actual tool runs"
            )

    return violations


def _check_tests_failed(
    raw: dict, fr_id: "str | None" = None,
    *, framework_run: "tuple[str, str, int] | None" = None,
) -> list[str]:
    """S4-B: Verify none of THIS FR's own tests are red.

    S4 cross-validates the coverage *percentage* — `_score_pytest(coverage=True)`
    reads `TOTAL … N%` and never looks at how many tests failed — so a gate
    could pass at 91% coverage with 5 red tests. That is what this check is for.

    Round 77 站1: it asks the run S4 just performed. S4 executes `pytest-cov`
    itself (gate1_per_fr.yaml declares `requires_tool_execution: true` for
    test_coverage) and holds the full output; until this round S4-B decided the
    same question by regex over the agent's 500-character `tool_evidence`
    excerpt forty lines later. Round 67 / Round 72's mother pattern: the
    framework computed the truth and the verdict read somewhere else.

    Three cases, and none of them treats unreadable output as clean:

    (a) the harness ran a pytest-family tool and its short summary reconciles
        with its own counts line — the verdict is the framework's, scoped by
        `test_suite_run.select_fr_outcomes` (the same predicate `fr_suite_verdict`
        uses for TDD-GREEN, so the convention has one implementation). The
        agent's `tool_evidence` does not enter into it.
    (b) the harness ran a test tool whose per-test outcomes it cannot read —
        a JS runner, or pytest output it could not reconcile.
    (c) the harness did not run the tool (the agent self-reported below
        threshold, so S4 skipped it).

    (b) and (c) keep the pre-Round-76 rule unchanged: any `N failed` in the
    agent's evidence blocks. That rule is fail-closed, and deliberately not
    replaced with the framework's own whole-suite output for JS — a JS run is
    not per-FR scoped, so blocking on it would hand JS projects the exact
    defect this round removes from Python ones. Round 77 站5 adds the agent's
    own `tests_failed` to that branch: where the framework cannot see, a
    self-declared failure is an admission, and until this round the one field
    the prompt calls REQUIRED had no reader anywhere in the tree.

    Returns list of violation messages (empty = all clear).
    """
    from core.quality_gate.fr_test_scope import (
        declared_tests_failed,
        scoped_test_failures,
    )

    scoped = scoped_test_failures(fr_id, framework_run)
    if scoped is not None:
        mine = scoped[0]
        if mine:
            return [
                f"test_coverage: {len(mine)} of {str(fr_id).strip()}'s own "
                f"test(s) FAILED in the harness's own run — gate cannot pass "
                f"while they are red: {', '.join(sorted(mine))}"
            ]
        return []

    _declared = declared_tests_failed(raw)
    if _declared:
        return [
            f"test_coverage: the result declares tests_failed={_declared} and "
            f"the harness could not measure this FR's tests itself — a gate "
            f"cannot pass on a self-reported red suite. Fix the failures, or "
            f"cite a run the harness can read."
        ]

    breakdown = raw.get("breakdown", {})
    evidence = str(breakdown.get("test_coverage", {}).get("tool_evidence", "") or "")
    if not evidence:
        return []  # S3 already blocks on missing evidence

    m = re.search(r"(\d+)\s+failed", evidence)
    if m and int(m.group(1)) > 0:
        failed = int(m.group(1))
        return [
            f"test_coverage: {failed} test(s) FAILED in tool_evidence — "
            f"gate cannot pass with failing tests. Fix all failures before re-submitting."
        ]
    return []


def _parse_skip_counts(
    raw: dict, framework_run: "tuple[str, str, int] | None" = None,
) -> "tuple[int, int] | None":
    """`(skipped, total)` from the framework's own run, else from the evidence.

    One parse, two readers: the ratio WARN below and the ledger row at the
    finalize call site. Round 46 站2 split them apart because they answer
    different questions — "is coverage computed from a subset?" has a ratio
    threshold, "did any test not run?" does not.

    Round 77 站6: when S4 ran a test tool itself, that run is the source. The
    coverage number this ratio qualifies already comes from it —
    `_score_pytest` reads `TOTAL … N%` out of the same stdout — so numerator
    and denominator now come from one execution rather than two (Round 37 /
    Round 42: the denominator travels with the number). The scope changes
    with the source: the framework's run is the whole suite, the agent's
    excerpt was its per-FR scoped run, and the ledger row records which one
    it read.

    It also removes a way the row could vanish. Round 76 told the agent to put
    the FAILED lines in `tool_evidence` "before the summary line", inside a
    field the same prompt caps at 500 characters; measured, that evicts
    `N passed / N skipped` entirely and this function returns None — so the
    `gate:test-skips` row disappeared for exactly the FRs that had failing
    tests. Round 77 站3 removed the instruction; this removes the dependency.
    """
    from core.quality_gate.fr_test_scope import readable_run_output

    evidence = readable_run_output(framework_run)
    if not evidence:
        breakdown = raw.get("breakdown", {})
        evidence = str(
            breakdown.get("test_coverage", {}).get("tool_evidence", "") or "")
    if not evidence:
        return None
    passed_m = re.search(r"(\d+)\s+passed", evidence)
    skipped_m = re.search(r"(\d+)\s+skipped", evidence)
    if not (passed_m and skipped_m):
        return None
    passed = int(passed_m.group(1))
    skipped = int(skipped_m.group(1))
    total = passed + skipped
    return (skipped, total) if total else None


def _check_test_skip_ratio(
    raw: dict, threshold: float = 0.10,
    framework_run: "tuple[str, str, int] | None" = None,
) -> str | None:
    """W1: Warn when a high fraction of tests are skipped.

    Skipped tests contribute 0 coverage lines.  A skip ratio above *threshold*
    (default 10 %) means coverage is computed from a subset of the suite and
    may miss infrastructure code paths (e.g. DB schema, async sessions).

    This is a **WARN** (not BLOCK) — some projects legitimately skip tests
    that require real external services.

    Scope note (Round 46 站2): this is a statement about *coverage*, and about
    coverage it is honest. It is NOT the enforcer for "a requirement's own
    test did not run" — that is `compute_trace_dimension`'s absent-witness
    rule, which blocks through the traceability dimension. taskq-advance's
    17 skips are 6.25 % of its suite and never tripped this warning, while
    three of its NFRs had guards skipping themselves. Two questions, two
    mechanisms; do not make this one carry the other's weight.

    Returns a warning string, or ``None`` if the skip ratio is within threshold.
    """
    counts = _parse_skip_counts(raw, framework_run)
    if counts is None:
        return None
    skipped, total = counts

    skip_ratio = skipped / total
    if skip_ratio > threshold:
        return (
            f"[WARN] {skipped} of {total} tests ({skip_ratio:.0%}) are SKIPPED — "
            f"skipped tests contribute 0 coverage lines. Coverage score reflects only "
            f"non-skipped tests. Consider mocking infrastructure to run skipped tests, "
            f"or document why the skips are architectural constraints in TODO.md."
        )
    return None


def _gate_dimension_names(ctx: "GateContext") -> frozenset[str]:
    """The dimension names this gate's config declares.

    Round 53 站5a. `ctx.config` is a GateConfig or a plain dict depending on
    the caller, and two places in `finalize_gate` already branch on that to
    get the full entries. This returns names only, and stays separate from
    those two on purpose: they are inline inside long functions and need the
    entries, so folding them together would trade one duplicated branch for a
    parameter that means "which shape do you want".
    """
    if isinstance(ctx.config, dict):
        entries = ctx.config.get("dimensions") or []
    else:
        entries = getattr(ctx.config, "dimensions", None) or []
    names: set[str] = set()
    for entry in entries:
        name = entry.get("name") if isinstance(entry, dict) else getattr(entry, "name", None)
        if name:
            names.add(str(name))
    return frozenset(names)


def _verify_system_reach_block(ctx: "GateContext") -> list[str]:
    """Which replaced boundaries `make verify-system` did not execute for real.

    Round 52 站2. Round 51 站3 recorded that a dimension was scored over a
    suite which replaced a SAB high-risk module before every test in the file,
    and let the number stand with a marker. The obligation this raises is the
    one thing the framework can still ask: the project's own verification
    target — the only command it runs that the test suite did not configure —
    has to execute what the suite replaced.

    Returns [] when there is nothing outstanding AND when the reach could not
    be measured; the ledger row carries the difference. A gate must not be
    blocked by a measurement that did not happen (Round 35 站2), and it must
    not read a measurement that did not happen as a pass either — which is why
    `unmet_obligations` omits the key rather than returning [], and why this
    function branches on the status instead of on the list.

    Never raises: a report about coverage instrumentation is a worse reason to
    stop a gate than the thing it was going to report.
    """
    from core.degradation_ledger import record_degradation
    from core.quality_gate.verify_system_reach import (
        STATUS_MEASURED,
        unmet_obligations,
    )

    # Round 53 站5a: only a gate that runs `execute_verification_target` can
    # have a reach artifact, so only such a gate has this question. Measured on
    # taskq-super's full P1-P8 run: 116 `gate:verify-system-reach` rows, every
    # one "no reach artifact", and correlating each row's `ts` against
    # gate_timestamps.jsonl puts ALL 116 at Gate 1 and none at Gate 2, 3 or 4 —
    # 18.5% of that project's degradation ledger, filed under owner `harness`,
    # asking a gate a question its own config says it cannot answer.
    #
    # Not "quieten the log". Round 46 站1's rule is that abstaining is not
    # passing; a question that was never in this gate's scope was never
    # abstained from, and the gate config is the single source of what a gate's
    # scope is.
    if "execute_verification_target" not in _gate_dimension_names(ctx):
        return []

    try:
        verdict = unmet_obligations(ctx.project_root)
    except Exception as exc:  # pragma: no cover — reporting must not stop a gate
        record_degradation(
            ctx.project_root, "gate:verify-system-reach",
            "reach obligation check failed", f"{type(exc).__name__}: {exc}",
            owner="harness",
        )
        return []

    if verdict["status"] != STATUS_MEASURED:
        record_degradation(
            ctx.project_root, "gate:verify-system-reach",
            "which boundaries `make verify-system` executed is unknown",
            verdict["reason"], owner="harness",
        )
        return []

    for row in verdict.get("unmeasurable") or []:
        record_degradation(
            ctx.project_root, "gate:verify-system-reach",
            f"obligation {row['module']}.{row['attr']} cannot be evaluated",
            row["why"], owner="harness",
        )

    return [
        f"{row['module']}.{row['attr']} is replaced by an autouse fixture in "
        f"the test suite and is never executed by `make verify-system`"
        for row in verdict.get("unmet") or []
    ]
