"""Per-phase workflow assembly — the declarative spec layer.

Mirrors `scripts/plangen/phase_tasks.py`'s one-function-per-phase shape:
each `generate_phaseN()` assembles shared `js_blocks` renderers plus the
phase's own unique content (verbatim business logic that genuinely differs
per phase — Config Docs / Archive / Final Push have no shared counterpart
because no other phase does what they do) into the final JS source text.

Phases 5, 7, 8 are implemented (Round 11 stations 1-2). Station 3-4 add
generate_phase3/4/6/1/2 following the same shape.
"""
from __future__ import annotations

from . import js_blocks as B

_HEADER_8 = """\
// Phase 8 — Configuration Management (faithful to .methodology/phase8_plan.md v2.12.0)
//
// GENERATED FILE — do not hand-edit. Source of truth:
// scripts/workflowgen/phase_specs.py::generate_phase8() (+ js_blocks.py for
// the blocks shared across phase workflow files). Regenerate with:
//   python3 scripts/workflowgen/generate_workflows.py --write --phase 8
//
// Structure: FR-loop型 → push-milestone p8 → advance-phase --completed 8 → Sync → Phase 9 (Maintenance).
// Per-FR GATE1-DELTA re-eval, then REVIEW+APPEND the config baseline (the
// framework deterministically generated CONFIG_RECORDS.md + RELEASE_CHECKLIST.md
// via `scripts/phase8_doc_gen.py` during P7→P8 advance-phase (cli/phase_cmds.py
// advance-phase hook, harness commits 4738542 + 3f1fd73), create .methodology-archive/
// (cp -r .methodology/ — NOT .sessi-work/, per harness commit 3f1fd73), verify
// no Phase 9 refs, p8 push.
//
// Playbook lessons: NO import/fs/process, Bash CLI, SCOPE RULES,
// PY = .venv/bin/python, scriptPath launch.
// v4 (2026-07-02): gate verdicts use FLAT schema (playbook §5.2 rev) — regex
// over LLM prose was the root cause of the #126/#134/#135/#136/ENV_CHECK_RC
// bug class. Heavy orchestrators keep prose narrative; verdicts come from
// schema proxy agents reading harness artifacts (manifest qc, git log, rc).
"""

_HEADER_5 = """\
// Phase 5 — Verification & Delivery (faithful to .methodology/phase5_plan.md v2.12.0)
//
// GENERATED FILE — do not hand-edit. Source of truth:
// scripts/workflowgen/phase_specs.py::generate_phase5() (+ js_blocks.py for
// the blocks shared across phase workflow files). Regenerate with:
//   python3 scripts/workflowgen/generate_workflows.py --write --phase 5
//
// Structure: FR-loop型, NO harness run-gate (P5 cleared by Gate 3 at P4 exit).
// Per-FR GATE1-DELTA re-eval (auto-triggers full TDD on code change), then
// generate BASELINE.md + VERIFICATION_REPORT.md (BASELINE.md is a blocking
// audit-phase C1 deliverable with a 7-H2-section depth check; the earlier
// "merged into VERIFICATION_REPORT" note came from a generate_full_plan.py
// copy-paste error, fixed harness-side), p5-baseline milestone push, advance
// (advance-phase still enforces TDD-PRECHECK + D4 ≥80%, and Gate 4 next phase
// needs ≥90% so we warn-check here).
//
// Playbook lessons: NO import/fs/process, Bash CLI, SCOPE RULES,
// PY = .venv/bin/python, scriptPath launch.
// v4 (2026-07-02): gate verdicts use FLAT schema: (playbook §5.2 rev) — regex
// over LLM prose was the root cause of the #126/#134/#135/#136/ENV_CHECK_RC
// bug class. Heavy orchestrators keep prose narrative; verdicts come from
// schema proxy agents reading harness artifacts (manifest qc, state.json, rc).
"""

