"""Exit code registry (Round 13 站0) — single source of truth for every
integer harness_cli.py's cmd_* handlers `return` or `sys.exit()`.

Before this module, the codes were documented only in harness_cli.py's
top-of-file docstring, which had drifted out of sync with the actual
`return N` sites in cli/*.py (12/14/17/18/19/20/22/24 were all in use but
undocumented). tests/test_exit_code_registry.py enforces both directions:
every code actually returned by cli/*.py must appear in REGISTRY, and
harness_cli.py's docstring "Exit codes" section must match REGISTRY.

KNOWN INCONSISTENCIES (documented, not fixed this round — renumbering is a
larger compatibility-risk change than a documentation pass; anything that
already scripts against a specific exit code today keeps working):
  12 means BOTH "phase_truth_passed missing in state.json" (phase_cmds.py's
     advance-phase FR-truth check) AND "SAB architecture violation"
     (advance-phase's pre-advance SAB check) — two unrelated precondition
     blocks share one code.
  17 means BOTH "finalize-gate not called for a required gate" AND
     "unresolved deferred fixes in deferred_fixes.md" — same situation.
  18 means BOTH "ruff linting failure" AND "submodule safety violation".
  19 means BOTH "sync-harness SubmoduleSyncError" (a different subcommand
     entirely) AND "mypy type-safety failure" (advance-phase).
All four are internally consistent in ONE respect: every site prints a
`[BLOCKED]`/`[FATAL]` message identifying the specific precondition before
returning, so the exit code alone is never the only signal — but a script
branching purely on the integer cannot distinguish the sub-cases.
"""

from __future__ import annotations

EX_OK = 0
EX_FAIL = 1
EX_GAP_ANALYSIS_CRITICAL = 2
EX_GATE4_PREREQ_BLOCK = 5
EX_FINALIZE_COMMIT_NOT_LANDED = 6
EX_MISSING_DELIVERABLES = 8
EX_COVERAGE_100_REQUIRED = 9
EX_PAUSE_AWAIT_EVAL = 10
EX_PHASE_TRUTH_LOW = 11
EX_ADVANCE_PRECONDITION_BLOCK = 12
EX_AGENT_B_APPROVALS_INCOMPLETE = 13
EX_GATE1_LIVE_COVERAGE_FAIL = 14
EX_NEXT_PHASE_PLAN_MISSING = 15
EX_RETIRED_CONSTITUTION_GATE = 16  # tombstone (減法 T3) — do not reuse this number
EX_ADVANCE_GATE_NOT_FINALIZED = 17
EX_ADVANCE_QUALITY_CHECK_FAIL = 18
EX_SYNC_OR_TYPE_CHECK_FAIL = 19
EX_SECRETS_SCAN_FAIL = 20
EX_SCOPE_VIOLATION = 21
EX_GHOST_DETECTED = 22
EX_DISPATCH_STRUCTURALLY_BROKEN = 23
EX_SUBSTRATE_PREFLIGHT_FAIL = 24
EX_FR_STEP_INFRA_ABORT = 25
EX_STATE_CORRUPT = 26
EX_HARNESS_BUG = 70
EX_KEYBOARD_INTERRUPT = 130

# code -> one-line semantic description (rendered into harness_cli.py's
# docstring by tests/test_exit_code_registry.py's diff check, and into
# docs/ERROR_HANDLING.md's exit-code table).
REGISTRY: dict[int, str] = {
    EX_OK: "All phases complete / command succeeded",
    EX_FAIL: "Hard failure (investigate error)",
    EX_GAP_ANALYSIS_CRITICAL: "run-gap-analysis: critical gaps detected (distinct from hard error)",
    EX_GATE4_PREREQ_BLOCK: "Gate 4 prerequisites block (A2/A3/A5 schema, B2 score files)",
    EX_FINALIZE_COMMIT_NOT_LANDED: "finalize-gate: gate passed but git commit did not land (manifest rolled back) — fix and re-run",
    EX_MISSING_DELIVERABLES: "Missing deliverables block — required artifacts not found on disk or not git-tracked",
    EX_COVERAGE_100_REQUIRED: "advance-phase: 100% coverage required on 03-development/src not met (TDD-governed source)",
    EX_PAUSE_AWAIT_EVAL: "PAUSE — Claude must evaluate gate; run finalize-gate then re-run pipeline",
    EX_PHASE_TRUTH_LOW: "Phase Truth < 90% (HR-11); fix and re-run with --phase-from N",
    EX_ADVANCE_PRECONDITION_BLOCK: "advance-phase precondition block — phase_truth_passed missing OR SAB architecture violation (see printed message)",
    EX_AGENT_B_APPROVALS_INCOMPLETE: "advance-phase: Agent B approvals incomplete for this phase",
    EX_GATE1_LIVE_COVERAGE_FAIL: "advance-phase: live pytest --cov could not run, or coverage below the manifest's recorded threshold",
    EX_NEXT_PHASE_PLAN_MISSING: "advance-phase: next phase's plan file not found — run generate-next-plan first",
    EX_RETIRED_CONSTITUTION_GATE: "RETIRED (減法 T3) — constitution keyword scoring is on-demand only; kept as a tombstone, do not reuse this number",
    EX_ADVANCE_GATE_NOT_FINALIZED: "advance-phase precondition block — finalize-gate not called for a required gate OR unresolved deferred_fixes.md items (see printed message)",
    EX_ADVANCE_QUALITY_CHECK_FAIL: "advance-phase precondition block — ruff linting failure OR submodule safety violation (see printed message)",
    EX_SYNC_OR_TYPE_CHECK_FAIL: "sync-harness: SubmoduleSyncError, OR advance-phase: mypy type-safety failure (see printed message)",
    EX_SECRETS_SCAN_FAIL: "advance-phase: gitleaks secrets scan failed or timed out",
    EX_SCOPE_VIOLATION: "Scope violation: untracked diagnostic script(s) at repo root; move to .sessi-work/tmp or delete, then re-run advance-phase",
    EX_GHOST_DETECTED: "GHOST_DETECTED — agent claimed work but made no substantive code change (see .sessi-work/ghost_detected/)",
    EX_DISPATCH_STRUCTURALLY_BROKEN: "Sub-agent dispatch is structurally broken (e.g. claude.ai connectors disabled) — not a retryable failure",
    EX_SUBSTRATE_PREFLIGHT_FAIL: "run-phase: spawn-substrate preflight probe FAILED — sub-agents cannot run pytest/git in this environment",
    EX_FR_STEP_INFRA_ABORT: "run-fr-step: a [HARNESS-BUG] banner or INFRA_FAIL precondition-block signature was found in the sub-agent's GATE1 output — aborted before dispatching a fix agent at a problem no code change can resolve",
    EX_STATE_CORRUPT: "[FATAL] .methodology/state.json or quality_manifest.json exists but is not readable/parseable JSON — project data corruption, NOT a harness-methodology bug (see core/state_io.py's StateCorruptError)",
    EX_HARNESS_BUG: "[HARNESS-BUG] — an uncaught exception in harness-methodology's own code (see core/errors.py); not a project quality failure",
    EX_KEYBOARD_INTERRUPT: "Interrupted (Ctrl-C)",
}
