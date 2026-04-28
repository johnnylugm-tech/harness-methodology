"""
DevelopmentLogParser — pure Markdown-parsing logic for DEVELOPMENT_LOG.md.

Extracted from ABEnforcer (crg-003) so that parsing concerns are
separated from A/B-enforcement business logic.
"""
from __future__ import annotations

import re
from typing import Optional


class DevelopmentLogParser:
    """Stateless parser for DEVELOPMENT_LOG.md content."""

    @staticmethod
    def extract_phase_content(content: str, phase: str) -> Optional[str]:
        """
        Return the section of content that belongs to the given phase.

        Args:
            content: full DEVELOPMENT_LOG text
            phase:   phase identifier e.g. "phase_1" or bare "1"

        Returns:
            Matched section string, or None if phase not found.
        """
        phase_num = phase.split("_")[1] if "_" in phase else phase
        patterns = [
            rf"##\s*Phase\s*{phase_num}.*?(?=##\s*Phase|$)",
            rf"(?:#{{1,6}}\s*)?[Pp]hase\s*{phase_num}.*?(?=(?:#{{1,6}}\s*)?[Pp]hase\s*\d|$)",
        ]
        for pattern in patterns:
            match = re.search(pattern, content, re.DOTALL | re.IGNORECASE)
            if match:
                return match.group(0)
        return None

    @staticmethod
    def extract_session(content: str, role: str) -> Optional[str]:
        """
        Find a session identifier for *role* inside *content*.

        Tries explicit session-ID patterns first; falls back to inferring
        from agent-role marker text.

        Args:
            content: phase-scoped DEVELOPMENT_LOG text
            role:    role name e.g. "developer", "reviewer"

        Returns:
            Session-ID string, or None if not found.
        """
        # Patterns that capture an explicit session token (group 1 required)
        session_patterns = [
            rf"[Ss]ession[-]?[Ii][Dd][:]\s*([a-zA-Z0-9-]+)",
            rf"{role}.*?[Ss]ession[:]\s*([a-zA-Z0-9-]+)",
            rf"[Ss]ub[-]?[Aa]gent.*?{role}.*?([a-zA-Z0-9-]+)",
        ]
        for pattern in session_patterns:
            match = re.search(pattern, content, re.IGNORECASE)
            if match and match.lastindex:
                return match.group(1)

        # Fallback: infer from role-marker text (no explicit ID)
        _role_markers = {
            "developer": r"[Dd]eveloper\s*[Aa]gent",
            "reviewer":  r"[Rr]eviewer\s*[Aa]gent",
            "tester":    r"[Tt]ester\s*[Aa]gent",
            "qa":        r"[Qq][Aa]\s*[Aa]gent",
        }
        role_lower = role.lower()
        marker = _role_markers.get(role_lower)
        if marker and re.search(marker, content, re.IGNORECASE):
            return f"inferred_{role_lower}_agent"

        return None

    @staticmethod
    def normalize_session(session: str) -> str:
        """
        Normalise a session identifier for equality comparison.

        Strips non-alphanumeric characters and lowercases.
        An empty/falsy input returns "".
        """
        if not session:
            return ""
        return re.sub(r"[^a-zA-Z0-9]", "", session.lower())
