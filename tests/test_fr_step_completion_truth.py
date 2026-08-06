"""Round 41 站0 — a TDD step is done when its own definition is satisfied.

`_fr_step_already_done` decides whether run-fr-step / resume-fr-phase may skip
a step. For the three TDD steps it decides that from commit-message
archaeology: `git log --grep "feat(FR-04): GREEN"` plus, for GREEN, a scan for
a `[FR-04]` tag in some source docstring. Neither of those is what GREEN means.
GREEN means the test that was failing now passes.

taskq-api walked into the gap on 2026-08-06. Its FR-04 TDD-GREEN dispatch was
cut off by a transport failure AFTER it had committed (`0311a42 feat(FR-04):
GREEN`, dispatch recorded `status=ERROR error_class=INFRA_ERROR`), leaving a
suite with three failures — one of them FR-04's own test, two of them
regressions in FR-03, an FR that had already scored Gate 1 100.0. Measured on
that tree at 17:19: `pytest` 3 failed / 33 passed, and
`_fr_step_already_done("TDD-GREEN", "FR-04")` -> True.

So the framework advanced to TDD-IMPROVE, whose agent correctly refused to
refactor on a red baseline and returned no commit; the refusal was recorded as
`Commit-required step 'TDD-IMPROVE' returned empty commit`, exit 1; and
`resume-fr-phase` pointed at TDD-IMPROVE again. Eight byte-identical failures,
$6.02, 3h11m, and no way out through any CLI the framework offers.

The framework already owns the answer: `core.quality_gate.test_suite_run
.run_suite` (Round 25 站1), memoised per process and fingerprinted against the
tree, with `SuiteResult.test_outcomes` giving per-test granularity from the same
junitxml the coverage run already produces. It has five consumers. The decision
"has this step been done" was not one of them.

Scope of the truth check is deliberately THIS FR's test family, not the whole
suite: a red suite may be red because of the FR being worked on next, and
Gate 1 is where the whole-suite verdict belongs. GREEN answers for itself.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

import harness_cli  # noqa: F401  entry-first load order
from cli.fr_cmds import _fr_step_already_done  # noqa: E402

pytestmark = [pytest.mark.core]


_PASSING_TEST = (
    '"""Acceptance test for FR-01."""\n'
    "\n"
    "from widget import handle\n"
    "\n"
    "\n"
    "def test_fr01_handles_input():\n"
    "    assert handle('x') == 'X'\n"
)

_FAILING_TEST = (
    '"""Acceptance test for FR-01."""\n'
    "\n"
    "from widget import handle\n"
    "\n"
    "\n"
    "def test_fr01_handles_input():\n"
    "    assert handle('x') == 'NOPE'\n"
)

_SRC = (
    '"""Widget implementation.\n'
    "\n"
    "[FR-01]\n"
    '"""\n'
    "\n"
    "\n"
    "def handle(value):\n"
    '    """Upper-case the value. [FR-01]"""\n'
    "    return value.upper()\n"
)


def _fr_project(tmp_path: Path, *, test_body: str, commit_subjects: list[str]) -> Path:
    """A project whose FR-01 test family really runs, with real commits.

    Layout matches ProjectLayout's canonical answer (03-development/src and
    03-development/tests), so resolve_targets picks real directories and
    run_suite actually measures rather than returning ran=False.
    """
    (tmp_path / "03-development" / "src").mkdir(parents=True, exist_ok=True)
    (tmp_path / "03-development" / "tests").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".methodology").mkdir(parents=True, exist_ok=True)
    (tmp_path / "03-development" / "src" / "widget.py").write_text(_SRC, encoding="utf-8")
    (tmp_path / "03-development" / "tests" / "test_fr01.py").write_text(
        test_body, encoding="utf-8"
    )
    # Import the module under test without installing it.
    (tmp_path / "conftest.py").write_text(
        "import sys\n"
        "from pathlib import Path\n"
        "sys.path.insert(0, str(Path(__file__).parent / '03-development' / 'src'))\n",
        encoding="utf-8",
    )
    (tmp_path / ".methodology" / "state.json").write_text(
        json.dumps({"language": "python", "current_phase": 3}), encoding="utf-8"
    )
    subprocess.run(["git", "init", "-q"], cwd=str(tmp_path), check=True)
    subprocess.run(["git", "add", "-A"], cwd=str(tmp_path), check=True)
    env = {
        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
        "PATH": "/usr/bin:/bin:/usr/local/bin",
    }
    for i, subject in enumerate(commit_subjects):
        subprocess.run(
            ["git", "commit", "-q", "--allow-empty", "-m", subject],
            cwd=str(tmp_path), check=True, env=env,
        )
        _ = i
    return tmp_path


