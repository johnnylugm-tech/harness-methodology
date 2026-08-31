"""Round 83 站4 — doctor reported "0 error(s)" on a main that was red.

Measured during this round's own investigation: `6ba535e7` was pushed at 16:37
with `Framework Self-Tests` failing, `aacac81f` fixed it at 19:16, and for the
three hours between them `harness_cli.py doctor --project .` printed

    0 error(s), 2 other finding(s)

Nothing in the tooling said main was red. CI's own UI was the only place that
fact existed, and doctor is the command an operator runs when they want to
know whether anything is wrong.

The producer already existed and already had a reader: `find_latest_green_sha`
is read by `preflight_submodule_pin_ci`, and this round measured it working —
it returns the last green commit and refuses a red pin, so a CONSUMING project
is protected. What had no reader was the framework repo's own view of itself.

One `gh` call for HEAD, not a walk: "is the commit I am on red" is the
cheapest true form of the question. `unavailable` is silence, because an
offline laptop is not evidence about the tree (Round 32 站4).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from core.doctor_checks.git_state import _check_head_ci_verdict

pytestmark = [pytest.mark.core]


def _runner_returning(*names_and_conclusions):
    payload = json.dumps([{"name": n, "conclusion": c}
                          for n, c in names_and_conclusions])

    def _run(_cmd):
        return 0, payload, ""
    return _run


def _unavailable_runner(_cmd):
    return 1, "", "gh: command not found"


def _repo(tmp_path: Path, *, framework: bool) -> Path:
    """A throwaway repo, with or without a TRACKED scripts/self_check.sh —
    the same predicate `scripts/hooks/pre-push` reads to tell this repo from
    a consuming project."""
    proj = tmp_path / ("fw" if framework else "consumer")
    (proj / "scripts").mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=proj, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=proj, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=proj, check=True)
    (proj / "README.md").write_text("x\n", encoding="utf-8")
    if framework:
        (proj / "scripts" / "self_check.sh").write_text("#!/bin/bash\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=proj, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=proj, check=True)
    return proj


def test_a_red_head_is_an_error(tmp_path):
    findings = _check_head_ci_verdict(
        _repo(tmp_path, framework=True),
        runner=_runner_returning(("Harness CI", "failure")))
    assert len(findings) == 1 and findings[0].severity == "ERROR"
    assert "Harness CI" in findings[0].message, (
        "the finding has to name the check that failed — 'CI is red' sends "
        f"the operator to the web UI to find out what: {findings[0].message}")
    assert "find_latest_green_sha" in findings[0].message, (
        "and it has to name the way out, which is the producer that already "
        "answers it (Round 48: a halt names its own repair)")


def test_a_green_head_is_silent(tmp_path):
    assert _check_head_ci_verdict(
        _repo(tmp_path, framework=True),
        runner=_runner_returning(("Harness CI", "success"))) == []


def test_an_unobtainable_verdict_is_not_a_finding(tmp_path):
    """No `gh`, no network, no run yet. Could-not-measure is not a finding
    (Round 32 站4) — the inversion would turn every offline `doctor` into an
    ERROR about a tree it learned nothing about."""
    assert _check_head_ci_verdict(
        _repo(tmp_path, framework=True), runner=_unavailable_runner) == []


def test_a_consuming_project_is_never_asked(tmp_path):
    """A project that does not track scripts/self_check.sh has no
    `Framework Self-Tests` to be red, so the question is not meaningful there
    — and asking it would spend a network call per doctor run to learn
    nothing. The red runner proves the check returned before reaching it."""
    assert _check_head_ci_verdict(
        _repo(tmp_path, framework=False),
        runner=_runner_returning(("Harness CI", "failure"))) == []
