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
import re
import subprocess
from contextlib import contextmanager
from pathlib import Path

__all__ = [
    "REACH_RELPATH",
    "SIDECAR_KEYS",
    "SIDECAR_RELPATH",
    "STATUS_MEASURED",
    "STATUS_UNMEASURED",
    "harvest_selection",
    "read_reach",
    "reach_instrumentation",
    "stubbed_obligations",
    "suite_pids",
    "unmet_obligations",
    "write_reach",
    "write_reach_unmeasured",
    "write_sidecar_row",
]

REACH_RELPATH = ".sessi-work/verify_system_reach.json"
SIDECAR_RELPATH = ".sessi-work/verify_system_processes.jsonl"

STATUS_MEASURED = "measured"
STATUS_UNMEASURED = "unmeasured"

# One row per process that ran under the instrumentation. `pid` is the join key
# to coverage's parallel data files, which are named
# `<COVERAGE_FILE>.<host>.pid<PID>.<random>`; `argv` and `mods` are what say
# whether that process was the project's own test suite.
SIDECAR_KEYS: tuple[str, ...] = ("pid", "argv", "mods")

# Round 53 站4. Station 0's premise P2 measured two things about this channel.
# The pid does reach the data-file name, so processes can be told apart. But
# `sys.argv` at `.pth` execution time for `python -m pytest x` is `["-m", "x"]`
# — the interpreter has not yet expanded the module — so the runner cannot be
# identified at startup. An `atexit` hook sees the completed argv
# (`/…/pytest/__main__.py x`) and `sys.modules`, which names it outright.
#
# Two files rather than one line. The single-line `exec('try: …')` form was
# measured and fails: names bound inside the exec are not in the lambda's
# closure at exit (`NameError: name '_p' is not defined`), and the workarounds
# are write-only. A module the `.pth` imports is readable, and both files are
# removed in the same finally.
_PTH_NAME = "_harness_verify_system_reach.pth"
_HOOK_NAME = "_harness_verify_system_reach_hook.py"
_PTH_BODY = (
    f"import coverage,{_HOOK_NAME[:-3]};"
    f"coverage.process_startup();{_HOOK_NAME[:-3]}.install()\n"
)
_HOOK_BODY = f'''"""Written by harness-methodology for one `make verify-system` run.

Removed in a finally by core/quality_gate/verify_system_reach.py. A leftover
copy is the framework's and is safe to delete.
"""
import atexit, json, os, sys


def _record():
    path = os.environ.get("HARNESS_REACH_SIDECAR")
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(dict(zip(
                {SIDECAR_KEYS!r},
                (os.getpid(), list(sys.argv),
                 sorted(m for m in sys.modules if m in _RUNNERS)),
            ))) + "\\n")
    except OSError:
        pass


_RUNNERS = frozenset(
    (os.environ.get("HARNESS_REACH_RUNNERS") or "").split(",")
) - {{""}}


def install():
    atexit.register(_record)
'''

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


# Which module, if a process imported it, means that process was running the
# project's test suite. `state.json.test_runner` is the project's own answer
# where it has one (it is written for JS/TS by `cli/project_cmds.py`); Python
# projects do not carry the key, and the registry's pytest ToolSpec is the only
# thing in the framework that names their runner.
_RUNNER_BY_LANGUAGE: dict[str, str] = {
    "python": "pytest",
    "javascript": "vitest",
    "typescript": "vitest",
}


def _runner_modules(project: Path) -> frozenset[str]:
    """The test-runner module names for this project."""
    from core.state_io import load_state

    try:
        state = load_state(project, lenient=True)
    except Exception:  # pragma: no cover — a project mid-init has no state yet
        state = {}
    declared = state.get("test_runner")
    if declared:
        return frozenset({str(declared)})
    language = str(state.get("language") or "python")
    return frozenset({_RUNNER_BY_LANGUAGE.get(language, "pytest")})


def write_sidecar_row(project: "str | Path", row: dict) -> None:
    """Append one process row. The hook module writes the same three keys."""
    project = Path(project)
    path = project / SIDECAR_RELPATH
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({k: row.get(k) for k in SIDECAR_KEYS}) + "\n")


