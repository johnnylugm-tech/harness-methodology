"""advance-phase reports success without checking that its record landed.

Round 89. taskq-super reached Phase 9 with `phase_completed` entries for
1, 2, 3, 4, 6, 7 — no 5 — and every check downstream stayed quiet. The
`handover: advance to Phase 6` commit exists and `last_milestone_command` in
that same commit reads `advance-phase --completed-phase 5`, so the command
ran, the FSM advanced, and the record never appeared. `"5": {` is absent from
that project's ENTIRE git history, and the next commit to touch state.json —
5h23m later — carries no `phase_completed` change either, so the entry was
never written to the working tree at all.

WHAT THIS DOES NOT CLAIM

Not a root cause. Round 89 measured four candidate explanations and ruled
each out: `postflight_update_state` cannot be a lost-update writer (its write
sits behind `self.phase > old_phase`, false on every post-advance run, and
`run-phase`/`advance-phase` are sequential dispatches in all eight
workflows); the other state writers keep `load` inside their own `file_lock`;
the commit-failure path rolls the FSM back; and both write-failure branches
call `record_degradation`, of which that project's 626-entry ledger holds
none. `cli/phase_cmds.py:1697`'s own diagnosis — "a later whole-document
writer that had loaded state before it dropped the key" — describes a writer
this repository does not appear to contain.

So the fix is on the outcome, which is the same sentence whoever the culprit
is:

    advance-phase declared phase N complete, the record did not land, and
    nothing noticed.

WHERE IT HAS TO SIT

`_advance_step_commit_and_push`'s terminal `return 0` is at indent 4 — OUTSIDE
the `if os.environ.get("HARNESS_NO_GIT") / else` that both write branches live
in. A check placed inside that `else` is skipped by exactly the condition that
is the only remaining path consistent with what taskq-super shows, so the
placement is the load-bearing part and `test_the_check_is_not_inside_the_no_git_branch`
pins it.

The R72 站1 check is the other half and asks a different question: it runs
when the NEXT phase starts (`_verify_entry_gate`), and from P4 on it does not
read `phase_completed` at all — the entry gate consults it only for phases 2
and 3, which is precisely why taskq-super's missing P5 did not stop its P6.
One asks "did the previous leg hand over"; this one asks "did I put my own leg
down".
"""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

import pytest

pytestmark = [pytest.mark.core]


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True, check=False)


@pytest.fixture
def advance_project(tmp_path, monkeypatch):
    """A project one advance-phase away from Phase 2.

    Same shape as `tests/test_phase_completed_authority.py`'s fixture — the
    prechecks and the doctor run are stubbed so the test is about the write,
    not about the gates in front of it.
    """
    from cli import phase_cmds

    proj = tmp_path / "proj"
    proj.mkdir()
    _git(proj, "init")
    _git(proj, "config", "user.email", "t@example.com")
    _git(proj, "config", "user.name", "t")
    _git(proj, "config", "core.hooksPath", ".git/hooks")
    meth = proj / ".methodology"
    meth.mkdir()
    (meth / "state.json").write_text(
        json.dumps({"state": "RUNNING", "current_phase": 1}) + "\n", encoding="utf-8")
    (proj / "CLAUDE.md").write_text("# Project\n", encoding="utf-8")
    _git(proj, "add", "-A")
    assert _git(proj, "commit", "-m", "baseline").returncode == 0

    monkeypatch.setattr(phase_cmds, "_advance_prechecks", lambda *_a, **_k: 0)
    monkeypatch.setattr(
        phase_cmds, "_verify_entry_gate",
        lambda *_a, **_k: {"passed": True, "gate": "stub", "reason": "test stub"})
    # This tree holds no P1 deliverables, so `previous_phase_artifacts`
    # correctly reports four missing SPECIFY artifacts and advance-phase
    # correctly refuses. Stubbed for the same reason as the two above — these
    # tests are about what happens to the record, not about obligations.
    monkeypatch.setattr(
        "core.phase_hooks.PhaseHooks.preview_next_phase_blocking",
        lambda _self, _next_phase: [])
    monkeypatch.delenv("HARNESS_NO_GIT", raising=False)
    return proj


def _advance(proj: Path, completed: int = 1) -> int:
    from cli import phase_cmds

    return phase_cmds.cmd_advance_phase(argparse.Namespace(
        completed_phase=completed, project=str(proj), push=False,
    ))


def _swallow_the_record(monkeypatch) -> None:
    """Let every write through EXCEPT the one that records the phase.

    Reproduces the observable state taskq-super is in — FSM advanced, handover
    commit made, `phase_completed[N]` absent — without asserting which real
    mechanism produced it. Targeting the write by its content rather than by
    stubbing the whole function keeps the rest of advance-phase real.
    """
    from cli import advance_steps

    real = advance_steps.atomic_write_json

    def selective(path, payload, *a, **k):
        if isinstance(payload, dict) and "phase_completed" in payload:
            payload = {k2: v for k2, v in payload.items() if k2 != "phase_completed"}
        return real(path, payload, *a, **k)

    monkeypatch.setattr(advance_steps, "atomic_write_json", selective)


# ── the defect ───────────────────────────────────────────────────────────


