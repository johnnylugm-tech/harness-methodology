"""Tests for cross-run failure memory (Direction C).

Lessons are distilled from Gate BLOCKs / findings and stored under
.methodology/lessons/. recall_lessons is relevance-ranked (dimension + fr_id
match) and CAPPED — that cap is the anti-pollution control that keeps auto-
injection into a phase prompt from ballooning context. record is idempotent
(same failure recorded once).
"""

from __future__ import annotations

from pathlib import Path

from core.lessons import (
    Lesson,
    format_lessons_block,
    load_lessons,
    lessons_dir,
    recall_lessons,
    record_lesson,
)


def _mk(project: Path, **kw) -> Lesson:
    base = dict(failure_mode="f", fix="x", source="gate-block", created_at="2026-07-01")
    base.update(kw)
    lesson = Lesson(**base)
    record_lesson(project, lesson)
    return lesson


def test_record_then_load_roundtrip(tmp_path: Path) -> None:
    _mk(tmp_path, failure_mode="mutation 55<70 in FR-01", dimension="mutation_testing",
        fr_ids=["FR-01"], phase=3)
    loaded = load_lessons(tmp_path)
    assert len(loaded) == 1
    assert loaded[0].dimension == "mutation_testing"
    assert loaded[0].fr_ids == ["FR-01"]
    assert loaded[0].phase == 3


def test_record_is_idempotent(tmp_path: Path) -> None:
    _mk(tmp_path, failure_mode="same", dimension="security")
    _mk(tmp_path, failure_mode="same", dimension="security")
    assert len(list(lessons_dir(tmp_path).glob("*.md"))) == 1


def test_recall_ranks_and_gates_by_relevance(tmp_path: Path) -> None:
    _mk(tmp_path, failure_mode="dim+fr", dimension="mutation_testing", fr_ids=["FR-01"])
    _mk(tmp_path, failure_mode="dim only", dimension="mutation_testing", fr_ids=["FR-05"])
    _mk(tmp_path, failure_mode="unrelated", dimension="linting", fr_ids=["FR-02"])
    got = recall_lessons(tmp_path, fr_ids=["FR-01"], dimension="mutation_testing", limit=5)
    # relevance-GATED: the unrelated (score-0) lesson must NOT be surfaced —
    # that gating is the anti-pollution guarantee for prompt injection.
    assert [g.failure_mode for g in got] == ["dim+fr", "dim only"]


def test_recall_is_capped(tmp_path: Path) -> None:
    for i in range(10):
        _mk(tmp_path, failure_mode=f"f{i}", dimension="security")
    got = recall_lessons(tmp_path, dimension="security", limit=3)
    assert len(got) == 3


def test_recall_global_lessons(tmp_path: Path) -> None:
    # A project-level lesson (no fr_ids) should be recalled even if it doesn't match the query fr_ids perfectly,
    # because its base relevance is 1.
    _mk(tmp_path, failure_mode="global block", dimension=None, fr_ids=[])
    got = recall_lessons(tmp_path, fr_ids=["FR-01"], dimension=None)
    assert len(got) == 1
    assert got[0].failure_mode == "global block"


def test_recall_empty_when_no_lessons(tmp_path: Path) -> None:
    assert recall_lessons(tmp_path, dimension="x") == []


def test_recall_no_match_returns_empty(tmp_path: Path) -> None:
    # Relevance-gated: a lesson unrelated to the query is not surfaced.
    _mk(tmp_path, failure_mode="a", dimension="security", fr_ids=["FR-09"])
    got = recall_lessons(tmp_path, fr_ids=["FR-01"], dimension="mutation_testing")
    assert got == []


def test_format_block_contains_failure_and_fix() -> None:
    ls = [Lesson(failure_mode="boom", fix="do X", source="gate-block",
                 dimension="security", created_at="2026-07-01")]
    block = format_lessons_block(ls)
    assert "boom" in block and "do X" in block and "security" in block


def test_format_block_empty_is_empty_string() -> None:
    assert format_lessons_block([]) == ""


# ── capture (Gate BLOCK → lessons) ───────────────────────────────────────────


class _Dim:
    def __init__(self, name: str, score: float, threshold: float) -> None:
        self.name, self.score, self.threshold = name, score, threshold


class _GateResult:
    score = 55.0
    dimensions = [_Dim("mutation_testing", 55, 70), _Dim("security", 90, 80)]


def test_record_gate_block_captures_only_failing_dimensions(tmp_path: Path) -> None:
    from core.lessons import record_gate_block

    record_gate_block(tmp_path, gate_num=3, phase=4, fr_id="FR-01", result=_GateResult())
    ls = load_lessons(tmp_path)
    assert {le.dimension for le in ls} == {"mutation_testing"}  # security passed
    assert ls[0].fr_ids == ["FR-01"] and ls[0].source == "gate-block" and ls[0].phase == 4


def test_record_gate_block_composite_when_no_dim_detail(tmp_path: Path) -> None:
    from core.lessons import record_gate_block

    class _R:
        score = 40.0
        dimensions: list = []

    record_gate_block(tmp_path, gate_num=2, phase=3, fr_id=None, result=_R())
    ls = load_lessons(tmp_path)
    assert len(ls) == 1 and "Gate 2 blocked" in ls[0].failure_mode


