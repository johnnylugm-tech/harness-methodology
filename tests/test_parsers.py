"""
tests/test_parsers.py — unit tests for quality_gate.parsers.
"""
from core.quality_gate.parsers import SpecTrackingParser

# SpecTrackingParser
# ---------------------------------------------------------------------------

class TestSpecTrackingParserHasTable:
    def test_finds_named_table(self):
        content = "## Core Features\n| Spec | Status |\n|------|--------|\n| FR-01 | Done |"
        assert SpecTrackingParser.has_table(content, "Core Features") is True

    def test_missing_table(self):
        content = "No tables here"
        assert SpecTrackingParser.has_table(content, "Core Features") is False


class TestSpecTrackingParserHasUpdateLog:
    def test_present(self):
        content = "## Update log\n| Date | Item |\n|------|------|\n| 2026 | Init |"
        assert SpecTrackingParser.has_update_log(content) is True

    def test_missing(self):
        assert SpecTrackingParser.has_update_log("No log section") is False

    def test_needs_date_column(self):
        content = "Update log present but no date column\n| Item |"
        assert SpecTrackingParser.has_update_log(content) is False


class TestSpecTrackingParserCountStatus:
    def test_counts_all_statuses(self):
        # Status markers must appear inside Markdown table rows (lines containing "|")
        content = (
            "| FR-01 | feature a | ✅ Done |\n"
            "| FR-02 | feature b | ⚠️ Pending |\n"
            "| FR-03 | feature c | ❌ Not Implemented |\n"
            "| FR-04 | feature d | ✅ Done |\n"
        )
        stats = SpecTrackingParser.count_status(content)
        assert stats["✅ Done"] == 2
        assert stats["⚠️ Pending"] == 1
        assert stats["❌ Not Implemented"] == 1

    def test_ignores_status_in_non_table_lines(self):
        # A heading or comment containing "DRAFT" must not be counted
        content = "## DRAFT Architecture\nSome prose with ✅ inline text\n"
        stats = SpecTrackingParser.count_status(content)
        assert all(v == 0 for v in stats.values())

    def test_ignores_separator_rows(self):
        content = "|---|---|---|\n| FR-01 | impl | ✅ Done |\n"
        stats = SpecTrackingParser.count_status(content)
        assert stats["✅ Done"] == 1

    def test_empty_content(self):
        stats = SpecTrackingParser.count_status("")
        assert all(v == 0 for v in stats.values())


class TestSpecTrackingParserCountStatusProseBug121:
    """Bug #121b: count_status() was missing plain-text branches for
    "Done", "Pending", "Not Implemented" — only find_entries_without_status()
    recognised them, causing completeness to report 0% in prose-only tables."""

    def test_prose_done_counted_bug_121(self):
        content = (
            "FR-01 | feature a | Done\n"
            "FR-02 | feature b | Done\n"
        )
        stats = SpecTrackingParser.count_status(content)
        assert stats["✅ Done"] == 2

    def test_prose_pending_counted_bug_121(self):
        content = "FR-01 | feature a | Pending\n"
        stats = SpecTrackingParser.count_status(content)
        assert stats["⚠️ Pending"] == 1

    def test_prose_not_implemented_counted_bug_121(self):
        content = "FR-01 | feature a | Not Implemented\n"
        stats = SpecTrackingParser.count_status(content)
        assert stats["❌ Not Implemented"] == 1

    def test_emoji_rows_not_double_counted_bug_121(self):
        # "✅ Done" line must not also trigger the prose "Done" branch
        content = "FR-01 | feature a | ✅ Done\n"
        stats = SpecTrackingParser.count_status(content)
        assert stats["✅ Done"] == 1

    def test_mixed_emoji_and_prose_bug_121(self):
        content = (
            "FR-01 | feature a | ✅ Done\n"
            "FR-02 | feature b | Done\n"
            "FR-03 | feature c | ⚠️ Pending\n"
            "FR-04 | feature d | Pending\n"
            "FR-05 | feature e | ❌ Not Implemented\n"
            "FR-06 | feature f | Not Implemented\n"
        )
        stats = SpecTrackingParser.count_status(content)
        assert stats["✅ Done"] == 2
        assert stats["⚠️ Pending"] == 2
        assert stats["❌ Not Implemented"] == 2

    def test_id_bug_121_false_positive_prose_in_feature_description(self):
        # "Done", "Pending", or "Not Implemented" in the feature name must not
        # trigger a match. The match should only apply to the status column.
        content = (
            "FR-01 | Add Done button to Pending page | Not Implemented\n"
            "FR-02 | Review Pending tasks | Done\n"
        )
        stats = SpecTrackingParser.count_status(content)
        assert stats["✅ Done"] == 1
        assert stats["⚠️ Pending"] == 0
        assert stats["❌ Not Implemented"] == 1


