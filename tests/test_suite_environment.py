"""What this suite pins about the machine it runs on.

A test that reads an ambient setting is measuring the host, not the code. The
conftest fixtures that pin those settings are themselves untested unless
something asserts the pin is in effect — so this file does, against a host
configured the wrong way on purpose.

CI run 31613445606 is why the file exists: two tests passed on the author's
machine and failed on the runner because `git init` inherits
`init.defaultBranch`, and Round 48 站8 had just made that name decide a verdict.
"""

from __future__ import annotations

import subprocess

import pytest


def _branch(path) -> str:
    return subprocess.run(
        ["git", "-C", str(path), "symbolic-ref", "--short", "HEAD"],
        capture_output=True, text=True,
    ).stdout.strip()


def test_git_init_ignores_a_host_that_says_master(tmp_path, monkeypatch):
    """The pin must beat the host's global config, not merely coexist with it.

    Written as a host that actively disagrees: an assertion made on a machine
    whose git already creates `main` cannot tell a working pin from a lucky
    default, which is the precise reason the runner caught what this machine
    could not.
    """
    hostile = tmp_path / "gitconfig"
    hostile.write_text("[init]\n\tdefaultBranch = master\n", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(hostile))
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", str(hostile))

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "."], cwd=str(repo), check=True)

    assert _branch(repo) == "main", (
        "tests/conftest.py::_git_default_branch_pinned is not in effect — a "
        "`git init` anywhere in this suite would take its branch name from "
        "whatever machine happens to run it"
    )


def test_the_pin_is_an_override_a_test_can_take_back(tmp_path, monkeypatch):
    """Otherwise no test could ever exercise the un-pinned behaviour.

    test_repair_harness.py's fixture guard depends on this: it sets these vars
    to `master` and asserts its own `-b main` still wins.
    """
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "init.defaultBranch")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "master")

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "."], cwd=str(repo), check=True)

    assert _branch(repo) == "master"


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__]))
