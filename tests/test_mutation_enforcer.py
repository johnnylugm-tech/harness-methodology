"""Unit tests for ``core.quality_gate.mutation_enforcer``.

These tests focus on the path-resolution helpers and the public entry
point's contract (without actually invoking mutmut). End-to-end mutmut
behaviour is covered by the agent's gate-evaluation protocol in
``evaluate_dimension.md``, not here.

Regression coverage includes Bug #41 (setup.cfg rewrite), Bug #42
(stash/restore), Bug #43 (no-setup.cfg fallback), and Bug #91
(sys.executable instead of hardcoded "python" so Homebrew Python 3.11+
can invoke pytest through mutmut).
"""
import configparser
import json
import sys
from pathlib import Path

import pytest

from core.quality_gate.mutation_enforcer import (
    _resolve_test_dir,
    _resolve_mutmut_workdir,
    _abs_paths_to_mutate,
    _read_paths_to_exclude,
    _detect_data_only_files,
    _copy_setup_cfg_to_workdir,
    _find_source_setup_cfg,
    _paths_to_exclude_flag,
    _count_mutmut_results,
    run_mutation_precheck,
)


# ---------------------------------------------------------------------------
# _resolve_mutmut_workdir
# ---------------------------------------------------------------------------


def test_resolve_mutmut_workdir_falls_back_to_dev_src(tmp_path):
    """No [mutmut] section → default to 03-development/src."""
    cwd, paths = _resolve_mutmut_workdir(tmp_path)
    assert cwd == tmp_path
    assert paths == "03-development/src"


def test_resolve_mutmut_workdir_reads_root_section(tmp_path):
    """[mutmut] at project root wins over hardcoded default."""
    (tmp_path / "setup.cfg").write_text(
        "[mutmut]\npaths_to_mutate = custom/src\n",
        encoding="utf-8",
    )
    cwd, paths = _resolve_mutmut_workdir(tmp_path)
    assert cwd == tmp_path
    assert paths == "custom/src"


def test_resolve_mutmut_workdir_subdir_override(tmp_path):
    """[mutmut] in a nested setup.cfg flips cwd to that subdirectory."""
    (tmp_path / "setup.cfg").write_text(
        "[mutmut]\npaths_to_mutate = 03-development/src\n",
        encoding="utf-8",
    )
    sub = tmp_path / "03-development"
    sub.mkdir()
    (sub / "setup.cfg").write_text("[mutmut]\n", encoding="utf-8")
    cwd, paths = _resolve_mutmut_workdir(tmp_path)
    assert cwd == sub
    assert paths == "src"


# ---------------------------------------------------------------------------
# _abs_paths_to_mutate
# ---------------------------------------------------------------------------


def test_abs_paths_to_mutate_single(tmp_path):
    result = _abs_paths_to_mutate(tmp_path, "src")
    assert result == str((tmp_path / "src").resolve())


def test_abs_paths_to_mutate_comma_separated(tmp_path):
    result = _abs_paths_to_mutate(tmp_path, "a, b , c")
    parts = result.split(",")
    assert len(parts) == 3
    assert all(p.startswith("/") for p in parts)


# ---------------------------------------------------------------------------
# _read_paths_to_exclude (Bug G regression)
# ---------------------------------------------------------------------------


def test_read_paths_to_exclude_splits_on_whitespace(tmp_path):
    """configparser returns the value as one string — mutmut 2.x then
    iterates characters, producing a broken exclude list. We must split
    on whitespace ourselves before passing via --paths-to-exclude."""
    (tmp_path / "setup.cfg").write_text(
        "[mutmut]\npaths_to_exclude = config.py constants.py models.py\n",
        encoding="utf-8",
    )
    result = _read_paths_to_exclude(tmp_path)
    assert result == ["config.py", "constants.py", "models.py"]


def test_read_paths_to_exclude_missing(tmp_path):
    """No [mutmut] section → empty list, no exception."""
    (tmp_path / "setup.cfg").write_text("[other]\n", encoding="utf-8")
    assert _read_paths_to_exclude(tmp_path) == []


def test_read_paths_to_exclude_empty_value(tmp_path):
    (tmp_path / "setup.cfg").write_text("[mutmut]\n", encoding="utf-8")
    assert _read_paths_to_exclude(tmp_path) == []


# ---------------------------------------------------------------------------
# _paths_to_exclude_flag (regression: mutmut CLI is single-string only)
# ---------------------------------------------------------------------------


def test_paths_to_exclude_flag_single_value():
    """One exclude → one --paths-to-exclude flag with that value."""
    assert _paths_to_exclude_flag(["config.py"]) == "--paths-to-exclude=config.py"


def test_paths_to_exclude_flag_multiple_joined_comma():
    """Multiple excludes → SINGLE flag, comma-joined. Multiple flags on the
    command line would collapse to the last one (mutmut's option has
    type=click.STRING, no multiple=True)."""
    flag = _paths_to_exclude_flag(["config.py", "constants.py", "models.py"])
    assert flag == "--paths-to-exclude=config.py,constants.py,models.py"
    assert flag.count("--paths-to-exclude=") == 1, (
        "Must be exactly one --paths-to-exclude flag, not multiple"
    )


# ---------------------------------------------------------------------------
# _detect_data_only_files
# ---------------------------------------------------------------------------


def test_detect_data_only_files_excludes_known_names(tmp_path):
    (tmp_path / "config.py").write_text("X = 1\n", encoding="utf-8")
    (tmp_path / "constants.py").write_text("Y = 2\n", encoding="utf-8")
    (tmp_path / "__init__.py").write_text("", encoding="utf-8")
    result = _detect_data_only_files(tmp_path)
    assert "config.py" in result
    assert "constants.py" in result
    assert "__init__.py" in result


def test_detect_data_only_files_keeps_logic_files(tmp_path):
    (tmp_path / "engine.py").write_text(
        "def run():\n    if True:\n        return 1\n",
        encoding="utf-8",
    )
    result = _detect_data_only_files(tmp_path)
    assert "engine.py" not in result


def test_detect_data_only_files_handles_tab_indent(tmp_path):
    """Tab-indented logic must not be mis-classified as data-only."""
    (tmp_path / "tab_logic.py").write_text(
        "def run():\n\tif True:\n\t\treturn 1\n",
        encoding="utf-8",
    )
    result = _detect_data_only_files(tmp_path)
    assert "tab_logic.py" not in result


def test_detect_data_only_files_handles_two_space_indent(tmp_path):
    """2-space-indented logic must not be mis-classified as data-only."""
    (tmp_path / "two_space.py").write_text(
        "def run():\n  if True:\n    return 1\n",
        encoding="utf-8",
    )
    result = _detect_data_only_files(tmp_path)
    assert "two_space.py" not in result


# ---------------------------------------------------------------------------
# _copy_setup_cfg_to_workdir
# ---------------------------------------------------------------------------


def test_copy_setup_cfg_to_workdir(tmp_path):
    src_cfg = tmp_path / "setup.cfg"
    src_cfg.write_text("[mutmut]\npaths_to_mutate = 03-development/src\n",
                       encoding="utf-8")
    workdir = tmp_path / "workdir"
    workdir.mkdir()
    _copy_setup_cfg_to_workdir(tmp_path, str(workdir), "/abs/tests")
    # Bug #41 fix: the [mutmut] section is rewritten — runner is added,
    # tests_dir is set to the absolute path, backup/disable stripped.
    cp = configparser.ConfigParser()
    cp.read(str(workdir / "setup.cfg"), encoding="utf-8")
    assert cp["mutmut"]["paths_to_mutate"] == "03-development/src"
    assert cp["mutmut"]["runner"] == f"{sys.executable} -m pytest"
    assert cp["mutmut"]["tests_dir"] == "/abs/tests"


