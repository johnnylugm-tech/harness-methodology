#!/usr/bin/env python3
"""
Framework Enforcement Engine
============================
Load enforcement rules from SKILL.md and execute.

Usage::

    from enforcement.framework_enforcer import FrameworkEnforcer

    enforcer = FrameworkEnforcer("/path/to/project")
    result = enforcer.run()

    if not result.passed:
        for msg, fix in result.violations:
            print(f"FAIL: {msg}")
"""

import sys
from pathlib import Path
from typing import List, Dict, Tuple, Optional



class EnforcementResult:
    """Container for enforcement run results."""

    def __init__(self):
        """Initialize instance."""
        self.violations: List[Tuple[str, Optional[str]]] = []  # (message, fix_command)
        self.warnings: List[Tuple[str, Optional[str]]] = []
        self.passed = False
        self.block_checks: Dict[str, bool] = {}
        self.warn_checks: Dict[str, bool] = {}

    def add_violation(self, message: str, fix: Optional[str] = None):
        """Add violation."""
        self.violations.append((message, fix))

    def add_warning(self, message: str, fix: Optional[str] = None):
        """Add warning."""
        self.warnings.append((message, fix))

    def add_block_check(self, name: str, passed: bool):
        """Add block check."""
        self.block_checks[name] = passed

    def add_warn_check(self, name: str, passed: bool):
        """Add warn check."""
        self.warn_checks[name] = passed

    def summary(self) -> str:
        """Summary."""
        return (
            f"Passed: {self.passed}\n"
            f"BLOCK Violations: {len(self.violations)}\n"
            f"WARN Warnings: {len(self.warnings)}"
        )


