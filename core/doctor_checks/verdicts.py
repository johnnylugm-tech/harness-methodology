"""doctor checks: a recorded verdict against the tree it was recorded for.

Split out of core/doctor.py in R49-B. Five checks and their two helpers, all
asking one question in different tenses: does a verdict this project already
holds still describe what is on disk?

  enforcer provenance     which enforcer produced the result, and is it here
  phase verdict staleness a verdict older than the tree it judged
  milestone tree          the delivered tree vs the one the milestone recorded
  gate1 evidence          a quality_complete claim with no record behind it

Rounds 43, 44 and 45 each added one of these, which is why they read as
variations: the class is "the verdict outlived its proof", and it kept coming
back at a different coordinate. Grouping them puts the four coordinates on one
screen for whoever meets the fifth.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
from pathlib import Path

from core.doctor_checks import Finding
from core.quality_gate.gate1_evidence import (
    EVIDENCE_SOURCE_SKIP,
    GATE_TIMESTAMPS_FILE,
    verify_finalize_evidence,
)
from core.state_io import StateCorruptError, load_quality_manifest, load_state
from core.utils.project_layout import ProjectLayout

def _check_enforcer_provenance(project: Path) -> list[Finding]:
    """WARN when a recorded `enforcer_sha` no longer resolves in the harness.

    Round 30 站4. Round 19 站3 made every verdict record the harness commit that
    produced it, so "which enforcer said this?" became answerable from the
    artifact. The identifier it records is MUTABLE: taskq-advance's 8 Gate 1
    results, its Gate 2 result and both `state.json.phase_completed` entries all
    cite `01bb3bb4`, and a rebase of the harness submodule on 2026-08-02 left
    that commit reachable from nothing:

        git merge-base --is-ancestor 01bb3bb4 main  → NO
        git branch -a --contains 01bb3bb4           → (empty)

    The content still exists as `7154768`; the recorded name does not resolve.
    Rebasing is a normal part of this workflow, so this will keep happening —
    which is why Round 29 站4 added `enforcer_surface`, git object IDs for the
    three paths that actually produce verdicts. Those survive a rebase (measured:
    identical across `01bb3bb4` and `7154768`, and correctly DIFFERENT from the
    pre-fix base `c5971cd`). Round 29 wrote them and gave them no reader.

    WARN, never ERROR: an unreachable enforcer commit does not make the verdict
    wrong, and this framework is developed while it runs. What it costs is the
    ability to answer the question the field was added for, so it has to be
    visible somewhere.
    """
    from core.harness_provenance import enforcer_surface, harness_root

    recorded: dict[str, list[str]] = {}
    method = project / ".methodology"
    if not method.is_dir():
        return []
    for path in sorted(method.rglob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for sha in _enforcer_shas_in(data):
            recorded.setdefault(sha, []).append(str(path.relative_to(project)))
    if not recorded:
        return []

    root = harness_root()
    findings: list[Finding] = []
    for sha, sources in sorted(recorded.items()):
        bare = sha.removesuffix("-dirty")
        if bare in ("", "unknown"):
            continue
        try:
            proc = subprocess.run(
                ["git", "-C", str(root), "cat-file", "-e", f"{bare}^{{commit}}"],
                capture_output=True, text=True, timeout=10,
            )
        except (OSError, subprocess.SubprocessError):
            return []  # git unusable — see _check_submodule_behind's precedent
        if proc.returncode == 0:
            continue
        current = enforcer_surface()
        findings.append(Finding(
            "provenance", "WARN",
            f"verdict(s) in {', '.join(sorted(set(sources))[:3])} name enforcer "
            f"{bare[:12]}, which resolves to no commit in {root} — most likely a "
            f"rebase of the harness after the verdict was written. The recorded "
            f"`enforcer_surface` still answers whether the enforcing code was the "
            f"same; today's surface is "
            f"{ {k.rsplit('/', 1)[-1]: v[:8] for k, v in current.items()} }. "
            f"Fix: compare that against the `enforcer_surface` in those files; "
            f"if they match, the verdict stands and only its label is stale.",
        ))
    return findings


def _check_phase_verdict_staleness(project: Path) -> list[Finding]:
    """WARN for each completed phase whose enforcement surface has since moved.

    Round 43 站4. `_check_enforcer_provenance` above asks whether a recorded
    enforcer SHA still resolves. This asks the question an operator actually
    has when a preflight fires on an artifact belonging to a phase that
    already passed: did the rules change, or did I break something.

    Measured case: Round 42 站3 turned a missing SRS FR Block from a warning
    into a P2+ block. taskq-api's Phase 1 was accepted five rounds earlier and
    then failed a check that did not exist when it passed, with nothing able
    to say so.

    WARN, never ERROR, and it changes no verdict. Grandfathering the rule
    would mean the framework can never raise its own bar (Round 38's
    no-waivable-threshold rule, inverted); what a stale recorded PASS needs is
    to stop claiming to be current, not to be honoured.
    """
    from core.harness_provenance import phase_verdict_staleness
    from core.phase_topology import VALID_PHASES

    findings: list[Finding] = []
    for phase in sorted(VALID_PHASES):
        moved = phase_verdict_staleness(project, phase)
        if not moved:
            continue
        findings.append(Finding(
            "provenance", "WARN",
            f"Phase {phase}'s recorded PASS was measured under a different "
            f"enforcement surface — {', '.join(moved['moved'])} changed since. "
            f"A check that fires on a Phase {phase} artifact may be a raised "
            f"bar rather than a regression you introduced. The verdict is not "
            f"waived: fix what the check names. This is here so you know which "
            f"of the two it is.",
        ))
    return findings


def _check_milestone_tree_matches_verdict(project: Path) -> list[Finding]:
    """WARN for each completed phase whose commit is not the tree it was judged on.

    Round 44 站4. Two comparisons, in order, and both compare digests taken
    under the same definition — a cross-definition comparison would report
    every project as broken the day the definition moved, which is what the
    first draft of this function did.

      1. `phase_completed[N].delivered_tree_sha256` (station 2) against the
         tree of `phase_completed[N].sha`. Exact, and the whole point of
         recording the field.
      2. For records written before that field existed: the phase's last PASS
         verdict, which since station 1 carries both `delivered_tree_sha256`
         (the tree measured) and `head_tree_sha256` (the tree git had). When
         those two differ, the verdict was measured on content nobody had
         committed. Both come out of one row, so no definition can drift
         between them.

    This is the shape taskq-advance's Phase 3 has: `81bbeb4` recorded as the
    milestone at 13:17:55, the `@given` tests that unblocked its P4 entry
    first entering git at 13:32, and `git archive 81bbeb4 | grep -rl @given`
    empty.

    Diagnosis, never a re-judgement — the same standing as
    `_check_phase_verdict_staleness` above. The verdict does not move and
    nothing is waived. What changes is that a milestone certifying a tree
    nobody committed stops being invisible.

    Silence when neither comparison is available (Round 39/40 — a record
    predating a field is not a violation) or when git can no longer resolve
    the sha (Round 32/35 — could-not-measure is not a finding).
    """
    from core.phase_topology import EXIT_GATE_MAP
    from core.quality_gate.gate_verify import PASS, read_verdicts
    from core.utils.delivery_scope import committed_tree_digest

    try:
        state = load_state(project, lenient=True)
    except Exception:  # pylint: disable=broad-exception-caught
        # `lenient=True` already routes a corrupt state.json to the
        # degradation ledger; doctor does not re-report it.
        logging.getLogger(__name__).debug(
            "milestone-tree check: state.json unreadable for %s", project,
            exc_info=True,
        )
        return []

    completed = state.get("phase_completed") or {}
    if not isinstance(completed, dict):
        return []
    verdicts, _err = read_verdicts(project)

    findings: list[Finding] = []
    for key in sorted(completed, key=lambda k: str(k)):
        entry = completed.get(key)
        if not isinstance(entry, dict):
            continue
        sha = entry.get("sha")
        if not isinstance(sha, str) or not sha:
            continue

        recorded = entry.get("delivered_tree_sha256")
        if isinstance(recorded, str) and recorded:
            actual = committed_tree_digest(project, sha)
            if not actual or actual == recorded:
                continue
            findings.append(Finding(
                "provenance", "WARN",
                f"Phase {key} was certified on a tree its own commit does not "
                f"contain — the milestone records {recorded[:12]} and "
                f"{sha[:12]}'s tree digests to {actual[:12]}. A clone of that "
                f"commit is not what the phase's checks read. The verdict is "
                f"not re-judged; this says which tree it was about.",
            ))
            continue

        try:
            gate = EXIT_GATE_MAP[int(key)]
        except (KeyError, ValueError, TypeError):
            continue
        passes = [r for r in verdicts
                  if r.get("gate") == gate and r.get("verdict") == PASS]
        if not passes:
            continue
        latest = passes[-1]
        measured = latest.get("delivered_tree_sha256")
        committed = latest.get("head_tree_sha256")
        if not isinstance(measured, str) or not isinstance(committed, str):
            continue
        if not measured or not committed or measured == committed:
            continue
        findings.append(Finding(
            "provenance", "WARN",
            f"Phase {key}'s gate {gate} PASS was measured on uncommitted "
            f"content — the verdict read tree {measured[:12]} while git held "
            f"{committed[:12]}, and the milestone is recorded at {sha[:12]}. "
            f"Work that satisfied the phase's checks was in the working "
            f"directory and not in any commit. The verdict is not re-judged; "
            f"this says which tree it was about.",
        ))
    return findings


def _enforcer_shas_in(data: object) -> "list[str]":
    """Every `enforcer_sha` value in a parsed artifact, at any nesting depth.

    state.json carries them under `phase_completed.<n>`, gate results at the top
    level, and the quality manifest not at all — one walker rather than three
    readers that each know one shape.
    """
    out: list[str] = []
    if isinstance(data, dict):
        value = data.get("enforcer_sha")
        if isinstance(value, str) and value:
            out.append(value)
        for nested in data.values():
            out.extend(_enforcer_shas_in(nested))
    elif isinstance(data, list):
        for item in data:
            out.extend(_enforcer_shas_in(item))
    return out


def _check_gate1_evidence(project: Path, layout: ProjectLayout) -> list[Finding]:
    manifest_path = layout.quality_manifest_path
    if not manifest_path.is_file():
        return []
    try:
        manifest = load_quality_manifest(project)
    except StateCorruptError:
        return []  # check 3 already reports the parse failure
    if not isinstance(manifest, dict):
        return []
    gate_results = manifest.get("gate_results")
    gate1 = gate_results.get("gate1") if isinstance(gate_results, dict) else None
    if not isinstance(gate1, dict):
        return []

    ts_frs: set[str] = set()
    ts_file = layout.methodology_dir / GATE_TIMESTAMPS_FILE
    if ts_file.is_file():
        try:
            for line in ts_file.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not (isinstance(entry, dict) and entry.get("gate") == 1):
                    continue
                # Round 20 站4: only a `finalize` row is independent evidence.
                # A `skip` row is written by GATE1-DELTA's already-done branch,
                # whose precondition is that a sentinel/commit ALREADY exists —
                # so counting it here would corroborate the sentinel channel
                # with a shadow of itself. Rows written before that station
                # carry no `source` and are accepted, since they predate the
                # distinction and the skip branch was the rarer writer.
                if entry.get("source") == EVIDENCE_SOURCE_SKIP:
                    continue
                ts_frs.add(str(entry.get("fr_id", "")).replace("-", "").lower())
        except OSError:
            pass

    sentinels_dir = project / ".sessi-work" / "sentinels"
    findings: list[Finding] = []
    for fr_id, rec in gate1.items():
        if not (isinstance(rec, dict) and rec.get("quality_complete")):
            continue
        fr_key = str(fr_id).replace("-", "").lower()
        # Round 32 站2: `.flag` no longer counts. run-gate writes it when the
        # gate STARTS — measured on a live P4, g1_p4_fr01.flag was written at
        # 03:49:53 and the gate BLOCKED at 03:54:07, and this check read that
        # flag as evidence FR-01 had passed. Only a finalize receipt attests a
        # verdict, and it has to agree with the registries written beside it.
        try:
            receipts = sorted(sentinels_dir.glob(f"g1_p*_{fr_key}.finalized"))
        except OSError:
            receipts = []
        if not receipts and fr_key not in ts_frs:
            findings.append(Finding(
                "gate1-evidence", "ERROR",
                f"quality_manifest.json marks {fr_id} Gate 1 quality_complete but "
                f"no evidence exists in any channel (finalize receipt, "
                f"{GATE_TIMESTAMPS_FILE}) — fabricated or hand-edited result; "
                f"re-run run-gate/finalize-gate for this FR or correct the manifest"))
            continue
        # One implementation, two consumers: cli/phase_cmds.py's advance-phase
        # precheck calls the same function. Each having its own copy of the
        # rule is how the two drifted into disagreeing about what counts.
        for _receipt in receipts:
            _phase = _phase_from_sentinel_name(_receipt.name)
            for problem in verify_finalize_evidence(project, 1, _phase, str(fr_id)):
                findings.append(Finding("gate1-evidence", "ERROR", problem))
    return findings


def _phase_from_sentinel_name(name: str) -> "int | None":
    """`g1_p4_fr01.finalized` -> 4. None when the name is not phase-scoped
    (the pre-v2.13 form, which verify_finalize_evidence then checks for shape
    only — there is no phase to look the registry rows up under)."""
    m = re.match(r"^g\d+_p(\d+)_", name)
    return int(m.group(1)) if m else None
