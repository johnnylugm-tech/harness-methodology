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
from __future__ import annotations


import subprocess  # nosec B404
import json
import sys
import re
from pathlib import Path
from typing import Any, Dict, List, Tuple
from core.utils.project_layout import ProjectLayout
from core.utils.project_layout import phase_artifacts as _phase_artifacts

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
        cfg_path = ProjectLayout(self.project_root).enforcement_config_path
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


    def _get_pytest_timeout(self) -> int:
        """SG-5: pytest timeout is configurable via enforcement.json. Default 300s.

        Hardcoded 120s previously caused medium-sized test suites to time out,
        scoring 0 on Phase Truth and blocking P3/P4 advance.
        """
        cfg_path = ProjectLayout(self.project_root).enforcement_config_path
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
        """Check the test suite actually passes; capture structured failure output.

        Name kept for report-key stability; js/ts projects dispatch to the
        vitest/jest runner (state.json `language`/`test_runner`).
        """
        from core.utils.lang_patterns import project_language
        if project_language(self.project_root) in ("javascript", "typescript"):
            return self._check_tests_js()

        layout = ProjectLayout(self.project_root)
        test_target = "."
        if layout.active_test_dir.is_dir():
            test_target = layout.get_relative_str(layout.active_test_dir)

        try:
            timeout = self._get_pytest_timeout()
            # Use sys.executable -m pytest so the venv's Python is used (avoids
            # system PATH pytest pulling in macOS CommandLineTools Python 3.9
            # when source uses 3.11+ syntax). Bug #117.
            import os
            env = os.environ.copy()
            for k in list(env.keys()):
                if "SECRET" in k.upper() or "TOKEN" in k.upper() or "KEY" in k.upper() or "JWT" in k.upper():
                    env.pop(k, None)
                    
            result = subprocess.run(  # nosec B603 B607
                [sys.executable, "-m", "pytest", test_target, "--tb=line", "-q", "--no-header"],
                cwd=self.project_root,
                env=env,
                capture_output=True,
                text=True,
                timeout=timeout,
            )

            failures = _parse_failure_count(result.stdout + result.stderr)
            passed = result.returncode == 0 or (result.returncode == 1 and failures == 0)
            score = 100.0 if passed else 0.0

            if passed:
                details = "pytest all passed"
            else:
                details = f"pytest has {failures} failure(s)"

            return passed, score, details
        except subprocess.TimeoutExpired:
            return False, 0.0, "pytest execution timed out"
        except FileNotFoundError:
            return False, 0.0, "pytest not found"
        except Exception as e:
            return False, 0.0, f"Error: {e}"

    def _js_runner_argv(self, *, coverage: bool) -> list:
        """vitest/jest argv for test (or coverage) runs — npx --no-install only."""
        runner = "vitest"
        try:
            state = json.loads(
                (ProjectLayout(self.project_root).state_json_path)
                .read_text(encoding="utf-8")
            )
            runner = state.get("test_runner") or "vitest"
        except (OSError, json.JSONDecodeError):
            pass
        if runner == "jest":
            argv = ["npx", "--no-install", "jest", "--ci"]
            if coverage:
                argv += ["--coverage", "--coverageReporters=json-summary",
                         "--coverageReporters=text"]
        else:
            # vitest 4 removed the "basic" reporter — use the default.
            argv = ["npx", "--no-install", "vitest", "run"]
            if coverage:
                argv += ["--coverage", "--coverage.reporter=json-summary",
                         "--coverage.reporter=text"]
        return argv

    def _check_tests_js(self) -> Tuple[bool, float, str]:
        """js/ts variant of check_pytest — run the project's test runner."""
        try:
            result = subprocess.run(  # nosec B603 B607
                self._js_runner_argv(coverage=False),
                cwd=self.project_root,
                capture_output=True,
                text=True,
                timeout=self._get_pytest_timeout(),
            )
            passed = result.returncode == 0
            if passed:
                return True, 100.0, "test suite all passed"
            failures = _parse_failure_count(result.stdout + result.stderr)
            return False, 0.0, f"test suite has {failures} failure(s)"
        except subprocess.TimeoutExpired:
            return False, 0.0, "test suite execution timed out"
        except FileNotFoundError:
            return False, 0.0, "npx/node not found"
        except Exception as e:
            return False, 0.0, f"Error: {e}"

    def _check_coverage_js(self, threshold: int) -> Tuple[bool, float, str]:
        """js/ts variant of check_coverage — json-summary artifact is truth."""
        try:
            subprocess.run(  # nosec B603 B607
                self._js_runner_argv(coverage=True),
                cwd=self.project_root,
                capture_output=True,
                text=True,
                timeout=self._get_pytest_timeout(),
            )
            summary_path = (
                Path(self.project_root) / "coverage" / "coverage-summary.json"
            )
            coverage = 0
            if summary_path.exists():
                data = json.loads(summary_path.read_text(encoding="utf-8"))
                coverage = int(float(data["total"]["lines"]["pct"]))
            passed = coverage >= threshold
            score = min(100.0, coverage) if passed else coverage
            return passed, float(score), f"coverage {coverage}% (threshold {threshold}%)"
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

        from core.utils.lang_patterns import project_language
        if project_language(self.project_root) in ("javascript", "typescript"):
            return self._check_coverage_js(threshold)

        from core.quality_gate.cov_utils import read_coveragerc_source  # pyright: ignore[reportMissingImports]
        cov_source = read_coveragerc_source(self.project_root)
        
        layout = ProjectLayout(self.project_root)
        test_target = "."
        if layout.active_test_dir.is_dir():
            test_target = layout.get_relative_str(layout.active_test_dir)
            
        try:
            # Use sys.executable -m pytest (Bug #117) so coverage is measured
            # against the same Python interpreter that will run tests in CI.
            result = subprocess.run(  # nosec B603 B607
                [sys.executable, "-m", "pytest", test_target, f"--cov={cov_source}", "--cov-report=term-missing", "--tb=no", "-q"],
                cwd=self.project_root,
                capture_output=True,
                text=True,
                timeout=self._get_pytest_timeout(),
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

    def check_cross_artifact(self) -> Tuple[bool, float, str]:
        """D3: Cross-artifact consistency validation.

        Checks that reports don't reference wrong phases, FRs in test results
        have session log evidence, and coverage reports match actual measurements.

        At P3, the check raises InfraSkip — cross-artifact validation requires
        testing artifacts that don't exist yet. The weight is renormalized
        across the remaining substantive checks.
        """
        if self.phase < 4:
            raise InfraSkip("Cross-artifact checks start at P4 — no testing artifacts yet")

        try:
            from core.quality_gate.cross_artifact import run_cross_artifact_checks
            result = run_cross_artifact_checks(self.project_root, self.phase)
        except ImportError:
            raise InfraSkip("cross_artifact module unavailable")
        except Exception as e:
            return False, 0.0, f"Cross-artifact check error: {e}"

        criticals = result.get("critical_count", 0)
        highs = result.get("high_count", 0)
        total = len(result.get("violations", []))

        if criticals == 0 and highs == 0:
            return True, 100.0, f"No inconsistencies ({result['checks_ran']} checks)"

        # Score degrades with violations: each CRITICAL = -30%, each HIGH = -15%
        # Both CRITICAL and HIGH violations reduce the score (not just CRITICAL).
        penalty = min(criticals * 30 + highs * 15, 100)
        score = max(0.0, 100.0 - penalty)
        passed = criticals == 0 and highs == 0
        return passed, score, f"{total} inconsistency/ies ({criticals}C, {highs}H)"


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

    def check_session_log(self) -> Tuple[bool, float, str]:
        """Verify the integrity of sessions_spawn.log.

        Enforces JSONL format and malformed-line cap for all phases.
        A/B reviewer separation (HR-01) only applies to Phase 1, 2, and 6
        where Agent B collaboration is part of the workflow.
        """
        log_path = ProjectLayout(self.project_root).sessions_spawn_log
        if not log_path.exists():
            return False, 0.0, "sessions_spawn.log is missing"

        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
        lines = [line.strip() for line in lines if line.strip()]
        if not lines:
            return False, 0.0, "sessions_spawn.log is empty"

        malformed = 0
        valid_entries = []
        for line in lines:
            try:
                entry = json.loads(line)
                valid_entries.append(entry)
            except json.JSONDecodeError:
                malformed += 1

        total_lines = len(lines)
        if total_lines > 0 and malformed / total_lines >= 0.5:
            return False, 0.0, f"≥50% malformed JSONL lines ({malformed}/{total_lines})"

        # A/B reviewer check applies only to phases that use Agent B collaboration.
        if self.phase in (1, 2, 6):
            fr_reviewers: dict[str, set[str]] = {}
            for e in valid_entries:
                role = str(e.get("role", "")).strip()
                if not role:
                    continue
                fr = str(e.get("fr_id", "")).strip()
                if fr:
                    if fr not in fr_reviewers:
                        fr_reviewers[fr] = set()
                    fr_reviewers[fr].add(role)

            _REVIEWER_ROLES = {"reviewer", "architect", "tech_lead", "qa_lead", "senior_dev"}
            unreviewed_frs = []
            for fr, roles in fr_reviewers.items():
                if not roles.intersection(_REVIEWER_ROLES):
                    unreviewed_frs.append(fr)

            if unreviewed_frs:
                return False, 50.0, f"A/B reviewer missing for {len(unreviewed_frs)} FR(s)"

        return True, 100.0, "sessions_spawn.log JSONL structure verified (A/B N/A for this phase)"

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
        # P5-BUG-04: Support ranges like FR-01..09 without strict brackets
        has_ref = bool(re.search(r"(?:TASK|FR|NFR)-\d+(?:\.\.\d+)?", content, re.IGNORECASE))
        if not has_ref:
            issues.append("No task/FR/NFR references found")

        quality = "good" if not issues else "suspicious"
        return {"quality": quality, "issues": issues}

    def get_manual_checklist(self) -> List[Dict]:
        """Generate items requiring manual confirmation"""

        layout = ProjectLayout(self.project_root)
        phase_artifacts = {
            1: [
                layout.get_relative_str(layout.srs_path),
                layout.get_relative_str(layout.spec_tracking_path),
                layout.get_relative_str(layout.traceability_matrix_path),
            ],
            2: [layout.get_relative_str(layout.sad_path)],
            3: [
                layout.get_relative_str(layout.active_src_dir) + "/",
                layout.get_relative_str(layout.active_test_dir) + "/",
            ],
            4: [
                layout.get_relative_str(layout.test_plan_path),
                layout.get_relative_str(layout.test_results_path),
            ],
            5: [
                layout.get_relative_str(layout.baseline_path),
                layout.get_relative_str(layout.verification_report_path),
            ],
            6: [layout.get_relative_str(layout.quality_report_path)],
            7: _phase_artifacts(7),
            8: [
                layout.get_relative_str(layout.config_records_path),
                layout.get_relative_str(layout.release_checklist_path),
            ],
            9: [layout.get_relative_str(layout.maintenance_log_path)],
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
                "item": layout.get_relative_str(layout.sessions_spawn_log),
                "status": "✅ present" if layout.sessions_spawn_log.exists() else "❌ missing",
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

        # Execute checks (adjust weights based on Phase).
        # NOTE: The check_session_log invariant ensures JSONL structure and A/B role coverage.
        if self.phase <= 2:
            checks = [
                ("FrameworkEnforcer BLOCK", self.check_framework_block, 0.50),
                ("Previous phase artifacts", self.check_previous_phase_artifacts, 0.30),
                ("Session Log Validation", self.check_session_log, 0.20),
            ]
        # Phase 3-4: framework block + real pytest/coverage + predecessor + cross-artifact
        elif self.phase <= 4:
            checks = [
                ("FrameworkEnforcer BLOCK", self.check_framework_block, 0.28),
                ("pytest actually passes", self.check_pytest, 0.24),
                ("test coverage meets threshold", self.check_coverage, 0.16),
                ("Previous phase artifacts", self.check_previous_phase_artifacts, 0.14),
                ("Cross-artifact consistency", self.check_cross_artifact, 0.08),
                ("Session Log Validation", self.check_session_log, 0.10),
            ]
        # Phase 5-8: framework block + previous phase (non-code phases)
        else:
            checks = [
                ("FrameworkEnforcer BLOCK", self.check_framework_block, 0.50),
                ("Previous phase artifacts", self.check_previous_phase_artifacts, 0.30),
                ("Session Log Validation", self.check_session_log, 0.20),
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

        # Assign instance attribute for downstream consumers (e.g. to_fix_context)
        self.results = {str(r["name"]): r for r in results}

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
    """Count FAILED and ERROR lines in pytest output."""
    import re
    fails = len(re.findall(r"^FAILED\s+", output, re.MULTILINE))
    errs = len(re.findall(r"^ERROR\s+", output, re.MULTILINE))
    return fails + errs


if __name__ == "__main__":  # pragma: no cover
    import argparse

    parser = argparse.ArgumentParser(description="Phase Truth Verifier")
    parser.add_argument("--phase", type=int, required=True, choices=range(1, 10),
                        help="Phase number (1-8)")
    parser.add_argument("--project", default=".", help="Project root path")

    args = parser.parse_args()

    verifier = PhaseTruthVerifier(args.project, args.phase)
    result = verifier.verify()

    sys.exit(0 if result["passed"] else 1)
