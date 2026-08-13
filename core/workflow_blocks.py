"""workflow_blocks.py — where the pipeline stopped, written down (Round 48 站2).

`run-report` aggregates three sources: `.methodology/sessions_spawn.log`,
`.methodology/degradations.jsonl`, and the per-gate result files. Measured
2026-08-12, a workflow halt appears in none of them. The eight generated phase
workflows carry 125 terminal halt sites and every one of them returns a JS
object that reaches the conversation and is then gone. The single event in the
whole pipeline nobody records is the one that says where it stopped.

Two things become possible once it is recorded, and neither was before:

  - "this project blocks at P4 preflight every time" is a queryable fact
    rather than something a human has to remember across sessions;
  - "the repair worked" is checkable — station 5 re-runs and asks whether the
    SAME coordinate came back, instead of taking the repair's word for it.

WHY A SIGNATURE AND NOT A TIMESTAMP

The coordinate is `(phase, step, normalised message)`, hashed. Round 41 站3
paid for the alternative: taskq-api's FR-04 failed eight times with
byte-identical output across 3h11m for $6.02, and the ledger held four lines
for the whole run, none of them about the repetition. A record keyed on
anything that changes per run — a timestamp, a pid, a run id — makes a repeat
look like a new event, which is precisely the fact worth seeing.

Numbers are stripped from the message before hashing. "did not PASS in 3
attempts" and "did not PASS in 5 attempts" are the same block; the attempt
count is the retry budget, not the defect.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
import time
from pathlib import Path

from core.fault_owner import Owner

__all__ = [
    "LEDGER_RELPATH",
    "UnknownBlockError",
    "block_signature",
    "record_block",
    "read_blocks",
    "open_blocks",
    "harness_owned_open_blocks",
    "resolve_block",
]

# Beside degradations.jsonl, and for the same reason Round 27 站3 moved that
# one out of .sessi-work: a cross-run audit record has to outlive the work
# directory it describes.
LEDGER_RELPATH = ".methodology/workflow_blocks.jsonl"

_DIGITS = re.compile(r"\d+")
_WS = re.compile(r"\s+")


class UnknownBlockError(LookupError):
    """A receipt was offered for a block that was never recorded.

    Round 45's rule at the ledger layer: a verdict must not outlive — or
    precede — its proof. Accepting "signature X is fixed" for a signature
    nobody wrote would let a repair close a block that never happened, which
    is the one way this ledger could start lying about convergence.
    """


def block_signature(phase: int, step: str, message: str) -> str:
    """A stable coordinate for "the pipeline stopped HERE, for THIS reason"."""
    normalised = _WS.sub(" ", _DIGITS.sub("N", message or "")).strip().lower()
    raw = f"{phase}\x1f{(step or '').strip().lower()}\x1f{normalised}"
    return "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _ledger_path(project: "str | Path") -> Path:
    return Path(project) / LEDGER_RELPATH


def record_block(
    project: "str | Path",
    *,
    phase: int,
    step: str,
    owner: str,
    message: str,
    exit_code: "int | None" = None,
    evidence: str = "",
) -> str:
    """Append one halt and return its signature.

    Unlike `record_degradation`, this one does NOT swallow write failures. A
    degradation that could not be recorded still leaves a run that completed;
    a halt that could not be recorded leaves the repair loop with no subject,
    and station 5 would then have nothing to reconcile against.
    """
    signature = block_signature(phase, step, message)
    path = _ledger_path(project)

    # Round 48 站5 — the re-run reconciliation, done HERE rather than at the
    # start of the next run.
    #
    # "The repair worked" is a claim. The check is whether the SAME coordinate
    # comes back, and the cheapest place to notice that is the moment it does:
    # a fresh record whose signature already has a resolution behind it. Doing
    # it in the workflow's Phase Cursor instead would cost a dispatch on every
    # run, in a sandbox with no filesystem, to learn something only the ledger
    # knows — and would learn it on the runs where nothing is wrong.
    prior = _latest_by_signature(read_blocks(project)).get(signature)
    recurred = bool(prior and prior.get("resolved"))

    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "ts": time.time(),
        "signature": signature,
        "phase": phase,
        "step": step,
        "owner": owner,
        "exit_code": exit_code,
        "message": (message or "")[:2000],
        "evidence": evidence,
        "resolved": False,
        "recurred_after_resolution": recurred,
    }
    if recurred and prior is not None:
        entry["previous_resolution"] = str(prior.get("resolution", ""))[:400]
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    print(
        f"[BLOCK] phase {phase} / {step} — owner={owner} signature={signature}",
        file=sys.stderr,
    )
    if recurred:
        print(
            f"[BLOCK] this coordinate was marked RESOLVED before "
            f"({entry['previous_resolution']}) and has come back. The repair "
            f"was recorded, not verified — do not repeat it unchanged.",
            file=sys.stderr,
        )
    return signature


def read_blocks(project: "str | Path") -> list[dict]:
    """Every row, oldest first. A malformed line is reported, never skipped
    in silence — Round 30's rule: an unreadable ledger is not an empty one."""
    path = _ledger_path(project)
    if not path.exists():
        return []
    rows: list[dict] = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as exc:
            print(
                f"[WARN] {LEDGER_RELPATH}:{lineno} is not valid JSON ({exc}) — "
                f"the row is not counted and not silently treated as absent",
                file=sys.stderr,
            )
    return rows


def _latest_by_signature(rows: list[dict]) -> dict[str, dict]:
    """Last write wins — a resolution recorded after a block supersedes it."""
    latest: dict[str, dict] = {}
    for row in rows:
        sig = row.get("signature")
        if sig:
            latest[sig] = row
    return latest


def open_blocks(project: "str | Path") -> list[dict]:
    """Blocks whose latest record is still unresolved."""
    return [
        row
        for row in _latest_by_signature(read_blocks(project)).values()
        if not row.get("resolved")
    ]


def harness_owned_open_blocks(project: "str | Path") -> list[dict]:
    """Open blocks the framework attributed to its OWN code.

    Its own query rather than a filter at each call site: this is the one
    subset with a different route (the harness repair workflow, never a fix
    agent pointed at the project), and a predicate every reader re-derives is
    how two readers end up disagreeing about what "harness-owned" means.
    """
    return [row for row in open_blocks(project) if row.get("owner") == Owner.HARNESS]


def resolve_block(
    project: "str | Path", signature: str, *, resolution: str
) -> None:
    """Record that a previously-seen block is gone, with what closed it.

    Appends rather than rewrites: the ledger stays append-only, so the history
    of "blocked, repaired, blocked again" survives instead of collapsing into
    whatever the last writer believed.
    """
    rows = read_blocks(project)
    prior = _latest_by_signature(rows).get(signature)
    if prior is None:
        raise UnknownBlockError(
            f"no block with signature {signature!r} in {LEDGER_RELPATH} — "
            f"refusing to record a resolution for a halt that was never seen"
        )
    path = _ledger_path(project)
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "ts": time.time(),
        "signature": signature,
        "phase": prior.get("phase"),
        "step": prior.get("step"),
        "owner": prior.get("owner"),
        "exit_code": prior.get("exit_code"),
        "message": prior.get("message", ""),
        "evidence": prior.get("evidence", ""),
        "resolved": True,
        "resolution": resolution,
    }
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