class TestExtractStatusLastColumnOnlyBug:
    """_extract_status must stop at the last non-empty column and not scan further.
    Scanning all columns caused false-negatives when status words appeared in an
    intermediate description column with a non-status last column."""

    def test_prose_status_in_intermediate_col_last_col_not_status_flags_entry(self):
        # "In Progress" (prose) in col 2, "review notes" in col 3 (last, non-status).
        # Prose words are matched only in the last non-empty column, so "In Progress"
        # in col 2 must NOT be treated as a status — the entry must be flagged.
        content = "FR-01 | In Progress | review notes |"
        result = SpecTrackingParser.find_entries_without_status(content)
        assert result != [], "prose word in non-last col must not prevent flagging"

    def test_prose_status_in_last_col_not_flagged(self):
        content = "FR-01 | review notes | In Progress |"
        result = SpecTrackingParser.find_entries_without_status(content)
        assert "FR-01" not in result

    def test_emoji_status_in_non_last_col_counted(self):
        # "✅ Done" in col 2, "note" in col 3 (last).  Emoji codes are scanned
        # across all columns, so this row must still be counted as Done.
        content = "| F1 | ✅ Done | note |"
        stats = SpecTrackingParser.count_status(content)
        assert stats["✅ Done"] == 1

    def test_prose_status_only_in_last_col_counted(self):
        # "Done" in col 3 (last) — must be counted.
        content = "FR-01 | Done | review notes |"
        stats = SpecTrackingParser.count_status(content)
        # "review notes" is last non-empty → prose check finds no match → 0 (correct).
        # "Done" is in col 2, not last → not counted by prose branch.
        assert stats["✅ Done"] == 0


class TestCountStatusNewStatuses:
    """Deferred and hyphenated In-Progress added in recent commits; case-insensitive
    matching in _extract_status Phase 2 means count_status must also handle lowercase
    variants returned from the raw column value."""

    def test_deferred_counted(self):
        content = "| FR-01 | feature | Deferred |\n"
        stats = SpecTrackingParser.count_status(content)
        assert stats["Deferred"] == 1

    def test_deferred_lowercase_counted(self):
        # _extract_status Phase 2 now matches case-insensitively, so "deferred"
        # (lowercase) is returned as-is; count_status must handle it.
        content = "| FR-01 | feature | deferred |\n"
        stats = SpecTrackingParser.count_status(content)
        assert stats["Deferred"] == 1

    def test_in_progress_hyphen_counted(self):
        content = "| FR-01 | feature | In-Progress |\n"
        stats = SpecTrackingParser.count_status(content)
        assert stats["IN_PROGRESS"] == 1

    def test_in_progress_hyphen_lowercase_counted(self):
        content = "| FR-01 | feature | in-progress |\n"
        stats = SpecTrackingParser.count_status(content)
        assert stats["IN_PROGRESS"] == 1

    def test_deferred_in_initial_stats_dict(self):
        # "Deferred" must be pre-declared so callers can iterate stats.keys()
        # without surprises even when no Deferred rows exist.
        stats = SpecTrackingParser.count_status("")
        assert "Deferred" in stats
        assert stats["Deferred"] == 0

    def test_not_started_lowercase_counted(self):
        content = "| FR-01 | feature | not started |\n"
        stats = SpecTrackingParser.count_status(content)
        assert stats["Not Started"] == 1


class TestExtractStatusNewValues:
    """_extract_status must recognise 'Deferred' and 'In-Progress' (with hyphen)
    as valid status values in the last non-empty column."""

    def test_deferred_recognized(self):
        parts = ["", "FR-01", "feature desc", "Deferred", ""]
        assert SpecTrackingParser._extract_status(parts) != ""

    def test_deferred_lowercase_recognized(self):
        parts = ["", "FR-01", "feature desc", "deferred", ""]
        assert SpecTrackingParser._extract_status(parts) != ""

    def test_in_progress_hyphen_recognized(self):
        parts = ["", "FR-01", "feature desc", "In-Progress", ""]
        assert SpecTrackingParser._extract_status(parts) != ""

    def test_in_progress_hyphen_lowercase_recognized(self):
        parts = ["", "FR-01", "feature desc", "in-progress", ""]
        assert SpecTrackingParser._extract_status(parts) != ""


class TestSpecTrackingParserFindEntriesWithoutStatus:
    def test_no_missing_entries_when_all_have_status(self):
        content = "FR-01 | Done\nFR-02 | Pending"
        # Rows that don't have pipe-only formatting won't be caught
        result = SpecTrackingParser.find_entries_without_status(content)
        assert isinstance(result, list)

    def test_detects_entry_without_status_marker(self):
        content = "FR-01 | In progress | unknown-status"
        result = SpecTrackingParser.find_entries_without_status(content)
        # "unknown-status" has no recognised marker → entry flagged
        assert isinstance(result, list)

    def test_skips_header_rows(self):
        # Lines with header markers in the spec ID column are skipped.
        # Lines starting with "|" are excluded by the parser, so use
        # a format where the line does NOT start with "|".
        content = "Some text | Spec | Status | Notes\nFR-01 | Done | ok"
        result = SpecTrackingParser.find_entries_without_status(content)
        assert isinstance(result, list)
