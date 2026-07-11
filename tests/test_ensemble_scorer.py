"""
Unit tests for EnsembleScorer.
"""

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

    def test_score_non_dict_returns_zero(self):
        from detection.ensemble_scorer import _ConsistencyScorer
        scorer = _ConsistencyScorer()
        assert scorer.score("not a dict") == 0.0  # type: ignore[arg-type]

    def test_score_with_result_field(self):
        scorer = EnsembleScorer()
        result = {"status": "success", "confidence": 8, "result": "something"}
        score = scorer.score(result)
        assert score.ensemble_confidence > 0.0

    def test_confidence_scorer_invalid_value(self):
        scorer = EnsembleScorer()
        result = {"status": "success", "confidence": "not_a_number"}
        score = scorer.score(result)
        assert score.confidence_score == 0.0

    def test_ensemble_score_to_dict(self):
        from detection.ensemble_scorer import EnsembleScore
        s = EnsembleScore(citation_score=0.9, coverage_score=1.0,
                         consistency_score=0.8, confidence_score=0.7,
                         ensemble_confidence=0.85, passed=True)
        d = s.to_dict()
        assert d["citation_score"] == 0.9
        assert d["passed"] is True
        assert "details" in d

    def test_aggregate_score_to_dict(self):
        from detection.ensemble_scorer import AggregateScore
        s = AggregateScore(scores=[], mean_confidence=0.5, min_confidence=0.3,
                          max_confidence=0.7, passed=True, threshold=0.6)
        d = s.to_dict()
        assert d["mean_confidence"] == 0.5
        assert d["threshold"] == 0.6
        assert d["count"] == 0

    def test_coverage_scorer_ignores_nfr_mentions(self):
        """An NFR-03 mention must not satisfy expected_fr=FR-03 (phantom substring)."""
        scorer = EnsembleScorer()
        result = {
            "status": "success",
            "confidence": 9,
            "summary": "Verified NFR-03 reliability targets",
            "citations": ["NFR-03 SAD.md#L220"],
        }
        score = scorer.score(result, expected_fr="FR-03")
        assert score.coverage_score == 0.0
