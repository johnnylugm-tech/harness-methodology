"""
Quality Gate Core Module.

Provides AutoQualityGate with linter, complexity, coverage, style checkers.
Also re-exports SABSpec and extract_sab_from_sad from sab_parser.
"""

from __future__ import annotations

from .sab_parser import SABSpec as SABSpec, extract_sab_from_sad as extract_sab_from_sad
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any  # noqa: F401 — may be used by derived classes


# The project's own line-coverage floor, from the SAB's quality_targets.
#
# Round 27 站5. Four call sites read this independently — cli/phase_cmds.py,
# cli/gate_cmds.py, cli/fr_cmds.py and scripts/phase8_doc_gen.py — each with its
# own default, its own coercion (float / int / float-in-a-try) and its own idea
# of what a malformed value means. Same key, four readings.
#
# What it is NOT: the `test_coverage` threshold in the gate YAML. That number
# drives finalize_gate's per-dimension verdict; THIS one drives the live
# coverage check Gate 1 runs against the FR's own code. A run where both happen
# to be present makes the two indistinguishable in the output — a project whose
# quality_targets said 100 produced 23 gate-block lessons all reading
# "test_coverage scored 98.9, needs 100.0" while the gate YAML's threshold for
# that dimension was 80, and nothing in the message said which number had
# actually blocked, or that they were different numbers from different files.
DEFAULT_MIN_COVERAGE: float = 80.0


def min_coverage_floor(manifest: "dict | None") -> float:
    """The project's declared line-coverage floor, or the framework default.

    A malformed value falls back rather than raising: this feeds a threshold
    comparison, and a typo in quality_targets should not crash a gate. It is a
    FLOOR — callers compare `measured >= floor`.
    """
    try:
        raw = (manifest or {}).get("quality_targets", {}).get(
            "min_coverage", DEFAULT_MIN_COVERAGE
        )
        return float(raw)
    except (AttributeError, TypeError, ValueError):
        return DEFAULT_MIN_COVERAGE


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
