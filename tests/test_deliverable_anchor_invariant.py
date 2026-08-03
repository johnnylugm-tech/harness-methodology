"""Round 34 站0/站2 — an anchor is an invariant of the deliverable, not a
precondition of one load.

`DELIVERABLE_ANCHORS` (Round 33 站1) gave the H1 rule a single source. What it
did not give it was a single moment: the anchor was checked only where the
Phase 1/2 orchestrator reloads the file, so a deliverable that satisfied it at
P1 and was rewritten at P4 satisfied nothing thereafter and nobody asked.

Measured on run-all-by-workflow, `01-requirements/TRACEABILITY_MATRIX.md`:

    b694901  07-28 13:18  chore(harness): init          # TRACEABILITY_MATRIX.md
    dfd7abd  07-28 14:23  phase1(review-complete)       # Traceability Matrix — taskq
    fa21439  07-28 19:27  handover: advance to Phase 4  (blank)
    91e87c6 / 026c120     P4 / P6                       (blank)

It broke on the P3→P4 advance and stayed broken through Gate 4 and P8, on four
of five real projects, with `last_gate: 4` and every phase green. The check
sat before the defect in time.

Placement is the design: this runs AFTER `_regen_traceability_views`, so the
render-only views the framework owns are repaired first and anything still
failing is a file the framework has no right to rewrite — which is exactly the
population that deserves a BLOCK rather than a warning.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = [pytest.mark.core]

_LEGAL_SRS = {
    "functional_requirements": [
        {"id": "FR-01", "title": "t", "implementation_modules": ["a.py"]},
    ],
    "non_functional_requirements": [
        {"id": "NFR-01", "type": "performance", "dimension": "performance",
         "description": "d", "test_method": "m"},
    ],
}


def _srs_text(first_line: str) -> str:
    return (
        f"{first_line}\n\n## 7. Appendix A\n\n"
        "<!-- FR:START -->\n```json\n"
        + json.dumps(_LEGAL_SRS, indent=2)
        + "\n```\n<!-- FR:END -->\n"
    )


def _project(tmp_path: Path, srs_first_line: str, phase: int = 1) -> Path:
    from core.quality_gate.legal_artifacts import anchor_for

    proj = tmp_path / "proj"
    (proj / ".methodology").mkdir(parents=True)
    (proj / "01-requirements").mkdir(parents=True)
    (proj / ".methodology" / "quality_manifest.json").write_text(
        json.dumps({"fr_ids": ["FR-01"], "frs": [{"id": "FR-01"}]}), encoding="utf-8"
    )
    (proj / ".methodology" / "state.json").write_text(
        json.dumps({"state": "RUNNING", "current_phase": phase, "language": "python"}),
        encoding="utf-8",
    )
    (proj / "01-requirements" / "SRS.md").write_text(
        _srs_text(srs_first_line), encoding="utf-8"
    )
    (proj / "01-requirements" / "SPEC_TRACKING.md").write_text(
        f"{anchor_for('SPEC_TRACKING.md')} — fixture\n\nrows\n", encoding="utf-8"
    )
    return proj


def test_a_broken_anchor_blocks_the_phase_it_was_written_in(tmp_path):
    from cli import phase_cmds
    from cli.exit_codes import EX_ADVANCE_DELIVERABLE_ANCHOR_BROKEN

    broken = _project(tmp_path / "broken", "# SRS - {Project Name}")
    assert phase_cmds._advance_prechecks(broken, 1) == (
        EX_ADVANCE_DELIVERABLE_ANCHOR_BROKEN
    ), (
        "a Phase 1 deliverable whose first line is still the template placeholder "
        "must not be sealed as complete — the Phase 1 orchestrator cannot reload it"
    )


def test_a_correct_anchor_reaches_the_later_checks(tmp_path):
    """Discriminating, not universally blocking (the Round 32 counter-proof).

    The legal fixture must fail LATER — on a missing deliverable, not on the
    anchor — or this check is indistinguishable from an unconditional BLOCK.
    """
    from cli import phase_cmds
    from cli.exit_codes import EX_ADVANCE_DELIVERABLE_ANCHOR_BROKEN
    from core.quality_gate.legal_artifacts import anchor_for

    ok = _project(tmp_path / "ok", f"{anchor_for('SRS.md')} (SRS) — fixture")
    assert phase_cmds._advance_prechecks(ok, 1) != EX_ADVANCE_DELIVERABLE_ANCHOR_BROKEN


def test_a_late_phase_advance_still_checks_an_early_phase_deliverable(tmp_path):
    """The defect this whole station exists for.

    Nothing in Phase 8 reads SRS.md's H1, so nothing in Phase 8 noticed it had
    been rewritten. The invariant is not scoped to the phase that produced the
    file — it covers every anchored deliverable present on disk.
    """
    from cli import phase_cmds
    from cli.exit_codes import EX_ADVANCE_DELIVERABLE_ANCHOR_BROKEN

    late = _project(tmp_path / "late", "Draft notes — SRS", phase=8)
    assert phase_cmds._advance_prechecks(late, 8) == (
        EX_ADVANCE_DELIVERABLE_ANCHOR_BROKEN
    )


def test_zero_anchored_files_is_recorded_not_silently_passed(tmp_path):
    """Denominator protection (R30 站6 / R31 站4).

    A project whose layout puts nothing at the registered paths measures zero
    deliverables, and zero failures out of zero is not a pass. It must not
    BLOCK — an ingestion-mode or non-standard layout is legitimate — but it
    must leave a record, or "we could not look" reads identically to "we looked
    and it was fine".
    """
    from cli import phase_cmds

    bare = tmp_path / "bare"
    (bare / ".methodology").mkdir(parents=True)
    (bare / ".methodology" / "state.json").write_text(
        json.dumps({"state": "RUNNING", "current_phase": 1, "language": "python"}),
        encoding="utf-8",
    )
    findings = phase_cmds._broken_deliverable_anchors(bare)
    assert findings == [], "no anchored file exists, so none can be broken"

    from core.degradation_ledger import LEDGER_RELPATH

    ledger = bare / LEDGER_RELPATH
    assert ledger.is_file(), (
        "scanning zero anchored deliverables must be recorded in the degradation "
        "ledger, not treated as a clean result"
    )
    assert "anchor" in ledger.read_text(encoding="utf-8").lower()
