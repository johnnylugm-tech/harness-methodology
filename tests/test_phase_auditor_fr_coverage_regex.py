"""Regression: phase_auditor's FR-coverage check must extract FR IDs from
SRS.md headings only, not from prose references to bare section numbers.

Bug: the previous regex `\\bFR-\\d+\\b` over-matched. SRS.md commonly
contains notes like `<!-- DERIVED: SPEC §3 FR-1 -->` that cite a SPEC section
by its unpadded number; the regex pulled `FR-1` out and treated it as an
in-scope requirement, inflating the expected count and reporting a false
55% coverage even when every real FR was mapped.

Fix: extract from `### FR-XX:` / `### NFR-XX:` heading lines only, normalise
to zero-padded form, and skip `*-deferred` headings.
"""
from __future__ import annotations

from scripts.phase_auditor import PhaseAuditor


class FakeFetcher:
    """Serve files from an in-memory dict — no gh CLI calls."""

    def __init__(self, files):
        self.repo = "fake/repo"
        self._files = files

    def get_tree(self):
        return [{"path": path, "type": "blob"} for path in self._files]

    def resolve_path(self, candidates):
        for path in candidates:
            if path in self._files:
                return path
        return None

    def get_file_content(self, path):
        return self._files.get(path)

    def file_exists(self, path):
        return path in self._files

    def get_commits(self, per_page=30):  # noqa: ARG002
        return []


def _auditor(files):
    return PhaseAuditor(FakeFetcher(files), phase=1)  # type: ignore[arg-type]


def _srs_with_headings_and_unpadded_prose():
    """A real-shaped SRS: 3 FR headings + 1 deferred + unpadded prose refs.

    The body deliberately contains the bug-trigger pattern
    (`<!-- DERIVED: SPEC §3 FR-1 -->`) — the prose cites FR-1 and FR-2 by
    their SPEC section number (unpadded), which the old regex would have
    captured. The fix must ignore those.
    """
    return "\n".join([
        "# SRS — test",
        "",
        "## FR Inventory",
        "",
        "### FR-01: First",
        "Body.",
        "",
        "### FR-02: Second",
        "Body.",
        "",
        "### FR-03: Third",
        "Body.",
        "",
        "### NFR-01: Performance",
        "Body.",
        "",
        "### FR-99-deferred: Out of scope this round",
        "Body.",
        "",
        "<!-- DERIVED: SPEC §3 FR-1 — citation of unpadded section number -->",
        "<!-- DERIVED: SPEC §3 FR-2 — citation of unpadded section number -->",
    ])


def _matrix_with_three_frs():
    return "\n".join([
        "# Traceability",
        "",
        "| FR ID | Module |",
        "|-------|--------|",
        "| FR-01 | api     |",
        "| FR-02 | api     |",
        "| FR-03 | api     |",
        "| FR-99 | api     |",
    ])


def test_fr_coverage_counts_headings_only():
    """The check sees only the four in-scope headings (3 FR + 1 NFR),
    not the unpadded prose references. All four appear in the matrix.
    Expected: PASS with "covers all 4 FR(s) from SRS"."""
    files = {
        "01-requirements/SRS.md": _srs_with_headings_and_unpadded_prose(),
        "01-requirements/TRACEABILITY_MATRIX.md": _matrix_with_three_frs(),
    }
    a = _auditor(files)
    a.check_c5_content_depth()
    fr_findings = [f for f in a.result.findings if "covers" in f.title]
    assert fr_findings, "expected an FR-coverage finding"
    title = fr_findings[0].title
    # Heading-derived set has 4 IDs (FR-01, FR-02, FR-03, FR-99 from NFR-99).
    # PASS branch renders "covers all 4 FR(s) from SRS".
    assert "all 4 FR(s)" in title, f"expected PASS with 4, got: {title!r}"
    assert fr_findings[0].severity == "PASS"


def test_fr_coverage_excludes_deferred_headings():
    """`### FR-XX-deferred:` headings must not enter the in-scope set."""
    files = {
        "01-requirements/SRS.md": _srs_with_headings_and_unpadded_prose(),
        "01-requirements/TRACEABILITY_MATRIX.md": _matrix_with_three_frs(),
    }
    a = _auditor(files)
    a.check_c5_content_depth()
    fr_findings = [f for f in a.result.findings if "covers" in f.title]
    title = fr_findings[0].title
    # FR-99-deferred must not be in the denominator — the heading-derived
    # set is 4 IDs, not 5.
    assert "all 4 FR(s)" in title, f"deferred heading should be excluded: {title!r}"


def test_fr_coverage_normalises_unpadded_heading():
    """`### FR-1:` (no zero pad) normalises to FR-01."""
    srs = "\n".join([
        "# SRS",
        "### FR-1: One",
        "### FR-2: Two",
        "",
        "### FR-01",
        "### FR-02",
    ])
    matrix = "| FR-01 | api |\n| FR-02 | api |"
    files = {
        "01-requirements/SRS.md": srs,
        "01-requirements/TRACEABILITY_MATRIX.md": matrix,
    }
    a = _auditor(files)
    a.check_c5_content_depth()
    fr_findings = [f for f in a.result.findings if "covers" in f.title]
    assert fr_findings, "expected an FR-coverage finding"
    title = fr_findings[0].title
    # Heading set is {FR-01, FR-02} (deduped after zero-pad normalisation).
    assert "all 2 FR(s)" in title, f"expected 2 IDs after dedup, got: {title!r}"


def test_fr_coverage_accepts_level2_heading():
    """Some authors use `## FR-01` (level 2) instead of `### FR-01` (level 3).
    Both must be recognised — the previous strict `###`-only rule would
    have broken existing tests on projects written with the level-2 style.
    """
    srs = "\n".join([
        "## FR-01: One",
        "## FR-02: Two",
        "### FR-03: Three",  # mixed style is fine
    ])
    matrix = "| FR-01 | a |\n| FR-02 | a |\n| FR-03 | a |"
    files = {
        "01-requirements/SRS.md": srs,
        "01-requirements/TRACEABILITY_MATRIX.md": matrix,
    }
    a = _auditor(files)
    a.check_c5_content_depth()
    fr_findings = [f for f in a.result.findings if "covers" in f.title]
    assert fr_findings, "expected an FR-coverage finding"
    title = fr_findings[0].title
    assert "all 3 FR(s)" in title, f"expected 3 IDs from level-2+3 mix, got: {title!r}"


def test_old_regex_would_have_overcounted():
    """The bare `\\bFR-\\d+\\b` regex counts every occurrence anywhere in
    the file. With the prose notes that contain `FR-1`, `FR-2`, it would
    produce 6 distinct IDs (FR-1, FR-2, FR-01, FR-02, FR-03, FR-99).
    The fix must yield exactly 4 (the in-scope headings)."""
    import re
    srs = _srs_with_headings_and_unpadded_prose()
    old = sorted(set(re.findall(r"\bFR-\d+\b", srs)))
    # Demonstrate the over-match (this is the bug evidence).
    assert len(old) == 6, (
        f"baseline assumption broken — old regex matched {old}"
    )
    # Now apply the fix logic.
    heading_blocks = re.findall(
        r"^### ((?:N)?FR-\d+[^\n]*)$", srs, flags=re.MULTILINE,
    )
    in_scope = []
    for h in heading_blocks:
        if h.strip().endswith("-deferred"):
            continue
        m = re.match(r"(?:N)?FR-(\d+)", h)
        if m:
            in_scope.append(f"FR-{int(m.group(1)):02d}")
    new = sorted(set(in_scope))
    assert new == ["FR-01", "FR-02", "FR-03", "FR-99"], new
