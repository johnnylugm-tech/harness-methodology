"""Phase 7 (Risk Management) workflow assembly — Round 15 station2
extraction from the former monolithic phase_specs.py. See
scripts/workflowgen/spec_shared.py for the cross-phase _render_meta.
"""
from __future__ import annotations

from . import js_blocks as B
from . import spec_shared as S
from .spec_shared import _render_meta

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

_META_PHASES_7 = [
    "Entry & Preflight", "Env Check", "Load FRs",
    "Per-FR Delta", "Risk Docs", "Artifacts Commit", "Milestone",
    "Preview Next-Phase", "Advance", "Sync",
]


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
        + "  return halt('risk-docs', { error: 'Phase 7 risk docs did not PASS', reason: docsReport ? String(docsReport.reason ?? '').slice(-500) : 'agent returned null' })\n"
        + "}\n"
    )


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
        B.RESOLVE_REPO_BLOCK + B.REPO_LOG_LINE + B.BUDGET_GUARD_BLOCK,
        "",
        B.WRITE_SCOPE_BLOCK,
        "",
        B.render_schemas(["VERDICT_SCHEMA", "RC_SCHEMA", "ENV_CHECK_SCHEMA", "CTX_SCHEMA", "DELTA_FAST_SCHEMA", "PHASE_SCHEMA"]),
        B.render_entry_preflight(
            phase=7, gate_num=4, gate_owner_phase=6, prev_phase=6,
            extra_note=(
                "- DO NOT generate risk docs or run TDD steps.\\n"
                "- DO NOT run advance-phase/push-milestone.\\n"
            ),
        ),
        B.render_env_check(phase=7),
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
        B.render_preview_next_phase(7),
        B.render_advance_loop(
            phase=7, next_phase=8,
            scope_extra="- DO NOT re-do P7 docs.\\n",
            log_msg="advance-phase --completed 7 (TDD-PRECHECK + D4 90% enforced)",
        ),
        B.render_sync_verified(),
        (
            "\nlog('Phase 7 workflow complete. Open .methodology/phase8_plan.md to continue.')\n"
            "return {\n"
            + S.render_phase_complete_marker()
            +
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
