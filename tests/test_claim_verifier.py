"""Tests for constitution/claim_verifier.py — claims-to-citations verification."""

import pytest
from constitution.claim_verifier import (
    ClaimVerifier,
    VerifiedClaim,
    verify_claims,
    verify_result,
    verification_to_dict,
)


class TestVerifiedClaim:
    def test_defaults(self):
        vc = VerifiedClaim(id="c1", text="test", claim_type="design", keywords=["k"], verified=False)
        assert vc.supporting_citations == []
        assert vc.supporting_count == 0
        assert vc.reason == ""
        assert vc.reasoning_chain_score == 0.0

    def test_full_fields(self):
        vc = VerifiedClaim(
            id="c1", text="test", claim_type="design",
            keywords=["k"], verified=True,
            supporting_citations=["SRS.md"],
            supporting_count=1, reason="found",
            reasoning_chain_score=0.9,
        )
        assert vc.verified is True
        assert vc.supporting_citations == ["SRS.md"]


class TestClaimVerifier:
    def test_init(self):
        cv = ClaimVerifier()
        assert cv.inferential_sensor is not None
        assert cv.citation_parser is not None

    def test_init_custom_threshold(self):
        cv = ClaimVerifier(min_coverage_threshold=0.7)
        assert cv.inferential_sensor.min_coverage_threshold == 0.7

    def test_verify_claims_no_keywords(self):
        cv = ClaimVerifier()
        claims = [{"id": "c1", "text": "test", "keywords": []}]
        result = cv.verify_claims(claims, ["SRS.md#45"], {"SRS.md": "content"})
        assert len(result) == 1
        assert result[0].verified is False
        assert result[0].reason == "No keywords to verify"

    def test_verify_claims_strict_all_match(self):
        cv = ClaimVerifier()
        claims = [{"id": "c1", "text": "test", "keywords": ["lru", "cache"]}]
        result = cv.verify_claims(
            claims,
            ["SRS.md#45"],
            {"SRS.md": "The system uses lru cache strategy."},
            strict=True,
        )
        assert result[0].verified is True

    def test_verify_claims_strict_missing(self):
        cv = ClaimVerifier()
        claims = [{"id": "c1", "text": "test", "keywords": ["lru", "nonexistent"]}]
        result = cv.verify_claims(
            claims,
            ["SRS.md#45"],
            {"SRS.md": "The system uses lru cache."},
            strict=True,
        )
        assert result[0].verified is False

    def test_verify_claims_non_strict_partial(self):
        cv = ClaimVerifier()
        claims = [{"id": "c1", "text": "test", "keywords": ["lru", "missing"]}]
        result = cv.verify_claims(
            claims,
            ["SRS.md#45"],
            {"SRS.md": "The system uses lru cache."},
            strict=False,
        )
        assert result[0].verified is True

    def test_verify_claims_non_strict_none_found(self):
        cv = ClaimVerifier()
        claims = [{"id": "c1", "text": "test", "keywords": ["missing1", "missing2"]}]
        result = cv.verify_claims(
            claims,
            ["SRS.md#45"],
            {"SRS.md": "completely unrelated content"},
            strict=False,
        )
        assert result[0].verified is False

    def test_verify_claims_empty_citations(self):
        cv = ClaimVerifier()
        claims = [{"id": "c1", "text": "test", "keywords": ["lru"]}]
        result = cv.verify_claims(claims, [], {"SRS.md": "lru content"})
        # No citations to check against
        assert result[0].verified is False

    def test_verify_result_pipeline(self):
        cv = ClaimVerifier()
        result = cv.verify_result(
            "The system shall use LRU cache for performance optimization.",
            ["SRS.md#45"],
            {"SRS.md": "The system shall use LRU cache for performance."},
            strict=True,
        )
        assert "total_claims" in result
        assert "verified_claims" in result
        assert "verification_rate" in result

    def test_verify_result_empty_text(self):
        cv = ClaimVerifier()
        result = cv.verify_result("", ["SRS.md#45"], {"SRS.md": "content"})
        assert result["total_claims"] == 0
        assert result["verification_rate"] == 1.0

    def test_reasoning_chain_score_included(self):
        cv = ClaimVerifier()
        claims = [{"id": "c1", "text": "must use lru", "keywords": ["lru"], "claim_type": "design"}]
        result = cv.verify_claims(
            claims,
            ["SRS.md#45"],
            {"SRS.md": "The system shall use lru cache for performance optimization."},
            strict=True,
        )
        assert result[0].reasoning_chain_score >= 0.0


class TestModuleLevelFunctions:
    def test_verify_claims_module_level(self):
        claims = [{"id": "c1", "text": "test", "keywords": ["lru"]}]
        result = verify_claims(claims, ["SRS.md#45"], {"SRS.md": "lru cache system"})
        assert len(result) == 1

    def test_verify_result_module_level(self):
        result = verify_result(
            "The system shall use LRU cache.",
            ["SRS.md#45"],
            {"SRS.md": "The system shall use LRU cache."},
        )
        assert "total_claims" in result

    def test_verification_to_dict(self):
        vc = VerifiedClaim(id="c1", text="test", claim_type="t", keywords=[], verified=True)
        result = verification_to_dict([vc])
        assert len(result) == 1
        assert result[0]["verified"] is True
