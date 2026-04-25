"""detection -- M2: UQLM EnsembleScorer (phase_hooks #7 transitive dep)."""

from .ensemble_scorer import EnsembleScorer, EnsembleScore, AggregateScore
from .drift_detector import DriftDetector, DriftResult, DriftSeverity
from .pattern_matcher import PatternMatcher, PatternMatch, RuleSet

__all__ = [
    "EnsembleScorer",
    "EnsembleScore",
    "AggregateScore",
    "DriftDetector",
    "DriftResult",
    "DriftSeverity",
    "PatternMatcher",
    "PatternMatch",
    "RuleSet",
]
