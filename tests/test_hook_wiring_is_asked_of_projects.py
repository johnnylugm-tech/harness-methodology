"""The framework installs hooks into projects and never asks about them again.

Round 81 站4. `cli/project_cmds.py cmd_init_project` installs two things:
step 2 writes the CI workflow, step 3 runs scripts/setup-git-hooks.sh. Of
`core/doctor.py`'s sixteen checks, one — `_check_ci_template_drift` (Round 40
站1) — goes back and asks whether the deployed workflow is still the one this
harness ships. Nothing went back to the hooks.

The two decay differently, and the wrong one was guarded. The CI workflow is a
committed file; it travels with the repository. The hooks are `.git/hooks/*`
plus `core.hooksPath` in `.git/config`, and **`git clone` copies neither**. Any
consumer project that has been cloned since init has four dead hooks —
pre-push, pre-commit, post-merge, prepare-commit-msg — and nothing in this
framework was in a position to see it.

WHY THIS IS NOT THE LIMITATION ROUND 80 站3 DECLARED UNCLOSABLE

That script's header says "closing it needs a required status check on the
branch". True of what it was about: `git push --no-verify`, and anyone who
never runs self_check. Not true of a project whose hooks are simply absent —
that one needs someone to ask, and the asking is free. 站3 wrote the two as one
limitation, and only the first half needs branch protection.

WHY THE FIXTURE BUILDS A REAL REPOSITORY

`_check_hook_wiring` is fail-soft on a tree git does not manage, per
`core/doctor_checks/git_state.py`'s own rule. Every doctor fixture in this
suite is a bare `tmp_path`, so the check is correctly silent in all of them —
and a guard that only ever exercises the silent path is the absent witness
Round 46 is named after. `git init` here, deliberately.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

pytestmark = [pytest.mark.core]

REPO = Path(__file__).resolve().parents[1]
RECORDED = REPO / "tests" / "golden" / "hook_wiring" / "operator_report.json"


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True,
                   capture_output=True, text=True)


@pytest.fixture(autouse=True)
def _not_ci(monkeypatch):
    """CI short-circuits the predicate to `n/a`, which would make every case
    below vacuously pass on the runner — the exact shape this file exists to
    stop."""
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)


def _project(tmp_path: Path, *, hook: str | None) -> Path:
    """A git repository, optionally with a wired pre-push hook.

    `hook` is None (none installed), "exec" or "noexec".
    """
    project = tmp_path / "proj"
    (project / ".methodology").mkdir(parents=True)
    (project / ".methodology" / "state.json").write_text(
        json.dumps({"state": "RUNNING", "current_phase": 1}), encoding="utf-8"
    )
    _git(project, "init", "-q")
    if hook is not None:
        hooks = project / "scripts" / "hooks"
        hooks.mkdir(parents=True)
        target = hooks / "pre-push"
        target.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        target.chmod(0o755 if hook == "exec" else 0o644)
        _git(project, "config", "core.hooksPath", "scripts/hooks")
    return project


def _hook_findings(project: Path):
    from core.doctor import run_doctor

    return [f for f in run_doctor(project) if f.check == "git-hooks"]


# ── the check itself ─────────────────────────────────────────────────────────

def test_a_project_whose_hooks_a_clone_dropped_is_reported(tmp_path):
    findings = _hook_findings(_project(tmp_path, hook=None))

    assert len(findings) == 1, [f.message for f in findings]
    assert findings[0].severity == "WARN", (
        "WARN, matching the sibling check this one was modelled on. Written as "
        "ERROR first; two e2e journeys turned red because cmd_doctor exits 1 on "
        "any ERROR and a FRESH CLONE of any project has no hooks, so ERROR here "
        "fires on the normal state of a new checkout. See _check_hook_wiring's "
        "docstring for the reversal and its re-open condition."
    )
    assert "clone" in findings[0].message, (
        f"the finding has to name the cause an operator can act on — a clone "
        f"drops .git/hooks and core.hooksPath — or it reads as a broken "
        f"install: {findings[0].message!r}"
    )


def test_a_wired_project_is_silent(tmp_path):
    """The positive control. Without it this file proves only that doctor can
    complain, not that it can stop."""
    assert _hook_findings(_project(tmp_path, hook="exec")) == []


def test_a_hook_git_cannot_execute_is_reported(tmp_path):
    findings = _hook_findings(_project(tmp_path, hook="noexec"))

    assert len(findings) == 1 and findings[0].severity == "WARN"
    assert "not executable" in findings[0].message


def test_a_tree_git_does_not_manage_is_silent(tmp_path):
    """Fail-soft, the rule core/doctor_checks/git_state.py states for itself.

    Also the reason the cases above build a real repository: every other doctor
    fixture in this suite lands here.
    """
    project = tmp_path / "proj"
    (project / ".methodology").mkdir(parents=True)
    (project / ".methodology" / "state.json").write_text(
        json.dumps({"state": "RUNNING", "current_phase": 1}), encoding="utf-8"
    )
    assert _hook_findings(project) == []


# ── one predicate, two readers ───────────────────────────────────────────────

def test_doctor_asks_through_the_shared_predicate(tmp_path, monkeypatch):
    """Replacing the predicate must change doctor's answer.

    A doctor check that reached for git itself would be unmoved by this and
    would keep agreeing with the shell script by coincidence rather than by
    construction.
    """
    import core.doctor_checks.git_state as gs

    project = _project(tmp_path, hook="exec")
    assert _hook_findings(project) == []

    import core.git_hooks as gh
    monkeypatch.setattr(
        gh, "pre_push_hook_status",
        lambda repo: gh.HookWiring("missing", ".git/hooks/pre-push", ""),
    )
    assert gs._check_hook_wiring(project), (
        "doctor did not go through core.git_hooks.pre_push_hook_status — "
        "swapping that function left its verdict unchanged"
    )


@pytest.mark.parametrize("hook", [None, "exec", "noexec"])
def test_the_script_prints_exactly_what_the_predicate_says(tmp_path, hook):
    """Round 17's rule, as a behaviour rather than a source read.

    The script is supposed to hold no copy of the question and no copy of the
    answers. If it held either, its stdout could drift from what the in-process
    predicate produces for the same tree. Run both against the same repository
    in every reachable state and compare.
    """
    from core.git_hooks import operator_report, pre_push_hook_status

    project = _project(tmp_path, hook=hook)
    expected = operator_report(pre_push_hook_status(project))

    result = subprocess.run(
        ["bash", str(REPO / "scripts" / "check_hook_wiring.sh"), str(project)],
        capture_output=True, text=True,
        env={k: v for k, v in os.environ.items()
             if k not in ("CI", "GITHUB_ACTIONS")},
    )

    assert result.stdout.rstrip("\n") == expected, (
        f"scripts/check_hook_wiring.sh and core.git_hooks disagree about the "
        f"same repository, so one of them is answering from its own copy of "
        f"the question.\n  script:    {result.stdout!r}\n  predicate: {expected!r}"
    )
    assert result.returncode == (0 if hook == "exec" else 1)


def test_the_operator_report_is_byte_for_byte_what_the_script_used_to_print():
    """The only counter-proof a rewrite can offer where a move offers sha256.

    Round 80 站3's script held the question AND the five answers. 站4 moved both
    into core/git_hooks.py so doctor could reuse the question. That is a
    rewrite, so its safety property is the observable surface — and that
    surface is finite and reproducible: wired, missing, not-executable,
    not-a-repo, CI. All five were recorded from the shell script at bb15023d,
    before it was touched.
    """
    from core.git_hooks import HookWiring, operator_report

    recorded = json.loads(RECORDED.read_text(encoding="utf-8"))
    for case in recorded:
        wiring = HookWiring(**case["wiring"])
        assert operator_report(wiring) == case["report"], (
            f"state {case['wiring']['status']!r} no longer prints what "
            f"scripts/check_hook_wiring.sh printed before Round 81 站4. A "
            f"rewrite whose output moved is a rewrite, not a relocation."
        )
