#!/usr/bin/env python3
"""
Generate Full Plan with Phase-Specific Detailed Tasks

This script parses previous phase artifacts to generate detailed tasks
for each phase in the harness-methodology framework.

Phase Artifacts Mapping:
- Phase 1: (no previous artifacts)
- Phase 2: SRS.md -> Architecture requirements
- Phase 3: SRS.md + SAD.md -> Implementation tasks
- Phase 4: SRS.md + SAD.md + Code -> Testing tasks
- Phase 5: TEST_RESULTS.md -> Verification tasks
- Phase 6: QUALITY_REPORT.md -> Quality assurance tasks
- Phase 7: RISK_REGISTER.md -> Risk management tasks
- Phase 8: CONFIG_RECORDS.md -> Configuration tasks

Usage:
    python3 scripts/generate_full_plan.py --phase 3 --repo /path/to/project
    python3 scripts/generate_full_plan.py --phase 3 --repo /path/to/project --output phase3_FULL.md
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from datetime import datetime
from typing import Optional, cast
from core.harness_config import load_harness_config as _load_harness_config
from core.phase_topology import (
    VALID_PHASES,
    phase_name,
)
from core.utils.project_layout import ProjectLayout

# The generator internals live in scripts/plangen/ (Round 3 M2-M4:
# artifact_parsers / blocks / phase_tasks; byte-equal proof is
# tests/test_plangen_golden.py). Package-qualified import on purpose: this
# module already requires the harness root on sys.path (the `from core...`
# imports above say so loudly), and under that precondition `scripts.plangen`
# resolves for BOTH module identities — `scripts.generate_full_plan` and bare
# `generate_full_plan` (tests put the repo root on sys.path alongside
# scripts/). Only the names the dispatcher below actually calls:
from scripts.plangen.artifact_parsers import _HARNESS_VERSION
from scripts.plangen.blocks import _build_gate_meta
from scripts.plangen.phase_tasks import (
    generate_phase1_tasks,
    generate_phase2_tasks,
    generate_phase3_tasks,
    generate_phase4_tasks,
    generate_phase5_tasks,
    generate_phase6_tasks,
    generate_phase7_tasks,
    generate_phase8_tasks,
    generate_phase9_tasks,
)

# Pure re-exports — every module-level name this file defined before the
# M2-M4 split stays importable from here (tests, cli/project_cmds and any
# other consumer are untouched; cli/_shared.py's gate1_evidence re-export
# convention).
from scripts.plangen.artifact_parsers import (
    _ALL_NFR_TYPES,  # noqa: F401
    _NFR_TYPES_CHECK,  # noqa: F401
    _get_harness_version,  # noqa: F401
    _parse_srs_fr_block_json,  # noqa: F401
    nfr_types_check_satisfied,  # noqa: F401
    parse_config_records,  # noqa: F401
    parse_quality_report,  # noqa: F401
    parse_risk_register,  # noqa: F401
    parse_sad_modules,  # noqa: F401
    parse_srs_fr_nfr_xref,  # noqa: F401
    parse_srs_fr_sections,  # noqa: F401
    parse_srs_nfr_sections,  # noqa: F401
    parse_test_plan,  # noqa: F401
)
from scripts.plangen.blocks import (
    _AGENT_B_CHECKS,  # noqa: F401
    _AGENT_B_EMBED_DOCS,  # noqa: F401
    _GATE_META,  # noqa: F401
    _PHASE_DELIVERABLE_DEPS,  # noqa: F401
    _PHASE_EXIT_GATES,  # noqa: F401
    _PHASE_GATE1_PHASES,  # noqa: F401
    _PHASE_PUSH_LABELS,  # noqa: F401
    _PHASE_ROLES,  # noqa: F401
    _RULES_DIR,  # noqa: F401
    _SPEC_COVERAGE_THRESHOLDS,  # noqa: F401
    _agent_b_dispatch_block,  # noqa: F401
    _checkpoint_index,  # noqa: F401
    _constitution_self_check,  # noqa: F401
    _decomposition_section,  # noqa: F401
    _deliverable_ab_block,  # noqa: F401
    _dynamic_fr_template_block,  # noqa: F401
    _dynamic_phase_context_block,  # noqa: F401
    _entry_gate_check,  # noqa: F401
    _fr_carryforward_steps,  # noqa: F401
    _fr_dev_steps,  # noqa: F401
    _gate4_prerequisites_block,  # noqa: F401
    _gate_exit_checkpoint,  # noqa: F401
    _load_manifest_fr_ids,  # noqa: F401
    _load_rule,  # noqa: F401
    _milestone_push_steps,  # noqa: F401
    _p3_milestone_push_steps,  # noqa: F401
    _phase_advance_step,  # noqa: F401
    _post_adr_constitution_check,  # noqa: F401
    _preflight_steps,  # noqa: F401
    _review_checkpoint,  # noqa: F401
    _rule_block,  # noqa: F401
    _sessions_spawn_deliverable,  # noqa: F401
    _validate_handoff_precondition_block,  # noqa: F401
)


# ============================================================================
# Main Generator
# ============================================================================

def generate_full_plan(phase: int, repo_path: Path, output_path: Optional[Path] = None,
                       dynamic: bool = False, force: bool = False) -> Optional[str]:
    """Generate full plan with phase-specific detailed tasks.

    Idempotency guard: if *output_path* already exists and contains completed
    checklist items (`- [x]`), the plan is NOT overwritten unless *force* is True.
    This prevents `plan-all`/`plan-phase` re-runs from wiping in-progress marks on
    a phase that is already underway. Returns the existing content unchanged in
    that case.
    """
    gate_meta = _build_gate_meta(_load_harness_config(repo_path))

    if output_path and output_path.exists() and not force:
        try:
            _existing = output_path.read_text(encoding="utf-8")
        except OSError:
            _existing = ""
        if "- [x]" in _existing:
            print(
                f"[SKIP] Phase {phase}: {output_path.name} has progress marks — "
                "preserved (use --force to regenerate)."
            )
            return _existing

    srs_paths = [
        ProjectLayout(repo_path).srs_path,
    ]
    srs_path = next((p for p in srs_paths if p.exists()), None)

    # Phase 2-4 need existing SRS; dynamic mode skips this requirement
    if srs_path is None and phase in (2, 3, 4) and not dynamic:
        print(f"[ERROR] SRS.md not found for phase {phase}")
        return None
    _srs = cast(Path, srs_path)  # safe: phases 1 and 5-8 don't use srs_path; dynamic skips it

    generators = {
        1: lambda: generate_phase1_tasks(repo_path, _srs, dynamic=dynamic),
        2: lambda: generate_phase2_tasks(repo_path, _srs, dynamic=dynamic),
        3: lambda: generate_phase3_tasks(repo_path, _srs, dynamic=dynamic, gate_meta=gate_meta),
        4: lambda: generate_phase4_tasks(repo_path, _srs, dynamic=dynamic, gate_meta=gate_meta),
        5: lambda: generate_phase5_tasks(repo_path, dynamic=dynamic, gate_meta=gate_meta),
        6: lambda: generate_phase6_tasks(repo_path, dynamic=dynamic, gate_meta=gate_meta),
        7: lambda: generate_phase7_tasks(repo_path, dynamic=dynamic, gate_meta=gate_meta),
        8: lambda: generate_phase8_tasks(repo_path, dynamic=dynamic, gate_meta=gate_meta),
        9: lambda: generate_phase9_tasks(repo_path, dynamic=dynamic, gate_meta=gate_meta),
    }

    generator = generators.get(phase)
    if not generator:
        print(f"Unknown phase: {phase}")
        return None

    print(f"Generating Phase {phase} tasks...")

    task_lines = generator()

    phase_names = {p: phase_name(p) for p in VALID_PHASES}

    mode_line = ["> **Mode**: Dynamic (load-context at execution time)", ""] if dynamic else []
    plan_lines = [
        f"# Phase {phase} Full Execution Plan -- {repo_path.name}",
        "",
        f"> **Version**: v{_HARNESS_VERSION} (project plan)",
        f"> **Project**: {repo_path.name}",
        f"> **Date**: {datetime.now().strftime('%Y-%m-%d')}",
        f"> **Framework**: harness-methodology v{_HARNESS_VERSION}",
        f"> **Phase**: {phase} - {phase_names.get(phase, 'Unknown')}",
        f"> **Status**: Full version (including Phase {phase} detailed tasks)",
        *mode_line,
        "",
        "> **Hard Rules in Force (this plan)** — explicit reminders:",
        "> - HR-04: HybridWorkflow ON — Agent A authors, a separate Agent B sub-agent reviews. Never role-play A or B yourself.",
        "> - HR-05: harness-methodology wins all conflicts — if a project decision contradicts SKILL.md / INIT / this plan, the harness wins.",
        "> - HR-16: Trace dimension = `min(4a, 4b, 4c)` — ALL THREE must pass (G2/G3/G4 only): 4a = 100% over IN_PROGRESS+VERIFIED FRs, 4b = TEST_SPEC→test coverage (60/80/90% at G2/G3/G4), 4c = NFR→test coverage (60/80/90% at G2/G3/G4, NFR-99 placeholder excluded). `gate_score_overrides` is a **threshold floor (raises, not lowers)** per `sab_parser.derive_gate_score_overrides` — cannot bypass a failing trace dim. Remediation: fix code/FRs/tests to pass, accept gate block, or escalate to human. No automated override.",
        "> - HR-17: NEVER modify files inside `harness/` — debug the framework, never hot-patch the submodule.",
        "",
        "---",
        "",
    ]

    plan_lines.extend(task_lines)

    plan_text = '\n'.join(plan_lines)

    if output_path:
        output_path.write_text(plan_text, encoding='utf-8')
        print(f"Full plan saved to: {output_path}")

    return plan_text


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description='Generate full plan with phase-specific detailed tasks',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    python3 scripts/generate_full_plan.py --phase 3 --repo /path/to/project
    python3 scripts/generate_full_plan.py --phase 5 --repo /path/to/project --output phase5_FULL.md
        """
    )
    parser.add_argument('--phase', type=int, required=True, help='Phase number (1-8)')
    parser.add_argument('--repo', type=str, required=True, help='Repository path')
    parser.add_argument('--output', type=str, help='Output file path')
    parser.add_argument('--no-output', action='store_true', help='Print to stdout instead of saving to file')
    parser.add_argument('--force', action='store_true', help='Force regeneration even if file contains progress marks')

    args = parser.parse_args()

    repo_path = Path(args.repo)
    if not repo_path.exists():
        print(f"Repository not found: {repo_path}")
        return 1

    output_path = Path(args.output) if args.output else None
    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)

    plan = generate_full_plan(args.phase, repo_path, output_path, force=args.force)

    if plan:
        if args.no_output:
            print(plan)
        else:
            print(f"\nFull plan generated ({len(plan)} chars)")
            print(plan[:1500])
        return 0
    else:
        return 1


if __name__ == '__main__':
    sys.exit(main())