def test_copy_setup_cfg_to_workdir_no_setup_cfg(tmp_path):
    """No setup.cfg + no abs_test_dir → no error, no file written."""
    workdir = tmp_path / "workdir"
    workdir.mkdir()
    _copy_setup_cfg_to_workdir(tmp_path, str(workdir))
    assert not (workdir / "setup.cfg").exists()


def test_copy_setup_cfg_no_project_cfg_writes_both_sections(tmp_path):
    """Bug #43 v2 + Finding #1: no project setup.cfg + abs_test_dir → workdir
    setup.cfg must contain BOTH [mutmut] (runner, tests_dir) AND
    [tool:pytest] (testpaths). Previously a fresh ConfigParser was used,
    silently discarding the [mutmut] section built earlier in the function.
    """
    workdir = tmp_path / "workdir"
    workdir.mkdir()
    _copy_setup_cfg_to_workdir(tmp_path, str(workdir), "/abs/path/tests")
    assert (workdir / "setup.cfg").exists(), "setup.cfg must be generated"
    cp = configparser.ConfigParser()
    cp.read(str(workdir / "setup.cfg"), encoding="utf-8")
    assert cp["mutmut"]["runner"] == f"{sys.executable} -m pytest"
    assert cp["mutmut"]["tests_dir"] == "/abs/path/tests"
    assert cp["tool:pytest"]["testpaths"] == "/abs/path/tests"


# ---------------------------------------------------------------------------
# Bug #41: [mutmut] section rewrite for temp-workdir context
# ---------------------------------------------------------------------------


def test_copy_setup_cfg_rewrites_runner_pytest(tmp_path):
    """Project has runner=pytest → workdir setup.cfg has runner=python -m pytest."""
    src_cfg = tmp_path / "setup.cfg"
    src_cfg.write_text("[mutmut]\nrunner = pytest\n", encoding="utf-8")
    workdir = tmp_path / "workdir"
    workdir.mkdir()
    _copy_setup_cfg_to_workdir(tmp_path, str(workdir), "/abs/path/tests")
    cp = configparser.ConfigParser()
    cp.read(str(workdir / "setup.cfg"), encoding="utf-8")
    assert cp["mutmut"]["runner"] == f"{sys.executable} -m pytest"


@pytest.mark.parametrize("project_runner", ["pytest", ""])
def test_copy_setup_cfg_runner_uses_sys_executable_bug_91(tmp_path, project_runner):
    """Bug #91: runner must be sys.executable-based, not hardcoded "python -m pytest".

    Modern macOS (Homebrew Python 3.11+) and PEP 394-compliant systems
    do not provide a `python` symlink — only `python3` / `python3.11`.
    Hardcoding the runner to `python -m pytest` causes mutmut's Popen
    to throw FileNotFoundError [Errno 2] No such file or directory: 'python'.
    The fix uses sys.executable (the interpreter actually running the
    framework), which always resolves to a real binary, including inside
    a virtualenv.
    """
    src_cfg = tmp_path / "setup.cfg"
    if project_runner:
        src_cfg.write_text(f"[mutmut]\nrunner = {project_runner}\n", encoding="utf-8")
    else:
        src_cfg.write_text("[mutmut]\n", encoding="utf-8")
    workdir = tmp_path / "workdir"
    workdir.mkdir()
    _copy_setup_cfg_to_workdir(tmp_path, str(workdir), "/abs/path/tests")
    cp = configparser.ConfigParser()
    cp.read(str(workdir / "setup.cfg"), encoding="utf-8")
    runner = cp["mutmut"]["runner"]
    assert not runner.startswith("python "), (
        f"runner still hardcoded to 'python ...' form (Bug #91 not fixed): {runner!r}"
    )
    assert runner == f"{sys.executable} -m pytest", (
        f"runner must be sys.executable + ' -m pytest', got {runner!r}"
    )


def test_copy_setup_cfg_runner_warning_for_custom_runner_bug_91(tmp_path, capsys):
    """Bug #91: custom runner scripts are left untouched (and warned).

    When the project uses a non-well-known runner (e.g. `make test`),
    the framework should NOT overwrite it with sys.executable, because
    the custom runner may not be Python at all. The framework logs a
    warning to stderr so the operator knows the runner may not be
    workdir-aware.
    """
    src_cfg = tmp_path / "setup.cfg"
    src_cfg.write_text("[mutmut]\nrunner = make test\n", encoding="utf-8")
    workdir = tmp_path / "workdir"
    workdir.mkdir()
    _copy_setup_cfg_to_workdir(tmp_path, str(workdir), "/abs/path/tests")
    cp = configparser.ConfigParser()
    cp.read(str(workdir / "setup.cfg"), encoding="utf-8")
    # Custom runner preserved verbatim.
    assert cp["mutmut"]["runner"] == "make test"
    # Operator warned on stderr.
    captured = capsys.readouterr()
    assert "runner is custom" in captured.err, (
        f"expected stderr warning about custom runner, got: {captured.err!r}"
    )


@pytest.mark.parametrize("project_runner", [
    "python3 -m pytest -x --assert=plain --no-header -q /abs/project/tests/",
    "python3 -m pytest -x",
    "python -m pytest --tb=short -q",
    "pytest --tb=short",
])
def test_copy_setup_cfg_runner_prefix_match_bug_116(tmp_path, project_runner):
    """Bug #116: well-known runner with extra flags must be normalised to sys.executable.

    Exact-match on _WELL_KNOWN_RUNNERS missed forms like
    'python3 -m pytest -x --assert=plain … /abs/path/tests/'.
    The runner was kept as-is, so on machines where `python3` resolves
    to an older interpreter the mutmut baseline test fails and score = 0.
    Prefix matching fixes this: any runner starting with a well-known
    base is normalised to '{sys.executable} -m pytest'.
    """
    src_cfg = tmp_path / "setup.cfg"
    src_cfg.write_text(f"[mutmut]\nrunner = {project_runner}\n", encoding="utf-8")
    workdir = tmp_path / "workdir"
    workdir.mkdir()
    _copy_setup_cfg_to_workdir(tmp_path, str(workdir), "/abs/path/tests")
    cp = configparser.ConfigParser()
    cp.read(str(workdir / "setup.cfg"), encoding="utf-8")
    runner = cp["mutmut"]["runner"]
    assert runner == f"{sys.executable} -m pytest", (
        f"Bug #116: well-known-prefix runner was not normalised to sys.executable; "
        f"input={project_runner!r} output={runner!r}"
    )


def test_copy_setup_cfg_overrides_tests_dir(tmp_path):
    """Project has tests_dir=tests (relative) → workdir has the absolute path."""
    src_cfg = tmp_path / "setup.cfg"
    src_cfg.write_text("[mutmut]\ntests_dir = tests\n", encoding="utf-8")
    workdir = tmp_path / "workdir"
    workdir.mkdir()
    _copy_setup_cfg_to_workdir(tmp_path, str(workdir), "/abs/path/tests")
    cp = configparser.ConfigParser()
    cp.read(str(workdir / "setup.cfg"), encoding="utf-8")
    assert cp["mutmut"]["tests_dir"] == "/abs/path/tests"


def test_copy_setup_cfg_strips_backup(tmp_path):
    """Project has backup=1 → key is removed (Bug #42 hook)."""
    src_cfg = tmp_path / "setup.cfg"
    src_cfg.write_text("[mutmut]\nbackup = 1\n", encoding="utf-8")
    workdir = tmp_path / "workdir"
    workdir.mkdir()
    _copy_setup_cfg_to_workdir(tmp_path, str(workdir), "/abs/tests")
    cp = configparser.ConfigParser()
    cp.read(str(workdir / "setup.cfg"), encoding="utf-8")
    assert "backup" not in cp["mutmut"]


def test_copy_setup_cfg_strips_disable(tmp_path):
    """Project has disable lines → removed (project disables can hide mutants)."""
    src_cfg = tmp_path / "setup.cfg"
    src_cfg.write_text(
        "[mutmut]\ndisable = 1,2,3\n",
        encoding="utf-8",
    )
    workdir = tmp_path / "workdir"
    workdir.mkdir()
    _copy_setup_cfg_to_workdir(tmp_path, str(workdir), "/abs/tests")
    cp = configparser.ConfigParser()
    cp.read(str(workdir / "setup.cfg"), encoding="utf-8")
    assert "disable" not in cp["mutmut"]


