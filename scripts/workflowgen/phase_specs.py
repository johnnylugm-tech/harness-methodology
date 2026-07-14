"""Per-phase workflow assembly — the declarative spec layer.

Mirrors `scripts/plangen/phase_tasks.py`'s one-function-per-phase shape:
each `generate_phaseN()` assembles shared `js_blocks` renderers plus the
phase's own unique content (verbatim business logic that genuinely differs
per phase — Config Docs / Archive / Final Push have no shared counterpart
because no other phase does what they do) into the final JS source text.

Phases 3, 4, 5, 7, 8 are implemented (Round 11 stations 1-3). Station 4 adds
generate_phase6/1/2 following the same shape.
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

_HEADER_3 = """\
// Phase 3 — Implementation (faithful to .methodology/phase3_plan.md v2.12.0)
//
// GENERATED FILE — do not hand-edit. Source of truth:
// scripts/workflowgen/phase_specs.py::generate_phase3() (+ js_blocks.py for
// the blocks shared across phase workflow files). Regenerate with:
//   python3 scripts/workflowgen/generate_workflows.py --write --phase 3
//
// Structure: FR-loop型 + Gate 2 exit. Script holds the per-FR loop (playbook
// "plan as code"): load fr_ids via an agent, then for each FR dispatch a
// narrow agent that runs the TDD chain (RED→MIRROR→GREEN→IMPROVE→GATE1).
// Milestone pushes are script-driven (≥1/3 → p3-mid; all done → p3-pre-gate2).
// Gate 2 is one orchestrator agent (run-gate → eval → finalize → D4 60%).
//
// Playbook lessons: NO import/fs/process, Bash for all harness CLI,
// SCOPE RULES per agent, PY = .venv/bin/python, scriptPath launch.
// v4 (2026-07-02): gate verdicts use FLAT schema: (playbook §5.2 rev) — the
// v2 blanket schema ban was itself a workaround that forced every gate onto
// regex-over-LLM-prose, the root cause of bugs #126/#134/#135/#136 and the
// ENV_CHECK_RC paraphrase false-negative. Flat 2-3 field schemas on bash-proxy
// agents are runtime-validated (AJV + 2 retries); complex nested schemas on
// heavy-cognition agents remain forbidden (that was the real v2 lesson).
"""

_HEADER_4 = """\
// Phase 4 — Testing (faithful to .methodology/phase4_plan.md v2.12.0)
//
// GENERATED FILE — do not hand-edit. Source of truth:
// scripts/workflowgen/phase_specs.py::generate_phase4() (+ js_blocks.py for
// the blocks shared across phase workflow files). Regenerate with:
//   python3 scripts/workflowgen/generate_workflows.py --write --phase 4
//
// Structure: FR-loop型 + adversarial bug hunt + Gate 3 (15 dims) exit.
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

_META_PHASES_3 = [
    "Entry & Preflight", "Env Check", "Manifest Integrity", "Load FRs",
    "Per-FR TDD", "Milestones", "Gate 2", "Advance", "Sync",
]

_META_PHASES_4 = [
    "Entry & Preflight", "Test Plan", "Env Check", "Manifest Integrity",
    "Load FRs", "Per-FR Delta", "Coverage", "Bug Hunt", "Artifacts Commit",
    "Gate 3", "Advance", "Sync",
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
        + "  return { error: 'Phase 4 TEST_PLAN did not PASS', reason: testPlanReport ? String(testPlanReport.reason ?? '').slice(-500) : 'agent returned null' }\n"
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
        + "  + '1. TEST_RESULTS: write ' + REPO + '/04-testing/TEST_RESULTS.md — summarise test execution: cases run, pass/fail, deferred issues. (Real execution is enforced by advance-phase pytest --cov-fail-under=100, not by string-matching this doc.)\\n'\n"
        + "  + '2. COVERAGE: run `' + PY + ' -m pytest --cov=03-development/src --cov-report=term-missing -q | tee ' + REPO + '/04-testing/coverage_raw.txt` then `' + PY + ' -m coverage report --format=total`. Write ' + REPO + '/04-testing/COVERAGE_REPORT.md with overall coverage % (≥80% for Gate 3), per-module breakdown, uncovered lines.\\n'\n"
        + "  + '   WARNING: cross_artifact.py validates these numbers against live pytest --cov at Gate 3 — fabricated numbers are caught. Use REAL numbers.\\n\\n'\n"
        + "  + 'Verdict: report via the StructuredOutput tool — pass=true ONLY if both docs were written from real pytest output; reason = one-line summary.\\n\\n'\n"
        + "  + 'SCOPE RULES:\\n- DO NOT run run-gate / bug-hunt / advance.\\n- DO NOT modify harness/.\\n- DO NOT fabricate coverage numbers.\\n- ONLY generate the 2 docs from real pytest output.',\n"
        + "  { label: 'coverage', phase: 'Coverage', agentType: 'general-purpose', schema: VERDICT_SCHEMA },\n"
        + ")\n"
        + "if (!(coverageReport && coverageReport.pass === true)) {\n"
        + "  return { error: 'Phase 4 coverage docs did not PASS', reason: coverageReport ? String(coverageReport.reason ?? '').slice(-500) : 'agent returned null' }\n"
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
        + "  + 'Verdict: report via the StructuredOutput tool — pass=true ONLY if bug_hunt_report.json was written AND all confirmed critical/high findings are resolved-or-refuted; reason = one-line summary. (Truth is enforced downstream: Gate 3\\'s framework-owned adversarial_review dim re-reads the report itself.)\\n\\n'\n"
        + "  + 'SCOPE RULES:\\n- DO NOT run run-gate (Gate 3) / advance-phase / push-milestone.\\n- DO NOT modify harness/ (running its scripts/prompts is fine; editing is NOT — HR-17).\\n- ONLY targets + hunt + resolve + write bug_hunt_report.json.',\n"
        + "  { label: 'bug-hunt', phase: 'Bug Hunt', agentType: 'general-purpose', model: HUNT_MODEL, schema: VERDICT_SCHEMA },\n"
        + ")\n"
        + "if (!(huntReport && huntReport.pass === true)) {\n"
        + "  return { error: 'Phase 4 bug hunt did not PASS (Gate 3 adversarial_review will block)', reason: huntReport ? String(huntReport.reason ?? '').slice(-600) : 'agent returned null' }\n"
        + "}\n"
    )


