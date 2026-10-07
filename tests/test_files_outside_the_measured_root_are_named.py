"""Round 116 站2 — a file in the root the suite does not run is named, not silently skipped.

taskq-final wrote its tests in root `tests/`, then built a symlink mirror in
`03-development/tests` for Gate 2's integration tool; once the mirror held
files the measured root moved there, and seven tracked files written to the
root afterwards — four bug-hunt repros, a benchmark, two conftests — were
never run by the framework's suite, with nothing saying so (Round 46). A file
reached through a symlink from the measured root IS run, so it is not stray.
`advance-phase` names the stray files from Phase 3 on.
"""

from __future__ import annotations

from pathlib import Path

from core.utils.project_layout import ProjectLayout


def _project(tmp_path: Path) -> Path:
    (tmp_path / "01-requirements").mkdir()
    (tmp_path / "01-requirements" / "SRS.md").write_text("# SRS\n", encoding="utf-8")
    dev = tmp_path / "03-development" / "tests"
    dev.mkdir(parents=True)
    (dev / "test_fr02.py").write_text("x = 1\n", encoding="utf-8")
    root = tmp_path / "tests"
    root.mkdir()
    (root / "test_fr01.py").write_text("x = 1\n", encoding="utf-8")
    (root / "test_bug_hunt_t07.py").write_text("x = 1\n", encoding="utf-8")
    (dev / "test_fr01.py").symlink_to(root / "test_fr01.py")  # the mirror
    (root / "__pycache__").mkdir()
    (root / "__pycache__" / "x.pyc").write_bytes(b"\0")
    return tmp_path


def test_a_file_the_suite_never_runs_is_stray_and_a_mirrored_one_is_not(tmp_path):
    proj = _project(tmp_path)
    assert ProjectLayout(proj).stray_files() == ["tests/test_bug_hunt_t07.py"]


def test_a_root_symlinked_onto_the_measured_root_has_no_stray_files(tmp_path):
    """omnibot / tts-new: root `tests` -> 03-development/tests."""
    (tmp_path / "01-requirements").mkdir()
    dev = tmp_path / "03-development" / "tests"
    dev.mkdir(parents=True)
    (dev / "test_fr01.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "tests").symlink_to(dev)
    assert ProjectLayout(tmp_path).stray_files() == []


def test_source_files_outside_the_measured_source_root_are_named(tmp_path):
    proj = _project(tmp_path)
    (proj / "03-development" / "src" / "pkg").mkdir(parents=True)
    (proj / "03-development" / "src" / "pkg" / "a.py").write_text("x = 1\n", encoding="utf-8")
    (proj / "src").mkdir()
    (proj / "src" / "b.py").write_text("x = 1\n", encoding="utf-8")
    assert "src/b.py" in ProjectLayout(proj).stray_files()


def test_the_p3_exit_refuses_stray_files_and_earlier_phases_do_not(tmp_path):
    from cli.advance_prechecks import _precheck_stray_files
    from cli.exit_codes import EX_ADVANCE_STRAY_TEST_FILES
    from core.fault_owner import OWNER_BY_EXIT, Owner

    proj = _project(tmp_path)
    assert _precheck_stray_files(2, proj) is None
    assert _precheck_stray_files(3, proj) == EX_ADVANCE_STRAY_TEST_FILES
    assert OWNER_BY_EXIT[EX_ADVANCE_STRAY_TEST_FILES] is Owner.PROJECT
    (proj / "tests" / "test_bug_hunt_t07.py").rename(proj / "03-development" / "tests" / "test_bug_hunt_t07.py")
    assert _precheck_stray_files(3, proj) is None


def test_advance_phase_asks():
    from tests.support.pipeline import pipeline_source

    src = pipeline_source("cli/phase_cmds.py", "_advance_prechecks", helper_prefix="_precheck_")
    assert "_precheck_stray_files(completed_phase, project)" in src
