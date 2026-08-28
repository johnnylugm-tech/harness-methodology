"""A hunk header that cannot name a method cannot measure one.

Round 81 站1. `tests/test_function_size_ratchet.py` argued its case with hunk
counts, and the ruler it used was blind to every method in this repository.
Measured over `cli core harness scripts detection` at 122ea009:

    11442  hunk headers in the whole history
     5725  whose context is a top-level `def`
        0  whose context is an indented `def`

Zero, out of 626 methods in 175 classes. Git's built-in DEFAULT diff driver
matches a definition starting in column 0 and nothing else, so every edit
inside a class body was filed under the class. `HarnessBridge.finalize_gate`
— the largest function in the repo, and (once the ruler could see it) the
most-edited one — measured 0.

`.gitattributes` selects git's built-in python driver instead. This file is
the reason that line cannot be deleted by accident.

WHY THE NEGATIVE CONTROL IS THE POINT

Asserting `git check-attr` reports `python` proves the attribute is set, not
that setting it does anything. So the witness below builds a throwaway
repository, edits a method body, and reads the header git actually emits —
twice, with the attribute and without it. If the two agree, the attribute is
not what produces the method name and this file is measuring the wrong thing.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

pytestmark = [pytest.mark.core]

REPO = Path(__file__).resolve().parents[1]

_MODULE_V1 = '''\
class Widget:
    def alpha(self):
        return 1

    def beta(self):
        value = 2
        return value
'''

_MODULE_V2 = '''\
class Widget:
    def alpha(self):
        return 1

    def beta(self):
        value = 3
        return value
'''


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=str(repo), capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, f"git {' '.join(args)} failed: {result.stderr}"
    return result.stdout


def _hunk_headers(repo: Path) -> list[str]:
    out = _git(repo, "diff", "--unified=0", "--", "mod.py")
    return [line for line in out.splitlines() if line.startswith("@@")]


def _throwaway_repo(tmp_path: Path, attributes: str | None) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@example.invalid")
    _git(repo, "config", "user.name", "t")
    if attributes is not None:
        (repo / ".gitattributes").write_text(attributes, encoding="utf-8")
    (repo / "mod.py").write_text(_MODULE_V1, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "init")
    (repo / "mod.py").write_text(_MODULE_V2, encoding="utf-8")
    return repo


def test_this_repo_selects_the_python_diff_driver():
    """The wiring. `.gitattributes` is the only thing that sets it."""
    out = subprocess.run(
        ["git", "check-attr", "diff", "--", "harness/harness_bridge.py"],
        cwd=str(REPO), capture_output=True, text=True, check=False,
    ).stdout
    assert out.strip().endswith(": python"), (
        f"harness/harness_bridge.py is not using the python diff driver — "
        f"`.gitattributes` is missing or no longer says `*.py diff=python`. "
        f"Every hunk header in a class body reverts to naming the class, and "
        f"the 626 methods in this repo become unmeasurable again. Got: {out!r}"
    )


def test_the_driver_is_what_makes_a_method_visible(tmp_path):
    """The witness, and its own negative control.

    Same edit, same git, two repositories: one carrying this repo's attribute
    line and one carrying none. The header must name the method in the first
    and must not in the second — otherwise the attribute is not the mechanism.
    """
    with_driver = _hunk_headers(_throwaway_repo(tmp_path / "a", "*.py diff=python\n"))
    without = _hunk_headers(_throwaway_repo(tmp_path / "b", None))

    assert len(with_driver) == 1 and len(without) == 1, (with_driver, without)

    assert "def beta" in with_driver[0], (
        f"with `*.py diff=python` the header still does not name the edited "
        f"method: {with_driver[0]!r}"
    )
    assert "def beta" not in without[0], (
        f"the header names the method WITHOUT the attribute, so `.gitattributes` "
        f"is not what produces it and this guard is measuring the wrong thing: "
        f"{without[0]!r}"
    )
    assert "class Widget" in without[0], (
        f"expected the default driver to fall back to the enclosing class — the "
        f"exact behaviour that hid every method in this repo: {without[0]!r}"
    )