def test_copy_setup_cfg_promotes_pytest_testpaths_to_absolute(tmp_path):
    """Bug #43: relative [tool:pytest] testpaths gets promoted to absolute
    so pytest 8.x discovers tests regardless of internal --rootdir resolution.
    """
    project = tmp_path / "proj"
    project.mkdir()
    tests = project / "03-development" / "tests"
    tests.mkdir(parents=True)
    (tests / "test_x.py").write_text("def test_x(): assert True\n", encoding="utf-8")
    src_cfg = project / "setup.cfg"
    src_cfg.write_text(
        "[tool:pytest]\ntestpaths = 03-development/tests\n",
        encoding="utf-8",
    )
    workdir = project / "workdir"
    workdir.mkdir()
    _copy_setup_cfg_to_workdir(project, str(workdir), str(tests))
    cp = configparser.ConfigParser()
    cp.read(str(workdir / "setup.cfg"), encoding="utf-8")
    assert cp["tool:pytest"]["testpaths"] == str(tests.resolve())


def test_copy_setup_cfg_leaves_absolute_pytest_testpaths_alone(tmp_path):
    """Bug #43: an already-absolute testpaths is left untouched (no double-resolve)."""
    project = tmp_path / "proj"
    project.mkdir()
    src_cfg = project / "setup.cfg"
    src_cfg.write_text(
        "[tool:pytest]\ntestpaths = /abs/path/to/tests\n",
        encoding="utf-8",
    )
    workdir = project / "workdir"
    workdir.mkdir()
    _copy_setup_cfg_to_workdir(project, str(workdir), "/abs/path/to/tests")
    cp = configparser.ConfigParser()
    cp.read(str(workdir / "setup.cfg"), encoding="utf-8")
    assert cp["tool:pytest"]["testpaths"] == "/abs/path/to/tests"


def test_copy_setup_cfg_promotes_pythonpath_to_absolute_bug_106(tmp_path):
    """Bug #106: relative [tool:pytest] pythonpath is promoted to absolute.

    pytest reads `pythonpath` from setup.cfg during early startup and inserts
    it into sys.path. A relative value (e.g. `pythonpath = src`) in the
    workdir mutmut creates resolves to `<workdir>/src` which doesn't exist,
    silently breaking imports of the project's own package — observed as
    `ModuleNotFoundError: No module named 'taskq'` on integration-test.
    """
    project = tmp_path / "proj"
    project.mkdir()
    src_dir = project / "03-development" / "src"
    src_dir.mkdir(parents=True)
    (src_dir / "taskq").mkdir()
    (src_dir / "taskq" / "__init__.py").write_text("", encoding="utf-8")
    src_cfg = project / "setup.cfg"
    src_cfg.write_text(
        "[tool:pytest]\npythonpath = 03-development/src\n",
        encoding="utf-8",
    )
    workdir = project / "workdir"
    workdir.mkdir()
    _copy_setup_cfg_to_workdir(project, str(workdir), str(project / "tests"))
    cp = configparser.ConfigParser()
    cp.read(str(workdir / "setup.cfg"), encoding="utf-8")
    assert cp["tool:pytest"]["pythonpath"] == str(src_dir.resolve())


def test_copy_setup_cfg_leaves_absolute_pythonpath_alone_bug_106(tmp_path):
    """Bug #106: already-absolute [tool:pytest] pythonpath is untouched (no double-resolve)."""
    project = tmp_path / "proj"
    project.mkdir()
    src_cfg = project / "setup.cfg"
    src_cfg.write_text(
        "[tool:pytest]\npythonpath = /abs/path/to/src\n",
        encoding="utf-8",
    )
    workdir = project / "workdir"
    workdir.mkdir()
    _copy_setup_cfg_to_workdir(project, str(workdir), "/abs/tests")
    cp = configparser.ConfigParser()
    cp.read(str(workdir / "setup.cfg"), encoding="utf-8")
    assert cp["tool:pytest"]["pythonpath"] == "/abs/path/to/src"


def test_copy_setup_cfg_warns_on_nonexistent_pythonpath_bug_106(tmp_path, capsys):
    """Bug #106: relative pythonpath that doesn't resolve → log warning, leave original.

    Preserves existing misconfigured-project behavior (ModuleNotFoundError)
    rather than silently changing to a different broken state.
    """
    project = tmp_path / "proj"
    project.mkdir()
    src_cfg = project / "setup.cfg"
    src_cfg.write_text(
        "[tool:pytest]\npythonpath = does/not/exist\n",
        encoding="utf-8",
    )
    workdir = project / "workdir"
    workdir.mkdir()
    _copy_setup_cfg_to_workdir(project, str(workdir), "/abs/tests")
    cp = configparser.ConfigParser()
    cp.read(str(workdir / "setup.cfg"), encoding="utf-8")
    # Value unchanged
    assert cp["tool:pytest"]["pythonpath"] == "does/not/exist"
    # Warning emitted
    captured = capsys.readouterr()
    assert "pythonpath" in captured.err
    assert "does/not/exist" in captured.err


def test_copy_setup_cfg_no_pytest_section_is_noop(tmp_path):
    """Bug #43: project setup.cfg without [tool:pytest] does not crash; mutmut section rewrite still happens."""
    project = tmp_path / "proj"
    project.mkdir()
    src_cfg = project / "setup.cfg"
    src_cfg.write_text(
        "[mutmut]\npaths_to_mutate = src/\n",
        encoding="utf-8",
    )
    workdir = project / "workdir"
    workdir.mkdir()
    _copy_setup_cfg_to_workdir(project, str(workdir), "/abs/tests")
    cp = configparser.ConfigParser()
    cp.read(str(workdir / "setup.cfg"), encoding="utf-8")
    assert "tool:pytest" not in cp
    assert cp["mutmut"]["tests_dir"] == "/abs/tests"


def test_copy_setup_cfg_adds_mutmut_section_if_missing(tmp_path):
    """Project has no [mutmut] section → function injects one with all required keys."""
    src_cfg = tmp_path / "setup.cfg"
    src_cfg.write_text("[tool:pytest]\ntestpaths = tests\n", encoding="utf-8")
    workdir = tmp_path / "workdir"
    workdir.mkdir()
    _copy_setup_cfg_to_workdir(tmp_path, str(workdir), "/abs/tests")
    cp = configparser.ConfigParser()
    cp.read(str(workdir / "setup.cfg"), encoding="utf-8")
    assert "mutmut" in cp
    assert cp["mutmut"]["runner"] == f"{sys.executable} -m pytest"
    assert cp["mutmut"]["tests_dir"] == "/abs/tests"


def test_copy_setup_cfg_leaves_custom_runner_alone(tmp_path, capsys):
    """Project uses a custom runner (e.g. make test) → don't override, log warn."""
    src_cfg = tmp_path / "setup.cfg"
    src_cfg.write_text("[mutmut]\nrunner = make test\n", encoding="utf-8")
    workdir = tmp_path / "workdir"
    workdir.mkdir()
    _copy_setup_cfg_to_workdir(tmp_path, str(workdir), "/abs/tests")
    cp = configparser.ConfigParser()
    cp.read(str(workdir / "setup.cfg"), encoding="utf-8")
    # Custom runner preserved
    assert cp["mutmut"]["runner"] == "make test"
    # Warning logged
    assert "custom" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# _resolve_test_dir
# ---------------------------------------------------------------------------


def test_resolve_test_dir_project_root_dev_tests(tmp_path):
    """Standard tts-new layout: 03-development/tests wins over root/tests."""
    dev = tmp_path / "03-development" / "tests"
    dev.mkdir(parents=True)
    (tmp_path / "tests").mkdir()
    result = _resolve_test_dir(tmp_path, tmp_path)
    assert result == str(dev.resolve())


