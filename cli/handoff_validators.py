"""P(N)→P(N+1) handoff validators — one per transition, plus the dispatch table.

Round 80 站7. Moved out of cli/phase_cmds.py verbatim; the function bodies here
are byte-identical to the ones that were there, which is what
tests/test_god_file_split_safety.py asserts by AST source segment.

Why these nine belong together and away from the rest: each answers one
question about one transition, none of them is reached from any code path in
phase_cmds other than `cmd_validate_handoff`, and the dispatch table below is
the only thing that knows which is which. `_resolve_fr_ids_from_manifest`
travels with them because P3→P4 is its main caller; cli/phase_cmds.py keeps a
re-export, which is also how the split-safety net checks that a move left the
wiring behind.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from cli import _shared
from core.canonical_form import canonical_form
from core.quality_gate import spec_coverage
from core.quality_gate.spec_coverage import _parse_inventory_fallback, _parse_test_spec
from core.state_io import StateCorruptError, load_quality_manifest
from core.utils.project_layout import ProjectLayout


def _validate_handoff_p1_to_p2(project: Path) -> list[str]:
    """P1→P2: TEST_INVENTORY.yaml must exist, be non-empty, and cover all FRs."""
    errors: list[str] = []
    # NOTE: TEST_INVENTORY.yaml lives at project root per harness design
    # (D4 spec-coverage fallback, init-project template). This B.1 check originally
    # looked at 01-requirements/ — inconsistent with the rest of harness,
    # and silently blocked every fresh project's P2 entry (Bug
    # discovered 2026-06-17, integration-test E2E).
    inv_path = project / "TEST_INVENTORY.yaml"
    if not inv_path.exists():
        return [
            "TEST_INVENTORY.yaml missing at project root. "
            "P1 Sub-Task 4/4 in the plan template produces this file. "
            "Re-run the Phase 1 orchestrator or invoke the inventory skill manually."
        ]
    text = inv_path.read_text(encoding="utf-8", errors="replace").strip()
    if not text:
        return [
            "TEST_INVENTORY.yaml is empty. P1 orchestrator produced a stub. "
            "Re-run Phase 1 with explicit --fr-tests populated."
        ]

    # Parse and check coverage
    try:
        import yaml
        inventory = yaml.safe_load(text)
    except ImportError:
        inventory = _parse_inventory_fallback(text)
    except Exception as e:  # pylint: disable=broad-exception-caught
        return [f"TEST_INVENTORY.yaml is unparseable: {e}"]

    if not inventory.get("fr_tests") and not inventory.get("cross_cutting"):
        errors.append(
            "TEST_INVENTORY.yaml has neither `fr_tests:` nor `cross_cutting:` "
            "sections. At minimum the P1 naming authority must declare per-FR test names."
        )

    # Check that every FR in SRS has at least one test name in inventory
    srs_path = ProjectLayout(project).srs_path
    if srs_path.exists():
        srs_text = srs_path.read_text(encoding="utf-8", errors="replace")
        declared_frs = set(re.findall(r"\bFR-\d+\b", srs_text))
        covered_frs: set[str] = set()
        fr_tests = inventory.get("fr_tests") or {}
        for fr_id, names in fr_tests.items():
            if names:  # non-empty list of test names
                # I: use canonical_form() — handles all variants
                try:
                    norm = canonical_form(fr_id)
                except ValueError:
                    continue
                if norm in declared_frs:
                    covered_frs.add(norm)
        missing_frs = declared_frs - covered_frs
        if missing_frs:
            errors.append(
                f"TEST_INVENTORY.yaml missing test names for {len(missing_frs)} "
                f"FR(s) declared in SRS.md: {', '.join(sorted(missing_frs))}. "
                f"P1 deliverable must name at least one test per FR."
            )

    return errors

def _validate_handoff_p2_to_p3(project: Path) -> list[str]:
    """P2→P3: TEST_SPEC.md must contain parseable named test cases (table format)."""
    errors: list[str] = []
    spec_path = ProjectLayout(project).test_spec_path
    if not spec_path.exists():
        return [
            "TEST_SPEC.md missing at 02-architecture/TEST_SPEC.md. "
            "P2 Sub-Task 3/3 produces this file via the derive_test_cases.md skill. "
            "Re-run Phase 2 orchestrator with explicit skill invocation."
        ]
    items = _parse_test_spec(spec_path)
    if not items:
        # 0 cases may be legitimate (genuinely empty) or wrong-shape. Distinguish.
        _code, _ = spec_coverage._run_spec_coverage_check(
            project, threshold=60.0, fr_id=None, verbose=False
        )
        if _code == 1:
            errors.append(
                "TEST_SPEC.md has 0 parseable test cases but FRs are defined. "
                "The file is likely the wrong shape (prose strategy doc instead "
                "of the derive_test_cases.md table). Re-run the skill in Phase 2."
            )
        # else: spec-coverage returned 0 (vacuous OK because no FRs); pass.
    return errors

def _validate_handoff_p3_to_p4(project: Path) -> list[str]:
    """P3→P4: every FR must have a per-FR Gate 1 sentinel.

    Same precondition as push-milestone --type p3-post-gate2, but fr_ids
    is auto-resolved from the manifest if not provided.
    """
    fr_ids = _resolve_fr_ids_from_manifest(project)
    if not fr_ids:
        return [
            "Could not resolve FR IDs from .methodology/quality_manifest.json "
            "or --fr-ids. Cannot verify per-FR Gate 1 sentinels."
        ]
    return _shared._validate_p3_post_gate2_precondition(project, fr_ids)

def _validate_handoff_p4_to_p5(project: Path) -> list[str]:
    """P4→P5: TEST_RESULTS.md (P4's deliverable) must exist with non-trivial content,
    AND Gate 3 must be PASS in quality_manifest.json.

    Bug fix (harness-methodology handoff-loop): the previous implementation
    required `05-verification/VERIFICATION_REPORT.md` here, but that file is
    *produced by Phase 5* — checking it on P4→P5 handoff is a chicken-and-egg
    that blocks every fresh Phase 5 entry. Aligned with the other handoff
    validators (P1→P2 checks P1's SRS, P2→P3 checks P2's TEST_SPEC, etc.):
    verify the *upstream* phase's deliverable, not the downstream one.

    VERIFICATION_REPORT.md existence is still asserted by `_validate_handoff_p5_to_p6`
    below, which is the correct handoff boundary for that file.
    """
    errors: list[str] = []
    results_path = ProjectLayout(project).test_results_path
    if not results_path.exists():
        return [
            "TEST_RESULTS.md missing at 04-testing/TEST_RESULTS.md. "
            "Phase 4 produces this file. Re-run Phase 4 orchestrator."
        ]
    text = results_path.read_text(encoding="utf-8", errors="replace").strip()
    if len(text) < 200:
        errors.append(
            f"TEST_RESULTS.md is suspiciously short ({len(text)} chars). "
            f"Real test results are ≥ 1KB. Possible stub."
        )
    # Gate 3 PASS precondition: verified via quality_manifest.json (written by P4
    # workflow). Mirrors the entry-gate check at _verify_entry_gate
    # (harness_cli.py:1553): the manifest's top-level key is `gate_results`
    # (not `gates`), and the field that signals completion is
    # `quality_complete` (not `status`).
    manifest_path = project / ".methodology" / "quality_manifest.json"
    if manifest_path.exists():
        try:
            manifest = load_quality_manifest(project)
            gate_results = manifest.get("gate_results") or {}
            gate3 = gate_results.get("gate3") or {}
            if not gate3.get("quality_complete"):
                errors.append(
                    "Gate 3 not PASS in .methodology/quality_manifest.json "
                    "(gate_results.gate3.quality_complete is not True). "
                    "Re-run Phase 4 Gate 3 evaluation."
                )
        except StateCorruptError:
            pass  # unparseable manifest is a separate concern; don't double-fail here
    return errors

def _validate_handoff_p5_to_p6(project: Path) -> list[str]:
    """P5→P6: VERIFICATION_REPORT.md must exist (aligned with plan text)."""
    errors: list[str] = []
    report = ProjectLayout(project).verification_report_path
    if not report.exists() and not (project / "VERIFICATION_REPORT.md").exists():
        return [
            "VERIFICATION_REPORT.md missing at 05-verification/VERIFICATION_REPORT.md (or VERIFICATION_REPORT.md). "
            "Phase 5 produces this file via the verify methodology."
        ]
    return errors

def _validate_handoff_p6_to_p7(project: Path) -> list[str]:
    """P6→P7: QUALITY_REPORT.md, RELEASE_NOTES.md, FINAL_SIGN_OFF.md must exist
    (same artifacts P6 dispatch review covers; also gate4 quality_complete must be True)."""
    errors: list[str] = []
    q6 = ProjectLayout(project).phase6_quality_dir
    for name in ("QUALITY_REPORT.md", "RELEASE_NOTES.md", "FINAL_SIGN_OFF.md"):
        if not (q6 / name).exists() and not (project / name).exists():
            errors.append(f"{name} missing at 06-quality/{name} (or root). Phase 6 produces this file.")
            
    manifest_path = project / ".methodology" / "quality_manifest.json"
    if manifest_path.exists():
        try:
            manifest = load_quality_manifest(project)
            gate_results = manifest.get("gate_results") or {}
            gate4 = gate_results.get("gate4") or {}
            if not gate4.get("quality_complete"):
                errors.append(
                    "Gate 4 not PASS in .methodology/quality_manifest.json "
                    "(gate_results.gate4.quality_complete is not True). "
                    "Re-run Phase 6 Gate 4 evaluation."
                )
        except StateCorruptError as exc:
            print(f"[WARN] P6→P7 handoff: quality_manifest.json malformed "
                  f"(not blocking handoff — separate concern): {exc}", file=sys.stderr)
    else:
        errors.append("quality_manifest.json missing; run `finalize-gate --gate 4 --phase 6` first.")
    return errors

def _validate_handoff_p7_to_p8(project: Path) -> list[str]:
    """P7→P8: risk register deliverables must exist (07-risk/RISK_REGISTER.md,
    RISK_MITIGATION_PLANS.md, RISK_STATUS_REPORT.md)."""
    errors: list[str] = []
    q7 = ProjectLayout(project).phase7_risk_dir
    for name in ("RISK_REGISTER.md", "RISK_MITIGATION_PLANS.md", "RISK_STATUS_REPORT.md"):
        if not (q7 / name).exists():
            errors.append(f"{name} missing at 07-risk/{name}. Phase 7 produces this file.")
    return errors

def _validate_handoff_p8_to_p9(project: Path) -> list[str]:
    """P8→P9: config records + release checklist must exist, and the
    .methodology-archive/ release snapshot must be populated (P8 milestone
    prerequisite) before entering maintenance."""
    errors: list[str] = []
    q8 = ProjectLayout(project).phase8_config_dir
    for name in ("CONFIG_RECORDS.md", "RELEASE_CHECKLIST.md"):
        if not (q8 / name).exists():
            errors.append(f"{name} missing at 08-config/{name}. Phase 8 produces this file.")
    archive_dir = project / ".methodology-archive"
    if not archive_dir.is_dir() or not any(archive_dir.iterdir()):
        errors.append(
            ".methodology-archive/ missing or empty — the P8 release snapshot "
            "must exist before entering maintenance (push-milestone --type p8 validates it)."
        )
    return errors


_HANDOFF_VALIDATORS = {
    1: _validate_handoff_p1_to_p2,
    2: _validate_handoff_p2_to_p3,
    3: _validate_handoff_p3_to_p4,
    4: _validate_handoff_p4_to_p5,
    5: _validate_handoff_p5_to_p6,
    6: _validate_handoff_p6_to_p7,
    7: _validate_handoff_p7_to_p8,
    8: _validate_handoff_p8_to_p9,
}


def _validate_handoff(project: Path, from_phase: int) -> list[str]:
    """Dispatch to the right per-transition validator.

    Args:
        project:    project root
        from_phase: phase number that just completed (1..8). P8→P9 checks
                    the release snapshot before entering maintenance; P9
                    itself never hands off (terminal steady state).

    Returns:
        list of error strings (empty = handoff OK).
    """
    if from_phase not in _HANDOFF_VALIDATORS:
        return [
            f"No handoff validator for from-phase={from_phase}. "
            f"Supported: {sorted(_HANDOFF_VALIDATORS.keys())}."
        ]
    return _HANDOFF_VALIDATORS[from_phase](project)

def _resolve_fr_ids_from_manifest(project: Path) -> list[str]:
    """Resolve FR IDs from .methodology/quality_manifest.json (fr_ids field)."""
    return list(load_quality_manifest(project, lenient=True).get("fr_ids") or [])
