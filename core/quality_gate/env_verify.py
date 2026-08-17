"""Round 20 站1 — deterministic environment probing.

Extracted verbatim from cli/gate_cmds.py::_verify_env_check_claims, whose 19
tests in tests/cli/test_gate_cmds_cli.py::TestVerifyEnvCheckClaims are the
safety net for the move: they exercise every fallback below and must stay green.

WHY this became its own module. env-check answers two questions of completely
different natures with one mechanism (an LLM):

  classification — is this env var REQUIRED, or does the project run fine
                   without it? Decided by the project's documentation.
  verification   — is it present right now? Decided by the actual environment,
                   and fully computable.

Only the first needs judgement. The second was already implemented here, as a
one-directional anti-fabrication spot check ("the agent said present:true — is
it?"), so the agent's `ready` verdict never had a deterministic counterpart.
Round 24 (37adc43) recorded the consequence: the same env var, against the same
unchanged project state, was classified optional_missing (ready=true) in one
workflow run and required+present:false (ready=false, a false FAIL) in another.

These probes are now callable independently of any agent claim, so `ready` can
be COMPUTED from a stored classification instead of asserted by a sub-agent.
"""
from __future__ import annotations

import concurrent.futures
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from core.utils.project_layout import ProjectLayout

__all__ = [
    "normalize_tool_name",
    "probe_env_var",
    "probe_cli_tools",
]


def normalize_tool_name(raw_name: str) -> str:
    """The executable name to actually probe, from an agent-written claim.

    Strips parenthetical annotations ("python3 (.venv)" -> "python3") and keeps
    only the first token ("python3 -m pip" -> "python3").
    """
    stripped = re.sub(r"\s*\(.*?\)\s*$", "", raw_name).strip()
    return stripped.split()[0] if stripped else raw_name


def probe_env_var(name: str) -> bool:
    """Whether an environment variable is exported. The whole of it — presence
    is the only thing checkable here, never the value's correctness."""
    return name in os.environ


def _is_framework_subcommand(name: str) -> bool:
    """v2.13 Bug #123: names ending in `.py` (e.g. "harness_cli.py
    finalize-env-check") are subcommands of framework scripts, not PATH tools —
    they never appear in shutil.which() results, and probing them produced a
    false "fabricated claim" that blocked P3/P5/P7 entry."""
    return name.lower().endswith(".py")


def _is_in_process_tool(name: str) -> bool:
    """True iff the toolchain registry marks `name` as `in_process=True`.

    In-process scorers are dispatched via `python -m harness.toolchains.<name>`
    (or via the in-proc runner) — they have no PATH binary to probe. Returning
    False here would force env-check to probe the registry's `check_cmd`
    (which for `ast-docstrings` is `import ast; ast.parse(...)` — always exit 0
    on Python 3.x) and accept the result of THAT probe as the in-process
    tool's presence, which is a different question and produces a different
    false-positive (every project looks like it has the tool even when the
    toolchain module is missing). The registry is the source of truth.

    Symmetric with `_found_on_path_or_venv`'s underscore↔dash probe (Bug #131):
    contract names use either form (the agent that writes the env contract
    sometimes reaches for the import-style `ast_docstrings`, sometimes the
    console-script-style `ast_docstrings`); the registry canonicalises on
    the dash form. Probe both before deciding the tool is absent.

    Wrapped in try/except so a missing toolchains module still falls through
    to the import probe below — the original behaviour, preserved as a
    fallback for environments that strip optional packages.
    """
    try:
        from harness.toolchains.registry import TOOL_SPECS
    except ImportError:
        return False
    for candidate in (name, name.replace("_", "-")):
        spec = TOOL_SPECS.get(candidate)
        if not spec:
            continue
        if getattr(spec, "in_process", False):
            return True
        if spec.cmd and len(spec.cmd) >= 3 and spec.cmd[1] == "-m" and "harness.toolchains" in str(spec.cmd[2]):
            return True
    return False


