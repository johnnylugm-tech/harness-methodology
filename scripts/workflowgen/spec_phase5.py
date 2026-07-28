"""Phase 5 (Verification & Delivery) workflow assembly — Round 15 station2
extraction from the former monolithic phase_specs.py. See
scripts/workflowgen/spec_shared.py for the cross-phase _render_meta.
"""
from __future__ import annotations

from . import js_blocks as B
from .spec_shared import _render_meta

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

_META_PHASES_5 = [
    "Entry & Preflight", "Env Check", "Load FRs",
    "Per-FR Delta", "Verification Docs", "Artifacts Commit", "Milestone",
    "Advance", "Sync",
]


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
        B.RESOLVE_REPO_BLOCK + B.REPO_LOG_LINE + B.BUDGET_GUARD_BLOCK,
        "",
        B.WRITE_SCOPE_BLOCK,
        "",
        B.render_schemas(["VERDICT_SCHEMA", "RC_SCHEMA", "ENV_CHECK_SCHEMA", "CTX_SCHEMA", "DELTA_FAST_SCHEMA", "PHASE_SCHEMA"]),
        B.render_entry_preflight(
            phase=5, gate_num=3, gate_owner_phase=4, prev_phase=4,
            extra_note=(
                "- DO NOT generate BASELINE/VERIFICATION docs or run TDD steps.\\n"
                "- DO NOT run advance-phase/push-milestone.\\n"
            ),
        ),
        B.render_env_check(phase=5),
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