_HEADER_7 = """\
// Phase 7 — Risk Management (faithful to .methodology/phase7_plan.md v2.12.0)
//
// GENERATED FILE — do not hand-edit. Source of truth:
// scripts/workflowgen/phase_specs.py::generate_phase7() (+ js_blocks.py for
// the blocks shared across phase workflow files). Regenerate with:
//   python3 scripts/workflowgen/generate_workflows.py --write --phase 7
//
// Structure: FR-loop型, NO harness run-gate (P7 cleared by Gate 4). Per-FR
// GATE1-DELTA re-eval, then generate the 3 risk deliverables, p7 milestone
// push, advance (TDD-PRECHECK + D4 ≥90% enforced by advance-phase).
//
// Playbook lessons: NO import/fs/process, Bash CLI, SCOPE RULES,
// PY = .venv/bin/python, scriptPath launch.
// v4 (2026-07-02): gate verdicts use FLAT schema: (playbook §5.2 rev) — regex
// over LLM prose was the root cause of the #126/#134/#135/#136/ENV_CHECK_RC
// bug class. Heavy orchestrators keep prose narrative; verdicts come from
// schema proxy agents reading harness artifacts (manifest qc, state.json, rc).
"""

_META_PHASES_8 = [
    "Entry & Preflight", "Env Check", "Manifest Integrity", "Load FRs",
    "Per-FR Delta", "Config Docs", "Artifacts Commit", "Archive",
    "Final Push", "Sync",
]

_META_PHASES_5 = [
    "Entry & Preflight", "Env Check", "Manifest Integrity", "Load FRs",
    "Per-FR Delta", "Verification Docs", "Artifacts Commit", "Milestone",
    "Advance", "Sync",
]

_META_PHASES_7 = [
    "Entry & Preflight", "Env Check", "Manifest Integrity", "Load FRs",
    "Per-FR Delta", "Risk Docs", "Artifacts Commit", "Milestone",
    "Advance", "Sync",
]


def _render_meta(*, name: str, description: str, phases: list[str]) -> str:
    lines = ["export const meta = {", f"  name: '{name}',"]
    lines.append(f"  description: '{description}',")
    lines.append("  phases: [")
    lines.extend(f"    {{ title: '{t}' }}," for t in phases)
    lines.append("  ],")
    lines.append("}")
    return "\n".join(lines) + "\n"


def _render_config_docs() -> str:
    return (
        B.render_phase_header("Config Docs")
        + "// Per phase8_plan.md + harness commit 4738542: CONFIG_RECORDS.md and\n"
        + "// RELEASE_CHECKLIST.md are DETERMINISTICALLY generated by scripts/phase8_doc_gen.py\n"
        + "// during P7→P8 advance-phase (cli/phase_cmds.py:554, P7→P8 advance-phase hook).\n"
        + "// P8 work is therefore REVIEW + APPEND human-only context, NOT regenerate from\n"
        + "// scratch. Do NOT overwrite the deterministic baseline (it breaks byte-equality\n"
        + "// for downstream consumers); use Edit/append to add the human-only sections.\n"
        + "log('Review deterministic baseline (phase8_doc_gen.py output) + append human-only context')\n"
        + "const docsReport = await agent(\n"
        + "  'YOU ARE THE P8 CONFIG REVIEWER. The framework has ALREADY deterministically generated\\n'\n"
        + "  + 'the config baseline during P7→P8 advance-phase. Your job: REVIEW + APPEND.\\n'\n"
        + "  + 'REPO: ' + REPO + '\\nPYTHON: ' + PY + '\\n\\n'\n"
        + "  + 'Steps (Bash for read-only checks; Edit for human-only append):\\n'\n"
        + "  + '0. VERIFY BASELINE EXISTS: `test -f ' + REPO + '/08-config/CONFIG_RECORDS.md && test -f ' + REPO + '/08-config/RELEASE_CHECKLIST.md && echo BASELINE_OK || echo BASELINE_MISSING`. If MISSING, regenerate via `' + PY + ' ' + REPO + '/harness/scripts/phase8_doc_gen.py --project ' + REPO + '` (fallback per harness advance-phase behavior; should not normally fire).\\n'\n"
        + "  + '1. CONFIG_RECORDS APPEND: Edit ' + REPO + '/08-config/CONFIG_RECORDS.md and APPEND a `## Human Context (P8 append)` section with: ownership per config item, secret rotation cadence, access audit log reference. KEEP all existing framework-generated sections (env var inventory, source-of-truth module refs, feature flags) intact. Do NOT overwrite the framework version.\\n'\n"
        + "  + '2. RELEASE_CHECKLIST APPEND: Edit ' + REPO + '/08-config/RELEASE_CHECKLIST.md and APPEND a `## Human Context (P8 append)` section with: deployment runbook URL, rollback owner + on-call, post-release monitoring dashboard, customer comms template. KEEP the framework-generated Gate 4 PASS proof, quality_manifest composite_score, FR coverage, git tag/hash intact.\\n'\n"
        + "  + '3. SANITY: `grep -c \"^## \" ' + REPO + '/08-config/CONFIG_RECORDS.md && grep -c \"^## \" ' + REPO + '/08-config/RELEASE_CHECKLIST.md` — confirm both files still have the framework sections (count >= baseline).\\n\\n'\n"
        + "  + 'Verdict: report via the StructuredOutput tool — pass=true ONLY if the baseline was verified AND human context appended; reason = one-line summary.\\n\\n'\n"
        + "  + 'SCOPE RULES:\\n- DO NOT regenerate CONFIG_RECORDS.md / RELEASE_CHECKLIST.md from scratch.\\n- DO NOT use Write tool to overwrite either file — Edit/append only.\\n- DO NOT run push-milestone / create archive (next phases do that).\\n- DO NOT modify harness/.\\n- DO NOT re-implement FRs.\\n- ONLY verify baseline + append human context.',\n"
        + "  { label: 'config-docs', phase: 'Config Docs', agentType: 'general-purpose', schema: VERDICT_SCHEMA },\n"
        + ")\n"
        + "if (!(docsReport && docsReport.pass === true)) {\n"
        + "  return { error: 'Phase 8 config docs did not PASS', reason: docsReport ? String(docsReport.reason ?? '').slice(-500) : 'agent returned null' }\n"
        + "}\n"
    )


