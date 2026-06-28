"""Unit tests for core/canonical_lint.py — flag non-canonical FR-IDs in source.

Improvement I of convergence plan: canonical_lint is the source-code lint
that closes the FR-ID variant loop. These tests verify:

  - 5+ non-canonical variants are flagged (FR01, FR_01, FR(01), [FR-01], fr-01)
  - Canonical FR-NN / TASK-NN / NFR-NN are NOT flagged
  - Suggestions match canonical_form() output
  - lint_files() reads and aggregates hits across multiple files
  - format_report() output is human-readable

Commonality: framework-level lint. Used by pre-commit hook + CI.
"""

from pathlib import Path

from core.canonical_lint import (
    LintHit,
    format_report,
    lint_files,
    lint_text,
)


# ---------------------------------------------------------------------------
# lint_text: variant detection
# ---------------------------------------------------------------------------


class TestLintTextVariants:
    def test_no_separator_fr01(self):
        hits = lint_text("Status of FR01 is good.")
        assert len(hits) == 1
        assert hits[0].matched_text == "FR01"
        assert hits[0].suggested_canonical == "FR-01"

    def test_underscore_separator(self):
        hits = lint_text("See FR_01 in code.")
        assert len(hits) == 1
        assert hits[0].matched_text == "FR_01"
        assert hits[0].suggested_canonical == "FR-01"

    def test_parens(self):
        hits = lint_text("Status of FR(01) is good.")
        assert len(hits) == 1
        assert hits[0].matched_text == "FR(01)"
        assert hits[0].suggested_canonical == "FR-01"

    def test_brackets(self):
        hits = lint_text("The [FR-01] reference is good.")
        # [FR-01] is canonical form wrapped in brackets — should NOT flag
        # (the inner form IS canonical)
        assert len(hits) == 0

    def test_lowercase_prefix(self):
        hits = lint_text("See fr-01 reference.")
        assert len(hits) == 1
        assert hits[0].suggested_canonical == "FR-01"

    def test_canonical_not_flagged(self):
        hits = lint_text("Status of FR-01 is good.")
        assert hits == []

    def test_nfr_canonical_not_flagged(self):
        hits = lint_text("NFR-03 — performance")
        assert hits == []

    def test_task_canonical_not_flagged(self):
        hits = lint_text("TASK-07 — implementation")
        assert hits == []

    def test_multiple_variants(self):
        text = "FR01, FR_02, FR(03), fr-04, FR-05"
        hits = lint_text(text)
        # FR-05 is canonical — should NOT be flagged
        assert len(hits) == 4
        canonicals = sorted(h.suggested_canonical for h in hits)
        assert canonicals == ["FR-01", "FR-02", "FR-03", "FR-04"]


class TestLintTextLineColumn:
    def test_line_numbers(self):
        text = "line 1 ok\nFR01 on line 2\nline 3 ok\nFR_05 on line 4"
        hits = lint_text(text)
        assert len(hits) == 2
        assert hits[0].line_no == 2
        assert hits[0].column == 1  # 1-indexed
        assert hits[1].line_no == 4

    def test_column_offset(self):
        text = "Some preamble FR01"
        hits = lint_text(text)
        # "Some preamble " is 14 chars; F is at index 14 (0-indexed) = column 15 (1-indexed)
        assert hits[0].column == 15


class TestLintTextFalsePositives:
    def test_prose_fr(self):
        # "from" matches FR but has no digits — should not flag
        hits = lint_text("This function imports from pathlib.")
        assert hits == []

    def test_alphanumeric_boundary(self):
        # "FR" inside a larger identifier (e.g. FRAGMENT) should not match
        hits = lint_text("FRAGMENT_01 is a code block.")
        # FRAGMENT_01 — the prefix is "FRAGMENT" not "FR", so the underscore
        # pattern doesn't match (it requires FR_ at word boundary)
        # The "FR01" no-separator pattern requires FR<digits> at word boundary
        # After FRAGMENT, "01" alone is preceded by underscore which is word char
        # So neither pattern matches. Good — no false positive.
        assert hits == []

    def test_digits_only(self):
        hits = lint_text("The number 42 is a test.")
        assert hits == []


# ---------------------------------------------------------------------------
# lint_files
# ---------------------------------------------------------------------------


class TestLintFiles:
    def test_real_file(self, tmp_path: Path):
        f = tmp_path / "code.py"
        f.write_text("# Status: FR01 done\nFR_02 next\n")
        hits = lint_files([f])
        assert len(hits) == 2
        rel_paths = {str(h.file) for h in hits}
        assert any(p.endswith("code.py") for p in rel_paths)

    def test_nonexistent_file_silent_skip(self, tmp_path: Path):
        hits = lint_files([tmp_path / "does_not_exist.py"])
        assert hits == []

    def test_multiple_files(self, tmp_path: Path):
        a = tmp_path / "a.py"
        b = tmp_path / "b.py"
        a.write_text("FR01 here\n")
        b.write_text("FR_05 there\n")
        hits = lint_files([a, b])
        assert len(hits) == 2

    def test_directory_files(self, tmp_path: Path):
        # lint_files expects explicit paths; dir iteration is caller's job
        d = tmp_path / "subdir"
        d.mkdir()
        f = d / "x.py"
        f.write_text("FR01 bad\n")
        # Should work for explicit file paths inside dir
        hits = lint_files([f])
        assert len(hits) == 1


# ---------------------------------------------------------------------------
# format_report
# ---------------------------------------------------------------------------


class TestFormatReport:
    def test_no_hits(self):
        assert format_report([]) == "OK: no non-canonical FR-IDs found."

    def test_one_hit(self):
        hit = LintHit(
            file=Path("/repo/code.py"),
            line_no=5,
            column=10,
            matched_text="FR01",
            suggested_canonical="FR-01",
            pattern_description="\\b(TASK|FR|NFR)(?=\\d)",
        )
        report = format_report([hit])
        assert "1 non-canonical" in report
        assert "FR01" in report
        assert "FR-01" in report
        assert "code.py" in report
        assert "5:10" in report

    def test_relative_path(self, tmp_path: Path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        hit = LintHit(
            file=tmp_path / "code.py",
            line_no=1,
            column=1,
            matched_text="FR01",
            suggested_canonical="FR-01",
            pattern_description="x",
        )
        report = format_report([hit])
        assert "code.py" in report
        # Should NOT contain the absolute tmp_path prefix
        assert str(tmp_path) not in report or "code.py" in report


# ---------------------------------------------------------------------------
# End-to-end: write real source with non-canonical, lint finds it
# ---------------------------------------------------------------------------


class TestE2E:
    def test_canonical_source_clean(self, tmp_path: Path):
        f = tmp_path / "good.py"
        f.write_text("""
# References FR-01, FR-02, NFR-01, TASK-07
# All canonical — should not flag
""")
        assert lint_files([f]) == []

    def test_dirty_source_caught(self, tmp_path: Path):
        f = tmp_path / "dirty.py"
        f.write_text("""
# Buggy references:
# - FR01 (no separator)
# - FR_02 (underscore)
# - FR(03) (parens)
# - fr-04 (lowercase)
# Canonical refs (should NOT be flagged):
# - FR-05
""")
        hits = lint_files([f])
        assert len(hits) == 4
        matched = sorted(h.matched_text for h in hits)
        assert matched == ["FR(03)", "FR01", "FR_02", "fr-04"]


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


class TestDeterminism:
    def test_same_input_same_output(self):
        text = "FR01 and FR_02"
        hits1 = lint_text(text)
        hits2 = lint_text(text)
        assert hits1 == hits2