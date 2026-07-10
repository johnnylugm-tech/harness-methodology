"""Absolute-path loader for <harness_repo>/scripts helpers (moved from
harness_cli.py, S4e). Layout contract: this file lives at
core/utils/script_loader.py, so parents[2] is the repo root that contains
scripts/ — the same directory harness_cli.py sits in.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

__all__ = ["load_harness_script"]

def load_harness_script(module_filename: str):
    """Load a helper from `<harness_repo>/scripts/<name>.py` by absolute path.

    Bug fix P6-2026-07-07: cwd-relative `from scripts.X` failed whenever
    finalize-gate was run from the consumer project root (scripts/ lives
    under the harness submodule, not the consumer project's cwd / sys.path).
    Each generator is loaded by absolute file path so the call works
    regardless of cwd / PYTHONPATH.

    A1-2026-07-07 (completion): the same pattern existed in
    `_run_phase_auditor` (Site 2, silent skip + return 0) and
    `cmd_audit_phase` (Site 3, user-facing CLI; hard fail with traceback)
    — both hoisted to call this module-scope helper, eliminating 3
    inline duplicate copies.

    Layout contract:
      harness_repo             = Path(__file__).resolve().parent        (directory containing harness_cli.py)
      harness_repo / scripts   = location of helper modules
    Tests replicate this via
      tests/test_finalize_gate_helpers_load_via_absolute_path.py:39-40
    (`HARNESS_REPO / "scripts"`) so the .parent / "scripts" resolution
    below is the single source of truth. A dedicated
    `TestA1_HelperPathFix::test_load_harness_script_resolves_correct_scripts_dir`
    test invokes the real function (not the replicated path math).
    """
    harness_repo = Path(__file__).resolve().parents[2]  # core/utils/ → repo root
    target = harness_repo / "scripts" / module_filename
    if not target.is_file():
        raise ImportError(
            f"harness scripts helper not found: {target} "
            f"(cwd={Path.cwd()}, harness_repo={harness_repo})"
        )
    spec = importlib.util.spec_from_file_location(
        f"harness_runtime_{module_filename[:-3]}", target,
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load spec for {module_filename} at {target}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod
