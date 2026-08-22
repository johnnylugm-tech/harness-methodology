"""Phase 4 (Testing) workflow assembly — Round 15 station3 extraction from
the former monolithic phase_specs.py. See scripts/workflowgen/spec_shared.py
for the cross-phase _render_meta.
"""
from __future__ import annotations

from . import js_blocks as B
from . import spec_shared as S
from .spec_shared import _render_meta

_HEADER_4 = f"""\
// Phase 4 — Testing (faithful to .methodology/phase4_plan.md v2.12.0)
//
// GENERATED FILE — do not hand-edit. Source of truth:
// scripts/workflowgen/phase_specs.py::generate_phase4() (+ js_blocks.py for
// the blocks shared across phase workflow files). Regenerate with:
//   python3 scripts/workflowgen/generate_workflows.py --write --phase 4
//
// Structure: FR-loop型 + adversarial bug hunt + Gate 3 ({S.gate_dim_count(3)} dims) exit.
// CHECKPOINT-0 TEST_PLAN → per-FR GATE1-DELTA → TEST_RESULTS/COVERAGE →
// Step 4b bug hunt (adversarial_review is a Gate 3 dim, needs bug_hunt_report.json)
// → Gate 3 → p4-pre-gate3 milestone + advance.
//
// Playbook lessons: NO import/fs/process, Bash CLI, SCOPE RULES,
// PY = .venv/bin/python, scriptPath launch.
// v4 (2026-07-02): gate verdicts use FLAT schema: (playbook §5.2 rev) — regex
// over LLM prose was the root cause of the #126/#134/#135/#136/ENV_CHECK_RC
// bug class. Heavy orchestrators keep prose narrative; verdicts come from
// schema proxy agents reading harness artifacts (manifest qc, state.json, rc).
"""

_META_PHASES_4 = [
    "Entry & Preflight", "Test Plan", "Env Check",
    "Load FRs", "Per-FR Delta", "Coverage", "Bug Hunt", "Artifacts Commit",
    "Gate 3", "Preview Next-Phase", "Advance", "Sync",
]


def _render_test_plan() -> str:
    return (
        B.render_phase_header("Test Plan")
        + "log('Generate 04-testing/TEST_PLAN.md from SRS FR acceptance criteria')\n"
        + "const testPlanReport = await agent(\n"
        + "  'YOU ARE THE P4 TEST PLAN AUTHOR. Generate TEST_PLAN.md (runs once before per-FR testing).\\n'\n"
        + "  + 'REPO: ' + REPO + '\\nPYTHON: ' + PY + '\\n\\n'\n"
        + "  + 'Steps (create 04-testing/ if missing):\\n'\n"
        + "  + '1. Read 01-requirements/SRS.md FR acceptance criteria + .methodology/quality_manifest.json FR list.\\n'\n"
        + "  + '2. Write ' + REPO + '/04-testing/TEST_PLAN.md. For each FR: test case ID, description, input, expected output, priority. Include positive, negative, boundary, and edge-case categories. Cover ALL FRs + NFRs.\\n'\n"
        + "  + '3. Verify TEST_PLAN.md covers every FR from the manifest.\\n\\n'\n"
        + "  + 'Verdict: report via the StructuredOutput tool — pass=true ONLY if TEST_PLAN.md was written and covers every FR; reason = one-line summary.\\n\\n'\n"
        + "  + 'SCOPE RULES:\\n- DO NOT run TDD/run-gate/bug-hunt/advance.\\n- DO NOT modify harness/.\\n- ONLY author TEST_PLAN.md.',\n"
        + "  { label: 'test-plan', phase: 'Test Plan', agentType: 'general-purpose', schema: VERDICT_SCHEMA },\n"
        + ")\n"
        + "if (!(testPlanReport && testPlanReport.pass === true)) {\n"
        + "  return halt('test-plan', { error: 'Phase 4 TEST_PLAN did not PASS', reason: testPlanReport ? String(testPlanReport.reason ?? '').slice(-500) : 'agent returned null' })\n"
        + "}\n"
    )