def test_a_record_that_did_not_land_blocks_the_advance(advance_project, monkeypatch, capsys):
    """The reproduction. Before Round 89 this returned 0."""
    _swallow_the_record(monkeypatch)
    rc = _advance(advance_project)
    _cap = capsys.readouterr()
    out = _cap.out + _cap.err
    assert rc != 0, (
        "advance-phase reported success while its own record was absent — "
        "taskq-super reached Phase 9 that way"
    )
    state = json.loads(
        (advance_project / ".methodology" / "state.json").read_text(encoding="utf-8"))
    assert "1" not in (state.get("phase_completed") or {}), "fixture no longer reproduces"
    assert "phase_completed[1]" in out, out


def test_the_block_names_what_to_re_run(advance_project, monkeypatch, capsys):
    """Round 48: a block that names no owner and no command is a dead end."""
    _swallow_the_record(monkeypatch)
    _advance(advance_project)
    _cap = capsys.readouterr()
    out = _cap.out + _cap.err
    assert "advance-phase" in out and "--completed" in out, out


def test_the_loss_reaches_the_ledger(advance_project, monkeypatch):
    """`owner="harness"` — a record the framework failed to write is its own bug."""
    _swallow_the_record(monkeypatch)
    _advance(advance_project)
    ledger = advance_project / ".methodology" / "degradations.jsonl"
    assert ledger.is_file(), "the loss left no trace"
    rows = [json.loads(ln) for ln in ledger.read_text(encoding="utf-8").splitlines() if ln.strip()]
    hits = [r for r in rows if "phase_completed" in str(r.get("what", ""))]
    assert hits, rows
    assert hits[0].get("owner") == "harness", hits[0]


# ── it must not fire on the healthy path ─────────────────────────────────


def test_a_healthy_advance_still_returns_zero(advance_project):
    assert _advance(advance_project) == 0
    state = json.loads(
        (advance_project / ".methodology" / "state.json").read_text(encoding="utf-8"))
    assert state["phase_completed"]["1"]["sha"], state


def test_no_git_records_the_gap_without_blocking(advance_project, monkeypatch, capsys):
    """`HARNESS_NO_GIT=1` is a documented switch, not a framework failure.

    SAD.md: "disables git across all commands without a flag". On that path
    there is no commit, so there is no sha for the record to carry and its
    absence is the switch working. It still reaches the ledger — an
    abstention nobody can see is indistinguishable from a pass (Round 27).
    """
    monkeypatch.setenv("HARNESS_NO_GIT", "1")
    rc = _advance(advance_project)
    assert rc == 0, capsys.readouterr().out
    ledger = advance_project / ".methodology" / "degradations.jsonl"
    if ledger.is_file():
        rows = [json.loads(ln) for ln in ledger.read_text(encoding="utf-8").splitlines() if ln.strip()]
        assert any("phase_completed" in str(r.get("what", "")) for r in rows), rows
    else:
        pytest.fail("HARNESS_NO_GIT skipped the record and left no trace")


# ── the placement is the load-bearing part ───────────────────────────────


def test_the_check_is_not_inside_the_no_git_branch() -> None:
    """Indent 4, not 8.

    Both write branches live inside `else:` of
    `if os.environ.get("HARNESS_NO_GIT")`. A verification placed there is
    skipped by the same condition that is the only remaining explanation
    consistent with what taskq-super shows — the check would be absent
    exactly when it is needed. This pins the level rather than the text, so a
    reformat cannot quietly move it in.
    """
    import ast

    src = (Path(__file__).resolve().parents[1] / "cli" / "advance_steps.py")
    text = src.read_text(encoding="utf-8")
    tree = ast.parse(text)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "_advance_step_commit_and_push")

    def calls_read_back(node) -> bool:
        return any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                   and n.func.id == "phase_record_defects" for n in ast.walk(node))

    assert calls_read_back(fn), "advance-phase no longer reads back the record it wrote"

    # Ancestry, not indentation: the first version of this guard measured the
    # column of the line the call sits on, and a multi-line expression puts its
    # continuation deeper than the statement — it reported indent 16 for a
    # statement at indent 4. What the check is actually about is whether the
    # read-back is reachable when HARNESS_NO_GIT is set, and that is an AST
    # question.
    no_git_branches = [
        n for n in ast.walk(fn)
        if isinstance(n, ast.If)
        and "HARNESS_NO_GIT" in (ast.dump(n.test) if n.test else "")
    ]
    assert no_git_branches, (
        "the HARNESS_NO_GIT branch is gone — re-derive where the read-back "
        "has to sit before deleting this guard")
    for branch in no_git_branches:
        inside = any(calls_read_back(stmt) for stmt in [*branch.body, *branch.orelse])
        assert not inside, (
            "the read-back sits inside the HARNESS_NO_GIT if/else — it would "
            "be skipped by exactly the condition it exists to survive"
        )


def test_the_read_back_reuses_the_shared_record_contract() -> None:
    """One definition of "what a record looks like".

    `phase_record_defects` (Round 72 站1) already knows: a 40-character sha, a
    64-character sha256. A second copy here would be a second answer, which is
    the defect this repository keeps finding.
    """
    from core.harness_provenance import phase_record_defects

    assert phase_record_defects(Path("/nonexistent"), None)
    assert phase_record_defects(Path("/nonexistent"), {"sha": "a1" * 20}) == []
