"""
Inferential Sensor — reasoning chain quality quantification.

Evaluates whether claims' reasoning processes are sound.
Core component of HR-09 Claims Verifier.

Usage:
    from constitution.inferential_sensor import InferentialSensor

    sensor = InferentialSensor()
    result = sensor.assess(
        claim={"text": "...", "keywords": [...]},
        citations=[{"text": "...", "line": 1}, ...]
    )
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ReasoningChainAssessment:
    """Reasoning chain assessment result."""
    overall_score: float       # 0.0-1.0
    citation_existence: float  # 40% weight
    citation_coverage: float   # 30% weight
    reasoning_coherence: float # 30% weight
    issues: list[str]          # discovered issues
    recommendation: str        # recommendation


class InferentialSensor:
    """Reasoning chain quality quantifier.

    Evaluates whether claims' reasoning processes are sound.
    Core component of HR-09 Claims Verifier.
    """

    _STOP_WORDS = {
        "is", "it", "as", "at", "by", "or", "an", "be", "to", "of",
        "in", "on", "we", "us", "my", "do", "if", "so",
    }

    def __init__(self, min_coverage_threshold: float = 0.5) -> None:
        self.min_coverage_threshold = min_coverage_threshold

    def assess(self, claim: dict, citations: list[dict]) -> ReasoningChainAssessment:
        """Assess reasoning chain quality for a claim.

        Args:
            claim: {"text": "...", "keywords": [...]}
            citations: [{"text": "...", "line": 1}, ...]

        Returns:
            ReasoningChainAssessment
        """
        issues = []

        # Dimension 1: Citation existence (40%)
        citation_existence = 1.0 if citations else 0.0
        if not citations:
            issues.append("No citations provided for claim")

        # Dimension 2: Citation coverage (30%)
        citation_coverage = self._calculate_coverage(claim, citations)
        if citation_coverage < self.min_coverage_threshold:
            issues.append(f"Citation coverage too low: {citation_coverage:.1%}")

        # Dimension 3: Reasoning coherence (30%)
        reasoning_coherence = self._assess_coherence(claim, citations)

        # Weighted total
        overall_score = (
            citation_existence * 0.4
            + citation_coverage * 0.3
            + reasoning_coherence * 0.3
        )

        # Recommendation
        if overall_score >= 0.8:
            recommendation = "Claim is well-supported"
        elif overall_score >= 0.5:
            recommendation = "Claim needs additional citations or stronger reasoning"
        else:
            recommendation = "Claim is insufficiently supported — requires major revision"

        return ReasoningChainAssessment(
            overall_score=overall_score,
            citation_existence=citation_existence,
            citation_coverage=citation_coverage,
            reasoning_coherence=reasoning_coherence,
            issues=issues,
            recommendation=recommendation,
        )

    def _calculate_coverage(self, claim: dict, citations: list[dict]) -> float:
        """Calculate citation coverage using keyword overlap."""
        if not citations:
            return 0.0

        claim_keywords = set(
            kw.lower() for kw in claim.get("keywords", [])
        )
        if not claim_keywords:
            claim_text = claim.get("text", "").lower()
            claim_keywords = set(w for w in claim_text.split() if len(w) > 4)

        # Collect citation keywords
        citation_keywords: set[str] = set()
        for c in citations:
            citation_keywords.update(
                w.lower()
                for w in c.get("text", "").split()
                if len(w) > 2 and w.lower() not in self._STOP_WORDS
            )
            # Check short terms (lru, api, sql, etc.)
            for kw in claim_keywords:
                if len(kw) <= 4 and kw.lower() in c.get("text", "").lower():
                    citation_keywords.add(kw.lower())

        if not claim_keywords:
            return 0.0

        overlap = len(claim_keywords & citation_keywords)
        return min(overlap / len(claim_keywords), 1.0)

    def _assess_coherence(self, claim: dict, citations: list[dict]) -> float:
        """Assess reasoning logical coherence via keyword coverage in citations."""
        if not citations:
            return 0.0

        claim_keywords = set(kw.lower() for kw in claim.get("keywords", []))
        if not claim_keywords:
            claim_text = claim.get("text", "").lower()
            claim_keywords = set(w for w in claim_text.split() if len(w) > 2)

        citation_text = " ".join(c.get("text", "").lower() for c in citations)

        if not claim_keywords:
            return 0.0

        matched_keywords = sum(
            1 for kw in claim_keywords if kw.lower() in citation_text
        )
        return min(matched_keywords / len(claim_keywords), 1.0)


# Standalone demo
if __name__ == "__main__":
    sensor = InferentialSensor()

    claim = {
        "text": "The system uses LRU cache for performance optimization.",
        "keywords": ["lru", "cache", "performance"],
    }
    citations = [
        {"text": "The system shall use LRU cache strategy for performance optimization.", "line": 1},
        {"text": "Asyncio handles concurrent requests efficiently.", "line": 2},
    ]

    result = sensor.assess(claim, citations)
    print(f"Overall score: {result.overall_score:.2f}")
    print(f"Citation existence: {result.citation_existence:.2f}")
    print(f"Citation coverage: {result.citation_coverage:.2f}")
    print(f"Reasoning coherence: {result.reasoning_coherence:.2f}")
    print(f"Issues: {result.issues}")
    print(f"Recommendation: {result.recommendation}")
