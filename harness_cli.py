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
    8   Missing deliverables block — required artifacts not found on disk or not git-tracked
    10  PAUSE — Claude must evaluate gate; run finalize-gate then re-run pipeline
    11  Phase Truth < 90% (HR-11); fix and re-run with --phase-from N
    16  Constitution postflight below phase threshold; fix document quality
    21  Scope violation: untracked diagnostic script(s) at repo root; move to
        .sessi-work/tmp or delete, then re-run advance-phase
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import warnings
from datetime import datetime, timedelta, timezone  # noqa: F401  (timedelta: cli/ families resolve via _hc)
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, Optional

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

from harness.handover_generator import HandoverGenerator

# Ensure repo root on path so core/ and harness/ resolve
_REPO_ROOT = Path(__file__).parent
sys.path.insert(0, str(_REPO_ROOT))

# Script mode runs this file as __main__; register it under its module name
# too, so the cli/ family modules' `import harness_cli as _hc` binds THIS
# running module instead of re-executing the file (circular-import crash).
if __name__ == "__main__":  # pragma: no cover  (script-mode only)
    sys.modules.setdefault("harness_cli", sys.modules[__name__])

# Atomic state-file writers (CV-3 / SG-12 from robustness audit)
from core.atomic_io import StateTransaction, atomic_write_json, file_lock, state_lock_path  # noqa: E402
from core.phase_topology import (  # noqa: E402
    ADVANCE_GATE1_CHECK_PHASES as _TOPOLOGY_ADVANCE_GATE1,
    ENTRY_GATE_MAP as _TOPOLOGY_ENTRY_GATES,
    EXIT_GATE_MAP as _TOPOLOGY_EXIT_GATES,
    PER_FR_GATE1_PHASES as _TOPOLOGY_PER_FR_GATE1,
    PHASE_DIRS as _TOPOLOGY_PHASE_DIRS,
    VALID_PHASES,
    phase_name as _topology_phase_name,
)
from core.pre_flight import check_cli_tools  # noqa: E402
from core.harness_config import get_timeout  # noqa: E402
from core.utils.project_layout import ProjectLayout  # noqa: E402
from core.canonical_form import canonical_form  # noqa: E402  # I: single source of truth for FR/NFR/TASK IDs


# I: helper for test_frNN.py / sentinel filenames — replaces 6 sites that each
# did their own `re.match(r"FR-(\\d+)", fr_id)` + fallback `re.sub("[^a-z0-9]", ...)`.
def _fr_num_str(fr_id: str) -> str:
    """Return zero-padded digit string from FR-ID (canonical_form first).

    Examples:
      _fr_num_str("FR-01") -> "01"
      _fr_num_str("fr01") -> "01"   # canonicalised via canonical_form
      _fr_num_str("FR_12") -> "12"
      _fr_num_str("FR-100") -> "100" # 3+ digits preserved
      _fr_num_str("invalid") -> "invalid"  # passthrough on parse failure
    """
    try:
        canon = canonical_form(fr_id)
        m = re.match(r"(?:FR|NFR|TASK)-(\d+)", canon)
        if m:
            return m.group(1).zfill(2)
        return canon
    except ValueError:
        return fr_id
# Bug #105: framework-owned mutation_testing path. Pyright cannot resolve this
# import statically (no type stub for core.quality_gate.mutation_enforcer),
# so we silence reportAttributeAccessIssue here. Kept even though no direct
# caller remains in this file: cli/gate_cmds.py resolves it via _hc.
from core.quality_gate.mutation_enforcer import compute_mutation_score  # noqa: E402, F401 # type: ignore[reportAttributeAccessIssue] # Bug #105

# ---------------------------------------------------------------------------
# .env file loader (no external dependency)
# ---------------------------------------------------------------------------

def _load_env_file(env_path: Path) -> list[str]:
    """Load KEY=VALUE pairs from a .env file into os.environ.

    Rules:
    - Lines starting with # or blank are skipped.
    - Does NOT override variables already set in the shell environment.
    - Strips surrounding single/double quotes from values.
    - Inline comments (value # comment) are stripped.

    Returns list of keys that were loaded (empty if file not found).
    """
    if not env_path.is_file():
        return []
    loaded: list[str] = []
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if not key:
            continue
        value = value.split("#")[0].strip().strip('"').strip("'")
        if key not in os.environ:   # never override shell-level vars
            os.environ[key] = value
            loaded.append(key)
    return loaded

# Phases where Gate 1 runs per-FR (P9 maintenance: per-CR touched FRs).
# Sourced from the topology SSOT (core/phase_topology.py) — do not re-declare.
_PER_FR_GATE1_PHASES: frozenset[int] = _TOPOLOGY_PER_FR_GATE1
# Statuses that indicate an agent dispatch failure (all others treated as success).
_DISPATCH_ERROR_STATUSES: frozenset[str] = frozenset({"REJECT", "BLOCKED", "FAILED", "ERROR", "TIMEOUT", "REGRESSION_GUARD"})
# Per-step default max_turns for run-fr-step. --max-turns override takes priority.
# GATE1 needs more turns: 5-step workflow (run-gate → evaluate → write result.json
# → finalize-gate → report) plus multi-dimension assessment on brownfield codebases.
_STEP_MAX_TURNS: dict[str, int] = {
    "TDD-RED":      40,
    "TDD-GREEN":    40,
    "TDD-IMPROVE":  40,
    "GATE1":        70,
    "GATE1-DELTA":  70,
    "CODE-FIX":     50,
    "TEST-FIX":     40,
    "INFRA-FIX":    40,
    "LINT-FIX":     70,   # 20+ constant renames with reference updates need many turns
    "COVERAGE-FIX": 90,   # bulk spec-test writing (100+ tests) needs headroom
}

# ---------------------------------------------------------------------------
# Tool availability checks (S2: prevents LLM guessing when tools are missing).
# Tool-id → (check_cmd, human_name) lives in the toolchain registry
# (harness/toolchains/registry.py) and is resolved per project language.
# Only the dimension-name fallback for older YAML configs without a tool field
# remains here. Dimension names that don't map to a dedicated tool
# (LLM-evaluated dimensions) have no tool requirement.
_DIM_FALLBACK_CHECKS: dict[str, tuple[str, str]] = {
    "secrets_scanning": ("gitleaks version 2>&1", "gitleaks"),
    "mutation_testing": ("mutmut --help 2>&1", "mutmut"),
    "license_compliance": ("scancode --version 2>&1", "scancode-toolkit"),
    "linting": ("ruff --version 2>&1 || python3 -m ruff --version 2>&1", "ruff"),
    "type_safety": ("mypy --version 2>&1", "mypy"),
    "test_coverage": ("pytest --version 2>&1", "pytest + coverage"),
    "architecture": ("code-review-graph status 2>&1", "code-review-graph"),
}

def _run_tool_check(check_cmd: str, cwd: str | None = None) -> bool:
    """Run a shell availability probe; True when it exits 0.

    *cwd* is the target project root: some check_cmds are cwd-relative —
    `npx --no-install <tool>` resolves node_modules from cwd, and the
    tsc-checkjs probe does `test -f tsconfig.checkjs.json`. Passing it
    explicitly decouples the probe from the harness's ambient cwd.
    """
    result = subprocess.run(
        ["bash", "-c", check_cmd],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        timeout=10, text=True, cwd=cwd,
    )
    return result.returncode == 0


def _check_tool_for_dim(
    dim_name: str, tool_name: str | None, language: str = "python",
    project_root: str | None = None,
) -> tuple[bool, str]:
    """Check if the required tool for a dimension is installed.

    Resolves the YAML tool name through the toolchain registry (for python the
    YAML name passes through unchanged; other languages resolve by dimension
    via DIMENSION_TOOLS). Falls back to the dimension-name table for older
    configs without a tool field. *project_root* is the cwd for cwd-relative
    probes (npx, tsconfig.checkjs.json). Returns (available, diagnostic).
    """
    from harness.toolchains import get_tool_spec, resolve_tool_id

    resolved = resolve_tool_id(dim_name, language, yaml_tool=tool_name)
    if resolved is None and language != "python":
        # Registered languages must cover every tool-scored dimension (R8);
        # reaching here means the toolchain registry has no entry.
        return False, (
            f"{dim_name}: no '{language}' toolchain entry — "
            f"language not fully supported (see harness/toolchains/registry.py)"
        )

    spec = get_tool_spec(resolved) if resolved else None
    if spec is not None:
        try:
            ok = _run_tool_check(spec.check_cmd, cwd=project_root)
            return ok, (
                "" if ok else f"{dim_name}: {spec.human_name} ({resolved}) not found"
            )
        except Exception:
            return False, f"{dim_name}: {spec.human_name} ({resolved}) check failed"

    # Fall back to dimension name lookup (older configs without tool field)
    info = _DIM_FALLBACK_CHECKS.get(dim_name)
    if info is None:
        return True, ""  # No tool requirement — pass (LLM-evaluated dimension)
    check_cmd, human_name = info
    try:
        ok = _run_tool_check(check_cmd, cwd=project_root)
        return ok, ("" if ok else f"{dim_name}: {human_name} not found")
    except Exception:
        return False, f"{dim_name}: {human_name} check failed"

def _verify_gate_tools(
    gate_num: int, project: str, state_root: str | None = None
) -> tuple[bool, list[str]]:
    """Check all required tools for a gate exist (S2).

    Reads gate YAML config: if a dimension has requires_tool_execution: true
    and a tool field, the tool MUST be installed. Dimensions without
    requires_tool_execution (e.g. security, architecture) are LLM-evaluated
    and skipped.

    Tool resolution is language-aware: the project language is read from
    *state_root*/.methodology/state.json (defaults to *project* — pass
    state_root explicitly when gate configs and FSM state live in different
    roots, as in init-project where configs come from the harness checkout).

    Returns (all_ok, missing_list).
    """
    from harness.toolchains import get_project_language
    # The target project (where state.json + node_modules + tsconfig live) is
    # state_root when given (init-project: configs come from the harness
    # checkout), else project itself.
    target_root = state_root or project
    language = get_project_language(target_root)
    import yaml as _yaml
    cfg_path = None
    # Try phase-specific name first, then generic pattern
    for pattern in [
        f"gate{gate_num}_p*.yaml",
        f"gate{gate_num}_*.yaml",
    ]:
        import glob as _glob
        candidates = _glob.glob(
            str(Path(project) / "harness" / "gate_configs" / pattern)
        )
        if candidates:
            cfg_path = Path(candidates[0])
            break
    if not cfg_path or not cfg_path.exists():
        return True, []  # No config to check — pass

    try:
        cfg = _yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    except Exception:
        return True, []

    from harness.harness_bridge import filter_enabled_dimensions
    cfg["dimensions"] = filter_enabled_dimensions(
        cfg.get("dimensions", []), target_root
    )

    missing: list[str] = []
    for dim in cfg.get("dimensions", []):
        dim_name = dim.get("name", "")
        requires_tool = dim.get("requires_tool_execution", False)
        if not requires_tool:
            continue  # LLM-evaluated dimension — skip tool check
        tool_name = dim.get("tool")  # May be None for older configs
        ok, diag = _check_tool_for_dim(
            dim_name, tool_name, language, project_root=target_root
        )
        if not ok and diag:
            missing.append(diag)
    return len(missing) == 0, missing


def _verify_all_gate_tools(project: str) -> tuple[bool, list[str]]:
    """Check that every tool required by ANY gate config is installed.

    Run at each phase entry so missing required components (notably
    `code-review-graph`, which scores the architecture dimension) surface at
    project setup rather than deep inside Gate 3/4. CRG and the other SSI tools
    are hard dependencies — there is no graceful degradation.
    """
    all_missing: list[str] = []
    seen: set[str] = set()
    for gate_num in (1, 2, 3, 4):
        _, missing = _verify_gate_tools(gate_num, project)
        for m in missing:
            if m not in seen:
                seen.add(m)
                all_missing.append(m)

    # Soft check: mutmut 2.5.x hardcodes `python` (not `python3`). If mutmut is
    # required but only python3 exists, warn (don't block — the user can symlink).
    if not all_missing:
        _mutmut_needed = any("mutmut" in m for m in seen)
        if _mutmut_needed:
            import shutil as _shutil
            if _shutil.which("mutmut") and not _shutil.which("python") and _shutil.which("python3"):
                print(
                    "  [WARN] mutmut hardcodes `python` (not `python3`).\n"
                    "    Preferred fix: activate the project venv (`.venv/bin/python` exists).\n"
                    "    Fallback: ln -s $(which python3) /usr/local/bin/python\n"
                    "    Without this, mutation_testing dimension will fail at Gate 3/4.",
                    file=sys.stderr,
                )

    return len(all_missing) == 0, all_missing

def _fr_step_preflight(step: str, project: Path, fr_id: str | None, srs_path: "Path | str | None" = None) -> tuple[bool, list[str]]:
    """Verify environment and artifacts are ready before spawning a sub-agent for an FR step.

    Returns (ok, error_lines). On ok=[], sub-agent spawn proceeds. On failure,
    caller prints error_lines to stderr and returns 1 before any agent dispatch.

    Step-aware: GATE1/CODE-FIX need full tool + DB checks; TDD-RED only needs pytest.
    """
    errors: list[str] = []
    step = step.upper()

    # ── 1. Git repo check ────────────────────────────────────────────────────
    if not project.exists() or not (project / ".git").exists():
        errors.append(f"✗ {project} is not a git repo or does not exist")

    # ── 2. SRS.md (required for all steps — traceability back to requirements) ─
    srs = project / "SRS.md"
    if srs_path:
        srs_arg = Path(srs_path)
        srs = srs_arg if srs_arg.is_absolute() else project / srs_arg
    else:
        for candidate in ["01-requirements/SRS.md", "SRS.md", ".methodology/SRS.md"]:
            if (project / candidate).exists():
                srs = project / candidate
                break

    if not srs.exists():
        try:
            rel_path = srs.relative_to(project)
        except ValueError:
            rel_path = srs
        errors.append(f"✗ SRS.md not found at {rel_path} (required for all FR steps)")

    # ── 3. quality_manifest.json + FR-ID registration ────────────────────────
    manifest_path = project / ".methodology" / "quality_manifest.json"
    if not manifest_path.exists():
        errors.append("✗ .methodology/quality_manifest.json not found (run run-phase first)")
    else:
        try:
            m = json.loads(manifest_path.read_text(encoding="utf-8"))
            registered = m.get("fr_ids", [])
            if fr_id and fr_id not in registered:
                errors.append(
                    f"✗ FR-ID {fr_id} not in quality_manifest.json fr_ids ({', '.join(registered)})"
                )
        except Exception:
            errors.append("✗ quality_manifest.json is malformed JSON")

    # ── 4. TEST_SPEC.md (required for TDD-RED — test names come from here) ───
    # Must match _extract_test_spec_names: canonical location is 02-architecture/
    test_spec = ProjectLayout(project).test_spec_path
    if step == "TDD-RED":
        if not test_spec.exists():
            errors.append(
                "✗ 02-architecture/TEST_SPEC.md not found (TDD-RED requires test catalog)"
            )
        else:
            # Basic validity: must contain FR-ID sections
            try:
                content = test_spec.read_text(encoding="utf-8")
                if fr_id and not re.search(rf'#+\s+{re.escape(fr_id)}\b', content):
                    errors.append(
                        f"✗ 02-architecture/TEST_SPEC.md has no section for {fr_id}"
                        " (run derive_test_cases.md skill first)"
                    )
            except Exception:
                errors.append("✗ 02-architecture/TEST_SPEC.md exists but is unreadable")

    # ── 5. Tool checks (step-aware) ───────────────────────────────────────────
    def _missing_tool(name: str) -> str:
        return f"✗ {name} not found in PATH — install with: pip install {name}"

    if step in ("GATE1", "GATE1-DELTA", "CODE-FIX"):
        _, gate_errors = _verify_gate_tools(1, str(project))
        errors.extend(gate_errors)
        # Delegate env readiness to LLM-driven run-env-check — no hardcoded
        # DATABASE_URL/pytest/ruff here. Claude evaluates project-specific needs
        # from SAD.md + SRS.md at run-env-check time.
        env_result = project / ".sessi-work" / "env_check_result.json"
        if not env_result.exists():
            errors.append(
                "✗ env_check_result.json not found. "
                "Run: python harness_cli.py run-env-check --phase <phase> --project . "
                "then evaluate inline and run finalize-env-check."
            )

    if step in ("TDD-RED", "TDD-GREEN", "TDD-IMPROVE"):
        missing_tools = check_cli_tools(["pytest", "ruff"])
        for tool in missing_tools:
            errors.append(_missing_tool(tool))

    return len(errors) == 0, errors

# Non-dotfile (consistent with other .methodology/ files like state.json, sessions_spawn.log).
# Replaces the old ".gate_timestamps.jsonl" hidden file name used before 2026-05-18.
_GATE_TIMESTAMPS_FILE = "gate_timestamps.jsonl"
_GATE_TIMESTAMPS_MAX_ENTRIES = 200
# Sizing: 22 FRs × max_fix_rounds(3) × 3 phases ≈ 200; increase if FR count > 22.

