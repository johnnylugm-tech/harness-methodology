"""Round 78 站4 — the hook must measure the tree it is about to push.

`scripts/hooks/pre-push` runs `scripts/self_check.sh`, which is exactly what
CI's Framework Self-Tests job runs — ruff, the guard registry, the full pytest
suite. Round 67 站5 wired it here so a red build could not be discovered after
the push instead of before it.

It measures what is on disk. CI measures the commit that goes out. Nothing in
the hook referenced `$_local_sha`, so those are two different trees whenever
the working tree has moved on: commit A red, fix it on disk without
committing, push A — the hook passes and CI fails.

Measured, on the eight commits pushed between `1d111daa` and `70e95c9b`:

    1d111daa  RED  file-size ratchet          repaired by f893c7ae
    0db74f4d  RED  file-size ratchet          repaired by f893c7ae
    d5549c3a  RED  file-size ratchet          repaired by c66402d1
    860b5d32  RED  ruff E741                  repaired by e35b66b8

Four of eight, every repair in the NEXT commit, and every one of those checks
runs in seconds inside the script this hook had already run and passed.

Round 44's shape — the judged tree is not the recorded tree — in the one place
built to stop exactly that. The cheapest true statement is that the two trees
must be the same, so a dirty working tree is refused rather than measured.

These tests run the real hook as a subprocess against a throwaway repo, the
same way tests/test_pre_push_phase_detection.py does. Reading the script for a
string would repeat the mistake Round 78 站1 found in Plan F's own tests.
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

_STUB_HARNESS_CLI = "import sys\nsys.exit(0)\n"
_STUB_SELF_CHECK = f"#!/bin/bash\necho {_SELF_CHECK_RAN}\nexit 0\n"
_STUB_VERIFY_GUARDS = "import sys\nsys.exit(0)\n"


def _framework_repo(tmp_path: Path) -> Path:
    """A repo shaped like this one: the hook's framework-only branch fires
    only when tests/REGRESSION_GUARDS.yaml and the verify script both exist."""
    proj = tmp_path / "proj"
    (proj / "scripts").mkdir(parents=True)
    (proj / "tests").mkdir()
    (proj / ".methodology").mkdir()

    subprocess.run(["git", "init", "-q"], cwd=proj, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=proj, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=proj, check=True)

    (proj / ".methodology" / "state.json").write_text(
        json.dumps({"current_phase": 1}), encoding="utf-8")
    (proj / "harness_cli.py").write_text(_STUB_HARNESS_CLI, encoding="utf-8")
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


def test_a_clean_tree_reaches_the_self_check(tmp_path):
    """The positive control. Without it, a hook that blocks everything would
    pass the two tests below and stop every push in the repo."""
    result = _run_hook(_framework_repo(tmp_path))
    assert _SELF_CHECK_RAN in result.stdout, (
        f"a clean tree must still be measured "
        f"(stdout={result.stdout[-400:]!r} stderr={result.stderr[-400:]!r})")
    assert "working tree is not the tree being pushed" not in result.stdout


def test_an_uncommitted_edit_to_a_tracked_file_blocks_the_push(tmp_path):
    """The measured failure mode: the fix is on disk, the commit going out
    does not have it, and self_check would report on the wrong one."""
    proj = _framework_repo(tmp_path)
    (proj / "harness_cli.py").write_text("import sys\nsys.exit(1)\n", encoding="utf-8")

    result = _run_hook(proj)
    assert result.returncode != 0, "a dirty tree must not be pushed unmeasured"
    assert "working tree is not the tree being pushed" in result.stdout
    assert "harness_cli.py" in result.stdout, (
        "the block must name the files, not just refuse")
    assert _SELF_CHECK_RAN not in result.stdout, (
        "self_check must not run at all — its verdict would be about a tree "
        "nobody is pushing, which is the whole defect")


def test_an_untracked_test_file_blocks_the_push_too(tmp_path):
    """`git diff HEAD` would call this clean. A new tests/test_*.py changes
    what pytest collects, so self_check's verdict on the working tree is not
    a verdict on the commit — which is exactly the case being closed."""
    proj = _framework_repo(tmp_path)
    (proj / "tests" / "test_brand_new.py").write_text(
        "def test_x():\n    assert True\n", encoding="utf-8")

    result = _run_hook(proj)
    assert result.returncode != 0
    assert "test_brand_new.py" in result.stdout


def test_the_block_tells_the_operator_both_ways_out(tmp_path):
    """Round 48: a halt names its owner and its remedy. Refusing a push with
    no way forward is how a guard gets disabled instead of satisfied."""
    proj = _framework_repo(tmp_path)
    (proj / "harness_cli.py").write_text("# edited\n", encoding="utf-8")

    out = _run_hook(proj).stdout
    assert "git commit" in out and "git stash -u" in out, out[-600:]
