"""The diagnostic gets a reader that is not a human remembering to look.

Round 45 站0. `grep -rn "run_doctor"` over the whole repository finds exactly
one call site: `cli/project_cmds.py:929`, which IS the `doctor` CLI command.
`advance-phase` does not call it. `preflight` does not call it. None of the
nine generated workflow JS files mention it.

So three mechanisms built to be read at a phase boundary have never been read
at one:

  * Round 43 站4  `_check_enforcer_provenance`      — the recorded PASS was
                                                      measured under a
                                                      different enforcer
  * Round 44 站4  `_check_milestone_tree_matches_verdict`
  * Round 45 站3  the per-FR result reconciliation

This is Round 43's mother pattern — detected, no executor — raised to the
level of a whole command.

The wiring is deliberately weak: doctor runs AFTER the phase has turned over,
its ERRORs land in the degradation ledger, and the advance's exit code does
not change. `run_doctor` measured 1.15s on a copy of taskq-advance, so there
is no subset registry and no budget: it runs whole.

Why not block: doctor is the framework's own read of state it also wrote, and
station 2 of this round exists because that read produced thirty false
accusations against a healthy project. A check whose false-positive rate was
100% four hours ago does not get the power to stop a pipeline. It gets a
reader. Same standing as Round 43 站4's `phase_verdict_staleness`: a
diagnosis, not a waiver.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

import pytest

# Round 82 站3: `_run_doctor_after_advance` moved to cli/advance_steps.py, so
# that is where `run_doctor` is resolved. Patching phase_cmds' own copy
# would still succeed and would no longer reach the caller — a green test
# measuring nothing, which is worse than the AttributeError that caught it.
from cli import advance_steps, phase_cmds
from core.doctor import Finding
from core.phase_hooks import PhaseHooks

pytestmark = [pytest.mark.core]


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True,
    )


@pytest.fixture
def advance_project(tmp_path, monkeypatch):
    """Same fixture shape as tests/test_milestone_tree_is_the_judged_tree.py.

    Phase 1 on purpose: `EXIT_GATE_MAP` is `{3: 2, 4: 3, 6: 4}`, so a P1→P2
    advance needs no recorded gate verdict and this file tests one thing.
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
    src = proj / "src"
    src.mkdir()
    (src / "app.py").write_text("def f():\n    return 1\n")
    _git(proj, "add", "-A")
    assert _git(proj, "commit", "-m", "baseline").returncode == 0

    monkeypatch.setattr(phase_cmds, "_advance_prechecks", lambda *_a, **_k: 0)
    monkeypatch.setattr(
        phase_cmds, "_verify_entry_gate",
        lambda *_a, **_k: {"passed": True, "gate": "stub",
                           "reason": "test stub (doctor-wiring fixture)"},
    )
    monkeypatch.setattr(
        PhaseHooks, "preview_next_phase_blocking", lambda _self, _n: [],
    )
    monkeypatch.delenv("HARNESS_NO_GIT", raising=False)
    return proj


def _advance(proj: Path) -> int:
    return phase_cmds.cmd_advance_phase(
        argparse.Namespace(project=str(proj), completed_phase=1)
    )


def _ledger(proj: Path) -> list[dict]:
    from core.degradation_ledger import LEDGER_RELPATH
    path = proj / LEDGER_RELPATH
    if not path.is_file():
        return []
    return [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines()
            if ln.strip()]


def test_an_error_finding_reaches_the_ledger(advance_project, monkeypatch):
    monkeypatch.setattr(
        advance_steps, "run_doctor",
        lambda _p: [Finding("gate1-evidence", "ERROR", "FR-03 evidence is gone")],
    )

    rc = _advance(advance_project)

    assert rc == 0, rc
    rows = [r for r in _ledger(advance_project)
            if str(r.get("component", "")).startswith("doctor:")]
    assert rows, "doctor found an ERROR and the run left no trace of it"
    assert rows[0]["component"] == "doctor:gate1-evidence"
    assert "FR-03 evidence is gone" in rows[0]["what"]


def test_the_advance_still_turns_the_phase_over(advance_project, monkeypatch):
    """Not a new blocking point. The phase advanced; the finding is a record."""
    monkeypatch.setattr(
        advance_steps, "run_doctor",
        lambda _p: [Finding("provenance", "ERROR", "enforcer moved")],
    )

    assert _advance(advance_project) == 0
    state = json.loads(
        (advance_project / ".methodology" / "state.json").read_text())
    assert state["current_phase"] == 2


def test_warnings_are_not_ledger_rows(advance_project, monkeypatch):
    """taskq-advance's doctor output today is six provenance WARNs and one
    submodule WARN. Writing those to the ledger every advance would bury the
    ERRORs this wiring exists to surface."""
    monkeypatch.setattr(
        advance_steps, "run_doctor",
        lambda _p: [Finding("provenance", "WARN", "measured under another enforcer"),
                    Finding("submodule", "WARN", "harness/ is 1 behind")],
    )

    _advance(advance_project)

    assert not [r for r in _ledger(advance_project)
                if str(r.get("component", "")).startswith("doctor:")]


def test_a_doctor_crash_does_not_take_the_advance_with_it(
    advance_project, monkeypatch, capsys,
):
    """The phase has already turned over by this point. A diagnostic that
    raises must not undo a milestone that is already correct — but it must say
    so out loud rather than being swallowed (docs/ERROR_HANDLING.md)."""
    def _boom(_p):
        raise RuntimeError("doctor exploded")
    monkeypatch.setattr(advance_steps, "run_doctor", _boom)

    assert _advance(advance_project) == 0
    assert "doctor" in capsys.readouterr().out.lower()
