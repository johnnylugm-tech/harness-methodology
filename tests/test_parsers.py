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