def test_resolve_test_dir_project_root_falls_back(tmp_path):
    """No dev/tests → fall back to root/tests, then root/test."""
    (tmp_path / "tests").mkdir()
    result = _resolve_test_dir(tmp_path, tmp_path)
    assert result == str((tmp_path / "tests").resolve())


def test_resolve_test_dir_project_root_test_singular(tmp_path):
    """`test/` (no 's') is also a valid fallback."""
    (tmp_path / "test").mkdir()
    result = _resolve_test_dir(tmp_path, tmp_path)
    assert result == str((tmp_path / "test").resolve())


def test_resolve_test_dir_no_tests_returns_none(tmp_path):
    """No test dir anywhere → None (caller must hard-error)."""
    assert _resolve_test_dir(tmp_path, tmp_path) is None


# ---------------------------------------------------------------------------
# run_mutation_precheck — Bug: paths_to_mutate used as single literal path
# ---------------------------------------------------------------------------


def test_run_mutation_precheck_rejects_missing_paths(tmp_path, monkeypatch):
    """Bug: comma-separated paths_to_mutate was joined as one literal path,
    so src_dir.exists() was always False and the precheck silently returned
    (True, '') — bypassing the TDD-PRECHECK gate entirely.

    Fix: split on commas and fail on any missing entry.
    """
    import core.quality_gate.mutation_enforcer as me
    from core.utils.lang_patterns import project_language

    # Create a partial setup: core/ exists with foo.py, but bar.py is absent.
    core_dir = tmp_path / "core"
    core_dir.mkdir()
    (core_dir / "foo.py").write_text("", encoding="utf-8")
    # bar.py intentionally NOT created.

    (tmp_path / "setup.cfg").write_text(
        "[mutmut]\npaths_to_mutate = core/foo.py,core/bar.py\n",
        encoding="utf-8",
    )

    # Ensure mutmut is found so we don't early-return on the shutil.which check.
    monkeypatch.setattr("shutil.which", lambda _: "/bin/true")
    monkeypatch.setattr("core.utils.lang_patterns.project_language", lambda _: "python")

    result = me.run_mutation_precheck(tmp_path)

    # Must NOT silently return (True, ''); missing paths must be reported.
    assert result != (True, ""), (
        f"BUG: run_mutation_precheck silently returned (True, '') for "
        f"missing core/bar.py. Expected a non-empty failure message. Got: {result}"
    )
    # The fix reports which paths are missing.
    assert "missing" in result[1] or "bar.py" in result[1], (
        f"Expected failure message mentioning 'missing' or 'bar.py', got: {result}"
    )


def test_resolve_test_dir_subdir_override(tmp_path):
    """When mutmut cwd is a subdir (setup.cfg override), search
    tests/test at the subdir level, not project root."""
    sub = tmp_path / "03-development"
    (sub / "tests").mkdir(parents=True)
    # project-root tests/ exists but should NOT match — cwd is at sub.
    (tmp_path / "tests").mkdir()
    result = _resolve_test_dir(sub, tmp_path)
    assert result == str((sub / "tests").resolve())


def test_resolve_test_dir_subdir_override_fallback(tmp_path):
    """Subdir-override + no tests at subdir level → None.

    We deliberately do not fall back to project root tests/ because the
    override scenario assumes tests are siblings of the source.
    """
    sub = tmp_path / "03-development"
    sub.mkdir()
    (tmp_path / "tests").mkdir()
    assert _resolve_test_dir(sub, tmp_path) is None

def test_l1_mutmut_cache_persistence(tmp_path, monkeypatch):
    """Test L1: precheck starts with a FRESH workdir when no prior cache
    exists. The updated cache is post-copied back to the project.

    With Bug #42 fix, the contract is now:
    - If a prior .mutmut-cache existed in the project, it is stashed before
      mutmut starts and restored after mutmut finishes (the workdir
      contains a copy of the stashed cache, but mutmut's run will overwrite
      it with the fresh result, and the finally block restores the stash).
    - If no prior cache existed, the workdir starts empty; the post-copy
      from the workdir to the project root is the only way the project
      gains a new cache.

    This test exercises the "no prior cache" path (the simpler one).
    """
    import core.quality_gate.mutation_enforcer as me

    # Controlled workdir so we can inspect it during the fake subprocess call
    fake_workdir = tmp_path / "fake_workdir"
    fake_workdir.mkdir()

    mkdtemp_calls: list[str] = []
    stash_dir_path = tmp_path / "fake_stash"
    stash_dir_path.mkdir()
    def fake_mkdtemp(prefix="", **_kw):
        if prefix.startswith("_mutmut_cache_stash"):
            mkdtemp_calls.append(str(stash_dir_path))
            return str(stash_dir_path)
        mkdtemp_calls.append(str(fake_workdir))
        return str(fake_workdir)
    monkeypatch.setattr(me.tempfile, "mkdtemp", fake_mkdtemp)

    # No prior cache in project (intentionally do NOT create .mutmut-cache)

    # Bypass all the setup helpers
    (tmp_path / "src").mkdir()
    monkeypatch.setattr(me, "_resolve_mutmut_workdir", lambda _p: (tmp_path, "src"))
    monkeypatch.setattr(me, "_is_editable_install", lambda _p: False)
    monkeypatch.setattr(me, "_read_paths_to_exclude", lambda _p: [])
    monkeypatch.setattr(me, "_detect_data_only_files", lambda _p: [])
    monkeypatch.setattr(me, "_abs_paths_to_mutate", lambda _cwd, _paths: str(tmp_path / "src"))
    monkeypatch.setattr(me, "_resolve_test_dir", lambda _cwd, _p: str(tmp_path / "tests"))
    monkeypatch.setattr(me, "_copy_setup_cfg_to_workdir", lambda _p, _w, _td, **kw: None)
    monkeypatch.setattr(me.shutil, "which", lambda _cmd: "/usr/bin/mutmut")

    workdir_had_cache_before_run: list[bool] = []
    updated_cache = b"updated-cache-bytes"

    def fake_subprocess_run(cmd, **kwargs):
        class R:
            returncode = 0
            stdout = ""
            stderr = ""

        if cmd[0] == "mutmut" and len(cmd) > 1 and cmd[1] == "run":
            # Record whether workdir already had a cache (it must NOT — no
            # prior cache, no stash, fresh workdir).
            workdir_had_cache_before_run.append((fake_workdir / ".mutmut-cache").exists())
            # Simulate mutmut writing an updated cache after run
            (fake_workdir / ".mutmut-cache").write_bytes(updated_cache)
        return R()

    monkeypatch.setattr(me.subprocess, "run", fake_subprocess_run)

    ok, msg = me.run_mutation_precheck(tmp_path)
    assert ok, msg

    # Fresh run: workdir must NOT have had the old cache when mutmut started
    assert workdir_had_cache_before_run == [False], (
        "old .mutmut-cache was pre-copied into workdir — precheck must start fresh"
    )
    # Post-copy: updated cache was written back to project
    assert (tmp_path / ".mutmut-cache").read_bytes() == updated_cache, (
        "updated cache was not post-copied back to project"
    )


# ---------------------------------------------------------------------------
# Bug #42: stash/restore of .mutmut-cache around precheck
# ---------------------------------------------------------------------------