def _render_phase3_entry_preflight() -> str:
    """phase3's Entry & Preflight has a 5th step (P2-ARTIFACTS) that no other
    migrated phase has — it's the only phase transitioning FROM Phase 2's
    architecture artifacts specifically. render_entry_preflight()'s 4-step
    template is already shared/golden-locked by phase4/5/7/8; forcing a 5th
    conditional step into it for one outlier is the wrong abstraction, so
    this stays a phase3-specific verbatim renderer."""
    return (
        B.render_phase_header("Entry & Preflight")
        + "log('ENTRY-CHECK + P2-ARTIFACTS + run-phase 3 + validate-handoff + CI')\n"
        + "const preflightReport = await agent(\n"
        + "  'YOU ARE THE PHASE-3 PREFLIGHT ORCHESTRATOR. Run bash in order; report.\\n'\n"
        + "  + 'REPO: ' + REPO + '\\nPYTHON: ' + PY + '\\n\\n'\n"
        + "  + 'Steps:\\n'\n"
        + "  + '1. ENTRY-CHECK: `git -C ' + REPO + ' log --oneline --grep=\"phase2(review-complete)\" -1` OR confirm P2 artifacts exist.\\n'\n"
        + "  + '2. P2-ARTIFACTS: `ls ' + REPO + '/02-architecture/SAD.md ' + REPO + '/02-architecture/adr/ADR.md ' + REPO + '/02-architecture/TEST_SPEC.md ' + REPO + '/.methodology/quality_manifest.json ' + REPO + '/.methodology/SAB.json`. ALL must exist (else FAIL → return to Phase 2).\\n'\n"
        + "  + '3. PREFLIGHT: `' + PY + ' ' + REPO + '/harness_cli.py run-phase --phase 3 --project ' + REPO + '`. FAIL → fix FSM/Constitution/Drift, re-run (max 3).\\n'\n"
        + "  + '4. HANDOFF: `' + PY + ' ' + REPO + '/harness_cli.py validate-handoff --from-phase 2 --project ' + REPO + '`. Must exit 0.\\n'\n"
        + "  + '5. PREFLIGHT-CI: confirm `' + REPO + '/.github/workflows/harness_quality_gate.yml` (CI workflow) + `' + REPO + '/.git/hooks/prepare-commit-msg` (git hook) both exist; confirm state.json current_phase=3. If stale: `init-project --phase 3 --project ' + REPO + ' --overwrite`.\\n\\n'\n"
        + "  + 'Verdict: report via the StructuredOutput tool — pass=true ONLY if ALL 5 steps succeeded; reason = one-line summary (on FAIL: which step + verbatim error tail).\\n\\n'\n"
        + "  + 'SCOPE RULES:\\n- DO NOT implement any FR or run TDD steps.\\n- DO NOT run advance-phase/push-milestone/run-gate.\\n- DO NOT modify harness/.\\n- ONLY preflight commands + fixes.',\n"
        + "  { label: 'preflight', phase: 'Entry & Preflight', agentType: 'general-purpose', schema: VERDICT_SCHEMA },\n"
        + ")\n"
        + "if (!(preflightReport && preflightReport.pass === true)) {\n"
        + "  return { error: 'Phase 3 preflight did not PASS', reason: preflightReport ? String(preflightReport.reason ?? '').slice(-600) : 'agent returned null (skipped or terminal API error)' }\n"
        + "}\n"
    )


def _render_phase3_load_frs() -> str:
    """phase3's Load FRs computes fr_titles SERVER-SIDE (Python, forwarded via
    CTX_SCHEMA's fr_titles field) — a different, WORKING mechanism from the
    shared render_load_frs(include_fr_titles=True), whose client-side
    `Array.isArray(ctx.fr_details)` check is always false (fr_details is a
    dict keyed by FR id, per cli/project_cmds.py:666 — confirmed pre-existing
    in phase5/7/8's original files too, not introduced by this migration).
    Forcing phase3 onto the shared (broken) mechanism would be a real
    regression, so this stays its own renderer. Also appends the Gate 1
    pre-check (identify already-PASSed FRs to skip on resume) that no other
    migrated phase has — phase3 is the only full-TDD-chain phase where
    "already done" is a meaningful per-FR skip condition.
    """
    return (
        B.render_phase_header("Load FRs")
        + "log('load-context --phase 3 → fr_ids (script holds the loop)')\n"
        + "// v15: retry loop — agent() wrapped (Bug #2); v4: schema transport, no prose parsing\n"
        + "// v2.13.1: hardened against agent hallucination — verify .sessi-work/phase3_ctx.json\n"
        + "// actually exists and contains non-empty fr_ids before accepting (Bug #122).\n"
        + "let ctx = null\n"
        + "const ctxFile = REPO + '/.sessi-work/phase3_ctx.json'\n"
        + "for (let attempt = 1; attempt <= 3; attempt++) {\n"
        + "  try {\n"
        + "    const ctxCheckCmd = `${PY} -c \"import json,os,sys; json.load(open('${ctxFile}')); print('FILE_OK_'+str(os.path.getsize('${ctxFile}')))\" || echo FILE_MISSING`\n"
        + "    const existsVerdict = await agent(\n"
        + "      `You MUST use the Bash tool. Run exactly:\\n${ctxCheckCmd}\\nThen report via the StructuredOutput tool: pass = true ONLY if stdout starts with FILE_OK_; reason = the verbatim stdout.`,\n"
        + "      { label: 'ctx-check-' + attempt, phase: 'Load FRs', agentType: 'general-purpose', schema: VERDICT_SCHEMA },\n"
        + "    )\n"
        + "    if (!(existsVerdict && existsVerdict.pass === true)) {\n"
        + "      log('  ctx file missing/invalid (attempt ' + attempt + ') — regenerating')\n"
        + "      const ctxRegenCmd = `${PY} ${REPO}/harness_cli.py load-context --phase 3 --project ${REPO} --json > ${ctxFile} && ${PY} -c \"import json,os; json.load(open('${ctxFile}')); print('REGEN_OK_'+str(os.path.getsize('${ctxFile}')))\"`\n"
        + "      await agent(\n"
        + "        `You MUST use the Bash tool. Run exactly:\\n${ctxRegenCmd}\\nReturn the raw stdout as your final message.`,\n"
        + "        { label: 'ctx-regen-' + attempt, phase: 'Load FRs', agentType: 'general-purpose' },\n"
        + "      )\n"
        + "      continue\n"
        + "    }\n"
        + "  } catch (e) { log('  ctx-check agent failed: ' + String(e.message ?? e).slice(0, 80)); continue }\n"
        + "\n"
        + "  try {\n"
        + "    // J1 fix (2026-06-29): forward fr_titles too. load-context emits fr_details as a\n"
        + "    // DICT keyed by FR id ({\"FR-01\":{\"title\":...}}). The previous parse only forwarded\n"
        + "    // fr_details_keys (no titles), and the consumer (frTitle below) read it as an Array\n"
        + "    // — so titles silently never populated. Emit an {id:title} map the consumer uses\n"
        + "    // directly.\n"
        + "    const ctxParseCmd = `${PY} -c \"import json; d=json.load(open('${ctxFile}')); fd=d.get('fr_details') or {}; print(json.dumps({'fr_ids':d.get('fr_ids',[]),'fr_count':len(d.get('fr_ids',[])),'fr_titles':{k:(v.get('title','') if isinstance(v,dict) else '') for k,v in fd.items()}}))\"`\n"
        + "    const ctxResult = await agent(\n"
        + "      `You MUST use the Bash tool. Run exactly:\\n${ctxParseCmd}\\nStdout is a single JSON line. Report via the StructuredOutput tool: fr_ids, fr_count, fr_titles = the EXACT values from that JSON line (transcribe, do not recompute).`,\n"
        + "      { label: 'load-ctx-a' + attempt, phase: 'Load FRs', agentType: 'general-purpose', schema: CTX_SCHEMA },\n"
        + "    )\n"
        + "    if (ctxResult && Array.isArray(ctxResult.fr_ids) && ctxResult.fr_ids.length > 0) {\n"
        + "      ctx = ctxResult\n"
        + "      log('  load-ctx OK (schema-validated, ' + ctx.fr_ids.length + ' FRs)')\n"
        + "      break\n"
        + "    }\n"
        + "    log('  load-ctx returned empty fr_ids (attempt ' + attempt + '): keys=' + Object.keys(ctxResult ?? {}).join(','))\n"
        + "  } catch (e) { log('  load-ctx agent failed: ' + String(e.message ?? e).slice(0, 80)); continue }\n"
        + "}\n"
        + "if (!ctx) return { error: 'Load FRs: ctx failed after 3 attempts', ctxFile }\n"
        + "let frIds = Array.isArray(ctx.fr_ids) ? ctx.fr_ids : []\n"
        + "if (!frIds.length) return { error: 'Load FRs: no fr_ids found in ctx', ctxKeys: Object.keys(ctx) }\n"
        + "// J1: fr_titles is the {id:title} map emitted by ctxParseCmd above.\n"
        + "const frTitle = (ctx.fr_titles && typeof ctx.fr_titles === 'object') ? ctx.fr_titles : {}\n"
        + "log('  fr_ids = ' + JSON.stringify(frIds))\n"
        + "\n"
        + "// Gate 1 pre-check: identify FRs that ALREADY passed Gate 1 (skip TDD on resume/re-run).\n"
        + "// AUTHORITATIVE source = quality_manifest.gate_results.gate1[fr].quality_complete, which\n"
        + "// harness_bridge writes on EVERY finalize-gate (pass OR fail). NOT the g1_p3_*.flag\n"
        + "// sentinel: that flag is written by run-gate (it only proves run-gate executed), so a\n"
        + "// finalize-gate that raised GateBlockedError on a failing dimension still leaves the\n"
        + "// sentinel behind — using it as a PASS signal misreports blocked FRs as done.\n"
        + "const precheckCmd = PY + ' -c \"import json; g=(json.load(open(\\'' + REPO + '/.methodology/quality_manifest.json\\')).get(\\'gate_results\\',{}) or {}).get(\\'gate1\\',{}) or {}; print(chr(10).join(fr for fr,v in g.items() if isinstance(v,dict) and v.get(\\'quality_complete\\') is True))\"'\n"
        + "const precheckResult = await agent(\n"
        + "  'Run EXACTLY this command via the Bash tool (stdout is a newline-separated list of FR ids, possibly empty):\\n`' + precheckCmd + '`\\n'\n"
        + "  + 'Then report via the StructuredOutput tool: fr_ids_done = the EXACT FR ids from stdout as an array (empty array if stdout is empty).',\n"
        + "  { label: 'gate1-precheck', phase: 'Load FRs', agentType: 'general-purpose', schema: FR_LIST_SCHEMA }\n"
        + ")\n"
        + "const alreadyDone = new Set()\n"
        + "for (const id of (precheckResult && Array.isArray(precheckResult.fr_ids_done) ? precheckResult.fr_ids_done : [])) {\n"
        + "  if (/^FR-\\d+$/.test(String(id).trim())) alreadyDone.add(String(id).trim())\n"
        + "}\n"
        + "if (alreadyDone.size > 0) log('  sentinel pre-check: Gate 1 (Phase 3) already PASS for ' + [...alreadyDone].join(', ') + ' — skipping TDD agents')\n"
    )


