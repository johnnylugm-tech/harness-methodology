"""Gate 1 evidence persistence + anti-fabrication interval checks.

Moved verbatim from harness_cli.py (絞殺者續章 S3). Three co-equal Gate 1
evidence channels (sentinel flags, finalized marks, gate_timestamps.jsonl)
plus the batch-commit fraud detector and the per-FR score tracker feeding
the inter-FR variance check.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import re
import sys
import warnings
from typing import Optional

from core.atomic_io import atomic_write_json
from core.quality_gate.spec_coverage import _git_test_patterns

__all__ = [
    "fr_gate1_commit_sha",
    "fr_code_changed_since_last_gate1",
    "validate_fr_coverage_immediate",
    "GATE_TIMESTAMPS_FILE",
    "GATE1_SCORES_FILE",
    "per_fr_result_path",
    "record_gate_timestamp",
    "gate1_evidence_exists",
    "check_commit_intervals",
    "record_gate1_score",
    "gate1_phase_summary",
    "RECEIPT_SCHEMA",
    "format_finalize_receipt",
    "read_finalize_receipt_text",
    "write_finalize_receipt",
    "verify_finalize_evidence",
    "_sentinel_path",
    "_finalize_sentinel_path",
    "SENTINEL_FLAG_TEMPLATE",
    "SENTINEL_FINALIZED_TEMPLATE",
]

# Sentinel filename SSOT (Round 3 Station I): every consumer of the on-disk
# sentinel format — the two path builders below, the evidence probe, and the
# prose generate_full_plan.py renders into phase plans — formats these
# templates instead of hand-writing the pattern. bea1bb1 fixed exactly that
# hand-written-copy drift by hand; tests/test_sentinel_template_ssot.py makes
# the next one fail at birth.
SENTINEL_FLAG_TEMPLATE = "g{gate}_p{phase}_{key}.flag"
SENTINEL_FINALIZED_TEMPLATE = "g{gate}_p{phase}_{key}.finalized"


def _sentinel_path(project: Path, gate: int, fr_id: str | None, phase: int | None = None) -> Path:
    """Return the sentinel file path that run-gate writes and finalize-gate verifies.

    v2.13 sentinel scope fix: include phase in the path so that Gate 1 written
    by Phase 1 (spec coverage) does NOT satisfy Gate 1 required by Phase 3
    (code coverage). Without phase, the same `g1_fr01.flag` path is reused
    across phases and stale Phase 1 sentinels leak into Phase 3 pre-checks.

    Path format:
      FR-specific:  g{gate}_p{phase}_{fr}.flag    e.g. g1_p3_fr01.flag
      Phase-level:  g{gate}_p{phase}_phase.flag  e.g. g2_p3_phase.flag (fr_id=None)

    Moved from cli/_shared.py (harness bug: GATE1 idempotency phase-scoping
    fix) so core/quality_gate/gate1_evidence.py can reuse it without a
    core -> cli circular import.
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
    return d / SENTINEL_FLAG_TEMPLATE.format(gate=gate, phase=phase, key=key)


def _finalize_sentinel_path(project: Path, gate: int, fr_id: str | None, phase: int | None = None) -> Path:
    """Return the sentinel that finalize-gate writes. advance-phase verifies it.

    See _sentinel_path for the v2.13 phase-scoping rationale. Moved from
    cli/_shared.py alongside _sentinel_path (see its docstring).
    """
    key = (fr_id or "phase").replace("-", "").lower()
    d = project / ".sessi-work" / "sentinels"

    if phase is not None:
        return d / SENTINEL_FINALIZED_TEMPLATE.format(gate=gate, phase=phase, key=key)

    # Legacy fallback (no phase provided): prefer the new-style .finalized;
    # fall back to legacy .flag with hyphen-stripped fr id (Bug #120 compat).
    std_path = d / f"g{gate}_{key}.finalized"
    if fr_id:
        legacy_path = d / f"g{gate}_{fr_id}.flag"
        if not std_path.exists() and legacy_path.exists():
            return legacy_path

    return std_path

# Non-dotfile (consistent with other .methodology/ files like state.json, sessions_spawn.log).
# Replaces the old ".gate_timestamps.jsonl" hidden file name used before 2026-05-18.
GATE_TIMESTAMPS_FILE = "gate_timestamps.jsonl"

GATE1_SCORES_FILE = ".gate1_scores.json"


def per_fr_result_path(project: Path, gate: int, fr_id: str) -> Path:
    """Where finalize-gate keeps ONE FR's gate result, permanently.

    `.methodology/gate{N}_result.json` is a rolling alias — every FR's finalize
    overwrites it, so after a phase it holds whichever FR went last. This is the
    per-FR copy, and it is the artifact a receipt for that FR has to be able to
    point at.

    Round 45 站3: the path was written out by hand in three places
    (cli/_shared.py's two resolvers and cli/gate_cmds.py's writer). It is stated
    here once and read from here, which is the shape this whole round is about.
    """
    return project / ".methodology" / "gate_results" / f"gate{gate}" / f"{fr_id}.json"


