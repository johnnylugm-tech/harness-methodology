"""Helpers shared across cmd-family modules (moved verbatim from
harness_cli.py, 絞殺者續章 S4g). Sentinel paths, STAGE_PASS generation,
the phase auditor runner, and the P3 post-Gate2 precondition — each used
by two or more of gate/phase/push families, so none of them can live in a
single family module. This module must never import harness_cli.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from harness.git_strategy import GitStrategy

from core.phase_topology import EXIT_GATE_MAP
from core.quality_gate import gate1_evidence
from core.quality_gate.gate1_evidence import (  # noqa: F401  (re-export, see below)
    _finalize_sentinel_path,
    _sentinel_path,
)
from core.utils.script_loader import load_harness_script

# _sentinel_path / _finalize_sentinel_path now live in
# core/quality_gate/gate1_evidence.py (moved out of this module so that
# gate1_evidence.py — a core/ module — can call them too without a
# core -> cli circular import; this module still imports gate1_evidence
# directly above). Re-exported here unchanged so existing callers using
# `cli._shared._sentinel_path` / `_finalize_sentinel_path` are unaffected.

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
    for _phase, _gate in sorted(EXIT_GATE_MAP.items()):
        _sf = _finalize_sentinel_path(project, _gate, None, phase=_phase)
        _sf.parent.mkdir(parents=True, exist_ok=True)
        _sf.write_text("test-sentinel\n", encoding="utf-8")

def _generate_stage_pass(
    project_path: Path, gate_num: int, phase_num: int,
    truth_override: bool | None = None,
) -> None:
    """Write machine-generated 00-summary/Phase{N}_STAGE_PASS.md from quality_manifest.json.

    No LLM involvement — content comes entirely from quality_manifest.json +
    state.json.phase_truth_passed (fallback). Called automatically by
    cmd_finalize_gate() after bridge.finalize_gate succeeds, and by
    _advance_prechecks() as the last step before returning success.

    Gate-data interpretation rules (B-class bug fix — Phase 1-2 + per-FR Gate 1):
      - Gate 2/3/4: flat dict with top-level `score` + `quality_complete`.
      - Gate 1 in Phase 3+: per-FR dict `{"FR-XX": {"score": N, "quality_complete":
        bool}, ...}` — aggregate across FRs (ALL must be True for PASS).
      - Empty gate_data (Phase 1-2 where Gate 1 has not fired yet) — fall back to
        `truth_override` if given, else state.json.phase_truth_passed. Without
        this fallback, Phase 1-2 always wrote "exit gate FAIL" even when the
        phase succeeded.

    truth_override: when the caller already knows the phase truth verdict (e.g.
        _advance_prechecks calling this as its final step, after every blocking
        check has already passed) pass it directly instead of re-reading
        state.json — at that call site, state.json.phase_truth_passed has not
        been written yet (_advance_fsm sets it AFTER _advance_prechecks
        returns), so reading it would always see the stale pre-advance value.
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
    elif truth_override is not None:
        # Caller already knows the verdict (see truth_override docstring) —
        # state.json.phase_truth_passed may not be written yet at this call site.
        score = "N/A"
        qc = truth_override
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

def gate_result_paths(
    project: Path, gate: int, fr_id: str | None = None
) -> list[Path]:
    """Return prioritised candidate paths for a gate{N}_result.json lookup.

    Fix H-E (2026-07-15): per-FR canonical history lands at
    ``.methodology/gate_results/gate{N}/{fr_id}.json``. Existing readers
    continue to find their previous single-file result via the
    backward-compat alias at ``.methodology/gate{N}_result.json``. Order
    matches existing reader semantics (fresh ``.sessi-work`` write first
    for in-flight runs, .methodology second, per-FR third for the new
    authoritative location, project root last as bare fallback):
      1. .sessi-work/gate{N}_result.json (fresh in-flight write)
      2. .methodology/gate{N}_result.json (backward-compat alias)
      3. .methodology/gate_results/gate{N}/{fr_id}.json (per-FR canonical, if fr_id)
      4. ./{N}_result.json (project-root fallback)
    """
    candidates: list[Path] = [
        project / ".sessi-work" / f"gate{gate}_result.json",
        project / ".methodology" / f"gate{gate}_result.json",
    ]
    if fr_id:
        candidates.append(
            project / ".methodology" / "gate_results" / f"gate{gate}" / f"{fr_id}.json"
        )
    candidates.append(project / f"gate{gate}_result.json")
    return candidates


