"""Mutation testing FSM enforcer.

Implements the full mutmut protocol from
``harness/ssi/prompts/evaluate_dimension.md`` §mutation_testing:

* ``-b 10`` baseline budget (Bug F)
* editable-install detection → hard block (Bug F)
* cwd isolation via ``mktemp -d`` (Bug F)
* absolute testpaths in temp ``setup.cfg`` (Bug F)
* data-only file auto-exclusion (Bug F)
* ``paths_to_exclude`` from ``setup.cfg`` passed via CLI (Bug G)
"""
import configparser
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional
from core.utils.project_layout import ProjectLayout

# Basenames that are almost certainly data-only (no logic to mutate).
_DATA_ONLY_NAMES: frozenset[str] = frozenset({
    "config.py", "constants.py", "settings.py", "__init__.py",
})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve_mutmut_workdir(project: Path) -> tuple[Path, str]:
    """Return ``(cwd, paths_to_mutate)`` resolved from project ``setup.cfg``.

    Reads ``[mutmut] paths_to_mutate`` from the project-root ``setup.cfg``.
    If that path lives inside a subdirectory that has its own ``setup.cfg``
    with a ``[mutmut]`` section (e.g. for ``paths_to_exclude`` overrides),
    the working directory is switched to that subdirectory so mutmut picks
    up the local config.  Falls back to ``03-development/src`` when no
    ``[mutmut]`` section exists at all.
    """
    root_cfg = configparser.ConfigParser()
    root_cfg.read(str(project / "setup.cfg"))

    layout = ProjectLayout(project)
    default_paths = layout.get_relative_str(layout.phase3_development_dir / "src")
    paths: str = default_paths
    if root_cfg.has_section("mutmut"):
        paths = root_cfg.get("mutmut", "paths_to_mutate", fallback=default_paths)

    cwd = project
    parts = Path(paths).parts
    # If the configured path is nested (e.g. 03-development/src), check
    # whether the top-level subdirectory carries its own [mutmut] config.
    if len(parts) > 1:
        sub_project = project / parts[0]
        sub_cfg = configparser.ConfigParser()
        sub_cfg.read(str(sub_project / "setup.cfg"))
        if sub_cfg.has_section("mutmut"):
            cwd = sub_project
            paths = str(Path(*parts[1:]))

    return cwd, paths


def _is_editable_install(project: Path) -> bool:
    """Check whether *project* is installed in editable (``pip install -e``) mode.

    Editable installs place a ``.pth`` file in site-packages pointing back to
    the original source.  When mutmut copies mutated code to a temp dir,
    Python resolves imports via the ``.pth`` file → mutations are never tested.
    """
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "list", "--editable", "--format", "json"],
            capture_output=True, text=True, timeout=30,
            cwd=str(project),
        )
        if result.returncode != 0 or not result.stdout.strip():
            return False
        packages = json.loads(result.stdout)
        project_path = project.resolve()
        for pkg in packages:
            loc = (
                pkg.get("editable_project_location")
                or pkg.get("location")
                or ""
            )
            if loc and Path(loc).resolve() == project_path:
                return True
        return False
    except Exception:
        return False


def _read_paths_to_exclude(cwd: Path) -> list[str]:
    """Read ``paths_to_exclude`` from ``[mutmut]`` in ``setup.cfg``.

    ConfigParser returns the value as a single string; mutmut 2.x iterates
    its characters instead of splitting on whitespace → effectively broken.
    We read it ourselves, split by whitespace, and pass individual
    ``--paths-to-exclude`` CLI options (Bug G).
    """
    cfg = configparser.ConfigParser()
    cfg.read(str(cwd / "setup.cfg"))
    if not cfg.has_section("mutmut"):
        return []
    raw = cfg.get("mutmut", "paths_to_exclude", fallback="")
    return raw.split() if raw.strip() else []


def _detect_data_only_files(src_dir: Path) -> list[str]:
    """Auto-detect data-only ``.py`` files that have no mutate-able logic.

    Returns **basenames** (mutmut matches ``paths_to_exclude`` on basename).
    Files matching ``_DATA_ONLY_NAMES`` are excluded immediately; for the
    rest a heuristic counts logic-keyword lines at any indentation level.
    """
    excludes: list[str] = []
    # ``\s+`` covers 2-space, 4-space, and tab-indented code.
    _logic_re = re.compile(
        r"^\s+(if |for |while |with |return |raise |try:|except |assert )",
        re.MULTILINE,
    )
    for py_file in src_dir.rglob("*.py"):
        basename = py_file.name
        if basename in _DATA_ONLY_NAMES:
            excludes.append(basename)
            continue
        try:
            text = py_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if not _logic_re.search(text):
            excludes.append(basename)
    return sorted(set(excludes))


def _abs_paths_to_mutate(cwd: Path, paths_to_mutate: str) -> str:
    """Convert comma-separated relative paths to absolute, comma-separated."""
    parts = [p.strip() for p in paths_to_mutate.split(",") if p.strip()]
    return ",".join(str((cwd / p).resolve()) for p in parts)


def _resolve_test_dir(cwd: Path, project: Path) -> Optional[str]:
    """Return the absolute path to the test directory, relative to *cwd*.

    *cwd* is the resolved mutmut working directory — either the project
    root (no override) or a subdirectory (when ``setup.cfg`` subdir override
    is active). Tests always live as siblings of the source, so we search
    relative to *cwd*. Candidate lists come from ``ProjectLayout`` so the
    path layout is centralised.
    """
    layout = ProjectLayout(project)
    cwd_resolved = cwd.resolve()
    project_resolved = project.resolve()
    if cwd_resolved == project_resolved:
        candidates: list[Path] = layout.root_test_dir_candidates
    else:
        candidates = ProjectLayout.subdir_test_dirs(cwd)
    for candidate in candidates:
        if candidate.is_dir():
            return str(candidate.resolve())
    return None


