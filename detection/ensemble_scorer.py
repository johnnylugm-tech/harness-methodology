#!/usr/bin/env python3
"""
M2: UQLM EnsembleScorer
========================
Scores agent output confidence using an ensemble of methods:

- CitationScorer   : citation completeness (HR-15)
- CoverageScorer   : FR requirement coverage
- ConsistencyScorer: output structural consistency
- ConfidenceScorer : agent-stated confidence values

Used as phase_hooks transitive dependency (step #7 postflight).

Usage:
    from detection import EnsembleScorer

    scorer = EnsembleScorer()
    score = scorer.score(dev_result)
    print(score.ensemble_confidence)  # 0.0 - 1.0
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class EnsembleScore:
    """Score for a single agent result."""
    citation_score: float       # 0.0 - 1.0
    coverage_score: float       # 0.0 - 1.0
    consistency_score: float    # 0.0 - 1.0
    confidence_score: float     # 0.0 - 1.0 (agent-stated)
    ensemble_confidence: float  # weighted aggregate
    passed: bool
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """To dict."""
        return {
            "citation_score": round(self.citation_score, 3),
            "coverage_score": round(self.coverage_score, 3),
            "consistency_score": round(self.consistency_score, 3),
            "confidence_score": round(self.confidence_score, 3),
            "ensemble_confidence": round(self.ensemble_confidence, 3),
            "passed": self.passed,
            "details": self.details,
        }


@dataclass
class AggregateScore:
    """Aggregate score across multiple agent results."""
    scores: List[EnsembleScore]
    mean_confidence: float
    min_confidence: float
    max_confidence: float
    passed: bool
    threshold: float

    def to_dict(self) -> Dict[str, Any]:
        """To dict."""
        return {
            "mean_confidence": round(self.mean_confidence, 3),
            "min_confidence": round(self.min_confidence, 3),
            "max_confidence": round(self.max_confidence, 3),
            "passed": self.passed,
            "threshold": self.threshold,
            "count": len(self.scores),
        }


# ---------------------------------------------------------------------------
# Individual scorers
# ---------------------------------------------------------------------------

class _CitationScorer:
    """Score citation completeness (HR-15)."""

    CITATION_PATTERN = re.compile(r'\[?FR-\d+\]?|SAD\.md#L\d+|SRS\.md#L\d+')
    LINE_REF_PATTERN = re.compile(r'#L\d+')

    def score(self, result: Dict[str, Any]) -> float:
        """Score."""
        citations = result.get("citations", [])
        if not citations:
            return 0.0

        # Check at least one line-referenced citation exists
        has_line_ref = any(
            self.LINE_REF_PATTERN.search(str(c)) for c in citations
        )
        # Check citations look valid
        valid = sum(
            1 for c in citations if self.CITATION_PATTERN.search(str(c))
        )
        completeness = valid / max(len(citations), 1)
        line_bonus = 0.2 if has_line_ref else 0.0
        return min(1.0, completeness + line_bonus)


class _CoverageScorer:
    """Score FR requirement coverage."""

    FR_PATTERN = re.compile(r'FR-\d+')

    def score(self, result: Dict[str, Any], expected_fr: Optional[str] = None) -> float:
        """Score."""
        citations = result.get("citations", [])
        summary = result.get("summary", "")
        result_text = result.get("result", "")

        all_text = " ".join([str(c) for c in citations] + [summary, result_text])
        found_frs = set(self.FR_PATTERN.findall(all_text))

        if expected_fr:
            return 1.0 if expected_fr in found_frs else 0.0

        # No specific expectation — reward any FR mention
        return min(1.0, len(found_frs) * 0.3) if found_frs else 0.0


class _ConsistencyScorer:
    """Score output structural consistency."""

    REQUIRED_KEYS = {"status", "confidence"}
    VALID_STATUSES = {"success", "error", "unable_to_proceed"}

    def score(self, result: Dict[str, Any]) -> float:
        """Score."""
        score = 0.0
        if not isinstance(result, dict):
            return 0.0

        # Has required keys
        present = self.REQUIRED_KEYS & result.keys()
        score += len(present) / len(self.REQUIRED_KEYS) * 0.4

        # Status is valid
        status = result.get("status", "")
        if status in self.VALID_STATUSES:
            score += 0.3

        # Has summary
        summary = result.get("summary", "")
        if summary and len(summary) > 5:
            score += 0.2

        # Has result content
        if result.get("result") or result.get("files"):
            score += 0.1

        return min(1.0, score)


class _ConfidenceScorer:
    """Normalize agent-stated confidence (1-10 scale) to 0.0-1.0."""

    def score(self, result: Dict[str, Any]) -> float:
        """Score."""
        raw = result.get("confidence", 0)
        try:
            val = float(raw)
            return min(1.0, max(0.0, val / 10.0))
        except (TypeError, ValueError):
            return 0.0


# ---------------------------------------------------------------------------
# EnsembleScorer
# ---------------------------------------------------------------------------

class EnsembleScorer:
    """
    M2 UQLM EnsembleScorer.

    Combines CitationScorer, CoverageScorer, ConsistencyScorer, and
    ConfidenceScorer into a weighted ensemble score.

    Default weights (sum to 1.0):
        citation    = 0.35  (HR-15 critical)
        coverage    = 0.25
        consistency = 0.20
        confidence  = 0.20
    """

    DEFAULT_WEIGHTS = {
        "citation": 0.35,
        "coverage": 0.25,
        "consistency": 0.20,
        "confidence": 0.20,
    }
    DEFAULT_THRESHOLD = 0.70

    def __init__(
        self,
        weights: Optional[Dict[str, float]] = None,
        threshold: float = DEFAULT_THRESHOLD,
    ):
        """Initialize instance."""
        self.weights = weights or self.DEFAULT_WEIGHTS
        self.threshold = threshold
        self._citation = _CitationScorer()
        self._coverage = _CoverageScorer()
        self._consistency = _ConsistencyScorer()
        self._confidence = _ConfidenceScorer()

    def score(
        self,
        result: Dict[str, Any],
        expected_fr: Optional[str] = None,
    ) -> EnsembleScore:
        """
        Score a single agent result.

        Args:
            result: agent output dict (status, confidence, citations, summary, result)
            expected_fr: FR ID to verify coverage (e.g. 'FR-01')

        Returns:
            EnsembleScore with per-component and ensemble confidence
        """
        c_score = self._citation.score(result)
        cov_score = self._coverage.score(result, expected_fr)
        cons_score = self._consistency.score(result)
        conf_score = self._confidence.score(result)

        ensemble = (
            c_score * self.weights["citation"]
            + cov_score * self.weights["coverage"]
            + cons_score * self.weights["consistency"]
            + conf_score * self.weights["confidence"]
        )

        return EnsembleScore(
            citation_score=c_score,
            coverage_score=cov_score,
            consistency_score=cons_score,
            confidence_score=conf_score,
            ensemble_confidence=ensemble,
            passed=ensemble >= self.threshold,
            details={
                "threshold": self.threshold,
                "weights": self.weights,
                "expected_fr": expected_fr,
            },
        )

    def score_ensemble(
        self,
        results: List[Dict[str, Any]],
        expected_frs: Optional[List[str]] = None,
    ) -> AggregateScore:
        """
        Score multiple agent results and aggregate.

        Args:
            results: list of agent output dicts
            expected_frs: optional list of FR IDs parallel to results

        Returns:
            AggregateScore with mean/min/max confidence
        """
        if not results:
            return AggregateScore(
                scores=[], mean_confidence=0.0, min_confidence=0.0,
                max_confidence=0.0, passed=False, threshold=self.threshold
            )

        scores = [
            self.score(r, expected_frs[i] if expected_frs and i < len(expected_frs) else None)
            for i, r in enumerate(results)
        ]

        confidences = [s.ensemble_confidence for s in scores]
        mean_c = sum(confidences) / len(confidences)
        min_c = min(confidences)
        max_c = max(confidences)

        return AggregateScore(
            scores=scores,
            mean_confidence=mean_c,
            min_confidence=min_c,
            max_confidence=max_c,
            passed=mean_c >= self.threshold and min_c >= (self.threshold * 0.8),
            threshold=self.threshold,
        )