def _render_per_fr_tdd() -> str:
    """phase3's Per-FR TDD is the ONLY full-implementation chain (RED→MIRROR
    →GREEN→IMPROVE→amend-sab→GATE1→ORCH-POST) — no other migrated phase runs
    TDD-RED/GREEN/IMPROVE at all (they only re-evaluate via GATE1-DELTA), so
    this has no shared counterpart. Includes the mid-loop p3-mid milestone
    push (fires once at the historical, textually-inconsistent "≥1/3" wording
    despite the actual threshold formula computing 50% — Math.max(1,
    Math.floor(n/2)) — a pre-existing mismatch between comment/log text and
    code preserved verbatim, not corrected by this migration) and the
    [FATAL] structurally-broken-dispatch abort path unique to this phase.
    """
    return (
        B.render_phase_header("Per-FR TDD")
        + "const gate1Pass = []\n"
        + "const gate1Fail = []\n"
        + "let p3MidPushed = false\n"
        + "const p3MidThreshold = Math.max(1, Math.floor(frIds.length / 2))  // PUSH ③ trigger: ≥50% FRs Gate 1 PASS (phase3_plan.md, harness push_cmds.py)\n"
        + "for (const frId of frIds) {\n"
        + "  if (alreadyDone.has(frId)) {\n"
        + "    log('  ' + frId + ' — sentinel exists, Gate 1 PASS (skip TDD)')\n"
        + "    gate1Pass.push(frId)\n"
        + "  } else {\n"
        + "        log('  === ' + frId + ' (' + (frTitle[frId] || '') + ') — TDD chain ===')\n"
        + "    const frNum = frId.match(/\\d+/)[0].padStart(2, '0')\n"
        + "    const frReport = await agent(\n"
        + "      'YOU ARE THE IMPLEMENTER for ' + frId + ' (' + (frTitle[frId] || '') + '). Run the full TDD chain for THIS ONE FR.\\n'\n"
        + "      + 'REPO: ' + REPO + '\\nPYTHON: ' + PY + '\\n\\n'\n"
        + "      + 'Direction C (past lessons): FIRST, Bash `cat ' + REPO + '/.sessi-work/phase3_ctx.json` and READ the `lessons` field (compact markdown, \"\" if none). DO NOT repeat those past failure modes in this FR\\'s TDD chain (implementation / tests / GATE1 fixes).\\n\\n'\n"
        + "      + 'Run these harness steps IN ORDER (each is a bash command; read its output before the next):\\n'\n"
        + "      + '1. TDD-RED:    `' + PY + ' ' + REPO + '/harness_cli.py run-fr-step --phase 3 --fr-id ' + frId + ' --step TDD-RED --project ' + REPO + ' --srs 01-requirements/SRS.md`\\n'\n"
        + "  + '   AFTER RED writes the test file: open `tests/test_fr' + frNum + '.py` and ensure EVERY NFR associated with ' + frId + ' in the traceability table (TRACEABILITY_MATRIX.md §5 is the canonical listing) has a `# NFR-XX` comment on at least one test function. Without these annotations, `compute_trace_dimension` 4c = 0% and Gate 2 blocks (HR-16). Use `grep -n \"# NFR-\" tests/test_fr' + frNum + '.py` and check against the NFR list for ' + frId + ' — document every association.\\n'\n"
        + "      + '2. MIRROR:     `' + PY + ' ' + REPO + '/harness_cli.py check-test-mirrors-spec --fr-id ' + frId + ' --test-file tests/test_fr' + frNum + '.py --project ' + REPO + '`\\n'\n"
        + "      + '   On MIRROR FAIL: fix the TEST to match TEST_SPEC.md — do NOT edit TEST_SPEC.md (correctness was locked in Phase 2; P3 only implements). Re-run.\\n'\n"
        + "      + '3. TDD-GREEN:  `' + PY + ' ' + REPO + '/harness_cli.py run-fr-step --phase 3 --fr-id ' + frId + ' --step TDD-GREEN --project ' + REPO + ' --srs 01-requirements/SRS.md`\\n'\n"
        + "      + '4. TDD-IMPROVE:`' + PY + ' ' + REPO + '/harness_cli.py run-fr-step --phase 3 --fr-id ' + frId + ' --step TDD-IMPROVE --project ' + REPO + '`\\n'\n"
        + "      + '5. amend-sab (proactive, BEFORE GATE1): `' + PY + ' ' + REPO + '/harness_cli.py amend-sab --project ' + REPO + '` (idempotent, scans 03-development/src/). If it registers any new modules: `git -C ' + REPO + ' add .methodology/SAB.json && git -C ' + REPO + ' commit -m \"amend: register SAB modules (' + frId + ')\"`. This FR\\'s GREEN/IMPROVE steps may have added modules GATE1\\'s Architecture Amendment Protocol would otherwise BLOCK on — registering them now avoids a wasted GATE1 round.\\n'\n"
        + "      + '6. GATE1 — long-running (harness runs up to 3 internal CODE-FIX rounds, each up to ~600s: can silently block ~2400s worst case). Run it BACKGROUNDED — do NOT invoke it as a plain synchronous command:\\n'\n"
        + "      + '   GATE1 invocation procedure (a/b/c):\\n'\n"
        + "      + '   a. Launch: `nohup ' + PY + ' ' + REPO + '/harness_cli.py run-fr-step --phase 3 --fr-id ' + frId + ' --step GATE1 --project ' + REPO + ' > /tmp/gate1_' + frId + '.log 2>&1 & echo $!` — note the printed PID.\\n'\n"
        + "      + '   b. Poll: every 30s run `kill -0 <PID> 2>/dev/null && echo RUNNING || echo DONE`. Repeat until DONE (cap 40 polls / ~20min, comfortably above the ~2400s worst case). Still RUNNING past the cap → report \"' + frId + ' GATE1: TIMEOUT\" (not FAIL) and stop — do not kill the PID.\\n'\n"
        + "      + '   c. Once DONE: `cat /tmp/gate1_' + frId + '.log` for the full output — identical to what a synchronous run would have printed. Parse PASS/FAIL from it exactly as before.\\n'\n"
        + "      + '   Gate 1 thresholds: linting(90) type_safety(85) test_coverage(80).\\n'\n"
        + "      + '   - PASS → done.\\n'\n"
        + "      + '   - FAIL → fix failing dims (ruff check . --fix; add tests for coverage; fix pyright errors), repeat the GATE1 invocation procedure (a/b/c). Max 3 rounds.\\n'\n"
        + "      + '   - Still failing after 3 → report FAIL.\\n'\n"
        + "      + '   - Structurally-broken dispatch [FATAL]: if the log contains \"[FATAL]\" (e.g. \"claude.ai connectors are disabled\" or any other structurally-broken-dispatch signature), STOP IMMEDIATELY — do NOT unset/modify any environment variables yourself, do NOT retry the GATE1 invocation procedure. The message\\'s suggested fix is for a human operator to run OUTSIDE this session, not something you can act on. Report \"' + frId + ' GATE1: FAIL — structurally broken dispatch environment, escalate to human (see [FATAL] message)\" and stop this FR\\'s TDD chain.\\n'\n"
        + "      + '   - Architecture Amendment Protocol [BLOCKED]: if the log contains \"Unregistered modules detected: {…}\", DO NOT hand-edit SAB.json by hand. Run `' + PY + ' ' + REPO + '/harness_cli.py amend-sab --project ' + REPO + '` to register the new modules (idempotent, scans 03-development/src/), `git -C ' + REPO + ' add .methodology/SAB.json && git -C ' + REPO + ' commit -m \"amend: register SAB modules (' + frId + ')\"`, then repeat the GATE1 invocation procedure (a/b/c). Max 1 amend round per FR.\\n'\n"
        + "      + '   run-fr-step auto-pushes on completion (idempotent). Crash recovery: `resume-fr-phase --phase 3 --project ' + REPO + '`.\\n'\n"
        + "      + '7. ORCH-POST (after GATE1 PASS, per phase3_plan.md [ORCH-POST]):\\n'\n"
        + "      + '   a. `' + PY + ' ' + REPO + '/harness_cli.py spec-coverage-check --project ' + REPO + ' --threshold 40.0 --fr-id ' + frId + '` (per-FR D4 ≥40%). FAIL → add the missing test implementations for ' + frId + ', re-run.\\n'\n"
        + "      + '   b. SAB.json is kept in sync by amend-sab (step 5 above). Do NOT run generate_sab.py --overwrite here.\\n'\n"
        + "      + '      (generate_sab.py --overwrite rebuilds SAB purely from SAD.md §5, which was locked in Phase 2\\n'\n"
        + "      + '       and may not reflect modules added during Phase 3 implementation. Only run generate_sab.py --overwrite\\n'\n"
        + "      + '       manually AFTER updating SAD.md §5 to include all Phase 3 modules.)\\n\\n'\n"
        + "      + 'Implement the module per SPEC.md (read ' + REPO + '/SPEC.md for ' + frId + ') + SAD.md module mapping. Write source under `03-development/src/<module>/` (the harness-scaffolded canonical layout — see `init-project`\\'s directory scaffold). Tests under `tests/` per project layout (MIRROR step above already validated them at that path). All tests for ' + frId + ' MUST live in the single canonical file `tests/test_fr' + frNum + '.py` — this is the only filename the harness coverage/RED-check/GATE1-DELTA diff tooling recognizes. Do not create satellite files like `test_fr' + frNum + '_unit.py`; add more test functions to the one file instead. Docstrings must include [' + frId + '] reference (NFR-05).\\n\\n'\n"
        + "      + 'Report final line: \"' + frId + ' GATE1: PASS\" or \"' + frId + ' GATE1: FAIL — <reason>\".\\n\\n'\n"
        + "      + 'SCOPE RULES:\\n- DO NOT implement any FR OTHER than ' + frId + '.\\n- DO NOT run run-gate (Gate 2), advance-phase, or push-milestone.\\n- DO NOT edit .methodology/quality_manifest.json or .sessi-work/gate1_result.json to fake/reset scores — fix the underlying code/tests instead.\\n- DO NOT modify harness/ (HR-17).\\n- ONLY the 7 steps above for ' + frId + ' (amend-sab in step 5, spec-coverage-check in step 7a is allowed).',\n"
        + "      { label: 'tdd-' + frId, phase: 'Per-FR TDD', agentType: 'general-purpose' },\n"
        + "    )\n"
        + "    // L1: distinguish a session/rate-limit block (null/empty agent return) from a real\n"
        + "    // Gate 1 FAIL — mirror the Gate 2 detection (below). Without this, a rate-limit mid-\n"
        + "    // TDD is misreported as a code-quality Gate 1 failure. Sentinel GUARD skips completed\n"
        + "    // FRs on resume, so aborting here is safe.\n"
        + "    if (frReport === null || frReport === undefined || (typeof frReport === 'string' && frReport.length < 10)) {\n"
        + "      log('  ' + frId + ' agent blocked (session limit / rate limit) — aborting, resume after quota reset')\n"
        + "      return { session_limit_blocked: true, phase: 3, fr_id: frId, gate1Pass, message: 'Agent hit session/rate limit during ' + frId + ' TDD. Resume after quota reset — sentinel GUARD will skip completed FRs.' }\n"
        + "    }\n"
        + "    // L1.5: detect a structurally-broken dispatch [FATAL] surfaced via the sub-agent\n"
        + "    // (harness/cli/fr_cmds.py:_abort_dispatch_structurally_broken prints \"[FATAL] <fr> <step>:\n"
        + "    // sub-agent dispatch is structurally broken — claude.ai connectors are disabled\" to\n"
        + "    // stderr and returns exit code 23). A sub-agent reading its own GATE1 log and seeing\n"
        + "    // that banner will escalate to human with \"FAIL — structurally broken dispatch\" even\n"
        + "    // when the gate has not yet run a single evaluation round. The harness-side\n"
        + "    // _is_connector_disabled_failure guard already catches this AT the fr_cmds.py layer\n"
        + "    // for LINT-FIX / COVERAGE-FIX / GATE1-final-dispatch, but TDD-RED/GREEN/IMPROVE\n"
        + "    // dispatches AND the GATE1 first-round prompt path do NOT have it. Continuing to\n"
        + "    // dispatch FR-02..FR-05 in that state burns ~5min and ~50K tokens per FR on\n"
        + "    // identically-broken dispatches. Abort once the structural signal is observed.\n"
        + "    // FIX-N: workflow JS L1 [FATAL] detection — abort loop on connector-disabled signature.\n"
        + "    const frReportText = (typeof frReport === 'string') ? frReport : JSON.stringify(frReport)\n"
        + "    if (/structurally broken dispatch environment/i.test(frReportText) || /\\[FATAL\\][^\\n]*dispatch is structurally broken/i.test(frReportText)) {\n"
        + "      log('  ' + frId + ' reports [FATAL] structurally broken dispatch (claude.ai connectors disabled) — aborting remaining FRs')\n"
        + "      return { dispatch_structurally_broken: true, phase: 3, fr_id: frId, gate1Pass, gate1Fail: [...gate1Fail, frId], message: frId + ' GATE1: dispatch is structurally broken (env: ANTHROPIC_API_KEY overrides claude.ai login). Human must unset ANTHROPIC_API_KEY/ANTHROPIC_AUTH_TOKEN/ANTHROPIC_BASE_URL/ANTHROPIC_DEFAULT_HAIKU_MODEL in the shell that launches this process, then re-run via Workflow({scriptPath, resumeFromRunId}).' }\n"
        + "    }\n"
        + "    // AUTHORITATIVE Gate 1 verdict: read the harness quality_manifest (bridge writes\n"
        + "    // gate_results.gate1[fr].quality_complete on every finalize-gate, pass OR fail) —\n"
        + "    // NOT the sub-agent's self-reported \"GATE1: PASS\" string. A sub-agent can report\n"
        + "    // PASS from its own gate1_result.json overall score even when finalize-gate raised\n"
        + "    // GateBlockedError (e.g. spec-coverage short, or a dimension below threshold), which\n"
        + "    // silently advances a FR that the harness actually blocked. Verify against the\n"
        + "    // harness's own record so a blocked gate is never counted as passed.\n"
        + "    const verifyCmd = PY + ' -c \"import json; g=(json.load(open(\\'' + REPO + '/.methodology/quality_manifest.json\\')).get(\\'gate_results\\',{}) or {}).get(\\'gate1\\',{}).get(\\'' + frId + '\\',{}) or {}; print(\\'GATE1_VERIFIED_PASS\\' if g.get(\\'quality_complete\\') is True else \\'GATE1_VERIFIED_FAIL score=\\'+str(g.get(\\'score\\')))\"'\n"
        + "    const verdict = await agent(\n"
        + "      'Run EXACTLY this command via the Bash tool:\\n`' + verifyCmd + '`\\n'\n"
        + "      + 'Then report via the StructuredOutput tool: pass = true ONLY if stdout is GATE1_VERIFIED_PASS; reason = the verbatim stdout.',\n"
        + "      { label: 'gate1-verify-' + frId, phase: 'Per-FR TDD', agentType: 'general-purpose', schema: VERDICT_SCHEMA },\n"
        + "    )\n"
        + "    const passed = !!(verdict && verdict.pass === true)\n"
        + "    if (passed) { gate1Pass.push(frId); log('  ' + frId + ' Gate 1 PASS (' + gate1Pass.length + '/' + frIds.length + ') [harness-verified]') }\n"
        + "    else { gate1Fail.push(frId); log('  ' + frId + ' Gate 1 FAIL [harness manifest qc != true; sub-agent self-report ignored]') }\n"
        + "  }\n"
        + "\n"
        + "  // PUSH ③ p3-mid — fire once when ≥1/3 FRs have Gate 1 PASS (but not yet all done).\n"
        + "  if (!p3MidPushed && gate1Pass.length >= p3MidThreshold && gate1Pass.length < frIds.length) {\n"
        + "    p3MidPushed = true\n"
        + "    log('  ≥1/3 FRs Gate 1 PASS (' + gate1Pass.length + '/' + frIds.length + ') — pushing p3-mid milestone')\n"
        + "    await agent(\n"
        + "      'YOU ARE THE P3 MID-MILESTONE PUSHER (≥1/3 FRs Gate 1 PASS).\\n'\n"
        + "      + 'REPO: ' + REPO + '\\nPYTHON: ' + PY + '\\n\\n'\n"
        + "      + '0. GUARD: `git -C ' + REPO + ' log --oneline --grep=\"P3-mid)\" -1`. If a p3-mid commit already exists, report \"MILESTONE: PASS (already pushed)\" and stop — do NOT push again.\\n'\n"
        + "      + '1. Command: `' + PY + ' ' + REPO + '/harness_cli.py push-milestone --type p3-mid --project ' + REPO\n"
        + "      + ' --fr-done ' + gate1Pass.length + ' --fr-total ' + frIds.length + ' --fr-ids ' + gate1Pass.join(',') + '`\\n'\n"
        + "      + '   Writes HANDOVER.md + commits + pushes. If a hook blocks, reword commit to start with `chore(harness):` (NOT --no-verify), retry.\\n\\n'\n"
        + "      + 'Report: \"MILESTONE: PASS|FAIL — <details>\".\\n\\n'\n"
        + "      + 'SCOPE RULES:\\n- DO NOT run run-gate / advance-phase / implement FRs.\\n- ONLY push-milestone p3-mid.',\n"
        + "      { label: 'milestone-p3-mid', phase: 'Per-FR TDD', agentType: 'general-purpose' },\n"
        + "    )\n"
        + "  }\n"
        + "}\n"
        + "if (gate1Fail.length) {\n"
        + "  return { error: 'Phase 3: Gate 1 FAILED for FR(s): ' + gate1Fail.join(', ') + ' (escalate — fix code/tests, resume-fr-phase)', gate1Pass, gate1Fail }\n"
        + "}\n"
    )


