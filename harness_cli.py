#!/usr/bin/env python3
"""
harness_cli.py — Standalone CLI for harness-methodology.

Standalone entrypoint for the harness-methodology repo.
Does NOT require the full parent system (cli.py needs 30+ external modules).

Usage:
    python harness_cli.py plan-phase       --phase 3 [--project .] [--output plan.md]
    python harness_cli.py run-phase        --phase 3 [--project .]
    python harness_cli.py run-gate         --gate 2  --phase 3 [--project .] [--fr-id FR-01]
    python harness_cli.py finalize-gate    --gate 2  --phase 3 [--project .] [--fr-id FR-01]
    python harness_cli.py generate-next-plan [--project .] [--phase 3]
    python harness_cli.py manifest         --fr-ids FR-01 FR-02 [--sad SAD.md]
    python harness_cli.py status           [--project .]
    python harness_cli.py effort           [--phase 3] [--project .]
    python harness_cli.py reload-policy    [--policy-file enforcement/enforcement.json]
    python harness_cli.py run-gap-analysis  [--project .] [--spec SPEC.md] [--similarity 0.6]
    python harness_cli.py audit-phase       --phase 3 --repo owner/repo [--branch main]
    python harness_cli.py verify-spec       [--project .]
    python harness_cli.py check-logic       [--project .] [--srs SRS.md]
    python harness_cli.py init-project      --project /path/to/target [--phase 3] [--overwrite]
    python harness_cli.py push-checkpoint   --phase 1|2 --project . [--fr-ids FR-01,FR-02]
    python harness_cli.py push-milestone    --type p3-mid|p3-pre-gate2|p3-post-gate2|p4-mid|p4-pre-gate3|p5-baseline|p7|p8 --project .
    python harness_cli.py advance-phase     --completed-phase 3 [--project .]
    python harness_cli.py dispatch          --role developer|reviewer --fr-id FR-01 --prompt "..." --phase 3

Gate Evaluation (two-phase flow):
    1. run-gate    → prints evaluation prompt for Claude; exits 0
    2. Claude evaluates inline, writes .sessi-work/gate{N}_result.json
    3. finalize-gate → reads result, checks thresholds, commits

Available gates:
    Gate 1  per-FR check       (P3/P4/P5/P7/P8, trigger: per_fr_completion)
    Gate 2  P3 phase-exit      (score_gate: 75, 9 dims)
    Gate 3  P4 phase-exit      (score_gate: 80, 14 dims, full CRG)
    Gate 4  P6 full-project    (score_gate: 85, 14 dims)

Exit codes:
    0   All phases complete
    1   Hard failure (investigate error)
    2   run-gap-analysis: critical gaps detected (distinct from hard error)
    5   Gate 4 prerequisites block (A2/A3/A5 schema, B2 score files)
    6   finalize-gate: gate passed but git commit did not land (manifest rolled back) — fix and re-run
    8   Missing deliverables block — required artifacts not found on disk or not git-tracked
    10  PAUSE — Claude must evaluate gate; run finalize-gate then re-run pipeline
    11  Phase Truth < 90% (HR-11); fix and re-run with --phase-from N
    16  (retired 減法 T3 — constitution keyword scoring is on-demand only)
    21  Scope violation: untracked diagnostic script(s) at repo root; move to
        .sessi-work/tmp or delete, then re-run advance-phase
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import warnings
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Optional

# Fail fast with a clear message when an unsupported Python is used.
# Common mistake: agents or shells use /usr/bin/python3 (macOS system 3.9)
# instead of the project's .venv/bin/python. The error otherwise propagates
# as a cryptic ImportError deep inside the call stack.
if sys.version_info < (3, 10):  # type: ignore[reportUnreachable]
    print(
        f"ERROR: harness-methodology requires Python 3.10+. "
        f"Got {sys.version.split()[0]} at {sys.executable}\n"
        "  Fix: run with .venv/bin/python or python3.10+ "
        "instead of /usr/bin/python3 (macOS system Python 3.9)"
    )
    sys.exit(1)

if TYPE_CHECKING:
    from harness.git_strategy import GitStrategy
    from harness.harness_bridge import GateBlockedError


# Ensure repo root on path so core/ and harness/ resolve
_REPO_ROOT = Path(__file__).parent
sys.path.insert(0, str(_REPO_ROOT))

# Script mode runs this file as __main__; register it under its module name
# too, so the cli/ family modules' `import harness_cli as _hc` binds THIS
# running module instead of re-executing the file (circular-import crash).
if __name__ == "__main__":  # pragma: no cover  (script-mode only)
    sys.modules.setdefault("harness_cli", sys.modules[__name__])

# Atomic state-file writers (CV-3 / SG-12 from robustness audit)
from core.atomic_io import atomic_write_json, file_lock, state_lock_path  # noqa: E402
from core.phase_topology import (  # noqa: E402
    ADVANCE_GATE1_CHECK_PHASES as _TOPOLOGY_ADVANCE_GATE1,
    ENTRY_GATE_MAP as _TOPOLOGY_ENTRY_GATES,
    EXIT_GATE_MAP as _TOPOLOGY_EXIT_GATES,
    PER_FR_GATE1_PHASES as _TOPOLOGY_PER_FR_GATE1,
)
from core.utils.project_layout import ProjectLayout  # noqa: E402
from core.canonical_form import canonical_form, fr_num_str  # noqa: E402, F401  # ID SSOT (fr_num_str: in-file + re-export)


# (Bug #105 compute_mutation_score import removed in S1 — cli/gate_cmds.py now
# imports it directly from core.quality_gate.mutation_enforcer.)
from core.quality_gate.legal_artifacts import PHASE_DELIVERABLES  # noqa: E402  # DRY: single source of truth shared with artifact_consistency.LEGAL_ARTIFACTS
# S2 extractions — call via module namespace (tool_checks.verify_…) so the
# only monkeypatch seam is the function's home module, never a harness_cli
# attribute that could go stale.
from core import claude_md  # noqa: E402
from core.quality_gate import gate1_evidence  # noqa: E402
from core.utils.script_loader import load_harness_script  # noqa: E402, F401  (public re-export: tests + cli import it from here)
from core.utils import env_loader  # noqa: E402
from harness import tool_checks  # noqa: E402

# Phases where Gate 1 runs per-FR (P9 maintenance: per-CR touched FRs).
# Sourced from the topology SSOT (core/phase_topology.py) — do not re-declare.
_PER_FR_GATE1_PHASES: frozenset[int] = _TOPOLOGY_PER_FR_GATE1








# Entry gate required per phase (CONSTITUTION.md §2.3)
# Single source of truth: core/phase_topology.py
_ENTRY_GATE_MAP: dict[int, int] = _TOPOLOGY_ENTRY_GATES

# Phase → composite exit gate number (topology SSOT)
_PHASE_EXIT_GATES: dict[int, int] = _TOPOLOGY_EXIT_GATES

# Phases that require Gate 1 per-FR evaluation during advance-phase.
# Phase 6 (Quality Assurance) has no FR loop — it uses Gate 4 exclusively.
# Phase 9 (Maintenance) is deliberately absent: advance-phase --completed 9
# is always BLOCKED (terminal steady state), so its Gate 1 records are
# checked per-CR by cr-close, not here. Expressed as a derivation in
# core/phase_topology.py so it can never drift from PER_FR_GATE1_PHASES.
_PHASES_WITH_GATE1_FR_CHECK: frozenset[int] = _TOPOLOGY_ADVANCE_GATE1

# P1/P2 deliverable labels used as approval-file keys in agent_b_approvals/
# Authoritative list lives in `core.quality_gate.legal_artifacts` (single source
# of truth shared with `core.quality_gate.artifact_consistency.LEGAL_ARTIFACTS`).
_PHASE_DELIVERABLES = PHASE_DELIVERABLES  # re-export for backward compat (see legal_artifacts.py)

# ---------------------------------------------------------------------------
# plan-phase
# ---------------------------------------------------------------------------

# Moved verbatim to cli/phase_cmds.py (方案六). Re-exported so
# existing `from harness_cli import ...` imports keep working.
from cli._shared import (  # noqa: E402, F401  (S4g interim — dies with the impls in S4h)
    _finalize_sentinel_path,
    _generate_stage_pass,
    _run_phase_auditor,
    _sentinel_path,
    _write_finalize_sentinels_for_tests,
)
from cli.phase_cmds import (  # noqa: E402, F401
    cmd_advance_phase,
    cmd_generate_next_plan,
    cmd_plan_all,
    cmd_plan_phase,
    cmd_pre_commit_check,
    cmd_run_phase,
    cmd_sync_harness,
    cmd_validate_handoff,
)
# ---------------------------------------------------------------------------
# plan-all
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# run-phase
# ---------------------------------------------------------------------------



# D4 spec-coverage cluster moved verbatim to core/quality_gate/spec_coverage.py
# (方案六: core must not import the CLI layer). Re-exported here for the
# in-file callers, cli/ families (_hc), and existing monkeypatch targets.
from core.quality_gate.spec_coverage import (  # noqa: E402, F401
    _collect_shared_test_files,
    _flatten_test_names,
    _get_test_directories,
    _git_test_patterns,
    _parse_inventory_fallback,
    _parse_test_spec,
    _run_spec_coverage_check,
    _scan_test_functions,
)



def _check_fr_test_file_exists(project: Path, fr_id: str) -> tuple[bool, str]:
    """Gate 1: verify a test file exists for the given FR (TDD RED phase).

    Accepts test_fr07.py or test_fr7.py naming. Skips non-standard FR-IDs.
    Called during cmd_finalize_gate Gate 1 path.
    """
    m = re.match(r"FR-(\d+)", fr_id, re.IGNORECASE)
    if not m:
        return True, ""
    num = m.group(1).zfill(2)
    test_dirs = _get_test_directories(project)
    if not test_dirs:
        test_dirs = [project / "tests"]  # default fallback
        
    patterns = [f"test_fr{num}.py", f"test_fr{str(int(num))}.py"]
    for test_dir in test_dirs:
        for pat in patterns:
            if (test_dir / pat).exists():
                return True, ""
    return False, (
        f"[BLOCKED] FR test file missing: tests/test_fr{num}.py\n"
        "  TDD requires a test file BEFORE implementation is merged.\n"
        "  Create tests/test_fr{num}.py with at minimum one failing test."
    )

def _check_red_phase_ordering(project: Path, fr_id: str) -> tuple[bool, str]:
    """D1 extension: test first commit must be an ancestor of source first commit.

    Uses git ancestry (merge-base --is-ancestor) rather than author timestamps:
    immune to clock skew, sub-second jitter, and glob mis-matches that pick up
    wrong files in nested test directories (e.g. 03-development/tests/).

    Source exclude uses :(glob,exclude) magic pathspec to recursively skip ALL
    test directories and test files, regardless of nesting depth — fixing the
    issue where :(exclude)tests/ only excluded the repo-root tests/ directory.

    Supports configurable source_patterns in project.json for non-standard layouts.
    TDD_JITTER_TOLERANCE is no longer needed and is ignored.
    """
    m = re.match(r"FR-(\d+)", fr_id, re.IGNORECASE)
    if not m:
        return True, ""
    num = m.group(1).zfill(2)
    num_raw = str(int(num))
    test_patterns = _git_test_patterns(project, num, num_raw)

    def _first_sha(patterns: list[str],
                   exclude_globs: list[str] | None = None) -> str | None:
        """Return the SHA of the earliest 'A'dd commit matching any pattern.

        Uses --format='%at %H' to get timestamp + SHA, then returns the SHA
        of the earliest match (handles files added, deleted, re-added).
        exclude_globs uses :(glob,exclude) pathspec — recursively excludes any
        path matching the pattern at any directory depth.
        """
        cmd = ["git", "-C", str(project), "log", "--diff-filter=A",
               "--format=%at %H", "--"]
        cmd.extend(patterns)
        if exclude_globs:
            for exc in exclude_globs:
                cmd.append(f":(glob,exclude){exc}")
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        except subprocess.TimeoutExpired:
            return None
        best: tuple[float, str] | None = None
        for line in r.stdout.splitlines():
            parts = line.strip().split(" ", 1)
            if len(parts) == 2:
                try:
                    ts, sha = float(parts[0]), parts[1].strip()
                    if best is None or ts < best[0]:
                        best = (ts, sha)
                except ValueError:
                    continue
        return best[1] if best else None

    test_sha = _first_sha(test_patterns)
    if test_sha is None:
        return False, (
            f"[BLOCKED] D1-RED: tests/test_fr{num}.py has no git history.\n"
            "  Commit the failing test BEFORE implementing the source."
        )

    # Source glob patterns — :(glob,exclude) recursively excludes ALL test
    # directories at any depth (fixes the 03-development/tests/ mis-match).
    src_patterns = [
        f":(glob)**/fr{num_raw}*",
        f":(glob)**/*fr_{num_raw}*",
        f":(glob)**/*fr{num}*",
    ]
    _src_exclude = ["**/tests/**", "**/test_*.py"]

    config_path = project / "project.json"
    if config_path.exists():
        try:
            config = json.loads(config_path.read_text())
            overrides = config.get("source_patterns", {})
            fr_overrides = overrides.get(f"FR-{num_raw}", overrides.get(f"FR-{num}", []))
            if fr_overrides:
                src_patterns = fr_overrides if isinstance(fr_overrides, list) else [fr_overrides]
        except (json.JSONDecodeError, OSError):
            pass

    src_sha = _first_sha(src_patterns, exclude_globs=_src_exclude)
    if src_sha is None:
        return True, ""   # no source committed yet — TDD-RED phase is valid

    # Ancestry check: test_sha must be an ancestor of src_sha.
    # exit 0 → test came before source → OK (RED before GREEN).
    # exit 1 → source is not descended from test → source committed first → BLOCKED.
    try:
        anc = subprocess.run(
            ["git", "-C", str(project), "merge-base", "--is-ancestor",
             test_sha, src_sha],
            capture_output=True, timeout=10,
        )
        if anc.returncode != 0:
            return False, (
                f"[BLOCKED] D1-RED: Source was committed before test for {fr_id}.\n"
                f"  test commit : {test_sha[:12]}\n"
                f"  source commit: {src_sha[:12]}\n"
                "  TDD requires RED (failing test commit) → GREEN (source commit).\n"
                "  The test file's first commit must be an ancestor of the source "
                "file's first commit on the current branch."
            )
    except subprocess.TimeoutExpired:
        return True, ""   # ancestry check timed out → fail-open (non-fatal)
    return True, ""

# Moved verbatim to cli/check_cmds.py (方案六). Re-exported so
# existing `from harness_cli import ...` imports keep working.
from cli.check_cmds import (  # noqa: E402, F401
    cmd_bug_hunt_targets,
    cmd_build_trace_attestation,
    cmd_check_constitution,
    cmd_check_logic,
    cmd_check_test_mirrors_spec,
    cmd_check_test_spec_consistency,
    cmd_crg_arch_check,
    cmd_generate_verification_report,
    cmd_manifest,
    cmd_migrate_trace_overlay,
    cmd_run_gap_analysis,
    cmd_spec_coverage_check,
    cmd_verify_agent_b_approvals,
    cmd_verify_file,
    cmd_verify_spec,
    cmd_verify_trace,
    cmd_write_approval,
)
















# ---------------------------------------------------------------------------
# run-gate  (Phase 1 of two-phase evaluation)
# ---------------------------------------------------------------------------

def _fr_source_files_from_imports(
    project: Path, test_file: str, src_dir: str
) -> list[str]:
    """Return source files under src_dir that are imported by test_file.

    Parses the test file with ast and matches imported module paths against
    .py files under src_dir.  Returns relative-to-project paths, e.g.
    ["03-development/src/omnibot/adapters/telegram_adapter.py"].

    Returns [] when the test file is absent, unparseable, or no matches are
    found — callers should fall back to the full src_dir in that case.
    """
    import ast as _ast

    test_path = project / test_file
    if not test_path.exists():
        return []
    try:
        tree = _ast.parse(test_path.read_text(encoding="utf-8"))
    except SyntaxError:
        return []

    # Collect every dotted name that appears in an import statement.
    imported: set[str] = set()
    for node in _ast.walk(tree):
        if isinstance(node, _ast.Import):
            for alias in node.names:
                imported.add(alias.name)
        elif isinstance(node, _ast.ImportFrom):
            if node.module:
                imported.add(node.module)
                # "from pkg import name" also covers pkg.name
                for alias in node.names:
                    imported.add(f"{node.module}.{alias.name}")

    src_path = project / src_dir
    if not src_path.exists():
        return []

    matched: list[str] = []
    for py_file in sorted(src_path.rglob("*.py")):
        if py_file.name == "__init__.py":
            continue
        # Convert file path to dotted module name relative to src_dir root.
        try:
            rel_parts = py_file.relative_to(src_path).with_suffix("").parts
        except ValueError:
            continue
        module_dot = ".".join(rel_parts)
        # Match if any imported name equals or is a sub-path of the module.
        for imp in imported:
            if imp == module_dot or imp.startswith(module_dot + "."):
                matched.append(str(py_file.relative_to(project)))
                break

    # Layer 2 (auto): follow __init__.py re-exports for imported packages.
    # When a test does `from omnibot.queries import ODD_QUERIES`, the AST sees
    # the package `omnibot.queries` but not the submodule `odd_queries.py` that
    # __init__.py re-exports.  One level of __init__.py expansion catches this.
    _seen_dirs: set[str] = set()
    for imp in list(imported):
        pkg_candidate = src_path / Path(*imp.split("."))
        if not pkg_candidate.is_dir():
            continue
        pkg_key = str(pkg_candidate)
        if pkg_key in _seen_dirs:
            continue
        _seen_dirs.add(pkg_key)
        init_file = pkg_candidate / "__init__.py"
        if not init_file.exists():
            continue
        try:
            init_tree = _ast.parse(init_file.read_text(encoding="utf-8"))
        except (SyntaxError, OSError):
            continue
        for node in _ast.walk(init_tree):
            if not isinstance(node, _ast.ImportFrom):
                continue
            mod = node.module or ""
            # Resolve relative import: "from .sub import X" inside pkg/__init__.py
            rel_mod = mod.lstrip(".")
            for seg in (rel_mod, *(f"{alias.name}" for alias in node.names)):
                candidate = pkg_candidate / f"{seg}.py"
                if candidate.exists():
                    rel = str(candidate.relative_to(project))
                    if rel not in matched:
                        matched.append(rel)

    return matched


def _print_fr_scoped_overrides_py(
    project: str,
    fr_id: str,
    test_file: str,
    src_dir: str,
    manifest_data: dict,
    *,
    non_code_frs: set[str],
    cov_threshold: int,
) -> None:
    """Print Gate-1 FR-scoped tool commands for a Python project."""
    # Priority 1: fr_module_traceability from manifest (authoritative mapping
    # FR-XX → owned module). This avoids the import-based scope problem where
    # a TDD test imports helpers from other FRs' modules, inflating the
    # scope and diluting coverage. Example: test_fr04.py imports
    # taskq.{cli, store, executor, config, models, cache} as helpers, but
    # fr_module_traceability["FR-04"] = "taskq.cache" says FR-04 owns cache
    # only — measuring all 6 modules reports ~17% per FR instead of 100%.
    # Accepts str or list[str]; malformed entries (".", "..", empty,
    # path-traversal, non-string) emit a warning and fall back to imports
    # rather than crashing the audit with ValueError from Path.with_suffix.
    src_files: list[str] = []
    fr_trace = manifest_data.get("fr_module_traceability", {}).get(fr_id)
    trace_entries: list[str] = []
    if isinstance(fr_trace, str):
        trace_entries = [fr_trace]
    elif isinstance(fr_trace, list):
        non_str = [t for t in fr_trace if not isinstance(t, str)]
        trace_entries = [t for t in fr_trace if isinstance(t, str)]
        if non_str:
            warnings.warn(
                f"fr_module_traceability[{fr_id}] contains non-string entries; "
                f"non-string entries ignored",
                stacklevel=3,
            )
    elif fr_trace is not None:
        warnings.warn(
            f"fr_module_traceability[{fr_id}] is {type(fr_trace).__name__}, "
            f"expected str or list[str]; falling back to import-based detection",
            stacklevel=3,
        )

    for trace in trace_entries:
        parts = trace.replace("\\", "/").split("/")
        if not trace or any(p in (".", "..") for p in parts):
            warnings.warn(
                f"fr_module_traceability[{fr_id}]={trace!r} is malformed "
                f"(empty or contains '.' / '..' path segment); skipped",
                stacklevel=3,
            )
            continue
        try:
            owned_path = (
                Path(project) / src_dir
                / Path(trace.replace(".", "/")).with_suffix(".py")
            )
        except ValueError as exc:
            warnings.warn(
                f"fr_module_traceability[{fr_id}]={trace!r} produced invalid "
                f"path ({exc}); skipped",
                stacklevel=3,
            )
            continue
        if owned_path.exists():
            # Fix III: when owned_path is a thin re-export shim (≤ 5 lines
            # after stripping comments) and a package directory with the same
            # stem exists next to it, coverage --include must match the WHOLE
            # package (e.g. executor/**/*.py), not just the shim file.
            # Without this, FR-02 executor shows 0% coverage because the real
            # code lives in executor/runner.py.
            pkg_dir = owned_path.with_suffix("")
            if pkg_dir.is_dir() and (pkg_dir / "__init__.py").exists():
                # Use recursive glob to cover the package directory
                pkg_glob = str(owned_path.relative_to(project).with_suffix("") / "**" / "*.py")
                src_files.append(pkg_glob)
            else:
                src_files.append(str(owned_path.relative_to(project)))
        else:
            # Fix III extension: .py file doesn't exist at all (e.g. executor.py
            # was never created, but executor/__init__.py + executor/runner.py
            # exist as an untracked package). Use recursive glob to match the
            # whole package directory.
            pkg_dir = owned_path.with_suffix("")
            if pkg_dir.is_dir() and (pkg_dir / "__init__.py").exists():
                pkg_glob = str(owned_path.relative_to(project).with_suffix("") / "**" / "*.py")
                src_files.append(pkg_glob)

    # Priority 2: detect FR-specific source files by parsing the test file's
    # imports. Used when fr_module_traceability is absent or the owned path
    # does not exist on disk.
    if not src_files:
        src_files = _fr_source_files_from_imports(Path(project), test_file, src_dir)

    # Issue 4: manual fr_scope_overrides — merges declared files into scope.
    # Use when __init__.py transitive re-exports can't be auto-detected.
    # Add to quality_manifest.json: {"fr_scope_overrides": {"FR-16": ["path/to/file.py"]}}
    scope_override = manifest_data.get("fr_scope_overrides", {}).get(fr_id, [])
    if scope_override:
        src_files = list(dict.fromkeys(src_files + scope_override))

    if not src_files and fr_id in non_code_frs:
        print(
            f"\n[FR-SCOPED TOOL OVERRIDES — {fr_id}]\n"
            f"Gate 1 scope is single_fr. Replace the project-wide defaults in\n"
            f"evaluate_dimension.md with these FR-scoped commands:\n\n"
            f"test_coverage — {fr_id} is declared as a non-code FR "
            f"(no scoreable source to measure):\n"
            f"  echo 'NON_CODE_FR: coverage not applicable'\n"
            f"  Score this dimension as {cov_threshold} (= threshold). "
            f"Infrastructure/config FRs are exempt from coverage measurement.\n"
            f"  Set tool_evidence = 'non-code FR: {fr_id} declared in fr_non_code'\n\n"
            f"linting — lint only the FR source directory:\n"
            f"  ruff check {src_dir}/ --extend-ignore RUF001,RUF002,RUF003 2>&1 | head -200\n\n"
            f"type_safety — type-check only the FR source directory:\n"
            f"  pyright {src_dir}/ --outputjson 2>&1 | head -200\n"
        )
        return

    if src_files:
        include_flag = ",".join(src_files)
        cov_cmd = (
            f"  coverage run -m pytest {test_file} "
            f"&& coverage report --include=\"{include_flag}\" --format=json \\\n"
            f"    || PYTHONPATH=. coverage run -m pytest {test_file} "
            f"&& coverage report --include=\"{include_flag}\" --format=json \\\n"
            f"    || PYTHONPATH=. python3 -m pytest {test_file} "
            f"--cov={src_dir} --cov-report=term-missing"
        )
        cov_note = f"  (FR source files detected: {', '.join(src_files)})"
    else:
        # Fallback: test file absent or no imports matched — use full src dir
        cov_cmd = (
            f"  coverage run --source={src_dir} -m pytest {test_file} "
            f"&& coverage report --format=json \\\n"
            f"    || PYTHONPATH=. coverage run --source={src_dir} -m pytest {test_file} "
            f"&& coverage report --format=json \\\n"
            f"    || PYTHONPATH=. python3 -m pytest {test_file} "
            f"--cov={src_dir} --cov-report=term-missing"
        )
        cov_note = f"  (fallback: {src_dir} — test file not found or no imports detected)"

    print(
        f"\n[FR-SCOPED TOOL OVERRIDES — {fr_id}]\n"
        f"Gate 1 scope is single_fr. Replace the project-wide defaults in\n"
        f"evaluate_dimension.md with these FR-scoped commands:\n\n"
        f"test_coverage — measure only {fr_id}'s source files:\n"
        f"{cov_cmd}\n"
        f"{cov_note}\n\n"
        f"linting — lint only the FR source directory:\n"
        f"  ruff check {src_dir}/ --extend-ignore RUF001,RUF002,RUF003 2>&1 | head -200\n\n"
        f"type_safety — type-check only the FR source directory:\n"
        f"  pyright {src_dir}/ --outputjson 2>&1 | head -200\n"
    )


def _print_fr_scoped_overrides_js(
    project: str,
    fr_id: str,
    num_str: str,
    test_dir_str: str,
    *,
    non_code: bool,
    cov_threshold: int,
) -> None:
    """Print Gate-1 FR-scoped tool commands for a JS/TS project.

    Per-FR scoping uses the test-TITLE filter (-t "test_frNN") instead of a
    source-file include list: the harness naming convention guarantees every
    FR test title starts with test_frNN, and both vitest and jest support
    title filtering natively.
    """
    from harness.toolchains import get_project_language, get_project_test_runner
    language = get_project_language(project)
    runner = get_project_test_runner(project) or "vitest"

    if non_code:
        print(
            f"\n[FR-SCOPED TOOL OVERRIDES — {fr_id}]\n"
            f"Gate 1 scope is single_fr. Replace the project-wide defaults in\n"
            f"evaluate_dimension.md with these FR-scoped commands:\n\n"
            f"test_coverage — {fr_id} is declared as a non-code FR "
            f"(no scoreable source to measure):\n"
            f"  echo 'NON_CODE_FR: coverage not applicable'\n"
            f"  Score this dimension as {cov_threshold} (= threshold). "
            f"Infrastructure/config FRs are exempt from coverage measurement.\n"
            f"  Set tool_evidence = 'non-code FR: {fr_id} declared in fr_non_code'\n\n"
            f"linting — lint the project (eslint scope comes from eslint.config.mjs):\n"
            f"  npx --no-install eslint . -f json 2>&1 | head -200\n\n"
            f"type_safety — type-check the project:\n"
            f"  npx --no-install tsc --noEmit --pretty false 2>&1; echo \"tsc exit=$?\"\n"
        )
        return

    if runner == "jest":
        cov_cmd = (
            f"  npx --no-install jest -t \"test_fr{num_str}\" --coverage --ci \\\n"
            f"    --coverageReporters=json-summary --coverageReporters=text"
        )
    else:
        cov_cmd = (
            f"  npx --no-install vitest run {test_dir_str} -t \"test_fr{num_str}\" "
            f"--coverage \\\n"
            f"    --coverage.reporter=json-summary --coverage.reporter=text"
        )
    tsc_cmd = (
        "npx --no-install tsc -p tsconfig.checkjs.json --noEmit --pretty false"
        if language == "javascript"
        else "npx --no-install tsc --noEmit --pretty false"
    )
    print(
        f"\n[FR-SCOPED TOOL OVERRIDES — {fr_id}]\n"
        f"Gate 1 scope is single_fr. Replace the project-wide defaults in\n"
        f"evaluate_dimension.md with these FR-scoped commands:\n\n"
        f"test_coverage — run only {fr_id}'s tests (title filter), then read\n"
        f"coverage/coverage-summary.json total.lines.pct:\n"
        f"{cov_cmd}\n"
        f"  (convention: every {fr_id} test title starts with test_fr{num_str})\n\n"
        f"linting — eslint scope comes from eslint.config.mjs:\n"
        f"  npx --no-install eslint . -f json 2>&1 | head -200\n\n"
        f"type_safety — type-check the project (tsconfig owns the include set):\n"
        f"  {tsc_cmd} 2>&1; echo \"tsc exit=$?\"\n"
    )


# Moved verbatim to cli/gate_cmds.py (方案六). Re-exported so
# existing `from harness_cli import ...` imports keep working.
from cli.gate_cmds import (  # noqa: E402, F401
    cmd_finalize_env_check,
    cmd_finalize_gate,
    cmd_gate4_tag,
    cmd_mutation_test_score,
    cmd_run_env_check,
    cmd_run_gate,
)

def _normalize_sab_module_to_dotted(mod: object) -> Optional[str]:
    """Normalise a SAB ``modules`` entry into a dotted module name.

    Delegates to `core.quality_gate.sab_amender.normalize_sab_module_to_dotted`
    — the single source of truth for this normalization — so this alignment
    check and `amend_sab` can never silently disagree about which modules
    are "registered".
    """
    from core.quality_gate.sab_amender import normalize_sab_module_to_dotted
    return normalize_sab_module_to_dotted(mod)


def _filter_phantoms_for_fr(project: str, fr_id: str, phantoms: set[str]) -> set[str]:
    """Narrow a global phantom-module set to what `fr_id`'s Gate 1 should block on.

    Gate 1 runs once per FR, in sequence (P3/P5/P7/P8 per harness/CLAUDE.md's
    Gate Status Reference). A phantom module is only this FR's problem when
    it's owned by `fr_id` itself, or owned by an FR that has ALREADY passed
    Gate 1 (real regression — that FR claimed done but the module is now
    missing). A module owned by an FR not yet gated simply hasn't been built
    yet by sequencing, not by drift.

    A module with NO owner in `fr_module_traceability` (shared/entry-layer
    scaffolding like config/models/__main__) is not skipped here even though
    it isn't blocked — it's simply not blockable at this per-FR gate, because
    no single FR's TDD loop is responsible for building it, so blocking here
    only punishes whichever FR happens to be gated first (2026-07-08 false-
    block: taskq.config/models/breaker/store/__main__ have no FR owner and
    would BLOCK any early FR forever). The real, unconditional enforcement
    point for a permanently-missing SAB module is `preflight_sab_check`
    (core/phase_hooks.py:341), which checks every SAB-layer module against
    disk regardless of FR ownership, gated at P4 entry (`self.phase >= 4`) —
    that already closes the original 6436ab6 orphan case; this per-FR gate
    doesn't need to duplicate it.

    Ownership lookup reuses `fr_module_traceability` — the same manifest
    field `_print_fr_scoped_overrides_py`/`_js` already use for per-FR
    scoping — and `_normalize_sab_module_to_dotted` so ownership keys and
    the phantom set being filtered agree on format. Manifest unreadable →
    stay conservative and return `phantoms` unfiltered (original behavior).
    """
    manifest_path = Path(project) / ".methodology" / "quality_manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception:
        return phantoms

    gate1_results = manifest.get("gate_results", {}).get("gate1", {})
    passed_frs = {
        fr for fr, result in gate1_results.items()
        if isinstance(result, dict) and result.get("quality_complete") is True
    }

    owner_of: dict[str, str] = {}
    for owner_fr, entries in manifest.get("fr_module_traceability", {}).items():
        mods = [entries] if isinstance(entries, str) else (entries if isinstance(entries, list) else [])
        for m in mods:
            dotted = _normalize_sab_module_to_dotted(m)
            if dotted is not None:
                owner_of.setdefault(dotted, owner_fr)

    return {
        mod for mod in phantoms
        if owner_of.get(mod) == fr_id or owner_of.get(mod) in passed_frs
    }


def _check_sab_module_alignment(project: str, gate: int, fr_id: Optional[str] = None) -> Optional[int]:
    """Gate 1 Architecture Amendment Protocol: block on bidirectional SAB drift.

    Returns 1 when gate==1 and either:
      (a) unregistered: at least one .py file in src/ is absent from SAB.json, OR
      (b) phantom: SAB.json declares modules the codebase has not implemented.
    Returns None when the check is skipped (gate != 1, SAB.json missing, no src dir)
    or when SAB and codebase are symmetrically aligned.

    SAB ``modules`` entries may be expressed in either dotted
    (``taskq.cli``) or path (``03-development/src/taskq/cli.py``) form;
    both are normalised to dotted names before comparison so the check
    agrees with `drift_detector.sab_module_to_path_variants`.

    Phantom detection (the (b) branch) closes the silent gap that previously
    let P2 architecture planning register `taskq.config` / `taskq.models`
    layers survive into P4 uncaught. The implementation delegates to
    `core.quality_gate.sab_amender.phantom_modules` so this check, the
    standalone `amend-sab` CLI, and `preflight_sab_check` (P4+) all agree
    on what "phantom" means — three callers, one definition.

    Bug class: P2-SAB-drift — first surfaced 2026-07-06 during phase4-testing
    E2E, where preflight_sab_check BLOCKED with "Layer config: 1 modules
    missing from codebase" because nothing had enforced (b) at any earlier
    gate. Pushing the symmetric check down to Gate 1 forces amendment at
    the earliest point where recovery is still cheap (P2 amendment protocol
    or P3 implementation).

    Per-FR scoping (2026-07-08 fix): the (b) phantom check above is
    project-wide by construction, but Gate 1 is documented/designed as
    per-FR (see harness/CLAUDE.md Gate Status Reference, and
    `_print_fr_scoped_overrides_py`/`_js` using the same
    `fr_module_traceability` mapping). Phase 3 gates FRs sequentially, so
    gating an early FR (e.g. FR-01) was tripping on later FRs' modules that
    legitimately don't exist yet — see `_filter_phantoms_for_fr`. Passing
    `fr_id` narrows the phantom set to that FR's own scope before deciding
    whether to block; `fr_id=None` preserves the original unscoped check.
    """
    if gate != 1:
        return None
    sab_path = Path(project) / ".methodology" / "SAB.json"
    src_dir = ProjectLayout(project).active_src_dir
    if not (sab_path.exists() and src_dir.exists()):
        return None
    try:
        sab_data = json.loads(sab_path.read_text(encoding="utf-8"))
        sab_modules: set[str] = set()
        for layer in sab_data.get("layers", []):
            for mod in layer.get("modules", []):
                dotted = _normalize_sab_module_to_dotted(mod)
                if dotted is not None:
                    sab_modules.add(dotted)

        actual_modules: set[str] = set()
        for py_file in src_dir.rglob("*.py"):
            if py_file.name == "__init__.py":
                continue
            rel_path = py_file.relative_to(src_dir)
            mod_name = ".".join(rel_path.with_suffix("").parts)
            actual_modules.add(mod_name)

        unregistered = actual_modules - sab_modules
        if unregistered:
            print(
                f"\n[BLOCKED] run-gate: Architecture Amendment Protocol violation.\n"
                f"Unregistered modules detected: {unregistered}\n"
                f"You must create an Amendment PR to update SAB.json and SAD.md "
                f"before Gate 1 evaluation can proceed."
            )
            return 1

        # Phantom check: SAB declares modules the codebase lacks. Use the
        # shared helper so the message + handling stay in sync with
        # `preflight_sab_check` (P4+) and the standalone `amend-sab` CLI.
        from core.quality_gate.sab_amender import phantom_modules as _phantom
        phantoms = set(_phantom(sab_data, actual_modules))
        if phantoms and fr_id:
            phantoms = _filter_phantoms_for_fr(project, fr_id, phantoms)
        if phantoms:
            print(
                f"\n[BLOCKED] run-gate: Architecture Amendment Protocol violation.\n"
                f"Phantom modules declared in SAB.json but not implemented in codebase: {sorted(phantoms)}\n"
                f"You must either:\n"
                f"  (a) implement them in 03-development/src/<module>.py, OR\n"
                f"  (b) amend SAB.json to remove them from the layer's modules list\n"
                f"      (and sync the SAD.md sections that reference the removed modules — "
                f"amend-sab does not edit SAD.md).\n"
                f"Phantom drift caught here (Gate 1) so recovery is still cheap — "
                f"otherwise P4 preflight will block on the same drift with no path back to P2 amendment."
            )
            return 1
    except Exception as e:
        print(f"Warning: SAB Module Alignment Check failed to parse: {e}")
    return None


def _cmd_run_gate_impl(args: argparse.Namespace) -> int:
    """
    Phase 1: prepare gate context and print evaluation instructions for Claude.

    Claude must evaluate inline and write .sessi-work/gate{N}_result.json,
    then call `finalize-gate` to complete threshold checks and git operations.

    Delta-check mode (--delta, P5/P7/P8): skips full re-evaluation when FR
    code hasn't changed since last Gate 1. Previous score is reused.
    """
    delta = getattr(args, "delta", False)
    fr_id = getattr(args, "fr_id", None) or None

    # ── Delta-check: skip re-evaluation if FR code unchanged ────────────
    if delta and fr_id:
        project_path = Path(args.project).resolve()
        manifest_path = project_path / ".methodology" / "quality_manifest.json"
        if manifest_path.exists():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                prev_score = (
                    manifest.get("gate_results", {})
                    .get("gate1", {})
                    .get(fr_id, {})
                    .get("score")
                )
            except Exception:
                prev_score = None

            if prev_score is not None:
                print(f"\n{'='*60}")
                print(f"DELTA-CHECK: {fr_id} — reusing previous Gate 1 score ({prev_score})")
                print("  (No code changes detected or delta-mode active)")
                print(f"{'='*60}")
                return 0

    from harness.harness_bridge import HarnessBridge

    project = str(Path(args.project).resolve())

    # Block evaluation before printing the prompt — prevents fabrication via
    # "evaluate without tools, then install stub to pass finalize-gate".
    _tools_ok, _missing_tools = tool_checks.verify_gate_tools(args.gate, project)
    if not _tools_ok:
        print(
            f"\n[BLOCKED] run-gate: required tools not installed for Gate {args.gate}:\n"
            + "".join(f"  ✗ {m}\n" for m in _missing_tools)
            + "\n  Install tools before starting evaluation.\n"
            "  tool_score=null is not accepted for Tier 1/2 dimensions (R8).\n"
            "  See evaluate_dimension.md Step 1 for install commands."
        )
        return 8

    # Architecture Amendment Protocol: Module Alignment Check (Gate 1)
    _amend_result = _check_sab_module_alignment(project, args.gate, fr_id)
    if _amend_result is not None:
        return _amend_result

    bridge = HarnessBridge()

    print(f"\n{'='*60}\nrun-gate: Gate {args.gate} | Phase {args.phase}\n{'='*60}")

    ctx = bridge.prepare_gate(
        gate_num=args.gate,
        project_root=project,
        phase=args.phase,
        fr_id=fr_id,
    )

    print(ctx.evaluation_prompt())

    # Gate 1 scope is single_fr — inject FR-scoped tool command overrides so
    # the evaluator only measures coverage for this FR's source files, not the
    # entire project (which dilutes the score with other FRs at 0%).
    if fr_id and args.gate == 1:
        # I: use canonical_form() — handles all variants (FR-01, fr01, FR_01, etc.)
        try:
            _canon = canonical_form(fr_id)
        except ValueError:
            _canon = fr_id
        _num_match = re.match(r"FR-(\d+)", _canon)
        _num_str = (
            _num_match.group(1).zfill(2)
            if _num_match
            else _canon
        )
        _layout = ProjectLayout(project)
        _test_dir_str = _layout.get_relative_str(_layout.active_test_dir)
        _test_file = f"{_test_dir_str}/test_fr{_num_str}.py"
        _src_dir = "03-development/src"

        # Load quality_manifest for per-FR overrides (scope + non-code flag).
        _manifest_data: dict = {}
        _manifest_path_g = Path(project) / ".methodology" / "quality_manifest.json"
        try:
            _manifest_data = json.loads(_manifest_path_g.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass

        # Issue 3 (generalized): non-code FRs (Docker Compose, SQL, YAML) have
        # no scoreable source. When scope is empty and the FR is declared
        # non-code, bypass coverage measurement and assign threshold directly.
        # quality_manifest.json: {"fr_non_code": ["FR-15"]} — the pre-v2.8 key
        # fr_non_python is honored as an alias.
        _non_code_frs = (
            set(_manifest_data.get("fr_non_code", []))
            | set(_manifest_data.get("fr_non_python", []))
        )
        _cov_threshold = int(
            _manifest_data.get("quality_targets", {}).get("min_coverage", 80)
        )

        from core.utils.lang_patterns import project_language as _proj_lang
        _language = _proj_lang(Path(project))
        if _language in ("javascript", "typescript"):
            _print_fr_scoped_overrides_js(
                project, fr_id, _num_str, _test_dir_str,
                non_code=fr_id in _non_code_frs, cov_threshold=_cov_threshold,
            )
        else:
            _print_fr_scoped_overrides_py(
                project, fr_id, _test_file, _src_dir, _manifest_data,
                non_code_frs=_non_code_frs, cov_threshold=_cov_threshold,
            )

    print("\n" + "─" * 60)
    print("NEXT STEP: Evaluate the dimensions above, then run:")
    fr_flag = f" --fr-id {fr_id}" if fr_id else ""
    print(
        f"  python harness_cli.py finalize-gate --gate {args.gate} "
        f"--phase {args.phase} --project {args.project}{fr_flag}"
    )
    print("─" * 60)

    # Write sentinel so finalize-gate can verify run-gate was actually called.
    # Without this file, finalize-gate will block to prevent fabricated gate scores.
    # v2.13: pass args.phase so the sentinel is scoped to this phase (Bug #121).
    sf = _sentinel_path(Path(project), args.gate, fr_id, phase=args.phase)
    sf.parent.mkdir(parents=True, exist_ok=True)
    sf.write_text(f"{datetime.now(timezone.utc).isoformat()}\n", encoding="utf-8")
    print(f"[SENTINEL] {sf.relative_to(Path(project))} written.")
    return 0

# ---------------------------------------------------------------------------
# run-env-check (project-aware environment readiness evaluation)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Gate 4 prerequisite checks  (A2-A5 schema, B2 score files)
# ---------------------------------------------------------------------------

# Tier 3 dimensions that require Devil's Advocate (A3) and high-score confirmation (A4)
_TIER3_DIMS: frozenset[str] = frozenset({
    "architecture", "readability", "error_handling", "documentation", "performance",
})
# Per-dim score file directory (relative to project root) — legacy fallback only
_SCORES_SUBDIR = Path(".sessi-work") / "round_1" / "scores"


def _find_latest_round_dir(project: Path) -> "tuple[Path, int] | None":
    """Return (scores_dir, round_number) for the highest-numbered round_N with score files.

    Looks for .sessi-work/round_N/scores/*.json directories and returns the one
    with the largest N that actually contains score files.  Falls back to None
    if .sessi-work doesn't exist or no round directories have score files.
    """
    sessi = project / ".sessi-work"
    if not sessi.is_dir():
        return None
    rounds: list[tuple[Path, int]] = []
    for d in sessi.iterdir():
        if d.is_dir() and d.name.startswith("round_"):
            suffix = d.name[len("round_"):]
            if suffix.isdigit():
                rounds.append((d, int(suffix)))
    rounds.sort(key=lambda x: x[1], reverse=True)
    for rd, rn in rounds:
        scores_dir = rd / "scores"
        if list(scores_dir.glob("*.json")):
            return scores_dir, rn
    return None


_DA_EVIDENCE_MIN_CHARS = 120  # minimum length for challenge / response to count as real


def _validate_da_evidence(dim: str, g4: dict) -> "str | None":
    """A3 hardening: verify a Tier 3 dim's Devil's Advocate challenge is artifact-backed.

    A bare `devil_advocate.<dim>: true` is no longer sufficient — the agent must record
    the actual challenge under `devil_advocate_evidence.<dim>` with substantive
    `challenge` and `response` text. Returns a violation message, or None if valid.
    """
    evidence = g4.get("devil_advocate_evidence", {})
    if not isinstance(evidence, dict) or dim not in evidence:
        return (f"'{dim}': devil_advocate.{dim}=true but devil_advocate_evidence.{dim} is missing. "
                f"Record the actual DA challenge (a Claude sub-agent challenger persona's "
                f"critique + the defence) — a bare boolean is not accepted.")
    entry = evidence[dim]
    if not isinstance(entry, dict):
        return f"'{dim}': devil_advocate_evidence.{dim} must be an object with challenge + response."
    for field in ("challenge", "response"):
        val = str(entry.get(field, "")).strip()
        if len(val) < _DA_EVIDENCE_MIN_CHARS:
            return (f"'{dim}': devil_advocate_evidence.{dim}.{field} is too short "
                    f"({len(val)} chars < {_DA_EVIDENCE_MIN_CHARS}) — provide the real "
                    f"{field}, not a placeholder.")
    return None


def _load_gate_result_json(project: Path, gate: int) -> dict:
    """Load gate{gate}_result.json from the standard candidate locations.

    Candidate order (first parseable hit wins): .sessi-work/ (agent-written,
    freshest), .methodology/ (persisted by a previous finalize-gate), project
    root. Returns {} when no candidate exists or parses.
    """
    result_candidates = [
        project / ".sessi-work" / f"gate{gate}_result.json",
        project / ".methodology" / f"gate{gate}_result.json",
        project / f"gate{gate}_result.json",
    ]
    for candidate in result_candidates:
        if candidate.exists():
            try:
                return json.loads(candidate.read_text(encoding="utf-8"))
            except Exception as _e:
                print(f"[Gate {gate}] ⚠ Could not parse {candidate}: {_e} — skipping extended checks",
                      file=sys.stderr)
    return {}


def _collect_da_waivers(project: Path, gate: int, gres: "dict | None" = None) -> "tuple[bool, set[str]]":
    """Collect artifact-backed DA score-threshold waivers from gate{gate}_result.json.

    A waiver lets a CRG-ONLY dim (e.g. architecture) pass below threshold when
    the Devil's Advocate challenge concluded the design is intentional
    (Orchestrator/hub-and-spoke, small-package Leiden fragmentation). Valid at
    Gate 3 and Gate 4 — finalize_gate's threshold zeroing is gate-agnostic.

    Requires devil_advocate.<dim>=true, da_waiver.<dim>=true, AND real
    devil_advocate_evidence.<dim> (challenge + response). Returns
    (blocked, da_waivers): blocked=True only when a requested waiver's DA
    evidence is missing/insufficient — a requested-but-unbacked waiver must
    fail loudly (fabrication guard), not be silently dropped.

    Note: the .methodology/ candidate can carry a waiver persisted from a
    previous finalize-gate run (parity with the long-standing Gate 4
    behavior); .sessi-work/ is checked first so a fresh agent-written file
    always wins.
    """
    blocked = False
    da_waivers: set[str] = set()
    g = _load_gate_result_json(project, gate) if gres is None else gres
    if not g:
        return blocked, da_waivers
    devil_advocate: dict = g.get("devil_advocate", {})
    _da_waiver_raw: dict = g.get("da_waiver", {})
    for _dim, _waived in _da_waiver_raw.items():
        if not (_waived and devil_advocate.get(_dim, False)):
            continue
        _w_problem = _validate_da_evidence(_dim, g)
        if _w_problem:
            print(
                f"\n[BLOCKED] Gate {gate} (A3): da_waiver for '{_dim}' requires DA evidence — {_w_problem}",
                file=sys.stderr,
            )
            blocked = True
            continue
        # Only apply the waiver when the dimension is actually below threshold.
        # If tool_score >= threshold the dimension already passes; accepting
        # the waiver would still set da_waiver_needs_human_review = True in
        # quality_manifest.json, which is a false-positive review flag.
        _bd = g.get("breakdown", {}).get(_dim, {})
        _tool_score = float(_bd.get("tool_score", 0.0))
        _threshold = float(_bd.get("threshold", float("inf")))
        if _tool_score >= _threshold:
            print(
                f"[Gate {gate}] A3: da_waiver for '{_dim}' skipped — "
                f"tool_score={_tool_score:.1f} ≥ threshold={_threshold:.1f} "
                "(waiver not needed; dimension already passes).",
                file=sys.stderr,
            )
            continue
        da_waivers.add(_dim)
        print(
            f"[Gate {gate}] A3: DA waiver active for '{_dim}' "
            "(score threshold bypassed — artifact-backed DA challenge confirmed intentional design).",
            file=sys.stderr,
        )
    return blocked, da_waivers


def _check_gate4_prerequisites(project: Path) -> "tuple[bool, set[str]]":
    """
    Run all Gate 4 blocking prerequisites before calling bridge.finalize_gate.

    Returns (blocked, da_waivers):
        blocked    — True if any prerequisite fails
        da_waivers — set of dimension names whose score threshold is waived via DA challenge

    Checks:
        A3 — devil_advocate: each marked-done Tier 3 dim (and every da_waiver) must carry a
             real `devil_advocate_evidence` artifact (challenge + response, not a bare boolean)
        B2 — per-dim score files exist in latest round_N/scores/ and have correct round field

    Non-blocking advisory: A5 issue_registry_path (contents are agent-written).
    Removed: A2 model_used (constant "claude" after MCP backends dropped),
    A4 high_score_confirmations (self-attested boolean ceremony).
    """
    blocked = False
    da_waivers: set[str] = set()

    # ── Load gate4_result.json for A2/A3/A4/A5 ───────────────────────
    g4 = _load_gate_result_json(project, 4)

    if g4:
        # (A2 model_used removed — after the MCP backends were dropped every dim
        # is evaluated by the Claude sub-agent, so the field was a constant "claude"
        # with zero verification value.)

        # ── A3: Devil's Advocate for Tier 3 dims ─────────────────────
        devil_advocate: dict = g4.get("devil_advocate", {})
        if not devil_advocate:
            print(
                "\n[BLOCKED] Gate 4 (A3): 'devil_advocate' field missing from gate4_result.json.\n"
                "  For each Tier 3 dimension, add devil_advocate: {dim: true/false}.\n"
                f"  Required dims: {sorted(_TIER3_DIMS)}",
                file=sys.stderr,
            )
            blocked = True
        else:
            not_done = [d for d in _TIER3_DIMS if not devil_advocate.get(d, False)]
            if not_done:
                print(
                    "\n[BLOCKED] Gate 4 (A3): Devil's Advocate challenge not completed for:\n"
                    + "\n".join(f"  - {d}" for d in sorted(not_done)) + "\n"
                    "  For each Tier 3 dim, dispatch a Claude sub-agent with a challenger persona\n"
                    "  to critique the evaluation, then set devil_advocate.<dim> = true AND record\n"
                    "  the challenge under devil_advocate_evidence.<dim> in gate4_result.json.",
                    file=sys.stderr,
                )
                blocked = True
            else:
                # A3 hardening: each marked-done Tier 3 dim must be artifact-backed.
                _da_problems = [
                    msg for d in sorted(_TIER3_DIMS)
                    if devil_advocate.get(d, False) and (msg := _validate_da_evidence(d, g4))
                ]
                if _da_problems:
                    print(
                        "\n[BLOCKED] Gate 4 (A3): Devil's Advocate evidence missing or insufficient:\n"
                        + "\n".join(f"  - {m}" for m in _da_problems),
                        file=sys.stderr,
                    )
                    blocked = True
                else:
                    # DA challenge complete + artifact-backed — collect score-threshold
                    # waivers (shared with the Gate 3 path; see _collect_da_waivers).
                    _w_blocked, da_waivers = _collect_da_waivers(project, 4, gres=g4)
                    blocked = blocked or _w_blocked

        # ── A5: Issue Registry (advisory only — no longer blocks) ─────
        # The registry contents are agent-written; "exists + non-empty" never
        # verified anything an agent couldn't trivially satisfy. Downgraded to a
        # non-blocking advisory.
        issue_registry_path_str: str = g4.get("issue_registry_path", "")
        if not issue_registry_path_str:
            print("[Gate 4] (A5, advisory): 'issue_registry_path' not set in gate4_result.json.",
                  file=sys.stderr)
        else:
            issue_registry: Optional[Path] = (project / issue_registry_path_str) if not Path(issue_registry_path_str).is_absolute() else Path(issue_registry_path_str)
            # Containment check: agent-controlled path must resolve inside the
            # project root. Blocking traversal (`../../etc/passwd`) probes here
            # even though the registry contents are only advisory.
            from harness.harness_bridge import path_escapes_root
            try:
                if issue_registry and path_escapes_root(issue_registry, project):
                    print(
                        f"[Gate 4] (A5, advisory): issue_registry_path escapes project root "
                        f"({issue_registry}); refusing to read.",
                        file=sys.stderr,
                    )
                    issue_registry = None
            except (OSError, RuntimeError):
                issue_registry = None
            if issue_registry is not None and not issue_registry.exists():
                print(f"[Gate 4] (A5, advisory): issue registry not found: {issue_registry}",
                      file=sys.stderr)
            elif issue_registry is not None:
                try:
                    registry_data = json.loads(issue_registry.read_text(encoding="utf-8"))
                    if not registry_data:
                        print(f"[Gate 4] (A5, advisory): issue registry is empty: {issue_registry}",
                              file=sys.stderr)
                except json.JSONDecodeError:
                    print(f"[Gate 4] (A5, advisory): issue registry is not valid JSON: {issue_registry}",
                          file=sys.stderr)

    # ── B2: Per-dim score files (latest round, stale-round detection) ────
    _b2_latest = _find_latest_round_dir(project)
    _b2_round: int | None = None
    if _b2_latest is None:
        # Fallback to hardcoded round_1 path for backward compat
        scores_dir = project / _SCORES_SUBDIR
    else:
        scores_dir, _b2_round = _b2_latest

    if not scores_dir.is_dir():
        print(
            f"\n[BLOCKED] Gate 4 (B2): Per-dimension score directory not found.\n"
            f"  Expected: {scores_dir}\n"
            "  Write individual <dim>.json files for each evaluated dimension.",
            file=sys.stderr,
        )
        blocked = True
    else:
        score_files = list(scores_dir.glob("*.json"))
        if not score_files:
            print(
                f"\n[BLOCKED] Gate 4 (B2): No per-dimension score files found in {scores_dir}.\n"
                "  Write <dim>.json (e.g. architecture.json, linting.json) for each evaluated dimension.",
                file=sys.stderr,
            )
            blocked = True
        else:
            # Stale-round detection: each score file's "round" field must match the directory number.
            if _b2_round is not None:
                stale_files = []
                for sf in score_files:
                    try:
                        _sf_data = json.loads(sf.read_text(encoding="utf-8"))
                        _sf_round = _sf_data.get("round")
                        # Only flag if "round" is explicitly set to a different value.
                        # Missing "round" is caught by score.py R1 (required field) — not stale.
                        if _sf_round is not None and _sf_round != _b2_round:
                            stale_files.append(
                                f"{sf.name} (round={_sf_round!r}, expected {_b2_round})"
                            )
                    except Exception:
                        pass  # unparseable files are caught by score.py R1 later
                if stale_files:
                    print(
                        f"\n[BLOCKED] Gate 4 (B2): Stale score files detected in {scores_dir}:\n"
                        + "\n".join(f"  - {s}" for s in stale_files) + "\n"
                        "  Score files were copied from an earlier round without re-evaluation.\n"
                        "  Re-run the SSI evaluation for each stale dimension.",
                        file=sys.stderr,
                    )
                    blocked = True
                else:
                    print(
                        f"[Gate 4] B2: {len(score_files)} per-dim score file(s) found "
                        f"(round={_b2_round}) ✅",
                        file=sys.stderr,
                    )
            else:
                print(f"[Gate 4] B2: {len(score_files)} per-dim score file(s) found ✅", file=sys.stderr)

    # ── B3: CRG recon output existence ────────────────────────────────
    # If the gate config declares crg.reconnaissance: true, the CRG bridge
    # must have been executed before finalize-gate is called.  The canonical
    # evidence is .sessi-work/crg_reconnaissance.json (written by the CRG
    # reconnaissance protocol).
    # A missing or empty file means CRG was never run — architecture-tier
    # scores derived from CRG data are therefore groundless.
    from core.harness_config import is_dim_disabled
    if is_dim_disabled("architecture", str(project)):
        print("[Gate 4] B3: CRG recon check skipped (crg_architecture disabled)", file=sys.stderr)
    else:
        try:
            import yaml as _yaml
            import glob as _b3glob
            _crg_cfg_files = sorted(_b3glob.glob(
                str(project / "harness" / "gate_configs" / "gate4_*.yaml")
            ))
            _crg_recon_required = False
            for _crg_cfg_path in _crg_cfg_files:
                try:
                    _crg_cfg = _yaml.safe_load(Path(_crg_cfg_path).read_text(encoding="utf-8"))
                    if (_crg_cfg or {}).get("crg", {}).get("reconnaissance"):
                        _crg_recon_required = True
                        break
                except Exception as _b3_cfg_exc:
                    print(f"[Gate 4] B3: skipping {_crg_cfg_path} (parse error: {_b3_cfg_exc})",
                          file=sys.stderr)
            if _crg_recon_required:
                recon_file = project / ".sessi-work" / "crg_reconnaissance.json"
                recon_exists = recon_file.is_file() and recon_file.stat().st_size > 0
                if not recon_exists:
                    print(
                        "\n[BLOCKED] Gate 4 (B3): CRG reconnaissance output not found.\n"
                        f"  Expected: {recon_file} (non-empty)\n"
                        "  Gate 4 config declares crg.reconnaissance: true — the CRG bridge\n"
                        "  must be executed before finalize-gate to provide architecture-tier\n"
                        "  evaluation context.\n"
                        "  Run the CRG reconnaissance protocol, then re-run:\n"
                        "    python harness_cli.py finalize-gate --gate 4 --phase 6 --project .",
                        file=sys.stderr,
                    )
                    blocked = True
                else:
                    print(
                        f"[Gate 4] B3: CRG recon output found "
                        f"({recon_file.name}, {recon_file.stat().st_size} bytes) ✅",
                        file=sys.stderr,
                    )
        except Exception as _b3exc:
            print(f"[Gate 4] B3: CRG recon check error ({_b3exc}) — skipping", file=sys.stderr)

    return blocked, da_waivers

# ---------------------------------------------------------------------------
# finalize-gate  (Phase 2 of two-phase evaluation)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# _cmd_finalize_gate_impl section helpers
# Return an int exit code when the gate should be blocked, None to continue.
# ---------------------------------------------------------------------------

def _finalize_gate_preflight(args: argparse.Namespace, project_path: Path) -> "int | None":
    """S0: tool availability + commit interval + sentinel check."""
    project = str(project_path)
    fr_id = getattr(args, "fr_id", None) or None

    # S0a: Tool availability
    _tools_ok, _missing_tools = tool_checks.verify_gate_tools(args.gate, project)
    if not _tools_ok:
        print(
            f"\n[BLOCKED] Required tools not installed for Gate {args.gate}:\n"
            + "".join(f"  ✗ {m}\n" for m in _missing_tools)
            + "\n  Install the missing tools and re-run finalize-gate.\n"
            "  Tool scores must come from actual tool execution, not estimation."
        )
        return 8

    # S0b: Commit interval enforcement (P1 — prevent batch fabrication).
    # Per-FR isolation: pass fr_id so distinct FRs finalizing in the same
    # 2s window are not falsely flagged as batch fabrication.
    _interval_ok, _interval_msg = gate1_evidence.check_commit_intervals(
        project, args.phase, args.gate, fr_id
    )
    if not _interval_ok:
        print(f"\n[BLOCKED] Commit interval violation: {_interval_msg}")
        print("  Re-run per-FR evaluations with genuine evidence and natural spacing.")
        return 1

    # Sentinel: run-gate must have been called before finalize-gate
    # v2.13: pass args.phase so the path matches what run-gate wrote
    # in the same phase (Bug #121 — no cross-phase sentinel reuse).
    sf = _sentinel_path(project_path, args.gate, fr_id, phase=args.phase)
    if not sf.exists():
        print(
            f"\n[BLOCKED] run-gate --gate {args.gate} --phase {args.phase}"
            + (f" --fr-id {fr_id}" if fr_id else "")
            + f" --project {args.project}"
            f"\n  must be called before finalize-gate."
            f"\n  Missing sentinel: {sf.relative_to(project_path)}"
            f"\n  Writing gate{{N}}_result.json directly without run-gate is not permitted."
        )
        return 1

    return None


def _finalize_gate_fr_checks(args: argparse.Namespace, project_path: Path) -> "int | None":
    """I-2/I-3/I-4: Gate 1 per-FR checks (test file existence, RED ordering, spec coverage)."""
    fr_id = getattr(args, "fr_id", None) or None
    _active_tests = ProjectLayout(project_path).active_test_dir

    # I-2: FR test file existence
    if args.gate == 1 and fr_id and _active_tests.is_dir():
        _fr_ok, _fr_msg = _check_fr_test_file_exists(project_path, fr_id)
        if not _fr_ok:
            print(_fr_msg)
            return 8

    # I-3: RED phase ordering
    if args.gate == 1 and fr_id and _active_tests.is_dir():
        _red_ok, _red_msg = _check_red_phase_ordering(project_path, fr_id)
        if not _red_ok:
            print(_red_msg)
            return 1

    # I-4: Spec Coverage (Gate 1, threshold 40%)
    if args.gate == 1 and fr_id and (ProjectLayout(project_path).test_spec_path).exists():
        _sc1_code, _sc1_pct = _run_spec_coverage_check(
            project_path, 40.0, fr_id=fr_id, verbose=True
        )
        if _sc1_code != 0:
            print(f"\n[BLOCKED] Gate 1 spec-coverage [{fr_id}] {_sc1_pct:.1f}% < 40% threshold")
            return 1

    return None


def _finalize_gate_cross_checks(args: argparse.Namespace, project_path: Path) -> "int | None":
    """I-5/I-6: Gates 2-4 D4 spec-coverage + PR 4 trace dimension.

    NOTE: HR-10/HR-01 A/B audit removed — see comment in _cmd_finalize_gate_impl.
    """
    # I-5: D4 Spec Coverage (Gates 2-4, unified v2.6)
    # Thresholds: Gate2=60%, Gate3=80%, Gate4=90%.
    if args.gate >= 2 and (ProjectLayout(project_path).test_spec_path).exists():
        # F-2.4 fix: source the threshold from the canonical constant
        # in `spec_tracking_checker` to prevent silent divergence if
        # either side is updated independently.
        from core.quality_gate.spec_tracking_checker import SPEC_COV_THRESHOLDS
        _sc_threshold = SPEC_COV_THRESHOLDS.get(args.gate, 60.0)
        _sc_code, _sc_pct = _run_spec_coverage_check(
            project_path, _sc_threshold, verbose=True
        )
        if _sc_code != 0:
            print(f"\n[BLOCKED] Gate {args.gate} spec-coverage {_sc_pct:.1f}% < {_sc_threshold}%")
            return 1

    # ── I-6: PR 4 closed-loop trace dimension (Gates 2-4) ───────────
    # Fuses 4a (FR→code→test, 100% over IN_PROGRESS+VERIFIED FRs) with
    # 4b (TEST_SPEC→test, gate-specific threshold). Merged = min(4a, 4b).
    # Skipped if no SAD.md and no [FR-XX] annotations (project not at P3+).
    # The framework-computed score is patched into gate{N}_result.json
    # breakdown so it flows through bridge.finalize_gate (same pattern as
    # _crg_overrides_applied for the architecture dimension).
    if args.gate >= 2:
        try:
            from core.quality_gate.spec_tracking_checker import (
                compute_trace_dimension,
            )
            _trace = compute_trace_dimension(project_path, args.gate)
            if _trace.get("error"):
                print(f"\n[WARN] trace dimension error: {_trace['error']}",
                      file=sys.stderr)
            _t_4a = _trace["4a_fr_to_test_pct"]
            _t_4b = _trace["4b_test_spec_pct"]
            _t_4c = _trace.get("4c_nfr_to_test_pct", 100.0)
            _t_merged = _trace["merged_pct"]
            _t_passed = _trace["passed"]
            print(
                f"\n[trace] Gate {args.gate} | "
                f"4a (FR→code→test): {_t_4a:.1f}% ≥ {_trace['threshold_4a']}%  "
                f"4b (TEST_SPEC→test): {_t_4b:.1f}% ≥ {_trace['threshold_4b']:.1f}%  "
                f"4c (NFR→test): {_t_4c:.1f}%  "
                f"merged: {_t_merged:.1f}%  "
                f"{'PASS' if _t_passed else 'FAIL'}"
            )
            if _trace["active_uncoded"]:
                print(f"  active FRs without code: {_trace['active_uncoded']}")
            if _trace["active_untested"]:
                print(f"  active FRs without test: {_trace['active_untested']}")
            if _trace.get("nfr_untested"):
                print(f"  NFRs without test coverage: {_trace['nfr_untested']}")
            # ── Patch trace score into gate{N}_result.json breakdown ─────
            # Same pattern as the architecture CRG override in
            # harness_bridge.finalize_gate (line ~1418): the framework
            # overrides the agent's score for trace because the agent has
            # no tool to compute it.
            _gp = project_path / ".sessi-work" / f"gate{args.gate}_result.json"
            if _gp.exists():
                try:
                    _gr = json.loads(_gp.read_text(encoding="utf-8"))
                    _gr.setdefault("breakdown", {}).setdefault(
                        "traceability", {}
                    )["score"] = _t_merged
                    _gr["breakdown"]["traceability"]["tool_evidence"] = (
                        f"framework: compute_trace_dimension(gate={args.gate}) → "
                        f"4a={_t_4a:.1f}% 4b={_t_4b:.1f}% 4c={_t_4c:.1f}% merged={_t_merged:.1f}%"
                    )
                    _gr["breakdown"]["traceability"]["threshold"] = float(
                        _trace["threshold_4a"]
                    )
                    _gr["breakdown"]["traceability"]["framework_override"] = True
                    _gp.write_text(
                        json.dumps(_gr, indent=2, ensure_ascii=False),
                        encoding="utf-8",
                    )
                except (OSError, json.JSONDecodeError) as _gp_err:
                    print(f"[WARN] could not patch trace score into result: {_gp_err}",
                          file=sys.stderr)
            if not _t_passed:
                print(
                    f"\n[BLOCKED] Gate {args.gate} trace dimension "
                    f"merged {_t_merged:.1f}% < threshold "
                    f"(4a={_trace['threshold_4a']}%, "
                    f"4b={_trace['threshold_4b']:.1f}%)"
                )
                return 1
        except Exception as e:
            # Framework-side error: fail-closed at G2+ (don't silently pass)
            print(f"\n[BLOCKED] compute_trace_dimension raised: {e}",
                  file=sys.stderr)
            return 1

    return None  # all cross-checks passed


def _mark_gate_commit_failed(project_path: Path, gate: int, fr_id: str | None) -> None:
    """Roll back gate_results.quality_complete after a failed git commit.

    finalize-gate optimistically patches quality_manifest.json's gate_results
    BEFORE attempting the git commit. Every phase workflow (phase3..8-*.js)
    treats quality_complete==True as the SOLE authority that a gate passed —
    it never inspects this CLI's exit code. If `git commit` is rejected
    (e.g. prepare-commit-msg hook stale trace attestation) after that
    optimistic write, the on-disk manifest still reads True even though
    nothing landed in git. Flip it back so quality_complete==True always
    implies "durably committed".
    """
    _mfst = project_path / ".methodology" / "quality_manifest.json"
    if not _mfst.exists():
        return
    try:
        _mfst_json = json.loads(_mfst.read_text(encoding="utf-8"))
        _gr = _mfst_json.get("gate_results", {}) or {}
        if gate == 1:
            _actual_fr = fr_id or "unknown"
            _entry = (_gr.get("gate1") or {}).get(_actual_fr)
        else:
            _entry = _gr.get(f"gate{gate}")
        if isinstance(_entry, dict):
            _entry["quality_complete"] = False
            _entry["commit_landed"] = False
            atomic_write_json(_mfst, _mfst_json)
            print(f"  [WARN] git commit did not land — rolled back quality_complete "
                  f"to False for gate{gate}" + (f"/{fr_id}" if fr_id else ""))
    except (OSError, json.JSONDecodeError) as _mf_err:
        print(f"  [WARN] Could not roll back quality_manifest.json after commit failure: {_mf_err}")


def _cmd_finalize_gate_impl(args: argparse.Namespace) -> int:
    """
    Phase 2: read gate{N}_result.json, check thresholds, update manifest, git.

    Called after Claude has completed inline evaluation and written the result file.
    Delegates preflight/fr/cross-checks to section helpers; handles bridge + post-flight.

    NOTE: HR-10/HR-01 A/B audit (sessions_spawn.log entry-count + distinct-session
    enforcement) was REMOVED. The log is a plain agent-writable file; proof of an
    independent Agent B review cannot be derived from it. P1/P2 quality is enforced
    by the Agent B deliverable review itself; P3+ by tool-scored gates and S4.
    AgentSpawner still records dispatches to sessions_spawn.log as a non-blocking debug trail.
    """
    from harness.harness_bridge import HarnessBridge, GateBlockedError

    project_path = Path(args.project).resolve()
    project = str(project_path)
    bridge = HarnessBridge()
    fr_id = getattr(args, "fr_id", None) or None

    print(f"\n{'='*60}\nfinalize-gate: Gate {args.gate} | Phase {args.phase}\n{'='*60}")

    if (code := _finalize_gate_preflight(args, project_path)) is not None:
        return code
    if (code := _finalize_gate_fr_checks(args, project_path)) is not None:
        return code
    if (code := _finalize_gate_cross_checks(args, project_path)) is not None:
        return code

    # ── Gate 4 extra enforcement (A1/A2/A3/A4/A5/B2) ─────────────────
    _da_waivers: set[str] = set()
    if args.gate == 4:
        _gate4_block, _da_waivers = _check_gate4_prerequisites(Path(project))
        if _gate4_block:
            return 5
    elif args.gate == 3:
        # Gate 3 honors the same artifact-backed DA waivers (from
        # gate3_result.json) — waiver collection only, none of the Gate 4
        # A3-completeness/A5/B2/B3 prerequisites apply at this gate.
        _g3_block, _da_waivers = _collect_da_waivers(Path(project), 3)
        if _g3_block:
            return 5

    # Rebuild context (loads config; skips CRG recon second time since recon file already exists)
    ctx = bridge.prepare_gate(
        gate_num=args.gate,
        project_root=project,
        phase=args.phase,
        fr_id=fr_id,
    )

    try:
        result = bridge.finalize_gate(ctx, da_waivers=_da_waivers)
        # Surface score/quality_complete to OTEL span wrapper via args (Namespace allows
        # dynamic attributes; wrapper reads _span_score/_span_quality_complete after _impl).
        args._span_score = result.score  # type: ignore[attr-defined]
        args._span_quality_complete = result.quality_complete  # type: ignore[attr-defined]
        print(f"\nGATE {args.gate} PASSED")
        print(f"  score           : {result.score:.1f}")
        print(f"  quality_complete: {result.quality_complete}")
        print(f"  open_critical   : {result.open_critical}")
        print(f"  open_high       : {result.open_high}")

        # Write finalize sentinel — advance-phase checks this to prove finalize-gate
        # was actually called (not bypassed by fabricating quality_manifest.json).
        # v2.13: pass args.phase so the path matches run-gate's per-phase path
        # (Bug #121).
        _fsf = _finalize_sentinel_path(project_path, args.gate, fr_id, phase=args.phase)
        _fsf.parent.mkdir(parents=True, exist_ok=True)
        _fsf.write_text(f"{datetime.now(timezone.utc).isoformat()}\n", encoding="utf-8")

        # ── Persist gate result to .methodology/ (phase-persistent evidence) ──
        # gate{N}_result.json is written by the agent to .sessi-work/, which is
        # (a) gitignored and (b) wiped by advance-phase's rmtree. The PhaseAuditor
        # C10 check needs gate4_result.json as Gate 4 PASS evidence in CI. Copy the
        # just-finalized result to .methodology/ where it is committable and survives
        # the phase-transition cleanup.
        for _gp_src in (
            project_path / ".sessi-work" / f"gate{args.gate}_result.json",
            project_path / f"gate{args.gate}_result.json",
        ):
            if _gp_src.exists():
                _gp_dst = project_path / ".methodology" / f"gate{args.gate}_result.json"
                try:
                    _gp_dst.parent.mkdir(parents=True, exist_ok=True)
                    # Patch composite_score with the harness-computed weighted value.
                    # The agent writes its own self-assessed score to gate{N}_result.json;
                    # the harness recomputes it from breakdown weights.  Without this
                    # patch the persisted file would still carry the agent's raw score.
                    try:
                        _gp_json = json.loads(_gp_src.read_text(encoding="utf-8"))
                        _gp_json["composite_score"] = round(result.score, 4)
                        # P6-BUG-13: also patch harness-computed fields so that
                        # PhaseAuditor C10 and advance-phase can read gate PASS
                        # status from the committable .methodology/ copy without
                        # requiring a manual post-finalize patch.
                        _gp_json["quality_complete"] = result.quality_complete
                        _gp_json["verdict"] = "PASS" if result.quality_complete else "FAIL"
                        _gp_json["passed"] = result.quality_complete
                        _gp_dst.write_text(
                            json.dumps(_gp_json, indent=2, ensure_ascii=False),
                            encoding="utf-8",
                        )
                    except json.JSONDecodeError:
                        # Malformed source — fall back to verbatim copy
                        _gp_dst.write_text(_gp_src.read_text(encoding="utf-8"), encoding="utf-8")
                    print(f"  persisted       : {_gp_dst.relative_to(project_path)} (committable)")
                except OSError as _gp_err:
                    print(f"  [WARN] Could not persist gate result to .methodology/: {_gp_err}")
                break

        # ── Bug #118: keep .methodology/quality_manifest.json gate_results in sync ──
        # Without this, the next phase's entry_gate sees gate_results.gate{N}=null
        # and blocks the advance. Pre-fix required a manual edit; auto-patch the
        # gate that just finalized.
        _mfst = project_path / ".methodology" / "quality_manifest.json"
        if _mfst.exists():
            try:
                _mfst_json = json.loads(_mfst.read_text(encoding="utf-8"))
                _mfst_gr = _mfst_json.setdefault("gate_results", {})
                _gr_key = f"gate{args.gate}"
                if args.gate == 1 and fr_id:
                    # Gate 1: per-FR dict under gate1.{fr_id}
                    _g1 = _mfst_gr.setdefault("gate1", {})
                    if not isinstance(_g1, dict):
                        _g1 = {}
                        _mfst_gr["gate1"] = _g1
                    _prev = _g1.get(fr_id) or {}
                    _g1[fr_id] = {
                        "score": round(result.score, 2),
                        "quality_complete": result.quality_complete,
                        "rounds_used": (int(_prev.get("rounds_used", 0)) if isinstance(_prev, dict) else 0) + 1,
                        "open_critical": result.open_critical,
                        "open_high": result.open_high,
                    }
                else:
                    # Gate 2+: composite block at gate_results.gate{N}
                    _prev = _mfst_gr.get(_gr_key) or {}
                    if not isinstance(_prev, dict):
                        _prev = {}
                    _mfst_gr[_gr_key] = {
                        **_prev,
                        "score": round(result.score, 2),
                        "quality_complete": result.quality_complete,
                        "rounds_used": (int(_prev.get("rounds_used", 0)) if isinstance(_prev, dict) else 0) + 1,
                        "open_critical": result.open_critical,
                        "open_high": result.open_high,
                        "phase": args.phase,
                        "gate": args.gate,
                        "fr_scope": fr_id or "all",
                        "overall_score": round(result.score, 2),
                    }
                atomic_write_json(_mfst, _mfst_json)
                print(f"  manifest        : quality_manifest.json {_gr_key} patched "
                      f"(score={round(result.score, 2)}, qc={result.quality_complete})")
            except (OSError, json.JSONDecodeError) as _mf_err:
                print(f"  [WARN] Could not patch quality_manifest.json gate_results: {_mf_err}")

        # ── Structural post-flight for phase-exit gates (gate ≥ 2) ──────────
        # Checks ASPICE artifact cross-references and drift against artifacts
        # finalize-gate called directly also needs these blocking checks so the
        # FSM cannot advance past a gate with structural violations.
        # NOTE: _update_state_checkpoint intentionally placed AFTER this block —
        # if postflight fails we return early without marking the gate as passed.
        if args.gate >= 2:
            print(f"\n[POST-FLIGHT] Structural checks (Gate {args.gate})...")
            try:
                from core.phase_hooks import PhaseHooks
                _ph = PhaseHooks(project, phase=args.phase, enable_kill_switch=False)
                _art = _ph.postflight_artifact_links()
                _drft = _ph.postflight_drift_check()
                _pf_ok = _art.get("passed", True) and _drft.get("passed", True)
                if not _pf_ok:
                    # Structural postflight (artifact links / drift) detects substantive
                    # gaps — a broken phase artifact chain or spec↔code drift over the
                    # threshold. These need real work, not an auto-fix: the auto_fix
                    # strategies only emit stubs/comments that never clear these checks
                    # (verified end-to-end), so block honestly.
                    print(f"\n[BLOCKED] Post-flight structural check failed after Gate {args.gate}.")
                    print("  Fix the issues listed above, then re-run:")
                    print(f"  python harness_cli.py finalize-gate --gate {args.gate} "
                          f"--phase {args.phase} --project {project}")
                    return 5
                print("[POST-FLIGHT] Structural checks PASS")
            except ImportError:
                print("[WARN] PhaseHooks unavailable — postflight structural checks skipped")
            except Exception as _pf_exc:
                # Blocking only for Gate 4 (final gate); earlier gates warn only.
                if args.gate >= 4:
                    print(f"[BLOCKED] Post-flight error: {_pf_exc}")
                    return 5
                print(f"[WARN] Post-flight hooks error (non-blocking): {_pf_exc}")

        # ── Advisory: rounds_used=0 suggests A/B evaluation was skipped ──
        _rounds = getattr(result, "rounds_used", None)
        if _rounds is None:
            _rounds = 0
        if _rounds == 0 and args.gate == 1:
            print(
                f"  [WARN] rounds_used=0 for {fr_id or 'this gate'}: "
                "Gate 1 with zero review rounds suggests A/B evaluation was skipped. "
                "Ensure Agent A and Agent B both ran."
            )

        # ── D2: Score uniformity CRITICAL check ──────────────────────────
        # stddev=0 (all scores identical) is impossible under genuine
        # per-FR evaluation — it means scores were batch-copied.
        # This is a harder block than the existing advisory check in
        # harness_bridge.py (which only LOGs low variance).
        #
        # Saturation exemption: when ALL dimension scores are at (or near)
        # the ceiling (mean ≥ 99.5), stddev == 0 is a legitimate outcome.
        # Example: a 25-line minimal module where ruff, mypy, and pytest-cov
        # all genuinely score 100.  Blocking this case is a false positive.
        # The suspicious pattern is mid-range uniformity (e.g. all 78.5),
        # not ceiling uniformity.
        # Exclude not-yet-applicable dims (score=None — e.g. CRG architecture
        # override or a benchmark-less perf dim at Gate 2+) before the variance
        # math.  Mirrors harness_bridge None handling (skip None dims); without
        # it, statistics.pstdev/sum raise TypeError on None, crashing
        # finalize-gate AFTER the manifest patch — a split-write that leaves
        # gate_results recorded but the gate un-finalized.
        _d_scores = [d.score for d in result.dimensions if d.score is not None]
        if len(_d_scores) >= 3:
            import statistics as _stats
            _d_stdev = _stats.pstdev(_d_scores)
            _d_mean = sum(_d_scores) / len(_d_scores)
            _saturated = _d_mean >= 99.5  # all tools at maximum — not suspicious
            if _d_stdev == 0.0 and not _saturated:
                print(
                    f"\n[BLOCKED] CRITICAL: All {len(_d_scores)} dimension scores "
                    f"are identical ({_d_scores[0]:.1f}).\n"
                    f"  Genuine per-dimension evaluation produces natural variance.\n"
                    f"  Re-run run-gate with actual tool execution per dimension."
                )
                return 1
            # Advisory: low-but-nonzero variance (skip when saturated)
            if _d_stdev < 0.5 and not _saturated:
                print(
                    f"  [WARN] Per-dimension scores cluster tightly "
                    f"(stddev={_d_stdev:.3f}) — verify evidence trail."
                )

        # ── D2: Gate repeat detection ─────────────────────────────────────
        # Check if this gate+FR has been finalized before (within this phase).
        # Repeated identical finalizations suggest batch-rerun without fixes.
        _dup_flag = project_path / ".sessi-work" / "sentinels" / f"finalized_{args.gate}_{(fr_id or 'phase').replace('-','').lower()}.flag"
        if _dup_flag.exists():
            print(
                f"\n[WARN] Gate {args.gate} was previously finalized for this phase/FR.\n"
                f"  Re-running without changes wastes CI resources.\n"
                f"  If this is intentional (e.g., after fixing issues), ignore this warning."
            )
        _dup_flag.parent.mkdir(parents=True, exist_ok=True)
        _dup_flag.write_text(f"{datetime.now(timezone.utc).isoformat()}\n", encoding="utf-8")

        # ── S1: Phase Truth for last gate of phase ────────────────────────
        # Ensures PhaseTruthVerifier runs even when finalize-gate is called
        _last_gate = _PHASE_EXIT_GATES.get(args.phase)
        if _last_gate is not None and args.gate == _last_gate and args.phase >= 3:
            print(f"\n[PHASE-TRUTH] Phase {args.phase} final gate — running HR-11 check...")
            try:
                from core.quality_gate.phase_truth_verifier import PhaseTruthVerifier
                verifier = PhaseTruthVerifier(project, args.phase)
                truth_result = verifier.verify()
                if not truth_result["passed"]:
                    print(
                        f"\n[BLOCKED] Phase {args.phase} truth = "
                        f"{truth_result['total_score']:.0f}% < 90% (HR-11)"
                    )
                    print("  Fix gaps then re-run finalize-gate.")
                    return 11
                print(f"  [HR-11] Phase Truth = {truth_result['total_score']:.0f}% ≥ 90% ✓")
            except ImportError:
                print("  [BLOCKED] PhaseTruthVerifier unavailable — cannot verify Phase Truth")
                return 11
            except Exception as _pte:
                print(f"  [WARN] Phase Truth check error: {_pte}")

        _update_state_checkpoint(
            Path(args.project).resolve(), args.gate, fr_id,
            gate_score=result.score, phase=args.phase,
        )
        claude_md.update_claude_md(Path(args.project).resolve())  # gate pass → refresh CLAUDE.md

        # P1: Record successful finalization timestamp HERE (after all checks pass),
        # not inside check_commit_intervals.  Failed attempts must not leave a trace
        # so that retries don't accumulate phantom entries.
        gate1_evidence.record_gate_timestamp(Path(args.project).resolve(), args.phase, args.gate, fr_id)

        # ── Auto-generate machine STAGE_PASS.md ──────────────────────
        _generate_stage_pass(project_path, args.gate, args.phase)

        # ── Auto-generate quality deliverables for Gate 4 ─────────────
        if args.gate == 4:
            # Bug fix P6-2026-07-07: cwd-relative `from scripts.X` failed
            # whenever finalize-gate was run from the project root (scripts/
            # lives under the harness submodule, not the consumer project).
            # Each generator is loaded by absolute file path so the call works
            # regardless of cwd / PYTHONPATH.
            #
            # A1-2026-07-07: helper hoisted to module-scope `load_harness_script`
            # (see top of file) so `_run_phase_auditor` and `cmd_audit_phase`
            # share the same code path; this inline definition is removed.
            try:
                _qreport_mod = load_harness_script("generate_quality_report.py")
                _qreport_mod.generate_quality_report(str(Path(args.project).resolve()))
            except Exception as _qre:
                print(f"  [WARN] QUALITY_REPORT.md generation skipped: {_qre}")

            try:
                _rnotes_mod = load_harness_script("generate_release_notes.py")
                _rnotes_mod.generate_release_notes(str(Path(args.project).resolve()))
            except Exception as _rne:
                print(f"  [WARN] RELEASE_NOTES.md generation skipped: {_rne}")

        # ── CRG cross-phase baseline: snapshot metrics for the next exit gate ──
        _project_path = Path(args.project).resolve()
        _crg_metrics_path = _project_path / ".sessi-work" / "crg_metrics.json"
        if _crg_metrics_path.is_file() and args.gate in _PHASE_EXIT_GATES.values():
            try:
                import shutil as _shutil
                _baseline_path = (
                    _project_path / ".methodology"
                    / f"crg_baseline_p{args.phase}.json"
                )
                _shutil.copy2(_crg_metrics_path, _baseline_path)
                # Stamp with git SHA for traceability
                import subprocess as _sp
                _sha_r = _sp.run(
                    ["git", "rev-parse", "HEAD"],
                    capture_output=True, text=True, cwd=str(_project_path),
                )
                _bl_data = json.loads(_baseline_path.read_text(encoding="utf-8"))
                _bl_data["_baseline_sha"] = _sha_r.stdout.strip()
                _bl_data["_baseline_phase"] = args.phase
                _baseline_path.write_text(json.dumps(_bl_data, indent=2), encoding="utf-8")
                print(f"  [CRG] Baseline saved: .methodology/crg_baseline_p{args.phase}.json")
            except Exception as _bl_exc:
                print(f"  [WARN] CRG baseline save failed: {_bl_exc}")

        git = _make_git(args, Path(args.project).resolve())
        git.ensure_gitignore()
        # Bug fix (P8 E2E 2026-07-04): write last_milestone_command for gate 4
        # to state.json BEFORE commit_and_push_gate so the audit field lands in
        # the pushed commit. Previously this block was inside the else AFTER
        # push, leaving state.json dirty in the working tree forever. The
        # original raw `write_text` also lacked the file_lock used elsewhere
        # (file_lock + atomic_write_json pattern from _update_state_checkpoint).
        # See plan: ~/.claude/plans/abundant-stargazing-hejlsberg.md
        if args.gate == 4:
            _state_path = Path(args.project).resolve() / ".methodology" / "state.json"
            if _state_path.exists():
                try:
                    with file_lock(state_lock_path(Path(args.project).resolve())):
                        _sd = json.loads(_state_path.read_text(encoding="utf-8"))
                        _sd["last_milestone_command"] = (
                            f"finalize-gate --gate 4 --phase {args.phase}"
                        )
                        atomic_write_json(_state_path, _sd)
                except Exception as _sme:
                    print(f"  [WARN] Could not write last_milestone_command to state.json: {_sme}")
        if args.gate == 1:
            _commit_ok = git.commit_fr_gate1(fr_id or "unknown", result.score, args.phase)
        else:
            _commit_ok = git.commit_and_push_gate(args.gate, args.phase, result.score)
            if _commit_ok:
                # Post-push self-check: warn loudly on dirty residue. Push itself
                # succeeded — the dirt is post-commit residue. Don't fail-fast.
                _dirty = _post_push_self_check(Path(args.project).resolve())
                if _dirty:
                    print(
                        f"  [WARN] post-push dirty tree ({len(_dirty)} path(s)):\n"
                        + "\n".join(f"    • {p}" for p in _dirty[:10])
                        + (f"\n    ... and {len(_dirty) - 10} more" if len(_dirty) > 10 else "")
                    )

        if not _commit_ok:
            _mark_gate_commit_failed(project_path, args.gate, fr_id)
            print(
                f"\n[BLOCKED] Gate {args.gate} evaluation passed but the git commit "
                "did not land (see '[git WARN] git commit failed' above — often a "
                "prepare-commit-msg hook rejection, e.g. stale trace attestation).\n"
                "  quality_manifest.json rolled back to quality_complete=false.\n"
                "  Fix the reported error, then re-run:\n"
                f"  python harness_cli.py finalize-gate --gate {args.gate} "
                f"--phase {args.phase} --project {project}"
            )
            return 6
        return 0

    except FileNotFoundError as e:
        print(f"\n[ERROR] {e}")
        print(
            f"  Run `python harness_cli.py run-gate --gate {args.gate} "
            f"--phase {args.phase} --project {args.project}` first,\n"
            "  then evaluate the dimensions and write the result file."
        )
        return 2

    except GateBlockedError as e:
        project_path = Path(args.project).resolve()
        print(_format_block_diagnostic(
            e, args.gate, args.phase, fr_id, 3, project_path,
        ))
        # Direction C: distil the block into cross-run failure memory so the
        # next run recalls it (best-effort — must never break the gate flow).
        try:
            from core.lessons import record_gate_block
            record_gate_block(project_path, gate_num=args.gate, phase=args.phase,
                              fr_id=fr_id, result=e.result)
        except Exception:  # noqa: BLE001
            pass
        return 1

# ---------------------------------------------------------------------------
# generate-next-plan
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# manifest
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# generate-verification-report  (P5 — produces 05-verification/VERIFICATION_REPORT.md)
# ---------------------------------------------------------------------------


def _post_push_self_check(project: Path) -> list[str]:
    """List dirty/untracked paths after a push (read-only, no modification).

    Bug class (post-28864f7): any post-push dirtiness (state.json mid-write
    residue, attestation.latest.json drift, HANDOVER.md half-flushed, etc.)
    leaves the working tree dirty. The caller should WARN loudly but NOT
    fail-fast — the push itself succeeded; the dirt is residue from the same
    atomic_write_json fsync that landed in the commit.

    Best-effort: if the probe fails (no git, non-zero rc, exception), return
    []. The probe is a diagnostic aid, never a gate.
    """
    import subprocess as _sp
    try:
        _r = _sp.run(
            ["git", "-C", str(project), "status", "--porcelain"],
            capture_output=True, text=True, timeout=10,
        )
    except Exception:  # pylint: disable=broad-exception-caught
        return []
    if _r.returncode != 0:
        return []
    # porcelain lines: " XY path" or "?? path" — split off the 2-char status
    # prefix and the optional space.
    out: list[str] = []
    for _line in _r.stdout.splitlines():
        if len(_line) > 3:
            out.append(_line[3:].strip())
    return out


# ---------------------------------------------------------------------------
# push-checkpoint  (P1/P2 human review checkpoint push + HANDOVER.md)
# ---------------------------------------------------------------------------

# Moved verbatim to cli/push_cmds.py (方案六). Re-exported so
# existing `from harness_cli import ...` imports keep working.
from cli.push_cmds import (  # noqa: E402, F401
    cmd_push_checkpoint,
    cmd_push_milestone,
)









# ---------------------------------------------------------------------------
# push-milestone  (P3+ milestone push + HANDOVER.md)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# v2.9.1 B.1: validate-handoff  (cross-deliverable dependency check)
# ---------------------------------------------------------------------------
# Closes the e2e finding where P1 orchestrator failed to produce
# TEST_INVENTORY.yaml, P2 orchestrator produced a wrong-shape TEST_SPEC.md
# (prose instead of derive_test_cases.md table), and Agent B peer review
# did not catch the cross-deliverable chain break. Workflow JS can now
# call this CLI as a pre-launch precondition before spawning the next
# phase's orchestrator.
# ---------------------------------------------------------------------------























# ---------------------------------------------------------------------------
# gate4-tag  (create annotated git tag from gate4_result.json)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------

# Moved verbatim to cli/project_cmds.py (方案六). Re-exported so
# existing `from harness_cli import ...` imports keep working.
from cli.project_cmds import (  # noqa: E402, F401
    cmd_amend_sab,
    cmd_audit_phase,
    cmd_audit_structure,
    cmd_doctor,
    cmd_effort,
    cmd_init_project,
    cmd_kill_switch,
    cmd_load_context,
    cmd_read_file,
    cmd_status,
)
# ---------------------------------------------------------------------------
# load-context
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# read-file (deterministic file read for workflow JS agents)
# ---------------------------------------------------------------------------
#
# Wraps scripts/file_loader.load_file() with CLI argument parsing so that
# workflow JS (which cannot use host APIs per playbook §4) can call this via
# the Bash tool through a SHELL WRAPPER agent. All validation (prefix, length,
# SHA-256, 8 MiB cap) happens server-side in Python — the LLM agent's only job
# is to emit the JSON stdout verbatim, eliminating LLM-interpretation failure
# modes documented in fc99e7f (v6 revert) and the 32-commit churn on
# loadFileViaBash/Python.
#
# Exit codes (machine-readable contract for workflow JS):
#   0 = OK (file exists, prefix matches, length within bounds)
#   1 = MISSING / PREFIX_MISMATCH / TOO_SHORT / TOO_LONG (recoverable)
#   2 = READ_ERROR (fatal: OSError, UnicodeDecodeError, etc.)
#
# Commonality: same flag surface as scripts/file_loader.py CLI, so callers can
# pick whichever entry point (standalone script or this CLI) without learning
# a new API.

# ---------------------------------------------------------------------------
# effort
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# advance-phase
# ---------------------------------------------------------------------------





















# Precision > recall by design (see _scope_violation_scripts docstring): this is
# a small, explicit set of tokens, matched whole (not as a substring) against
# "_"/"-"-separated segments of the filename stem — "_diag_constitution" splits
# to ["diag", "constitution"], an exact hit; "swipe" or "attempt" never match
# "wip"/"tmp" as a substring would. Whole-token matching makes it safe to extend
# this set (no accidental substring collisions to reason about), unlike a raw
# substring/regex search.








# ---------------------------------------------------------------------------
# dispatch  (spawn Agent A/B + auto-log sessions_spawn.log for HR-10)
# ---------------------------------------------------------------------------

# Moved verbatim to cli/fr_cmds.py (方案六). Re-exported so
# existing `from harness_cli import ...` imports keep working.
from cli.fr_cmds import (  # noqa: E402, F401
    cmd_dispatch,
    cmd_reload_policy,
    cmd_resume_fr_phase,
    cmd_run_fr_step,
    cmd_run_tool,
)
# ---------------------------------------------------------------------------
# run-fr-step  (Phase 3-8 sub-agent orchestration with per-step GitHub push)
# ---------------------------------------------------------------------------







# ---------------------------------------------------------------------------
# reload-policy
# ---------------------------------------------------------------------------



def _make_git(args: argparse.Namespace, project: Path) -> "GitStrategy":  # noqa: F821 — lazy import
    """Instantiate GitStrategy from parsed args. Lazy-imports to keep startup fast.

    Git is disabled if either --no-git or --dry-run is set. --dry-run is the
    preferred safety flag for push-milestone (Bug #112) — it prevents accidental
    origin pollution when exercising the command during bug hunts.
    """
    from harness.git_strategy import GitStrategy
    no_git = getattr(args, "no_git", False) or getattr(args, "dry_run", False)
    return GitStrategy(project=project, enabled=not no_git)



def _update_state_checkpoint(
    project: Path, gate_num: int, fr_id: str | None,
    gate_score: float | None = None, phase: int | None = None,
) -> None:
    """Write last_gate / last_fr to .methodology/state.json after a gate passes.

    Cross-process locked (SG-12): two parallel finalize-gate calls cannot
    race on the read-modify-write of state.json.
    """
    from datetime import datetime, timezone
    state_path = project / ".methodology" / "state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    with file_lock(state_lock_path(project)):
        existing: dict = {}
        if state_path.exists():
            try:
                existing = json.loads(state_path.read_text(encoding="utf-8"))
            except Exception:  # pylint: disable=broad-exception-caught
                pass
        # Track Gate 1 score for inter-FR variance check (D2 extension)
        if gate_num == 1 and fr_id and gate_score is not None and phase is not None:
            gate1_evidence.record_gate1_score(project, phase, fr_id, gate_score)
        existing["last_gate"] = gate_num
        existing["last_fr"] = fr_id
        existing["last_update"] = datetime.now(timezone.utc).isoformat()
        # Record phase_truth_passed when the phase exit gate completes
        _current_phase = int(existing.get("current_phase", phase or 0))
        if gate_num == _PHASE_EXIT_GATES.get(_current_phase):
            existing["phase_truth_passed"] = True
        atomic_write_json(state_path, existing)


    # 2. No other phase storage — state.json is the single source of truth.
    #    git config quality.phase and GitHub CURRENT_PHASE variable are no longer used.

# ---------------------------------------------------------------------------
# Gate BLOCKED diagnostic helpers
# ---------------------------------------------------------------------------

_DIMENSION_HINTS: dict[str, str] = {
    "linting":            "Run `ruff check . --fix` (or flake8); resolve all remaining lint errors",
    "type_safety":        "Run `mypy .`; add missing annotations and fix all type errors",
    "test_coverage":      "Run `pytest --cov` to find uncovered lines; add unit tests for each gap",
    "security":           "Fix OWASP-category issues; validate all inputs; remove eval/exec patterns",
    "secrets_scanning":   "Remove hard-coded secrets; move to env vars / vault; run `gitleaks detect`",
    "license_compliance": "Run `pip-licenses`; replace or vendor GPL/incompatible dependencies",
    "mutation_testing":   "Run `mutmut run`; add assertions that kill every surviving mutant",
    "architecture":       (
        "Two distinct failure modes — check tool_evidence to identify which applies: "
        "(1) CRG community issues: if god-module (size>50) or low cohesion (all communities <0.3) — "
        "either file an artifact-backed DA waiver in .sessi-work/gate{N}_result.json "
        "(devil_advocate + da_waiver + devil_advocate_evidence.architecture; valid at Gate 3 AND Gate 4) "
        "then re-run finalize-gate, OR reduce cross-package coupling so CRG detects sub-communities; "
        "for persistent CRG false positives (workflow tooling counted as product code, small-package "
        "Leiden over-fragmentation) calibrate crg_excludes / crg_cohesion_healthy in "
        ".methodology/harness_config.json; "
        "(2) Import boundary violations: verify imports comply with SAD.md layer boundaries and fix violations."
    ),
    "readability":        "Add [FR-XX] docstrings with Citations:; split functions >30 lines",
    "error_handling":     (
        "CRG flow_coverage score: percent of call-chain flows with at least one specific error handler. "
        "Use `get_affected_flows` CRG tool to identify which flows lack handlers. "
        "Fix: add try/except with specific exception types (not bare `except:`) to I/O, "
        "network, and external service calls. Bare `except:` does NOT improve CRG score."
    ),
    "documentation":      "All public APIs need [FR-XX] docstrings with Citations: + line numbers",
    "performance":        "Profile with cProfile; fix N+1 queries; add caching where needed",
}

def _format_block_diagnostic(
    exc: "GateBlockedError",  # noqa: F821 — lazy import
    gate_num: int,
    phase: int,
    fr_id: str | None,
    max_rounds: int,
    project: Path,
) -> str:
    """Format a structured diagnostic for a gate BLOCKED event; also writes last_block.md."""
    failing = [d for d in exc.result.dimensions if d.score is not None and d.score < d.threshold]
    passing = [d for d in exc.result.dimensions if d.score is not None and d.score >= d.threshold]

    lines = [
        "",
        "─" * 60,
        f"GATE {gate_num} BLOCKED"
        + (f"  fr={fr_id}" if fr_id else "")
        + f"  phase={phase}  after {max_rounds} SSI round(s)",
        f"  composite score : {exc.result.score:.1f}",
        f"  open critical   : {exc.result.open_critical}",
        f"  open high       : {exc.result.open_high}",
        "",
        f"Failing dimensions ({len(failing)}):",
    ]
    for dim in failing:
        if dim.score is not None:
            gap = dim.threshold - dim.score
            lines.append(
                f"  [FAIL] {dim.name:<22} score={dim.score:>5.1f}  "
                f"need={dim.threshold:>5.1f}  gap={gap:>4.1f}"
            )
        else:
            lines.append(
                f"  [FAIL] {dim.name:<22} score= None  "
                f"need={dim.threshold:>5.1f}  gap= N/A"
            )
        hint = _DIMENSION_HINTS.get(dim.name, "Review dimension-specific issues in SSI output")
        lines.append(f"         → {hint}")

    if passing:
        lines.append("")
        lines.append(
            f"Passing ({len(passing)}): "
            + ", ".join(f"{d.name}={d.score:.1f}" if d.score is not None else f"{d.name}=None" for d in passing)
        )

    fr_flag = f" --fr-id {fr_id}" if fr_id else ""
    lines.extend([
        "",
        "Fix the failing dimensions above, then resume:",
        f"  python harness_cli.py run-gate --gate {gate_num} --phase {phase}"
        f"{fr_flag} --project {project}",
        "─" * 60,
    ])

    # Write .methodology/last_block.md
    report_lines = [
        f"# Gate {gate_num} BLOCKED — Phase {phase}",
        "",
        f"Generated: {__import__('datetime').datetime.now().isoformat()}",
        f"fr_id: {fr_id or 'n/a'} | rounds: {exc.result.rounds_used} | "
        f"open_critical: {exc.result.open_critical} | open_high: {exc.result.open_high}",
        "",
        "## Failing Dimensions",
        "",
    ]
    for dim in failing:
        hint = _DIMENSION_HINTS.get(dim.name, "Review SSI output")
        if dim.score is not None:
            gap = dim.threshold - dim.score
            report_lines += [
                f"### {dim.name}",
                f"- score: {dim.score:.1f} / threshold: {dim.threshold:.1f} (gap: {gap:.1f})",
                f"- fix: {hint}",
                "",
            ]
        else:
            report_lines += [
                f"### {dim.name}",
                f"- score: None / threshold: {dim.threshold:.1f} (gap: N/A)",
                f"- fix: {hint}",
                "",
            ]
    report_lines += [
        "## Resume Commands",
        "",
        "```bash",
        f"python harness_cli.py run-gate --gate {gate_num} --phase {phase}"
        + (f" --fr-id {fr_id}" if fr_id else "")
        + f" --project {project}",
        "```",
    ]
    try:
        report_path = project / ".methodology" / "last_block.md"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text("\n".join(report_lines), encoding="utf-8")
        lines.append(f"  Full report → {report_path}")
    except Exception:  # pylint: disable=broad-exception-caught
        pass

    return "\n".join(lines)

# ---------------------------------------------------------------------------
# run-gap-analysis (M3)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# (run-pipeline removed in v2.5)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# verify-spec
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# doctor
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# migrate-trace-overlay (PR 2 of closed-loop traceability plan)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# build-trace-attestation / verify-trace (PR 3)
# ---------------------------------------------------------------------------



# ---------------------------------------------------------------------------
# check-logic
# ---------------------------------------------------------------------------









# ---------------------------------------------------------------------------
# Phase 9 Maintenance — Change Request lifecycle (cr-open / cr-update /
# cr-status / cr-close). ASPICE SUP.9 (bug) / SUP.10 (feat).
# ---------------------------------------------------------------------------

# Moved verbatim to cli/cr_cmds.py (方案六 family 1/7). Re-exported so
# existing `from harness_cli import cmd_cr_*` imports keep working.
from cli.cr_cmds import (  # noqa: E402, F401
    _cr_next_steps,
    cmd_cr_close,
    cmd_cr_open,
    cmd_cr_status,
    cmd_cr_update,
)

# ---------------------------------------------------------------------------
# CLI wiring
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    """Construct the ArgumentParser for the CLI."""
    p = argparse.ArgumentParser(
        prog="harness_cli.py",
        description="Harness-methodology standalone CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = p.add_subparsers(dest="command", metavar="command")
    sub.required = True

    from cli.phase_cmds import register as _register_phase_cmds
    _register_phase_cmds(sub)




    from cli.check_cmds import register as _register_check_cmds
    _register_check_cmds(sub)




    from cli.push_cmds import register as _register_push_cmds
    _register_push_cmds(sub)


    # ── Phase 9 Maintenance: Change Request lifecycle (cli/cr_cmds.py) ─────
    from cli.cr_cmds import register as _register_cr_cmds
    _register_cr_cmds(sub)


    from cli.gate_cmds import register as _register_gate_cmds
    _register_gate_cmds(sub)










    # (run-pipeline removed in v2.5 — old code consumed ~370 lines)



    from cli.project_cmds import register as _register_project_cmds
    _register_project_cmds(sub)






    from cli.fr_cmds import register as _register_fr_cmds
    _register_fr_cmds(sub)











    # check-constitution










    return p

def main() -> int:
    """Main entry point for the CLI."""
    # Load .env from CWD first (covers `cd project && python harness_cli.py`).
    env_loader.load_env_file(Path.cwd() / ".env")
    # Also load from --project path if it differs from CWD.
    for i, arg in enumerate(sys.argv[1:], 1):
        if arg in ("--project", "-p") and i < len(sys.argv):
            proj_env = Path(sys.argv[i]) / ".env"
            if proj_env.resolve() != (Path.cwd() / ".env").resolve():
                env_loader.load_env_file(proj_env)
            break
        if arg.startswith("--project="):
            proj_env = Path(arg.split("=", 1)[1]) / ".env"
            if proj_env.resolve() != (Path.cwd() / ".env").resolve():
                env_loader.load_env_file(proj_env)
            break

    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)

if __name__ == "__main__":
    sys.exit(main())