def _copy_setup_cfg_to_workdir(project: Path, workdir: str) -> None:
    """Copy ``setup.cfg`` into *workdir* so mutmut's setup.cfg lookup succeeds.

    mutmut reads ``paths_to_mutate`` from ``[mutmut]`` in setup.cfg relative
    to cwd — without a copy in *workdir* it falls back to defaults.
    """
    setup_cfg = project / "setup.cfg"
    if setup_cfg.exists():
        (Path(workdir) / "setup.cfg").write_text(
            setup_cfg.read_text(encoding="utf-8"),
            encoding="utf-8",
        )


def _paths_to_exclude_flag(excludes: list[str]) -> str:
    """Build a single ``--paths-to-exclude=...`` flag for mutmut 2.x.

    mutmut's CLI defines this option as ``type=click.STRING`` (no
    ``multiple=True``), so multiple flags on the command line would
    collapse to the last value. mutmut itself splits the string on ``,``
    and ``\\n`` at parse time, so we comma-join all excludes here.
    """
    return f"--paths-to-exclude={','.join(excludes)}"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def run_mutation_precheck(project: Path) -> tuple[bool, str]:
    """Run mutmut and enforce no surviving mutants.

    Implements the full protocol from ``evaluate_dimension.md``:
    editable-install detection, ``-b 10`` baseline budget, cwd isolation
    via temp dir, absolute testpaths, data-only file auto-exclusion, and
    ``paths_to_exclude`` CLI passthrough (Bug G).
    """
    if not shutil.which("mutmut"):
        return False, (
            "mutmut not installed. Required for TDD-PRECHECK. "
            "Install: pip install mutmut"
        )

    cwd, paths_to_mutate = _resolve_mutmut_workdir(project)

    src_dir = cwd / paths_to_mutate
    if not src_dir.exists():
        return True, ""

    # --- Bug F: editable install detection ---
    if _is_editable_install(project):
        return False, (
            "Project is installed in editable mode (pip install -e). "
            "This prevents mutmut from testing mutations — Python resolves "
            "imports to the original (unmutated) source via .pth files.\n"
            "Fix:  pip install .  (non-editable, regular install)\n"
            "Then re-run TDD-PRECHECK."
        )

    # --- Bug G: read paths_to_exclude from setup.cfg (split properly) ---
    declared_excludes = _read_paths_to_exclude(cwd)

    # --- Bug F: auto-detect data-only files ---
    auto_excludes = _detect_data_only_files(src_dir)
    # Declared excludes take precedence — don't duplicate.
    auto_excludes = [e for e in auto_excludes if e not in frozenset(declared_excludes)]

    # --- Bug F: absolute paths (temp-dir cwd isolation) ---
    abs_mutate = _abs_paths_to_mutate(cwd, paths_to_mutate)

    workdir = tempfile.mkdtemp(prefix="_mutmut_run.", dir="/tmp")
    try:
        _copy_setup_cfg_to_workdir(project, workdir)

        cmd = [
            "mutmut", "run",
            f"--paths-to-mutate={abs_mutate}",
            "-b", "10",                     # Bug F: baseline budget
        ]
        # Bug fix: mutmut 2.x does NOT read [tool:pytest] testpaths — it
        # needs --tests-dir with an absolute path. Without this the
        # test-discovery code crashes with FileNotFoundError in the temp
        # workdir that has no tests/ subdirectory.
        test_dir = _resolve_test_dir(cwd, project)
        if test_dir is None:
            return False, (
                "No test directory found. Searched for tests/, test/, "
                "03-development/tests/ relative to the mutmut workdir. "
                "Mutation testing is meaningless without tests — cannot proceed."
            )
        cmd.append(f"--tests-dir={test_dir}")
        all_excludes = declared_excludes + auto_excludes
        if all_excludes:
            cmd.append(_paths_to_exclude_flag(all_excludes))

        r = subprocess.run(
            cmd, cwd=workdir, capture_output=True, text=True,
            timeout=3600,  # 60 min hard cap — mutation testing is meaningless if it hangs
        )

        if r.returncode != 0:
            return False, (
                f"mutmut run crashed (return code {r.returncode}).\n\n"
                f"STDOUT:\n{r.stdout.strip()}\n\n"
                f"STDERR:\n{r.stderr.strip()}"
            )

        res = subprocess.run(
            ["mutmut", "results"], cwd=workdir, capture_output=True, text=True,
            timeout=30,
        )

        out = res.stdout.strip()
        if out:
            m = re.search(r"Survived[^(]*\((\d+)\)", out)
            if m and int(m.group(1)) > 0:
                return False, (
                    f"Mutation testing failed: {m.group(1)} surviving mutant(s) found.\n\n"
                    f"{out}"
                )

        return True, ""
    except subprocess.TimeoutExpired:
        return False, (
            "mutmut timed out after 60 minutes. "
            "The test suite may be too slow per mutant, or a mutant caused "
            "an infinite loop. Consider excluding data-only files via "
            "paths_to_exclude in setup.cfg to reduce the mutant count."
        )
    except Exception as e:
        return False, f"Error running mutmut: {e}"
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