def _render_phase3_milestones() -> str:
    return (
        B.render_phase_header("Milestones")
        + "log('All ' + frIds.length + ' FRs Gate 1 PASS — push p3-pre-gate2 (last stable snapshot before Gate 2)')\n"
        + "const preGate2Report = await agent(\n"
        + "  'YOU ARE THE P3 MILESTONE PUSHER. Push the pre-Gate-2 milestone.\\n'\n"
        + "  + 'REPO: ' + REPO + '\\nPYTHON: ' + PY + '\\n\\n'\n"
        + "  + '0. GUARD: `git -C ' + REPO + ' log --oneline --grep=\"P3-pre-gate2)\" -1`. If a p3-pre-gate2 commit already exists, report \"MILESTONE: PASS (already pushed)\" and stop.\\n'\n"
        + "  + '1. Command: `' + PY + ' ' + REPO + '/harness_cli.py push-milestone --type p3-pre-gate2 --project ' + REPO + ' --fr-ids ' + gate1Pass.join(',') + '`\\n'\n"
        + "  + '   Writes HANDOVER.md + commits + pushes. If a hook blocks, reword commit to start with `chore(harness):` (NOT --no-verify), retry.\\n\\n'\n"
        + "  + 'Verdict: report via the StructuredOutput tool — pass=true if the milestone commit exists or was pushed; reason = one-line detail.\\n\\n'\n"
        + "  + 'SCOPE RULES:\\n- DO NOT run run-gate or advance-phase.\\n- ONLY push-milestone p3-pre-gate2.',\n"
        + "  { label: 'milestone-pre-gate2', phase: 'Milestones', agentType: 'general-purpose', schema: VERDICT_SCHEMA },\n"
        + ")\n"
        + "if (!(preGate2Report && preGate2Report.pass === true)) {\n"
        + "  log('  WARNING: p3-pre-gate2 milestone push did not confirm PASS — continuing to Gate 2 (milestone is a snapshot, not a hard gate)')\n"
        + "}\n"
    )


