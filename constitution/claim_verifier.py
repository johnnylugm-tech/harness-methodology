#!/usr/bin/env python3
"""
Claim Verifier — verifies claims are supported by citations.

HR-09 Claims Verifier system core.

Usage:
    from constitution.claim_verifier import verify_claims

    verified = verify_claims(
        claims=[{"id": "c1", "text": "...", "keywords": [...]}],
        citations=["SRS.md#L45"],
        artifact_content={"SRS.md": "The system shall ..."}
    )
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Dict, Any

from constitution.claim_extractor import extract_claims, claims_to_dict
from constitution.citation_parser import CitationParser
from constitution.inferential_sensor import InferentialSensor


@dataclass
class VerifiedClaim:
    """A verified claim with supporting evidence."""
    id: str
    text: str
    claim_type: str
    keywords: List[str]
    verified: bool
    supporting_citations: List[str] = field(default_factory=list)
    supporting_count: int = 0
    reason: str = ""
    reasoning_chain_score: float = 0.0


class ClaimVerifier:
    """Claims verifier integrating keyword-based verification with
    reasoning chain quality assessment.
    """

    def __init__(self, min_coverage_threshold: float = 0.5) -> None:
        self.inferential_sensor = InferentialSensor(
            min_coverage_threshold=min_coverage_threshold
        )
        self.citation_parser = CitationParser()

    def _assess_reasoning_chain(self, claim: dict, citations: list[dict]) -> float:
        """Assess reasoning chain quality.

        Args:
            claim: {"text": "...", "keywords": [...]}
            citations: [{"text": "...", "line": 1}, ...]

        Returns:
            overall_score: 0.0-1.0
        """
        result = self.inferential_sensor.assess(claim, citations)
        return result.overall_score

    def verify_claims(
        self,
        claims: List[Dict[str, Any]],
        citations: List[str],
        artifact_content: Dict[str, str],
        strict: bool = True,
    ) -> List[VerifiedClaim]:
        """Verify claims against citation content.

        Algorithm:
        1. For each claim, check if its keywords appear in cited artifact content
        2. If any keyword matches → verified = True (non-strict)
        3. If strict=True, all keywords must match

        Args:
            claims: Claims from claim_extractor.
            citations: Citation strings.
            artifact_content: {artifact_name: content} dict.
            strict: True = all keywords must match, False = any match.

        Returns:
            List of VerifiedClaim objects.
        """
        verified_claims = []

        # Parse citations
        parsed_citations = [
            {
                "artifact": cite.split("#")[0] if "#" in cite else cite,
                "line": cite.split("#")[1] if "#" in cite else "1",
            }
            for cite in citations
        ] if citations else []

        for claim in claims:
            claim_id = claim.get("id", "")
            claim_text = claim.get("text", "")
            claim_type = claim.get("claim_type", "unknown")
            keywords = claim.get("keywords", [])

            if not keywords:
                verified_claims.append(VerifiedClaim(
                    id=claim_id, text=claim_text, claim_type=claim_type,
                    keywords=keywords, verified=False, reason="No keywords to verify",
                ))
                continue

            supporting = []
            missing_keywords = []

            for keyword in keywords:
                keyword_lower = keyword.lower()
                found_in = []

                for parsed_cite in parsed_citations:
                    artifact_name = parsed_cite["artifact"]
                    content = artifact_content.get(artifact_name, "")

                    if content and keyword_lower in content.lower():
                        found_in.append(artifact_name)

                if found_in:
                    supporting.extend(found_in)
                else:
                    missing_keywords.append(keyword)

            supporting = list(set(supporting))

            if strict:
                verified = len(missing_keywords) == 0
                reason = (
                    f"All keywords found in {len(supporting)} artifacts"
                    if verified
                    else f"Missing keywords: {missing_keywords}"
                )
            else:
                verified = len(supporting) > 0
                reason = (
                    f"Keywords found in {len(supporting)} artifacts"
                    if verified
                    else f"No keywords found: {missing_keywords}"
                )

            # Assess reasoning chain quality
            reasoning_score = self._assess_reasoning_chain(
                {"text": claim_text, "keywords": keywords},
                [
                    {
                        "text": artifact_content.get(pc["artifact"], ""),
                        "line": int(pc.get("line", 1)),
                    }
                    for pc in parsed_citations
                ],
            )

            verified_claims.append(VerifiedClaim(
                id=claim_id,
                text=claim_text,
                claim_type=claim_type,
                keywords=keywords,
                verified=verified,
                supporting_citations=supporting,
                supporting_count=len(supporting),
                reason=reason,
                reasoning_chain_score=reasoning_score,
            ))

        return verified_claims

    def verify_result(
        self,
        result_text: str,
        citations: List[str],
        artifact_content: Dict[str, str],
        strict: bool = True,
    ) -> Dict[str, Any]:
        """Convenience function: extract and verify claims directly from result text.

        Args:
            result_text: Subagent result text.
            citations: Citation strings.
            artifact_content: {artifact_name: content}.
            strict: Strict mode.

        Returns:
            {
                "total_claims": N,
                "verified_claims": N,
                "unverified_claims": N,
                "verification_rate": 0.0-1.0,
                "verified": bool,
                "claims": [...]
            }
        """
        claims = extract_claims(result_text)
        claims_dict = claims_to_dict(claims)

        verified = self.verify_claims(claims_dict, citations, artifact_content, strict=strict)

        total = len(verified)
        verified_count = sum(1 for c in verified if c.verified)

        return {
            "total_claims": total,
            "verified_claims": verified_count,
            "unverified_claims": total - verified_count,
            "verification_rate": verified_count / total if total > 0 else 1.0,
            "verified": verified_count == total if strict else verified_count > 0,
            "claims": [
                {
                    "id": c.id,
                    "text": c.text,
                    "claim_type": c.claim_type,
                    "verified": c.verified,
                    "supporting_citations": c.supporting_citations,
                    "supporting_count": c.supporting_count,
                    "reason": c.reason,
                }
                for c in verified
            ],
        }


# Module-level functions for backward compatibility


def verify_claims(
    claims: List[Dict[str, Any]],
    citations: List[str],
    artifact_content: Dict[str, str],
    strict: bool = True,
) -> List[VerifiedClaim]:
    """Verify claims against citations (module-level, backward-compatible)."""
    verifier = ClaimVerifier()
    return verifier.verify_claims(claims, citations, artifact_content, strict)


def verify_result(
    result_text: str,
    citations: List[str],
    artifact_content: Dict[str, str],
    strict: bool = True,
) -> Dict[str, Any]:
    """Extract and verify claims from result text (module-level, backward-compatible)."""
    verifier = ClaimVerifier()
    return verifier.verify_result(result_text, citations, artifact_content, strict)


def verification_to_dict(verified: List[VerifiedClaim]) -> List[Dict[str, Any]]:
    """Convert VerifiedClaim list to dict."""
    return [
        {
            "id": c.id,
            "text": c.text,
            "claim_type": c.claim_type,
            "keywords": c.keywords,
            "verified": c.verified,
            "supporting_citations": c.supporting_citations,
            "supporting_count": c.supporting_count,
            "reason": c.reason,
        }
        for c in verified
    ]


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--help":
        print("Claim Verifier - verifies claims are supported by citations")
        print("Usage: python claim_verifier.py")
        print("Or:    from constitution.claim_verifier import verify_claims, verify_result")
    else:
        demo_text = (
            "Implement LRU cache strategy based on SRS.md §4.2, "
            "using asyncio for performance."
        )
        demo_citations = ["SRS.md#L45"]
        demo_artifacts = {
            "SRS.md": (
                "The system shall use LRU cache strategy for performance optimization. "
                "Asyncio will handle concurrent requests."
            )
        }

        result = verify_result(demo_text, demo_citations, demo_artifacts)
        print("Verification result:")
        print(f"  total_claims: {result['total_claims']}")
        print(f"  verified_claims: {result['verified_claims']}")
        print(f"  verification_rate: {result['verification_rate']:.2f}")
        print(f"  verified: {result['verified']}")
        for claim in result["claims"]:
            print(f"  - [{claim['claim_type']}] {claim['text']}")
            print(f"      verified={claim['verified']}, reason={claim['reason']}")
