"""Phase 5's verification report has never read an acceptance criterion.

Round 97. `05-verification/VERIFICATION_REPORT.md` is the artefact Phase 5
delivers: per-FR, the acceptance criteria and the verdict against them. Every
copy in the corpus says, for every FR:

    _No acceptance criteria extracted from SRS.md — verify manually._

The framework has two acceptance-criteria parsers. Measured against the same
eleven SRS documents:

    core/quality_gate/artifact_consistency.py::_srs_acceptance_criteria
        11/11 projects, 1,004 criteria (28-134 each)

    scripts/generate_verification_report.py::_extract_acceptance_criteria
        11/11 projects, ZERO

The second one requires `AC-FR-1-1: text` on a plain line. Every SRS in the
corpus writes `#### AC-1.1` headings under a `**Acceptance criteria**` block —
the shape the framework's own Phase 1 prompt produces, and the shape the
canonical parser was written for. That parser already has three consumers
(`check_ac_identifiers` and two more) and is registered in
`delivery_fingerprint`; the report generator carried a private copy that has
never matched anything, on any project, ever.

Round 17 / Round 33's mother pattern: one contract, two statements. The fix is
not a better regex here — it is that there is only one place the framework
decides what an acceptance criterion looks like.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = [pytest.mark.core]

REPO = Path(__file__).resolve().parent.parent

#: The shape every corpus SRS uses, and the shape the P1 prompt produces.
_HEADING_SRS = """\
# SRS

### FR-01: Task CRUD

**Acceptance criteria**

#### AC-1.1
Creating a task returns 201.

#### AC-1.2
A duplicate name returns 409.

### FR-02: Listing

**Acceptance criteria**

#### AC-2.1
Listing is cursor-paginated.
"""

#: The other spelling the canonical parser reads, so the fix must keep it.
_BULLET_SRS = """\
# SRS

### FR-01: Task CRUD

**Acceptance criteria**

- AC-FR-1-1: Creating a task returns 201.
- AC-FR-1-2: A duplicate name returns 409.
"""


def _project(root: Path, srs: str) -> Path:
    (root / "01-requirements").mkdir(parents=True, exist_ok=True)
    (root / "01-requirements" / "SRS.md").write_text(srs, encoding="utf-8")
    (root / ".methodology").mkdir(exist_ok=True)
    (root / ".methodology" / "quality_manifest.json").write_text(
        '{"fr_ids": ["FR-01", "FR-02"], "gate_results": {}}', encoding="utf-8",
    )
    return root


def test_the_report_and_the_framework_agree_on_what_a_criterion_is(tmp_path):
    """The property, stated as an equality rather than as a regex.

    A second parser that merely *works* would pass a "the report is non-empty"
    assertion while disagreeing with every other consumer about which lines
    count. This pins that they are the same answer.
    """
    from core.quality_gate.artifact_consistency import srs_acceptance_criteria
    from scripts.generate_verification_report import _extract_acceptance_criteria

    project = _project(tmp_path, _HEADING_SRS)
    canonical = srs_acceptance_criteria(project)
    report = _extract_acceptance_criteria(project)
    assert report == canonical, (
        "the report generator answers a different question than the rest of "
        "the framework about what an acceptance criterion is"
    )
    assert canonical.get("FR-01") == ["AC-1.1", "AC-1.2"], canonical


def test_the_heading_shape_every_corpus_project_uses_is_read(tmp_path):
    """The regression itself: `#### AC-1.1` produced zero criteria."""
    from scripts.generate_verification_report import _extract_acceptance_criteria

    project = _project(tmp_path, _HEADING_SRS)
    got = _extract_acceptance_criteria(project)
    assert got, "the shape every corpus SRS uses still yields nothing"
    assert sum(len(v) for v in got.values()) == 3, got


def test_the_bullet_shape_keeps_working(tmp_path):
    """Negative control: the canonical parser reads both spellings, so the fix
    must not trade one for the other."""
    from scripts.generate_verification_report import _extract_acceptance_criteria

    got = _extract_acceptance_criteria(_project(tmp_path, _BULLET_SRS))
    assert got.get("FR-01"), got
    assert any("AC-FR-1-1" in c for c in got["FR-01"]), got["FR-01"]


def test_the_rendered_report_lists_them(tmp_path):
    """End of the chain: what Phase 5 delivers must carry the criteria.

    The generator is the only writer of that file, so this is the assertion
    that would have failed on all eleven projects.
    """
    from scripts.generate_verification_report import generate_verification_report

    project = _project(tmp_path, _HEADING_SRS)
    out = generate_verification_report(project)
    text = out.read_text(encoding="utf-8")
    assert "No acceptance criteria extracted" not in text, text[:600]
    assert "AC-1.1" in text and "AC-2.1" in text


def test_a_project_with_no_criteria_still_says_so(tmp_path):
    """Negative control: the fallback sentence is right when it is true.

    Removing it would turn "this SRS declares nothing" into silence, which is
    the opposite defect.
    """
    from scripts.generate_verification_report import generate_verification_report

    project = _project(tmp_path, "# SRS\n\n### FR-01: Task CRUD\n\nprose only.\n")
    out = generate_verification_report(project)
    assert "No acceptance criteria extracted" in out.read_text(encoding="utf-8")


def test_the_generator_holds_no_private_criteria_parser():
    """The fix is single-sourcing, not a better regex.

    A private regex that happens to agree today is the same defect with a
    longer fuse: this file's whole subject is two parsers drifting apart while
    both looked fine in isolation.
    """
    src = (REPO / "scripts" / "generate_verification_report.py").read_text(encoding="utf-8")
    assert "AC-FR-" not in src, (
        "the generator still carries its own acceptance-criteria pattern"
    )
    assert "srs_acceptance_criteria" in src, (
        "the generator does not call the framework's own extractor"
    )
