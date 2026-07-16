"""Top-level crash boundary (Round 13 站0).

harness_cli.py's main() had no crash boundary: an unhandled exception in
harness code produced a raw traceback and Python's default exit 1 —
indistinguishable from a normal "hard failure" documented at that same exit
code. A harness bug then leaked into the pipeline disguised as one of the
other failure classes (a GATE1 FAIL an agent tried to "fix", a BLOCK an
agent tried to satisfy) instead of being recognized as harness's own bug.
This module gives that path a distinct, machine-readable signal — see
docs/ERROR_HANDLING.md for the full block/degrade/warn taxonomy this fits
into. Wired into harness_cli.py's `_dispatch()`, not called directly by
individual cli/*.py commands.
"""

from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
import time
import traceback
from pathlib import Path

CRASH_DIR_RELPATH = ".sessi-work/crash"


def _project_from_argv(argv: list[str]) -> Path:
    """Best-effort `--project`/`-p` resolution mirroring harness_cli.py
    main()'s own .env-loading scan. Never raises — falls back to cwd."""
    for i, arg in enumerate(argv):
        if arg in ("--project", "-p") and i + 1 < len(argv):
            return Path(argv[i + 1])
        if arg.startswith("--project="):
            return Path(arg.split("=", 1)[1])
    return Path.cwd()


def _harness_git_sha() -> str:
    try:
        r = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=Path(__file__).resolve().parent.parent,
            capture_output=True, text=True, timeout=10,
        )
        return r.stdout.strip() or "unknown"
    except (subprocess.SubprocessError, OSError):
        return "unknown"


def format_harness_bug_banner(exc: BaseException, bundle_path: "Path | None") -> str:
    """The [HARNESS-BUG] banner. First line is machine-readable — workflow
    JS is taught the same escalate semantics as the existing [FATAL]
    structurally-broken-dispatch signature (see
    scripts/workflowgen/phase_specs.py): stop, do not retry, do not modify
    project code, report verbatim."""
    msg = str(exc).strip()
    summary = msg.splitlines()[0] if msg else "(no message)"
    lines = [
        f"[HARNESS-BUG] {type(exc).__name__}: {summary}",
        "  This is a bug in harness-methodology itself, NOT a problem with your project's code or tests.",
        "  STOP: do not retry, do not modify project code, do not attempt to fix this yourself.",
        "  Report this banner verbatim to the human operator and stop this FR/step.",
    ]
    if bundle_path is not None:
        lines.append(f"  Crash bundle: {bundle_path}")
    return "\n".join(lines)


def _maintenance_prompt(exc: BaseException, repro_cmd: str) -> str:
    return (
        f"harness-methodology crashed with {type(exc).__name__}: {exc}\n"
        f"Reproduce: {repro_cmd}\n"
        "Diagnose the root cause in harness-methodology's own code (not the "
        "target project's), write a regression test that reproduces this "
        "crash, then fix it. Follow SKILL.md's Phase 9 CR-BUG maintenance "
        "flow (`harness_cli.py cr-open --type bug ...`)."
    )


def write_crash_bundle(exc: BaseException, argv: list[str]) -> "Path | None":
    """Write a diagnostic bundle for an uncaught exception. Best-effort — a
    failure here must never mask the original exception, so every error is
    swallowed with a printed fallback rather than a second raise.

    Returns the bundle path, or None if the write itself failed.
    """
    try:
        project = _project_from_argv(argv)
        crash_dir = project / CRASH_DIR_RELPATH
        crash_dir.mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        bundle_path = crash_dir / f"crash_{ts}_{os.getpid()}.json"
        repro_cmd = " ".join([sys.executable, str(Path(__file__).resolve().parent.parent / "harness_cli.py"), *argv])
        bundle = {
            "timestamp": ts,
            "exc_type": type(exc).__name__,
            "exc_message": str(exc),
            "traceback": "".join(
                traceback.format_exception(type(exc), exc, exc.__traceback__)
            ),
            "argv": argv,
            "cwd": str(Path.cwd()),
            "project": str(project),
            "harness_git_sha": _harness_git_sha(),
            "python_version": platform.python_version(),
            "repro_command": repro_cmd,
            "maintenance_prompt": _maintenance_prompt(exc, repro_cmd),
        }
        bundle_path.write_text(
            json.dumps(bundle, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        return bundle_path
    except Exception as bundle_exc:  # noqa: BLE001 -- must never mask the original crash
        print(f"[WARN] failed to write crash bundle: {bundle_exc}", file=sys.stderr)
        return None
