"""Mutation testing FSM enforcer."""
import configparser
import re
import subprocess
import shutil
from pathlib import Path

HARDCODED_FALLBACK = "03-development/src"


def _resolve_mutmut_workdir(project: Path) -> tuple[Path, str]:
    """Return (cwd, paths_to_mutate) resolved from project setup.cfg.

    Reads ``[mutmut] paths_to_mutate`` from the project-root ``setup.cfg``.
    If that path lives inside a subdirectory that has its own ``setup.cfg``
    with a ``[mutmut]`` section (e.g. for ``paths_to_exclude`` overrides),
    the working directory is switched to that subdirectory so mutmut picks
    up the local config.  Falls back to ``03-development/src`` when no
    ``[mutmut]`` section exists at all.
    """
    root_cfg = configparser.ConfigParser()
    root_cfg.read(str(project / "setup.cfg"))

    paths: str = HARDCODED_FALLBACK
    if root_cfg.has_section("mutmut"):
        paths = root_cfg.get("mutmut", "paths_to_mutate", fallback=HARDCODED_FALLBACK)

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


def run_mutation_precheck(project: Path) -> tuple[bool, str]:
    """Run mutmut and enforce no surviving mutants.

    Reads ``paths_to_mutate`` (and indirectly ``paths_to_exclude``) from
    the project's ``setup.cfg`` ``[mutmut]`` section, with a hardcoded
    fallback of ``03-development/src``.

    mutmut run exits 0 regardless of surviving mutants, so we always
    parse ``mutmut results`` output afterwards to detect survivors.
    """
    if not shutil.which("mutmut"):
        return False, "mutmut not installed. Required for TDD-PRECHECK. Install: pip install mutmut"

    cwd, paths_to_mutate = _resolve_mutmut_workdir(project)

    src_dir = cwd / paths_to_mutate
    if not src_dir.exists():
        return True, ""

    try:
        r = subprocess.run(
            ["mutmut", "run", f"--paths-to-mutate={paths_to_mutate}"],
            cwd=str(cwd), capture_output=True, text=True,
        )

        if r.returncode != 0:
            return False, (
                f"mutmut run crashed (return code {r.returncode}).\n\n"
                f"STDOUT:\n{r.stdout.strip()}\n\n"
                f"STDERR:\n{r.stderr.strip()}"
            )

        res = subprocess.run(
            ["mutmut", "results"], cwd=str(cwd), capture_output=True, text=True,
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
    except Exception as e:
        return False, f"Error running mutmut: {e}"
