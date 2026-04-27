#!/usr/bin/env python3
r"""
M2: Pattern Matcher
===================
Matches code and artifact content against rule-based patterns.

Supports:
- FORBIDDEN patterns (must NOT appear)
- REQUIRED patterns (must appear)
- QUALITY patterns (scored, not blocked)

Usage:
    from detection import PatternMatcher, RuleSet

    rules = RuleSet.from_dict([
        {"name": "no-bypass", "type": "FORBIDDEN", "pattern": r"--no-verify"},
        {"name": "has-fr-ref", "type": "REQUIRED",  "pattern": r"\[FR-\d+\]"},
    ])
    matcher = PatternMatcher(rules)
    matches = matcher.match_text(code_text)
    print(matches.passed)  # False if any FORBIDDEN hits or REQUIRED misses
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

class RuleType(Enum):
    """Types of rules supported by the pattern matcher."""
    FORBIDDEN = "FORBIDDEN"
    REQUIRED = "REQUIRED"
    QUALITY = "QUALITY"


@dataclass
class Rule:
    """A single pattern rule."""
    name: str
    rule_type: RuleType
    pattern: str
    description: str = ""
    severity: str = "HIGH"  # HIGH, MEDIUM, LOW
    _compiled: Any = field(default=None, repr=False)

    def __post_init__(self):
        """Compile the regex pattern upon initialization."""
        try:
            self._compiled = re.compile(self.pattern, re.MULTILINE | re.IGNORECASE)
        except re.error:
            self._compiled = None

    def matches(self, text: str) -> bool:
        """Return True if the pattern matches the given text."""
        if not self._compiled:
            return False
        return bool(self._compiled.search(text))

    def find_all(self, text: str) -> List[str]:
        """Return all occurrences of the pattern in the given text."""
        if not self._compiled:
            return []
        return self._compiled.findall(text)


@dataclass
class RuleSet:
    """A named collection of rules."""
    name: str
    rules: List[Rule] = field(default_factory=list)

    @classmethod
    def from_dict(cls, rule_dicts: List[Dict], name: str = "default") -> "RuleSet":
        """Create a RuleSet from a list of dictionaries."""
        rules = []
        for d in rule_dicts:
            rule_type = RuleType(d.get("type", "FORBIDDEN"))
            rules.append(Rule(
                name=d["name"],
                rule_type=rule_type,
                pattern=d["pattern"],
                description=d.get("description", ""),
                severity=d.get("severity", "HIGH"),
            ))
        return cls(name=name, rules=rules)

    def forbidden(self) -> List[Rule]:
        """Return all FORBIDDEN rules in the set."""
        return [r for r in self.rules if r.rule_type == RuleType.FORBIDDEN]

    def required(self) -> List[Rule]:
        """Return all REQUIRED rules in the set."""
        return [r for r in self.rules if r.rule_type == RuleType.REQUIRED]

    def quality(self) -> List[Rule]:
        """Return all QUALITY rules in the set."""
        return [r for r in self.rules if r.rule_type == RuleType.QUALITY]


@dataclass
class PatternMatch:
    """Result of matching a text against a RuleSet."""
    passed: bool
    forbidden_hits: List[Dict] = field(default_factory=list)
    required_misses: List[Dict] = field(default_factory=list)
    quality_hits: List[Dict] = field(default_factory=list)
    quality_score: float = 1.0  # 0.0-1.0

    def to_dict(self) -> Dict:
        """Serialize the match result to a dictionary."""
        return {
            "passed": self.passed,
            "quality_score": round(self.quality_score, 3),
            "forbidden_hits": self.forbidden_hits,
            "required_misses": self.required_misses,
            "quality_hits": self.quality_hits,
        }


# ---------------------------------------------------------------------------
# Built-in rule sets
# ---------------------------------------------------------------------------

HARNESS_RULES = RuleSet.from_dict(
    name="harness-default",
    rule_dicts=[
        # Forbidden
        {
            "name": "no-git-no-verify",
            "type": "FORBIDDEN",
            "pattern": r"--no-verify",
            "description": "git commit --no-verify bypasses hooks",
            "severity": "CRITICAL",
        },
        {
            "name": "no-bypass-commands",
            "type": "FORBIDDEN",
            "pattern": r"rm\s+-rf\s+/|kill\s+-9|shutdown",
            "description": "Destructive system commands forbidden",
            "severity": "CRITICAL",
        },
        {
            "name": "no-infrastructure-import",
            "type": "FORBIDDEN",
            "pattern": r"from\s+app\.infrastructure\s+import|import\s+app\.infrastructure",
            "description": "app.infrastructure deprecated; use 03-development/infrastructure/",
            "severity": "HIGH",
        },
        {
            "name": "no-covers-annotation",
            "type": "FORBIDDEN",
            "pattern": r"@covers:\s*L1",
            "description": "@covers: L1 Error annotation forbidden",
            "severity": "HIGH",
        },
        # Required
        {
            "name": "has-fr-reference",
            "type": "REQUIRED",
            "pattern": r"\[FR-\d+\]",
            "description": "Docstrings must reference FR numbers",
            "severity": "HIGH",
        },
        {
            "name": "has-citations",
            "type": "REQUIRED",
            "pattern": r"Citations?:",
            "description": "Docstrings must include Citations section (HR-15)",
            "severity": "HIGH",
        },
        # Quality
        {
            "name": "has-type-hints",
            "type": "QUALITY",
            "pattern": r"def\s+\w+\([^)]*:\s*\w+",
            "description": "Functions should use type hints",
            "severity": "LOW",
        },
        {
            "name": "has-docstring",
            "type": "QUALITY",
            "pattern": r'def\s+\w+\([^)]*\).*?:\s*\n\s+"""',
            "description": "Functions should have docstrings",
            "severity": "MEDIUM",
        },
    ]
)