def _render_verification_docs() -> str:
    return (
        B.render_phase_header("Verification Docs")
        + "// BASELINE.md is a blocking audit-phase C1 deliverable (advance-phase runs the\n"
        + "// audit) and its depth check (C5) counts H2 sections — 7 required per\n"
        + "// harness/templates/BASELINE.md. VERIFICATION_REPORT.md is asserted by\n"
        + "// validate-handoff on the P5→P6 edge.\n"
        + "log('Generate BASELINE.md + VERIFICATION_REPORT.md; re-run integration + security')\n"
        + "const docsReport = await agent(\n"
        + "  'YOU ARE THE P5 VERIFICATION AUTHOR. Generate the verification deliverables.\\n'\n"
        + "  + 'REPO: ' + REPO + '\\nPYTHON: ' + PY + '\\n\\n'\n"
        + "  + 'Steps:\\n'\n"
        + "  + '1. BASELINE: write ' + REPO + '/05-verification/BASELINE.md (system state snapshot). Follow ' + REPO + '/harness/templates/BASELINE.md — EXACTLY 7 `## ` sections (Baseline Overview, Functional Baseline, Quality Baseline, Performance Baseline, Known Issues, Change Log, Acceptance Sign-off); audit-phase counts H2 headings and warns below 7. Fill with real data: current version, test results summary, coverage %, Gate 3 composite score, the 03-development/src/ module list; Change Log from `git -C ' + REPO + ' log --oneline -10`.\\n'\n"
        + "  + '2. VERIFICATION_REPORT: run `' + PY + ' ' + REPO + '/harness_cli.py generate-verification-report --project ' + REPO + '` FIRST — it deterministically generates ' + REPO + '/05-verification/VERIFICATION_REPORT.md from quality_manifest.json (Gate 1/3 results) + SRS.md acceptance criteria, with the correct FR certification precedence (UNKNOWN → FAIL → Conditional PASS → PASS). Then Read the generated file and APPEND richer evidence narrative on top (do not rewrite the generated sections). Must be NON-trivial (validate-handoff checks this). Reference 04-testing/TEST_RESULTS.md. **NOTE**: Mutation testing is gated per-FR at Gate 1 (P3 exit) — DO NOT re-run mutmut here; reference the mutation score from Gate 1 artifacts if needed.\\n'\n"
        + "  + '3. Re-run integration tests: `' + PY + ' -m pytest ' + REPO + '/tests/integration/ -q` (skip gracefully if dir absent).\\n'\n"
        + "  + '4. Confirm performance NFRs: review benchmark entries in 04-testing/TEST_RESULTS.md.\\n'\n"
        + "  + '5. Security clean: `bandit -r ' + REPO + '/03-development/src/ -ll` + `gitleaks detect --source ' + REPO + '`.\\n\\n'\n"
        + "  + 'Verdict: report via the StructuredOutput tool — pass=true ONLY if BOTH BASELINE.md (7 H2 sections) and VERIFICATION_REPORT.md were written and all re-run checks succeeded; reason = one-line summary.\\n\\n'\n"
        + "  + 'SCOPE RULES:\\n- DO NOT run advance-phase / push-milestone.\\n- DO NOT modify harness/.\\n- DO NOT re-implement FRs (only document verification + re-run existing checks).\\n- ONLY generate BASELINE.md + VERIFICATION_REPORT.md + re-run checks.',\n"
        + "  { label: 'verification-docs', phase: 'Verification Docs', agentType: 'general-purpose', schema: VERDICT_SCHEMA },\n"
        + ")\n"
        + "if (!(docsReport && docsReport.pass === true)) {\n"
        + "  return { error: 'Phase 5 verification docs did not PASS', reason: docsReport ? String(docsReport.reason ?? '').slice(-500) : 'agent returned null' }\n"
        + "}\n"
    )


