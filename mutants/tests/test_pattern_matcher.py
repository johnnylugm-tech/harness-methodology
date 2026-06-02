"""
Unit tests for PatternMatcher.
"""

from detection.pattern_matcher import PatternMatcher, RuleSet


class TestPatternMatcher:
    """Tests for the PatternMatcher class."""

    def test_forbidden_rule_triggers(self):
        """Verify FORBIDDEN rules detect bad patterns."""
        rules = RuleSet.from_dict([
            {"name": "no-secrets", "type": "FORBIDDEN", "pattern": r"secret_key", "severity": "CRITICAL"}
        ])
        matcher = PatternMatcher(rules)
        
        # Test pass
        res = matcher.match_text("public_data = 1")
        assert res.passed is True
        assert len(res.forbidden_hits) == 0
        
        # Test fail
        res = matcher.match_text("secret_key = 'abc'")
        assert res.passed is False
        assert res.forbidden_hits[0]["rule"] == "no-secrets"

    def test_required_rule_triggers(self):
        """Verify REQUIRED rules detect missing patterns."""
        rules = RuleSet.from_dict([
            {"name": "has-header", "type": "REQUIRED", "pattern": r"Copyright", "severity": "HIGH"}
        ])
        matcher = PatternMatcher(rules)
        
        # Test pass
        res = matcher.match_text("# Copyright 2026")
        assert res.passed is True
        
        # Test fail
        res = matcher.match_text("print('hello')")
        assert res.passed is False
        assert res.required_misses[0]["rule"] == "has-header"

    def test_quality_rule_scoring(self):
        """Verify QUALITY rules affect score but not pass/fail status."""
        rules = RuleSet.from_dict([
            {"name": "hint", "type": "QUALITY", "pattern": r"type_hint", "severity": "LOW"}
        ])
        matcher = PatternMatcher(rules)
        
        # Hit quality rule
        res = matcher.match_text("def f(x: type_hint): pass")
        assert res.passed is True
        assert res.quality_score == 1.0
        
        # Miss quality rule
        res = matcher.match_text("def f(x): pass")
        assert res.passed is True
        assert res.quality_score == 0.0

    def test_to_dict_conversion(self):
        """Verify dictionary serialization of match results."""
        rules = RuleSet.from_dict([
            {"name": "rule1", "type": "FORBIDDEN", "pattern": r"xxx"}
        ])
        matcher = PatternMatcher(rules)
        res = matcher.match_text("xxx")
        data = res.to_dict()
        assert data["passed"] is False
        assert data["forbidden_hits"][0]["rule"] == "rule1"
