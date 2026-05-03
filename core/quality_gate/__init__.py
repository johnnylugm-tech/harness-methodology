"""
Quality Gate Core Module.

Provides AutoQualityGate with linter, complexity, coverage, style checkers.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any  # noqa: F401 — may be used by derived classes


@dataclass
class Violation:
    check_type: str
    rule_id: str
    message: str
    file: str | None = None
    line: int | None = None
    column: int | None = None
    severity: str = "warning"
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"check_type": self.check_type, "rule_id": self.rule_id,
                "message": self.message, "file": self.file, "line": self.line,
                "column": self.column, "severity": self.severity, "extra": self.extra}


class BaseChecker:
    name: str = "base"
    def run(self, artifacts: dict) -> list:
        raise NotImplementedError


class LinterChecker(BaseChecker):
    name = "linter"
    def run(self, artifacts: dict) -> list:
        return [Violation(check_type="linter", rule_id=e.get("rule", "UNKNOWN"),
                          message=e.get("message", ""), file=e.get("file"),
                          line=e.get("line"), column=e.get("column"),
                          severity=e.get("severity", "warning"), extra=e.get("extra", {}))
                for e in artifacts.get("linter_output", [])]


class ComplexityChecker(BaseChecker):
    name = "complexity"
    DEFAULT_THRESHOLD = 10
    def run(self, artifacts: dict, threshold: int | None = None) -> list:
        threshold = threshold or self.DEFAULT_THRESHOLD
        return [Violation(check_type="complexity", rule_id=f"CC{threshold}p",
                          message=f"Function '{f.get('name', '?')}' CC={f.get('cc',0)} (threshold={threshold})",
                          file=f.get("file"), line=f.get("start_line"), severity="warning",
                          extra={"cc": f.get("cc", 0), "threshold": threshold})
                for f in artifacts.get("functions", []) if f.get("cc", 0) > threshold]


class CoverageChecker(BaseChecker):
    name = "coverage"
    DEFAULT_MIN_COVERAGE = 80.0
    def run(self, artifacts: dict, min_coverage: float | None = None) -> list:
        min_coverage = min_coverage or self.DEFAULT_MIN_COVERAGE
        report = artifacts.get("coverage_report", {})
        violations = []
        total = report.get("total", 100.0)
        if total < min_coverage:
            violations.append(Violation(check_type="coverage", rule_id="COVERAGE",
                                        message=f"Overall coverage {total:.1f}% below {min_coverage}%",
                                        severity="warning", extra={"coverage": total}))
        for fe in report.get("files", []):
            fc = fe.get("coverage", 100.0)
            if fc < min_coverage:
                violations.append(Violation(check_type="coverage", rule_id="COVERAGE",
                                            message=f"File {fe.get('file','?')} {fc:.1f}% below {min_coverage}%",
                                            file=fe.get("file"), severity="warning",
                                            extra={"coverage": fc}))
        return violations


class StyleChecker(BaseChecker):
    name = "style"
    def run(self, artifacts: dict) -> list:
        return [Violation(check_type="style", rule_id=e.get("rule", "STYLE"),
                          message=e.get("message", ""), file=e.get("file"),
                          line=e.get("line"), severity="info", extra=e.get("extra", {}))
                for e in artifacts.get("style_violations", [])]


class AutoQualityGate:
    """Multi-check quality gate."""

    DEFAULT_CHECKERS = [LinterChecker(), ComplexityChecker(), CoverageChecker(), StyleChecker()]

    def __init__(self, checkers=None, fail_fast: bool = False, feedback_store=None) -> None:
        self.checkers = checkers or self.DEFAULT_CHECKERS
        self.fail_fast = fail_fast
        self._feedback_store = feedback_store

    def check(self, *, phase: int, artifacts: dict) -> dict:
        all_violations, checks_run, check_results, passed = [], [], [], True
        for checker in self.checkers:
            checks_run.append(checker.name)
            try:
                violations = checker.run(artifacts)
                check_results.append({"checker": checker.name, "violations": len(violations)})
                for v in violations:
                    all_violations.append(v.to_dict())
                    if v.severity == "error":
                        passed = False
                        if self.fail_fast:
                            break
            except Exception as exc:
                all_violations.append(Violation(check_type=checker.name, rule_id="CHECKER_ERROR",
                                                 message=str(exc), severity="error").to_dict())
                check_results.append({"checker": checker.name, "error": str(exc)})
                passed = False
                if self.fail_fast:
                    break
        passed = passed and not any(v.get("severity") == "error" for v in all_violations)
        return {"phase": phase, "passed": passed,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "violations": all_violations, "checks_run": checks_run,
                "check_results": check_results}

    def run(self, *, phase: int, artifacts: dict) -> dict:
        return self.check(phase=phase, artifacts=artifacts)