def _render_risk_docs() -> str:
    return (
        B.render_phase_header("Risk Docs")
        + "log('Generate the 3 risk deliverables under 07-risk/')\n"
        + "const docsReport = await agent(\n"
        + "  'YOU ARE THE P7 RISK AUTHOR. Generate the risk deliverables.\\n'\n"
        + "  + 'REPO: ' + REPO + '\\nPYTHON: ' + PY + '\\n\\n'\n"
        + "  + 'Steps (create 07-risk/ if missing):\\n'\n"
        + "  + '1. RISK_REGISTER: write ' + REPO + '/07-risk/RISK_REGISTER.md. Review open issues from Gate 3/4, .methodology/deferred_fixes.md, .sessi-work/issue_registry.json. For each risk: ID, name, likelihood (1–5), impact (1–5), category, mitigation approach. Seed from SPEC.md §9 risk matrix (R1 concurrent write / R2 subprocess hang / R3 breaker deadlock / R4 stale cache).\\n'\n"
        + "  + '2. RISK_MITIGATION_PLANS: write ' + REPO + '/07-risk/RISK_MITIGATION_PLANS.md. For HIGH risks (likelihood × impact ≥ 9): formal mitigation plan with owner + deadline.\\n'\n"
        + "  + '3. RISK_STATUS_REPORT: write ' + REPO + '/07-risk/RISK_STATUS_REPORT.md. Summary of all risks, current status, mitigation owner, target date.\\n\\n'\n"
        + "  + 'All 3 must be NON-trivial (validate-handoff checks presence + well-formedness).\\n'\n"
        + "  + 'Verdict: report via the StructuredOutput tool — pass=true ONLY if all 3 docs were written; reason = one-line summary.\\n\\n'\n"
        + "  + 'SCOPE RULES:\\n- DO NOT run advance-phase / push-milestone.\\n- DO NOT modify harness/.\\n- DO NOT re-implement FRs.\\n- ONLY generate the 3 risk docs.',\n"
        + "  { label: 'risk-docs', phase: 'Risk Docs', agentType: 'general-purpose', schema: VERDICT_SCHEMA },\n"
        + ")\n"
        + "if (!(docsReport && docsReport.pass === true)) {\n"
        + "  return { error: 'Phase 7 risk docs did not PASS', reason: docsReport ? String(docsReport.reason ?? '').slice(-500) : 'agent returned null' }\n"
        + "}\n"
    )


