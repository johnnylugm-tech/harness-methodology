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


def _registry_check_cmd(name: str) -> "str | None":
    """The shell probe the toolchain registry declares for `name`, if any.

    Round 56 站2. This replaces `_is_in_process_tool`, a classifier that
    sorted names into "PATH tool" and "in-process tool" and reported the
    second kind present WITHOUT MEASURING ANYTHING. Two families went green
    on hosts that cannot run them:

      radon-mi / readability-v2 — dispatched as `python -m
        harness.toolchains.*`, and both modules shell out to the `radon`
        binary (radon_mi_ast_stripped.py:17, readability_v2.py:14). Their
        registry check_cmd is literally `radon --version`.
      js-assertions / js-error-handling / js-doc-coverage / js-mi — check_cmd
        is `import tree_sitter, tree_sitter_javascript, tree_sitter_typescript`,
        a real probe that was skipped whole.

    `ToolSpec.check_cmd` is the registry's own answer to "can this tool run",
    written per tool for exactly this reason —
    docs/PROPOSAL_ADJUDICATIONS.md:2555 adjudicated it in those words. Asking
    it is both more honest and less code than classifying names.

    `skip_inline` tools are excluded: the registry marks them precisely
    because their probe cannot be run inline (too slow, or env-coupled — see
    `ToolSpec.skip_inline`), and their gate evidence is a committed
    tool_output validated at finalize-gate. They keep the PATH probe.

    Honest limitation, recorded rather than papered over: the `ast-*` scanners
    declare `import ast`, which is always true. That is not a gap in this
    routing — the stdlib IS their whole dependency, and the scanner module
    lives in the harness checkout that must exist for env-check to be running
    at all.

    Underscore↔dash symmetric with `_found_on_path_or_venv` (Bug #131): the
    agent writing the env contract reaches for either form; the registry
    canonicalises on the dash. Wrapped in try/except so an environment
    without the toolchains package falls through to the import probe.
    """
    try:
        from harness.toolchains.registry import TOOL_SPECS
    except ImportError:
        return None
    for candidate in (name, name.replace("_", "-")):
        spec = TOOL_SPECS.get(candidate)
        if spec and spec.check_cmd and not getattr(spec, "skip_inline", False):
            return spec.check_cmd
    return None


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


def _is_distribution_installed(name: str) -> bool:
    """Whether a Python distribution named `name` is installed *in THIS process*.

    PEP 503 normalises case + `._-` for distribution lookup, so
    `pip-tools`, `pip_tools`, and `PIPTOOLS` all resolve to the same
    distribution. Distribution lookup is the canonical "is this PyPI
    package installed" check — it is exactly what `pip show <name>`,
    `pip install --dry-run`, and `pkg_resources.get_distribution` do
    internally.

    Symmetric with `_found_on_path_or_venv`'s `_` <-> `-` fallback: that
    function fixes the case where a tool's CLI uses a different separator
    than its distribution name. This covers the case that
    `_found_on_path_or_venv` CANNOT — packages whose distribution name,
    CLI binaries, and top-level module name all differ.

    Worked example observed on taskq-verify (2026-08-30):

      distribution name : pip-tools
      console_scripts   : pip-compile, pip-sync        (neither named `pip-tools`)
      top-level module  : piptools                       (no separator at all)
      import probe name : pip_tools  (= name.replace("-","_"))  ← doesn't match

    THE SCOPE, because it is the whole reason this is not the last word:
    `importlib.metadata` answers for the interpreter that calls it, which is
    the harness's. The claim being checked is about the PROJECT's environment.
    Measured 2026-08-31 on the very incident above: `taskq-verify/.venv` has
    pip-tools, this repo's `.venv` does not, and asking here returned False —
    so the legitimate install was still reported as a fabricated claim, which
    is exactly the finding this probe was added to remove.

    So this stays as the free fast path — it costs no subprocess, and a hit
    is a true hit — and the project's own interpreters are asked by the
    batched probe in `probe_cli_tools`, which already spawns one process per
    interpreter and now asks it this question too. Same rule as
    `_import_probe_spec`'s Bug #129 note: whether a package verifies must not
    depend on which interpreter happens to run harness_cli.
    """
    import importlib.metadata
    try:
        importlib.metadata.distribution(name)
        return True
    except importlib.metadata.PackageNotFoundError:
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
        from core.utils.venv_env import find_venv_python
        if find_venv_python(project) is not None:
            return True
    except Exception as exc:  # pylint: disable=broad-exception-caught
        print(f"[WARN] tool-check: venv-Python semantic-name probe for "
              f"'{name}' failed: {exc}", file=sys.stderr)
    return False