def test_run_mutation_precheck_promotes_workdir_cache_on_success(tmp_path, monkeypatch):
    """Pre-existing cache in project must be preserved after successful precheck."""
    import core.quality_gate.mutation_enforcer as me

    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    initial_cache = b"original-cache-bytes"
    (tmp_path / ".mutmut-cache").write_bytes(initial_cache)

    # Separate stash dir from workdir dir (the real code uses two distinct
    # tempfile.mkdtemp calls; this test simulates both).
    stash_dir_path = tmp_path / "fake_stash"
    stash_dir_path.mkdir()
    workdir_path = tmp_path / "fake_workdir"
    workdir_path.mkdir()

    mkdtemp_calls: list[str] = []
    def fake_mkdtemp(prefix="", **_kw):
        if prefix.startswith("_mutmut_cache_stash"):
            mkdtemp_calls.append(str(stash_dir_path))
            return str(stash_dir_path)
        mkdtemp_calls.append(str(workdir_path))
        return str(workdir_path)
    monkeypatch.setattr(me.tempfile, "mkdtemp", fake_mkdtemp)
    monkeypatch.setattr(me, "_resolve_mutmut_workdir", lambda _p: (tmp_path, "src"))
    monkeypatch.setattr(me, "_is_editable_install", lambda _p: False)
    monkeypatch.setattr(me, "_read_paths_to_exclude", lambda _p: [])
    monkeypatch.setattr(me, "_detect_data_only_files", lambda _p: [])
    monkeypatch.setattr(me, "_abs_paths_to_mutate", lambda _cwd, _p: str(tmp_path / "src"))
    monkeypatch.setattr(me, "_resolve_test_dir", lambda _cwd, _p: str(tmp_path / "tests"))
    monkeypatch.setattr(me, "_copy_setup_cfg_to_workdir", lambda _p, _w, _td, **kw: None)
    monkeypatch.setattr(me.shutil, "which", lambda _cmd: "/usr/bin/mutmut")

    def fake_run(cmd, **kwargs):
        class R:
            returncode = 0
            stdout = ""
            stderr = ""

        if cmd[0] == "mutmut" and cmd[1] == "run":
            (workdir_path / ".mutmut-cache").write_bytes(b"workdir-cache")
        return R()

    monkeypatch.setattr(me.subprocess, "run", fake_run)

    me.run_mutation_precheck(tmp_path)

    # Original cache replaced by workdir's output.
    assert (tmp_path / ".mutmut-cache").read_bytes() == b"workdir-cache", (
        "stashed cache was incorrectly restored instead of promoting workdir cache"
    )


def test_run_mutation_precheck_no_partial_cache_left_on_failure(tmp_path, monkeypatch):
    """If no prior cache existed and precheck fails, no partial cache should remain."""
    import core.quality_gate.mutation_enforcer as me

    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    # Intentionally do NOT create .mutmut-cache

    fake_workdir = tmp_path / "fake_workdir"
    fake_workdir.mkdir()

    monkeypatch.setattr(me.tempfile, "mkdtemp", lambda **_kw: str(fake_workdir))
    monkeypatch.setattr(me, "_resolve_mutmut_workdir", lambda _p: (tmp_path, "src"))
    monkeypatch.setattr(me, "_is_editable_install", lambda _p: False)
    monkeypatch.setattr(me, "_read_paths_to_exclude", lambda _p: [])
    monkeypatch.setattr(me, "_detect_data_only_files", lambda _p: [])
    monkeypatch.setattr(me, "_abs_paths_to_mutate", lambda _cwd, _p: str(tmp_path / "src"))
    monkeypatch.setattr(me, "_resolve_test_dir", lambda _cwd, _p: str(tmp_path / "tests"))
    monkeypatch.setattr(me, "_copy_setup_cfg_to_workdir", lambda _p, _w, _td, **kw: None)
    monkeypatch.setattr(me.shutil, "which", lambda _cmd: "/usr/bin/mutmut")

    def fake_run(cmd, **kwargs):
        class R:
            returncode = 99  # simulate mutmut failure
            stdout = ""
            stderr = "simulated crash"
        return R()

    monkeypatch.setattr(me.subprocess, "run", fake_run)

    ok, msg = me.run_mutation_precheck(tmp_path)
    assert not ok

    # No partial cache was left behind.
    assert not (tmp_path / ".mutmut-cache").exists(), (
        "partial .mutmut-cache was left behind after precheck failure"
    )


def test_apply_mutmut_to_workdir_runs_in_workdir(tmp_path, monkeypatch):
    """Bug #42 safety: mutmut apply must run in the workdir, not the project root."""
    import core.quality_gate.mutation_enforcer as me
    calls: dict = {}

    def fake_run(cmd, **kwargs):
        calls["cmd"] = cmd
        calls["kwargs"] = kwargs
        class R:
            returncode = 0
            stdout = ""
            stderr = ""
        return R()

    monkeypatch.setattr(me.subprocess, "run", fake_run)
    me._apply_mutmut_to_workdir(5, "/tmp/_mutmut_run.abc")
    assert calls["cmd"] == ["mutmut", "apply", "5"]
    assert calls["kwargs"]["cwd"] == "/tmp/_mutmut_run.abc"


# ---------------------------------------------------------------------------
# Bug #105: compute_mutation_score is the publish-side counterpart to
# run_mutation_precheck. finalize-gate's LLM agent runs `mutmut run` from
# project root, where Bug #91's workdir-isolated runner rewrite does not
# apply — on macOS Homebrew Python 3.11+ (no `python` symlink) mutmut 2.x
# crashes with FileNotFoundError. compute_mutation_score runs mutmut in a
# workdir (with the runner fix) AND promotes the cache to project root so
# downstream consumers can read `mutmut results` without rerunning.
# ---------------------------------------------------------------------------


def test_compute_mutation_score_promotes_cache_to_project_root(tmp_path, monkeypatch):
    """Bug #105: on success, workdir cache MUST be copied to project root."""
    import core.quality_gate.mutation_enforcer as me
    # ProjectLayout defaults to 03-development/src + 03-development/tests.
    src = tmp_path / "03-development" / "src" / "pkg"
    src.mkdir(parents=True)
    (src / "mod.py").write_text("def f():\n    return 1\n")
    tests = tmp_path / "03-development" / "tests"
    tests.mkdir()
    (tests / "test_x.py").write_text("def test_x(): pass\n")

    def fake_run(cmd, **kwargs):
        cwd = kwargs.get("cwd", "")
        is_run = isinstance(cmd, list) and len(cmd) >= 2 and cmd[1] == "run"
        is_results = isinstance(cmd, list) and len(cmd) >= 2 and cmd[1] == "results"
        if is_run and cwd.startswith("/tmp/_mutmut_score."):
            # Bug #108: score is now read from the sqlite cache, not
            # emoji counts. Populate the cache with 2 ok_killed + 1
            # bad_survived so the score is 2/3 * 100 = 66.7.
            _make_fake_mutmut_cache(
                Path(cwd) / ".mutmut-cache",
                ["ok_killed", "ok_killed", "bad_survived"],
            )
            return _R(0, "", "")
        if is_results:
            return _R(0, "Survived 🙁 (1)\n", "")
        return _R(0, "", "")

    monkeypatch.setattr(me.subprocess, "run", fake_run)
    monkeypatch.setattr(me.shutil, "which", lambda name: "/usr/bin/mutmut" if name == "mutmut" else None)

    ok, score, msg = me.compute_mutation_score(tmp_path)

    assert ok, f"compute_mutation_score returned not-ok: {msg}"
    assert score == pytest.approx(66.7), f"expected 2/3*100=66.7, got {score}"
    assert "killed=2" in msg and "survived=1" in msg
    # Bug #105 contract: cache promoted to project root.
    assert (tmp_path / ".mutmut-cache").exists()


def test_compute_mutation_score_does_not_promote_on_failure(tmp_path, monkeypatch):
    """Bug #105: if mutmut crashes, the project-root cache MUST stay untouched
    so callers can distinguish 'we ran mutmut and it failed' from 'we have
    valid prior results'."""
    import core.quality_gate.mutation_enforcer as me

    src = tmp_path / "03-development" / "src" / "pkg"
    src.mkdir(parents=True)
    (src / "mod.py").write_text("def f():\n    return 1\n")
    tests = tmp_path / "03-development" / "tests"
    tests.mkdir()
    (tests / "test_x.py").write_text("def test_x(): pass\n")

    def fake_run(cmd, **kwargs):
        return _R(1, "boom", "mutmut crashed")

    monkeypatch.setattr(me.subprocess, "run", fake_run)
    monkeypatch.setattr(me.shutil, "which", lambda name: "/usr/bin/mutmut" if name == "mutmut" else None)

    ok, score, _msg = me.compute_mutation_score(tmp_path)

    assert not ok
    assert score == 0.0
    assert not (tmp_path / ".mutmut-cache").exists()