def _render_archive() -> str:
    return (
        B.render_phase_header("Archive")
        + "// P8-ARCHIVE + P8-HANDOVER-CHECK — required by CI p8-archive-check.\n"
        + "log('Create .methodology-archive/ + verify HANDOVER.md has no Phase 9 refs')\n"
        + "const archiveReport = await agent(\n"
        + "  'YOU ARE THE P8 ARCHIVE ORCHESTRATOR. Prepare the archive (REQUIRED before p8 push).\\n'\n"
        + "  + 'REPO: ' + REPO + '\\n\\n'\n"
        + "  + 'Steps (Bash):\\n'\n"
        + "  + '1. P8-ARCHIVE: `mkdir -p ' + REPO + '/.methodology-archive && cp -r ' + REPO + '/.methodology/ ' + REPO + '/.methodology-archive/`. (push-milestone _validate_p8_completion + CI p8-archive-check both verify this dir. Source MUST be `.methodology/` — NOT `.sessi-work/` per harness commit 3f1fd73 which fixed the wrong-source silent bug.)\\n'\n"
        + "  + '2. P8-HANDOVER-CHECK: `grep -qi \"phase 9\\\\|phase9\\\\|phase9_plan\" ' + REPO + '/HANDOVER.md && echo \"HAS_P9\" || echo \"NO_P9\"`. Phase 8 is final — if HAS_P9, remove the Phase 9 references from HANDOVER.md (Edit).\\n\\n'\n"
        + "  + 'Verdict: report via the StructuredOutput tool — pass=true ONLY if the archive dir was created AND HANDOVER.md has no Phase 9 refs; reason = one-line summary.\\n\\n'\n"
        + "  + 'SCOPE RULES:\\n- DO NOT run push-milestone yet.\\n- DO NOT modify harness/.\\n- ONLY create .methodology-archive/ + clean HANDOVER.md Phase 9 refs.',\n"
        + "  { label: 'archive', phase: 'Archive', agentType: 'general-purpose', schema: VERDICT_SCHEMA },\n"
        + ")\n"
        + "if (!(archiveReport && archiveReport.pass === true)) {\n"
        + "  return { error: 'Phase 8 archive prep did not PASS', reason: archiveReport ? String(archiveReport.reason ?? '').slice(-500) : 'agent returned null' }\n"
        + "}\n"
    )


