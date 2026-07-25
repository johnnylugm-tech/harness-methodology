"""
SpecTrackingParser — pure Markdown-parsing logic for SPEC_TRACKING.md.

Extracted from SpecTrackingChecker (crg-003) so that parsing concerns
are separated from spec-completeness business logic.
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional


class SpecTrackingParser:
    """Stateless parser for SPEC_TRACKING.md content."""

    # Prose status tokens recognised in a resolved Status cell (case-insensitive,
    # after stripping non-word/space/hyphen characters). Includes both the
    # long-standing Phase-1/Phase-2 vocabulary (done/pending/.../approved) and
    # this project's own Status Legend (baselined/in_design/implemented/verified,
    # see 01-requirements/SPEC_TRACKING.md §2) plus "verified", which
    # spec_tracking_render.py's TraceStatus-driven refresh writes into the file.
    _PROSE_STATUSES = (
        "done", "pending", "not implemented", "in progress", "in-progress",
        "not started", "deferred", "approved",
        "baselined", "in_design", "in design", "implemented", "verified",
    )

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
    def split_row(text: str, strip_edges: bool = False) -> List[str]:
        """Split a Markdown table row on column-boundary `|`, respecting the
        `\\|` escape for a literal pipe embedded in cell content (shell
        pipe syntax, regex alternation, etc.) so it is not miscounted as an
        extra column — which previously shifted every cell after it and
        made a correctly-header-resolved status_col index point at the
        wrong cell."""
        _PIPE_SENTINEL = "\x00"
        protected = text.replace("\\|", _PIPE_SENTINEL)
        if strip_edges:
            protected = protected.strip("|")
        # Restore the escape itself (not just the pipe) — callers that
        # re-serialize cells back to disk (spec_tracking_render.py) must
        # round-trip byte-for-byte, or the escape is silently dropped and
        # the pipe becomes a real column delimiter on the next parse.
        return [p.replace(_PIPE_SENTINEL, "\\|") for p in protected.split("|")]

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
        # Phase 2: prose words ONLY in the last non-empty column.  Scanning all
        # columns for "Done" / "In Progress" etc. caused false-negatives when those
        # words appeared inside feature descriptions rather than the status column.
        # This positional heuristic is only reached for tables with no resolvable
        # header (see _iter_data_rows) — real, header-declared tables use the
        # header-based column instead, so a Status column in any position works.
        if non_empty:
            p_clean = re.sub(r"[^\w\s\-]", "", non_empty[0]).strip().lower()
            if p_clean in SpecTrackingParser._PROSE_STATUSES:
                return non_empty[0]
        return ""

    @staticmethod
    def _is_status_value(text: str) -> bool:
        """True if *text* (a single, already-located Status cell) is a
        recognised status marker/value."""
        if any(x in text for x in ("✅", "⚠️", "❌", "DRAFT", "IN_PROGRESS", "NOT_STARTED")):
            return True
        clean = re.sub(r"[^\w\s\-]", "", text).strip().lower()
        return clean in SpecTrackingParser._PROSE_STATUSES

    @staticmethod
    def _iter_data_rows(content: str):
        """Yield (line, cells, status_col, has_header) for each Markdown
        table DATA row in *content* — header and separator rows themselves
        are excluded.

        `cells` are stripped and pipe-boundary-normalised via `split_row()`
        (also used by spec_tracking_render.py::refresh_status_table), which
        respects `\\|`-escaped pipes embedded in cell content so column
        indices are stable regardless of leading/trailing "|" or escaped
        pipes inside a cell.

        `has_header` is True once a header+separator pair has been seen for
        the enclosing table. `status_col` is that header's "Status" column
        index (found by header cell name, not position) when present.

        - has_header=True, status_col=int  → use cells[status_col] directly.
        - has_header=True, status_col=None → this table has a header but no
          column literally named "Status" (e.g. an Update Log or Module
          Ownership table) — it is not a status-tracked table; callers
          should skip these rows entirely, not flag them as missing status.
        - has_header=False                 → no header was ever resolved
          for this row (header-less content fragment); callers fall back
          to _extract_status()'s positional heuristic, preserving existing
          behaviour (see TestExtractStatusLastColumnOnlyBug).
        """
        lines = content.split("\n")
        status_col: Optional[int] = None
        has_header = False
        for i, line in enumerate(lines):
            stripped = line.strip()
            if "|" not in stripped:
                status_col = None
                has_header = False
                continue
            if all(c in "|-: " for c in stripped):
                continue  # separator row — table context carries over
            cells = [c.strip() for c in SpecTrackingParser.split_row(stripped, strip_edges=True)]
            next_stripped = lines[i + 1].strip() if i + 1 < len(lines) else ""
            next_is_separator = (
                bool(next_stripped) and "|" in next_stripped
                and all(c in "|-: " for c in next_stripped)
            )
            if next_is_separator:
                # This line is a header row for the table starting below it.
                status_col = next(
                    (j for j, c in enumerate(cells) if c.lower() == "status"), None
                )
                # A "Status" column at index 0 means this table's primary/
                # identifying column IS the status value itself — e.g. a
                # "| Status | Meaning |" legend/definition table — not an
                # entity-tracking table (FR/NFR ID, ..., Status, ...) with
                # Status as an attribute. Treat as untracked so legend rows
                # (BASELINED/IN_DESIGN/... definitions) aren't counted as
                # tracked entries.
                if status_col == 0:
                    status_col = None
                has_header = True
                continue
            yield line, cells, status_col, has_header

    @staticmethod
    def find_entries_without_status(content: str) -> List[str]:
        """
        Return table-row entries whose columns lack a
        recognised status marker (✅ / ⚠️ / ❌ / Done / Pending /
        Not Implemented / DRAFT / In Progress / Not Started).
        """
        entries: List[str] = []
        _header_markers = ("Spec", "Requirement", "Item")

        for line, cells, status_col, has_header in SpecTrackingParser._iter_data_rows(content):
            if has_header:
                if status_col is None:
                    continue  # table has a header but no "Status" column — not tracked
                if len(cells) < 2 or any(x in cells[0] for x in _header_markers):
                    continue
                status_val = cells[status_col] if status_col < len(cells) else ""
                if not status_val or not SpecTrackingParser._is_status_value(status_val):
                    entries.append(cells[0])
                continue

            # No header resolved for this row's table — fall back to the
            # original positional heuristic (header-less content).
            parts = [p.strip() for p in SpecTrackingParser.split_row(line)]
            if len(parts) < 4:
                continue
            if any(x in parts[1] for x in _header_markers):
                continue
            if not SpecTrackingParser._extract_status(parts):
                entries.append(parts[1] if len(parts) > 1 else "Unknown")
        return entries

    @staticmethod
    def count_status(content: str) -> Dict[str, int]:
        """
        Count lines containing each status emoji/keyword.

        Returns dict with keys "✅ Done", "⚠️ Pending", "❌ Not Implemented",
        "DRAFT", "IN_PROGRESS", "Not Started", "Deferred", "Baselined",
        "In Design", "Implemented", "Verified".  Projects that use prose
        status words instead of emoji (e.g. during Phase 1 spec tracking)
        are counted correctly so completeness is not reported as 0%.
        """
        stats: Dict[str, int] = {
            "✅ Done": 0,
            "⚠️ Pending": 0,
            "❌ Not Implemented": 0,
            "DRAFT": 0,
            "IN_PROGRESS": 0,
            "Not Started": 0,
            "Deferred": 0,
            "Baselined": 0,
            "In Design": 0,
            "Implemented": 0,
            "Verified": 0,
        }
        for line, cells, status_col, has_header in SpecTrackingParser._iter_data_rows(content):
            if has_header:
                if status_col is None:
                    continue  # table has a header but no "Status" column — not tracked
                status_val = cells[status_col] if status_col < len(cells) else ""
            else:
                parts = [p.strip() for p in SpecTrackingParser.split_row(line)]
                status_val = SpecTrackingParser._extract_status(parts)
            status_lower = status_val.lower()

            if "✅" in status_val:
                stats["✅ Done"] += 1
            elif "⚠️" in status_val:
                stats["⚠️ Pending"] += 1
            elif "❌" in status_val:
                stats["❌ Not Implemented"] += 1
            elif "DRAFT" in status_val:
                stats["DRAFT"] += 1
            elif "in_progress" in status_lower or "in progress" in status_lower or "in-progress" in status_lower:
                stats["IN_PROGRESS"] += 1
            elif "not_started" in status_lower or "not started" in status_lower:
                stats["Not Started"] += 1
            # Bug #121: plain-text prose statuses recognised by
            # find_entries_without_status() but missing from this chain.
            # Checked after emoji branches so "✅ Done" rows are not
            # double-counted (the emoji branch matches first).
            elif "not implemented" in status_lower:
                stats["❌ Not Implemented"] += 1
            elif "verified" in status_lower:
                stats["Verified"] += 1
            elif "implemented" in status_lower:
                stats["Implemented"] += 1
            elif "in_design" in status_lower or "in design" in status_lower:
                stats["In Design"] += 1
            elif "baselined" in status_lower:
                stats["Baselined"] += 1
            elif "done" in status_lower:
                stats["✅ Done"] += 1
            elif "pending" in status_lower:
                stats["⚠️ Pending"] += 1
            elif "deferred" in status_lower:
                stats["Deferred"] += 1
        return stats
