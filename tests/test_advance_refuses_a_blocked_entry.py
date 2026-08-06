"""advance-phase must not walk into a phase whose entry it just measured as blocked.

Round 43 站0. `cmd_advance_phase` runs `preview_next_phase_blocking(next_phase)`
before `_advance_fsm`, gets back a list whose own rendered warning says

    ⚠️ The following preflight findings would BLOCK entry to Phase N.

prints it, writes it into HANDOVER.md, and then advances anyway. Measured on
taskq-api: the P3→P4 handover commit carried six obligations naming five
`# pragma: no cover` sites by file and line; three commits later the push was
rejected by the pre-push hook on exactly those five lines, and a human fixed
them by hand.

Nothing reads the table. `grep -r "Entry Obligations"` over the whole
repository returns one producer (`harness/handover_generator.py`) and four
test assertions — no workflow JS, no prompt, no phase spec.

Advancing produces a state with no truth value: `state.json::current_phase`
says N+1 while N+1's entry preflight fails. `scripts/hooks/pre-push` has to
guess which phase to check a commit against by pattern-matching HEAD's subject
line against `^handover: advance to Phase N$` precisely because that state can
exist. Refusing to create it is the fix; the guess becomes removable as a
consequence, not as part of this change.

Round 30's rule is that abstaining is not passing. This is one step short of
abstaining: the framework did the work, got the right answer, and filed it.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

import pytest

from cli import phase_cmds
from core.phase_hooks import Obligation, PhaseHooks


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True,
    )


@pytest.fixture
def advance_project(tmp_path, monkeypatch):
    """Clean tmp git repo one advance away from Phase 2, prechecks stubbed.

    Same shape as tests/test_advance_commit_rollback.py's fixture — the
    prechecks and the exit gate are not what is under test here.
    """
    proj = tmp_path / "proj"
    proj.mkdir()
    _git(proj, "init")
    _git(proj, "config", "user.email", "t@example.com")
    _git(proj, "config", "user.name", "t")
    _git(proj, "config", "core.hooksPath", ".git/hooks")

    meth = proj / ".methodology"
    meth.mkdir()
    (meth / "state.json").write_text(
        json.dumps({"state": "RUNNING", "current_phase": 1}) + "\n"
    )
    (proj / "CLAUDE.md").write_text("# Project\n")
    _git(proj, "add", "-A")
    assert _git(proj, "commit", "-m", "baseline").returncode == 0

    monkeypatch.setattr(phase_cmds, "_advance_prechecks", lambda *_a, **_k: 0)
    monkeypatch.setattr(
        phase_cmds, "_verify_entry_gate",
        lambda *_a, **_k: {"passed": True, "gate": "stub",
                           "reason": "test stub (obligation fixture)"},
    )
    monkeypatch.delenv("HARNESS_NO_GIT", raising=False)
    return proj


def _with_obligations(monkeypatch, obligations):
    monkeypatch.setattr(
        PhaseHooks, "preview_next_phase_blocking",
        lambda _self, next_phase: obligations,
    )


_PRAGMA = Obligation(
    check_id="reliability_lint", target_phase=2,
    rule_id="py-pragma-no-cover", file="src/app.py", line=67,
    message="WARNING py-pragma-no-cover src/app.py:67 — resolve before "
            "entering the target phase",
)


def _current_phase(proj: Path) -> int:
    return json.loads(
        (proj / ".methodology" / "state.json").read_text()
    )["current_phase"]


def test_advance_refuses_while_the_next_entry_is_blocked(
    advance_project, monkeypatch, capsys,
):
    from cli.exit_codes import EX_ADVANCE_ENTRY_OBLIGATIONS

    proj = advance_project
    _with_obligations(monkeypatch, [_PRAGMA])

    rc = phase_cmds.cmd_advance_phase(
        argparse.Namespace(project=str(proj), completed_phase=1)
    )

    assert rc == EX_ADVANCE_ENTRY_OBLIGATIONS, (
        f"advance-phase returned {rc} while holding a blocking obligation "
        f"for the phase it was entering"
    )
    assert _current_phase(proj) == 1, (
        "state.json advanced into a phase whose entry preflight fails — the "
        "pre-push hook then has to guess which phase to judge the commit at"
    )


def test_the_block_names_the_finding(advance_project, monkeypatch, capsys):
    """R24 站1: a [BLOCKED] must carry the remediation, not a pointer to it."""
    proj = advance_project
    _with_obligations(monkeypatch, [_PRAGMA])

    phase_cmds.cmd_advance_phase(
        argparse.Namespace(project=str(proj), completed_phase=1)
    )
    out = capsys.readouterr().out

    assert "[BLOCKED]" in out
    assert "reliability_lint" in out
    assert "src/app.py:67" in out


def test_a_clean_preview_still_advances(advance_project, monkeypatch):
    """Negative control: no obligations, no new block."""
    proj = advance_project
    _with_obligations(monkeypatch, [])

    rc = phase_cmds.cmd_advance_phase(
        argparse.Namespace(project=str(proj), completed_phase=1)
    )

    assert rc == 0, "an obligation-free advance must still succeed"
    assert _current_phase(proj) == 2