# ── wiring guards (auto-loop must stay connected) ────────────────────────────

_REPO = Path(__file__).resolve().parent.parent


def test_gate_block_capture_is_wired_into_finalize_gate() -> None:
    src = (_REPO / "cli" / "gate_cmds.py").read_text(encoding="utf-8")
    assert "record_gate_block" in src, (
        "gate-block lesson capture unwired from finalize-gate — the closed loop "
        "no longer learns from blocks"
    )


def test_lesson_injection_is_wired_into_load_context() -> None:
    src = (_REPO / "cli" / "project_cmds.py").read_text(encoding="utf-8")
    assert "recall_lessons" in src, (
        "lesson auto-injection unwired from load-context — phase entry no longer "
        "surfaces past failures"
    )


def test_load_context_cli_injects_recalled_lessons(tmp_path: Path) -> None:
    """End-to-end: a recorded lesson for FR-01 must appear in load-context output
    when this phase's FRs include FR-01 (the auto-injection the user chose)."""
    import json
    import subprocess
    import sys

    record_lesson(tmp_path, Lesson(
        failure_mode="mutation 55 below 70 in boundary", fix="add killing asserts",
        source="gate-block", dimension="mutation_testing", fr_ids=["FR-01"],
        created_at="2026-07-01"))
    meth = tmp_path / ".methodology"
    meth.mkdir(exist_ok=True)  # record_lesson already created .methodology/lessons/
    (meth / "quality_manifest.json").write_text(
        json.dumps({"fr_ids": ["FR-01"]}), encoding="utf-8")
    (meth / "state.json").write_text(
        json.dumps({"current_phase": 3}), encoding="utf-8")

    r = subprocess.run(
        [sys.executable, str(_REPO / "harness_cli.py"), "load-context",
         "--project", str(tmp_path), "--phase", "3"],
        capture_output=True, text=True, cwd=_REPO, timeout=120,
    )
    assert "mutation 55 below 70 in boundary" in r.stdout, r.stdout[-1500:]


class TestRecallHappensWhereFailureHappens:
    """Round 27 站5a — the recall exists, is wired, and fires at the wrong moment.

    `load-context --phase N` calls recall_lessons once at phase entry and drops
    the text into phase{N}_ctx.json for the entry agent. Two things follow from
    that being the ONLY call site:

      * entering Phase 3 the lessons directory is empty, so the lessons that
        phase produces reach nobody until Phase 4 opens;
      * the call passes no `dimension`, so _relevance's +2 for a dimension match
        never fires.

    Failure is per-FR and per-dimension. One run recorded 23 test_coverage blocks
    across five phases, several on the same FR twice running, each fix dispatched
    knowing nothing about the last.
    """

    def test_the_fix_prompt_carries_what_already_failed_here(self, tmp_path):
        from core.lessons import Lesson, record_lesson
        from cli.fr_prompts.fix import build_code_fix_prompt

        record_lesson(tmp_path, Lesson(
            source="gate-block", phase=3, dimension="test_coverage",
            fr_ids=["FR-05"],
            failure_mode="test_coverage scored 93.0, needs 100.0 (gap 7.0)",
            fix="Run pytest --cov to find uncovered lines; add unit tests per gap",
        ))
        prompt = build_code_fix_prompt(
            "FR-05", 3, tmp_path, tmp_path / "SRS.md",
            "tests/test_fr05.py", "src", failing_dims=["test_coverage 93.0 < 100.0"],
        )
        assert "Known failure modes from past runs" in prompt
        assert "scored 93.0" in prompt

    def test_an_unrelated_lesson_is_not_pulled_in(self, tmp_path):
        """recall_lessons is relevance-gated, so this cannot pad the prompt."""
        from core.lessons import Lesson, record_lesson
        from cli.fr_prompts.fix import build_code_fix_prompt

        record_lesson(tmp_path, Lesson(
            source="gate-block", phase=3, dimension="linting", fr_ids=["FR-99"],
            failure_mode="ruff found 3 violations", fix="run ruff --fix",
        ))
        prompt = build_code_fix_prompt(
            "FR-05", 3, tmp_path, tmp_path / "SRS.md",
            "tests/test_fr05.py", "src", failing_dims=["test_coverage 93.0 < 100.0"],
        )
        assert "ruff found 3 violations" not in prompt

    def test_no_lessons_yet_is_silent(self, tmp_path):
        from cli.fr_prompts.fix import build_code_fix_prompt
        prompt = build_code_fix_prompt(
            "FR-05", 3, tmp_path, tmp_path / "SRS.md",
            "tests/test_fr05.py", "src", failing_dims=["test_coverage 93.0 < 100.0"],
        )
        assert "Known failure modes" not in prompt

    def test_dimension_is_passed_so_relevance_can_use_it(self, tmp_path):
        """The +2 dimension bonus in _relevance was unreachable from the only
        production call site, which passes fr_ids and a limit and nothing else."""
        import inspect
        from cli.fr_prompts import _shared
        src = inspect.getsource(_shared._past_failures_block)
        assert "dimension=dimension" in src