def _render_coverage() -> str:
    return (
        B.render_phase_header("Coverage")
        + "log('Generate TEST_RESULTS.md + COVERAGE_REPORT.md (cross-artifact validated at Gate 3)')\n"
        + "const coverageReport = await agent(\n"
        + "  'YOU ARE THE P4 COVERAGE AUTHOR. Generate the test-results + coverage deliverables.\\n'\n"
        + "  + 'REPO: ' + REPO + '\\nPYTHON: ' + PY + '\\n\\n'\n"
        + "  + 'Steps:\\n'\n"
        + "  + '1. TEST_RESULTS: write ' + REPO + '/04-testing/TEST_RESULTS.md — summarise test execution: cases run, pass/fail, deferred issues. Include the VERBATIM pytest summary line of the run you are describing (the `N passed, M skipped … in T s` line pytest prints); `cross_artifact.check_test_count_reconciliation` compares its counts against the framework own run_suite measurement and reports a mismatch as CRITICAL, so this document cannot record a run over a tree the project does not deliver. Scope the run to the `test_target` step 2 reads, NOT to the repository root — the root also holds the vendored harness copy, and a run from there collects thousands of the framework own tests. Measured: one project recorded `4 failed, 7563 passed, 3 skipped` for a 349-test tree, and that number then travelled into BASELINE.md and VERIFICATION_REPORT.md unchallenged.\\n'\n"
        + "  + '2. COVERAGE: read TESTS=`test_target` and SRC=`cov_target` (project-relative) from ' + REPO + '/.sessi-work/phase4_ctx.json — load-context writes them from the resolver Gate 3 re-measures with. Do NOT substitute your own: the layout differs between projects, and .coveragerc may scope SRC. Run `' + PY + ' -m pytest ' + REPO + '/<TESTS> --cov=<SRC> --cov-report=term-missing -q | tee ' + REPO + '/04-testing/coverage_raw.txt` then `' + PY + ' -m coverage report --format=total`. Write ' + REPO + '/04-testing/COVERAGE_REPORT.md with overall coverage % (≥80% for Gate 3), per-module breakdown, uncovered lines.\\n'\n"
        + "  + '   WARNING: cross_artifact.py validates these numbers against live pytest --cov at Gate 3 — fabricated numbers are caught. Use REAL numbers.\\n\\n'\n"
        + "  + 'Verdict: report via the StructuredOutput tool — pass=true ONLY if both docs were written from real pytest output; reason = one-line summary.\\n\\n'\n"
        + "  + 'SCOPE RULES:\\n- DO NOT run run-gate / bug-hunt / advance.\\n- DO NOT modify harness/.\\n- DO NOT fabricate coverage numbers.\\n- ONLY generate the 2 docs from real pytest output.',\n"
        + "  { label: 'coverage', phase: 'Coverage', agentType: 'general-purpose', schema: VERDICT_SCHEMA },\n"
        + ")\n"
        + "if (!(coverageReport && coverageReport.pass === true)) {\n"
        + "  return halt('coverage-docs', { error: 'Phase 4 coverage docs did not PASS', reason: coverageReport ? String(coverageReport.reason ?? '').slice(-500) : 'agent returned null' })\n"
        + "}\n"
    )


