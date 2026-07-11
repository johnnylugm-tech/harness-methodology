"""Tests for artifact_consistency — the two decidable gates that machine-catch
the P1/P2 audit hallucinations (issues 2 & 3).

check_forward_refs: a stage-directory file reference (e.g. `02-architecture/
ARCHITECTURE.md`) must name a real framework deliverable — catches the
ARCHITECTURE.md forward-reference hallucination (real: SAD.md).

check_nfr_adr_coverage: every SRS NFR must appear in ADR.md's traceability
TABLE (not just anywhere in the doc — NFR-06 appeared in a prose roll-up while
being dropped from the table) — catches the NFR-06 coverage gap.
"""

from __future__ import annotations

from pathlib import Path

from core.quality_gate.artifact_consistency import (
    check_forward_refs,
    check_module_fr_coverage,
    check_nfr_adr_coverage,
)
from core.utils.project_layout import ProjectLayout


def _w(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _adr_path(proj: Path) -> Path:
    return proj / "02-architecture" / "adr" / "ADR.md"


_ADR_TABLE = (
    "## Traceability Matrix\n\n"
    "| ADR | FR / NFR served | SPEC.md anchor |\n"
    "|-----|-----------------|----------------|\n"
    "| ADR-001 | NFR-01 (perf), FR-05 (CLI) | §5 |\n"
    "| ADR-002 | NFR-02, NFR-03 | §6 |\n"
)


# ── issue 3: forward-reference legality ──────────────────────────────────────


def test_illegal_forward_ref_blocks(tmp_path: Path) -> None:
    _w(ProjectLayout(tmp_path).traceability_matrix_path,
       "Architecture doc: `./02-architecture/ARCHITECTURE.md`\n")
    errs = [v for v in check_forward_refs(tmp_path) if v.severity == "error"]
    assert len(errs) == 1 and "ARCHITECTURE.md" in errs[0].message


def test_legal_refs_pass(tmp_path: Path) -> None:
    _w(ProjectLayout(tmp_path).traceability_matrix_path,
       "See ./02-architecture/SAD.md and ./02-architecture/adr/ADR.md and "
       "./07-risk/RISK_REGISTER.md\n")
    assert [v for v in check_forward_refs(tmp_path) if v.severity == "error"] == []


def test_unknown_stage_dir_not_flagged(tmp_path: Path) -> None:
    # A directory with no deliverable whitelist must not be second-guessed.
    _w(ProjectLayout(tmp_path).traceability_matrix_path,
       "./03-development/notes.md and ./00-summary/scratch.md\n")
    assert check_forward_refs(tmp_path) == []


# ── issue 2: NFR → ADR traceability-table coverage ───────────────────────────


def test_nfr_missing_from_adr_table_blocks(tmp_path: Path) -> None:
    _w(ProjectLayout(tmp_path).srs_path,
       "### NFR-01\n### NFR-02\n### NFR-03\n### NFR-06\n")
    _w(_adr_path(tmp_path), _ADR_TABLE)  # table covers NFR-01/02/03, not NFR-06
    errs = [v for v in check_nfr_adr_coverage(tmp_path) if v.severity == "error"]
    assert any("NFR-06" in v.message for v in errs)


def test_nfr_in_prose_but_absent_from_table_still_blocks(tmp_path: Path) -> None:
    # The real bug: NFR-06 present in a prose roll-up but dropped from the table.
    _w(ProjectLayout(tmp_path).srs_path, "### NFR-01\n### NFR-06\n")
    _w(_adr_path(tmp_path),
       _ADR_TABLE + "\n### roll-up\n- NFR-06 (atomic write) covered by ADR-002\n")
    errs = [v for v in check_nfr_adr_coverage(tmp_path) if v.severity == "error"]
    assert any("NFR-06" in v.message for v in errs), "prose mention must not satisfy table coverage"


def test_all_nfr_covered_passes(tmp_path: Path) -> None:
    _w(ProjectLayout(tmp_path).srs_path, "### NFR-01\n### NFR-02\n### NFR-03\n")
    _w(_adr_path(tmp_path), _ADR_TABLE)
    assert [v for v in check_nfr_adr_coverage(tmp_path) if v.severity == "error"] == []


def test_nfr99_excluded(tmp_path: Path) -> None:
    _w(ProjectLayout(tmp_path).srs_path, "### NFR-01\n### NFR-02\n### NFR-99\n")
    _w(_adr_path(tmp_path), _ADR_TABLE)  # NFR-99 need not be covered
    assert [v for v in check_nfr_adr_coverage(tmp_path) if v.severity == "error"] == []


def test_no_adr_table_needs_review(tmp_path: Path) -> None:
    _w(ProjectLayout(tmp_path).srs_path, "### NFR-01\n")
    _w(_adr_path(tmp_path), "# ADR\n\nprose only, no traceability table.\n")
    assert [v.severity for v in check_nfr_adr_coverage(tmp_path)] == ["info"]


# ── module ↔ FR/NFR ownership coverage ────────────────────────────────────────
# Real defects reproduced from a live P1 run (taskq project): TRACEABILITY_MATRIX.md
# §3/§4 AC rows cite `taskq.cli::cmd_run` under FR-03, but §5.3's own `taskq.cli`
# row omits FR-03 (self-contradiction); SPEC_TRACKING.md's §5 assigns FR-05 to
# `taskq.executor` when every FR-05 AC row only ever cites `taskq.cli`/`taskq.store`
# (unbacked ownership claim, no AC row ever cites taskq.executor under FR-05).


def test_module_coverage_gap_in_matrix_own_table_blocks(tmp_path: Path) -> None:
    _w(ProjectLayout(tmp_path).traceability_matrix_path,
       "### 3.1 FR-01\n"
       "| AC-FR01-1 | desc | `pkg.mod::func` | test | ok |\n\n"
       "### 5.3 Module Coverage\n"
       "| `pkg.mod` | (none direct) | | |\n")
    errs = [v for v in check_module_fr_coverage(tmp_path) if v.check_type == "module_coverage_gap"]
    assert any("FR-01" in v.message and "pkg.mod" in v.message for v in errs)


def test_module_ownership_mismatch_in_spec_tracking_blocks(tmp_path: Path) -> None:
    layout = ProjectLayout(tmp_path)
    _w(layout.traceability_matrix_path,
       "### 3.1 FR-01\n"
       "| AC-FR01-1 | desc | `pkg.mod::func` | test | ok |\n\n"
       "### 5.3 Module Coverage\n"
       "| `pkg.mod` | FR-01 | | |\n")
    _w(layout.spec_tracking_path,
       "## 5. Module Ownership\n"
       "| `pkg.other` | high-risk | X | Y | FR-01 |\n")
    errs = [v for v in check_module_fr_coverage(tmp_path) if v.check_type == "module_ownership_mismatch"]
    assert any("pkg.other" in v.message and "FR-01" in v.message and "pkg.mod" in v.message
               for v in errs), "unbacked FR-01 claim on pkg.other must be flagged"


def test_spec_tracking_partial_ownership_not_flagged_as_missing(tmp_path: Path) -> None:
    """SPEC_TRACKING.md's §5 is explicitly scoped to 'high-risk modules per
    C-11' ownership assignment, not a completeness claim (unlike
    TRACEABILITY_MATRIX.md's own §5.3, whose heading claims exhaustive
    coverage) — omitting an FR/NFR there must NOT be flagged as a gap."""
    layout = ProjectLayout(tmp_path)
    _w(layout.traceability_matrix_path,
       "### 3.1 FR-01\n"
       "| AC-FR01-1 | desc | `pkg.mod::func` | test | ok |\n\n"
       "### 4.1 NFR-01\n"
       "| AC-NFR01-1 | desc | `pkg.mod::other` | test | ok |\n\n"
       "### 5.3 Module Coverage\n"
       "| `pkg.mod` | FR-01, NFR-01 | | |\n")
    _w(layout.spec_tracking_path,
       "## 5. Module Ownership\n"
       "| `pkg.mod` | high-risk | X | Y | FR-01 |\n")  # NFR-01 omitted — allowed
    gaps = [v for v in check_module_fr_coverage(tmp_path)
            if v.check_type == "module_coverage_gap" and "SPEC_TRACKING" in v.message]
    assert gaps == []


def test_module_fr_coverage_all_consistent_passes(tmp_path: Path) -> None:
    layout = ProjectLayout(tmp_path)
    _w(layout.traceability_matrix_path,
       "### 3.1 FR-01\n"
       "| AC-FR01-1 | desc | `pkg.mod::func` | test | ok |\n\n"
       "### 5.3 Module Coverage\n"
       "| `pkg.mod` | FR-01 | | |\n")
    _w(layout.spec_tracking_path,
       "## 5. Module Ownership\n"
       "| `pkg.mod` | high-risk | X | Y | FR-01 |\n")
    assert check_module_fr_coverage(tmp_path) == []


def test_module_fr_coverage_no_fr_headings_no_op(tmp_path: Path) -> None:
    _w(ProjectLayout(tmp_path).traceability_matrix_path,
       "# Traceability Matrix\n\nNo requirement headings here.\n")
    assert check_module_fr_coverage(tmp_path) == []


# ── preflight wiring (phase-gated blocking + composition guard) ──────────────


def _hooks(project: Path, phase: int):
    from core.phase_hooks import PhaseHooks

    return PhaseHooks(str(project), phase=phase, enable_kill_switch=False)


def test_preflight_forward_refs_informational_at_p1_blocking_at_p2(tmp_path: Path) -> None:
    _w(ProjectLayout(tmp_path).traceability_matrix_path,
       "Arch: ./02-architecture/ARCHITECTURE.md\n")
    r1 = _hooks(tmp_path, 1).preflight_artifact_consistency()
    assert r1["passed"] is True and r1["errors"] == 1  # P1 informational
    r2 = _hooks(tmp_path, 2).preflight_artifact_consistency()
    assert r2["passed"] is False and r2["errors"] == 1


def test_preflight_nfr_coverage_only_checked_from_p3(tmp_path: Path) -> None:
    _w(ProjectLayout(tmp_path).srs_path, "### NFR-01\n### NFR-06\n")
    _w(_adr_path(tmp_path), _ADR_TABLE)  # table has NFR-01/02/03 — NFR-06 dropped
    r2 = _hooks(tmp_path, 2).preflight_artifact_consistency()
    assert r2["passed"] is True  # NFR→ADR not evaluated until P3 (ADR just written)
    r3 = _hooks(tmp_path, 3).preflight_artifact_consistency()
    assert r3["passed"] is False and r3["errors"] >= 1


def test_artifact_consistency_is_wired_into_preflight_all() -> None:
    """Mechanism upgraded with the PREFLIGHT_CHECKS registry: membership in
    the registry IS composition — tests/test_preflight_registry.py proves
    _do_preflight_all runs exactly the registry."""
    from core.phase_hooks import PREFLIGHT_CHECKS

    assert (
        "artifact_consistency", "preflight_artifact_consistency",
    ) in PREFLIGHT_CHECKS, (
        "artifact_consistency gate dropped from PREFLIGHT_CHECKS — invented "
        "filenames / NFR coverage gaps would stop blocking"
    )
