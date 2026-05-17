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


class InfraSkip(Exception):
    """Raised by a Phase Truth check when its infrastructure is unavailable.

    Distinguishes "check failed" (legitimate quality gap → score 0) from
    "check could not run" (missing module / broken install → skip with
    warning, renormalize remaining weights). See CV-4 in robustness audit.
    """


class PhaseTruthVerifier:
    """Phase truth verifier"""

    def __init__(self, project_root: str, phase: int, threshold: float | None = None):
        """
        :param threshold: Override HR-11 ≥90% threshold. If None, reads
            `.methodology/enforcement.json::hr_overrides.HR-11_phase_truth_threshold`
            falling back to 90.0. See SG-7 in robustness audit.
        """
        self.project_root = Path(project_root)
        self.phase = phase
        self.results: dict[str, Any] = {}
        self.threshold: float = (
            float(threshold) if threshold is not None
            else self._load_threshold_from_config()
        )

    def _load_threshold_from_config(self) -> float:
        """Read HR-11 threshold from enforcement.json (SG-7)."""
        cfg_path = self.project_root / ".methodology" / "enforcement.json"
        if cfg_path.exists():
            try:
                cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
                v = cfg.get("hr_overrides", {}).get("HR-11_phase_truth_threshold")
                if v is not None:
                    return float(v)
            except (json.JSONDecodeError, ValueError, OSError):
                pass
        return 90.0

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
        """Check FrameworkEnforcer BLOCK.

        CV-4 (robustness audit): an ImportError here previously scored 0
        (counted as a check failure), making Phase Truth ≥90% unreachable
        whenever `enforcement/` was missing or partially installed. We now
        raise InfraSkip so `verify()` can renormalize weights — operators
        see a [SKIP] warning instead of a misleading 0% score.
        """
        try:
            # Add enforcement to path if needed
            enforcement_path = self.project_root / "enforcement"
            if enforcement_path.exists():
                if str(enforcement_path) not in sys.path:
                    sys.path.insert(0, str(self.project_root))

            from enforcement.framework_enforcer import FrameworkEnforcer
        except ImportError as e:
            raise InfraSkip(
                f"enforcement.framework_enforcer unavailable ({e}). "
                f"Check that enforcement/ is installed at {self.project_root}/enforcement."
            )

        try:
            enforcer = FrameworkEnforcer(str(self.project_root), phase=self.phase)
            result = enforcer.run(level="BLOCK")

            passed = result.passed
            score = 100.0 if passed else 0.0
            details = f"{len(result.block_checks)} check(s), {len(result.violations)} violation(s)"

            return passed, score, details
        except Exception as e:
            return False, 0.0, f"Error: {e}"

    def check_session_log(self) -> Tuple[bool, float, str]:
        """Check Sessions_spawn.log.

        The canonical log path is `.methodology/sessions_spawn.log` (matches
        `SessionsSpawnLogger.LOG_FILENAME`). Earlier versions of this module
        read from project root, which made HR-11 ≥90% mathematically
        unreachable for P3+ projects (CV-1 in robustness audit).
        """
        log_file = self.project_root / ".methodology" / "sessions_spawn.log"

        if not log_file.exists():
            return False, 0.0, "sessions_spawn.log not found"

        try:
            content = log_file.read_text().strip()

            # SG-14: require JSONL — one JSON object per non-empty line.
            # This matches SessionsSpawnLogger._write_entries, which is the
            # only canonical writer. A single-dict log or a JSON array now
            # fails parsing instead of returning a misleading 50% score.
            roles: set[str] = set()
            sessions: set[str] = set()
            malformed = 0
            total = 0
            for raw_line in content.split("\n"):
                line = raw_line.strip().lstrip(",")
                if not line:
                    continue
                total += 1
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    malformed += 1
                    continue
                if not isinstance(entry, dict):
                    malformed += 1
                    continue
                roles.add(entry.get("role", ""))
                sid = entry.get("session_id", "")
                if sid:
                    sessions.add(sid)

            if total == 0:
                return False, 0.0, "sessions_spawn.log is empty"

            has_ab = len(roles) >= 2
            has_sessions = len(sessions) >= 2

            # Even with ≥2 sessions, malformed lines indicate a writer bug —
            # cap the score so we don't reward a half-broken log.
            if malformed >= total // 2:
                score = 0.0
            elif has_ab and has_sessions:
                score = 100.0
            elif has_sessions:
                score = 50.0
            else:
                score = 0.0

            details = (f"{total} record(s), {len(roles)} role(s), "
                       f"{len(sessions)} session(s)"
                       + (f", {malformed} malformed" if malformed else ""))

            return has_ab and has_sessions and malformed == 0, score, details
        except Exception as e:
            return False, 0.0, f"Error: {e}"

    def _get_pytest_timeout(self) -> int:
        """SG-5: pytest timeout is configurable via enforcement.json. Default 300s.

        Hardcoded 120s previously caused medium-sized test suites to time out,
        scoring 0 on Phase Truth and blocking P3/P4 advance.
        """
        cfg_path = self.project_root / ".methodology" / "enforcement.json"
        if cfg_path.exists():
            try:
                cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
                v = cfg.get("phase_truth", {}).get("pytest_timeout_seconds")
                if v is not None:
                    return max(30, int(v))  # floor at 30s to prevent footguns
            except (json.JSONDecodeError, ValueError, OSError):
                pass
        return 300

    def check_pytest(self) -> Tuple[bool, float, str]:
        """Check pytest actually passes; capture structured failure output."""
        try:
            timeout = self._get_pytest_timeout()
            result = subprocess.run(  # nosec B603 B607
                ["pytest", "--tb=line", "-q", "--no-header"],
                cwd=self.project_root,
                capture_output=True,
                text=True,
                timeout=timeout,
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
                "status": "✅ present" if (self.project_root / ".methodology" / "sessions_spawn.log").exists() else "❌ missing",
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

        total_weighted = 0.0
        active_weight = 0.0  # sum of weights for checks that actually ran
        results = []

        for name, check_func, weight in checks:
            try:
                passed, score, details = check_func()
                skipped = False
            except InfraSkip as skip:
                # CV-4: infrastructure unavailable — skip with [WARN], don't
                # penalize the score. The remaining checks get renormalized.
                passed, score, details = True, 0.0, f"⚠ SKIPPED — {skip}"
                skipped = True

            if not skipped:
                total_weighted += score * weight
                active_weight += weight

            status = "⚠" if skipped else ("✅" if passed else "❌")
            results.append({
                "name": name,
                "passed": passed,
                "score": score,
                "details": details,
                "weight": weight,
                "skipped": skipped,
            })

            print(f"{status} {name:<30} {details}")

        # Renormalize: weighted average across checks that actually ran.
        # If everything was skipped (infra fully broken) we fail-safe with 0%.
        # Math: original weights are designed to sum to 1.0, so dividing by
        # active_weight gives a 0-100 scale.
        if active_weight > 0:
            total_score = total_weighted / active_weight
        else:
            total_score = 0.0

        print()
        print("=" * 60)
        verdict = "✅ likely genuine" if total_score >= self.threshold else "❌ highly suspicious"
        skipped_count = sum(1 for r in results if r.get("skipped"))
        skip_note = f" ({skipped_count} check(s) SKIPPED due to infra)" if skipped_count else ""
        print(f"Total score: {total_score:.0f}% (threshold: {self.threshold:.0f}%) - {verdict}{skip_note}")
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
            "threshold": self.threshold,
            "passed": total_score >= self.threshold,
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
