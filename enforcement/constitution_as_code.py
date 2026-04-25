#!/usr/bin/env python3
"""
Constitution as Code
====================
Framework rules as executable code, not advisory documents.

Key principle:
- Not 'suggested to read Constitution'
- But 'violating Constitution = blocked'
- Rules are code: executable and testable
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Callable, Any, Optional
import re


class RuleSeverity(Enum):
    CRITICAL = "critical"  # Immediate block
    HIGH = "high"          # Warn and block
    MEDIUM = "medium"      # Warn only
    LOW = "low"            # Log only


@dataclass
class Rule:
    """A single constitution rule."""
    id: str
    description: str
    check_fn: Callable[[Any], bool]
    severity: RuleSeverity
    error_message: str
    enabled: bool = True


class ConstitutionAsCode:
    """
    Constitution as Code.

    Converts Constitution documents into executable rules::

        rules = ConstitutionAsCode()

        rules.add_rule(Rule(
            id="R001",
            description="All commits must have task_id",
            check_fn=lambda msg: bool(re.search(r'\[[A-Z]+-\d+\]', msg)),
            severity=RuleSeverity.CRITICAL,
            error_message="Commit has no task_id! Format: [TASK-123] message"
        ))

        violations = rules.check_commit_message("[DEV-456] Add feature")

        if violations:
            for v in violations:
                print(v.error_message)
            raise BlockedException("Constitution violation")
    """

    def __init__(self):
        self.rules: List[Rule] = []
        self._setup_default_rules()

    def _setup_default_rules(self):
        """Set up default rules derived from CONSTITUTION.md."""

        self.add_rule(Rule(
            id="R001",
            description="All commits must have task_id",
            check_fn=lambda msg: bool(re.search(r'\[[A-Z]+-\d+\]', msg or "")),
            severity=RuleSeverity.CRITICAL,
            error_message="Commit message must include task_id, format: [TASK-123]",
        ))

        self.add_rule(Rule(
            id="R002",
            description="Bypass/skip/--no-verify commands are forbidden",
            check_fn=lambda cmd: not any(kw in (cmd or "").lower() for kw in ["--bypass", "--skip", "--no-verify"]),
            severity=RuleSeverity.CRITICAL,
            error_message="bypass/skip/--no-verify commands are not allowed",
        ))

        self.add_rule(Rule(
            id="R003",
            description="Quality Gate score must be >= 90",
            check_fn=lambda score: (score or 0) >= 90,
            severity=RuleSeverity.CRITICAL,
            error_message="Quality Gate score below 90 — not compliant",
        ))

        self.add_rule(Rule(
            id="R004",
            description="Test coverage must be >= 80%",
            check_fn=lambda coverage: (coverage or 0) >= 80,
            severity=RuleSeverity.HIGH,
            error_message="Test coverage below 80% — not compliant",
        ))

        self.add_rule(Rule(
            id="R005",
            description="Security score must be >= 95",
            check_fn=lambda score: (score or 0) >= 95,
            severity=RuleSeverity.HIGH,
            error_message="Security score below 95 — not compliant",
        ))

        self.add_rule(Rule(
            id="R006",
            description="Self-approval is forbidden",
            check_fn=lambda ctx: ctx.get("approver") != ctx.get("operator") if ctx else True,
            severity=RuleSeverity.CRITICAL,
            error_message="Operator cannot approve their own actions",
        ))

        self.add_rule(Rule(
            id="R007",
            description="New features must have corresponding tests",
            check_fn=lambda ctx: ctx.get("has_test", False) if ctx else False,
            severity=RuleSeverity.HIGH,
            error_message="New feature has no tests — TDD required",
        ))

    def add_rule(self, rule: Rule):
        self.rules.append(rule)

    def remove_rule(self, rule_id: str):
        self.rules = [r for r in self.rules if r.id != rule_id]

    def check_commit_message(self, message: str) -> List[Rule]:
        violations = []
        for rule in self.rules:
            if not rule.enabled:
                continue
            if "task_id" in rule.description.lower() or "commit" in rule.description.lower():
                if not rule.check_fn(message):
                    violations.append(rule)
        return violations

    def check_command(self, command: str) -> List[Rule]:
        violations = []
        for rule in self.rules:
            if not rule.enabled:
                continue
            if "bypass" in rule.description.lower() or "skip" in rule.description.lower():
                if not rule.check_fn(command):
                    violations.append(rule)
        return violations

    def check(self, context: Dict[str, Any]) -> List[Rule]:
        """Generic context check."""
        violations = []
        if "commit_message" in context:
            violations.extend(self.check_commit_message(context["commit_message"]))
        if "command" in context:
            violations.extend(self.check_command(context["command"]))
        for key, desc_kw in [
            ("quality_score", "quality gate"),
            ("coverage", "coverage"),
            ("security_score", "security"),
        ]:
            if key in context:
                for rule in self.rules:
                    if not rule.enabled:
                        continue
                    if desc_kw in rule.description.lower():
                        if not rule.check_fn(context[key]):
                            violations.append(rule)
        if "approval_context" in context:
            for rule in self.rules:
                if not rule.enabled:
                    continue
                if "approval" in rule.description.lower() or "approve" in rule.description.lower():
                    if not rule.check_fn(context["approval_context"]):
                        violations.append(rule)
        return violations

    def enforce(self, context: Dict[str, Any]):
        """
        Run checks and raise on violation::

            constitution = ConstitutionAsCode()
            try:
                constitution.enforce({
                    "commit_message": "[DEV-123] Add feature",
                    "quality_score": 95,
                    "coverage": 85,
                })
            except ConstitutionViolation as e:
                sys.exit(1)
        """
        violations = self.check(context)
        if violations:
            critical = [v for v in violations if v.severity == RuleSeverity.CRITICAL]
            error_msg = "Constitution Violations:\n"
            error_msg += "\n".join(f"  [{v.severity.value}] {v.error_message}" for v in violations)
            if critical:
                raise ConstitutionViolation(error_msg)
            else:
                raise ConstitutionWarning(error_msg)

    def get_rules_summary(self) -> Dict:
        return {
            "total": len(self.rules),
            "enabled": len([r for r in self.rules if r.enabled]),
            "by_severity": {
                "critical": len([r for r in self.rules if r.severity == RuleSeverity.CRITICAL]),
                "high": len([r for r in self.rules if r.severity == RuleSeverity.HIGH]),
                "medium": len([r for r in self.rules if r.severity == RuleSeverity.MEDIUM]),
                "low": len([r for r in self.rules if r.severity == RuleSeverity.LOW]),
            },
        }


class ConstitutionViolation(Exception):
    """Critical-severity constitution violation."""
    pass


class ConstitutionWarning(Exception):
    """High-severity constitution warning."""
    pass