def _bin_dir() -> str:
    return "Scripts" if os.name == "nt" else "bin"


def _found_on_path_or_venv(name: str, project: Path) -> bool:
    """PATH, then project-local venv bin/, with python-version-name normalizing.

    Bug #129 (2026-07-02): probe project-local venvs (.venv/venv) directly, not
    only $VIRTUAL_ENV. Orchestrated runs invoke `.venv/bin/python harness_cli.py`
    without activating, so VIRTUAL_ENV is never exported and the old probe was
    dead code there — honest claims about venv-only tools read as fabricated.
    Also normalizes python-version-semantic names ("python311" -> "python3.11"):
    sub-agents name the interpreter after the SAD version string, but the binary
    is `python3.11`. A wrong-version claim (python312 with only 3.11 installed)
    still fails every probe and stays flagged.

    Bug #131 (2026-08-12): normalize underscore <-> dash variants. Many PyPI
    packages ship a CLI entry-point whose console-script name uses a dash
    (e.g. `pip-licenses`) while their import name uses an underscore
    (e.g. `pip_licenses`) and the package directory uses neither
    (e.g. `piplicenses`). env-check accepts the agent-claimed name verbatim,
    so a contract that names `pip_licenses` (matching the agent's
    `import pip_licenses` mental model) was probed against a binary that
    does not exist; the project installed `pip-licenses` correctly, the
    framework probed the wrong filename, and env-check failed forever.
    The fix: try the underscore form *and* the dash form. Symmetric with
    `_import_probe_spec`'s `name.replace("-", "_")`.
    """
    bindir = _bin_dir()
    cands = [name]
    # Underscore <-> dash variant (Bug #131).
    if "_" in name:
        cands.append(name.replace("_", "-"))
    elif "-" in name:
        cands.append(name.replace("-", "_"))
    # Python version semantic name (Bug #129).
    pv = re.fullmatch(r"python[-_.]?(\d)[-_.]?(\d+)", name.lower())
    if pv:
        cands.append(f"python{pv.group(1)}.{pv.group(2)}")
    # Dedupe while preserving order.
    deduped: list[str] = []
    for cn in cands:
        if cn not in deduped:
            deduped.append(cn)
    cands = deduped
    venv_dirs = [os.environ.get("VIRTUAL_ENV", "")]
    venv_dirs += [str(project / d) for d in (".venv", "venv")]
    for cn in cands:
        if shutil.which(cn) is not None:
            return True
        for vd in venv_dirs:
            if vd and os.path.exists(os.path.join(vd, bindir, cn)):
                return True
    return False


def _is_venv_python_semantic_name(name: str, project: Path) -> bool:
    """Bug #128 (2026-06-27): "venv-python", "python-venv", "venv-python3" are
    LOGICAL names meaning "the Python interpreter inside the project's
    virtualenv", not literal PATH binaries. The claim is honest when the running
    interpreter is itself a venv interpreter, or a project-local venv exists
    with a Python binary. Without this, every project using venv-semantic naming
    got a false fabrication finding. Any name whose lowercased text contains
    both "venv" and "python" is treated as such.
    """
    name_lc = name.lower()
    if not ("venv" in name_lc and "python" in name_lc):
        return False
    try:
        if sys.prefix != getattr(sys, "base_prefix", sys.prefix):
            return True
        exe_name = "python.exe" if os.name == "nt" else "python3"
        bindir = _bin_dir()
        for venv_dir in (".venv", "venv"):
            if (project / venv_dir / bindir / exe_name).exists():
                return True
    except Exception as exc:  # pylint: disable=broad-exception-caught
        print(f"[WARN] tool-check: venv-Python semantic-name probe for "
              f"'{name}' failed: {exc}", file=sys.stderr)
    return False


