#!/usr/bin/env python3
"""bootstrap_env.py — build the virtualenv every workflow already assumes.

Round 47 站2. Every generated workflow opens with

    const PY = REPO + '/.venv/bin/python'          (js_blocks.py:110)

and Phase 1's first preflight command runs through it. A whole-tree grep for
`python -m venv` before this file found exactly one hit: a string inside a
BLOCKED message (cli/project_cmds.py:341) telling the operator to run it
themselves. Nothing created it. When it was absent, P1 failed its first
command three times and reported "preflight did not PASS in 3 orchestrator
attempts" — a message about the retry loop, not about the missing interpreter.

The half that existed asked the wrong interpreter. init-project's step
[10b/11] decided importability with `__import__(pkg)` — which runs in the
CALLING process — and then installed into `project/.venv`. Run it from any
interpreter that already has pyyaml and it printed "OK — all runtime Python
deps importable" while the project's venv stayed empty. It also checked 2 of
the 20 packages requirements.txt pins.

STDLIB ONLY, and a standalone script rather than a harness_cli subcommand,
because of what it is for: it runs BEFORE the venv exists, on whatever
interpreter the operator has. Measured 2026-08-12 on a clean 3.14 venv,
`import harness_cli` raises ModuleNotFoundError: No module named 'yaml'
(sab_parser.py:45, security_design.py:47, traceability/overlay.py:23 are all
module-level). harness.toolchains does import stdlib-only, which is why the
install SSOT lives there and this file can read it.

Usage:
    python3 scripts/bootstrap_env.py --project /path/to/project
    python3 scripts/bootstrap_env.py --project . --json
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_SCRIPT_DIR.parent))

import harness.toolchains.bootstrap as _ssot  # noqa: E402  (aliased: `bootstrap` is this module's own verb)

__all__ = [
    "BootstrapReport",
    "venv_python",
    "ensure_venv",
    "missing_imports",
    "bootstrap",
]

# Same floor harness_cli.py enforces. Stated here too because this script runs
# on the interpreter that has not been vetted yet — that is its whole job.
_MIN_PYTHON = (3, 10)


def _pip_env_with_icu() -> dict:
    """A subprocess env overlay that helps pip's wheel builds find host ICU.

    `scancode-toolkit==32.4.1` (`PIP_STEPS` `gate-extras`) pulls `pyicu`, whose
    setup.py shells out to `pkg-config --modversion icu-i18n`. The host ICU is
    present on most distros (Debian: libicu-dev; Homebrew: icu4c@<v>) but the
    .pc files sit under a non-default location that pip's inherited env does
    not advertise. Without help, pyicu's build fails with
    `Please install pkg-config ... or set ICU_VERSION`, the scancode
    distribution never installs, and bootstrap reports
    `[BLOCKED] still not resolvable after install: scancode`.

    Probing `pkg-config` and forwarding its view of the world is the correct
    fix: the ICU headers ARE on the host; pip just cannot see them. The probe
    starts with pkg-config's own pc_path, then layers Homebrew keg-only
    paths on top — Homebrew deliberately does not advertise `icu4c@*` to
    pkg-config because the formula is versioned (`icu4c@78` /
    `icu4c@76` / ...), so pkg-config's view is not exhaustive. The overlay
    only takes effect when a real ICU version comes back; a host without ICU
    keeps its current error and remediation, so the fix is additive and
    cross-platform.

    Returns a dict of additional env vars; empty when no usable ICU is
    detected. Callers must apply/restore this around the pip subprocess —
    a global env mutation is the smallest change that does not require
    threading a new optional kwarg through every `run=` test mock.
    """
    # Layer 1: pkg-config's own advertised search path.
    try:
        path_proc = subprocess.run(
            ["pkg-config", "--variable=pc_path", "pkg-config"],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        path_proc = None
    pc_paths: list[str] = []
    if path_proc is not None and getattr(path_proc, "returncode", 1) == 0:
        declared = (path_proc.stdout or "").strip()
        if declared:
            pc_paths.extend(p for p in declared.split(":") if p)

    # Layer 2: Homebrew keg-only icu4c formulas. `brew --prefix` is
    # macOS-specific; its absence (Linux) is silently ignored. Note that
    # `brew --prefix <name>` exits 0 with a default path even when the
    # formula is NOT installed, so we must verify the directory exists
    # before trusting the answer.
    for prefix_cmd in (
        ["brew", "--prefix", "icu4c"],
        ["brew", "--prefix", "icu4c@78"],
        ["brew", "--prefix", "icu4c@76"],
        ["brew", "--prefix", "icu4c@74"],
    ):
        try:
            prefix_proc = subprocess.run(
                prefix_cmd, capture_output=True, text=True, timeout=10,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if getattr(prefix_proc, "returncode", 1) != 0:
            continue
        prefix = (prefix_proc.stdout or "").strip()
        if not prefix or not Path(prefix + "/lib/pkgconfig").is_dir():
            continue
        candidate = prefix + "/lib/pkgconfig"
        if candidate not in pc_paths:
            pc_paths.append(candidate)

    if not pc_paths:
        return {}

    probe_env = os.environ.copy()
    probe_env["PKG_CONFIG_PATH"] = ":".join(pc_paths)
    try:
        proc = subprocess.run(
            ["pkg-config", "--modversion", "icu-i18n"],
            capture_output=True, text=True, timeout=10, env=probe_env,
        )
    except (OSError, subprocess.TimeoutExpired):
        return {}
    if getattr(proc, "returncode", 1) != 0:
        return {}
    version = (proc.stdout or "").strip()
    if not version:
        return {}
    return {"PKG_CONFIG_PATH": ":".join(pc_paths), "ICU_VERSION": version}


@dataclass
class BootstrapReport:
    """What was done and what is still not true afterwards."""

    python: "Path | None" = None
    venv_created: bool = False
    steps_run: list[str] = field(default_factory=list)
    still_missing_imports: list[str] = field(default_factory=list)
    still_missing_tools: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """Decided by what is measurable afterwards, not by pip's exit code.

        A pip step can fail on a package the host already satisfies another
        way — measured 2026-08-12 on macOS, `scancode-toolkit==32.4.1` fails to
        build (pyicu needs ICU headers) on a host where `scancode --version`
        answers fine from a system install. Blocking there would be the same
        error as trusting a green install: taking the installer's word over the
        probe's.
        """
        return (
            self.python is not None
            and not self.still_missing_imports
            and not self.still_missing_tools
        )

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "python": str(self.python) if self.python else None,
            "venv_created": self.venv_created,
            "steps_run": list(self.steps_run),
            "still_missing_imports": list(self.still_missing_imports),
            "still_missing_tools": list(self.still_missing_tools),
            "failures": list(self.failures),
        }


def _bin_dir() -> str:
    return "Scripts" if os.name == "nt" else "bin"


def _python_name() -> str:
    return "python.exe" if os.name == "nt" else "python"


def venv_python(project: "Path | str") -> "Path | None":
    """The project's venv interpreter, if one already exists."""
    root = Path(project)
    for venv_dir in (".venv", "venv"):
        candidate = root / venv_dir / _bin_dir() / _python_name()
        if candidate.exists():
            return candidate
    return None


