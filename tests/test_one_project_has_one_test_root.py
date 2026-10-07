"""Round 116 站1 — the test and source roots are decided by where files are, not by an empty directory.

`ProjectLayout.active_test_dir` returned `03-development/tests` when that
DIRECTORY existed and root `tests/` otherwise. `init-project` creates the two
`03-development` directories empty, and git does not track empty directories,
so any checkout of an initialised project that has no test yet — a clone, a
worktree, CI — resolved to root `tests/` and `src/`. Measured on taskq-final's
init commit 992bf37 (`git archive`): `tests`, `src`. Its FR-01 RED (0295497)
was told to "Create `tests/test_fr01.py`", and the framework's own golden
prompts say the same thing beside `--cov=03-development/src`.

Rule now (tests and src alike): the root that holds files; when neither does,
`03-development/<x>` for a project laid out in phase directories, root `<x>`
otherwise. A project that keeps `src/` and `tests/` at the root (the JS
toolchain templates support it; the TS pilot fixture is one) keeps them.
"""

from __future__ import annotations

from pathlib import Path

from core.utils.project_layout import ProjectLayout


def _init_snapshot(tmp_path: Path) -> Path:
    """What a fresh checkout of an initialised project holds: phase docs, no code yet."""
    (tmp_path / "01-requirements").mkdir()
    (tmp_path / "01-requirements" / "SRS.md").write_text("# SRS\n", encoding="utf-8")
    return tmp_path


def _rel(layout: ProjectLayout, p: Path) -> str:
    return layout.get_relative_str(p)


def test_a_fresh_checkout_of_a_phase_project_points_at_03_development(tmp_path):
    layout = ProjectLayout(_init_snapshot(tmp_path))
    assert _rel(layout, layout.active_test_dir) == "03-development/tests"
    assert _rel(layout, layout.active_src_dir) == "03-development/src"


def test_the_answer_does_not_depend_on_an_empty_directory(tmp_path):
    proj = _init_snapshot(tmp_path)
    before = ProjectLayout(proj).active_test_dir
    (proj / "03-development" / "tests").mkdir(parents=True)
    assert ProjectLayout(proj).active_test_dir == before


def test_a_root_layout_with_phase_docs_keeps_its_root(tmp_path):
    """The TS pilot shape: phase docs, code and tests at the root."""
    proj = _init_snapshot(tmp_path)
    (proj / "src").mkdir()
    (proj / "src" / "parser.ts").write_text("export const x = 1\n", encoding="utf-8")
    (proj / "tests").mkdir()
    (proj / "tests" / "test_fr01.test.ts").write_text("// t\n", encoding="utf-8")
    (proj / "03-development" / "tests").mkdir(parents=True)  # init's empty dir
    layout = ProjectLayout(proj)
    assert _rel(layout, layout.active_test_dir) == "tests"
    assert _rel(layout, layout.active_src_dir) == "src"


def test_a_project_without_phase_directories_keeps_the_root(tmp_path):
    """The framework repository itself: no phase directories, tests at the root."""
    layout = ProjectLayout(tmp_path)
    assert layout.active_test_dir == tmp_path / "tests"


def test_files_win_over_the_layout_default(tmp_path):
    proj = _init_snapshot(tmp_path)
    (proj / "03-development" / "tests").mkdir(parents=True)
    (proj / "03-development" / "tests" / "test_fr01.py").write_text("x = 1\n", encoding="utf-8")
    (proj / "tests").mkdir()
    (proj / "tests" / "test_fr02.py").write_text("x = 1\n", encoding="utf-8")
    assert _rel(ProjectLayout(proj), ProjectLayout(proj).active_test_dir) == "03-development/tests"


def test_caches_and_hidden_files_are_not_files_of_a_root(tmp_path):
    proj = _init_snapshot(tmp_path)
    (proj / "tests" / "__pycache__").mkdir(parents=True)
    (proj / "tests" / "__pycache__" / "x.pyc").write_bytes(b"\0")
    (proj / "tests" / ".pytest_cache").mkdir()
    (proj / "tests" / ".pytest_cache" / "README.md").write_text("x", encoding="utf-8")
    (proj / "tests" / ".gitkeep").write_text("", encoding="utf-8")
    assert _rel(ProjectLayout(proj), ProjectLayout(proj).active_test_dir) == "03-development/tests"