# ---------------------------------------------------------------------------
# Bugs 5 + 6: multi-value testpaths / pythonpath (space-separated)
# ---------------------------------------------------------------------------


def test_testpaths_multi_value_not_joined_as_single_path(tmp_path):
    """Bug 5: testpaths = 'tests other_tests' must not be joined as one bogus path.

    pytest accepts space-separated testpaths (valid INI syntax). The bug joins
    the whole string as a single relative path, producing the non-existent
    '<cfg_dir>/tests other_tests' — pytest then finds nothing.
    """
    import core.quality_gate.mutation_enforcer as me

    cfg = tmp_path / "setup.cfg"
    cfg.write_text("[tool:pytest]\ntestpaths = tests other_tests\n", encoding="utf-8")
    # Create both directories so the split-resolve produces real absolute paths.
    (tmp_path / "tests").mkdir()
    (tmp_path / "other_tests").mkdir()
    workdir = tmp_path / "workdir"
    workdir.mkdir()
    me._copy_setup_cfg_to_workdir(tmp_path, str(workdir), str(tmp_path / "tests"))
    written = (workdir / "setup.cfg").read_text()
    # Must not contain the bogus joined path.
    assert "tests other_tests" not in written, (
        f"Multi-value testpaths wrongly joined as a single literal path: {written}"
    )


def test_pythonpath_multi_value_not_left_broken(tmp_path):
    """Bug 6: pythonpath = 'src lib' must not be left as a broken relative path.

    pytest accepts space-separated pythonpath entries. The bug treats the
    whole string as one path, resolves it as '<cfg_dir>/src lib' (which does
    not exist), and leaves the broken string in place — causing
    ModuleNotFoundError for both packages.
    """
    import core.quality_gate.mutation_enforcer as me

    cfg = tmp_path / "setup.cfg"
    cfg.write_text("[tool:pytest]\npythonpath = src lib\n", encoding="utf-8")
    # Create both directories so split-resolve produces real absolute paths.
    (tmp_path / "src").mkdir()
    (tmp_path / "lib").mkdir()
    workdir = tmp_path / "workdir"
    workdir.mkdir()
    me._copy_setup_cfg_to_workdir(tmp_path, str(workdir), str(tmp_path / "tests"))
    written = (workdir / "setup.cfg").read_text()
    # Must not leave the broken relative "src lib" string unchanged.
    assert "pythonpath = src lib" not in written, (
        f"Multi-value pythonpath left as broken relative string: {written}"
    )


# ---------------------------------------------------------------------------
# Bug 7: _copy_setup_cfg_to_workdir ignores nested cwd from _resolve_mutmut_workdir
# ---------------------------------------------------------------------------


def test_copy_setup_cfg_uses_nested_cwd_setup_cfg_not_project_root(tmp_path):
    """Bug 7: when _resolve_mutmut_workdir returns a nested cwd that has its
    own setup.cfg, _copy_setup_cfg_to_workdir MUST read that nested setup.cfg,
    not always the project-root one.

    Real scenario: project root has a minimal setup.cfg (no [tool:pytest]
    pythonpath), but 03-development/setup.cfg has pythonpath = src.
    _resolve_mutmut_workdir detects the nested [mutmut] section and returns
    cwd=03-development/. _copy_setup_cfg_to_workdir must use 03-development/
    as the config source so pythonpath is preserved in the workdir copy.
    """
    import core.quality_gate.mutation_enforcer as me

    # Root setup.cfg: has [mutmut] but no pythonpath — the bug would use this.
    (tmp_path / "setup.cfg").write_text(
        "[mutmut]\npaths_to_mutate = 03-development/src\n",
        encoding="utf-8",
    )
    # Nested cwd has a setup.cfg with pythonpath.
    nested = tmp_path / "03-development"
    nested.mkdir()
    (nested / "setup.cfg").write_text(
        "[tool:pytest]\npythonpath = src\n",
        encoding="utf-8",
    )
    src_dir = nested / "src"
    src_dir.mkdir()
    tests_dir = nested / "tests"
    tests_dir.mkdir()

    # Simulate what run_mutation_precheck does: pass cwd=nested.
    workdir = tmp_path / "workdir"
    workdir.mkdir()
    me._copy_setup_cfg_to_workdir(tmp_path, str(workdir), str(tests_dir), cwd=nested)

    cp = configparser.ConfigParser()
    cp.read(str(workdir / "setup.cfg"), encoding="utf-8")
    # The workdir setup.cfg must contain the pythonpath from the nested
    # 03-development/setup.cfg, NOT whatever the root setup.cfg has.
    # If Bug 7 is present, pythonpath will be absent or come from root.
    assert cp.has_option("tool:pytest", "pythonpath"), (
        f"pythonpath missing from workdir setup.cfg — "
        f"_copy_setup_cfg_to_workdir read project-root setup.cfg instead of "
        f"the nested cwd's setup.cfg"
    )
    assert cp["tool:pytest"]["pythonpath"] == str(src_dir.resolve()), (
        f"pythonpath should be resolved to {src_dir.resolve()}, "
        f"got {cp['tool:pytest']['pythonpath']!r}"
    )


# Bug #105 (follow-up): when workdir cache never materializes (all source
# excluded), a pre-existing project-root cache must be deleted so downstream
# LLM agents cannot read stale scores.
def test_stale_cache_removed_when_workdir_cache_absent(tmp_path, monkeypatch):
    """When workdir cache never materializes, a stale project-root cache must be deleted."""
    import core.quality_gate.mutation_enforcer as me

    # Pre-existing stale cache at project root.
    stale_cache = tmp_path / ".mutmut-cache"
    stale_cache.write_text("old stale data")

    # Minimal project layout.
    src = tmp_path / "03-development" / "src" / "pkg"
    src.mkdir(parents=True)
    (src / "mod.py").write_text("def f():\n    return 1\n")
    tests = tmp_path / "03-development" / "tests"
    tests.mkdir()
    (tests / "test_x.py").write_text("def test_x(): pass\n")

    # Fake mutmut run that produces 0 mutants (all excluded → no cache created
    # in workdir). The workdir cache (.mutmut-cache) will NOT exist.
    class FakeRes:
        returncode = 0
        stdout = "TotalMutants = 0\n"
        stderr = ""

    def fake_run(cmd, *args, **kwargs):
        if isinstance(cmd, list) and len(cmd) >= 2 and cmd[1] == "results":
            return FakeRes()
        class R:
            returncode = 0; stdout = ""; stderr = ""
        return R()

    monkeypatch.setattr(me.subprocess, "run", fake_run)
    monkeypatch.setattr(me.shutil, "which", lambda name: "/usr/bin/mutmut" if name == "mutmut" else None)

    ok, score, msg = me.compute_mutation_score(tmp_path)

    assert ok, f"compute_mutation_score returned not-ok: {msg}"
    assert not stale_cache.exists(), (
        f"Stale cache should be deleted, but still exists at {stale_cache}"
    )