# ══════════════════════════════════════════════════════════════════════════════
# The two the deadlock is made of
# ══════════════════════════════════════════════════════════════════════════════


def test_green_is_not_done_while_its_own_tests_fail(tmp_path):
    """A GREEN commit whose test still fails has not achieved GREEN.

    This is the exact taskq-api shape: the commit landed, a source docstring
    carries the FR tag, and the FR's test is red. Today both of the checks
    `_fr_step_already_done` performs are satisfied, so the step is skipped
    forever and the pipeline advances onto a broken baseline.
    """
    project = _fr_project(
        tmp_path, test_body=_FAILING_TEST,
        commit_subjects=["test(RED): failing test for FR-01", "feat(FR-01): GREEN"],
    )
    assert _fr_step_already_done("TDD-GREEN", "FR-01", project, phase=3) is False, (
        "TDD-GREEN was reported done while its own acceptance test fails — "
        "'done' was decided by the commit message, not by the step's definition"
    )


def test_red_is_not_done_while_its_own_tests_pass(tmp_path):
    """RED is defined by a test that FAILS; a passing one is not a RED state.

    The mirror of the case above, and the reason a fix cannot simply be
    'require the suite to be green': each step has its own truth condition and
    they point in opposite directions.
    """
    project = _fr_project(
        tmp_path, test_body=_PASSING_TEST,
        commit_subjects=["test(RED): failing test for FR-01"],
    )
    assert _fr_step_already_done("TDD-RED", "FR-01", project, phase=3) is False, (
        "TDD-RED was reported done while its acceptance test passes — the file "
        "exists and the commit exists, but nothing about that is a RED state"
    )


# ══════════════════════════════════════════════════════════════════════════════
# Positive controls — a fix that just returns False everywhere is not a fix
# ══════════════════════════════════════════════════════════════════════════════


def test_green_is_done_when_its_own_tests_pass(tmp_path):
    project = _fr_project(
        tmp_path, test_body=_PASSING_TEST,
        commit_subjects=["test(RED): failing test for FR-01", "feat(FR-01): GREEN"],
    )
    assert _fr_step_already_done("TDD-GREEN", "FR-01", project, phase=3) is True


def test_red_is_done_when_its_own_tests_fail(tmp_path):
    project = _fr_project(
        tmp_path, test_body=_FAILING_TEST,
        commit_subjects=["test(RED): failing test for FR-01"],
    )
    assert _fr_step_already_done("TDD-RED", "FR-01", project, phase=3) is True


def test_no_commit_still_means_not_done(tmp_path):
    """Commit evidence stays a necessary condition. A green suite proves the
    code works; it does not prove this step ran."""
    project = _fr_project(
        tmp_path, test_body=_PASSING_TEST, commit_subjects=["chore: unrelated"],
    )
    assert _fr_step_already_done("TDD-GREEN", "FR-01", project, phase=3) is False


def test_a_project_with_nothing_to_measure_keeps_its_old_answer(tmp_path):
    """Round 32's rule: could-not-measure is not a failing measurement.

    With no source directory, run_suite returns ran=False. That must leave the
    commit-evidence answer standing rather than blocking a project the
    framework cannot measure (a non-Python project reaches exactly this path).
    """
    (tmp_path / "03-development" / "tests").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".methodology").mkdir(parents=True, exist_ok=True)
    (tmp_path / "03-development" / "tests" / "test_fr01.py").write_text(
        "def test_fr01_x():\n    assert True\n", encoding="utf-8"
    )
    (tmp_path / "03-development" / "src2").mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=str(tmp_path), check=True)
    subprocess.run(["git", "add", "-A"], cwd=str(tmp_path), check=True)
    env = {
        "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
        "PATH": "/usr/bin:/bin:/usr/local/bin",
    }
    subprocess.run(
        ["git", "commit", "-q", "-m", "test(RED): failing test for FR-01"],
        cwd=str(tmp_path), check=True, env=env,
    )
    assert _fr_step_already_done("TDD-RED", "FR-01", tmp_path, phase=3) is True
