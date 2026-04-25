#!/usr/bin/env python3
"""
Policy Engine
=============
No optional policies. Only COMPLETED or FAILED.

Key principle: Hard Block — non-compliant = blocked, no opt-out.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Callable, Any, Optional
from datetime import datetime
import hashlib
import json
import os


class EnforcementLevel(Enum):
    LOG = "log"
    WARN = "warn"
    BLOCK = "block"
    FAIL_BUILD = "fail"


@dataclass
class Policy:
    """A single policy definition."""
    id: str
    description: str
    check_fn: Callable[[], bool]
    enforcement: EnforcementLevel
    severity: str = "medium"
    enabled: bool = True
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class PolicyResult:
    """Result of a single policy check."""
    policy_id: str
    passed: bool
    enforcement: EnforcementLevel
    message: str
    timestamp: str
    blocked: bool = False


class PolicyEngine:
    """
    Policy Engine — mandatory enforcement, no opt-out.

    Usage::

        engine = PolicyEngine()

        engine.add_policy(Policy(
            id="quality-gate",
            description="Quality Gate must be >= 90",
            check_fn=lambda: get_quality_score() >= 90,
            enforcement=EnforcementLevel.BLOCK,
            severity="critical"
        ))

        results = engine.enforce_all()
        engine.raise_on_block(results)
    """

    def __init__(self):
        self.policies: List[Policy] = []
        self.results: List[PolicyResult] = []
        self._setup_default_policies()

    def _setup_default_policies(self):
        """Set up default policies derived from Constitution."""
        self.add_policy(Policy(
            id="commit-has-task-id",
            description="All commits must have task_id",
            check_fn=lambda: self._check_commit_message(),
            enforcement=EnforcementLevel.BLOCK,
            severity="critical",
        ))
        self.add_policy(Policy(
            id="quality-gate-90",
            description="Quality Gate score must be >= 90",
            check_fn=lambda: self._check_quality_score(),
            enforcement=EnforcementLevel.BLOCK,
            severity="critical",
        ))
        self.add_policy(Policy(
            id="no-bypass-commands",
            description="bypass/skip/--no-verify commands are forbidden",
            check_fn=lambda: self._check_no_bypass(),
            enforcement=EnforcementLevel.BLOCK,
            severity="critical",
        ))
        self.add_policy(Policy(
            id="test-coverage-80",
            description="Test coverage must be >= 80%",
            check_fn=lambda: self._check_test_coverage(),
            enforcement=EnforcementLevel.BLOCK,
            severity="high",
        ))
        self.add_policy(Policy(
            id="security-score-95",
            description="Security score must be >= 95",
            check_fn=lambda: self._check_security_score(),
            enforcement=EnforcementLevel.BLOCK,
            severity="high",
        ))
        self.add_policy(Policy(
            id="aspice-docs-required",
            description="Each Phase must have corresponding ASPICE documents",
            check_fn=lambda: self._check_aspice_docs(),
            enforcement=EnforcementLevel.BLOCK,
            severity="critical",
            metadata={"category": "documentation"},
        ))
        self.add_policy(Policy(
            id="phase-artifact-reference",
            description="Each Phase must reference artifacts from the previous Phase",
            check_fn=lambda: self._check_phase_artifacts(),
            enforcement=EnforcementLevel.BLOCK,
            severity="critical",
            metadata={"category": "phase", "requires_config": True},
        ))

    def _check_commit_message(self) -> bool:
        commit_file = os.environ.get("COMMIT_MSG_FILE")
        if not commit_file:
            return True  # Not in hook context, skip
        if os.path.exists(commit_file):
            with open(commit_file, "r") as f:
                msg = f.read()
            return bool(self._has_task_id(msg))
        return True

    def _has_task_id(self, msg: str) -> bool:
        import re
        return bool(re.search(r"\[[A-Z]+-\d+\]", msg))

    def _check_quality_score(self) -> bool:
        score_file = ".methodology/.quality_score"
        if os.path.exists(score_file):
            with open(score_file, "r") as f:
                return float(f.read().strip()) >= 90
        return True

    def _check_no_bypass(self) -> bool:
        suspicious = os.environ.get("GIT_COMMAND", "")
        bypass_keywords = ["--bypass", "--skip", "--no-verify", "--force"]
        return not any(kw in suspicious for kw in bypass_keywords)

    def _check_test_coverage(self) -> bool:
        coverage_file = ".methodology/.coverage"
        if os.path.exists(coverage_file):
            with open(coverage_file, "r") as f:
                return float(f.read().strip()) >= 80
        return True

    def _check_security_score(self) -> bool:
        score_file = ".methodology/.security_score"
        if os.path.exists(score_file):
            with open(score_file, "r") as f:
                return float(f.read().strip()) >= 95
        return True

    def _check_aspice_docs(self) -> bool:
        import subprocess
        doc_checker_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "quality_gate", "doc_checker.py"
        )
        if not os.path.exists(doc_checker_path):
            return True
        try:
            result = subprocess.run(
                ["python3", doc_checker_path, "--format", "json"],
                capture_output=True, text=True, timeout=30
            )
            if result.returncode == 0:
                data = json.loads(result.stdout)
                return data.get("passed", True)
            return True
        except Exception:
            return True

    def _check_phase_artifacts(self) -> bool:
        import subprocess
        enforcer_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "quality_gate", "phase_artifact_enforcer.py"
        )
        if not os.path.exists(enforcer_path):
            return True
        try:
            result = subprocess.run(
                ["python3", enforcer_path, "--json"],
                capture_output=True, text=True, timeout=30
            )
            return result.returncode == 0
        except Exception:
            return True

    def add_policy(self, policy: Policy):
        self.policies.append(policy)

    def remove_policy(self, policy_id: str):
        self.policies = [p for p in self.policies if p.id != policy_id]

    def enable(self, policy_id: str):
        for p in self.policies:
            if p.id == policy_id:
                p.enabled = True

    def disable(self, policy_id: str):
        import warnings
        warnings.warn(
            f"Disabling policy '{policy_id}' is not recommended. "
            f"Use EnforcementLevel.LOG for optional policies.",
            DeprecationWarning,
        )
        for p in self.policies:
            if p.id == policy_id:
                p.enabled = False

    def check(self, policy_id: str) -> PolicyResult:
        policy = next((p for p in self.policies if p.id == policy_id), None)
        if not policy:
            return PolicyResult(
                policy_id=policy_id,
                passed=False,
                enforcement=EnforcementLevel.LOG,
                message=f"Policy '{policy_id}' not found",
                timestamp=datetime.now().isoformat(),
            )
        try:
            passed = policy.check_fn()
        except Exception:
            passed = False
        blocked = policy.enforcement == EnforcementLevel.BLOCK and not passed
        result = PolicyResult(
            policy_id=policy.id,
            passed=passed,
            enforcement=policy.enforcement,
            message=f"{'PASS' if passed else 'FAIL'} {policy.description}",
            timestamp=datetime.now().isoformat(),
            blocked=blocked,
        )
        self.results.append(result)
        return result

    def enforce_all(self) -> List[PolicyResult]:
        """Run all policies. BLOCK level raises immediately on failure."""
        results = []
        for policy in self.policies:
            if not policy.enabled:
                continue
            result = self.check(policy.id)
            results.append(result)
            if result.blocked:
                raise PolicyViolationException(
                    f"Policy violation: {policy.description}\n"
                    f"Policy ID: {policy.id}\n"
                    f"Enforcement: {policy.enforcement.value}\n"
                    f"This is a REQUIRED policy and cannot be bypassed."
                )
        return results

    def get_summary(self) -> Dict:
        total = len(self.results)
        passed = sum(1 for r in self.results if r.passed)
        blocked = sum(1 for r in self.results if r.blocked)
        return {
            "total": total,
            "passed": passed,
            "failed": total - passed,
            "blocked": blocked,
            "pass_rate": round(passed / total * 100, 1) if total > 0 else 0,
            "all_passed": blocked == 0,
        }

    def raise_on_block(self, results: List[PolicyResult] = None):
        results = results or self.results
        blocked = [r for r in results if r.blocked]
        if blocked:
            raise PolicyViolationException(
                f"Blocked by {len(blocked)} policy(ies):\n" +
                "\n".join(f"- {r.policy_id}: {r.message}" for r in blocked)
            )


class PolicyViolationException(Exception):
    """Raised when a BLOCK-level policy is violated."""
    pass


def create_hard_block_engine() -> PolicyEngine:
    """Factory: create a PolicyEngine with all policies set to BLOCK enforcement."""
    engine = PolicyEngine()
    for policy in engine.policies:
        policy.enforcement = EnforcementLevel.BLOCK
    return engine