def test_compute_mutation_score_uses_sys_executable_for_runner(tmp_path, monkeypatch):
    """Bug #91 / #105: setup.cfg rewrite pins the runner to sys.executable so
    mutmut 2.x's hardcoded `python` fallback never gets a chance to crash on
    macOS Homebrew Python 3.11+ (no `python` symlink)."""
    import core.quality_gate.mutation_enforcer as me

    src = tmp_path / "03-development" / "src" / "pkg"
    src.mkdir(parents=True)
    (src / "mod.py").write_text("def f():\n    return 1\n")
    tests = tmp_path / "03-development" / "tests"
    tests.mkdir()
    (tests / "test_x.py").write_text("def test_x(): pass\n")
    (tmp_path / "setup.cfg").write_text("[metadata]\nname=proj\n")

    captured_setup_cfgs: list = []

    def fake_run(cmd, **kwargs):
        cwd = kwargs.get("cwd", "")
        is_run = isinstance(cmd, list) and len(cmd) >= 2 and cmd[1] == "run"
        if is_run and cwd.startswith("/tmp/_mutmut_score."):
            cfg_text = (Path(cwd) / "setup.cfg").read_text()
            captured_setup_cfgs.append(cfg_text)
        return _R(0, "", "")

    monkeypatch.setattr(me.subprocess, "run", fake_run)
    monkeypatch.setattr(me.shutil, "which", lambda name: "/usr/bin/mutmut" if name == "mutmut" else None)

    me.compute_mutation_score(tmp_path)

    assert captured_setup_cfgs, "workdir setup.cfg was never created"
    workdir_cfg = captured_setup_cfgs[0]
    # Bug #91 contract: runner must be sys.executable, not bare `python`.
    # ConfigParser writes with spaces around `=`, so match accordingly.
    assert f"runner = {sys.executable} -m pytest" in workdir_cfg, (
        f"runner not pinned to sys.executable; got:\n{workdir_cfg}"
    )


def test_compute_mutation_score_no_mutmut_returns_zero(tmp_path, monkeypatch):
    """Bug #105: if mutmut is not installed, return (False, 0.0, msg) cleanly
    rather than crashing. Gate prompt can then surface a blocking message."""
    import core.quality_gate.mutation_enforcer as me

    monkeypatch.setattr(me.shutil, "which", lambda name: None if name == "mutmut" else "/usr/bin/python3")

    ok, score, msg = me.compute_mutation_score(tmp_path)
    assert not ok
    assert score == 0.0
    assert "mutmut not installed" in msg.lower()


def test_cmd_mutation_test_score_exits_zero_on_success(tmp_path, monkeypatch):
    """Bug #105: the CLI command must exit 0 on success and print machine-readable JSON."""
    import core.quality_gate.mutation_enforcer as me
    from harness_cli import cmd_mutation_test_score

    src = tmp_path / "03-development" / "src" / "pkg"
    src.mkdir(parents=True)
    (src / "mod.py").write_text("def f():\n    return 1\n")
    tests = tmp_path / "03-development" / "tests"
    tests.mkdir()
    (tests / "test_x.py").write_text("def test_x(): pass\n")

    def fake_run(cmd, **kwargs):
        cwd = kwargs.get("cwd", "")
        is_run = isinstance(cmd, list) and len(cmd) >= 2 and cmd[1] == "run"
        is_results = isinstance(cmd, list) and len(cmd) >= 2 and cmd[1] == "results"
        if is_run and cwd.startswith("/tmp/_mutmut_score."):
            # Bug #108: score is now read from the sqlite cache.
            # 1 ok_killed + 2 bad_survived → 1/3 * 100 = 33.3.
            _make_fake_mutmut_cache(
                Path(cwd) / ".mutmut-cache",
                ["ok_killed", "bad_survived", "bad_survived"],
            )
            return _R(0, "", "")
        if is_results:
            return _R(0, "Survived 🙁 (2)\n", "")
        return _R(0, "", "")

    monkeypatch.setattr(me.subprocess, "run", fake_run)
    monkeypatch.setattr(me.shutil, "which", lambda name: "/usr/bin/mutmut" if name == "mutmut" else None)

    import argparse
    import io
    import contextlib
    args = argparse.Namespace(project=str(tmp_path))
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = cmd_mutation_test_score(args)
    out = buf.getvalue().strip()
    assert rc == 0, f"expected exit 0, got {rc}; output={out!r}"
    payload = json.loads(out)
    assert payload["success"] is True
    assert payload["score"] == pytest.approx(33.3)
    assert payload["cache_path"] == str(tmp_path / ".mutmut-cache")


class _R:
    """Lightweight stand-in for subprocess.CompletedProcess (avoids the
    'R redefined inside a function' Pyright warning)."""
    def __init__(self, returncode: int, stdout: str, stderr: str) -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


# ---------------------------------------------------------------------------
# _find_source_setup_cfg (Bug #106b: nested layout discovery)
# ---------------------------------------------------------------------------


def test_find_source_setup_cfg_returns_root_when_present(tmp_path):
    """Bug #106b: root-level setup.cfg wins when both exist."""
    project = tmp_path / "proj"
    project.mkdir()
    root_cfg = project / "setup.cfg"
    root_cfg.write_text("[tool:pytest]\n", encoding="utf-8")
    (project / "03-development").mkdir()
    (project / "03-development" / "setup.cfg").write_text(
        "[tool:pytest]\npythonpath = src\n", encoding="utf-8"
    )
    assert _find_source_setup_cfg(project) == root_cfg


def test_find_source_setup_cfg_falls_back_to_nested(tmp_path):
    """Bug #106b: when no root setup.cfg, use 03-development/setup.cfg.

    This is the actual production case: integration-test has the project's
    setup.cfg under 03-development/ (the ProjectLayout.phase3_development_dir),
    not at project root.
    """
    project = tmp_path / "proj"
    project.mkdir()
    (project / "03-development").mkdir()
    nested_cfg = project / "03-development" / "setup.cfg"
    nested_cfg.write_text("[tool:pytest]\npythonpath = src\n", encoding="utf-8")
    assert _find_source_setup_cfg(project) == nested_cfg


def test_find_source_setup_cfg_returns_none_when_neither_exists(tmp_path):
    """Bug #106b: no setup.cfg anywhere returns None (caller falls through to
    the 'no setup.cfg' branch that generates a minimal config)."""
    project = tmp_path / "proj"
    project.mkdir()
    assert _find_source_setup_cfg(project) is None


def test_copy_setup_cfg_reads_nested_layout_and_promotes_pythonpath_bug_106b(tmp_path):
    """Bug #106b: end-to-end — a project with setup.cfg under 03-development/
    has its pythonpath preserved (not dropped) when copied to workdir.

    This is the regression that broke the integration-test finalize-gate:
    the function used to look for project/setup.cfg only, hit the
    'no setup.cfg' branch, and generated a minimal config without
    pythonpath = src, causing ModuleNotFoundError on taskq.
    """
    project = tmp_path / "proj"
    project.mkdir()
    dev = project / "03-development"
    dev.mkdir()
    src_dir = dev / "src"
    src_dir.mkdir()
    (src_dir / "taskq").mkdir()
    (src_dir / "taskq" / "__init__.py").write_text("", encoding="utf-8")
    (dev / "setup.cfg").write_text(
        "[tool:pytest]\npythonpath = src\naddopts = -ra\n",
        encoding="utf-8",
    )
    tests_dir = dev / "tests"
    tests_dir.mkdir()
    workdir = project / "workdir"
    workdir.mkdir()
    _copy_setup_cfg_to_workdir(project, str(workdir), str(tests_dir))
    cp = configparser.ConfigParser()
    cp.read(str(workdir / "setup.cfg"), encoding="utf-8")
    # pythonpath must be promoted to absolute (Bug #106)
    assert cp["tool:pytest"]["pythonpath"] == str(src_dir.resolve())
    # addopts must be preserved (we only rewrite specific keys)
    assert cp["tool:pytest"]["addopts"] == "-ra"


# ---------------------------------------------------------------------------
# _count_mutmut_results (Bug #108: read score from sqlite cache)
# ---------------------------------------------------------------------------


