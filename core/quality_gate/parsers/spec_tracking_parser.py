"""
SpecTrackingParser — pure Markdown-parsing logic for SPEC_TRACKING.md.

Extracted from SpecTrackingChecker (crg-003) so that parsing concerns
are separated from spec-completeness business logic.
"""
from __future__ import annotations

import re
from typing import Dict, List


class SpecTrackingParser:
    """Stateless parser for SPEC_TRACKING.md content."""

    @staticmethod
    def has_table(content: str, table_name: str) -> bool:
        """Return True if a Markdown table with *table_name* heading exists."""
        pattern = rf"{table_name}.*\|.*\|"
        return bool(re.search(pattern, content, re.DOTALL))

    @staticmethod
    def has_update_log(content: str) -> bool:
        """Return True if an update-log table is present in *content*."""
        return "Update log" in content and "Date" in content and "|" in content

    @staticmethod
    def find_entries_without_status(content: str) -> List[str]:
        """
        Return table-row entries whose last non-empty column lacks a
        recognised status marker (✅ / ⚠️ / ❌ / Done / Pending /
        Not Implemented / DRAFT / In Progress / Not Started).
        """
        entries: List[str] = []
        _status_markers = (
            "✅", "⚠️", "❌",
            "Done", "Pending", "Not Implemented",
            "DRAFT", "IN_PROGRESS", "In Progress", "Not Started", "NOT_STARTED",
        )
        _header_markers = ("Spec", "Requirement", "Item")

        for line in content.split("\n"):
            if "|" not in line or line.strip().startswith("|"):
                continue
            parts = [p.strip() for p in line.split("|")]
            if len(parts) < 4:
                continue
            if any(x in parts[1] for x in _header_markers):
                continue
            # Find last non-empty column
            status_col = next((p for p in reversed(parts) if p), None)
            if status_col and not any(x in status_col for x in _status_markers):
                entries.append(parts[1] if len(parts) > 1 else "Unknown")
        return entries

    @staticmethod
    def count_status(content: str) -> Dict[str, int]:
        """
        Count lines containing each status emoji/keyword.

        Returns dict with keys "✅ Done", "⚠️ Pending", "❌ Not Implemented",
        "DRAFT", "IN_PROGRESS", "Not Started".  Projects that use prose status
        words instead of emoji (e.g. during Phase 1 spec tracking) are counted
        correctly so completeness is not reported as 0%.
        """
        stats: Dict[str, int] = {
            "✅ Done": 0,
            "⚠️ Pending": 0,
            "❌ Not Implemented": 0,
            "DRAFT": 0,
            "IN_PROGRESS": 0,
            "Not Started": 0,
        }
        for line in content.split("\n"):
            stripped = line.strip()
            # Only count status markers inside Markdown table rows.
            # A table row contains "|" and is not a pure separator line
            # (separators contain only "|", "-", ":", and spaces).
            if "|" not in stripped or all(c in "|-: " for c in stripped):
                continue
            if "✅" in line:
                stats["✅ Done"] += 1
            elif "⚠️" in line:
                stats["⚠️ Pending"] += 1
            elif "❌" in line:
                stats["❌ Not Implemented"] += 1
            elif "DRAFT" in line:
                stats["DRAFT"] += 1
            elif "IN_PROGRESS" in line or "In Progress" in line:
                stats["IN_PROGRESS"] += 1
            elif "Not Started" in line or "NOT_STARTED" in line:
                stats["Not Started"] += 1
        return stats