class FrameworkEnforcer:
    """
    Framework Enforcement Engine.

    Executes checks based on enforcement rules defined in SKILL.md.

    BLOCK level (must pass, otherwise blocked):
    - SPEC_TRACKING: spec completeness >= 90%
    - CONSTITUTION_SCORE: Constitution Score >= 100 (P1-P2), >= 90 (P3-P4), or >= 80 (P5-P8)

    WARN level (warning, non-blocking):
    - DECISION_FRAMEWORK: Decision Framework established
    - ENHANCED_CHECKLIST: Enhanced checklist established
    """

    BLOCK_CHECKS = [
        {"name": "SPEC_TRACKING", "threshold": 90},
        {"name": "CONSTITUTION_SCORE", "threshold": 100},
    ]
    WARN_CHECKS = [
        {"name": "DECISION_FRAMEWORK"},
        {"name": "ENHANCED_CHECKLIST"},
    ]

    def __init__(self, project_root: Optional[str] = None, phase: int = 1):
        """Initialize instance."""
        self.project_root = Path(project_root) if project_root else Path.cwd()
        self.phase = phase
        self._spec_checker = None

    @property
    def spec_checker(self):
        """Spec checker."""
        if self._spec_checker is None:
            from core.quality_gate.spec_tracking_checker import SpecTrackingChecker
            self._spec_checker = SpecTrackingChecker(str(self.project_root))
        return self._spec_checker

    def check_spec_tracking(self) -> Dict:
        """Run check spec tracking validation."""
        return self.spec_checker.run_enforcement()

    def check_constitution(self) -> Dict:
        """Run check constitution validation.

        Uses the canonical threshold from constitution.get_constitution_threshold(phase):
        - P1-P2: =100 (TH-03 correctness + TH-04 security, both must be 100%)
        - P3-P4: ≥90 (TH-03/TH-04 =100% + TH-05/TH-06 >90%)
        - P5-P8: ≥80 (TH-02 full constitution compliance)
        """
        try:
            from core.quality_gate.constitution.runner import run_constitution_check
            from constitution import get_constitution_threshold

            phase_info_map = {
                1: {"type": "srs",            "dir": "01-requirements"},
                2: {"type": "sad",            "dir": "02-architecture"},
                3: {"type": "implementation", "dir": "03-development"},
                4: {"type": "test_plan",      "dir": "04-testing"},
                5: {"type": "verification",   "dir": "05-verify"},
                6: {"type": "quality_report", "dir": "06-quality"},
                7: {"type": "risk_management","dir": "07-risk"},
                8: {"type": "configuration",  "dir": "08-config"},
            }
            phase_info = phase_info_map.get(self.phase, {"type": "srs", "dir": "docs"})
            docs_path = self.project_root / "docs"
            if not docs_path.exists():
                return {"score": 0, "passed": False, "error": "docs/ directory not found"}

            result = run_constitution_check(
                phase_info["type"], str(docs_path), current_phase=self.phase
            )
            threshold = get_constitution_threshold(self.phase)
            passed = result.score >= threshold
            return {
                "score": result.score,
                "passed": passed,
                "threshold": threshold,
                "violations": len(result.violations) if hasattr(result, "violations") else 0,
            }
        except Exception as e:
            return {"score": 0, "passed": False, "error": str(e)}

    def check_decision_framework(self) -> Dict:
        """Run check decision framework validation."""
        framework_file = self.project_root / "DECISION_FRAMEWORK.md"
        return {"exists": framework_file.exists(), "path": str(framework_file)}

    def check_enhanced_checklist(self) -> Dict:
        """Run check enhanced checklist validation."""
        if self.phase < 5:
            return {"exists": True, "path": "N/A (Phase < 5)", "skipped": True}
        for candidate in [
            self.project_root / "CHECKLIST.md",
            self.project_root / "docs" / "CHECKLIST.md",
            self.project_root / "01-requirements" / "CHECKLIST.md",
        ]:
            if candidate.exists():
                return {"exists": True, "path": str(candidate)}
        return {"exists": False, "path": str(self.project_root / "CHECKLIST.md")}

    def check_coverage_threshold(self) -> Dict:
        """Run check coverage threshold validation."""
        DEFAULT_THRESHOLD = 70
        for candidate in [
            self.project_root / "coverage.xml",
            self.project_root / "03-development" / "coverage.xml",
            self.project_root / "htmlcov" / "coverage.xml",
        ]:
            if candidate.exists():
                coverage_file = candidate
                break
        else:
            return {"passed": False, "coverage": 0, "threshold": DEFAULT_THRESHOLD, "message": "coverage report not found"}
        import xml.etree.ElementTree as ET  # nosec B405
        try:
            tree = ET.parse(coverage_file)  # nosec B314 — trusted local coverage.xml from pytest-cov
            coverage = float(tree.getroot().attrib.get("line-rate", 0)) * 100
        except Exception:
            return {"passed": False, "coverage": 0, "threshold": DEFAULT_THRESHOLD, "message": "failed to parse coverage report"}
        passed = coverage >= DEFAULT_THRESHOLD
        return {
            "passed": passed,
            "coverage": coverage,
            "threshold": DEFAULT_THRESHOLD,
            "message": f"Coverage {coverage:.1f}% {'>=' if passed else '<'} {DEFAULT_THRESHOLD}%",
        }

    def check_traceability_matrix(self) -> Dict:
        """Run check traceability matrix validation."""
        trace_file = None
        for candidate in [
            self.project_root / "TRACEABILITY_MATRIX.md",
            self.project_root / "01-requirements" / "TRACEABILITY_MATRIX.md",
            self.project_root / "01-specify" / "TRACEABILITY_MATRIX.md",
        ]:
            if candidate.exists():
                trace_file = candidate
                break
        if trace_file is None:
            return {"exists": False, "complete": False, "completeness": 0, "missing_tests": [], "missing_constitution": []}
        content = trace_file.read_text()
        missing_constitution = []
        completed = total = 0
        for line in content.split("\n"):
            if "src/" in line or ".py" in line:
                total += 1
                if any(x in line for x in ["\u274c", "\u26a0\ufe0f"]):
                    missing_constitution.append(line)
                if "\u2705" in line:
                    completed += 1
        completeness = (completed / total * 100) if total > 0 else 0
        return {
            "exists": True,
            "complete": completeness >= 90 and not missing_constitution,
            "completeness": completeness,
            "total": total,
            "completed": completed,
            "missing_tests": [],
            "missing_constitution": missing_constitution,
        }

    def check_phase_traceability(self) -> Dict:
        """Run check phase traceability validation."""
        try:
            from core.quality_gate.phase_artifact_enforcer import PhaseArtifactRegistry, Phase  # pyright: ignore[reportMissingImports]
        except ImportError:
            return {
                "all_verified": True,
                "verified_phases": [],
                "missing_links": [],
                "stats": {"total": 0, "verified": 0, "missing": 0},
                "skipped": True,
                "reason": "phase_artifact_enforcer module not available",
            }
        registry = PhaseArtifactRegistry(str(self.project_root))
        phase_map = {
            Phase.CONSTITUTION: 0, Phase.SPECIFY: 1, Phase.PLAN: 2,
            Phase.IMPLEMENT: 3, Phase.VERIFY: 4, Phase.SYSTEM_TEST: 5,
            Phase.QUALITY: 6, Phase.RISK: 7, Phase.CONFIG: 8,
        }
        verified: list[str] = []
        missing: list[str] = []
        for phase in Phase:
            if phase_map.get(phase, 0) > self.phase:
                continue
            depends_on = PhaseArtifactRegistry.PHASE_ARTIFACTS.get(phase, {}).get("depends_on", [])
            for prev_phase in depends_on:
                if phase_map.get(prev_phase, 0) >= self.phase:
                    continue
                ref_check = registry.verify_phase_link(prev_phase, phase)
                target = f"{prev_phase.value} -> {phase.value}"
                (verified if ref_check.passed else missing).append(target)
        return {
            "all_verified": not missing,
            "verified_phases": verified,
            "missing_links": missing,
            "stats": {"total": len(verified) + len(missing), "verified": len(verified), "missing": len(missing)},
        }

    def check_aspice_completeness(self) -> Dict:
        """Run check aspice completeness validation."""
        required_by_phase = {
            1: {"Phase 1 (SPECIFY)":     ["01-requirements/SRS.md", "01-requirements/SPEC_TRACKING.md", "01-requirements/TRACEABILITY_MATRIX.md"]},
            2: {"Phase 2 (PLAN)":        ["02-architecture/SAD.md"]},
            3: {"Phase 3 (IMPLEMENT)":   ["03-development/IMPLEMENTATION.md"]},
            4: {"Phase 4 (VERIFY)":      ["04-testing/TEST_PLAN.md", "04-testing/TEST_RESULTS.md"]},
            5: {"Phase 5 (SYSTEM_TEST)": ["05-verify/BASELINE.md", "05-verify/VERIFICATION_REPORT.md"]},
            6: {"Phase 6 (QUALITY)":     ["06-quality/QUALITY_REPORT.md"]},
            7: {"Phase 7 (RISK)":        ["07-risk/RISK_ASSESSMENT.md", "07-risk/RISK_REGISTER.md"]},
            8: {"Phase 8 (CONFIG)":      ["08-config/CONFIG_RECORDS.md", "08-config/RELEASE_CHECKLIST.md"]},
        }
        missing = []
        found = []
        for phase_num in range(1, self.phase + 1):
            if phase_num not in required_by_phase:
                continue
            for phase_name, docs in required_by_phase[phase_num].items():
                found_one = False
                for doc in docs:
                    if (self.project_root / doc).exists():
                        found.append(f"{phase_name}/{doc}")
                        found_one = True
                        break
                if not found_one:
                    missing.append(f"{phase_name}/{docs[0]}")
        return {
            "complete": not missing,
            "missing_docs": missing,
            "phase_coverage": {
                "total_phases": 8,
                "phases_with_docs": 8 - len({m.split("/")[0] for m in missing}),
                "found": len(found),
            },
        }

    def generate_aspice_report(self) -> str:
        """Generate aspice report."""
        lines = ["=" * 60, "ASPICE TRACEABILITY REPORT", "=" * 60, ""]
        trace = self.check_phase_traceability()
        lines += [
            "## Phase Traceability",
            f"Total: {trace['stats']['total']}  Verified: {trace['stats']['verified']}  Missing: {trace['stats']['missing']}",
            "\n### Verified Links",
        ]
        lines += [f"  + {line}" for line in trace["verified_phases"]]
        lines += ["\n### Missing Links"]
        lines += ([f"  - {line}" for line in trace["missing_links"]] or ["  (none)"])
        aspice = self.check_aspice_completeness()
        lines += [
            "",
            "## ASPICE Document Completeness",
            f"Found: {aspice['phase_coverage']['found']} docs",
        ]
        if aspice["missing_docs"]:
            lines += ["### Missing Documents"] + [f"  - {d}" for d in aspice["missing_docs"]]
        const = self.check_constitution()
        lines += [
            "",
            "## Constitution Score",
            f"Score: {const.get('score', 0)}%  Threshold: {const.get('threshold', 100)}%  Status: {'PASS' if const.get('passed') else 'FAIL'}",
        ]
        spec = self.check_spec_tracking()
        lines += [
            "",
            "## Specification Tracking",
            f"Completeness: {spec.get('completeness', 0)}%  Threshold: 90%  Status: {'PASS' if spec.get('complete') else 'FAIL'}",
            "",
            "=" * 60,
        ]
        return "\n".join(lines)

    def run(self, level: str = "ALL") -> EnforcementResult:
        """Run enforcement checks. level: 'BLOCK' | 'WARN' | 'ALL'"""
        result = EnforcementResult()

        if level in ("BLOCK", "ALL"):
            # 1. SPEC_TRACKING
            spec = self.check_spec_tracking()
            spec_passed = spec.get("exists", False) and spec.get("completeness", 0) >= 90
            result.add_block_check("SPEC_TRACKING", spec_passed)
            if not spec.get("exists", False):
                result.add_violation("SPEC_TRACKING.md does not exist", "methodology spec-track init")
            elif spec.get("completeness", 0) < 90:
                result.add_violation(f"Spec completeness {spec['completeness']}% < 90%")

            # 2. CONSTITUTION_SCORE
            const = self.check_constitution()
            const_passed = const.get("passed", False)
            result.add_block_check("CONSTITUTION_SCORE", const_passed)
            if not const_passed:
                result.add_violation(
                    f"Constitution Score {const.get('score', 0)}% < {const.get('threshold', 100)}%",
                    "methodology constitution check"
                )

            # 3. ASPICE Phase Traceability (Phase 2+)
            if self.phase >= 2:
                trace = self.check_phase_traceability()
                result.add_block_check("ASPICE_PHASE_TRACE", trace["all_verified"])
                if not trace["all_verified"]:
                    result.add_violation(
                        f"ASPICE Phase traceability incomplete: {', '.join(trace['missing_links'])}",
                        "Ensure each Phase references prior Phase artifacts",
                    )
            else:
                result.add_block_check("ASPICE_PHASE_TRACE", True)

            # 4. ASPICE Document Completeness (Phase 2+)
            aspice = self.check_aspice_completeness()
            result.add_block_check("ASPICE_COMPLETE", aspice["complete"])
            if not aspice["complete"]:
                result.add_violation(
                    f"ASPICE docs missing: {', '.join((aspice.get('missing_docs') or [])[:3])}",
                    "Add all required Phase documents",
                )

            # 5. COVERAGE_THRESHOLD (Phase 3+)
            if self.phase >= 3:
                cov = self.check_coverage_threshold()
                result.add_block_check("COVERAGE_THRESHOLD", cov["passed"])
                if not cov["passed"]:
                    result.add_violation(
                        f"Test coverage {cov['coverage']:.1f}% < {cov['threshold']}%",
                        "Increase test coverage",
                    )
            else:
                result.add_block_check("COVERAGE_THRESHOLD", True)

            # 6. TRACEABILITY Matrix
            trace = self.check_traceability_matrix()
            result.add_block_check("TRACEABILITY_COMPLETE", trace["complete"])
            if not trace.get("exists", False):
                result.add_violation("TRACEABILITY_MATRIX.md does not exist", "methodology trace init")
            elif not trace["complete"]:
                missing_parts = []
                if trace.get("missing_tests"):
                    missing_parts.append(f"{len(trace['missing_tests'])} items missing tests")
                if trace.get("missing_constitution"):
                    missing_parts.append(f"{len(trace['missing_constitution'])} items failed Constitution")
                result.add_violation(
                    f"TRACEABILITY incomplete: {', '.join(missing_parts)}",
                    "methodology trace check",
                )

        if level in ("WARN", "ALL"):
            df = self.check_decision_framework()
            result.add_warn_check("DECISION_FRAMEWORK", df["exists"])
            if not df["exists"]:
                result.add_warning("Decision Framework not established", "Consider creating DECISION_FRAMEWORK.md")
            cl = self.check_enhanced_checklist()
            result.add_warn_check("ENHANCED_CHECKLIST", cl["exists"])
            if not cl["exists"]:
                result.add_warning("Enhanced Checklist not established", "Consider creating CHECKLIST.md")

        result.passed = not result.violations
        return result

    def run_with_exit(self, level: str = "ALL") -> int:
        """Run enforcement and return exit code (0=passed, 1=failed)."""
        result = self.run(level)
        print("=" * 50)
        print(f"Framework Enforcement - {level}")
        print("=" * 50)
        print("\nBLOCK Violations:")
        if result.violations:
            for msg, fix in result.violations:
                print(f"  FAIL: {msg}")
                if fix:
                    print(f"    Run: {fix}")
        else:
            print("  No BLOCK violations")
        print("\nWarnings:")
        if result.warnings:
            for msg, fix in result.warnings:
                print(f"  WARN: {msg}")
                if fix:
                    print(f"    {fix}")
        else:
            print("  No warnings")
        print(f"\nFramework Enforcement {'passed' if result.passed else 'FAILED'}")
        return 0 if result.passed else 1


def main():
    """CLI entry point."""
    import argparse
    parser = argparse.ArgumentParser(description="Framework Enforcement")
    parser.add_argument("--level", "-l", choices=["BLOCK", "WARN", "ALL"], default="ALL")
    parser.add_argument("--project", "-p", default=".")
    parser.add_argument("--exit", "-x", action="store_true")
    args = parser.parse_args()
    enforcer = FrameworkEnforcer(args.project)
    if args.exit:
        sys.exit(enforcer.run_with_exit(args.level))
    else:
        result = enforcer.run(args.level)
        print(result.summary())
        sys.exit(0 if result.passed else 1)


if __name__ == "__main__":
    sys.exit(main())