def _render_phase3_sync() -> str:
    """phase3's Sync is the most elaborate of any migrated phase: a retry-once
    (covers transient network/auth blips, not a real pre-push gate block)
    followed by a HANDOVER.md-append fallback (never --no-verify without a
    human decision — HR-17) if both attempts fail. Every other migrated
    phase's Sync is much simpler (render_sync / render_sync_verified);
    phase3 accumulated this extra resilience first (its own comments are the
    origin of the "ported from phase3" references throughout the other
    files), so it stays its own renderer rather than forcing the others to
    match its complexity."""
    return (
        "// Bug A fix (2026-07-07): advance-phase intentionally commits the handover\n"
        "// locally without pushing (harness/cli/phase_cmds.py: \"next milestone push\n"
        "// publishes to origin\"). This workflow ends right after Advance with no\n"
        "// next-phase push queued, so the handover commit was left stranded on\n"
        "// local until whatever runs next happened to push it. Publish it now.\n"
        "phase('Sync')\n"
        "log('git push origin main (publish advance handover commit)')\n"
        "const SYNC_PROMPT = 'Run EXACTLY this command via Bash:\\n'\n"
        "  + 'git -C ' + REPO + ' push origin main\\n\\n'\n"
        "  + 'Report final outcome as plain text: \"SYNC: PASS\" or \"SYNC: FAIL — <one-line reason>\"'\n"
        "  + ' (if a pre-push hook printed a blocker list, include it verbatim).'\n"
        "let syncReport = await agent(SYNC_PROMPT, { label: 'sync', phase: 'Sync', agentType: 'general-purpose' })\n"
        "let syncPass = /SYNC:\\s*PASS/.test(String(syncReport ?? ''))\n"
        "if (!syncPass) {\n"
        "  // One retry only — covers transient failures (DNS/auth-token blips), not\n"
        "  // a real pre-push gate block, which is deterministic and won't clear on\n"
        "  // its own.\n"
        "  log('  Sync FAIL on first attempt — retrying once (covers transient network failures)')\n"
        "  syncReport = await agent(SYNC_PROMPT, { label: 'sync-retry', phase: 'Sync', agentType: 'general-purpose' })\n"
        "  syncPass = /SYNC:\\s*PASS/.test(String(syncReport ?? ''))\n"
        "}\n"
        "\n"
        "if (!syncPass) {\n"
        "  // Do NOT auto `--no-verify` (HR-17 forbids bypassing the gate without a\n"
        "  // human decision). Surface the blocker instead of terminating with a bare\n"
        "  // error: state.json current_phase is already authoritative for Phase 4\n"
        "  // (Advance PASS'd above), the handover commit just hasn't reached origin\n"
        "  // yet — a human resolves the printed blocker(s) and pushes manually.\n"
        "  const blockers = String(syncReport ?? '').slice(-600)\n"
        "  await agent(\n"
        "    'Append this section to the END of ' + REPO + '/HANDOVER.md (append — do not overwrite '\n"
        "    + 'existing content; create the file only if it truly does not exist):\\n\\n'\n"
        "    + '## Sync Blocked — manual push required\\n\\n'\n"
        "    + 'The Phase 3 advance handover commit landed locally but `git push origin main` '\n"
        "    + 'did not pass the pre-push hook:\\n\\n'\n"
        "    + '```\\n' + blockers + '\\n```\\n\\n'\n"
        "    + 'Resolve the blocker(s) above, then run `git push origin main` manually. '\n"
        "    + 'Do NOT use `--no-verify` without explicit human sign-off.\\n',\n"
        "    { label: 'sync-handover-note', phase: 'Sync', agentType: 'general-purpose' },\n"
        "  )\n"
        "  log('Phase 3 workflow ends with Sync unresolved — see HANDOVER.md \"Sync Blocked\" section.')\n"
        "  return {\n"
        "    phase: 3,\n"
        "    fr_count: frIds.length,\n"
        "    gate1_pass: gate1Pass,\n"
        "    gate2_status: gate2Pass ? 'PASS' : 'unknown',\n"
        "    advance_status: 'PASS',\n"
        "    sync_status: 'MANUAL_REQUIRED',\n"
        "    blockers,\n"
        "    artifacts: ['03-development/src/', 'tests/', '.methodology/gate2_result.json', 'HANDOVER.md'],\n"
        "    notes: 'Phase 3 complete (Advance PASS) but the handover commit could not be auto-pushed — see HANDOVER.md \"Sync Blocked\" section for the pre-push blocker list.',\n"
        "  }\n"
        "}\n"
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


_GATE3_STEPS = [
    "0. TRACE-PRECHECK: `' + PY + ' ' + REPO + '/harness_cli.py build-trace-attestation --project ' + REPO + ' --write 2>&1 | tail -4`. If output contains \"wrote canonical\", commit immediately: `git -C ' + REPO + ' add .methodology/trace/attestation.json && git -C ' + REPO + ' commit -m \"trace: regen attestation before Gate 3\"`. Prevents trace_dirt from blocking finalize-gate.",
    "1. G3a: `' + PY + ' ' + REPO + '/harness_cli.py run-gate --gate 3 --phase 4 --project ' + REPO + '` (CRG recon runs inside automatically). Read the printed evaluation prompt.",
    (
        "2. G3b: Evaluate ALL Gate 3 dimensions inline per ' + REPO + '/harness/harness/ssi/prompts/evaluate_dimension.md. Write ' + REPO + '/.sessi-work/gate3_result.json.\\n"
        "   15 dims: linting(90) type_safety(85) test_coverage(80) security(80) secrets_scanning(100) license_compliance(100) integration_coverage(60) architecture(80) readability(80) error_handling(80) documentation(75) test_assertion_quality(60) performance(75).\\n"
        "   NOTE: mutation_testing is disabled by default via .methodology/harness_config.json (mutation_testing=false). If enabled, the harness auto-includes it and re-normalises the composite score.\\n"
        "   FRAMEWORK-OWNED (do NOT self-score): traceability + architecture (harness CRG override) + adversarial_review (from bug_hunt_report.json).\\n"
        "   For any failing dim: fix ROOT CAUSE in code (ruff/pyright/tests/bandit/readability_v2/ast-error-handling/pytest-benchmark), re-run the tool, update score. (readability tool is `python3 -m harness.toolchains.readability_v2` — NOT `radon mi` — per phase3/4/6_plan.md v2.12.0.) If architecture=0 due to Orchestrator/hub-and-spoke: complete DA challenge + set da_waiver."
    ),
    (
        "3. G3c: `' + PY + ' ' + REPO + '/harness_cli.py finalize-gate --gate 3 --phase 4 --project ' + REPO + '`.\\n"
        "   - If blocked by traceability: `build-trace-attestation --project ' + REPO + ' --write` + commit attestation.json, re-run finalize."
    ),
    "4. D4: `' + PY + ' ' + REPO + '/harness_cli.py spec-coverage-check --project ' + REPO + ' --threshold 80.0`. FAIL → add missing tests, re-run.",
]

_GATE3_SCOPE_RULES = (
    "- DO NOT run advance-phase.\\n"
    "- DO NOT edit gate3_result.json to fake scores — fix the code.\\n"
    "- DO NOT modify harness/ (HR-17).\\n"
    "- ONLY run-gate/eval/finalize/spec-coverage + code fixes."
)

_GATE3_DEFERRED_FIXES_STEP = (
    "  log('  Gate 3 exhausted 3 rounds — generating deferred_fixes.md')\n"
    "  const gate3StateCmd = PY + ' -c \"import json; g=(json.load(open(\\'' + REPO + '/.methodology/quality_manifest.json\\')).get(\\'gate_results\\',{}) or {}).get(\\'gate3\\') or {}; print(json.dumps({\\'score\\': g.get(\\'score\\'), \\'qc\\': g.get(\\'quality_complete\\'), \\'dims\\': g.get(\\'dimensions\\',{})}))\"'\n"
    "  await agent(\n"
    "    'YOU ARE THE DEFERRED-FIX RECORDER. Gate 3 failed to reach PASS in 3 rounds.\\n'\n"
    "    + 'REPO: ' + REPO + '\\nPYTHON: ' + PY + '\\n\\n'\n"
    "    + '1. Get the last-known Gate 3 state:\\n`' + gate3StateCmd + '`\\n'\n"
    "    + '2. Run `' + PY + ' ' + REPO + '/harness_cli.py spec-coverage-check --project ' + REPO + ' --threshold 80.0; echo \"RC=$?\"` for the D4 status.\\n'\n"
    "    + '3. Write `' + REPO + '/.methodology/deferred_fixes.md` with:\\n'\n"
    "    + '   - A brief header: \"Gate 3 — deferred fixes\" + date + last-known composite score\\n'\n"
    "    + '   - Each failing dimension (score below its threshold) as a `- [ ]` checkbox item\\n'\n"
    "    + '   - D4 as a `- [ ]` checkbox item (spec-coverage < 80%)\\n'\n"
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
                "bug hunt + Gate 3 (15 dims) exit (phase4_plan.md v2.12.0)"
            ),
            phases=_META_PHASES_4,
        ),
        "",
        B.RESOLVE_REPO_BLOCK,
        B.HUNT_MODEL_BLOCK,
        "",
        B.WRITE_SCOPE_BLOCK,
        "",
        B.render_schemas(["VERDICT_SCHEMA", "RC_SCHEMA", "CTX_SCHEMA", "DELTA_FAST_SCHEMA", "GATE_VERIFY_SCHEMA", "PHASE_SCHEMA"]),
        B.render_entry_preflight(
            phase=4, gate_num=2, gate_owner_phase=3, prev_phase=3,
            extra_note=(
                "- DO NOT generate TEST_PLAN / run TDD / run-gate / bug hunt.\\n"
                "- DO NOT run advance-phase/push-milestone.\\n"
            ),
        ),
        _render_test_plan(),
        B.render_env_check(phase=4),
        B.render_manifest_integrity_phase(phase=4),
        B.render_load_frs(phase=4, include_fr_titles=True),
        B.render_per_fr_delta(
            phase=4,
            forbidden_note="- DO NOT run run-gate / bug-hunt / advance-phase / push-milestone.\\n",
            verifier_role="TEST VERIFIER",
            use_fr_titles=True,
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
            log_msg="Gate 3 exit (composite ≥80, 15 dims: 12 self-scored + traceability/architecture/adversarial_review framework-owned)",
            prompt_steps=_GATE3_STEPS,
            pass_line_desc="composite ≥80 AND all dims ≥ threshold AND D4 ≥80%",
            scope_rules=_GATE3_SCOPE_RULES,
            d4_threshold=80.0,
            on_fail_error_msg="Gate 3 did not PASS in 3 rounds (HR-08); deferred_fixes.md written to .methodology/ (advance-phase exit 17 until resolved)",
            include_manifest_integrity=False,
            deferred_fixes_step=_GATE3_DEFERRED_FIXES_STEP,
        ),
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


