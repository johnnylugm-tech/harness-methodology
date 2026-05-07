"""Tests for constitution/inferential_sensor.py — reasoning chain quality quantification."""

import pytest
from constitution.inferential_sensor import InferentialSensor, ReasoningChainAssessment


class TestReasoningChainAssessment:
    def test_fields(self):
        rca = ReasoningChainAssessment(
            overall_score=0.85, citation_existence=1.0,
            citation_coverage=0.7, reasoning_coherence=0.6,
            issues=[], recommendation="good",
        )
        assert rca.overall_score == 0.85
        assert rca.citation_existence == 1.0
        assert rca.citation_coverage == 0.7
        assert rca.reasoning_coherence == 0.6


class TestInferentialSensor:
    def test_init_default(self):
        sensor = InferentialSensor()
        assert sensor.min_coverage_threshold == 0.5

    def test_init_custom_threshold(self):
        sensor = InferentialSensor(min_coverage_threshold=0.7)
        assert sensor.min_coverage_threshold == 0.7

    def test_assess_full_citations(self):
        sensor = InferentialSensor()
        claim = {"text": "LRU cache for performance", "keywords": ["lru", "cache", "performance"]}
        citations = [
            {"text": "The system shall use LRU cache strategy for performance optimization.", "line": 1},
        ]
        result = sensor.assess(claim, citations)
        assert result.citation_existence == 1.0
        assert result.overall_score >= 0.4
        assert "well-supported" in result.recommendation or "needs additional" in result.recommendation

    def test_assess_no_citations(self):
        sensor = InferentialSensor()
        claim = {"text": "Some claim", "keywords": ["some", "claim"]}
        result = sensor.assess(claim, [])
        assert result.citation_existence == 0.0
        assert result.citation_coverage == 0.0
        assert result.reasoning_coherence == 0.0
        assert "No citations" in result.issues[0]

    def test_assess_low_coverage(self):
        sensor = InferentialSensor(min_coverage_threshold=0.8)
        claim = {"text": "unrelated claim here", "keywords": ["unrelated", "claim", "xyzzy"]}
        citations = [
            {"text": "This is completely different content with no overlap.", "line": 1},
        ]
        result = sensor.assess(claim, citations)
        assert result.citation_coverage < 0.8

    def test_assess_recommendation_major_revision(self):
        sensor = InferentialSensor()
        claim = {"text": "test", "keywords": ["unique"]}
        result = sensor.assess(claim, [])
        assert "insufficiently supported" in result.recommendation

    def test_calculate_coverage_empty_citations(self):
        sensor = InferentialSensor()
        claim = {"keywords": ["a"]}
        assert sensor._calculate_coverage(claim, []) == 0.0

    def test_calculate_coverage_no_keywords(self):
        sensor = InferentialSensor()
        claim = {"text": "x" * 10, "keywords": []}
        citations = [{"text": "some content here", "line": 1}]
        cov = sensor._calculate_coverage(claim, citations)
        assert 0.0 <= cov <= 1.0

    def test_calculate_coverage_partial_overlap(self):
        sensor = InferentialSensor()
        claim = {"keywords": ["lru", "cache", "unique_missing"]}
        citations = [
            {"text": "The lru cache strategy is effective for performance.", "line": 1},
        ]
        cov = sensor._calculate_coverage(claim, citations)
        assert 0.0 < cov < 1.0  # partial match

    def test_assess_coherence_no_citations(self):
        sensor = InferentialSensor()
        claim = {"keywords": ["a"]}
        assert sensor._assess_coherence(claim, []) == 0.0

    def test_assess_coherence_full_match(self):
        sensor = InferentialSensor()
        claim = {"keywords": ["lru", "cache"]}
        citations = [{"text": "lru cache is used for performance", "line": 1}]
        coh = sensor._assess_coherence(claim, citations)
        assert coh == 1.0