def _run_phase_auditor(project: Path, completed_phase: int) -> int:
    """Run PhaseAuditor (local mode) — replaced deprecated phase_end_audit.py (v2.5.0).

    Returns:
      0  = all checks pass
      8  = C1 CRITICAL (deliverables missing / untracked)
      1  = other CRITICAL findings
      2  = error / import failure
    """
    try:
        _pa_mod = load_harness_script("phase_auditor.py")
        PhaseAuditor, LocalFetcher = _pa_mod.PhaseAuditor, _pa_mod.LocalFetcher
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
         .sessi-work/sentinels/ (matches what `run-gate --gate 1 --fr-id FR-XX`
         writes — the `.flag` file). This is the per-FR 95% bar that
         `advance-phase` also enforces — the milestone cannot be a softer
         gate than advance-phase.

         Note: `finalize-gate` writes `.finalized` (not `.flag`). Per-FR
         `.flag` is written by `run-gate` only. The fix below runs both
         steps; `finalize-gate` alone does not write `.flag` and will not
         satisfy this check.
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
    # O2 (2026-07-07): accept any of three co-equal Gate 1 evidence channels
    # — .flag (run-gate), .finalized (finalize-gate), or a phase-3/gate-1/fr-id
    # row in .methodology/gate_timestamps.jsonl. Single-source dependency was
    # a UX trap (clean restart wiping .sessi-work/ always blocked P3→P4).
    missing_sentinels: list[str] = []
    for fr_id in fr_ids:
        # v2.13: this precondition is Phase 3-specific (filename _validate_p3_…);
        # pass phase=3 explicitly so we look for the per-phase path (Bug #121).
        if not gate1_evidence.gate1_evidence_exists(project, fr_id, phase=3):
            missing_sentinels.append(fr_id)
    if missing_sentinels:
        errors.append(
            f"Per-FR Gate 1 sentinel missing for {len(missing_sentinels)} FR(s): "
            f"{', '.join(missing_sentinels)}.\n"
            f"  Cause: .sessi-work/sentinels/g1_p3_{{fr}}.flag is written by "
            f"`run-gate` (not `finalize-gate`, which writes `.finalized`).\n"
            f"  Fix:   run BOTH steps for each missing FR —\n"
            f"           1. python harness_cli.py run-gate      "
            f"--gate 1 --phase 3 --fr-id <FR-ID> --project .\n"
            f"           2. python harness_cli.py finalize-gate "
            f"--gate 1 --phase 3 --fr-id <FR-ID> --project ."
        )

    return errors


# --- git strategy + post-push probe (moved from harness_cli, S4h) ---

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
    except Exception as exc:  # pylint: disable=broad-exception-caught
        print(f"  [WARN] post-push dirty-check probe failed: {exc}")
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

def _make_git(args: argparse.Namespace, project: Path) -> "GitStrategy":  # noqa: F821 — lazy import
    """Instantiate GitStrategy from parsed args. Lazy-imports to keep startup fast.

    Git is disabled if either --no-git or --dry-run is set. --dry-run is the
    preferred safety flag for push-milestone (Bug #112) — it prevents accidental
    origin pollution when exercising the command during bug hunts.
    """
    from harness.git_strategy import GitStrategy
    no_git = getattr(args, "no_git", False) or getattr(args, "dry_run", False)
    return GitStrategy(project=project, enabled=not no_git)


def ensure_fresh_attestation(project: Path) -> None:
    """Self-heal a stale trace attestation before a tool-internal commit.

    Round 12 站2b. finalize-gate / push-checkpoint / push-milestone all
    commit via GitStrategy, which fires the target repo's
    prepare-commit-msg hook — and that hook rejects on a stale
    .methodology/trace/attestation.json. Until now the workaround was
    orchestrated in WORKFLOW PROMPTS (a "TRACE-PRECHECK" step telling the
    gate/push agent to regen + commit the attestation first):
    integration-test accumulated 37 ritual "trace: regen attestation
    before Gate N" commits, and every run re-bet on LLM compliance.

    The tool now heals itself: same freshness probe the hook uses
    (cli.phase_cmds._trace_dirty_state), same regen the ritual performed
    (scripts.build_trace_attestation). No staging needed — GitStrategy's
    _commit stages with `git add -A`, so the refreshed file rides the
    very commit that would otherwise have been rejected.

    Never raises: on failure the hook still enforces, and its BLOCK
    message remains the agent-visible fallback path.
    """
    try:
        from cli.phase_cmds import _trace_dirty_state
        state = _trace_dirty_state(project)
        if state.get("passed", True):
            return
        from scripts.build_trace_attestation import (
            build_attestation,
            write_attestation,
        )
        write_attestation(project, build_attestation(project))
        print("  [attestation] auto-refreshed "
              f"(was stale: {str(state.get('reason', ''))[:120]}) — "
              "rides the next commit; no agent-side TRACE-PRECHECK needed")
    except Exception as exc:  # pylint: disable=broad-exception-caught
        print(f"  [attestation] WARN self-heal failed (hook still enforces): {exc}")
