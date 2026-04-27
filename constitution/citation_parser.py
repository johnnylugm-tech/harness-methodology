"""Citation Parser — extracts citations and claims from agent output text.

Supports HR-07 (citation reference standards) and HR-09 (claims accuracy).

Used by:
    steering/integrations.py :: SteeringConstitutionIntegrator

Interface contract:
    extract_citations(text: str) -> list[str]   # e.g. ["[FR-01]", "[SAD §3.2]"]
    extract_claims(text: str)    -> list[str]   # sentences with verification verbs
    verify_claim(claim, citations) -> bool      # claim is supported by citations
"""

from __future__ import annotations

import re
from typing import List


class CitationParser:
    """Regex-based citation and claims extractor."""

    # Matches: [FR-01], [NFR-03], [SAD §3.2], [SRS §5], [ADR-007], [TASK-123]
    # Also matches inline doc refs like [SAD.md#L42] or [§3.2]
    CITATION_PATTERN: re.Pattern = re.compile(
        r'\['
        r'(?:'
        r'(?:FR|NFR|SAD|SRS|ADR|TASK|ARCH|P\d)[-\s]\d+[^\]]{0,40}'
        r'|§[^\]]{1,60}'
        r'|[A-Z][^\]]{1,40}\.md(?:#[^\]]{1,30})?'
        r')'
        r'\]',
        re.IGNORECASE,
    )

    # Sentences containing verification/obligation verbs (HR-09 claims)
    CLAIM_PATTERN: re.Pattern = re.compile(
        r'(?:must|shall|should|will\s+ensure|guarantee|proves?|verif(?:y|ies)|enforce[sd]?)'
        r'[^.!?]{5,120}[.!?]',
        re.IGNORECASE,
    )

    # Keywords that indicate a claim is related to a traceable artifact
    TRACEABLE_KEYWORDS: tuple[str, ...] = (
        "fr", "nfr", "sad", "srs", "adr", "task", "arch", "phase", "gate",
        "requirement", "spec", "test", "artifact",
    )

    def extract_citations(self, text: str) -> List[str]:
        """Return all citation markers found in text."""
        return self.CITATION_PATTERN.findall(text)

    def extract_claims(self, text: str) -> List[str]:
        """Return all claim sentences found in text."""
        return self.CLAIM_PATTERN.findall(text)

    def verify_claim(self, claim: str, citations: List[str]) -> bool:
        """
        Verify that a claim is supported by at least one citation.

        Logic:
        - If no citations exist at all, claim is unverified.
        - If the claim references a traceable keyword AND citations exist,
          the claim is considered verified (conservative but practical).
        - Falls back to True if any citation is present (citations as corpus).
        """
        if not citations:
            return False

        claim_lower = claim.lower()
        has_traceable_ref = any(kw in claim_lower for kw in self.TRACEABLE_KEYWORDS)

        if has_traceable_ref:
            return True

        # Generic: at least one citation in the surrounding text acts as support
        return len(citations) >= 1