# ---------------------------------------------------------------------------
# PatternMatcher
# ---------------------------------------------------------------------------

class PatternMatcher:
    """
    Matches text or file content against a RuleSet.

    FORBIDDEN hits => passed=False
    REQUIRED misses => passed=False
    QUALITY hits => scored but do not block
    """

    def __init__(self, rule_set: Optional[RuleSet] = None):
        """Initialize with an optional rule set (defaults to HARNESS_RULES)."""
        self.rule_set = rule_set or HARNESS_RULES

    def match_text(self, text: str) -> PatternMatch:
        """
        Match text against the rule set.

        Args:
            text: content to check (code, artifact, commit message, etc.)

        Returns:
            PatternMatch with pass/fail status and detailed findings
        """
        forbidden_hits = []
        required_misses = []
        quality_hits = []

        for rule in self.rule_set.forbidden():
            if rule.matches(text):
                forbidden_hits.append({
                    "rule": rule.name,
                    "severity": rule.severity,
                    "description": rule.description,
                })

        for rule in self.rule_set.required():
            if not rule.matches(text):
                required_misses.append({
                    "rule": rule.name,
                    "severity": rule.severity,
                    "description": rule.description,
                })

        total_quality = len(self.rule_set.quality())
        for rule in self.rule_set.quality():
            if rule.matches(text):
                quality_hits.append({
                    "rule": rule.name,
                    "description": rule.description,
                })

        quality_score = len(quality_hits) / max(total_quality, 1)
        passed = len(forbidden_hits) == 0 and len(required_misses) == 0

        return PatternMatch(
            passed=passed,
            forbidden_hits=forbidden_hits,
            required_misses=required_misses,
            quality_hits=quality_hits,
            quality_score=quality_score,
        )

    def match_file(self, file_path: str) -> PatternMatch:
        """Match a file's content against the rule set."""
        try:
            with open(file_path, encoding="utf-8", errors="replace") as f:
                text = f.read()
            return self.match_text(text)
        except Exception as e:
            return PatternMatch(
                passed=False,
                forbidden_hits=[{
                    "rule": "file-read-error",
                    "description": str(e),
                    "severity": "HIGH"
                }],
            )

    def match_files(self, file_paths: List[str]) -> Dict[str, PatternMatch]:
        """Match multiple files sequentially."""
        return {fp: self.match_file(fp) for fp in file_paths}
