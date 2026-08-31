"""Round 79 站5 — the hook may not reach `exit 0` without saying what it ran.

`scripts/hooks/pre-push` is the only thing between a red tree and main, and it
had four ways to exit 0 having checked nothing:

    line 148  tests/REGRESSION_GUARDS.yaml or verify_regression_guards.py
              missing  ->  the whole block skipped, INCLUDING self_check.sh
              and the dirty-tree gate, with no output at all
    line 246  scripts/self_check.sh not EXECUTABLE  ->  skipped, no output
    line  55  python not found  ->  Warning, exit 0
    line  61  harness_cli.py not found  ->  Warning, exit 0

The first two printed nothing, so "the hook ran and everything passed" and
"the hook ran and checked nothing" produced identical terminal output and
identical exit codes. Round 46's shape: an absent witness is not a failed
testimony.

Measured cost, 2026-08-26. `4c24cf37` landed on main red on five checks that
run inside `scripts/self_check.sh` — the unclassified dispatch label, the
RUNALL_MAX_BYTES ratchet, the ratchet note's own measurement, the sim testbed,
and the wrapper's dispatch count. Its own commit-to-CI interval was 8 seconds,
shorter than a self_check run. `36d8c1d6`'s commit message records what
followed: a run-all preflight on taskq-cc-new saw the red pin, rewound to the
last green commit (`0978364c`, by then stale), and FR-01..FR-03's Gate 1
scores landed against a harness whose fix had been reverted.

Why the branches were wrong is narrower than "they were skips". A consuming
project genuinely has no self-check suite, so the skip is right THERE and
wrong HERE, and the two were told apart by asking the FILESYSTEM whether a
file existed and was executable. `git ls-files --error-unmatch
scripts/self_check.sh` asks the repo instead: a chmod cannot change what git
tracks. That predicate is what these tests pin.

Two things this deliberately does NOT close, recorded rather than papered
over:

  * `git push --no-verify` never invokes the hook, so nothing inside it can
    detect that path. Branch protection is the only mechanism that could, and
    it is out of scope by standing instruction.
  * a COMMITTED removal of scripts/self_check.sh makes `git ls-files` answer
    "not the framework repo", and the hook then has nothing to enforce. That
    is the predicate working as intended: a committed deletion is in the diff
    and reviewable, unlike a chmod or a half-finished checkout, which are the
    silent states this closes. A guard cannot defend against its own
    deliberate, recorded removal.

A fifth test was drafted and dropped: it deleted self_check.sh and asserted a
block, which the OLD hook also produced — the unlink left the tree dirty and
Round 78 站4's gate fired. Committing the deletion first turned it into the
case in the bullet above. Vacuous either way; the counter-proof is what
surfaced it (three of the then-six turned red against the old hook and that
one did not).

These drive the real script in a throwaway repo, the same idiom as
tests/test_pre_push_measures_the_pushed_tree.py and
tests/test_pre_push_phase_detection.py — reading the script for a string
would repeat the mistake Round 79 站4 found in the sim testbed.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

pytestmark = [pytest.mark.core]

REPO_ROOT = Path(__file__).resolve().parent.parent
HOOK_SCRIPT = REPO_ROOT / "scripts" / "hooks" / "pre-push"

_SELF_CHECK_RAN = "STUB_SELF_CHECK_RAN"
_BLOCKED = "PRE-PUSH BLOCKED"

_STUB_HARNESS_CLI = "import sys\nsys.exit(0)\n"
_STUB_SELF_CHECK = f"#!/bin/bash\necho {_SELF_CHECK_RAN}\nexit 0\n"
_STUB_VERIFY_GUARDS = "import sys\nsys.exit(0)\n"


def _repo(tmp_path: Path, *, framework: bool, name: str = "proj") -> Path:
    """A throwaway repo, with or without the files that make it the framework.

    `framework` decides whether scripts/self_check.sh is COMMITTED, which is
    the predicate the hook now reads. Everything else is identical, so a test
    that passes on one and blocks on the other is measuring exactly that.
    """
    proj = tmp_path / name
    (proj / "scripts").mkdir(parents=True)
    (proj / "tests").mkdir()
    (proj / ".methodology").mkdir()

    subprocess.run(["git", "init", "-q"], cwd=proj, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=proj, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=proj, check=True)

    (proj / ".methodology" / "state.json").write_text(
        json.dumps({"current_phase": 1}), encoding="utf-8")
    (proj / "harness_cli.py").write_text(_STUB_HARNESS_CLI, encoding="utf-8")
    if framework:
        (proj / "tests" / "REGRESSION_GUARDS.yaml").write_text("[]\n", encoding="utf-8")
        (proj / "scripts" / "verify_regression_guards.py").write_text(
            _STUB_VERIFY_GUARDS, encoding="utf-8")
        check = proj / "scripts" / "self_check.sh"
        check.write_text(_STUB_SELF_CHECK, encoding="utf-8")
        check.chmod(0o755)

    subprocess.run(["git", "add", "-A"], cwd=proj, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "feat: initial"], cwd=proj, check=True)
    return proj


def _run_hook(proj: Path) -> subprocess.CompletedProcess:
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=proj, check=True,
                          capture_output=True, text=True).stdout.strip()
    stdin = f"refs/heads/main {head} refs/heads/main {'0' * 40}\n"
    return subprocess.run(["bash", str(HOOK_SCRIPT)], cwd=proj, input=stdin,
                          capture_output=True, text=True)


def test_the_framework_repo_runs_its_self_check(tmp_path):
    """The positive control. Without it every test below would pass on a hook
    that blocks unconditionally, which stops all work in this repo."""
    result = _run_hook(_repo(tmp_path, framework=True))
    assert _SELF_CHECK_RAN in result.stdout, result.stdout[-400:]
    assert result.returncode == 0, result.stdout[-400:]


def test_a_stripped_executable_bit_no_longer_hides_the_self_check(tmp_path):
    """The measured hole. `[ -x ]` asked the filesystem, so `chmod -x` on a
    file git still tracks removed the most expensive check in the hook and
    printed nothing about it."""
    proj = _repo(tmp_path, framework=True)
    (proj / "scripts" / "self_check.sh").chmod(0o644)
    # git tracks the mode bit, so the chmod alone leaves the tree dirty and
    # Round 78 站4's gate fires first — correctly. Commit it, so what is
    # measured here is the mode bit and nothing else.
    subprocess.run(["git", "add", "scripts/self_check.sh"], cwd=proj, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "chore: drop the mode bit"],
                   cwd=proj, check=True)

    result = _run_hook(proj)
    assert _SELF_CHECK_RAN in result.stdout, (
        "the mode bit must not decide whether the suite runs — the file is "
        f"tracked, so it is required (stdout={result.stdout[-400:]!r})")


def test_a_missing_guard_registry_blocks_instead_of_skipping_everything(tmp_path):
    """The widest hole: one absent file silently disabled the registry check,
    the added-guard check, the dirty-tree gate and self_check.sh together."""
    proj = _repo(tmp_path, framework=True)
    (proj / "tests" / "REGRESSION_GUARDS.yaml").unlink()

    result = _run_hook(proj)
    assert result.returncode != 0, (
        "the framework repo cannot push with its guard registry gone "
        f"(stdout={result.stdout[-500:]!r})")
    assert _BLOCKED in result.stdout
    assert "REGRESSION_GUARDS.yaml" in result.stdout, (
        "the block must name what is missing, not just refuse (Round 48)")


def test_a_consuming_project_is_untouched(tmp_path):
    """The other side of the predicate, and the reason it is `git ls-files`
    rather than a hostname or a path. A project that never committed
    self_check.sh has no self-check to run; blocking it would push the cost of
    this repo's discipline onto every consumer (Round 42)."""
    result = _run_hook(_repo(tmp_path, framework=False))
    assert result.returncode == 0, result.stdout[-500:]
    assert _BLOCKED not in result.stdout
    assert _SELF_CHECK_RAN not in result.stdout