def _make_fake_mutmut_cache(path: Path, statuses: list[str]) -> None:
    """Create a fake mutmut 2.x cache with the right schema and the given
    Mutant.status rows. Mirrors what mutmut 2.x actually writes.
    """
    import sqlite3
    db = sqlite3.connect(str(path))
    db.executescript(
        """
        CREATE TABLE SourceFile (id INTEGER PRIMARY KEY, filename TEXT);
        CREATE TABLE Mutant (
            id INTEGER PRIMARY KEY,
            srcfile_id INTEGER,
            line INTEGER,
            column INTEGER,
            status TEXT
        );
        INSERT INTO SourceFile VALUES (1, 'fake.py');
        """
    )
    for i, status in enumerate(statuses, start=1):
        db.execute(
            "INSERT INTO Mutant VALUES (?, ?, ?, ?, ?)",
            (i, 1, 1, 0, status),
        )
    db.commit()
    db.close()


def test_count_mutmut_results_typical_mix_bug_108(tmp_path):
    """Bug #108: typical mix of ok_killed and bad_survived is counted
    correctly. The previous emoji-counting logic would report 0 killed
    because mutmut results only prints 🙁 for survivors.
    """
    cache = tmp_path / ".mutmut-cache"
    _make_fake_mutmut_cache(
        cache,
        ["ok_killed"] * 113 + ["bad_survived"] * 115,
    )
    killed, survived = _count_mutmut_results(cache)
    assert killed == 113
    assert survived == 115


def test_count_mutmut_results_timeout_and_suspicious_count_as_survived(tmp_path):
    """Bug #108: per evaluate_dimension.md, timeout and suspicious mutants
    count as survived (the test infrastructure couldn't definitively kill
    them in reasonable time).
    """
    cache = tmp_path / ".mutmut-cache"
    _make_fake_mutmut_cache(
        cache,
        ["ok_killed", "bad_survived", "timeout", "suspicious"],
    )
    killed, survived = _count_mutmut_results(cache)
    assert killed == 1
    assert survived == 3


def test_count_mutmut_results_ignores_pending_and_infra_failures(tmp_path):
    """Bug #108: pending/checking/no_tests/skipped/check_failed are
    infrastructure states, not mutant verdicts. They should be ignored
    (not counted as killed OR survived).
    """
    cache = tmp_path / ".mutmut-cache"
    _make_fake_mutmut_cache(
        cache,
        ["ok_killed", "pending", "checking", "no_tests", "skipped", "check_failed"],
    )
    killed, survived = _count_mutmut_results(cache)
    assert killed == 1
    assert survived == 0


def test_count_mutmut_results_missing_cache_returns_zeros(tmp_path):
    """Bug #108: missing cache file → (0, 0). Caller treats as no score."""
    cache = tmp_path / "does-not-exist"
    killed, survived = _count_mutmut_results(cache)
    assert killed == 0
    assert survived == 0


# ---------------------------------------------------------------------------
# Bug: mutmut results returncode never checked
# ---------------------------------------------------------------------------


def test_mutmut_results_crash_returns_false(tmp_path, monkeypatch):
    """mutmut results returning non-zero must be treated as a precheck failure.

    A crashed mutmut results subprocess (returncode=1, empty stdout, non-empty
    stderr) was treated as a clean precheck pass because `out = res.stdout.strip()`
    evaluated to '' and `if out:` was False, causing `_precheck_ok = True`.
    The fix checks `res.returncode != 0` before reading stdout.
    """
    import core.quality_gate.mutation_enforcer as me

    # Minimal project layout so run_mutation_precheck doesn't early-exit.
    src = tmp_path / "03-development" / "src"
    src.mkdir(parents=True)
    tests = tmp_path / "03-development" / "tests"
    tests.mkdir(parents=True)
    (tests / "test_x.py").write_text("def test_x(): pass\n")

    def fake_run(cmd, **kwargs):
        if isinstance(cmd, list) and len(cmd) >= 2 and cmd[1] == "results":
            # mutmut results crashes: this is the bug we're testing
            class R:
                returncode = 1
                stdout = ""
                stderr = "mutmut: error: no results yet"
            return R()
        # mutmut run succeeds
        class R:
            returncode = 0
            stdout = ""
            stderr = ""
        return R()

    monkeypatch.setattr(me.subprocess, "run", fake_run)
    monkeypatch.setattr(me, "_write_survivors_artifact", lambda *a, **k: None)
    monkeypatch.setattr(me.shutil, "which", lambda name: "/usr/bin/mutmut" if name == "mutmut" else None)
    monkeypatch.setattr(me, "_resolve_mutmut_workdir", lambda _p: (tmp_path, "03-development/src"))
    monkeypatch.setattr(me, "_is_editable_install", lambda _p: False)
    monkeypatch.setattr(me, "_read_paths_to_exclude", lambda _p: [])
    monkeypatch.setattr(me, "_detect_data_only_files", lambda _p: [])
    monkeypatch.setattr(me, "_abs_paths_to_mutate", lambda _cwd, _paths: str(src))
    monkeypatch.setattr(me, "_resolve_test_dir", lambda _cwd, _p: str(tests))
    monkeypatch.setattr(me, "_copy_setup_cfg_to_workdir", lambda _p, _w, _td, **kw: None)

    ok, msg = me.run_mutation_precheck(tmp_path)
    assert ok is False, f"Expected False for crashed mutmut results, got ({ok}, {msg!r})"
    assert "return code 1" in msg, f"Expected error message to mention return code, got: {msg!r}"


# Bug: corrupt sqlite cache indistinguishable from zero mutants
# ---------------------------------------------------------------------------


def test_zero_mutants_from_corrupt_cache_returns_false(tmp_path, monkeypatch):
    """Zero mutants from sqlite but non-zero from text output must fail.

    When `_count_mutmut_results` returns (0, 0) because the sqlite cache is
    corrupt or unreadable, but `mutmut results` stdout contains a non-zero
    TotalMutants line, `compute_mutation_score` must return False — not a false
    clean pass — so that a corrupt cache cannot report a passing score.
    """
    import core.quality_gate.mutation_enforcer as me

    src = tmp_path / "03-development" / "src"
    src.mkdir(parents=True)
    tests = tmp_path / "03-development" / "tests"
    tests.mkdir(parents=True)
    (tests / "test_x.py").write_text("def test_x(): pass\n")

    def fake_count(*a, **k):
        return (0, 0)  # sqlite says 0 (corrupt cache)

    class FakeRes:
        returncode = 0
        stdout = "TotalMutants = 42\nSurvived(3)"
        stderr = ""

    def fake_run(cmd, *args, **kwargs):
        if isinstance(cmd, list) and len(cmd) >= 2 and cmd[1] == "results":
            return FakeRes()
        class R:
            returncode = 0
            stdout = ""
            stderr = ""
        return R()

    monkeypatch.setattr(me, "_count_mutmut_results", fake_count)
    monkeypatch.setattr(me.subprocess, "run", fake_run)
    monkeypatch.setattr(me, "_write_survivors_artifact", lambda *a, **k: None)
    monkeypatch.setattr(me.shutil, "which", lambda name: "/usr/bin/mutmut" if name == "mutmut" else None)
    monkeypatch.setattr(me, "_resolve_mutmut_workdir", lambda _p: (tmp_path, "03-development/src"))
    monkeypatch.setattr(me, "_is_editable_install", lambda _p: False)
    monkeypatch.setattr(me, "_read_paths_to_exclude", lambda _p: [])
    monkeypatch.setattr(me, "_detect_data_only_files", lambda _p: [])
    monkeypatch.setattr(me, "_abs_paths_to_mutate", lambda _cwd, _paths: str(src))
    monkeypatch.setattr(me, "_resolve_test_dir", lambda _cwd, _p: str(tests))
    monkeypatch.setattr(me, "_copy_setup_cfg_to_workdir", lambda _p, _w, _td, **kw: None)

    ok, score, msg = me.compute_mutation_score(tmp_path)
    assert ok is False, f"Expected False for corrupt-cache mismatch, got ({ok}, {score}, {msg!r})"
    assert "42" in msg, f"Expected error message to mention text-output total 42, got: {msg!r}"
