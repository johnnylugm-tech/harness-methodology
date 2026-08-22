"""FR and NFR are two namespaces, and the matrix comparison is not a
substring search (Round 69 站4).

54651a0 fixed a real over-match — `\\bFR-\\d+\\b` pulled `FR-1` out of a
`<!-- DERIVED: SPEC §3 FR-1 -->` note and inflated the denominator — and
introduced three defects doing it:

  * ``re.match(r"(?:N)?FR-(\\d+)", heading)`` folds NFR into FR. `### NFR-05:`
    becomes `FR-05` (a requirement that does not exist) and `### NFR-03:`
    silently merges with the real `FR-03`. The regex it replaced could not do
    this: there is no word boundary inside `NFR`, so `\\bFR-` never matched
    one. Whether NFRs are covered is `preflight_traceability` 4c's question,
    and this check is named FR coverage.
  * ``heading.strip().endswith("-deferred")`` is evaluated against the whole
    heading *including its title*, so `### FR-99-deferred: out of scope` never
    matches and the deferral is counted as in-scope.
  * ``fr not in content`` is a substring test, so a `NFR-05` row in
    TRACEABILITY_MATRIX.md satisfies `FR-05`. That is why the first defect did
    not show up as a false "Missing:" — the two cancelled, and the shipped
    test's expected value (4) is the product of both.

A fourth, introduced by the same commit and not visible in its tests: the SRS
side is now zero-padded (`FR-1` → `FR-01`) and the matrix side is raw text, so
a project that writes `FR-1` in both files gets reported as missing.
"""
from __future__ import annotations

from scripts.phase_auditor import _matrix_fr_ids, _srs_in_scope_fr_ids

_SRS = "\n".join([
    "# SRS",
    "### FR-01: first",
    "### FR-02: second",
    "### FR-03: third",
    "### NFR-03: durability",
    "### NFR-05: security",
    "### FR-99-deferred: out of scope this round",
    "<!-- DERIVED: SPEC §3 FR-1 — a section number, not a requirement -->",
])


def test_an_nfr_heading_never_enters_the_fr_set() -> None:
    ids = _srs_in_scope_fr_ids(_SRS)
    assert "FR-05" not in ids, (
        "NFR-05 was read as FR-05 — a requirement the SRS never declared"
    )
    assert sorted(ids) == ["FR-01", "FR-02", "FR-03"]


def test_a_deferred_heading_with_a_title_is_excluded() -> None:
    """`### FR-99-deferred: <title>` is the shape projects actually write —
    taskq-new's SRS.md:1402 is one, verbatim."""
    assert "FR-99" not in _srs_in_scope_fr_ids(_SRS)


def test_an_nfr_row_in_the_matrix_does_not_cover_an_fr() -> None:
    matrix = "| FR-01 | api |\n| FR-02 | api |\n| NFR-05 | api |\n"
    assert _matrix_fr_ids(matrix) == {"FR-01", "FR-02"}


def test_an_unpadded_matrix_id_covers_an_unpadded_srs_id() -> None:
    """Both sides normalise, or normalising one of them invents a gap."""
    assert _srs_in_scope_fr_ids("### FR-1: one\n") == {"FR-01"}
    assert _matrix_fr_ids("| FR-1 | api |\n") == {"FR-01"}


def test_the_shipped_check_reports_the_three_in_scope_frs() -> None:
    """End to end, through the auditor's own finding text."""
    from tests.test_phase_auditor_fr_coverage_regex import _auditor

    matrix = "| FR-01 | api |\n| FR-02 | api |\n| FR-03 | api |\n"
    a = _auditor({
        "01-requirements/SRS.md": _SRS,
        "01-requirements/TRACEABILITY_MATRIX.md": matrix,
    })
    a.check_c5_content_depth()
    titles = [f.title for f in a.result.findings if "covers" in f.title]
    assert titles, "expected an FR-coverage finding"
    assert "all 3 FR(s)" in titles[0], titles[0]