def test_the_closing_line_says_which_checks_produced_the_verdict(tmp_path):
    """"Pre-push check passed!" read identically whether self_check ran or was
    skipped, and the skip printed nothing — so the operator's only signal for
    the difference was how long the push took."""
    ran = _run_hook(_repo(tmp_path, framework=True)).stdout
    assert "self-check: ran" in ran, ran[-300:]

    skipped = _run_hook(_repo(tmp_path, framework=False, name="consumer")).stdout
    assert "self-check: skipped" in skipped, skipped[-300:]
    assert "framework-repo: false" in skipped


def _run_hook_with_no_refs(proj: Path) -> subprocess.CompletedProcess:
    """Invoke the hook with nothing on stdin — the fifth silent path."""
    return subprocess.run(["bash", str(HOOK_SCRIPT)], cwd=proj, input="",
                          capture_output=True, text=True)


def test_zero_refs_blocks_in_the_framework_repo(tmp_path):
    """Round 83 站4 — the fifth way out, in the same file as the other four.

    `_ALL_HARNESS_CHORE` is initialised to true and only ever moves to false
    INSIDE the ref loop. Zero refs on stdin means the loop body never runs, so
    the flag stayed true and the hook printed "All commits are infrastructure
    (harness submodule) — skipping gate check" and exited 0 having run
    nothing: no guard registry, no self_check, no preflight. The message is
    also false — a push carrying no commits has no infrastructure commits
    either.

    Measured on the harness repo 2026-08-31, at a moment when its own
    self_check was red:

        $ printf '' | bash scripts/hooks/pre-push origin https://example.invalid/x.git
        All commits are infrastructure (harness submodule) — skipping gate check
        EXIT=0
    """
    result = _run_hook_with_no_refs(_repo(tmp_path, framework=True))
    assert result.returncode != 0, (
        "zero refs ran zero checks and exited 0 — the state Round 79 站5 "
        f"closed four other doors on (stdout={result.stdout[-400:]!r})")
    assert _BLOCKED in result.stdout and "no refs" in result.stdout, (
        "the block has to say WHY nothing was read, or the operator's next "
        f"move is to run it again: {result.stdout[-500:]!r}")
    assert "All commits are infrastructure" not in result.stdout, (
        "a push with no commits at all must not be described as one carrying "
        "only infrastructure commits")


def test_zero_refs_is_silent_in_a_consuming_project(tmp_path):
    """The positive control, and the reason the block is not unconditional.

    A consuming project has no self-check suite and no reason to run one, so
    the same condition is a legitimate not-applicable there. Without this the
    test above would pass against a hook that refuses every push everywhere.
    """
    result = _run_hook_with_no_refs(_repo(tmp_path, framework=False))
    assert result.returncode == 0, result.stdout[-400:]
    assert _BLOCKED not in result.stdout, result.stdout[-400:]