_GATE2_STEPS = [
    "0. TRACE-PRECHECK: `' + PY + ' ' + REPO + '/harness_cli.py build-trace-attestation --project ' + REPO + ' --write 2>&1 | tail -4`. If output contains \"wrote canonical\", commit immediately: `git -C ' + REPO + ' add .methodology/trace/attestation.json && git -C ' + REPO + ' commit -m \"trace: regen attestation before Gate 2\"`. Prevents trace_dirt from blocking finalize-gate.",
    "1. G2a: `' + PY + ' ' + REPO + '/harness_cli.py run-gate --gate 2 --phase 3 --project ' + REPO + '` — read the printed evaluation prompt.",
    (
        "2. G2b: Evaluate ALL Gate 2 dimensions inline per ' + REPO + '/harness/harness/ssi/prompts/evaluate_dimension.md. Write ' + REPO + '/.sessi-work/gate2_result.json.\\n"
        "   Dims: use the exact `dimensions:` list G2a just printed (it is computed from gate2_p3_exit.yaml, filtered by enabled feature flags — always current, do NOT hand-copy a dim list here).\\n"
        "   NOTE: mutation_testing is disabled by default via .methodology/harness_config.json (mutation_testing=false). If enabled, the harness auto-includes it and re-normalises the composite score.\\n"
        "   NOTE: traceability is FRAMEWORK-OWNED — do NOT score it; the harness injects it in finalize-gate.\\n"
        "   For any failing dim: fix the ROOT CAUSE in code (ruff/pyright/add tests/bandit/mutation), re-run the tool, update the score. (No auto-fix engine.)"
    ),
    (
        "3. G2c — run BACKGROUNDED (finalize-gate\\'s own git push triggers the local pre-push hook, plus CRG refresh: bounded on this project today, but a single opaque Bash call with no visible output until it returns is exactly the shape the 180s stall watchdog kills — same class of risk as GATE1, same fix):\\n"
        "   a. Launch: `nohup ' + PY + ' ' + REPO + '/harness_cli.py finalize-gate --gate 2 --phase 3 --project ' + REPO + ' > /tmp/gate2_finalize_r' + round + '.log 2>&1 & echo $!` — note the printed PID.\\n"
        "   b. Poll: every 15s run `kill -0 <PID> 2>/dev/null && echo RUNNING || echo DONE`. Repeat until DONE (cap 40 polls / ~10min). Still RUNNING past the cap → report \"GATE2: TIMEOUT\" and stop — do not kill the PID.\\n"
        "   c. Once DONE: `cat /tmp/gate2_finalize_r' + round + '.log` for the full output — identical to what a synchronous run would have printed.\\n"
        "   - If blocked by traceability: `' + PY + ' ' + REPO + '/harness_cli.py build-trace-attestation --project ' + REPO + ' --write` then `git -C ' + REPO + ' add .methodology/trace/attestation.json && git -C ' + REPO + ' commit -m \"trace: regen attestation\"`, re-run the G2c backgrounded procedure (a/b/c)."
    ),
    "4. D4: `' + PY + ' ' + REPO + '/harness_cli.py spec-coverage-check --project ' + REPO + ' --threshold 60.0`. FAIL → add missing test implementations, re-run.",
]

