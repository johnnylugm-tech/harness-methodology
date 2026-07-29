"""Round 25 站1 — running this project's test suite has ONE implementation.

WHY THIS MODULE EXISTS
----------------------
A single `advance-phase --completed 3` used to run the whole test suite FIVE
times, in one process, seconds apart, with nothing changing in between:

    pytest --cov=…                       gate1_evidence (no test target at all)
    pytest <tests> --cov=…               FrameworkEnforcer, threshold 70/80
    pytest <tests>                       PhaseTruthVerifier.check_pytest
    pytest <tests> --cov=…               PhaseTruthVerifier.check_coverage
    pytest <tests> --cov=… --cov-fail-under=100   _advance_prechecks TDD block

The last one logically implies the first four (all green AND 100% ⇒ all green,
⇒ ≥70, ⇒ ≥80). Measured on the run-all-by-workflow P1-P8 evidence run: P3
prechecks 55.4s of which 53.7s was pytest; P1→P8 totalled 18 suite executions
and ~195s, while every non-test check in advance-phase summed to about 2s.

The duplication was a symptom. The defect was that four call sites each
hand-rolled the argv, so "the project's tests" had three different definitions
and "the project's source" two:

    gate1_evidence          test: (none — pytest rootdir)   cov: active_src_dir
    FrameworkEnforcer       test: hardcoded probe           cov: .coveragerc or "."
    PhaseTruthVerifier      test: active_test_dir           cov: .coveragerc or "."
    _advance_prechecks      test: active_test_dir           cov: active_src_dir

Round 22 already fixed exactly this for the fourth one — its root-cause note
(tests/test_advance_phase_pytest_scope.py) explains that a bare `pytest` from
the project root also collects harness/tests/* because harness/ is vendored
inside the tree. It did not fix the identical sibling in gate1_evidence, which
is still a bare call. The consequence reached a real project:
run-all-by-workflow commit 00e732e patched its own pyproject.toml mid-Phase-4
("Without pinning, pytest discovered the entire tree including harness/tests/*
which crashes during collection"), and the `[tool.mypy] exclude = ["^harness/"]`
in the same file is the same story for `mypy .`. The project was compensating
for a missing SSOT in the framework.

WHAT THIS MODULE GUARANTEES
---------------------------
One canonical execution per process, one canonical (test target, cov target),
one exact coverage number. Every threshold that existed before still exists and
still blocks — they now read the same measurement instead of each commissioning
their own. Measurement and judgement are separate concerns; this module only
measures.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess  # nosec B404
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from core.quality_gate.cov_utils import read_coveragerc_source
from core.utils.project_layout import ProjectLayout

__all__ = [
    "SuiteResult",
    "resolve_targets",
    "suite_timeout",
    "run_suite",
    "reset_suite_cache",
    "DEFAULT_SUITE_TIMEOUT",
]

DEFAULT_SUITE_TIMEOUT = 300

# Files whose content can change what the suite reports without any source or
# test file changing. Part of the fingerprint so the in-process memo cannot
# serve a stale verdict across a config edit.
_CONFIG_FILENAMES = (
    "pyproject.toml",
    "setup.cfg",
    "pytest.ini",
    "tox.ini",
    ".coveragerc",
    "conftest.py",
)


@dataclass(frozen=True)
class SuiteResult:
    """One measurement of the project's test suite. Carries no verdict.

    `coverage` is the exact line percentage (e.g. 99.60317460317461), read from
    coverage's JSON report rather than the `TOTAL … n%` terminal line. The
    terminal line truncates, which is *equivalent* for the integer thresholds
    in use (floor(x) >= T ⟺ x >= T) but over-strict against a fractional
    quality_manifest `min_coverage`, and it reports 85.0% when the truth is
    85.9%. Exactness costs nothing here.

    `ran` is False when no measurement was possible or appropriate — a non-
    Python project, or a project with no source/test directory yet. Callers
    must decide what that means for them; this module does not.
    """

    passed: bool
    coverage: float | None
    test_target: str
    cov_target: str
    returncode: int
    output: str
    ran: bool
    reason: str = ""


# project path -> (fingerprint, SuiteResult)
_CACHE: dict[str, tuple[str, SuiteResult]] = {}


def reset_suite_cache() -> None:
    """Drop the in-process memo. For tests and long-lived processes."""
    _CACHE.clear()


def resolve_targets(project: "str | Path") -> tuple[str, str]:
    """The one definition of (test target, coverage target) for this project.

    test target: `ProjectLayout.active_test_dir`, always explicit. A bare
    `pytest` with no path also collects harness/tests/* when harness/ is
    vendored inside the project (Round 22).

    cov target: an explicit `.coveragerc` `[run] source` wins — a project that
    scoped its own coverage meant it. Otherwise `ProjectLayout.active_src_dir`,
    NOT coverage's `"."` default: measured on the evidence project, `--cov=.`
    pulled `harness_cli.py` (the harness's own shim) and `conftest.py` into the
    denominator and reported 95.98% where the project's source is 100%.

    There is deliberately NO `"."` fallback on either target. A project with no
    source or test directory yet must come back as "nothing to measure" — the
    two callers that used to fall back to `"."` would run pytest over the whole
    project root, which is the collection bug Round 22 removed, reintroduced
    from the other side.
    """
    layout = ProjectLayout(Path(project))
    test_target = layout.get_relative_str(layout.active_test_dir)
    cov_target = layout.get_relative_str(layout.active_src_dir)
    if (Path(project) / ".coveragerc").is_file():
        declared = read_coveragerc_source(Path(project))
        if declared and declared != ".":
            cov_target = declared
    return test_target, cov_target


def suite_timeout(project: "str | Path") -> int:
    """Seconds to allow the suite. Floor 30.

    Precedence, unchanged from PhaseTruthVerifier._get_pytest_timeout (SG-5,
    Round 9 站3): harness_config ``values.phase_truth_pytest_timeout`` > legacy
    ``.methodology/enforcement.json::phase_truth.pytest_timeout_seconds`` >
    the configured default.

    Unifies four divergent settings: PhaseTruthVerifier's configurable value,
    gate1_evidence's hardcoded 120, FrameworkEnforcer's hardcoded 300, and the
    _advance_prechecks TDD block's *absence* of any timeout. That last one is
    the reason a single number is needed at all — an unbounded suite inside an
    unattended run is the stall class Round 24 站5 was written for. Projects
    whose suite legitimately runs longer than the default raise the configured
    value; they no longer get an unbounded run by accident.
    """
    from core.harness_config import get_value, value_is_configured

    root = Path(project)
    try:
        if value_is_configured(root, "phase_truth_pytest_timeout"):
            return max(30, int(get_value(root, "phase_truth_pytest_timeout")))
    except (TypeError, ValueError, KeyError):
        pass

    legacy = _legacy_timeout_seconds(root)
    if legacy is not None:
        return max(30, legacy)

    try:
        return max(30, int(get_value(root, "phase_truth_pytest_timeout")))
    except (TypeError, ValueError, KeyError):
        return DEFAULT_SUITE_TIMEOUT


def _legacy_timeout_seconds(project: Path) -> "int | None":
    """Legacy enforcement.json read + migration nudge, or None."""
    cfg_path = ProjectLayout(project).enforcement_config_path
    if not cfg_path.is_file():
        return None
    try:
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        value = cfg.get("phase_truth", {}).get("pytest_timeout_seconds")
    except (OSError, ValueError, AttributeError):
        return None
    if value is None:
        return None
    print("[test-suite] NOTE: reading legacy enforcement.json "
          "phase_truth.pytest_timeout_seconds — migrate to harness_config.json "
          "values.phase_truth_pytest_timeout (doctor flags this file)")
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _fingerprint(project: Path, test_target: str, cov_target: str) -> str:
    """sha256 over everything that can change what the suite reports.

    The memo below is per-process and every consumer runs within one
    advance-phase call, so this is a tripwire rather than a cache key: if
    anything under source, tests, or the test configuration changed between two
    consumers, the memo is discarded and the suite runs again. Same shape as
    core/quality_gate/env_contract.compute_source_fingerprint, including the
    \\x00 separator so "ab"+"c" cannot collide with "a"+"bc".
    """
    h = hashlib.sha256()
    for rel in (test_target, cov_target):
        base = project / rel
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.py")):
            h.update(str(path.relative_to(project)).encode("utf-8"))
            h.update(b"\x00")
            try:
                h.update(path.read_bytes())
            except OSError as exc:
                # Unreadable file: fold the error in so the fingerprint still
                # changes when the condition clears, instead of silently
                # matching a run that could read it.
                h.update(f"<unreadable:{exc}>".encode("utf-8"))
            h.update(b"\x00")
    for name in _CONFIG_FILENAMES:
        cfg = project / name
        h.update(name.encode("utf-8"))
        h.update(b"\x00")
        if cfg.is_file():
            try:
                h.update(cfg.read_bytes())
            except OSError as exc:
                h.update(f"<unreadable:{exc}>".encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()


def _scrubbed_env() -> dict:
    """os.environ minus anything that looks like a credential.

    Adopted from PhaseTruthVerifier.check_pytest, which was the only one of the
    four call sites doing it. Unifying on the strictest existing behaviour
    cannot lower any bar: check_pytest already runs at P3/P4, so a suite that
    needs a secret in the environment was already blocked there.
    """
    env = os.environ.copy()
    for key in list(env):
        upper = key.upper()
        if any(token in upper for token in ("SECRET", "TOKEN", "KEY", "JWT")):
            env.pop(key, None)
    return env


def _measure(project: Path, test_target: str, cov_target: str) -> SuiteResult:
    timeout = suite_timeout(project)
    with tempfile.TemporaryDirectory(prefix="harness-cov-") as tmpdir:
        json_path = Path(tmpdir) / "coverage.json"
        cmd = [
            sys.executable, "-m", "pytest", test_target,
            "--tb=short", "-q",
            f"--cov={cov_target}",
            "--cov-report=term-missing",
            f"--cov-report=json:{json_path}",
        ]
        try:
            proc = subprocess.run(  # nosec B603
                cmd, cwd=str(project), env=_scrubbed_env(),
                capture_output=True, text=True, timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return SuiteResult(
                passed=False, coverage=None, test_target=test_target,
                cov_target=cov_target, returncode=124, output="",
                ran=True, reason=f"test suite timed out after {timeout}s",
            )
        except FileNotFoundError as exc:
            return SuiteResult(
                passed=False, coverage=None, test_target=test_target,
                cov_target=cov_target, returncode=127, output="",
                ran=False, reason=f"pytest not runnable: {exc}",
            )
        output = proc.stdout + proc.stderr
        coverage = _read_coverage(json_path)
    return SuiteResult(
        passed=proc.returncode == 0, coverage=coverage,
        test_target=test_target, cov_target=cov_target,
        returncode=proc.returncode, output=output, ran=True,
    )


def _read_coverage(json_path: Path) -> float | None:
    """totals.percent_covered from coverage's JSON report, or None."""
    if not json_path.is_file():
        return None
    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
        return float(data["totals"]["percent_covered"])
    except (OSError, ValueError, KeyError, TypeError):
        # A malformed or absent report is "not measured", never "0%" — a
        # fabricated zero would block on a reporting failure and send the
        # agent looking for missing tests that exist.
        return None


def run_suite(project: "str | Path", *, force: bool = False) -> SuiteResult:
    """Measure the project's test suite. Executes at most once per process.

    Repeat callers get the first measurement back, unless the source, tests, or
    test configuration changed since it was taken — then the suite runs again.
    Pass force=True to bypass the memo entirely.
    """
    root = Path(project).resolve()
    key = str(root)
    test_target, cov_target = resolve_targets(root)

    from core.utils.lang_patterns import project_language

    language = project_language(root)
    if language != "python":
        # Round 25: js/ts is deliberately out of scope (R25-DEFER-1). Returning
        # "not measured" keeps this module honest instead of running pytest
        # against a TypeScript tree, and leaves every js/ts caller on the path
        # it already had.
        return SuiteResult(
            passed=False, coverage=None, test_target=test_target,
            cov_target=cov_target, returncode=0, output="", ran=False,
            reason=f"language is {language}, not python — suite not measured here",
        )

    if not (root / cov_target).is_dir():
        return SuiteResult(
            passed=False, coverage=None, test_target=test_target,
            cov_target=cov_target, returncode=0, output="", ran=False,
            reason=f"coverage target {cov_target} is not a directory",
        )
    if not (root / test_target).is_dir():
        return SuiteResult(
            passed=False, coverage=None, test_target=test_target,
            cov_target=cov_target, returncode=0, output="", ran=False,
            reason=f"test target {test_target} is not a directory",
        )

    fingerprint = _fingerprint(root, test_target, cov_target)
    if not force:
        cached = _CACHE.get(key)
        if cached is not None and cached[0] == fingerprint:
            return cached[1]

    result = _measure(root, test_target, cov_target)
    _CACHE[key] = (fingerprint, result)
    return result
