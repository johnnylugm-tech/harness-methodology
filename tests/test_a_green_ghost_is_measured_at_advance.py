"""Round 118 站4 — advance asks the tree it hands on whether a GREEN ghost still holds.

A ghost paper-trail record is written when a code-producing step reports done
with no substantive code change, and advance-phase blocked on every record it
found — forever: nothing ever cleared or re-judged `.sessi-work/ghost_detected/`
(since 2026-07-11), and the remedy it printed, "re-run each flagged step with
genuine code changes", cannot be met by an FR whose work is already done.

taskq-retry, 2026-10-11: a collection-interrupted suite (fixed in 站2) made
FR-01/FR-02 read RED, so TDD-GREEN was re-dispatched on FRs that already had
their GREEN commits; both agents truthfully answered "already passes; no
changes needed" and both were recorded as ghosts. After 站2, both FRs passed
Gate 1 and Gate 2 passed, yet Advance failed five rounds on those two records.

TDD-GREEN is defined by the FR's own tests passing — something the framework
measures itself (Round 41's `fr_suite_verdict`). So a GREEN record whose FR is
GREEN on the tree being advanced no longer blocks; it is listed and ledgered.
RED or UNKNOWN still block, and so does every other step: IMPROVE has no
measurable outcome, and Gate 1 passing says nothing about whether it ran.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import harness_cli  # noqa: F401  entry-first load order
from cli.phase_cmds import _check_ghost_paper_trail  # noqa: E402
from core.quality_gate import test_suite_run as suite_run  # noqa: E402
from core.quality_gate.ghost_detector import GHOST_PAPER_TRAIL_DIR  # noqa: E402

pytestmark = [pytest.mark.core]


def _project(tmp_path: Path, *, expected: str, language: str = "python") -> Path:
    (tmp_path / ".methodology").mkdir()
    (tmp_path / ".methodology" / "state.json").write_text(
        json.dumps({"language": language, "current_phase": 3}), encoding="utf-8")
    if language != "python":
        return tmp_path
    src = tmp_path / "03-development" / "src"
    tests = tmp_path / "03-development" / "tests"
    src.mkdir(parents=True)
    tests.mkdir(parents=True)
    (src / "widget.py").write_text("def handle(v):\n    return v.upper()\n", encoding="utf-8")
    (tests / "test_fr01.py").write_text(
        "from widget import handle\n\n\n"
        f"def test_fr01_handles_input():\n    assert handle('x') == {expected!r}\n",
        encoding="utf-8")
    (tmp_path / "conftest.py").write_text(
        "import sys\nfrom pathlib import Path\n"
        "sys.path.insert(0, str(Path(__file__).parent / '03-development' / 'src'))\n",
        encoding="utf-8")
    return tmp_path


def _ghost(project: Path, fr_id: str, step: str) -> None:
    d = project / GHOST_PAPER_TRAIL_DIR
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{fr_id}_{step}.json").write_text(json.dumps({
        "ghost_detected": True, "phase": 3, "fr_id": fr_id, "step": step,
        "reason": "HEAD did not move — agent produced no commits",
    }), encoding="utf-8")


@pytest.fixture(autouse=True)
def _fresh_memo():
    suite_run._CACHE.clear()
    yield
    suite_run._CACHE.clear()


def test_a_green_ghost_whose_fr_is_green_does_not_block(tmp_path, capsys):
    project = _project(tmp_path, expected="X")
    _ghost(project, "FR-01", "TDD-GREEN")
    assert _check_ghost_paper_trail(project, 3) == 0
    assert "FR-01 / TDD-GREEN" in capsys.readouterr().out


def test_a_green_ghost_whose_fr_is_red_still_blocks(tmp_path):
    project = _project(tmp_path, expected="NOPE")
    _ghost(project, "FR-01", "TDD-GREEN")
    assert _check_ghost_paper_trail(project, 3) == 22


def test_an_improve_ghost_still_blocks_on_a_green_tree(tmp_path):
    project = _project(tmp_path, expected="X")
    _ghost(project, "FR-01", "TDD-IMPROVE")
    assert _check_ghost_paper_trail(project, 3) == 22


def test_a_green_ghost_the_framework_cannot_measure_still_blocks(tmp_path):
    project = _project(tmp_path, expected="X", language="javascript")
    _ghost(project, "FR-01", "TDD-GREEN")
    assert _check_ghost_paper_trail(project, 3) == 22


def test_a_green_ghost_for_an_fr_with_no_tests_still_blocks(tmp_path):
    project = _project(tmp_path, expected="X")
    _ghost(project, "FR-07", "TDD-GREEN")
    assert _check_ghost_paper_trail(project, 3) == 22


def test_the_record_itself_is_kept(tmp_path):
    project = _project(tmp_path, expected="X")
    _ghost(project, "FR-01", "TDD-GREEN")
    _check_ghost_paper_trail(project, 3)
    assert (project / GHOST_PAPER_TRAIL_DIR / "FR-01_TDD-GREEN.json").is_file()
