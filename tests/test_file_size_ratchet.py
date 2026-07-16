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
    "harness/harness_bridge.py": 3042,
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
    "cli/phase_cmds.py": 2680,
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
    "cli/fr_cmds.py": 2414,
    # 2026-07-12: +3 lines — Round 5 建議2站2: same load_harness_script
    # migration for the parse_srs_fr_sections/parse_sad_modules call sites.
    # 2026-07-13: +7 lines — audit-phase subparser gained a `description=`
    # clarifying it must run BEFORE advance-phase for a phase-scoped C10
    # result (no workflow JS ever calls it automatically).
    # 2026-07-16: Round 12 站3a — red_assertion_check crossed the
    # unlisted threshold (900) adding _unsatisfiable_spec_rule_ids: the
    # satisfiability probe that downgrades provably-impossible spec
    # constraints to spec_unsatisfiable warnings (R5 incident mechanized).
    "core/quality_gate/red_assertion_check.py": 1000,
    "cli/project_cmds.py": 1920,  # 2026-07-15 R3: cmd_amend_sab gained PHANTOM block + --strict (~50 lines)
    # 2026-07-16: Round 12 站0 — agent_spawner crossed the unlisted-file
    # threshold (900) with three additions: _extract_dispatch_error /
    # _denoise_cli_stderr (站0c error-capture denoise — closes the 76×
    # banner-only observability black hole), _UNATTENDED_PREAMBLE +
    # --append-system-prompt (站0d isolation against the measured
    # setting-sources user-CLAUDE.md leak), and preflight_substrate()
    # (站0b probe). All three are dispatch-substrate concerns that belong
    # with spawn(); splitting them out would separate the probe from the
    # substrate it measures.
    "core/agent_spawner.py": 940,
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
    "scripts/plangen/blocks.py": 1682,
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
    "core/phase_hooks.py": 1597,
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
    "cli/check_cmds.py": 1531,
    # 2026-07-12: +2 lines — Round 5 exception-swallow ratchet: _manifest_fr_ids
    # / _auto_fr_ids now log the swallowed parse error before returning [].
    "harness/git_strategy.py": 1292,
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
    "scripts/plangen/phase_tasks.py": 1141,
    "core/quality_gate/mutation_enforcer.py": 967,
    # 2026-07-14: new god file — Round 11 station3: workflowgen's
    # phase_specs.py mirrors plangen/phase_tasks.py's shape (one
    # generate_phaseN() + its phase-specific custom renderers per migrated
    # phase). Station1/2 (phase5/7/8) stayed under the threshold; station3
    # adds phase3 (full TDD chain, own Load FRs/Milestones/Sync — no shared
    # counterpart) and phase4 (Test Plan/Coverage/Bug Hunt), pushing it over.
    # 2026-07-15: +1763 lines — Round 11 station4 adds phase6 (Gate 4/Release
    # Docs/Peer Review/Tag & Advance) and the largest remaining files, phase2
    # (3 serial A/B sub-tasks + SAB Generation + holistic Peer Review) and
    # phase1 (4 serial A/B sub-tasks + Load Legal Artifacts + Forward Ref
    # Check + a dedicated runPeerReview) — all now workflowgen-generated, so
    # all 8 phases' declarative specs live here. Per-phase custom renderers
    # (buildAPrompt/buildBDocs/checklist text) are genuinely one-off business
    # prose with no cross-phase counterpart; kept verbatim rather than forced
    # into a false shared abstraction (see js_blocks.py's own note below on
    # phase1's runSubTask vs phase2's abLoop for the same judgment call).
    # 2026-07-15: +1 line — Fix 6/7/8 (agent-format-drift bug class): SEC
    # checklist gains a verified_by single-name reminder, the SRS prompt
    # gains a canonical `### FR-XX:` heading example, and the Phase 3 FR-dev
    # prompt gains an explicit "don't share files across FRs" contrast next
    # to the existing single-test-file rule — three prompt/template gaps
    # found by re-verifying the prior session's inference-only "5-layer"
    # analysis against the actual code.
    # 2026-07-16: +2 lines — Bug A (v2.13.3 follow-up to v2.13.2 spawnSync):
    # the v2.13.2 GATE1-verify block (lines 548-572) is replaced by an
    # `await agent()` Bash dispatch into `harness/scripts/verify_gate1_qc.py`.
    # +2 comes from two added explanatory comment lines ("//" + "//\n") in
    # the JS literal explaining why spawnSync doesn't work in the workflow
    # runtime sandbox and how the LLM-as-string-carrier substitute preserves
    # the AUTHORITATIVE manifest read. Net JS shape is +2 lines in
    # phase3-implementation.js too. Verified: regenerated
    # tests/golden/workflowgen/phase3.js == hand-edited phase3-implementation.js
    # (diff = 0 lines).
    # 2026-07-16: +16 lines — Round 12 站1: three sim-testbed-caught fixes
    # at the generator source: phase4 pre_loop_state (p4MidPushed/
    # p4MidThreshold declarations dropped by the station-3a migration),
    # phase6 MAX_OUTER_ATTEMPTS declaration (dropped by station-4's A/B
    # unification), and String(x ?? '') null guards on phase1's three
    # agent-return .slice(-800) error paths (session-limit null crashed
    # instead of returning {error}). Comments explaining each restoration
    # account for most of the delta.
    "scripts/workflowgen/phase_specs.py": 2973,
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
    "scripts/workflowgen/js_blocks.py": 1329,
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