def ensure_venv(
    project: "Path | str", *, creator: "str | None" = None, run=subprocess.run
) -> Path:
    """Return the project's venv interpreter, creating `.venv` if absent.

    *creator* is the interpreter that builds the venv (defaults to the running
    one). Raises RuntimeError with the operator's next move on failure — a
    missing interpreter is not something to degrade past.
    """
    root = Path(project)
    existing = venv_python(root)
    if existing is not None:
        return existing

    if sys.version_info < _MIN_PYTHON:
        raise RuntimeError(
            f"harness-methodology requires Python "
            f"{_MIN_PYTHON[0]}.{_MIN_PYTHON[1]}+. Got "
            f"{sys.version_info.major}.{sys.version_info.minor} at {sys.executable}\n"
            f"  Fix: re-run bootstrap-env with a newer python3."
        )

    root.mkdir(parents=True, exist_ok=True)
    proc = run(
        [creator or sys.executable, "-m", "venv", str(root / ".venv")],
        capture_output=True,
        text=True,
    )
    if getattr(proc, "returncode", 1) != 0:
        raise RuntimeError(
            f"could not create {root / '.venv'}:\n"
            f"{(getattr(proc, 'stderr', '') or '')[-600:]}"
        )
    created = venv_python(root)
    if created is None:
        raise RuntimeError(
            f"`python -m venv` reported success but {root / '.venv'} has no "
            f"interpreter — the exit code was not the evidence it looked like"
        )
    return created


