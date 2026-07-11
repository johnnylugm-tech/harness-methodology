"""Absolute-path loader for <harness_repo>/scripts helpers (moved from
harness_cli.py, S4e). Layout contract: this file lives at
core/utils/script_loader.py, so parents[2] is the repo root that contains
scripts/ — the same directory harness_cli.py sits in.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

__all__ = ["load_harness_script", "harness_scripts_dir"]


def harness_scripts_dir() -> Path:
    """Return `<harness_repo>/scripts` by absolute path, independent of cwd.

    Round 5: hoisted out of `load_harness_script` so every caller that needs
    the scripts/ directory (whether to `importlib` a module or to build a
    `subprocess` command) shares one path computation. Two prior incidents
    each independently miscounted `.parent` levels: P6-2026-07-07's original
    inlined helper, and `cli/check_cmds.py:_generate_sab_json`'s subprocess
    path build (`Path(__file__).parent / "scripts"`, which resolves to the
    non-existent `cli/scripts/` — never swept by the P6/A1 fixes because
    those only grepped for `from scripts.X import`, not manual Path builds).
    """
    return Path(__file__).resolve().parents[2] / "scripts"  # core/utils/ → repo root / scripts


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

    Layout contract: see `harness_scripts_dir()`, the single source of
    truth for `<harness_repo>/scripts` resolution. Tests replicate this via
    tests/test_finalize_gate_helpers_load_via_absolute_path.py:39-40
    (`HARNESS_REPO / "scripts"`). A dedicated
    `TestA1_HelperPathFix::test_load_harness_script_resolves_correct_scripts_dir`
    test invokes the real function (not the replicated path math).
    """
    target = harness_scripts_dir() / module_filename
    if not target.is_file():
        raise ImportError(
            f"harness scripts helper not found: {target} "
            f"(cwd={Path.cwd()})"
        )
    spec = importlib.util.spec_from_file_location(
        f"harness_runtime_{module_filename[:-3]}", target,
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load spec for {module_filename} at {target}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod
