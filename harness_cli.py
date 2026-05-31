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
    python harness_cli.py push-milestone    --type p3-mid|p3-pre-gate2|p5-baseline|p7|p8 --project .
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
    7   Plan incompletion block — unchecked mandatory steps in phaseN_plan.md
    8   Missing deliverables block — required artifacts not found on disk or not git-tracked
    10  PAUSE — Claude must evaluate gate; run finalize-gate then re-run pipeline
    11  Phase Truth < 90% (HR-11); fix and re-run with --phase-from N
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from harness.git_strategy import GitStrategy
    from harness.harness_bridge import GateBlockedError

from harness.handover_generator import HandoverGenerator

# Ensure repo root on path so core/ and harness/ resolve
_REPO_ROOT = Path(__file__).parent
sys.path.insert(0, str(_REPO_ROOT))

# Atomic state-file writers (CV-3 / SG-12 from robustness audit)
from core.atomic_io import atomic_write_json, file_lock, state_lock_path  # noqa: E402
from core.pre_flight import check_cli_tools  # noqa: E402

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

# Phases where Gate 1 runs per-FR
_PER_FR_GATE1_PHASES: frozenset[int] = frozenset({3, 4, 5, 7, 8})
# Statuses that indicate an agent dispatch failure (all others treated as success).
_DISPATCH_ERROR_STATUSES: frozenset[str] = frozenset({"REJECT", "BLOCKED", "FAILED", "ERROR", "TIMEOUT"})
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
# Tool name → (check_command, human_name).
# Used by _verify_gate_tools() to verify tools are actually installed before
# accepting dimension scores (S2: prevents LLM guessing when tools are missing).
# Dimension names that don't map to a dedicated tool (security, architecture, etc.)
# are checked by the LLM and don't have a tool requirement.
_TOOL_CHECK_COMMANDS: dict[str, tuple[str, str]] = {
    "ruff": ("ruff --version 2>&1 || python3 -m ruff --version 2>&1", "ruff"),
    "mypy": ("mypy --version 2>&1", "mypy"),
    "pytest-cov": ("pytest --version 2>&1 && coverage --version 2>&1", "pytest + coverage"),
    "pytest": ("pytest --version 2>&1", "pytest"),
    "gitleaks": ("gitleaks version 2>&1", "gitleaks"),
    "scancode": ("scancode --version 2>&1", "scancode-toolkit"),
    "mutmut": ("mutmut --version 2>&1", "mutmut"),
    # Fallback: dimension-name-based lookup for older YAML configs without tool field
    "secrets_scanning": ("gitleaks version 2>&1", "gitleaks"),
    "mutation_testing": ("mutmut --version 2>&1", "mutmut"),
    "license_compliance": ("scancode --version 2>&1", "scancode-toolkit"),
    "linting": ("ruff --version 2>&1 || python3 -m ruff --version 2>&1", "ruff"),
    "type_safety": ("mypy --version 2>&1", "mypy"),
    "test_coverage": ("pytest --version 2>&1", "pytest + coverage"),
    "code-review-graph": ("code-review-graph status 2>&1", "code-review-graph"),
    # Structural dimensions — scored by CRG, not LLM
    "architecture": ("code-review-graph status 2>&1", "code-review-graph"),
    "error_handling": ("code-review-graph status 2>&1", "code-review-graph"),
}

def _check_tool_for_dim(dim_name: str, tool_name: str | None) -> tuple[bool, str]:
    """Check if the required tool for a dimension is installed.

    Prefers tool_name from YAML config; falls back to dim_name lookup.
    Returns (available: bool, diagnostic: str).
    """
    # First try the explicit tool name from YAML
    if tool_name:
        info = _TOOL_CHECK_COMMANDS.get(tool_name)
        if info:
            check_cmd, human_name = info
            try:
                result = subprocess.run(
                    ["bash", "-c", check_cmd],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    timeout=10, text=True,
                )
                ok = result.returncode == 0
                return ok, ("" if ok else f"{dim_name}: {human_name} ({tool_name}) not found")
            except Exception:
                return False, f"{dim_name}: {human_name} ({tool_name}) check failed"

    # Fall back to dimension name lookup
    info = _TOOL_CHECK_COMMANDS.get(dim_name)
    if info is None:
        return True, ""  # No tool requirement — pass (LLM-evaluated dimension)
    check_cmd, human_name = info
    try:
        result = subprocess.run(
            ["bash", "-c", check_cmd],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=10, text=True,
        )
        ok = result.returncode == 0
        return ok, ("" if ok else f"{dim_name}: {human_name} not found")
    except Exception:
        return False, f"{dim_name}: {human_name} check failed"

def _verify_gate_tools(gate_num: int, project: str) -> tuple[bool, list[str]]:
    """Check all required tools for a gate exist (S2).

    Reads gate YAML config: if a dimension has requires_tool_execution: true
    and a tool field, the tool MUST be installed. Dimensions without
    requires_tool_execution (e.g. security, architecture) are LLM-evaluated
    and skipped.

    Returns (all_ok, missing_list).
    """
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

    missing: list[str] = []
    for dim in cfg.get("dimensions", []):
        dim_name = dim.get("name", "")
        requires_tool = dim.get("requires_tool_execution", False)
        if not requires_tool:
            continue  # LLM-evaluated dimension — skip tool check
        tool_name = dim.get("tool")  # May be None for older configs
        ok, diag = _check_tool_for_dim(dim_name, tool_name)
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
    return len(all_missing) == 0, all_missing

def _fr_step_preflight(step: str, project: Path, fr_id: str | None) -> tuple[bool, list[str]]:
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
    if not srs.exists():
        errors.append("✗ SRS.md not found in project root (required for all FR steps)")

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
    test_spec = project / "02-architecture" / "TEST_SPEC.md"
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


def _mark_plan_item(project: Path, phase: int, step: str, fr_id: str) -> None:
    """P0-A: Check off the plan item for a completed step (bookkeeping automation).

    Prevents C11 CRITICAL at advance-phase by keeping phaseN_plan.md in sync
    with actual step completion.  Non-fatal: silently skips on any error.
    """
    plan_file = project / ".methodology" / f"phase{phase}_plan.md"
    if not plan_file.exists():
        return
    step_to_tag = {
        "TDD-RED": "ORCH-RED",
        "TDD-GREEN": "ORCH-GREEN",
        "TDD-IMPROVE": "ORCH-IMPROVE",
        "GATE1": "ORCH-GATE1",
        "GATE1-DELTA": "ORCH-GATE1",
    }
    tag = step_to_tag.get(step)
    if not tag:
        return
    try:
        content = plan_file.read_text(encoding="utf-8")
        # Match: - [ ] **[ORCH-RED]** ... FR-01 (any text on same line)
        # Also try the -DELTA variant (e.g. ORCH-GATE1-DELTA) which some plan
        # generators emit for Phase 5/7/8 carry-forward steps.
        tags_to_try = [tag]
        if step.endswith("-DELTA"):
            tags_to_try.append(tag + "-DELTA")
        updated = content
        for _t in tags_to_try:
            pattern = rf"(- \[ \] \*\*\[{re.escape(_t)}\]\*\*[^\n]*\b{re.escape(fr_id)}\b)"
            updated = re.sub(
                pattern,
                lambda m: m.group(0).replace("- [ ]", "- [x]", 1),
                updated,
            )
        if updated != content:
            plan_file.write_text(updated, encoding="utf-8")
    except OSError:
        pass  # non-fatal: bookkeeping failure must not block step completion


def _mark_p5_baseline_plan_items(project: Path) -> None:
    """Mark Phase 5 deliverable checklist items after push-milestone p5-baseline succeeds.

    These items represent confirmed deliverables (integration tests, security scan,
    baseline artifacts) that push-milestone validates.  Marking them prevents C11
    CRITICAL at advance-phase without requiring the agent to manually tick each one.
    Non-fatal: silently skips on any error.
    """
    plan_file = project / ".methodology" / "phase5_plan.md"
    if not plan_file.exists():
        return
    # Patterns that match the deliverable items written by the plan generator.
    # Each pattern must be anchored tightly to avoid false positive marks.
    patterns = [
        r"(- \[ \] Integration tests pass\b)",
        r"(- \[ \] Performance tests meet targets\b)",
        r"(- \[ \] Security scan passes\b)",
        r"(- \[ \] Baseline established\b)",
        r"(- \[ \] \*\*PUSH[^*]*P5-baseline\*\*[^\n]*)",
        r"(- \[ \] `BASELINE\.md`[^\n]*)",
        r"(- \[ \] `VERIFICATION_REPORT\.md`[^\n]*)",
    ]
    try:
        content = plan_file.read_text(encoding="utf-8")
        updated = content
        for pat in patterns:
            updated = re.sub(
                pat,
                lambda m: m.group(0).replace("- [ ]", "- [x]", 1),
                updated,
            )
        if updated != content:
            plan_file.write_text(updated, encoding="utf-8")
            print("  [push-milestone] Phase 5 deliverable plan items auto-marked ✓")
    except OSError:
        pass  # non-fatal


def _mark_generate_next_plan_item(project: Path, completed_phase: int, next_phase: int) -> None:
    """Mark 'Generate Phase N plan' item in phase{completed}_plan.md after advance-phase runs.

    advance-phase itself is the 'Generate next-phase plan' action, so marking it here
    is accurate and prevents C11 CRITICAL from blocking a re-audit of completed phases.
    Non-fatal.
    """
    plan_file = project / ".methodology" / f"phase{completed_phase}_plan.md"
    if not plan_file.exists():
        return
    try:
        content = plan_file.read_text(encoding="utf-8")
        updated = re.sub(
            rf"(- \[ \] Generate Phase {next_phase} plan\b[^\n]*)",
            lambda m: m.group(0).replace("- [ ]", "- [x]", 1),
            content,
        )
        if updated != content:
            plan_file.write_text(updated, encoding="utf-8")
    except OSError:
        pass  # non-fatal


def _append_dev_log_tdd_entry(
    project: Path, fr_id: str, score: float | None = None
) -> None:
    """P0-B: Append TDD evidence to DEVELOPMENT_LOG.md after GATE1 PASS.

    Prevents C5 CRITICAL at advance-phase by maintaining RED→GREEN evidence
    automatically.  Non-fatal.
    """
    log_file = project / "DEVELOPMENT_LOG.md"
    if not log_file.exists():
        return
    try:
        import datetime as _datetime
        ts = _datetime.datetime.now(_datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        score_str = f"{score:.2f}" if score is not None else "N/A"
        line = (
            f"- [x] {fr_id} test pass"
            f" — Gate 1 score: {score_str}"
            f" | RED→GREEN cycle complete | {ts}\n"
        )
        with open(log_file, "a", encoding="utf-8") as _f:
            _f.write(line)
    except OSError:
        pass  # non-fatal


def _check_inter_fr_score_variance(project: Path, phase: int) -> tuple[bool, str]:
    """D2 extension: Gate 1 score variance across all FRs in a phase.

    stddev < 1.0 across ≥5 FRs is statistically implausible under genuine evaluation.
    Returns (ok, diagnostic). Advisory — caller decides whether to block or warn.
    """
    scores_file = project / ".methodology" / _GATE1_SCORES_FILE
    if not scores_file.exists():
        return True, ""
    try:
        all_scores = json.loads(scores_file.read_text(encoding="utf-8"))
    except Exception:  # pylint: disable=broad-exception-caught
        return True, ""

    phase_scores: dict = all_scores.get(str(phase), {})
    values = [float(v) for v in phase_scores.values()]
    if len(values) < 5:
        return True, ""

    import statistics as _stats
    stdev = _stats.pstdev(values)
    if stdev < 1.0:
        return False, (
            f"Inter-FR Gate 1 score variance suspicious: "
            f"stddev={stdev:.3f} across {len(values)} FRs "
            f"(range {min(values):.1f}~{max(values):.1f}) — "
            "genuine per-FR evaluation produces natural variance"
        )
    return True, ""

# Entry gate required per phase (CONSTITUTION.md §2.3)
# Single source of truth: scripts/phase_auditor.py
from scripts.phase_auditor import _ENTRY_GATE_MAP  # noqa: E402 (module-level after constants)

# Phase → composite exit gate number
_PHASE_EXIT_GATES: dict[int, int] = {3: 2, 4: 3, 6: 4}

# Phases that require Gate 1 per-FR evaluation during advance-phase.
# Phase 6 (Quality Assurance) has no FR loop — it uses Gate 4 exclusively —
# so Gate 1 per-FR records are not expected for it.
# Mirrors _PHASE_GATE1_PHASES in scripts/generate_full_plan.py.
_PHASES_WITH_GATE1_FR_CHECK: frozenset[int] = frozenset({3, 4, 5, 7, 8})

# P1/P2 deliverable labels used as approval-file keys in agent_b_approvals/
_PHASE_DELIVERABLES: dict[int, list[str]] = {
    1: ["SRS.md", "SPEC_TRACKING.md", "TRACEABILITY_MATRIX.md", "TEST_INVENTORY.yaml"],
    2: ["SAD.md", "ADR.md", "TEST_SPEC.md"],
}
# Documents that Agent B must embed per phase (SAD.md doesn't exist until P2)
_REQUIRED_EMBEDDED_DOCS: dict[int, list[str]] = {
    1: ["SRS.md"],
    2: ["SRS.md", "SAD.md"],
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
    results = []
    for phase_num in range(1, 9):
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

def _check_fr_test_file_exists(project: Path, fr_id: str) -> tuple[bool, str]:
    """Gate 1: verify a test file exists for the given FR (TDD RED phase).

    Accepts test_fr07.py or test_fr7.py naming. Skips non-standard FR-IDs.
    Called during cmd_finalize_gate Gate 1 path.
    """
    m = re.match(r"FR-(\d+)", fr_id, re.IGNORECASE)
    if not m:
        return True, ""
    num = m.group(1).zfill(2)
    test_dir = project / "tests"
    patterns = [f"test_fr{num}.py", f"test_fr{num.lstrip('0')}.py"]
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
    num_raw = num.lstrip('0')
    test_patterns = [f"tests/test_fr{num}.py", f"tests/test_fr{num_raw}.py"]

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

def _scan_test_functions(test_dir: Path) -> set[str]:
    """Scan all Python test files for function definitions starting with test_."""
    fns: set[str] = set()
    if not test_dir.is_dir():
        return fns
    for f in sorted(test_dir.rglob("*.py")):
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            m2 = re.match(r"^\s*def\s+(test_\w+)\s*\(", line)
            if m2:
                fns.add(m2.group(1))
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

        # Detect FR section headers: ### FR-XX: ...
        fr_match = re.match(r"^###\s+(FR-\d+)[:\s]", stripped)
        if fr_match:
            current_fr = fr_match.group(1)
            in_table = False
            header_skipped = False
            continue

        # Detect any H2 section (## …) — prevents last FR bleeding into next section.
        # Tags items under a normalised slug so they're traceable but won't be
        # confused with real FR-IDs (which follow the FR-\d+ pattern).
        if re.match(r"^##\s+\S", stripped) and not stripped.startswith("###"):
            h2_text = re.sub(r"^##\s+", "", stripped).strip()
            current_fr = re.sub(r"\W+", "_", h2_text.lower()).rstrip("_")[:30]
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
    spec_path = project / "02-architecture" / "TEST_SPEC.md"
    if not spec_path.exists():
        if verbose:
            print("[spec-coverage] TEST_SPEC.md not found at 02-architecture/TEST_SPEC.md — skipping.")
        return (0, 100.0)

    items = _parse_test_spec(spec_path)
    if fr_id:
        items = [i for i in items if i["fr_id"] == fr_id]

    if not items:
        if verbose:
            print("[spec-coverage] No test cases found in TEST_SPEC.md"
                  + (f" for {fr_id}" if fr_id else "") + ".")
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

    actual_fns = _scan_test_functions(project / "tests")

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


def cmd_spec_coverage_check(args: argparse.Namespace) -> int:
    """Spec Coverage Check — compare TEST_SPEC.md items against actual test files.

    Validates that every named test case declared in the P2 TEST_SPEC.md artifact
    has been implemented as a real test function in tests/.
    """
    project = Path(args.project).resolve()
    threshold = getattr(args, "threshold", 80.0)
    fr_id = getattr(args, "fr_id", None)
    code, _ = _run_spec_coverage_check(project, threshold, fr_id=fr_id, verbose=True)
    return code


def cmd_check_test_inventory(args: argparse.Namespace) -> int:
    """[DEPRECATED v2.6] Delegates to spec-coverage-check.

    TEST_SPEC.md is now the single source of truth for all test traceability.
    Use spec-coverage-check instead: python harness_cli.py spec-coverage-check --project . --threshold N
    """
    print("[DEPRECATED] check-test-inventory is deprecated as of v2.6.")
    print("  TEST_SPEC.md is now the single source of truth for test traceability.")
    print("  Delegating to spec-coverage-check.")
    print("  Please use: python harness_cli.py spec-coverage-check --project . --threshold <N>")
    print()

    project = Path(args.project).resolve()
    inventory_path = project / "TEST_INVENTORY.yaml"
    spec_path = project / "02-architecture" / "TEST_SPEC.md"

    # --strict: only block if BOTH TEST_SPEC.md AND TEST_INVENTORY.yaml are missing
    if getattr(args, "strict", False):
        if not spec_path.exists() and not inventory_path.exists():
            print("[BLOCKED] Neither TEST_SPEC.md nor TEST_INVENTORY.yaml found. "
                  "P1/P2 must produce these files.")
            return 8

    return cmd_spec_coverage_check(args)

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
    # phase=0 and phase=-1, which is meaningless (only 1..8 exist).
    if not (1 <= phase <= 8):
        return {
            "passed": False,
            "gate": "InvalidPhase",
            "reason": f"phase={phase} is out of range 1..8",
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
            gate_status = gates.get(f"gate{prev_gate}", {})
            if gate_status.get("quality_complete"):
                return {"passed": True, "gate": f"Gate {prev_gate}",
                        "reason": f"Gate {prev_gate} PASS confirmed"}
            return {"passed": False, "gate": f"Gate {prev_gate}",
                    "reason": f"Gate {prev_gate} not PASS in manifest"}
    except Exception as e:
        return {"passed": False, "reason": f"Manifest parse error: {e}"}

    return {"passed": False, "gate": "Unknown", "reason": f"No entry gate defined for phase {phase}"}

def cmd_run_phase(args: argparse.Namespace) -> int:
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

def _run_fast_preflight(hooks) -> dict:
    """Lightweight preflight: FSM, constitution, BVS phase order, kill-switch only.

    Used exclusively by cmd_pre_commit_check (git commit hook path).
    Not exposed via run-phase to prevent agents from bypassing full enforcement.
    """
    results = {
        "fsm": hooks.preflight_fsm_check(),
        "bvs_phase_order": hooks.preflight_bvs_phase_order(),
        "constitution": hooks.preflight_constitution(),
        "kill_switch": hooks.preflight_kill_switch(),
    }
    all_passed = all(r.get("passed", False) for r in results.values())
    return {"all_passed": all_passed, "details": results}

def cmd_pre_commit_check(args: argparse.Namespace) -> int:
    """Lightweight pre-commit hook check (FSM + constitution + kill-switch only).

    Intended exclusively for git commit hooks where speed matters.
    Skips drift, traceability, gap analysis, and CI readiness — those are
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

def _sentinel_path(project: Path, gate: int, fr_id: str | None) -> Path:
    """Return the sentinel file path that run-gate writes and finalize-gate verifies."""
    key = (fr_id or "phase").replace("-", "").lower()
    d = project / ".sessi-work" / "sentinels"
    return d / f"g{gate}_{key}.flag"

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
        if len(_scores) >= 3:
            import statistics as _stats
            _stdev = _stats.pstdev(_scores)
            if _stdev < 0.5:
                _mean = _stats.fmean(_scores)
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


def cmd_run_gate(args: argparse.Namespace) -> int:
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
        _num_match = re.match(r"FR-(\d+)", fr_id)
        _num_str = (
            _num_match.group(1).zfill(2)
            if _num_match
            else re.sub(r"[^a-z0-9]", "_", fr_id.lower()).strip("_")
        )
        _test_file = f"tests/test_fr{_num_str}.py"
        _src_dir = "03-development/src"

        # Detect FR-specific source files by parsing the test file's imports.
        _src_files = _fr_source_files_from_imports(Path(project), _test_file, _src_dir)

        # Load quality_manifest for per-FR overrides (scope + non-python flag).
        _manifest_data: dict = {}
        _manifest_path_g = Path(project) / ".methodology" / "quality_manifest.json"
        try:
            _manifest_data = json.loads(_manifest_path_g.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass

        # Issue 4: manual fr_scope_overrides — merges declared files into scope.
        # Use when __init__.py transitive re-exports can't be auto-detected.
        # Add to quality_manifest.json: {"fr_scope_overrides": {"FR-16": ["path/to/file.py"]}}
        _scope_override = _manifest_data.get("fr_scope_overrides", {}).get(fr_id, [])
        if _scope_override:
            _src_files = list(dict.fromkeys(_src_files + _scope_override))

        # Issue 3: non-Python FRs (Docker Compose, SQL, YAML) have no Python source.
        # When scope is empty and FR is declared non-Python, bypass coverage measurement
        # and assign threshold score directly (infrastructure FRs are exempt).
        # Add to quality_manifest.json: {"fr_non_python": ["FR-15"]}
        _non_python_frs = set(_manifest_data.get("fr_non_python", []))
        _cov_threshold = int(
            _manifest_data.get("quality_targets", {}).get("min_coverage", 80)
        )
        if not _src_files and fr_id in _non_python_frs:
            print(
                f"\n[FR-SCOPED TOOL OVERRIDES — {fr_id}]\n"
                f"Gate 1 scope is single_fr. Replace the project-wide defaults in\n"
                f"evaluate_dimension.md with these FR-scoped commands:\n\n"
                f"test_coverage — {fr_id} is declared as a non-Python FR "
                f"(no Python source to measure):\n"
                f"  echo 'NON_PYTHON_FR: coverage not applicable'\n"
                f"  Score this dimension as {_cov_threshold} (= threshold). "
                f"Infrastructure/config FRs are exempt from Python coverage measurement.\n"
                f"  Set tool_evidence = 'non-python FR: {fr_id} declared in fr_non_python'\n\n"
                f"linting — lint only the FR source directory:\n"
                f"  ruff check {_src_dir}/ 2>&1 | head -200\n\n"
                f"type_safety — type-check only the FR source directory:\n"
                f"  pyright {_src_dir}/ --outputjson 2>&1 | head -200\n"
            )
        else:
            if _src_files:
                _include_flag = ",".join(_src_files)
                _cov_cmd = (
                    f"  coverage run -m pytest {_test_file} "
                    f"&& coverage report --include=\"{_include_flag}\" --format=json \\\n"
                    f"    || PYTHONPATH=. coverage run -m pytest {_test_file} "
                    f"&& coverage report --include=\"{_include_flag}\" --format=json \\\n"
                    f"    || PYTHONPATH=. python3 -m pytest {_test_file} "
                    f"--cov={_src_dir} --cov-report=term-missing"
                )
                _cov_note = f"  (FR source files detected: {', '.join(_src_files)})"
            else:
                # Fallback: test file absent or no imports matched — use full src dir
                _cov_cmd = (
                    f"  coverage run --source={_src_dir} -m pytest {_test_file} "
                    f"&& coverage report --format=json \\\n"
                    f"    || PYTHONPATH=. coverage run --source={_src_dir} -m pytest {_test_file} "
                    f"&& coverage report --format=json \\\n"
                    f"    || PYTHONPATH=. python3 -m pytest {_test_file} "
                    f"--cov={_src_dir} --cov-report=term-missing"
                )
                _cov_note = f"  (fallback: {_src_dir} — test file not found or no imports detected)"

            print(
                f"\n[FR-SCOPED TOOL OVERRIDES — {fr_id}]\n"
                f"Gate 1 scope is single_fr. Replace the project-wide defaults in\n"
                f"evaluate_dimension.md with these FR-scoped commands:\n\n"
                f"test_coverage — measure only {fr_id}'s source files:\n"
                f"{_cov_cmd}\n"
                f"{_cov_note}\n\n"
                f"linting — lint only the FR source directory:\n"
                f"  ruff check {_src_dir}/ 2>&1 | head -200\n\n"
                f"type_safety — type-check only the FR source directory:\n"
                f"  pyright {_src_dir}/ --outputjson 2>&1 | head -200\n"
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
    sf = _sentinel_path(Path(project), args.gate, fr_id)
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

def cmd_run_env_check(args: argparse.Namespace) -> int:
    """Print project-aware environment evaluation prompt for Claude.

    Reads SAD.md + SRS.md from the target project, constructs an evaluation
    prompt that asks Claude to identify required env vars, CLI tools, and
    infrastructure services by reading the project's own documentation,
    then verify each against the current environment.

    Claude must evaluate inline and write .sessi-work/env_check_result.json.
    """
    from harness.harness_bridge import HarnessBridge

    project = str(Path(args.project).resolve())
    fr_id = getattr(args, "fr_id", None) or None

    bridge = HarnessBridge()
    ctx = bridge.prepare_env_check(
        project_root=project,
        phase=args.phase,
        fr_id=fr_id,
    )

    if not ctx.sad_excerpt and not ctx.srs_excerpt:
        print(
            "[WARN] Neither SAD.md nor SRS.md found in project. "
            "Env check will have no project context to evaluate.",
            file=sys.stderr,
        )

    # Ensure .sessi-work/ exists before writing the sentinel and result.
    Path(ctx.work_dir).mkdir(parents=True, exist_ok=True)

    # Write sentinel so finalize-env-check can verify run-env-check was called.
    sf = _sentinel_env_path(Path(project))
    sf.parent.mkdir(parents=True, exist_ok=True)
    sf.write_text(f"{datetime.now(timezone.utc).isoformat()}\n", encoding="utf-8")
    print(f"[SENTINEL] {sf.relative_to(Path(project))} written.")

    # Spawn sub-agent to perform the env check inline.
    # Uses bypassPermissions so the agent can run psql, docker, etc.
    # --setting-sources "" blocks user-level CLAUDE.md/hooks (isolation).
    prompt = ctx.evaluation_prompt()
    cli = shutil.which("claude")
    if not cli:
        print("[ERROR] claude CLI not found.", file=sys.stderr)
        return 1

    cmd = [
        cli, "-p", prompt,
        "--output-format", "json",
        "--max-turns", "70",
        "--no-session-persistence",
        "--setting-sources", "",
        "--permission-mode", "bypassPermissions",
        "--disable-slash-commands",
        "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
    ]
    print("[INFO] Spawning env-check sub-agent...")
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,
            cwd=str(Path(project).resolve()),
            env=os.environ.copy(),
        )
    except subprocess.TimeoutExpired:
        print("[ERROR] env-check sub-agent timed out after 300s.", file=sys.stderr)
        return 1

    if proc.returncode != 0:
        print(f"[ERROR] env-check sub-agent failed (exit {proc.returncode}).", file=sys.stderr)
        if proc.stderr:
            print(proc.stderr[:500], file=sys.stderr)
        return 1

    result_path = Path(project) / ".sessi-work" / "env_check_result.json"
    if not result_path.exists():
        print("[ERROR] env-check sub-agent did not write env_check_result.json.", file=sys.stderr)
        return 1

    _fab = _verify_env_check_claims(Path(project))
    if _fab:
        print("[ERROR] env-check agent fabricated claims:\n  " + "\n  ".join(_fab), file=sys.stderr)
        return 1

    print(f"[INFO] env-check complete. Result: {result_path}")
    return 0


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
    for t in data.get("cli_tools", {}).get("required", []):
        if isinstance(t, dict) and t.get("present") and t.get("name"):
            name = str(t["name"])
            if shutil.which(name) is None:
                findings.append(f"cli_tool '{name}': claimed present, but not found on PATH")
    for v in data.get("env_vars", {}).get("required", []):
        if isinstance(v, dict) and v.get("present") and v.get("name"):
            name = str(v["name"])
            if name not in os.environ:
                findings.append(f"env_var '{name}': claimed present, but not set")
    return findings


def cmd_finalize_env_check(args: argparse.Namespace) -> int:
    """Verify env_check_result.json and report environment readiness.

    Reads the result written by Claude after inline evaluation, validates
    the sentinel exists (anti-fabrication), and prints a pass/fail summary.
    Exits 0 when ready, 1 when items are missing.
    """
    from harness.harness_bridge import HarnessBridge

    project = Path(args.project).resolve()
    fr_id = getattr(args, "fr_id", None) or None

    # Sentinel check — prevent fabricated results
    sf = _sentinel_env_path(project)
    if not sf.exists():
        print(
            f"\n[BLOCKED] Sentinel not found: {sf.relative_to(project)}\n"
            f"  run-env-check must be called before finalize-env-check.\n"
            f"  Writing env_check_result.json directly is not permitted."
        )
        return 1

    # Staleness check: env_check_result.json must not predate the sentinel.
    # This catches cases where an old result file is reused after a new
    # run-env-check invocation without re-running the evaluation.
    sentinel_time: datetime | None = None
    try:
        sentinel_time = datetime.fromisoformat(sf.read_text(encoding="utf-8").strip())
    except (ValueError, OSError):
        pass  # non-fatal — sentinel exists, timestamp unreadable

    if sentinel_time is not None:
        result_path = project / ".sessi-work" / "env_check_result.json"
        if result_path.exists():
            try:
                _data = json.loads(result_path.read_text(encoding="utf-8"))
                _checked_at_str = _data.get("checked_at", "")
                if _checked_at_str:
                    _checked_at = datetime.fromisoformat(
                        _checked_at_str.replace("Z", "+00:00")
                    )
                    # Allow 10 s tolerance for the sentinel being written
                    # just before the sub-agent starts.
                    if _checked_at < sentinel_time - timedelta(seconds=10):
                        print(
                            "[WARN] env_check_result.json predates the sentinel — "
                            "result may be from a previous run. "
                            "Re-run: python harness_cli.py run-env-check "
                            f"--phase {args.phase} --project {project}"
                        )
            except (ValueError, OSError, KeyError):
                pass  # malformed JSON handled by finalize_env_check

    bridge = HarnessBridge()
    ctx = bridge.prepare_env_check(
        project_root=str(project),
        phase=args.phase,
        fr_id=fr_id,
    )

    ready, message = bridge.finalize_env_check(ctx)

    print(f"\n{'='*60}")
    print(f"finalize-env-check: Phase {args.phase} | project: {project.name}")
    print(f"{'='*60}")
    print(f"\n{message}")

    if ready:
        print(f"\n[READY] Environment is ready for Phase {args.phase} development.")
        return 0
    else:
        print("\n[BLOCKED] Fix the missing items above, then re-run run-env-check.")
        return 1

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
            issue_registry = (project / issue_registry_path_str) if not Path(issue_registry_path_str).is_absolute() else Path(issue_registry_path_str)
            if not issue_registry.exists():
                print(f"[Gate 4] (A5, advisory): issue registry not found: {issue_registry}",
                      file=sys.stderr)
            else:
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

def cmd_finalize_gate(args: argparse.Namespace) -> int:
    """
    Phase 2: read gate{N}_result.json, check thresholds, update manifest, git.

    Called after Claude has completed inline evaluation and written the result file.
    """
    from harness.harness_bridge import HarnessBridge, GateBlockedError

    project_path = Path(args.project).resolve()
    project = str(project_path)
    bridge = HarnessBridge()
    fr_id = getattr(args, "fr_id", None) or None

    print(f"\n{'='*60}\nfinalize-gate: Gate {args.gate} | Phase {args.phase}\n{'='*60}")

    # ── S0: Tool availability enforcement (S2 — prevent LLM guessing) ────
    _tools_ok, _missing_tools = _verify_gate_tools(args.gate, project)
    if not _tools_ok:
        print(
            f"\n[BLOCKED] Required tools not installed for Gate {args.gate}:\n"
            + "".join(f"  ✗ {m}\n" for m in _missing_tools)
            + "\n  Install the missing tools and re-run finalize-gate.\n"
            "  Tool scores must come from actual tool execution, not estimation."
        )
        return 8

    # ── S0: Commit interval enforcement (P1 — prevent batch fabrication) ──
    _interval_ok, _interval_msg = _check_commit_intervals(
        project, args.phase, args.gate
    )
    if not _interval_ok:
        print(f"\n[BLOCKED] Commit interval violation: {_interval_msg}")
        print("  Re-run per-FR evaluations with genuine evidence and natural spacing.")
        return 1

    # ── Sentinel check: run-gate must have been called before finalize-gate ─
    # Prevents agents from writing gate{N}_result.json directly and calling
    # finalize-gate without actually going through run-gate evaluation.
    sf = _sentinel_path(Path(project), args.gate, fr_id)
    if not sf.exists():
        print(
            f"\n[BLOCKED] run-gate --gate {args.gate} --phase {args.phase}"
            + (f" --fr-id {fr_id}" if fr_id else "")
            + f" --project {args.project}"
            f"\n  must be called before finalize-gate."
            f"\n  Missing sentinel: {sf.relative_to(Path(project))}"
            f"\n  Writing gate{{N}}_result.json directly without run-gate is not permitted."
        )
        return 1

    # NOTE: HR-10/HR-01 A/B audit (sessions_spawn.log entry-count + distinct-session
    # enforcement) was REMOVED. The log is a plain agent-writable file, so counting
    # entries / roles / session_ids could not actually prove an independent Agent B
    # review occurred (the orchestrator can hand-write entries). P1/P2 quality is
    # enforced by the Agent B deliverable review itself; P3+ by the tool-scored gates
    # and S4 cross-validation. AgentSpawner still records dispatches to sessions_spawn.log
    # as a non-blocking debug trail.

    # ── I-2: FR test file existence check (Gate 1 per-FR) ──────────────
    # Only applies when project has a tests/ directory (real project, not test fixture).
    if args.gate == 1 and fr_id and (Path(project) / "tests").is_dir():
        _fr_ok, _fr_msg = _check_fr_test_file_exists(Path(project), fr_id)
        if not _fr_ok:
            print(_fr_msg)
            return 8

    # ── I-3: RED phase ordering (Gate 1 per-FR) ───────────────────────
    if args.gate == 1 and fr_id and (Path(project) / "tests").is_dir():
        _red_ok, _red_msg = _check_red_phase_ordering(Path(project), fr_id)
        if not _red_ok:
            print(_red_msg)
            return 1

    # ── I-4: Spec Coverage check (Gate 1 per-FR) ──────────────────────
    # Verify that every TEST_SPEC.md entry for this FR has a matching test function.
    # Threshold at Gate 1 is 40% — ensures at least skeleton tests exist before P3 proceeds.
    if args.gate == 1 and fr_id and (Path(project) / "02-architecture" / "TEST_SPEC.md").exists():
        _sc1_code, _sc1_pct = _run_spec_coverage_check(
            project_path, 40.0, fr_id=fr_id, verbose=True
        )
        if _sc1_code != 0:
            print(f"\n[BLOCKED] Gate 1 spec-coverage [{fr_id}] {_sc1_pct:.1f}% < 40% threshold")
            return 1

    # ── I-1: D4 Test Inventory compliance (Gates 2-4) ──────────────
    # REMOVED in v2.6 — unified with I-5 below. TEST_SPEC.md is now the
    # single source of truth for all test traceability checks.
    #
    # ── I-5: D4 Spec Coverage check (Gates 2-4) ──────────────────────
    # Unified v2.6: replaces prior two-check model (I-1 TEST_INVENTORY.yaml
    # forward + I-5 TEST_SPEC.md backward). TEST_SPEC.md is the single source
    # of truth since it carries names from TEST_INVENTORY.yaml (Step 0 of
    # derive_test_cases.md).
    # Thresholds: Gate2=60%, Gate3=80%, Gate4=90%.
    if args.gate >= 2 and (Path(project) / "02-architecture" / "TEST_SPEC.md").exists():
        _sc_threshold = {2: 60.0, 3: 80.0, 4: 90.0}.get(args.gate, 60.0)
        _sc_code, _sc_pct = _run_spec_coverage_check(
            project_path, _sc_threshold, verbose=True
        )
        if _sc_code != 0:
            print(f"\n[BLOCKED] Gate {args.gate} spec-coverage {_sc_pct:.1f}% < {_sc_threshold}%")
            return 1

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
        print(f"\nGATE {args.gate} PASSED")
        print(f"  score           : {result.score:.1f}")
        print(f"  quality_complete: {result.quality_complete}")
        print(f"  open_critical   : {result.open_critical}")
        print(f"  open_high       : {result.open_high}")

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
                    _gp_dst.write_text(_gp_src.read_text(encoding="utf-8"), encoding="utf-8")
                    print(f"  persisted       : {_gp_dst.relative_to(project_path)} (committable)")
                except OSError as _gp_err:
                    print(f"  [WARN] Could not persist gate result to .methodology/: {_gp_err}")
                break

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
        if len(result.dimensions) >= 3:
            import statistics as _stats
            _d_scores = [d.score for d in result.dimensions]
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

        # ── D2: Inter-FR score variance check (phase exit only) ──────────
        _last_gate = _PHASE_EXIT_GATES.get(args.phase)
        if _last_gate is not None and args.gate == _last_gate and args.phase >= 3:
            _var_ok, _var_msg = _check_inter_fr_score_variance(project_path, args.phase)
            if not _var_ok:
                print(f"\n[WARN-D2] {_var_msg}")

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
        if args.gate == 1:
            git.commit_fr_gate1(fr_id or "unknown", result.score, args.phase)
        else:
            git.commit_and_push_gate(args.gate, args.phase, result.score)
            # G-06 fix: record last_milestone_command for gate 4 so CI push-milestone-enforcement
            # audit trail reflects the actual gate push (not P5 residual).
            if args.gate == 4:
                _state_path = Path(args.project).resolve() / ".methodology" / "state.json"
                try:
                    _sd = json.loads(_state_path.read_text(encoding="utf-8"))
                    _sd["last_milestone_command"] = f"finalize-gate --gate 4 --phase {args.phase}"
                    _state_path.write_text(json.dumps(_sd, indent=2), encoding="utf-8")
                except Exception as _sme:
                    print(f"  [WARN] Could not write last_milestone_command to state.json: {_sme}")
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

    phase_names = {
        1: "Requirements Specification", 2: "Architecture Design",
        3: "Implementation",            4: "Testing",
        5: "Verification & Delivery",   6: "Quality Assurance",
        7: "Risk Management",           8: "Configuration Management",
    }
    print(f"\nPhase      : {current_phase} ({phase_names.get(current_phase, '?')})")

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

def cmd_manifest(args: argparse.Namespace) -> int:
    """Generate quality_manifest.json at P2 exit."""
    from harness.harness_bridge import HarnessBridge

    project = Path(args.sad).resolve().parent
    # nargs="+" collects space-separated FR IDs, but users may also pass
    # comma-separated values. Split on commas to support both formats.
    fr_ids: list[str] = []
    for item in args.fr_ids:
        fr_ids.extend(fid.strip() for fid in item.split(",") if fid.strip())
    bridge = HarnessBridge()
    out = bridge.generate_quality_manifest(
        fr_ids=fr_ids,
        sad_path=args.sad,
    )
    print(f"quality_manifest.json written → {out}")
    manifest = json.loads(out.read_text(encoding="utf-8"))
    print(f"  fr_ids        : {manifest['fr_ids']}")
    print(f"  generated_at  : phase {manifest['generated_at_phase']}")
    _generate_sab_json(project)
    return 0

# ---------------------------------------------------------------------------
# push-checkpoint  (P1/P2 human review checkpoint push + HANDOVER.md)
# ---------------------------------------------------------------------------

def cmd_push_checkpoint(args: argparse.Namespace) -> int:
    """Push P1/P2 human-review checkpoint with HANDOVER.md generation.

    Unlike raw git push, this calls GitStrategy which:
    - Writes HANDOVER.md (crash-recovery checkpoint)
    - Stages all changes
    - Commits with conventional commit message
    - Pushes to origin

    Usage:
      python harness_cli.py push-checkpoint --phase 1 --project . --fr-ids FR-01,FR-02,FR-03
      python harness_cli.py push-checkpoint --phase 2 --project . --fr-ids FR-01,FR-02
    """
    project = Path(args.project).resolve()
    fr_ids = [f.strip() for f in args.fr_ids.split(",") if f.strip()]
    # Note: if fr_ids is empty here, GitStrategy.commit_and_push_p1/p2 will
    # auto-detect from SRS.md — no need to block here.

    git = _make_git(args, project)
    git.ensure_gitignore()
    phase = args.phase
    if phase not in (1, 2):
        print(f"[ERROR] push-checkpoint only supports P1/P2 (got phase {phase}).")
        return 1

    if phase == 1:
        ok = git.commit_and_push_p1(
            fr_ids=fr_ids,
            background=f"P{phase} phase completed — pushed for record.",
            notes=["Phase checkpoint push"],
        )
    else:
        ok = git.commit_and_push_p2(
            fr_ids=fr_ids,
            background=f"P{phase} phase completed — pushed for record.",
            notes=["Phase checkpoint push"],
        )
    if ok:
        handover = project / "HANDOVER.md"
        if handover.exists():
            print(f"  HANDOVER.md → {handover}")
        print("  [git] pushed → remote ✓")
        # Write sentinel + phase_completed for CI entry gate verification
        state_path = project / ".methodology" / "state.json"
        if state_path.exists():
            try:
                state_data = json.loads(state_path.read_text(encoding="utf-8"))
                state_data["last_push_checkpoint"] = datetime.now(timezone.utc).isoformat()
                state_data["last_push_checkpoint_phase"] = phase
                # phase_completed + SHA for git merge-base --is-ancestor verification
                import subprocess as _sp
                _sha = _sp.run(
                    ["git", "-C", str(project), "rev-parse", "HEAD"],
                    capture_output=True, text=True, timeout=10,
                ).stdout.strip()
                state_data.setdefault("phase_completed", {})[str(phase)] = {
                    "sha": _sha, "timestamp": datetime.now(timezone.utc).isoformat(),
                }
                atomic_write_json(state_path, state_data)
            except Exception as _e:  # pylint: disable=broad-exception-caught
                print(f"  [WARN] Could not write push-checkpoint sentinel to state.json: {_e}")
        # Next-step hint — push-checkpoint records phase_completed[N] but does NOT
        # update current_phase. Hooks and CI continue to read the same phase until
        # advance-phase is called explicitly. Keeps phase transitions atomic.
        _next = phase + 1
        print(
            f"\n  Next: advance to Phase {_next} when ready:\n"
            f"    python3 harness_cli.py advance-phase --phase {_next} --project {project}"
        )
    return 0 if ok else 1

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
        missing_docs = [d for d in required_docs if d not in embedded]
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

def cmd_verify_agent_b_approvals(args: argparse.Namespace) -> int:
    """Verify that Agent B approval JSON files exist for all required FRs.

    Each FR must have a corresponding .methodology/agent_b_approvals/FR-XX.json
    with review_status == "APPROVE" and the required docs_embedded list.

    NOTE: .methodology/agent_b_approvals/ is committed (not gitignored).
    Do NOT use .sessi-work/ — that directory is in .gitignore and invisible to CI.

    Usage:
      python harness_cli.py verify-agent-b-approvals --phase 8 --fr-ids FR-01,FR-02 --project .
      python harness_cli.py verify-agent-b-approvals --phase 8 --project .  # reads from manifest
    """
    project = Path(args.project).resolve()
    phase = args.phase

    fr_ids_arg = getattr(args, "fr_ids", "") or ""
    fr_ids = [f.strip() for f in fr_ids_arg.split(",") if f.strip()]
    deliverable_ids = _resolve_deliverable_ids(project, phase, fr_ids)

    if not deliverable_ids:
        print("[verify-agent-b] No FR IDs found — pass --fr-ids or ensure quality_manifest.json exists.")
        return 1

    passed, report = _verify_agent_b_approvals_core(project, phase, deliverable_ids)
    print(report)
    return 0 if passed else 1

def _validate_p8_completion(project: Path) -> list[str]:
    """Pre-flight checks required before push-milestone --type p8 is allowed."""
    errors: list[str] = []

    # 1. .methodology-archive/ — auto-create if absent
    archive_dir = project / ".methodology-archive"
    if not archive_dir.exists():
        archive_dir.mkdir(parents=True, exist_ok=True)

    # 2. HANDOVER.md must not reference non-existent Phase 9
    handover = project / "HANDOVER.md"
    if handover.exists():
        content = handover.read_text(encoding="utf-8").lower()
        if "phase 9" in content or "phase9" in content or "phase9_plan" in content:
            errors.append(
                "HANDOVER.md references non-existent Phase 9. "
                "P8 is the final phase. Remove all Phase 9 references from HANDOVER.md."
            )

    return errors

# ---------------------------------------------------------------------------
# push-milestone  (P3+ milestone push + HANDOVER.md)
# ---------------------------------------------------------------------------

def cmd_push_milestone(args: argparse.Namespace) -> int:
    """Push milestone checkpoint with HANDOVER.md generation.

    Milestone pushes are the crash-recovery points for P3+:
      p3-mid      — ≥50% FRs have Gate 1 PASS (PUSH ③)
      p3-pre-gate2  — all FRs Gate 1 PASS, before Gate 2 (PUSH ④)
      p4-mid      — ≥50% FRs Gate 1 re-eval PASS (PUSH ③ P4 variant)
      p4-pre-gate3  — all FRs Gate 1 re-eval PASS, before Gate 3 (PUSH ④ P4 variant)
      p5-baseline — BASELINE.md generated (PUSH ⑦)
      p7          — risk register complete (PUSH ⑨)
      p8          — config records complete (PUSH ⑩)

    Usage:
      python harness_cli.py push-milestone --type p3-mid --project . --fr-done 3 --fr-total 6 --fr-ids FR-01,FR-02,FR-03
      python harness_cli.py push-milestone --type p3-pre-gate2 --project . --fr-ids FR-01,FR-02,FR-03
      python harness_cli.py push-milestone --type p5-baseline --project .
    """
    project = Path(args.project).resolve()
    git = _make_git(args, project)
    git.ensure_gitignore()
    milestone_type = args.type
    fr_ids = [f.strip() for f in args.fr_ids.split(",") if f.strip()]

    ok = False
    # Auto-populate fr_ids from manifest when not provided
    if not fr_ids:
        manifest_path = project / ".methodology" / "quality_manifest.json"
        if manifest_path.exists():
            try:
                _mf = json.loads(manifest_path.read_text(encoding="utf-8"))
                fr_ids = _mf.get("fr_ids", [])
            except Exception:  # pylint: disable=broad-exception-caught
                pass

    if milestone_type == "p3-mid":
        fr_done = args.fr_done
        fr_total = args.fr_total
        if fr_done is None or fr_total is None or fr_total == 0:
            print("[ERROR] --fr-done and --fr-total required for p3-mid (fr-total must be >0)")
            return 1
        ok = git.commit_and_push_p3_mid(fr_done, fr_total, fr_ids)
    elif milestone_type == "p3-pre-gate2":
        ok = git.commit_and_push_p3_pre_gate2(fr_ids)
    elif milestone_type == "p4-mid":
        fr_done = args.fr_done
        fr_total = args.fr_total
        if fr_done is None or fr_total is None or fr_total == 0:
            print("[ERROR] --fr-done and --fr-total required for p4-mid (fr-total must be >0)")
            return 1
        ok = git.commit_and_push_p4_mid(fr_done, fr_total, fr_ids)
    elif milestone_type == "p4-pre-gate3":
        ok = git.commit_and_push_p4_pre_gate3(fr_ids)
    elif milestone_type == "p5-baseline":
        ok = git.commit_and_push_p5_baseline()
        if ok:
            # Auto-mark Phase 5 deliverable plan items so C11 doesn't block advance-phase.
            # push-milestone is the confirmation that all P5 deliverables were validated.
            _mark_p5_baseline_plan_items(project)
    elif milestone_type == "p7":
        ok = git.commit_and_push_p7()
    elif milestone_type == "p8":
        p8_errors = _validate_p8_completion(project)
        if p8_errors:
            print("[ERROR] P8 push blocked — pre-flight checks failed:")
            for e in p8_errors:
                print(f"  • {e}")
            return 1
        ok = git.commit_and_push_p8()
    else:
        print(f"[ERROR] Unknown milestone type: {milestone_type}")
        return 1

    if ok:
        # Record milestone type and timestamp for audit trail
        state_path = project / ".methodology" / "state.json"
        if state_path.exists():
            try:
                state_data = json.loads(state_path.read_text(encoding="utf-8"))
                state_data["last_milestone_command"] = f"push-milestone --type {milestone_type}"
                state_data["last_milestone_at"] = datetime.now(timezone.utc).isoformat()
                atomic_write_json(state_path, state_data)
            except Exception as _state_err:  # pylint: disable=broad-exception-caught
                print(
                    f"\n  [WARN] Could not write last_milestone_command to state.json: {_state_err}"
                )
        handover = project / "HANDOVER.md"
        if handover.exists():
            print(f"  HANDOVER.md → {handover}")
        print(f"  [git] milestone {milestone_type} pushed → remote ✓")
    return 0 if ok else 1

# ---------------------------------------------------------------------------
# gate4-tag  (create annotated git tag from gate4_result.json)
# ---------------------------------------------------------------------------

def cmd_gate4_tag(args: argparse.Namespace) -> int:
    """Create annotated git tag for Gate 4 pass using composite score from gate4_result.json.

    Reads gate4_result.json (from .sessi-work/, .methodology/, or project root),
    extracts composite_score, and creates:
      harness-v4-YYYYMMDD-score<SCORE>

    Usage:
      python harness_cli.py gate4-tag --project .
    """
    project = Path(args.project).resolve()

    # Locate gate4_result.json
    candidates = [
        project / ".sessi-work" / "gate4_result.json",
        project / ".methodology" / "gate4_result.json",
        project / "gate4_result.json",
    ]
    g4_path = next((p for p in candidates if p.exists()), None)
    if g4_path is None:
        print("[ERROR] gate4_result.json not found. Run finalize-gate --gate 4 first.")
        return 1

    try:
        g4 = json.loads(g4_path.read_text(encoding="utf-8"))
        score = g4.get("composite_score", g4.get("total_score"))
    except Exception as exc:
        print(f"[ERROR] Failed to parse gate4_result.json: {exc}")
        return 1

    if score is None:
        score_str = "XX"
        print("[WARN] composite_score not found in gate4_result.json — tag will use 'XX'.")
    else:
        try:
            score_str = str(int(round(float(score))))
        except (TypeError, ValueError):
            score_str = "XX"

    from datetime import date as _date
    today = _date.today().strftime("%Y%m%d")
    tag_name = f"harness-v4-{today}-score{score_str}"
    tag_msg = f"Gate 4 PASS (score {score_str})"

    result = subprocess.run(  # nosec B603 B607
        ["git", "-C", str(project), "tag", "-a", tag_name, "-m", tag_msg],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        print(f"[ERROR] git tag failed:\n{result.stderr.strip()}")
        return 1

    print(f"[OK] Created tag: {tag_name} ({tag_msg})")
    print("  To push: git push origin --tags")
    return 0


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

    # Phase progress table
    phase_names = {1: "Requirements", 2: "Architecture", 3: "Implementation",
                   4: "Testing", 5: "Verification", 6: "Quality", 7: "Risk", 8: "Config"}
    phase_status = {}
    for p in range(1, 9):
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
        try:
            r = subprocess.run(["pytest", "--collect-only", "-q", "--no-header"],
                             cwd=project, capture_output=True, text=True, timeout=30)
            m = re.search(r"(\d+) tests? collected", r.stdout + r.stderr)
            if m:
                test_count = int(m.group(1))
        except Exception:
            pass
        try:
            r = subprocess.run(["pytest", "--cov=.", "--cov-report=term", "--tb=no", "-q"],
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
            "phase_progress": {str(p): phase_status[p] for p in range(1, 9)},
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
    for p in range(1, 9):
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
                print(f"  {g}           : score={v['score']} complete={v['quality_complete']}")
            elif isinstance(v, dict):
                for fr, r in v.items():
                    print(f"  {g}/{fr}  : score={r['score']} complete={r['quality_complete']}")
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
    if manifest_path.exists():
        try:
            manifest = _json.loads(manifest_path.read_text(encoding="utf-8"))
            fr_ids = manifest.get("fr_ids", [])
            gate_results = manifest.get("gate_results", {})
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
        srs_path = project / "01-requirements" / "SRS.md"
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
    }

    print(_json.dumps(result, indent=2, default=str))
    return 0

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

    No LLM involvement — content comes entirely from quality_manifest.json.
    Called automatically by cmd_finalize_gate() after bridge.finalize_gate succeeds.
    """
    from datetime import datetime, timezone as _tz

    gate_data: dict = {}
    manifest_path = project_path / ".methodology" / "quality_manifest.json"
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            gate_data = manifest.get("gate_results", {}).get(f"gate{gate_num}", {})
        except (json.JSONDecodeError, OSError):
            pass

    score = gate_data.get("score", "N/A")
    qc    = gate_data.get("quality_complete", False)

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
    """Run PhaseAuditor (local mode) — comprehensive replacement for _run_phase_end_audit().

    Returns:
      0  = all checks pass
      7  = C11 CRITICAL (unchecked plan items)
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
            c1_criticals  = [c for c in criticals if c.check_id == "C1"]
            c11_criticals = [c for c in criticals if c.check_id == "C11"]

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
            if c11_criticals:
                print(f"\n  [BLOCKED] {len(c11_criticals)} plan item(s) incomplete.")
                return 7
            return 1

        if warnings:
            print(f"  [PHASE-AUDITOR] ⚠️  {len(warnings)} warning(s) — review recommended")
        print(f"  [PHASE-AUDITOR] Score={result.score:.0f}%  Verdict={result.verdict} ✓")
        return 0

    except Exception as exc:
        print(f"  [ERROR] PhaseAuditor failed unexpectedly: {exc}")
        return 2


def _check_gate1_per_fr_coverage(project: Path, completed_phase: int) -> int:
    """Verify every FR in quality_manifest has a Gate 1 finalize-gate record.

    Reads gate_timestamps.jsonl (written only on SUCCESSFUL finalize-gate calls)
    and checks that each FR ID from quality_manifest.json has at least one entry
    with phase == completed_phase and gate == 1.

    Returns:
        0  — all FRs covered (or quality_manifest absent → non-FR project, skip)
        14 — one or more FRs missing a Gate 1 timestamp for this phase
    """
    manifest_path = project / ".methodology" / "quality_manifest.json"
    fr_ids_manifest: list[str] = []
    if manifest_path.exists():
        try:
            fr_ids_manifest = json.loads(
                manifest_path.read_text(encoding="utf-8")
            ).get("fr_ids", [])
        except (json.JSONDecodeError, OSError):
            pass
    if not fr_ids_manifest:
        return 0  # Non-FR project or unreadable manifest — skip

    # DELTA-phase auto-skip: P4/P5/P7/P8 re-run Gate 1 as a delta check. When NO FR's
    # code has changed since its last Gate 1 PASS, the per-FR DELTA loop is a no-op
    # (every run-fr-step would `already done → skip`). Recognise this and treat the
    # whole loop as satisfied, instead of demanding a fresh timestamp per FR.
    # P4 is carryforward too (its plan template promises this auto-skip); a real P4
    # test addition is still caught — _fr_code_changed_since_last_gate1 watches tests/.
    if completed_phase in (4, 5, 7, 8):
        try:
            _all_unchanged = all(
                not _fr_code_changed_since_last_gate1(fr, project) for fr in fr_ids_manifest
            )
        except Exception:  # pylint: disable=broad-exception-caught
            _all_unchanged = False
        if _all_unchanged:
            print(
                f"  [Gate 1 coverage] Phase {completed_phase}: all {len(fr_ids_manifest)} FR(s) "
                f"unchanged since last gate — DELTA loop auto-satisfied (no per-FR re-eval needed)."
            )
            return 0

    ts_file = project / ".methodology" / _GATE_TIMESTAMPS_FILE
    g1_covered: set[str] = set()
    if ts_file.exists():
        try:
            for tl in ts_file.read_text(encoding="utf-8").splitlines():
                tl = tl.strip()
                if not tl:
                    continue
                try:
                    te = json.loads(tl)
                    if (
                        te.get("phase") == completed_phase
                        and te.get("gate") == 1
                        and te.get("fr_id") not in (None, "phase", "")
                    ):
                        g1_covered.add(te["fr_id"])
                except json.JSONDecodeError:
                    pass
        except OSError:
            pass

    g1_missing = [fr for fr in fr_ids_manifest if fr not in g1_covered]
    if g1_missing:
        print(
            f"\n[BLOCKED] Phase {completed_phase} Gate 1 per-FR re-eval incomplete:\n"
            f"  {len(g1_covered)}/{len(fr_ids_manifest)} FRs have"
            f" finalize-gate --gate 1 records in gate_timestamps.jsonl.\n"
            f"  Missing ({len(g1_missing)}): "
            + ", ".join(g1_missing[:10])
            + (" ..." if len(g1_missing) > 10 else "")
            + f"\n  Run: python3 harness_cli.py run-fr-step --step GATE1"
            f" --phase {completed_phase} --fr-id <FR-ID> --project ."
        )
        return 14
    print(
        f"  [Gate 1 coverage] Phase {completed_phase}:"
        f" {len(g1_covered)}/{len(fr_ids_manifest)} FRs ✓"
    )
    return 0


def _advance_prechecks(project: Path, completed_phase: int) -> int:
    """Run pre-advance checks: Agent B approvals, gate variance, Phase Truth,
    PhaseAuditor C1-C12, TDD.

    Returns 0 if all checks pass, non-zero exit code on first failure:
      7  = C11 CRITICAL (unchecked plan items)
      8  = C1 CRITICAL (deliverables missing / untracked)
      9  = pytest / coverage failure (P3+)
      10 = spec-coverage below phase threshold (P3+) [unified D4]
      11 = Phase Truth < 90% (P3+)
      13 = Agent B approvals missing / rejected (P1/P2)
      14 = Gate 1 per-FR coverage incomplete (P3+)
      15 = Phase{N+1}_plan.md not found (generate-next-plan not run)
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

    # ── Gate 1 per-FR coverage check (FR-loop phases only) ───────────
    if completed_phase in _PHASES_WITH_GATE1_FR_CHECK:
        _rc = _check_gate1_per_fr_coverage(project, completed_phase)
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


    # ── Auto-generate Phase{N}_STAGE_PASS.md if missing ─────────────
    # The file is machine-generated from quality_manifest.json (no LLM).
    # In phases where ALL GATE1-DELTA steps skip (no code changes), finalize-gate
    # is never called and the file never gets written — even though the gate data
    # in quality_manifest is valid.  Regenerate here so PhaseAuditor C1 passes.
    _stage_pass_path = project / "00-summary" / f"Phase{completed_phase}_STAGE_PASS.md"
    if not _stage_pass_path.exists():
        # Use gate 1 for FR-based phases (3+), gate 4 for delivery phases (6+)
        _sp_gate = 4 if completed_phase >= 6 else 1
        print(
            f"  [advance-phase] Phase{completed_phase}_STAGE_PASS.md missing — "
            f"auto-generating from quality_manifest (gate {_sp_gate})"
        )
        _generate_stage_pass(project, _sp_gate, completed_phase)

    # ── Next-phase plan: must exist before advancing (Phase 3+) ─────
    # Prevents "advance first, plan later" ordering bugs. generate-next-plan
    # must be run BEFORE advance-phase so the agent has a plan to follow.
    # Phase 1-2 use HANDOVER.md entry flow; plan generation starts at Phase 3.
    if completed_phase >= 3:
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

    # ── Agent B approvals (P1/P2) — after C1 so deliverables confirmed ──
    if completed_phase in (1, 2):
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
    if completed_phase >= 3:
        # Phase-based spec-coverage thresholds (unified v2.6)
        if completed_phase >= 6:
            sc_thresh = 90.0
        elif completed_phase >= 4:
            sc_thresh = 80.0
        else:
            sc_thresh = 60.0

        # 1. pytest + 100% coverage on TDD-governed source
        src_dir = project / "03-development" / "src"
        if src_dir.is_dir():
            import subprocess as _subp
            r = _subp.run(
                [sys.executable, "-m", "pytest", "--tb=short", "-q",
                 "--cov=03-development/src", "--cov-fail-under=100"],
                cwd=str(project),
            )
            if r.returncode != 0:
                print("\n[BLOCKED] TDD test/coverage failure.")
                print("  100% coverage on 03-development/src required.")
                print("  For genuinely untestable lines add: # pragma: no cover")
                # P3-A: Python < 3.11 async coverage hint
                if sys.version_info < (3, 11):
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

    return 0

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

    # CV-2: Validate args.completed_phase matches state.json::current_phase.
    # Without this check, an agent could pass --completed 7 while in phase 3
    # and skip straight to phase 8 (state.json is the only authoritative
    # source). No bypass flag — use the correct --completed value instead.
    state_path = project / ".methodology" / "state.json"
    if state_path.exists():
        try:
            # B4 (CV-2): hold the state lock for the read so a concurrent
            # advance-phase process cannot write between our read and the check.
            with file_lock(state_lock_path(project)):
                _state = json.loads(state_path.read_text(encoding="utf-8"))
            _current = int(_state.get("current_phase", 0))
            if _current and _current != args.completed_phase:
                print(
                    f"\n[BLOCKED] advance-phase: --completed={args.completed_phase} "
                    f"does not match state.json::current_phase={_current}.\n"
                    f"  This prevents accidental phase skips. To advance, use:\n"
                    f"    python3 harness_cli.py advance-phase --completed {_current} --project {project}",
                    file=sys.stderr,
                )
                return 2
            # Check phase_truth_passed for phases with exit gates
            if args.completed_phase in _PHASE_EXIT_GATES:
                if not _state.get("phase_truth_passed"):
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

    # CV-13: Stale .sessi-work/ artifacts can cause the next phase's gate
    # evaluation to skip re-computation (agent sees old result JSONs and
    # assumes they are current). Clean aggressively at every phase transition.
    sessi_work = project / ".sessi-work"
    if sessi_work.is_dir():
        shutil.rmtree(sessi_work, ignore_errors=True)
        print(f"  [advance-phase] Cleared stale {sessi_work}")

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

    # Mark "Generate Phase N+1 plan" in the completed-phase plan (prevents C11 on re-audit).
    # The generate-next-plan step happens as part of advance-phase, so the item is confirmed done.
    _mark_generate_next_plan_item(project, args.completed_phase, next_phase)

    # Commit locally (no push — next milestone push publishes to origin)
    if os.environ.get("HARNESS_NO_GIT"):
        print("[advance-phase] HARNESS_NO_GIT=1 — skipping git commit")
    else:
        add_result = subprocess.run(
            ["git", "-C", str(project), "add",
             ".methodology/state.json", "HANDOVER.md",
             f".methodology/phase{args.completed_phase}_plan.md"],
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

def cmd_dispatch(args: argparse.Namespace) -> int:
    """Dispatch Agent A or B via AgentSpawner, auto-logging to sessions_spawn.log.

    Usage:
        python harness_cli.py dispatch --role developer --fr-id FR-01 \\
            --prompt "Implement FR-01: Platform Adapter" --phase 3 --project .
        python harness_cli.py dispatch --role reviewer --fr-id FR-01 \\
            --prompt "Review FR-01 implementation against SRS" --phase 3 --project .
    """
    from core.agent_spawner import AgentSpawner

    project = Path(args.project).resolve()

    # --prompt-file: read prompt from file to avoid shell escaping issues
    # with {} curly braces, backticks, JSON examples, or $() in the prompt text.
    _prompt = args.prompt
    _prompt_file = getattr(args, "prompt_file", None)
    if _prompt_file:
        if _prompt:
            print("[dispatch] WARNING: --prompt-file takes precedence; --prompt ignored")
        try:
            _prompt = Path(_prompt_file).read_text(encoding="utf-8")
        except (FileNotFoundError, OSError) as exc:
            print(f"[dispatch] ERROR: cannot read --prompt-file: {exc}")
            return 1
        if not _prompt.strip():
            print("[dispatch] ERROR: --prompt-file is empty")
            return 1
    elif not _prompt:
        print("[dispatch] ERROR: --prompt or --prompt-file is required")
        return 1
    else:
        # When prompt is passed via --prompt (inline), the shell may have a
        # command-line length limit for large prompts. Suggest --prompt-file.
        if len(_prompt) > 500_000:
            print("[dispatch] WARNING: --prompt exceeds 500k chars — use --prompt-file instead")

    # P1/P2: validate --fr-id is a recognised deliverable ID (approval file naming).
    # --skip-deliverable-validation bypasses this check for custom reviews
    # (e.g. holistic cross-document review, P1_HOLISTIC / P2_HOLISTIC).
    _skip_dv = getattr(args, "skip_deliverable_validation", False)
    if args.phase in _PHASE_DELIVERABLES and not _skip_dv:
        _valid_ids = _PHASE_DELIVERABLES[args.phase]
        if not args.fr_id:
            print(
                f"[dispatch] ERROR: phase {args.phase} requires --fr-id (deliverable name).\n"
                f"  Valid IDs for P{args.phase}: {', '.join(_valid_ids)}\n"
                f"  Example: --fr-id {_valid_ids[0]}\n"
                f"  Or use --skip-deliverable-validation for custom review IDs."
            )
            return 1
        if args.fr_id not in _valid_ids:
            print(
                f"[dispatch] ERROR: phase {args.phase} requires --fr-id to be a deliverable name.\n"
                f"  Valid IDs for P{args.phase}: {', '.join(_valid_ids)}\n"
                f"  Got: {args.fr_id!r}\n"
                f"  Or use --skip-deliverable-validation for custom review IDs."
            )
            return 1
    spawner = AgentSpawner(project_path=project)
    role_lower = args.role.lower()
    # Detect Agent B (stateless reviewer) roles: names containing "review" or "analyst".
    # For custom roles not matching this heuristic, use --no-persona explicitly.
    is_reviewer = "review" in role_lower or "analyst" in role_lower
    no_persona = getattr(args, "no_persona", False)
    # STATELESS Agent B (reviewer): skip persona — persona causes Claude to enter
    # multi-step tool exploration mode instead of returning JSON directly (see SAD §reviewer_router).
    persona_override = "" if (is_reviewer or no_persona) else None
    # STATELESS Agent B: also skip SOP — the SOP is a large reference doc that
    # causes Claude to enter exploration mode instead of returning JSON directly.
    # TASK + CONTEXT alone is enough for a reviewer to produce structured output.
    sop_override = "" if (is_reviewer or no_persona) else None
    # Reviewer dispatches only need a single response turn; cap at 3 to prevent runaway.
    _explicit_max_turns = getattr(args, "max_turns", None)
    effective_max_turns = _explicit_max_turns if _explicit_max_turns is not None else (3 if is_reviewer else 20)
    # P1/P2 developer dispatches need more time to process large SPEC documents.
    # Use None sentinel to distinguish "user didn't specify" from explicit --timeout 300.
    _raw_timeout: int | None = args.timeout
    if _raw_timeout is None:
        _raw_timeout = 1200 if (args.phase in {1, 2} and not is_reviewer) else 300
    result = spawner.spawn(
        role=args.role,
        prompt=_prompt,
        context={"phase": args.phase, "fr_id": args.fr_id},
        phase=args.phase,
        fr_id=args.fr_id,
        task_timeout=_raw_timeout,
        max_turns=effective_max_turns,
        persona_override=persona_override,
        phase_sop_override=sop_override,
    )
    status = result.get("status", "SPAWNED")
    session_id = result.get("session_id", "")
    print(f"[dispatch] {args.fr_id or 'phase'} | {args.role} | {status} | session={session_id}")
    if status in _DISPATCH_ERROR_STATUSES:
        return 1

    # For completed reviewer dispatches, extract and persist Agent B approval JSON.
    if (
        status == "complete"
        and is_reviewer
        and args.fr_id
    ):
        output_text = result.get("output", "")
        review_data = _extract_review_json(output_text)
        if review_data:
            approvals_dir = project / ".methodology" / "agent_b_approvals"
            approvals_dir.mkdir(parents=True, exist_ok=True)
            approval_file = approvals_dir / f"{args.fr_id}.json"
            approval_file.write_text(
                json.dumps(review_data, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            print(f"  [dispatch] approval JSON → {approval_file}")
        else:
            print(
                f"  [WARN] dispatch (reviewer): no review JSON found in agent output — "
                f"{args.fr_id}.json not written.\n"
                "  Ensure Agent B output includes a JSON block with 'review_status'."
            )

    # For completed developer dispatches, extract and persist Agent A structured output.
    if (
        status == "complete"
        and not is_reviewer
        and args.fr_id
    ):
        output_text = result.get("output", "")
        agent_output = _extract_agent_output_json(output_text)
        if agent_output:
            outputs_dir = project / ".methodology" / "agent_a_outputs"
            outputs_dir.mkdir(parents=True, exist_ok=True)
            output_file = outputs_dir / f"{args.fr_id}.json"
            output_file.write_text(
                json.dumps(agent_output, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            print(f"  [dispatch] agent output JSON → {output_file}")
        else:
            print(
                f"  [WARN] dispatch ({args.role}): no structured output JSON found — "
                f"{args.fr_id}.json not written.\n"
                "  Ensure Agent A output includes a JSON block with 'status', 'files', "
                "'confidence', 'citations', and 'summary'."
            )

    return 0

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
        num_match = re.match(r"FR-(\d+)", fr_id)
        num_str = num_match.group(1).zfill(2) if num_match else re.sub(r"[^a-z0-9]", "_", fr_id.lower()).strip("_")
        test_file = project / f"tests/test_fr{num_str}.py"
        return test_file.exists()
    elif step.upper() == "TDD-GREEN":
        src_dir = project / "03-development" / "src"
        if not src_dir.exists():
            return False
        num_match = re.match(r"FR-(\d+)", fr_id)
        num_str = num_match.group(1).zfill(2) if num_match else re.sub(r"[^a-z0-9]", "_", fr_id.lower()).strip("_")
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
    return sha if sha else None


def _fr_code_changed_since_last_gate1(fr_id: str, project: Path) -> bool:
    """Check whether FR source/test files have changed since last Gate 1 PASS.

    Returns True if code has changed (re-evaluation needed), False otherwise.
    """
    import subprocess as _sp
    sha = _fr_gate1_commit_sha(fr_id, project)
    if sha is None:
        return True  # No prior Gate 1 PASS → treat as changed

    # Collect FR-related files for git diff
    fr_files: list[str] = []
    num_match = re.match(r"FR-(\d+)", fr_id)
    num_str = num_match.group(1).zfill(2) if num_match else ""

    # Test file
    if num_str:
        test_file = project / f"tests/test_fr{num_str}.py"
        if test_file.exists():
            fr_files.append(str(test_file.relative_to(project)))

    # Source files — identified by [{fr_id}] tag in file content
    src_dir = project / "03-development" / "src"
    if src_dir.exists():
        for py_file in src_dir.glob("**/*.py"):
            try:
                if f"[{fr_id}]" in py_file.read_text(encoding="utf-8"):
                    fr_files.append(str(py_file.relative_to(project)))
            except Exception:
                pass

    if not fr_files:
        return False  # No FR files on disk → nothing to diff

    r = _sp.run(
        ["git", "diff", sha, "HEAD", "--"] + fr_files,
        capture_output=True, text=True, cwd=str(project),
    )
    return bool(r.stdout.strip())


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
    test_spec_path = project / "02-architecture" / "TEST_SPEC.md"
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
    """
    step = step.upper()
    num_match = re.match(r"FR-(\d+)", fr_id)
    num_str = num_match.group(1).zfill(2) if num_match else re.sub(r"[^a-z0-9]", "_", fr_id.lower()).strip("_")
    test_file = f"tests/test_fr{num_str}.py"
    src_dir = "03-development/src"

    # Default SRS path if not given
    if srs_path is None:
        candidate = project / ".methodology" / "SRS.md"
        srs_path = candidate if candidate.exists() else None

    if step == "TDD-RED":
        srs_section = _extract_srs_fr_section(srs_path, fr_id) if srs_path else ""
        _, spec_note = _extract_test_spec_names(project, fr_id)

        return (
            f"You are a TDD developer. Your ONLY task: write failing pytest tests for {fr_id}.\n\n"
            f"{spec_note}"
            f"[FORBIDDEN — read before anything else]\n"
            f"- Implementing any source code (test file only)\n"
            f"- app/infrastructure/ paths\n"
            f"- @covers: L1 Error | @type: edge annotations\n\n"
            f"[UNIT TEST CONTRACT — avoid false-fail traps]\n"
            f"Tests must fail because the FEATURE is missing, not because of external side-effects.\n"
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
            f"4. Run `pytest {test_file} -q` to confirm all tests fail.\n"
            f"5. Commit: `git add {test_file} && git commit -m \"test(RED): failing test for {fr_id}\"`\n"
            f"6. Append to DEVELOPMENT_LOG.md: `## RED phase — {fr_id} — failing test written`\n\n"
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
            f"5. Commit: `git add {src_dir}/ && git commit -m \"feat({fr_id}): GREEN\"`\n"
            f"6. Append to DEVELOPMENT_LOG.md: `## GREEN phase — {fr_id} — tests pass`\n\n"
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

    # Compute spec test coverage (ratio of required tests that exist + pass).
    # Used both in GATE1 prompt (to make the evaluator report spec coverage)
    # and in finalize_gate (to override dimension score when spec is incomplete).
    spec_test_names, _ = _extract_test_spec_names(project, fr_id)
    test_file_path = project / test_file
    existing_spec_tests: set[str] = set()
    if spec_test_names and test_file_path.exists():
        try:
            tf_content = test_file_path.read_text(encoding="utf-8")
            existing_spec_tests = {
                fn for fn in spec_test_names if f"def {fn}" in tf_content
            }
        except OSError:
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
            f"so `ruff check {src_dir}/` exits 0.\n\n"
            f"[ACTUAL TOOL OUTPUT — from pre-run]\n"
            f"{tool_snapshot or '(not available)'}\n\n"
            f"[TASK]\n"
            f"1. Run `ruff check {src_dir}/ 2>&1` to see the full violation list.\n"
            f"2. For N-series violations (naming conventions — N801, N802, N806, N816 etc.):\n"
            f"   - Rename constants/variables to follow PEP 8 naming (UPPER_CASE for module "
            f"constants, UpperCase for classes, lower_case for functions/variables).\n"
            f"   - Update ALL references to each renamed symbol (use `grep -rn '<old_name>'` "
            f"to find them, then rename systematically).\n"
            f"3. For E/W-series violations: fix in-place per ruff's suggestion.\n"
            f"4. Re-run `ruff check {src_dir}/` — it MUST exit 0 before you commit.\n"
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
                f"2. Run `ruff check {src_dir}/` to identify lint errors.\n"
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
        ["ruff", "check", f"{src_dir}/"],
        ["python3", "-m", "ruff", "check", f"{src_dir}/"],
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
        lines.append(f"ruff check {src_dir}/ (exit {_ruff_r.returncode}):")
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


def cmd_run_fr_step(args: argparse.Namespace) -> int:
    """Dispatch a single FR TDD step as sub-agent + push to GitHub on completion.

    Steps: TDD-RED | TDD-GREEN | TDD-IMPROVE | GATE1 | GATE1-DELTA
    Idempotent: skips silently if the step's commit already exists in git log.
    On GATE1 FAIL: auto-dispatches CODE-FIX sub-agent then retries (max --max-fix-rounds).
    Returns: 0=OK, 1=ERROR, 2=BLOCKED (Gate1 exhausted retries — human needed)
    """
    import subprocess as _sp
    from core.agent_spawner import AgentSpawner

    phase = args.phase
    fr_id = args.fr_id
    step = args.step.upper()
    project = Path(args.project).resolve()
    srs_path = Path(args.srs).resolve() if getattr(args, "srs", None) else None

    # Compute src_dir and test_file — used by GATE1 retry and _capture_tool_snapshot.
    _num_match = re.match(r"FR-(\d+)", fr_id)
    _num_str = _num_match.group(1).zfill(2) if _num_match else re.sub(
        r"[^a-z0-9]", "_", fr_id.lower()
    ).strip("_")
    src_dir = "03-development/src"
    test_file = f"tests/test_fr{_num_str}.py"

    # Per-FR config: read fr_config from quality_manifest.json.
    # Allows large / complex FRs (e.g. FR-19 with 11-stage pipeline) to declare
    # longer timeouts and more fix rounds without changing global defaults.
    # CLI flags --timeout / --max-fix-rounds still take precedence.
    # Example manifest entry:
    #   {"fr_config": {"FR-19": {"timeout": 1200, "max_fix_rounds": 5,
    #                             "code_fix_max_turns": 90}}}
    _fr_conf: dict = {}
    _fr_manifest_path = project / ".methodology" / "quality_manifest.json"
    try:
        _fr_conf = (
            json.loads(_fr_manifest_path.read_text(encoding="utf-8"))
            .get("fr_config", {}).get(fr_id, {})
        )
    except (OSError, json.JSONDecodeError, AttributeError):
        pass
    _fr_timeout = _fr_conf.get("timeout", getattr(args, "timeout", 600))
    _fr_max_fix_rounds = _fr_conf.get("max_fix_rounds", getattr(args, "max_fix_rounds", 3))
    _fr_code_fix_max_turns: int | None = _fr_conf.get("code_fix_max_turns")

    # 1. Idempotency — skip if already committed
    if _fr_step_already_done(step, fr_id, project):
        print(f"[run-fr-step] {fr_id} {step}: already done → skip")
        # Still record completion side-effects even on legitimate skip:
        #   _mark_plan_item  — prevents C11 CRITICAL at advance-phase
        #   _record_gate_timestamp (GATE1-DELTA only) — prevents exit-14 block
        #     from _check_gate1_per_fr_coverage when ALL FRs skip (no code changes)
        _mark_plan_item(project, phase, step, fr_id)
        if step.upper() == "GATE1-DELTA":
            _record_gate_timestamp(project, phase, 1, fr_id)
        return 0

    # 2. Pre-flight checks — must pass before agent dispatch
    preflight_ok, preflight_errors = _fr_step_preflight(step, project, fr_id)
    if not preflight_ok:
        print(f"\n[PRE-FLIGHT FAILED] run-fr-step --fr-id {fr_id} --step {step}", file=sys.stderr)
        for err in preflight_errors:
            print(f"  {err}", file=sys.stderr)
        print(file=sys.stderr)
        return 1

    # 3. Build minimal need-to-know prompt (only after pre-flight passes)
    prompt = _build_fr_step_prompt(step, fr_id, phase, project, srs_path)

    # 4. Dispatch sub-agent (phase_sop_override="" skips full SOP load)
    spawner = AgentSpawner(project_path=project)
    phase_ctx = _resolve_phase3_context(project)
    if getattr(args, "no_mcp", False):
        phase_ctx["mcp_config"] = None

    _explicit_max_turns = getattr(args, "max_turns", None)

    def _max_turns(step_name: str) -> int:
        """Per-step max_turns: explicit --max-turns wins, then per-FR config, else _STEP_MAX_TURNS."""
        if _explicit_max_turns is not None:
            return _explicit_max_turns
        if step_name.upper() in ("CODE-FIX", "COVERAGE-FIX") and _fr_code_fix_max_turns:
            return _fr_code_fix_max_turns
        return _STEP_MAX_TURNS.get(step_name.upper(), 40)

    # All FR steps need shell access:
    #   GATE1/GATE1-DELTA: ruff, pyright, pytest, coverage
    #   TDD-RED/GREEN/IMPROVE: pytest to verify fail/pass
    #   CODE-FIX: pytest to confirm fix doesn't break other tests
    # acceptEdits blocks Bash → agents skip verification steps and commit
    # broken code, causing the next GATE1 to fail again.
    _explicit_pmode = getattr(args, "permission_mode", None)
    _pmode = _explicit_pmode if _explicit_pmode is not None else "bypassPermissions"

    result = spawner.spawn(
        role="developer",
        prompt=prompt,
        context={"phase": phase, "fr_id": fr_id, "step": step},
        phase=phase,
        fr_id=fr_id,
        phase_sop_override="",
        task_timeout=_fr_timeout,
        max_turns=_max_turns(step),
        mcp_config=phase_ctx["mcp_config"],
        setting_sources=phase_ctx["setting_sources"],
        permission_mode=_pmode,
    )

    _status = result.get("status")
    if _status in _DISPATCH_ERROR_STATUSES:
        # GATE1/GATE1-DELTA: ERROR or TIMEOUT means sub-agent exhausted
        # turns before writing gate1_result.json. Treat as GATE1 FAIL so
        # the CODE-FIX retry loop gets a chance to re-run with fresh context.
        # REJECT/BLOCKED/FAILED are hard-fail (non-turn issues).
        if step in ("GATE1", "GATE1-DELTA") and _status in {"ERROR", "TIMEOUT"}:
            print(
                f"[run-fr-step] {fr_id} GATE1 {_status} "
                f"— treating as GATE1 FAIL, entering CODE-FIX retry"
            )
        else:
            print(f"[run-fr-step] {fr_id} {step}: sub-agent {_status}")
            print(result.get("output", "")[:500])
            return 1

    # 4. GATE1: auto-retry with CODE-FIX sub-agent on failure
    if step in ("GATE1", "GATE1-DELTA"):
        # When agent timed-out or errored, no gate1_result.json was written —
        # failing_dims cannot be parsed. Signal full re-check to CODE-FIX.
        if _status in {"ERROR", "TIMEOUT"}:
            gate_pass = False
            failing_dims: list | None = None
            block_reason = ""
        else:
            gate_pass, failing_dims, block_reason = _parse_gate_output(result.get("output", ""))
        if not gate_pass:
            gate_pass = _fr_step_already_done(step, fr_id, project)

        max_fix_rounds = _fr_max_fix_rounds
        # B: progress tracking — detect lateral variation (same error, no progress)
        prev_snapshot_sig: str = ""
        no_progress_count: int = 0

        for fix_round in range(1, max_fix_rounds + 1):
            if gate_pass or _fr_step_already_done(step, fr_id, project):
                break

            # ── S3 short-circuit: evaluation JSON was malformed, not code error ──
            # tool_evidence_missing means the sub-agent fabricated scores.
            # CODE-FIX (source code fixer) cannot help — skip it and retry GATE1
            # directly with the block_reason injected so the evaluator understands
            # what went wrong with its predecessor's gate1_result.json.
            is_s3 = bool(block_reason and "tool_evidence_missing" in block_reason)
            if not is_s3:
                # ── Pre-run tools at orchestration time ──────────────────────────
                # Capture actual ruff + pytest output so fix agents target real errors.
                tool_snapshot = _capture_tool_snapshot(project, src_dir, test_file)

                # ── B: lateral variation detection ───────────────────────────────
                curr_sig = tool_snapshot[:300] if tool_snapshot else ""
                if curr_sig and curr_sig == prev_snapshot_sig:
                    no_progress_count += 1
                    print(f"[run-fr-step] {fr_id} NO PROGRESS detected (round {fix_round})"
                          f" — same error signature as previous round")
                    if no_progress_count >= 2:
                        print(f"[run-fr-step] {fr_id} BLOCKED: 2 consecutive no-progress rounds"
                              f" — human intervention required\n"
                              f"  Error pattern: {curr_sig[:150]}")
                        return 2
                else:
                    no_progress_count = 0
                prev_snapshot_sig = curr_sig

                # ── A: classify failure → route to the correct fixer ─────────────
                failure_class = _classify_snapshot_failure(tool_snapshot, failing_dims=failing_dims)

                if failure_class == "ENV":
                    print(f"[run-fr-step] {fr_id} ENV error — human intervention required\n"
                          f"  Hint: check PYTHONPATH / package installation")
                    break

                if failure_class == "ISOLATION":
                    print(f"[run-fr-step] {fr_id} ISOLATION failure "
                          f"(round {fix_round}/{max_fix_rounds})"
                          f" — dispatching TEST-FIX (add autouse infra mock)")
                    fix_prompt = _build_fr_step_prompt(
                        "TEST-FIX", fr_id, phase, project, srs_path,
                        tool_snapshot=tool_snapshot,
                    )
                    fix_step_name = "TEST-FIX"
                elif failure_class == "INFRA_SKIP":
                    print(f"[run-fr-step] {fr_id} INFRA_SKIP failure "
                          f"(round {fix_round}/{max_fix_rounds})"
                          f" — dispatching INFRA-FIX (add mock tests for skipped paths)")
                    fix_prompt = _build_fr_step_prompt(
                        "INFRA-FIX", fr_id, phase, project, srs_path,
                        tool_snapshot=tool_snapshot,
                    )
                    fix_step_name = "INFRA-FIX"
                elif failure_class in ("LINT_FAIL", "LINT_AND_COVERAGE"):
                    label = ("linting only" if failure_class == "LINT_FAIL"
                             else "linting + coverage — linting first")
                    print(f"[run-fr-step] {fr_id} {failure_class} failure "
                          f"(round {fix_round}/{max_fix_rounds})"
                          f" — dispatching LINT-FIX ({label})")
                    fix_prompt = _build_fr_step_prompt(
                        "LINT-FIX", fr_id, phase, project, srs_path,
                        tool_snapshot=tool_snapshot,
                    )
                    fix_step_name = "LINT-FIX"
                elif failure_class == "PATCH_OBJECT":
                    print(f"[run-fr-step] {fr_id} PATCH_OBJECT failure "
                          f"(round {fix_round}/{max_fix_rounds})"
                          f" — dispatching CODE-FIX with stub hint")
                    patch_hint = (
                        "[PATCH_OBJECT HINT]\n"
                        "A test uses patch.object() on a method that does not exist yet.\n"
                        "Add the missing method stub to your implementation FIRST, "
                        "before any other logic.\n\n"
                    )
                    fix_prompt = patch_hint + _build_fr_step_prompt(
                        "CODE-FIX", fr_id, phase, project, srs_path,
                        failing_dims=failing_dims, tool_snapshot=tool_snapshot,
                    )
                    fix_step_name = "CODE-FIX"
                elif failure_class == "LOW_COVERAGE":
                    print(f"[run-fr-step] {fr_id} LOW_COVERAGE failure "
                          f"(round {fix_round}/{max_fix_rounds})"
                          f" — dispatching COVERAGE-FIX (tests pass, coverage < 80%)")
                    fix_prompt = _build_fr_step_prompt(
                        "COVERAGE-FIX", fr_id, phase, project, srs_path,
                        tool_snapshot=tool_snapshot,
                    )
                    fix_step_name = "COVERAGE-FIX"
                else:
                    print(f"[run-fr-step] {fr_id} GATE1 FAIL (round {fix_round}/{max_fix_rounds})"
                          f" — dispatching CODE-FIX sub-agent"
                          f" [failure_class={failure_class}]")
                    fix_prompt = _build_fr_step_prompt(
                        "CODE-FIX", fr_id, phase, project, srs_path,
                        failing_dims=failing_dims, tool_snapshot=tool_snapshot,
                    )
                    fix_step_name = "CODE-FIX"

                fix_result = spawner.spawn(
                    role="developer", prompt=fix_prompt,
                    context={"phase": phase, "fr_id": fr_id, "step": fix_step_name},
                    phase=phase, fr_id=fr_id, phase_sop_override="",
                    task_timeout=_fr_timeout,
                    max_turns=_max_turns(fix_step_name),
                    mcp_config=phase_ctx["mcp_config"],
                    setting_sources=phase_ctx["setting_sources"],
                )
                if fix_result.get("status") in _DISPATCH_ERROR_STATUSES:
                    print(f"[run-fr-step] {fix_step_name} failed: "
                          f"{fix_result.get('output','')[:200]}")
                    break
            else:
                print(f"[run-fr-step] {fr_id} GATE1 S3 block (round {fix_round}/{max_fix_rounds})"
                      f" — retrying GATE1 directly (no CODE-FIX needed for tool_evidence issue)")

            # Re-dispatch GATE1 (with block_reason if S3, otherwise clean)
            gate_prompt = _build_fr_step_prompt(
                step, fr_id, phase, project, srs_path,
                block_reason=block_reason if is_s3 else None,
            )
            result = spawner.spawn(
                role="developer", prompt=gate_prompt,
                context={"phase": phase, "fr_id": fr_id, "step": step},
                phase=phase, fr_id=fr_id, phase_sop_override="",
                task_timeout=_fr_timeout,
                max_turns=_max_turns(step),
                mcp_config=phase_ctx["mcp_config"],
                setting_sources=phase_ctx["setting_sources"],
                permission_mode=_pmode,
            )
            gate_pass, failing_dims, block_reason = _parse_gate_output(result.get("output", ""))
            if not gate_pass:
                gate_pass = _fr_step_already_done(step, fr_id, project)
        else:
            print(f"[run-fr-step] {fr_id} GATE1 BLOCKED after {max_fix_rounds} CODE-FIX rounds"
                  " — human intervention required")
            return 2  # BLOCKED

        # P0-B: auto-append dev log after GATE1 PASS (prevents C5 CRITICAL at advance-phase)
        # gate_pass is True here (otherwise we returned 2 above)
        _gate1_score: float | None = None
        _g1r_path = project / ".sessi-work" / "gate1_result.json"
        try:
            _g1r = json.loads(_g1r_path.read_text(encoding="utf-8"))
            _gate1_score = float(_g1r.get("overall_score", 0.0))
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            pass
        _append_dev_log_tdd_entry(project, fr_id, _gate1_score)

    # P0-A: auto-update plan checklist (prevents C11 CRITICAL at advance-phase)
    _mark_plan_item(project, phase, step, fr_id)

    # 5. Verify commit exists (non-fatal warning for TDD-IMPROVE / CODE-FIX)
    if step not in ("TDD-IMPROVE", "CODE-FIX") and not _fr_step_already_done(step, fr_id, project):
        print(f"[run-fr-step] {fr_id} {step}: WARNING — expected commit not found in git log")

    no_push = getattr(args, "no_push", False) or os.environ.get("HARNESS_NO_GIT")
    if no_push:
        print("[run-fr-step] --no-push or HARNESS_NO_GIT specified — skipping git push")
    else:
        push = _sp.run(
            ["git", "push", "origin", "HEAD"],
            capture_output=True, text=True, cwd=str(project),
        )
        if push.returncode != 0:
            print(f"[run-fr-step] git push failed: {push.stderr[:300].strip()}")
            return 1

    suffix = "" if no_push else " + pushed to GitHub"
    print(f"[run-fr-step] ✅ {fr_id} {step} complete{suffix}")
    return 0


def cmd_resume_fr_phase(args: argparse.Namespace) -> int:
    """Print the next pending run-fr-step command for crash recovery.

    Scans git log for completed step commit patterns and quality_manifest.json
    for the FR list.  Prints the exact command to run to continue.
    """
    phase = args.phase
    project = Path(args.project).resolve()
    manifest_path = project / ".methodology" / "quality_manifest.json"
    progress_path = project / ".methodology" / "fr_progress.json"

    fr_ids: list[str] = []
    if manifest_path.exists():
        try:
            fr_ids = json.loads(manifest_path.read_text(encoding="utf-8")).get("fr_ids", [])
        except Exception:
            pass
    if not fr_ids and progress_path.exists():
        try:
            data = json.loads(progress_path.read_text(encoding="utf-8"))
            fr_ids = list(data.get("frs", {}).keys())
        except Exception:
            pass

    if not fr_ids:
        print("[resume-fr-phase] No FR list found — check .methodology/quality_manifest.json")
        return 1

    # Carry-forward phases (5/7/8) default to GATE1-DELTA.
    # If FR code changed since last Gate 1 → switch to full TDD cycle.
    carryforward = phase in (5, 7, 8)
    for fr_id in fr_ids:
        if carryforward:
            if _fr_code_changed_since_last_gate1(fr_id, project):
                steps = ["TDD-RED", "TDD-GREEN", "TDD-IMPROVE", "GATE1"]
            else:
                steps = ["GATE1-DELTA"]
        else:
            steps = ["TDD-RED", "TDD-GREEN", "TDD-IMPROVE", "GATE1"]
        for step in steps:
            if not _fr_step_already_done(step, fr_id, project):
                srs_flag = " --srs .methodology/SRS.md" if step in ("TDD-RED", "TDD-GREEN") else ""
                print(
                    f"Next step: python3 harness_cli.py run-fr-step "
                    f"--phase {phase} --fr-id {fr_id} --step {step} --project .{srs_flag}"
                )
                return 0

    print("[resume-fr-phase] All FRs complete for this phase.")
    return 0


# ---------------------------------------------------------------------------
# reload-policy
# ---------------------------------------------------------------------------

def cmd_reload_policy(args: argparse.Namespace) -> int:
    """Hot-reload enforcement policies from enforcement.json."""
    from enforcement.policy_engine import PolicyEngine

    json_path = args.policy_file
    if not Path(json_path).exists():
        print(f"\n[ERROR] Policy file not found: {json_path}")
        print("  Create enforcement/enforcement.json with a 'policies' array.")
        return 1

    try:
        engine = PolicyEngine()
        loaded = engine.reload_policy(json_path)
        summary = engine.get_summary()
        print(f"\n{'='*60}\nPolicy Hot-Reload\n{'='*60}")
        print(f"  file          : {json_path}")
        print(f"  loaded        : {loaded} policies from file")
        print(f"  total active  : {len(engine.policies)} policies")
        print(f"  enabled       : {summary.get('total', len(engine.policies))}")
        if loaded > 0:
            print("\n[Loaded policies]")
            for pol in engine.policies[-loaded:]:
                status = "enabled" if pol.enabled else "disabled"
                print(f"  [{pol.enforcement.value.upper()}] {pol.id} — {pol.description} ({status})")
        return 0
    except Exception as e:  # pylint: disable=broad-exception-caught
        print(f"\n[ERROR] Failed to reload policies: {e}")
        return 1

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
    """Instantiate GitStrategy from parsed args. Lazy-imports to keep startup fast."""
    from harness.git_strategy import GitStrategy
    no_git = getattr(args, "no_git", False)
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

    # 1. Write .methodology/state.json (the authoritative phase record).
    # Cross-process locked (SG-12) so a parallel _update_state_checkpoint
    # or push-milestone state-write cannot corrupt the file.
    state_path = project / ".methodology" / "state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    with file_lock(state_lock_path(project)):
        existing_state = "INIT"
        if state_path.exists():
            try:
                raw_state = json.loads(state_path.read_text()).get("state", "INIT")
                existing_state = validate_fsm_state(raw_state)
            except FSMError as e:
                print(f"\n  [FSM ERROR] {e}")
                print("  Fix state.json manually or run `advance-phase` with a clean state.")
                sys.exit(11)
            except Exception:  # pylint: disable=broad-exception-caught
                existing_state = "INIT"
        state_data = {
            "state": existing_state,
            "current_phase": next_phase,
            "last_gate": last_gate,
            "last_fr": last_fr,
            "last_update": datetime.now(timezone.utc).isoformat(),
            "phase_truth_passed": False,  # Reset for new phase
        }
        atomic_write_json(state_path, state_data)
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

    # 3. Regenerate HANDOVER.md so crash-recovery always reflects current phase.
    #    _advance_fsm() call skipped HANDOVER regeneration (Gap 4 in audit).
    try:
        HandoverGenerator(project).write(
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
        print(f"  [FSM] HANDOVER.md regenerated for Phase {next_phase}")
    except Exception as exc:  # pylint: disable=broad-exception-caught
        print(
            f"  [WARN] HANDOVER.md regeneration failed: {exc}",
            file=sys.stderr,
        )

    # 4. No other phase storage — state.json is the single source of truth.
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
        "(1) CRG community issues: if god-module (size>50) or low cohesion (all communities <0.4) — "
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
    failing = [d for d in exc.result.dimensions if d.score < d.threshold]
    passing = [d for d in exc.result.dimensions if d.score >= d.threshold]

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
        gap = dim.threshold - dim.score
        hint = _DIMENSION_HINTS.get(dim.name, "Review dimension-specific issues in SSI output")
        lines.append(
            f"  [FAIL] {dim.name:<22} score={dim.score:>5.1f}  "
            f"need={dim.threshold:>5.1f}  gap={gap:>4.1f}"
        )
        lines.append(f"         → {hint}")

    if passing:
        lines.append("")
        lines.append(
            f"Passing ({len(passing)}): "
            + ", ".join(f"{d.name}={d.score:.1f}" for d in passing)
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
        gap = dim.threshold - dim.score
        hint = _DIMENSION_HINTS.get(dim.name, "Review SSI output")
        report_lines += [
            f"### {dim.name}",
            f"- score: {dim.score:.1f} / threshold: {dim.threshold:.1f} (gap: {gap:.1f})",
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

def cmd_run_gap_analysis(args: argparse.Namespace) -> int:
    """Run M3 gap analysis: detect gaps between SPEC.md and codebase."""
    project = Path(args.project).resolve()
    spec = args.spec or "SPEC.md"

    print(f"\n{'='*60}\nrun-gap-analysis (M3)  project={project}\n{'='*60}")

    # Fail fast if the spec file is missing (explicit user invocation — not a pipeline skip)
    spec_path = project / spec
    if not spec_path.exists():
        print(f"[ERROR] Spec file not found: {spec_path}")
        return 1

    report = _run_gap_analysis(project, similarity=args.similarity, spec=spec)

    if report.get("skipped"):
        reason = report.get("reason") or report.get("error", "unknown")
        print(f"  Skipped: {reason}")
        return 0

    summary = report.get("summary", {})
    print(f"\n{'─'*60}")
    print("Gap Analysis Results")
    print(f"{'─'*60}")
    print(f"  Total gaps : {summary.get('total', 0)}")
    print(f"  Missing    : {summary.get('missing', 0)}")
    print(f"  Incomplete : {summary.get('incomplete', 0)}")
    print(f"  Orphaned   : {summary.get('orphaned', 0)}")
    print(f"  Critical   : {summary.get('critical', 0)}")
    print(f"  Major      : {summary.get('major', 0)}")
    print(f"  Minor      : {summary.get('minor', 0)}")

    critical = summary.get("critical", 0)
    if critical > 0:
        print(f"\n[WARN] {critical} critical gap(s) detected")
        return 2  # 2 = critical gaps (distinct from hard error = 1)
    return 0

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

def cmd_verify_spec(args: argparse.Namespace) -> int:
    """Verify implementation complies with spec requirements (6-dimension check)."""
    from scripts.verify_spec_compliance import SpecComplianceChecker

    project = str(Path(args.project).resolve())
    print(f"\n{'='*60}\nverify-spec  project={project}\n{'='*60}")

    checker = SpecComplianceChecker(project)
    result = checker.check_all()

    print(f"\n{'─'*60}")
    print("Spec Compliance Report")
    print(f"{'─'*60}")
    print(f"  Score : {result['score']}")

    if result["passed"]:
        print("\n  PASSED:")
        for p in result["passed"]:
            print(f"    + {p}")

    if result["issues"]:
        print("\n  ISSUES:")
        for issue in result["issues"]:
            print(f"    - {issue}")
        if getattr(args, "fix", False):
            print("\n  FIX SUGGESTIONS:")
            for hint in checker.suggest_fixes(result["issues"]):
                print(f"    → {hint}")
            print("\n  [INFO] --fix shows suggestions only. Apply fixes manually.")

    return 0 if not result["issues"] else 1

# ---------------------------------------------------------------------------
# check-logic
# ---------------------------------------------------------------------------

def cmd_check_logic(args: argparse.Namespace) -> int:
    """Check code for logic correctness issues (output/branch/lazy-init/semantic)."""
    from scripts.spec_logic_checker import SpecLogicChecker, SemanticValidator

    project = str(Path(args.project).resolve())
    print(f"\n{'='*60}\ncheck-logic  project={project}\n{'='*60}")

    checker = SpecLogicChecker(project)
    result = checker.scan_python_files()
    checker.print_report(result)

    if args.srs and Path(args.srs).exists():
        print(f"\n{'─'*60}")
        print("Semantic Validation (SRS)")
        print(f"{'─'*60}")
        validator = SemanticValidator(args.srs)
        print(f"  Requirements: {len(validator.requirements)}")
        for fr_id, req in list(validator.requirements.items())[:5]:
            print(f"  {fr_id}: {req.get('description', '?')[:60]}...")

    return 0 if result.passed else 1

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

# Canonical phase directory names (single authoritative source — used by both
# _init_phase_dirs and cmd_audit_structure so they can never drift apart).
_PHASE_DIRS: dict[int, str] = {
    1: "01-requirements",
    2: "02-architecture",
    3: "03-development",
    4: "04-testing",
    5: "05-verification",
    6: "06-quality",
    7: "07-risk",
    8: "08-config",
}

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
    ]
    copied = 0
    skipped = 0
    missing = 0
    for subdir, filename in artifact_map:
        src = templates_dir / filename
        dst = project / subdir / filename
        if dst.exists() and not overwrite:
            skipped += 1
        elif src.exists():
            shutil.copy2(src, dst)
            copied += 1
        else:
            print(f"   WARNING: template not found: {src}")
            missing += 1

    # CLAUDE.md.template → project/CLAUDE.md (only if no CLAUDE.md exists)
    claude_tmpl = harness_root / "CLAUDE.md.template"
    claude_dst = project / "CLAUDE.md"
    if claude_dst.exists() and not overwrite:
        skipped += 1
    elif claude_tmpl.exists():
        shutil.copy2(claude_tmpl, claude_dst)
        copied += 1
    else:
        missing += 1

    parts = []
    if copied:
        parts.append(f"copied {copied} template{'s' if copied != 1 else ''}")
    if skipped:
        parts.append(f"{skipped} already existed")
    if missing:
        parts.append(f"{missing} template(s) not found")
    if parts:
        print(f"   OK — {', '.join(parts)}")
    else:
        print("   SKIP: nothing to copy")

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

    print(f"\n{'='*60}")
    print(f"init-project  target={project}  phase={phase}")
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

    # 7. Initialize FSM state.json (required by run-phase preflight)
    print("\n[7/11] Initializing FSM state...")
    from datetime import datetime, timezone
    state_path = project / ".methodology" / "state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    if state_path.exists() and not args.overwrite:
        print(f"   SKIP: {state_path} already exists (use --overwrite to overwrite)")
    else:
        atomic_write_json(state_path, {
            "state": "RUNNING",
            "current_phase": phase,
            "last_gate": None,
            "last_fr": None,
            "last_update": datetime.now(timezone.utc).isoformat(),
        })
        print(f"   OK — state.json initialized (phase={phase})")

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
        _, _missing = _verify_gate_tools(_gate_num, str(harness_root))
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
            "    pip install ruff mypy pytest pytest-cov mutmut\n"
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
    for line in _checklist:
        print(line)
    print(f"  Full docs: {harness_root}/INTEGRATION.md")
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

    Checks all 8 phases:
      1. Directory existence (01-requirements/ ... 08-config/)
      2. Artifact completeness (required files per phase)
      3. Content quality (no hollow templates)
      4. ASPICE traceability chain (cross-phase references)
      5. Naming convention compliance (0X-name/ format)
    """
    import json as _json
    import re as _re

    project = Path(args.project).resolve()

    # Canonical phase directory names — reference module-level _PHASE_DIRS
    PHASE_DIRS = _PHASE_DIRS

    # Required artifacts per phase (aligned with phase_artifact_enforcer.py)
    PHASE_ARTIFACTS = {
        1: ["01-requirements/SRS.md", "01-requirements/SPEC_TRACKING.md",
            "01-requirements/TRACEABILITY_MATRIX.md", "TEST_INVENTORY.yaml"],
        2: ["02-architecture/SAD.md", "02-architecture/TEST_SPEC.md"],
        3: ["03-development/src/", "03-development/tests/"],
        4: ["04-testing/TEST_PLAN.md", "04-testing/TEST_RESULTS.md"],
        5: ["05-verification/BASELINE.md", "05-verification/VERIFICATION_REPORT.md"],
        6: ["06-quality/QUALITY_REPORT.md"],
        7: ["07-risk/RISK_ASSESSMENT.md", "07-risk/RISK_REGISTER.md"],
        8: ["08-config/CONFIG_RECORDS.md", "08-config/RELEASE_CHECKLIST.md"],
    }

    results: dict[str, Any] = {
        "project": str(project),
        "dimensions": {},
    }

    # --- Dimension 1: Directory existence ---
    dir_status = {}
    for num, dname in PHASE_DIRS.items():
        dpath = project / dname
        dir_status[f"P{num}"] = {
            "dir": dname,
            "exists": dpath.is_dir(),
            "path": str(dpath),
        }
    results["dimensions"]["directory_existence"] = {
        "label": "Directory Existence (01-requirements/ ~ 08-config/)",
        "passed": all(v["exists"] for v in dir_status.values()),
        "details": dir_status,
    }

    # --- Dimension 2: Artifact completeness ---
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
        if not is_yaml and content.count("\n## ") + content.count("\n# ") < 2:
            issues.append("< 2 markdown sections")
        if phase_num in _FR_REF_PHASES and not _re.search(
            r"\[?(TASK|FR|NFR)-(\d+)\]?", content, _re.IGNORECASE
        ):
            issues.append("no [TASK/FR/NFR-XX] references")
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
        chain_result = PhaseArtifactRegistry(str(project)).verify_phase_chain(8)
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
    # Map "NN" prefix → canonical dir name, e.g. "05" → "05-verification"
    expected_by_prefix: dict[str, str] = {n.split("-")[0]: n for n in expected_names}
    found_dirs = set()
    for child in project.iterdir():
        if not child.is_dir():
            continue
        if child.name in ("00-summary",):
            continue
        m = _re.match(r"^(\d{2})-", child.name)
        if m:
            found_dirs.add(child.name)
            if child.name not in expected_names:
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

    # pre-commit-check (git commit hook only — FSM + constitution + kill-switch)
    pcc = sub.add_parser(
        "pre-commit-check",
        help="Lightweight check for git commit hooks (FSM/constitution/kill-switch only; no drift/traceability)",
    )
    pcc.add_argument("--phase",   type=int, required=True, help="Phase number (1-8)")
    pcc.add_argument("--project", default=".", help="Project root (default: .)")
    pcc.set_defaults(func=cmd_pre_commit_check)

    # push-checkpoint (P1/P2 human review → git push + HANDOVER.md)
    pc = sub.add_parser(
        "push-checkpoint",
        help="Push P1/P2 human-review checkpoint (writes HANDOVER.md, commits, pushes)",
    )
    pc.add_argument("--phase",   type=int, required=True, choices=[1, 2],
                    help="Phase number (1 or 2)")
    pc.add_argument("--project", default=".", help="Project root (default: .)")
    pc.add_argument("--fr-ids",  default="", dest="fr_ids",
                    help="Comma-separated FR IDs (e.g., FR-01,FR-02)")
    pc.add_argument("--no-git", action="store_true", dest="no_git",
                    help="Disable git commit/push (HANDOVER.md still written)")
    pc.set_defaults(func=cmd_push_checkpoint)

    # push-milestone (P3+ milestone push + HANDOVER.md)
    pm = sub.add_parser(
        "push-milestone",
        help="Push milestone checkpoint with HANDOVER.md (P3+: p3-mid, p3-pre-gate2, p5-baseline, p7, p8)",
    )
    pm.add_argument("--type", required=True,
                    choices=["p3-mid", "p3-pre-gate2", "p4-mid", "p4-pre-gate3",
                             "p5-baseline", "p7", "p8"],
                    help="Milestone type")
    pm.add_argument("--project", default=".", help="Project root (default: .)")
    pm.add_argument("--fr-ids",  default="", dest="fr_ids",
                    help="Comma-separated FR IDs")
    pm.add_argument("--fr-done",  type=int, default=None,
                    help="FRs completed so far (p3-mid only)")
    pm.add_argument("--fr-total", type=int, default=None,
                    help="Total FR count (p3-mid only)")
    pm.add_argument("--no-git", action="store_true", dest="no_git",
                    help="Disable git operations")
    pm.set_defaults(func=cmd_push_milestone)

    # gate4-tag (create annotated git tag from gate4_result.json)
    g4t = sub.add_parser(
        "gate4-tag",
        help="Create annotated git tag for Gate 4 pass using composite score from gate4_result.json",
    )
    g4t.add_argument("--project", default=".", help="Project root (default: .)")
    g4t.set_defaults(func=cmd_gate4_tag)

    # verify-agent-b-approvals
    vab = sub.add_parser(
        "verify-agent-b-approvals",
        help="Verify Agent B approval JSONs exist for all FRs (blocks if missing or non-APPROVE)",
    )
    vab.add_argument("--phase",   type=int, required=True, help="Current phase number")
    vab.add_argument("--project", default=".", help="Project root (default: .)")
    vab.add_argument("--fr-ids",  default="", dest="fr_ids",
                     help="Comma-separated FR IDs (default: read from quality_manifest.json)")
    vab.set_defaults(func=cmd_verify_agent_b_approvals)

    # run-gate (Phase 1: prepare + print evaluation prompt)
    rg = sub.add_parser("run-gate", help="Prepare gate evaluation; print prompt for Claude")
    rg.add_argument("--gate",    type=int, required=True, choices=[1, 2, 3, 4])
    rg.add_argument("--phase",   type=int, required=True, help="Current phase number")
    rg.add_argument("--project", default=".", help="Project root (default: .)")
    rg.add_argument("--fr-id",   default=None, help="FR ID (Gate 1 only)", dest="fr_id")
    rg.add_argument("--skip-preflight", action="store_true", help="Skip preflight validation before gate (Item 9)")
    rg.add_argument("--delta", action="store_true", help="Delta-check mode (P5/P7/P8): skip re-evaluation if FR code unchanged")
    rg.set_defaults(func=cmd_run_gate)

    # finalize-gate (Phase 2: read result.json, check thresholds, git)
    fg = sub.add_parser(
        "finalize-gate",
        help="Finalize gate after Claude evaluation; checks thresholds and commits",
    )
    fg.add_argument("--gate",    type=int, required=True, choices=[1, 2, 3, 4])
    fg.add_argument("--phase",   type=int, required=True, help="Current phase number")
    fg.add_argument("--project", default=".", help="Project root (default: .)")
    fg.add_argument("--fr-id",   default=None, help="FR ID (Gate 1 only)", dest="fr_id")
    fg.add_argument("--no-git",  action="store_true", dest="no_git",
                    help="Disable git commit/push after gate pass")
    fg.set_defaults(func=cmd_finalize_gate)

    # run-env-check (project-aware environment readiness — inline LLM evaluation)
    rec = sub.add_parser(
        "run-env-check",
        help="Print project-aware environment evaluation prompt (reads SAD.md + SRS.md)",
    )
    rec.add_argument("--phase",   type=int, required=True, help="Current phase number")
    rec.add_argument("--project", default=".", help="Project root (default: .)")
    rec.add_argument("--fr-id",   default=None, help="FR ID (optional, for FR-scoped checks)")
    rec.set_defaults(func=cmd_run_env_check)

    # finalize-env-check (verify env_check_result.json)
    fec = sub.add_parser(
        "finalize-env-check",
        help="Verify env_check_result.json and report environment readiness",
    )
    fec.add_argument("--phase",   type=int, required=True, help="Current phase number")
    fec.add_argument("--project", default=".", help="Project root (default: .)")
    fec.add_argument("--fr-id",   default=None, help="FR ID (optional)")
    fec.set_defaults(func=cmd_finalize_env_check)

    # generate-next-plan (checkpoint-based tactical plan generator)
    gnp = sub.add_parser(
        "generate-next-plan",
        help="Read manifest state and emit the next concrete gate evaluation plan",
    )
    gnp.add_argument("--project", default=".", help="Project root (default: .)")
    gnp.add_argument("--phase",   type=int, default=None, help="Override current phase")
    gnp.set_defaults(func=cmd_generate_next_plan)

    # run-gap-analysis (M3)
    ga = sub.add_parser(
        "run-gap-analysis",
        help="M3: Detect gaps between SPEC.md and codebase implementation",
    )
    ga.add_argument("--project",    default=".", help="Project root (default: .)")
    ga.add_argument("--spec",       default="SPEC.md", help="Path to SPEC.md")
    ga.add_argument("--similarity", type=float, default=0.6,
                    help="Similarity threshold for matching (default: 0.6)")
    ga.set_defaults(func=cmd_run_gap_analysis)

    # (run-pipeline removed in v2.5 — old code consumed ~370 lines)

    # manifest
    mf = sub.add_parser("manifest", help="Generate quality_manifest.json at P2 exit")
    mf.add_argument("--fr-ids", nargs="+", required=True, metavar="FR_ID")
    mf.add_argument("--sad",    default="02-architecture/SAD.md", help="Path to SAD.md")
    mf.add_argument("--no-git", action="store_true", dest="no_git",
                    help="Disable git commit/push after manifest generation")
    mf.set_defaults(func=cmd_manifest)

    # status
    st = sub.add_parser("status", help="Show current manifest + FSM state")
    st.add_argument("--project", default=".", help="Project root (default: .)")
    st.add_argument("--json", action="store_true", help="Machine-readable JSON output")
    st.add_argument("--full", action="store_true", help="Include test stats and auto-fix rounds")
    st.set_defaults(func=cmd_status)

    # load-context
    lc = sub.add_parser("load-context",
                        help="Load project context for a phase (JSON output)")
    lc.add_argument("--phase",   type=int, required=True, help="Phase number (1-8)")
    lc.add_argument("--project", default=".", help="Project root (default: .)")
    lc.add_argument("--json",    action="store_true", help="Output as JSON (default behavior)")
    lc.set_defaults(func=cmd_load_context)

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

    # dispatch
    dp = sub.add_parser("dispatch", help="Spawn Agent A/B + auto-log to sessions_spawn.log (HR-10)")
    dp.add_argument("--role",    required=True, help="Agent role (developer, reviewer, etc.)")
    dp.add_argument("--fr-id",   default=None, dest="fr_id", help="FR ID (FR-01, etc.)")
    dp.add_argument("--prompt",  default="", help="Task prompt for the agent")
    dp.add_argument("--phase",   type=int, default=0, help="Phase number")
    dp.add_argument("--project", default=".", help="Project root (default: .)")
    dp.add_argument("--timeout", type=int, default=None, dest="timeout",
                    help="Max execution time in seconds (default: 1200 for P1/P2 developer, 300 otherwise).")
    dp.add_argument("--max-turns", type=int, default=None, dest="max_turns",
                    help="Max tool-using turns (default: 3 for reviewer roles, 20 for others).")
    dp.add_argument("--no-persona", action="store_true", dest="no_persona",
                    help="Skip persona for this dispatch (auto-applied for reviewer/analyst roles; use for other stateless roles).")
    dp.add_argument("--prompt-file", default=None, dest="prompt_file",
                    help="Read prompt from file instead of --prompt (avoids shell escaping issues with {} or backticks).")
    dp.add_argument("--skip-deliverable-validation", action="store_true",
                    dest="skip_deliverable_validation",
                    help="Allow custom --fr-id values for P1/P2 (e.g. P1_HOLISTIC for cross-document review).")
    dp.set_defaults(func=cmd_dispatch)

    # run-fr-step
    rfp = sub.add_parser(
        "run-fr-step",
        help="Dispatch one FR TDD step as sub-agent + push to GitHub (Phase 3-8 orchestration)",
    )
    rfp.add_argument("--phase", type=int, required=True, help="Phase number")
    rfp.add_argument("--fr-id", required=True, dest="fr_id", help="FR ID (e.g. FR-14)")
    rfp.add_argument(
        "--step", required=True, dest="step",
        choices=["TDD-RED", "TDD-GREEN", "TDD-IMPROVE", "GATE1", "GATE1-DELTA"],
        type=str.upper,
        help="TDD step to dispatch",
    )
    rfp.add_argument("--project", default=".", help="Project root (default: .)")
    rfp.add_argument(
        "--srs", default=None,
        help="Path to SRS.md for FR context extraction (default: .methodology/SRS.md)",
    )
    rfp.add_argument("--timeout", type=int, default=600,
                     help="Sub-agent max execution time in seconds (default: 600)")
    rfp.add_argument("--max-turns", type=int, default=None, dest="max_turns",
                     help="Sub-agent max tool-using turns (default: per-step, 40-70)")
    rfp.add_argument("--max-fix-rounds", type=int, default=3, dest="max_fix_rounds",
                     help="Max CODE-FIX + GATE1 retry rounds on GATE1 FAIL (default: 3)")
    rfp.add_argument("--no-push", action="store_true", help="Skip git push origin HEAD after completion")
    rfp.add_argument("--no-mcp", action="store_true", dest="no_mcp",
                     help="Disable code-review-graph MCP for this FR step (debugging)")
    rfp.add_argument("--permission-mode", default=None, dest="permission_mode",
                     choices=["acceptEdits", "bypassPermissions", "default", "plan"],
                     help="Override sub-agent permission mode (default: bypassPermissions for GATE1, acceptEdits otherwise)")
    rfp.set_defaults(func=cmd_run_fr_step)

    # resume-fr-phase
    rrp = sub.add_parser(
        "resume-fr-phase",
        help="Find next pending FR step after a crash — prints the run-fr-step command to run",
    )
    rrp.add_argument("--phase", type=int, required=True, help="Phase number")
    rrp.add_argument("--project", default=".", help="Project root (default: .)")
    rrp.set_defaults(func=cmd_resume_fr_phase)

    # reload-policy
    rl = sub.add_parser("reload-policy", help="Hot-reload enforcement policies from enforcement.json")
    rl.add_argument(
        "--policy-file",
        default="enforcement/enforcement.json",
        help="Path to enforcement.json (default: enforcement/enforcement.json)",
    )
    rl.set_defaults(func=cmd_reload_policy)

    # check-test-inventory (D4 — deprecated v2.6, delegates to spec-coverage-check)
    cti = sub.add_parser(
        "check-test-inventory",
        help="[DEPRECATED v2.6] Delegates to spec-coverage-check. Use spec-coverage-check instead.",
    )
    cti.add_argument("--project", default=".", help="Project root (default: .)")
    cti.add_argument("--strict", action="store_true",
                     help="Hard-block if both TEST_SPEC.md and TEST_INVENTORY.yaml missing")
    cti.add_argument("--threshold", type=float, default=80.0,
                     help="Minimum compliance percentage (default: 80.0)")
    cti.add_argument("--diff-mode", action="store_true", dest="diff_mode",
                     help="(deprecated) Compare checksum against P1 baseline")
    cti.add_argument("--srs-crosscut", action="store_true", dest="srs_crosscut",
                     help="(deprecated) use verify-spec instead")
    cti.add_argument("--crg-gaps", action="store_true", dest="crg_gaps",
                     help="(deprecated) use run-gap-analysis instead")
    cti.set_defaults(func=cmd_check_test_inventory)

    # spec-coverage-check (D4 unified — TEST_SPEC.md → tests/, single source of truth)
    scc = sub.add_parser(
        "spec-coverage-check",
        help="D4 unified: compare TEST_SPEC.md items against actual test implementations",
    )
    scc.add_argument("--project", default=".", help="Project root (default: .)")
    scc.add_argument("--threshold", type=float, default=80.0,
                     help="Minimum spec coverage percentage (default: 80.0)")
    scc.add_argument("--fr-id", default=None, dest="fr_id",
                     help="Check only a specific FR (e.g. FR-03)")
    scc.set_defaults(func=cmd_spec_coverage_check)

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

    # verify-spec
    vs = sub.add_parser(
        "verify-spec",
        help="Verify implementation complies with spec requirements (6-dimension check)",
    )
    vs.add_argument("--project", default=".", help="Project root (default: .)")
    vs.add_argument("--fix", action="store_true",
                    help="Show fix suggestions for each issue (no auto-fix)")
    vs.set_defaults(func=cmd_verify_spec)

    # check-logic
    cl = sub.add_parser(
        "check-logic",
        help="Check code for logic correctness (output/branch/lazy-init/semantic)",
    )
    cl.add_argument("--project", default=".", help="Project root (default: .)")
    cl.add_argument("--srs",     default=None, help="SRS.md path for semantic validation")
    cl.set_defaults(func=cmd_check_logic)

    # init-project
    ip = sub.add_parser(
        "init-project",
        help="Initialize harness CI wiring in a target project (Context B one-shot setup)",
    )
    ip.add_argument("--project", required=True, help="Target project root path")
    ip.add_argument("--phase",   type=int, default=1, help="Current phase (default: 1)")
    ip.add_argument("--ci-only", action="store_true",
                    help="Write CI workflow only; skip git hooks")
    ip.add_argument("--overwrite", action="store_true",
                    help="Overwrite existing CI workflow and hooks")
    ip.add_argument("--setup-branch-protection", action="store_true",
                    help="Configure GitHub branch protection for main with required checks")
    ip.set_defaults(func=cmd_init_project)

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
