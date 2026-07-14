"""Plan <-> workflow JS alignment audit (Round 11 station0).

Two independently-enforced registries close the loop the three review
clauses require ((1) 100% alignment, (2) methodology stays in harness, (3)
any kept gap must be explicitly justified):

  KNOWN_GAPS = {phase: {marker_name: reason}}
    A plan MARKER genuinely invokes a harness_cli.py subcommand (see
    scripts/workflow_audit/extract.py's precision rules — status/effort/
    dispatch/doctor/manifest are real subcommand names AND common English
    words, so a bare substring scan is not enough) that the corresponding
    workflow JS never invokes anywhere. Every entry MUST carry a non-empty
    reason. Clause (1) is enforced by this registry SHRINKING over
    stations 2-4 as each file is migrated and the gap closed — or, rarely,
    by a reason proving the "gap" is a legitimate runtime substitution
    (see phase6 B-DISPATCH below: `agent()` replaces `harness_cli.py
    dispatch` because dispatch's job — spawn a sub-session from OUTSIDE a
    running agent — is exactly what `agent()` already does INSIDE a
    workflow; calling the CLI dispatch a second time would be redundant,
    not a gap).

  RUNTIME_ONLY = {phase: {js_phase_title: reason}}
    A workflow JS `phase()` box whose purpose has no counterpart anywhere
    in the plan — invented for a runtime/safety reason the plan (written
    for a human or a single orchestrating agent, not a resumable script)
    doesn't need.

Scope note (honesty over false completeness): a JS phase() title that
doesn't literally repeat plan wording is NOT automatically a runtime-only
invention — most JS phase() boxes group several plan steps under a
shorter progress-view label (e.g. JS's "Advance" phase covers the plan's
PHASE-TRUTH + TDD-PRECHECK + advance-phase steps; the box title doesn't
quote plan text, but its CONTENT is 100% plan-derived — confirmed by
reading the JS body, not by title matching). RUNTIME_ONLY here holds only
the 3 concepts verified during station0 by reading the actual JS body and
its own code comments to have NO plan-side instruction at all
(Manifest Integrity, Artifacts Commit, Sync). It is NOT yet an exhaustive
classification of every phase() title in every file — further entries are
added as each file is migrated in stations 2-4, where that migration reads
the specific file/plan pair in full depth. Forcing an exhaustive
classification here, sight-unseen, would produce confident-but-wrong
answers for titles like "Milestones"/"Release Docs"/"Tag & Advance" that
DO correspond to real plan content under different wording — verified
individually and deliberately excluded from this registry.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from scripts.workflow_audit.extract import (
    extract_js_phases,
    extract_js_subcommands,
    extract_plan_markers,
    generate_dynamic_plan,
    known_subcommands,
    make_fixture_project,
)

WORKFLOWS_DIR = Path(__file__).resolve().parent.parent / ".claude" / "workflows"

JS_FILES: dict[int, str] = {
    1: "phase1-requirements.js",
    2: "phase2-architecture.js",
    3: "phase3-implementation.js",
    4: "phase4-testing.js",
    5: "phase5-verification.js",
    6: "phase6-quality.js",
    7: "phase7-risk.js",
    8: "phase8-config.js",
}

_ENV_CHECK_REASON = (
    "JS calls run-env-check and reads its own process exit code directly "
    "(comment: 'root-cause fix: CLI exit code reflects ready flag', "
    "Bug #127 / commit 17d6d53) but never calls finalize-env-check, which "
    "the plan's ENV-CHECK marker explicitly instructs as a second step and "
    "which performs anti-fabrication sentinel + staleness validation "
    "(cli/gate_cmds.py::cmd_finalize_env_check). Needs investigation when "
    "this file is migrated: confirm whether Bug #127's fix made "
    "finalize-env-check's validation redundant (then the plan's own "
    "wording is stale and should be flagged to the user, not silently "
    "changed), or whether skipping it is a live gap that must be closed."
)

_ORCH_POST_REASON = (
    "plan's ORCH-POST step runs `spec-coverage-check --project . "
    "--threshold 40.0 --fr-id {FR-ID}` then `amend-sab --project .` "
    "immediately after each FR's GATE1-DELTA PASS; the JS's per-FR delta "
    "phase does neither. Needs adding when this file is migrated."
)

KNOWN_GAPS: dict[int, dict[str, str]] = {
    3: {"ENV-CHECK": _ENV_CHECK_REASON},
    4: {"ENV-CHECK": _ENV_CHECK_REASON},
    5: {
        "ENV-CHECK": _ENV_CHECK_REASON,
        "ORCH-POST": _ORCH_POST_REASON,
    },
    6: {
        "B-DISPATCH": (
            "plan's B-DISPATCH marker instructs `harness_cli.py dispatch "
            "--role reviewer --fr-id QUALITY_REPORT.md ...` — the "
            "SKILL.md-driven manual A/B dispatch mechanism for a human "
            "orchestrator running the plan step by step. JS's Peer Review "
            "phase instead calls the workflow's own agent() primitive "
            "directly with an equivalent reviewer prompt. This is the "
            "correct substitution inside a workflow script: harness_cli.py "
            "dispatch exists to spawn a Claude Code sub-session from "
            "OUTSIDE a running agent context, which is exactly what "
            "agent() already does from INSIDE a workflow — invoking "
            "`dispatch` a second time here would nest an unnecessary CLI "
            "subprocess, not close a real gap. Documented so the "
            "100%-alignment check doesn't silently miss this marker; no "
            "fix needed."
        ),
    },
    7: {
        "ENV-CHECK": _ENV_CHECK_REASON,
        "ORCH-POST": _ORCH_POST_REASON,
        "TDD-PRECHECK": (
            "the plan's TDD-PRECHECK marker lists `spec-coverage-check "
            "--threshold 90.0` as one of advance-phase's OWN enforced "
            "pre-checks (exit code 10, D4 unified v2.6) — advance-phase "
            "enforces this regardless of workflow JS, so this is NOT a "
            "missing-enforcement gap. phase5's JS additionally runs this "
            "check PROACTIVELY as its own 'D4-GAP' early-warning step "
            "(catching the 80%-vs-90% threshold gap before wasting an "
            "advance-phase retry round); phase7's Advance phase does not. "
            "Cosmetic/UX gap, not a correctness gap — optional to close "
            "when this file is migrated."
        ),
    },
    8: {
        "ENV-CHECK": _ENV_CHECK_REASON,
        "ORCH-POST": _ORCH_POST_REASON,
        "TDD-PRECHECK": (
            "same shape as phase7's TDD-PRECHECK entry — advance-phase "
            "enforces spec-coverage-check --threshold 90.0 internally "
            "regardless; phase8's Advance phase lacks phase5's proactive "
            "early-warning step. Cosmetic/UX gap, optional to close."
        ),
    },
}

_MANIFEST_INTEGRITY_REASON = (
    "checkManifestIntegrity() / check-manifest-integrity has zero "
    "counterpart anywhere in the plan text. Invented in response to a "
    "2026-07-02 incident: a sub-agent action (e.g. a bare `pytest` "
    "leaking the harness's own test CWD) can corrupt "
    "quality_manifest.json mid-run, not just before phase entry — this "
    "phase() box re-checks the three known corruption patterns "
    "(fr_ids truncated, traceability cleared, gate1 wiped) both at entry "
    "AND immediately before the phase-exit push, so corruption is never "
    "baked into a milestone commit (commit 3198402 shipped exactly that "
    "failure once). A human running the plan by hand has no equivalent "
    "step because a human doesn't dispatch sub-agents that can race the "
    "manifest file the way a workflow's agent() calls can."
)

_ARTIFACTS_COMMIT_REASON = (
    "phase() box with zero plan counterpart. The plan's own milestone "
    "push (push-milestone/commit_and_push_gate) sweeps the working tree "
    "wholesale (`git add -A`-equivalent) at the very end of the phase — "
    "fine for a human who only reaches that point after everything else "
    "already succeeded. A workflow script can also exit EARLY (a "
    "verify-handoff or gate FAIL returns before the milestone push is "
    "ever reached), which would leave that phase's already-deterministic "
    "artifacts (e.g. 05-verification/BASELINE.md) sitting uncommitted on "
    "a dirty tree with no milestone commit to rescue them. This phase() "
    "commits those specific paths early, via an explicit allowlist "
    "(never `git add -A` mid-workflow, mirrors phase4's original d4f4724 "
    "fix) — the same problem-shape test_workflow_artifacts_commit_pattern.py "
    "already guards structurally."
)

_SYNC_REASON = (
    "phase() box with zero plan counterpart. advance-phase deliberately "
    "commits the phase handover LOCALLY without pushing (cli/phase_cmds.py "
    "docstring: 'next milestone push publishes to origin') — a human "
    "following the plan by hand simply continues to the next phase's plan "
    "file, whose own milestone push eventually publishes everything "
    "together. A workflow SCRIPT ends immediately after Advance with no "
    "next-phase push queued in the same run, so the handover commit would "
    "be stranded on local until whatever runs next happens to push it "
    "(Bug A, 2026-07-07). This phase() publishes it immediately via "
    "`git push origin main` so no run ever ends with unpushed history."
)

RUNTIME_ONLY: dict[int, dict[str, str]] = {
    1: {"Sync": _SYNC_REASON},
    2: {"Sync": _SYNC_REASON},
    3: {"Manifest Integrity": _MANIFEST_INTEGRITY_REASON, "Sync": _SYNC_REASON},
    4: {
        "Manifest Integrity": _MANIFEST_INTEGRITY_REASON,
        "Artifacts Commit": _ARTIFACTS_COMMIT_REASON,
        "Sync": _SYNC_REASON,
    },
    5: {
        "Manifest Integrity": _MANIFEST_INTEGRITY_REASON,
        "Artifacts Commit": _ARTIFACTS_COMMIT_REASON,
        "Sync": _SYNC_REASON,
    },
    6: {"Manifest Integrity": _MANIFEST_INTEGRITY_REASON, "Sync": _SYNC_REASON},
    7: {
        "Manifest Integrity": _MANIFEST_INTEGRITY_REASON,
        "Artifacts Commit": _ARTIFACTS_COMMIT_REASON,
        "Sync": _SYNC_REASON,
    },
    8: {
        "Manifest Integrity": _MANIFEST_INTEGRITY_REASON,
        "Artifacts Commit": _ARTIFACTS_COMMIT_REASON,
        "Sync": _SYNC_REASON,
    },
}


@pytest.fixture(scope="module")
def fixture_project(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("workflow_alignment_audit")
    return make_fixture_project(root)


@pytest.fixture(scope="module")
def subcommands() -> set[str]:
    return known_subcommands()


@pytest.mark.parametrize("phase", sorted(JS_FILES))
def test_plan_commands_have_js_coverage(phase, fixture_project, subcommands):
    """Every genuinely-invoked harness_cli.py subcommand in phase N's
    CURRENT plangen output must appear somewhere in phase N's workflow JS,
    unless the (phase, marker) pair is explicitly listed in KNOWN_GAPS."""
    plan_text = generate_dynamic_plan(phase, fixture_project)
    js_text = (WORKFLOWS_DIR / JS_FILES[phase]).read_text(encoding="utf-8")

    plan_markers = extract_plan_markers(plan_text, subcommands)
    js_commands = extract_js_subcommands(js_text, subcommands)
    gaps_for_phase = KNOWN_GAPS.get(phase, {})

    missing = []
    for marker, cmds in plan_markers.items():
        uncovered = cmds - js_commands
        if not uncovered:
            continue
        if gaps_for_phase.get(marker, "").strip():
            continue  # documented, tracked in KNOWN_GAPS
        missing.append((marker, sorted(uncovered)))

    assert not missing, (
        f"phase{phase}: plan marker(s) invoke commands absent from the "
        f"workflow JS and not documented in KNOWN_GAPS[{phase}]: {missing}"
    )


def test_known_gaps_have_reasons():
    for phase, markers in KNOWN_GAPS.items():
        for marker, reason in markers.items():
            assert reason and reason.strip(), (
                f"KNOWN_GAPS[{phase}][{marker!r}] has an empty reason"
            )


def test_known_gaps_reference_real_markers(fixture_project, subcommands):
    """A KNOWN_GAPS entry for a marker no longer in the current plan
    (fixed upstream, renamed, ...) must be pruned — otherwise the registry
    silently accumulates dead entries forever, the same failure mode that
    left test_workflow_artifacts_commit_pattern.py skipping silently."""
    for phase, markers in KNOWN_GAPS.items():
        plan_text = generate_dynamic_plan(phase, fixture_project)
        plan_markers = extract_plan_markers(plan_text, subcommands)
        for marker in markers:
            assert marker in plan_markers, (
                f"KNOWN_GAPS[{phase}][{marker!r}] no longer exists as a "
                f"marker in the current phase{phase} plan — prune this "
                f"stale entry"
            )


def test_runtime_only_have_reasons():
    for phase, titles in RUNTIME_ONLY.items():
        for title, reason in titles.items():
            assert reason and len(reason.strip()) >= 20, (
                f"RUNTIME_ONLY[{phase}][{title!r}] needs a substantive reason"
            )


@pytest.mark.parametrize("phase", sorted(JS_FILES))
def test_runtime_only_phases_exist_in_js(phase):
    """Every RUNTIME_ONLY entry must match a real phase() title in that
    file — otherwise the registry accumulates stale entries for phases
    that were renamed or removed during migration."""
    js_text = (WORKFLOWS_DIR / JS_FILES[phase]).read_text(encoding="utf-8")
    js_phase_titles = set(extract_js_phases(js_text))
    for title in RUNTIME_ONLY.get(phase, {}):
        assert title in js_phase_titles, (
            f"RUNTIME_ONLY[{phase}][{title!r}] does not match any phase() "
            f"title in {JS_FILES[phase]} — stale entry"
        )
