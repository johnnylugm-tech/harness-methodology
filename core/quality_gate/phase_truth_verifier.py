#!/usr/bin/env python3
"""
Phase Truth Verifier
====================
Verify whether a Phase truly passed.

Output:
- Automatically checked score
- List of items requiring manual confirmation

Usage:
    from quality_gate.phase_truth_verifier import PhaseTruthVerifier

    verifier = PhaseTruthVerifier("/path/to/project", 3)
    result = verifier.verify()
"""

import subprocess  # nosec B404
import json
import sys
import re
from pathlib import Path
from typing import Any, Dict, List, Tuple

# Ensure core/ is on sys.path so sibling quality_gate.* imports resolve
_script_dir = Path(__file__).resolve().parent  # quality_gate/
_methodology_root = _script_dir.parent         # core/
if str(_methodology_root) not in sys.path:
    sys.path.insert(0, str(_methodology_root))


class PhaseTruthVerifier:
    """Phase truth verifier"""

    def __init__(self, project_root: str, phase: int):
        self.project_root = Path(project_root)
        self.phase = phase
        self.results: dict[str, Any] = {}

    def to_fix_context(self) -> dict:
        """Serialize verify failures for AutoFixEngine consumption."""
        failing = {k: v for k, v in self.results.items() if not v.get("passed", True)}
        problem_type = "phase_truth_low" if len(failing) > 1 else "low_constitution_score"
        return {
            "source": "phase_truth_verifier",
            "problem_type": problem_type,
            "severity": "critical",
            "phase": self.phase,
            "failing_checks": list(failing.keys()),
            "results": {k: {"passed": v.get("passed", True), "score": v.get("score", 0)}
                       for k, v in self.results.items()},
        }

    def check_framework_block(self) -> Tuple[bool, float, str]:
        """Check FrameworkEnforcer BLOCK"""
        try:
            # Add enforcement to path if needed
            enforcement_path = self.project_root / "enforcement"
            if enforcement_path.exists():
                if str(enforcement_path) not in sys.path:
                    sys.path.insert(0, str(self.project_root))

            from enforcement.framework_enforcer import FrameworkEnforcer
            enforcer = FrameworkEnforcer(str(self.project_root), phase=self.phase)
            result = enforcer.run(level="BLOCK")

            passed = result.passed
            score = 100.0 if passed else 0.0
            details = f"{len(result.block_checks)} check(s), {len(result.violations)} violation(s)"

            return passed, score, details
        except ImportError as e:
            return False, 0.0, f"Cannot import FrameworkEnforcer: {e}"
        except Exception as e:
            return False, 0.0, f"Error: {e}"

    def check_session_log(self) -> Tuple[bool, float, str]:
        """Check Sessions_spawn.log"""
        log_file = self.project_root / "sessions_spawn.log"

        if not log_file.exists():
            return False, 0.0, "sessions_spawn.log not found"

        try:
            content = log_file.read_text().strip()
            
            # Support two formats:
            # 1. Line-by-line JSON (one JSON object per line)
            # 2. Single JSON (contains sessions array)
            roles = set()
            sessions = set()
            
            # Try parsing as whole JSON
            try:
                data = json.loads(content)
                if isinstance(data, dict) and "sessions" in data:
                    # Format 2: {"sessions": [...]}
                    for entry in data.get("sessions", []):
                        roles.add(entry.get("role", ""))
                        sessions.add(entry.get("session_id", ""))
                elif isinstance(data, list):
                    # Format 1: directly a list
                    for entry in data:
                        roles.add(entry.get("role", ""))
                        sessions.add(entry.get("session_id", ""))
                else:
                    # May be single entry
                    roles.add(data.get("role", ""))
                    sessions.add(data.get("session_id", ""))
            except json.JSONDecodeError:
                # Try line-by-line parsing
                lines = [line for line in content.split("\n") if line]
                for line in lines:
                    try:
                        entry = json.loads(line)
                        roles.add(entry.get("role", ""))
                        sessions.add(entry.get("session_id", ""))
                    except Exception:  # nosec B110
                        pass

            has_ab = len(roles) >= 2
            has_sessions = len(sessions) >= 2

            score = 100.0 if has_ab and has_sessions else 50.0 if has_sessions else 0.0
            details = f"{len(sessions)} record(s), {len(roles)} role(s), {len(sessions)} session(s)"

            return has_ab and has_sessions, score, details
        except Exception as e:
            return False, 0.0, f"Error: {e}"

    def check_pytest(self) -> Tuple[bool, float, str]:
        """Check pytest actually passes; capture structured failure output."""
        try:
            result = subprocess.run(  # nosec B603 B607
                ["pytest", "--tb=line", "-q", "--no-header"],
                cwd=self.project_root,
                capture_output=True,
                text=True,
                timeout=120
            )

            passed = result.returncode == 0
            score = 100.0 if passed else 0.0

            if passed:
                details = "pytest all passed"
            else:
                failures = _parse_failure_count(result.stdout + result.stderr)
                details = f"pytest has {failures} failure(s)"

            return passed, score, details
        except subprocess.TimeoutExpired:
            return False, 0.0, "pytest execution timed out"
        except FileNotFoundError:
            return False, 0.0, "pytest not found"
        except Exception as e:
            return False, 0.0, f"Error: {e}"

    def _get_coverage_threshold(self) -> int:
        """Return the coverage threshold for the current phase.

        TH-11: >=70% for P3
        TH-12: >=80% for P4+
        P1-P2: N/A (no coverage check)
        """
        if self.phase <= 2:
            return 0
        if self.phase == 3:
            return 70
        return 80

    def check_coverage(self) -> Tuple[bool, float, str]:
        """Check coverage against phase-dependent threshold."""
        threshold = self._get_coverage_threshold()
        if threshold == 0:
            return True, 100.0, "No coverage requirement for P1-P2"

        try:
            result = subprocess.run(  # nosec B603 B607
                ["pytest", "--cov=.","--cov-report=term-missing","--tb=no","-q"],
                cwd=self.project_root,
                capture_output=True,
                text=True,
                timeout=120
            )

            # Try parsing coverage from output
            output = result.stdout + result.stderr

            coverage_match = re.search(r"TOTAL\s+\d+\s+\d+\s+(\d+)%", output)
            if coverage_match:
                coverage = int(coverage_match.group(1))
            else:
                coverage_match = re.search(r" coverage: (\d+)%", output)
                coverage = int(coverage_match.group(1)) if coverage_match else 0

            passed = coverage >= threshold
            score = min(100.0, coverage) if passed else coverage
            details = f"coverage {coverage}% (threshold {threshold}%)"

            return passed, score, details
        except Exception as e:
            return False, 0.0, f"Error: {e}"

    def check_previous_phase_artifacts(self) -> Tuple[bool, float, str]:
        """Check that the previous phase produced required deliverables.

        Uses PhaseArtifactRegistry to verify the ASPICE chain is intact
        before proceeding with the current phase.
        """
        if self.phase <= 1:
            return True, 100.0, "P1 has no previous phase"

        from core.quality_gate.phase_artifact_enforcer import PhaseArtifactRegistry  # pyright: ignore[reportMissingImports]

        registry = PhaseArtifactRegistry(str(self.project_root))
        result = registry.verify_phase_chain(self.phase)

        if result["all_verified"]:
            return True, 100.0, f"All {result['stats']['verified']} phase links verified"
        return False, 0.0, (
            f"{result['stats']['missing']} broken phase link(s): "
            + "; ".join(result["missing_links"][:3])
        )

    def _check_artifact_content_quality(self, artifact_path: Path) -> Dict[str, Any]:
        """Perform basic automated content quality check on an artifact.

        Detects hollow templates that exist but have no real content.
        """
        if not artifact_path.exists() or not artifact_path.is_file():
            return {"quality": "missing", "issues": ["file not found"]}

        try:
            content = artifact_path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            return {"quality": "unreadable", "issues": ["cannot read file"]}

        issues = []
        if len(content.strip()) < 200:
            issues.append("Content <200 chars — may be hollow template")
        section_count = content.count("\n## ") + content.count("\n# ")
        if section_count < 2:
            issues.append("Fewer than 2 markdown sections — may lack structure")
        has_ref = bool(re.search(r"\[(TASK|FR|NFR)-\d+\]", content, re.IGNORECASE))
        if not has_ref:
            issues.append("No task/FR/NFR references found")

        quality = "good" if not issues else "suspicious"
        return {"quality": quality, "issues": issues}

    def get_manual_checklist(self) -> List[Dict]:
        """Generate items requiring manual confirmation"""

        phase_artifacts = {
            1: ["01-requirements/SRS.md", "01-requirements/SPEC_TRACKING.md", "01-requirements/TRACEABILITY_MATRIX.md"],
            2: ["02-architecture/SAD.md"],
            3: [
                "03-development/src/",
                "03-development/tests/",
            ],
            4: ["04-testing/TEST_PLAN.md", "04-testing/TEST_RESULTS.md"],
            5: ["05-verification/BASELINE.md", "05-verification/VERIFICATION_REPORT.md"],
            6: ["06-quality/QUALITY_REPORT.md"],
            7: ["07-risk/RISK_ASSESSMENT.md", "07-risk/RISK_REGISTER.md"],
            8: ["08-config/CONFIG_RECORDS.md", "08-config/RELEASE_CHECKLIST.md"],
        }

        checklist = []

        # Add items to confirm based on Phase (artifact paths are relative to project root)
        if self.phase in phase_artifacts:
            dirs_to_check = [None]  # artifact paths already include the 0X-name/ prefix
            
            for artifact in phase_artifacts[self.phase]:
                exists = False
                found_path = artifact
                quality = {"quality": "missing", "issues": []}
                
                for dir_prefix in dirs_to_check:
                    if dir_prefix:
                        path = self.project_root / dir_prefix / artifact
                    else:
                        path = self.project_root / artifact
                    
                    if path.exists():
                        exists = True
                        found_path = f"{dir_prefix}/{artifact}" if dir_prefix else artifact
                        quality = self._check_artifact_content_quality(path)
                        break


                checklist.append({
                    "item": found_path,
                    "status": "✅ present" if exists else "❌ missing",
                    "content_quality": quality,
                    "action": "Pick 1 at random, confirm content is not a hollow template"
                })

        # General checks
        checklist.extend([
            {
                "item": "DEVELOPMENT_LOG.md",
                "status": "✅ present" if (self.project_root / "DEVELOPMENT_LOG.md").exists() else "❌ missing",
                "action": "Check for actual command output (text, not screenshot)"
            },
            {
                "item": "sessions_spawn.log",
                "status": "✅ present" if (self.project_root / "sessions_spawn.log").exists() else "❌ missing",
                "action": "Pick 1 record at random, confirm task description is reasonable"
            },
        ])

        return checklist

    def verify(self) -> Dict:
        """Execute full verification"""

        print("=" * 60)
        print(f"Phase {self.phase} truth verification")
        print("=" * 60)
        print()

        # Execute checks (adjust weights based on Phase)
        # Phase 1-2: BLOCK + session_log, no previous phase artifacts
        if self.phase <= 1:
            checks = [
                ("FrameworkEnforcer BLOCK", self.check_framework_block, 0.60),
                ("Sessions_spawn.log", self.check_session_log, 0.40),
            ]
        elif self.phase <= 2:
            checks = [
                ("FrameworkEnforcer BLOCK", self.check_framework_block, 0.50),
                ("Sessions_spawn.log", self.check_session_log, 0.35),
                ("Previous phase artifacts", self.check_previous_phase_artifacts, 0.15),
            ]
        # Phase 3-4: 5 checks (includes pytest/coverage + previous phase)
        elif self.phase <= 4:
            checks = [
                ("FrameworkEnforcer BLOCK", self.check_framework_block, 0.30),
                ("Sessions_spawn.log", self.check_session_log, 0.22),
                ("pytest actually passes", self.check_pytest, 0.22),
                ("test coverage meets threshold", self.check_coverage, 0.13),
                ("Previous phase artifacts", self.check_previous_phase_artifacts, 0.13),
            ]
        # Phase 5-8: BLOCK + session_log + previous phase (non-code phases)
        else:
            checks = [
                ("FrameworkEnforcer BLOCK", self.check_framework_block, 0.50),
                ("Sessions_spawn.log", self.check_session_log, 0.35),
                ("Previous phase artifacts", self.check_previous_phase_artifacts, 0.15),
            ]

        total_score = 0.0
        results = []

        for name, check_func, weight in checks:
            passed, score, details = check_func()

            weighted_score = score * weight
            total_score += weighted_score

            status = "✅" if passed else "❌"
            results.append({
                "name": name,
                "passed": passed,
                "score": score,
                "details": details,
                "weight": weight,
            })

            print(f"{status} {name:<30} {details}")

        print()
        print("=" * 60)
        verdict = "✅ likely genuine" if total_score >= 90 else "❌ highly suspicious"
        print(f"Total score: {total_score:.0f}% - {verdict}")
        print("=" * 60)
        print()

        # Output items requiring manual confirmation
        print("[Manual Confirmation Required]")
        print()

        checklist = self.get_manual_checklist()
        for i, item in enumerate(checklist, 1):
            print(f"{i}. [{item['item']}]")
            print(f"   Status: {item['status']}")
            print(f"   → {item['action']}")
            print()

        return {
            "phase": self.phase,
            "total_score": total_score,
            "passed": total_score >= 90,
            "checks": results,
            "checklist": checklist,
        }


def _parse_failure_count(output: str) -> int:
    """Count FAILED lines in pytest output."""
    import re
    return len(re.findall(r"^FAILED\s+", output, re.MULTILINE))


if __name__ == "__main__":  # pragma: no cover
    import argparse

    parser = argparse.ArgumentParser(description="Phase Truth Verifier")
    parser.add_argument("--phase", type=int, required=True, choices=range(1, 9),
                        help="Phase number (1-8)")
    parser.add_argument("--project", default=".", help="Project root path")

    args = parser.parse_args()

    verifier = PhaseTruthVerifier(args.project, args.phase)
    result = verifier.verify()

    sys.exit(0 if result["passed"] else 1)