def _render_bug_hunt() -> str:
    return (
        B.render_phase_header("Bug Hunt")
        + "log('Adversarial bug hunt (targets → scout → hunters → verify → synthesize → resolve)')\n"
        + "const huntReport = await agent(\n"
        + "  'YOU ARE THE ADVERSARIAL BUG HUNT ORCHESTRATOR (Step 4b, before Gate 3).\\n'\n"
        + "  + 'REPO: ' + REPO + '\\nPYTHON: ' + PY + '\\n\\n'\n"
        + "  + 'The Gate 3 dimension adversarial_review (threshold 100) BLOCKS the gate if .methodology/bug_hunt_report.json is absent or any confirmed critical/high finding is still \"open\". Run the hunt NOW.\\n\\n'\n"
        + "  + 'Steps:\\n'\n"
        + "  + '1. HUNT-TARGETS: `' + PY + ' ' + REPO + '/harness_cli.py bug-hunt-targets --project ' + REPO + '` → .methodology/bug_hunt_targets.json (CRG hubs + mutation survivors + integration gaps). **IMPORTANT**: the file\\'s `threat_model` entries (SAD.md §6 declared threats via `core.quality_gate.security_design:extract_security_block()`) are forced high-risk attack-vector seeds — INDEPENDENT of CRG/mutation signals — and MUST be present in the target list.\\n'\n"
        + "  + '2. HUNT-RUN: execute the 4-phase protocol in ' + REPO + '/harness/harness/ssi/prompts/hunt_bugs.md (scout → lens hunters → adversarial verify → synthesize). Reference workflow: ' + REPO + '/harness/templates/workflows/hunt-bugs.js. Spawn hunters/verifiers as sub-agents (you have the Agent tool); use model ' + HUNT_MODEL + ' (DIFFERENT from the developer model to minimise same-source bias). Build the CRG graph first if needed. **`threat_model` targets**: verify the declared `mitigation` actually blocks the attack (not just that defensive-looking code exists). For each `threat_model` entry, produce a finding row in `.methodology/bug_hunt_report.json` with `attack_vector`, `attempted_exploit`, and `mitigation_effective: true|false`.\\n'\n"
        + "  + '   Output: .methodology/bug_hunt_report.json (schema: harness/schemas/bug_hunt_report.schema.json) + human markdown under 03-development/.audit/.\\n'\n"
        + "  + '3. HUNT-RESOLVE: for EACH confirmed critical/high finding set resolution.status:\\n'\n"
        + "  + '   - resolved: include fix_commit (SHA) or repro_test (path in tests/).\\n'\n"
        + "  + '   - refuted: include refute_evidence (explanation + line citation).\\n'\n"
        + "  + '   Medium/low: record only (not required to resolve before Gate 3).\\n\\n'\n"
        + "  + 'PERMITTED actions to populate `resolved` (the `DO NOT modify harness/` scope rule covers ONLY the `harness/` submodule — project source IS in scope for HUNT-RESOLVE):\\n'\n"
        + "  + '- Write a repro test under 03-development/tests/ that RED-fails on the bug. Apply the minimal source fix in 03-development/src/<module>.py. Confirm the repro test now PASSES (RED→GREEN anti-fabrication gate per hunt_bugs.md).\\n'\n"
        + "  + '- Commit the repro test + source fix with `fix(<module>): <title>` prefix.\\n'\n"
        + "  + '- Update .methodology/bug_hunt_report.json resolution.status to `resolved` with the fix_commit SHA + repro_test path.\\n'\n"
        + "  + 'Refuted is ALSO permitted: read the offending code, find a guard/fallback the finding missed, cite the exact line numbers as refute_evidence.\\n\\n'\n"
        + "  + 'Verdict: report via the StructuredOutput tool — pass=true ONLY if bug_hunt_report.json was written AND all confirmed critical/high findings are resolved-or-refuted; reason = one-line summary. (Truth is enforced downstream: Gate 3\\'s framework-owned adversarial_review dim re-reads the report itself.)\\n\\n'\n"
        + "  + 'SCOPE RULES:\\n- DO NOT run run-gate (Gate 3) / advance-phase / push-milestone.\\n- DO NOT modify harness/ (running its scripts/prompts is fine; editing is NOT — HR-17).\\n- ONLY targets + hunt + resolve + write bug_hunt_report.json.',\n"
        + "  { label: 'bug-hunt', phase: 'Bug Hunt', agentType: 'general-purpose', model: HUNT_MODEL, schema: VERDICT_SCHEMA },\n"
        + ")\n"
        + "if (!(huntReport && huntReport.pass === true)) {\n"
        + "  return halt('bug-hunt', { error: 'Phase 4 bug hunt did not PASS (Gate 3 adversarial_review will block)', reason: huntReport ? String(huntReport.reason ?? '').slice(-600) : 'agent returned null' })\n"
        + "}\n"
    )


# The D4 spec-coverage floor for this phase, stated once. It is NOT a gate
# dimension — spec-coverage-check owns it — so it has no entry in the gate
# config the rest of this block reads from; naming it here at least keeps the
# prose, the `--threshold` argument and the pass line from drifting apart.
_D4_THRESHOLD_P4 = 80.0

