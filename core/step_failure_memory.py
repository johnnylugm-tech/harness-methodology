"""Round 41 站3 — what the framework already tried, remembered across processes.

`cmd_run_fr_step` adapts to failure in three ways, and all three forget:

    _turn_budget_escalated   double the turn ceiling, once per step
    _wallclock_escalated     double the wall-clock budget, once per step
    no_progress_count        abort after two identical fix rounds

Every one is a local of that function. The execution model is one process per
step invocation. So each retry meets its predecessor's failure as a first
occurrence, and the framework's whole capacity to notice repetition lasts
exactly as long as a single command.

taskq-api's FR-04 priced it. Between 06:51 and 09:14 on 2026-08-06, TDD-GREEN
then TDD-IMPROVE failed EIGHT times with byte-identical output —
`subtype=success API Error: Stream idle timeout - no chunks received` — for
$6.02 across 3h11m. `.methodology/degradations.jsonl` holds four lines for the
entire run and not one of them mentions the repetition.

TWO BOUNDS, BOTH DERIVED
------------------------
How many identical attempts are enough: `_STEP_RETRY_ATTEMPTS`, the number the
in-process loop already spends before giving up. Not a new threshold — the same
one, applied across processes instead of within one.

When the refusal lifts: when the tree changes. An identical prompt against an
identical tree cannot produce a different answer, which is the reasoning
`fr_code_changed_since_last_gate1` and `run_suite`'s fingerprint already run
on. Any repair — a fix, a revert, a config edit — re-opens the step; a blind
re-run does not. Without that half, this module would trade an unbounded retry
loop for an unbreakable stop, which is the same defect facing the other way.

The signature is the failure's exact text. Nothing is normalised away: the
measured case was byte-identical, and refusing only on provable repetition is
the conservative direction. A failure that differs in any character is new
information and gets its own attempt.
"""

from __future__ import annotations

import hashlib
import subprocess  # nosec B404
from pathlib import Path

from core.degradation_ledger import read_degradations, record_degradation

__all__ = [
    "failure_signature",
    "tree_fingerprint",
    "record_step_failure",
    "repeated_failure",
    "LEDGER_WHAT",
]

# The `what` every step-failure record carries. One string, one home: the
# writer and the reader below are the only two users, and a second copy of it
# is how a registry-keyed reader goes quietly blind.
LEDGER_WHAT = "step dispatch failed"


def failure_signature(result: dict) -> str:
    """A stable id for "this exact failure", from the dispatch result.

    Includes the error class so that two different failures which happen to
    print the same text (a timeout and a turn-budget kill both say very little)
    are not merged.
    """
    material = f"{result.get('error_class') or result.get('status') or ''}\n{result.get('output') or ''}"
    return hashlib.sha256(material.encode("utf-8", errors="replace")).hexdigest()[:16]


def tree_fingerprint(project: "str | Path") -> str:
    """A stable id for "the tree as it is right now": HEAD plus the dirt.

    Both halves matter. HEAD alone would call a working tree with uncommitted
    repairs unchanged; the porcelain alone would miss a commit that landed. A
    project that is not a git repository fingerprints as "" — every attempt
    then looks like a fresh tree, which keeps this module from blocking a
    project it cannot describe.
    """
    root = Path(project)
    parts: list[str] = []
    for cmd in (["git", "rev-parse", "HEAD"], ["git", "status", "--porcelain"]):
        try:
            proc = subprocess.run(  # nosec B603
                cmd, capture_output=True, text=True, cwd=str(root), timeout=30,
            )
        except (OSError, subprocess.SubprocessError):
            return ""
        if proc.returncode != 0 and cmd[1] == "status":
            return ""
        parts.append(proc.stdout)
    return hashlib.sha256("\n".join(parts).encode("utf-8", errors="replace")).hexdigest()[:16]


def record_step_failure(
    project: "str | Path", fr_id: str, step: str, result: dict, fingerprint: str,
) -> None:
    """Write one step failure to the ledger, keyed for the reader below.

    Round 72 站3: the owner comes from the class this row already carries.
    `_classify_dispatch_error` decides it, `why` prints it and `data` stores
    it — and this call passed `owner="unknown"` beside all three. Measured on
    taskq-new: 37 rows own nobody, twenty-six of them reading "INFRA" in their
    own `why`. The table is `core.fault_owner.OWNER_BY_ERROR_CLASS`, next to
    the exit-code table it has to agree with.
    """
    from core.fault_owner import owner_of_error_class

    error_class = result.get("error_class") or result.get("status") or ""
    record_degradation(
        project,
        component=f"run-fr-step:{step}",
        what=LEDGER_WHAT,
        why=f"{fr_id} {step}: {error_class or 'failure'}",
        data={
            "fr_id": fr_id,
            "step": step,
            "signature": failure_signature(result),
            "tree": fingerprint,
            "error_class": error_class,
        }, owner=owner_of_error_class(error_class)
    )


def repeated_failure(
    project: "str | Path", fr_id: str, step: str, fingerprint: str, limit: int,
) -> dict | None:
    """The record of a failure already seen `limit` times here, or None.

    "Here" is this (FR, step) at this tree fingerprint. Returns the most recent
    matching record so the caller can name the signature in its refusal — a
    stop that does not say what it is stopping on is the same dead end as no
    stop at all (Round 24: a block that does not say what to do is half a
    block).

    An empty fingerprint (not a git repository) never matches, so this can only
    ever refuse a repetition it can actually prove.
    """
    if not fingerprint:
        return None
    counts: dict[str, int] = {}
    latest: dict[str, dict] = {}
    for entry in read_degradations(project):
        if entry.get("what") != LEDGER_WHAT:
            continue
        data = entry.get("data")
        if not isinstance(data, dict):
            continue
        if (data.get("fr_id") != fr_id or data.get("step") != step
                or data.get("tree") != fingerprint):
            continue
        signature = str(data.get("signature") or "")
        if not signature:
            continue
        counts[signature] = counts.get(signature, 0) + 1
        latest[signature] = data
    for signature, seen in counts.items():
        if seen >= limit:
            return {**latest[signature], "seen": seen}
    return None
