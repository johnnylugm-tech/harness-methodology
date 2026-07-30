"""pre-push hook phase detection at the P(N)->P(N+1) boundary.

advance-phase writes a fixed "handover: advance to Phase N" commit and
flips state.json::current_phase to N in the same call, but deliberately
does NOT push (crash-safety — cmd_advance_phase). The push that follows,
publishing that commit, used to be checked with the ALREADY-FLIPPED
current_phase (N) instead of the phase the commit is closing (N-1). Since
several preflights (property_spec's execution requirement among them)
only start blocking once phase >= their fulfill_phase, this fired the
Phase-N-scoped gate one phase early against a commit that has no way to
satisfy it — the actual taskq-plus P3->P4 incident: two guaranteed-to-fail
SYNC pushes, `git log` confirmed HEAD was `handover: advance to Phase 4`
while the check should have run under Phase 3's (looser) rules.

The fix (scripts/hooks/pre-push): if HEAD is exactly that fixed commit
message, check against N-1, not state.json's current_phase.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

pytestmark = [pytest.mark.core]

REPO_ROOT = Path(__file__).resolve().parent.parent
HOOK_SCRIPT = REPO_ROOT / "scripts" / "hooks" / "pre-push"

_STUB_HARNESS_CLI = (
    "import sys\n"
    "phase = sys.argv[sys.argv.index('--phase') + 1]\n"
    "print(f'PHASE_USED={phase}')\n"
    "sys.exit(0)\n"
)


def _make_project(tmp_path: Path, *, current_phase: int, head_commit_message: str) -> Path:
    proj = tmp_path / "proj"
    proj.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=proj, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=proj, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=proj, check=True)

    (proj / ".methodology").mkdir()
    (proj / ".methodology" / "state.json").write_text(
        json.dumps({"current_phase": current_phase}), encoding="utf-8")
    (proj / "harness_cli.py").write_text(_STUB_HARNESS_CLI, encoding="utf-8")

    subprocess.run(["git", "add", "-A"], cwd=proj, check=True)
    subprocess.run(["git", "commit", "-q", "-m", head_commit_message], cwd=proj, check=True)
    return proj


def _run_hook(proj: Path) -> subprocess.CompletedProcess:
    head_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=proj, check=True,
        capture_output=True, text=True,
    ).stdout.strip()
    # Simulate git's own pre-push stdin: "<local ref> <local sha> <remote ref> <remote sha>".
    # Zero remote sha = new-branch push, so _RANGE covers just this one commit.
    stdin = f"refs/heads/main {head_sha} refs/heads/main {'0' * 40}\n"
    return subprocess.run(
        ["bash", str(HOOK_SCRIPT)], cwd=proj, input=stdin,
        capture_output=True, text=True,
    )


def test_handover_commit_is_checked_against_the_phase_it_closes(tmp_path):
    proj = _make_project(
        tmp_path, current_phase=4, head_commit_message="handover: advance to Phase 4")
    result = _run_hook(proj)
    assert "PHASE_USED=3" in result.stdout, (
        f"expected the Phase-3 handover push to be checked under Phase 3's "
        f"rules, not the already-flipped current_phase=4 "
        f"(stdout={result.stdout!r} stderr={result.stderr!r})"
    )


def test_a_normal_commit_still_uses_current_phase(tmp_path):
    proj = _make_project(
        tmp_path, current_phase=4, head_commit_message="feat: implement FR-09")
    result = _run_hook(proj)
    assert "PHASE_USED=4" in result.stdout, (
        f"non-handover pushes must keep checking against state.json's "
        f"current_phase unchanged (stdout={result.stdout!r} stderr={result.stderr!r})"
    )
