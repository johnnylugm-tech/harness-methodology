#!/usr/bin/env python3
"""
Cross-Artifact Consistency Validator (D3)
==========================================
Detects fabrication patterns that span multiple artifacts:

1. Report phase number mismatch — e.g. COVERAGE_REPORT.md title says
   "Phase 3" but the file is in 04-testing/ (Phase 4 territory).
2. FR coverage mismatch — TEST_RESULTS.md lists FRs that have no
   corresponding entries in sessions_spawn.log.
3. Coverage number mismatch — COVERAGE_REPORT.md claims N% but
   pytest --cov output disagrees.

These checks run during Phase Truth verification and finalize-gate
postflight, not as standalone tools.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List
from core.phase_topology import VALID_PHASES
# What an unfilled template placeholder looks like — imported, not re-spelled.
# `constitution/runner.py` has owned this pattern since it was written (it
# excludes `${VAR}` shell expansions, dotted `{Platform.TELEGRAM}` code and
# `{key: value}` literals), and a second spelling of the same idea is the
# defect Round 36 named: the copy the gate reads is the copy that goes stale.
from core.quality_gate.constitution.runner import _STUB_PLACEHOLDER_RE
from core.utils.project_layout import ProjectLayout, phase_artifacts as _phase_artifacts


# Canonical artifact paths per phase (relative to project root)
# Defined dynamically via ProjectLayout within functions now, so we remove the static map.


_TEMPLATES_DIR = Path(__file__).resolve().parent.parent.parent / "templates"


def _template_placeholders(basename: str) -> set:
    """The placeholders the framework's own template for *basename* ships.

    This, not "the file contains braces", is what makes the check decidable.
    Measured over the seven projects here, a presence rule fires on
    `/v1/tasks/{id}` in six SRS files, nine TEST_SPECs and every TEST_PLAN —
    a URL path template is content, not an unfilled field. Intersecting with
    the template's own set leaves exactly the fault being described: a
    placeholder this framework put there and nobody replaced.
    """
    path = _TEMPLATES_DIR / basename
    if not path.is_file():
        return set()
    try:
        return set(_STUB_PLACEHOLDER_RE.findall(path.read_text(encoding="utf-8")))
    except OSError:
        return set()


# pytest's own terminal summary line. Identified by the `in <T>s` suffix it
# always prints, not by counts alone: a markdown row reading
# `| **Total** | 441 |` is the agent's transcription, and the summary line is
# the runner's output. Round 55 站0 measured both shapes in the corpus.
_PYTEST_SUMMARY_RE = re.compile(
    r"^(?=.*\bin \d+(?:\.\d+)?s\b)(?=.*\b\d+ (?:passed|failed|error|errors)\b).*$",
    re.MULTILINE,
)
_PYTEST_COUNT_RE = re.compile(
    r"\b(\d+) (passed|failed|skipped|error|errors|xfailed|xpassed)\b")


def measured_suite(project_root: Path):
    """The framework's own measurement of this project's test suite.

    Public because it is the seam a test replaces — `run_suite` executes
    pytest, and a unit test of the reconciliation must be able to say what the
    framework measured without running one (Round 49 C2's patch ratchet is the
    rule that keeps that seam public rather than private).

    Costs nothing on the path that matters: `run_suite` memoises per
    (project, fingerprint) within a process, and `phase_truth_verifier`'s
    `check_pytest` calls it before `check_cross_artifact` at P4.
    """
    from core.quality_gate.test_suite_run import run_suite

    return run_suite(project_root)


def check_test_count_reconciliation(
    project_root: Path, phase: int
) -> List[Dict[str, str]]:
    """TEST_RESULTS.md's own execution summary against the framework's.

    Round 55 站4. The counts in `04-testing/TEST_RESULTS.md` are prose the
    agent writes; `run_suite` is a measurement the framework takes, scoped by
    `resolve_targets` to the project's own test directory. The two had never
    met, and the Phase 4 prompt said why in as many words: "Real execution is
    enforced by advance-phase pytest --cov-fail-under=100, **not by
    string-matching this doc**."

    taskq-super's document records `4 failed, 7563 passed, 3 skipped, 2
    warnings in 281.16s` as its source of truth, and explains it two sections
    later — "4 failed (all `harness/tests/`)", "plus the bulk of the harness
    guard suite". The agent ran `pytest` from the repository root, where the
    vendored copy of this framework lives. The framework's measurement of that
    tree is 349 tests. The 7,563 then travelled unchallenged into
    `05-verification/BASELINE.md` and `VERIFICATION_REPORT.md`.

    Four of the seven projects here carry a summary measured over a wider tree
    than the one they deliver (taskq-super 7570; taskq-plus 6866 beside its own
    441; run-all-by-workflow 6256 beside 59), and three have no
    machine-readable summary at all. taskq-api's single 326 is the one honest
    case.

    A document with no summary line is a finding too. That is the one place
    this check deliberately does not copy `check_coverage_report` next door,
    which returns nothing when it finds no numeric claim: a test-results
    document with nothing to reconcile has not been reconciled (Round 46 站1).
    """
    # Phase 4 only, and the guard lives here rather than only at the call site:
    # this function executes the project's test suite, and `run_suite`'s memo is
    # warm only where `check_pytest` ran first (Phase 3-4). A caller that
    # forgets the condition would run the whole suite again at every later
    # phase, to re-judge a document Phase 4 wrote.
    if phase != 4:
        return []
    layout = ProjectLayout(project_root)
    results = layout.phase4_testing_dir / "TEST_RESULTS.md"
    if not results.is_file():
        return []
    try:
        text = results.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        print(f"[WARN] cross_artifact: could not read {results}: {exc}",
              file=sys.stderr)
        return []

    suite = measured_suite(project_root)
    measured = len(getattr(suite, "test_outcomes", None) or {})
    if not getattr(suite, "ran", False) or not measured:
        # The framework did not measure, so it has no number to compare
        # against. `test_outcomes` comes from the same run's `--junitxml`; an
        # empty one means the per-test detail was not parsed, and reading that
        # as zero tests would turn a reporting failure into an accusation
        # (Round 32 站4 / Round 35 站2 — could-not-measure is not zero).
        return []
    target = getattr(suite, "test_target", "the project's test directory")
    rel = layout.get_relative_str(results)

    lines = [ln.strip() for ln in _PYTEST_SUMMARY_RE.findall(text)]
    if not lines:
        return [{
            "file": rel,
            "issue": (
                f"no pytest summary line to reconcile; the framework measured "
                f"{measured} test(s) under {target}"
            ),
            "severity": "CRITICAL",
            "suggestion": (
                f"Paste the verbatim summary line of the run this document "
                f"describes (the `N passed … in T s` line pytest prints). "
                f"The run must be scoped to {target}, not to the repository "
                f"root — the root also holds the vendored harness suite."
            ),
        }]

    totals = []
    for line in lines:
        counts = {k: int(v) for v, k in _PYTEST_COUNT_RE.findall(line)}
        totals.append((sum(counts.values()), line))
    if any(total == measured for total, _ in totals):
        return []

    return [{
        "file": rel,
        "issue": (
            f"summary line reports {total} test(s); the framework measured "
            f"{measured} under {target} ({line})"
        ),
        "severity": "CRITICAL",
        # Two causes, and this check cannot tell them apart from one number,
        # so it names both rather than asserting the one it has seen most.
        "suggestion": (
            f"Re-run the suite scoped to {target} and record that summary "
            f"line verbatim. A count far above {measured} means the run was "
            f"not scoped to the project — `pytest` from the repository root "
            f"also collects the vendored harness suite. A count near "
            f"{measured} means the document describes an earlier run and "
            f"tests have been added since; re-record it as the last thing "
            f"Phase 4 does."
        ),
    } for total, line in totals]


def check_unfilled_placeholders(project_root: Path, phase: int) -> List[Dict[str, str]]:
    """A delivered artifact may not still carry its template's placeholders.

    Round 55. `08-config/CONFIG_RECORDS.md` is Phase 8's key artifact.
    `templates/CONFIG_RECORDS.md` ships it with `{{config}}` / `{{VAR}}` /
    `{{rollback commands}}` for a human to fill, `scripts/phase8_doc_gen.py`
    copies it, and until now no automatic check read the result — the file's
    existence was verified (`legal_artifacts`, `phase_artifact_enforcer`) and
    its content never was.

    Measured 2026-08-17, and the number is the point: **all seven** projects on
    this machine shipped the same nine unreplaced placeholders in
    CONFIG_RECORDS.md, including the whole Rollback SOP. Seven for seven is
    not seven project failures; it is a deliverable the framework generates,
    tells nobody to fill, and never reads. Every other deliverable in the
    corpus is clean under this rule.

    One thing this is NOT: `constitution/runner._is_stub_template` returns a
    vacuous 100/100/100/100 for a file with eight or more placeholders, and
    taskq-super's trips it. That is not the live path — constitution left the
    automatic pipeline at 減法 T3 (2026-07-07) and says so in
    `phase_hooks.NON_PIPELINE_PREFLIGHTS`. The defect was the absent reader,
    not the generous score, so the fix is a reader on the live path.
    """
    violations: List[Dict[str, str]] = []
    for rel_path in phase_artifact_relpaths(project_root, phase):
        fpath = project_root / rel_path
        if not fpath.is_file():
            continue
        expected = _template_placeholders(fpath.name)
        if not expected:
            continue
        try:
            content = fpath.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            print(f"[WARN] cross_artifact: could not read {fpath}: {exc}",
                  file=sys.stderr)
            continue
        left = sorted(expected & set(_STUB_PLACEHOLDER_RE.findall(content)))
        if not left:
            continue
        violations.append({
            "file": rel_path,
            "issue": (
                f"{len(left)} placeholder(s) from templates/{fpath.name} are "
                f"still unreplaced: {', '.join(left)}"
            ),
            "severity": "CRITICAL",
            "suggestion": (
                f"Replace each of {', '.join(left)} in {rel_path} with the "
                f"real value for this release. If a section genuinely does "
                f"not apply, say so in words and delete the placeholder — a "
                f"template field left as-is reads as an answer nobody gave."
            ),
        })
    return violations


def phase_artifact_relpaths(project_root: Path, phase: int) -> List[str]:
    """The deliverables *phase* writes, project-relative.

    One registry, two readers: `check_phase_title` asks whether each one's H1
    names the right phase, and `check_unfilled_placeholders` asks whether it is
    still the template it was copied from. Round 55 extracted it from the first
    of those rather than letting the second grow a second copy.
    """
    layout = ProjectLayout(project_root)
    return {
        4: [
            layout.get_relative_str(layout.test_plan_path),
            layout.get_relative_str(layout.phase4_testing_dir / "TEST_RESULTS.md"),
            layout.get_relative_str(layout.phase4_testing_dir / "COVERAGE_REPORT.md"),
        ],
        5: [
            layout.get_relative_str(layout.phase5_verification_dir / "BASELINE.md"),
            layout.get_relative_str(layout.phase5_verification_dir / "VERIFICATION_REPORT.md"),
        ],
        6: [layout.get_relative_str(layout.phase6_quality_dir / "QUALITY_REPORT.md")],
        7: _phase_artifacts(7),
        8: [
            layout.get_relative_str(layout.phase8_config_dir / "CONFIG_RECORDS.md"),
            layout.get_relative_str(layout.phase8_config_dir / "RELEASE_CHECKLIST.md"),
        ],
        9: [layout.get_relative_str(layout.maintenance_log_path)],
    }.get(phase, [])


def check_phase_title(project_root: Path, phase: int) -> List[Dict[str, str]]:
    """Check that report H1 titles reference the correct phase number.

    Detects copy-paste from previous phase reports (e.g. Phase 3 title
    in a Phase 4 report).

    Returns list of violations (empty if clean).
    """
    violations: List[Dict[str, str]] = []
    artifacts = phase_artifact_relpaths(project_root, phase)

    for rel_path in artifacts:
        fpath = project_root / rel_path
        if not fpath.exists():
            continue

        try:
            content = fpath.read_text(encoding="utf-8", errors="replace")
        except Exception as exc:
            print(f"[WARN] cross_artifact: could not read {fpath}, skipping it: {exc}", file=sys.stderr)
            continue

        # Find H1 heading (# Title)
        h1_match = re.search(r'^#\s+(.+)$', content, re.MULTILINE)
        if not h1_match:
            continue

        title = h1_match.group(1)

        # Check for wrong phase number in title
        wrong_phase = None
        for p in VALID_PHASES:
            if p == phase:
                continue
            if f"Phase {p}" in title or f"phase {p}" in title:
                wrong_phase = p
                break

        if wrong_phase is not None:
            violations.append({
                "file": rel_path,
                "issue": f"Title says 'Phase {wrong_phase}' but file belongs to Phase {phase}",
                "title": title,
                "severity": "HIGH",
                "suggestion": f"Update H1 to reference Phase {phase}",
            })

        elif f"Phase {phase}" not in title and f"phase {phase}" not in title:
            violations.append({
                "file": rel_path,
                "issue": f"Title does not reference Phase {phase}",
                "title": title,
                "severity": "MEDIUM",
                "suggestion": f"Include 'Phase {phase}' in the H1 heading",
            })

    return violations


def check_fr_coverage(project_root: Path, _phase: int) -> List[Dict[str, str]]:
    """Verify FRs claimed in TEST_RESULTS.md have entries in sessions_spawn.log.

    Args:
        _phase: Reserved for future per-phase FR coverage rules.
    """
    layout = ProjectLayout(project_root)
    violations: List[Dict[str, str]] = []

    results_path = layout.phase4_testing_dir / "TEST_RESULTS.md"
    if not results_path.exists():
        return violations

    spawn_log = layout.sessions_spawn_log
    if not spawn_log.exists():
        return [{
            "file": layout.get_relative_str(results_path),
            "issue": "Cannot cross-validate: sessions_spawn.log not found",
            "severity": "HIGH",
        }]

    # Extract FR IDs from TEST_RESULTS.md
    try:
        results_content = results_path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return violations

    claimed_frs: set[str] = set()
    # (?<![A-Za-z]) so "NFR-06" is not miscounted as a claim of FR-06 —
    # NFR rows in TEST_RESULTS.md are legitimate and have no fr_id session
    # log entries, so the substring match produced false-positive HIGHs.
    for m in re.finditer(r'(?<![A-Za-z])FR-(\d+)', results_content, re.IGNORECASE):
        claimed_frs.add(f"FR-{m.group(1).zfill(2)}")

    if not claimed_frs:
        return violations

    # Extract FR IDs from sessions_spawn.log
    try:
        log_content = spawn_log.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return violations

    logged_frs: set[str] = set()
    for line in log_content.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        fr = entry.get("fr_id", "")
        if fr:
            logged_frs.add(fr)

    # FRs claimed in TEST_RESULTS but missing from session log
    unverified = claimed_frs - logged_frs
    for fr in sorted(unverified):
        violations.append({
            "file": layout.get_relative_str(results_path),
            "fr_id": fr,
            "issue": f"{fr} claimed in TEST_RESULTS.md but has no session log evidence",
            "severity": "HIGH",
            "suggestion": f"Dispatch Agent A/B for {fr} or remove from test results",
        })

    return violations


def check_coverage_report(project_root: Path, _phase: int) -> List[Dict[str, str]]:
    """Validate COVERAGE_REPORT.md numbers against actual pytest --cov output.

    Parses COVERAGE_REPORT.md for coverage percentage claims and runs
    pytest --cov to compare. Only compares line coverage (most commonly
    reported).

    Returns list of violations (empty if clean or coverage tool unavailable).
    """
    layout = ProjectLayout(project_root)
    violations: List[Dict[str, str]] = []

    cov_report = layout.phase4_testing_dir / "COVERAGE_REPORT.md"
    if not cov_report.exists():
        return violations

    try:
        cov_content = cov_report.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return violations

    # Extract claimed coverage percentage
    # Match only on the "Line coverage" line to avoid picking up target/other %.
    # Allow optional leading bullet/indentation ("- Line coverage: 85%").
    claimed_match = re.search(
        r'(?im)^[\s\-*]*Line coverage[^\d]*(\d{2,3}(?:\.\d)?)\s*%',
        cov_content,
    )
    if not claimed_match:
        # Bare percentage: "Overall: 85%" or "Total: 95%"
        bare_m = re.search(
            r'(?im)^[\s\-*]*(?:Total|Overall|Average)[:\s]+(\d{2,3}(?:\.\d)?)\s*%',
            cov_content,
        )
        if not bare_m:
            return violations  # No numeric claim to validate
        claimed_pct = float(bare_m.group(1))
    else:
        claimed_pct = float(claimed_match.group(1))

    # Try running pytest --cov to get actual coverage.
    # Costs up to 120s per finalize-gate call, so it is opt-in: the
    # HARNESS_CROSS_ARTIFACT_COV env var decides when set (per-invocation
    # override, "1" = on, anything else = off); otherwise the persistent
    # per-project features.cross_artifact_live_cov flag decides (Round 9;
    # default False = the pre-flag behavior). When off, fall back to
    # .coverage data if available.
    import os as _os
    import sys

    from core.quality_gate.source_tree_lock import run_against_source_tree

    _live_env = _os.environ.get("HARNESS_CROSS_ARTIFACT_COV")
    if _live_env is not None:
        _live_cov = _live_env == "1"
    else:
        from core.harness_config import get_feature
        _live_cov = bool(get_feature(project_root, "cross_artifact_live_cov"))
    if _live_cov:
        test_target = "."
        if layout.active_test_dir.is_dir():
            test_target = layout.get_relative_str(layout.active_test_dir)
            
        try:
            # Round 66: waits out any in-flight mutation window and kills the
            # whole group on timeout. Before this, a coverage number taken here
            # could be measured against a tree mutmut was mid-way through
            # mutating, and a slow suite left its xdist workers behind.
            result = run_against_source_tree(
                [sys.executable, "-m", "pytest", test_target, "--cov=.", "--cov-report=term-missing", "-q"],
                project=project_root,
                timeout=120,
            )
        except Exception:
            return violations  # Cannot verify — skip
    else:
        # Fast path: try to use existing .coverage SQLite data
        coverage_data = project_root / ".coverage"
        if not coverage_data.exists():
            return violations  # No coverage data — skip silently
        try:
            result = run_against_source_tree(
                [sys.executable, "-m", "coverage", "report", "--format=total"],
                project=project_root,
                timeout=15,
            )
        except Exception:
            return violations

    # Parse coverage output. Two formats:
    # 1. pytest --cov: "TOTAL    123    45    85%" (term-missing table)
    # 2. coverage report --format=total: just "85" or "85.0" on stdout
    actual_match = re.search(
        r'TOTAL\s+\d+\s+\d+\s+(\d{2,3}(?:\.\d)?)\s*%',
        result.stdout + result.stderr,
    )
    if actual_match:
        actual_pct = float(actual_match.group(1))
    else:
        # Try --format=total output (bare number)
        stripped = result.stdout.strip()
        if stripped and re.match(r'^\d{1,3}(?:\.\d+)?$', stripped):
            actual_pct = float(stripped)
        else:
            return violations  # Cannot parse — skip
    diff = abs(claimed_pct - actual_pct)

    if diff > 10:
        violations.append({
            "file": layout.get_relative_str(cov_report),
            "issue": (
                f"Coverage mismatch: report claims {claimed_pct}% but "
                f"pytest --cov measured {actual_pct}% (diff={diff:.1f}%)"
            ),
            "severity": "CRITICAL",
            "claimed": str(claimed_pct),
            "actual": str(actual_pct),
        })
    elif diff > 5:
        violations.append({
            "file": layout.get_relative_str(cov_report),
            "issue": (
                f"Coverage discrepancy: report claims {claimed_pct}% vs "
                f"actual {actual_pct}% (diff={diff:.1f}%)"
            ),
            "severity": "HIGH",
            "claimed": str(claimed_pct),
            "actual": str(actual_pct),
        })

    return violations


def run_cross_artifact_checks(
    project_root: Path, phase: int
) -> Dict[str, Any]:
    """Run all cross-artifact consistency checks for a phase.

    Returns:
        {"passed": bool, "violations": [...], "checks_ran": int}
    """
    violations: List[Dict[str, str]] = []
    ran = 0

    # Every phase that has deliverables: these two read the artifacts *this*
    # phase produced, through phase_artifact_relpaths.
    violations.extend(check_phase_title(project_root, phase))
    violations.extend(check_unfilled_placeholders(project_root, phase))
    ran += 2

    # Phase 4 only. All three read Phase 4's artifacts — TEST_RESULTS.md and
    # COVERAGE_REPORT.md — so re-running them at Phase 5-8 would re-judge a
    # document this phase did not write. Round 55 站6 narrowed these from
    # `phase >= 4` when Phase 5-8 gained a cross-artifact check for the first
    # time; before that the difference was unobservable, because Phase 5-8
    # never called this function at all. The narrowing also keeps
    # check_test_count_reconciliation off a path where `run_suite`'s memo is
    # cold — Phase 5-8 has no check_pytest ahead of it, so each call there
    # would execute the project's whole suite again.
    if phase == 4:
        violations.extend(check_fr_coverage(project_root, phase))
        violations.extend(check_coverage_report(project_root, phase))
        violations.extend(check_test_count_reconciliation(project_root, phase))
        ran += 3

    criticals = [v for v in violations if v.get("severity") == "CRITICAL"]
    highs = [v for v in violations if v.get("severity") == "HIGH"]

    passed = len(criticals) == 0

    if violations:
        print(f"\n[Cross-Artifact] {len(violations)} issue(s):")
        for v in violations:
            sev = v.get("severity", "INFO")
            print(f"  [{sev}] {v.get('file', '?')}: {v.get('issue', '?')}")

    return {
        "passed": passed,
        "violations": violations,
        "checks_ran": ran,
        "critical_count": len(criticals),
        "high_count": len(highs),
    }
