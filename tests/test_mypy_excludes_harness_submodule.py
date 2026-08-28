"""Round 43: the Type Safety (mypy) step of advance-phase must not walk into
the harness submodule's own test fixtures.

`cmd_advance_phase`'s `_advance_prechecks` helper runs `mypy .
--ignore-missing-imports` from a consumer project's root
(harness/cli/phase_cmds.py). Unlike `ruff check .` in the same
block — whose file-walker respects `.gitignore` and stops at nested `.git`
boundaries, i.e. a checked-out submodule — mypy has no such default and walks
straight into `harness/`. harness ships a test fixture shaped like the
framework's own canonical `03-development/` layout
(`harness/tests/fixtures/mutmut_bare_cfg/03-development/tests/conftest.py`),
which collides with any consumer project's own `03-development/tests/
conftest.py` the moment it exists: neither directory has an `__init__.py`, so
mypy cannot tell the two `conftest.py` files apart and aborts with "Duplicate
module named conftest" before checking anything else — a fatal, deterministic
failure for every project using the standard layout, not specific to any one
consumer.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = [pytest.mark.core]


def test_advance_prechecks_pins_the_mypy_exclude_args():
    """Delegation check (same style as test_advance_phase_pragma_guidance.py,
    which pins this same function for its pragma-guidance message): the real
    mypy invocation must reference the named, testable constant, not a
    hand-written `--exclude` literal that could silently drift or be
    dropped."""

    # Round 81 站6: the precheck pipeline is `_advance_prechecks` plus the
    # `_precheck_*` helpers extracted from it. Reading only the caller now
    # answers a question this test never meant to ask.
    from tests.support.pipeline import pipeline_source
    src = pipeline_source("cli/phase_cmds.py", "_advance_prechecks",
                          helper_prefix="_precheck_")
    assert "_MYPY_EXCLUDE_ARGS" in src, (
        "_advance_prechecks's mypy subprocess.run call must splat "
        "_MYPY_EXCLUDE_ARGS — a hand-written --exclude literal here can "
        "silently drift from (or be dropped independently of) the constant "
        "this test and the real-behavior test below both pin."
    )


def test_the_exclude_args_target_the_harness_submodule_path():
    from cli.phase_cmds import _MYPY_EXCLUDE_ARGS

    assert _MYPY_EXCLUDE_ARGS == ["--exclude", "^harness/"]


def _write_collision_fixture(root: Path) -> None:
    """Shape *root* like the real collision: a consumer project's own
    conftest.py, plus a harness/ subtree containing the same-named fixture
    file harness actually ships."""
    real_tests = root / "03-development" / "tests"
    real_tests.mkdir(parents=True)
    (real_tests / "conftest.py").write_text("x = 1\n", encoding="utf-8")

    fixture_tests = (
        root / "harness" / "tests" / "fixtures" / "mutmut_bare_cfg"
        / "03-development" / "tests"
    )
    fixture_tests.mkdir(parents=True)
    (fixture_tests / "conftest.py").write_text("y = 2\n", encoding="utf-8")


@pytest.mark.skipif(shutil.which("mypy") is None, reason="mypy not installed")
def test_without_the_exclude_the_collision_reproduces(tmp_path: Path) -> None:
    """Negative control: proves this fixture shape actually reproduces the
    real bug — so the assertion below is proof the fix works, not just proof
    mypy ran."""
    _write_collision_fixture(tmp_path)
    result = subprocess.run(
        [sys.executable, "-m", "mypy", ".", "--ignore-missing-imports"],
        cwd=str(tmp_path), capture_output=True, text=True,
    )
    assert result.returncode != 0
    assert "Duplicate module named" in result.stdout


@pytest.mark.skipif(shutil.which("mypy") is None, reason="mypy not installed")
def test_with_the_exclude_the_collision_is_resolved(tmp_path: Path) -> None:
    from cli.phase_cmds import _MYPY_EXCLUDE_ARGS

    _write_collision_fixture(tmp_path)
    result = subprocess.run(
        [sys.executable, "-m", "mypy", ".", "--ignore-missing-imports", *_MYPY_EXCLUDE_ARGS],
        cwd=str(tmp_path), capture_output=True, text=True,
    )
    assert "Duplicate module named" not in result.stdout
    assert result.returncode == 0