_GATE2_SCOPE_RULES = (
    "- DO NOT run advance-phase or push-milestone p3-post-gate2 (next phase does that).\\n"
    "- DO NOT edit .sessi-work/gate2_result.json to fake scores — fix the code.\\n"
    "- DO NOT modify harness/ (HR-17).\\n"
    "- ONLY run-gate/eval/finalize/spec-coverage + code fixes."
)

_PHASE3_ADVANCE_STEP_OVERRIDE = (
    "advance-phase — run BACKGROUNDED (internally runs `ruff check .` + `mypy .` + `pytest --cov-fail-under=100` over the WHOLE project as sequential subprocess calls inside one opaque Bash call; harmless today at this project\\'s size (~25s measured) but this cost only grows as more FRs/tests land, and a single opaque long Bash call is exactly what the 180s stall watchdog kills — same class of risk as GATE1, same fix):\\n"
    "   a. Launch: `nohup ' + PY + ' ' + REPO + '/harness_cli.py advance-phase --completed 3 --project ' + REPO + ' > /tmp/advance_r' + round + '.log 2>&1 & echo $!` — note the printed PID.\\n"
    "   b. Poll: every 15s run `kill -0 <PID> 2>/dev/null && echo RUNNING || echo DONE`. Repeat until DONE (cap 40 polls / ~10min). Still RUNNING past the cap → report \"ADVANCE: TIMEOUT\" and stop — do not kill the PID.\\n"
    "   c. Once DONE: `cat /tmp/advance_r' + round + '.log` for the full output — identical to what a synchronous run would have printed.\\n"
    "   advance-phase independently re-verifies EVERYTHING before it will advance (lint, types, coverage, document quality, reliability lint, architecture drift, Phase Truth, and more) — its own output tells you exactly what is missing. If it prints \"[BLOCKED] ...\", that message IS the fix instruction: read it verbatim and do exactly what it says (it often includes the precise command to run), then repeat the advance-phase backgrounded procedure (a/b/c). Do NOT guess what might be wrong — trust only what advance-phase itself reports.\\n"
    "   advance-phase is safe to re-run: it re-checks and re-reports without side effects until every check passes, so iterate within this round as many times as needed."
)


