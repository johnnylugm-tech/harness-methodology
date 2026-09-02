"""A tree that was already accepted cannot be re-judged silently.

Round 88 站2/站3. Every gate verdict has recorded WHO produced it since Round
19 站3 (`harness_provenance.enforcer_surface`), and every project records the
commit its phase exit was taken on. Nothing put the two together, so the
framework could — and did — change its mind about work it had already passed,
with no trace anywhere.

MEASURED, on the nine corpus projects that carry a `phase_completed["3"].sha`:
seven are judged differently today than when they were accepted. taskq-super
and taskq-new each move by about thirty-six declarations; taskq-cc-new moves
twenty-five the OTHER way, a loosening nobody reviewed. taskq-redo — the one
whose delivered quality Round 87 found had degraded — is the only tree that
cannot move at all, because 130 correctly-named stubs satisfy every parser.

WHY THE GATE IS NOT NOISE

Measured across Round 87's own nine commits, each replayed from its own git
worktree: **one** moved a real tree's verdict (站4, which added a check that
reports on eight projects — exactly the kind of change that should be
reviewed). Two more made a metric measurable for the first time. Six moved
nothing. Compare the trigger this replaced: 120 of the last 120 commits touch
`ENFORCER_SURFACE_PATHS`, so a path-based trigger fires on everything.

WHY FOUR PARTS

R73/R74/R83 each moved this measurement and each could have written the first
two parts truthfully — "the parser was dropping rows and now it is not" is
correct and is the point. None of them was asked for parts 3 and 4, and the
first project below the new line answered the bar with 29 correctly-named
stubs. Part 3 asks what the cheapest way to satisfy the new verdict is; part 4
asks what tells that apart from the honest way, and "nothing does" is a
legitimate answer that puts the gap on the record instead of into a project.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

import sys

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

from corpus_replay import (  # noqa: E402
    BASELINE_PATH,
    corpus_projects,
    CORPUS_ROOT,
    MIN_NOTE_CHARS,
    NOTE_PARTS,
    corpus_vector,
    diff_against,
    note_defects,
    replay,
)

pytestmark = [pytest.mark.core]


# ── the note contract ────────────────────────────────────────────────────


def test_a_moved_number_with_no_note_is_a_defect() -> None:
    previous = {"proj": {"spec_undelivered": 40}}
    current = {"proj": {"spec_undelivered": 15}}
    defects = note_defects(previous, current)
    assert any("moved with no `_note`" in d for d in defects), defects


def test_all_four_parts_are_required() -> None:
    """Three of four is not three quarters of a review."""
    previous = {"proj": {"spec_undelivered": 40}}
    current = {"proj": {"spec_undelivered": 15, "_note": {
        "moved": "spec_undelivered 40 -> 15 on the frozen P3 tree",
        "why_right": "the parser stopped dropping the NFR rows it could not read",
        "cheapest_satisfaction": "declare fewer criteria, which nothing here notices",
    }}}
    defects = note_defects(previous, current)
    assert any("discriminating_signal" in d for d in defects), defects


def test_a_part_that_says_nothing_does_not_count() -> None:
    """`"n/a"` is shorter than the anti-rubber-stamp minimum, on purpose."""
    previous = {"proj": {"spec_undelivered": 40}}
    current = {"proj": {"spec_undelivered": 15, "_note": {
        p: ("x" * MIN_NOTE_CHARS if p != "discriminating_signal" else "n/a")
        for p in NOTE_PARTS}}}
    assert any("discriminating_signal" in d for d in note_defects(previous, current))


def test_no_such_signal_is_a_valid_answer_when_it_says_so() -> None:
    """The gap belongs on the record, not inside the next project."""
    previous = {"proj": {"spec_undelivered": 40}}
    current = {"proj": {"spec_undelivered": 15, "_note": {
        "moved": "spec_undelivered 40 -> 15 on the frozen P3 tree",
        "why_right": "the parser stopped dropping rows it could not read; the "
                     "denominator is now the whole declared set",
        "cheapest_satisfaction": "a correctly-named empty test satisfies the new "
                                 "denominator exactly as it satisfied the old one",
        "discriminating_signal": "there is none today — presence-only delivery "
                                 "cannot tell a stub from an assertion; recorded "
                                 "in the ledger with its re-open condition",
    }}}
    assert note_defects(previous, current) == []


def test_the_note_must_name_the_number_that_moved() -> None:
    """A note about a different field is a note about nothing."""
    previous = {"proj": {"spec_undelivered": 40, "runtime_test_seams": 0}}
    current = {"proj": {"spec_undelivered": 40, "runtime_test_seams": 1, "_note": {
        p: "x" * MIN_NOTE_CHARS for p in NOTE_PARTS}}}
    defects = note_defects(previous, current)
    assert any("does not name `runtime_test_seams`" in d for d in defects), defects


def test_creating_the_baseline_needs_no_note() -> None:
    """There is no earlier verdict being overruled the first time."""
    assert note_defects({}, {"proj": {"spec_undelivered": 15}}) == []


# ── the shipped baseline ─────────────────────────────────────────────────


def test_the_shipped_baseline_notes_are_complete() -> None:
    baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    incomplete = []
    for name, entry in sorted(baseline.items()):
        note = entry.get("_note") if isinstance(entry, dict) else None
        if note is None:
            continue
        missing = [p for p in NOTE_PARTS
                   if len(str(note.get(p, "")).strip()) < MIN_NOTE_CHARS]
        if missing:
            incomplete.append(f"{name}: {missing}")
    assert not incomplete, incomplete


def test_the_baseline_covers_every_corpus_project() -> None:
    """Including the ones that cannot be measured.

    A project dropped from the file is a tree this gate stopped watching, and
    Round 46's rule is that an absent witness has to say it is absent. Three
    of the twelve carry no P3 verdict commit and are recorded as unmeasured,
    which is a reading, not a zero.
    """
    if not (CORPUS_ROOT / "harness-methodology").is_dir():
        pytest.skip("corpus projects not present on this machine")
    baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    assert sorted(baseline) == corpus_projects(), (
        "the baseline no longer covers exactly the harness-managed projects "
        "beside this one — a project that appears or disappears changes what "
        "this gate watches, and that is a reviewed decision")
    unmeasured = {n for n, v in baseline.items() if "unmeasured" in v}
    assert unmeasured, (
        "at least one project carries no P3 verdict commit and must be "
        "recorded as unmeasured rather than dropped")


def test_the_baseline_records_the_project_that_cannot_move() -> None:
    """taskq-redo's 130/0 is the whole argument, kept executable.

    Every improvement to the thing that reads declarations is absorbed by the
    projects that left criteria honestly undelivered; a project that answered
    each one with a correctly-named stub is immune to all of them. If this
    line ever moves, the framework has finally gained a signal that a stub is
    not a test — and that is worth stopping for.
    """
    baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    redo = baseline["taskq-redo"]
    assert redo["spec_declared"] == 130 and redo["spec_undelivered"] == 0
    assert redo["runtime_test_seams"] == 1, (
        "the shipped runtime test seam is what does distinguish this tree")


def test_the_shipped_baseline_matches_a_live_replay() -> None:
    """The file is a recording of a measurement, not a hand-written table."""
    if not (CORPUS_ROOT / "taskq-cc" / ".methodology").is_dir():
        pytest.skip("corpus projects not present on this machine")
    baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    for entry in baseline.values():
        entry.pop("_note", None)
    moved = diff_against(baseline, replay())
    assert not moved, "\n  ".join(["baseline no longer matches the corpus:"] + moved)


def test_replaying_leaves_the_corpus_untouched() -> None:
    """`git archive` writes nothing — the property the whole gate rests on."""
    if not (CORPUS_ROOT / "taskq-cc" / ".git").is_dir():
        pytest.skip("corpus projects not present on this machine")

    def snapshot() -> dict:
        out = {}
        for name in corpus_projects():
            project = CORPUS_ROOT / name
            if not (project / ".git").is_dir():
                continue
            status = subprocess.run(
                ["git", "-C", str(project), "status", "--porcelain"],
                capture_output=True, text=True, check=False)
            trees = subprocess.run(
                ["git", "-C", str(project), "worktree", "list"],
                capture_output=True, text=True, check=False)
            out[name] = (status.stdout, trees.stdout)
        return out

    before = snapshot()
    replay()
    assert snapshot() == before, "the replay wrote to a corpus repository"


def test_every_baseline_metric_is_a_pure_function_of_the_tree() -> None:
    """The baseline may not measure the machine.

    `check_ac_deferral_targets` was in the first draft of the vector and is the
    most interesting candidate in the repository — it is the check that blocks
    on "this criterion has no verifier". It is excluded because it calls
    `run_suite`: on an archived tree there is no `.venv`, pytest exits 2, and
    what gets recorded is whether the machine had the project's dependencies
    installed. A baseline that moves when somebody runs `pip install` is noise,
    and it would ratchet a project down for it.

    Coverage and mutation are out for the same reason. The consequence is
    stated in the module docstring rather than hidden: a change to the
    outcome-aware half of delivery does not move this baseline.
    """
    import ast

    source = (REPO / "scripts" / "corpus_replay.py").read_text(encoding="utf-8")
    metrics = next(
        n for n in ast.walk(ast.parse(source))
        if isinstance(n, ast.FunctionDef) and n.name == "_metrics")
    impure = {"run_suite", "_live_test_outcomes", "check_ac_deferral_targets",
              "subprocess", "Popen", "check_output", "run_quality_gate"}
    found: dict[str, set[str]] = {}
    for node in ast.walk(metrics):
        if not isinstance(node, ast.FunctionDef) or node is metrics:
            continue
        names = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
        names |= {n.attr for n in ast.walk(node) if isinstance(n, ast.Attribute)}
        for imp in ast.walk(node):
            if isinstance(imp, (ast.Import, ast.ImportFrom)):
                names |= {a.name for a in imp.names}
        if names & impure:
            found[node.name] = names & impure
    assert not found, (
        f"a baseline metric that executes something measures the machine, not "
        f"this framework's judgement: {found}")


def test_a_moved_verdict_makes_the_replay_exit_nonzero(tmp_path: Path, monkeypatch) -> None:
    """Reporting a move without blocking on it is Round 43's shape.

    The whole gate is that a verdict on already-accepted work cannot move
    silently; a printed warning in a 400-line pre-push log is silence.
    """
    import corpus_replay as cr

    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps({"proj": {"spec_declared": 100}}), encoding="utf-8")
    monkeypatch.setattr(cr, "BASELINE_PATH", baseline)
    monkeypatch.setattr(cr, "replay", lambda *a, **k: {"proj": {"spec_declared": 42}})
    # `tmp_path` holds no harness-managed project, which is now the skip
    # condition — the corpus has to be non-empty for the comparison to be
    # reached at all. Stubbing discovery is what makes this test about the
    # exit code rather than about whether a corpus is present.
    monkeypatch.setattr(cr, "corpus_projects", lambda *a, **k: ["proj"])
    monkeypatch.setattr(sys, "argv", ["corpus_replay.py", "--corpus", str(tmp_path)])
    assert cr._cli() == 1, "a moved verdict must block, not merely print"


def test_a_machine_with_no_corpus_skips_instead_of_blocking(tmp_path: Path, monkeypatch, capsys) -> None:
    """The gate is local; a runner without delivered trees must not go red.

    This is the failure this gate's own first push produced. The skip was
    conditioned on `not corpus.is_dir()`, and on a CI runner the parent of the
    checkout DOES exist — it just holds no project. So the replay measured
    zero trees, `diff_against` read all fifteen baseline entries as vanished,
    and CI blocked on a machine that had nothing to say.

    The condition now names what actually makes the gate local: whether any
    harness-managed project is there. `tmp_path` is a real, existing,
    empty-of-projects directory — the same shape as the runner.
    """
    import corpus_replay as cr

    monkeypatch.setattr(sys, "argv", ["corpus_replay.py", "--corpus", str(tmp_path)])
    assert cr._cli() == 0, "a machine with no corpus must skip, not block"
    out = capsys.readouterr().out
    assert "SKIP" in out and "no harness-managed project" in out, out


def test_a_directory_that_does_not_exist_also_skips(tmp_path: Path, monkeypatch) -> None:
    """The older condition still has to hold — this widened it, not replaced it."""
    import corpus_replay as cr

    monkeypatch.setattr(
        sys, "argv", ["corpus_replay.py", "--corpus", str(tmp_path / "nope")])
    assert cr._cli() == 0


def test_a_metric_this_enforcer_cannot_compute_is_none_not_zero(tmp_path: Path) -> None:
    """Rounds 32/35, and the reason the historical replay works at all.

    Eight of Round 87's nine commits predate `criteria_review`, and the first
    version of `corpus_vector` imported it at the top — so replaying those
    enforcers raised ModuleNotFoundError and produced no vector at all. Each
    metric now degrades on its own, and `diff_against` reports the transition
    rather than a number.
    """
    vector = corpus_vector(tmp_path)
    assert set(vector) >= {"spec_declared", "runtime_test_seams"}
    assert all(v is None or isinstance(v, int)
               for k, v in vector.items() if k != "_unmeasured_metrics")

    was = {"proj": {"spec_declared": 100}}
    now = {"proj": {"spec_declared": None,
                    "_unmeasured_metrics": {"spec_declared": "ModuleNotFoundError: gone"}}}
    lines = diff_against(was, now)
    assert any("NO LONGER MEASURABLE" in ln for ln in lines), lines
    assert not any("-> 0" in ln for ln in lines), lines
