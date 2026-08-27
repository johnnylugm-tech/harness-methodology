"""A pre-push hook nobody installed is a gate nobody has.

Round 80 站3. `tests/test_selfcheck_single_source.py` opened with

    There IS a pre-push hook, and it IS active (`core.hooksPath = scripts/hooks`).

and that file asserts the script exists, is executable, and is named by both
CI and the hook — never that the hook is WIRED. Measured on the clone this was
written in, at dff609e6:

    $ git config --get core.hooksPath        # (nothing; exit 1)
    $ git rev-parse --git-path hooks/pre-push
    .git/hooks/pre-push                      # does not exist

So every check Round 79 站5 added to that hook — the four silent-pass paths it
converted into named BLOCKs — could not fire here, because git had nothing to
invoke. Round 72 recorded the same gap as deferred item B ("`core.hooksPath=
scripts/hooks` 沒有任何東西驗證它有設"), and Round 79 spent a station hardening
the hook's insides while the outside went unasked.

WHAT IS ASKED, AND WHY THAT QUESTION

`git rev-parse --git-path hooks/pre-push` is git's own answer to "which file
would I run", and it resolves through `core.hooksPath` when that is set
(verified against git in a scratch repo before this was written). Asking it
accepts both shapes `scripts/setup-git-hooks.sh` produces — the config, and
the `.git/hooks/*` symlinks it mirrors for older tooling — without this test
having to know which one is in play.

Not asked: whether the resolved hook is the canonical one rather than a stale
physical copy. `setup-git-hooks.sh` already replaces legacy physical hooks with
symlinks, and no incident here has been a drifted copy. Inventing a rule for a
population of zero is the shape Round 55 was about.

HONEST LIMIT

This narrows the window; it does not close it. Someone who does not run
`self_check.sh` will not see this either, and `git push --no-verify` never
invokes a hook at all. The only mechanism that closes it is a required status
check on the branch, which the boss has forbidden touching — recorded in Round
80's ledger with that reason rather than papered over here.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

pytestmark = [pytest.mark.core]

REPO = Path(__file__).resolve().parents[1]
CHECK = REPO / "scripts" / "check_hook_wiring.sh"
SELF_CHECK = REPO / "scripts" / "self_check.sh"


def _run(repo: Path, env_extra: "dict[str, str] | None" = None):
    env = dict(os.environ)
    # The real environment may itself be CI; decide it per test instead.
    env.pop("CI", None)
    env.pop("GITHUB_ACTIONS", None)
    env.update(env_extra or {})
    return subprocess.run(  # nosec B603 B607
        ["bash", str(CHECK), str(repo)],
        capture_output=True, text=True, env=env, timeout=60,
    )


def _scratch_repo(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "-q", str(tmp_path)],  # nosec B603 B607
                   check=True, capture_output=True)
    hooks = tmp_path / "scripts" / "hooks"
    hooks.mkdir(parents=True)
    return tmp_path


def test_a_clone_with_no_hook_installed_is_refused(tmp_path):
    repo = _scratch_repo(tmp_path)

    result = _run(repo)

    assert result.returncode != 0, (
        f"a clone whose `git push` runs no pre-push hook passed the wiring "
        f"check:\n{result.stdout}\n{result.stderr}"
    )
    assert "setup-git-hooks.sh" in result.stdout + result.stderr, (
        "the refusal has to name the command that fixes it — a block that "
        "carries the remedy, not a pointer to it (R24 站1)"
    )


def test_a_wired_clone_passes(tmp_path):
    repo = _scratch_repo(tmp_path)
    hook = repo / "scripts" / "hooks" / "pre-push"
    hook.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    hook.chmod(0o755)
    subprocess.run(  # nosec B603 B607
        ["git", "-C", str(repo), "config", "core.hooksPath", "scripts/hooks"],
        check=True, capture_output=True,
    )

    result = _run(repo)

    assert result.returncode == 0, (
        f"a correctly wired clone was refused:\n{result.stdout}\n{result.stderr}"
    )


def test_a_hook_present_but_not_executable_is_refused(tmp_path):
    """git will not run a hook it cannot execute, so neither may this pass.

    The mode bit is load-bearing HERE in a way Round 79 站5 established it must
    not be inside the hook: git itself consults it before invoking anything.
    """
    repo = _scratch_repo(tmp_path)
    hook = repo / "scripts" / "hooks" / "pre-push"
    hook.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    hook.chmod(0o644)
    subprocess.run(  # nosec B603 B607
        ["git", "-C", str(repo), "config", "core.hooksPath", "scripts/hooks"],
        check=True, capture_output=True,
    )

    result = _run(repo)

    assert result.returncode != 0, (
        f"a non-executable hook passed:\n{result.stdout}\n{result.stderr}"
    )


def test_ci_is_not_applicable_and_says_so_rather_than_passing_quietly(tmp_path):
    """The same rule Round 79 站5 wrote for the hook: a check that did not run
    must not read like one that passed."""
    repo = _scratch_repo(tmp_path)

    result = _run(repo, {"GITHUB_ACTIONS": "true"})

    assert result.returncode == 0, (
        "CI clones never push, so there is nothing to wire — this must not "
        "turn every CI run red"
    )
    assert "not applicable" in result.stdout.lower(), (
        f"CI skipped the check with no statement that it was skipped; that is "
        f"the shape this station exists to remove. stdout={result.stdout!r}"
    )


def test_self_check_runs_the_wiring_check_before_anything_else():
    """Ordering is the point: it decides whether the rest ever runs pre-push."""
    body = SELF_CHECK.read_text(encoding="utf-8")
    assert "check_hook_wiring.sh" in body, (
        "scripts/self_check.sh does not run the hook-wiring check, so the one "
        "place that states what must be green before a push does not ask "
        "whether the push will check anything at all"
    )
    steps = [
        line for line in body.splitlines()
        if line.strip().startswith("_step ")
    ]
    assert steps and "check_hook_wiring.sh" in steps[0], (
        f"the wiring check is not the first step; it is the precondition for "
        f"every other one being reached at push time. First step: {steps[:1]}"
    )
