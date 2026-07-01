"""
Unit tests for PatternMatcher.
"""

import re

import pytest

from detection.pattern_matcher import PatternMatcher, RuleSet, Rule, RuleType


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


class TestRuleRegexErrorHandling:
    """Bug fix: Rule.__post_init__ must NOT silently swallow re.error on a
    FORBIDDEN rule — otherwise a malformed regex makes the rule inert and
    bypasses the check. Failing the rule at construction is the correct
    fail-loud behavior.
    """

    def test_invalid_pattern_raises_at_construction(self):
        """Malformed regex on a FORBIDDEN rule must raise re.error at __post_init__."""
        with pytest.raises(re.error):
            Rule(name="bad", rule_type=RuleType.FORBIDDEN, pattern="[abc")

    def test_invalid_pattern_via_from_dict_raises(self):
        """RuleSet.from_dict must surface the regex error, not hide it."""
        with pytest.raises(re.error):
            RuleSet.from_dict([
                {"name": "bad", "type": "FORBIDDEN", "pattern": "[abc"},
            ])

    def test_valid_pattern_compiles_normally(self):
        """Sanity: well-formed regex still compiles."""
        rule = Rule(name="ok", rule_type=RuleType.FORBIDDEN, pattern=r"secret")
        assert rule._compiled is not None
        assert rule.matches("a secret thing") is True
        assert rule.matches("nothing here") is False