def missing_imports(
    python: "Path | str", modules=_ssot.RUNTIME_IMPORTS, *, run=subprocess.run
) -> list[str]:
    """Which of *modules* the TARGET interpreter cannot import.

    The interpreter asked is the one being prepared, never the one asking.
    That distinction is the whole of Round 47's F2: init-project asked its own
    process and installed somewhere else, so a host that already had pyyaml
    reported success over an empty project venv.
    """
    missing: list[str] = []
    for module in modules:
        proc = run(
            [str(python), "-c", f"import {module}"], capture_output=True, text=True
        )
        if getattr(proc, "returncode", 1) != 0:
            missing.append(module)
    return missing


def bootstrap(
    project: "Path | str",
    *,
    run=subprocess.run,
    creator: "str | None" = None,
    probe=None,
) -> BootstrapReport:
    """Create the venv if needed, run every pip step, then re-measure.

    The re-measure is not ceremony: pip can exit 0 and leave the import
    unsatisfiable (a resolver backtrack that lands on an older pin, a wheel
    that fails at import time). The installer's exit code is a claim; the
    import is the evidence.
    """
    report = BootstrapReport()
    had_venv = venv_python(project) is not None
    try:
        python = ensure_venv(project, creator=creator, run=run)
    except RuntimeError as exc:
        report.failures.append(str(exc))
        return report
    report.python = python
    report.venv_created = not had_venv
    measure = probe or unsatisfied_tools

    # Measure before installing. An environment that is already complete needs
    # no pip round at all, and running one anyway would make every
    # init-project / phase entry pay for a network resolve it cannot use.
    report.still_missing_imports = missing_imports(python, run=run)
    report.still_missing_tools = measure(project)
    if report.ok:
        return report

    # `scancode-toolkit` (in `gate-extras`) pulls `pyicu`, whose wheel build
    # shells out to `pkg-config` for the host ICU. On hosts where ICU is
    # installed but pkg-config's search path is not in pip's inherited env
    # (Homebrew macOS being the measured case), the build fails and
    # bootstrap reports the distribution as unresolvable. Detect host ICU
    # once and apply it to every pip subprocess in this loop; restore the
    # caller's env on exit so this script has no observable side effect.
    icu_overlay = _pip_env_with_icu()
    _saved_env: dict = {}
    if icu_overlay:
        _saved_env = os.environ.copy()
        os.environ.update(icu_overlay)
    try:
        for step in _ssot.PIP_STEPS:
            argv = [str(python), "-m", "pip", "install", *_ssot.pip_args(step)]
            proc = run(argv, capture_output=True, text=True)
            report.steps_run.append(step.name)
            if getattr(proc, "returncode", 1) != 0:
                report.failures.append(
                    f"pip step {step.name!r} failed ({step.why}):\n"
                    f"{(getattr(proc, 'stderr', '') or '')[-600:]}"
                )
    finally:
        if icu_overlay and _saved_env:
            os.environ.clear()
            os.environ.update(_saved_env)

    report.still_missing_imports = missing_imports(python, run=run)
    report.still_missing_tools = measure(project)
    return report


def _distribution_name(tool_id: str) -> "str | None":
    """The pip distribution that provides *tool_id*, from the install SSOT.

    `package_for_tool` answers for anything the framework installs by name
    (`scancode-toolkit==32.4.1` → `scancode-toolkit`). Everything else comes
    from requirements.txt, where the distribution is named as the tool is
    (`mutmut`). Returns None when neither knows — a name this file must not
    guess at.
    """
    pinned = _ssot.package_for_tool(tool_id)
    if pinned:
        return pinned.split("==")[0].strip()
    if tool_id in set(_ssot.requirements_packages()):
        return tool_id
    return None