def _render_final_push() -> str:
    return (
        B.render_phase_header("Final Push")
        + "log('push-milestone p8 (final — pipeline complete)')\n"
        + "// Round loop (2026-07-02 audit finding, ported from phase3): this round's\n"
        + "// two steps (push-milestone p8, then advance-phase) each run their own\n"
        + "// independent completion checks that are more than any single prompt can\n"
        + "// safely enumerate, and a static checklist goes stale the moment harness\n"
        + "// adds or changes one. The GUARD at step 0 makes this safe to re-run: an\n"
        + "// already-pushed p8 commit short-circuits immediately.\n"
        + "let p8Ok = false, pushReport = ''\n"
        + "const ADVANCE_MAX_ROUNDS = 5\n"
        + "for (let round = 1; round <= ADVANCE_MAX_ROUNDS; round++) {\n"
        + "  log('  Final Push round ' + round + '/' + ADVANCE_MAX_ROUNDS)\n"
        + "  // Last-line integrity guard: the phase-exit push commits .methodology/\n"
        + "  // wholesale — block here so mid-run corruption never reaches git history\n"
        + "  // (2026-07-02: commit 3198402 baked a corrupted manifest into main).\n"
        + "  // Re-check every round — a fix attempt in a prior round could reintroduce it.\n"
        + "  const advIntegrity = await checkManifestIntegrity('Final Push', 'advance-integrity-r' + round)\n"
        + "  if (!advIntegrity.ok) {\n"
        + "    return { error: 'Final Push round ' + round + ': quality_manifest.json corrupted — refusing to commit it', detail: advIntegrity.raw, recovery: 'git checkout HEAD -- .methodology/quality_manifest.json (verify HEAD is healthy first), merge the latest gate result back into gate_results, then resume', note: 'Blocking prevents the corruption from being committed by the p8 final push.' }\n"
        + "  }\n"
        + "  pushReport = await agent(\n"
        + "    'YOU ARE THE P8 FINAL PUSHER. This is the LAST step of the 8-phase pipeline. ROUND ' + round + '.\\n'\n"
        + "    + 'REPO: ' + REPO + '\\nPYTHON: ' + PY + '\\n\\n'\n"
        + "    + 'Steps:\\n'\n"
        + "    + '0. GUARD: `git -C ' + REPO + ' log --oneline --grep=\"P8\" -1`. If exists, report \"P8-PUSH: PASS (already pushed)\" and stop.\\n'\n"
        + "    + '1. PUSH ⑩: `' + PY + ' ' + REPO + '/harness_cli.py push-milestone --type p8 --project ' + REPO + '`. _validate_p8_completion checks the `.methodology-archive/` presence + contents (its output tells you exactly what is missing — lint/types/coverage/Phase Truth are advance-phase\\'s job, step 2 below, not this step\\'s). If it prints \"[BLOCKED] ...\" or \"[ERROR] P8 push blocked ...\", that message IS the fix instruction: read it verbatim and do exactly what it says, then re-run this same push-milestone command. Do NOT guess what might be wrong — trust only what push-milestone itself reports. It is safe to re-run repeatedly within this round. On success it writes HANDOVER.md + commits + pushes. If a hook blocks, reword commit to start with `chore(harness):` (NOT --no-verify), retry.\\n'\n"
        + "    + '2. ADVANCE: `' + PY + ' ' + REPO + '/harness_cli.py advance-phase --completed 8 --project ' + REPO + '`. This transitions into Phase 9 (Maintenance — steady-state, CR-driven). advance-phase independently re-verifies EVERYTHING (TDD-PRECHECK, HR-11 Phase Truth, HR-17 submodule guard, etc.) — its own output tells you exactly what is missing. If it prints \"[BLOCKED] ...\", that message IS the fix instruction. It is safe to re-run repeatedly within this round.\\n'\n"
        + "    + '3. Read ' + REPO + '/.methodology/state.json; confirm current_phase >= 8.\\n\\n'\n"
        + "    + 'Report final line: \"P8-PUSH: PASS|FAIL — <details>\". If still FAIL after exhausting this round\\'s turn, report the LAST [BLOCKED] message verbatim so the next round starts from where this one left off. PHASE_9_PLAN: ' + REPO + '/.methodology/phase9_plan.md\\n\\n'\n"
        + "    + 'SCOPE RULES:\\n- DO NOT use --no-verify.\\n- DO NOT modify harness/ (HR-17).\\n- ONLY push-milestone p8 + advance-phase --completed 8 + the specific fixes their own output asked for.\\n- Any diagnostic/debug script MUST be written under .sessi-work/tmp/ (never repo root or source dirs) and self-cleaned before you exit.',\n"
        + "    { label: 'final-push-r' + round, phase: 'Final Push', agentType: 'general-purpose' },\n"
        + "  )\n"
        + "  if (pushReport === null || pushReport === undefined || (typeof pushReport === 'string' && pushReport.length < 10)) {\n"
        + "    log('  Final Push agent blocked (session limit / rate limit) — aborting retries, resume after quota reset')\n"
        + "    return { session_limit_blocked: true, phase: 8, step: 'final-push', message: 'Agent hit session/rate limit during Final Push. Resume after quota reset — the GUARD step skips if already pushed.' }\n"
        + "  }\n"
        + "  // AUTHORITATIVE Final Push verdict: push-milestone p8 creates a milestone\n"
        + "  // commit — the same artifact the step-0 GUARD checks. Read git log via a\n"
        + "  // schema proxy; the pusher's prose \"P8-PUSH: PASS\" is narrative only.\n"
        + "  const p8VerifyCmd = 'git -C ' + REPO + ' log --oneline --grep=\"P8\" -1'\n"
        + "  const p8v = await agent(\n"
        + "    'Run EXACTLY this command via the Bash tool:\\n`' + p8VerifyCmd + '`\\n'\n"
        + "    + 'Then report via the StructuredOutput tool: pass = true ONLY if stdout contains a commit line (non-empty); reason = the verbatim stdout (or \"empty\").',\n"
        + "    { label: 'p8-verify-r' + round, phase: 'Final Push', agentType: 'general-purpose', schema: VERDICT_SCHEMA },\n"
        + "  )\n"
        + "  p8Ok = !!(p8v && p8v.pass === true)\n"
        + "  if (p8Ok) { log('  Final Push PASS [git-verified: ' + String(p8v.reason ?? '').slice(0, 80) + ']'); break }\n"
        + "  log('  Final Push not yet PASS [' + (p8v ? String(p8v.reason ?? '').slice(0, 80) : 'verify agent null') + '] — retry round ' + (round + 1))\n"
        + "}\n"
        + "if (!p8Ok) return { error: 'Phase 8 p8 push did not PASS in ' + ADVANCE_MAX_ROUNDS + ' rounds — check the last [BLOCKED] message below', raw: String(pushReport ?? '').slice(-600) }\n"
        + "\n"
        + "log('Phase 8 push-milestone + advance-phase complete. 🎉 Pipeline complete — Phase 9 (Maintenance) begins.')\n"
    )


