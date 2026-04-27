"""
Unit tests for EnsembleScorer.
"""

import pytest
from detection.ensemble_scorer import EnsembleScorer


class TestEnsembleScorer:
    """Tests for the EnsembleScorer class."""

    def test_score_basic(self):
        """Verify basic scoring of a single result."""
        scorer = EnsembleScorer()
        result = {
            "status": "success",
            "confidence": 8,
            "summary": "Completed the task successfully",
            "citations": ["[FR-01] SAD.md#L10"]
        }
        # c_score = 1.0 (valid + line bonus), cov=1.0, cons=1.0, conf=0.8
        score = scorer.score(result, expected_fr="FR-01")
        assert score.ensemble_confidence > 0.8
        assert score.passed is True

    def test_score_missing_info(self):
        """Verify low score for results with missing information."""
        scorer = EnsembleScorer()
        result = {
            "status": "success",
            "confidence": 2
        }
        score = scorer.score(result)
        assert score.ensemble_confidence < 0.5
        assert score.passed is False

    def test_score_ensemble_aggregate(self):
        """Verify aggregation across multiple results."""
        scorer = EnsembleScorer()
        results = [
            {"status": "success", "confidence": 9, "summary": "Good result", "citations": ["[FR-01]"]},
            {"status": "success", "confidence": 4, "summary": "Poor result"}
        ]
        aggregate = scorer.score_ensemble(results)
        assert len(aggregate.scores) == 2
        assert aggregate.mean_confidence == (
            scorer.score(results[0]).ensemble_confidence + 
            scorer.score(results[1]).ensemble_confidence
        ) / 2

    def test_score_ensemble_empty(self):
        """Verify handling of empty result lists."""
        scorer = EnsembleScorer()
        aggregate = scorer.score_ensemble([])
        assert len(aggregate.scores) == 0
        assert aggregate.mean_confidence == 0.0
        assert aggregate.passed is False
