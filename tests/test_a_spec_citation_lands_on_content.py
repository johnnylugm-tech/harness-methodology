"""Round 113 站4 — a `SPEC.md:N` citation must point at the line it means.

taskq-sol's SRS and SPEC_TRACKING cite the canonical spec by line, and the
citations are shifted by one: all ten SPEC_TRACKING ranges start on the blank
line before their `### FR-xx` heading (`SPEC.md:78-90` for a section that is
79-91), and SRS cites table-separator rows where it means the row below. Agent
B reported "stale SPEC line references" — and approved, because nothing in the
framework reads what a citation points at: `citation_resolves_in` reads only
`DERIVED:` tags and only checks the range, and the approval resolver treats a
blank line as valid.

The rule is the one that is decidable without understanding prose: a cited
line (the first line of a range) must exist, and must be neither blank nor a
table separator. Measured on the corpus, 972 line citations in three forms:
the rule names 61 — 58 in taskq-sol and 3 in taskq-wow, every one an
off-by-one — and nothing in the other 21 projects. A shift that lands on
another content line is not caught; that limit is recorded, not hidden.
"""

from __future__ import annotations

import ast
from pathlib import Path

from core.quality_gate.spec_citations import misplaced_spec_citations

_SPEC = "\n".join([
    "# Spec",               # 1
    "",                     # 2
    "### FR-01: Things",    # 3
    "",                     # 4
    "| a | b |",            # 5
    "|---|---|",            # 6
    "| 1 | 2 |",            # 7
]) + "\n"


def _project(tmp_path: Path, srs: str, tracking: str = "") -> Path:
    (tmp_path / "SPEC.md").write_text(_SPEC)
    req = tmp_path / "01-requirements"
    req.mkdir()
    (req / "SRS.md").write_text("# Software Requirements Specification\n" + srs)
    if tracking:
        (req / "SPEC_TRACKING.md").write_text("# Specification Tracking Matrix\n" + tracking)
    return tmp_path


def test_a_citation_on_a_blank_line_is_named(tmp_path):
    proj = _project(tmp_path, "Source: `SPEC.md:2-7`.\n")
    found = misplaced_spec_citations(proj)
    assert len(found) == 1
    assert "SPEC.md:2" in found[0] and "blank" in found[0]


def test_a_citation_on_a_table_separator_is_named(tmp_path):
    proj = _project(tmp_path, "See SPEC.md:6.\n")
    found = misplaced_spec_citations(proj)
    assert len(found) == 1 and "separator" in found[0]


def test_a_citation_past_the_end_is_named(tmp_path):
    proj = _project(tmp_path, "See SPEC.md:99.\n")
    assert len(misplaced_spec_citations(proj)) == 1


def test_citations_on_content_pass(tmp_path):
    proj = _project(
        tmp_path,
        "Source: `SPEC.md:3-7`, SPEC.md:5, SPEC.md L7, SPEC.md lines 3-5, "
        "`SPEC.md` (root) line 7.\n",
    )
    assert misplaced_spec_citations(proj) == []


def test_every_line_citation_form_is_read(tmp_path):
    # The three forms the corpus uses, each pointing at a blank line.
    proj = _project(
        tmp_path,
        "a SPEC.md:2\nb SPEC.md L4\nc SPEC.md line 2\nd `SPEC.md` (root) lines 4-7\n",
    )
    assert len(misplaced_spec_citations(proj)) == 4


def test_other_files_named_like_the_spec_are_not_read_as_it(tmp_path):
    proj = _project(tmp_path, "TEST_SPEC.md:2 and 02-architecture/TEST_SPEC.md:4\n")
    assert misplaced_spec_citations(proj) == []


def test_the_tracking_matrix_is_read_too(tmp_path):
    proj = _project(tmp_path, "", tracking="| FR-01 | Source: `SPEC.md:2-7`. |\n")
    found = misplaced_spec_citations(proj)
    assert len(found) == 1 and "SPEC_TRACKING.md" in found[0]


def test_no_spec_means_nothing_to_resolve_against(tmp_path):
    proj = _project(tmp_path, "SPEC.md:2\n")
    (proj / "SPEC.md").unlink()
    assert misplaced_spec_citations(proj) == []


def test_the_advance_blocks_on_a_misplaced_citation(tmp_path):
    from cli.advance_prechecks import _precheck_spec_citations_land_on_content
    from cli.exit_codes import EX_ADVANCE_SPEC_CITATION_OFF_CONTENT

    proj = _project(tmp_path, "Source: `SPEC.md:2-7`.\n")
    assert _precheck_spec_citations_land_on_content(1, proj) == EX_ADVANCE_SPEC_CITATION_OFF_CONTENT
    # Asked once, at the boundary that closes the phase which wrote them.
    assert _precheck_spec_citations_land_on_content(2, proj) is None


def test_advance_phase_runs_the_check() -> None:
    from tests.support.pipeline import inlined

    fn = inlined("cli/phase_cmds.py", "_advance_prechecks", helper_prefix="_precheck_")
    called = {
        n.func.attr if isinstance(n.func, ast.Attribute) else n.func.id
        for n in ast.walk(fn)
        if isinstance(n, ast.Call) and isinstance(n.func, (ast.Name, ast.Attribute))
    }
    assert "misplaced_spec_citations" in called