def suite_pids(project: "str | Path") -> set[int]:
    """Pids of processes that ran the project's own test suite.

    Round 53 站4. Round 52 站2 asked whether a replaced boundary was executed
    during `make verify-system` and took any execution as an answer. On
    taskq-super — whose verify-system is the whole pytest suite plus
    `-m taskq_api --help` — the execution it found was **inside the pytest run
    that installs the stand-in**, so Gates 2, 3 and 4 all recorded
    `obligations_unmet: []` for a boundary no test has ever exercised for real.
    The obligation was written to mean "something the suite did not configure
    has to run this"; when verify-system contains the suite, the discharging
    process and the stubbing process are the same process. That is my defect,
    not the project's, and this is where it is separated.

    Two independent signals, either one enough: the runner module is in the
    process's `sys.modules` at exit, or the project's test target appears in
    its completed argv.
    """
    from core.quality_gate.test_suite_run import resolve_targets

    project = Path(project)
    path = project / SIDECAR_RELPATH
    if not path.is_file():
        return set()
    runners = _runner_modules(project)
    try:
        test_target = resolve_targets(project)[0]
    except Exception:  # pragma: no cover — targets unresolvable, argv signal off
        test_target = ""

    pids: set[int] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        pid = row.get("pid")
        if not isinstance(pid, int):
            continue
        argv = " ".join(str(a) for a in (row.get("argv") or []))
        mods = {str(m) for m in (row.get("mods") or [])}
        if mods & runners or (test_target and test_target in argv):
            pids.add(pid)
    return pids


_PID_RE = re.compile(r"\.pid(\d+)\.")


def harvest_selection(
    project: "str | Path", data_file: Path,
) -> "tuple[list[Path], str | None]":
    """Coverage data files written by something other than the test suite.

    Returns `(kept, reason)`. `reason` is non-None exactly when nothing is left
    to combine, and it is a reason rather than a verdict: an obligation the
    framework has no witness for is `unmeasured`, never `unmet`. Charging a
    project for the framework's blind spot is what Round 32 站4 forbids, and
    Round 35 站2 forbids scoring it zero.
    """
    project = Path(project)
    suite = suite_pids(project)
    parent = data_file.parent
    files = sorted(parent.glob(data_file.name + ".*"))
    if not files:
        return [], None  # nothing ran under instrumentation at all
    kept = [p for p in files
            if not (_PID_RE.search(p.name)
                    and int(_PID_RE.search(p.name).group(1)) in suite)]
    if kept:
        return kept, None
    return [], (
        "every process that ran under `make verify-system` was the project's "
        "own test suite, which is the suite that installs the stand-in — so "
        "nothing outside it witnessed the real boundary"
    )


def write_reach_unmeasured(project: "str | Path", reason: str) -> Path:
    """Record that reach could not be established, and why."""
    return _write_unmeasured(Path(project), reason)


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
    hook = site / _HOOK_NAME
    sidecar = project / SIDECAR_RELPATH
    sidecar.unlink(missing_ok=True)
    env["COVERAGE_PROCESS_START"] = str(rcfile)
    env["COVERAGE_FILE"] = str(data_file)
    # Round 53 站4: the hook needs both of these, and neither can be inferred
    # inside a project venv that knows nothing about the harness.
    env["HARNESS_REACH_SIDECAR"] = str(sidecar)
    env["HARNESS_REACH_RUNNERS"] = ",".join(sorted(_runner_modules(project)))
    try:
        hook.write_text(_HOOK_BODY, encoding="utf-8")
        pth.write_text(_PTH_BODY, encoding="utf-8")
        yield
    finally:
        pth.unlink(missing_ok=True)
        hook.unlink(missing_ok=True)
        _harvest(project, python, rcfile, data_file)


def _harvest(project: Path, python: Path, rcfile: Path, data_file: Path) -> None:
    """Combine the per-process data files and write the reach artifact."""
    env = os.environ.copy()
    env["COVERAGE_FILE"] = str(data_file)
    json_out = data_file.with_suffix(".json")

    # Round 53 站4: drop the data files written by the project's own test
    # suite before combining. The obligation says a replaced boundary must be
    # executed by something the suite did not configure; counting the suite's
    # own execution is how taskq-super's `session.transactional` was reported
    # discharged at Gates 2, 3 and 4 while no test has ever run the real one.
    kept, only_suite = harvest_selection(project, data_file)
    if only_suite:
        _write_unmeasured(project, only_suite)
        return
    for stale in sorted(data_file.parent.glob(data_file.name + ".*")):
        if stale not in kept:
            stale.unlink(missing_ok=True)

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