def _import_probe_spec(name: str, project: Path) -> "tuple[str, dict, list[str]]":
    """Build the (package, env, interpreters) triple for an `import <pkg>` probe.

    src-layout projects (e.g. 03-development/src/taskq) are importable only with
    the project's src root on PYTHONPATH — the deliverable package is a valid
    "present" claim even before pip install. Bug #129: try the project venv's
    python too, so whether a plugin-only package (pytest-cov) verifies does not
    depend on which interpreter happens to run harness_cli.
    """
    pkg = name.replace("-", "_")
    import_env = {**os.environ}
    try:
        src_dir = ProjectLayout(project).active_src_dir
        if src_dir.is_dir():
            import_env["PYTHONPATH"] = os.pathsep.join(
                p for p in (str(src_dir), import_env.get("PYTHONPATH", "")) if p
            )
    except Exception as exc:  # pylint: disable=broad-exception-caught
        print(f"[WARN] tool-check: could not resolve active_src_dir for "
              f"PYTHONPATH (import probe for '{name}' may miss src-layout "
              f"packages): {exc}", file=sys.stderr)
    interps = [sys.executable]
    py_exe = "python.exe" if os.name == "nt" else "python"
    for vd in (".venv", "venv"):
        vp = project / vd / _bin_dir() / py_exe
        if vp.exists():
            interps.append(str(vp))
    return pkg, import_env, interps


def probe_cli_tools(raw_names: "list[str]", project: Path) -> "dict[str, bool]":
    """Probe CLI tool names, returning {raw_name: found}.

    Batched on purpose: several unresolved tools each sequentially spawning up
    to len(interps) `import <pkg>` probes (5s timeout each) serialize to tens of
    seconds on a blocking CLI path, so all deferred import probes run
    concurrently at the end. Framework subcommands (`*.py`) are reported found —
    they are not PATH tools and probing them is meaningless (Bug #123).
    """
    results: dict[str, bool] = {}
    pending: list[tuple[str, str, dict, list[str]]] = []
    for raw_name in raw_names:
        name = normalize_tool_name(raw_name)
        if not name:
            continue
        if _is_framework_subcommand(name):
            results[raw_name] = True
            continue
        # Round 56 站1: in-process scorers (`ast-docstrings`,
        # `readability-v2`, `ast-assertions`, `ast-error-handling`,
        # `radon-mi`) run inside the harness via `python -m
        # harness.toolchains.<name> {src}` — they have no PATH binary to
        # probe, and the env contract's `cli_tools` list is generated from
        # the same registry that knows they're in-process. Reporting them
        # missing here produced false-positive P3 env-check FAILs on every
        # fresh project that inherited the suite. The registry IS the
        # source of truth for "is this a PATH tool?". Proxy through a
        # try/except so a missing toolchains module (forced by an env with
        # only stdlib) still falls through to the import probe below.
        if _is_in_process_tool(name):
            results[raw_name] = True
            continue
        if _found_on_path_or_venv(name, project):
            results[raw_name] = True
            continue
        if _is_venv_python_semantic_name(name, project):
            results[raw_name] = True
            continue
        pkg, import_env, interps = _import_probe_spec(name, project)
        pending.append((raw_name, pkg, import_env, interps))

    if pending:
        def _probe_import(item: "tuple[str, str, dict, list[str]]") -> "tuple[str, bool]":
            _raw_name, _pkg, _import_env, _interps = item
            for _interp in _interps:
                try:
                    _r = subprocess.run(
                        [_interp, "-c", f"import {_pkg}"],
                        capture_output=True, timeout=5, env=_import_env,
                    )
                    if _r.returncode == 0:
                        return _raw_name, True
                except Exception as exc:  # pylint: disable=broad-exception-caught
                    print(f"[WARN] tool-check: import probe for '{_pkg}' via "
                          f"{_interp} failed: {exc}", file=sys.stderr)
            return _raw_name, False

        with concurrent.futures.ThreadPoolExecutor(
            max_workers=min(8, len(pending))
        ) as ex:
            for _raw_name, _found in ex.map(_probe_import, pending):
                results[_raw_name] = _found
    return results
