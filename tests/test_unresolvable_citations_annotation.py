"""
tests/test_unresolvable_citations_annotation.py — Regression tests for the
`(annotation)` citation-suffix accepted by `unresolvable_citations`.

Background: round 27 (2026-08-03). The validator's `_CITATION` regex used `$`
to anchor the end of the string, so an Agent B citation of the form
`SRS.md:972 (FR-05 §10 verification array missing AC-05-6)` failed to match
and fell through to the whole-file path resolution branch, which tried to
resolve the whole string as a filename and reported "no such file" for every
existing file. The run-all-by-workflow Phase 1 → advance-phase blocked on
this. The regex was extended to accept an optional trailing `(annotation)`.
These tests pin the contract.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.quality_gate.agent_b_approvals import unresolvable_citations


# ---------------------------------------------------------------------------
# Fixture: a tmp project containing the four Phase 1 deliverables with line
# counts large enough to exercise the cited ranges.
# ---------------------------------------------------------------------------

@pytest.fixture
def project_with_deliverables(tmp_path: Path) -> Path:
    (tmp_path / "01-requirements").mkdir()
    # 80 lines is enough for every cite used in these tests.
    (tmp_path / "01-requirements" / "SRS.md").write_text(
        "\n".join(f"SRS line {i}" for i in range(1, 81)) + "\n",
        encoding="utf-8",
    )
    (tmp_path / "01-requirements" / "TRACEABILITY_MATRIX.md").write_text(
        "\n".join(f"TM line {i}" for i in range(1, 81)) + "\n",
        encoding="utf-8",
    )
    (tmp_path / "TEST_INVENTORY.yaml").write_text(
        "\n".join(f"TI line {i}" for i in range(1, 81)) + "\n",
        encoding="utf-8",
    )
    return tmp_path


# ---------------------------------------------------------------------------
# Annotation suffix MUST be accepted (the round-27 fix)
# ---------------------------------------------------------------------------

class TestAnnotationSuffixAccepted:
    def test_single_line_with_annotation(self, project_with_deliverables: Path):
        # The exact shape Agent B emitted in the run-all round-27 P1 failure.
        bad = unresolvable_citations(
            project_with_deliverables,
            ["01-requirements/SRS.md:50 (FR-05 §10 verification array missing AC-05-6)"],
        )
        assert bad == []

    def test_line_range_with_annotation(self, project_with_deliverables: Path):
        bad = unresolvable_citations(
            project_with_deliverables,
            ["01-requirements/SRS.md:40-60 (block of 21 lines verifying FR-05)"],
        )
        assert bad == []

    def test_root_level_yaml_with_annotation(self, project_with_deliverables: Path):
        # TEST_INVENTORY.yaml lives at root, but Agent B prefixed `01-requirements/`
        # (the basename-fallback resolver still locates it).
        bad = unresolvable_citations(
            project_with_deliverables,
            ["01-requirements/TEST_INVENTORY.yaml:10-30 (FR-05 test block)"],
        )
        assert bad == []

    def test_line_colon_col_with_annotation(self, project_with_deliverables: Path):
        # Legacy `path:N:M` form, with annotation.
        bad = unresolvable_citations(
            project_with_deliverables,
            ["01-requirements/SRS.md:20:5 (column hint)"],
        )
        assert bad == []

    def test_bare_filename_with_annotation(self, project_with_deliverables: Path):
        bad = unresolvable_citations(
            project_with_deliverables,
            ["SRS.md:1 (header)"],
        )
        assert bad == []


# ---------------------------------------------------------------------------
# Annotation suffix MUST NOT mask the existing correctness checks
# ---------------------------------------------------------------------------

class TestAnnotationSuffixDoesNotMaskChecks:
    def test_nonexistent_file_with_annotation_still_fails(
        self, project_with_deliverables: Path
    ):
        bad = unresolvable_citations(
            project_with_deliverables,
            ["99-requirements/DOES_NOT_EXIST.md:1 (annotation)"],
        )
        assert len(bad) == 1
        assert "no such file" in bad[0]

    def test_out_of_range_line_with_annotation_still_fails(
        self, project_with_deliverables: Path
    ):
        # SRS.md has 80 lines; line 999 is past the end.
        bad = unresolvable_citations(
            project_with_deliverables,
            ["01-requirements/SRS.md:999 (annotation)"],
        )
        assert len(bad) == 1
        assert "999" in bad[0]
        assert "lines" in bad[0] or "exceeds" in bad[0]

    def test_inverted_range_with_annotation_still_fails(
        self, project_with_deliverables: Path
    ):
        # end (5) < start (50); annotation does not rescue this.
        bad = unresolvable_citations(
            project_with_deliverables,
            ["01-requirements/SRS.md:50-5 (annotation)"],
        )
        assert len(bad) == 1
        assert "before start" in bad[0]

    def test_unparenthesised_trailing_prose_still_rejected(
        self, project_with_deliverables: Path
    ):
        # Bare trailing prose (no parentheses) must NOT be silently swallowed;
        # it has to fall through to the no-such-file branch as before.
        bad = unresolvable_citations(
            project_with_deliverables,
            ["01-requirements/SRS.md:50 free text after the line number"],
        )
        # The validator must surface this as a bad citation, not pretend it
        # parsed cleanly. (It will be flagged as "no such file" because the
        # whole string is treated as a path.)
        assert len(bad) == 1


# ---------------------------------------------------------------------------
# Mixed list — one valid annotation cite plus one bogus one
# ---------------------------------------------------------------------------

class TestMixedCitationList:
    def test_mixed_list_reports_only_the_bad_one(
        self, project_with_deliverables: Path
    ):
        bad = unresolvable_citations(
            project_with_deliverables,
            [
                "01-requirements/SRS.md:50 (annotation, fine)",
                "01-requirements/SRS.md:9999 (annotation, but line too large)",
            ],
        )
        assert len(bad) == 1
        assert "9999" in bad[0]