"""Which of the replaced boundaries `make verify-system` really ran (Round 52 站2).

Round 51 站3 named the modules a project's test suite replaces with an
`autouse` stand-in before any test can observe them, marked the affected
dimension `stubbed_boundary`, and let the run continue. This is the question
that marker raises and does not answer: *does anything, anywhere, execute the
real one?*

The framework already runs exactly one thing that the test suite did not
configure — the project's own `make verify-system`, run by the
`execute_verification_target` dimension. So the obligation writes itself, with
no threshold to pick:

    whatever the suite replaced, the verification target has to execute.

A project that stubs nothing owes nothing. Five of the six projects on this
machine are that case. taskq-api owes three:

    taskq_api.service.auth.verify_key            9 autouse fixtures
    taskq_api.repository.session.get_session     9 autouse fixtures
    taskq_api.service.auth.scope_allows          1 autouse fixture

Both ways out lead to the same place. Stop stubbing it and the suite tests it
for real; make verify-system reach it and something else does. Neither can be
satisfied by adding a fixture, because verify-system does not load conftest.

**Attribute granularity, not module.** Station 0's premise P3 measured
taskq-api's product step (`-m taskq_api --help`) under coverage:

    repository/session.py   8 lines executed, 0 inside any function body
    service/auth.py        21 lines executed, 2 inside a function body
    service/runner.py       0 lines executed

`session.py` appears in the coverage report at 27% from imports and `def`
headers alone; a module-granularity check would have called that reached. The
two body lines in `auth.py` belong to `install_log_redaction`, which a
module-level call runs at import — not to `verify_key`, which is what the
fixtures replace. So the obligation is `module.attr`, discharged only by a
statement inside that function's body.

Premise P4 measured the other half: nothing under `migrations/` references
`service.auth`, `verify_key` or `get_session`, so the three alembic steps in
taskq-api's verify-system cannot discharge either obligation either.

**Could-not-measure is not could-not-pass.** This check blocks a gate, so the
rule from Round 35 站2 applies with more force than usual: when the reach
artifact is absent, or an obligation names something that is not a function,
the answer is `unmeasured` and the `unmet` key is absent — not `[]`, which a
caller would read as "nothing outstanding".
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
from contextlib import contextmanager
from pathlib import Path

__all__ = [
    "REACH_RELPATH",
    "STATUS_MEASURED",
    "STATUS_UNMEASURED",
    "read_reach",
    "reach_instrumentation",
    "stubbed_obligations",
    "unmet_obligations",
    "write_reach",
]

REACH_RELPATH = ".sessi-work/verify_system_reach.json"

STATUS_MEASURED = "measured"
STATUS_UNMEASURED = "unmeasured"

# The framework's own file in the project's venv, installed for the duration of
# one verify-system run and removed in a finally. Named so a leftover copy is
# unambiguously ours.
_PTH_NAME = "_harness_verify_system_reach.pth"
_PTH_BODY = "import coverage; coverage.process_startup()\n"

_COMBINE_TIMEOUT = 120


def _src_dir(project: Path) -> Path:
    from core.utils.project_layout import ProjectLayout

    return ProjectLayout(str(project)).active_src_dir


def _dotted(path: Path, src: Path, project: Path) -> "str | None":
    """Dotted module for a path in a coverage report, or None if not delivered.

    coverage.py writes paths RELATIVE TO THE RUN'S CWD, which is the project
    root, not to whatever directory the harness process happens to be in.
    Resolving them against `Path.cwd()` — which `Path.resolve()` does for a
    relative path — silently mapped every file to nothing: measured on a
    taskq-api copy, a 91 KB coverage report with 28 files produced an empty
    reach map, and every obligation came back unmet for the wrong reason. The
    unit fixture used absolute paths and was green throughout.
    """
    if not path.is_absolute():
        path = project / path
    try:
        rel = path.resolve().relative_to(src.resolve())
    except (ValueError, OSError):
        return None
    parts = list(rel.with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts) if parts else None


def write_reach(project: "str | Path", coverage_json: "str | Path") -> Path:
    """Translate a coverage JSON report into the reach artifact.

    Keyed by dotted module because the obligation is; files outside the
    project's active src directory (its own tests, its dependencies) are not
    part of the delivered tree and are dropped here rather than by every
    reader.
    """
    project = Path(project)
    src = _src_dir(project)
    out = project / REACH_RELPATH
    out.parent.mkdir(parents=True, exist_ok=True)

    modules: dict[str, dict] = {}
    status, reason = STATUS_MEASURED, ""
    try:
        data = json.loads(Path(coverage_json).read_text(encoding="utf-8"))
        files = data.get("files") or {}
        for raw_path, info in files.items():
            dotted = _dotted(Path(raw_path), src, project)
            if dotted is None:
                continue
            modules[dotted] = {
                "file": Path(raw_path).name,
                "executed_lines": sorted(int(n) for n in
                                         (info.get("executed_lines") or [])),
            }
    except (OSError, ValueError, TypeError) as exc:
        status = STATUS_UNMEASURED
        reason = f"coverage report unreadable: {type(exc).__name__}: {exc}"

    out.write_text(json.dumps(
        {"status": status, "reason": reason, "modules": modules},
        indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return out


def _venv_python(project: Path) -> "Path | None":
    from core.utils.venv_env import find_venv_bin_dir

    bin_dir = find_venv_bin_dir(project)
    if bin_dir is None:
        return None
    exe = bin_dir / ("python.exe" if os.name == "nt" else "python")
    return exe if exe.exists() else None


def _site_packages(python: Path) -> "Path | None":
    try:
        proc = subprocess.run(
            [str(python), "-c",
             "import sysconfig;print(sysconfig.get_paths()['purelib'])"],
            capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    path = Path(proc.stdout.strip())
    return path if path.is_dir() else None


def _write_unmeasured(project: Path, reason: str) -> Path:
    out = project / REACH_RELPATH
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(
        {"status": STATUS_UNMEASURED, "reason": reason, "modules": {}},
        indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return out


@contextmanager
def reach_instrumentation(project: "str | Path", env: dict):
    """Measure what one `make verify-system` run executes, then clean up.

    Mutates *env* so the subprocess and everything it spawns records coverage,
    and writes the reach artifact on the way out. Always writes one: an
    instrumentation that could not be installed has to say so, or the caller
    reads a missing file as a missing run.

    The channel is a `.pth` in the project venv's site-packages, which is
    coverage's own documented way to reach subprocesses. Not PYTHONPATH:
    station 0's premise F8 measured taskq-api's recipe overriding PYTHONPATH
    inline on every step, which would drop a sitecustomize injected that way.

    **Stated because it is a real choice**: for the duration of the run this
    writes one file into the project's `.venv/`. It never touches the source
    tree, the harness already owns venv lifecycle (Round 47 站2's
    `bootstrap-env`), and the removal is in a `finally` — but this is the first
    time the framework writes into a project's environment in order to measure
    it, and a reader who finds `_harness_verify_system_reach.pth` left behind
    after a crash should know it is ours and safe to delete.
    """
    project = Path(project)
    work = project / ".sessi-work"
    work.mkdir(parents=True, exist_ok=True)

    python = _venv_python(project)
    site = _site_packages(python) if python else None
    if python is None or site is None:
        _write_unmeasured(project, "no project venv with an importable "
                                   "site-packages; subprocess coverage cannot "
                                   "be installed")
        yield
        return

    rcfile = work / "verify_system_cov.rc"
    data_file = work / "verify_system.coverage"
    src = _src_dir(project)
    rcfile.write_text(
        f"[run]\nparallel = True\nsource = {src}\n", encoding="utf-8")
    for stale in work.glob("verify_system.coverage*"):
        stale.unlink(missing_ok=True)

    pth = site / _PTH_NAME
    env["COVERAGE_PROCESS_START"] = str(rcfile)
    env["COVERAGE_FILE"] = str(data_file)
    try:
        pth.write_text(_PTH_BODY, encoding="utf-8")
        yield
    finally:
        pth.unlink(missing_ok=True)
        _harvest(project, python, rcfile, data_file)


def _harvest(project: Path, python: Path, rcfile: Path, data_file: Path) -> None:
    """Combine the per-process data files and write the reach artifact."""
    env = os.environ.copy()
    env["COVERAGE_FILE"] = str(data_file)
    json_out = data_file.with_suffix(".json")
    try:
        subprocess.run([str(python), "-m", "coverage", "combine",
                        "--rcfile", str(rcfile)],
                       cwd=str(project), env=env, capture_output=True,
                       text=True, timeout=_COMBINE_TIMEOUT)
        proc = subprocess.run([str(python), "-m", "coverage", "json",
                               "--rcfile", str(rcfile), "-o", str(json_out),
                               "-q"],
                              cwd=str(project), env=env, capture_output=True,
                              text=True, timeout=_COMBINE_TIMEOUT)
    except (OSError, subprocess.SubprocessError) as exc:
        _write_unmeasured(project, f"coverage harvest failed: "
                                   f"{type(exc).__name__}: {exc}")
        return
    if proc.returncode != 0 or not json_out.is_file():
        _write_unmeasured(project, f"coverage produced no JSON report "
                                   f"(exit {proc.returncode}): "
                                   f"{proc.stderr.strip()[:200]}")
        return
    write_reach(project, json_out)


def read_reach(project: "str | Path") -> "dict | None":
    """The reach artifact, or None when this run did not produce one."""
    path = Path(project) / REACH_RELPATH
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def stubbed_obligations(project: "str | Path") -> list[dict]:
    """Distinct ``{"module", "attr"}`` the suite replaced, sorted."""
    from core.quality_gate.boundary_realism import stubbed_attributes

    seen: set[tuple[str, "str | None"]] = set()
    rows: list[dict] = []
    for row in stubbed_attributes(project):
        key = (row["module"], row["attr"])
        if key in seen:
            continue
        seen.add(key)
        rows.append({"module": row["module"], "attr": row["attr"]})
    return sorted(rows, key=lambda r: (r["module"], r["attr"] or ""))


def _function_body_lines(project: Path, module: str, attr: str) -> "set[int] | None":
    """Line numbers inside ``module.attr``'s body, or None if it is not a function."""
    src = _src_dir(project)
    candidates = [src.joinpath(*module.split(".")).with_suffix(".py"),
                  src.joinpath(*module.split("."), "__init__.py")]
    path = next((p for p in candidates if p.is_file()), None)
    if path is None:
        return None
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return None
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name != attr:
            continue
        lines: set[int] = set()
        for stmt in node.body:
            for sub in ast.walk(stmt):
                lineno = getattr(sub, "lineno", None)
                if lineno is not None:
                    lines.add(int(lineno))
        return lines
    return None


