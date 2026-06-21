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
    def _extract_status(parts: List[str]) -> str:
        non_empty = [p for p in reversed(parts) if p]
        # Phase 1: scan all columns for emoji / abbreviated codes.  These are
        # distinctive enough (✅ / ⚠️ / ❌ / DRAFT / IN_PROGRESS …) to be safe
        # to match in any column, which handles tables like "| FR | ✅ Done | note |"
        # where the status column is not the last one.
        for p in non_empty:
            if any(x in p for x in ("✅", "⚠️", "❌", "DRAFT", "IN_PROGRESS", "NOT_STARTED")):
                return p
        # Phase 2: prose words in ANY column. The status column may not be the last column.
        for p in non_empty:
            p_clean = re.sub(r"[^\w\s\-]", "", p).strip().lower()
            if p_clean in ("done", "pending", "not implemented", "in progress", "in-progress", "not started", "deferred"):
                return p
        return ""

    @staticmethod
    def find_entries_without_status(content: str) -> List[str]:
        """
        Return table-row entries whose columns lack a
        recognised status marker (✅ / ⚠️ / ❌ / Done / Pending /
        Not Implemented / DRAFT / In Progress / Not Started).
        """
        entries: List[str] = []
        _header_markers = ("Spec", "Requirement", "Item", "FR ID", "NFR ID", "Title")

        for line in content.split("\n"):
            stripped = line.strip()
            # Ignore if it doesn't contain "|" or is just a separator line
            if "|" not in stripped or all(c in "|-: " for c in stripped):
                continue
            
            parts = [p.strip() for p in line.split("|")]
            if len(parts) < 4:
                continue
            # Check if this row is a header row by seeing if it contains header names
            if any(x.lower() in parts[1].lower() or x.lower() in parts[2].lower() for x in _header_markers):
                continue
            
            status_col = SpecTrackingParser._extract_status(parts)
            if not status_col:
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
            parts = [p.strip() for p in line.split("|")]
            status_col = SpecTrackingParser._extract_status(parts)

            if "✅" in status_col:
                stats["✅ Done"] += 1
            elif "⚠️" in status_col:
                stats["⚠️ Pending"] += 1
            elif "❌" in status_col:
                stats["❌ Not Implemented"] += 1
            elif "DRAFT" in status_col:
                stats["DRAFT"] += 1
            elif "IN_PROGRESS" in status_col or "In Progress" in status_col or "In-Progress" in status_col:
                stats["IN_PROGRESS"] += 1
            elif "Not Started" in status_col or "NOT_STARTED" in status_col:
                stats["Not Started"] += 1
            # Bug #121: plain-text prose statuses recognised by
            # find_entries_without_status() but missing from this chain.
            # Checked after emoji branches so "✅ Done" rows are not
            # double-counted (the emoji branch matches first).
            elif "Not Implemented" in status_col:
                stats["❌ Not Implemented"] += 1
            elif "Done" in status_col:
                stats["✅ Done"] += 1
            elif "Pending" in status_col:
                stats["⚠️ Pending"] += 1
            elif "Deferred" in status_col:
                if "Deferred" not in stats:
                    stats["Deferred"] = 0
                stats["Deferred"] += 1
        return stats