def _record_gate_timestamp(project: Path, phase: int, gate_num: int, fr_id: str | None) -> None:
    """Append gate commit timestamp to .methodology/gate_timestamps.jsonl (P1 persistence).

    Called only on SUCCESSFUL gate finalization — not on failed checks — so the file
    represents genuine completed gates, not attempts.  Trims to the last
    _GATE_TIMESTAMPS_MAX_ENTRIES entries to bound file growth.
    """
    import time as _time
    ts_dir = project / ".methodology"
    ts_dir.mkdir(parents=True, exist_ok=True)
    ts_file = ts_dir / _GATE_TIMESTAMPS_FILE

    # One-time migration: rename old hidden-file to the new visible name
    _old = ts_dir / ".gate_timestamps.jsonl"
    if _old.exists() and not ts_file.exists():
        try:
            _old.rename(ts_file)
        except OSError:
            pass

    entry = {"phase": phase, "gate": gate_num, "fr_id": fr_id or "phase", "ts": _time.time()}
    try:
        # Append
        with open(str(ts_file), "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
        # Trim to last _GATE_TIMESTAMPS_MAX_ENTRIES lines
        raw = ts_file.read_text(encoding="utf-8")
        lines = [line for line in raw.splitlines() if line.strip()]
        if len(lines) > _GATE_TIMESTAMPS_MAX_ENTRIES:
            ts_file.write_text(
                "\n".join(lines[-_GATE_TIMESTAMPS_MAX_ENTRIES:]) + "\n",
                encoding="utf-8",
            )
    except OSError:
        pass  # Non-blocking

def _check_commit_intervals(
    project: str, phase: int, gate_num: int
) -> tuple[bool, str]:
    """Check if current gate attempt would exceed the batch-commit threshold (P1).

    Pure read — does NOT write timestamps.  The caller (cmd_finalize_gate) must call
    _record_gate_timestamp() only on successful finalization, so failed attempts don't
    accumulate in the file and trigger false positives on retry.

    Blocks if ≥2 prior successful finalizations exist within a 2-second window for the
    same (phase, gate) bucket (3 total = statistically implausible for genuine per-FR work).
    Returns (ok, diagnostic).
    """
    import time as _time
    project_path = Path(project)
    ts_dir = project_path / ".methodology"
    ts_file = ts_dir / _GATE_TIMESTAMPS_FILE

    # One-time migration: honour renamed dotfile for legacy projects
    _old = ts_dir / ".gate_timestamps.jsonl"
    if _old.exists() and not ts_file.exists():
        try:
            _old.rename(ts_file)
        except OSError:
            pass

    now = _time.time()
    recent: list[dict] = []

    if ts_file.exists():
        try:
            for line in ts_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if (entry.get("phase") == phase
                        and entry.get("gate") == gate_num
                        and now - entry.get("ts", 0) <= 2.0):
                    recent.append(entry)
        except OSError:
            pass

    if len(recent) >= 2:  # 2 prior successful + 1 current attempt = 3 total in window
        return False, (
            f"{len(recent) + 1} gate commits within 2 seconds — "
            "scores must be evaluated per-FR with genuine evidence, not batch-copied"
        )
    return True, ""

_GATE1_SCORES_FILE = ".gate1_scores.json"

def _record_gate1_score(project: Path, phase: int, fr_id: str, score: float) -> None:
    """Track Gate 1 composite score per FR for inter-FR variance check.

    Prunes phases older than (phase - 1) to bound file growth; the previous phase
    data is kept so finalize-gate can still reference it, but anything further back
    is stale and safe to drop.
    """
    scores_file = project / ".methodology" / _GATE1_SCORES_FILE
    scores: dict = {}
    if scores_file.exists():
        try:
            scores = json.loads(scores_file.read_text(encoding="utf-8"))
        except Exception:  # pylint: disable=broad-exception-caught
            pass
    scores.setdefault(str(phase), {})[fr_id] = score
    # Prune stale phases — keep current and previous only
    stale = [k for k in list(scores.keys()) if int(k) < phase - 1]
    for k in stale:
        del scores[k]
    try:
        atomic_write_json(scores_file, scores)
    except Exception:  # pylint: disable=broad-exception-caught
        pass




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
_PHASE_DELIVERABLES: dict[int, list[str]] = {
    1: ["SRS.md", "SPEC_TRACKING.md", "TRACEABILITY_MATRIX.md", "TEST_INVENTORY.yaml"],
    2: ["SAD.md", "ADR.md", "TEST_SPEC.md"],
    6: ["QUALITY_REPORT.md", "RELEASE_NOTES.md", "FINAL_SIGN_OFF.md", "quality_manifest"],
}
# Documents that Agent B must embed per phase (SAD.md doesn't exist until P2)
_REQUIRED_EMBEDDED_DOCS: dict[int, list[str]] = {
    1: ["SRS.md"],
    2: ["SRS.md", "SAD.md"],
    6: ["QUALITY_REPORT.md", "RELEASE_NOTES.md", "FINAL_SIGN_OFF.md", "VERIFICATION_REPORT.md"],
}

# ---------------------------------------------------------------------------
# plan-phase
# ---------------------------------------------------------------------------

def cmd_plan_phase(args: argparse.Namespace) -> int:
    """Generate phase execution plan from SRS/SAD artifacts."""
    from scripts.generate_full_plan import generate_full_plan

    repo_path = Path(args.project).resolve()
    output_path = Path(args.output) if args.output else None

    print(f"\n{'='*60}\nplan-phase: Phase {args.phase} | repo={repo_path}\n{'='*60}")

    plan = generate_full_plan(args.phase, repo_path, output_path,
                              force=getattr(args, "force", False))
    if plan is None:
        print(f"\n[ERROR] Failed to generate plan for phase {args.phase}")
        return 1

    if output_path:
        print(f"\nPlan written → {output_path}  ({len(plan)} chars)")
    else:
        print(plan)
    return 0

# ---------------------------------------------------------------------------
# plan-all
# ---------------------------------------------------------------------------

def cmd_plan_all(args: argparse.Namespace) -> int:
    """Generate all 8 phase plans in dynamic mode at project start."""
    from scripts.generate_full_plan import generate_full_plan

    project = Path(args.project).resolve()
    out_dir = Path(args.output_dir) if args.output_dir else project / ".methodology"

    if not (project / ".methodology").is_dir():
        print("[ERROR] .methodology/ not found. Run init-project first.")
        return 1

    _force = getattr(args, "force", False)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Guard quality_manifest.json from accidental shrink. `plan-all` only
    # regenerates phaseN_plan.md + plan_status.md; it never writes
    # quality_manifest.json. If a manifest already exists *and* is a
    # readable, valid JSON file we leave it alone — because the manifest
    # holds accumulated Gate scores and shrinking it resets pipeline
    # progress. An empty file, a directory, a broken symlink, or
    # non-JSON content all bypass this guard so plan-all can proceed
    # (the manifest is effectively absent in those cases).
    _manifest = out_dir / "quality_manifest.json"
    if _manifest.is_file():
        try:
            json.loads(_manifest.read_text(encoding="utf-8"))
            _manifest_usable = True
        except (OSError, json.JSONDecodeError):
            _manifest_usable = False
    else:
        _manifest_usable = False
    if _manifest_usable:
        print(
            f"[PRESERVE] {_manifest.name} already exists; "
            "plan-all does not touch it. Use 'harness_cli manifest --force "
            "--fr-ids ... --sad ...' to regenerate."
        )
    results = []
    for phase_num in VALID_PHASES:
        out_path = out_dir / f"phase{phase_num}_plan.md"
        plan = generate_full_plan(phase_num, project, out_path, dynamic=True, force=_force)
        status = "OK" if plan else "FAIL"
        results.append((phase_num, status, str(out_path)))
        print(f"  Phase {phase_num}: {status} → {out_path}")

    # Write plan_status.md
    status_path = out_dir / "plan_status.md"
    status_lines = [
        "# Plan Generation Status",
        "",
        f"Generated: {__import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "Mode: Dynamic",
        "",
        "| Phase | Status | File |",
        "|-------|--------|------|",
    ]
    for phase_num, status, path in results:
        status_lines.append(f"| {phase_num} | {status} | {Path(path).name} |")
    status_lines.append("")
    status_path.write_text("\n".join(status_lines), encoding="utf-8")
    print(f"\nplan_status.md → {status_path}")

    failed = [p for p, s, _ in results if s == "FAIL"]
    if failed:
        print(f"[ERROR] Failed phases: {failed}")
        return 1
    return 0

# ---------------------------------------------------------------------------
# run-phase
# ---------------------------------------------------------------------------

def _collect_shared_test_files(project: Path, base: str,
                                existing: list[str]) -> None:
    """Append git-tracked conftest.py and helpers/**/*.py under *base*."""
    import subprocess as _sp
    try:
        r = _sp.run(
            ["git", "ls-files", f"{base}/conftest.py", f"{base}/helpers/"],
            capture_output=True, text=True, cwd=str(project),
        )
        for line in r.stdout.splitlines():
            if line.endswith(".py") and line not in existing:
                existing.append(line)
    except Exception:
        pass


def _get_test_directories(project: Path) -> list[Path]:
    """Return all valid test directories (resolving symlinks and canonical layout)."""
    dirs = []
    
    # 1. Project root tests/
    tests_root = project / "tests"
    if tests_root.is_dir() and not tests_root.is_symlink():
        dirs.append(tests_root)
        
    if tests_root.is_symlink():
        try:
            real_tests = tests_root.resolve()
            if real_tests.is_dir() and real_tests not in dirs:
                dirs.append(real_tests)
        except ValueError:
            pass
            
    # 2. Canonical harness layout
    canonical_tests = ProjectLayout(project).phase3_development_dir / "tests"
    if canonical_tests.is_dir():
        # Avoid duplicate if symlink already resolved to canonical
        if canonical_tests.resolve() not in [d.resolve() for d in dirs]:
            dirs.append(canonical_tests)
            
    return dirs


def _git_test_patterns(project: Path, num: str, num_raw: str) -> list[str]:
    """Return git-tracked test file path patterns, resolving symlinks.

    Bug #130 fix (2026-06-27): canonical harness layout places tests at
    ``03-development/tests/``. Without explicit patterns for it, `git log`
    returns empty and D1-RED blocks. We scan all valid test directories
    returned by `_get_test_directories`.
    """
    patterns = []
    # Always include 'tests/' by default to preserve historical behavior
    test_dirs_rel = ["tests"]

    for d in _get_test_directories(project):
        try:
            d_rel = str(d.resolve().relative_to(project.resolve()))
            if d_rel not in test_dirs_rel:
                test_dirs_rel.append(d_rel)
        except ValueError:
            continue

    for d_rel in test_dirs_rel:
        patterns.extend([
            f"{d_rel}/test_fr{num}.py",
            f"{d_rel}/test_fr{num_raw}.py",
        ])
        _collect_shared_test_files(project, d_rel, patterns)

    return patterns


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

def _scan_test_functions(test_dir: Path, language: str = "python") -> set[str]:
    """Scan test files for harness-convention test names.

    python: `def test_*` function definitions.
    js/ts:  it('test_*') / test("test_*") TITLES — the harness naming
            convention (templates/TEST_SPEC.md) that keeps D4 spec-coverage
            and P1 Naming Authority matching language-independent.
    """
    from core.utils.lang_patterns import JS_TEST_TITLE_PATTERN, iter_test_files

    fns: set[str] = set()
    if not test_dir.is_dir():
        return fns
    if language == "python":
        files = sorted(test_dir.rglob("*.py"))
    else:
        files = list(iter_test_files(test_dir, language))
    for f in files:
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if language == "python":
            for line in text.splitlines():
                m2 = re.match(r"^\s*(?:async\s+)?def\s+(test_\w+)\s*\(", line)
                if m2:
                    fns.add(m2.group(1))
        else:
            fns.update(JS_TEST_TITLE_PATTERN.findall(text))
    return fns

def _flatten_test_names(inventory: dict | None) -> set[str]:
    """Flatten TEST_INVENTORY.yaml fr_tests + cross_cutting into a set of function names."""
    names: set[str] = set()
    if not inventory:
        return names
    for fr_key in ("fr_tests", "cross_cutting"):
        section = inventory.get(fr_key, {})
        if isinstance(section, list):
            names.update(section)
        elif isinstance(section, dict):
            for layers in section.values():
                if isinstance(layers, list):
                    names.update(layers)
                elif isinstance(layers, dict):
                    for items in layers.values():
                        if isinstance(items, list):
                            names.update(items)
    return names


def _parse_test_spec(spec_path: Path) -> list[dict]:
    """Parse TEST_SPEC.md and return all named test cases.

    Handles the markdown table format produced by the derive_test_cases.md skill:
      | # | Test Function | Type | Derivation |
      |---|---|---|---|
      | 1 | `test_frXX_...` | happy_path | Q1 |

    Returns a list of dicts with keys: test_fn, type, derivation, fr_id.
    Backtick-wrapped function names (e.g. `test_foo`) are unwrapped automatically.
    """
    results: list[dict] = []
    if not spec_path.exists():
        return results

    text = spec_path.read_text(encoding="utf-8")
    current_fr: str = ""
    in_table = False
    header_skipped = False

    for line in text.splitlines():
        stripped = line.strip()

        # Detect FR section headers: ## FR-XX: ... or ### FR-XX: ...
        # Accept both H2 and H3 levels so docs and concrete specs can use either.
        fr_match = re.match(r"^#{2,3}\s+(FR-\d+)[:\s]", stripped)
        if fr_match:
            current_fr = fr_match.group(1)
            in_table = False
            header_skipped = False
            continue

        # Detect any H2/H3 section that is NOT an FR header — prevents last FR
        # bleeding into the next section. Tags items under a normalised slug so
        # they're traceable but won't be confused with real FR-IDs (which follow
        # the FR-\d+ pattern).
        if re.match(r"^#{2,3}\s+\S", stripped) and not re.match(r"^#{2,3}\s+(FR-\d+)[:\s]", stripped):
            h_text = re.sub(r"^#{2,3}\s+", "", stripped).strip()
            current_fr = re.sub(r"\W+", "_", h_text.lower()).rstrip("_")[:30]
            in_table = False
            header_skipped = False
            continue

        # Horizontal rule — close current table without changing FR context
        if re.match(r"^---+$", stripped) or re.match(r"^\*\*\*+$", stripped):
            in_table = False
            continue

        # Detect table header row (| # | Test Function | ...)
        if "|" in stripped and re.search(r"Test Function", stripped, re.IGNORECASE):
            in_table = True
            header_skipped = False
            continue

        # Skip the separator row (|---|---|...)
        if in_table and re.match(r"^\|[-| ]+\|$", stripped):
            header_skipped = True
            continue

        # Parse data rows
        if in_table and header_skipped and stripped.startswith("|") and stripped.endswith("|"):
            cols = [c.strip() for c in stripped.split("|")[1:-1]]
            if len(cols) >= 3:
                # cols[0] = #, cols[1] = test fn, cols[2] = type, cols[3] = derivation
                raw_fn = cols[1].strip("`").strip()
                raw_fn = re.sub(r"\[.*\]$", "", raw_fn)  # strip parametrize IDs
                if raw_fn.startswith("test_") and len(raw_fn) > 6:
                    results.append({
                        "test_fn": raw_fn,
                        "type": cols[2] if len(cols) > 2 else "",
                        "derivation": cols[3] if len(cols) > 3 else "",
                        "fr_id": current_fr,
                    })
            continue

        # A blank line or non-table line ends the table
        if in_table and not stripped.startswith("|"):
            if stripped:
                in_table = False

    return results


def _run_spec_coverage_check(
    project: Path,
    threshold: float = 80.0,
    *,
    fr_id: str | None = None,
    verbose: bool = True,
) -> tuple[int, float]:
    """Check TEST_SPEC.md items against actual test function implementations.

    This is the UNIFIED D4 check (v2.6). TEST_SPEC.md is the single source of
    truth for all test traceability. For each test case declared in TEST_SPEC.md
    (P2 deliverable), verify that a matching test function exists in tests/.
    Replaces the prior two-check model (I-1 TEST_INVENTORY.yaml forward +
    I-5 TEST_SPEC.md backward).

    Args:
        project: Project root directory.
        threshold: Minimum percentage of spec items that must be implemented.
        fr_id: If set, check only items for that FR (e.g. "FR-03").
        verbose: Print detailed results.

    Returns:
        (exit_code, coverage_pct). 0 = pass, 1 = below threshold.
        If TEST_SPEC.md is absent, returns (0, 100.0) — non-blocking.
    """
    spec_path = ProjectLayout(project).test_spec_path
    if not spec_path.exists():
        sad_path = ProjectLayout(project).sad_path
        sad_has_frs = False
        if sad_path.exists():
            sad_text = sad_path.read_text(encoding="utf-8", errors="replace")
            if re.search(r"\bFR-\d+\b", sad_text):
                sad_has_frs = True

        if sad_has_frs:
            if verbose:
                print("[spec-coverage] ERROR: TEST_SPEC.md not found at 02-architecture/TEST_SPEC.md but SAD.md has FRs.")
            return (1, 0.0)
            
        if verbose:
            print("[spec-coverage] TEST_SPEC.md not found and SAD.md has no FRs — skipping.")
        return (0, 100.0)

    items = _parse_test_spec(spec_path)
    if fr_id:
        items = [i for i in items if i["fr_id"] == fr_id]

    if not items:
        # v2.9 B.3 fix: vacuous pass was masking wrong-shape TEST_SPEC.md
        # (e.g. prose strategy doc instead of derive_test_cases.md table).
        # Check whether FRs are actually defined — if yes, 0 cases is a real
        # failure (orchestrator skipped derive_test_cases.md skill), not a
        # vacuous pass.
        fr_defined = False
        # Authoritative source: SAD.md FR table (P2 deliverable).
        # Fallback: SPEC_TRACKING.md (P1 deliverable) — if SAD doesn't exist
        # but SPEC_TRACKING does, FRs are still declared.
        for probe_rel in (
            "02-architecture/SAD.md",
            "01-requirements/SPEC_TRACKING.md",
        ):
            probe_path = project / probe_rel
            if probe_path.exists():
                try:
                    _probe_text = probe_path.read_text(encoding="utf-8", errors="replace")
                    if re.search(r"\bFR-\d+\b", _probe_text):
                        fr_defined = True
                        break
                except OSError:
                    pass
        scope = f" for {fr_id}" if fr_id else ""
        if fr_defined:
            if verbose:
                print(
                    f"[spec-coverage] BLOCKED{scope}: TEST_SPEC.md has 0 parseable "
                    f"test cases but FRs are defined. The file is likely the wrong "
                    f"shape (prose strategy doc instead of derive_test_cases.md "
                    f"table). Re-run the derive_test_cases.md skill in Phase 2."
                )
            return (1, 0.0)
        if verbose:
            print(f"[spec-coverage] No test cases found in TEST_SPEC.md{scope} and no FRs defined — vacuous pass.")
        return (0, 100.0)

    # v2.6.1: Enforce P1 Naming Authority to prevent LLM hallucinations
    inventory_path = project / "TEST_INVENTORY.yaml"
    if inventory_path.exists() and not fr_id:
        try:
            import yaml
            inventory = yaml.safe_load(inventory_path.read_text())
        except ImportError:
            inventory = _parse_inventory_fallback(inventory_path.read_text())
            
        all_required = set(_flatten_test_names(inventory))
        spec_fns = {i["test_fn"] for i in items}
        missing_in_spec = all_required - spec_fns
        if missing_in_spec:
            if verbose:
                print(f"\n[BLOCKED] P1 Naming Authority Violation: {len(missing_in_spec)} test(s) from TEST_INVENTORY.yaml missing in TEST_SPEC.md.")
                for m in sorted(missing_in_spec)[:10]:
                    print(f"  - {m}")
                print("  Agent A may have hallucinated names. Re-run derive_test_cases.md.")
            return (1, 0.0)

    from core.utils.lang_patterns import project_language

    # Bug #130 fix (2026-06-27): the canonical harness layout puts tests at
    # `03-development/tests/`, not `<project>/tests/`. _scan_test_functions
    # reads only the directory it's pointed at, so without scanning both
    # paths D4 spec-coverage reports 0% on the canonical layout. Combine
    # both scans (dedup via set union) so projects with either layout
    # produce the correct coverage percentage.
    _lang = project_language(project)
    actual_fns = set()
    for test_dir in _get_test_directories(project):
        actual_fns |= _scan_test_functions(test_dir, _lang)

    covered = [i for i in items if i["test_fn"] in actual_fns]
    missing = [i for i in items if i["test_fn"] not in actual_fns]
    pct = len(covered) / len(items) * 100

    if verbose:
        scope = f" [{fr_id}]" if fr_id else ""
        print(f"[spec-coverage]{scope} {len(covered)}/{len(items)} ({pct:.1f}%)")
        if missing:
            print(f"  Missing ({len(missing)}):")
            for item in missing[:20]:
                print(f"    - {item['test_fn']}  (type={item['type']}, deriv={item['derivation']})")
            if len(missing) > 20:
                print(f"    ... and {len(missing) - 20} more")

    if pct < threshold:
        if verbose:
            print(f"\n[BLOCKED] spec-coverage {pct:.1f}% < {threshold}% threshold")
        return (1, pct)
    return (0, pct)


# Moved verbatim to cli/check_cmds.py (方案六). Re-exported so
# existing `from harness_cli import ...` imports keep working.
from cli.check_cmds import (  # noqa: E402, F401
    cmd_bug_hunt_targets,
    cmd_build_trace_attestation,
    cmd_check_constitution,
    cmd_check_logic,
    cmd_check_test_inventory,
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





def _parse_inventory_fallback(text: str) -> dict:
    """Minimal YAML-free parser for flat test name lists."""
    result: dict = {"fr_tests": {}, "cross_cutting": {}}
    current_section = "fr_tests"
    current_sub = "unit"
    for line in text.splitlines():
        line_s = line.strip()
        if line_s.startswith("cross_cutting"):
            current_section = "cross_cutting"
        elif line and line[0] == " " and (_m := re.match(r"^(\w+):\s*$", line_s)):
            # Indented YAML key (sub-section like unit:, integration:, security:).
            # Must check original `line` for indentation (line_s is stripped).
            # Must NOT catch list items like "      - test_name" which also have
            # leading spaces but do not match r"^(\w+):\s*$".
            current_sub = _m.group(1)
        elif line_s.startswith("- "):
            name = line_s[2:].strip()
            result.setdefault(current_section, {}).setdefault(current_sub, []).append(name)
    return result

def _verify_entry_gate(project: Path, phase: int) -> dict:
    """Automatically verify entry gate conditions before phase execution.

    CONSTITUTION.md SS2.3 defines:
    - P1: None
    - P2: Agent B¹ (P1) — git log APPROVE
    - P3: Agent B¹ (P2) — git log APPROVE
    - P4-P8: quality_manifest.json gate PASS
    """
    # SG-6: reject out-of-range phase early. Previously `phase <= 1` accepted
    # phase=0 and phase=-1, which is meaningless (only 1..9 exist).
    if phase not in VALID_PHASES:
        return {
            "passed": False,
            "gate": "InvalidPhase",
            "reason": f"phase={phase} is out of range 1..9",
        }
    if phase == 1:
        return {"passed": True, "gate": "None", "reason": "P1 has no entry gate"}

    if phase in (2, 3):
        prev = phase - 1
        state_path = project / ".methodology" / "state.json"
        import subprocess as sp

        # Primary: state.json phase_completed[N].sha + git merge-base --is-ancestor.
        # When state.json records a SHA, it IS the authority: a mismatched ancestry
        # means the recorded commit is no longer reachable from HEAD (branch reset,
        # force-push, etc.) and must hard-fail. We do NOT fall through to grep —
        # that would risk a false positive matching a commit message text alone.
        if state_path.exists():
            try:
                state = json.loads(state_path.read_text())
            except Exception as exc:  # pylint: disable=broad-exception-caught
                return {"passed": False, "gate": f"Human1 (P{prev})",
                        "reason": f"state.json unreadable: {exc}"}
            entry = state.get("phase_completed", {}).get(str(prev))
            if entry and entry.get("sha"):
                try:
                    r = sp.run(
                        ["git", "-C", str(project), "merge-base", "--is-ancestor",
                         entry["sha"], "HEAD"],
                        capture_output=True, text=True, timeout=10,
                    )
                except Exception as exc:  # pylint: disable=broad-exception-caught
                    return {"passed": False, "gate": f"Human1 (P{prev})",
                            "reason": f"git merge-base check failed: {exc}"}
                if r.returncode == 0:
                    return {"passed": True, "gate": f"Human1 (P{prev})",
                            "reason": f"Found human APPROVE commit for P{prev} "
                                      f"(sha={entry['sha'][:8]})"}
                # merge-base failed — check whether this is a shallow clone before
                # concluding branch reset. Shallow clones legitimately can't reach
                # older commits even when the ancestry is correct.
                try:
                    shallow = sp.run(
                        ["git", "-C", str(project), "rev-parse", "--is-shallow-repository"],
                        capture_output=True, text=True, timeout=5,
                    )
                    if shallow.returncode == 0 and shallow.stdout.strip() == "true":
                        deliverables = _PHASE_DELIVERABLES.get(prev, [])
                        if deliverables:
                            passed_ab, _ = _verify_agent_b_approvals_core(
                                project, prev, deliverables
                            )
                            if passed_ab:
                                return {"passed": True, "gate": f"Human1 (P{prev})",
                                        "reason": (
                                            f"Shallow clone — git ancestry unverifiable; "
                                            f"P{prev} phase-level approvals verified via "
                                            "agent_b_approvals"
                                        )}
                            return {"passed": False, "gate": f"Human1 (P{prev})",
                                    "reason": (
                                        f"Shallow clone — git ancestry unverifiable and "
                                        f"agent_b_approvals check failed for P{prev} "
                                        "deliverables (run push-checkpoint)"
                                    )}
                except Exception:  # pylint: disable=broad-exception-caught
                    pass
                return {"passed": False, "gate": f"Human1 (P{prev})",
                        "reason": f"phase_completed[{prev}].sha={entry['sha'][:8]} "
                                  "is not an ancestor of HEAD — branch may have been "
                                  "reset or force-pushed; re-run push-checkpoint."}

        # Fallback: git log --grep — only reached when state.json has no
        # phase_completed entry (legacy projects). Accept both old marker
        # (human-review) and new marker (review-complete) for backward compat.
        try:
            for commit_marker in (f"phase{prev}(review-complete)", f"phase{prev}(human-review)"):
                result = sp.run(
                    ["git", "-C", str(project), "log", "--oneline", "--grep", commit_marker, "-1"],
                    capture_output=True, text=True, timeout=10,
                )
                if result.stdout.strip():
                    return {"passed": True, "gate": f"Human1 (P{prev})",
                            "reason": f"Found human APPROVE commit for P{prev} (legacy grep)"}
            return {"passed": False, "gate": f"Human1 (P{prev})",
                    "reason": f"No human APPROVE commit found for P{prev}"}
        except Exception as e:
            return {"passed": False, "gate": f"Human1 (P{prev})",
                    "reason": f"Git log check failed: {e}"}

    manifest_path = project / ".methodology" / "quality_manifest.json"
    if not manifest_path.exists():
        return {"passed": False, "gate": f"Gate {_ENTRY_GATE_MAP.get(phase)}",
                "reason": "quality_manifest.json not found"}

    try:
        manifest = json.loads(manifest_path.read_text())
        gates = manifest.get("gate_results", {})
        prev_gate = _ENTRY_GATE_MAP.get(phase)
        if prev_gate:
            # A freshly generated manifest seeds gate2/3/4 as None (not yet run).
            # `gates.get(key, {})` returns that None, and None.get(...) raised
            # AttributeError → caught below → a return that OMITTED "gate" → the
            # caller's entry_gate['gate'] then KeyError-crashed. `or {}` makes a
            # not-yet-run gate read as a clean "not PASS".
            gate_status = gates.get(f"gate{prev_gate}") or {}
            if gate_status.get("quality_complete"):
                return {"passed": True, "gate": f"Gate {prev_gate}",
                        "reason": f"Gate {prev_gate} PASS confirmed"}
            return {"passed": False, "gate": f"Gate {prev_gate}",
                    "reason": f"Gate {prev_gate} not PASS in manifest"}
    except Exception as e:
        return {"passed": False, "gate": "Unknown", "reason": f"Manifest parse error: {e}"}

    return {"passed": False, "gate": "Unknown", "reason": f"No entry gate defined for phase {phase}"}


def cmd_run_phase(args: argparse.Namespace) -> int:
    """OTEL span wrapper for run-phase. Business logic in _cmd_run_phase_impl."""
    try:
        from core.observability import init_tracer
        _tracer = init_tracer(Path(args.project).resolve())
    except Exception:
        _tracer = None
    if _tracer is None:
        return _cmd_run_phase_impl(args)
    with _tracer.start_as_current_span("run_phase") as _span:
        _span.set_attribute("harness.phase", args.phase)
        _exit = _cmd_run_phase_impl(args)
        _span.set_attribute("harness.exit_code", _exit)
        _span.set_attribute("harness.blocked", _exit != 0)
        return _exit


def _cmd_run_phase_impl(args: argparse.Namespace) -> int:
    """Run preflight checks for a phase.

    Preflight scans the most recently completed phase's artifacts (via
    state.json.phase_completed) to ensure the project is ready to enter the
    target phase.  No postflight is executed here.

    Postflight coverage by command:
        - finalize-gate (gate >= 2, standalone): runs only postflight_artifact_links()
      + postflight_drift_check().  Constitution and BVS invariants are NOT
      checked on this path.
    - finalize-gate (gate 1): no postflight; constitution/BVS covered by the
    """
    from core.phase_hooks import PhaseHooks

    project = Path(args.project).resolve()
    hooks = PhaseHooks(str(project), phase=args.phase)

    print(f"\n{'='*60}\nrun-phase: Phase {args.phase}\n{'='*60}")

    # Entry gate check (CONSTITUTION.md SS2.3)
    entry_gate = _verify_entry_gate(project, args.phase)
    if not entry_gate["passed"]:
        print(f"\n[ENTRY GATE FAILED] {entry_gate['gate']} — {entry_gate['reason']}")
        return 10
    print(f"\n[ENTRY GATE] {entry_gate['gate']}: {entry_gate['reason']}")

    pre = hooks.preflight_all()
    if not pre["all_passed"]:
        # PR 9: most preflight failures are substantive gaps that need real
        # development work or a human. The exception is the trace gap
        # (problem_type="missing_traceability"): PhaseHooks.preflight_traceability
        # dispatches _dispatch_trace_auto_fix for one bounded attempt
        # (per-strategy allowlist inside AutoFixEngine — only
        # fix_missing_traceability is wired). Other strategies (coverage,
        # drift, artifact chain) still emit stubs and are not production-wired.
        # If we reach this point, all preflights are still failing — block.
        print(f"\nPRE-FLIGHT FAILED: {pre['details']}")
        return 1

    # Required-component check (hard dependencies — incl. code-review-graph, which
    # scores the architecture dimension). Verified at every phase entry so a missing
    # component surfaces at setup, not deep inside Gate 3/4. No graceful degradation.
    _tools_ok, _missing_components = _verify_all_gate_tools(str(project))
    if not _tools_ok:
        print(
            "\n[BLOCKED] run-phase: required components not installed:\n"
            + "\n".join(f"  - {m}" for m in _missing_components)
            + "\n  These are hard dependencies (no degradation). Install them, then re-run.\n"
            "  See SKILL.md / harness/ssi/prompts/evaluate_dimension.md for install commands."
        )
        return 1

    # Phase 3+: point to LLM-driven env check (project-aware, reads SAD.md + SRS.md).
    # preflight_all() validates governance artifacts but does not check runtime
    # dependencies (env vars, CLI tools, DB/cache connectivity, docker services)
    # that sub-agents need. Those are project-specific — Claude evaluates them
    # inline via run-env-check.
    if args.phase in _PER_FR_GATE1_PHASES:
        print(f"\n[INFO] Phase {args.phase} requires environment validation. Run:")
        print(f"  python harness_cli.py run-env-check --phase {args.phase} --project {project}")
        print("  # then evaluate inline and run finalize-env-check")
        print("  # or run run-fr-step directly — _fr_step_preflight also guards each step")

    print("\n[INFO] Preflight passed. Phase execution hooks ready.")

    print("[INFO] Next steps:")
    if args.phase in _PER_FR_GATE1_PHASES:
        manifest_path = project / ".methodology" / "quality_manifest.json"
        fr_ids = []
        if manifest_path.exists():
            try:
                fr_ids = json.loads(manifest_path.read_text()).get("fr_ids", [])
            except Exception:
                pass
        if fr_ids:
            print(f"        Per-FR Gate 1 ({len(fr_ids)} FRs): {', '.join(fr_ids)}")
            for fr_id in fr_ids:
                print(f"          python harness_cli.py run-gate --gate 1 --phase {args.phase} --project {project} --fr-id {fr_id}")
        else:
            print(f"        python harness_cli.py run-gate --gate 1 --phase {args.phase} --project {project} --fr-id FR-XX")
            print("        (quality_manifest.json not found — run 'plan-phase' first to populate FR IDs)")
    return 0

def _trace_dirty_state(project_path: Path) -> Dict[str, Any]:
    """PR 6: mtime-based trace staleness probe — <50ms, no rglob.

    Compares `attestation.json` mtime against `SAD.md` mtime and the
    newest `tests/test_fr*.py` mtime. Returns the *first* staleness
    cause found, in this order: missing attestation, SAD newer,
    tests newer. Catches the common case where a developer edited
    code or spec but forgot to re-derive `attestation.json`. False
    negatives (edits to `core/foo.py` without FR tag changes) are
    caught by the full preflight at `run-phase` time.
    """
    trace_dir = project_path / ".methodology" / "trace"
    att_path = trace_dir / "attestation.json"

    _FIX_HINT = (
        "Fix: python3 harness_cli.py build-trace-attestation --project . --write"
    )
    if not att_path.exists():
        return {
            "passed": False,
            "reason": f"attestation.json missing — {_FIX_HINT}",
            "staler": None,
            "newer": None,
        }

    try:
        att_mtime = att_path.stat().st_mtime
    except OSError as e:
        return {"passed": False, "reason": f"attestation.json stat failed: {e}",
                "staler": None, "newer": None}

    # SAD.md (canonical locations)
    for sad_candidate in ("02-architecture/SAD.md", "SAD.md"):
        sad_path = project_path / sad_candidate
        if sad_path.exists():
            try:
                if sad_path.stat().st_mtime > att_mtime:
                    return {"passed": False,
                            "reason": (
                                f"{sad_candidate} newer than attestation.json — "
                                f"{_FIX_HINT}"
                            ),
                            "staler": str(sad_path.relative_to(project_path)),
                            "newer": "attestation.json"}
            except OSError:
                pass
            break

    # Newest test file (language-aware glob; test_*.py or *.test.ts etc.)
    from core.utils.lang_patterns import iter_test_files, project_language
    tests_dir = ProjectLayout(project_path).active_test_dir
    if tests_dir.is_dir():
        try:
            candidates = list(
                iter_test_files(tests_dir, project_language(project_path))
            )
        except OSError:
            candidates = []
        if candidates:
            try:
                newest_test = max(candidates,
                                  key=lambda p: p.stat().st_mtime)
                if newest_test.stat().st_mtime > att_mtime:
                    rel = str(newest_test.relative_to(project_path))
                    return {"passed": False,
                            "reason": (
                                f"{rel} newer than attestation.json — "
                                f"{_FIX_HINT}"
                            ),
                            "staler": rel, "newer": "attestation.json"}
            except OSError:
                pass

    return {"passed": True, "reason": "trace attestation is current",
            "staler": None, "newer": None}


def _run_fast_preflight(hooks) -> dict:
    """Lightweight preflight: FSM, constitution, BVS phase order, kill-switch, trace mtime.

    Used exclusively by cmd_pre_commit_check (git commit hook path).
    Not exposed via run-phase to prevent agents from bypassing full enforcement.

    PR 6: adds `_trace_dirty_state` mtime probe (cheaper than the full
    `preflight_traceability` re-derive). Catches the common case of
    "I edited [FR-XX] but forgot to re-attest" before commit.
    """
    results = {
        "fsm": hooks.preflight_fsm_check(),
        "bvs_phase_order": hooks.preflight_bvs_phase_order(),
        "kill_switch": hooks.preflight_kill_switch(),
        "trace_dirt": _trace_dirty_state(hooks.project_path),
    }
    all_passed = all(r.get("passed", False) for r in results.values())
    return {"all_passed": all_passed, "details": results}

def cmd_pre_commit_check(args: argparse.Namespace) -> int:
    """Lightweight pre-commit hook check (FSM + kill-switch only).

    Intended exclusively for git commit hooks where speed matters.
    Skips constitution (verified at advance-phase postflight), drift,
    traceability, gap analysis, and CI readiness — those are
    enforced by run-phase / finalize-gate.

    Do NOT use this command in pipelines or as a substitute for run-phase.
    """
    from core.phase_hooks import PhaseHooks

    project = Path(args.project).resolve()
    hooks = PhaseHooks(str(project), phase=args.phase)

    print(f"\n{'='*60}\npre-commit-check: Phase {args.phase}\n{'='*60}")

    entry_gate = _verify_entry_gate(project, args.phase)
    if not entry_gate["passed"]:
        print(f"\n[ENTRY GATE FAILED] {entry_gate['gate']} — {entry_gate['reason']}")
        return 10
    print(f"\n[ENTRY GATE] {entry_gate['gate']}: {entry_gate['reason']}")

    pre = _run_fast_preflight(hooks)
    if not pre["all_passed"]:
        print(f"\nPRE-FLIGHT FAILED: {pre['details']}")
        return 1

    print("\n[INFO] Fast preflight passed (FSM + constitution + kill-switch).")
    print("[INFO] Full enforcement (drift, traceability) runs at run-phase / finalize-gate.")

    print("[INFO] Skipped: drift, traceability, gap analysis, CI readiness.")
    print("[INFO] Next steps:")
    return 0

def _sentinel_path(project: Path, gate: int, fr_id: str | None, phase: int | None = None) -> Path:
    """Return the sentinel file path that run-gate writes and finalize-gate verifies.

    v2.13 sentinel scope fix: include phase in the path so that Gate 1 written
    by Phase 1 (spec coverage) does NOT satisfy Gate 1 required by Phase 3
    (code coverage). Without phase, the same `g1_fr01.flag` path is reused
    across phases and stale Phase 1 sentinels leak into Phase 3 pre-checks.

    Path format:
      FR-specific:  g{gate}_p{phase}_{fr}.flag    e.g. g1_p3_fr01.flag
      Phase-level:  g{gate}_p{phase}_phase.flag  e.g. g2_p3_phase.flag (fr_id=None)
    """
    key = (fr_id or "phase").replace("-", "").lower()
    d = project / ".sessi-work" / "sentinels"
    if phase is None:
        warnings.warn(
            f"_sentinel_path(gate={gate}, fr_id={fr_id!r}) called without phase= "
            "(Bug #121 regression risk): cross-phase sentinel collision possible. "
            "Pass phase= explicitly.",
            DeprecationWarning,
            stacklevel=2,
        )
        return d / f"g{gate}_{key}.flag"
    return d / f"g{gate}_p{phase}_{key}.flag"


def _finalize_sentinel_path(project: Path, gate: int, fr_id: str | None, phase: int | None = None) -> Path:
    """Return the sentinel that finalize-gate writes. advance-phase verifies it.

    See _sentinel_path for the v2.13 phase-scoping rationale.
    """
    key = (fr_id or "phase").replace("-", "").lower()
    d = project / ".sessi-work" / "sentinels"

    if phase is not None:
        return d / f"g{gate}_p{phase}_{key}.finalized"

    # Legacy fallback (no phase provided): prefer the new-style .finalized;
    # fall back to legacy .flag with hyphen-stripped fr id (Bug #120 compat).
    std_path = d / f"g{gate}_{key}.finalized"
    if fr_id:
        legacy_path = d / f"g{gate}_{fr_id}.flag"
        if not std_path.exists() and legacy_path.exists():
            return legacy_path

    return std_path


def _write_finalize_sentinels_for_tests(  # type: ignore[reportUnusedFunction]
    project: Path,
    fr_ids: list[str] | None = None,
    phase: int | None = None,
):
    """Create the finalize sentinels that advance-phase checks.

    Tests that exercise _advance_prechecks must call this BEFORE invoking
    the function — otherwise the finalize-gate sentinel check will block.

    Creates: Gate 1 per-FR sentinel for each fr_id (auto-detected from
    quality_manifest.json if not provided), plus the phase-exit
    gate sentinel for every known exit gate.

    v2.13: `phase` is the caller's current phase; if provided, sentinels
    are written under the per-phase path (g1_p{phase}_{fr}.finalized).
    If None (legacy test path), uses the non-phase-scoped path so old
    tests that don't know about phases still work.
    """
    frs = list(fr_ids) if fr_ids else []
    if not frs:
        # Auto-detect FR IDs from quality_manifest.json so tests that create
        # FRs via the manifest don't need to pass them explicitly.
        _mp = project / ".methodology" / "quality_manifest.json"
        if _mp.exists():
            try:
                _mf = json.loads(_mp.read_text(encoding="utf-8"))
                frs = list(_mf.get("fr_ids", []))
            except (json.JSONDecodeError, OSError):
                pass
    for _frid in frs:
        _sf = _finalize_sentinel_path(project, 1, _frid, phase=phase)
        _sf.parent.mkdir(parents=True, exist_ok=True)
        _sf.write_text("test-sentinel\n", encoding="utf-8")
    # Also write phase-level exit gate sentinels for phases 3,4,6 so any test
    # that advances past these phases has them available.
    for _phase, _gate in sorted(_PHASE_EXIT_GATES.items()):
        _sf = _finalize_sentinel_path(project, _gate, None, phase=_phase)
        _sf.parent.mkdir(parents=True, exist_ok=True)
        _sf.write_text("test-sentinel\n", encoding="utf-8")

def _check_gate_score_variance(project: Path, phase: int) -> int:
    """Check that gate scores within a phase vary across FRs.

    Returns 0 on pass, 1 on fabrication detected, or 0 on skip
    (not enough files, missing yaml, etc.).
    """
    try:
        import glob as _glob
        import yaml as _yaml
    except ImportError:
        print("[advance-phase] ⚠ yaml unavailable — skipping gate score variance check")
        return 0

    try:
        _decision_dir = project / ".methodology" / "decision_logs"
        _score_files = _glob.glob(
            str(_decision_dir / "**" / f"GATE_{phase}_*.yaml"),
            recursive=True,
        )
        _scores: list[float] = []
        for _sf in _score_files:
            try:
                _d = _yaml.safe_load(open(_sf, encoding="utf-8"))
                # Skip aggregate entries (Gate2/Gate4 have fr_id=null); only check per-FR scores.
                if (_d or {}).get("ctx", {}).get("fr_id") is None:
                    continue
                _s = (_d or {}).get("scores", {}).get("gate_score")
                if _s is not None:
                    _scores.append(float(_s))
            except Exception:
                pass

        # SG-1: stricter fabrication detection. The previous check fired only
        # when ALL scores were identical (one decimal of variation defeated it,
        # e.g. 85.0 + 85.0 + 85.1). Now we compute stddev — if N≥3 scores have
        # stddev < 0.5, they're suspiciously uniform.
        # Saturated exception: when every FR is at-or-near the ceiling
        # (mean >= 99.5), per-FR variance is bounded by the distance to the
        # ceiling, so low stddev is a legitimate outcome of a clean codebase
        # rather than fabrication. Same threshold as the gate-3
        # dimension-variance `_saturated` exemption below.
        if len(_scores) >= 3:
            import statistics as _stats
            _stdev = _stats.pstdev(_scores)
            _mean = _stats.fmean(_scores)
            _saturated = _mean >= 99.5
            if _stdev < 0.5 and not _saturated:
                print(
                    f"\n[BLOCKED] Gate score variance check failed for Phase {phase}:\n"
                    f"  {len(_scores)} per-FR scores cluster around {_mean:.2f} "
                    f"(stddev={_stdev:.3f} < 0.5).\n"
                    f"  Scores: {_scores}\n"
                    f"  This indicates scores were copied/fabricated rather than\n"
                    f"  evaluated per FR. Re-run run-gate + evaluate dimensions\n"
                    f"  inline + finalize-gate for each FR with genuine evidence."
                )
                return 1
        if _scores:
            print(f"[advance-phase] Gate score variance OK "
                  f"({len(_scores)} per-FR scores: {sorted(set(_scores))})")
        return 0
    except Exception as _exc:
        print(f"[advance-phase] ⚠ Gate score variance check error ({_exc}) — skipping")
        return 0

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


def _check_sab_module_alignment(project: str, gate: int) -> Optional[int]:
    """Gate 1 Architecture Amendment Protocol: block if unregistered modules found.

    Returns 1 when gate==1 and at least one .py file in src/ is absent from SAB.json.
    Returns None when the check is skipped (gate != 1, SAB.json missing, no src dir)
    or when all modules are registered.

    SAB ``modules`` entries may be expressed in either dotted
    (``taskq.cli``) or path (``03-development/src/taskq/cli.py``) form;
    both are normalised to dotted names before comparison so the check
    agrees with `drift_detector.sab_module_to_path_variants`.
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
    _tools_ok, _missing_tools = _verify_gate_tools(args.gate, project)
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
    _amend_result = _check_sab_module_alignment(project, args.gate)
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

def _sentinel_env_path(project: Path) -> Path:
    """Return the sentinel file path for env-check."""
    d = project / ".sessi-work" / "sentinels"
    return d / "env_check.flag"


def _verify_env_check_claims(project: Path) -> "list[str]":
    """A2: independently re-verify the cli_tools / env_vars the env-check agent
    claimed present. Returns fabrication findings (empty = all claims hold up).

    Only claims of `present: true` are checked — tools/vars the agent reported as
    absent/optional are not forced. infra_services (DB/docker) stay agent-reported
    (the framework cannot reliably probe them here).
    """
    result_path = project / ".sessi-work" / "env_check_result.json"
    if not result_path.exists():
        return []
    try:
        data = json.loads(result_path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return []
    findings: list[str] = []
    # Tools whose fast checks (PATH/venv-bin/semantic-name) all missed are
    # deferred here instead of probed inline — see the batched concurrent
    # probe below.
    _pending_probes: list[tuple[str, str, dict, list[str]]] = []
    for t in data.get("cli_tools", {}).get("required", []):
        if isinstance(t, dict) and t.get("present") and t.get("name"):
            raw_name = str(t["name"])
            # Strip parenthetical annotations added by sub-agents (e.g. "python3 (.venv)")
            # and take only the first token so "python3 -m pip" → "python3".
            _stripped = re.sub(r"\s*\(.*?\)\s*$", "", raw_name).strip()
            name = _stripped.split()[0] if _stripped else raw_name
            if not name:
                continue
            # v2.13 Bug #123 fix: skip framework-internal subcommands.
            # Names ending in `.py` (e.g. "harness_cli.py finalize-env-check") are
            # subcommands of framework scripts, not standalone PATH tools — they
            # never appear in `shutil.which()` results. Without this skip, every
            # env-check that reports a framework subcommand FAILs with a false
            # "fabricated claim" finding, blocking P3/P5/P7 entry.
            if name.lower().endswith(".py"):
                continue
            _found = shutil.which(name) is not None
            _bindir = "Scripts" if os.name == "nt" else "bin"
            if not _found:
                # PATH miss: also check venv-local bin/ and Python import as fallbacks.
                # Covers tools installed only inside .venv and Python packages (e.g.
                # pydantic) that are not CLI binaries but are valid "present" claims.
                #
                # Bug #129 root-cause fix (2026-07-02): probe project-local venvs
                # (.venv/venv) directly, not only $VIRTUAL_ENV. Orchestrated runs
                # invoke `.venv/bin/python harness_cli.py ...` without activating,
                # so VIRTUAL_ENV is never exported and the old probe was dead code
                # there — honest claims about venv-only tools were flagged as
                # fabricated. Also normalize python-version-semantic names
                # ("python311" → "python3.11"): sub-agents name the interpreter
                # after the SAD version string, but the binary is `python3.11`.
                # A wrong-version claim (e.g. python312 with only 3.11 installed)
                # still fails every probe and stays flagged.
                _cands = [name]
                _pv = re.fullmatch(r"python[-_.]?(\d)[-_.]?(\d+)", name.lower())
                if _pv:
                    _cands.append(f"python{_pv.group(1)}.{_pv.group(2)}")
                _venv_dirs = [os.environ.get("VIRTUAL_ENV", "")]
                _venv_dirs += [str(project / d) for d in (".venv", "venv")]
                for _cn in _cands:
                    if _cn != name and shutil.which(_cn):
                        _found = True
                    for _vd in _venv_dirs:
                        if _vd and os.path.exists(os.path.join(_vd, _bindir, _cn)):
                            _found = True
                            break
                    if _found:
                        break
                if not _found:
                    # Bug #128 root-cause fix (2026-06-27): semantic venv-Python names
                    # like "venv-python", "python-venv", "venv-python3" are LOGICAL
                    # names meaning "the Python interpreter inside the project's
                    # virtualenv", not literal PATH binaries. The agent's claim is
                    # honest when (a) the running interpreter is itself a venv
                    # interpreter (`sys.prefix != sys.base_prefix`), or (b) a
                    # project-local venv (.venv/venv) exists and contains a Python
                    # binary. Without this fallback, every project using venv-
                    # semantic naming gets a false "fabricated claim" finding and
                    # P3/P5/P7 entry is wrongly blocked. Generalization: any name
                    # whose lowercased tokens contain both "venv" and "python"
                    # is treated as a venv-Python semantic name.
                    _name_lc = name.lower()
                    if "venv" in _name_lc and "python" in _name_lc:
                        try:
                            if sys.prefix != getattr(sys, "base_prefix", sys.prefix):
                                _found = True
                            else:
                                exe_name = "python.exe" if os.name == "nt" else "python3"
                                bindir = "Scripts" if os.name == "nt" else "bin"
                                for _venv_dir in (".venv", "venv"):
                                    _cand = project / _venv_dir / bindir / exe_name
                                    if _cand.exists():
                                        _found = True
                                        break
                        except Exception:
                            pass
                if not _found:
                    # Python package fallback: "import <name>" via the current interpreter.
                    # src-layout projects (e.g. 03-development/src/taskq) are importable
                    # only with the project's src root on PYTHONPATH — the deliverable
                    # package is a valid "present" claim even before pip install.
                    _pkg = name.replace("-", "_")
                    _import_env = {**os.environ}
                    try:
                        _src_dir = ProjectLayout(project).active_src_dir
                        if _src_dir.is_dir():
                            _import_env["PYTHONPATH"] = os.pathsep.join(
                                p for p in (str(_src_dir), _import_env.get("PYTHONPATH", "")) if p
                            )
                    except Exception:
                        pass
                    # Bug #129: try the project venv's python too — whether a
                    # plugin-only package (e.g. pytest-cov) verifies must not
                    # depend on which interpreter happens to run harness_cli.
                    _interps = [sys.executable]
                    _py_exe = "python.exe" if os.name == "nt" else "python"
                    for _vd in (".venv", "venv"):
                        _vp = project / _vd / _bindir / _py_exe
                        if _vp.exists():
                            _interps.append(str(_vp))
                    # Defer the actual subprocess spawn: several unresolved
                    # tools each sequentially spawning up to len(_interps)
                    # `import <pkg>` probes (5s timeout each) can serialize
                    # to tens of seconds on this blocking CLI path. Batch
                    # all deferred probes below and run them concurrently.
                    _pending_probes.append((raw_name, _pkg, _import_env, _interps))
                    continue
            if not _found:
                findings.append(
                    f"cli_tool '{raw_name}': claimed present, but not found on PATH, "
                    f"in $VIRTUAL_ENV/bin/, or via Python import"
                )

    if _pending_probes:
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
                except Exception:
                    pass
            return _raw_name, False

        with concurrent.futures.ThreadPoolExecutor(
            max_workers=min(8, len(_pending_probes))
        ) as _ex:
            for _raw_name, _found_import in _ex.map(_probe_import, _pending_probes):
                if not _found_import:
                    findings.append(
                        f"cli_tool '{_raw_name}': claimed present, but not found on PATH, "
                        f"in $VIRTUAL_ENV/bin/, or via Python import"
                    )

    for v in data.get("env_vars", {}).get("required", []):
        if isinstance(v, dict) and v.get("present") and v.get("name"):
            name = str(v["name"])
            if name not in os.environ:
                findings.append(f"env_var '{name}': claimed present, but not set")
    return findings


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
_MIN_REVIEW_REASON_CHARS = 40  # minimum length for an Agent B APPROVE reason to count as substantive


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
    result_candidates = [
        project / ".sessi-work" / "gate4_result.json",
        project / ".methodology" / "gate4_result.json",
        project / "gate4_result.json",
    ]
    g4: dict = {}
    for candidate in result_candidates:
        if candidate.exists():
            try:
                g4 = json.loads(candidate.read_text(encoding="utf-8"))
                break
            except Exception as _e:
                print(f"[Gate 4] ⚠ Could not parse {candidate}: {_e} — skipping extended checks", file=sys.stderr)

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
                    # DA challenge complete + artifact-backed — collect score-threshold waivers.
                    # A waiver lets a CRG-ONLY dim (e.g. architecture) pass below threshold when the
                    # DA challenge concluded the design is intentional (Orchestrator/hub-and-spoke).
                    # Requires devil_advocate.<dim>=true, da_waiver.<dim>=true, AND DA evidence.
                    _da_waiver_raw: dict = g4.get("da_waiver", {})
                    for _dim, _waived in _da_waiver_raw.items():
                        if not (_waived and devil_advocate.get(_dim, False)):
                            continue
                        _w_problem = _validate_da_evidence(_dim, g4)
                        if _w_problem:
                            print(
                                f"\n[BLOCKED] Gate 4 (A3): da_waiver for '{_dim}' requires DA evidence — {_w_problem}",
                                file=sys.stderr,
                            )
                            blocked = True
                            continue
                        # Only apply the waiver when the dimension is actually below threshold.
                        # If tool_score >= threshold the dimension already passes; accepting
                        # the waiver would still set da_waiver_needs_human_review = True in
                        # quality_manifest.json, which is a false-positive review flag.
                        _bd = g4.get("breakdown", {}).get(_dim, {})
                        _tool_score = float(_bd.get("tool_score", 0.0))
                        _threshold = float(_bd.get("threshold", float("inf")))
                        if _tool_score >= _threshold:
                            print(
                                f"[Gate 4] A3: da_waiver for '{_dim}' skipped — "
                                f"tool_score={_tool_score:.1f} ≥ threshold={_threshold:.1f} "
                                "(waiver not needed; dimension already passes).",
                                file=sys.stderr,
                            )
                            continue
                        da_waivers.add(_dim)
                        print(
                            f"[Gate 4] A3: DA waiver active for '{_dim}' "
                            "(score threshold bypassed — artifact-backed DA challenge confirmed intentional design).",
                            file=sys.stderr,
                        )

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
    _tools_ok, _missing_tools = _verify_gate_tools(args.gate, project)
    if not _tools_ok:
        print(
            f"\n[BLOCKED] Required tools not installed for Gate {args.gate}:\n"
            + "".join(f"  ✗ {m}\n" for m in _missing_tools)
            + "\n  Install the missing tools and re-run finalize-gate.\n"
            "  Tool scores must come from actual tool execution, not estimation."
        )
        return 8

    # S0b: Commit interval enforcement (P1 — prevent batch fabrication)
    _interval_ok, _interval_msg = _check_commit_intervals(
        project, args.phase, args.gate
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
        _update_claude_md(Path(args.project).resolve())  # gate pass → refresh CLAUDE.md

        # P1: Record successful finalization timestamp HERE (after all checks pass),
        # not inside _check_commit_intervals.  Failed attempts must not leave a trace
        # so that retries don't accumulate phantom entries.
        _record_gate_timestamp(Path(args.project).resolve(), args.phase, args.gate, fr_id)

        # ── Auto-generate machine STAGE_PASS.md ──────────────────────
        _generate_stage_pass(project_path, args.gate, args.phase)

        # ── Auto-generate quality deliverables for Gate 4 ─────────────
        if args.gate == 4:
            try:
                from scripts.generate_quality_report import generate_quality_report
                generate_quality_report(str(Path(args.project).resolve()))
            except Exception as _qre:
                print(f"  [WARN] QUALITY_REPORT.md generation skipped: {_qre}")

            try:
                from scripts.generate_release_notes import generate_release_notes
                generate_release_notes(str(Path(args.project).resolve()))
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
            git.commit_fr_gate1(fr_id or "unknown", result.score, args.phase)
        else:
            git.commit_and_push_gate(args.gate, args.phase, result.score)
            # Post-push self-check: warn loudly on dirty residue. Push itself
            # succeeded — the dirt is post-commit residue. Don't fail-fast.
            _dirty = _post_push_self_check(Path(args.project).resolve())
            if _dirty:
                print(
                    f"  [WARN] post-push dirty tree ({len(_dirty)} path(s)):\n"
                    + "\n".join(f"    • {p}" for p in _dirty[:10])
                    + (f"\n    ... and {len(_dirty) - 10} more" if len(_dirty) > 10 else "")
                )
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
        return 1

# ---------------------------------------------------------------------------
# generate-next-plan
# ---------------------------------------------------------------------------

def cmd_generate_next_plan(args: argparse.Namespace) -> int:
    """
    Recovery / position reporter.

    Reports WHERE the main agent currently is in the phase plan so it can
    resume execution without re-reading the full SKILL.md.

    Output (always):
      Phase      : N (Name)
      Plan file  : path/to/phase{N}_plan.md   ← open and follow this
      Last ckpt  : CHECKPOINT-K (Gate X / FR-YY) PASS  (or "none")
      Next ckpt  : CHECKPOINT-K+1 (Gate X / ...)
      Action     : exact single command to run next

    If no plan file exists for the current phase, instructs the agent to
    generate it first.  If all checkpoints in the current phase are done,
    reports the next phase to start.
    """
    project = Path(getattr(args, "project", ".")).resolve()
    phase_hint = getattr(args, "phase", None)
    manifest_path = project / ".methodology" / "quality_manifest.json"

    W = 62
    print(f"\n{'='*W}")
    print("POSITION REPORT  (generate-next-plan)")
    print(f"{'='*W}")

    # ── Read state.json ──────────────────────────────────────────────────────
    state_path = project / ".methodology" / "state.json"
    current_phase: int = phase_hint or 3
    last_gate: int | None = None
    last_fr: str | None = None
    if state_path.exists():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
            current_phase = phase_hint or int(state.get("current_phase", 3))
            last_gate = state.get("last_gate")
            last_fr = state.get("last_fr")
        except Exception:  # pylint: disable=broad-exception-caught
            pass

    print(f"\nPhase      : {current_phase} ({_topology_phase_name(current_phase, default='?')})")

    # ── Resolve plan file ────────────────────────────────────────────────────
    plan_file = project / ".methodology" / f"phase{current_phase}_plan.md"
    if plan_file.exists():
        print(f"Plan file  : {plan_file}")
        print("             → Open this file and follow from the next checkpoint")
    else:
        print(f"Plan file  : *** NOT FOUND ***  ({plan_file})")
        print("\n[ACTION] Generate the phase plan first:")
        print(f"  python harness_cli.py plan-phase --phase {current_phase} "
              f"--project {project}")
        print(f"  python scripts/generate_full_plan.py --phase {current_phase} "
              f"--repo {project} --output {plan_file}")
        print(f"\n{'='*W}")
        return 0

    # ── Read manifest ────────────────────────────────────────────────────────
    if not manifest_path.exists():
        print("\n[WARN] quality_manifest.json not found — cannot determine checkpoints.")
        print("  Run: python harness_cli.py manifest --fr-ids FR-01 ... --sad SAD.md")
        print(f"\n{'='*W}")
        return 0

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    fr_ids: list[str] = manifest.get("fr_ids", [])
    gate_results: dict = manifest.get("gate_results", {})
    gate1_results: dict = gate_results.get("gate1", {})

    # ── Build ordered checkpoint list for current phase ──────────────────────
    # Each entry: (label, is_complete_fn)
    checkpoints: list[tuple[str, bool]] = []

    if current_phase in _PER_FR_GATE1_PHASES:
        for fr_id in fr_ids:
            # Prefer state.json's last_gate/last_fr for completion signal;
            # fall back to manifest gate_results scan.
            if last_gate is not None:
                # A per-FR gate is complete if we've passed it (last_gate > 1)
                # or if it matches last_gate=1, last_fr
                done = (last_gate > 1
                        or (last_gate == 1 and last_fr is not None
                            and last_fr in fr_ids
                            and fr_ids.index(fr_id) <= fr_ids.index(last_fr)))
            else:
                fr_res = gate1_results.get(fr_id) if isinstance(gate1_results, dict) else None
                done = bool(fr_res and fr_res.get("quality_complete"))
            checkpoints.append((f"Gate 1 / {fr_id}", done))

    if current_phase in _PHASE_EXIT_GATES:
        gate_num = _PHASE_EXIT_GATES[current_phase]
        if last_gate is not None:
            done = last_gate >= gate_num
        else:
            g_res = gate_results.get(f"gate{gate_num}")
            done = bool(g_res and g_res.get("quality_complete"))
        checkpoints.append((f"Gate {gate_num} — Phase {current_phase} Exit", done))
    elif current_phase == 6:
        if last_gate is not None:
            done = last_gate >= 4
        else:
            g_res = gate_results.get("gate4")
            done = bool(g_res and g_res.get("quality_complete"))
        checkpoints.append(("Gate 4 — Full Project", done))

    # ── Find last complete and first incomplete ──────────────────────────────
    last_done_idx = -1
    for i, (_, done) in enumerate(checkpoints):
        if done:
            last_done_idx = i

    next_idx = last_done_idx + 1

    if last_done_idx < 0:
        print("Last ckpt  : (none — starting from the beginning)")
    else:
        label, _ = checkpoints[last_done_idx]
        print(f"Last ckpt  : CHECKPOINT-{last_done_idx + 1} ({label}) ✓ PASS")

    if next_idx >= len(checkpoints):
        # All done in current phase
        next_phase = current_phase + 1
        print("Next ckpt  : (all checkpoints complete in this phase)")
        if current_phase >= 1:
            print(f"\n  Phase Truth ≥ 90% (HR-11): verify before advancing to Phase {next_phase}:")
            print("    (Exits 0 on PASS, 11 if Phase Truth < 90%)")
        print(f"\n✓ Phase {current_phase} complete — start Phase {next_phase}:")
        print(f"  python harness_cli.py run-phase --phase {next_phase} "
              f"--project {project}")
        print(f"  python scripts/generate_full_plan.py --phase {next_phase} "
              f"--repo {project} --output "
              f"{project}/.methodology/phase{next_phase}_plan.md")
        print(f"\n{'='*W}")
        return 0

    next_label, _ = checkpoints[next_idx]
    print(f"Next ckpt  : CHECKPOINT-{next_idx + 1} ({next_label})")

    # ── Emit single action command ───────────────────────────────────────────
    print(f"\n[ACTION] Open plan and execute from CHECKPOINT-{next_idx + 1}:")
    print(f"  Plan: {plan_file}")

    # Also emit the run-gate command as a quick-start shortcut
    if "Gate 1 /" in next_label:
        fr_id_next = next_label.split("Gate 1 / ")[-1].strip()
        print(f"\n  Quick-start Gate 1 for {fr_id_next}:")
        print(f"  python harness_cli.py run-gate --gate 1 --phase {current_phase} "
              f"--project {project} --fr-id {fr_id_next}")
    elif "Gate" in next_label:
        m = re.search(r"Gate (\d+)", next_label)
        if m:
            g = m.group(1)
            print(f"\n  Quick-start Gate {g}:")
            print(f"  python harness_cli.py run-gate --gate {g} "
                  f"--phase {current_phase} --project {project}")

    print(f"\n{'='*W}")
    return 0

# ---------------------------------------------------------------------------
# manifest
# ---------------------------------------------------------------------------

def _generate_sab_json(project: Path) -> bool:
    """Run scripts/generate_sab.py to produce .methodology/SAB.json. Returns True on success."""
    import subprocess  # nosec B404
    sab_script = Path(__file__).parent / "scripts" / "generate_sab.py"
    if not sab_script.exists():
        print("  [SAB] ERROR: generate_sab.py not found — pipeline blocked")
        return False
    try:
        result = subprocess.run(  # nosec B603 B607
            ["python3", str(sab_script), "--project", str(project)],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            sab_path = project / ".methodology" / "SAB.json"
            print(f"  [SAB] SAB.json written → {sab_path}")
            return True
        else:
            print(f"  [SAB] ERROR: generate_sab.py failed — pipeline blocked: {result.stderr[:200]}")
            return False
    except Exception as exc:
        print(f"  [SAB] ERROR: SAB generation error — pipeline blocked: {exc}")
        return False

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
    cmd_ci_ack,
    cmd_push_checkpoint,
    cmd_push_milestone,
)
# ---------------------------------------------------------------------------
# ci-ack  (acknowledge a CI-readiness advisory component to silence its warning)
# ---------------------------------------------------------------------------

def _extract_review_json(text: str, _depth: int = 0) -> "dict | None":
    """Extract the first JSON object containing 'review_status' from free text.

    Scans from every '{' position so it works whether the agent output is plain
    JSON, JSON inside a markdown code fence, or JSON embedded in prose.

    Also unwraps the Claude CLI JSON envelope (``{"result": "...", "session_id": "..."}``)
    when the agent output was captured as the raw CLI response rather than the
    unwrapped ``result`` field.  Recursion is bounded at 2 levels (Claude CLI
    envelope is always exactly 1 level deep).
    Returns None if no valid review JSON is found.
    """
    if not text or not isinstance(text, str):
        return None

    decoder = json.JSONDecoder()
    for i, ch in enumerate(text):
        if ch != '{':
            continue
        try:
            obj, _ = decoder.raw_decode(text, i)
            if not isinstance(obj, dict):
                continue
            if "review_status" in obj:
                return obj
            # Unwrap Claude CLI envelope: {"result": "...", "session_id": "..."}
            if "result" in obj and isinstance(obj["result"], str) and _depth < 2:
                inner = _extract_review_json(obj["result"], _depth + 1)
                if inner is not None:
                    return inner
        except (json.JSONDecodeError, ValueError):
            pass
    return None

def _extract_agent_output_json(text: str) -> "dict | None":
    """Extract Agent A's structured output JSON from free text.

    Looks for a dict that has 'status' plus at least one of the Agent A
    output fields (files, confidence, citations, summary).  This is distinct
    from Agent B's review JSON which carries 'review_status'.
    Returns None if no matching JSON block is found.
    """
    _AGENT_A_FIELDS = frozenset({"files", "confidence", "citations", "summary"})
    decoder = json.JSONDecoder()
    for i, ch in enumerate(text):
        if ch != '{':
            continue
        try:
            obj, _ = decoder.raw_decode(text, i)
            if (
                isinstance(obj, dict)
                and "status" in obj
                and "review_status" not in obj  # not an Agent B block
                and _AGENT_A_FIELDS & obj.keys()
            ):
                return obj
        except json.JSONDecodeError:
            pass
    return None

def _resolve_deliverable_ids(
    project: Path, phase: int, fr_ids: "list[str]"
) -> "list[str]":
    """Return the deliverable IDs to check for Agent B approvals.

    P1/P2: always returns the phase-level deliverables from _PHASE_DELIVERABLES
           (per-FR approval is only meaningful from P3 onwards).
    P3+:   fr_ids from caller → quality_manifest.json → empty list.
    """
    if phase in _PHASE_DELIVERABLES:
        return _PHASE_DELIVERABLES[phase]
    if fr_ids:
        return fr_ids
    manifest_path = project / ".methodology" / "quality_manifest.json"
    if manifest_path.exists():
        try:
            return json.loads(
                manifest_path.read_text(encoding="utf-8")
            ).get("fr_ids", [])
        except Exception:  # pylint: disable=broad-exception-caught
            pass
    return []

def _verify_agent_b_approvals_core(
    project: Path, phase: int, deliverable_ids: "list[str]"
) -> "tuple[bool, str]":
    """Verify agent_b_approvals/<id>.json files exist and carry APPROVE status.

    Returns (passed, report) where report is a human-readable summary.
    Uses phase-appropriate required_embedded_docs (P1 only needs SRS.md;
    P2 needs SRS.md + SAD.md).
    """
    required_docs = _REQUIRED_EMBEDDED_DOCS.get(phase, ["SRS.md", "SAD.md"])
    approvals_dir = project / ".methodology" / "agent_b_approvals"
    lines: list[str] = [
        f"[verify-agent-b] Phase {phase} — checking {len(deliverable_ids)} deliverables",
        f"  Approvals dir : {approvals_dir}",
    ]
    missing: list[str] = []
    rejected: list[str] = []
    errors: list[str] = []

    for did in deliverable_ids:
        approval_file = approvals_dir / f"{did}.json"
        if not approval_file.exists():
            missing.append(did)
            continue
        try:
            data = json.loads(approval_file.read_text(encoding="utf-8"))
        except Exception as exc:  # pylint: disable=broad-exception-caught
            errors.append(f"{did}: JSON parse error — {exc}")
            continue
        status = data.get("review_status", "")
        if status != "APPROVE":
            rejected.append(f"{did}: review_status={status!r} (expected APPROVE)")
            continue
        # A1 structure guard: an APPROVE must carry a substantive review, not an empty
        # rubber-stamp. This cannot verify Agent B authenticity (a structural limit of a
        # document-phase review) but it blocks the trivially-faked empty APPROVE.
        _reason = str(data.get("reason", "")).strip()
        _citations = data.get("citations", [])
        if len(_reason) < _MIN_REVIEW_REASON_CHARS:
            errors.append(
                f"{did}: APPROVE with empty/too-short reason "
                f"(need ≥{_MIN_REVIEW_REASON_CHARS} chars of review rationale)"
            )
            continue
        if not isinstance(_citations, list) or not _citations:
            errors.append(
                f"{did}: APPROVE without citations[] — Agent B must cite what it reviewed."
            )
            continue
        embedded = data.get("docs_embedded", [])
        # Bug v26 fix (2026-06-29): required_docs may be basenames ("SRS.md") while
        # B agent writes full repo-relative paths ("01-requirements/SRS.md"). Normalize
        # both sides to a comparable form (basename + full path) before the membership
        # check so neither authoring convention triggers a false-positive missing-docs
        # failure. Previously the strict `d not in embedded` rejected "SRS.md" because
        # the list contained "01-requirements/SRS.md" — a contract mismatch, not a
        # real coverage gap.
        def _norm(s: str) -> set[str]:
            p = Path(s)
            return {s, p.name, str(p).lstrip("./")}
        embedded_norm: set[str] = set()
        for e in embedded:
            embedded_norm |= _norm(str(e))
        missing_docs = [d for d in required_docs if not (_norm(d) & embedded_norm)]
        if missing_docs:
            errors.append(
                f"{did}: docs_embedded missing {missing_docs} — "
                "Agent B prompt must embed the required source documents."
            )

    passed = not (missing or rejected or errors)
    if passed:
        lines.append(f"  ✓ All {len(deliverable_ids)} Agent B approvals verified.")
    else:
        lines.append("\n[BLOCKED] Agent B approval verification failed:")
        if missing:
            lines.append(f"  Missing approval files ({len(missing)}):")
            for d in missing:
                lines.append(f"    • {approvals_dir / d}.json")
        if rejected:
            lines.append(f"  Non-APPROVE statuses ({len(rejected)}):")
            for r in rejected:
                lines.append(f"    • {r}")
        if errors:
            lines.append(f"  Schema/content errors ({len(errors)}):")
            for e in errors:
                lines.append(f"    • {e}")
        lines.append(
            "\n  Fix: ensure Agent B writes approval JSON for each deliverable:\n"
            '    {"fr": "<id>", "review_status": "APPROVE", '
            '"docs_embedded": ["SRS.md"], "confidence": 0.9}'
        )
    return passed, "\n".join(lines)



def _validate_p8_completion(project: Path) -> list[str]:
    """Pre-flight checks required before push-milestone --type p8 is allowed."""
    errors: list[str] = []

    # 1. .methodology-archive/ — auto-create if absent
    archive_dir = project / ".methodology-archive"
    if not archive_dir.exists():
        archive_dir.mkdir(parents=True, exist_ok=True)

    # 2. (removed) HANDOVER.md used to be forbidden from referencing Phase 9
    # back when P8 was the terminal phase. Phase 9 (Maintenance) is now a
    # legal steady state entered via `advance-phase --completed 8`, so a
    # P8-exit HANDOVER legitimately points at Phase 9 next steps.

    # 3. Finding #24: archive must contain .methodology/ contents (not .sessi-work/).
    # Old P8 plan had a typo: 'cp -r .sessi-work/ .methodology-archive/' which copied
    # the gitignored runtime scratch dir instead of the methodology artifacts the
    # archive name semantically implies. Validator now checks the archive actually
    # has methodology content (e.g. a phase*_plan.md or quality_manifest.json).
    # Also catches the inverse case: archive contains a `sessi-work/` subdir
    # (i.e. the agent ran the buggy cp command verbatim and produced
    # .methodology-archive/sessi-work/).
    archive_sessi = archive_dir / "sessi-work"
    if archive_sessi.exists():
        errors.append(
            ".methodology-archive/sessi-work/ exists — this is the Finding #24 "
            "typo outcome. The P8 archive must contain .methodology/ contents, "
            "not the gitignored runtime scratch dir. Re-run: "
            "`rm -rf .methodology-archive && mkdir -p .methodology-archive && "
            "cp -r .methodology/ .methodology-archive/`."
        )

    # Positive content check: `cp -r .methodology/ .methodology-archive/` (trailing
    # slash on source, destination already created by mkdir) copies the CONTENTS of
    # .methodology/ directly into .methodology-archive/ — phase*_plan.md and
    # quality_manifest.json land at archive_dir/*.  There is no "methodology/"
    # subdirectory.  Catch both an empty archive (mkdir ran but cp didn't) and any
    # other wrong-source copy, but skip when sessi-work was already reported above.
    if not archive_sessi.exists():
        _has_methodology_content = any(archive_dir.glob("phase*_plan.md")) or (
            archive_dir / "quality_manifest.json"
        ).exists()
        if not _has_methodology_content:
            errors.append(
                ".methodology-archive/ contains no methodology artifacts "
                "(phase*_plan.md / quality_manifest.json). "
                "Re-run: `rm -rf .methodology-archive && mkdir -p .methodology-archive"
                " && cp -r .methodology/ .methodology-archive/` "
                "(do NOT copy .sessi-work/ — that is the Finding #24 typo)."
            )

    return errors


def _validate_p3_post_gate2_precondition(
    project: Path, fr_ids: list[str]
) -> list[str]:
    """v2.9.1 B.2: Pre-flight checks for push-milestone --type p3-post-gate2.

    PUSH ⑤ is the formal P3-exit milestone. It must not be allowed to land
    on a label-only claim (the e2e orchestrator previously called its commit
    "P3-exit" without verifying any gate; this milestone type makes the
    check structural, not narrative).

    Required (errors block the push):
      1. .methodology/gate2_result.json exists, gate == 2, composite ≥ 75
      2. every FR in `fr_ids` has a per-FR Gate 1 sentinel in
         .sessi-work/sentinels/ (matches what `finalize-gate --gate 1 --fr-id FR-XX`
         would write). This is the per-FR 95% bar that `advance-phase` also
         enforces — the milestone cannot be a softer gate than advance-phase.
    """
    errors: list[str] = []

    # 1. Gate 2 PASS precondition
    gate2_path = project / ".methodology" / "gate2_result.json"
    if not gate2_path.exists():
        errors.append(
            ".methodology/gate2_result.json not found. Run "
            "`finalize-gate --gate 2 --phase 3 --project .` first."
        )
    else:
        try:
            _g2 = json.loads(gate2_path.read_text(encoding="utf-8"))
            _g2_score = _g2.get("composite_score") or _g2.get("overall_score") or 0
            if _g2_score < 75:
                errors.append(
                    f"Gate 2 composite score {_g2_score} < 75. "
                    f"Fix Gate 2 failures before PUSH ⑤."
                )
        except (json.JSONDecodeError, OSError) as e:
            errors.append(f"Could not parse gate2_result.json: {e}")

    # 2. Per-FR Gate 1 sentinel precondition
    # Bug #120: _sentinel_path() (run-gate) writes the file as
    #   g{gate}_{fr_id.replace('-', '').lower()}.flag    -> g1_fr01.flag
    # This check must use the same naming so the two sides agree. Pre-fix
    # the check used .lower() without stripping the hyphen, looking for
    # g1_fr-01.flag and reporting a spurious missing sentinel after a
    # successful Gate 1 finalize.
    missing_sentinels: list[str] = []
    for fr_id in fr_ids:
        # v2.13: this precondition is Phase 3-specific (filename _validate_p3_…);
        # pass phase=3 explicitly so we look for the per-phase path (Bug #121).
        sentinel = _sentinel_path(project, 1, fr_id, phase=3)
        if not sentinel.exists():
            missing_sentinels.append(fr_id)
    if missing_sentinels:
        errors.append(
            f"Per-FR Gate 1 sentinel missing for {len(missing_sentinels)} FR(s): "
            f"{', '.join(missing_sentinels)}. Run "
            f"`finalize-gate --gate 1 --phase 3 --fr-id <FR-ID> --project .` for each."
        )

    return errors

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

def _validate_handoff_p1_to_p2(project: Path) -> list[str]:
    """P1→P2: TEST_INVENTORY.yaml must exist, be non-empty, and cover all FRs."""
    errors: list[str] = []
    # NOTE: TEST_INVENTORY.yaml lives at project root per harness design
    # (cmd_check_test_inventory @ line ~993, D4 checksum @ line ~5018,
    #  init-project template @ line ~8286). This B.1 check originally
    # looked at 01-requirements/ — inconsistent with the rest of harness,
    # and silently blocked every fresh project's P2 entry (Bug
    # discovered 2026-06-17, integration-test E2E).
    inv_path = project / "TEST_INVENTORY.yaml"
    if not inv_path.exists():
        return [
            "TEST_INVENTORY.yaml missing at project root. "
            "P1 Sub-Task 4/4 in the plan template produces this file. "
            "Re-run the Phase 1 orchestrator or invoke the inventory skill manually."
        ]
    text = inv_path.read_text(encoding="utf-8", errors="replace").strip()
    if not text:
        return [
            "TEST_INVENTORY.yaml is empty. P1 orchestrator produced a stub. "
            "Re-run Phase 1 with explicit --fr-tests populated."
        ]

    # Parse and check coverage
    try:
        import yaml
        inventory = yaml.safe_load(text)
    except ImportError:
        inventory = _parse_inventory_fallback(text)
    except Exception as e:  # pylint: disable=broad-exception-caught
        return [f"TEST_INVENTORY.yaml is unparseable: {e}"]

    if not inventory.get("fr_tests") and not inventory.get("cross_cutting"):
        errors.append(
            "TEST_INVENTORY.yaml has neither `fr_tests:` nor `cross_cutting:` "
            "sections. At minimum the P1 naming authority must declare per-FR test names."
        )

    # Check that every FR in SRS has at least one test name in inventory
    srs_path = ProjectLayout(project).srs_path
    if srs_path.exists():
        srs_text = srs_path.read_text(encoding="utf-8", errors="replace")
        declared_frs = set(re.findall(r"\bFR-\d+\b", srs_text))
        covered_frs: set[str] = set()
        fr_tests = inventory.get("fr_tests") or {}
        for fr_id, names in fr_tests.items():
            if names:  # non-empty list of test names
                # I: use canonical_form() — handles all variants
                try:
                    norm = canonical_form(fr_id)
                except ValueError:
                    continue
                if norm in declared_frs:
                    covered_frs.add(norm)
        missing_frs = declared_frs - covered_frs
        if missing_frs:
            errors.append(
                f"TEST_INVENTORY.yaml missing test names for {len(missing_frs)} "
                f"FR(s) declared in SRS.md: {', '.join(sorted(missing_frs))}. "
                f"P1 deliverable must name at least one test per FR."
            )

    return errors


def _validate_handoff_p2_to_p3(project: Path) -> list[str]:
    """P2→P3: TEST_SPEC.md must contain parseable named test cases (table format)."""
    errors: list[str] = []
    spec_path = ProjectLayout(project).test_spec_path
    if not spec_path.exists():
        return [
            "TEST_SPEC.md missing at 02-architecture/TEST_SPEC.md. "
            "P2 Sub-Task 3/3 produces this file via the derive_test_cases.md skill. "
            "Re-run Phase 2 orchestrator with explicit skill invocation."
        ]
    items = _parse_test_spec(spec_path)
    if not items:
        # 0 cases may be legitimate (genuinely empty) or wrong-shape. Distinguish.
        _code, _ = _run_spec_coverage_check(
            project, threshold=60.0, fr_id=None, verbose=False
        )
        if _code == 1:
            errors.append(
                "TEST_SPEC.md has 0 parseable test cases but FRs are defined. "
                "The file is likely the wrong shape (prose strategy doc instead "
                "of the derive_test_cases.md table). Re-run the skill in Phase 2."
            )
        # else: spec-coverage returned 0 (vacuous OK because no FRs); pass.
    return errors


def _validate_handoff_p3_to_p4(project: Path) -> list[str]:
    """P3→P4: every FR must have a per-FR Gate 1 sentinel.

    Same precondition as push-milestone --type p3-post-gate2, but fr_ids
    is auto-resolved from the manifest if not provided.
    """
    fr_ids = _resolve_fr_ids_from_manifest(project)
    if not fr_ids:
        return [
            "Could not resolve FR IDs from .methodology/quality_manifest.json "
            "or --fr-ids. Cannot verify per-FR Gate 1 sentinels."
        ]
    return _validate_p3_post_gate2_precondition(project, fr_ids)


def _validate_handoff_p4_to_p5(project: Path) -> list[str]:
    """P4→P5: TEST_RESULTS.md (P4's deliverable) must exist with non-trivial content,
    AND Gate 3 must be PASS in quality_manifest.json.

    Bug fix (harness-methodology handoff-loop): the previous implementation
    required `05-verification/VERIFICATION_REPORT.md` here, but that file is
    *produced by Phase 5* — checking it on P4→P5 handoff is a chicken-and-egg
    that blocks every fresh Phase 5 entry. Aligned with the other handoff
    validators (P1→P2 checks P1's SRS, P2→P3 checks P2's TEST_SPEC, etc.):
    verify the *upstream* phase's deliverable, not the downstream one.

    VERIFICATION_REPORT.md existence is still asserted by `_validate_handoff_p5_to_p6`
    below, which is the correct handoff boundary for that file.
    """
    errors: list[str] = []
    results_path = ProjectLayout(project).test_results_path
    if not results_path.exists():
        return [
            "TEST_RESULTS.md missing at 04-testing/TEST_RESULTS.md. "
            "Phase 4 produces this file. Re-run Phase 4 orchestrator."
        ]
    text = results_path.read_text(encoding="utf-8", errors="replace").strip()
    if len(text) < 200:
        errors.append(
            f"TEST_RESULTS.md is suspiciously short ({len(text)} chars). "
            f"Real test results are ≥ 1KB. Possible stub."
        )
    # Gate 3 PASS precondition: verified via quality_manifest.json (written by P4
    # workflow). Mirrors the entry-gate check at _verify_entry_gate
    # (harness_cli.py:1553): the manifest's top-level key is `gate_results`
    # (not `gates`), and the field that signals completion is
    # `quality_complete` (not `status`).
    manifest_path = project / ".methodology" / "quality_manifest.json"
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            gate_results = manifest.get("gate_results") or {}
            gate3 = gate_results.get("gate3") or {}
            if not gate3.get("quality_complete"):
                errors.append(
                    "Gate 3 not PASS in .methodology/quality_manifest.json "
                    "(gate_results.gate3.quality_complete is not True). "
                    "Re-run Phase 4 Gate 3 evaluation."
                )
        except (json.JSONDecodeError, OSError):
            pass  # unparseable manifest is a separate concern; don't double-fail here
    return errors


def _validate_handoff_p5_to_p6(project: Path) -> list[str]:
    """P5→P6: VERIFICATION_REPORT.md must exist (aligned with plan text)."""
    errors: list[str] = []
    report = ProjectLayout(project).verification_report_path
    if not report.exists() and not (project / "VERIFICATION_REPORT.md").exists():
        return [
            "VERIFICATION_REPORT.md missing at 05-verification/VERIFICATION_REPORT.md (or VERIFICATION_REPORT.md). "
            "Phase 5 produces this file via the verify methodology."
        ]
    return errors


def _validate_handoff_p6_to_p7(project: Path) -> list[str]:
    """P6→P7: QUALITY_REPORT.md, RELEASE_NOTES.md, FINAL_SIGN_OFF.md must exist
    (same artifacts P6 dispatch review covers; also gate4 quality_complete must be True)."""
    errors: list[str] = []
    q6 = ProjectLayout(project).phase6_quality_dir
    for name in ("QUALITY_REPORT.md", "RELEASE_NOTES.md", "FINAL_SIGN_OFF.md"):
        if not (q6 / name).exists() and not (project / name).exists():
            errors.append(f"{name} missing at 06-quality/{name} (or root). Phase 6 produces this file.")
            
    manifest_path = project / ".methodology" / "quality_manifest.json"
    if manifest_path.exists():
        try:
            import json as _json
            manifest = _json.loads(manifest_path.read_text(encoding="utf-8"))
            gate_results = manifest.get("gate_results") or {}
            gate4 = gate_results.get("gate4") or {}
            if not gate4.get("quality_complete"):
                errors.append(
                    "Gate 4 not PASS in .methodology/quality_manifest.json "
                    "(gate_results.gate4.quality_complete is not True). "
                    "Re-run Phase 6 Gate 4 evaluation."
                )
        except Exception:  # pylint: disable=broad-exception-caught
            pass  # malformed JSON is a separate concern; don't block handoff
    else:
        errors.append("quality_manifest.json missing; run `finalize-gate --gate 4 --phase 6` first.")
    return errors


def _validate_handoff_p7_to_p8(project: Path) -> list[str]:
    """P7→P8: risk register deliverables must exist (07-risk/RISK_REGISTER.md,
    RISK_MITIGATION_PLANS.md, RISK_STATUS_REPORT.md)."""
    errors: list[str] = []
    q7 = ProjectLayout(project).phase7_risk_dir
    for name in ("RISK_REGISTER.md", "RISK_MITIGATION_PLANS.md", "RISK_STATUS_REPORT.md"):
        if not (q7 / name).exists():
            errors.append(f"{name} missing at 07-risk/{name}. Phase 7 produces this file.")
    return errors


def _validate_handoff_p8_to_p9(project: Path) -> list[str]:
    """P8→P9: config records + release checklist must exist, and the
    .methodology-archive/ release snapshot must be populated (P8 milestone
    prerequisite) before entering maintenance."""
    errors: list[str] = []
    q8 = ProjectLayout(project).phase8_config_dir
    for name in ("CONFIG_RECORDS.md", "RELEASE_CHECKLIST.md"):
        if not (q8 / name).exists():
            errors.append(f"{name} missing at 08-config/{name}. Phase 8 produces this file.")
    archive_dir = project / ".methodology-archive"
    if not archive_dir.is_dir() or not any(archive_dir.iterdir()):
        errors.append(
            ".methodology-archive/ missing or empty — the P8 release snapshot "
            "must exist before entering maintenance (push-milestone --type p8 validates it)."
        )
    return errors


_HANDOFF_VALIDATORS = {
    1: _validate_handoff_p1_to_p2,
    2: _validate_handoff_p2_to_p3,
    3: _validate_handoff_p3_to_p4,
    4: _validate_handoff_p4_to_p5,
    5: _validate_handoff_p5_to_p6,
    6: _validate_handoff_p6_to_p7,
    7: _validate_handoff_p7_to_p8,
    8: _validate_handoff_p8_to_p9,
}


def _resolve_fr_ids_from_manifest(project: Path) -> list[str]:
    """Resolve FR IDs from .methodology/quality_manifest.json (fr_ids field)."""
    manifest_path = project / ".methodology" / "quality_manifest.json"
    if not manifest_path.exists():
        return []
    try:
        _mf = json.loads(manifest_path.read_text(encoding="utf-8"))
        return list(_mf.get("fr_ids") or [])
    except (json.JSONDecodeError, OSError):
        return []


def _validate_handoff(project: Path, from_phase: int) -> list[str]:
    """Dispatch to the right per-transition validator.

    Args:
        project:    project root
        from_phase: phase number that just completed (1..8). P8→P9 checks
                    the release snapshot before entering maintenance; P9
                    itself never hands off (terminal steady state).

    Returns:
        list of error strings (empty = handoff OK).
    """
    if from_phase not in _HANDOFF_VALIDATORS:
        return [
            f"No handoff validator for from-phase={from_phase}. "
            f"Supported: {sorted(_HANDOFF_VALIDATORS.keys())}."
        ]
    return _HANDOFF_VALIDATORS[from_phase](project)


def cmd_validate_handoff(args: argparse.Namespace) -> int:
    """v2.9.1 B.1: Cross-deliverable dependency check for phase handoffs.

    Validates that the upstream phase's deliverables are present and
    well-formed before the downstream phase is launched. Used by
    workflow JS as a pre-launch precondition and by Agent B peer
    review as a structural cross-deliverable assertion.

    Usage:
        python harness_cli.py validate-handoff --from-phase 1 --project .
        python harness_cli.py validate-handoff --from-phase 2 --project .
        python harness_cli.py validate-handoff --from-phase 3 --project .

    Exit 0 = handoff OK. Exit 1 = handoff blocked (error list printed).
    """
    project = Path(args.project).resolve()
    from_phase = args.from_phase
    errors = _validate_handoff(project, from_phase)
    if not errors:
        print(f"[validate-handoff] P{from_phase} → P{from_phase + 1}: OK")
        return 0
    print(f"[validate-handoff] P{from_phase} → P{from_phase + 1}: BLOCKED")
    for e in errors:
        print(f"  • {e}")
    return 1

# ---------------------------------------------------------------------------
# gate4-tag  (create annotated git tag from gate4_result.json)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------

def cmd_status(args: argparse.Namespace) -> int:
    """Show current manifest + FSM state, phase progress, and optionally test stats."""
    project = Path(args.project).resolve()
    manifest_path = project / ".methodology" / "quality_manifest.json"
    state_path    = project / ".methodology" / "state.json"
    json_out = getattr(args, "json", False)
    full = getattr(args, "full", False)

    # Gather state
    state = {}
    if state_path.exists():
        state = json.loads(state_path.read_text(encoding="utf-8"))

    manifest = {}
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    current_phase = state.get("current_phase", 0)
    fr_ids = manifest.get("fr_ids", [])
    gates = manifest.get("gate_results", {})

    # Phase progress table (short display names shared with the CLAUDE.md
    # status block — one map, keys anchored by test_phase_topology_ssot)
    phase_names = _PHASE_NAMES
    phase_status = {}
    for p in VALID_PHASES:
        if p < current_phase:
            phase_status[p] = "COMPLETE"
        elif p == current_phase:
            phase_status[p] = "IN_PROGRESS"
        else:
            phase_status[p] = "NOT_STARTED"

    # FR gate status for current phase
    fr_status = {}
    if current_phase >= 3 and gates.get("gate1"):
        for fr_id in fr_ids:
            fr_result = gates["gate1"].get(fr_id)
            if fr_result and isinstance(fr_result, dict):
                fr_status[fr_id] = {"score": fr_result.get("score", 0), "complete": fr_result.get("quality_complete", False)}
            else:
                fr_status[fr_id] = {"score": None, "complete": False}

    # Test stats (only when --full)
    test_count = None
    coverage_pct = None
    if full:
        import subprocess  # nosec B404
        # Bug #117 ext: route through sys.executable so the venv's pytest is
        # used; bare 'pytest' on macOS PATH resolves to CommandLineTools 3.9.
        try:
            r = subprocess.run([sys.executable, "-m", "pytest", "--collect-only", "-q", "--no-header"],
                             cwd=project, capture_output=True, text=True, timeout=30)
            m = re.search(r"(\d+) tests? collected", r.stdout + r.stderr)
            if m:
                test_count = int(m.group(1))
        except Exception:
            pass
        try:
            r = subprocess.run([sys.executable, "-m", "pytest", "--cov=.", "--cov-report=term", "--tb=no", "-q"],
                             cwd=project, capture_output=True, text=True, timeout=120)
            m = re.search(r"TOTAL\s+\d+\s+\d+\s+(\d+)%", r.stdout + r.stderr)
            if m:
                coverage_pct = int(m.group(1))
        except Exception:
            pass

    # Auto-fix rounds
    auto_fix_rounds_used = 0
    if full and gates:
        for gate_name in ["gate1", "gate2", "gate3", "gate4"]:
            gv = gates.get(gate_name)
            if isinstance(gv, dict) and "rounds_used" in gv:
                auto_fix_rounds_used = max(auto_fix_rounds_used, gv.get("rounds_used", 0))

    if json_out:
        result = {
            "project": str(project),
            "fsm": {"state": state.get("state", "UNKNOWN"), "current_phase": current_phase,
                    "last_update": state.get("last_update", "-")},
            "phase_progress": {str(p): phase_status[p] for p in VALID_PHASES},
            "fr_ids": fr_ids,
            "gates": gates,
        }
        if full:
            result["test_count"] = test_count
            result["coverage_pct"] = coverage_pct
            result["auto_fix_rounds_used"] = auto_fix_rounds_used
        print(json.dumps(result, indent=2, default=str))
        return 0

    # Text output
    print(f"\n{'='*60}\nHarness Status: {project}\n{'='*60}")

    if state:
        print("\n[FSM State]")
        print(f"  state         : {state.get('state', 'UNKNOWN')}")
        print(f"  current_phase : {current_phase}")
        print(f"  last_update   : {state.get('last_update', '-')}")
    else:
        print("\n[FSM State] .methodology/state.json not found (project not initialised)")

    # Phase progress table
    print("\n[Phase Progress]")
    for p in VALID_PHASES:
        icon = {"COMPLETE": "✅", "IN_PROGRESS": "🔄", "NOT_STARTED": "⬜"}.get(phase_status[p], "⬜")
        print(f"  {icon} P{p} {phase_names.get(p, 'Unknown'):<16} {phase_status[p]}")

    if manifest:
        print("\n[Quality Manifest]")
        print(f"  schema_version: {manifest.get('schema_version')}")
        print(f"  fr_ids        : {fr_ids}")
        for g, v in gates.items():
            if v is None:
                print(f"  {g}           : not run")
            elif isinstance(v, dict) and "score" in v:
                print(f"  {g}           : score={v['score']} complete={v.get('quality_complete', False)}")
            elif isinstance(v, dict):
                for fr, r in v.items():
                    if isinstance(r, dict):
                        print(f"  {g}/{fr}  : score={r.get('score', 0)} complete={r.get('quality_complete', False)}")
                    else:
                        print(f"  {g}/{fr}  : {r}")
    else:
        print("\n[Quality Manifest] Not found — run `harness_cli.py manifest` first")

    # FR detail for current phase
    if fr_status:
        print(f"\n[FR Gate 1 Status — Phase {current_phase}]")
        for fr_id, fs in fr_status.items():
            if fs["score"] is not None:
                print(f"  {fr_id}: score={fs['score']} complete={fs['complete']}")
            else:
                print(f"  {fr_id}: not run")

    # CRG status
    crg_status_path = project / ".sessi-work" / "crg_status.json"
    print("\n[CRG]")
    if crg_status_path.exists():
        try:
            crg_status = json.loads(crg_status_path.read_text(encoding="utf-8"))
            if crg_status.get("available"):
                nodes = crg_status.get("node_count", "?")
                action = crg_status.get("action", "")
                tag = " (auto-built)" if action == "auto_built" else ""
                print(f"  graph     : {nodes} nodes{tag}")
                # Reconnaissance
                recon_path = project / ".sessi-work" / "crg_reconnaissance.json"
                if recon_path.is_file() and recon_path.stat().st_size > 0:
                    print(f"  recon     : available ({recon_path.stat().st_size} bytes)")
                else:
                    print("  recon     : not yet run")
                # Metrics
                metrics_path = project / ".sessi-work" / "crg_metrics.json"
                if metrics_path.is_file():
                    print(f"  metrics   : available ({metrics_path.stat().st_size} bytes)")
                else:
                    print("  metrics   : not yet computed")
                # Graph stats (live from MCP — graceful degrade if unavailable)
                try:
                    from mcp_tools import (  # type: ignore[import-untyped, import-not-found, attr-defined]
                        mcp__code_review_graph__list_graph_stats_tool as _gs_fn,  # type: ignore[attr-defined]
                    )
                    _gs = _gs_fn(repo_root=str(project))
                    print(
                        f"  graph_db  : {_gs.get('total_nodes','?')} nodes · "
                        f"{_gs.get('total_edges','?')} edges · "
                        f"updated {(_gs.get('last_updated') or '')[:10]}"
                    )
                except Exception:
                    pass  # MCP not available in this subprocess context
            else:
                print(f"  status    : unavailable — {crg_status.get('reason', 'unknown')}")
        except (json.JSONDecodeError, OSError):
            print("  status    : error reading crg_status.json")
    else:
        print("  status    : not initialized — run Gate 3 or Gate 4 to build graph")

    if full:
        print("\n[Test Stats]")
        print(f"  tests collected: {test_count if test_count is not None else 'N/A'}")
        print(f"  coverage       : {coverage_pct}%" if coverage_pct is not None else "  coverage       : N/A")
        print("\n[Auto-Fix]")
        print(f"  rounds_used    : {auto_fix_rounds_used}")

    return 0

# ---------------------------------------------------------------------------
# load-context
# ---------------------------------------------------------------------------

def cmd_load_context(args: argparse.Namespace) -> int:
    """Load project context for a phase and output as JSON."""
    import json as _json

    project = Path(args.project).resolve()
    phase = args.phase

    manifest_path = project / ".methodology" / "quality_manifest.json"
    state_path = project / ".methodology" / "state.json"

    # fr_ids and gate_results from manifest
    fr_ids: list = []
    gate_results: dict = {}
    fr_id_source: str = ""  # diagnostics: where did fr_ids come from?
    if manifest_path.exists():
        try:
            manifest = _json.loads(manifest_path.read_text(encoding="utf-8"))
            fr_ids = manifest.get("fr_ids", [])
            gate_results = manifest.get("gate_results", {})
            if fr_ids:
                fr_id_source = "quality_manifest.json"
        except Exception:  # pylint: disable=broad-exception-caught
            pass

    # P1 fallback (bug #2 fix): when quality_manifest.json is missing or empty
    # (the chicken-and-egg case at P1 entry, before P2 generates the manifest),
    # extract fr_ids from the canonical_spec declared in PROJECT_BRIEF.md.
    # Without this fallback, load-context at P1 returns fr_ids=[] and the
    # orchestrator cannot enumerate FR scope. Repro: integration-test P1
    # bootstrap 2026-06-15.
    if not fr_ids:
        brief_path = project / "PROJECT_BRIEF.md"
        if brief_path.exists():
            try:
                import re as _re

                _brief_text = brief_path.read_text(encoding="utf-8")
                # Support two PROJECT_BRIEF.md layouts:
                # (a) inline frontmatter / config-style: `canonical_spec: SPEC.md`
                # (b) markdown heading:        `## canonical_spec\nSPEC.md\n`
                _spec_rel: str | None = None
                _m_inline = _re.search(
                    r"^\s*canonical_spec\s*:\s*(\S+)\s*$",
                    _brief_text,
                    _re.MULTILINE,
                )
                if _m_inline:
                    _spec_rel = _m_inline.group(1).strip()
                else:
                    _m_heading = _re.search(
                        r"^##\s*canonical_spec\s*$\n([^\n]+)",
                        _brief_text,
                        _re.MULTILINE,
                    )
                    if _m_heading:
                        _spec_rel = _m_heading.group(1).strip()
                if _spec_rel:
                    _spec_path = (
                        Path(_spec_rel)
                        if Path(_spec_rel).is_absolute()
                        else project / _spec_rel
                    )
                    if _spec_path.exists():
                        _spec_text = _spec_path.read_text(encoding="utf-8")
                        # Extract FR headers like `### FR-01: ...`
                        _frs = _re.findall(
                            r"^###\s+FR-(\d+)\s*:", _spec_text, _re.MULTILINE
                        )
                        if _frs:
                            fr_ids = [f"FR-{n}" for n in _frs]
                            fr_id_source = (
                                f"PROJECT_BRIEF.md::canonical_spec → "
                                f"{_spec_rel} (P1 fallback, quality_manifest.json not yet generated)"
                            )
            except Exception:  # pylint: disable=broad-exception-caught
                pass

    # current_phase from state.json
    current_phase = 0
    if state_path.exists():
        try:
            state = _json.loads(state_path.read_text(encoding="utf-8"))
            current_phase = state.get("current_phase", 0)
        except Exception:  # pylint: disable=broad-exception-caught
            pass

    # fr_details from SRS.md (optional)
    fr_details: dict = {}
    try:
        from scripts.generate_full_plan import parse_srs_fr_sections
        srs_path = ProjectLayout(project).srs_path
        frs = parse_srs_fr_sections(srs_path if srs_path.exists() else None)
        for fr in frs:
            fr_details[fr["fr"]] = {
                "title": fr.get("title", ""),
                "desc": fr.get("desc", ""),
                "acceptance": fr.get("requirements", []),
            }
    except Exception:  # pylint: disable=broad-exception-caught
        pass

    # modules from SAD.md (optional)
    modules: dict = {}
    try:
        from scripts.generate_full_plan import parse_sad_modules
        modules = parse_sad_modules(project)
    except Exception:  # pylint: disable=broad-exception-caught
        pass

    result = {
        "phase": phase,
        "project_name": project.name,
        "fr_ids": fr_ids,
        "fr_details": fr_details,
        "modules": modules,
        "gate_results": gate_results,
        "current_phase": current_phase,
        "fr_id_source": fr_id_source or "none",
    }

    # Sentinel warning: existing artifacts still in template state?
    # P1/P2 entry agents must distinguish "real SRS.md" from "template placeholder
    # left by `init-project`". Without this check, Agent A might assume P1 is
    # complete because SRS.md exists — but the file is still a stub.
    # Per SKILL.md §0.3.1, stub = sentinel literal OR ≥8 {placeholder} patterns
    # (co-equal heuristic `_is_stub_template`). Paths match
    # `_init_copy_templates` artifact_map (the locations init-project writes).
    from core.quality_gate.constitution.runner import _is_stub_template
    _sentinel = "<!-- harness:template-stub -->"
    _template_artifacts = (
        "01-requirements/SRS.md",
        "02-architecture/SAD.md",
        "02-architecture/TEST_SPEC.md",
        "02-architecture/adr/ADR.md",
    )
    _warnings: list = []
    for _rel in _template_artifacts:
        _p = project / _rel
        if _p.exists():
            try:
                _content = _p.read_text(encoding="utf-8")
            except OSError:
                continue
            if (_sentinel in _content) or _is_stub_template(_content):
                _warnings.append(
                    f"{_rel} is a template stub (sentinel literal or "
                    f"≥8 {{placeholder}} patterns per SKILL.md §0.3.1) — "
                    f"this is a template placeholder, not real content. "
                    f"Remove the sentinel / fill the placeholders before "
                    f"treating it as a real artifact."
                )
    if _warnings:
        result["warnings"] = _warnings

    print(_json.dumps(result, indent=2, default=str))
    return 0

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
def cmd_read_file(args: argparse.Namespace) -> int:
    from scripts.file_loader import load_file  # lazy import — file_loader.py is heavy

    result = load_file(
        file_path=args.file,
        expect_prefix=args.expect_prefix,
        min_length=args.min_length,
        max_length=args.max_length,
        include_content=args.content,
    )

    json_text = json.dumps(result, indent=2, ensure_ascii=False)

    if args.json_out:
        Path(args.json_out).write_text(json_text, encoding="utf-8")
    else:
        print(json_text)

    if args.content_out and result.get("content") is not None:
        Path(args.content_out).write_text(result["content"], encoding="utf-8")

    if not args.quiet:
        status = result["status"]
        sha = result["content_sha256"]
        sha_short = (sha[:12] + "...") if sha else "(none)"
        msg = (
            f"[read-file] {status} "
            f"file={args.file} "
            f"sha256={sha_short} "
            f"bytes={result['byte_size']} "
            f"lines={result['line_count']}"
        )
        if status != "OK":
            msg += f" — {result['diagnostic']}"
        print(msg, file=sys.stderr)

    if result["status"] == "OK":
        return 0
    if result["status"] in {"MISSING", "PREFIX_MISMATCH", "TOO_SHORT", "TOO_LONG"}:
        return 1
    return 2  # READ_ERROR


# ---------------------------------------------------------------------------
# effort
# ---------------------------------------------------------------------------

def cmd_effort(args: argparse.Namespace) -> int:
    """Show gate effort metrics summary."""
    from harness.effort_tracker import EffortTracker

    tracker = EffortTracker()
    summary = tracker.summary(phase=args.phase)

    print(f"\n{'='*60}")
    title = f"Effort Summary{' | Phase ' + str(args.phase) if args.phase else ''}"
    print(f"{title}\n{'='*60}")
    print(json.dumps(summary, indent=2))
    return 0

# ---------------------------------------------------------------------------
# advance-phase
# ---------------------------------------------------------------------------


def _generate_stage_pass(project_path: Path, gate_num: int, phase_num: int) -> None:
    """Write machine-generated 00-summary/Phase{N}_STAGE_PASS.md from quality_manifest.json.

    No LLM involvement — content comes entirely from quality_manifest.json +
    state.json.phase_truth_passed (fallback). Called automatically by
    cmd_finalize_gate() after bridge.finalize_gate succeeds.

    Gate-data interpretation rules (B-class bug fix — Phase 1-2 + per-FR Gate 1):
      - Gate 2/3/4: flat dict with top-level `score` + `quality_complete`.
      - Gate 1 in Phase 3+: per-FR dict `{"FR-XX": {"score": N, "quality_complete":
        bool}, ...}` — aggregate across FRs (ALL must be True for PASS).
      - Empty gate_data (Phase 1-2 where Gate 1 has not fired yet) — fall back to
        state.json.phase_truth_passed to derive verdict. Without this fallback,
        Phase 1-2 always wrote "exit gate FAIL" even when the phase succeeded.
    """
    from datetime import datetime, timezone as _tz

    gate_data: dict = {}
    manifest_path = project_path / ".methodology" / "quality_manifest.json"
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            gate_data = manifest.get("gate_results", {}).get(f"gate{gate_num}", {}) or {}
        except (json.JSONDecodeError, OSError):
            pass

    # Detect per-FR Gate 1 structure: dict values are dicts (FR records), not scalars.
    # Flat Gate 2/3/4 has top-level "score" + "quality_complete" scalars.
    is_per_fr_gate1 = (
        gate_num == 1
        and bool(gate_data)
        and all(isinstance(v, dict) for v in gate_data.values())
    )

    if is_per_fr_gate1:
        # Aggregate per-FR Gate 1: all FRs must be quality_complete for PASS.
        fr_records = list(gate_data.values())
        scores = [r.get("score") for r in fr_records if isinstance(r.get("score"), (int, float))]
        qc = all(bool(r.get("quality_complete")) for r in fr_records)
        if scores:
            score = round(sum(scores) / len(scores), 2)
        else:
            score = "N/A"
    elif gate_data:
        # Flat structure (Gate 2/3/4 or pre-DELTA Gate 1).
        score = gate_data.get("score", "N/A")
        qc = bool(gate_data.get("quality_complete", False))
    else:
        # Empty gate_data — gate has not fired for this phase.
        # Phase 1-2 + Phase 5/7/8: Gate 1 not fired yet (Gate 1 is per-FR at
        # Phase 3+, or DELTA at Phase 5/7/8 — DELTA may not write gate_results
        # when no code changes). Fall back to state.json.phase_truth_passed,
        # which is set by advance-phase verify_phase_truth on success.
        score = "N/A"
        qc = False
        state_path = project_path / ".methodology" / "state.json"
        if state_path.exists():
            try:
                state = json.loads(state_path.read_text(encoding="utf-8"))
                if state.get("phase_truth_passed") is True:
                    qc = True
            except (json.JSONDecodeError, OSError):
                pass

    out_dir = project_path / "00-summary"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"Phase{phase_num}_STAGE_PASS.md"

    content = (
        f"# Phase {phase_num} STAGE_PASS\n\n"
        f"Generated: {datetime.now(_tz.utc).strftime('%Y-%m-%d %H:%M UTC')}\n\n"
        f"## Gate Score\n"
        f"Gate {gate_num} Composite Score: **{score}**\n\n"
        f"## Quality Status\n"
        f"quality_complete: **{qc}**\n\n"
        f"## Deliverables\n"
        f"Phase {phase_num} deliverables verified by PhaseArtifactRegistry.\n\n"
        f"## Summary\n"
        f"Phase {phase_num} exit gate {'PASS' if qc else 'FAIL'}.\n"
    )
    try:
        out_path.write_text(content, encoding="utf-8")
        print(f"  [STAGE_PASS] Written → {out_path.relative_to(project_path)}")
    except OSError as exc:
        print(f"  [WARN] Could not write STAGE_PASS.md: {exc}")


def _run_phase_auditor(project: Path, completed_phase: int) -> int:
    """Run PhaseAuditor (local mode) — replaced deprecated phase_end_audit.py (v2.5.0).

    Returns:
      0  = all checks pass
      8  = C1 CRITICAL (deliverables missing / untracked)
      1  = other CRITICAL findings
      2  = error / import failure
    """
    try:
        from scripts.phase_auditor import PhaseAuditor, LocalFetcher
    except ImportError as exc:
        print(f"  [WARN] PhaseAuditor unavailable ({exc}) — skipping comprehensive audit")
        return 0

    try:
        fetcher = LocalFetcher(project_root=str(project))
        auditor = PhaseAuditor(fetcher=fetcher, phase=completed_phase)

        result = auditor.run_all_checks()

        criticals = result.criticals()
        warnings  = result.warnings()

        if criticals:
            # Route exit code by check_id for semantic consistency
            c1_criticals = [c for c in criticals if c.check_id == "C1"]

            print(f"\n  [PHASE-AUDITOR] ❌ {len(criticals)} CRITICAL finding(s) — must fix:")
            for c in criticals[:5]:
                print(f"    ❌ [{c.check_id}] {c.title}")
            if len(criticals) > 5:
                print(f"    ... and {len(criticals) - 5} more")
            print("\n  Full report:")
            print(f"    python harness_cli.py audit-phase --phase {completed_phase}"
                  f" --project {project}")

            if c1_criticals:
                print(f"\n  [BLOCKED] {len(c1_criticals)} deliverable(s) missing/untracked.")
                return 8
            return 1

        if warnings:
            print(f"  [PHASE-AUDITOR] ⚠️  {len(warnings)} warning(s) — review recommended")
        print(f"  [PHASE-AUDITOR] Score={result.score:.0f}%  Verdict={result.verdict} ✓")
        return 0

    except Exception as exc:
        print(f"  [ERROR] PhaseAuditor failed unexpectedly: {exc}")
        return 2


def _validate_fr_coverage_immediate(
    project: Path, timeout: int = 120
) -> Optional[float]:
    """Run ``pytest --cov`` for the whole project right now and return line coverage %.

    Returns:
        ``None``      — pytest not installed, no tests found, or subprocess error.
        ``float``     — coverage percentage (0.0 - 100.0), or 0.0 if tests failed.

    Single whole-project pytest run (1-2s in practice) is used rather than
    per-FR scoped runs. Rationale: per-FR coverage is structurally misleading
    in multi-FR projects — each FR's test only covers its own source files,
    not the other 7 FRs' files, so a per-FR scope would always report
    ~1/N of project coverage. Whole-project coverage is the only signal
    that proves "all source is exercised by tests" (the actual TDD goal).
    Mirrors the TDD-PRECHECK check at line ~4220; advance-phase re-runs the
    same measurement so the manifest's recorded score is verified live.

    """
    layout = ProjectLayout(project)
    src_dir = layout.active_src_dir
    tests_dir = layout.active_test_dir
    if not src_dir.is_dir():
        return None
    if not tests_dir.is_dir():
        return None
    cov_target = layout.get_relative_str(src_dir)
    cmd = [
        sys.executable, "-m", "pytest",
        f"--cov={cov_target}", "--cov-report=term",
        "--tb=no", "-q",
    ]
    try:
        r = subprocess.run(
            cmd, capture_output=True, text=True,
            cwd=str(project), timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return None
    except Exception:
        return None
    m = re.search(r"TOTAL\s+\d+\s+\d+\s+(\d+)%", r.stdout)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            return None
    return 0.0 if r.returncode == 0 else None


def _check_gate1_live_coverage(project: Path, completed_phase: int) -> int:
    """Verify Gate 1 coverage by running pytest --cov right now.

    Replaces the old gate_timestamps.jsonl-only check: a sentinel existing
    in the jsonl does NOT prove the code actually passes coverage today
    (the file is append-only and the manifest's ``gate_results.gate1[fr]``
    record is agent-writable). This function runs pytest per FR, scoped to
    the FR's own test + tagged source files, and verifies the live coverage
    meets ``min_coverage`` from the manifest.

    Returns:
        0  — all FRs pass live coverage (or manifest absent → non-FR project)
        14 — one or more FRs missing, failing, or below min_coverage
    """
    manifest_path = project / ".methodology" / "quality_manifest.json"
    manifest: dict = {}
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    fr_ids_manifest: list[str] = manifest.get("fr_ids", [])
    if not fr_ids_manifest:
        return 0  # Non-FR project or unreadable manifest — skip

    # Read min_coverage from manifest, default 80.0 (matches _check_fr_test_step).
    _min_cov = float(
        manifest.get("quality_targets", {}).get("min_coverage", 80.0)
    )

    # DELTA-phase auto-skip: P4/P5/P7/P8 re-run Gate 1 as a delta check. When
    # NO FR's code has changed since its last Gate 1 PASS, the per-FR DELTA
    # loop is a no-op (every run-fr-step would `already done → skip`). In
    # that case trust the prior finalize-gate record — re-running pytest
    # 8 times per advance would be wasted work. Code changes (test additions
    # included) force a fresh live check.
    if completed_phase in (4, 5, 7, 8):
        try:
            _all_unchanged = all(
                not _fr_code_changed_since_last_gate1(fr, project)
                for fr in fr_ids_manifest
            )
        except Exception:  # pylint: disable=broad-exception-caught
            _all_unchanged = False
        if _all_unchanged:
            print(
                f"  [Gate 1 coverage] Phase {completed_phase}: all {len(fr_ids_manifest)}"
                f" FR(s) unchanged since last gate — DELTA auto-satisfied (live pytest skipped)."
            )
            return 0

    # Live verification: one whole-project pytest --cov run proves the
    # manifest's recorded per-FR coverage is achievable against current code.
    cov = _validate_fr_coverage_immediate(project)
    if cov is None:
        print(
            f"\n[BLOCKED] Phase {completed_phase} Gate 1 live coverage check failed:\n"
            f"  pytest --cov could not be run (pytest missing, no tests/, or timeout).\n"
            f"  Re-run: python3 harness_cli.py finalize-gate --gate 1"
            f" --phase {completed_phase} --fr-id <FR-ID> --project {project}"
        )
        return 14
    if cov < _min_cov:
        print(
            f"\n[BLOCKED] Phase {completed_phase} Gate 1 live coverage check failed:\n"
            f"  whole-project coverage {cov:.1f}% < {_min_cov:.1f}% (from manifest)\n"
            f"  Add tests or use '# pragma: no cover' for unreachable paths, then re-run."
        )
        return 14
    print(
        f"  [Gate 1 coverage] Phase {completed_phase}: live pytest --cov"
        f" = {cov:.1f}% ≥ {_min_cov:.1f}% ✓ ({len(fr_ids_manifest)} FRs covered)"
    )
    return 0


def _check_deferred_fixes_resolved(project: Path) -> int:
    """Hard-block advance if deferred_fixes.md has unresolved items (Stage 5).

    Deferred fixes are escape-hatch debt from the CASE PLATEAU protocol — they
    close the quality loop only if they are actually resolved before leaving the
    phase (the audit found they were created but never enforced). Items are
    marked '- [ ]' (open) / '- [x]' (resolved); any open item blocks advance.
    Legacy free-text files with no checkboxes are treated as resolved
    (backward-compatible).

    Returns 0 if clear, 17 if unresolved deferred items remain.
    """
    dpath = project / ".methodology" / "deferred_fixes.md"
    if not dpath.exists():
        return 0
    try:
        content = dpath.read_text(encoding="utf-8")
    except OSError:
        return 0
    open_items = re.findall(r"^\s*-\s*\[ \]\s*(.+)$", content, re.MULTILINE)
    if open_items:
        print(f"\n[BLOCKED] {len(open_items)} unresolved deferred fix(es) in "
              ".methodology/deferred_fixes.md:")
        for _it in open_items[:10]:
            print(f"    - [ ] {_it.strip()}")
        print("  Resolve each item, then mark it '- [x]' (with evidence) before advancing.")
        return 17
    return 0


def _check_submodule_drift(project: Path) -> None:
    """Phase 6 improvement #3: detect when harness/ submodule HEAD is behind
    origin/main (e.g. CI auto-fix landed). Prints actionable warning.
    Non-blocking — silent skip when offline / no origin access.

    J improvement: prefer `harness sync` (one-shot) over manual 4-step process.
    Delegates to core.submodule_sync.behind_count() for the count.
    """
    from core.submodule_sync import behind_count as _behind_count
    _sub = project / "harness"
    _behind = _behind_count(_sub)
    if _behind <= 0:
        return  # offline (-1) or already up to date (0) → silent
    print(
        f"\n[WARN] harness/ submodule is {_behind} commit(s) behind "
        f"origin/main. CI may have applied test-fix commits."
    )
    print("  Quick fix — one-shot sync:")
    print("    python3 -m harness.cli sync-harness")
    print("  Or manually:")
    print(f"    git -C {project}/harness pull --ff-only origin main")
    print(f"    git -C {project} add harness && git commit -m "
          f"'chore(harness): bump submodule to latest'")
    print("  (Non-blocking — local checkout is still functional.)")


def cmd_sync_harness(args: argparse.Namespace) -> int:
    """J: `harness sync` — pull + commit + push harness submodule.

    One-shot replacement for the 4-step manual process:
      1. cd harness && git pull --ff-only
      2. cd .. && git add harness
      3. git commit -m "chore(harness): bump to v <sha>"
      4. git push

    Pre-condition: working tree must be clean (asserted).
    """
    from core.submodule_sync import (
        SubmoduleSyncError,
        sync_submodule,
    )
    project = Path(getattr(args, "project", "."))
    submodule = project / (args.submodule or "harness")
    push = not getattr(args, "no_push", False)

    try:
        result = sync_submodule(
            submodule,
            push=push,
            remote=getattr(args, "remote", "origin"),
            branch=getattr(args, "branch", "main"),
        )
    except SubmoduleSyncError as e:
        print(f"[sync-harness] FAILED: {e}", file=sys.stderr)
        return 19

    n = result["behind_count"]
    sha = result["short_sha"]
    if n == 0:
        print(f"[sync-harness] OK — already up-to-date ({sha})")
        return 0

    print(f"[sync-harness] OK — pulled {n} commit(s); new SHA: {sha}")
    import subprocess
    commit_msg = result["message"]
    subprocess.run(["git", "commit", "-m", commit_msg, "--", "harness"], cwd=project, check=True)
    if push:
        subprocess.run(["git", "push", "origin", "HEAD"], cwd=project, check=True)
        print(f"[sync-harness] Pushed: {commit_msg}")
    else:
        print(f"[sync-harness] (--no-push) Committed locally: {commit_msg}")
    return 0


def _advance_prechecks(project: Path, completed_phase: int) -> int:
    """Run pre-advance checks: Agent B approvals, gate variance, Phase Truth,
    PhaseAuditor C1-C12, TDD.

    Returns 0 if all checks pass, non-zero exit code on first failure:
      8  = C1 CRITICAL (deliverables missing / untracked)
      9  = pytest / coverage failure (P3+)
      10 = spec-coverage below phase threshold (P3+) [unified D4]
      11 = Phase Truth < 90% (P3+) or Mutation Testing failure (P3+)
      13 = Agent B approvals missing / rejected (P1/P2)
      14 = Gate 1 per-FR coverage incomplete (P3+)
      15 = Phase{N+1}_plan.md not found (generate-next-plan not run)
      16 = Constitution postflight below phase threshold (all phases)
      17 = Unresolved deferred fixes in deferred_fixes.md (P3+)
      18 = Submodule guard: harness/ has uncommitted edits that would be clobbered
    """
    # ── P1 checksum: TEST_INVENTORY.yaml baseline ────────────────────
    if completed_phase == 1:
        inventory_path = project / "TEST_INVENTORY.yaml"
        if inventory_path.exists():
            import hashlib
            _cksum = hashlib.sha256(inventory_path.read_bytes()).hexdigest()
            _state_path = project / ".methodology" / "state.json"
            try:
                with file_lock(state_lock_path(_state_path.parent.parent)):
                    _state: dict = {}
                    if _state_path.exists():
                        try:
                            _state = json.loads(_state_path.read_text(encoding="utf-8"))
                        except (json.JSONDecodeError, OSError):
                            pass
                    _state["test_inventory_checksum"] = _cksum
                    atomic_write_json(_state_path, _state)
                    print(f"  [D4] TEST_INVENTORY.yaml checksum: {_cksum[:12]}...")
            except OSError as _e:
                print(f"  [WARN] Could not write test_inventory_checksum: {_e}")

    # ── Gate score variance check ─────────────────────────────────────
    if completed_phase >= 3:
        _rc = _check_gate_score_variance(project, completed_phase)
        if _rc != 0:
            return _rc

    # ── Deferred-fix closure (P3+) — close the quality loop ────────────
    if completed_phase >= 3:
        _rc = _check_deferred_fixes_resolved(project)
        if _rc != 0:
            return _rc

    # ── Finalize-gate sentinel check ───────────────────────────────────
    # Verify finalize-gate was actually called — prevents the agent from
    # fabricating gate{N}_result.json + quality_manifest.json directly
    # without the harness running S3/S4 cross-validation.
    _missing_finalize: list[str] = []
    # Exit gate check (phase-level): Gate 2 for P3, Gate 3 for P4, Gate 4 for P6
    if completed_phase in _PHASE_EXIT_GATES:
        _exit_gate = _PHASE_EXIT_GATES[completed_phase]
        # v2.13: pass completed_phase so the path matches what finalize-gate
        # wrote (Bug #121 — no cross-phase sentinel reuse).
        _fs = _finalize_sentinel_path(project, _exit_gate, None, phase=completed_phase)
        if not _fs.exists():
            _missing_finalize.append(
                f"Gate {_exit_gate} (phase-exit) — expected {_fs.name}"
            )
    # Gate 1 per-FR check: every FR must have a finalized Gate 1 sentinel
    manifest_path = project / ".methodology" / "quality_manifest.json"
    _fr_ids_for_finalize: list[str] = []
    if manifest_path.exists():
        try:
            _fr_ids_for_finalize = json.loads(
                manifest_path.read_text(encoding="utf-8")
            ).get("fr_ids", [])
        except (json.JSONDecodeError, OSError):
            pass
    if completed_phase >= 3 and _fr_ids_for_finalize:
        _missing_fr_finalize: list[str] = []
        for _frid in _fr_ids_for_finalize:
            # v2.13: pass completed_phase so the path matches finalize-gate's
            # per-phase write (Bug #121).
            _fs = _finalize_sentinel_path(project, 1, _frid, phase=completed_phase)
            if not _fs.exists():
                # DELTA auto-skip exemption: if no code changed since last Gate 1,
                # the per-FR finalize step was never called (correctly). Skip check
                # for FRs where code hasn't changed — same logic as _check_gate1_live_coverage.
                try:
                    if not _fr_code_changed_since_last_gate1(_frid, project):
                        continue
                except Exception:
                    pass
                _missing_fr_finalize.append(_frid)
                _ = None  # appease pyright
        if _missing_fr_finalize:
            _missing_finalize.append(
                f"Gate 1 per-FR ({len(_missing_fr_finalize)} FRs): "
                + ", ".join(_missing_fr_finalize[:5])
                + (f" +{len(_missing_fr_finalize)-5} more" if len(_missing_fr_finalize) > 5 else "")
            )
    if _missing_finalize:
        print(
            "\n[BLOCKED] finalize-gate not called for required gate(s):\n"
            + "".join(f"  ✗ {m}\n" for m in _missing_finalize)
            + "\n  The agent must call finalize-gate (with S3/S4 cross-validation)\n"
            + "  before advance-phase. Fabricating gate{N}_result.json or\n"
            + "  quality_manifest.json without finalize-gate is not permitted.\n"
            + "  Run: python3 harness_cli.py finalize-gate --gate <N> --phase <P> --project ."
        )
        return 17

    # ── Gate 1 per-FR coverage check (FR-loop phases only) ───────────
    if completed_phase in _PHASES_WITH_GATE1_FR_CHECK:
        _rc = _check_gate1_live_coverage(project, completed_phase)
        if _rc != 0:
            return _rc

    # ── Phase Truth check (HR-11 ≥90%) ────────────────────────────────
    if completed_phase >= 3:
        try:
            from core.quality_gate.phase_truth_verifier import PhaseTruthVerifier
            verifier = PhaseTruthVerifier(str(project), completed_phase)
            truth_result = verifier.verify()
            if not truth_result["passed"]:
                score = truth_result.get("total_score", 0)
                print(f"\n[BLOCKED] Phase {completed_phase} truth = {score:.0f}% < 90% (HR-11)")
                print("  Fix gaps first, then re-run advance-phase.")
                return 11
            else:
                score = truth_result.get("total_score", 0)
                print(f"  [HR-11] Phase Truth = {score:.0f}% ≥ 90% ✓")
        except ImportError:
            print("  [WARN] PhaseTruthVerifier not available — skipping HR-11 check")
        except Exception as e:
            print(f"\n  [BLOCKED] Phase Truth check failed with unexpected error: {e}")
            print("  Please resolve this engineering exception before advancing.")
            return 11


    # ── Always-regenerate Phase{N}_STAGE_PASS.md ─────────────────────
    # The file is machine-generated from quality_manifest.json + state.json (no LLM).
    # Always regenerate (not just when missing) so a previously-committed stale
    # artifact (e.g. pre-d8fccea "always FAIL" content from older _generate_stage_pass
    # logic) gets refreshed on every advance-phase run. Stage the file only if
    # its content actually changed — avoids empty no-op commits when the logic
    # already produced the right bytes.
    _stage_pass_path = project / "00-summary" / f"Phase{completed_phase}_STAGE_PASS.md"
    _sp_gate = 4 if completed_phase >= 6 else 1
    _existing_bytes_hash: int | None = None
    if _stage_pass_path.exists():
        try:
            _existing_bytes_hash = hash(_stage_pass_path.read_bytes())
        except OSError:
            pass
    print(
        f"  [advance-phase] Regenerating Phase{completed_phase}_STAGE_PASS.md "
        f"from quality_manifest (gate {_sp_gate})"
    )
    _generate_stage_pass(project, _sp_gate, completed_phase)
    # Stage only if content changed — avoids touching git index when nothing
    # actually differs from what is already committed.
    if _stage_pass_path.exists():
        try:
            _new_bytes_hash = hash(_stage_pass_path.read_bytes())
        except OSError:
            _new_bytes_hash = None
        if _new_bytes_hash != _existing_bytes_hash:
            subprocess.run(
                ["git", "add", str(_stage_pass_path)],
                cwd=str(project), capture_output=True,
            )
            print(
                f"  [STAGE_PASS] content changed → staged {completed_phase} advance commit"
            )

    # ── Next-phase plan: must exist before advancing (Phase 3–7) ────
    # Prevents "advance first, plan later" ordering bugs. generate-next-plan
    # must be run BEFORE advance-phase so the agent has a plan to follow.
    # Phase 1-2 use HANDOVER.md entry flow; plan generation starts at Phase 3.
    # P8→P9 is exempt: Phase 9 (Maintenance) is ticket-driven — its plan is
    # a static playbook (phase9_plan.md, generated by plan-all), and the real
    # work plan materializes per-CR via cr-open, so no pre-advance plan gate.
    if 3 <= completed_phase < 8:
        _next_phase = completed_phase + 1
        _next_plan = project / ".methodology" / f"phase{_next_phase}_plan.md"
        if not _next_plan.exists():
            print(
                f"\n[BLOCKED] Phase{_next_phase}_plan.md not found.\n"
                f"  Run generate-next-plan BEFORE advance-phase:\n"
                f"    python3 harness_cli.py generate-next-plan --phase {_next_phase}"
                f" --project .\n"
                f"  Then re-run: python3 harness_cli.py advance-phase"
                f" --completed-phase {completed_phase} --project ."
            )
            return 15

    # ── Phase Auditor: full C1-C12 for all phases ────────────────────
    audit_rc = _run_phase_auditor(project, completed_phase)
    if audit_rc != 0:
        return audit_rc

    # ── WRITE_SCOPE guard: no orphan diagnostic scripts at the repo root ──
    # Mechanism (not agent self-discipline) that keeps debug artifacts out of the
    # source tree. A workflow advance agent once stranded _diag_constitution.py here
    # while diagnosing a constitution BLOCK; BLOCK the advance until it is cleaned.
    _orphans = _scope_violation_scripts(project)
    if _orphans:
        print(
            f"\n[BLOCKED] Scope violation: {len(_orphans)} untracked diagnostic "
            f"script(s) at the repo root:"
        )
        for _o in _orphans:
            print(f"  - {_o}")
        print(
            "  Debug/diagnostic artifacts must live under .sessi-work/tmp/ "
            "(gitignored). Move or delete them, then re-run advance-phase."
        )
        return 21

    # ── Constitution postflight: check current phase's own docs ──────
    # (all phases 1-8).  Closed-loop: each phase verifies its OWN
    # document quality before advancing, not the next phase's preflight.
    # Scans only the phase-specific directory (e.g. 01-requirements/),
    # NOT the entire project root — table/matrix/tracking docs in other
    # directories dilute keyword density and produce false positives.
    try:
        from core.quality_gate.constitution import run_constitution_check
        from core.quality_gate.constitution.profile import get_profile
        _phase_dir = ProjectLayout(project).get_phase_dir(completed_phase)
        _const_result = run_constitution_check(
            check_type="all", docs_path=str(_phase_dir),
            current_phase=completed_phase, check_mode="postflight",
        )
        _const_threshold = get_profile().composite_threshold(completed_phase)
        if not _const_result.passed:
            # Turn the opaque "security 20% < 65%" into an actionable list of the
            # exact keywords each failing dimension is missing. Without this the
            # fixing agent has to reverse-engineer the gap itself — which is what
            # drove agents to write throwaway diagnostic scripts into the repo root.
            from core.quality_gate.constitution.runner import missing_keywords
            _failing_dims: list[str] = []
            for _v in _const_result.violations:
                _d = _v.get("dimension")
                if _d and _d not in _failing_dims:
                    _failing_dims.append(_d)
            _dim_missing = {
                _d: missing_keywords(str(_phase_dir), _d, completed_phase)
                for _d in _failing_dims
            }
            print(
                f"\n[BLOCKED] Phase {completed_phase} constitution = "
                f"{_const_result.score:.0f}% "
                f"(threshold={_const_threshold:.0f}%), "
                f"violations={len(_const_result.violations)}"
            )
            for v in _const_result.violations[:5]:
                _vd = v.get("dimension", "?")
                _miss = _dim_missing.get(_vd) or []
                _suffix = f"  ·  missing: {', '.join(_miss)}" if _miss else ""
                print(f"  - [{_vd}] {v.get('message', str(v))[:120]}{_suffix}")
            _gap_str = "; ".join(
                f"{_d}: {', '.join(_kws)}"
                for _d, _kws in _dim_missing.items() if _kws
            )
            _gap_clause = (
                f" Add explicit, substantive coverage of these missing keywords — "
                f"{_gap_str}."
                if _gap_str else ""
            )
            print(
                f"\n  Re-dispatch Agent A to fix document quality:\n"
                f"    python harness_cli.py dispatch --role developer "
                f"--phase {completed_phase} --project . \\\n"
                f'      --prompt "Constitution check failed '
                f'(score {_const_result.score:.0f}%, '
                f'threshold {_const_threshold:.0f}%).'
                f'{_gap_clause} '
                f'Improve document quality to meet keyword coverage thresholds."'
            )
            return 16
        print(
            f"  [Constitution] Phase {completed_phase} postflight = "
            f"{_const_result.score:.0f}% (threshold={_const_threshold:.0f}%) ✓"
        )
    except ImportError:
        print("  [WARN] Constitution checker not available — skipping postflight")
    except Exception as _ce:
        print(f"  [WARN] Constitution postflight failed: {_ce}")

    # ── Agent B approvals (P1/P2/P6) — after C1 so deliverables confirmed ──
    if completed_phase in (1, 2, 6):
        deliverable_ids = _PHASE_DELIVERABLES.get(completed_phase, [])
        if deliverable_ids:
            passed_ab, report_ab = _verify_agent_b_approvals_core(
                project, completed_phase, deliverable_ids
            )
            if not passed_ab:
                print(f"\n[BLOCKED] Agent B approvals incomplete for Phase {completed_phase}:")
                print(report_ab)
                print(
                    "\n  Each deliverable needs "
                    ".methodology/agent_b_approvals/<id>.json "
                    "with review_status=APPROVE and "
                    "docs_embedded containing the required source documents."
                )
                return 13
            print(f"  [Agent B] Phase {completed_phase} approvals verified ✓")

    # ── TDD checks: pytest + coverage, spec-coverage (P3+) ──────
    # Return code map for this block (pre-existing codes occupy 1-17):
    #   17 → finalize-gate sentinel missing (see check above)
    #   18 → ruff: lint errors in src
    #   19 → mypy: type errors in src
    #   20 → gitleaks: hardcoded secrets detected
    if completed_phase >= 3:
        # 0.1 Secrets Scanning (gitleaks)
        # Runs outside src_dir.is_dir() intentionally: gitleaks scans the whole
        # repo (docs, configs, history), not just the source tree.
        if shutil.which("gitleaks"):
            try:
                _gl_r = subprocess.run(
                    ["gitleaks", "detect", "--source", "."],
                    cwd=str(project),
                    capture_output=True,
                    text=True,
                    timeout=get_timeout("gitleaks"),
                )
            except subprocess.TimeoutExpired:
                print("\n[BLOCKED] Secrets Scanning (gitleaks) timed out.")
                return 20
            if _gl_r.returncode != 0:
                print("\n[BLOCKED] Secrets Scanning (gitleaks) failure.")
                print("  Hardcoded secrets detected in the codebase/docs.")
                return 20
        else:
            print("  [WARN] gitleaks not installed. Skipping secrets scanning.")
        # Phase-based spec-coverage thresholds (unified v2.6)
        if completed_phase >= 6:
            sc_thresh = 90.0
        elif completed_phase >= 4:
            sc_thresh = 80.0
        else:
            sc_thresh = 60.0

        # 1. pytest + 100% coverage on TDD-governed source
        src_dir = ProjectLayout(project).active_src_dir
        if src_dir.is_dir():
            # 0.2 Linting (ruff)
            if shutil.which("ruff"):
                _rf_r = subprocess.run(["ruff", "check", ".", "--extend-ignore", "RUF001,RUF002,RUF003"], cwd=str(project))
                if _rf_r.returncode != 0:
                    print("\n[BLOCKED] Linting (ruff) failure.")
                    print("  Please fix the linting errors before advancing.")
                    return 18
            else:
                print("  [WARN] ruff not installed. Skipping linting.")

            # 0.3 Type Safety (mypy)
            if shutil.which("mypy"):
                _mp_r = subprocess.run([sys.executable, "-m", "mypy", ".", "--ignore-missing-imports"], cwd=str(project))
                if _mp_r.returncode != 0:
                    print("\n[BLOCKED] Type Safety (mypy) failure.")
                    print("  Please fix the type errors before advancing.")
                    return 19
            else:
                print("  [WARN] mypy not installed. Skipping type safety.")

            r = subprocess.run(
                [sys.executable, "-m", "pytest", "--tb=short", "-q",
                 "--cov=03-development/src", "--cov-fail-under=100"],
                cwd=str(project),
            )
            if r.returncode != 0:
                print("\n[BLOCKED] TDD test/coverage failure.")
                print("  100% coverage on 03-development/src required.")
                print("  For genuinely untestable lines add: # pragma: no cover")
                # P3-A: Python < 3.11 async coverage hint
                if sys.version_info < (3, 11):  # type: ignore[reportUnreachable]
                    print(
                        f"  [Python {sys.version_info.major}.{sys.version_info.minor} note] "
                        "async function bodies called via asyncio.run() may not be tracked."
                    )
                    print("  Add '# pragma: no cover' to the 'async def' line to exclude it.")
                return 9

        # 2. D4 traceability: TEST_SPEC.md → tests/ (spec-coverage — unified)
        #    TEST_SPEC.md is the single source of truth (v2.6).
        sc_rc, sc_pct = _run_spec_coverage_check(project, sc_thresh, verbose=True)
        if sc_rc != 0:
            print(f"\n[BLOCKED] spec-coverage {sc_pct:.1f}% < threshold {sc_thresh:.0f}%.")
            print("  Implement missing test cases from TEST_SPEC.md in tests/.")
            return 10

    # ── P2-A: SAB consistency pre-check (MEDIUM violations block advance) ────
    # Catches "architecture declared file X but not in codebase" before git push
    # fails.  Gives an actionable message + the specific missing files.
    if completed_phase >= 3:
        try:
            from detection.drift_detector import DriftDetector
            _dd = DriftDetector(str(project))
            _sab_result = _dd.detect_sab_drift()
            _sab_medium = [
                _item for _item in _sab_result.drift_items
                if _item.severity.value in ("MEDIUM", "HIGH", "CRITICAL")
                and "missing from codebase" in _item.description
            ]
            if _sab_medium:
                print(
                    f"\n[BLOCKED] SAB architecture violations — "
                    f"{len(_sab_medium)} declared file(s) missing from codebase:"
                )
                for _item in _sab_medium:
                    print(f"  [{_item.location}] expected: {_item.expected}")
                    print("    → Create the file OR remove its declaration from SAD.md")
                return 12
        except ImportError:
            print("  [WARN] DriftDetector not available — skipping SAB pre-advance check")
        except Exception as _sab_err:  # pylint: disable=broad-exception-caught
            print(f"  [WARN] SAB pre-advance check error: {_sab_err}")

    # ── P3-B: Phase 4+ integration package advisory (non-blocking) ───────────
    if completed_phase >= 3:
        _missing_pkgs = []
        for _pkg in ("fastapi", "httpx"):
            try:
                __import__(_pkg)
            except ImportError:
                _missing_pkgs.append(_pkg)
        if _missing_pkgs:
            print(
                f"\n[WARN] Phase {completed_phase + 1} integration packages not installed: "
                f"{', '.join(_missing_pkgs)}"
            )
            print(f"  Install: pip install {' '.join(_missing_pkgs)}")
            print("  (Non-blocking — integration tests will fail without these)")

    # ── Submodule guard (improvement E2) ───────────────────────────────
    # Detect uncommitted edits in harness/ submodule before `git submodule
    # update --remote` would silently clobber them. Hard-fail (exit 18) on
    # unsafe state. Silent skip when path is not a submodule (project-side
    # harness CLI uses pre_flight.check_submodule_safety directly).
    from core.pre_flight import check_submodule_safety
    _sub_safe, _sub_diag = check_submodule_safety(project / "harness")
    if not _sub_safe:
        print(f"\n[BLOCKED] {_sub_diag}")
        return 18

    # ── Submodule drift advisory (non-blocking) ──────────────────────
    _check_submodule_drift(project)

    return 0


_SCOPE_SCRIPT_EXTS: frozenset[str] = frozenset({".py", ".js", ".ts", ".sh"})
# Precision > recall by design (see _scope_violation_scripts docstring): this is
# a small, explicit set of tokens, matched whole (not as a substring) against
# "_"/"-"-separated segments of the filename stem — "_diag_constitution" splits
# to ["diag", "constitution"], an exact hit; "swipe" or "attempt" never match
# "wip"/"tmp" as a substring would. Whole-token matching makes it safe to extend
# this set (no accidental substring collisions to reason about), unlike a raw
# substring/regex search.
_SCOPE_DEBUG_NAME_TOKENS: frozenset[str] = frozenset({
    "diag", "debug", "scratch", "explore", "probe", "tmp",
    "sandbox", "throwaway", "adhoc", "wip", "poc",
})


def _scope_debug_name_match(stem: str) -> bool:
    tokens = re.split(r"[_\-\s]+", stem.lower())
    return any(t in _SCOPE_DEBUG_NAME_TOKENS for t in tokens)


def _scope_violation_scripts(project: Path) -> list[str]:
    """Untracked diagnostic/debug scripts stranded at the repo root.

    WRITE_SCOPE convention: agent-generated debug artifacts belong under
    .sessi-work/tmp/ (gitignored), never the source tree. A workflow advance agent
    once left _diag_constitution.py at the repo root while diagnosing a constitution
    BLOCK. This is the mechanism that catches such orphans (the per-phase self-clean
    prompt rule only reduces their frequency; it relies on the agent complying).

    Narrow, high-precision pattern to avoid false positives that would halt the
    pipeline: untracked (git ??) AND top-level (no path separator — recursing would
    flag legitimate new module files not yet committed mid-phase) AND a script
    extension AND a name signalling a diagnostic. .sessi-work/ is gitignored, so its
    contents never surface as untracked and are never flagged.

    Uses `-z` (NUL-terminated, unquoted paths): without it, `git status --porcelain`
    quotes any path containing a space or non-ASCII character (core.quotePath), so
    e.g. "diag tool.py" comes back as the literal 13-char string `"diag tool.py"`
    (quotes included) — Path(...).suffix is then '.py"', which never matches
    _SCOPE_SCRIPT_EXTS and the file silently evades detection.

    `--untracked-files=normal` (git's default) rather than `=all`: an untracked
    directory is reported once (`?? dirname/`) instead of git recursing into and
    listing every file inside it — those entries would all be discarded by the
    top-level-only filter below anyway, so `=all` only adds wasted work on a large
    untracked tree (e.g. a not-yet-gitignored build/venv dir) with no behavior
    difference for the loose top-level files this check actually targets.
    """
    result = subprocess.run(
        ["git", "-C", str(project), "status", "--porcelain=v1", "-z",
         "--untracked-files=normal"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        return []
    offenders: list[str] = []
    for entry in result.stdout.split("\0"):
        if not entry.startswith("??"):
            continue
        path = entry[3:]
        if "/" in path:  # top-level only
            continue
        p = Path(path)
        if p.suffix.lower() in _SCOPE_SCRIPT_EXTS and _scope_debug_name_match(p.stem):
            offenders.append(path)
    return offenders


def _advance_commit_targets(
    completed_phase: int,
    next_phase: int,
    manifest_regenerated: bool,
    fr_progress_exists: bool,
    gate_timestamps_exists: bool = False,
    stage_pass_exists: bool = False,
) -> list[str]:
    """Files the advance-phase local commit must stage.

    Uses an explicit list (not `git add -A`) so unrelated working-tree noise is
    not swept in. fr_progress.json is rewritten by _advance_fsm during this same
    advance, so it must be staged — but only when present: pre-Gate-1 advances
    (P1->P2, P2->P3) have no fr_progress.json yet, and an explicit `git add` of a
    missing pathspec fails the whole commit.

    gate_timestamps.jsonl is functional FR-gate state (read back to verify per-FR
    gate events) that the DELTA fast-path appends within a phase; the advance
    commit sweeps its tail so it does not linger unstaged after every phase bump.
    Conditional-exists for the same missing-pathspec reason as fr_progress.json.

    00-summary/Phase{N}_STAGE_PASS.md is machine-generated by _generate_stage_pass
    on every advance-phase run (always-regenerate). It is staged here too so a
    single `git add` in the advance commit covers it — even if the earlier
    conditional git-add at line ~6372 was skipped because content matched the
    already-committed bytes.
    """
    targets = [
        ".methodology/state.json", "HANDOVER.md",
        "CLAUDE.md",
        f".methodology/phase{completed_phase}_plan.md",
    ]
    if fr_progress_exists:
        targets.append(".methodology/fr_progress.json")
    if gate_timestamps_exists:
        targets.append(".methodology/gate_timestamps.jsonl")
    if manifest_regenerated:
        targets.append(".methodology/quality_manifest.json")
    if stage_pass_exists:
        targets.append(f"00-summary/Phase{completed_phase}_STAGE_PASS.md")
    if next_phase == 8:
        targets += ["08-config/CONFIG_RECORDS.md", "08-config/RELEASE_CHECKLIST.md"]
    return targets


def cmd_advance_phase(args: argparse.Namespace) -> int:
    """Advance to next phase: update state.json atomically.

    Calls _advance_fsm() which:
      1. Writes .methodology/state.json (current_phase = completed + 1) — the
         single source of truth read by hooks and CI.

    After FSM advance, regenerates HANDOVER.md so crash-recovery always
    reflects the current phase, then commits locally (no push — next
    milestone push will publish to origin).

    Usage:
        python harness_cli.py advance-phase --completed 3   # advances to phase 4
    """
    # Preserve CWD — if any Python code in this process changes directory
    # (e.g. os.chdir in a hook or library), restore it before returning.
    # Subprocess calls (git -C, claude -p) do NOT change the parent CWD.
    _saved_cwd = os.getcwd()
    project = Path(args.project).resolve()

    # Phase 9 (Maintenance) is a terminal steady state: work happens as
    # re-entrant CR tickets (cr-open/cr-close), never as a phase exit.
    if args.completed_phase >= 9:
        print(
            "\n[BLOCKED] advance-phase: Phase 9 (Maintenance) is a terminal "
            "steady state — there is no Phase 10.\n"
            "  Maintenance work is ticket-driven and re-entrant:\n"
            "    python3 harness_cli.py cr-open --type bug|feat --title ... --project .\n"
            "    python3 harness_cli.py cr-close --cr CR-NN --project .",
            file=sys.stderr,
        )
        return 2

    # CV-2: Validate args.completed_phase against state.json::current_phase.
    #
    # Three cases:
    #   1. current == completed  → normal advance (run prechecks, advance FSM)
    #   2. current >  completed  → re-verify (run prechecks only, no FSM change)
    #   3. current <  completed  → skip attempt — BLOCKED (prevent phase skips)
    state_path = project / ".methodology" / "state.json"
    if state_path.exists():
        try:
            # B4 (CV-2): hold the state lock for the read so a concurrent
            # advance-phase process cannot write between our read and the check.
            with file_lock(state_lock_path(project)):
                _state = json.loads(state_path.read_text(encoding="utf-8"))
            _current = int(_state.get("current_phase", 0))

            if _current and _current > args.completed_phase:
                # Re-verify mode: Phase N was already advanced past. Re-run
                # exit checks so the user can fix document quality at the
                # correct phase boundary without hacking state.json.
                # Does NOT change current_phase or write state.
                print(
                    f"\n[RE-VERIFY] Phase {args.completed_phase} already advanced "
                    f"(current_phase={_current}). Re-running exit checks…"
                )
                rc = _advance_prechecks(project, args.completed_phase)
                if rc != 0:
                    print(
                        f"\n[BLOCKED] Phase {args.completed_phase} exit checks "
                        f"failed (code={rc}). Fix issues above, then re-run:\n"
                        f"    python3 harness_cli.py advance-phase "
                        f"--completed {args.completed_phase} --project {project}"
                    )
                    return rc
                print(
                    f"\n[RE-VERIFY] Phase {args.completed_phase} exit checks "
                    f"re-verified ✓ (already at Phase {_current})"
                )
                return 0

            if _current and _current < args.completed_phase:
                # Skip attempt: agent tried to jump ahead
                print(
                    f"\n[BLOCKED] advance-phase: --completed={args.completed_phase} "
                    f"is ahead of state.json::current_phase={_current}.\n"
                    f"  This prevents accidental phase skips. To advance, use:\n"
                    f"    python3 harness_cli.py advance-phase --completed {_current} --project {project}",
                    file=sys.stderr,
                )
                return 2
            # Check phase_truth_passed for phases with exit gates
            if args.completed_phase in _PHASE_EXIT_GATES:
                _req_gate = _PHASE_EXIT_GATES[args.completed_phase]
                _passed = _state.get("phase_truth_passed")
                _last_gate = _state.get("last_gate")
                # P5-BUG-02 defense: Ensure both phase_truth_passed and the last_gate match the exit gate
                if not _passed or _last_gate != _req_gate:
                    print(
                        f"\n[BLOCKED] advance-phase: phase_truth_passed not recorded "
                        f"in state.json for Phase {args.completed_phase}.\n"
                        f"  Run: python harness_cli.py finalize-gate "
                        f"--gate {_PHASE_EXIT_GATES[args.completed_phase]} "
                        f"--phase {args.completed_phase} --project {project}\n"
                        f"  and ensure Phase Truth ≥ 90% before advancing.",
                        file=sys.stderr,
                    )
                    # Exit 12 = phase_truth_passed missing in state.json.
                    # Distinct from exit 11 (Phase Truth score < 90%) so pipeline
                    # automation and humans can apply the correct remediation:
                    #   11 → re-run Phase Truth until score ≥ 90%
                    #   12 → run finalize-gate for the exit gate of this phase
                    return 12
        except (ValueError, OSError, json.JSONDecodeError) as exc:
            print(
                f"  [WARN] Could not read state.json::current_phase for validation: {exc} — proceeding.",
                file=sys.stderr,
            )

    next_phase = args.completed_phase + 1

    # Look up gate/FR state from quality_manifest.json for accurate state.json
    manifest_path = project / ".methodology" / "quality_manifest.json"
    manifest = {}
    last_gate_num = None
    last_fr_id = None
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:  # pylint: disable=broad-exception-caught
            pass

    gate_results = manifest.get("gate_results", {})
    for gn in (4, 3, 2, 1):
        gv = gate_results.get(f"gate{gn}")
        if isinstance(gv, dict) and gv.get("quality_complete"):
            last_gate_num = gn
            break

    gate1 = gate_results.get("gate1", {})
    if isinstance(gate1, dict):
        for fr_id in manifest.get("fr_ids", []):
            if isinstance(gate1.get(fr_id), dict) and gate1[fr_id].get("quality_complete"):
                last_fr_id = fr_id

    gate_score_str = ""
    if last_gate_num and isinstance(gate_results.get(f"gate{last_gate_num}"), dict):
        _gscore = gate_results[f"gate{last_gate_num}"].get("score", "")
        if _gscore:
            gate_score_str = f" (score={_gscore})"

    fr_done = len([f for f in manifest.get("fr_ids", [])
                   if isinstance(gate1, dict)
                   and isinstance(gate1.get(f), dict)
                   and gate1[f].get("quality_complete")])
    fr_total = len(manifest.get("fr_ids", []))

    task_bg = (f"Phase transition from Phase {args.completed_phase} to Phase {next_phase}."
               if not fr_total else
               f"Phase {args.completed_phase} complete ({fr_done}/{fr_total} FRs Gate 1 PASS). "
               f"Gate {last_gate_num}{gate_score_str}. Advancing to Phase {next_phase}.")

    status = (f"Phase {args.completed_phase} completed. Ready to begin Phase {next_phase}."
              if not fr_total else
              f"Phase {args.completed_phase}: {fr_done}/{fr_total} FRs Gate 1 PASS. "
              f"Gate {last_gate_num}{gate_score_str} — quality_complete. "
              f"Ready to begin Phase {next_phase}.")

    # ── Pre-advance checks ────────────────────────────────────────────
    rc = _advance_prechecks(project, args.completed_phase)
    if rc != 0:
        return rc

    print(f"\n[advance-phase] Completed phase {args.completed_phase} → advancing to {next_phase}")
    _advance_fsm(project, args.completed_phase,
                 last_gate=last_gate_num, last_fr=last_fr_id)
    _update_claude_md(project)               # phase number just changed → refresh CLAUDE.md
    _llm_clean_stale_claude_md(project)      # remove stale manual harness status text

    # Generate CRG wiki on P3+ advance (architecture docs for agents, incremental).
    # Driven via the code-review-graph CLI so it works in any environment — the old
    # mcp_tools import only existed inside interactive Claude Code and silently no-op'd,
    # so .code-review-graph/wiki/ was never produced.
    if args.completed_phase >= 2:
        _crg_bin = shutil.which("code-review-graph")
        if _crg_bin:
            try:
                subprocess.run(
                    [_crg_bin, "wiki", "--repo", str(project)],
                    check=True, capture_output=True, text=True, timeout=get_timeout("subprocess"),
                )
                print("  [CRG] Wiki updated → .code-review-graph/wiki/")
            except Exception as _w:  # non-blocking, but surface the reason (no silent pass)
                print(f"  [CRG] Wiki skipped: {_w}")

    # CV-13: Stale .sessi-work/ artifacts can cause the next phase's gate
    # evaluation to skip re-computation (agent sees old result JSONs and
    # assumes they are current). Clean aggressively at every phase transition.
    # Bug #H fix: preserve .sessi-work/sentinels/ — those files are the
    # gate-completion evidence consumed by the next phase's
    # validate-handoff (g1_fr01.flag etc.). Wiping them caused every
    # advance-phase to drop Gate 1 evidence, so the precondition check
    # on the next phase always reported "sentinel missing" even after
    # a successful Gate 1 finalize.
    sessi_work = project / ".sessi-work"
    sentinels_dir = sessi_work / "sentinels"
    _sentinels_backup: Optional[Path] = None
    # Bug H1 fix: wrap backup→rm→restore in try/finally so the temp dir is
    # cleaned up even if shutil.rmtree / copytree raises a non-OSError
    # (KeyboardInterrupt, RuntimeError, etc.) that ignore_errors won't swallow.
    try:
        if sentinels_dir.is_dir():
            _sentinels_backup = Path(tempfile.mkdtemp(prefix="harness-sentinels-"))
            shutil.copytree(sentinels_dir, _sentinels_backup / "sentinels")
        if sessi_work.is_dir():
            shutil.rmtree(sessi_work, ignore_errors=True)
            print(f"  [advance-phase] Cleared stale {sessi_work}")
        if _sentinels_backup is not None:
            sentinels_dir.mkdir(parents=True, exist_ok=True)
            shutil.copytree(_sentinels_backup / "sentinels", sentinels_dir, dirs_exist_ok=True)
            _n = sum(1 for _ in sentinels_dir.iterdir() if _.is_file())
            print(f"  [advance-phase] Preserved {_n} sentinel(s) under {sentinels_dir}")
    finally:
        if _sentinels_backup is not None:
            shutil.rmtree(_sentinels_backup, ignore_errors=True)

    # Fix Finding #3: auto-regenerate quality_manifest.json at P2 exit.
    #
    # P2 plan delegates to scripts/generate_sab.py (writes SAB.json only) but
    # never re-invokes `harness_cli.py manifest` to update quality_manifest.json
    # with the fresh SAD-derived data (nfr_dim_map, high_risk_modules,
    # gate_score_overrides). P3 entry checks "manifest exists" and may use the
    # stale P1 manifest, causing downstream gate checks to score against the
    # wrong dimension floors. Re-run the manifest generator here using the
    # fresh SAD.md so P3/P4/P5 phases see current data.
    #
    # Best-effort: skip with WARNING if SAD.md is missing (caller can re-run
    # `harness_cli.py manifest` manually). Surface the reason rather than
    # silent-skip — we have been bitten by silent skips before.
    _manifest_regenerated = False
    if args.completed_phase == 2:
        sad_path = ProjectLayout(project).sad_path
        if sad_path.exists():
            try:
                from harness.harness_bridge import HarnessBridge
                # Reuse fr_ids from current manifest, fall back to SRS.md scan
                _mf_path = project / ".methodology" / "quality_manifest.json"
                _fr_ids: list[str] = []
                if _mf_path.exists():
                    try:
                        _fr_ids = json.loads(
                            _mf_path.read_text(encoding="utf-8")
                        ).get("fr_ids", [])
                    except Exception:  # pylint: disable=broad-exception-caught
                        pass
                if not _fr_ids:
                    # Fallback: scan SRS.md for FR markers. Match "### FR-XX" headers
                    # (separator can be `:`, `—`, `-`, `|`, or whitespace after the
                    # number) and table rows "| FR-XX | ...". Previous regex required
                    # `\s*:|\s*|` after the digits, which silently dropped SRS files
                    # using em-dash (`### FR-01 — ...`) — leaving fr_ids empty and
                    # tripping the manifest-integrity pre-flight (Bug #140).
                    import re as _re_fr
                    _srs = ProjectLayout(project).srs_path
                    if _srs.exists():
                        _fr_ids = [
                            f"FR-{n}" for n in _re_fr.findall(
                                r"^(?:###\s+FR-|\|\s*FR-)(\d+)\b",
                                _srs.read_text(encoding="utf-8"),
                                _re_fr.MULTILINE,
                            )
                        ]
                # Fail-fast guard: if both seed manifest and SRS regex
                # produce zero FRs, do NOT silently call
                # generate_quality_manifest with an empty list. An empty
                # manifest passes the regeneration print, then trips
                # preflight Pattern A in P3 — the failure surfaces far
                # from its cause. Refuse the advance locally instead so
                # the user fixes the SRS format / fr_ids injection at
                # the point of failure. (Bug #140 hardened the regex;
                # this guards the malformed-SRS case the regex now
                # correctly reports as zero matches.)
                if not _fr_ids:
                    print(
                        f"  [P2→P3] manifest regeneration REFUSED: "
                        f"fr_ids is empty (no seed in quality_manifest.json "
                        f"and no FR markers matched in SRS.md).\n"
                        f"    Fix one of:\n"
                        f"      - inject fr_ids into quality_manifest.json\n"
                        f"    (recommended: pre-populate via `harness_cli.py "
                        f"manifest --fr-ids FR-XX ... --sad {sad_path}`)\n"
                        f"      - repair SRS.md so FR headers are detectable "
                        f"by `^(?:###\\s+FR-|\\|\\s*FR-)(\\d+)\\b`",
                        file=sys.stderr,
                    )
                    return 2
                _bridge = HarnessBridge()
                _out = _bridge.generate_quality_manifest(
                    fr_ids=_fr_ids,
                    sad_path=str(sad_path),
                    project_root=str(project),
                    force=True,
                )
                print(
                    f"  [P2→P3] quality_manifest.json regenerated → {_out} "
                    f"({len(_fr_ids)} FRs, generated_at_phase=2)"
                )
                _manifest_regenerated = True
            except Exception as _m:  # pylint: disable=broad-exception-caught
                print(
                    f"  [P2→P3] manifest regeneration FAILED: {_m}\n"
                    f"    P3 entry will use stale P1 manifest. Fix and run:\n"
                    f"    python3 harness_cli.py manifest "
                    f"--fr-ids {' '.join(_fr_ids)} --sad {sad_path}",  # type: ignore[reportPossiblyUnboundVariable]
                    file=sys.stderr,
                )
        else:
            print(
                f"  [P2→P3] {sad_path} not found — manifest regeneration skipped.\n"
                f"    P3 entry will use the existing manifest. Create SAD.md and run:\n"
                f"    python3 harness_cli.py manifest --fr-ids FR-XX [...] --sad {sad_path}",
                file=sys.stderr,
            )

    # P7→P8: deterministic baseline for CONFIG_RECORDS.md / RELEASE_CHECKLIST.md.
    # LLM agents had been authoring these from scratch and stalling in P8 (4 stalls
    # in the workflow record before this change). The deterministic generator
    # builds both files from state.json + quality_manifest.json + git state;
    # the LLM agent that runs P8 can then review and append human-only context
    # instead of re-deriving the whole structure.
    if next_phase == 8:
        try:
            from scripts.phase8_doc_gen import generate as _p8_generate
            _p8_result = _p8_generate(project)
            print(
                f"  [P7→P8] CONFIG_RECORDS.md + RELEASE_CHECKLIST.md generated → "
                f"{_p8_result['config_path'].parent}"
            )
        except Exception as _p8e:  # pylint: disable=broad-exception-caught
            print(
                f"  [P7→P8] phase8_doc_gen failed: {_p8e}\n"
                f"    P8 entry will rely on LLM generation. Investigate:\n"
                f"    python3 scripts/phase8_doc_gen.py --project {project}",
                file=sys.stderr,
            )

    gen = HandoverGenerator(project)
    gen.write(
        checkpoint_id=f"P{next_phase}-entry-{datetime.now(timezone.utc).strftime('%Y%m%d')}",
        phase=next_phase,
        task_background=task_bg,
        current_status=status,
        next_steps=[
            f"Follow SKILL.md §0.1 Phase {next_phase} entry checklist",
            f"Read the Phase {next_phase} plan and execute",
        ],
        resume_phase=next_phase,
    )

    # Commit locally (no push — next milestone push publishes to origin)
    if os.environ.get("HARNESS_NO_GIT"):
        print("[advance-phase] HARNESS_NO_GIT=1 — skipping git commit")
    else:
        # Fix Finding #3: include regenerated quality_manifest.json in commit when
        # P2→P3 just regenerated it, so the advance commit captures the fresh data
        # atomically (state.json + manifest). Without this, the regenerated file
        # would only land in the next push, leaving a window where CI sees stale
        # manifest.
        _add_targets = _advance_commit_targets(
            args.completed_phase, next_phase, _manifest_regenerated,
            (project / ".methodology" / "fr_progress.json").exists(),
            (project / ".methodology" / "gate_timestamps.jsonl").exists(),
            (project / "00-summary" / f"Phase{args.completed_phase}_STAGE_PASS.md").exists(),
        )
        add_result = subprocess.run(
            ["git", "-C", str(project), "add", *_add_targets],
            capture_output=True, text=True,
        )
        if add_result.returncode != 0:
            print(f"[advance-phase] WARN: git add failed — {add_result.stderr.strip()}")
        else:
            commit_result = subprocess.run(
                ["git", "-C", str(project), "commit", "-m",
                 f"handover: advance to Phase {next_phase}"],
                capture_output=True, text=True,
            )
            if commit_result.returncode == 0:
                print("[advance-phase] Committed HANDOVER.md + state.json locally.")
            elif "nothing to commit" in (commit_result.stdout + commit_result.stderr):
                print("[advance-phase] Nothing to commit (already clean).")
            else:
                print(f"[advance-phase] WARN: git commit failed — {commit_result.stderr.strip()}")

    print(f"[advance-phase] Done — local hooks and CI now target phase {next_phase}")
    # Restore CWD if any internal Python code (hook, library) changed it.
    # Subprocess calls do NOT change the parent process CWD.
    try:
        if os.getcwd() != _saved_cwd:
            os.chdir(_saved_cwd)
            print(f"[advance-phase] CWD restored to {_saved_cwd}")
    except OSError:
        pass
    return 0

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

# Commit patterns for idempotency check — must match git_strategy.py commit messages.
_FR_STEP_COMMIT_PATTERNS: dict[str, str] = {
    "TDD-RED":     "test(RED): failing test for {fr_id}",
    "TDD-GREEN":   "feat({fr_id}): GREEN",
    "TDD-IMPROVE": "refactor({fr_id}): IMPROVE",
    "GATE1":       "feat({fr_id}): Gate1 PASS",         # prefix match; phase-scoped
    "GATE1-DELTA": "feat({fr_id}): Gate1 PASS",         # same prefix + git diff check
}


def _fr_step_already_done(step: str, fr_id: str, project: Path) -> bool:
    """Idempotency check: scan git log for step's expected commit pattern.

    For GATE1-DELTA: additionally checks whether FR code has changed since
    the last Gate 1 PASS commit. If code changed, returns False so the
    step re-runs with a full evaluation (not a delta-skip).

    Returns True if the step can be safely skipped (crash recovery / no-change).
    """
    import subprocess as _sp
    tmpl = _FR_STEP_COMMIT_PATTERNS.get(step.upper(), "")
    if not tmpl:
        return False
    pattern = tmpl.format(fr_id=fr_id)
    r = _sp.run(
        ["git", "log", "--oneline", "--grep", pattern],
        capture_output=True, text=True, cwd=str(project),
    )
    committed = bool(r.stdout.strip())
    if not committed:
        return False

    # GATE1 / GATE1-DELTA: commit pattern alone is insufficient — a "Gate1 PASS"
    # commit may have been written with a fabricated or sub-threshold score (e.g.
    # 0.0 or 66.0). Verify the recorded score actually meets the project threshold
    # before treating this step as done. Threshold is read from quality_targets
    # (min_coverage in quality_manifest.json) with 80.0 as the fallback default.
    if step.upper() in ("GATE1", "GATE1-DELTA"):
        _manifest_path = project / ".methodology" / "quality_manifest.json"
        try:
            _manifest = json.loads(_manifest_path.read_text(encoding="utf-8"))
            _threshold = float(
                _manifest.get("quality_targets", {}).get("min_coverage", 80.0)
            )
            _score = float(
                _manifest.get("gate_results", {})
                .get("gate1", {}).get(fr_id, {}).get("score", 0.0)
            )
            if _score < _threshold:
                return False   # commit exists but score below threshold → re-run
        except (OSError, json.JSONDecodeError, ValueError, AttributeError):
            return False       # manifest unreadable → re-run to be safe

    # GATE1-DELTA: code-change detection (not just commit-pattern check)
    if step.upper() == "GATE1-DELTA":
        return not _fr_code_changed_since_last_gate1(fr_id, project)

    # Dual verification for TDD
    if step.upper() == "TDD-RED":
        num_str = _fr_num_str(fr_id)
        test_dir = ProjectLayout(project).active_test_dir
        test_file = test_dir / f"test_fr{num_str}.py"
        return test_file.exists()
    elif step.upper() == "TDD-GREEN":
        src_dir = ProjectLayout(project).active_src_dir
        if not src_dir.exists():
            return False
        num_str = _fr_num_str(fr_id)
        for py_file in src_dir.glob("**/*.py"):
            if num_str in py_file.name:
                return True
            try:
                if f"[{fr_id}]" in py_file.read_text(encoding="utf-8"):
                    return True
            except Exception:
                pass
        return False
    return True


def _fr_gate1_commit_sha(fr_id: str, project: Path) -> str | None:
    """Return the SHA of the most recent Gate 1 PASS commit for the given FR."""
    import subprocess as _sp
    pattern = f"feat({fr_id}): Gate1 PASS"
    r = _sp.run(
        ["git", "log", "--oneline", "--grep", pattern, "-1", "--format=%H"],
        capture_output=True, text=True, cwd=str(project),
    )
    sha = r.stdout.strip()
    if sha:
        return sha
    # Fallback: P3 batch commit e.g. "feat(P3-mid): 8/8 FR(s) Gate1 PASS"
    r2 = _sp.run(
        ["git", "log", "--oneline", "--grep", "Gate1 PASS", "-1", "--format=%H"],
        capture_output=True, text=True, cwd=str(project),
    )
    sha2 = r2.stdout.strip()
    return sha2 if sha2 else None


def _fr_code_changed_since_last_gate1(fr_id: str, project: Path) -> bool:
    """Check whether FR source/test files have changed since last Gate 1 PASS.

    Returns True if code has changed (re-evaluation needed), False otherwise.
    Uses AST parsing to accurately determine if changed lines overlap with FR functions.
    """
    import subprocess as _sp
    import ast
    sha = _fr_gate1_commit_sha(fr_id, project)
    if sha is None:
        return True  # No prior Gate 1 PASS → treat as changed

    # 1. Check test files directly
    fr_files: list[str] = []
    num_match = re.match(r"FR-(\d+)", fr_id)
    num_str = num_match.group(1).zfill(2) if num_match else ""
    if num_str:
        for p in _git_test_patterns(project, num_str, str(int(num_str))):
            fr_files.append(p)
            
    r_test = _sp.run(
        ["git", "diff", "--name-only", sha, "HEAD", "--"] + fr_files,
        capture_output=True, text=True, cwd=str(project),
    )
    if r_test.stdout.strip():
        return True

    # 2. Check source files via AST diff overlap
    r_src = _sp.run(
        ["git", "diff", "--name-only", sha, "HEAD", "--", "03-development/src"],
        capture_output=True, text=True, cwd=str(project),
    )
    changed_src = [f for f in r_src.stdout.splitlines() if f.endswith(".py")]
    
    for py_file in changed_src:
        curr_path = project / py_file

        if not curr_path.exists():
            continue

        try:
            content = curr_path.read_text(encoding="utf-8")
            if f"[{fr_id}]" not in content:
                continue

            tree = ast.parse(content)
            fr_ranges = []
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    doc = ast.get_docstring(node)
                    if doc and f"[{fr_id}]" in doc:
                        fr_ranges.append((node.lineno, getattr(node, "end_lineno", node.lineno)))

            if not fr_ranges:
                # string is in file but not in a docstring, default to changed
                return True

            # Single -U0 diff for both removed-tag check and hunk line parsing
            r_u0 = _sp.run(
                ["git", "diff", "-U0", sha, "HEAD", "--", py_file],
                capture_output=True, text=True, cwd=str(project),
            )
            # Fallback: tag was removed in the diff
            if f"[{fr_id}]" in r_u0.stdout:
                return True
            for line in r_u0.stdout.splitlines():
                if line.startswith("@@ "):
                    # @@ -old,n +new,n @@
                    try:
                        parts = line.split(" ")[2].split(",")
                        start_line = int(parts[0].lstrip("+"))
                        count = int(parts[1]) if len(parts) > 1 else 1
                        end_line = start_line + count - 1

                        for (fr_start, fr_end) in fr_ranges:
                            # Overlap check
                            if start_line <= fr_end and end_line >= fr_start:
                                return True
                    except Exception:
                        pass
        except Exception:
            # On parse error, fail safe
            return True

    return False


def _extract_srs_fr_section(srs_path: Path, fr_id: str) -> str:
    """Extract a single FR's full markdown section from SRS.md.

    Returns text between '### FR-XX: ...' header and the next '### FR-' or '---'.
    Falls back to empty string if the section is not found.
    """
    if not srs_path or not srs_path.exists():
        return ""
    content = srs_path.read_text(encoding="utf-8")
    pat = re.compile(
        rf"(### {re.escape(fr_id)}:[^\n]+\n)(.*?)(?=\n---\n|\n### FR-\d+|$)",
        re.DOTALL,
    )
    m = pat.search(content)
    return (m.group(1) + m.group(2)).strip() if m else ""


def _parse_gate_output(out: str) -> tuple[bool, list, str]:
    """Extract gate_pass, failing_dims, and block_reason from sub-agent output.

    Tries full-string JSON parse first, then scans for embedded JSON objects
    by tracking brace depth — handles nested structures in failing_dims.
    Also scans for finalize-gate [BLOCKED] lines to surface S3/S4 details.

    Returns (gate_pass, failing_dims, block_reason).
    block_reason is a non-empty string when finalize-gate blocked with S3/S4;
    empty string otherwise.  Falls back to (False, [], "") on parse failure.
    """
    def _try(s: str) -> dict | None:
        try:
            obj = json.loads(s)
            if isinstance(obj, dict) and "pass" in obj:
                return obj
        except (json.JSONDecodeError, ValueError):
            pass
        return None

    def _extract_dims(obj: dict) -> list:
        # Accept both the prompt-specified key ("failing_dims") and the score.py
        # schema key ("failing_dimensions") — agents sometimes copy the wrong one.
        return obj.get("failing_dims") or obj.get("failing_dimensions") or []

    def _extract_block_reason(text: str) -> str:
        """Scan agent output for finalize-gate [BLOCKED] lines (S3/S4 errors)."""
        for line in text.splitlines():
            if "[BLOCKED]" in line and (
                "tool_evidence_missing" in line or "tool_score_fabrication" in line
            ):
                return line.strip()
        return ""

    block_reason = _extract_block_reason(out)

    # Try whole string first (agent returned bare JSON)
    obj = _try(out.strip())
    if obj:
        return bool(obj.get("pass", False)), _extract_dims(obj), block_reason

    # Scan for any embedded JSON object via brace-depth tracking
    i = 0
    while i < len(out):
        if out[i] == "{":
            depth = 0
            for j in range(i, len(out)):
                if out[j] == "{":
                    depth += 1
                elif out[j] == "}":
                    depth -= 1
                    if depth == 0:
                        obj = _try(out[i : j + 1])
                        if obj is not None:
                            return bool(obj.get("pass", False)), _extract_dims(obj), block_reason
                        break
        i += 1

    return False, [], block_reason


def _resolve_phase3_context(project: Path) -> dict:
    """Resolve MCP config and CLAUDE.md settings for Phase 3+ sub-agents.

    Auto-detects whether code-review-graph MCP tools and project CLAUDE.md
    are available, returning appropriate values for AgentSpawner.spawn().
    Gracefully degrades: if nothing is found, returns current defaults
    (no MCP, no CLAUDE.md).

    Returns:
        dict with keys:
            mcp_config: str | None  -- relative path to .mcp.json, or None
            setting_sources: str    -- "project" or ""
    """
    import shutil as _shutil
    result: dict[str, str | None] = {"mcp_config": None, "setting_sources": ""}

    # MCP: only enable if uvx is on PATH (required by our .mcp.json)
    if _shutil.which("uvx"):
        for candidate in ["harness/.mcp.json", ".mcp.json"]:
            if (project / candidate).exists():
                result["mcp_config"] = candidate
                break

    # CLAUDE.md: load if it exists at project root
    if (project / "CLAUDE.md").exists():
        result["setting_sources"] = "project"

    return result


def _extract_test_spec_names(project: Path, fr_id: str) -> tuple[list[str], str]:
    """Parse TEST_SPEC.md and return (test_names, formatted_note) for a given FR.

    Returns ([], "") when TEST_SPEC.md is missing or has no entries for this FR.
    """
    test_spec_path = ProjectLayout(project).test_spec_path
    if not test_spec_path.exists():
        return [], ""

    spec_text = test_spec_path.read_text(encoding="utf-8")
    current_fr = ""
    spec_rows: list[str] = []
    for line in spec_text.splitlines():
        stripped = line.strip()
        m = re.match(r"^###\s+([A-Z]+-\d+)(?:[:\s]|$)", stripped)
        if m:
            current_fr = m.group(1)
            continue
        if current_fr != fr_id:
            continue
        if "Test Function" in stripped:
            continue
        if stripped.startswith("|") and stripped.endswith("|"):
            cols = [c.strip() for c in stripped.split("|")[1:-1]]
            if len(cols) >= 2:
                clean_col = cols[1].strip(" `")
                if clean_col.startswith("test_"):
                    spec_rows.append(clean_col)
            continue
    if spec_rows:
        note = (
            f"\n[TEST SPEC — match these EXACT names]\n"
            f"TEST_SPEC.md at `02-architecture/TEST_SPEC.md` defines "
            f"{len(spec_rows)} test cases for {fr_id}. Write ALL of them "
            f"using these EXACT function names:\n"
            + "\n".join(f"  - {fn}" for fn in spec_rows)
            + "\nDo NOT invent names. spec-coverage-check uses exact match.\n"
        )
        return spec_rows, note
    return [], ""


# ---------------------------------------------------------------------------
# FR step prompt helpers — each builder returns the prompt string for one step.
# Called from _build_fr_step_prompt() dispatcher below.
# ---------------------------------------------------------------------------

def _compute_fr_spec_data(project: Path, fr_id: str, test_file: str) -> dict:
    """Compute spec test coverage data needed by GATE1, CODE-FIX, COVERAGE-FIX."""
    spec_test_names, _ = _extract_test_spec_names(project, fr_id)
    test_file_path = project / test_file
    existing_spec_tests: set[str] = set()
    if spec_test_names and test_file_path.exists():
        try:
            tf_content = test_file_path.read_text(encoding="utf-8")
            _actual_fns = set()
            for line in tf_content.splitlines():
                m2 = re.match(r"^\s*(?:async\s+)?def\s+(test_\w+)\s*\(", line)
                if m2:
                    _actual_fns.add(m2.group(1))
            for fn in spec_test_names:
                raw_fn = fn.strip("`").strip()
                raw_fn = re.sub(r"\[.*\]$", "", raw_fn)
                raw_fn = re.sub(r"\(\)$", "", raw_fn)
                if raw_fn in _actual_fns:
                    existing_spec_tests.add(fn)
        except (OSError, UnicodeDecodeError):
            pass
    spec_cov_pct = (
        round(len(existing_spec_tests) / max(len(spec_test_names), 1) * 100)
        if spec_test_names else 100
    )
    missing_spec_count = len(spec_test_names) - len(existing_spec_tests)
    spec_summary = (
        f"SPEC COVERAGE: {len(existing_spec_tests)}/{len(spec_test_names)} "
        f"({spec_cov_pct}%) — {missing_spec_count} missing"
        if spec_test_names else ""
    )
    return {
        "spec_test_names": spec_test_names,
        "existing_spec_tests": existing_spec_tests,
        "spec_cov_pct": spec_cov_pct,
        "missing_spec_count": missing_spec_count,
        "spec_summary": spec_summary,
    }


def _build_fr_step_prompt(step: str, fr_id: str, phase: int,
                           project: Path, srs_path: Path | None,
                           failing_dims: list | None = None,
                           tool_snapshot: str | None = None,
                           block_reason: str | None = None) -> str:
    """Build a minimal need-to-know prompt for a single FR TDD step.

    Each prompt is self-contained — the sub-agent receives only what it needs
    for that specific step (SRS section, test file content, etc.).

    Args:
        failing_dims: Required for CODE-FIX step — list of failing Gate 1
            dimension names.  Ignored for all other steps.
        tool_snapshot: Optional pre-run tool output (ruff + pytest) captured
            at orchestration time.  Injected into CODE-FIX prompt so agents
            can fix targeted errors without re-discovering them.
        block_reason: Optional finalize-gate block reason (e.g. S3/S4 detail)
            extracted from previous GATE1 sub-agent output.  Injected into
            GATE1 and CODE-FIX prompts so agents understand WHY gate blocked.

    Dispatches to step-specific builders.  Shared pre-computation (test_file,
    src_dir, srs_path normalisation, spec data) done once here.
    """
    step = step.upper()
    num_str = _fr_num_str(fr_id)
    _layout = ProjectLayout(project)
    test_dir_str = _layout.get_relative_str(_layout.active_test_dir)
    test_file = f"{test_dir_str}/test_fr{num_str}.py"
    src_dir = "03-development/src"

    # Default SRS path if not given
    if srs_path is None:
        candidate = project / ".methodology" / "SRS.md"
        srs_path = candidate if candidate.exists() else None

    if step == "TDD-RED":
        srs_section = _extract_srs_fr_section(srs_path, fr_id) if srs_path else ""
        _, spec_note = _extract_test_spec_names(project, fr_id)

        # CRG semantic search: find existing related code to avoid re-implementing
        _related_ctx = ""
        try:
            from harness.crg_bridge import CRGBridge as _CRGBridge
            _crg_sr = _CRGBridge()
            _sr = _crg_sr.semantic_search(str(project), fr_id, kind="Function", limit=5)
            _hits = (_sr or {}).get("results", [])
            if _hits:
                _related_ctx = (
                    "[RELATED EXISTING CODE — CRG semantic search]\n"
                    + "\n".join(
                        f"  - {h.get('name','?')} "
                        f"({(h.get('file_path') or '').split('/')[-1]})"
                        for h in _hits[:5]
                    )
                    + "\n\n"
                )
        except Exception:
            pass  # graceful: CRG not available or no match

        return (
            f"You are a TDD developer. Your ONLY task: write failing pytest tests for {fr_id}.\n\n"
            f"{spec_note}"
            f"{_related_ctx}"
            f"[FORBIDDEN — read before anything else]\n"
            f"- Implementing any source code (test file only)\n"
            f"- app/infrastructure/ paths\n"
            f"- @covers: L1 Error | @type: edge annotations\n"
            f"- Using try/except ImportError or lazy imports to hide ModuleNotFoundError. It is EXPECTED and PERFECTLY FINE for pytest to crash with Collection Error (Exit Code 2) because the source code doesn't exist yet.\n\n"
            f"[UNIT TEST CONTRACT — avoid false-fail traps]\n"
            f"Tests must fail because the FEATURE is missing, not because of external side-effects.\n"
            f"- Use standard top-level imports (e.g. `from src.engines.xxx import yyy`). Do NOT use try/except ImportError. If pytest returns Exit Code 2 (Collection Error) due to missing modules, this is a VALID RED STATE. Do not try to \"fix\" it by hiding the import.\n"
            f"- If tests call methods that perform real external operations (HMAC signature\n"
            f"  verification, DB connections, HTTP calls), use a pytest autouse fixture in\n"
            f"  `tests/conftest.py` (or an inline @pytest.fixture) to mock them. This is\n"
            f"  NOT 'implementing the feature' — it is required test isolation.\n"
            f"- Example: a pipeline.process() call performs HMAC verification internally.\n"
            f"  Add an autouse fixture: monkeypatch.setattr(Verifier, 'verify', lambda *a: True)\n"
            f"  so the test fails because the pipeline logic is absent, not because of bad sig.\n"
            f"- If you use patch.object(obj, 'method_name', ...) in a test, add a comment\n"
            f"  directly above that test explaining what the GREEN agent must implement:\n"
            f"  # GREEN TODO: <ClassName> must have <method_name>(self, *args) -> <return_type>\n"
            f"  Do NOT add stubs to source files yourself — GREEN does that.\n\n"
            f"[FR REQUIREMENTS]\n"
            f"{srs_section or f'See SRS.md for {fr_id} requirements'}\n\n"
            f"[TASK]\n"
            f"1. Create/edit `{test_file}` with failing tests covering the acceptance criteria above.\n"
            f"2. Every test function name MUST match the TEST SPEC names listed above exactly.\n"
            f"3. The tests MUST FAIL — do NOT implement the feature yet.\n"
            f"4. Run `pytest {test_file} -q`. Tests failing or raising Collection Error (ModuleNotFoundError) means SUCCESS for this RED step.\n"
            f"5. Commit: `git add {test_file} && git commit -m \"test(RED): failing test for {fr_id}\"`\n\n"
            f'[OUTPUT FORMAT]\nReturn JSON: {{"status": "DONE", "test_file": "{test_file}", '
            f'"commit": "<hash>", "summary": "<under 50 chars>"}}'
        )

    if step == "TDD-GREEN":
        srs_section = _extract_srs_fr_section(srs_path, fr_id) if srs_path else ""
        test_content = ""
        tf = project / test_file
        if tf.exists():
            test_content = tf.read_text(encoding="utf-8")
        return (
            f"You are a TDD developer. Your task: implement {fr_id} until the failing test passes.\n\n"
            f"[FORBIDDEN — read before anything else]\n"
            f"- Modifying test files\n"
            f"- app/infrastructure/ paths\n\n"
            f"[IMPLEMENTATION CONTRACT]\n"
            f"Before writing any code, scan `{test_file}` for:\n"
            f"  1. patch.object(obj, 'method_name', ...) — every patched method_name MUST\n"
            f"     exist in your implementation (even as a stub returning {{}}). Missing\n"
            f"     attributes cause AttributeError before the test even runs.\n"
            f"  2. autouse fixtures that mock verifiers — means the test bypasses real HMAC/auth.\n"
            f"     Do NOT add HMAC bypass to production code; the fixture already handles it.\n"
            f"  3. Any test that asserts on status codes (200/500/429/401) from a top-level\n"
            f"     orchestrator or pipeline method — verify the implementation handles unexpected\n"
            f"     exceptions and returns a structured error response rather than propagating.\n"
            f"     Only add try/except if the tests actually require it; do not add for utilities.\n\n"
            f"[FAILING TEST — {test_file}]\n"
            f"{test_content or f'(read from {test_file})'}\n\n"
            f"[FR REQUIREMENTS]\n"
            f"{srs_section or f'See SRS.md for {fr_id} requirements'}\n\n"
            f"[TASK]\n"
            f"1. Scan test file per [IMPLEMENTATION CONTRACT] above before writing any code.\n"
            f"2. Create/edit source files in `{src_dir}/` to make `{test_file}` pass.\n"
            f"3. Run `pytest {test_file} -q` — all tests must pass.\n"
            f"4. Docstrings must include `[{fr_id}]` tag + `Citations:` with line numbers (HR-15).\n"
            f"5. Commit: `git add {src_dir}/ && git commit -m \"feat({fr_id}): GREEN\"`\n\n"
            f'[OUTPUT FORMAT]\nReturn JSON: {{"status": "DONE", "files_changed": [...], '
            f'"commit": "<hash>", "summary": "<under 50 chars>"}}'
        )

    if step == "TDD-IMPROVE":
        test_content = ""
        tf = project / test_file
        if tf.exists():
            test_content = tf.read_text(encoding="utf-8")[:1500]
        return (
            f"You are a TDD refactorer. Your task: improve {fr_id} WITHOUT breaking tests.\n\n"
            f"[FORBIDDEN — read before anything else]\n"
            f"- Modifying test files (any file under tests/)\n"
            f"- Setting enum values to None (e.g. STATUS = None, EXIT = None)\n"
            f"- Changing sys.exit() codes from their current values\n"
            f"- Injecting XX...XX placeholder markers into source files\n\n"
            f"[TEST INVARIANTS — {test_file} (first 1500 chars)]\n"
            f"{test_content or f'(read from {test_file})'}\n\n"
            f"[TASK]\n"
            f"1. Run `pytest {test_file} -q` first — confirm all pass before any changes.\n"
            f"2. Refactor source code in `{src_dir}/` for clarity, remove duplication, improve naming.\n"
            f"3. Re-run `pytest {test_file} -q` — must still pass.\n"
            f"4. If changes made: `git commit -m \"refactor({fr_id}): IMPROVE\"`\n"
            f"5. If no refactor needed: no commit required.\n\n"
            f'[OUTPUT FORMAT]\nReturn JSON: {{"status": "DONE", "refactored": true/false, '
            f'"commit": "<hash or null>", "summary": "<under 50 chars>"}}'
        )

    # Spec data: compute once, pass to GATE1 / CODE-FIX / COVERAGE-FIX builders.
    spec = _compute_fr_spec_data(project, fr_id, test_file)
    spec_test_names = spec["spec_test_names"]
    existing_spec_tests = spec["existing_spec_tests"]
    spec_cov_pct = spec["spec_cov_pct"]
    missing_spec_count = spec["missing_spec_count"]
    spec_summary = spec["spec_summary"]

    # GATE1-DELTA no longer passes --delta to run-gate. The skip-if-unchanged
    # decision is now made by _fr_step_already_done() via git diff before dispatch.
    # Once we reach here, code has changed → full GATE1 evaluation.
    if step in ("GATE1", "GATE1-DELTA"):

        # ── TEST_SPEC.md required test names for test_coverage evaluation ──
        spec_test_names, _ = _extract_test_spec_names(project, fr_id)
        spec_section = ""
        if spec_test_names:
            spec_section = (
                f"\n[TEST SPEC — required test cases for {fr_id}]\n"
                f"TEST_SPEC.md requires these EXACT test functions:\n"
                + "\n".join(f"  - {fn}" for fn in spec_test_names)
                + "\n\nWhen evaluating test_coverage, verify:\n"
                "  - EVERY required test EXISTS in the test file\n"
                "  - EVERY required test PASSES (not skipped, not failing)\n"
                "  - Missing or failing required test = test_coverage FAIL, "
                "regardless of raw coverage %\n\n"
            )

        # ── Previous block reason (S3/S4) surfaced for retry ──
        block_section = ""
        if block_reason:
            block_section = (
                f"\n[PREVIOUS ATTEMPT BLOCKED — read carefully]\n"
                f"{block_reason}\n"
                f"Ensure the gate1_result.json you write this time satisfies the\n"
                f"tool_evidence requirement described in step 3 below.\n\n"
            )

        # ── Spec test coverage status (inject so evaluator knows the current state) ──
        spec_section = ""
        if spec_test_names:
            spec_section = (
                f"\n[TEST SPEC — required test cases for {fr_id}]\n"
                f"TEST_SPEC.md requires these EXACT test functions:\n"
                + "\n".join(f"  - {fn}" for fn in spec_test_names)
                + f"\n\n{spec_summary}\n"
                f"→ score = min(coverage_pct, spec_cov_pct). Missing tests count as 0.\n"
                f"  All required tests MUST exist and pass — partial coverage = partial score.\n\n"
            )

        return (
            f"You are a Gate 1 evaluator. Your task: run Gate 1 evaluation for {fr_id}.\n"
            f"{spec_section}"
            f"{block_section}"
            f"[STOP RULE — follow when tools fail or you are unsure]\n"
            f"- If any tool command fails to execute (error, not found, env issue):\n"
            f"  → Record score=0 for that dimension\n"
            f"  → Set tool_evidence = first 300 chars of the error output\n"
            f"  → Move on to the next dimension — do NOT retry the same command\n"
            f"- If finalize-gate prints [BLOCKED]:\n"
            f"  → Include the exact BLOCKED message in your output summary\n"
            f"  → Do NOT attempt to fix source code yourself — that is CODE-FIX's job\n"
            f"- Write gate1_result.json and call finalize-gate within 10 turns of starting.\n"
            f"  A low score with tool_evidence is always better than a timeout.\n\n"
            f"[TASK — follow EXACTLY in order]\n"
            f"1. Run: `python3 harness_cli.py run-gate --gate 1 --phase {phase} "
            f"--fr-id {fr_id} --project .`\n"
            f"   The output contains FR-SCOPED TOOL OVERRIDES — exact commands for each\n"
            f"   dimension.  Use those commands, not the generic ones in evaluate_dimension.md.\n\n"
            f"2. Run the three tool commands from step 1's FR-SCOPED TOOL OVERRIDES:\n"
            f"   a. linting:      ruff check ... (exact command shown in run-gate output)\n"
            f"   b. type_safety:  pyright ... (exact command shown in run-gate output)\n"
            f"   c. test_coverage: coverage run / pytest ... (exact command shown in run-gate output)\n"
            f"   Save each tool's output to .sessi-work/round_1/tools/<dimension>.txt\n\n"
            f"3. Write `.sessi-work/gate1_result.json` with this EXACT schema:\n"
            f"   {{\n"
            f'     "gate": 1, "phase": {phase}, "fr_id": "{fr_id}",\n'
            f'     "overall_score": <float>,           // weighted avg of breakdown scores\n'
            f'     "quality_complete": true,            // true if overall_score >= 80\n'
            f'     "rounds_used": 1,\n'
            f'     "breakdown": {{\n'
            f'       "linting":       {{"score": <0-100>, "threshold": 90, "tool_evidence": "<first 500 chars of ruff stdout>"}},\n'
            f'       "type_safety":   {{"score": <0-100>, "threshold": 85, "tool_evidence": "<first 500 chars of pyright stdout>"}},\n'
            f'       "test_coverage": {{\n'
            f'           "score": <0-100>, "threshold": 80,\n'
            f'           "tests_passed": <int>,   // REQUIRED: count from pytest summary line\n'
            f'           "tests_failed": <int>,   // REQUIRED: must be 0 — any failed test blocks the gate\n'
            f'           "tests_skipped": <int>,  // REQUIRED: count skipped tests\n'
            f'           "tool_evidence": "<first 500 chars of coverage/pytest stdout>"\n'
            f'       }}\n'
            f'     }}\n'
            f"   }}\n"
            f"   overall_score = (linting.score × 0.33 + type_safety.score × 0.33 + test_coverage.score × 0.34).\n"
            f"   quality_complete = (overall_score >= 80) AND (every dimension score >= its threshold).\n"
            f"   CRITICAL: `tool_evidence` is REQUIRED for every dimension.\n"
            f"   If you omit it, finalize-gate will BLOCK with S3 error regardless of scores.\n"
            f"   Score fabrication (writing a score without running the tool) also causes S3 block.\n"
            f"   CRITICAL: `tests_failed` MUST be 0. finalize-gate parses tool_evidence for\n"
            f"   '{{N}} failed' and blocks immediately if any test is red — even at 96% coverage.\n\n"
            f"   Scoring formulas:\n"
            f"   - linting:      ruff exit 0 → 100; else count violations: max(0, 100 - violations×5)\n"
            f"   - type_safety:  parse pyright JSON summary.errorCount: max(0, 100 - errorCount×5)\n"
            f"   - test_coverage: score = min(coverage_pct, spec_cov_pct).\n"
            f"     spec_cov_pct = (existing_required_tests / total_required) × 100.\n"
            f"     Currently: {missing_spec_count} required tests missing → spec_cov_pct = {spec_cov_pct}% → score capped at {spec_cov_pct}.\n"
            f"     ALL required tests must exist and pass — partial spec coverage = partial score.\n\n"
            f"4. Run: `python3 harness_cli.py finalize-gate --gate 1 --phase {phase} "
            f"--fr-id {fr_id} --project .`\n"
            f"   If finalize-gate prints [BLOCKED], include the exact error in your output summary.\n\n"
            f"5. Report pass/fail and failing dimensions (if any).\n\n"
            f'[OUTPUT FORMAT]\nReturn JSON: {{"status": "DONE", "gate_score": <float>, '
            f'"pass": true/false, "failing_dims": [...], "commit": "<hash or null>", '
            f'"summary": "<under 50 chars>"}}'
        )

    if step == "TEST-FIX":
        # Dispatched when _classify_snapshot_failure returns "ISOLATION":
        # tests fail because infrastructure (HMAC, DB, HTTP) intercepts before
        # feature logic runs. Fix is to add autouse fixtures — not to touch source.
        return (
            f"You are a test isolation fixer for {fr_id}.\n\n"
            f"[FORBIDDEN — read first]\n"
            f"- Modifying source files in `{src_dir}/`\n"
            f"- Deleting or xfail-marking tests\n\n"
            f"[PROBLEM]\n"
            f"Gate 1 tests are failing because of EXTERNAL SIDE-EFFECTS, not because the "
            f"feature is missing. Tests call real infrastructure (HMAC verification, DB "
            f"connections, HTTP calls) that short-circuits before feature logic is reached. "
            f"Every test returns the same infrastructure error (e.g. 401 Unauthorized).\n\n"
            f"[ACTUAL TOOL OUTPUT]\n"
            f"{tool_snapshot or '(not available)'}\n\n"
            f"[TASK]\n"
            f"1. Identify the infrastructure call that intercepts (HMAC verifier, DB, HTTP).\n"
            f"2. Add a pytest autouse fixture to `{test_file}` (or `tests/conftest.py`) "
            f"that mocks it so tests reach the feature logic:\n"
            f"   @pytest.fixture(autouse=True)\n"
            f"   def _bypass_infra(monkeypatch):\n"
            f"       monkeypatch.setattr(InfraClass, 'verify', lambda *a, **kw: True)\n"
            f"3. Run `pytest {test_file} -q` — tests must now fail for the RIGHT reason "
            f"(AssertionError or NameError from missing feature, NOT 401/auth error).\n"
            f"4. Commit: `git add {test_file} tests/conftest.py && "
            f"git commit -m \"test({fr_id}): fix test isolation — add autouse infra mock\"`\n\n"
            f'[OUTPUT FORMAT]\nReturn JSON: {{"status": "DONE", "fixture_added": true, '
            f'"commit": "<hash>", "summary": "<under 50 chars>"}}'
        )

    if step == "COVERAGE-FIX":
        # Dispatched when _classify_snapshot_failure returns "LOW_COVERAGE":
        # all Gate 1 tests pass but test_coverage dimension is still failing.
        # Two root causes:
        #   A. Existing tests don't cover enough source lines (code_cov < 80%).
        #   B. Required test functions from TEST_SPEC.md are absent (spec_cov < 80%).
        return (
            f"You are a coverage fixer for {fr_id}.\n\n"
            f"[FORBIDDEN — read first]\n"
            f"- Deleting or xfail-marking existing tests\n"
            f"- Adding `# pragma: no cover` to lines that CAN be tested (only use it as a "
            f"last resort for genuinely untestable lines — see ESCAPE HATCH below)\n\n"
            f"[SITUATION]\n"
            f"All Gate 1 tests currently PASS, but the test_coverage dimension is FAILING.\n"
            f"Coverage is below the 80% threshold. Two possible root causes:\n"
            f"  A. Existing tests don't cover enough source lines (code coverage < 80%).\n"
            f"  B. Required test functions from TEST_SPEC.md are absent from `{test_file}`.\n\n"
            f"[ACTUAL TOOL OUTPUT — from pre-run]\n"
            f"{tool_snapshot or '(not available)'}\n\n"
            f"[TASK]\n"
            f"1. Run `pytest {test_file} --cov={src_dir} --cov-report=term-missing -q` "
            f"to identify which source lines are not covered (Miss column).\n"
            f"2. Read `02-architecture/TEST_SPEC.md` section for {fr_id} to identify required "
            f"test function names. For each function missing from `{test_file}` — add it.\n"
            f"3. For each uncovered line: decide which approach applies:\n"
            f"   a. Line CAN be reached by a test → add a targeted unit test.\n"
            f"   b. Line is genuinely untestable → apply ESCAPE HATCH (see below).\n"
            f"4. Re-run until coverage reaches ≥ 80%: "
            f"`pytest {test_file} --cov={src_dir} --cov-report=term-missing -q`\n"
            f"5. Commit both `{test_file}` and any source changes from ESCAPE HATCH:\n"
            f"   `git add {src_dir}/ {test_file} && "
            f"git commit -m \"test({fr_id}): add coverage tests and pragma exclusions\"`\n\n"
            f"[ESCAPE HATCH — pragma: no cover]\n"
            f"If after adding all reasonable tests coverage is still < 80%, you MAY annotate "
            f"lines in `{src_dir}/` with `# pragma: no cover` ONLY for lines that are "
            f"genuinely impossible or unreasonable to test:\n"
            f"  ✓ Allowed: defensive `raise NotImplementedError` / abstract stubs, "
            f"infrastructure fallback branches (e.g. `except OSError: sys.exit(1)`), "
            f"`if __name__ == '__main__':` blocks, platform-specific dead branches.\n"
            f"  ✗ Not allowed: ordinary business logic, error-handling paths that CAN be "
            f"triggered by passing a bad argument, any line reachable via monkeypatching.\n"
            f"Each `# pragma: no cover` annotation MUST be accompanied by a one-line comment "
            f"explaining WHY it is untestable, e.g.:\n"
            f"  `raise NotImplementedError  # pragma: no cover — abstract base, subclass must implement`\n\n"
            f"[PARTIAL PROGRESS NOTE]\n"
            f"If there are many missing spec tests (>50), add as many as you can and commit.\n"
            f"The meta-loop will re-run if coverage is still insufficient — each session "
            f"reads the test file fresh and picks up where the previous session left off.\n"
            f"Do NOT stop early to 'leave some for next time' — add the maximum you can.\n\n"
            f'[OUTPUT FORMAT]\nReturn JSON: {{"status": "DONE", "coverage_pct": <number>, '
            f'"tests_added": <count>, "pragmas_added": <count>, '
            f'"commit": "<hash>", "summary": "<under 50 chars>"}}'
        )

    if step == "INFRA-FIX":
        # Dispatched when _classify_snapshot_failure returns "INFRA_SKIP":
        # pytest reports N skipped (not failed) because Docker/Redis/external service
        # is unavailable in CI. Coverage is 0 for those paths.
        return (
            f"You are an infrastructure mock fixer for {fr_id}.\n\n"
            f"[FORBIDDEN — read first]\n"
            f"- Deleting or xfail-marking existing tests\n"
            f"- Removing skip markers without providing an alternative that actually runs\n\n"
            f"[SITUATION]\n"
            f"Gate 1 tests are being SKIPPED (not failing) because they depend on external "
            f"infrastructure (Docker, Redis, database, external HTTP) that is unavailable in "
            f"this environment. The skipped tests contribute 0 lines to coverage, causing "
            f"test_coverage to fail.\n\n"
            f"[ACTUAL TOOL OUTPUT — from pre-run]\n"
            f"{tool_snapshot or '(not available)'}\n\n"
            f"[TASK]\n"
            f"1. Identify which tests are skipped and WHY (read the skip condition: "
            f"`pytest {test_file} -v --collect-only 2>&1 | grep -i skip`).\n"
            f"2. For each skipped test, choose ONE approach:\n"
            f"   a. ADD a parallel mock-based test that exercises the same logic without "
            f"real infra (e.g. monkeypatch Redis/Docker client). Keep the original skip "
            f"test as-is for integration runs.\n"
            f"   b. If the skipped code path is genuinely untestable without the real service "
            f"AND the source branch is an infrastructure-only fallback: annotate with "
            f"`# pragma: no cover` + reason comment in `{src_dir}/`.\n"
            f"3. Run `pytest {test_file} -q` to verify no new failures are introduced.\n"
            f"4. Commit: `git add {src_dir}/ {test_file} && "
            f"git commit -m \"test({fr_id}): add mock tests for infra-skipped paths\"`\n\n"
            f'[OUTPUT FORMAT]\nReturn JSON: {{"status": "DONE", "mocks_added": <count>, '
            f'"pragmas_added": <count>, "commit": "<hash>", "summary": "<under 50 chars>"}}'
        )

    if step == "LINT-FIX":
        # Dispatched when _classify_snapshot_failure returns "LINT_FAIL" or "LINT_AND_COVERAGE".
        # LINT_AND_COVERAGE: fix linting only this round; coverage handled next round.
        return (
            f"You are a linting fixer for {fr_id}.\n\n"
            f"[FORBIDDEN — read first]\n"
            f"- Modifying test files in `tests/`\n"
            f"- Suppressing violations with `# noqa` unless the violation is a false positive "
            f"(document why if you use noqa)\n\n"
            f"[SITUATION]\n"
            f"Gate 1 linting dimension is FAILING. Fix ALL ruff violations in `{src_dir}/` "
            f"so `ruff check {src_dir}/ --extend-ignore RUF001,RUF002,RUF003` exits 0.\n\n"
            f"[ACTUAL TOOL OUTPUT — from pre-run]\n"
            f"{tool_snapshot or '(not available)'}\n\n"
            f"[TASK]\n"
            f"1. Run `ruff check {src_dir}/ --extend-ignore RUF001,RUF002,RUF003 2>&1` to see the full violation list.\n"
            f"2. For N-series violations (naming conventions — N801, N802, N806, N816 etc.):\n"
            f"   - Rename constants/variables to follow PEP 8 naming (UPPER_CASE for module "
            f"constants, UpperCase for classes, lower_case for functions/variables).\n"
            f"   - Update ALL references to each renamed symbol (use `grep -rn '<old_name>'` "
            f"to find them, then rename systematically).\n"
            f"3. For E/W-series violations: fix in-place per ruff's suggestion.\n"
            f"4. Re-run `ruff check {src_dir}/ --extend-ignore RUF001,RUF002,RUF003` — it MUST exit 0 before you commit.\n"
            f"5. Run `pytest {test_file} -q` to confirm no tests broken by renames.\n"
            f"6. Commit: `git add {src_dir}/ && "
            f"git commit -m \"fix({fr_id}): resolve ruff linting violations\"`\n\n"
            f"[NOTE] If BOTH linting AND test_coverage were failing, this session fixes "
            f"linting ONLY. The meta-loop will address coverage in the next round.\n\n"
            f'[OUTPUT FORMAT]\nReturn JSON: {{"status": "DONE", "violations_fixed": <count>, '
            f'"commit": "<hash>", "summary": "<under 50 chars>"}}'
        )

    if step == "CODE-FIX":
        # failing_dims=None means GATE1 timed out / errored before writing a result.
        # In this case we cannot know what failed — emit a diagnostic mode prompt
        # that tells the agent to self-diagnose first, rather than blindly fixing src.
        if failing_dims is None:
            return (
                f"You are a code fixer. Gate 1 for {fr_id} could not complete "
                f"(sub-agent timeout or error — no gate1_result.json was written).\n\n"
                f"[TASK — diagnostic mode]\n"
                f"1. Run `pytest tests/ -q` to identify failing / missing tests.\n"
                f"2. Run `ruff check {src_dir}/ --extend-ignore RUF001,RUF002,RUF003` to identify lint errors.\n"
                f"3. Based on actual results:\n"
                f"   a. If tests are failing or missing → add/fix tests in `{test_file}` "
                f"AND fix source code in `{src_dir}/` as needed.\n"
                f"   b. If lint errors → fix source code only.\n"
                f"4. Run `pytest tests/ -q` to confirm all tests pass.\n"
                f"5. Commit all changed files: "
                f"`git add {src_dir}/ {test_file} && "
                f"git commit -m \"fix({fr_id}): address Gate1 failures\"`\n\n"
                f"[FORBIDDEN]\n"
                f"- Deleting or modifying existing passing tests\n"
                f"- app/infrastructure/ paths\n\n"
                f'[OUTPUT FORMAT]\nReturn JSON: {{"status": "DONE", "dims_fixed": [...], '
                f'"commit": "<hash>", "summary": "<under 50 chars>"}}'
            )

        # Classify failing dims so we know what kind of fix is needed.
        _fdims_lower = {str(d).lower() for d in failing_dims}
        _test_cov_failing = "test_coverage" in _fdims_lower
        _src_failing = bool(_fdims_lower - {"test_coverage"})

        dims_str = "\n".join(str(d) for d in failing_dims)

        # ── test_coverage section ─────────────────────────────────────────
        # test_coverage can fail for two distinct reasons:
        #   A. Required test functions are MISSING from the test file.
        #   B. Required test functions EXIST but are FAILING.
        # Use the already-computed spec analysis from the top of this function.
        test_cov_section = ""
        if _test_cov_failing:
            missing_spec = [fn for fn in spec_test_names if fn not in existing_spec_tests]
            present_spec = [fn for fn in spec_test_names if fn in existing_spec_tests]

            parts: list[str] = [
                f"\n[TEST COVERAGE FIX — required for test_coverage dimension]\n"
                f"{spec_summary}\n\n"
            ]

            if missing_spec:
                parts.append(
                    f"MISSING ({len(missing_spec)} tests) — these required tests are NOT in `{test_file}`:\n"
                    + "\n".join(f"  - {fn}" for fn in missing_spec)
                    + "\n  → ADD ALL of them as real, passing tests in THIS session.\n"
                    + "  IMPORTANT: write ALL missing tests in one go — do not stop after 1-2.\n"
                    + "  The agent has enough max_turns (70) to add all remaining tests in one session.\n\n"
                )

            if present_spec:
                parts.append(
                    f"PRESENT but failing ({len(present_spec)} tests) — these tests exist in `{test_file}`:\n"
                    + "\n".join(f"  - {fn}" for fn in present_spec)
                    + "\n  → Run `pytest {test_file} -v` and fix each failing test.\n\n"
                )

            if not spec_test_names:
                # No spec info — generic triage instruction
                parts.append(
                    f"Read `02-architecture/TEST_SPEC.md` section for {fr_id} to get\n"
                    "required test function names, then for each:\n"
                    "  - NOT in test file → ADD as a real passing test\n"
                    "  - In test file but FAILING → fix source code or assertion\n\n"
                )

            test_cov_section = "".join(parts)

        # ── TASK steps (built dynamically) ───────────────────────────────
        task_lines = [
            "1. Read `harness/ssi/prompts/evaluate_dimension.md` for each failing dimension's criteria.",
        ]
        n = 2
        if _src_failing:
            task_lines.append(
                f"{n}. Fix source code in `{src_dir}/` to address non-test-coverage failing dimensions."
            )
            n += 1
        if _test_cov_failing:
            task_lines.append(
                f"{n}. Resolve test_coverage failures (see TEST COVERAGE FIX above):\n"
                f"   a. ADD any missing required test functions to `{test_file}`.\n"
                f"   b. For tests that exist but FAIL: fix source code or the failing assertion."
            )
            n += 1
        task_lines.append(f"{n}. Run `pytest tests/ -q` to confirm ALL tests pass.")
        n += 1
        git_paths = " ".join(filter(None, [
            f"{src_dir}/" if _src_failing else "",
            test_file if _test_cov_failing else "",
        ]))
        task_lines.append(
            f"{n}. Commit: `git add {git_paths} && "
            f"git commit -m \"fix({fr_id}): address Gate1 failing dims\"`"
        )

        # ── FORBIDDEN ────────────────────────────────────────────────────
        if _test_cov_failing:
            # May need to add tests AND fix failing test assertions.
            # Only hard prohibition is deleting tests.
            forbidden = (
                "- Deleting existing tests\n"
                "- Skipping or xfail-marking tests to make them 'pass'\n"
                "- app/infrastructure/ paths"
            )
        else:
            forbidden = (
                "- Modifying test files\n"
                "- app/infrastructure/ paths"
            )

        # test_cov_section ends with \n\n when non-empty, so it provides the gap
        # before [TASK]. When empty, insert the gap explicitly.
        gap = "\n" if not test_cov_section else ""

        # ── Tool snapshot captured at orchestration time (Fix 1) ──
        snapshot_section = ""
        if tool_snapshot:
            snapshot_section = (
                f"\n[ACTUAL TOOL OUTPUT — captured at orchestration time]\n"
                f"Use these exact errors as your fix targets. "
                f"Do NOT re-run the tools to re-discover them — fix what is shown here.\n"
                f"{tool_snapshot}\n\n"
            )

        return (
            f"You are a code fixer. Gate 1 FAILED for {fr_id}. Fix the failing dimensions.\n\n"
            f"[FORBIDDEN — read before anything else]\n"
            f"{forbidden}\n\n"
            f"[FAILING DIMENSIONS]\n"
            f"{dims_str}\n"
            f"{test_cov_section}"
            f"{snapshot_section}"
            f"{gap}"
            f"[TASK]\n"
            + "\n".join(task_lines) + "\n\n"
            + '[OUTPUT FORMAT]\nReturn JSON: {"status": "DONE", "dims_fixed": [...], '
            '"commit": "<hash>", "summary": "<under 50 chars>"}'
        )

    return f"[ERROR] Unknown step: {step}"


def _capture_tool_snapshot(
    project: Path, src_dir: str, test_file: str
) -> str:
    """Run ruff + pytest at orchestration time and return combined output (max 2000 chars).

    Used to give CODE-FIX agents concrete, targeted error messages rather than
    forcing them to re-discover failures from scratch.  Failures are non-fatal —
    returns "" on any subprocess error so the CODE-FIX prompt degrades gracefully.
    """
    import subprocess as _sp
    lines: list[str] = []
    # PYTHONPATH must include the src root for src-layout projects.
    # Using PYTHONPATH=project alone causes ModuleNotFoundError for packages
    # under 03-development/src/, masking the real assertion failures from fixers.
    # We include BOTH project root (original behaviour) and src_dir (new) so
    # that nothing that previously worked can regress.
    import os as _os
    _pythonpath = (
        _os.pathsep.join([str(project / src_dir), str(project)])
        if src_dir else str(project)
    )
    # Try ruff from PATH first; fall back to python3 -m ruff when ruff is
    # installed only inside a specific Python environment (e.g. Python 3.9 venv
    # while the system python3 is 3.14).  exit code 127 = command not found.
    _ruff_r = None
    for _ruff_cmd in (
        ["ruff", "check", f"{src_dir}/", "--extend-ignore", "RUF001,RUF002,RUF003"],
        ["python3", "-m", "ruff", "check", f"{src_dir}/", "--extend-ignore", "RUF001,RUF002,RUF003"],
    ):
        try:
            _ruff_r = _sp.run(
                _ruff_cmd, capture_output=True, text=True,
                cwd=str(project), timeout=30,
            )
            if _ruff_r.returncode != 127:
                break
        except Exception:
            _ruff_r = None
    if _ruff_r and (_ruff_r.stdout.strip() or _ruff_r.stderr.strip()):
        lines.append(f"ruff check {src_dir}/ --extend-ignore RUF001,RUF002,RUF003 (exit {_ruff_r.returncode}):")
        lines.append((_ruff_r.stdout + _ruff_r.stderr).strip()[:600])
        lines.append("")
    try:
        r = _sp.run(
            ["python3", "-m", "pytest", test_file, "-v", "--tb=short", "-q"],
            capture_output=True, text=True, cwd=str(project),
            timeout=60, env={**__import__("os").environ, "PYTHONPATH": _pythonpath},
        )
        output = (r.stdout + r.stderr).strip()
        if output:
            lines.append(f"pytest {test_file} -v --tb=short (exit {r.returncode}):")
            # Tail: most useful failures are at the end
            lines.append(output[-800:])
    except Exception:
        pass
    return "\n".join(lines)[:2000]


def _classify_snapshot_failure(snapshot: str, failing_dims: list | None = None) -> str:
    """Classify the root cause of a Gate 1 failure from tool snapshot output.

    Returns one of:
      "ENV"             — ModuleNotFoundError / ImportError (environment not set up)
      "ISOLATION"       — tests fail due to auth/HMAC short-circuit, not missing feature
      "PATCH_OBJECT"    — AttributeError: obj has no attribute 'method' (stub missing)
      "LOW_COVERAGE"    — all tests pass but test_coverage dim failing (coverage < threshold)
      "MISSING_FEATURE" — AssertionError / genuine logic failure (CODE-FIX can help)
      "UNKNOWN"         — cannot classify (fall through to CODE-FIX)
    """
    if not snapshot:
        return "UNKNOWN"
    s = snapshot.lower()
    if "no module named" in s or "modulenotfounderror" in s or "importerror" in s:
        return "ENV"
    if "attributeerror" in s and "has no attribute" in s:
        return "PATCH_OBJECT"
    # Isolation: infrastructure intercepts before feature logic — all tests return 401/auth
    if ("status_code=401" in s or "source='auth'" in s
            or 'source="auth"' in s or "401 unauthorized" in s):
        return "ISOLATION"
    # Compute shared flags early — referenced by INFRA_SKIP, LINT, and LOW_COVERAGE checks.
    _test_cov_failing = (
        failing_dims is not None
        and any("test_coverage" in str(d).lower() for d in failing_dims)
    )
    _has_test_failures = "failed" in s or "assertionerror" in s
    # INFRA_SKIP: tests skipped (not failed) because Docker/Redis/external service unavailable.
    # Coverage is low because skipped tests contribute 0 executed lines. Distinct from
    # ISOLATION: no 401/auth signal — pytest just reports "N skipped".
    if _test_cov_failing and "skipped" in s and not _has_test_failures:
        return "INFRA_SKIP"
    # LINT_FAIL / LINT_AND_COVERAGE: ruff linting dimension is failing.
    # Always fix linting first — mixing linting + coverage in one CODE-FIX round causes timeout.
    _lint_failing = (
        failing_dims is not None
        and any("linting" in str(d).lower() for d in failing_dims)
    )
    if _lint_failing:
        # LINT_AND_COVERAGE: both failing — fix linting this round, coverage next round.
        return "LINT_AND_COVERAGE" if _test_cov_failing else "LINT_FAIL"
    # LOW_COVERAGE: test_coverage dim failing but all tests pass — coverage % below threshold.
    # Snapshot is collected without --cov, so coverage % is not visible; detect via
    # failing_dims (test_coverage listed) + no test failures in snapshot + tests did pass.
    if _test_cov_failing and not _has_test_failures and "passed" in s:
        return "LOW_COVERAGE"
    if "assertionerror" in s or "failed" in s or "error" in s:
        return "MISSING_FEATURE"
    return "UNKNOWN"




# ---------------------------------------------------------------------------
# reload-policy
# ---------------------------------------------------------------------------


def _run_gap_analysis(project: Path, similarity: float = 0.6, spec: str = "SPEC.md") -> dict:
    """Run M3 gap analysis. Returns gap report dict; warns on failure."""
    try:
        from gap_detector.parser import SpecParser
        from gap_detector.scanner import CodeScanner
        from gap_detector.detector import GapDetector

        spec_path = project / spec
        if not spec_path.exists():
            print(f"  [M3] {spec} not found — skipping gap analysis")
            return {"skipped": True, "reason": f"{spec} not found"}

        parsed_spec = SpecParser(str(spec_path)).parse()
        scanner = CodeScanner(str(project))
        code = scanner.scan()
        detector = GapDetector(parsed_spec, code, similarity_threshold=similarity)
        gaps = detector.detect()
        summary = detector.get_summary()

        report = {
            "summary": {
                "total": summary.total_gaps, "missing": summary.missing,
                "incomplete": summary.incomplete, "orphaned": summary.orphaned,
                "critical": summary.critical, "major": summary.major,
                "minor": summary.minor,
            },
            "gaps": [{"type": g.gap_type, "severity": g.severity,
                       "reason": g.reason, "action": g.recommended_action}
                      for g in gaps],
        }
        report_path = project / ".methodology" / "gap_report.json"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, indent=2))
        print(f"  [M3] Gap report → {report_path}  "
              f"(total={summary.total_gaps}, critical={summary.critical})")
        return report
    except ImportError:
        print("  [M3] gap_detector unavailable — skipping gap analysis")
        return {"skipped": True, "reason": "gap_detector unavailable"}
    except Exception as exc:  # pylint: disable=broad-exception-caught
        print(f"  [M3] Gap analysis error: {exc}")
        return {"skipped": True, "error": str(exc)}

def _make_git(args: argparse.Namespace, project: Path) -> "GitStrategy":  # noqa: F821 — lazy import
    """Instantiate GitStrategy from parsed args. Lazy-imports to keep startup fast.

    Git is disabled if either --no-git or --dry-run is set. --dry-run is the
    preferred safety flag for push-milestone (Bug #112) — it prevents accidental
    origin pollution when exercising the command during bug hunts.
    """
    from harness.git_strategy import GitStrategy
    no_git = getattr(args, "no_git", False) or getattr(args, "dry_run", False)
    return GitStrategy(project=project, enabled=not no_git)

# ---------------------------------------------------------------------------
# CLAUDE.md auto-update helpers
# ---------------------------------------------------------------------------

_CLAUDE_AUTO_START = "<!-- harness:auto-start -->"
_CLAUDE_AUTO_END   = "<!-- harness:auto-end -->"

_PHASE_NAMES = {
    1: "Requirements", 2: "Architecture", 3: "Implementation",
    4: "Testing", 5: "Verification", 6: "Quality", 7: "Risk", 8: "Config Management",
    9: "Maintenance",
}


def _build_claude_md_auto_section(project_path: Path) -> str:
    """Build the harness status markdown block from state.json + quality_manifest.json.

    Gracefully degrades: missing files → empty dicts → "Not Started" placeholders.
    """
    from datetime import datetime, timezone as _tz

    manifest: dict = {}
    state: dict = {}
    for fpath, store_key in (
        (project_path / ".methodology" / "quality_manifest.json", "manifest"),
        (project_path / ".methodology" / "state.json", "state"),
    ):
        if fpath.exists():
            try:
                data = json.loads(fpath.read_text(encoding="utf-8"))
                if store_key == "manifest":
                    manifest = data
                else:
                    state = data
            except Exception:  # pylint: disable=broad-exception-caught
                pass

    current_phase = state.get("current_phase", 1)
    phase_name = _PHASE_NAMES.get(current_phase, f"Phase {current_phase}")
    last_gate = state.get("last_gate", "—")
    last_fr_str = f" | Last FR: {state['last_fr']}" if state.get("last_fr") else ""
    updated = datetime.now(_tz.utc).strftime("%Y-%m-%d")

    gates = manifest.get("gate_results", {})
    fr_ids: list = manifest.get("fr_ids", [])

    # Gate progress rows (G1 shows done/total FRs; G2-G4 show numeric score)
    gate_rows: list[str] = []
    for gn in (1, 2, 3, 4):
        g = gates.get(f"gate{gn}")
        if isinstance(g, dict) and "score" in g:
            score_str = f"{g['score']:.1f}"
            status = "✅ PASS" if g.get("quality_complete") else "🔄 In Progress"
        elif isinstance(g, dict):
            # gate1: {FR-XX: {score, quality_complete, ...}}
            fr_vals = [v for v in g.values() if isinstance(v, dict) and "score" in v]
            done = sum(1 for v in fr_vals if v.get("quality_complete"))
            total = len(fr_ids) if fr_ids else len(fr_vals)
            score_str = f"{done}/{total} FRs" if total else "—"
            status = "✅ PASS" if (total and done == total) else "🔄 In Progress"
        else:
            score_str, status = "—", "⬜ Not Started"
        gate_rows.append(f"| Gate {gn} | {score_str} | {status} |")

    gate_table = "\n".join(gate_rows)

    # FR Registry rows (from gate1 results)
    gate1 = gates.get("gate1")
    fr_rows: list[str] = []
    if fr_ids:
        for fr_id in fr_ids:
            r = gate1.get(fr_id) if isinstance(gate1, dict) else None
            if isinstance(r, dict) and "score" in r:
                fr_score = f"{r['score']:.1f}"
                fr_status = "✅ COMPLETE" if r.get("quality_complete") else "🔄 In Progress"
            else:
                fr_score, fr_status = "—", "⬜ Pending"
            fr_rows.append(f"| {fr_id} | {fr_score} | {fr_status} |")
    fr_table_body = ("\n".join(fr_rows)
                     if fr_rows else "| — | — | No FRs registered yet |")

    # Optional sections (only when non-empty)
    extra_sections = ""
    arch = manifest.get("architecture_constraints", [])
    if arch:
        items = "\n".join(f"- {c}" for c in arch)
        extra_sections += f"\n### Architecture Constraints\n{items}\n"
    high_risk = manifest.get("high_risk_modules", [])
    if high_risk:
        items = "\n".join(f"- {m}" for m in high_risk)
        extra_sections += f"\n### High-Risk Modules\n{items}\n"
    nfr_map = manifest.get("nfr_dimension_mapping", {})
    if nfr_map:
        items = "\n".join(f"- {k} → {v}" for k, v in nfr_map.items())
        extra_sections += f"\n### NFR → Dimension Mapping\n{items}\n"

    return (
        f"## Harness Status _(auto-generated — do not edit this block)_\n\n"
        f"> Phase: **{current_phase} — {phase_name}**"
        f" | Last Gate: **Gate {last_gate}**{last_fr_str}"
        f" | Updated: {updated}\n\n"
        f"### Gate Progress\n"
        f"| Gate | Score / FRs | Status |\n"
        f"|------|-------------|--------|\n"
        f"{gate_table}\n\n"
        f"### FR Registry (Gate 1)\n"
        f"| FR ID | Score | Status |\n"
        f"|-------|-------|--------|\n"
        f"{fr_table_body}\n"
        f"{extra_sections}"
    )


def _update_claude_md(project_path: Path) -> None:
    """Refresh the harness-managed block in project_path/CLAUDE.md (non-blocking).

    Called at: init-project, finalize-gate (pass), advance-phase.
    Replaces content between <!-- harness:auto-start/end --> markers.
    Preserves all content outside the markers (user customizations).
    Legacy CLAUDE.md without markers: auto block prepended, existing content kept.
    """
    try:
        auto = _build_claude_md_auto_section(project_path)
        claude_path = project_path / "CLAUDE.md"

        if not claude_path.exists():
            claude_path.write_text(
                f"# Project: {project_path.name}\n\n"
                + _CLAUDE_AUTO_START + "\n" + auto + _CLAUDE_AUTO_END + "\n",
                encoding="utf-8",
            )
            return

        existing = claude_path.read_text(encoding="utf-8")
        if _CLAUDE_AUTO_START in existing and _CLAUDE_AUTO_END in existing:
            s = existing.index(_CLAUDE_AUTO_START)
            e = existing.index(_CLAUDE_AUTO_END) + len(_CLAUDE_AUTO_END)
            new_content = (
                existing[:s]
                + _CLAUDE_AUTO_START + "\n"
                + auto
                + _CLAUDE_AUTO_END
                + existing[e:]
            )
        else:
            # Legacy CLAUDE.md: prepend auto block, keep all existing content
            new_content = (
                _CLAUDE_AUTO_START + "\n"
                + auto
                + _CLAUDE_AUTO_END + "\n\n"
                + existing
            )
        claude_path.write_text(new_content, encoding="utf-8")
    except Exception as _exc:  # pylint: disable=broad-exception-caught
        print(f"  [WARN] CLAUDE.md update skipped: {_exc}")


# Patterns that indicate stale harness phase/gate status in manual content.
# Deliberately narrow to avoid false-positives on architecture descriptions.
_STALE_HARNESS_RE = re.compile(
    r"Current\s+state:.*Phase\s+\d"           # "Current state: Phase 7"
    r"|Working\s+in\s+Phase\s+\d"             # "Working in Phase 7+"
    r"|Gate\s+[1-4]\s+\(\d+\s+dimensions"     # "Gate 4 (14 dimensions..."
    r"|Gate\s+[1-4]\s+(?:PASS|FAIL)"          # "Gate 4 PASS"
    r"|score\s+\d+(?:\.\d+)?\s*\)",           # "score 96.5)"
    re.IGNORECASE,
)


def _llm_clean_stale_claude_md(project_path: Path) -> None:
    """Remove stale harness phase/gate status text from CLAUDE.md via LLM.

    Called only on advance-phase (major milestone, acceptable 30-60s overhead).
    Pre-screens for stale patterns — skips LLM call when content is already clean.
    Non-blocking: any failure prints [WARN] and returns without modifying the file.
    """
    import shutil as _shutil
    from core.agent_spawner import _child_env as _agent_child_env
    try:
        claude_path = project_path / "CLAUDE.md"
        if not claude_path.exists():
            return

        content = claude_path.read_text(encoding="utf-8")

        # Extract content outside auto block for stale pattern detection
        if _CLAUDE_AUTO_START in content and _CLAUDE_AUTO_END in content:
            s = content.index(_CLAUDE_AUTO_START)
            e = content.index(_CLAUDE_AUTO_END) + len(_CLAUDE_AUTO_END)
            outside = content[:s] + content[e:]
        else:
            outside = content

        # Pre-screen: skip LLM call if no stale harness patterns found
        if not _STALE_HARNESS_RE.search(outside):
            return

        cli = _shutil.which("claude")
        if not cli:
            return  # claude CLI unavailable — skip silently

        prompt = (
            "Edit the following CLAUDE.md file. Rules:\n"
            "1. The block between <!-- harness:auto-start --> and "
            "<!-- harness:auto-end --> is auto-managed — preserve it EXACTLY as-is.\n"
            "2. Outside that block, remove or condense into a single short line "
            "any text that describes harness phase/gate status — e.g. "
            "'Current state: Phase 7', 'Gate 4 PASS (score 96.5)', "
            "'Working in Phase 7+', phase-specific task lists, "
            "gate dimension counts, completed gate result paths.\n"
            "3. Keep all architecture descriptions, commands, code blocks, "
            "and non-harness-status content exactly unchanged.\n"
            "4. Return ONLY the complete updated file content — "
            "no explanation, no markdown fencing.\n\n"
            f"File:\n{content}"
        )

        proc = subprocess.run(
            [
                cli, "-p", prompt,
                "--output-format", "text",
                "--setting-sources", "",
                "--disable-slash-commands",
                "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
                "--no-session-persistence",
            ],
            capture_output=True, text=True, timeout=60,
            cwd=str(project_path),
            env=_agent_child_env(),
        )

        if proc.returncode != 0:
            print(f"  [WARN] CLAUDE.md stale cleanup failed (exit {proc.returncode})")
            return

        cleaned = proc.stdout.strip()
        if not cleaned:
            print("  [WARN] CLAUDE.md stale cleanup: empty LLM output — skipping")
            return

        # Safety: auto block must survive the LLM edit intact
        if _CLAUDE_AUTO_START not in cleaned or _CLAUDE_AUTO_END not in cleaned:
            print("  [WARN] CLAUDE.md stale cleanup: LLM dropped auto markers — skipping")
            return

        claude_path.write_text(cleaned, encoding="utf-8")
        print("  [CLAUDE.md] Stale harness status cleaned")

    except subprocess.TimeoutExpired:
        print("  [WARN] CLAUDE.md stale cleanup timed out — skipping")
    except Exception as _exc:  # pylint: disable=broad-exception-caught
        print(f"  [WARN] CLAUDE.md stale cleanup skipped: {_exc}")


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
            _record_gate1_score(project, phase, fr_id, gate_score)
        existing["last_gate"] = gate_num
        existing["last_fr"] = fr_id
        existing["last_update"] = datetime.now(timezone.utc).isoformat()
        # Record phase_truth_passed when the phase exit gate completes
        _current_phase = int(existing.get("current_phase", phase or 0))
        if gate_num == _PHASE_EXIT_GATES.get(_current_phase):
            existing["phase_truth_passed"] = True
        atomic_write_json(state_path, existing)

def _advance_fsm(project: Path, completed_phase: int,
                 last_gate: int | None = None,
                 last_fr: str | None = None) -> None:
    """Write state.json — the single source of truth for phase state.

    Local hooks, CI, and all harness commands read .methodology/state.json::current_phase.
    No other phase storage mechanisms exist.
    """
    from datetime import datetime, timezone
    from core.fsm.fsm import validate_fsm_state, FSMError

    next_phase = completed_phase + 1

    # 1. Prepare the full write set BEFORE anything becomes visible, then
    # publish HANDOVER.md + state.json in one StateTransaction (state.json
    # LAST — it is the authoritative file, so a partial commit can never
    # claim more progress than the artifacts on disk support). This is the
    # fix for the half-state class: the old order wrote state.json first
    # and only WARNed when HANDOVER regeneration failed afterwards, leaving
    # state advanced with a stale crash-recovery document (the P8→9 crash).
    # Cross-process locked (SG-12) so a parallel _update_state_checkpoint
    # or push-milestone state-write cannot corrupt the file.
    state_path = project / ".methodology" / "state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    with file_lock(state_lock_path(project)):
        existing_state = "INIT"
        state_data: dict = {}
        if state_path.exists():
            try:
                state_data = json.loads(state_path.read_text())
            except Exception:  # pylint: disable=broad-exception-caught
                state_data = {}
            else:
                try:
                    existing_state = validate_fsm_state(state_data.get("state", "INIT"))
                except FSMError as e:
                    print(f"\n  [FSM ERROR] {e}")
                    print("  Fix state.json manually or run `advance-phase` with a clean state.")
                    sys.exit(11)
        # Merge into the existing dict rather than replacing it — state.json also
        # carries fields this function doesn't own (last_push_checkpoint,
        # phase_completed, ci_readiness_ack, language, test_runner, ...); a bare
        # replacement here silently discarded them on every advance-phase call.
        state_data.update({
            "state": existing_state,
            "current_phase": next_phase,
            "last_gate": last_gate,
            "last_fr": last_fr,
            "last_update": datetime.now(timezone.utc).isoformat(),
            # P5-BUG-02: User expects phase_truth_passed to be True after advance-phase runs verify_phase_truth
            "phase_truth_passed": True,
            "last_milestone_command": f"advance-phase --completed-phase {completed_phase}",
        })

        # Render HANDOVER.md before any write — a render failure aborts the
        # advance with NOTHING published (previously it warned after state
        # was already advanced).
        gen = HandoverGenerator(project)
        handover_content = gen.render(
            checkpoint_id=f"P{next_phase}-entry-{datetime.now(timezone.utc).strftime('%Y%m%d')}",
            phase=next_phase,
            task_background=(
                f"Phase {completed_phase} completed. Advancing FSM to Phase {next_phase}."
            ),
            current_status=f"FSM advanced from Phase {completed_phase} to Phase {next_phase}.",
            next_steps=[
                f"Follow SKILL.md §0.1 Phase {next_phase} entry checklist",
                f"Read the Phase {next_phase} plan and execute",
            ],
            resume_phase=next_phase,
        )

        with StateTransaction(project) as txn:
            txn.stage_text(gen.handover_path, handover_content)
            txn.stage_json(state_path, state_data)   # authoritative file last
            txn.commit()

        # B5: Advance fr_progress.json inside the same lock so state.json and
        # fr_progress.json are always updated atomically from any reader's
        # perspective. Moving it outside created a window where another process
        # could see next_phase in state.json but the old phase in fr_progress.json.
        # SG-9: do not silently swallow exceptions — log to stderr so the
        # operator knows if state.json and fr_progress.json fall out of sync.
        # FileNotFoundError is expected for P1/P2 (no fr_progress.json yet).
        try:
            from harness.fr_progress_tracker import FRProgressTracker
            FRProgressTracker(project, phase=next_phase).advance_phase(next_phase)
        except FileNotFoundError:
            pass  # P1/P2 projects: fr_progress.json doesn't exist yet — expected.
        except Exception as exc:  # pylint: disable=broad-exception-caught
            print(
                f"  [WARN] FRProgressTracker.advance_phase failed: {type(exc).__name__}: {exc}\n"
                f"  state.json advanced to phase {next_phase}, but fr_progress.json may now\n"
                f"  be out of sync. Inspect .methodology/fr_progress.json and repair if needed.",
                file=sys.stderr,
            )
    print(f"  [FSM] state.json current_phase → {next_phase}")
    print(f"  [FSM] HANDOVER.md regenerated for Phase {next_phase}")

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
        "either complete Devil's Advocate challenge to justify the design (Tier 3 prerequisite) "
        "then re-run run-gate, OR reduce cross-package coupling so CRG detects sub-communities; "
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

def cmd_audit_phase(args: argparse.Namespace) -> int:
    """Audit a phase against GitHub or local artifacts (C1-C10 PhaseAuditor check)."""
    from scripts.phase_auditor import PhaseAuditor, GitHubFetcher, LocalFetcher

    project = getattr(args, "project", None)
    if project:
        # Local mode
        print(f"\n{'='*60}\naudit-phase [LOCAL]: Phase {args.phase} | project={project}\n{'='*60}")
        fetcher: "GitHubFetcher | LocalFetcher" = LocalFetcher(  # X|Y requires Python 3.10+
            project_root=project, branch=args.branch
        )
    else:
        # GitHub mode (original)
        print(f"\n{'='*60}\naudit-phase [GITHUB]: Phase {args.phase} | repo={args.repo}\n{'='*60}")
        fetcher = GitHubFetcher(repo=args.repo, branch=args.branch)
        repo_info = fetcher.get_repo_info()
        if not repo_info:
            print(f"[ERROR] Cannot access repo: {args.repo} (check gh auth status)")
            return 1

    auditor = PhaseAuditor(fetcher=fetcher, phase=args.phase)
    result = auditor.run_all_checks()

    print(f"\n{'─'*60}")
    print(f"Audit Results — Phase {args.phase}")
    print(f"{'─'*60}")
    print(f"  Score        : {result.score:.0f}%")
    print(f"  Verdict      : {result.verdict}")
    print(f"  Critical     : {len(result.criticals())}")
    print(f"  Warnings     : {len(result.warnings())}")

    if args.save:
        save_path = Path(args.save)
        if args.output == "json":
            import json as _json
            save_path.write_text(_json.dumps({
                "phase": args.phase, "score": result.score,
                "verdict": result.verdict,
                "criticals": len(result.criticals()),
                "warnings": len(result.warnings()),
                "findings": [{"severity": f.severity, "check": f.check_id,
                              "detail": f.detail}
                             for f in result.findings],
            }, indent=2))
        else:
            save_path.write_text(str(result))
        print(f"\nReport saved → {save_path}")

    if getattr(args, "fail_on_critical", False) and result.criticals():
        return 1
    return 0 if result.verdict != "FAIL" else 1

# ---------------------------------------------------------------------------
# verify-spec
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# doctor
# ---------------------------------------------------------------------------

def cmd_doctor(args: argparse.Namespace) -> int:
    """Read-only cross-file state consistency check. Reports, never repairs —
    an auto-repair path would itself become a fabrication surface."""
    from core.doctor import run_doctor

    project = Path(args.project).resolve()
    findings = run_doctor(project)

    print(f"\n{'='*60}\ndoctor  project={project}\n{'='*60}")
    if not findings:
        print("  OK: state / manifest / CLAUDE.md / attestation consistent; "
              "no interrupted transactions")
        return 0

    errors = 0
    for finding in findings:
        print(f"  [{finding.severity}] {finding.check}: {finding.message}")
        if finding.severity == "ERROR":
            errors += 1
    print(f"\n  {errors} error(s), {len(findings) - errors} other finding(s)")
    return 1 if errors else 0

# ---------------------------------------------------------------------------
# migrate-trace-overlay (PR 2 of closed-loop traceability plan)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# build-trace-attestation / verify-trace (PR 3)
# ---------------------------------------------------------------------------



# ---------------------------------------------------------------------------
# check-logic
# ---------------------------------------------------------------------------


def _print_constitution_result(result, composite_threshold, profile, phase: int, docs_path) -> int:
    """Print per-dimension breakdown + pass/fail verdict. Shared between
    directory-mode and single-file-mode branches of cmd_check_constitution.
    *docs_path* is the graded directory or single file; it is used to enumerate
    the exact keywords behind each sub-threshold *active* dimension so a fixing
    agent adds content instead of reverse-engineering the gap (same idiom as the
    advance-phase postflight). Returns 0 on pass, 1 on fail.
    """
    from core.quality_gate.constitution.runner import missing_keywords

    # Only the active (composite-scored) dimensions gate the phase; display-only
    # dims (e.g. security on a P2 architecture doc) are shown but must NOT drive
    # keyword advice, or agents chase irrelevant terms into the wrong document.
    _active = set(profile.active_dimensions(phase))
    print(f"\n  Score: {result.score:.0f}%  (threshold={composite_threshold:.0f}%)")
    for dim, score in sorted(result.dimensions.items()):
        dim_threshold = profile.dimension_threshold(dim, phase)
        status = "✓" if score >= dim_threshold else "✗"
        suffix = ""
        if score < dim_threshold and dim in _active and docs_path is not None:
            _miss = missing_keywords(str(docs_path), dim, phase)
            if _miss:
                suffix = f"  ·  missing: {', '.join(_miss)}"
        print(f"    {status} {dim}: {score:.0f}%  (threshold={dim_threshold:.0f}%){suffix}")

    if result.violations:
        # result.violations flags any per-dimension score below its own
        # threshold (100% for P1-P4), which is independent from the
        # composite gate above (bottleneck min-of-dimensions vs
        # composite_threshold, e.g. 80%). A dimension can appear here while
        # the overall gate still PASSES — label accordingly so "Violations"
        # doesn't misread as a blocking failure when it isn't one.
        _label = "Violations" if not result.passed else "Sub-threshold notes (informational — composite already PASSED)"
        print(f"\n  {_label} ({len(result.violations)}):")
        for v in result.violations[:10]:
            print(f"    - [{v.get('dimension', '?')}] {v.get('message', str(v))[:120]}")
        if len(result.violations) > 10:
            print(f"    ... and {len(result.violations) - 10} more")

    if result.passed:
        print(f"\n  [PASS] Constitution quality ≥ {composite_threshold:.0f}% ✓")
        return 0
    print(f"\n  [FAIL] Constitution quality {result.score:.0f}% < {composite_threshold:.0f}%")
    print("  Add substantive coverage of the missing keywords listed above, then re-run check-constitution until PASS.")
    return 1



# ---------------------------------------------------------------------------
# init-project
# ---------------------------------------------------------------------------

def _harness_workflow_template() -> str:
    """Return the content of .github/workflows/harness_quality_gate.yml for a target project.

    Reads directly from templates/harness_quality_gate.yml — the single source of truth.
    init-project and harness-init.sh both deploy the same file, so there is no drift.
    """
    template_path = Path(__file__).parent / "templates" / "harness_quality_gate.yml"
    if not template_path.exists():
        raise FileNotFoundError(
            f"Workflow template not found: {template_path}\n"
            "Ensure templates/harness_quality_gate.yml exists in the harness-methodology repo."
        )
    return template_path.read_text(encoding="utf-8")

# Canonical phase directory names — sourced from the topology SSOT
# (core/phase_topology.py), shared by _init_phase_dirs and cmd_audit_structure.
_PHASE_DIRS: dict[int, str] = _TOPOLOGY_PHASE_DIRS

# Sub-directories created inside phase dirs on init (not tracked for naming checks).
_PHASE_INIT_SUBDIRS: list[str] = [
    "02-architecture/adr",
    "03-development/src",
    "03-development/tests",
]

def _init_phase_dirs(project: Path) -> None:
    """Create canonical 0X-name/ phase directory structure in target project."""
    dirs = [*_PHASE_DIRS.values(), *_PHASE_INIT_SUBDIRS]
    created = 0
    skipped = 0
    for d in dirs:
        target = project / d
        if target.exists():
            skipped += 1
        else:
            target.mkdir(parents=True, exist_ok=True)
            created += 1
    if created:
        print(f"   OK — created {created} director{'y' if created == 1 else 'ies'} ({skipped} already existed)")
    else:
        print(f"   SKIP: all {skipped} directories already exist")

def _init_copy_templates(project: Path, harness_root: Path, *, overwrite: bool = False) -> None:
    """Copy artifact templates from harness templates/ into the target project."""
    templates_dir = harness_root / "templates"
    artifact_map = [
        ("01-requirements", "SRS.md"),
        ("01-requirements", "SPEC_TRACKING.md"),
        ("01-requirements", "TRACEABILITY_MATRIX.md"),
        ("", "TEST_INVENTORY.yaml"),       # project root — D4 reads from here
        ("02-architecture", "SAD.md"),
        ("02-architecture/adr", "ADR.md"),
        ("02-architecture", "TEST_SPEC.md"),
        ("09-maintenance", "MAINTENANCE_LOG.md"),  # P9 CR index (cr-close appends)
    ]
    copied = 0
    skipped = 0
    missing = 0
    protected = 0
    for subdir, filename in artifact_map:
        src = templates_dir / filename
        dst = project / subdir / filename
        if dst.exists() and not overwrite:
            skipped += 1
        elif not src.exists():
            print(f"   WARNING: template not found: {src}")
            missing += 1
        elif dst.exists() and dst.read_bytes() != src.read_bytes():
            # Deliverable differs from its template → authored in-flight state.
            # Never overwritten, even with --overwrite — mirrors the state.json
            # never-reset rule (integration-test E2E clobber, 2026-07-02).
            print(
                f"   PROTECTED: {dst} differs from template (authored content); "
                "not overwritten — delete the file manually to re-template it."
            )
            protected += 1
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            copied += 1

    # CLAUDE.md.template → project/CLAUDE.md (only if no CLAUDE.md exists).
    # An existing CLAUDE.md is never re-copied, even with --overwrite: the
    # harness auto block is refreshed in place by _update_claude_md, and a
    # wholesale re-copy only destroys user custom sections below the block.
    claude_tmpl = harness_root / "CLAUDE.md.template"
    claude_dst = project / "CLAUDE.md"
    if claude_dst.exists():
        skipped += 1
    elif claude_tmpl.exists():
        shutil.copy2(claude_tmpl, claude_dst)
        # Substitute {PROJECT_NAME} so the header is immediately readable
        try:
            raw = claude_dst.read_text(encoding="utf-8").replace(
                "{PROJECT_NAME}", project.name
            )
            claude_dst.write_text(raw, encoding="utf-8")
        except Exception:  # pylint: disable=broad-exception-caught
            pass
        copied += 1
    else:
        missing += 1

    parts = []
    if copied:
        parts.append(f"copied {copied} template{'s' if copied != 1 else ''}")
    if skipped:
        parts.append(f"{skipped} already existed")
    if protected:
        parts.append(f"{protected} authored (protected)")
    if missing:
        parts.append(f"{missing} template(s) not found")
    if parts:
        print(f"   OK — {', '.join(parts)}")
    else:
        print("   SKIP: nothing to copy")


def _init_js_toolchain(
    project: Path,
    harness_root: Path,
    language: str,
    test_runner: str | None,
    *,
    overwrite: bool = False,
) -> None:
    """Copy the pinned JS/TS quality-toolchain templates into the project.

    package.json is MERGED (existing devDependencies/scripts win — the project
    owns its versions; the template only fills gaps). Config files are copied
    only when absent (or --overwrite). Gate commands use `npx --no-install`,
    so `npm ci` must run after this step.
    """
    src_dir = harness_root / "templates" / "js_toolchain"
    if not src_dir.is_dir():
        print(f"   WARNING: {src_dir} not found — skipping JS toolchain setup")
        return

    # 1. Merge devDependencies/scripts into package.json
    pkg_path = project / "package.json"
    tmpl = json.loads((src_dir / "package.json").read_text(encoding="utf-8"))
    try:
        pkg = json.loads(pkg_path.read_text(encoding="utf-8")) if pkg_path.exists() else {}
    except json.JSONDecodeError:
        print(f"   WARNING: {pkg_path} is not valid JSON — skipping merge")
        pkg = None
    if pkg is not None:
        added: list[str] = []
        for section in ("devDependencies", "scripts"):
            merged = dict(tmpl.get(section, {}))
            merged.update(pkg.get(section, {}))  # existing entries win
            added += [k for k in merged if k not in pkg.get(section, {})]
            pkg[section] = merged
        pkg_path.write_text(
            json.dumps(pkg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        print(f"   OK — package.json merged ({len(added)} entries added)")

    # 2. Config files — copy when absent
    files = ["eslint.config.mjs", "stryker.conf.json", "benchmarks/run.mjs"]
    if test_runner != "jest":
        files.append("vitest.config.ts")
    files.append("tsconfig.json" if language == "typescript" else "tsconfig.checkjs.json")
    copied = 0
    for rel in files:
        src, dst = src_dir / rel, project / rel
        if dst.exists() and not overwrite:
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        copied += 1
    print(f"   OK — {copied} toolchain config(s) copied")
    print("   NEXT: run `npm ci` in the project — gate commands use "
          "`npx --no-install` and fail without installed devDependencies.")


def _setup_branch_protection(project: Path) -> int:
    """Configure GitHub branch protection for main with required status checks.

    Requires:
      - gh CLI installed and authenticated
      - Remote 'origin' pointing to a GitHub repository

    Returns 0 on success, 1 on failure.
    """
    import subprocess

    # Detect GitHub remote URL
    try:
        remote = subprocess.run(
            ["git", "-C", str(project), "remote", "get-url", "origin"],
            capture_output=True, text=True, timeout=10,
        )
        if remote.returncode != 0 or not remote.stdout.strip():
            print("   ERROR: No git remote 'origin' found.")
            return 1
        remote_url = remote.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        print("   ERROR: Failed to read git remote.")
        return 1

    # Parse owner/repo from common URL formats
    owner = repo = None
    if remote_url.startswith("https://github.com/"):
        parts = remote_url.rstrip(".git").split("/")
        if len(parts) >= 2:
            owner, repo = parts[-2], parts[-1]
    elif remote_url.startswith("git@github.com:"):
        parts = remote_url.rstrip(".git").split(":")
        if len(parts) == 2:
            parts2 = parts[1].split("/")
            if len(parts2) == 2:
                owner, repo = parts2[0], parts2[1]
    elif "github.com" in remote_url:
        # Fallback: use gh repo view to parse
        try:
            rv = subprocess.run(
                ["gh", "repo", "view", "--json", "name,owner"],
                capture_output=True, text=True, timeout=10, cwd=str(project),
            )
            if rv.returncode == 0:
                import json as _json
                data = _json.loads(rv.stdout)
                owner, repo = data["owner"]["login"], data["name"]
        except Exception:
            pass

    if not owner or not repo:
        print("   ERROR: Could not parse GitHub owner/repo from remote URL.")
        print(f"   Remote: {remote_url}")
        print("   Use --repo OWNER/REPO to specify explicitly.")
        return 1

    print(f"   Remote: {owner}/{repo}")

    # Verify gh is authenticated
    try:
        auth_check = subprocess.run(
            ["gh", "auth", "status"], capture_output=True, text=True, timeout=10,
        )
        if auth_check.returncode != 0:
            print("   ERROR: gh CLI not authenticated. Run: gh auth login")
            return 1
    except FileNotFoundError:
        print("   ERROR: gh CLI not installed. Install GitHub CLI:")
        print("     brew install gh  (macOS)")
        print("     sudo apt install gh  (Linux)")
        return 1

    api_url = f"repos/{owner}/{repo}/branches/main/protection"
    # Direct-push model: only force-push and deletion protection are enabled.
    # PR-only fields must be present (GitHub PUT requires them) but set to disabled.
    payload = {
        "required_status_checks": None,
        "enforce_admins": False,
        "required_pull_request_reviews": None,
        "required_linear_history": False,
        "allow_force_pushes": False,
        "allow_deletions": False,
        "restrictions": None,
    }

    try:
        result = subprocess.run(
            ["gh", "api", api_url, "--method", "PUT",
             "--input", "-"],
            input=json.dumps(payload),
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            print(f"   OK — Branch protection configured for {owner}/{repo}/main")
            print("   Direct-push model: force pushes + deletions blocked.")
            _verify_no_pr_requirement(owner, repo)
            return 0
        else:
            err = result.stderr.strip() or result.stdout.strip()
            # 404 often means branch protection already exists; try PATCH
            if "404" in err or "Not Found" in err:
                # Update existing protection
                result2 = subprocess.run(
                    ["gh", "api", api_url, "--method", "PATCH",
                     "--input", "-"],
                    input=json.dumps(payload),
                    capture_output=True, text=True, timeout=30,
                )
                if result2.returncode == 0:
                    print(f"   OK — Branch protection updated for {owner}/{repo}/main (direct-push model)")
                    _verify_no_pr_requirement(owner, repo)
                    return 0
                err = result2.stderr.strip() or result2.stdout.strip()
            print(f"   ERROR: Failed to set branch protection:\n   {err[:500]}")
            return 1
    except subprocess.TimeoutExpired:
        print("   ERROR: API call timed out.")
        return 1

def _verify_no_pr_requirement(owner: str, repo: str) -> None:
    """Warn if branch protection has PR requirement — incompatible with direct-push.

    Best-effort: prints a [WARN] to stderr (not silent) when gh CLI is unavailable
    or the protection endpoint fails, so operators can see why verification was
    skipped.
    """
    import subprocess as _sp
    try:
        r = _sp.run(
            ["gh", "api", f"repos/{owner}/{repo}/branches/main/protection"],
            capture_output=True, text=True, timeout=15,
        )
        if r.returncode != 0:
            print(
                f"   [WARN] PR-requirement verification skipped — gh api returned "
                f"non-zero exit ({r.returncode}). Verify manually: GitHub repo → "
                f"Settings → Branches → 'Require a pull request' must be OFF.",
                file=sys.stderr,
            )
            return
        cfg = json.loads(r.stdout)
        pr_reviews = cfg.get("required_pull_request_reviews")
        if pr_reviews:
            print(f"   WARNING: 'Require a pull request' is still enabled on {owner}/{repo}/main.")
            print("   This will block push-checkpoint. Disable it manually:")
            print("     GitHub repo → Settings → Branches → Edit (main)")
            print("     → Uncheck 'Require a pull request before merging'")
    except (FileNotFoundError, _sp.TimeoutExpired, json.JSONDecodeError) as exc:
        print(
            f"   [WARN] PR-requirement verification skipped: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )

def _check_crg_available() -> bool:
    """Check whether CRG MCP server is reachable.

    CRG (Code Review Graph) is mandatory for Gate 3/4 structural dimensions
    (architecture, error_handling). The core tools (build, detect_changes,
    minimal_context) are imported at module level in harness/crg_bridge.py via
    ``from mcp_tools import ...`` — if the CRG MCP server is not configured,
    the import fails and the bridge is unavailable.
    """
    try:
        __import__("harness.crg_bridge")
        return True
    except (ImportError, ModuleNotFoundError):
        return False


def _check_and_offer_ecc_hooks(harness_root: Path) -> None:
    """Check for ECC hooks presence and offer to install if missing.

    ECC hooks intercept tool calls at the Claude Code session layer,
    providing a bypass-proof safety net against ``git --no-verify``.
    """
    hooks_file = Path.home() / ".claude" / "hooks" / "hooks.json"
    if hooks_file.exists():
        try:
            data = json.loads(hooks_file.read_text(encoding="utf-8"))
            if "pre:bash:dispatcher" in data:
                print("   OK — ECC hooks present (git --no-verify blocked at session layer)")
                return
            print("   WARNING: ECC hooks file exists but pre:bash:dispatcher hook is missing.")
        except Exception:
            print("   WARNING: ECC hooks file exists but is unreadable.")
    else:
        print("   WARNING: ECC hooks not installed — git --no-verify is NOT blocked.")

    # Offer installation
    setup_script = harness_root / "scripts" / "setup-ecc-hooks.sh"
    if setup_script.exists():
        print(f"   Install: bash {setup_script}")
        print(f"   Verify:  bash {setup_script} --verify")
    else:
        print("   Setup script not found in harness installation.")


def _auto_offer_branch_protection(project: Path) -> None:
    """Auto-detect gh CLI and offer to set up branch protection.

    When gh is available and authenticated, offers interactive setup.
    Otherwise prints the manual setup guide so the operator can configure
    protection via GitHub's web UI.
    """
    import subprocess
    # Check gh availability
    try:
        gh_check = subprocess.run(
            ["gh", "auth", "status"], capture_output=True, text=True, timeout=10,
        )
        if gh_check.returncode != 0:
            _print_manual_branch_protection_guide()
            return
    except FileNotFoundError:
        _print_manual_branch_protection_guide()
        return
    except subprocess.TimeoutExpired:
        print("   WARNING: gh CLI check timed out — skipping auto-setup.")
        _print_manual_branch_protection_guide()
        return

    # gh is available — offer setup
    print("   gh CLI detected and authenticated.")
    try:
        remote_check = subprocess.run(
            ["git", "-C", str(project), "remote", "get-url", "origin"],
            capture_output=True, text=True, timeout=10,
        )
        if remote_check.returncode != 0 or "github.com" not in remote_check.stdout:
            print("   SKIP: git remote 'origin' not pointing to GitHub.")
            return
    except Exception:
        print("   SKIP: cannot detect git remote.")
        return

    print("   Setting up branch protection automatically...")
    rc = _setup_branch_protection(project)
    if rc != 0:
        _print_manual_branch_protection_guide()


def _print_manual_branch_protection_guide() -> None:
    """Print manual branch protection setup instructions for GitHub web UI."""
    print("   ═══════════════════════════════════════════════════════════════")
    print("   Set up GitHub branch protection manually:")
    print("     Settings → Branches → Add branch protection rule")
    print("     Branch name pattern: main")
    print("     ✅ Block force pushes")
    print("     ✅ Block deletions")
    print("     ❌ Do NOT enable 'Require a pull request'")
    print("     ❌ Do NOT enable 'Require status checks'")
    print("   ═══════════════════════════════════════════════════════════════")
    print("   Or install gh CLI for automatic setup:")
    print("     brew install gh && gh auth login")
    print("     Then re-run: python3 harness_cli.py init-project --project . --setup-branch-protection")



def cmd_init_project(args: argparse.Namespace) -> int:
    """
    Initialize harness CI wiring in a target project (Context B setup).

    Automates INTEGRATION.md §3 steps:
      1. Verify harness is importable from the target project
      2. Write .github/workflows/harness_quality_gate.yml
      3. Optionally run setup-git-hooks.sh
      4. Initialize .methodology/state.json (phase source of truth)
      5. Print drift monitor crontab suggestion
    """
    import subprocess  # imported here (not at module level) to keep startup cost low

    project = Path(args.project).resolve()
    phase = args.phase
    harness_root = Path(__file__).parent.resolve()

    # Resolve project language before any writes — every later gate run reads
    # the persisted value from state.json (toolchain resolution, S2 checks).
    from harness.toolchains import (
        detect_language,
        detect_test_runner,
        supported_languages,
    )
    language = getattr(args, "language", None)
    if language is None:
        language = detect_language(project)
        if language is None:
            print(
                "[BLOCKED] Ambiguous project language: both Python and JS/TS "
                "manifests found.\n"
                "          Re-run with an explicit flag, e.g.: "
                "init-project --language typescript"
            )
            return 1
    if language not in supported_languages():
        print(
            f"[BLOCKED] Unsupported language '{language}'. "
            f"Registered toolchains: {', '.join(supported_languages())}\n"
            "          See docs/ADDING_LANGUAGE_SUPPORT_SOP.md to register a "
            "new language toolchain."
        )
        return 1
    test_runner = getattr(args, "test_runner", None)
    if test_runner is None and language in ("javascript", "typescript"):
        test_runner = detect_test_runner(project)
        if test_runner is None:
            print(
                "   WARNING: could not detect a unique test runner (vitest/jest) "
                "from package.json.\n"
                "            Coverage/benchmark dimensions resolve to the vitest "
                "toolchain by default; pass --test-runner to override."
            )

    print(f"\n{'='*60}")
    print(f"init-project  target={project}  phase={phase}  language={language}"
          + (f"  test_runner={test_runner}" if test_runner else ""))
    print(f"{'='*60}")

    # 1. Verify harness is importable
    print("\n[1/11] Checking harness importability...")
    importable = (
        (project / "harness" / "core" / "quality_gate" / "__init__.py").exists()
        or (project / "core" / "quality_gate" / "__init__.py").exists()
        or (project / "harness_cli.py").exists()
        or (project / "harness" / "harness_cli.py").exists()
    )
    if importable:
        print("   OK — harness is importable")
    else:
        print("   WARNING: harness not found in target project.")
        print(f"   Run:  git submodule add {harness_root} {project}/harness")
        print(f"   Or:   export PYTHONPATH=\"{harness_root}:$PYTHONPATH\"")
        if not args.overwrite:
            return 1

    # 1b. Submodule layout: create harness_cli.py root wrapper so every plan
    #     command (`python3 harness_cli.py ...`) works from the project root
    #     without any path adjustment.  Only written when harness lives at
    #     project/harness/ (submodule) and no wrapper exists yet.
    _WRAPPER_MARKER = "# auto-generated by init-project (harness submodule layout)"
    _submodule_cli = project / "harness" / "harness_cli.py"
    _root_cli = project / "harness_cli.py"
    _root_cli_is_ours = (
        _root_cli.exists()
        and _WRAPPER_MARKER in _root_cli.read_text(encoding="utf-8")
    )
    if _submodule_cli.exists():
        if not _root_cli.exists() or _root_cli_is_ours or args.overwrite:
            print("\n[1b/11] Writing harness_cli.py root wrapper (submodule layout)...")
            _root_cli.write_text(
                f'{_WRAPPER_MARKER}\n'
                '"""Delegates `python3 harness_cli.py <cmd>` to harness/harness_cli.py.\n'
                'Auto-generated by `init-project`; do not edit manually.\n'
                'Re-generate: python3 harness/harness_cli.py init-project --project . --overwrite\n'
                '"""\n'
                'import subprocess, sys, pathlib\n'
                '_target = pathlib.Path(__file__).parent / "harness" / "harness_cli.py"\n'
                'raise SystemExit(\n'
                '    subprocess.run(\n'
                '        [sys.executable, str(_target), *sys.argv[1:]]\n'
                '    ).returncode\n'
                ')\n',
                encoding="utf-8",
            )
            print(f"   OK — wrote {_root_cli}")
        else:
            print("\n[1b/11] harness_cli.py root wrapper...")
            print(f"   SKIP: {_root_cli} exists and was not created by init-project")
            print("         (use --overwrite to replace it)")

    # 2. Write CI workflow
    print("\n[2/11] Writing CI workflow...")
    workflows_dir = project / ".github" / "workflows"
    workflows_dir.mkdir(parents=True, exist_ok=True)
    workflow_path = workflows_dir / "harness_quality_gate.yml"
    if workflow_path.exists() and not args.overwrite:
        print(f"   SKIP: {workflow_path} already exists (use --overwrite to overwrite)")
    else:
        try:
            workflow_path.write_text(_harness_workflow_template())
        except FileNotFoundError as e:
            print(f"   ERROR: Cannot write CI workflow — {e}")
            print("   The template file is missing from the harness installation.")
            print("   Re-run harness-init.sh or ensure templates/harness_quality_gate.yml exists.")
            return 1
        print(f"   OK — wrote {workflow_path}")

    # 3. Git hooks
    print("\n[3/11] Git hooks...")
    hooks_script = harness_root / "scripts" / "setup-git-hooks.sh"
    if args.ci_only:
        print("   SKIP: --ci-only flag set (hooks not installed)")
    elif not hooks_script.exists():
        print(f"   WARNING: {hooks_script} not found — skipping hooks")
    else:
        hooks_dir = project / ".git" / "hooks"
        if (hooks_dir / "prepare-commit-msg").exists() and not args.overwrite:
            print("   SKIP: hooks already installed (use --overwrite to reinstall)")
        else:
            result = subprocess.run(
                ["bash", str(hooks_script)],
                cwd=str(project),
                input=f"{phase}\ny\n",  # auto-answer prompts
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                print("   OK — git hooks installed")
            else:
                print(f"   WARNING: hook install failed:\n{result.stderr[-500:]}")

    # 4. Phase state — managed via .methodology/state.json (written in step 7).
    #    The deprecated `git config quality.phase` knob is no longer set;
    #    state.json is the single source of truth read by hooks and CI.
    print("\n[4/11] Phase state...")
    print(f"   OK — phase {phase} will be written to .methodology/state.json (step 7)")

    # 5. Create canonical phase directory structure
    print("\n[5/11] Creating phase directory structure...")
    _init_phase_dirs(project)

    # 6. Copy template artifacts into phase directories
    print("\n[6/11] Copying artifact templates...")
    _init_copy_templates(project, harness_root, overwrite=args.overwrite)

    # 6a. Initialize .gitignore with harness runtime + dev artifact entries
    # (prevents pipeline-mode `git add -A` from committing .venv/ — semgrep-core
    # is 197MB and trips GH001 large-file rejection; bug discovered during
    # integration-test E2E bootstrap, 2026-06-15)
    print("\n[6a/11] Initializing .gitignore for pipeline mode...")
    from harness.git_strategy import GitStrategy
    _git_ignore_helper = GitStrategy(project, enabled=True, push=False)
    _git_ignore_helper.ensure_gitignore()

    # 6b. JS/TS quality toolchain (pinned devDeps + lint/type/test/bench configs)
    if language in ("javascript", "typescript"):
        print("\n[6b/11] Setting up JS/TS quality toolchain...")
        _init_js_toolchain(
            project, harness_root, language, test_runner, overwrite=args.overwrite
        )

    # 7. Initialize FSM state.json (required by run-phase preflight)
    print("\n[7/11] Initializing FSM state...")
    from datetime import datetime, timezone
    state_path = project / ".methodology" / "state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    if state_path.exists():
        # state.json is the FSM source of truth — never overwrite it, even with
        # --overwrite.  Overwriting mid-project would reset current_phase to 1,
        # destroying phase progress.  --overwrite is intentionally scoped to
        # templates / CI workflow / harness_cli.py wrapper, not FSM state.
        print(f"   SKIP: {state_path} already exists (FSM state is never reset by init-project; delete it manually to reinitialize)")
        try:
            _existing_state = json.loads(state_path.read_text(encoding="utf-8"))
            _existing_lang = _existing_state.get("language", "python")
        except (json.JSONDecodeError, OSError):
            _existing_lang = "python"
        if _existing_lang != language:
            print(
                f"   WARNING: persisted language '{_existing_lang}' differs from "
                f"requested/detected '{language}' — keeping '{_existing_lang}'. "
                f"A project cannot change toolchain mid-flight."
            )
        language = _existing_lang
    else:
        _state: dict = {
            "state": "RUNNING",
            "current_phase": phase,
            "last_gate": None,
            "last_fr": None,
            "last_update": datetime.now(timezone.utc).isoformat(),
            "language": language,
        }
        if test_runner:
            _state["test_runner"] = test_runner
        atomic_write_json(state_path, _state)
        print(f"   OK — state.json initialized (phase={phase}, language={language})")
    # Refresh CLAUDE.md harness status block now that state.json exists
    _update_claude_md(project)

    # 7a. Initialize trace attestation.json (required by pre-commit-check trace_dirt probe).
    # Without it, every fresh project's first commit fails pre-flight on
    # `attestation.json missing` — discovered during integration-test E2E
    # bootstrap, 2026-06-17. `--overwrite` re-creates it (cheap; SAD.md is
    # not yet authored at init-project time so the matrix is empty).
    print("\n[7a/11] Initializing trace attestation...")
    from scripts.build_trace_attestation import (
        build_attestation,
        write_attestation,
    )
    attestation_path = project / ".methodology" / "trace" / "attestation.json"
    if attestation_path.exists() and not args.overwrite:
        print(f"   SKIP: {attestation_path} already exists")
    else:
        _attestation = build_attestation(project)
        _canonical_path, _ = write_attestation(project, _attestation)
        print(f"   OK — wrote {_canonical_path}")

    # 8. Drift monitor hint
    print("\n[8/11] Drift Monitor hint (optional cronjob)")
    print("  Add this crontab entry (edit with: crontab -e):")
    print(f"  0 * * * * DRIFT_PROJECT_PATH={project} \\")
    print(f"    python3 {harness_root}/scripts/cron_drift_monitor.py \\")
    print(f"    >> {project}/logs/drift_monitor.log 2>&1")

    # 9. ECC hooks (Claude Code session layer — blocks git --no-verify)
    print("\n[9/11] ECC hooks (git --no-verify blocker)...")
    _check_and_offer_ecc_hooks(harness_root)

    # 10. Branch protection (GitHub server-side — bypass-proof)
    print("\n[10/11] GitHub branch protection...")
    if args.setup_branch_protection:
        rc = _setup_branch_protection(project)
        if rc != 0:
            _print_manual_branch_protection_guide()
    else:
        # Auto-detect gh availability and offer setup
        _auto_offer_branch_protection(project)

    # 11. Gate tool availability (blocking — all Tier 1 tools required before project start).
    # Driven by gate YAMLs so new requires_tool_execution entries are auto-detected.
    print("\n[11/11] Gate tool availability check...")
    _missing_init: list[str] = []
    for _gate_num in (1, 2, 3, 4):
        # Gate configs come from the harness checkout; the language comes from
        # the target project's freshly written state.json (state_root).
        _, _missing = _verify_gate_tools(
            _gate_num, str(harness_root), state_root=str(project)
        )
        for _m in _missing:
            if _m not in _missing_init:
                _missing_init.append(_m)
    if _missing_init:
        print("  [BLOCKED] Required Tier 1 gate tools are not installed:")
        for _m in _missing_init:
            print(f"    ✗ {_m}")
        print(
            "\n  All tools must be available before starting the project.\n"
            "  tool_score=null is not accepted for Tier 1/2 dimensions (score.py R8).\n"
            "  Install commands:\n"
            "    pip install ruff mypy pytest pytest-cov 'mutmut<3'  # mutmut 2.x (3.x incompatible with most project layouts)\n"
            "    pip install scancode-toolkit\n"
            "    brew install gitleaks  # or: go install github.com/gitleaks/gitleaks/v8@latest\n"
            "  Re-run init-project after installing."
        )
        return 1
    print("  OK — all required gate tools are available.")

    # CRG (Code Review Graph) — mandatory for Gate 3/4 structural dimensions.
    # Core tools (build, detect_changes, minimal_context) are imported at module
    # level in crg_bridge.py — import failure means CRG MCP is not configured.
    _crg_ok = _check_crg_available()
    if _crg_ok:
        print("  OK — CRG (Code Review Graph) MCP server reachable (Gate 3/4 ready)")
    else:
        print("  INFO: CRG MCP server not detected.")
        print("        CRG is mandatory for Gate 3/4 (same tier as ruff/mypy/pytest).")
        print("        prepare_gate() will fail for Gates 3/4 if CRG is not installed.")
        print("        Install before reaching P4:")
        print("          pip install code-review-graph")
        print("          code-review-graph register  # registers repo in ~/.code-review-graph/")
        print("        OK to proceed with P1/P2 — CRG is not required for these phases.")

    # Phase-aware human checklist
    _checklist: list[str] = [
        "  ╔══════════════════════════════════════════════════════════════╗",
        f"  ║  HUMAN CHECKLIST — Phase {phase} — verify before starting         ║",
        "  ╠══════════════════════════════════════════════════════════════╣",
        "  ║  [ ] Tier 1 tools installed (ruff, mypy, pytest-cov, ...)   ║",
        "  ║  [ ] gitleaks installed (secrets scanning)                  ║",
        "  ║  [ ] GitHub branch protection enabled on main               ║",
        "  ║      → Settings → Branches → main → Block force push+delete ║",
        "  ║  [ ] ECC hooks installed (blocks git --no-verify)           ║",
        "  ║      → bash scripts/setup-ecc-hooks.sh --verify             ║",
    ]
    if phase == 1:
        _checklist += [
            "  ║  [ ] SRS.md written with ### FR-XX: sections                ║",
            "  ║  [ ] SPEC_TRACKING.md + TRACEABILITY_MATRIX.md ready        ║",
        ]
    elif phase == 2:
        _checklist += [
            "  ║  [ ] SAD.md + ADR.md written (architecture design)          ║",
            "  ║  [ ] TEST_SPEC.md ready (from derive_test_cases.md)         ║",
        ]
    else:
        _checklist += [
            "  ║  [ ] Phase entry deliverables ready (see SKILL.md §1)       ║",
        ]
    _checklist += [
        "  ║  [ ] Review generated templates in phase directories        ║",
        "  ╚══════════════════════════════════════════════════════════════╝",
    ]
    print(f"\n{'='*60}")
    print("init-project complete.")
    print(f"{'='*60}")
    print(f"  Phase {phase} → .methodology/state.json")
    print()

    # Improvement C: SAB auto-amend on P3 init.
    # P3 introduces new modules under 03-development/src/; previously the user
    # had to hand-edit .methodology/SAB.json to register them, otherwise
    # `_check_sab_module_alignment` would BLOCK the gate. Run the amender so
    # the manifest is in sync with the source tree before the user starts TDD.
    if phase == 3:
        try:
            from core.quality_gate.sab_amender import amend_sab
            added = amend_sab(project)
            if added:
                print(f"[SAB AMEND] Added {len(added)} module(s) to "
                      ".methodology/SAB.json:")
                for m in added:
                    print(f"  + {m}")
                print("  Review layer assignment and commit SAB.json.")
            else:
                print("[SAB] No new modules to register (in sync).")
        except Exception as exc:  # amend is best-effort, never blocks init
            print(f"[SAB AMEND] Warning: amend failed: {exc}")

    for line in _checklist:
        print(line)
    print(f"  Full docs: {harness_root}/INTEGRATION.md")
    return 0


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


def cmd_amend_sab(args: argparse.Namespace) -> int:
    """Run the SAB Architecture Amendment Protocol as a standalone subcommand.

    `run-gate --gate 1` blocks with `[BLOCKED] Architecture Amendment Protocol
    violation` when 03-development/src/ has modules absent from any SAB layer.
    The amend logic already runs inside `init-project --phase 3`, but a Phase 3
    TDD/GATE1 agent that sees the [BLOCKED] message has no CLI to recover —
    it has to either hand-edit SAB.json or restart init-project. This wraps
    `core.quality_gate.sab_amender.amend_sab` so any agent can self-heal:

        python3 harness_cli.py amend-sab --project .
        python3 harness_cli.py amend-sab --project . --dry-run
        python3 harness_cli.py amend-sab --project . --src-dir src

    Idempotent: re-running adds nothing on the second call.
    Returns 0 on success (including no-op), 1 on hard failure.
    """
    project = Path(args.project).resolve()
    try:
        from core.quality_gate.sab_amender import amend_sab
        added = amend_sab(project, src_dir=args.src_dir, dry_run=args.dry_run)
    except Exception as exc:
        print(f"[amend-sab] failed: {exc}", file=sys.stderr)
        return 1

    if args.dry_run:
        if added:
            print(f"[amend-sab] dry-run: would add {len(added)} module(s):")
            for m in added:
                print(f"  + {m}")
        else:
            print("[amend-sab] dry-run: SAB is already in sync.")
        return 0

    if added:
        print(f"[amend-sab] Added {len(added)} module(s) to .methodology/SAB.json:")
        for m in added:
            print(f"  + {m}")
        print("  Review layer assignment, then commit SAB.json before re-running run-gate.")
    else:
        print("[amend-sab] SAB already in sync with 03-development/src/.")
    return 0


def cmd_kill_switch(args: argparse.Namespace) -> int:
    """CLI surface for the M1 KillSwitch (CV-6 from robustness audit).

    Operators previously had to write Python to manually trigger or re-enable
    an agent's circuit breaker. This subcommand wires `KillSwitch.manual_trigger`
    and `KillSwitch.re_enable` directly.

    Subcommands:
      trigger  --agent-id ID --reason TEXT [--operator ID]   open circuit
      reset    --agent-id ID --ack TEXT     [--operator ID]   re-enable agent
      status   [--agent-id ID]                                show circuit state

    Operator ID defaults to $USER (or 'operator' on systems without USER set).
    All operations are logged to KillSwitch's audit log.
    """
    try:
        from kill_switch.kill_switch import KillSwitch
    except ImportError as exc:
        print(f"[ERROR] kill_switch module unavailable: {exc}", file=sys.stderr)
        return 1

    operator = getattr(args, "operator", None) or os.environ.get("USER") or "operator"
    ks = KillSwitch()
    action = args.kill_action

    if action == "trigger":
        if not args.agent_id or not args.reason:
            print("[ERROR] kill-switch trigger requires --agent-id and --reason.", file=sys.stderr)
            return 2
        evt = ks.manual_trigger(
            agent_id=args.agent_id, reason=args.reason, operator_id=operator,
        )
        print(f"OK — agent {args.agent_id} circuit OPENED by {operator}.")
        print(f"  Reason: {args.reason}")
        print(f"  Event: {evt}")
        return 0

    if action == "reset":
        if not args.agent_id or not args.ack:
            print("[ERROR] kill-switch reset requires --agent-id and --ack.", file=sys.stderr)
            return 2
        ok = ks.re_enable(
            agent_id=args.agent_id, operator_id=operator, acknowledgment=args.ack,
        )
        if ok:
            print(f"OK — agent {args.agent_id} re-enabled by {operator}.")
            print(f"  Acknowledgment: {args.ack}")
            return 0
        print(f"[ERROR] re-enable failed for {args.agent_id}.", file=sys.stderr)
        return 1

    if action == "status":
        if args.agent_id:
            try:
                open_ = ks.is_agent_circuit_open(args.agent_id)
                state = ks.get_agent_state(args.agent_id)
                print(f"agent_id={args.agent_id}  circuit_open={open_}  state={state}")
            except Exception as exc:  # pylint: disable=broad-exception-caught
                print(f"[ERROR] could not read status for {args.agent_id}: {exc}", file=sys.stderr)
                return 1
        else:
            agents = ks.get_registered_agents()
            if not agents:
                print("No agents currently registered with KillSwitch.")
                return 0
            for aid in agents:
                try:
                    open_ = ks.is_agent_circuit_open(aid)
                    state = ks.get_agent_state(aid)
                    marker = "🔴 OPEN" if open_ else "🟢 CLOSED"
                    print(f"  {marker}  {aid}  state={state}")
                except Exception as exc:  # pylint: disable=broad-exception-caught
                    print(f"  ⚠  {aid}  status error: {exc}")
        return 0

    print(f"[ERROR] unknown kill-switch action: {action}", file=sys.stderr)
    return 2

def cmd_audit_structure(args: argparse.Namespace) -> int:
    """Audit target project directory structure and artifact completeness.

    Only checks phases up to current_phase — future-phase directories are
    not required to exist yet and should not be created as empty stubs.
    """
    import json as _json
    import re as _re
    from core.utils.project_layout import phase_artifacts as _phase_artifacts

    project = Path(args.project).resolve()

    # Read current phase from state.json — only audit up to this phase.
    try:
        _state = _json.loads((project / ".methodology" / "state.json").read_text())
        current_phase = int(_state.get("current_phase", 8))
    except Exception:
        current_phase = 8  # if state unreadable, check all phases

    # Canonical phase directory names — reference module-level _PHASE_DIRS
    PHASE_DIRS = {k: v for k, v in _PHASE_DIRS.items() if k <= current_phase}

    # Required artifacts per phase (aligned with phase_artifact_enforcer.py)
    _ALL_PHASE_ARTIFACTS = {
        1: ["01-requirements/SRS.md", "01-requirements/SPEC_TRACKING.md",
            "01-requirements/TRACEABILITY_MATRIX.md", "TEST_INVENTORY.yaml"],
        2: ["02-architecture/SAD.md", "02-architecture/TEST_SPEC.md"],
        3: ["03-development/src/", "03-development/tests/"],
        4: ["04-testing/TEST_PLAN.md", "04-testing/TEST_RESULTS.md"],
        5: ["05-verification/BASELINE.md", "05-verification/VERIFICATION_REPORT.md"],
        6: ["06-quality/QUALITY_REPORT.md"],
        7: _phase_artifacts(7),
        8: ["08-config/CONFIG_RECORDS.md", "08-config/RELEASE_CHECKLIST.md"],
        9: ["09-maintenance/MAINTENANCE_LOG.md"],
    }
    PHASE_ARTIFACTS = {k: v for k, v in _ALL_PHASE_ARTIFACTS.items() if k <= current_phase}

    results: dict[str, Any] = {
        "project": str(project),
        "dimensions": {},
    }

    # --- Dimension 1: Directory existence (≤ current_phase only) ---
    dir_status = {}
    for num, dname in PHASE_DIRS.items():
        dpath = project / dname
        dir_status[f"P{num}"] = {
            "dir": dname,
            "exists": dpath.is_dir(),
            "path": str(dpath),
        }
    results["dimensions"]["directory_existence"] = {
        "label": f"Directory Existence (up to P{current_phase})",
        "passed": all(v["exists"] for v in dir_status.values()),
        "details": dir_status,
    }

    # --- Dimension 2: Artifact completeness (≤ current_phase only) ---
    artifact_status = {}
    for phase_num, paths in PHASE_ARTIFACTS.items():
        phase_key = f"P{phase_num}"
        phase_files = []
        for p in paths:
            fpath = project / p
            exists = fpath.exists()
            size = fpath.stat().st_size if exists and fpath.is_file() else None
            phase_files.append({"path": p, "exists": exists, "size_bytes": size})
        artifact_status[phase_key] = {
            "dir": PHASE_DIRS[phase_num],
            "all_present": all(f["exists"] for f in phase_files),
            "files": phase_files,
        }
    results["dimensions"]["artifact_completeness"] = {
        "label": "Artifact Completeness",
        "passed": all(v["all_present"] for v in artifact_status.values()),
        "details": artifact_status,
    }

    # --- Dimension 3: Content quality ---
    # FR-reference check applies only to phases 1–4 (phases 5–8 produce
    # operational docs that legitimately contain no FR/NFR references).
    _FR_REF_PHASES = {1, 2, 3, 4}

    def _check_content_quality(fpath: Path, phase_num: int = 0) -> dict:
        if not fpath.exists() or not fpath.is_file():
            return {"quality": "missing"}
        try:
            content = fpath.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            return {"quality": "unreadable"}
        issues = []
        if len(content.strip()) < 200:
            issues.append("content < 200 chars")
        is_yaml = fpath.name.endswith(".yaml") or fpath.name.endswith(".yml")
        if not is_yaml and len(_re.findall(r"(?:^|\n)#{1,6} ", content)) < 2:
            issues.append("< 2 markdown sections")
        # I: require CANONICAL FR-ID form (FR-NN / TASK-NN / NFR-NN, ≥2 digits).
        # Previously accepted 4 variants (FR-01, FR01, fr_01, FR(01)) which
        # masked source-code inconsistencies. Now strict — run canonical_lint
        # to find/fix variants in existing docs.
        if phase_num in _FR_REF_PHASES and not _re.search(
            r"\b(?:TASK|FR|NFR)-\d{2,}\b", content
        ):
            issues.append("no [TASK/FR/NFR-NN] canonical references")
        return {"quality": "good" if not issues else "suspicious", "issues": issues}

    quality_status = {}
    for phase_num, paths in PHASE_ARTIFACTS.items():
        phase_key = f"P{phase_num}"
        phase_quality = []
        for art_path in paths:
            q = _check_content_quality(project / art_path, phase_num)
            q["path"] = art_path
            phase_quality.append(q)
        all_ok = all(q["quality"] == "good" for q in phase_quality
                     if not q["path"].endswith("/"))
        quality_status[phase_key] = {
            "dir": PHASE_DIRS[phase_num],
            "all_quality_ok": all_ok,
            "files": phase_quality,
        }
    results["dimensions"]["content_quality"] = {
        "label": "Content Quality (non-hollow templates)",
        "passed": all(v["all_quality_ok"] for v in quality_status.values()),
        "details": quality_status,
    }

    # --- Dimension 4: ASPICE traceability chain ---
    try:
        from core.quality_gate.phase_artifact_enforcer import PhaseArtifactRegistry
        chain_result = PhaseArtifactRegistry(str(project)).verify_phase_chain(current_phase)
        aspice_passed = chain_result["all_verified"]
        aspice_detail = {
            "all_verified": aspice_passed,
            "stats": chain_result["stats"],
            "missing_links": chain_result.get("missing_links", []),
        }
    except Exception as exc:
        aspice_passed = False
        aspice_detail = {"error": str(exc)}
    results["dimensions"]["aspice_chain"] = {
        "label": "ASPICE Traceability Chain (P1→P8)",
        "passed": aspice_passed,
        "details": aspice_detail,
    }

    # --- Dimension 5: Naming convention ---
    naming_issues = []
    expected_names = set(PHASE_DIRS.values())
    # Known/unknown judged against ALL canonical phase dirs — init-project
    # pre-creates every phase directory (including future phases), so a
    # not-yet-reached phase dir (e.g. 09-maintenance at P1) is legitimate.
    # Only the phase-truncated set drives the `missing` check below.
    all_canonical_names = set(_PHASE_DIRS.values())
    # Map "NN" prefix → canonical dir name, e.g. "05" → "05-verification"
    expected_by_prefix: dict[str, str] = {n.split("-")[0]: n for n in all_canonical_names}
    found_dirs = set()
    for child in project.iterdir():
        if not child.is_dir():
            continue
        if child.name in ("00-summary",):
            continue
        m = _re.match(r"^(\d{2})-", child.name)
        if m:
            found_dirs.add(child.name)
            if child.name not in all_canonical_names:
                prefix = m.group(1)
                canonical = expected_by_prefix.get(prefix)
                if canonical:
                    naming_issues.append(
                        f"naming deviation: '{child.name}' should be '{canonical}' "
                        f"— rename with: mv '{child.name}' '{canonical}'"
                    )
                else:
                    naming_issues.append(
                        f"unexpected directory '{child.name}' "
                        f"(no phase with prefix '{prefix}' in expected set)"
                    )
    missing = expected_names - found_dirs
    if missing:
        naming_issues.append(
            f"missing directories: {', '.join(sorted(missing))}"
        )
    naming_passed = len(naming_issues) == 0
    results["dimensions"]["naming_convention"] = {
        "label": "Naming Convention (0X-name/ format)",
        "passed": naming_passed,
        "details": {"issues": naming_issues},
    }

    # --- Summary ---
    dims = results["dimensions"]
    all_passed = all(d["passed"] for d in dims.values())
    results["summary"] = {
        "all_passed": all_passed,
        "pass_count": sum(1 for d in dims.values() if d["passed"]),
        "total_dims": len(dims),
    }

    if args.json:
        print(_json.dumps(results, indent=2, ensure_ascii=False))
    else:
        _print_audit_report(results)

    return 0 if all_passed else 1

def _print_audit_report(results: dict) -> None:
    """Print human-readable audit-structure report."""
    print(f"\n{'='*60}")
    print("Audit-Structure Report")
    print(f"Project: {results['project']}")
    print(f"{'='*60}")

    dims = results["dimensions"]
    for key, dim in dims.items():
        icon = "PASS" if dim["passed"] else "FAIL"
        print(f"\n  [{icon}] {dim['label']}")

        if key == "directory_existence":
            for pk, dv in dim["details"].items():
                mark = "✅" if dv["exists"] else "❌"
                print(f"     {mark} {pk}  {dv['dir']}")

        elif key == "artifact_completeness":
            for pk, pv in dim["details"].items():
                mark = "✅" if pv["all_present"] else "❌"
                print(f"     {mark} {pk} ({pv['dir']})")
                if not pv["all_present"]:
                    for f in pv["files"]:
                        if not f["exists"]:
                            print(f"        ❌ MISSING: {f['path']}")

        elif key == "content_quality":
            for pk, pv in dim["details"].items():
                mark = "✅" if pv["all_quality_ok"] else "⚠️"
                print(f"     {mark} {pk} ({pv['dir']})")
                for f in pv["files"]:
                    if f["quality"] != "good" and not f["path"].endswith("/"):
                        issues = ", ".join(f.get("issues", []))
                        print(f"        ⚠️  {f['path']}: {f['quality']}"
                              + (f" ({issues})" if issues else ""))

        elif key == "aspice_chain":
            stats = dim["details"].get("stats", {})
            print(f"     Verified: {stats.get('verified', '?')}/{stats.get('total', '?')} links")
            for link in dim["details"].get("missing_links", [])[:5]:
                print(f"        ❌ {link}")

        elif key == "naming_convention":
            if dim["passed"]:
                print("     ✅ All 0X-name/ directories match expected names")
            else:
                for issue in dim["details"]["issues"]:
                    print(f"        ❌ {issue}")

    # Footer
    s = results["summary"]
    print(f"\n{'='*60}")
    if s["all_passed"]:
        print(f"RESULT: ALL PASS ({s['pass_count']}/{s['total_dims']} dimensions)")
    else:
        print(f"RESULT: FAIL — {s['total_dims'] - s['pass_count']} dimension(s) failed")
    print(f"{'='*60}")

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

    # plan-phase
    help_plan = "Generate phase execution plan from SRS/SAD artifacts (stdlib only)"
    pp = sub.add_parser("plan-phase", help=help_plan)
    pp.add_argument("--phase",  type=int, required=True, help="Phase number (1-8)")
    pp.add_argument("--project", default=".", help="Project root path (default: .)")
    pp.add_argument("--output", default=None, help="Output file path (default: stdout)")
    pp.add_argument("--force", action="store_true",
                    help="Overwrite an existing plan even if it has progress marks ([x])")
    pp.set_defaults(func=cmd_plan_phase)

    # J: sync-harness — pull + commit + push harness submodule in one shot
    sh = sub.add_parser(
        "sync-harness",
        help="Pull + commit + push harness submodule (J improvement)",
    )
    sh.add_argument("--project", default=".", help="Project root (default: .)")
    sh.add_argument("--submodule", default="harness", help="Submodule path (default: harness)")
    sh.add_argument("--remote", default="origin", help="Remote name (default: origin)")
    sh.add_argument("--branch", default="main", help="Branch name (default: main)")
    sh.add_argument("--no-push", action="store_true",
                    help="Skip push; just pull + show commit message")
    sh.set_defaults(func=cmd_sync_harness)

    # plan-all
    pa = sub.add_parser("plan-all",
                        help="Generate all 8 phase plans (dynamic mode) at project start")
    pa.add_argument("--project", default=".", help="Project root path (default: .)")
    pa.add_argument("--output-dir", default=None, dest="output_dir",
                    help="Output directory (default: <project>/.methodology/)")
    pa.add_argument("--force", action="store_true",
                    help="Regenerate all plans even those with progress marks ([x])")
    pa.set_defaults(func=cmd_plan_all)

    # run-phase
    rp = sub.add_parser("run-phase", help="Run preflight checks before entering a phase")
    rp.add_argument("--phase",   type=int, required=True, help="Phase number (1-8)")
    rp.add_argument("--project", default=".", help="Project root (default: .)")
    rp.set_defaults(func=cmd_run_phase)

    from cli.check_cmds import register as _register_check_cmds
    _register_check_cmds(sub)

    # pre-commit-check (git commit hook only — FSM + constitution + kill-switch)
    pcc = sub.add_parser(
        "pre-commit-check",
        help="Lightweight check for git commit hooks (FSM/constitution/kill-switch only; no drift/traceability)",
    )
    pcc.add_argument("--phase",   type=int, required=True, help="Phase number (1-8)")
    pcc.add_argument("--project", default=".", help="Project root (default: .)")
    pcc.set_defaults(func=cmd_pre_commit_check)



    from cli.push_cmds import register as _register_push_cmds
    _register_push_cmds(sub)


    # ── Phase 9 Maintenance: Change Request lifecycle (cli/cr_cmds.py) ─────
    from cli.cr_cmds import register as _register_cr_cmds
    _register_cr_cmds(sub)


    from cli.gate_cmds import register as _register_gate_cmds
    _register_gate_cmds(sub)








    # generate-next-plan (checkpoint-based tactical plan generator)
    gnp = sub.add_parser(
        "generate-next-plan",
        help="Read manifest state and emit the next concrete gate evaluation plan",
    )
    gnp.add_argument("--project", default=".", help="Project root (default: .)")
    gnp.add_argument("--phase",   type=int, default=None, help="Override current phase")
    gnp.set_defaults(func=cmd_generate_next_plan)


    # (run-pipeline removed in v2.5 — old code consumed ~370 lines)



    # status
    st = sub.add_parser("status", help="Show current manifest + FSM state")
    st.add_argument("--project", default=".", help="Project root (default: .)")
    st.add_argument("--json", action="store_true", help="Machine-readable JSON output")
    st.add_argument("--full", action="store_true", help="Include test stats and auto-fix rounds")
    st.set_defaults(func=cmd_status)

    # doctor (read-only cross-file consistency check)
    dr = sub.add_parser(
        "doctor",
        help="Check state.json / manifest / CLAUDE.md / attestation consistency "
             "and detect interrupted state transactions (read-only)",
    )
    dr.add_argument("--project", default=".", help="Project root (default: .)")
    dr.set_defaults(func=cmd_doctor)

    # load-context
    lc = sub.add_parser("load-context",
                        help="Load project context for a phase (JSON output)")
    lc.add_argument("--phase",   type=int, required=True, help="Phase number (1-8)")
    lc.add_argument("--project", default=".", help="Project root (default: .)")
    lc.add_argument("--json",    action="store_true", help="Output as JSON (default behavior)")
    lc.set_defaults(func=cmd_load_context)

    # read-file (deterministic file read; CLI wrapper over scripts/file_loader.py)
    rf = sub.add_parser(
        "read-file",
        help="Deterministic file read with server-side prefix/length/SHA validation. "
             "Use from workflow JS via Bash agent to avoid LLM-as-shell hallucination "
             "of file content.",
    )
    rf.add_argument("--file", required=True, help="Path to the file to load (absolute or relative to project root)")
    rf.add_argument("--expect-prefix", default=None, help="If set, file's first line must start with this string")
    rf.add_argument("--min-length", type=int, default=0, help="Minimum byte size; below returns TOO_SHORT")
    rf.add_argument("--max-length", type=int, default=None, help="Maximum byte size; above truncates content with a suffix")
    rf.add_argument("--content", action="store_true", help="Include (possibly truncated) content text in JSON output")
    rf.add_argument("--content-out", default=None, help="If set, also write content to this path")
    rf.add_argument("--json-out", default=None, help="If set, write JSON result to this path; otherwise print to stdout")
    rf.add_argument("--quiet", action="store_true", help="Suppress the human-readable status line on stderr")
    rf.set_defaults(func=cmd_read_file)

    # effort
    ef = sub.add_parser("effort", help="Show gate effort metrics summary")
    ef.add_argument("--phase",   type=int, default=None, help="Filter by phase")
    ef.add_argument("--project", default=".", help="Project root (default: .)")
    ef.set_defaults(func=cmd_effort)

    # advance-phase
    adv = sub.add_parser(
        "advance-phase",
        help="Advance to next phase: update state.json (single source of truth)",
    )
    adv.add_argument(
        "--completed", type=int, required=True, dest="completed_phase",
        help="Phase number that just completed (advance-phase --completed 3 → sets phase 4)",
    )
    adv.add_argument("--project", default=".", help="Project root (default: .)")
    adv.set_defaults(func=cmd_advance_phase)

    from cli.fr_cmds import register as _register_fr_cmds
    _register_fr_cmds(sub)








    # audit-phase
    ap = sub.add_parser(
        "audit-phase",
        help="Audit a phase against GitHub artifacts (8-dimension PhaseAuditor check)",
    )
    ap.add_argument("--phase",  type=int, required=True, help="Phase number to audit (1-8)")
    _ap_src = ap.add_mutually_exclusive_group(required=True)
    _ap_src.add_argument(
        "--repo",
        help="GitHub repo in owner/repo format (e.g. johnnylugm-tech/my-project)"
    )
    _ap_src.add_argument(
        "--project",
        metavar="PATH",
        help="Local project root path for on-machine audit (e.g. /path/to/project)"
    )
    ap.add_argument("--branch", default="main", help="Target branch (default: main)")
    ap.add_argument("--output", choices=["markdown", "json"], default="markdown",
                    help="Output format (default: markdown)")
    ap.add_argument("--save",   default=None, metavar="FILE",
                    help="Save report to file")
    ap.add_argument(
        "--fail-on-critical",
        action="store_true",
        help="Exit 1 if any CRITICAL finding exists (stricter than default FAIL verdict).",
    )
    ap.set_defaults(func=cmd_audit_phase)



    # check-constitution

    # init-project
    ip = sub.add_parser(
        "init-project",
        help="Initialize harness CI wiring in a target project (Context B one-shot setup)",
    )
    ip.add_argument("--project", required=True, help="Target project root path")
    ip.add_argument("--phase",   type=int, default=1, help="Current phase (default: 1)")
    ip.add_argument("--language", default=None,
                    help="Project language (e.g. python, javascript, typescript). "
                         "Default: auto-detect from manifest files; required when "
                         "detection is ambiguous")
    ip.add_argument("--test-runner", default=None,
                    help="JS/TS test runner (vitest or jest). Default: auto-detect "
                         "from package.json; required when detection is ambiguous")
    ip.add_argument("--ci-only", action="store_true",
                    help="Write CI workflow only; skip git hooks")
    ip.add_argument("--overwrite", action="store_true",
                    help="Overwrite existing CI workflow and hooks")
    ip.add_argument("--setup-branch-protection", action="store_true",
                    help="Configure GitHub branch protection for main with required checks")
    ip.set_defaults(func=cmd_init_project)

    # amend-sab (SAB Architecture Amendment Protocol — standalone)
    asab = sub.add_parser(
        "amend-sab",
        help="Run SAB Architecture Amendment Protocol: register 03-development/src/ modules "
             "missing from .methodology/SAB.json (recovers run-gate BLOCKED state)",
    )
    asab.add_argument("--project", required=True, help="Target project root path")
    asab.add_argument("--src-dir", default="03-development/src",
                    help="Source directory to scan (default: 03-development/src)")
    asab.add_argument("--dry-run", action="store_true",
                    help="List modules that would be added without writing SAB.json")
    asab.set_defaults(func=cmd_amend_sab)

    # audit-structure
    aus = sub.add_parser(
        "audit-structure",
        help="Audit target project directory structure and artifact completeness",
    )
    aus.add_argument("--project", required=True, help="Target project root path")
    aus.add_argument("--json", action="store_true", help="Output as JSON")
    aus.set_defaults(func=cmd_audit_structure)




    # kill-switch (CV-6 — operator CLI for M1 KillSwitch)
    ks = sub.add_parser(
        "kill-switch",
        help="Manually trigger/reset/inspect M1 KillSwitch circuit for an agent.",
    )
    ks_sub = ks.add_subparsers(dest="kill_action", required=True)

    kst = ks_sub.add_parser("trigger", help="Open the circuit (halt agent dispatch).")
    kst.add_argument("--agent-id", required=True, help="Agent identifier to halt.")
    kst.add_argument("--reason", required=True, help="Reason — recorded in audit log.")
    kst.add_argument("--operator", help="Operator ID (default: $USER).")

    ksr = ks_sub.add_parser("reset", help="Close the circuit (re-enable agent dispatch).")
    ksr.add_argument("--agent-id", required=True, help="Agent to re-enable.")
    ksr.add_argument("--ack", required=True, help="Acknowledgment message — audit logged.")
    ksr.add_argument("--operator", help="Operator ID (default: $USER).")

    kss = ks_sub.add_parser("status", help="Show circuit state for one or all agents.")
    kss.add_argument("--agent-id", help="Specific agent to inspect (default: list all).")

    ks.set_defaults(func=cmd_kill_switch)

    # v2.9.1 B.1: validate-handoff
    vh = sub.add_parser(
        "validate-handoff",
        help="Cross-deliverable dependency check for phase handoffs (P{N} → P{N+1})",
    )
    vh.add_argument(
        "--from-phase", type=int, required=True, dest="from_phase",
        choices=[1, 2, 3, 4, 5, 6, 7, 8],
        help="Phase number that just completed; validator checks deliverables needed by P{N+1}",
    )
    vh.add_argument("--project", default=".", help="Project root (default: .)")
    vh.set_defaults(func=cmd_validate_handoff)


    return p

def main() -> int:
    """Main entry point for the CLI."""
    # Load .env from CWD first (covers `cd project && python harness_cli.py`).
    _load_env_file(Path.cwd() / ".env")
    # Also load from --project path if it differs from CWD.
    for i, arg in enumerate(sys.argv[1:], 1):
        if arg in ("--project", "-p") and i < len(sys.argv):
            proj_env = Path(sys.argv[i]) / ".env"
            if proj_env.resolve() != (Path.cwd() / ".env").resolve():
                _load_env_file(proj_env)
            break
        if arg.startswith("--project="):
            proj_env = Path(arg.split("=", 1)[1]) / ".env"
            if proj_env.resolve() != (Path.cwd() / ".env").resolve():
                _load_env_file(proj_env)
            break

    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)

if __name__ == "__main__":
    sys.exit(main())