def _sab_declares_high_risk(project: Path) -> bool:
    sab = project / ".methodology" / "SAB.json"
    if not sab.is_file():
        return False
    try:
        data = json.loads(sab.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return bool(isinstance(data, dict) and data.get("high_risk_modules"))


def unmet_obligations(project: "str | Path") -> dict:
    """Which replaced functions `make verify-system` did not execute.

    ``{"status", "reason", "unmet", "unmeasurable"}``. `unmet` is present only
    when status is `measured`; a caller cannot read "nothing unmet" out of
    "nothing measured".

    `unmeasurable` names obligations whose target is not a function this
    module can locate — a `setitem` on a module-level dict, a `setattr` whose
    attribute is computed, a name that resolves to a class. Those are reported,
    never counted as discharged, and never used to block: an obligation the
    framework cannot evaluate is the framework's gap, not the project's
    (Round 32 站4).
    """
    project = Path(project)

    if not _sab_declares_high_risk(project):
        # Round 51 站3's rule, at its caller: [] from the scan means "nothing
        # was replaced" only if we know which modules were at risk.
        return {"status": STATUS_UNMEASURED,
                "reason": "SAB.json declares no high_risk_modules, so which "
                          "boundaries a stand-in replaced is not knowable",
                "unmeasurable": []}

    obligations = stubbed_obligations(project)
    if not obligations:
        return {"status": STATUS_MEASURED, "reason": "", "unmet": [],
                "unmeasurable": []}

    reach = read_reach(project)
    if reach is None or reach.get("status") != STATUS_MEASURED:
        return {"status": STATUS_UNMEASURED,
                "reason": (reach or {}).get("reason")
                          or f"no reach artifact at {REACH_RELPATH}; "
                             f"`make verify-system` was not measured",
                "unmeasurable": []}

    modules = reach.get("modules") or {}
    unmet: list[dict] = []
    unmeasurable: list[dict] = []
    for row in obligations:
        module, attr = row["module"], row["attr"]
        if not attr:
            unmeasurable.append({**row, "why": "the fixture does not name an "
                                                "attribute"})
            continue
        body = _function_body_lines(project, module, attr)
        if body is None:
            unmeasurable.append({**row, "why": f"{module}.{attr} is not a "
                                                f"function this scan can locate"})
            continue
        executed = set((modules.get(module) or {}).get("executed_lines") or [])
        if not body & executed:
            unmet.append(row)
    return {"status": STATUS_MEASURED, "reason": "", "unmet": unmet,
            "unmeasurable": unmeasurable}
