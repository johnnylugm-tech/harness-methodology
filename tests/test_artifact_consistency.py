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
    """Bare-prose forward ref to an invented filename (real hallucination
    pattern: agent writes `02-architecture/ARCHITECTURE.md` when the legal P2
    deliverable is SAD.md). Real refs are not wrapped in backticks — that
    pattern is reserved for documentation quotes and is covered by
    test_forward_ref_in_code_span_allowed."""
    _w(ProjectLayout(tmp_path).traceability_matrix_path,
       "Architecture doc: see 02-architecture/ARCHITECTURE.md for design.\n")
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


def test_forward_ref_in_code_span_allowed(tmp_path: Path) -> None:
    """A path quoted inside a markdown code span is documentation, not an
    actionable forward ref — must NOT trigger. Regression for the
    SPEC_TRACKING.md false-positive where a warning note `` `01-requirements/
    SPEC.md` `` explaining the illegal path got flagged by its own message."""
    _w(ProjectLayout(tmp_path).traceability_matrix_path,
       "Note: `01-requirements/SPEC.md` is illegal per harness check.\n"
       "Canonical lives at `/SPEC.md` (root).\n")
    assert check_forward_refs(tmp_path) == []


def test_forward_ref_in_fenced_code_allowed(tmp_path: Path) -> None:
    """A path inside a fenced code block is documentation, not a forward ref."""
    _w(ProjectLayout(tmp_path).traceability_matrix_path,
       "Example of an illegal ref (do not copy):\n"
       "```\n"
       "02-architecture/ARCHITECTURE.md\n"
       "```\n")
    assert check_forward_refs(tmp_path) == []


def test_forward_ref_in_html_comment_allowed(tmp_path: Path) -> None:
    """A path inside an HTML comment is author-only metadata, not a forward ref."""
    _w(ProjectLayout(tmp_path).traceability_matrix_path,
       "<!-- TODO: replace 02-architecture/ARCHITECTURE.md with SAD.md -->\n"
       "Real ref: 02-architecture/SAD.md\n")
    assert check_forward_refs(tmp_path) == []


def test_forward_ref_outside_code_span_still_blocks(tmp_path: Path) -> None:
    """The fix must not weaken the real check — bare prose refs to invented
    filenames still block. (Code-stripping must not silently pass real refs.)"""
    _w(ProjectLayout(tmp_path).traceability_matrix_path,
       "See 02-architecture/ARCHITECTURE.md for the design.\n")
    errs = [v for v in check_forward_refs(tmp_path) if v.severity == "error"]
    assert len(errs) == 1 and "ARCHITECTURE.md" in errs[0].message


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


def test_class_method_citation_counts_as_ground_truth(tmp_path: Path) -> None:
    """Round-trip bug: `module::Class.method` (a dot in the function part, e.g.
    real citations like `taskq.breaker::Breaker.tick`) must still count as
    ground truth — a bare `\\w+` after `::` stops at the first `.` and fails
    the whole backtick-delimited match, silently dropping the citation. No
    "Linked Modules" line here, so there is no fallback masking the gap."""
    _w(ProjectLayout(tmp_path).traceability_matrix_path,
       "### 3.1 FR-01\n"
       "| AC-FR01-1 | desc | `pkg.mod::Cls.method` | test | ok |\n\n"
       "### 5.3 Module Coverage\n"
       "| `pkg.mod` | (none direct) | | |\n")
    errs = [v for v in check_module_fr_coverage(tmp_path) if v.check_type == "module_coverage_gap"]
    assert any("FR-01" in v.message and "pkg.mod" in v.message for v in errs), (
        "module::Class.method citation must establish ground truth, same as module::func")


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


def test_preflight_artifact_consistency_error_details_carries_violation_detail(
    tmp_path: Path,
) -> None:
    """Round 15 §3 regression guard: `error_details` must keep carrying
    rule_id/message for every error-severity violation — this is what
    preview_next_phase_blocking's obligation extractor reads instead of
    the previous print()-only, discarded detail."""
    _w(ProjectLayout(tmp_path).traceability_matrix_path,
       "Arch: ./02-architecture/ARCHITECTURE.md\n")
    r2 = _hooks(tmp_path, 2).preflight_artifact_consistency()
    assert r2["passed"] is False
    assert "error_details" in r2
    assert len(r2["error_details"]) == r2["errors"]
    assert all("rule_id" in d and "message" in d for d in r2["error_details"])


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


