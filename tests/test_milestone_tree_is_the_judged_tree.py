"""A phase may only turn over on a tree that git has recorded.

Round 44 站0. Measured on taskq-advance, P3→P4, 2026-08-11 (local times):

    13:14:07  advance-phase BLOCKED — obligation:property_spec named FR-02 and
              FR-06: "declares a property invariant but no executing
              property-based test covers it"     (.methodology/degradations.jsonl)
    13:17:36  verify-gate records Gate 2 PASS    (.methodology/gate_verify.jsonl)
    13:17:55  81bbeb4 "handover: advance to Phase 4" — the advance SUCCEEDS,
              and state.json::phase_completed["3"].sha is set to it
    13:32     8075e1f — the `@given` tests enter git for the FIRST time

    $ git archive 81bbeb4 | grep -rl "@given"
    (nothing)

The commit that records Phase 3 as complete does not contain the evidence that
unblocked it. The HANDOVER.md that same commit generates opens with
`git clone --recurse-submodules …` — and the tree that clone produces fails
the check that had just been satisfied.

The framework wrote the contradiction down itself and never read it back:
`gate_verify.jsonl` holds three Gate 2 PASS rows against the same
`git_sha c4698c2` carrying three different `delivered_tree_sha256` values.

Root: `core/utils/delivery_scope.py::iter_delivered_files` takes its paths from
`git ls-files --cached --others --exclude-standard` but reads content from the
working tree. That is right for scanners — Phase 3 TDD writes the
implementation before committing it, and traceability must see it. It is not
an answer to the different question `delivered_tree_digest` uses it for:
*which version* of the project a PASS certifies. Two questions, one function,
and `git_sha` sitting in the same record, never compared.

`grep -rn "status --porcelain" cli/ core/ scripts/` finds the per-step
dirty-tree guard in `cli/fr_cmds.py` and nothing on the milestone path.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

import pytest

from cli import phase_cmds
from core.phase_hooks import PhaseHooks


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True,
    )


@pytest.fixture
def advance_project(tmp_path, monkeypatch):
    """Clean tmp git repo one advance away from Phase 2, prechecks stubbed.

    Same shape as tests/test_advance_refuses_a_blocked_entry.py's fixture —
    the prechecks and the exit gate are not what is under test here. Phase 1
    is deliberate: `EXIT_GATE_MAP` is `{3: 2, 4: 3, 6: 4}`, so a P1→P2 advance
    does not need a recorded gate verdict and this file can test one thing.
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
                           "reason": "test stub (milestone-tree fixture)"},
    )
    monkeypatch.setattr(
        PhaseHooks, "preview_next_phase_blocking", lambda _self, _n: [],
    )
    monkeypatch.delenv("HARNESS_NO_GIT", raising=False)
    return proj


def _state(proj: Path) -> dict:
    return json.loads((proj / ".methodology" / "state.json").read_text())


def _advance(proj: Path, completed: int = 1) -> int:
    return phase_cmds.cmd_advance_phase(
        argparse.Namespace(project=str(proj), completed_phase=completed)
    )


# ── the refusal ─────────────────────────────────────────────────────────────

def test_advance_refuses_a_modified_deliverable(advance_project, capsys):
    """taskq-advance's shape: the file that satisfied the check is not in git."""
    from cli.exit_codes import EX_ADVANCE_UNCOMMITTED_DELIVERABLES

    proj = advance_project
    (proj / "src" / "app.py").write_text("def f():\n    return 2\n")

    rc = _advance(proj)

    assert rc == EX_ADVANCE_UNCOMMITTED_DELIVERABLES, (
        f"advance-phase returned {rc} while src/app.py differed from HEAD — "
        f"the milestone it is about to record does not contain the tree the "
        f"checks were measured on"
    )
    assert _state(proj)["current_phase"] == 1
    out = capsys.readouterr().out
    assert "[BLOCKED]" in out
    assert "src/app.py" in out, "the block must name the files, not the rule"


def test_advance_refuses_an_untracked_deliverable(advance_project):
    """`git ls-files --others` puts it in the digest; `git archive` does not.

    This is the sharper half of the taskq-advance defect: a brand-new test
    file is *visible to every check* and *absent from every clone*.
    """
    from cli.exit_codes import EX_ADVANCE_UNCOMMITTED_DELIVERABLES

    proj = advance_project
    (proj / "src" / "test_new.py").write_text("def test_x():\n    assert 1\n")

    assert _advance(proj) == EX_ADVANCE_UNCOMMITTED_DELIVERABLES
    assert _state(proj)["current_phase"] == 1


def test_the_refusal_lands_in_the_ledger(advance_project):
    """Machine-readable, same shape as Round 43 站2's `obligation:<check_id>`."""
    proj = advance_project
    (proj / "src" / "app.py").write_text("changed\n")

    _advance(proj)

    ledger = proj / ".methodology" / "degradations.jsonl"
    assert ledger.is_file()
    rows = [json.loads(line) for line in
            ledger.read_text(encoding="utf-8").splitlines() if line.strip()]
    mine = [r for r in rows if r.get("component") == "milestone:uncommitted"]
    assert mine, [r.get("component") for r in rows]
    assert any("src/app.py" in json.dumps(r) for r in mine)


# ── what the advance itself owns is not "uncommitted work" ──────────────────

def test_the_advances_own_write_set_does_not_block_it(advance_project):
    """HANDOVER.md is regenerated and staged by this very command.

    Measured on taskq-api 2026-08-11: `git status` there shows `M HANDOVER.md`
    at rest. Blocking on a file the advance is about to rewrite and commit
    would make the check unsatisfiable.
    """
    proj = advance_project
    (proj / "HANDOVER.md").write_text("stale handover from a previous run\n")

    assert _advance(proj) == 0, (
        "advance-phase blocked on a file listed in _advance_commit_targets"
    )
    assert _state(proj)["current_phase"] == 2


def test_harness_bookkeeping_does_not_block_it(advance_project):
    """`.methodology/` is the harness's own workspace — it is dirty by design
    at this moment, because this command is what is about to write it."""
    proj = advance_project
    (proj / ".methodology" / "heartbeat.json").write_text('{"ts": 1}')

    assert _advance(proj) == 0
    assert _state(proj)["current_phase"] == 2


# ── the record ──────────────────────────────────────────────────────────────

def test_the_milestone_records_the_tree_it_certified(advance_project):
    """`phase_completed[N]` already carries `sha`, `enforcer_sha` and
    `enforcer_surface` (Round 19 站3 / Round 29 站4). It never carried the
    one fact this round is about: which tree the phase was judged on.
    """
    from core.utils.delivery_scope import committed_tree_digest

    proj = advance_project
    assert _advance(proj) == 0

    entry = _state(proj)["phase_completed"]["1"]
    assert "delivered_tree_sha256" in entry, (
        "the milestone records who enforced and at which commit, but not "
        "which tree — so nothing can check afterwards that they agree"
    )
    assert entry["delivered_tree_sha256"] == committed_tree_digest(
        proj, entry["sha"]
    ), "the recorded tree is not the tree of the recorded commit"
