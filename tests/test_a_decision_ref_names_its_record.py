"""Round 114 站3 — a resolution reference names its record by content, not by line number.

Round 113 站9 made `resolution_ref` a `path:line` and checked that the line
names the issue as resolved. The check on the line's content was right; the
line NUMBER added nothing to it but fragility. taskq-open was blocked by it
twice over:

  55415df  re-pinned four refs by hand after `amend-sab --declare` added one
           line above them (the refs stayed put, validate-handoff blocked a
           correct amendment);
  78f79c15 then taught amend_sad to rebase `SAD.md:N` through a line diff —
           a repair for one writer, while any other edit above the records
           still moved them out from under their refs.

Measured on a copy of taskq-open: delete eight unrelated lines above the
records and `validate-handoff --from-phase 2` blocks on four refs whose
records are still there, unchanged.

The record's identity is the shape the template and the P2 prompt already
prescribe — a line reading `<id>: resolved — <decision>` — so the reference
names the file and the check finds the line. `path:N` keeps working (N is a
hint nobody needs). The same rule now answers `review_ref`, the sibling that
had the same `path:line` design (`<property_id>: accepted|rejected|revised`).
"""

from __future__ import annotations

from pathlib import Path

from core.quality_gate.decision_issues import decision_issue_findings
from core.quality_gate.property_check import _review_disposition_resolves


def _project(tmp_path: Path, record_text: str) -> Path:
    srs = tmp_path / "01-requirements" / "SRS.md"
    srs.parent.mkdir(parents=True)
    srs.write_text("# SRS\nNFR-99.1-deferred\n", encoding="utf-8")
    sad = tmp_path / "02-architecture" / "SAD.md"
    sad.parent.mkdir(parents=True)
    sad.write_text(record_text, encoding="utf-8")
    return tmp_path


def _rows(ref: str) -> list:
    return [{"id": "NFR-99.1-deferred", "status": "resolved", "resolution_ref": ref}]


_RECORD = "NFR-99.1-deferred: resolved — p95 from raw round timings\n"


def test_a_record_that_moved_down_still_resolves(tmp_path):
    proj = _project(tmp_path, "# SAD\n\nnew paragraph\nanother line\n" + _RECORD)
    # The ref was written when the record sat on line 2.
    assert decision_issue_findings(proj, _rows("02-architecture/SAD.md:2"), entering_phase=3) == []


def test_a_ref_may_name_the_file_alone(tmp_path):
    proj = _project(tmp_path, "# SAD\n" + _RECORD)
    assert decision_issue_findings(proj, _rows("02-architecture/SAD.md"), entering_phase=3) == []


def test_a_sentence_that_mentions_resolving_is_not_a_record(tmp_path):
    proj = _project(tmp_path, "# SAD\nNFR-99.1-deferred will be resolved once we measure p95.\n")
    found = decision_issue_findings(proj, _rows("02-architecture/SAD.md"), entering_phase=3)
    assert found and "nfr-99.1-deferred: resolved" in found[0].lower()


def test_an_unresolved_record_is_not_a_resolution(tmp_path):
    proj = _project(tmp_path, "# SAD\nNFR-99.1-deferred: unresolved — still open\n")
    assert decision_issue_findings(proj, _rows("02-architecture/SAD.md:2"), entering_phase=3)


def test_a_longer_id_does_not_answer_for_a_shorter_one(tmp_path):
    proj = _project(tmp_path, "# SAD\nNFR-99.10-deferred: resolved — another decision\n")
    assert decision_issue_findings(proj, _rows("02-architecture/SAD.md"), entering_phase=3)


def test_a_ref_outside_the_project_does_not_resolve(tmp_path):
    proj = _project(tmp_path, "# SAD\n" + _RECORD)
    assert decision_issue_findings(proj, _rows("/etc/passwd"), entering_phase=3)
    assert decision_issue_findings(proj, _rows("../SAD.md"), entering_phase=3)


def test_a_property_review_ref_follows_the_same_rule(tmp_path):
    adr = tmp_path / "ADR.md"
    adr.write_text("# ADR\n\nmoved\nP-1: accepted — invariant is total\n", encoding="utf-8")
    assert _review_disposition_resolves(tmp_path, "ADR.md:2", "P-1")
    assert _review_disposition_resolves(tmp_path, "ADR.md", "P-1")
    adr.write_text("# ADR\nP-1 may be accepted later\n", encoding="utf-8")
    assert not _review_disposition_resolves(tmp_path, "ADR.md", "P-1")


def test_amend_sab_no_longer_rewrites_line_references():
    """The rebase existed only for `SAD.md:N` refs; with the record found by
    content there is nothing for an amendment to move."""
    import core.quality_gate.sad_sab_edit as edit

    assert not hasattr(edit, "rebase_sad_refs")
