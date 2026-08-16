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
from core.phase_topology import VALID_PHASES
from core.state_io import load_state
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
            harness_config ``values.phase_truth_threshold`` (Round 9 home for
            tunables), honoring the legacy
            `.methodology/enforcement.json::hr_overrides.HR-11_phase_truth_threshold`
            as a fallback, else 90.0. See SG-7 in robustness audit.
        """
        self.project_root = Path(project_root)
        self.phase = phase
        self.results: dict[str, Any] = {}
        self.threshold: float = (
            float(threshold) if threshold is not None
            else self._load_threshold_from_config()
        )

    def _legacy_enforcement_value(self, section: str, key: str, label: str):
        """Legacy enforcement.json read + one-line migration nudge (Round 9
        station 3: these two keys moved to harness_config values.*; the old
        location keeps working so no project breaks mid-run)."""
        cfg_path = ProjectLayout(self.project_root).enforcement_config_path
        if not cfg_path.exists():
            return None
        try:
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
            v = cfg.get(section, {}).get(key)
        except (json.JSONDecodeError, ValueError, OSError):
            return None
        if v is not None:
            print(f"[phase-truth] NOTE: reading legacy enforcement.json "
                  f"{section}.{key} — migrate to harness_config.json "
                  f"values.{label} (doctor flags this file)")
        return v

    def _load_threshold_from_config(self) -> float:
        """HR-11 threshold: values.phase_truth_threshold > legacy enforcement.json > 90.0 (SG-7)."""
        from core.harness_config import get_value, value_is_configured
        if value_is_configured(self.project_root, "phase_truth_threshold"):
            return float(get_value(self.project_root, "phase_truth_threshold"))
        legacy = self._legacy_enforcement_value(
            "hr_overrides", "HR-11_phase_truth_threshold", "phase_truth_threshold")
        if legacy is not None:
            try:
                return float(legacy)
            except (TypeError, ValueError):
                pass
        return float(get_value(self.project_root, "phase_truth_threshold"))

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
            if result.violations:
                violation_text = "; ".join(
                    msg + (f" ({hint})" if hint else "")
                    for msg, hint in result.violations
                )
                details += f" — {violation_text}"

            return passed, score, details
        except Exception as e:
            return False, 0.0, f"Error: {e}"


    def _get_pytest_timeout(self) -> int:
        """SG-5: pytest cap — values.phase_truth_pytest_timeout > legacy
        enforcement.json > default. Floor 30s to prevent footguns.

        Hardcoded 120s previously caused medium-sized test suites to time out,
        scoring 0 on Phase Truth and blocking P3/P4 advance.

        Round 25: the precedence lives in
        ``core.quality_gate.test_suite_run.suite_timeout`` so the shared suite
        run and the js/ts runner calls below cannot drift apart.
        """
        from core.quality_gate.test_suite_run import suite_timeout

        return suite_timeout(self.project_root)

    def check_pytest(self) -> Tuple[bool, float, str]:
        """Check the test suite actually passes; capture structured failure output.

        Name kept for report-key stability; js/ts projects dispatch to the
        vitest/jest runner (state.json `language`/`test_runner`).
        """
        from core.utils.lang_patterns import project_language
        if project_language(self.project_root) in ("javascript", "typescript"):
            return self._check_tests_js()

        # Round 25: shares one suite execution with check_coverage,
        # FrameworkEnforcer, gate1_evidence and the _advance_prechecks TDD
        # block. The secret-scrubbed environment this branch used to build
        # inline is now the canonical one for all of them.
        from core.quality_gate.test_suite_run import run_suite

        result = run_suite(self.project_root)
        if not result.ran:
            return False, 0.0, result.reason or "test suite not measurable"
        if result.returncode == 124:
            return False, 0.0, "pytest execution timed out"

        failures = _parse_failure_count(result.output)
        # A returncode of 1 with no parsed failures is a pytest-level complaint
        # (plugin warning, coverage plugin exit) rather than a failing test —
        # kept verbatim from the pre-Round-25 behaviour.
        passed = result.passed or (result.returncode == 1 and failures == 0)
        score = 100.0 if passed else 0.0
        details = "pytest all passed" if passed else f"pytest has {failures} failure(s)"
        # Report skip count unconditionally — pytest's exit code is 0 even
        # when tests are skipped, so `passed` alone can silently hide them.
        # This does not change `passed`/`score` here: whether a project's own
        # SRS requires zero skips is reconciled separately (see
        # check_srs_mandatory_reconciliation), not hardcoded as a global rule.
        if result.skipped:
            details += f" ({result.skipped} skipped)"
        return passed, score, details

    def check_srs_mandatory_reconciliation(self) -> Tuple[bool, float, str]:
        """Reconcile SRS.md's hard-mandatory ACs against live measured state.

        A project's SRS.md can author two kinds of hard, non-negotiable ACs
        (typically as DERIVED clauses, per Phase 1's own authoring
        convention): a boolean feature flag ("`harness_config.json` must set
        `features.X: true`") or a zero-tolerance skip count ("output must
        report **0 skipped**" / "skipped count is **0**"). Neither is
        enforced by any continuous/percentage-scored Gate dimension —
        mutation_testing gets excluded_by_feature_flag when the flag is off,
        test_assertion_quality is a 0-100 blend a single skip barely moves —
        so a hard SRS rule can be silently violated forever. This check
        parses what SRS.md's own NFR sections literally demand and compares
        it to what the harness's own tools measured, failing loud on any
        mismatch. An NFR section whose heading or body contains "WAIVED"
        (the convention this project already uses to retire a requirement
        deliberately, e.g. NFR-08) is exempt — a documented waiver is not a
        gap.

        Known scope limit (this increment): reconciles boolean feature-flag
        ACs and zero-skip-count ACs only. `zero_assert == 0` ACs are not yet
        reconciled here — that needs a live zero-assert count source this
        module doesn't currently have plumbed in; tracked as follow-up
        rather than silently claimed as covered.
        """
        import json
        import re

        layout = ProjectLayout(self.project_root)
        srs_path = layout.srs_path
        if not srs_path.is_file():
            raise InfraSkip("SRS.md not present yet")
        try:
            srs_text = srs_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise InfraSkip(f"SRS.md unreadable: {exc}")

        config_path = layout.methodology_dir / "harness_config.json"
        try:
            config = (
                json.loads(config_path.read_text(encoding="utf-8"))
                if config_path.is_file() else {}
            )
        except (OSError, json.JSONDecodeError):
            config = {}
        features = config.get("features", {}) if isinstance(config, dict) else {}

        flag_re = re.compile(r"features\.(\w+)\s*:\s*true")
        skip_zero_re = re.compile(r"skipped count is \*\*0\*\*|report \*\*0 skipped\*\*")

        violations: list[str] = []
        sections = re.split(r"(?=^###\s+NFR-\d+)", srs_text, flags=re.MULTILINE)
        for section in sections:
            m = re.match(r"###\s+(NFR-\d+)", section)
            if not m:
                continue
            nfr_id = m.group(1)
            if re.search(r"WAIVED", section, re.IGNORECASE):
                continue  # explicit, documented waiver — not a gap

            for flag_match in flag_re.finditer(section):
                flag_name = flag_match.group(1)
                actual = features.get(flag_name)
                if actual is not True:
                    violations.append(
                        f"{nfr_id}: SRS demands features.{flag_name}=true, "
                        f"harness_config.json has {actual!r}"
                    )

            if skip_zero_re.search(section):
                from core.quality_gate.test_suite_run import run_suite
                result = run_suite(self.project_root)
                if result.ran and (result.skipped or 0) > 0:
                    violations.append(
                        f"{nfr_id}: SRS demands 0 skipped, pytest reported "
                        f"{result.skipped} skipped"
                    )
                # Round 27 站7b: a skip that did not fire is still a skip.
                #
                # The count above is what THIS machine measured. A suite whose
                # skips are conditional — `if not shutil.which(tool):
                # pytest.skip(...)`, `if not config.exists(): pytest.skip(...)`
                # — reports zero wherever the tools and files happen to be
                # present, and reports several everywhere else. One project
                # declared a zero-skip rule, was measured at 35 passed / 0
                # skipped, and had ten `pytest.skip(` calls sitting in the file
                # that measurement came from. "Zero skips" had come to mean
                # "this developer's laptop is fully provisioned".
                #
                # A project that wrote the rule for itself gets it enforced as
                # written: the skip calls must not be there at all.
                for where in _skip_sites(self.project_root):
                    violations.append(
                        f"{nfr_id}: SRS demands 0 skipped, but a skip is written "
                        f"at {where} — it did not fire here, and will fire "
                        f"wherever its condition holds"
                    )

        if violations:
            return False, 0.0, "SRS-mandatory reconciliation FAILED: " + "; ".join(violations)
        return True, 100.0, "SRS-mandatory ACs reconciled against live state (no violations)"

    def _js_runner_argv(self, *, coverage: bool) -> list:
        """vitest/jest argv for test (or coverage) runs — npx --no-install only."""
        state = load_state(self.project_root, lenient=True)
        runner = state.get("test_runner") or "vitest"
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

        # Round 25: same single suite execution as check_pytest. The number is
        # coverage's exact `totals.percent_covered` rather than the truncated
        # `TOTAL … n%` terminal line; for the integer thresholds here the two
        # agree (floor(x) >= T ⟺ x >= T for integer T), but the reported figure
        # is now the truth instead of 85.0% standing in for 85.9%.
        from core.quality_gate.test_suite_run import run_suite

        result = run_suite(self.project_root)
        if not result.ran:
            return False, 0.0, result.reason or "coverage not measurable"
        # An unreadable coverage report stays a failure, as before — but it is
        # named as such rather than silently reported as 0%.
        if result.coverage is None:
            return False, 0.0, f"coverage report unreadable (threshold {threshold}%)"

        coverage = result.coverage
        passed = coverage >= threshold
        score = min(100.0, coverage) if passed else coverage
        details = f"coverage {coverage:.1f}% (threshold {threshold}%)"
        return passed, score, details

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
        """Structural check on sessions_spawn.log. DIAGNOSTIC ONLY — not scored.

        Round 21 站3 removed this from verify()'s weighted checks. The file is
        written by the agent whose work the score judges, it is gitignored, and
        appending a line to it costs one Bash call — so anything read from it is
        something the audited party could have authored. It remains callable for
        forensics (core/doctor.py pairs it with an authenticity heuristic over
        the same entries), and must not be added back to a verdict: a check
        whose input its subject controls cannot corroborate that subject.

        The A/B reviewer-separation branch was removed with the scoring. Besides
        resting on the same writable input, it collected reviewer roles across
        the WHOLE log without filtering by phase, so a single `architect` entry
        from any phase satisfied it for phases 1, 2 and 6 alike. HR-01 is
        enforced by the deliverable review, not by counting log lines.
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

        return True, 100.0, f"sessions_spawn.log JSONL structure verified ({len(valid_entries)} entries)"

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
        #
        # Round 21 站3: sessions_spawn.log no longer contributes to this score.
        # It is written by the same agent the score is judging, it is gitignored
        # (so no review or CI ever sees it), and appending to it takes one Bash
        # call — every signal it carried was one the audited party could author.
        # SKILL.md recorded that decision when HR-10 was withdrawn ("log is
        # agent-writable, not tamper-evident"); the code kept scoring it at
        # weight 0.20 anyway, and SAD.md still called it a MUST. taskq's Phase 6
        # then produced six hand-written entries whose role and phase exactly
        # matched what the A/B branch below looked for.
        #
        # check_session_log is retained as a diagnostic (core/doctor.py runs the
        # authenticity heuristic over the same file) but must never again be a
        # term in a verdict: a forger can supply whatever the check reads.
        if self.phase <= 2:
            checks = [
                ("FrameworkEnforcer BLOCK", self.check_framework_block, 0.60),
                ("Previous phase artifacts", self.check_previous_phase_artifacts, 0.40),
            ]
        # Phase 3-4: framework block + real pytest/coverage + predecessor + cross-artifact
        elif self.phase <= 4:
            checks = [
                ("FrameworkEnforcer BLOCK", self.check_framework_block, 0.26),
                ("pytest actually passes", self.check_pytest, 0.23),
                ("test coverage meets threshold", self.check_coverage, 0.15),
                ("Previous phase artifacts", self.check_previous_phase_artifacts, 0.13),
                ("Cross-artifact consistency", self.check_cross_artifact, 0.08),
                ("SRS-mandatory reconciliation", self.check_srs_mandatory_reconciliation, 0.15),
            ]
        # Phase 5-8: framework block + previous phase (non-code phases)
        #
        # Round 55 站6 added cross-artifact here. It had lived only in the
        # Phase 3-4 list, and it is the sole consumer of
        # `cross_artifact.run_cross_artifact_checks` — so `check_phase_title`'s
        # P5/P6/P7/P8/P9 entries had never once executed, and the placeholder
        # check this round wrote to read Phase 8's CONFIG_RECORDS.md was never
        # called at Phase 8. Measured on taskq-super's delivered file: one
        # CRITICAL, `passed=False`, and nothing asked.
        #
        # The three Phase-4 sub-checks stay behind `phase == 4` inside that
        # function, so this addition re-judges no Phase 4 document and runs no
        # second test suite.
        #
        # The other three weights shrink to make room: 0.42/0.28/0.30 scaled by
        # 0.92, rounded to 0.39/0.26/0.27, plus 0.08. Round 21 站3's invariant
        # is that each phase's weights sum to 1.0 —
        # `test_weights_still_sum_to_one` — because `active_weight`
        # renormalisation would otherwise hide a list that does not, and a
        # score whose scale nobody stated is the defect that invariant exists
        # to prevent. Their proportions to each other are preserved.
        else:
            checks = [
                ("FrameworkEnforcer BLOCK", self.check_framework_block, 0.39),
                ("Previous phase artifacts", self.check_previous_phase_artifacts, 0.26),
                ("SRS-mandatory reconciliation", self.check_srs_mandatory_reconciliation, 0.27),
                ("Cross-artifact consistency", self.check_cross_artifact, 0.08),
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


def _skip_sites(project_root) -> list[str]:
    """Every place a skip is WRITTEN in the project's test tree.

    Round 27 站7b. Distinct from "how many skipped this run": a conditional skip
    reports zero wherever its condition is false, so a suite full of
    `if not shutil.which(tool): pytest.skip(...)` measures clean on a fully
    provisioned machine and dirty everywhere else.

    Covers the call form (`pytest.skip(...)`, `skip(...)`) and the marker form
    (`@pytest.mark.skip`, `@pytest.mark.skipif`, `@pytest.mark.xfail`). Parsed
    with ast rather than grepped, so the word appearing in a comment or a
    docstring — including the ones explaining this rule — is not a hit.

    Returns "path:line" strings, empty when the tree is clean or unparseable
    (an unreadable test tree is not this check's failure to report).
    """
    import ast
    from core.utils.project_layout import ProjectLayout

    def _dotted(node) -> str:
        parts = []
        while isinstance(node, ast.Attribute):
            parts.append(node.attr)
            node = node.value
        if isinstance(node, ast.Name):
            parts.append(node.id)
        return ".".join(reversed(parts))

    try:
        test_dir = ProjectLayout(project_root).active_test_dir
    except Exception as exc:  # pylint: disable=broad-exception-caught
        print(f"[skip-scan] cannot resolve the test directory ({type(exc).__name__}: "
              f"{exc}) — written-but-unfired skips are NOT being checked this run",
              file=sys.stderr)
        return []
    if not test_dir or not Path(test_dir).is_dir():
        return []

    sites: list[str] = []
    for path in sorted(Path(test_dir).rglob("test_*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except (OSError, SyntaxError):
            continue
        rel = path.relative_to(Path(project_root)).as_posix()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                name = _dotted(node.func)
                if name in ("pytest.skip", "skip", "pytest.xfail", "xfail"):
                    sites.append(f"{rel}:{node.lineno} ({name})")
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                for dec in node.decorator_list:
                    target = dec.func if isinstance(dec, ast.Call) else dec
                    name = _dotted(target)
                    if name in ("pytest.mark.skip", "pytest.mark.skipif",
                                "pytest.mark.xfail"):
                        sites.append(f"{rel}:{dec.lineno} (@{name})")
    return sites


def _parse_failure_count(output: str) -> int:
    """Count FAILED and ERROR lines in pytest output."""
    import re
    fails = len(re.findall(r"^FAILED\s+", output, re.MULTILINE))
    errs = len(re.findall(r"^ERROR\s+", output, re.MULTILINE))
    return fails + errs


if __name__ == "__main__":  # pragma: no cover
    import argparse

    parser = argparse.ArgumentParser(description="Phase Truth Verifier")
    parser.add_argument("--phase", type=int, required=True, choices=VALID_PHASES,
                        help="Phase number (1-9)")
    parser.add_argument("--project", default=".", help="Project root path")

    args = parser.parse_args()

    verifier = PhaseTruthVerifier(args.project, args.phase)
    result = verifier.verify()

    sys.exit(0 if result["passed"] else 1)
