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

import subprocess
import json
import sys
import re
from pathlib import Path
from typing import Any, Optional, Dict, List, Tuple

# Ensure core/ is on sys.path so sibling quality_gate.* imports resolve
_script_dir = Path(__file__).resolve().parent  # quality_gate/
_methodology_root = _script_dir.parent         # core/
if str(_methodology_root) not in sys.path:
    sys.path.insert(0, str(_methodology_root))

try:
    from core.quality_gate.phase_paths import PHASE_ARTIFACT_PATHS
except ImportError:
    pass  # type: ignore[no-redef]


class PhaseTruthVerifier:
    """Phase truth verifier"""

    # Weight configuration
    WEIGHTS = {
        "framework_block": 0.35,      # FrameworkEnforcer BLOCK
        "session_log": 0.25,           # Sessions_spawn.log
        "pytest_pass": 0.25,          # pytest actually passes
        "coverage": 0.15,             # coverage meets threshold
    }

    def __init__(self, project_root: str, phase: int):
        self.project_root = Path(project_root)
        self.phase = phase
        self.results: dict[str, Any] = {}

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
                    except Exception:
                        pass

            has_ab = len(roles) >= 2
            has_sessions = len(sessions) >= 2

            score = 100.0 if has_ab and has_sessions else 50.0 if has_sessions else 0.0
            details = f"{len(sessions)} record(s), {len(roles)} role(s), {len(sessions)} session(s)"

            return has_ab and has_sessions, score, details
        except Exception as e:
            return False, 0.0, f"Error: {e}"

    def check_pytest(self) -> Tuple[bool, float, str]:
        """Check pytest actually passes"""
        try:
            result = subprocess.run(
                ["pytest", "--tb=no", "-q"],
                cwd=self.project_root,
                capture_output=True,
                text=True,
                timeout=120
            )

            passed = result.returncode == 0
            score = 100.0 if passed else 0.0
            details = "pytest all passed" if passed else "pytest has failures"

            return passed, score, details
        except subprocess.TimeoutExpired:
            return False, 0.0, "pytest execution timed out"
        except FileNotFoundError:
            return False, 0.0, "pytest not found"
        except Exception as e:
            return False, 0.0, f"Error: {e}"

    def check_coverage(self) -> Tuple[bool, float, str]:
        """Check coverage"""
        threshold = 70

        try:
            result = subprocess.run(
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

    def get_manual_checklist(self) -> List[Dict]:
        """Generate items requiring manual confirmation"""

        # Phase directory mapping (supports multiple naming conventions)
        phase_dirs = {
            1: ["01-requirements", "01-specify", "requirements", "specify"],
            2: ["02-architecture", "02-plan", "architecture", "plan", "docs"],
            3: ["03-implementation", "03-implement", "implementation", "implement", "src"],
            4: ["04-testing", "04-verify", "testing", "verify"],
            5: ["05-verify", "05-system-test", "verify"],
            6: ["06-quality", "quality"],
            7: ["07-risk", "risk"],
            8: ["08-config", "08-configuration", "config", "configuration"],
        }

        phase_artifacts = {
            1: ["01-requirements/SRS.md", "01-requirements/SPEC_TRACKING.md", "01-requirements/TRACEABILITY_MATRIX.md"],
            2: ["02-architecture/SAD.md", "02-architecture/adr/001-fastapi-proxy-layer.md", "02-architecture/adr/002-redis-caching-strategy.md", "02-architecture/adr/003-circuit-breaker-resilience.md", "02-architecture/adr/004-text-chunking-strategy.md", "02-architecture/adr/005-cli-click-framework.md", "02-architecture/adr/006-audio-converter-ffmpeg.md"],
            3: [
                # Standard path
                "03-implementation/src/",
                "03-implementation/tests/",
                "03-implementation/COMPLIANCE_MATRIX.md",
                # Alternative: app/ structure (e.g., tts-kokoro-v613)
                "app/",
                "app/processing/",
                "app/synth/",
                "app/infrastructure/",
                "tests/",
            ],
            4: ["04-testing/TEST_PLAN.md", "04-testing/TEST_RESULTS.md"],
            5: ["05-verify/BASELINE.md", "05-verify/VERIFICATION_REPORT.md", "05-verify/MONITORING_PLAN.md"],
            6: ["06-quality/QUALITY_REPORT.md", "06-quality/MONITORING_PLAN.md"],
            7: ["07-risk/RISK_ASSESSMENT.md", "07-risk/RISK_REGISTER.md"],
            8: ["08-config/CONFIG_RECORDS.md", "08-config/RELEASE_CHECKLIST.md"],
        }

        checklist = []

        # Add items to confirm based on Phase (check multiple possible locations)
        if self.phase in phase_artifacts:
            dirs_to_check = [None] + phase_dirs.get(self.phase, [])  # None = root directory
            
            for artifact in phase_artifacts[self.phase]:
                exists = False
                found_path = artifact
                
                for dir_prefix in dirs_to_check:
                    if dir_prefix:
                        path = self.project_root / dir_prefix / artifact
                    else:
                        path = self.project_root / artifact
                    
                    if path.exists():
                        exists = True
                        found_path = f"{dir_prefix}/{artifact}" if dir_prefix else artifact
                        break
                
                checklist.append({
                    "item": found_path,
                    "status": "✅ present" if exists else "❌ missing",
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
        # Phase 1-2: only BLOCK + session_log, weights adjusted
        if self.phase < 3:
            checks = [
                ("FrameworkEnforcer BLOCK", self.check_framework_block, 0.60),
                ("Sessions_spawn.log", self.check_session_log, 0.40),
            ]
        # Phase 3-4: 4 checks (includes pytest/coverage)
        elif self.phase <= 4:
            checks = [
                ("FrameworkEnforcer BLOCK", self.check_framework_block, 0.35),
                ("Sessions_spawn.log", self.check_session_log, 0.25),
                ("pytest actually passes", self.check_pytest, 0.25),
                ("test coverage meets threshold", self.check_coverage, 0.15),
            ]
        # Phase 5-8: only BLOCK + session_log (non-code phases)
        else:
            checks = [
                ("FrameworkEnforcer BLOCK", self.check_framework_block, 0.60),
                ("Sessions_spawn.log", self.check_session_log, 0.40),
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
        verdict = "✅ likely genuine" if total_score >= 70 else "❌ highly suspicious"
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
            "passed": total_score >= 70,
            "checks": results,
            "checklist": checklist,
        }


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Phase Truth Verifier")
    parser.add_argument("--phase", type=int, required=True, choices=range(1, 9),
                        help="Phase number (1-8)")
    parser.add_argument("--project", default=".", help="Project root path")

    args = parser.parse_args()

    verifier = PhaseTruthVerifier(args.project, args.phase)
    result = verifier.verify()

    sys.exit(0 if result["passed"] else 1)