EVIDENCE_SOURCE_FINALIZE = "finalize"
EVIDENCE_SOURCE_SKIP = "skip"


# ── the finalize receipt (Round 32 站1) ─────────────────────────────────
#
# What a finalize sentinel has to prove is "finalize-gate ran and passed for
# this gate/phase/FR". Until this round its entire content was
# `datetime.now(timezone.utc).isoformat()` — a string with no connection to the
# thing it attested, and one any shell can produce. Measured on a live P4:
# eight of them appeared in the same second, without microseconds, one minute
# after Gate 1 had BLOCKED, and advance-phase read them as eight passes.
#
# A receipt costs what the verification cost. `result_sha256` is the digest of
# the gate result the verdict was taken on, so forging a receipt means forging
# a gate{N}_result.json that survives S3/S4 — which is the thing the gate
# already exists to prevent. The other fields let a later reader say WHICH run
# this was without re-deriving it.
#
# Schema 2 (Round 45 站3): `result_sha256` is the digest of THIS FR's own
# `gate_results/gate{N}/{fr}.json`, not of the rolling `gate{N}_result.json`
# alias the next FR's finalize overwrites. Under schema 1 that digest became
# unresolvable the moment another FR finalized — by construction, for every FR
# of a phase but the last — so a schema-1 receipt's digest is not evidence of
# anything and is not compared. Existence of the per-FR file is checked for
# both: that file has been written since 2026-07-15 (Fix H-E), so a receipt of
# either vintage had one.
RECEIPT_SCHEMA = 2
# Schema 1 receipts stay readable. A hard cut here would refuse every receipt
# on every existing project — the opposite of Round 39/40's rule that a record
# predating a mechanism is not a violation.
RECEIPT_SCHEMAS_ACCEPTED = frozenset({1, 2})