def generate_phase3() -> str:
    parts = [
        _HEADER_3,
        "",
        _render_meta(
            name="phase3-implementation",
            description=(
                "Phase 3 Implementation — per-FR TDD (RED/GREEN/IMPROVE/GATE1) "
                "+ milestones + Gate 2 exit (phase3_plan.md v2.12.0)"
            ),
            phases=_META_PHASES_3,
        ),
        "",
        B.RESOLVE_REPO_BLOCK,
        "",
        B.WRITE_SCOPE_BLOCK,
        "",
        B.render_schemas(["VERDICT_SCHEMA", "RC_SCHEMA", "CTX_SCHEMA_WITH_TITLES", "FR_LIST_SCHEMA", "GATE_VERIFY_SCHEMA", "PHASE_SCHEMA"]),
        _render_phase3_entry_preflight(),
        B.render_env_check(phase=3),
        B.render_manifest_integrity_phase(phase=3),
        _render_phase3_load_frs(),
        _render_per_fr_tdd(),
        _render_phase3_milestones(),
        B.render_gate_loop(
            gate_num=2, phase=3,
            log_msg="Gate 2 exit (composite ≥75, 9 dims: 8 self-scored + traceability framework-owned)",
            prompt_steps=_GATE2_STEPS,
            pass_line_desc="composite ≥75 AND all dims ≥ threshold AND D4 ≥60%",
            scope_rules=_GATE2_SCOPE_RULES,
            d4_threshold=60.0,
            on_fail_error_msg="Gate 2 did not PASS in 3 rounds (HR-08; write deferred_fixes.md + escalate to human)",
            include_manifest_integrity=True,
        ),
        B.render_advance_loop(
            phase=3, next_phase=4,
            precheck_steps=[
                (
                    "GUARD + PUSH ⑤ p3-post-gate2: `git -C ' + REPO + ' log --oneline --grep=\"P3-post-gate2)\" -1`. If a commit exists, skip the push. Else: `' + PY + ' ' + REPO + '/harness_cli.py push-milestone --type p3-post-gate2 --project ' + REPO + ' --fr-ids ' + gate1Pass.join(',') + '`\\n"
                    "   Pre-flight (enforced): gate2_result.json composite ≥75 + per-FR Gate 1 sentinel .sessi-work/sentinels/g1_p3_<fr>.flag exists for every FR. If BLOCKED, read the error list and fix."
                ),
            ],
            advance_step_override=_PHASE3_ADVANCE_STEP_OVERRIDE,
            scope_extra="- DO NOT re-implement FRs.\\n",
            only_extra="push-milestone p3-post-gate2 + ",
            log_msg="p3-post-gate2 milestone + advance-phase --completed 3 (TDD-PRECHECK enforced)",
        ),
        _render_phase3_sync(),
        (
            "\nlog('Phase 3 workflow complete. Open .methodology/phase4_plan.md to continue.')\n"
            "return {\n"
            "  phase: 3,\n"
            "  fr_count: frIds.length,\n"
            "  gate1_pass: gate1Pass,\n"
            "  gate2_status: gate2Pass ? 'PASS' : 'unknown',\n"
            "  advance_status: 'PASS',\n"
            "  sync_status: 'PASS',\n"
            "  artifacts: ['03-development/src/', 'tests/', '.methodology/gate2_result.json', 'HANDOVER.md'],\n"
            "  notes: 'Phase 3 complete per phase3_plan.md v2.12.0. All FRs Gate 1 PASS + Gate 2 PASS. Phase 4 (Testing) ready.',\n"
            "}\n"
        ),
    ]
    return "\n".join(p for p in parts if p is not None)