def unsatisfied_tools(project: "Path | str") -> list[str]:
    """Tool_ids the pip steps promised and the target interpreter does not have.

    Deliberately scoped to what the pip steps claim to deliver: this reports on
    the bootstrap's promise, not on the whole gate toolchain (which needs the
    gate YAMLs, and therefore pyyaml, which may not exist yet).

    Two probes, because the question is not the same for both kinds of tool.

    Ordinary tools are asked their own `check_cmd` against the project's
    venv-scoped PATH — the same question, asked the same way, that
    `verify_gate_tools` will ask later.

    `skip_inline` tools (code-review-graph, mutmut, scancode, stryker) are
    asked whether their DISTRIBUTION is installed in the target interpreter.
    Round 56 站3: they used to be skipped entirely, on the reasoning that the
    design excludes them from inline cross-validation. That reasoning is about
    gate evidence and does not transfer here, because this function's one
    production consumer is `bootstrap()`, where an empty result means
    `report.ok` and `report.ok` means the pip round never runs. Measured
    2026-08-17 on this repository: `unsatisfied_tools(".")` returned `[]` while
    `importlib.metadata` could find neither `code-review-graph` nor
    `scancode-toolkit` — so the tool that scores the architecture dimension was
    neither installed nor reported.

    Asking about the distribution rather than the executable is also what
    bootstrap actually needs to know ("does pip still have work to do"), and it
    sidesteps the failure that motivated the skip: on a host whose pyicu
    conflicts with the system ICU, `scancode --version` fails forever while the
    distribution is present, so the pip round was re-run on every call and
    never helped.
    """
    from core.utils.venv_env import venv_scoped_env
    from harness.tool_checks import run_tool_check
    from harness.toolchains.registry import TOOL_SPECS

    root = Path(project)
    env = venv_scoped_env(root)
    python = venv_python(root)
    unsatisfied: list[str] = []
    for step in _ssot.PIP_STEPS:
        for tool_id in _ssot.tools_for_step(step.name):
            spec = TOOL_SPECS[tool_id]
            try:
                if spec.skip_inline:
                    dist = _distribution_name(tool_id)
                    if dist is None:
                        unsatisfied.append(
                            f"{tool_id} (no distribution name in the install SSOT — "
                            f"cannot tell whether pip has delivered it)"
                        )
                        continue
                    if python is None:
                        unsatisfied.append(
                            f"{tool_id} (no target interpreter to ask about "
                            f"{dist})"
                        )
                        continue
                    # The interpreter asked is the one being prepared, never
                    # the one asking (Round 47 F2).
                    proc = subprocess.run(
                        [str(python), "-c",
                         f"import importlib.metadata as m; m.distribution({dist!r})"],
                        capture_output=True, text=True, timeout=30,
                    )
                    if getattr(proc, "returncode", 1) != 0:
                        unsatisfied.append(tool_id)
                elif not run_tool_check(spec.check_cmd, cwd=str(root), env=env):
                    unsatisfied.append(tool_id)
            except Exception as exc:  # pylint: disable=broad-exception-caught
                # Fail CLOSED and say so: a probe that raised did not measure,
                # and "did not measure" is not "present".
                print(
                    f"[WARN] bootstrap-env: probe for {tool_id!r} raised ({exc}) "
                    f"— counting it as unsatisfied",
                    file=sys.stderr,
                )
                unsatisfied.append(f"{tool_id} (probe failed: {exc})")
    return unsatisfied


def main(argv: "list[str] | None" = None) -> int:
    parser = argparse.ArgumentParser(
        description="Create and populate the project virtualenv the harness runs from."
    )
    parser.add_argument("--project", default=".", help="target project root")
    parser.add_argument("--json", action="store_true", help="machine-readable report")
    args = parser.parse_args(argv)

    project = Path(args.project).resolve()
    report = bootstrap(project)

    if args.json:
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
        return 0 if report.ok else 1

    print(f"bootstrap-env: {project}")
    print(f"  interpreter : {report.python} ({'created' if report.venv_created else 'existing'})")
    print(f"  pip steps   : {', '.join(report.steps_run) or 'none'}")
    for failure in report.failures:
        # A pip failure is a diagnostic, not the verdict — the probes below are.
        print(f"  [pip] {failure}", file=sys.stderr)
    if report.still_missing_imports:
        print(
            "  [BLOCKED] still not importable in that interpreter: "
            + ", ".join(report.still_missing_imports),
            file=sys.stderr,
        )
    if report.still_missing_tools:
        print(
            "  [BLOCKED] still not resolvable after install: "
            + ", ".join(report.still_missing_tools),
            file=sys.stderr,
        )
    if report.ok:
        print("  OK — the harness CLI can run from this interpreter.")
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
