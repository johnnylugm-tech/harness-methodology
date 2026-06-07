"""Unit tests for ``core.quality_gate.mutation_enforcer``.

These tests focus on the path-resolution helpers and the public entry
point's contract (without actually invoking mutmut). End-to-end mutmut
behaviour is covered by the agent's gate-evaluation protocol in
``evaluate_dimension.md``, not here.
"""
from core.quality_gate.mutation_enforcer import (
    _resolve_test_dir,
    _resolve_mutmut_workdir,
    _abs_paths_to_mutate,
    _read_paths_to_exclude,
    _detect_data_only_files,
    _copy_setup_cfg_to_workdir,
    _paths_to_exclude_flag,
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
    _copy_setup_cfg_to_workdir(tmp_path, str(workdir))
    assert (workdir / "setup.cfg").read_text(encoding="utf-8") == \
        "[mutmut]\npaths_to_mutate = 03-development/src\n"


def test_copy_setup_cfg_to_workdir_no_setup_cfg(tmp_path):
    """No setup.cfg → no error, no file copied."""
    workdir = tmp_path / "workdir"
    workdir.mkdir()
    _copy_setup_cfg_to_workdir(tmp_path, str(workdir))
    assert not (workdir / "setup.cfg").exists()


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
