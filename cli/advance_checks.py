"""The checks and repairs `advance-phase` runs against the delivered tree.

Round 80 站7. Moved out of cli/phase_cmds.py verbatim; the bodies here are
byte-identical to the ones that were there, which
tests/test_god_file_split_safety.py asserts by AST source segment.

Nine functions with one thing in common: each is called from
`_advance_prechecks` or `cmd_advance_phase` and each reads (or, for the two
regenerators, rewrites) the project tree rather than the framework's own state
machine. They referenced nothing else defined in phase_cmds, which is why they
could move as a block without a re-import cycle.

Both regenerators are here despite writing rather than checking: they run in
the same pass and for the same reason — a view the framework owns is repaired
first so that only files it may NOT rewrite can reach a BLOCK (Round 34 站2).
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

from core.quality_gate import gate1_evidence
from core.state_io import load_quality_manifest
from core.utils.project_layout import ProjectLayout

_SCOPE_SCRIPT_EXTS: frozenset[str] = frozenset({".py", ".js", ".ts", ".sh"})

_SCOPE_DEBUG_NAME_TOKENS: frozenset[str] = frozenset({
    "diag", "debug", "scratch", "explore", "probe", "tmp",
    "sandbox", "throwaway", "adhoc", "wip", "poc",
})


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
    manifest = load_quality_manifest(project, lenient=True)
    fr_ids_manifest: list[str] = manifest.get("fr_ids", [])
    if not fr_ids_manifest:
        return 0  # Non-FR project or unreadable manifest — skip

    from core.quality_gate import min_coverage_floor
    _min_cov = min_coverage_floor(manifest)

    # DELTA-phase auto-skip: P4/P5/P7/P8 re-run Gate 1 as a delta check. When
    # NO FR's code has changed since its last Gate 1 PASS, the per-FR DELTA
    # loop is a no-op (every run-fr-step would `already done → skip`). In
    # that case trust the prior finalize-gate record — re-running pytest
    # 8 times per advance would be wasted work. Code changes (test additions
    # included) force a fresh live check.
    if completed_phase in (4, 5, 7, 8):
        try:
            _all_unchanged = all(
                not gate1_evidence.fr_code_changed_since_last_gate1(fr, project)
                for fr in fr_ids_manifest
            )
        except Exception as exc:  # pylint: disable=broad-exception-caught
            print(f"[WARN] advance-phase Gate 1 coverage: DELTA-unchanged check failed, "
                  f"forcing a full live coverage run: {exc}", file=sys.stderr)
            _all_unchanged = False
        if _all_unchanged:
            print(
                f"  [Gate 1 coverage] Phase {completed_phase}: all {len(fr_ids_manifest)}"
                f" FR(s) unchanged since last gate — DELTA auto-satisfied (live pytest skipped)."
            )
            return 0

    # Live verification: one whole-project pytest --cov run proves the
    # manifest's recorded per-FR coverage is achievable against current code.
    cov = gate1_evidence.validate_fr_coverage_immediate(project)
    if cov is None:
        print(
            f"\n[BLOCKED] Phase {completed_phase} Gate 1 live coverage check failed:\n"
            f"  pytest --cov could not be run (pytest missing, no tests/, or timeout).\n"
            f"  Re-run: python3 harness_cli.py finalize-gate --gate 1"
            f" --phase {completed_phase} --fr-id <FR-ID> --project {project}"
        )
        return 14

    # Round 57 站1: every phase judges each FR on the modules it owns, because
    # this is Gate 1's check and Gate 1 declares `scope: single_fr` at every
    # phase it runs. Round 56 站6 built that for P3 only, so the same gate was
    # judged by two rules depending on which phase re-ran it — and S4, which
    # had no phase condition at all, used the per-FR number everywhere.
    #
    # From P4 the whole-project floor is kept as a SECOND, separately-reported
    # condition rather than replaced: P5/P7/P8 have no full-phase gate at their
    # exit, so this is the only whole-project coverage judgement those
    # transitions get. P3 is excluded from it deliberately — at P3 the
    # whole-project number necessarily carries modules later FRs have not
    # written yet, which is the measurement Round 56 站6 rests on.
    return _gate1_per_fr_coverage_verdict(
        project, fr_ids_manifest, _min_cov,
        whole_project=cov, phase=completed_phase,
        whole_project_floor_applies=completed_phase >= 4,
    )


def _gate1_per_fr_coverage_verdict(
    project: Path, fr_ids: "list[str]", min_cov: float, *, whole_project: float,
    phase: int, whole_project_floor_applies: bool,
) -> int:
    """Gate 1's verdict: every FR judged on the modules it owns.

    Round 56 站6. P3 is the per-FR TDD window and Gate 1 is a per-FR gate, but
    this check asked one whole-project question and printed "whole-project
    coverage {cov}%". Measured on taskq-cc: FR-01 covered 97.06% of its own
    modules while the whole-project number read 8.5%, because the SAB declares
    ten modules other FRs will activate later. The run spent three rounds
    dispatching CODE-FIX / COVERAGE-FIX against a number that was never about
    FR-01, then halted. The earlier fix threaded `fr_id` into run-fr-step's
    inline fallback and left this — the check that actually returns 14 —
    reading the wide number.

    Round 57 站1 removed the `phase == 3` condition in front of this function.
    Gate 1 declares `scope: single_fr` and re-runs as a DELTA check at
    P4/P5/P7/P8/P9; judging the same gate by the whole-project number at those
    phases let a tree advance with a whole-project 90% hiding an FR covering
    40% of what it owns.

    `whole_project_floor_applies` is the second, separately-reported
    condition, and it is a phase question rather than a gate one — which is
    why it arrives as an argument instead of being re-derived here. From P4 the
    tree is assembled and P5/P7/P8 have no full-phase gate at their exit, so
    this is their only whole-project coverage judgement. At P3 it does not
    apply: the whole-project number there necessarily carries modules later FRs
    have not written yet.

    The suite has already run (the caller's `validate_fr_coverage_immediate`),
    so each FR is arithmetic over the `.coverage` on disk: no second pytest,
    Round 25 站1's one-execution invariant intact.

    An FR whose per-FR scope cannot be computed (no SAB, no declared modules,
    none of its modules measured) is judged on the whole-project number and
    SAID SO. That is not abstention passing (Round 46): the whole-project
    figure is a real measurement and, carrying every other FR's uncovered
    modules, a strictly harsher one. Falling back to a looser number would be
    the abstention.
    """
    failures: list[str] = []
    lines: list[str] = []
    for fr_id in fr_ids:
        measured = gate1_evidence.fr_coverage_from_last_run(project, fr_id)
        if measured is None:
            scope = "whole-project (no per-FR module scope in SAB.json)"
            measured = whole_project
        else:
            scope = "own modules"
        mark = "✓" if measured >= min_cov else "✗"
        lines.append(f"    {mark} {fr_id}: {measured:.1f}% [{scope}]")
        if measured < min_cov:
            failures.append(f"{fr_id} {measured:.1f}% [{scope}]")

    _floor_failed = whole_project_floor_applies and whole_project < min_cov

    if failures or _floor_failed:
        # Two conditions with two different remedies, reported apart. A run
        # blocked only by the floor must not read as a per-FR defect: the
        # operator would go looking for an FR to fix and find every one of
        # them green.
        _head = (
            f"\n[BLOCKED] Phase {phase} Gate 1 live coverage check failed "
            f"(min {min_cov:.1f}%):\n"
        )
        _body = ""
        if failures:
            _body += (
                f"  per-FR: {len(failures)} of {len(fr_ids)} FR(s) below the "
                f"floor on the modules they own:\n"
                + "".join(f"    ✗ {f}\n" for f in failures)
                + "    Add tests for the named FRs, or use '# pragma: no cover' "
                "for unreachable paths.\n"
            )
        else:
            _body += (
                f"  per-FR: all {len(fr_ids)} FR(s) pass on the modules they "
                f"own — the defect below is not any one FR's:\n"
                + "".join(f"  {line}\n" for line in lines)
            )
        if _floor_failed:
            _body += (
                f"  whole-project: {whole_project:.1f}% < {min_cov:.1f}%. Code "
                f"no FR claims in fr_module_traceability is still delivered "
                f"code; Phase {phase} has no full-phase gate to catch it.\n"
            )
        elif failures:
            _body += (
                f"  whole-project coverage is {whole_project:.1f}%"
                + ("" if whole_project_floor_applies else
                   f", which at Phase {phase} includes modules later FRs will "
                   "activate")
                + " — the per-FR numbers above are what Gate 1 judges.\n"
            )
        print(_head + _body + "  Then re-run.")
        return 14
    print(
        f"  [Gate 1 coverage] Phase {phase}: every FR ≥ {min_cov:.1f}% on its own "
        f"modules ({len(fr_ids)} FR(s); whole-project {whole_project:.1f}%"
        + (f" ≥ {min_cov:.1f}%)" if whole_project_floor_applies else ", not a floor here)")
    )
    for line in lines:
        print(line)
    return 0

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
            except Exception as exc:
                print(f"[WARN] SG-1 fabrication check: {_sf} unparseable, "
                      f"excluded from stddev sample: {exc}", file=sys.stderr)

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

def _regen_traceability_views(project: Path) -> None:
    """Always-regenerate the human-readable traceability views from the live
    build_traceability scan, so a phase advance can never leave a stale or
    hand-mocked matrix behind. The authoritative FR status is that scan (code /
    test coverage) and quality_manifest.json — these Markdown files are
    render-only views (AUTO-GEN sentinel block); their content is never a gate
    input. Regenerated at phase granularity (advance-phase), matching their role
    as phase-level ASPICE tracking views.
    """
    try:
        from scripts.build_traceability import (
            build_traceability,
            generate_markdown_matrix,
        )
    except Exception as e:  # noqa: BLE001
        print(f"  [advance-phase] traceability views skipped (import): {e}")
        return
    try:
        rt = build_traceability(project)
    except Exception as e:  # noqa: BLE001
        print(f"  [advance-phase] traceability views skipped (scan failed): {e}")
        return
    layout = ProjectLayout(project)
    _regen_and_stage_view(
        project, layout.traceability_matrix_path,
        lambda p: generate_markdown_matrix(rt, p),
    )
    try:
        from core.traceability.spec_tracking_render import write_spec_tracking
        _regen_and_stage_view(
            project, layout.spec_tracking_path,
            lambda p: write_spec_tracking(project, rt, out_path=p),
        )
    except Exception as e:  # noqa: BLE001
        print(f"  [advance-phase] SPEC_TRACKING view skipped: {e}")

def _regen_and_stage_view(project: Path, path: Path, render) -> None:
    """Render a human-readable view file from SSOT and `git add` it only if its
    bytes actually changed (same no-op-commit guard as the STAGE_PASS regen).

    Best-effort: a render error is warned, never fatal — these are render-only
    views, not the authoritative source (that is build_traceability /
    quality_manifest.json).
    """
    old_hash = None
    if path.exists():
        try:
            old_hash = hash(path.read_bytes())
        except OSError:
            pass
    try:
        render(path)
    except Exception as e:  # noqa: BLE001
        print(f"  [advance-phase] {path.name} view regen skipped: {e}")
        return
    if not path.exists():
        return
    try:
        new_hash = hash(path.read_bytes())
    except OSError:
        new_hash = None
    _warn_if_view_lost_its_anchor(project, path)
    if new_hash != old_hash:
        subprocess.run(["git", "add", str(path)], cwd=str(project), capture_output=True)
        print(f"  [advance-phase] {path.name} refreshed from SSOT → staged")

def _broken_deliverable_anchors(project: Path) -> list[str]:
    """Every anchored deliverable present on disk whose first line no longer
    starts with the anchor its own path declares.

    Round 34 站2. Round 33 站1 gave the H1 rule a single SOURCE
    (`DELIVERABLE_ANCHORS`); it did not give it a single MOMENT. The anchor was
    verified only where the Phase 1/2 orchestrator reloads the file, so a
    deliverable that satisfied it at P1 and was rewritten at P4 satisfied
    nothing thereafter and nobody asked. Measured on run-all-by-workflow's
    01-requirements/TRACEABILITY_MATRIX.md: correct at dfd7abd (P1 review
    complete), blank first line from fa21439 (the P3->P4 advance) onward, and
    green through Gate 4 and P8 with last_gate 4. Four of five real projects
    are in that state today.

    Scans the whole registry, not this phase's deliverables: the defect is
    precisely that a LATER phase rewrites an EARLIER phase's artefact, so a
    phase-scoped check would miss every instance of it.

    Denominator protection (R30 站6 / R31 站4): zero anchored files found is
    recorded in the degradation ledger rather than returned as a clean result.
    A non-standard or ingestion-mode layout is legitimate, so it must not
    block — but "we could not look" must not read as "we looked and it was
    fine".
    """
    from core.quality_gate.legal_artifacts import DELIVERABLE_ANCHORS

    findings: list[str] = []
    checked = 0
    for rel, anchor in DELIVERABLE_ANCHORS.items():
        path = project / rel
        if not path.is_file():
            continue
        checked += 1
        try:
            head = path.read_text(encoding="utf-8", errors="replace").split("\n", 1)[0]
        except OSError as exc:
            findings.append(f"{rel}: unreadable ({exc})")
            continue
        if not head.startswith(anchor):
            findings.append(f"{rel}: first line {head[:60]!r} does not start with {anchor!r}")
    if checked == 0:
        from core.degradation_ledger import record_degradation

        record_degradation(
            project, "advance-phase:deliverable-anchors",
            "no anchored deliverable found at any registered path — the anchor "
            "invariant measured nothing",
            why=(
                "zero failures out of zero files is not a pass; a project whose "
                "layout puts deliverables elsewhere is legitimate but must not "
                "be reported as verified"
            ), owner="project"
        )
    return findings


def _warn_if_view_lost_its_anchor(project: Path, path: Path) -> None:
    """A regenerated view must still satisfy the loader anchor its own path
    declares (core.quality_gate.legal_artifacts.DELIVERABLE_ANCHORS).

    Round 33 站2. TRACEABILITY_MATRIX.md is both a peer-reviewed Phase 1
    deliverable and, from P3 onward, a render-only view — and the render did
    not inherit the deliverable's contract. Measured with the framework's own
    loader: PREFIX_MISMATCH on 4 of 4 real projects, because the H1 sat below
    the AUTO-GEN sentinel where a first-line anchor can never reach it.

    WARN, not BLOCK. The anchor is only read by the Phase 1 orchestrator, so a
    stale first line is latent on the forward path; blocking here would stop
    every existing project on a defect the framework itself introduced. The
    degradation ledger is the durable half — `run-report` reads it, so a
    recurrence is counted rather than scrolled past.
    """
    from core.quality_gate.legal_artifacts import anchor_for

    try:
        anchor = anchor_for(path.name)
    except KeyError:
        return  # not an anchored deliverable — nothing to check
    try:
        first = path.read_text(encoding="utf-8", errors="replace").splitlines()[:1]
    except OSError as exc:
        print(f"  [advance-phase] {path.name} anchor check skipped: {exc}")
        return
    if first and first[0].startswith(anchor):
        return
    from core.degradation_ledger import record_degradation

    record_degradation(
        project, "advance-phase:regen-view",
        f"{path.name} first line does not start with {anchor!r}",
        why=(
            "the Phase 1 orchestrator reloads this path with that anchor; a "
            "regenerated view that fails it will be rejected on any re-entry "
            "into Phase 1"
        ), owner="harness"
    )

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

def _scope_debug_name_match(stem: str) -> bool:
    tokens = re.split(r"[_\-\s]+", stem.lower())
    return any(t in _SCOPE_DEBUG_NAME_TOKENS for t in tokens)
