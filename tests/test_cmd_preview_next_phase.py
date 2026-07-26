"""Round 15 §2: `preview-next-phase` — read-only P(N+1) obligation preview.

cmd_advance_phase's own preview_next_phase_blocking call only runs AFTER
_advance_prechecks passes (phase_cmds.py:367-390), so an operator whose P(N)
exit gate is still failing has no way to check whether P(N+1) would also
block. This command is a non-destructive query: it never writes state.json,
never writes HANDOVER.md, and never creates a commit.
"""

from __future__ import annotations

import io
from contextlib import redirect_stdout, redirect_stderr

from cli.phase_cmds import cmd_preview_next_phase
from core.phase_hooks import Obligation, PhaseHooks


class _Args:
    def __init__(self, phase: int, project: str):
        self.phase = phase
        self.project = project


def test_clean_project_returns_zero(tmp_path, monkeypatch):
    monkeypatch.setattr(
        PhaseHooks, "preview_next_phase_blocking", lambda self, next_phase: [],
    )
    out = io.StringIO()
    with redirect_stdout(out):
        rc = cmd_preview_next_phase(_Args(phase=3, project=str(tmp_path)))  # type: ignore[reportArgumentType]
    assert rc == 0
    assert "clean" in out.getvalue().lower()


def test_obligations_found_returns_one(tmp_path, monkeypatch):
    monkeypatch.setattr(
        PhaseHooks, "preview_next_phase_blocking",
        lambda self, next_phase: [
            Obligation(
                check_id="reliability_lint", target_phase=next_phase,
                rule_id="py-mkstemp-outside-try", file="src/store.py", line=86,
                message="WARNING py-mkstemp-outside-try src/store.py:86",
            ),
        ],
    )
    out = io.StringIO()
    with redirect_stdout(out):
        rc = cmd_preview_next_phase(_Args(phase=3, project=str(tmp_path)))  # type: ignore[reportArgumentType]
    assert rc == 1
    text = out.getvalue()
    assert "reliability_lint" in text
    assert "py-mkstemp-outside-try" in text
    assert "src/store.py:86" in text


def test_no_state_writes_or_commits(tmp_path, monkeypatch):
    """Non-destructive contract: no .methodology/state.json, no HANDOVER.md,
    no git call — this command only reads."""
    monkeypatch.setattr(
        PhaseHooks, "preview_next_phase_blocking", lambda self, next_phase: [],
    )
    cmd_preview_next_phase(_Args(phase=3, project=str(tmp_path)))  # type: ignore[reportArgumentType]
    assert not (tmp_path / ".methodology" / "state.json").exists()
    assert not (tmp_path / "HANDOVER.md").exists()


def test_invalid_next_phase_returns_two(tmp_path, monkeypatch):
    def _raise(self, next_phase):
        raise ValueError(f"next_phase {next_phase} is out of range")

    monkeypatch.setattr(PhaseHooks, "preview_next_phase_blocking", _raise)
    err = io.StringIO()
    with redirect_stderr(err):
        rc = cmd_preview_next_phase(_Args(phase=99, project=str(tmp_path)))  # type: ignore[reportArgumentType]
    assert rc == 2
    assert "out of range" in err.getvalue()