# ── --forward-refs-only route narrows the check set (Round 42 站3 wiring fix) ──


# A real SRS that contains FR/NFR headings but is missing the
# `## FR Block (machine-readable)` JSON section. check_srs_structure would
# emit `SRS-FR-BLOCK` against this file in the full check; the forward-refs
# route must NOT — that check is structurally unrelated to invented
# filenames and its failure used to be misreported by the P1 Forward Ref
# Check step as "FWDREF: FAIL — invented filename ARCHITECTURE.md".
_SRS_WITHOUT_MACHINE_BLOCK = (
    "# Software Requirements Specification\n"
    "\n"
    "## 1. Introduction\n"
    "\n"
    "### FR-01: task submission\n"
    "\n"
    "The submitted command is validated before anything is written.\n"
    "\n"
    "### NFR-01: performance\n"
    "\n"
    "Response time under 200ms.\n"
)


def test_forward_refs_only_skips_srs_structure_block_check(tmp_path, capsys) -> None:
    """Regression for the round 42 站3 wiring bug: the P1 Forward Ref Check
    workflow step runs `check-artifact-consistency --forward-refs-only`
    as a cheap pre-push fast-fail for invented filenames, but
    `check_srs_structure` was added to the same violation set without
    updating the forward-refs-only branch — so an SRS missing its
    machine-readable FR Block surfaced to the workflow as
    "FWDREF: FAIL — invented filename ARCHITECTURE.md" (misclassified),
    halting P1 even when no actual forward ref existed. The fix: when
    `forward_refs_only=True`, run ONLY `check_forward_refs`; the other
    four cross-artifact checks (module_fr_coverage, nfr_adr_coverage,
    security_design, srs_structure) keep running in the default mode.
    """
    import argparse
    from cli.check_cmds import cmd_check_artifact_consistency

    _w(ProjectLayout(tmp_path).srs_path, _SRS_WITHOUT_MACHINE_BLOCK)
    rc = cmd_check_artifact_consistency(
        argparse.Namespace(project=str(tmp_path), forward_refs_only=True)
    )
    out = capsys.readouterr().out
    assert rc == 0, (
        f"forward_refs_only must skip srs_structure; got rc={rc}, stdout={out!r}"
    )
    assert "SRS-FR-BLOCK" not in out, (
        f"forward_refs_only must not emit SRS-FR-BLOCK; got stdout={out!r}"
    )


def test_full_mode_runs_srs_structure_block_check(tmp_path, capsys) -> None:
    """Companion to test_forward_refs_only_skips_srs_structure_block_check:
    the default (full) mode MUST still run srs_structure, otherwise the SRS
    FR Block regression would silently re-appear once the forward-refs
    route is the only one exercised."""
    import argparse
    from cli.check_cmds import cmd_check_artifact_consistency

    _w(ProjectLayout(tmp_path).srs_path, _SRS_WITHOUT_MACHINE_BLOCK)
    rc = cmd_check_artifact_consistency(
        argparse.Namespace(project=str(tmp_path), forward_refs_only=False)
    )
    out = capsys.readouterr().out
    assert rc == 1, "full mode must still block on missing SRS FR Block"
    assert "SRS-FR-BLOCK" in out, (
        f"full mode must emit SRS-FR-BLOCK; got stdout={out!r}"
    )


def test_forward_refs_only_still_catches_illegal_filename(tmp_path, capsys) -> None:
    """The forward_refs_only narrowing must NOT swallow the very thing the
    route is named after: an invented forward reference (e.g.
    `02-architecture/ARCHITECTURE.md` instead of `SAD.md`) must still block
    in this mode. Without this guard, a too-eager fix could silently turn
    the workflow's "Forward Ref Check" step into a no-op."""
    import argparse
    from cli.check_cmds import cmd_check_artifact_consistency

    _w(ProjectLayout(tmp_path).traceability_matrix_path,
       "Architecture doc: see 02-architecture/ARCHITECTURE.md for design.\n")
    rc = cmd_check_artifact_consistency(
        argparse.Namespace(project=str(tmp_path), forward_refs_only=True)
    )
    out = capsys.readouterr().out
    assert rc == 1, "forward_refs_only must still block on invented filenames"
    assert "ARCHITECTURE.md" in out, (
        f"forward_refs_only must still report the illegal filename; "
        f"got stdout={out!r}"
    )
