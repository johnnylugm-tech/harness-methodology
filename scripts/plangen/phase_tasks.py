"""Per-phase task generators for the phase-plan generator (scripts/plangen).

Moved verbatim from scripts/generate_full_plan.py (Round 3 Station M4 — the
byte-equal proof is tests/test_plangen_golden.py). One generate_phaseN_tasks
per phase 1-9; artifact READERS live in artifact_parsers.py, shared prose
builders in blocks.py, and the dispatcher/CLI stay in the facade.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List

from .artifact_parsers import (
    _HARNESS_VERSION,
    parse_config_records,
    parse_quality_report,
    parse_risk_register,
    parse_sad_modules,
    parse_srs_fr_nfr_xref,
    parse_srs_fr_sections,
    parse_srs_nfr_sections,
    parse_test_plan,
)
from .blocks import (
    _GATE_META,
    _PHASE_DELIVERABLE_DEPS,
    _checkpoint_index,
    _constitution_self_check,
    _decomposition_section,
    _deliverable_ab_block,
    _dynamic_fr_template_block,
    _dynamic_phase_context_block,
    _entry_gate_check,
    _fr_carryforward_steps,
    _fr_dev_steps,
    _gate4_prerequisites_block,
    _gate_exit_checkpoint,
    _load_manifest_fr_ids,
    _milestone_push_steps,
    _p3_milestone_push_steps,
    _phase_advance_step,
    _post_adr_constitution_check,
    _preflight_steps,
    _review_checkpoint,
    _sessions_spawn_deliverable,
)

# ============================================================================
# Phase Task Generators
# ============================================================================

def generate_phase1_tasks(repo_path: Path, srs_path: Path, dynamic: bool = False) -> List[str]:
    """Generate Phase 1 detailed tasks (Requirements Specification).

    Exit gate = Agent B peer review (NOT harness run-gate).
    A/B is serial per-deliverable: SRS → SPEC_TRACKING → TRACEABILITY.
    Each deliverable has its own A/B loop; REJECT only backtracks one step.
    """
    _ = repo_path  # reserved for future use (e.g. reading .methodology/state.json)
    lines = []
    lines.append("## Phase 1 Tasks: Requirements Specification")
    lines.append("")
    lines.append("### Phase 1 Overview")
    lines.append("Phase 1 is the project starting point. Define complete SRS.")
    lines.append("**Exit gate = Agent B peer review of deliverables** (not `harness run-gate --gate 1`).")
    lines.append("")

    lines.append("> **Crash Recovery**: after each push, `HANDOVER.md` is written to project root.")
    lines.append("> If context is lost, read `HANDOVER.md` first — it contains phase, status, and next steps.")
    lines.append("")

    # P1 has exactly one checkpoint: human sign-off at phase end
    lines.append("> **Checkpoint Index** (push to GitHub = checkpoint + HANDOVER.md saved):")
    lines.append("> - CHECKPOINT-PEER-REVIEW: Agent B Peer Review (Phase 1 Exit) → `push-checkpoint --phase 1`")
    lines.append("")

    lines.extend([
        "### Phase 1 Precondition",
        "",
        "- **[PROJECT-BRIEF]** Prepare `PROJECT_BRIEF.md` at project root **before starting Phase 1**:",
        "  - Project domain, stakeholders, business goals (1–2 pages)",
        "  - Key constraints (technical, regulatory, budget, timeline)",
        "  - This file is **Agent B's primary context** for all P1 reviews (embedded as DOC 1 in each B-1 prompt)",
        "  - Source: project owner / product manager supplies this before Phase 1 begins",
        "  - Not a P1 deliverable — it is the seed input that drives requirements authoring",
        "",
    ])

    lines.extend(_preflight_steps(1))

    # Load execution-time context BEFORE starting any sub-task (dynamic mode only).
    if dynamic:
        lines.extend(_dynamic_phase_context_block(1, has_fr_template=False))

    # Task decomposition: 3 deliverables with sequential dependencies
    lines.extend(_decomposition_section(1))

    # Serial per-deliverable A/B blocks
    deliverables = _PHASE_DELIVERABLE_DEPS.get(1, [])
    total = len(deliverables)
    label_to_sub_n = {d["label"]: i for i, d in enumerate(deliverables, 1)}
    lines.append("### Requirements Authoring (Serial A/B per Deliverable)")
    lines.append("")
    for i, d in enumerate(deliverables, 1):
        lines.extend(_deliverable_ab_block(1, d, i, total, label_to_sub_n))

    if not dynamic:
        # FR/NFR summary (informational — parsed from already-APPROVED SRS.md if exists)
        frs = parse_srs_fr_sections(srs_path)
        nfrs = parse_srs_nfr_sections(srs_path)

        if frs:
            lines.append("### FR Requirements ({} total)".format(len(frs)))
            lines.append("")
            for fr in frs:
                title = fr['title']
                fr_prefix = f"{fr['fr']}: "
                if title.startswith(fr_prefix):
                    title = title[len(fr_prefix):]
                lines.append(f"#### {fr['fr']}: {title}")
                lines.append(f"**Task**: {fr['desc']}")
                if fr['requirements']:
                    lines.append("**Requirements**:")
                    for req in fr['requirements'][:5]:
                        lines.append(f"- {req}")
                lines.append("")

        if nfrs:
            lines.append("### NFR Non-Functional Requirements ({} total)".format(len(nfrs)))
            lines.append("")
            for nfr in nfrs:
                _nfr_title = nfr['title'].replace(f"{nfr['nfr']}: ", '', 1)
                lines.append(f"#### {nfr['nfr']}: {_nfr_title}")
                lines.append(f"**Requirement**: {nfr['details'][:200]}")
                lines.append("")

    lines.append("### Phase 1 Deliverables")
    lines.append("- `SRS.md` - Software Requirements Specification (FRs + NFRs)")
    lines.append("- `SPEC_TRACKING.md` - Spec tracking matrix")
    lines.append("- `TRACEABILITY_MATRIX.md` - Requirements traceability matrix")
    lines.append("- `TEST_INVENTORY.yaml` - Test inventory (P1 naming authority — feeds TEST_SPEC.md)")
    lines.append(_sessions_spawn_deliverable())
    lines.append("")

    lines.extend(_constitution_self_check(1))
    lines.extend(_review_checkpoint(1))
    lines.extend(_phase_advance_step(1, dynamic=dynamic))
    return lines


def generate_phase2_tasks(repo_path: Path, srs_path: Path, dynamic: bool = False) -> List[str]:
    """Generate Phase 2 detailed tasks (Architecture Design).

    Entry = P1 human APPROVE.  Exit gate = Agent B peer review of SAD + ADR (NOT harness run-gate).
    """
    lines = []
    lines.append("## Phase 2 Tasks: Architecture Design")
    lines.append("")
    lines.append("### Phase 2 Overview")
    lines.append("Phase 2 designs the system architecture based on SRS, producing SAD and ADR.")
    lines.append("**Exit gate = Agent B peer review of deliverables** (not `harness run-gate --gate 1`).")
    lines.append("")
    lines.append("> **Crash Recovery**: after each push, `HANDOVER.md` is written to project root.")
    lines.append("> If context is lost, read `HANDOVER.md` first — it contains phase, status, and next steps.")
    lines.append("")

    # P2 has exactly one checkpoint: human sign-off at phase end
    lines.append("> **Checkpoint Index** (push to GitHub = checkpoint + HANDOVER.md saved):")
    lines.append("> - CHECKPOINT-PEER-REVIEW: Agent B Peer Review (Phase 2 Exit) → `push-checkpoint --phase 2`")
    lines.append("")

    lines.extend(_entry_gate_check(2))  # confirm P1 human APPROVE
    lines.extend(_preflight_steps(2))

    # Load execution-time context BEFORE starting any sub-task (dynamic mode only).
    if dynamic:
        lines.extend(_dynamic_phase_context_block(2, has_fr_template=False))

    # Task decomposition: 3 deliverables with sequential dependencies
    lines.extend(_decomposition_section(2))

    # Serial per-deliverable A/B blocks
    deliverables = _PHASE_DELIVERABLE_DEPS.get(2, [])
    total = len(deliverables)
    label_to_sub_n = {d["label"]: i for i, d in enumerate(deliverables, 1)}
    lines.append("### Architecture Design (Serial A/B per Deliverable)")
    lines.append("")
    for i, d in enumerate(deliverables, 1):
        lines.extend(_deliverable_ab_block(2, d, i, total, label_to_sub_n))
        if d["label"] == "ADR.md":
            lines.extend(_post_adr_constitution_check())

    if not dynamic:
        frs = parse_srs_fr_sections(srs_path)
        modules = parse_sad_modules(repo_path)

        if frs:
            lines.append("### FR Architecture Mapping ({} total)".format(len(frs)))
            lines.append("")
            for fr in frs:
                title = fr['title']
                fr_prefix = f"{fr['fr']}: "
                if title.startswith(fr_prefix):
                    title = title[len(fr_prefix):]
                lines.append(f"#### {fr['fr']}: {title}")
                lines.append(f"**Requirement**: {fr['desc']}")

                mod = modules.get(fr['fr'], {})
                if mod:
                    lines.append("**Module Mapping**:")
                    lines.append(f"- Module: `{mod.get('module', 'N/A')}`")
                    lines.append(f"- File:   `{mod.get('file', 'N/A')}`")
                lines.append("")

    try:
        from core.quality_gate.sab_parser import SAB_BLOCK_TEMPLATE
        _sab_block_md = "  ```yaml\n" + "\n".join(
            "  " + ln for ln in SAB_BLOCK_TEMPLATE.splitlines()
        ) + "\n  ```"
    except ImportError:
        _sab_block_md = (
            "  _(run `from core.quality_gate.sab_parser import render_canonical_sab_template`"
            " to get the canonical template)_"
        )

    lines.extend([
        "### SAB Generation (Machine-Readable Architecture Baseline)",
        "",
        "> **CONTRACT**: The SAB block in SAD.md §5 is parsed by",
        "> `core/quality_gate/sab_parser.py:extract_sab_from_sad()`.",
        "> Field names, `sab:` root key, `phase` as **int** (not string), and",
        "> NFR `type` values must match `render_canonical_sab_template()` exactly.",
        "> Do NOT hand-write the YAML — paste from the template below.",
        "",
        "- **[SAB-WRITE]** Write the SAB block into `02-architecture/SAD.md` §5",
        "  using the canonical template (replace EXAMPLE values with real project values):",
        _sab_block_md,
        "",
        "- **[SAB-VALIDATE]** Validate the SAB block before committing:",
        "  ```bash",
        "  python3 harness/scripts/generate_sab.py --validate --project .",
        "  ```",
        "  - MUST exit 0. On failure the message lists the exact problem",
        "    (e.g. unknown NFR type, `phase` as string).",
        "  - Fix and re-run until PASS.",
        "",
        "- **[SAB-GENERATE]** Generate `.methodology/SAB.json` from the validated SAB block:",
        "  ```bash",
        "  python3 harness/scripts/generate_sab.py --project .",
        "  ```",
        "  > **Note**: If `SAB.json` already exists and needs regeneration, pass `--overwrite`.",
        "  - SAB.json contains all 14 fields from `SABSpec`:",
        "    version, created_at, phase, project, layers, allowed_dependencies,",
        "    quality_targets, nfr_dimension_mapping, nfr_traceability, advisory_only,",
        "    gate_score_overrides, fr_module_traceability, architecture_constraints,",
        "    high_risk_modules.",
        "  - Used by: drift detector (M2), gate architecture dimension, constitution check",
        "  - Also embedded inline in `quality_manifest.json` via `harness_bridge`",
        "",
        "### Security Design (STRIDE-lite Threat Model, Round 10)",
        "",
        "> **CONTRACT**: The SEC block in SAD.md §6 is parsed by",
        "> `core/quality_gate/security_design.py:extract_security_block()`.",
        "> Do NOT hand-write the YAML — paste from the canonical template below.",
        "> `applicability: none` + a justification (>=20 chars) is a fully valid,",
        "> honest declaration for a project with no real attack surface.",
        "",
        "- **[SEC-WRITE]** Write the SEC block into `02-architecture/SAD.md` §6",
        "  using the canonical template (replace EXAMPLE values with real project values):",
        "  ```python",
        "  from core.quality_gate.security_design import render_canonical_security_template",
        "  print(render_canonical_security_template())",
        "  ```",
        "  `applicability: full` requires >=1 `trust_boundaries` and >=1 `threats` per",
        "  boundary; each threat's `owner_module` must be declared in the §5 SAB block,",
        "  `nfr` (optional) must exist in SRS.md, and `verified_by` names the test that",
        "  proves the mitigation (Step 1c of `derive_test_cases.md` forces this test",
        "  into TEST_SPEC.md; `check-artifact-consistency` requires it exist from Phase 5).",
        "",
        "- **[SEC-VALIDATE]** Validate before committing:",
        "  ```bash",
        "  python3 harness_cli.py check-artifact-consistency --project .",
        "  ```",
        "  - MUST exit 0. On failure the message lists the exact rule violated",
        "    (missing block, bad STRIDE category, unregistered owner_module, ...).",
        "  - Fix and re-run until PASS.",
        "",
    ])
    lines.append("### Phase 2 Deliverables")
    lines.append("- `SAD.md` — Software Architecture Document (every FR has module mapping)")
    lines.append("- `ADR.md` — Architecture Decision Records (tech stack, patterns, interfaces)")
    lines.append("- `TEST_SPEC.md` — Test specification catalog (named test cases from SRS, single source of truth — D4 unified check)")
    lines.append("- `.methodology/quality_manifest.json` — Quality manifest (FR list + SAB data)")
    lines.append("- `.methodology/SAB.json` — Machine-readable architecture baseline")
    lines.append(_sessions_spawn_deliverable())
    lines.append("")

    lines.extend(_constitution_self_check(2))
    lines.extend(_review_checkpoint(2))
    lines.extend(_phase_advance_step(2, dynamic=dynamic))
    return lines


def generate_phase3_tasks(repo_path: Path, srs_path: Path, dynamic: bool = False, gate_meta: "dict | None" = None, max_rounds: int = 3) -> List[str]:
    """Generate Phase 3 detailed tasks (Implementation + Gate 1 per-FR + Gate 2 exit)"""
    lines = []
    lines.append("## Phase 3 Tasks: Implementation")
    lines.append("")
    lines.append("### Phase 3 Overview")
    lines.append("Phase 3 implements all FR modules according to SAD, including unit tests.")
    lines.append("Each FR ends with a Gate 1 quality evaluation (CHECKPOINT). Phase exits via Gate 2.")
    lines.append("")

    if dynamic:
        fr_ids: List[str] = []
        checkpoint_n = 1
        lines.extend(_checkpoint_index(fr_ids, phase=3))
        lines.extend(_entry_gate_check(3))
        lines.extend(_preflight_steps(3))
        lines.extend(_dynamic_phase_context_block(3))
        lines.extend(_dynamic_fr_template_block(3, repo_path, gate_meta=gate_meta))
        lines.extend(_p3_milestone_push_steps(fr_ids, dynamic=True))
    else:
        frs = parse_srs_fr_sections(srs_path)
        modules = parse_sad_modules(repo_path)

        # Try manifest for definitive FR list
        manifest_fr_ids = _load_manifest_fr_ids(repo_path)
        fr_ids = manifest_fr_ids if manifest_fr_ids else [fr['fr'] for fr in frs]

        lines.extend(_checkpoint_index(fr_ids, phase=3))
        lines.extend(_entry_gate_check(3))
        lines.extend(_preflight_steps(3))

        if frs and fr_ids:
            srs_fr_map = {fr['fr']: fr for fr in frs}
            srs_fr_set = set(srs_fr_map.keys())
            carry_forward = [fid for fid in fr_ids if fid not in srs_fr_set]

            if carry_forward:
                lines.append("### FR Implementation Tasks ({} total: {} new + {} carry-forward)".format(
                    len(fr_ids), len(frs), len(carry_forward)))
                lines.append("")
                lines.append(f"> **Carry-forward from Phase 1**: {', '.join(carry_forward)} — "
                             "already implemented; Gate 1 re-evaluation only.")
                lines.append("")
            else:
                lines.append("### FR Implementation Tasks ({} total)".format(len(frs)))
                lines.append("")

            checkpoint_n = 1
            for fr_id in fr_ids:
                if fr_id in srs_fr_map:
                    fr = srs_fr_map[fr_id]
                    title = fr['title']
                    fr_prefix = f"{fr['fr']}: "
                    if title.startswith(fr_prefix):
                        title = title[len(fr_prefix):]
                    lines.append(f"#### {fr['fr']}: {title}")
                    lines.append(f"**Task**: {fr['desc']}")

                    if fr['requirements']:
                        lines.append("**SRS Requirements**:")
                        for req in fr['requirements'][:5]:
                            lines.append(f"- {req}")

                    if fr['test_cases']:
                        lines.append("**Test Cases**:")
                        for inp, out in fr['test_cases']:
                            lines.append(f"- Input [{inp}] -> Output [{out}]")

                    mod = modules.get(fr['fr'], {})
                    if mod:
                        lines.append("**SAD Mapping**:")
                        lines.append(f"- Module: `{mod.get('module', 'N/A')}`")
                        lines.append(f"- File:   `{mod.get('file', 'N/A')}`")

                    lines.append("**Forbidden**:")
                    lines.append("- app/infrastructure/ (deprecated)")
                    lines.append("- @covers: L1 Error")
                    lines.append("- @type: edge")
                    lines.append("")

                    lines.extend(_fr_dev_steps(fr['fr'], phase=3, project=repo_path))
                else:
                    lines.append(f"#### {fr_id}: Re-evaluation (carry-forward)")
                    lines.append("> Already implemented in Phase 1. Gate 1 re-run to verify no regressions.")
                    lines.append("")
                    lines.extend(_fr_carryforward_steps(fr_id, phase=3))

                # Gate 1 is handled inside the sub-agent dispatch (run-fr-step).
                # _gate1_checkpoint() removed — no duplicate inline G1a/G1b/G1c/G1d.
                checkpoint_n += 1

        elif fr_ids:
            lines.append("### FR Implementation Tasks ({} total)".format(len(fr_ids)))
            lines.append("")
            checkpoint_n = 1
            for fr_id in fr_ids:
                lines.append(f"#### {fr_id}: [See SRS.md and SAD.md for implementation details]")
                lines.append("")
                lines.extend(_fr_dev_steps(fr_id, phase=3, project=repo_path))
                # Gate 1 handled inside run-fr-step sub-agent dispatch.
                checkpoint_n += 1

        else:
            lines.append("### ⚠️  FR Implementation Tasks — NONE FOUND")
            lines.append("")
            lines.append("> **WARNING**: `parse_srs_fr_sections()` returned zero FRs and no")
            lines.append("> `quality_manifest.json` FR list was found. The plan has **no per-FR")
            lines.append("> task blocks**.  Verify SRS.md format: use `### FR-01: Title` sections")
            lines.append("> or `| FR-01 | description |` table rows.")
            lines.append("")
            lines.append("To fix: update SRS.md format and re-run `plan-phase --phase 3`.")
            lines.append("")
            checkpoint_n = 1

        # NFR summary — informational, shows which NFRs each FR implements
        nfrs = parse_srs_nfr_sections(srs_path)
        if nfrs:
            # Build NFR→FRs reverse map.  Primary source: §2 cross-reference table
            # (parse_srs_fr_nfr_xref).  Fallback: search raw FR description text for
            # NFR IDs (works when SRS embeds NFR refs inside FR sections directly).
            _fr_nfr_xref = parse_srs_fr_nfr_xref(srs_path)
            _nfr_to_frs: Dict[str, List[str]] = {}
            for _fr_id, _nfr_ids in _fr_nfr_xref.items():
                for _nfr_id in _nfr_ids:
                    _nfr_to_frs.setdefault(_nfr_id, []).append(_fr_id)

            lines.append("### NFR Coverage ({} total)".format(len(nfrs)))
            lines.append("")
            lines.append("> NFRs are implemented **within FRs** — each FR satisfies one or more NFRs.")
            lines.append("> **NFR traceability requirement (4c gate dim, F-2.3)**: every NFR-XX ID")
            lines.append("> in `01-requirements/SRS.md` MUST appear as a `# NFR-XX` annotation")
            lines.append("> in at least one test file under `03-development/tests/`. Without these")
            lines.append("> annotations the `traceability` gate dim scores 4c = 0% and Gate 2")
            lines.append("> fails. The per-FR TDD-RED step below shows the exact annotation")
            lines.append("> pattern; NFR-99 (deferred/ambiguity placeholder per phase1_plan.md")
            lines.append("> R-CANONICAL-INTERP-001) is excluded from the 4c denominator.")
            lines.append("")
            lines.append("| NFR | Type | FRs Implementing |")
            lines.append("|-----|------|-----------------|")
            for nfr in nfrs:
                nfr_id = nfr['nfr']
                nfr_type = nfr.get('title', '').replace(f'{nfr_id}: ', '')
                # Primary: cross-reference table lookup
                _ref_frs = _nfr_to_frs.get(nfr_id, [])
                # Fallback: grep NFR ID from FR raw_details text
                if not _ref_frs:
                    _ref_frs = [
                        fr['fr'] for fr in frs
                        if nfr_id.lower() in fr.get('raw_details', '').lower()
                    ] if frs else []
                fr_list = ', '.join(_ref_frs)
                if not fr_list:
                    fr_list = '—'
                lines.append(f"| {nfr_id} | {nfr_type[:30]} | {fr_list} |")
            lines.append("")
            if not _fr_nfr_xref:
                lines.append("> ⚠️ **NFR→FR mapping not found** — `—` entries above indicate no `NFR Association`")
                lines.append("> column was detected in SRS.md FR tables. To enable auto-mapping, add an")
                lines.append("> `NFR Association` column to each FR row in `01-requirements/SRS.md §2`.")
                lines.append("")
            lines.append("**Gate 2 NFR dimensions** (tool-scored, see Gate 2 config):")
            lines.append("- `security` (bandit), `secrets_scanning` (gitleaks), `mutation_testing` (mutmut 2.x — `pip install 'mutmut<3'`)")
            lines.append("- `integration_coverage` (pytest), `test_assertion_quality` (pytest)")
            lines.append("")
        else:
            # NFRs couldn't be parsed — remind the user to check manually
            lines.append("### NFR Coverage")
            lines.append("")
            lines.append("> ⚠️  NFR sections could not be parsed from SRS.md.")
            lines.append("> Verify NFR compliance manually against `01-requirements/SRS.md` §3.")
            lines.append("")

        lines.extend(_p3_milestone_push_steps(fr_ids))

    lines.extend(_gate_exit_checkpoint(gate_num=2, phase=3, gate_meta=gate_meta, max_rounds=max_rounds))

    lines.append("### Phase 3 Deliverables")
    lines.append("- `03-development/src/` - All FR modules implemented")
    lines.append("- `tests/` - Unit tests (≥80% coverage per FR)")
    lines.append(_sessions_spawn_deliverable())
    lines.append("- Gate 1 PASS for every FR")
    lines.append("- Gate 2 PASS (phase exit, composite ≥ 75)")
    lines.append("")

    # audit-phase runs inside advance-phase — no separate local step needed
    lines.extend(_phase_advance_step(3, dynamic=dynamic))
    return lines


def generate_phase4_tasks(repo_path: Path, srs_path: Path, dynamic: bool = False, gate_meta: "dict | None" = None, max_rounds: int = 3) -> List[str]:
    """Generate Phase 4 detailed tasks (Testing + Gate 1 per-FR + Gate 3 exit)"""
    _g3_dims = (gate_meta or _GATE_META)[3][1]
    lines = []
    lines.append("## Phase 4 Tasks: Test Planning & Execution")
    lines.append("")
    lines.append("### Phase 4 Overview")
    lines.append("Phase 4 formulates and executes a complete test plan based on Phase 3 code.")
    lines.append(f"Each FR ends with a Gate 1 re-evaluation (CHECKPOINT). Phase exits via Gate 3 ({_g3_dims} dims).")
    lines.append("")

    if dynamic:
        fr_ids: List[str] = []
        checkpoint_n = 1
        lines.extend(_checkpoint_index(fr_ids, phase=4))
        lines.extend(_entry_gate_check(4))
        lines.extend(_preflight_steps(4))
        lines.extend(_dynamic_phase_context_block(4))
        # ── CHECKPOINT-0: Generate TEST_PLAN.md before any FR testing ─────────
        lines.append("### CHECKPOINT-0: Generate TEST_PLAN.md")
        lines.append("")
        lines.append("> Generate `04-testing/TEST_PLAN.md` from SRS.md FR acceptance criteria.")
        lines.append("> This step runs once before per-FR test execution.")
        lines.append("")
        lines.append("**Generate TEST_PLAN.md** (orchestrator runs directly — not a sub-agent dispatch):")
        lines.append("- Read SRS.md FR acceptance criteria → write TEST_PLAN.md with per-FR test cases")
        lines.append("  - For each FR: test case ID, description, input, expected output, priority")
        lines.append("  - Include positive, negative, boundary, and edge case categories")
        lines.append("  - Output: `04-testing/TEST_PLAN.md`")
        lines.append("- Verify TEST_PLAN.md covers all FRs from manifest/quality_manifest.json")
        lines.append("- **[TP-DONE]** TEST_PLAN.md written: all FRs have ≥1 test case, NFRs addressed")
        lines.append("")
        lines.extend(_dynamic_fr_template_block(4, repo_path, gate_meta=gate_meta))
        lines.extend(_milestone_push_steps(fr_ids, phase=4, pre_gate=3,
                                           push_prefixes=("⑤", "⑥"),
                                           dynamic=True))
    else:
        frs = parse_srs_fr_sections(srs_path)
        test_plans = parse_test_plan(repo_path)
        manifest_fr_ids = _load_manifest_fr_ids(repo_path)
        fr_ids = manifest_fr_ids if manifest_fr_ids else [fr['fr'] for fr in frs]

        lines.extend(_checkpoint_index(fr_ids, phase=4))
        lines.extend(_entry_gate_check(4))
        lines.extend(_preflight_steps(4))

        # ── CHECKPOINT-0: Generate TEST_PLAN.md before any FR testing ─────────
        lines.append("### CHECKPOINT-0: Generate TEST_PLAN.md")
        lines.append("")
        lines.append("> Generate `04-testing/TEST_PLAN.md` from SRS.md FR acceptance criteria.")
        lines.append("> This step runs once before per-FR test execution.")
        lines.append("")
        lines.append("**Generate TEST_PLAN.md** (orchestrator runs directly — not a sub-agent dispatch):")
        lines.append("- Read SRS.md FR acceptance criteria → write TEST_PLAN.md with per-FR test cases")
        lines.append("  - For each FR: test case ID, description, input, expected output, priority")
        lines.append("  - Include positive, negative, boundary, and edge case categories")
        lines.append("  - Output: `04-testing/TEST_PLAN.md`")
        lines.append("- Verify TEST_PLAN.md covers all FRs from manifest/quality_manifest.json")
        lines.append("- **[TP-DONE]** TEST_PLAN.md written: all FRs have ≥1 test case, NFRs addressed")
        lines.append("")

        checkpoint_n = 1
        if test_plans:
            # Test plan exists — show items; Gate 1 still runs per FR from manifest
            lines.append("### Test Plan Items ({} total from TEST_PLAN.md)".format(len(test_plans)))
            lines.append("")
            for tp in test_plans:
                lines.append(f"#### {tp['title']}")
                lines.append(f"**Content**: {tp['details'][:200]}")
                lines.append("")
            if fr_ids:
                lines.append("### FR Gate 1 Evaluations ({} FRs from manifest)".format(len(fr_ids)))
                lines.append("> **Cross-reference**: Agent A's test scope for each FR = TEST_PLAN.md items above.")
                lines.append("> Match TEST_PLAN.md items to this FR's module before writing/executing tests.")
                lines.append("")
                for fr_id in fr_ids:
                    lines.append(f"#### {fr_id}: Test Execution")
                    lines.append("")
                    lines.extend(_fr_dev_steps(fr_id, phase=4, project=repo_path))
                    # Gate 1 handled inside run-fr-step sub-agent dispatch.
                    checkpoint_n += 1
        elif frs and fr_ids:
            srs_fr_map = {fr['fr']: fr for fr in frs}
            srs_fr_set = set(srs_fr_map.keys())
            carry_forward = [fid for fid in fr_ids if fid not in srs_fr_set]

            if carry_forward:
                lines.append("### FR Test Coverage ({} total: {} new + {} carry-forward)".format(
                    len(fr_ids), len(frs), len(carry_forward)))
                lines.append("")
                lines.append(f"> **Carry-forward from Phase 1**: {', '.join(carry_forward)} — "
                             "already implemented; Gate 1 re-evaluation only.")
                lines.append("")
            else:
                lines.append("### FR Test Coverage")
                lines.append("")

            checkpoint_n = 1
            for fr_id in fr_ids:
                if fr_id in srs_fr_map:
                    fr = srs_fr_map[fr_id]
                    title = fr['title']
                    fr_prefix = f"{fr['fr']}: "
                    if title.startswith(fr_prefix):
                        title = title[len(fr_prefix):]
                    lines.append(f"#### {fr['fr']}: {title}")
                    lines.append(f"**Test Target**: Verify {fr['desc']}")
                    if fr['test_cases']:
                        lines.append("**Test Cases**:")
                        for inp, out in fr['test_cases']:
                            lines.append(f"- Input [{inp}] -> Output [{out}]")
                    lines.append("")
                    lines.extend(_fr_dev_steps(fr['fr'], phase=4, project=repo_path))
                else:
                    lines.append(f"#### {fr_id}: Re-evaluation (carry-forward)")
                    lines.append("> Already implemented. Gate 1 re-run to verify no regressions.")
                    lines.append("")
                    lines.extend(_fr_carryforward_steps(fr_id, phase=4))

                # Gate 1 handled inside run-fr-step sub-agent dispatch.
                checkpoint_n += 1
        elif fr_ids:
            lines.append("### FR Test Coverage ({} FRs)".format(len(fr_ids)))
            lines.append("")
            for fr_id in fr_ids:
                lines.append(f"#### {fr_id}: [See SRS.md for test targets]")
                lines.append("")
                lines.extend(_fr_dev_steps(fr_id, phase=4, project=repo_path))
                # Gate 1 handled inside run-fr-step sub-agent dispatch.
                checkpoint_n += 1
        else:
            lines.append("### ⚠️  FR Test Coverage — NONE FOUND")
            lines.append("")
            lines.append("> **WARNING**: No FR sections parsed and no manifest FR list found.")
            lines.append("> Verify SRS.md format or re-run `plan-phase --phase 4` after")
            lines.append("> quality_manifest.json is generated.")
            lines.append("")
            checkpoint_n = 1

    lines.extend([
        "### TEST_RESULTS.md Summary",
        "",
        "- **[TEST-RESULTS-SUMMARY]** Finalize `04-testing/TEST_RESULTS.md` before milestone push:",
        "  - Summarise test execution: test cases run, pass/fail outcome, any deferred issues",
        "  - Real test execution is enforced by advance-phase TDD-PRECHECK "
        "(`pytest --cov-fail-under=100`), not by string-matching this document",
        "",
        "### COVERAGE_REPORT.md — Coverage Summary",
        "",
        "- **[COVERAGE-REPORT]** Generate `04-testing/COVERAGE_REPORT.md`:",
        "  ```bash",
        "  pytest --cov=03-development/src --cov-report=term-missing -q \\",
        "    | tee 04-testing/coverage_raw.txt",
        "  python3 -m coverage report --format=total  # → overall %",
        "  ```",
        "  Write `04-testing/COVERAGE_REPORT.md` including:",
        "  - Overall coverage % (must be ≥80% for Gate 3)",
        "  - Per-module breakdown (from term-missing output)",
        "  - Uncovered lines (if any)",
        "  > cross_artifact.py validates this file's numbers against live `pytest --cov` at Gate 3.",
        "  > Fabricated numbers will be caught by the cross-artifact check.",
        "",
    ])

    if not dynamic:
        lines.extend(_milestone_push_steps(fr_ids, phase=4, pre_gate=3,
                                           push_prefixes=("⑤", "⑥")))

    lines.extend(_gate_exit_checkpoint(gate_num=3, phase=4, gate_meta=gate_meta, max_rounds=max_rounds))

    lines.append("### Phase 4 Deliverables")
    lines.append("- `04-testing/TEST_PLAN.md` - Test plan")
    lines.append("- `04-testing/TEST_RESULTS.md` - Test results (test execution summary)")
    lines.append("- `04-testing/COVERAGE_REPORT.md` - Coverage report")
    lines.append(_sessions_spawn_deliverable())
    lines.append("- Gate 1 PASS for every FR")
    lines.append(f"- Gate 3 PASS (phase exit, composite ≥ 80, {_g3_dims} dims)")
    lines.append("")

    # audit-phase runs inside advance-phase — no separate local step needed
    lines.extend(_phase_advance_step(4, dynamic=dynamic))
    return lines


def generate_phase5_tasks(repo_path: Path, dynamic: bool = False, gate_meta: "dict | None" = None) -> List[str]:
    """Generate Phase 5 detailed tasks (Verification & Delivery + Gate 1 per-FR)"""
    lines = []
    lines.append("## Phase 5 Tasks: Verification & Delivery")
    lines.append("")
    lines.append("### Phase 5 Overview")
    lines.append("Phase 5 verifies the system against test results, ensuring all FRs meet acceptance criteria.")
    lines.append("Each FR ends with a Gate 1 re-evaluation (CHECKPOINT). No harness run-gate — P5 was cleared by Gate 3 at P4 exit. However, advance-phase still enforces TDD-PRECHECK (gitleaks + ruff + mypy + pytest 100% + D4 spec-coverage ≥80%) before FSM transition. Mutation testing is gated per-FR at Gate 1, not re-verified here.")
    lines.append("")
    lines.append(
        "> If code changes are needed for any FR (e.g., bug fixes found during verification), "
        "run full TDD: `run-fr-step --step TDD-RED` → TDD-GREEN → TDD-IMPROVE → GATE1. "
        "Crash recovery (`resume-fr-phase`) auto-detects code changes and switches from "
        "GATE1-DELTA to full TDD when needed."
    )
    lines.append("")

    manifest_fr_ids = _load_manifest_fr_ids(repo_path)
    lines.extend(_checkpoint_index(manifest_fr_ids if not dynamic else [], phase=5))
    lines.extend(_entry_gate_check(5))
    lines.extend(_preflight_steps(5))

    if dynamic:
        lines.extend(_dynamic_phase_context_block(5))
        lines.extend(_dynamic_fr_template_block(5, repo_path, gate_meta=gate_meta))
    elif manifest_fr_ids:
        lines.append("### FR Verification Tasks ({} total)".format(len(manifest_fr_ids)))
        lines.append("")
        for fr_id in manifest_fr_ids:
            lines.append(f"#### {fr_id}: Verification")
            lines.append(f"- Confirm all acceptance criteria from SRS.md are met for {fr_id}")
            lines.append(f"- Run integration tests for {fr_id}")
            lines.append("- Verify edge cases and error paths")
            lines.append("- Confirm ≥80% branch coverage")
            lines.append("")
            lines.extend(_fr_carryforward_steps(fr_id, phase=5))
    else:
        lines.append("### Verification Items")
        lines.append("(No FR list found — add per-FR verification steps based on SRS.md)")
        lines.append("")
    lines.extend([
        "### P5 System Verification",
        "",
        "- **[BASELINE]** Generate `05-verification/BASELINE.md` (system state snapshot):",
        "  - Document: current version, test results summary, coverage %, Gate 3 composite score",
        "  - Reference: `04-testing/TEST_RESULTS.md` and `03-development/src/` module list",
        "  - Structure: 7 `##` sections per `templates/BASELINE.md` (Overview, Functional,",
        "    Quality, Performance, Issue Log, Change Log, Acceptance Sign-off) — audit-phase",
        "    C5 counts H2 headings and warns below 7",
        "- **[VERIFY-REPORT]** Generate `05-verification/VERIFICATION_REPORT.md`:",
        "  - For each FR: verification status, acceptance criteria result (PASS/FAIL), evidence",
        "  - Include: test coverage %, mutation score, deferred issues from Gate 3",
        "  - Certify: all Gate 3 open issues addressed or deferred with justification",
        "- Re-run integration tests: `pytest tests/integration/ -q` (or equivalent per NFRs)",
        "- Confirm performance NFRs met: review benchmark entries in `04-testing/TEST_RESULTS.md`",
        "- Re-run security scan clean: `bandit -r 03-development/src/ -ll` + `gitleaks detect`",
        "",
    ])

    lines.extend([
        "### P5 Milestone Push (10-Push Strategy ⑦)",
        "",
        "- **PUSH ⑦ — P5-baseline** (after VERIFICATION_REPORT.md is generated):",
        "  ```bash",
        "  python3 harness_cli.py push-milestone --type p5-baseline --project .",
        "  ```",
        "  > Writes HANDOVER.md + commits + pushes.",
        "",
    ])

    lines.append("### Phase 5 Deliverables")
    lines.append("- `05-verification/BASELINE.md` - System baseline")
    lines.append("- `05-verification/VERIFICATION_REPORT.md` - Verification report")
    lines.append(_sessions_spawn_deliverable())
    lines.append("- Gate 1 PASS for every FR")
    lines.append("")

    # audit-phase runs inside advance-phase — no separate local step needed
    lines.extend(_phase_advance_step(5, dynamic=dynamic))
    return lines


def generate_phase6_tasks(repo_path: Path, dynamic: bool = False, gate_meta: "dict | None" = None, max_rounds: int = 3) -> List[str]:
    """Generate Phase 6 detailed tasks (Quality Assurance — Gate 4 full replacement)"""
    _g4_dims = (gate_meta or _GATE_META)[4][1]
    lines = []
    lines.append("## Phase 6 Tasks: Quality Assurance")
    lines.append("")
    lines.append("### Phase 6 Overview")
    lines.append("Phase 6 centres on Gate 4 — the full-project quality evaluation.")
    lines.append(f"No FR loop. Gate 4 = tool-scored automated evaluation ({_g4_dims} dims incl. traceability, CRG recon) PLUS")
    lines.append("Agent B peer review of the QA deliverables (HR-01) — both are required to exit.")
    lines.append("")

    # P6 has exactly one checkpoint: Gate 4
    lines.append("> **Checkpoint Index** (push to GitHub = checkpoint saved):")
    lines.append(f"> - CHECKPOINT-GATE-4: Gate 4 (Full Project — {_g4_dims} dims) + Agent B peer review")
    lines.append("")

    lines.extend(_entry_gate_check(6))
    lines.extend(_preflight_steps(6))

    if dynamic:
        lines.extend(_dynamic_phase_context_block(6, has_fr_template=False))

    lines.append("### P6 Phase End Audit (+ A/B Review)")
    lines.append("")
    lines.append("> A/B collaboration is active for Phase 6 deliverables (HR-01).")
    lines.append("> Agent A generates QUALITY_REPORT.md and RELEASE_NOTES.md.")
    lines.append("> Agent B (reviewer) reviews the deliverables and verifies Gate 4 score (3-layer defense, T1-B).")
    lines.append("")

    # Only embed static quality metrics from QUALITY_REPORT.md in non-dynamic mode.
    # In dynamic mode the plan is generated at project start (before any Gate 4 data exists),
    # so parsing the current QUALITY_REPORT.md would embed stale project-specific values.
    if not dynamic:
        qr = parse_quality_report(repo_path)
        if qr.get('metrics'):
            lines.append("### Existing Quality Metrics (from QUALITY_REPORT.md)")
            lines.append("")
            for metric, value in qr['metrics']:
                lines.append(f"- **{metric}**: {value}")
            lines.append("")

    lines.append("### Pre-Gate Preparation")
    lines.append("- Confirm all FRs are merged to main branch")
    lines.append("- Confirm no open critical or high issues from Gate 3")
    lines.append("")

    lines.extend(_gate4_prerequisites_block())

    lines.extend(_gate_exit_checkpoint(gate_num=4, phase=6, gate_meta=gate_meta, max_rounds=max_rounds))

    lines.append("### Post-Gate 4 Git Tagging")
    lines.append("- After Gate 4 PASS, generate the annotated git tag with composite scores:")
    lines.append("  ```bash")
    lines.append("  python3 harness_cli.py gate4-tag --project .")
    lines.append("  ```")
    lines.append("  → Verify: `git tag -l -n9` shows the new `harness-v4-*` tag.")
    lines.append("")
    lines.append("### Phase 6 Deliverables")
    lines.append(f"- Gate 4 PASS (composite ≥ 85, all {_g4_dims} dims, CRG recon done)")
    lines.append("- `06-quality/QUALITY_REPORT.md` - Quality report (auto-generated by Gate 4)")
    lines.append("- `RELEASE_NOTES.md` - Release notes")
    lines.append("- `FINAL_SIGN_OFF.md` - Final sign-off")
    lines.append(_sessions_spawn_deliverable())
    lines.append("")

    # audit-phase runs inside advance-phase — no separate local step needed
    lines.extend(_phase_advance_step(6, dynamic=dynamic))
    return lines


def generate_phase7_tasks(repo_path: Path, dynamic: bool = False, gate_meta: "dict | None" = None) -> List[str]:
    """Generate Phase 7 detailed tasks (Risk Management + Gate 1 per-FR)"""
    lines = []
    lines.append("## Phase 7 Tasks: Risk Management")
    lines.append("")
    lines.append("### Phase 7 Overview")
    lines.append("Phase 7 identifies, tracks, and mitigates all risks introduced during development.")
    lines.append("Each FR gets a Gate 1 risk-aware re-evaluation (CHECKPOINT). No harness run-gate — P7 cleared by Gate 4. However, advance-phase still enforces TDD-PRECHECK (gitleaks + ruff + mypy + pytest 100% + D4 spec-coverage ≥90%) before FSM transition. Mutation testing is gated per-FR at Gate 1, not re-verified here.")
    lines.append("")
    lines.append(
        "> If risk mitigation requires code changes to any FR, run full TDD: "
        "`run-fr-step --step TDD-RED` → TDD-GREEN → TDD-IMPROVE → GATE1. "
        "Crash recovery (`resume-fr-phase`) auto-detects code changes and switches from "
        "GATE1-DELTA to full TDD when needed."
    )
    lines.append("")

    manifest_fr_ids = _load_manifest_fr_ids(repo_path)
    lines.extend(_checkpoint_index(manifest_fr_ids if not dynamic else [], phase=7))
    lines.extend(_entry_gate_check(7))
    lines.extend(_preflight_steps(7))

    if dynamic:
        lines.extend(_dynamic_phase_context_block(7))
        lines.extend(_dynamic_fr_template_block(7, repo_path, gate_meta=gate_meta))
        lines.extend([
            "### P7 Risk Register Generation",
            "",
            "> Generate risk deliverables ONCE before per-FR evaluation (orchestrator runs directly).",
            "",
            "- **[RISK-REGISTER]** Generate `07-risk/RISK_REGISTER.md`:",
            "  - Review open issues from Gate 3/4, `deferred_fixes.md`, and `.sessi-work/issue_registry.json`",
            "  - For each risk: ID, name, likelihood (1–5), impact (1–5), category, mitigation approach",
            "- **[RISK-MITIGATION]** Generate `07-risk/RISK_MITIGATION_PLANS.md`:",
            "  - For HIGH risks (likelihood × impact ≥ 9): write formal mitigation plan with owner + deadline",
            "- **[RISK-STATUS]** Generate `07-risk/RISK_STATUS_REPORT.md`:",
            "  - Summary of all risks, current status, mitigation owner, target date",
            "",
        ])
    else:
        risks = parse_risk_register(repo_path)
        if risks:
            lines.append("### Risk Register ({} total)".format(len(risks)))
            lines.append("")
            for risk in risks:
                lines.append(f"- **{risk['name']}**: Define likelihood/impact scores and mitigation approach → document in RISK_REGISTER.md")
            lines.append("")
        else:
            lines.append("### Risk Categories")
            lines.append("- Technical risks")
            lines.append("- Schedule risks")
            lines.append("- Resource risks")
            lines.append("- External risks")
            lines.append("")

        if manifest_fr_ids:
            lines.append("### FR Risk Evaluation ({} total)".format(len(manifest_fr_ids)))
            lines.append("")
            for fr_id in manifest_fr_ids:
                lines.append(f"#### {fr_id}: Risk Assessment")
                lines.append(f"- Review open issues from previous gates for {fr_id}")
                lines.append(f"- Check `deferred_fixes.md` for {fr_id} entries")
                lines.append("- Confirm no new defects introduced")
                lines.append("")
                lines.extend(_fr_carryforward_steps(fr_id, phase=7))
        else:
            lines.append("(No FR list found in manifest — run Gate 1 per FR manually)")
            lines.append("")

    lines.extend([
        "### P7 Milestone Push (10-Push Strategy ⑨)",
        "",
        "- **PUSH ⑨ — P7 exit** (after risk register is complete):",
        "  ```bash",
        "  python3 harness_cli.py push-milestone --type p7 --project .",
        "  ```",
        "  > Writes HANDOVER.md + commits + pushes.",
        "",
    ])

    lines.append("### Phase 7 Deliverables")
    lines.append("- `07-risk/RISK_REGISTER.md` - Risk register")
    lines.append("- `07-risk/RISK_MITIGATION_PLANS.md` - Mitigation plans")
    lines.append("- `07-risk/RISK_STATUS_REPORT.md` - Risk status report")
    lines.append(_sessions_spawn_deliverable())
    lines.append("- Gate 1 PASS for every FR")
    lines.append("")

    # audit-phase runs inside advance-phase — no separate local step needed
    lines.extend(_phase_advance_step(7, dynamic=dynamic))
    return lines


def generate_phase8_tasks(repo_path: Path, dynamic: bool = False, gate_meta: "dict | None" = None) -> List[str]:
    """Generate Phase 8 detailed tasks (Configuration Management + Gate 1 per-FR)"""
    lines = []
    lines.append("## Phase 8 Tasks: Configuration Management")
    lines.append("")
    lines.append("### Phase 8 Overview")
    lines.append("Phase 8 establishes a complete configuration management system ensuring traceability.")
    lines.append("Each FR gets a Gate 1 config-aware re-evaluation (CHECKPOINT). No harness run-gate — P8 cleared by Gate 4. However, advance-phase still enforces TDD-PRECHECK (gitleaks + ruff + mypy + pytest 100% + D4 spec-coverage ≥90%) before FSM transition. Mutation testing is gated per-FR at Gate 1, not re-verified here.")
    lines.append("")
    lines.append(
        "> If configuration changes require code modifications to any FR, run full TDD: "
        "`run-fr-step --step TDD-RED` → TDD-GREEN → TDD-IMPROVE → GATE1. "
        "Crash recovery (`resume-fr-phase`) auto-detects code changes and switches from "
        "GATE1-DELTA to full TDD when needed."
    )
    lines.append("")

    manifest_fr_ids = _load_manifest_fr_ids(repo_path)
    lines.extend(_checkpoint_index(manifest_fr_ids if not dynamic else [], phase=8))
    lines.extend(_entry_gate_check(8))
    lines.extend(_preflight_steps(8))

    if dynamic:
        lines.extend(_dynamic_phase_context_block(8))
        lines.extend(_dynamic_fr_template_block(8, repo_path, gate_meta=gate_meta))
        p8_baseline_block = ([
            "> **Baseline source** (harness commits `4738542` + `51bd4a8`):",
            "> `CONFIG_RECORDS.md` and `RELEASE_CHECKLIST.md` are deterministically generated",
            "> by `scripts/phase8_doc_gen.py` during the P7→P8 advance-phase hook (see",
            "> `phase7_plan.md` §Auto-trigger on P7→P8 advance). P8 phase work is therefore",
            "> **review and append**, not regenerate:",
            ">",
            "> 1. Read the framework-generated baseline in `08-config/`",
            "> 2. Flag any missing sections the generator could not derive",
            "> 3. Append human-only context (ownership, on-call rotation, runbook links,",
            ">    anything not derivable from `state.json` / `quality_manifest.json` / git)",
            "> 4. Do NOT overwrite the framework-generated version — that would break",
            ">    determinism (byte-equal across runs) for downstream consumers",
            "",
        ] if _HARNESS_VERSION >= "2.12.0" else [])
        lines.extend([
            "### P8 Configuration Records Generation",
            "",
            "> Generate config deliverables ONCE before push-milestone (orchestrator runs directly).",
            "",
            *p8_baseline_block,
            "- **[CONFIG-RECORDS]** Review + append `08-config/CONFIG_RECORDS.md`:",
            "  - Framework baseline already contains: env var inventory, source-of-truth",
            "    module references, feature flags derived from `harness_config.json`",
            "  - Human-only additions: ownership per config item, secret rotation cadence,",
            "    access audit log reference",
            "  - Reference: `03-development/src/` module configs + any `.env.example` or `settings.py`",
            "- **[RELEASE-CHECKLIST]** Review + append `08-config/RELEASE_CHECKLIST.md`:",
            "  - Framework baseline already contains: Gate 4 PASS proof, quality_manifest",
            "    composite score, FR coverage summary, git tag/hash",
            "  - Human-only additions: deployment runbook URL, rollback owner + on-call,",
            "    post-release monitoring dashboard, customer comms template",
            "",
        ])
    else:
        configs = parse_config_records(repo_path)
        if configs:
            lines.append("### Configuration Items ({} total)".format(len(configs)))
            lines.append("")
            for config in configs:
                lines.append(f"- **{config['name']}**: Document value/source/access method → update CONFIG_RECORDS.md")
            lines.append("")
        else:
            lines.append("### Configuration Categories")
            lines.append("- Environment configuration")
            lines.append("- Deployment configuration")
            lines.append("- Security configuration")
            lines.append("- Monitoring configuration")
            lines.append("")

        if manifest_fr_ids:
            lines.append("### FR Configuration Evaluation ({} total)".format(len(manifest_fr_ids)))
            lines.append("")
            for fr_id in manifest_fr_ids:
                lines.append(f"#### {fr_id}: Configuration Record")
                lines.append(f"- Confirm {fr_id} configuration items are documented in CONFIG_RECORDS.md")
                lines.append("- Confirm environment variables / secrets are managed (not hardcoded)")
                lines.append(f"- Confirm deployment checklist entries for {fr_id}")
                lines.append("")
                lines.extend(_fr_carryforward_steps(fr_id, phase=8))
        else:
            lines.append("(No FR list found in manifest — run Gate 1 per FR manually)")
            lines.append("")

    lines.extend([
        "### P8 Archive — REQUIRED before push-milestone (CI p8-archive-check)",
        "",
        "- **[P8-ARCHIVE]** Create `.methodology-archive/` directory (required for CI `p8-archive-check`):",
        "  ```bash",
        "  mkdir -p .methodology-archive",
        "  cp -r .methodology/ .methodology-archive/",
        "  ```",
        "  > Must run BEFORE `push-milestone --type p8`; `_validate_p8_completion()` in push-milestone auto-verifies.",
        "  > CI job `p8-archive-check` also validates this directory on push to main.",
        "",
    ])

    lines.extend([
        "### P8 Milestone Push (10-Push Strategy ⑩)",
        "",
        "- **PUSH ⑩ — P8 exit** (after config records are complete):",
        "  ```bash",
        "  python3 harness_cli.py push-milestone --type p8 --project .",
        "  ```",
        "  > Writes HANDOVER.md + commits + pushes. Development pipeline complete.",
        "",
        "- **[P8→P9]** Enter maintenance mode (steady state — bug fixes and",
        "  feature changes continue as Change Requests):",
        "  ```bash",
        "  python3 harness_cli.py advance-phase --completed 8 --project .",
        "  ```",
        "  > Phase 9 is re-entrant and never exits; work is ticket-driven",
        "  > (`cr-open` / `cr-close`, see phase9_plan.md).",
        "  > **Sync**: `advance-phase` only commits the handover locally. The workflow",
        "  > orchestrator for this phase runs a separate `git push origin main` immediately",
        "  > after to publish that commit to origin, and also confirms any pending Gate 4",
        "  > `harness-v*` tag has been pushed.",
        "",
    ])

    lines.append("### Phase 8 Deliverables")
    lines.append("- `CONFIG_RECORDS.md` - Configuration records")
    lines.append("- `RELEASE_CHECKLIST.md` - Release checklist")
    lines.append(_sessions_spawn_deliverable())
    lines.append("- Gate 1 PASS for every FR")
    lines.append("")

    # audit-phase runs inside advance-phase — no separate local step needed
    lines.extend(_phase_advance_step(8, dynamic=dynamic))
    return lines


def generate_phase9_tasks(repo_path: Path, dynamic: bool = False, gate_meta: "dict | None" = None) -> List[str]:
    """Generate Phase 9 maintenance playbook (CR-driven steady state).

    Unlike P1-P8 plans, this is a re-entrant loop playbook: the real work
    plan materializes per-CR at cr-open time. ASPICE SUP.9 (CR-BUG) /
    SUP.10 (CR-FEAT).
    """
    _ = (repo_path, dynamic, gate_meta)  # P9 playbook is static — no per-FR template expansion
    lines: List[str] = []
    lines.append("## Phase 9 Tasks: Maintenance (Change Request loop)")
    lines.append("")
    lines.append("### Phase 9 Overview")
    lines.append(
        "Phase 9 is a RE-ENTRANT STEADY STATE — it never exits "
        "(`advance-phase --completed 9` is always BLOCKED). All work is "
        "ticket-driven: CR-BUG (ASPICE SUP.9 problem resolution) and CR-FEAT "
        "(ASPICE SUP.10 change request management). Every change re-enters "
        "the existing traceability chain; nothing bypasses the phase folders."
    )
    lines.append("")
    lines.extend(_entry_gate_check(9))
    lines.extend(_preflight_steps(9))
    lines.extend([
        "### CR-BUG workflow (SUP.9 — bug fix)",
        "",
        "- **[CR-OPEN]** `python3 harness_cli.py cr-open --type bug --title '...' --severity high --project .`",
        "- **[REPRO-FIRST]** Write a FAILING repro test BEFORE touching code; record it:",
        "  `cr-update --cr CR-NN --set repro_test=tests/test_crNN_repro.py`",
        "- **[ROOT-CAUSE]** Document root cause: `cr-update --cr CR-NN --set root_cause='...'`",
        "  then advance: `--status ANALYZED` → `--status APPROVED` → `--status IN_PROGRESS`",
        "- **[FIX]** Fix code (keep `[FR-XX]` annotations). If an SRS acceptance",
        "  criterion was itself wrong, correct SRS.md and note it in impact_analysis.",
        "- **[VERIFY]** Repro test green + full suite green; re-run Gate 1 on touched FRs:",
        "  ```bash",
        "  python3 harness_cli.py run-gate --gate 1 --fr-id FR-XX --phase 9 --project .",
        "  python3 harness_cli.py finalize-gate --gate 1 --fr-id FR-XX --phase 9 --project .",
        "  ```",
        "  Untouched FRs: `run-gate --gate 1 --fr-id FR-YY --phase 9 --delta` (regression check)",
        "- **[EVIDENCE]** `cr-update --cr CR-NN --set affected_frs=FR-XX --set resolution.fix_commit=<sha> --status VERIFIED`",
        "",
        "### CR-FEAT workflow (SUP.10 — feature add/change)",
        "",
        "- **[CR-OPEN]** `python3 harness_cli.py cr-open --type feat --title '...' --project .`",
        "- **[IMPACT]** Record impact + FR IDs: `cr-update --cr CR-NN --set affected_frs=FR-XX",
        "  --set impact_analysis.srs=true --set impact_analysis.sad=true --set impact_analysis.test_spec=true`",
        "- **[APPROVAL]** SUP.10 decision: `cr-update --cr CR-NN --set approval.approved_by=<name>",
        "  --set approval.justification='...'` then `--status ANALYZED` → `APPROVED` → `IN_PROGRESS`",
        "- **[SPEC-WRITEBACK]** Update the FROZEN artifacts in place (never around them):",
        "  1. `01-requirements/SRS.md` — add/update `### FR-XX:` section",
        "  2. `02-architecture/SAD.md` — FR→module table row (new module → `amend-sab`)",
        "  3. `02-architecture/TEST_SPEC.md` — FR test section; `TEST_INVENTORY.yaml` entry",
        "- **[TDD]** Implement via run-fr-step (same discipline as P3):",
        "  ```bash",
        "  python3 harness_cli.py run-fr-step --step TDD-RED --fr-id FR-XX --phase 9 --project .",
        "  # → TDD-GREEN → TDD-IMPROVE → GATE1",
        "  ```",
        "- **[EVIDENCE]** `cr-update --cr CR-NN --set resolution.fix_commit=<sha> --status VERIFIED`",
        "",
        "### CR closure (both types — fail-closed re-entry checklist)",
        "",
        "- **[ATTESTATION]** Rebuild the git-anchored trace attestation after artifact changes:",
        "  ```bash",
        "  python3 harness_cli.py build-trace-attestation --project . --write",
        "  ```",
        "- **[CR-CLOSE]** Full checklist (ticket evidence + Gate 1 per affected FR +",
        "  attestation verify + spec/SAD drift). Any failure prints the missing items:",
        "  ```bash",
        "  python3 harness_cli.py cr-close --cr CR-NN --project .",
        "  ```",
        "- **[PUSH]** One milestone push per closed CR:",
        "  ```bash",
        "  python3 harness_cli.py push-milestone --type cr-close --cr CR-NN --project .",
        "  ```",
        "",
        "### Phase 9 Deliverables",
        "- `09-maintenance/MAINTENANCE_LOG.md` — CR index (auto-appended by cr-close)",
        "- `.methodology/change_requests/CR-NN.json` — ticket state (machine)",
        "- Gate 1 PASS for every CR-touched FR; attestation clean after every close",
        "",
    ])
    return lines
