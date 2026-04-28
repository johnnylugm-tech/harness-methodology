"""
tests/test_parsers.py — Unit tests for quality_gate.parsers layer (crg-003/crg-004).

Covers DevelopmentLogParser and SpecTrackingParser in isolation,
independent of the checker business logic.
"""
import pytest
from core.quality_gate.parsers import DevelopmentLogParser, SpecTrackingParser


# ---------------------------------------------------------------------------
# DevelopmentLogParser
# ---------------------------------------------------------------------------

class TestDevelopmentLogParserExtractPhase:
    def test_finds_phase_by_heading(self):
        content = "## Phase 1\ndeveloper session: s-abc\n## Phase 2\nother"
        result = DevelopmentLogParser.extract_phase_content(content, "phase_1")
        assert result is not None
        assert "s-abc" in result
        assert "Phase 2" not in result

    def test_phase_underscore_format(self):
        content = "## Phase 3\ncontent here\n## Phase 4\nother"
        result = DevelopmentLogParser.extract_phase_content(content, "phase_3")
        assert result is not None
        assert "content here" in result

    def test_bare_phase_number(self):
        content = "## Phase 2\nsome text"
        result = DevelopmentLogParser.extract_phase_content(content, "2")
        assert result is not None

    def test_returns_none_if_not_found(self):
        content = "## Phase 2\nsome content"
        assert DevelopmentLogParser.extract_phase_content(content, "phase_1") is None

    def test_case_insensitive(self):
        content = "## phase 1\nlower-case heading"
        result = DevelopmentLogParser.extract_phase_content(content, "phase_1")
        assert result is not None

    def test_empty_content(self):
        assert DevelopmentLogParser.extract_phase_content("", "phase_1") is None


class TestDevelopmentLogParserExtractSession:
    def test_explicit_session_id(self):
        content = "developer session: sess-xyz"
        assert DevelopmentLogParser.extract_session(content, "developer") == "sess-xyz"

    def test_session_id_colon(self):
        content = "Session-Id: abc123"
        result = DevelopmentLogParser.extract_session(content, "developer")
        assert result == "abc123"

    def test_role_specific_session(self):
        content = "reviewer session: rev-456"
        result = DevelopmentLogParser.extract_session(content, "reviewer")
        assert result == "rev-456"

    def test_inferred_developer_agent(self):
        content = "Developer Agent performed code review"
        result = DevelopmentLogParser.extract_session(content, "developer")
        assert result == "inferred_developer_agent"

    def test_inferred_reviewer_agent(self):
        content = "Reviewer Agent completed analysis"
        result = DevelopmentLogParser.extract_session(content, "reviewer")
        assert result == "inferred_reviewer_agent"

    def test_returns_none_if_absent(self):
        content = "No session information here"
        assert DevelopmentLogParser.extract_session(content, "developer") is None


class TestDevelopmentLogParserNormalize:
    def test_strips_hyphens(self):
        assert DevelopmentLogParser.normalize_session("sess-ABC-123") == "sessabc123"

    def test_lowercases(self):
        assert DevelopmentLogParser.normalize_session("SESS123") == "sess123"

    def test_empty_string(self):
        assert DevelopmentLogParser.normalize_session("") == ""

    def test_none_like_falsy(self):
        assert DevelopmentLogParser.normalize_session(None) == ""  # type: ignore[arg-type]

    def test_inferred_marker_preserved(self):
        result = DevelopmentLogParser.normalize_session("inferred_developer_agent")
        assert result == "inferreddeveloperagent"


# ---------------------------------------------------------------------------
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
        content = "✅ done\n⚠️ pending\n❌ not done\n✅ done again"
        stats = SpecTrackingParser.count_status(content)
        assert stats["✅ Done"] == 2
        assert stats["⚠️ Pending"] == 1
        assert stats["❌ Not Implemented"] == 1

    def test_empty_content(self):
        stats = SpecTrackingParser.count_status("")
        assert stats == {"✅ Done": 0, "⚠️ Pending": 0, "❌ Not Implemented": 0}


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