_GATE3_STEPS = [
    "1. G3a: `' + PY + ' ' + REPO + '/harness_cli.py run-gate --gate 3 --phase 4 --project ' + REPO + '` (CRG recon runs inside automatically). Read the printed evaluation prompt.",
    (
        "2. G3b: Evaluate ALL Gate 3 dimensions inline per ' + REPO + '/harness/harness/ssi/prompts/evaluate_dimension.md. Write ' + REPO + '/.sessi-work/gate3_result.json.\\n"
        f"{S.render_dimension_table(3)}"
        "   For any failing dim: fix ROOT CAUSE in code (ruff/pyright/tests/bandit/readability_v2/ast-error-handling/pytest-benchmark), re-run the tool, update score. (readability tool is `python3 -m harness.toolchains.readability_v2` — NOT `radon mi` — per phase3/4/6_plan.md v2.12.0.) A low architecture score has no waiver route (Round 38): fix the structure, or — only for a genuine CRG false positive — calibrate `crg_excludes` / `crg_cohesion_healthy` in .methodology/harness_config.json, which is committed and therefore applies to CI too."
    ),
    (
        "3. G3c: `' + PY + ' ' + REPO + '/harness_cli.py finalize-gate --gate 3 --phase 4 --project ' + REPO + '`.\\n"
    ),
    f"4. D4: `' + PY + ' ' + REPO + '/harness_cli.py spec-coverage-check --project ' + REPO + ' --threshold {_D4_THRESHOLD_P4}`. FAIL → add missing tests, re-run.",
    "5. CRG-ARCH: `BASELINE=\"\"; [ -f ' + REPO + '/.methodology/crg_baseline_p4.json ] && BASELINE=\"--baseline ' + REPO + '/.methodology/crg_baseline_p4.json\"; ' + PY + ' ' + REPO + '/harness_cli.py crg-arch-check --project ' + REPO + ' $BASELINE`. CI enforces this as an absolute floor on every push, independent of the Gate 3 composite score. FAIL → the crg-arch-check output lists the low-cohesion communities / oversized functions; fix the underlying architecture issue, re-run.",
]

_GATE3_SCOPE_RULES = (
    "- DO NOT run advance-phase.\\n"
    "- DO NOT edit gate3_result.json to fake scores — fix the code.\\n"
    "- DO NOT modify harness/ (HR-17).\\n"
    "- ONLY run-gate/eval/finalize/spec-coverage/crg-arch-check + code fixes."
)

_GATE3_DEFERRED_FIXES_STEP = (
    "  log('  Gate 3 exhausted 3 rounds — generating deferred_fixes.md')\n"
    "  const gate3StateCmd = PY + ' -c \"import json; g=(json.load(open(\\'' + REPO + '/.methodology/quality_manifest.json\\')).get(\\'gate_results\\',{}) or {}).get(\\'gate3\\') or {}; print(json.dumps({\\'score\\': g.get(\\'score\\'), \\'qc\\': g.get(\\'quality_complete\\'), \\'dims\\': g.get(\\'dimensions\\',{})}))\"'\n"
    "  await agent(\n"
    "    'YOU ARE THE DEFERRED-FIX RECORDER. Gate 3 failed to reach PASS in 3 rounds.\\n'\n"
    "    + 'REPO: ' + REPO + '\\nPYTHON: ' + PY + '\\n\\n'\n"
    "    + '1. Get the last-known Gate 3 state:\\n`' + gate3StateCmd + '`\\n'\n"
    f"    + '2. Run `' + PY + ' ' + REPO + '/harness_cli.py spec-coverage-check --project ' + REPO + ' --threshold {_D4_THRESHOLD_P4}; echo \"RC=$?\"` for the D4 status.\\n'\n"
    "    + '3. Run `' + PY + ' ' + REPO + '/harness_cli.py crg-arch-check --project ' + REPO + '; echo \"RC=$?\"` for the CRG architecture status.\\n'\n"
    "    + '4. Write `' + REPO + '/.methodology/deferred_fixes.md` with:\\n'\n"
    "    + '   - A brief header: \"Gate 3 — deferred fixes\" + date + last-known composite score\\n'\n"
    "    + '   - Each failing dimension (score below its threshold) as a `- [ ]` checkbox item\\n'\n"
    "    + '   - D4 as a `- [ ]` checkbox item (spec-coverage < 80%)\\n'\n"
    "    + '   - CRG architecture as a `- [ ]` checkbox item if RC != 0 (architecture score < 80%)\\n'\n"
    "    + '   - Each item MUST cite the current score AND the required threshold\\n'\n"
    "    + '   - A final \"Next step:\" line: \"Resolve every item → re-run Phase 4 Gate 3 → advance-phase\"',\n"
    "    { label: 'deferred-fixes', phase: 'Gate 3', agentType: 'general-purpose' },\n"
    "  )\n"
)