def generate_phase8() -> str:
    parts = [
        _HEADER_8,
        "",
        _render_meta(
            name="phase8-config",
            description=(
                "Phase 8 Config — per-FR GATE1-DELTA + "
                "CONFIG_RECORDS/RELEASE_CHECKLIST + archive + p8 push "
                "(phase8_plan.md v2.12.0)"
            ),
            phases=_META_PHASES_8,
        ),
        "",
        B.RESOLVE_REPO_BLOCK,
        "",
        B.WRITE_SCOPE_BLOCK,
        "",
        B.render_schemas(["VERDICT_SCHEMA", "RC_SCHEMA", "CTX_SCHEMA", "DELTA_FAST_SCHEMA"]),
        B.render_entry_preflight(
            phase=8, gate_num=4, gate_owner_phase=6, prev_phase=7,
            extra_note=(
                "- DO NOT generate config docs / run TDD steps / create archive.\\n"
                "- DO NOT run push-milestone.\\n"
            ),
        ),
        B.render_env_check(phase=8),
        B.render_manifest_integrity_phase(phase=8),
        B.render_load_frs(phase=8),
        B.render_per_fr_delta(
            phase=8,
            forbidden_note="- DO NOT run push-milestone / generate config docs / create archive.\\n",
            verifier_role="CONFIG-AWARE VERIFIER",
        ),
        _render_config_docs(),
        B.render_artifacts_commit(
            paths=["08-config/CONFIG_RECORDS.md", "08-config/RELEASE_CHECKLIST.md", ".methodology"],
            commit_msg="chore(p8): config-records + release-checklist artifacts",
            phase=8,
        ),
        _render_archive(),
        _render_final_push(),
        B.render_sync(extra_lines=[
            "3. `git -C ' + REPO + ' tag -l \\\"harness-v*\\\" | head -3` — confirm any Phase 6 gate4 tag is pushed; if there is a P6 tag but `git push origin --tags` hasn\\'t run yet, push tags.",
        ]),
        (
            "\nreturn {\n"
            "  phase: 8,\n"
            "  fr_count: frIds.length,\n"
            "  gate1_pass: gate1Pass,\n"
            "  p8_push_status: p8Ok ? 'PASS' : 'unknown',\n"
            "  artifacts: ['08-config/CONFIG_RECORDS.md', '08-config/RELEASE_CHECKLIST.md', '.methodology-archive/', 'HANDOVER.md'],\n"
            "  notes: 'Phase 8 complete per phase8_plan.md v2.12.0. Full P1→P8 pipeline complete → Phase 9 (Maintenance, CR-driven steady state).',\n"
            "}\n"
        ),
    ]
    return "\n".join(p for p in parts if p is not None)


def generate_phase5() -> str:
    parts = [
        _HEADER_5,
        "",
        _render_meta(
            name="phase5-verification",
            description=(
                "Phase 5 Verification — per-FR GATE1-DELTA + "
                "BASELINE/VERIFICATION_REPORT + p5-baseline push "
                "(phase5_plan.md v2.12.0)"
            ),
            phases=_META_PHASES_5,
        ),
        "",
        B.RESOLVE_REPO_BLOCK,
        "",
        B.WRITE_SCOPE_BLOCK,
        "",
        B.render_schemas(["VERDICT_SCHEMA", "RC_SCHEMA", "CTX_SCHEMA", "DELTA_FAST_SCHEMA", "PHASE_SCHEMA"]),
        B.render_entry_preflight(
            phase=5, gate_num=3, gate_owner_phase=4, prev_phase=4,
            extra_note=(
                "- DO NOT generate BASELINE/VERIFICATION docs or run TDD steps.\\n"
                "- DO NOT run advance-phase/push-milestone.\\n"
            ),
        ),
        B.render_env_check(phase=5),
        B.render_manifest_integrity_phase(phase=5),
        B.render_load_frs(phase=5, include_fr_titles=True),
        B.render_per_fr_delta(
            phase=5,
            forbidden_note="- DO NOT run advance-phase / push-milestone / generate BASELINE docs.\\n",
            verifier_role="VERIFIER",
            use_fr_titles=True,
        ),
        _render_verification_docs(),
        B.render_artifacts_commit(
            paths=["05-verification", ".methodology"],
            commit_msg="chore(p5): baseline + verification-report artifacts",
            phase=5,
        ),
        B.render_milestone(
            phase=5, milestone_type="p5-baseline", guard_grep="P5): BASELINE.md",
            label="milestone-baseline",
            extra_note=" (after VERIFICATION_REPORT.md generated)",
        ),
        B.render_advance_loop(
            phase=5, next_phase=6,
            precheck_steps=[
                "D4-GAP: `' + PY + ' ' + REPO + '/harness_cli.py spec-coverage-check --project ' + REPO + ' --threshold 90.0`. Gate 4 (next phase) needs ≥90% but advance only needs 80% — if below 90%, ADD missing test implementations NOW to avoid a Gate 4 surprise.",
            ],
            scope_extra="- DO NOT re-do P5 docs.\\n",
            only_extra="spec-coverage-check + ",
            log_msg="D4 90% gap warning + advance-phase --completed 5 (TDD-PRECHECK enforced)",
        ),
        B.render_sync_verified(),
        (
            "\nlog('Phase 5 workflow complete. Open .methodology/phase6_plan.md to continue.')\n"
            "return {\n"
            "  phase: 5,\n"
            "  fr_count: frIds.length,\n"
            "  gate1_pass: gate1Pass,\n"
            "  advance_status: 'PASS',\n"
            "  artifacts: ['05-verification/BASELINE.md', '05-verification/VERIFICATION_REPORT.md', 'HANDOVER.md'],\n"
            "  notes: 'Phase 5 complete per phase5_plan.md v2.12.0. Phase 6 (Quality Assurance) ready. Reminder: Gate 4 needs spec-coverage ≥90%.',\n"
            "}\n"
        ),
    ]
    return "\n".join(p for p in parts if p is not None)


