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
EX_ADVANCE_MANIFEST_CORRUPT = 27
EX_ADVANCE_PUSH_FAILED = 28
EX_ADVANCE_SRS_VOCABULARY_ILLEGAL = 29
EX_ADVANCE_DELIVERABLE_ANCHOR_BROKEN = 30
EX_CI_RED = 31
EX_CI_VERDICT_UNAVAILABLE = 32
EX_GATE_VERIFY_FAILED = 33
EX_ADVANCE_GATE_VERDICT_MISSING = 34
EX_STEP_PRECONDITION_BLOCKED = 35
EX_STEP_REPEATED_FAILURE = 36
EX_ADVANCE_ENTRY_OBLIGATIONS = 37
EX_ADVANCE_UNCOMMITTED_DELIVERABLES = 38
EX_RETIRED_FEATURE_FLAG = 39
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
    EX_FR_STEP_INFRA_ABORT: "run-fr-step: an INFRA_FAIL precondition-block signature was found in the sub-agent's GATE1 output — aborted before dispatching a fix agent at a problem no code change can resolve; repair project state (amend-sab) and re-run",
    EX_STATE_CORRUPT: "[FATAL] .methodology/state.json or quality_manifest.json exists but is not readable/parseable JSON — project data corruption, NOT a harness-methodology bug (see core/state_io.py's StateCorruptError)",
    EX_ADVANCE_MANIFEST_CORRUPT: "advance-phase: quality_manifest.json parses but its structure is corrupt (truncated fr_ids / cleared traceability / wiped gate1) — refusing to commit it; restore from HEAD and re-run",
    EX_ADVANCE_PUSH_FAILED: "advance-phase --push: the handover commit landed locally but `git push` failed — NOT rolled back; fix connectivity/remote and re-run the push command printed in the [BLOCKED] message",
    EX_ADVANCE_SRS_VOCABULARY_ILLEGAL: "advance-phase (P1 exit): SRS.md's machine-readable NFR block uses a `type:` outside ALL_NFR_TYPES or a `dimension:` that names no scored dimension — fix the value in SRS.md; it is refused here rather than in Phase 2, where it would already be locked into an approved deliverable",
    EX_ADVANCE_DELIVERABLE_ANCHOR_BROKEN: "advance-phase: a deliverable's first line no longer starts with the H1 anchor its path declares in DELIVERABLE_ANCHORS — the Phase 1/2 orchestrator reloads it with that anchor and would abort after 3 attempts; fix the H1 in the named file",
    EX_CI_RED: "verify-ci: GitHub Actions reports at least one failing run for the pushed commit — the push landed, the build did not; fix the named job(s) and re-push before advancing",
    EX_CI_VERDICT_UNAVAILABLE: "verify-ci: the CI verdict could not be obtained (no gh, no network, no origin remote, or no run has appeared yet) — INFRA, not a pass; re-run once CI has reported",
    EX_GATE_VERIFY_FAILED: "verify-gate: at least one of the gate's three checks (last_gate, spec-coverage, crg-arch) failed — the verdict is recorded as FAIL in .methodology/gate_verify.jsonl; fix the named check and re-run",
    EX_ADVANCE_GATE_VERDICT_MISSING: "advance-phase: the exit gate has no PASS verdict recorded for the tree being advanced — run verify-gate against this tree; a verdict measured on a different tree is not a verdict for this one",
    EX_STEP_PRECONDITION_BLOCKED: "run-fr-step: the step correctly did nothing because its precondition was not met (a refactor step cannot run on a red baseline) — this is not an agent-logic error and re-dispatching it changes nothing; repair the named baseline failure, or revert the step that produced it, then re-run",
    EX_STEP_REPEATED_FAILURE: "run-fr-step: this (FR, step) pair has already failed with an identical signature as many times as the in-process retry allows — refusing to spend another dispatch on a failure that has not changed; read .methodology/degradations.jsonl for the signature and fix the underlying cause",
    EX_ADVANCE_ENTRY_OBLIGATIONS: "advance-phase: the preflight simulated at the phase being entered reports findings that would block entry there — the [BLOCKED] table names each one by check, rule and file:line. state.json was NOT advanced: a project whose current_phase names a phase its own entry preflight rejects is a state with no truth value. Resolve the listed findings and re-run",
    EX_ADVANCE_UNCOMMITTED_DELIVERABLES: "advance-phase: delivered files differ from HEAD, so the commit about to record this phase does not contain the tree the phase's checks were measured on — the [BLOCKED] list names each file. Harness bookkeeping and the files this command rewrites itself are excluded. Commit the listed work (or gitignore it, if it is generated at runtime) and re-run",
    EX_RETIRED_FEATURE_FLAG: "run-gate: .methodology/harness_config.json still switches a dimension off (features.<key>: false). No dimension can be excluded from a gate any more — a dimension is measured, or the gate blocks and the run routes to repair. Remove the named key; if the tool genuinely cannot run here, that is an INFRA block with a repair route, not a scoring exemption",
    EX_HARNESS_BUG: "[HARNESS-BUG] — a defect in harness-methodology's own code: an uncaught exception at the crash boundary (core/errors.py), or the same banner surfacing through a sub-agent's GATE1 output (run-fr-step); not a project quality failure, and no re-run will clear it",
    EX_KEYBOARD_INTERRUPT: "Interrupted — Ctrl-C, or SIGTERM from `kill <PID>` "
                           "(Round 66: the run unwinds and reaps what it started)",
}