def format_finalize_receipt(
    *,
    gate: int,
    phase: int | None,
    fr_id: str | None,
    score: float | None,
    result_path: "Path | None",
    enforcer_sha: str | None = None,
) -> str:
    """Render the receipt text finalize-gate writes into the sentinel.

    Formatter and parser live side by side on purpose (same shape as Round 31's
    mutmut_report): the two halves of a format drift apart the moment they stop
    being read together.
    """
    from datetime import datetime, timezone

    if enforcer_sha is None:
        from core.harness_provenance import enforcer_sha as _enforcer_sha
        enforcer_sha = _enforcer_sha()

    digest = None
    if result_path is not None and Path(result_path).is_file():
        from core.quality_gate.evidence_digest import digest_of_file
        digest = digest_of_file(
            Path(result_path), source=f"gate{gate}_result.json (finalize receipt)"
        )

    payload = {
        "schema": RECEIPT_SCHEMA,
        "gate": gate,
        "phase": phase,
        "fr_id": fr_id,
        "score": score,
        "result_sha256": (digest or {}).get("sha256"),
        "enforcer_sha": enforcer_sha,
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    return json.dumps(payload, indent=2, ensure_ascii=False) + "\n"


def read_finalize_receipt_text(text: str) -> "dict | None":
    """Parse receipt text, or None when the text carries no receipt.

    None means "this text is not a receipt", never "the receipt says nothing is
    wrong" (Round 31's parse-failure rule). The bare-timestamp form every
    sentinel used before this round lands here, which is what makes the hard
    cut a hard cut.
    """
    try:
        data = json.loads(text)
    except (TypeError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("schema") not in RECEIPT_SCHEMAS_ACCEPTED:
        return None
    if "gate" not in data or "result_sha256" not in data:
        return None
    return data


def write_finalize_receipt(
    project: Path,
    *,
    gate: int,
    phase: int | None,
    fr_id: str | None,
    score: float | None,
    result_path: "Path | None",
) -> Path:
    """Write the receipt to its sentinel path and return that path.

    Round 45 站3: for a per-FR gate the receipt digests THIS FR's own result,
    not the rolling `gate{N}_result.json` alias the caller usually hands in —
    that alias is overwritten by the next FR's finalize, so a receipt pointing
    at it stops resolving before anyone has done anything wrong. The choice is
    made here rather than at each call site so a schema-2 receipt cannot be
    written against the wrong artifact.
    """
    if fr_id:
        _per_fr = per_fr_result_path(project, gate, fr_id)
        if _per_fr.is_file():
            result_path = _per_fr

    path = _finalize_sentinel_path(project, gate, fr_id, phase=phase)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        format_finalize_receipt(
            gate=gate, phase=phase, fr_id=fr_id, score=score,
            result_path=result_path,
        ),
        encoding="utf-8",
    )
    return path


def verify_finalize_evidence(
    project: Path, gate: int, phase: int | None, fr_id: str | None,
) -> list:
    """Cross-check the three artifacts finalize-gate writes. Empty list = OK.

    Round 32 站2. The three channels exist so that forging a pass is expensive,
    but every reader combined them with OR — advance-phase asked `.exists()`,
    doctor asked `has_sentinel or fr_key in ts_frs` — so satisfying the
    cheapest one was enough, and the cheapest one had no content contract at
    all. Measured on a live P4: eight receipts, zero rows in
    gate_timestamps.jsonl, zero entries in .gate1_scores.json, phase recorded
    complete.

    AND is not the answer either: `GATE1-DELTA already done -> skip` writes a
    `source="skip"` timestamp row and deliberately writes no receipt
    (cli/fr_cmds.py:249). So the rule is one-directional, and it is exact
    because finalize-gate writes all three itself, receipt last (站1):

        receipt present  =>  a `finalize` timestamp row for the same
                             gate/phase/FR exists, AND (gate 1 only) a
                             .gate1_scores.json entry exists whose score
                             matches the receipt's.
        timestamp only   =>  legal (the DELTA skip, or a run-fr-step row
                             whose sub-agent never called finalize-gate).

    "Receipt present, registries empty" has no producer once the receipt is
    written last. That combination is the forgery's fingerprint.

    Old-format sentinels (the bare timestamp every project carries today) are
    rejected outright rather than grandfathered: a legacy channel that still
    clears the check is the same hole with a longer name.
    """
    path = _finalize_sentinel_path(project, gate, fr_id, phase=phase)
    if not path.exists():
        return []  # Absence is the caller's business, not this function's.

    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        return [f"{path.name}: unreadable ({exc})"]

    receipt = read_finalize_receipt_text(raw)
    if receipt is None:
        return [
            f"{path.name} is not a finalize receipt — it contains "
            f"{raw.strip()[:40]!r}. Sentinels written before Round 32 carry a "
            f"bare timestamp, which proves nothing about the gate result. "
            f"Re-run `finalize-gate --gate {gate}"
            + (f" --phase {phase}" if phase is not None else "")
            + (f" --fr-id {fr_id}" if fr_id else "")
            + "` to produce one."
        ]

    problems: list = []
    if not receipt.get("result_sha256"):
        problems.append(
            f"{path.name}: the receipt names no gate result — it cannot "
            f"attest a verdict it does not identify"
        )
    elif fr_id:
        problems.extend(_per_fr_result_problems(project, gate, fr_id, receipt))

    if phase is not None:
        rows = _finalize_timestamp_rows(project, gate, phase, fr_id)
        if not rows:
            problems.append(
                f"{path.name}: no `finalize` row for gate {gate} phase {phase}"
                + (f" {fr_id}" if fr_id else "")
                + f" in {GATE_TIMESTAMPS_FILE}. finalize-gate writes that row "
                f"before it writes this receipt, so a receipt without one was "
                f"not written by finalize-gate."
            )
        if gate == 1 and fr_id:
            scores = _read_gate1_scores(project)
            # Round 45 站2: the phase KEY is the discriminator. Until this
            # round `record_gate1_score` pruned everything older than
            # `phase - 1` while sentinels accumulated forever, so a project two
            # phases past a sentinel was accused of fabricating it — thirty
            # such accusations on taskq-advance, all false. The prune is gone,
            # but no removal restores what earlier runs already dropped.
            #
            # A register that holds OTHER phases but not this one is a register
            # the prune has already been through: it cannot speak about this
            # phase, and silence is the honest answer (Round 32/35:
            # could-not-measure is not a finding; Round 39/40: a record
            # predating a mechanism is not a violation).
            #
            # A phase whose key IS present was written by the finalize runs of
            # that phase's other FRs, so this FR's absence is a genuine
            # contradiction and keeps its ERROR. The first FR of a phase is the
            # one case this cannot see; the `finalize` row check above covers
            # it, since finalize-gate writes that row in the same function.
            #
            # An entirely absent or unreadable register keeps Round 32 站2's
            # ERROR untouched — the three artifacts have one author, and a
            # receipt standing alone is what that station exists to refuse.
            phase_scores = scores.get(str(phase))
            recorded = (phase_scores or {}).get(fr_id)
            if scores and phase_scores is None:
                pass  # cannot corroborate — not an accusation
            elif recorded is None:
                problems.append(
                    f"{path.name}: no {fr_id} entry for phase {phase} in "
                    f"{GATE1_SCORES_FILE}, which does hold "
                    f"{', '.join(sorted(phase_scores or {})) or 'no FRs'} for "
                    f"that phase — the same run writes both"
                )
            elif receipt.get("score") is not None and abs(
                float(recorded) - float(receipt["score"])
            ) > 0.05:
                problems.append(
                    f"{path.name}: receipt score {receipt['score']} does not "
                    f"match the {recorded} recorded in {GATE1_SCORES_FILE}"
                )
    return problems


def _per_fr_result_problems(
    project: Path, gate: int, fr_id: str, receipt: dict,
) -> list:
    """Dereference the receipt's `result_sha256` against this FR's own result.

    Round 45 站3. taskq-advance's last commit, `30638d9 feat(FR-09): Gate1
    PASS — score=100.0 [phase=7]`, deleted five sibling FRs' Gate 1 results in
    539 lines of removal. `fr_progress.json`, `.gate1_scores.json` and
    CLAUDE.md's FR Registry all still say those five scored 100.0. Nothing
    noticed, because `doctor` corroborates the manifest against the receipt and
    `gate_timestamps.jsonl`, and both of those survived.

    No new register is needed: Round 32 站1 put `result_sha256` in the receipt
    exactly so a verdict names the artifact it was taken on. The pointer was
    never dereferenced.

    Existence is asked of every receipt: `gate_results/gate{N}/{fr}.json` has
    been written since 2026-07-15, so a receipt of either vintage had one, and
    its absence is unambiguous.

    The digest is compared only for schema-2 receipts. A schema-1 receipt
    digested the rolling `gate{N}_result.json` alias, which the next FR's
    finalize overwrites — so for every FR of a phase but the last, that digest
    was already unresolvable before anyone touched anything. Comparing it would
    manufacture one accusation per FR: the same false-positive machine station
    2 has just finished dismantling.
    """
    from core.quality_gate.evidence_digest import digest_of_file

    per_fr = per_fr_result_path(project, gate, fr_id)
    if not per_fr.is_file():
        return [
            f"{fr_id} (phase {receipt.get('phase')}): the finalize receipt "
            f"names a gate {gate} result no longer on disk — "
            f"{per_fr.relative_to(project)} is missing"
            f"{_deleted_by(project, per_fr)}. The score registers still carry "
            f"this FR; re-run finalize-gate for it, or restore the file."
        ]

    if int(receipt.get("schema") or 1) < 2:
        return []

    # The path carries no phase: `gate_results/gate{N}/{fr}.json` is one slot
    # per FR, rewritten by every phase that re-runs that FR's gate. Measured on
    # taskq-advance after its P8 run — FR-03, FR-05, FR-06, FR-08 and FR-10 now
    # hold phase-8 results while their phase-7 receipts still exist. That is a
    # legitimate later run, not a rewrite of this verdict's evidence, and
    # comparing across it would fire for every FR at every phase boundary
    # forever: the same false-accusation machine station 2 removed, rebuilt one
    # station later. Existence has been checked; content cannot be.
    try:
        _on_disk_phase = json.loads(
            per_fr.read_text(encoding="utf-8")
        ).get("phase")
    except (OSError, ValueError):
        _on_disk_phase = None
    if _on_disk_phase is not None and _on_disk_phase != receipt.get("phase"):
        return []

    expected = receipt.get("result_sha256")
    if digest_of_file(per_fr, source=str(per_fr)).get("sha256") == expected:
        return []
    return [
        f"{fr_id}: {per_fr.relative_to(project)} is not the result this "
        f"receipt was taken on — its sha256 does not match the receipt's "
        f"{str(expected)[:12]}. The evidence behind this verdict was "
        f"rewritten after it was made."
    ]


def _deleted_by(project: Path, path: Path) -> str:
    """`" (deleted by <sha> <subject>)"`, or `""` when git cannot say.

    Names the commit rather than leaving the reader to go looking — Round 24
    站1's rule that a refusal states its cause, applied to an absence.
    """
    import subprocess
    try:
        proc = subprocess.run(
            ["git", "-C", str(project), "log", "--diff-filter=D", "-1",
             "--format=%h %s", "--", str(path.relative_to(project))],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError, ValueError):
        return ""
    line = proc.stdout.strip().splitlines()[0] if proc.stdout.strip() else ""
    # `%h %s` starts with an abbreviated sha. Anything else is not git speaking
    # — the message must not repeat whatever a caller's stubbed subprocess
    # happened to return.
    head = line.split(" ", 1)[0] if line else ""
    if proc.returncode != 0 or not re.fullmatch(r"[0-9a-f]{7,40}", head):
        return ""
    return f" (deleted by {line})"


def gate1_phase_summary(project: Path, phase: int, fr_ids: "list | None" = None) -> dict:
    """Which FRs have a recorded Gate 1 verdict for *phase*, and which do not.

    Round 32 站6. A milestone commit reading "all N FR(s) Gate1 re-eval PASS"
    was generated from a count of the FRs in the manifest, not from a count of
    the verdicts on record. Measured on a live P4: eight claimed, zero
    recorded in .gate1_scores.json, zero `finalize` rows in
    gate_timestamps.jsonl for (phase=4, gate=1), and the FR-01 gate BLOCKED
    fourteen minutes after the second of two byte-identical milestones.

    `fr_ids` defaults to the project's manifest, so the caller does not
    re-derive the roster the answer is measured against.

    Returns ``{"phase", "expected", "recorded", "missing"}``.
    """
    from core.state_io import load_quality_manifest

    if fr_ids is None:
        fr_ids = list(load_quality_manifest(project, lenient=True).get("fr_ids", []))
    expected = [str(f) for f in fr_ids]

    scores = (_read_gate1_scores(project).get(str(phase)) or {})
    recorded = []
    for fr_id in expected:
        if scores.get(fr_id) is None:
            continue
        if not _finalize_timestamp_rows(project, 1, phase, fr_id):
            continue
        recorded.append(fr_id)
    return {
        "phase": phase,
        "expected": expected,
        "recorded": recorded,
        "missing": [f for f in expected if f not in recorded],
    }


def _read_gate1_scores(project: Path) -> dict:
    """`.gate1_scores.json` as a dict; {} when absent or unreadable."""
    path = project / ".methodology" / GATE1_SCORES_FILE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _finalize_timestamp_rows(
    project: Path, gate: int, phase: int, fr_id: str | None,
) -> list:
    """`finalize`-sourced rows matching this gate/phase/FR.

    Rows written before Round 20 站4 carry no `source` and are accepted, the
    same allowance core/doctor.py already makes for them.
    """
    path = project / ".methodology" / GATE_TIMESTAMPS_FILE
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    want_fr = (fr_id or "phase")
    rows = []
    for line in lines:
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if not isinstance(entry, dict):
            continue
        if entry.get("gate") != gate or entry.get("phase") != phase:
            continue
        if str(entry.get("fr_id") or "phase") != want_fr:
            continue
        if entry.get("source") == EVIDENCE_SOURCE_SKIP:
            continue
        rows.append(entry)
    return rows


def record_gate_timestamp(
    project: Path,
    phase: int,
    gate_num: int,
    fr_id: str | None,
    source: str = EVIDENCE_SOURCE_FINALIZE,
) -> None:
    """Append gate commit timestamp to .methodology/gate_timestamps.jsonl (P1 persistence).

    Round 45 站2 removed a trim to the last 200 rows. This file is one of the
    two channels `verify_finalize_evidence` corroborates a finalize receipt
    against, and receipts are never pruned — so the trim guaranteed that a long
    enough project would be accused of forging its own early phases. Measured
    on taskq-advance: 131 bytes per row, so the 200-row cap bounded the file at
    26 KB and an unpruned 30-FR nine-phase run at roughly 70 KB. There was no
    growth to bound.

    `source` records WHY the row exists, and the distinction matters because
    core/doctor.py treats this file as evidence:

      "finalize"  a gate was actually evaluated and finalized just now.
      "skip"      cli/fr_cmds.py's GATE1-DELTA `already done → skip` branch,
                  which writes a row WITHOUT running an evaluation, so that
                  _check_gate1_live_coverage does not exit-14 when every FR
                  legitimately skips.

    A skip row is not independent evidence: the branch that writes it only runs
    when _fr_step_already_done already found a sentinel or commit — so it is a
    SHADOW of the sentinel channel, not a second opinion about it. doctor used
    to accept `has_sentinel or fr_key in ts_frs`, whose `or` reads like two
    corroborating sources; taskq's Phase 4 wrote five such rows within 3.1
    seconds with no dispatch behind them (Round 20 站4). Nothing was forged —
    the skip precondition guarantees real evidence exists elsewhere — but a
    reader, or the next person to change that check, could not tell the two
    apart. Now they can.
    """
    import time as _time
    ts_dir = project / ".methodology"
    ts_dir.mkdir(parents=True, exist_ok=True)
    ts_file = ts_dir / GATE_TIMESTAMPS_FILE

    # One-time migration: rename old hidden-file to the new visible name
    _old = ts_dir / ".gate_timestamps.jsonl"
    if _old.exists() and not ts_file.exists():
        try:
            _old.rename(ts_file)
        except OSError:
            pass

    # Round 24 站3: `ts` stays an epoch float — core/doctor.py and
    # _check_gate1_live_coverage do arithmetic on it, and swapping the format
    # would break every existing project's file. `iso` is ADDED alongside so a
    # human (or a cross-artifact audit) can line these rows up against
    # state.json / sessions_spawn.log without knowing the writer's timezone.
    from core.utils.timefmt import utc_now_iso

    entry = {
        "phase": phase, "gate": gate_num, "fr_id": fr_id or "phase",
        "ts": _time.time(), "iso": utc_now_iso(), "source": source,
    }
    try:
        with open(str(ts_file), "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError:
        pass  # Non-blocking


def gate1_evidence_exists(project: Path, fr_id: str, phase: int = 3) -> bool:
    """Multi-source Gate 1 evidence check (O2, 2026-07-07).

    Accepts any of three co-equal Gate 1 evidence channels — eliminates the
    single-source-of-evidence design defect where a clean restart wiping
    `.sessi-work/sentinels/` would block P3→P4 handoff even though Gate 1
    was genuinely complete (fr_progress.json + gate_timestamps.jsonl both
    persist this fact).

    Try in order:
      1. `.sessi-work/sentinels/g1_p{phase}_{fr}.flag`  — run-gate's mark
      2. `.sessi-work/sentinels/g1_p{phase}_{fr}.finalized` — finalize-gate's mark
         (finalize-gate implies run-gate ran, so this is sufficient)
      3. `.methodology/gate_timestamps.jsonl` row matching phase/gate/fr_id
         (P1-persistent; survives clean restart)

    `fr_id` normalization (`replace("-", "").lower()`) matches `_sentinel_path`
    and `_finalize_sentinel_path` in harness_cli.py.
    """
    fr_key = fr_id.replace("-", "").lower()
    sentinels_dir = project / ".sessi-work" / "sentinels"
    if (sentinels_dir / SENTINEL_FLAG_TEMPLATE.format(gate=1, phase=phase, key=fr_key)).exists():
        return True
    if (sentinels_dir / SENTINEL_FINALIZED_TEMPLATE.format(gate=1, phase=phase, key=fr_key)).exists():
        return True
    ts_file = project / ".methodology" / GATE_TIMESTAMPS_FILE
    if ts_file.exists():
        try:
            for line in ts_file.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                _e = json.loads(line)
                if (
                    _e.get("phase") == phase
                    and _e.get("gate") == 1
                    and str(_e.get("fr_id", "")).replace("-", "").lower() == fr_key
                ):
                    return True
        except (json.JSONDecodeError, OSError):
            pass
    return False


def check_commit_intervals(
    project: str, phase: int, gate_num: int, fr_id: str | None = None
) -> tuple[bool, str]:
    """Check if current gate attempt would exceed the batch-commit threshold (P1).

    Pure read — does NOT write timestamps.  The caller (cmd_finalize_gate) must call
    record_gate_timestamp() only on successful finalization, so failed attempts don't
    accumulate in the file and trigger false positives on retry.

    Blocks if ≥2 prior successful finalizations exist within a 2-second window for the
    same (phase, gate, fr_id) bucket (3 total = statistically implausible for genuine
    per-FR work). fr_id is optional: when None the check is phase-level only (legacy
    behaviour for callers that don't track per-FR); when provided, distinct FRs do
    not collide into the same bucket, so 5 FRs completing in the same 2s window is
    no longer flagged as fraud.
    Returns (ok, diagnostic).
    """
    import time as _time
    project_path = Path(project)
    ts_dir = project_path / ".methodology"
    ts_file = ts_dir / GATE_TIMESTAMPS_FILE

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
                    # Per-FR bucket isolation: when the caller provides an fr_id,
                    # only count entries with the SAME fr_id as "same bucket".
                    # Distinct FRs finalizing within 2s (the natural per-FR
                    # sequential finalize-gate pattern) must NOT collide here —
                    # doing so was producing false-positive fraud blocks during
                    # Phase 3 Gate 1 finalization runs.
                    if fr_id is not None and entry.get("fr_id") != fr_id:
                        continue
                    recent.append(entry)
        except OSError:
            pass

    if len(recent) >= 2:  # 2 prior successful + 1 current attempt = 3 total in window
        return False, (
            f"{len(recent) + 1} gate commits within 2 seconds — "
            "scores must be evaluated per-FR with genuine evidence, not batch-copied"
        )
    return True, ""


def record_gate1_score(project: Path, phase: int, fr_id: str, score: float) -> None:
    """Track Gate 1 composite score per FR for inter-FR variance check.

    Round 45 站2 removed a prune of every phase older than ``phase - 1``, whose
    stated reason was to bound file growth. Measured: the whole file unpruned
    is 1,706 bytes at 10 FRs across 8 phases and 5,519 at 30 across 9. There
    was nothing to bound, and the window had a cost — `.sessi-work/sentinels/`
    is never pruned, `verify_finalize_evidence` requires the two to agree, and
    `doctor` runs that check for every sentinel it finds. On a copy of
    taskq-advance at Phase 7 that produced **thirty ERROR-level accusations of
    fabrication** against a project that had passed every gate: one per FR per
    phase whose scores the window had already dropped.

    Removing the prune fixes it going forward. It cannot restore what earlier
    runs already discarded, which is why `verify_finalize_evidence` also learned
    to tell "this register never knew about that phase" from "this register
    knows the phase and contradicts you".
    """
    scores_file = project / ".methodology" / GATE1_SCORES_FILE
    scores: dict = {}
    if scores_file.exists():
        try:
            scores = json.loads(scores_file.read_text(encoding="utf-8"))
        except Exception as exc:  # pylint: disable=broad-exception-caught
            from core.degradation_ledger import record_degradation
            record_degradation(
                project, "gate1_evidence.record_gate1_score",
                f"{GATE1_SCORES_FILE} unreadable — inter-FR variance history for "
                "all other phases/FRs is discarded, starting fresh",
                why=str(exc), owner="harness"
            )
    scores.setdefault(str(phase), {})[fr_id] = score
    try:
        atomic_write_json(scores_file, scores)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        from core.degradation_ledger import record_degradation
        record_degradation(
            project, "gate1_evidence.record_gate1_score",
            f"{GATE1_SCORES_FILE} write failed — this Gate 1 score for "
            f"{fr_id} (phase {phase}) will not be available to future variance checks",
            why=str(exc), owner="harness"
        )


# --- Gate-1 change detection + live coverage (moved from harness_cli, S4e) ---

def fr_gate1_commit_sha(fr_id: str, project: Path, phase: int | None = None) -> str | None:
    """Return the SHA of the most recent Gate 1 PASS commit for the given FR.

    phase-scoped lookup (when phase is given): bounds the git-log search to
    commits at/after the phase-scoped finalize-gate sentinel's own timestamp
    (_finalize_sentinel_path is only ever written right after a genuine
    bridge.finalize_gate() PASS for that exact phase — see gate_cmds.py
    cmd_finalize_gate). This closes a real gap: without --since, --grep
    matches ANY commit reachable from HEAD regardless of phase, and the
    unscoped "Gate1 PASS" fallback below can bind to a DIFFERENT FR's batch
    commit. If the sentinel doesn't exist, there is provably no Gate 1 PASS
    for this FR in this phase yet, so no SHA lookup / fallback is attempted.
    """
    import subprocess as _sp
    pattern = f"feat({fr_id}): Gate1 PASS"

    if phase is not None:
        sentinel = _finalize_sentinel_path(project, 1, fr_id, phase=phase)
        if not sentinel.exists():
            return None
        since = sentinel.read_text(encoding="utf-8").strip()
        r = _sp.run(
            ["git", "log", "--oneline", "--grep", pattern, "--since", since, "-1", "--format=%H"],
            capture_output=True, text=True, cwd=str(project),
        )
        sha = r.stdout.strip()
        return sha if sha else None

    r = _sp.run(
        ["git", "log", "--oneline", "--grep", pattern, "-1", "--format=%H"],
        capture_output=True, text=True, cwd=str(project),
    )
    sha = r.stdout.strip()
    if sha:
        return sha
    # Fallback: P3 batch commit e.g. "feat(P3-mid): 8/8 FR(s) Gate1 PASS"
    # Only reached for legacy (no phase) callers — phase-scoped callers above
    # return None instead of falling back to a possibly-different FR's commit.
    r2 = _sp.run(
        ["git", "log", "--oneline", "--grep", "Gate1 PASS", "-1", "--format=%H"],
        capture_output=True, text=True, cwd=str(project),
    )
    sha2 = r2.stdout.strip()
    return sha2 if sha2 else None


def fr_code_changed_since_last_gate1(fr_id: str, project: Path, phase: int | None = None) -> bool:
    """Check whether FR source/test files have changed since last Gate 1 PASS.

    Returns True if code has changed (re-evaluation needed), False otherwise.
    Uses AST parsing to accurately determine if changed lines overlap with FR functions.
    """
    import subprocess as _sp
    import ast
    sha = fr_gate1_commit_sha(fr_id, project, phase=phase)
    if sha is None:
        return True  # No prior Gate 1 PASS (this phase) → treat as changed

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
                    except Exception as hunk_exc:
                        print(f"[WARN] git diff hunk-header parse failed for {fr_id} "
                              f"(line {line!r}), skipping this hunk: {hunk_exc}", file=sys.stderr)
        except Exception as exc:
            # On parse error, fail safe
            print(f"[WARN] git diff hunk parse failed for {fr_id}: {exc}")
            return True

    return False

def validate_fr_coverage_immediate(
    project: Path, fr_id: "str | None" = None
) -> Optional[float]:
    """Line coverage %, measured right now.

    Returns:
        ``None``      — not measurable (no src/tests, pytest missing, timeout).
        ``float``     — coverage percentage (0.0 - 100.0), or 0.0 if tests
                        failed and coverage could not be read.

    Scope:
      * ``fr_id`` provided + the project is in P3 (per-FR TDD window):
        coverage is restricted to the FR's ``fr_module_traceability`` modules
        from SAB.json. Bringing an empty-stub module that other FRs will
        activate would otherwise score 0% on FR-01's gate — the per-FR
        scope reflects what this FR actually owns.
      * Otherwise: whole-project coverage. The remaining-after-other-FRs
        fraction is the entire system under test, so the whole-project
        number is the only honest signal that one piece isn't black.

    Why the original whole-project docstring was wrong under P3 TDD:
    taskq-cc's P3 run (Round 56) showed FR-01 hitting 95% on its own
    modules but being reported as a low-coverage FAIL because the SAB
    declared 10 phantom modules that other FRs will activate later. The
    gate treated their absence as an FR-01 defect — the per-FR scope
    is the correct answer for the per-FR rule (HR-08-style: a phase
    doesn't carry scope beyond its own deliverables).

    Round 25: the pytest invocation moved to
    ``core.quality_gate.test_suite_run.run_suite`` — this function used to
    hand-roll its own argv with NO test target at all, relying on pytest's
    rootdir discovery. That is the exact shape Round 22 removed from
    ``_advance_prechecks`` (a bare call also collects ``harness/tests/*``,
    since harness/ is vendored inside the project tree); the sibling here was
    missed then. It is also one of four call sites that each ran the suite
    separately inside a single advance-phase — see the module docstring of
    test_suite_run for the measurements.
    """
    from core.quality_gate.test_suite_run import run_suite

    result = run_suite(project)
    if not result.ran:
        return None
    if result.coverage is None:
        # No coverage number: preserve the pre-Round-25 contract — a green suite
        # with an unreadable report is 0.0, a red one is "not measured".
        return 0.0 if result.passed else None

    # Per-FR scope when both conditions hold: the caller is in a per-FR
    # gate (always passing fr_id) and the project is in P3 (per-FR TDD).
    # P4+ doesn't take fr_id here — it uses whole-project coverage.
    if fr_id is not None and _is_phase3_per_fr(project):
        _scope = _fr_module_paths(project, fr_id)
        if _scope is None:
            # SAB miss or no modules for this FR — fall through to
            # whole-project so the caller still gets a number.
            return result.coverage
        return _coverage_for_paths(project, _scope, fallback=result.coverage)
    return result.coverage


def _is_phase3_per_fr(project: Path) -> bool:
    """True iff state.json::current_phase is 3 (per-FR TDD window)."""
    try:
        from core.state_io import load_state
        return int(load_state(project, lenient=True).get("current_phase", 0)) == 3
    except (OSError, ValueError, TypeError):
        return False


def _fr_module_paths(project: Path, fr_id: str) -> "list[str] | None":
    """The list of `path/to/file.py` strings this FR owns, per SAB.

    Returns ``None`` when the SAB is missing or the FR has no declared
    modules — both cases mean the per-FR scope cannot be computed and the
    caller should fall through to whole-project.
    """
    sab_path = project / ".methodology" / "SAB.json"
    if not sab_path.is_file():
        return None
    try:
        sab = json.loads(sab_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    sab_root = sab.get("sab", sab) if isinstance(sab, dict) else {}
    fr_table = sab_root.get("fr_module_traceability") or {}
    fr_modules = fr_table.get(fr_id)
    if not fr_modules:
        return None
    # fr_module_traceability entries are strings OR lists of strings.
    if isinstance(fr_modules, str):
        fr_modules = [fr_modules]
    if not isinstance(fr_modules, list):
        return None
    # Each module name maps to a real path under src/. Convert dotted
    # name (`taskq_api.api.tasks`) to a path glob (`taskq_api/api/tasks.py`).
    return [m.replace(".", "/") + ".py" if not m.endswith(".py") else m for m in fr_modules]


def _coverage_for_paths(
    project: Path, paths: "list[str]", fallback: float
) -> float:
    """Recompute coverage from .coverage restricted to `paths`.

    Reads the on-disk `.coverage` data file using the coverage package
    directly. Modules in `paths` contribute to both numerator and
    denominator; modules outside `paths` do not contribute at all. If
    the data file is missing or the coverage package fails to read it,
    return `fallback` (the whole-project number the caller already got).
    """
    try:
        import coverage  # noqa: WPS433 — runtime import keeps the hot path cheap
    except ImportError:
        return fallback
    cov = coverage.Coverage(data_file=str(project / ".coverage"), data_suffix=None)
    try:
        cov.load()
    except Exception:
        return fallback
    try:
        data = cov.get_data()
        measured = sorted(data.measured_files())
    except Exception:
        return fallback
    # Resolve each FR module path to a full filesystem path. Coverage
    # reports absolute paths; the SAB names are dotted/project-relative.
    from core.utils.project_layout import ProjectLayout
    layout = ProjectLayout(project)
    _scope_files = set()
    for rel in paths:
        _scope_files.add(str((layout.active_src_dir / rel).resolve()))
        # Also accept source-rooted paths (no `active_src_dir` prefix)
        _scope_files.add(str((layout.root / rel).resolve()))
    _total_executed = 0
    _total_coverable = 0
    for f in measured:
        fp = Path(f).resolve()
        if str(fp) not in _scope_files:
            continue
        try:
            analysis = cov._analyze(f)
        except Exception:
            logging.getLogger(__name__).debug("coverage analyze failed for %s", f)
            continue
        executed = len(analysis.executed)
        missing = len(analysis.missing)
        _total_executed += executed
        _total_coverable += executed + missing
    if _total_coverable == 0:
        # No measured lines in scope — fall back to whole-project so the
        # caller still gets a sensible number (and so the test suite is
        # not silently 100% because nothing was in scope).
        return fallback
    return round(100.0 * _total_executed / _total_coverable, 2)