def generate_phase7() -> str:
    parts = [
        _HEADER_7,
        "",
        _render_meta(
            name="phase7-risk",
            description=(
                "Phase 7 Risk — per-FR GATE1-DELTA + "
                "RISK_REGISTER/MITIGATION/STATUS + p7 push "
                "(phase7_plan.md v2.12.0)"
            ),
            phases=_META_PHASES_7,
        ),
        "",
        B.RESOLVE_REPO_BLOCK,
        "",
        B.WRITE_SCOPE_BLOCK,
        "",
        B.render_schemas(["VERDICT_SCHEMA", "RC_SCHEMA", "CTX_SCHEMA", "DELTA_FAST_SCHEMA", "PHASE_SCHEMA"]),
        B.render_entry_preflight(
            phase=7, gate_num=4, gate_owner_phase=6, prev_phase=6,
            extra_note=(
                "- DO NOT generate risk docs or run TDD steps.\\n"
                "- DO NOT run advance-phase/push-milestone.\\n"
            ),
        ),
        B.render_env_check(phase=7),
        B.render_manifest_integrity_phase(phase=7),
        B.render_load_frs(phase=7, include_fr_titles=True),
        B.render_per_fr_delta(
            phase=7,
            forbidden_note="- DO NOT run advance-phase / push-milestone / generate risk docs.\\n",
            verifier_role="RISK-AWARE VERIFIER",
            use_fr_titles=True,
        ),
        _render_risk_docs(),
        B.render_artifacts_commit(
            paths=["07-risk", ".methodology"],
            commit_msg="chore(p7): risk-register artifacts",
            phase=7,
        ),
        B.render_milestone(
            phase=7, milestone_type="p7", guard_grep="P7",
            label="milestone-p7",
            extra_note=" (after risk register complete)",
        ),
        B.render_advance_loop(
            phase=7, next_phase=8,
            scope_extra="- DO NOT re-do P7 docs.\\n",
            log_msg="advance-phase --completed 7 (TDD-PRECHECK + D4 90% enforced)",
        ),
        B.render_sync_verified(),
        (
            "\nlog('Phase 7 workflow complete. Open .methodology/phase8_plan.md to continue.')\n"
            "return {\n"
            "  phase: 7,\n"
            "  fr_count: frIds.length,\n"
            "  gate1_pass: gate1Pass,\n"
            "  advance_status: 'PASS',\n"
            "  artifacts: ['07-risk/RISK_REGISTER.md', '07-risk/RISK_MITIGATION_PLANS.md', '07-risk/RISK_STATUS_REPORT.md', 'HANDOVER.md'],\n"
            "  notes: 'Phase 7 complete per phase7_plan.md v2.12.0. Phase 8 (Configuration Management) ready.',\n"
            "}\n"
        ),
    ]
    return "\n".join(p for p in parts if p is not None)