def _import_probe_spec(name: str, project: Path) -> "tuple[str, dict, list[str]]":
    """Build the (probe source, env, interpreters) triple for the deferred probe.

    src-layout projects (e.g. 03-development/src/taskq) are importable only with
    the project's src root on PYTHONPATH — the deliverable package is a valid
    "present" claim even before pip install. Bug #129: try the project venv's
    python too, so whether a plugin-only package (pytest-cov) verifies does not
    depend on which interpreter happens to run harness_cli.

    The source asks TWO questions in the one process it was already going to
    spawn: can this interpreter import the module, and does this interpreter's
    site-packages hold a distribution by that name. They are different
    questions — pip-tools answers no to the first and yes to the second — and
    `_is_distribution_installed` can only ask the second one of the harness.
    Returning the source rather than the package name keeps what the probe asks
    in one place; two spellings of it is how the caller comes to ask something
    else than this function documents.
    """
    pkg = name.replace("-", "_")
    probe_src = (
        "import importlib, importlib.metadata as _m, sys\n"
        f"try:\n"
        f"    importlib.import_module({pkg!r})\n"
        f"    sys.exit(0)\n"
        f"except ImportError:\n"
        f"    pass\n"
        f"_m.distribution({name!r})\n"
    )
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
    from core.utils.venv_env import find_venv_python

    interps = [sys.executable]
    _vp = find_venv_python(project)
    if _vp is not None:
        interps.append(str(_vp))
    return probe_src, import_env, interps


def probe_cli_tools(raw_names: "list[str]", project: Path) -> "dict[str, bool]":
    """Probe CLI tool names, returning {raw_name: found}.

    Batched on purpose: several unresolved tools each sequentially spawning up
    to len(interps) probes (5s timeout each) serialize to tens of seconds on a
    blocking CLI path, so all deferred probes run concurrently at the end.
    Framework subcommands (`*.py`) are reported found — they are not PATH tools
    and probing them is meaningless (Bug #123).

    Order matters and is cheapest-first. PATH is a `shutil.which` lookup and
    costs nothing, so a tool that is simply installed never pays for a
    subprocess. After PATH, an in-process distribution lookup catches the same
    package when the harness's own interpreter happens to have it. Only after
    both miss does the registry's own `check_cmd` get a turn (Round 56 站2),
    and only after that the deferred probe, which asks BOTH questions —
    importable, or a distribution by that name — of every interpreter,
    including the project's own venv.
    Measured 2026-08-17: nine registry tools reach the check_cmd branch, each
    0.02–0.04s, and they run in the same thread pool as the deferred probes.
    """
    results: dict[str, bool] = {}
    pending: list[tuple[str, str, dict, list[str]]] = []
    checks: list[tuple[str, str]] = []
    for raw_name in raw_names:
        name = normalize_tool_name(raw_name)
        if not name:
            continue
        if _is_framework_subcommand(name):
            results[raw_name] = True
            continue
        if _found_on_path_or_venv(name, project):
            results[raw_name] = True
            continue
        if _is_venv_python_semantic_name(name, project):
            results[raw_name] = True
            continue
        # Distribution metadata lookup — the canonical `pip show` answer for
        # "is this PyPI package installed", asked of THIS interpreter. A hit
        # is free and true; a miss says nothing about the project, which is
        # why it is a fast path here and not the branch that decides. The
        # project's own interpreters get the same question in the deferred
        # probe below (`_import_probe_spec`).
        if _is_distribution_installed(name):
            results[raw_name] = True
            continue
        check_cmd = _registry_check_cmd(name)
        if check_cmd:
            checks.append((raw_name, check_cmd))
            continue
        probe_src, import_env, interps = _import_probe_spec(name, project)
        pending.append((raw_name, probe_src, import_env, interps))

    if pending or checks:
        def _probe_import(item: "tuple[str, str, dict, list[str]]") -> "tuple[str, bool]":
            _raw_name, _probe_src, _import_env, _interps = item
            for _interp in _interps:
                try:
                    _r = subprocess.run(
                        [_interp, "-c", _probe_src],
                        capture_output=True, timeout=5, env=_import_env,
                    )
                    if _r.returncode == 0:
                        return _raw_name, True
                except Exception as exc:  # pylint: disable=broad-exception-caught
                    print(f"[WARN] tool-check: import/distribution probe for "
                          f"'{_raw_name}' via {_interp} failed: {exc}",
                          file=sys.stderr)
            return _raw_name, False

        def _probe_check_cmd(item: "tuple[str, str]") -> "tuple[str, bool]":
            _raw_name, _cmd = item
            from core.utils.venv_env import venv_scoped_env
            from harness.tool_checks import run_tool_check
            try:
                return _raw_name, run_tool_check(
                    _cmd, cwd=str(project), env=venv_scoped_env(project))
            except Exception as exc:  # pylint: disable=broad-exception-caught
                # Fail CLOSED and say so: a probe that raised did not measure,
                # and "did not measure" is not "present" (same rule
                # scripts/bootstrap_env.py::unsatisfied_tools applies).
                print(f"[WARN] tool-check: registry probe for '{_raw_name}' "
                      f"({_cmd}) raised ({exc}) — counting it as absent",
                      file=sys.stderr)
                return _raw_name, False

        with concurrent.futures.ThreadPoolExecutor(
            max_workers=min(8, len(pending) + len(checks))
        ) as ex:
            futures = [ex.submit(_probe_import, item) for item in pending]
            futures += [ex.submit(_probe_check_cmd, item) for item in checks]
            for fut in futures:
                _raw_name, _found = fut.result()
                results[_raw_name] = _found
    return results
