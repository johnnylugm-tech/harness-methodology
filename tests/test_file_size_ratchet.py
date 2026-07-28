"""Production-file line-count ratchet — god-file growth must be deliberate.

Round 3 claims 2/4 residue: the repo's largest files (harness_bridge,
gate_cmds, phase_cmds, fr_cmds, ...) are safety-critical surfaces
deliberately NOT decomposed this round (the M2-M4 plangen split handled the
one with a proven drift wound). This ratchet does for file growth what
test_patch_discipline does for private patches, with one deliberate
difference spelled out here: line counts legitimately grow, so ceilings MAY
be raised — but only in the same commit as the growth, with the reason in
the commit message. The product is diff-visibility of growth, not an
absolute cap. A file not listed in _LINE_CEILING must stay below
_GOD_FILE_THRESHOLD entirely; lowering a ceiling after shrinking a file is
manual, same as the patch ratchet.
"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

_SCAN_DIRS = ("cli", "core", "harness", "scripts", "detection")
_GOD_FILE_THRESHOLD = 900

# Snapshot 2026-07-11 (Round 3 Station L, after the M2-M4 plangen split —
# generate_full_plan.py itself is down to a ~250-line facade and off this
# list; the split's two large products are honestly listed).
_LINE_CEILING: dict[str, int] = {
    # 2026-07-12: +31 lines — env-check prompt teaches optional_missing vs
    # required distinction (fix false fabrication flag on vars with baked-in
    # config defaults). Example uses generic DATABASE_URL, not project-specific.
    # 2026-07-16: +79 lines — Round 12 站3b: _check_infra_fail_pollution
    # (+ finalize_gate interception before S3) rejects zero scores whose
    # evidence carries a run-gate PRECONDITION-block signature — the
    # 2026-07-16 phantom-module incident wrote 3 fake quality zeros into
    # the manifest and dispatched CODE-FIX at healthy code.
    # 2026-07-17: +11 lines — Round 13 站1: exception-swallow ratchet paydown —
    # SAD.md §5 parse failure now records a degradation entry (SAB baseline
    # consequence is significant enough for the persistent ledger, not just a
    # print) + _trigger_hooks failure now logs via the file's own local logger.
    # 2026-07-21: +9 lines — fix/spec-cap-list-set-mismatch: replaced the
    # hardcoded *.py-only rglob scan with the shared language-aware
    # _get_test_directories/_scan_test_functions helpers, and switched the
    # numerator from a dedupe-set to a row-based count (matches the
    # denominator's row-based list; a parametrized case legitimately repeats
    # its Test Function name across multiple TEST_SPEC.md rows, so a
    # dedupe-set numerator against a non-deduped list denominator
    # mathematically capped test_coverage below 100% even when every
    # required test existed).
    # 2026-07-26: +13 lines — traceability dim threshold-mismatch fix:
    # merged_pct (min of 4a/4b/4c, each with a DIFFERENT threshold) was being
    # compared against the flat 4a threshold (100%) in both the per-dim
    # override (_override_traceability_dim_score) and the YAML-sourced
    # _dim_thresholds lookup, so a Gate 2 run with 4b/4c legitimately ≥60%
    # but <100% was misreported as FAIL even though compute_trace_dimension's
    # own `passed` field said PASS. Now both consumers use the function's new
    # `threshold_effective` (the threshold of whichever component is binding
    # the min), and the YAML override is dropped for this dim so it can't
    # shadow the fix.
    # 2026-07-26: +6 lines — same fix, follow-up: preserve a
    # gate_score_overrides threshold floor-raise (applied to dims before this
    # override runs) instead of unconditionally overwriting threshold with
    # threshold_effective, which would have silently discarded a project's
    # intentional stricter-than-default traceability floor.
    # 2026-07-27: +11 lines — Round 24: EnvCheckContext.evaluation_prompt()'s
    # CLASSIFICATION RULE gains a third bucket for test/dev-only opt-in flags
    # (env vars a project's docs describe as off/disabled/rejected by default
    # in production) plus a second worked example. Closes the gap that let
    # the same documented env var on the same unchanged project state
    # classify as optional_missing in one workflow run and required in
    # another (observed: wf_8b3a3f79-12b vs wf_4fe2125c-48d) — the prior rule
    # only distinguished "has a documented default value" vs "no default",
    # with no bucket for "intentionally absent by design" opt-in flags.
    # 2026-07-27: +36 lines — Round 21 站1: DA-waiver necessity is adjudicated
    # HERE now (against the framework's own post-CRG scores) instead of in
    # cli/gate_cmds.py before scoring, where the one waivable dimension is
    # always null. Adds the adjudication call + its BLOCK path, and lifts the
    # thrice-repeated effective-threshold expression into one local helper so
    # the verdict, the failing-dimension report, and the waiver decision cannot
    # drift onto different bars.
    # 2026-07-27: +22 lines — Round 21 站2: finalize_gate validates the gate
    # result against harness_gate_result.schema.json before scoring from it.
    # The schema had never been loaded by any code, so it drifted into
    # describing a document no run produces while consumers guessed field
    # names — the direct cause of the dead DA-waiver safeguard above.
    "harness/harness_bridge.py": 3150,
    # 2026-07-12: +2 lines net — Round 6 站2: _check_sab_module_alignment's
    # unregistered-direction scan now delegates to sab_amender.
    # discover_modules_at() (removed inline loop, +docstring paragraph
    # explaining the delegation) instead of a locally re-implemented rglob
    # loop that had silently diverged (never skipped __pycache__).
    "cli/gate_cmds.py": 2569,
    # 2026-07-11: +26 lines — cmd_advance_phase now refreshes the
    # traceability attestation before its handover commit (mirrors the
    # existing push_cmds.py refresh in push-checkpoint/push-milestone), and
    # the P2-A SAB pre-check now matches DriftItem.actual instead of a dead
    # description substring.
    # 2026-07-12: +14 lines — Round 5 建議2站2: cmd_plan_phase/cmd_plan_all
    # migrate from cwd-relative `from scripts.generate_full_plan import` to
    # load_harness_script("generate_full_plan.py") (same P6/A1 bug class,
    # never swept for this module).
    # 2026-07-13: +25 lines — STAGE_PASS generation-order fix: the
    # "Always-regenerate Phase{N}_STAGE_PASS.md" block moved from mid-function
    # (right after HR-11) to the true end of _advance_prechecks (after Agent B
    # approvals / TDD / SAB drift / submodule guard all pass), so it can pass
    # truth_override=True to _generate_stage_pass instead of reading
    # state.json.phase_truth_passed before _advance_fsm has written it. Added
    # a small early "ensure exists" pass at the block's old position so the
    # internal Phase Auditor call (a few lines later) doesn't CRITICAL-fail
    # its own C2 check on a first-ever advance.
    # 2026-07-14: +7 lines — P2->P3 manifest regen's SRS.md fallback scan
    # (Fix 5, FR-heading bug class 6th site) now imports SRS_SUBSECTION_PREFIX
    # from core.quality_gate.parsers so `### 3.1 FR-01` TOC-numbered headings
    # are detected, matching the fix already applied at spec_alignment.py /
    # phase_hooks.py / spec_coverage.py / artifact_parsers.py; the fail-fast
    # error message's regex literal was updated to match.
    # 2026-07-16: +93 lines — Round 12 站0b: _run_substrate_probe (spawn-
    # substrate preflight at run-phase entry for PER_FR_GATE1_PHASES, with
    # 6h success cache + --skip-substrate-probe escape hatch). One 90s
    # probe replaces the 2026-07-16 failure mode where the per-FR loop
    # burned 140 dispatches / ~2.5h on FR-01 discovering spawned agents
    # could not execute pytest/git.
    # 2026-07-17: +23 lines — Round 13 站1: exception-swallow ratchet
    # paydown — 12 previously-unlogged broad excepts now print a [WARN]
    # (2 via core.degradation_ledger for the state.json-corruption cases).
    # 2026-07-17: +12 lines — Round 13 站2c: 8 agent-facing [BLOCKED] messages
    # gained a "Fix:"/re-run remediation line (blocked-message-contract scan).
    # +15: Round 18 站3 added _attestation_content_still_current, the slow-path
    # adjudication that stops the mtime probe from manufacturing no-op commits.
    # 2026-07-28 (Round 22 站2): +33 — _advance_prechecks runs
    # PhaseHooks.preflight_manifest_integrity() first (exit 27), with the
    # restore command in its [BLOCKED] message. Relocated from workflow JS,
    # where it cost one sub-agent dispatch per advance round AND left every
    # non-workflow caller (human, resumed session, CI) unprotected.
    "cli/phase_cmds.py": 2864,  # 2026-07-27: +6 — Round 29: run-phase auto-skips the spawn-substrate preflight probe when CI/GITHUB_ACTIONS is set — CI never dispatches an interactive per-FR loop, so the probe (which requires the claude CLI, never present there) can only ever fail (sized to current 2825→2831). 2026-07-26: +80 — Round 14 A2/A4: cmd_advance_phase now previews P(N+1) entry blocking via PhaseHooks.preview_next_phase_blocking(), threads obligations into HandoverGenerator.write + _advance_fsm, and replaces "Ready to begin Phase N+1" with a pointer to the obligations table (sized to current 2768). 2026-07-26: +33 — Round 15 §2: new cmd_preview_next_phase() + preview-next-phase subparser — a read-only P(N+1) obligation query that never writes state.json/HANDOVER.md/a commit, usable before P(N) exit gate even passes (sized to current 2813).
    # 2026-07-11: +35 lines — _fr_step_already_done's idempotency grep is now
    # scoped to the current phase's lineage boundary (read from tracked
    # state.json phase_completed), fixing a false "already done" skip on
    # reset-and-rerun projects (TDD-IMPROVE had no secondary evidence check).
    # 2026-07-12: +39 lines — dispatch failures now fail fast on the
    # deterministic "claude.ai connectors are disabled" signature (shared
    # _abort_dispatch_structurally_broken() helper, 2 call sites) instead of
    # exhausting max_fix_rounds against an environment that cannot ever
    # succeed (P3 2026-07-12 FR-04 GATE1: 5.4h silent retry loop before the
    # external workflow watchdog aborted).
    # 2026-07-12: +26 lines — COVERAGE-FIX's measurement command now scopes
    # to the FR's own owned source via the shared
    # core.quality_gate.cov_utils.resolve_fr_scoped_src_files() (same
    # resolver run-gate --fr-id already uses), instead of the whole
    # 03-development/src tree — the whole tree was unsatisfiable while
    # sibling FRs' stub modules sit at 0% coverage (P3 2026-07-12: FR-01/
    # FR-02 both BLOCKED after 2 no-progress rounds chasing the wrong
    # denominator).
    # 2026-07-13: +21 lines — Round 9 station 2: run-fr-step's tunables now
    # read the harness_config `values` section (permission_mode /
    # max_fix_rounds / fr_step timeout / step_max_turns overlay with
    # unknown-step WARN); precedence chain unchanged and locked by
    # tests/test_fr_cmds_values_wiring.py.
    # 2026-07-13: +3 lines — FIX-O: cmd_run_fr_step's first-dispatch error
    # branch (TDD-RED/GREEN/IMPROVE + GATE1's pre-fix-loop attempt) now calls
    # _is_connector_disabled_failure/_abort_dispatch_structurally_broken,
    # mirroring the two other dispatch sites in this file that already had it.
    # 2026-07-13: net -2 lines — Bug D: SRS.md path resolution was hard-coded
    # to the wrong location (.methodology/SRS.md) in two call sites while
    # _fr_step_preflight's own fallback list (never reused) had the correct
    # 01-requirements/SRS.md entry (P3 FR-05: resume-fr-phase's suggested
    # command and TDD-RED's prompt builder both failed preflight with "SRS.md
    # not found"). Both sites + preflight's own fallback loop now call the
    # existing ProjectLayout(project).srs_path (single source of truth already
    # used by 14+ other call sites across the harness — phase_cmds.py,
    # harness_bridge.py, spec_alignment.py, ...) instead of guessing among
    # hard-coded candidate strings; dropped the wrong --srs flag from
    # resume-fr-phase's printed command.
    # Bug F: TDD-RED's prompt gives sub-agents no instruction for a test file
    # that already exists but is uncommitted (P3 FR-05: a sub-agent found
    # test_fr05.py surviving a mid-flight reset, chose "review, don't
    # overwrite", and never ran the commit step) — step 1 now says explicitly
    # that an existing-but-uncommitted file still requires completing step 5.
    # 2026-07-15: +5 lines — Fix H-B (defense-in-depth for H-A): _DISPATCH_ERROR_STATUSES
    # gains two inner-JSON semantic no-op signatures ("AWAITING_CONFIRMATION",
    # "NOTHING_TO_DO") so direct callers that reflect an inner-agent status
    # string are caught even if AgentSpawner._validate_inner_json is bypassed
    # (cf. P3 2026-07-15 FR-03 TDD-RED where transport exit 0 + status="AWAITING_CONFIRMATION"
    # silently passed every per-FR slot at the cost of one wasted FR).
    # 2026-07-15: +6 lines — Fix H-C: import _COMMIT_REQUIRED_STEPS SSOT from
    # core.agent_spawner (the validator-side frozenset introduced in H-A) and
    # replace 2 inline commit-required lists in run-fr-step (lines 738-739 +
    # 750-751) so they stay in sync with the validator. Net: a single SSOT
    # defines "steps that must produce a commit" and every consumer reads it.
    # 2026-07-15: +1 line — Fix H-F: the fix-loop dispatch in run-fr-step
    # (the single caller that knows its current round) now passes
    # `retry_round=fix_round` to spawner.spawn() so sessions_spawn.log
    # entries identify which fix iteration produced them.
    # 2026-07-15: +36 lines — Fix H-H (P3 2026-07-15 round 4): the first
    # dispatch for TDD-RED/GREEN/IMPROVE/MIRROR/amend-sab/ORCH-POST (all
    # _COMMIT_REQUIRED_STEPS except GATE1/GATE1-DELTA, which already had
    # their own fix-round retry loop) previously had zero retry on any
    # dispatch ERROR — production sessions_spawn.log evidence showed this
    # permanently killed an FR's progress on a single transient failure.
    # Wraps the dispatch in a bounded (2-attempt) plain re-dispatch loop
    # (identical prompt, no failure classification — unlike GATE1's
    # CODE-FIX/LINT-FIX routing) plus the new _STEP_RETRY_ATTEMPTS constant.
    # 2026-07-16: +11 lines — Round 12 站0a/0d: dispatch-prompt commands
    # switched to allowlist-compatible `python3 -m` module forms (guarded by
    # tests/test_dispatch_prompt_command_forms.py), and
    # _resolve_phase3_context's docstring now records the measured
    # --setting-sources semantics matrix ("project" also loads the USER's
    # global CLAUDE.md — the leak that stalled headless agents on
    # 2026-07-16) with setting_sources pinned to "".
    # 2026-07-16: +6 lines — Round 12 站3b: GATE1 evaluator STOP RULE
    # gains the INFRA_BLOCKED branch (run-gate precondition block → do NOT
    # write zeros; report the verbatim BLOCK) ahead of the per-tool
    # score=0 rule.
    # 2026-07-17: +16 lines — Fix: GATE1's record_gate_timestamp() append to
    # gate_timestamps.jsonl is now landed with a scoped commit immediately,
    # instead of being left dirty for the very next dirty-tree guard to
    # misreport as "commit did not land" (FR-01 GATE1 false-FAIL repro).
    # 2026-07-17: +11 lines — Round 13 站1: exception-swallow ratchet
    # paydown — 10 previously-unlogged broad excepts now print a [WARN]
    # diagnostic (or, for the CRG-search/git-sha sites, a one-line reason).
    # 2026-07-17: +74 lines — Round 13 站2a/2b: _classify_infra_or_harness_bug
    # + _abort_dispatch_infra_or_harness_bug (HARNESS_BUG/INFRA short-circuit
    # in the fix-round loop — do not dispatch CODE-FIX at a problem no code
    # change can resolve) + the UNKNOWN-exhausted hint at loop exhaustion.
    # 2026-07-24: -839 lines — Round 17 站4 (finding D): _build_fr_step_prompt extracted into cli/fr_prompts/ façade package.
    "cli/fr_cmds.py": 1980,
    # 2026-07-21: +4 lines — review fix on fix/fr-step-already-done-cascade: reverted the sub-change that relaxed `if not committed: return False` to only fire for GATE1/GATE1-DELTA (it let TDD-RED/TDD-GREEN mark themselves done from a leftover, uncommitted artifact alone — reproduced live; commit evidence is a hard requirement again for every step). The GATE1 sentinel+quality_complete cascade already covers the phase-boundary scenario (FR-02 GREEN commits pre-dating the boundary) that relaxation was meant to fix, so no expressiveness is lost. Also replaced the multi-tag docstring scan's 4 unanchored substring patterns with a `[...]`-bracket-anchored, exact-tag-set match (`re.findall(r"\[([^\]]*)\]", text)` + membership check) — the substring version could false-positive match an unrelated prose comment like "# see FR-03, FR-09" with no enclosing brackets at all (also reproduced live).
    # 2026-07-21: +15 lines — fix/round-18-dispatch-ssot (Bug B): import `PRAGMA_NO_COVER_ALLOWLIST` alongside `PRAGMA_NO_COVER_GUIDANCE` and render the allowlist verbatim in the COVERAGE-FIX prompt so future widening of the tuple auto-propagates; replace the contradictory `raise NotImplementedError` example with one that matches the SSOT (`except BaseException: pass`).
    # 2026-07-21: +1 line — fix/round-18-dispatch-ssot (Bug A): add `"AMEND-SAB"` key to `_FR_STEP_COMMIT_PATTERNS` so `_fr_step_already_done` short-circuits a re-run whose amend-sab commit is already in git log.
    # 2026-07-24: +7 lines — Round 17 站1 (finding A): import _GATE_DIMENSION_STANDARD and source the GATE1 prompt's linting/type_safety/test_coverage threshold floor from it, instead of a hand-copied `max(90.0/85.0/80.0, override)` — a second, unbound copy of gate1_per_fr.yaml's thresholds (parity now enforced by tests/test_prompt_gate_parity.py). This peak is temporary: 站4's per-step façade split moves the ~719-line `_build_fr_step_prompt` out of this file and harvests the ceiling back down.
    # 2026-07-24: +45 lines — Round 17 站2 (finding B): `_abort_no_progress_with_self_doubt` — when the fix-round loop hits 2 consecutive no-progress rounds, record the inescapable loop to the degradation ledger (was a silent `return 2`, invisible to run-report) and emit a gate-bug self-doubt channel (a deterministic same-error loop that survives 2 fix rounds may be a harness gate-calculation bug like the #20 spec-cap class, to be reported [HARNESS-BUG] not code-fixed forever). This is a fix-round helper, not the prompt builder, so 站4's split does NOT harvest it — durable finding-B observability growth.
    # 2026-07-21: +25 lines — review fix on fix/sab-sync-improve-to-gate1: the AMEND-SAB delegation branch returns before the general post-step dirty-tree guard / `_COMMIT_REQUIRED_STEPS` check can ever run (early return), and that SSOT set stores this step lowercase while `step` here is always upper-cased, so neither backstop would fire even if reached. Added a dedicated post-call `git status --porcelain -- .methodology/SAB.json` check that BLOCKs (exit 6) when `cmd_amend_sab` mutated SAB.json but it was left uncommitted, instead of only fixing the (now-incorrect) comment that claimed those backstops applied.
    # 2026-07-21: +15 lines — fix/sab-sync-improve-to-gate1: deterministic `amend-sab` dispatch branch (`run-fr-step --step amend-sab` delegates to `cmd_amend_sab` without spawning an LLM; SSOT-bridged from `agent_spawner._COMMIT_REQUIRED_STEPS` into the argparse `choices`) + extended comment block.
    # 2026-07-18: +55 lines — Fix 1a: TDD-RED subprocess coverage ceiling warning in INTEGRATION FR GUIDELINES (~22 lines) + Fix 1b: COVERAGE-FIX subprocess detection with in-process test guidance (~28 lines) + Fix 2: dynamic threshold overrides from quality_manifest gate_score_overrides (~5 lines)
    # 2026-07-17: +30 lines — dirty-tree guard bug fix: pre-step `git status --porcelain` baseline (set, captured under the same `step in _COMMIT_REQUIRED_STEPS` gate as the post-step guard) + the guard itself swapped from whole-tree strip to sorted(post - pre) diff + extended comment block. Mirrors the pre-dispatch _pre_step_sha + detect_ghost_changes idiom below for working-tree granularity instead of commit-level.
    # 2026-07-12: +3 lines — Round 5 建議2站2: same load_harness_script
    # migration for the parse_srs_fr_sections/parse_sad_modules call sites.
    # 2026-07-13: +7 lines — audit-phase subparser gained a `description=`
    # clarifying it must run BEFORE advance-phase for a phase-scoped C10
    # result (no workflow JS ever calls it automatically).
    # 2026-07-24: +1 line — pyright type narrowing fix for L647 trig union.
    "core/quality_gate/red_assertion_check.py": 1020,  # 2026-07-26: +6 — Round 14 B1: SubAssertion gains fulfill_phase field (1 dataclass line + 5-line docstring note) for the Direction-B TEST_SPEC Properties fulfill-phase schema (sized to current 1011).
    # 2026-07-17: +10 lines — Round 13 站1: exception-swallow ratchet
    # paydown — 13 previously-unlogged broad excepts now print a [WARN]
    # diagnostic.
    "cli/project_cmds.py": 1986,  # 2026-07-27: +13 — Round 29: _check_content_quality gained two per-file-type exemptions (MAINTENANCE_LOG.md from the section-count floor, TEST_RESULTS.md from the FR-ref rule) — both files' harness-generated canonical shape was being flagged suspicious by a one-size-fits-all heuristic (sized to current 1973→1986). 2026-07-21: +5 lines — fix/init-project-harness-root-path: Add explanatory comment for __file__.parent.parent dynamic path resolution.
    # 2026-07-21: +38 lines — fix/round-18-dispatch-ssot (Bug C): wrap cmd_amend_sab with a deterministic-tool sessions_spawn.log entry written from the mutation site (covers all callers — standalone amend-sab subcommand + run-fr-step amend-sab delegation + any future caller). Splits the function body into _cmd_amend_sab_impl returning (rc, outcome_tag) and a new _log_amend_sab_outcome helper that mirrors AgentSpawner._log_dispatch's swallowing try/except.
    # 2026-07-15 R3: cmd_amend_sab gained PHANTOM block + --strict (~50 lines)
    # 2026-07-16: Round 12 站0 — agent_spawner crossed the unlisted-file
    # threshold (900) with three additions: _extract_dispatch_error /
    # _denoise_cli_stderr (站0c error-capture denoise — closes the 76×
    # banner-only observability black hole), _UNATTENDED_PREAMBLE +
    # --append-system-prompt (站0d isolation against the measured
    # setting-sources user-CLAUDE.md leak), and preflight_substrate()
    # (站0b probe). All three are dispatch-substrate concerns that belong
    # with spawn(); splitting them out would separate the probe from the
    # substrate it measures.
    # 2026-07-17: +46 lines — Round 14 站0: claude -p envelope cost/turns/
    # usage fields (total_cost_usd, num_turns, duration_api_ms, usage
    # sub-keys) were parsed then discarded — _extract_envelope_metrics +
    # its two module constants, plus the _envelope threading through
    # spawn()'s three post-JSON-parse _log_dispatch call sites and
    # _log_dispatch's own new parameter, are the capture.
    # 2026-07-18: +45 lines — GATE1/GATE1-DELTA evaluation blocked empty commit
    # fix (pass=false/commit=null is a normal fail, not an ERROR) and the
    # sub-agent --allowedTools Bash,Read,Edit,Write fix for upstream bug 37442.
    # 2026-07-18 (Fix H-2): +53 lines — _extract_inner_result_json: the CLI
    # envelope's "result" field is a free-text string, not the sub-agent's
    # own JSON reply; _validate_inner_json and spawn()'s success-path
    # "commit" field were both reading status/commit/pass straight off the
    # envelope (always absent there), so this new helper unwraps it first —
    # see the function's own docstring for the sessions_spawn.log evidence.
    # 2026-07-26: +18 lines — Round 19 站1. Two INFRA signatures the CLI
    # actually emits ("stream idle timeout", "session limit") plus the comment
    # recording that they came from a real corpus rather than guesswork, and
    # _log_dispatch now writing `inner_status` — the field core.failure_modes
    # ._is_semantic_noop reads and which had never been emitted, leaving that
    # rule unable to match any real entry.
    # 2026-07-26: +33 lines — Round 19 站2: _envelope_metrics_from_stdout, and
    # wiring it into the non-zero-exit branch. That branch already json.loads()
    # its stdout to lift the error text (_extract_dispatch_error) and threw the
    # cost/token fields sitting in the same dict away, so failed dispatches
    # logged no cost: 2 of taskq's 19 failures carried one against 50 of 50
    # successes, while those failures burned 1.30h of wall clock.
    "core/agent_spawner.py": 1135,
    # 2026-07-12: +2 lines — Round 5 exception-swallow ratchet: GitHubFetcher/
    # LocalFetcher.get_file_content now log the swallowed decode/read error.
    "scripts/phase_auditor.py": 1848,
    # 2026-07-13: +5 lines — Round 10 站4: P2 Agent B checklist (both
    # _AGENT_B_CHECKS[2] and the SAD.md deliverable's own "checks" list)
    # gains a SEC-block-complete item; P4 hunt step text notes threat_model
    # targets from bug-hunt-targets.
    # 2026-07-14 (+2): _deliverable_ab_block templates updated to 3-layer
    # defense — remaining "NO access / paste full content" → Bash-cat prose.
    # 2026-07-14 (+14, 2nd round): _dynamic_fr_template_block (the function
    # plan-all actually calls, dynamic=True always) was missing ORCH-POST
    # (spec-coverage-check 40% + amend-sab) and the NFR-annotation reminder
    # that only ever existed in the static-mode-only _fr_dev_steps/
    # _fr_carryforward_steps — added for parity; also dropped the invalid
    # `--phase` arg from its check-test-mirrors-spec line (same argparse bug
    # independently found in phase3-implementation.js).
    # 2026-07-14: +3 lines — _phase_advance_step now documents the Sync-phase
    # post-advance `git push origin main` (all 8 workflow JS files already do
    # this; the generated plan prose never described it — SSOT fix, generator
    # source, not the 8 generated phaseN_plan.md files by hand).
    # 2026-07-28 (Round 22 站1): +12 lines — _orch_post_once_step() extracted so
    # `amend-sab` (no --fr-id, idempotent by construction) runs ONCE after the FR
    # loop instead of once per FR. Net effect on the *generated* plan is a
    # reduction; the generator grows by the new helper + its rationale docstring.
    "scripts/plangen/blocks.py": 1694,
    # 2026-07-11: +3/+6 lines — new check_module_fr_coverage gate (module/FR-NFR
    # ownership drift between TRACEABILITY_MATRIX.md's own §5.3 and
    # SPEC_TRACKING.md's §5) wired into preflight_artifact_consistency
    # (phase_hooks.py) and cmd_check_artifact_consistency (check_cmds.py),
    # mirroring the existing check_forward_refs/check_nfr_adr_coverage wiring.
    # 2026-07-12: +1 line — Round 6 站1: preflight_sab_check now imports
    # sab_amender.sab_module_candidate() instead of a locally-duplicated
    # dict-unwrap inline.
    # 2026-07-13: +11 lines — Round 10 站3: preflight_artifact_consistency
    # now also runs check_security_design (SAD.md §6 STRIDE-lite threat
    # model completeness).
    # 2026-07-14: +3 lines — preflight_fr_spec_consistency now imports
    # core.quality_gate.parsers.SRS_SUBSECTION_PREFIX instead of a
    # locally-duplicated regex fragment (same SSOT fix as spec_alignment.py /
    # artifact_parsers.py / spec_coverage.py for the subsection-numbered
    # FR-heading bug class).
    # 2026-07-17: +1 line — Round 13 站1: exception-swallow ratchet paydown —
    # the attestation-check except now prints its error instead of silently
    # discarding it.
    # 2026-07-21: +12 lines — pragma-allowlist drift fix: _audit_pragma_no_cover's
    # allowlist is now a named PRAGMA_NO_COVER_ALLOWLIST/PRAGMA_NO_COVER_GUIDANCE
    # pair (single source of truth) instead of an inline string literal, so
    # cli/fr_cmds.py's COVERAGE-FIX prompt can import and interpolate the same
    # guidance GATE1 actually enforces instead of hand-writing a broader,
    # unsynchronized allowlist (was whitelisting `if __name__ == "__main__":`
    # pragmas that GATE1 always rejected — guaranteed no-progress BLOCKED rounds).
    "core/phase_hooks.py": 1900,  # 2026-07-26: +167 — Round 14 A1 + B3: PhaseHooks gains preview_next_phase_blocking(next_phase) (~50 lines: Obligation dataclass, _DELAYED_BLOCKING_PREFLIGHTS frozenset, _obligations_from_preflight() helper covering property_spec / reliability_lint / generic fallback, simulation-driver method with stdout suppression), and preflight_property_spec rewires from hardcoded phase>=4 to dynamic max(fulfill_phase) across FRs with extracted SubAssertion.fulfill_phase (~100 lines including P3-skipped path that still carries fulfill_phase) — full carry-over obligation preview + proper back-compat (sized to current 1777). 2026-07-26: +104 — Round 15 §3: _obligations_from_preflight gains 8 per-check extractor branches (drift_detection / sab_check / traceability / fr_spec_consistency / artifact_consistency / config_liveness / previous_phase_artifacts / bvs_phase_order), replacing the generic "would block at phase N" fallback with actionable rule_id/file/line detail; preflight_artifact_consistency gains an additive `error_details` return key so its extractor has something to read (sized to current 1881).
    # 2026-07-12: +7 lines — Round 5 建議2站1: _generate_sab_json now resolves
    # scripts/ via the shared harness_scripts_dir() SSOT instead of its own
    # (broken) Path(__file__).parent arithmetic.
    # 2026-07-13: +63 lines — Round 10 站3: cmd_check_artifact_consistency
    # now also runs check_security_design (reads current_phase from
    # state.json); cmd_bug_hunt_targets gains a 6th targeting source
    # (threat_model — SAD.md §6 threats' owner_module resolved to an
    # on-disk path, same candidate expansion preflight_sab_check uses).
    # 2026-07-13: +34 lines — T1-A (8-phase workflow-audit remediation):
    # new cmd_check_manifest_integrity + its subparser registration, a thin
    # CLI wrapper around PhaseHooks.preflight_manifest_integrity() so
    # workflow JS stops reimplementing (and getting wrong) this check inline.
    # 2026-07-16: +15 lines — Round 12 站3c: check-test-mirrors-spec
    # consults values.checker_enforcement for spec_unsatisfiable
    # (default warn; operator-promotable to block after a clean E2E run).
    # 2026-07-17: +6 lines — Round 13 站1: exception-swallow ratchet paydown —
    # 2 previously-unlogged broad excepts now print a [WARN] diagnostic.
    "cli/check_cmds.py": 1537,
    # 2026-07-12: +2 lines — Round 5 exception-swallow ratchet: _manifest_fr_ids
    # / _auto_fr_ids now log the swallowed parse error before returning [].
    # 2026-07-17: +5 lines — the sessions_spawn.log.lock gitignore entry
    # (same rationale as the sessions_spawn.log entry two lines above: the
    # lock-file sibling was missing from the pattern, surfacing as a second
    # permanently-dirty file in run-fr-step's dirty-tree guard).
    # 2026-07-17: +2 lines — Round 13 站1: exception-swallow ratchet paydown —
    # the artifact line-count except now prints its error before falling
    # back to the no-linecount checklist item.
    "harness/git_strategy.py": 1299,
    # 2026-07-13: +28 lines — Round 10 站4: P2 tasks gain a
    # [SEC-WRITE]/[SEC-VALIDATE] step pair next to [SAB-WRITE], mirroring
    # the SAB block's own authoring-guidance shape.
    # 2026-07-14: +3 lines — 2nd-round workflow-JS-audit remediation: P4/P6
    # dims-count text (previously hardcoded "16"/"15" strings that drifted
    # from the yaml-derived enabled-dim count) now reads gate_meta[gate][1]
    # via two local `_g3_dims`/`_g4_dims` variables instead.
    # 2026-07-14: +4 lines — generate_phase8_tasks' P8→P9 block (phase 8 has
    # its own hand-written advance text, not the shared _phase_advance_step)
    # now documents the same Sync-phase post-advance push as blocks.py.
    # 2026-07-28 (Round 22 站1): +9 lines — P4/P5/P7/P8 each emit the
    # project-wide ORCH-POST-ONCE tail after their FR loop (P4's three FR-loop
    # branches share one insertion point at the loop's common exit). P3 is
    # deliberately excluded: its per-FR amend-sab runs BEFORE that FR's own
    # GATE1 so the Architecture Amendment Protocol sees the module the FR just
    # wrote — that one is not a repeat, it is the point.
    "scripts/plangen/phase_tasks.py": 1150,
    "core/quality_gate/mutation_enforcer.py": 967,
    # 2026-07-17: new god file — Round 15 station5: phase_specs.py (formerly
    # 2985 lines, see git history for its full growth log) was split one
    # module per phase (spec_phase1.py .. spec_phase8.py, spec_shared.py for
    # the one cross-phase renderer); phase_specs.py itself is now a ~20-line
    # facade re-exporting generate_phase1..generate_phase8, well under the
    # threshold and removed from this table. Phase 1 (4 serial A/B sub-tasks
    # + Load Legal Artifacts + Forward Ref Check + a dedicated runPeerReview)
    # is the largest single-phase family and the only one landing over the
    # threshold post-split — kept as one file (not split further) to match
    # every other phase's one-phase-one-file shape; see spec_phase1.py's own
    # module docstring.
    "scripts/workflowgen/spec_phase1.py": 917,
    # 2026-07-15: new god file — Round 11 station4: js_blocks.py crossed the
    # threshold for the first time (769→1314) adding the shared A/B-review-
    # machine renderers (safePrevB2/makeDocSummary/scopeRules/buildBPrompt/
    # structuredBReview/persistApproval/loadFileViaPython/genericAbLoop) that
    # unify phase1/phase2/phase6's previously triplicated JSON-parsing +
    # B-review helpers, plus splitting RESOLVE_REPO_BLOCK/BUDGET_GUARD_BLOCK/
    # REPO_LOG_LINE apart (phase1/phase2/phase6 each interleave their own
    # extra consts between REPO resolution and the REPO/PY log line, in three
    # different places — the split lets every phase reuse the same resolver
    # function body without forcing one phase's ordering onto another), plus
    # render_rule_prose() (loads harness/prompts/rules/<id>.md via plangen/
    # blocks.py's own _load_rule SSOT instead of phase1's original JS
    # hand-duplicating that prose — see phase_specs.py's srsAPrompt/
    # srsBChecklist for the 3 call sites this replaced).
    # 2026-07-16: +8 lines — Round 12 站1: render_per_fr_delta gains the
    # pre_loop_state parameter (phase4's p4Mid declarations, dropped by the
    # station-3a migration — sim testbed catch) + docstring documenting it.
    # 2026-07-16: +7 lines — Round 12 站2a: render_per_fr_delta's GATE1
    # verify swaps the inline python one-liner for the verify_gate1_qc.py
    # helper dispatch (v2.13.3 pattern, cef32c4's deferred P4/P5/P7/P8
    # migration) with the verdict derived from echoed canonical stdout
    # only; the delta is the comment documenting the hallucination class.
    # 2026-07-25: +34 lines — render_gate_loop()'s Gate 2/3/4 completion
    # signal was corrected from manifest.gate_results.gate{N}.quality_complete
    # (set from the SSI score alone, before Phase Truth runs, never reverts)
    # to state.json.last_gate >= gate_num (only written by finalize-gate's
    # _update_state_checkpoint AFTER PhaseTruthVerifier passes for an exit
    # gate — the authoritative "truly finalized" signal advance-phase's own
    # precondition check already trusts). Touches both the precheck block
    # and the in-loop verify step, plus rewriting the precheck's shell
    # one-liner as a template literal (matching the existing ctxCheckCmd
    # pattern) instead of the old sextuple-escaped nested-quote string,
    # which a manual decode showed was silently mis-concatenating REPO.
    # 2026-07-26: +44 lines — Round 23: render_env_check rewritten to use the
    # bash-timeout-aware background poll pattern (observed on taskq phase5
    # wf_4fe2125c-48d: the chained run-env-check + finalize-env-check
    # legitimately runs past Claude Code Bash tool's 10-min default timeout;
    # Bash auto-moves to background + returns rc=124, which the sub-agent
    # mis-reports as the env-check exit code, masking that env_check_result.
    # json was actually written). Same GATE1-DELTA background dispatch idiom
    # reused (nohup + kill -0 poll loop + log tail). New ENV_CHECK_SCHEMA
    # (+ready field) added to _SCHEMA_DEFS for the Bug #127 cross-check.
    # Replaces the previous one-line synchronous Bash invocation in 5 phase
    # workflows (phase3/4/5/7/8).
    # 2026-07-28 (Round 22 站1/站2): +6 net — the ORCH-POST once-per-phase
    # block and the render_manifest_integrity_fn rationale docstring cost
    # more generator lines than the two removed call sites saved; the
    # GENERATED workflow JS shrank by ~150 lines across the 6 phase files.
    # 2026-07-28 (Round 22 站4): +6 — the two background-poll prompts spell out
    # a backoff sequence and why the flat first interval was wrong, replacing
    # one-line "sleep 60"/"Poll every 30s" instructions.
    "scripts/workflowgen/js_blocks.py": 1419,
}


def _production_line_counts() -> dict[str, int]:
    counts: dict[str, int] = {}
    for d in _SCAN_DIRS:
        for path in sorted((REPO / d).rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            rel = path.relative_to(REPO).as_posix()
            counts[rel] = len(
                path.read_text(encoding="utf-8", errors="replace").splitlines()
            )
    return counts


def _violations(counts: dict[str, int]) -> list[str]:
    out = []
    for rel, count in sorted(counts.items()):
        ceiling = _LINE_CEILING.get(rel)
        if ceiling is not None:
            if count > ceiling:
                out.append(
                    f"{rel}: {count} lines > ceiling {ceiling} — split the "
                    f"file, or if the growth is deliberate raise the ceiling "
                    f"in THIS commit and justify it in the commit message"
                )
        elif count >= _GOD_FILE_THRESHOLD:
            out.append(
                f"{rel}: {count} lines — new god file (unlisted, threshold "
                f"{_GOD_FILE_THRESHOLD}); split it or add a justified "
                f"ceiling entry"
            )
    return out


def test_production_file_line_ratchet():
    over = _violations(_production_line_counts())
    assert not over, (
        "god-file growth must be a reviewed decision, not a silent drift:\n  "
        + "\n  ".join(over)
    )


def test_comparator_fires_on_listed_growth():
    """Negative: one line over a listed ceiling must trip the ratchet."""
    rel = "cli/gate_cmds.py"
    assert _violations({rel: _LINE_CEILING[rel] + 1})


def test_comparator_fires_on_new_god_file():
    """Negative: an unlisted file at the threshold must trip the ratchet."""
    assert _violations({"cli/newly_huge.py": _GOD_FILE_THRESHOLD})


def test_comparator_quiet_at_or_under_limits():
    rel = "cli/gate_cmds.py"
    assert _violations({
        rel: _LINE_CEILING[rel],
        "cli/small.py": _GOD_FILE_THRESHOLD - 1,
    }) == []
