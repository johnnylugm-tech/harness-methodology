#!/usr/bin/env python3
"""
Claim Extractor — extracts verifiable claims from subagent result text.

HR-09 Claims Verifier system component.

Usage:
    from constitution.claim_extractor import extract_claims

    claims = extract_claims(
        "Implement LRU cache strategy based on SRS.md §4.2"
    )
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional as _Optional


@dataclass
class Claim:
    """A verifiable claim."""
    id: str
    text: str
    keywords: List[str] = field(default_factory=list)
    pattern_matched: str = ""
    claim_type: str = "unknown"  # design_decision / implementation / reasoning / assumption


# Claim extraction patterns
CLAIM_PATTERNS = [
    # Design decisions
    (r'使用(\w+[演算法|策略|模式|機制])', 'design_decision'),
    (r'基於(\w+)', 'design_decision'),
    (r'遵循(\w+[設計|規格|標準])', 'design_decision'),
    (r'採用(\w+)', 'design_decision'),

    # Implementation choices
    (r'透過(\w+)', 'implementation'),
    (r'使用(\w+)來', 'implementation'),
    (r'以(\w+)實現', 'implementation'),
    (r'用(\w+)處理', 'implementation'),

    # Reasoning
    (r'由於(\w+)', 'reasoning'),
    (r'因為(\w+)', 'reasoning'),
    (r'根據(\w+)', 'reasoning'),
    (r'依據(\w+)', 'reasoning'),

    # Assumptions
    (r'假設(\w+)', 'assumption'),
    (r'假設(\w+)成立', 'assumption'),
]

# Stop words (skipped during keyword extraction)
STOP_WORDS = {'的', '了', '是', '在', '和', '與', '或', '但', '因為', '所以', '如果'}


def extract_claims(text: str) -> List[Claim]:
    """Extract verifiable claims from text.

    Args:
        text: Subagent result text.

    Returns:
        List of Claim objects.
    """
    if not text:
        return []

    claims = []
    sentences = re.split(r'[.。]\s+', text)

    for i, sentence in enumerate(sentences):
        sentence = sentence.strip()
        if not sentence or len(sentence) < 10:
            continue

        for pattern, claim_type in CLAIM_PATTERNS:
            match = re.search(pattern, sentence)
            if match:
                keywords = _extract_keywords(sentence)
                claims.append(Claim(
                    id=f"claim_{i}",
                    text=sentence,
                    keywords=keywords,
                    pattern_matched=pattern,
                    claim_type=claim_type,
                ))
                break

    return claims


def _extract_keywords(text: str, stop_words: _Optional[set] = None) -> List[str]:
    """Extract keywords from text."""
    if stop_words is None:
        stop_words = STOP_WORDS

    words = re.findall(r'[\w]+', text.lower())
    keywords = [w for w in words if w not in stop_words and len(w) >= 2]
    # Return top 5 unique keywords
    seen = set()
    result = []
    for w in keywords:
        if w not in seen:
            seen.add(w)
            result.append(w)
    return result[:5]


def claims_to_dict(claims: List[Claim]) -> List[Dict[str, Any]]:
    """Convert Claim list to dict (for JSON serialization)."""
    return [
        {
            "id": c.id,
            "text": c.text,
            "keywords": c.keywords,
            "pattern_matched": c.pattern_matched,
            "claim_type": c.claim_type,
        }
        for c in claims
    ]


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--help":
        print("Claim Extractor - extracts verifiable claims from text")
        print("Usage: python claim_extractor.py")
        print("Or:    from constitution.claim_extractor import extract_claims")
    else:
        demo_text = (
            "Implement LRU cache strategy based on SRS.md §4.2, "
            "using asyncio for performance. "
            "The system shall use this approach to handle concurrent requests."
        )
        claims = extract_claims(demo_text)
        print(f"Extracted {len(claims)} claims:")
        for c in claims:
            print(f"  [{c.claim_type}] {c.text}")
            print(f"    keywords: {c.keywords}")
