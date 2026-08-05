"""advance-phase ghost-state fix: the handover commit failing must roll back
the write-set (REGRESSION_GUARDS-pinned).

Live bug (2026-07-10 弱點強化 B1): cmd_advance_phase writes state.json (via
_advance_fsm), CLAUDE.md and HANDOVER.md, then runs `git add` + `git commit`.
When the commit was rejected (e.g. a prepare-commit-msg / pre-commit hook —
exactly what the 25cb002 P3 health-check round hit), the code only printed
`WARN: git commit failed`, kept the advanced state.json on disk, printed
"Done — local hooks and CI now target phase N" and exited 0. Local hooks and
CI then acted on a phase git history never recorded — the split-brain ghost
state the doctor git-sync check (B2) detects after the fact.

The fix snapshots the write-set before _advance_fsm and restores it when
`git add`/`git commit` fails (exit 6 — same commit-failed convention as
run-fr-step / finalize-gate).
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

import pytest

from cli import phase_cmds


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True,
    )


@pytest.fixture
def advance_project(tmp_path, monkeypatch):
    """Clean tmp git repo one advance away from Phase 2, prechecks stubbed."""
    proj = tmp_path / "proj"
    proj.mkdir()
    _git(proj, "init")
    _git(proj, "config", "user.email", "t@example.com")
    _git(proj, "config", "user.name", "t")
    # A global core.hooksPath would redirect hook injection — pin it local.
    _git(proj, "config", "core.hooksPath", ".git/hooks")

    meth = proj / ".methodology"
    meth.mkdir()
    (meth / "state.json").write_text(
        json.dumps({"state": "RUNNING", "current_phase": 1}) + "\n"
    )
    (proj / "CLAUDE.md").write_text("# Project\n")
    _git(proj, "add", "-A")
    assert _git(proj, "commit", "-m", "baseline").returncode == 0

    # completed_phase=1 keeps the path minimal (no exit gate, no CRG wiki,
    # no P2 manifest regen, no P8 doc gen). Prechecks are not under test.
    monkeypatch.setattr(phase_cmds, "_advance_prechecks", lambda *_a, **_k: 0)
    # Round 39: cmd_advance_phase now calls _verify_entry_gate before
    # _advance_fsm. Stub it the same way so these tests stay scoped to
    # commit-rollback behaviour, not the gate logic. Coverage for the
    # gate wiring lives in tests/test_phase_completed_authority.py.
    monkeypatch.setattr(
        phase_cmds, "_verify_entry_gate",
        lambda *_a, **_k: {"passed": True, "gate": "stub",
                           "reason": "test stub (rollback fixture)"},
    )
    monkeypatch.delenv("HARNESS_NO_GIT", raising=False)
    return proj


def _advance(proj: Path) -> int:
    args = argparse.Namespace(project=str(proj), completed_phase=1)
    return phase_cmds.cmd_advance_phase(args)


def _install_rejecting_hook(proj: Path) -> None:
    hook = proj / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\necho 'hook says no' >&2\nexit 1\n")
    hook.chmod(0o755)


class TestAdvanceCommitRollback:
    def test_commit_failure_rolls_back_and_exits_6(self, advance_project):
        proj = advance_project
        claude_md_before = (proj / "CLAUDE.md").read_bytes()
        _install_rejecting_hook(proj)

        rc = _advance(proj)

        assert rc == 6, (
            "a rejected handover commit must exit 6 (commit-failed), not "
            f"succeed — got {rc}"
        )
        state = json.loads((proj / ".methodology" / "state.json").read_text())
        assert state["current_phase"] == 1, (
            "state.json advanced to a phase git never recorded — ghost state"
        )
        assert not (proj / "HANDOVER.md").exists(), (
            "HANDOVER.md for the phantom phase left on disk"
        )
        assert (proj / "CLAUDE.md").read_bytes() == claude_md_before
        # The whole write-set must be back: working tree clean = no half-state.
        # The .state.lock file is process infrastructure (created by file_lock
        # on every invocation, even blocked ones), not phase state — ignore it.
        porcelain = [
            line for line in
            _git(proj, "status", "--porcelain").stdout.splitlines()
            if line.strip() and not line.endswith(".state.lock")
        ]
        assert porcelain == [], "residue after rollback:\n" + "\n".join(porcelain)

    def test_rerun_after_fixing_hook_succeeds(self, advance_project):
        """fail → fix → to-pass: the rollback leaves the project re-runnable."""
        proj = advance_project
        _install_rejecting_hook(proj)
        assert _advance(proj) == 6

        (proj / ".git" / "hooks" / "pre-commit").unlink()
        assert _advance(proj) == 0
        state = json.loads((proj / ".methodology" / "state.json").read_text())
        assert state["current_phase"] == 2

    def test_healthy_advance_commits_state(self, advance_project):
        proj = advance_project

        rc = _advance(proj)

        assert rc == 0
        state = json.loads((proj / ".methodology" / "state.json").read_text())
        assert state["current_phase"] == 2
        subject = _git(proj, "log", "-1", "--format=%s").stdout.strip()
        assert subject == "handover: advance to Phase 2"
        committed = _git(
            proj, "show", "HEAD:.methodology/state.json"
        ).stdout
        assert json.loads(committed)["current_phase"] == 2, (
            "the advance commit must carry the advanced state.json"
        )


class TestRollbackLockAndResetDiagnostic:
    """Round 2 Station F: restore()+git-reset run inside state_lock (closes
    the window where a concurrent lock-holder's legitimate write could
    interleave with the rollback); a failed `git reset` after rollback now
    prints a diagnostic instead of being silently swallowed."""

    def test_restore_runs_under_lock(self, advance_project, monkeypatch):
        """Verified without real threading: from inside FileSnapshot.restore
        (called only while cmd_advance_phase's own `with file_lock(...)` is
        held), a second NON-BLOCKING acquisition of the same lock file must
        fail — fcntl.flock is exclusive even within one process across
        distinct os.open() fds, so this proves the outer lock is actually
        held at the moment restore() runs, not just present somewhere in the
        function."""
        proj = advance_project
        _install_rejecting_hook(proj)

        from core.atomic_io import FileSnapshot, file_lock, state_lock_path

        original_restore = FileSnapshot.restore
        second_acquire_results = []

        def spying_restore(self):
            try:
                with file_lock(state_lock_path(proj), blocking=False):
                    second_acquire_results.append(True)  # would mean NOT locked
            except (OSError, BlockingIOError):
                second_acquire_results.append(False)  # expected: already held
            return original_restore(self)

        monkeypatch.setattr(FileSnapshot, "restore", spying_restore)

        rc = _advance(proj)

        assert rc == 6
        assert second_acquire_results == [False], (
            "a second non-blocking lock acquisition succeeded while "
            "restore() ran — the rollback is not actually holding state_lock"
        )

    def test_restore_and_reset_are_source_order_inside_the_lock(self):
        """Cross-check via source inspection: restore() and the post-restore
        `git reset` call must both appear inside the `with file_lock(...)`
        block, not after it (a regression could move reset back outside the
        lock while still passing the behavioral test above by coincidence).

        cmd_advance_phase holds state_lock in more than one place (the CV-2
        state.json read guard near the top, this rollback, and — since Round 24
        站4a — the phase_completed write after a successful commit). Anchor on
        the lock that actually ENCLOSES restore(), i.e. the last one opened
        before it, rather than on "the last lock in the function": that
        positional assumption broke the moment a third lock was added below.
        """
        import inspect

        src = inspect.getsource(phase_cmds.cmd_advance_phase)
        restore_pos_probe = src.rindex("_advance_snap.restore()")
        lock_pos = src.rindex(
            "with file_lock(state_lock_path(project)):", 0, restore_pos_probe
        )
        restore_pos = src.rindex("_advance_snap.restore()")
        reset_pos = src.rindex('"reset", "-q"')
        assert lock_pos < restore_pos < reset_pos, (
            "restore() and the git reset call must both be nested inside "
            "the rollback's file_lock block, in that order"
        )

    def test_reset_failure_prints_diagnostic_but_state_stays_correct(
        self, advance_project, monkeypatch, capsys
    ):
        """A `git reset` failure after a rolled-back commit must not be
        silently swallowed — worktree/state.json are already correctly
        restored by FileSnapshot regardless, so the exit code and rollback
        outcome are unaffected; only a diagnostic is added."""
        proj = advance_project
        _install_rejecting_hook(proj)

        real_run = subprocess.run

        def spying_run(cmd, *args, **kwargs):
            if isinstance(cmd, list) and "reset" in cmd and "-q" in cmd:
                return subprocess.CompletedProcess(
                    cmd, returncode=1, stdout="",
                    stderr="fatal: forced test failure\n",
                )
            return real_run(cmd, *args, **kwargs)

        monkeypatch.setattr(phase_cmds.subprocess, "run", spying_run)

        rc = _advance(proj)

        assert rc == 6
        captured = capsys.readouterr()
        assert "git reset after rollback failed" in captured.err
        state = json.loads((proj / ".methodology" / "state.json").read_text())
        assert state["current_phase"] == 1, (
            "a failed git reset must not affect the already-restored state.json"
        )