def generate_phase4() -> str:
    parts = [
        _HEADER_4,
        "",
        _render_meta(
            name="phase4-testing",
            description=(
                "Phase 4 Testing — TEST_PLAN + per-FR GATE1-DELTA + adversarial "
                f"bug hunt + Gate 3 ({S.gate_dim_count(3)} dims) exit (phase4_plan.md v2.12.0)"
            ),
            phases=_META_PHASES_4,
        ),
        "",
        B.RESOLVE_REPO_BLOCK + B.REPO_LOG_LINE + B.BUDGET_GUARD_BLOCK,
        B.HUNT_MODEL_BLOCK,
        "",
        B.WRITE_SCOPE_BLOCK,
        "",
        B.render_schemas(["VERDICT_SCHEMA", "RC_SCHEMA", "ENV_CHECK_SCHEMA", "CTX_SCHEMA", "DELTA_FAST_SCHEMA", "GATE_VERIFY_SCHEMA", "PHASE_SCHEMA"]),
        B.render_entry_preflight(
            phase=4, gate_num=2, gate_owner_phase=3, prev_phase=3,
            extra_note=(
                "- DO NOT generate TEST_PLAN / run TDD / run-gate / bug hunt.\\n"
                "- DO NOT run advance-phase/push-milestone.\\n"
            ),
        ),
        _render_test_plan(),
        B.render_env_check(phase=4),
        B.render_load_frs(phase=4, include_fr_titles=True),
        B.render_per_fr_delta(
            phase=4,
            forbidden_note="- DO NOT run run-gate / bug-hunt / advance-phase / push-milestone.\\n",
            verifier_role="TEST VERIFIER",
            use_fr_titles=True,
            # Round 12 站1: restored verbatim from the pre-migration file
            # (840d637^ lines 303-304) — the station-3a migration dropped
            # these declarations while keeping the mid_milestone_step that
            # reads them; first sim-testbed run caught the ReferenceError.
            pre_loop_state=(
                "let p4MidPushed = false\n"
                "const p4MidThreshold = Math.ceil(frIds.length / 2)  // PUSH ⑤ trigger: ≥50% FRs Gate 1 PASS\n"
            ),
            mid_milestone_step=(
                "\n"
                "  // PUSH ⑤ p4-mid — fire once when ≥50% FRs have Gate 1 PASS (but not yet all done).\n"
                "  if (!p4MidPushed && gate1Pass.length >= p4MidThreshold && gate1Pass.length < frIds.length) {\n"
                "    p4MidPushed = true\n"
                "    log('  ≥50% FRs Gate 1 PASS (' + gate1Pass.length + '/' + frIds.length + ') — pushing p4-mid milestone')\n"
                "    await agent(\n"
                "      'YOU ARE THE P4 MID-MILESTONE PUSHER (≥50% FRs Gate 1 PASS).\\n'\n"
                "      + 'REPO: ' + REPO + '\\nPYTHON: ' + PY + '\\n\\n'\n"
                "      + '0. GUARD: `git -C ' + REPO + ' log --oneline --grep=\"P4-mid)\" -1`. If exists, report \"MILESTONE: PASS (already pushed)\" and stop.\\n'\n"
                "      + '1. Command: `' + PY + ' ' + REPO + '/harness_cli.py push-milestone --type p4-mid --project ' + REPO\n"
                "      + ' --fr-done ' + gate1Pass.length + ' --fr-total ' + frIds.length + ' --fr-ids ' + gate1Pass.join(',') + '`\\n'\n"
                "      + 'Writes HANDOVER.md + commits + pushes. If a hook blocks, reword commit to start with `chore(harness):` (NOT --no-verify), retry.\\n\\n'\n"
                "      + 'Report: \"MILESTONE: PASS|FAIL — <details>\".\\n\\n'\n"
                "      + 'SCOPE RULES:\\n- DO NOT run run-gate / bug-hunt / advance-phase.\\n- ONLY push-milestone p4-mid.',\n"
                "      { label: 'milestone-p4-mid', phase: 'Per-FR Delta', agentType: 'general-purpose' },\n"
                "    )\n"
                "  }\n"
            ),
        ),
        _render_coverage(),
        _render_bug_hunt(),
        B.render_artifacts_commit(
            paths=["04-testing", ".methodology/bug_hunt_report.json", ".methodology/bug_hunt_targets.json", ".methodology/decision_logs"],
            commit_msg="chore(p4): test-plan + coverage + bug-hunt artifacts",
            phase=4,
        ),
        B.render_gate_loop(
            gate_num=3, phase=4,
            log_msg=f"Gate 3 exit ({S.render_gate_dims_summary(3)})",
            prompt_steps=_GATE3_STEPS,
            pass_line_desc=S.render_gate_pass_line(3, d4_threshold=_D4_THRESHOLD_P4),
            scope_rules=_GATE3_SCOPE_RULES,
            d4_threshold=_D4_THRESHOLD_P4,
            on_fail_error_msg="Gate 3 did not PASS in 3 rounds (HR-08); deferred_fixes.md written to .methodology/ (advance-phase exit 17 until resolved)",
            include_manifest_integrity=False,
            deferred_fixes_step=_GATE3_DEFERRED_FIXES_STEP,
        ),
        B.render_preview_next_phase(4),
        B.render_advance_loop(
            phase=4, next_phase=5,
            precheck_steps=[
                "PUSH ⑥ p4-pre-gate3 (if not already pushed): `' + PY + ' ' + REPO + '/harness_cli.py push-milestone --type p4-pre-gate3 --project ' + REPO + ' --fr-ids ' + gate1Pass.join(',') + '`. (Idempotent; skip if already snapshotted.)",
            ],
            scope_extra="- DO NOT re-do P4 testing.\\n",
            only_extra="push-milestone p4-pre-gate3 + ",
            log_msg="p4-pre-gate3 milestone + advance-phase --completed 4 (TDD-PRECHECK enforced)",
            on_pass_extra=(
                "    // [Phase close cleanup] advance-phase only commits its own target paths\n"
                "    // (state.json, HANDOVER.md, CLAUDE.md, phase plan). Post-advance edits\n"
                "    // (pragma annotations, style fixes, test additions, deleted scaffolding)\n"
                "    // remain uncommitted, leaving a dirty tree for the next phase. Commit\n"
                "    // everything advance-phase didn't include. This agent is SCOPED to git\n"
                "    // housekeeping only — no code, no phase transitions.\n"
                "    await agent(\n"
                "      'Run ONE bash command and report its stdout/stderr:\\n'\n"
                "      + '`git -C ' + REPO + ' add -A && git -C ' + REPO + ' commit -m \"chore: phase 4 clean-up\" || true`\\n\\n'\n"
                "      + 'Report: the verbatim stdout/stderr of that command.\\n\\n'\n"
                "      + 'SCOPE RULES:\\n- DO NOT run any code, tests, or phase transitions.\\n- ONLY the git commit above.',\n"
                "      { label: 'cleanup-r' + round, phase: 'Advance', agentType: 'general-purpose' },\n"
                "    )\n"
            ),
        ),
        B.render_sync_verified(),
        (
            "\nlog('Phase 4 workflow complete. Open .methodology/phase5_plan.md to continue.')\n"
            "return {\n"
            + S.render_phase_complete_marker()
            +
            "  phase: 4,\n"
            "  fr_count: frIds.length,\n"
            "  gate1_pass: gate1Pass,\n"
            "  gate3_status: gate3Pass ? 'PASS' : 'unknown',\n"
            "  advance_status: 'PASS',\n"
            "  artifacts: ['04-testing/TEST_PLAN.md', '04-testing/TEST_RESULTS.md', '04-testing/COVERAGE_REPORT.md', '.methodology/bug_hunt_report.json', '.methodology/gate3_result.json', 'HANDOVER.md'],\n"
            "  notes: 'Phase 4 complete per phase4_plan.md v2.12.0. All FRs Gate 1 PASS + bug hunt done + Gate 3 PASS. Phase 5 (Verification) ready.',\n"
            "}\n"
        ),
    ]
    return "\n".join(p for p in parts if p is not None)
