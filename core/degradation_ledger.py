"""Degradation ledger (Round 13 站1) — a visible trail for graceful
degradation.

Before this module, a "best-effort, fall back to a default" path had no
place to leave a trace beyond a one-off stdout print (if any) that scrolls
past in a long run and is gone. This gives every degradation two things at
once: a stderr line for whoever is watching the run live, and an append-
only JSONL record for whoever is debugging it after the fact — the
question this answers is "what silently happened differently than the
happy path expected, this run?" (see docs/ERROR_HANDLING.md).

Not for BLOCK-grade failures (those raise / return a failing exit code) or
for genuinely inconsequential events (a plain print is enough for those).
Use this when a fallback changes downstream behavior in a way a debugger
would want to know about, even though the run continues.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

# Round 27 站3: was ".sessi-work/degradations.jsonl". .sessi-work is gitignored
# and is cleaned between phases, so the ledger did not survive the run it
# described — taskq-plus's log holds 7 turn-budget exhaustions and the ledger
# was simply absent afterwards, leaving no way to tell "nothing was written"
# apart from "it was written and then removed". A cross-run audit record has to
# outlive the work directory it was recording, so it lives beside the other
# .methodology artefacts a consuming project commits.
LEDGER_RELPATH = ".methodology/degradations.jsonl"

# Warn once per (component, what) per process — a hot loop hitting the same
# fallback shouldn't spam stderr once per iteration (same rationale as
# harness_config.py's _warned_unknown).
_warned: set[tuple[str, str]] = set()


def _to_stderr(line: str) -> None:
    """Put `line` on stderr, or give up silently. The one thing that cannot
    fail here is this module's promise not to raise.

    Round 83 站2. `record_degradation` has said "Never raises" since Round 13
    站1 and did not: its `[DEGRADED]` print sat OUTSIDE the try, and the
    `except OSError` handler's own print sat inside a handler that could not
    catch itself. `BrokenPipeError` is an `OSError`, so a run whose stderr
    reader has gone away — a pipe into `head`, a killed tee, a closed terminal
    — took the whole command down from inside the function that exists to
    record that something went wrong quietly.

    Not hypothetical: taskq-new's committed crash bundle
    `.methodology/crash/crash_20260821T211052Z_33516.json` is
    `BrokenPipeError: [Errno 32] Broken pipe` at this module's line 77, with
    `argv: ['advance-phase', '--completed', '2', ...]`. A phase transition was
    ended by its own logging.

    `ValueError` joins `OSError` because a closed (rather than broken) stream
    raises "I/O operation on closed file", which is the same event with a
    different exception class and the same right answer.

    Deliberately NOT a broader `except Exception`: this takes a `str` and
    calls `print`, so the only failures available to it are stream failures,
    and swallowing more than that would hide a bug in this module rather than
    a condition in the environment.
    """
    try:
        print(line, file=sys.stderr)
    except (OSError, ValueError):
        pass


def record_degradation(
    project: "str | Path", component: str, what: str, why: str = "",
    data: "dict | None" = None, *, owner: str = "unknown",
) -> None:
    """Record a graceful degradation: print a `[DEGRADED]` line to stderr
    (once per component+what per process) and append a JSON record to
    `<project>/.methodology/degradations.jsonl`. Never raises — a failure
    to write the ledger must not be worse than the degradation it was
    trying to record.

    "Never raises" became true in Round 83 站2; before that it was a claim
    about the `try` around the file write only, and the two stderr prints
    beside it could each end the run (see `_to_stderr`). The order below is
    load-bearing for the same reason: whatever happens to stderr, the JSONL
    append still runs.

    `data` is an optional machine-readable payload, written under its own key.
    Round 41 站3 added it because the ledger acquired its first PROGRAMMATIC
    reader: a step that has failed identically before must be recognised as
    such across process boundaries, and `component`/`what`/`why` are free
    prose. Recovering a failure signature by parsing an English sentence is how
    a checker starts agreeing with its author instead of with the data
    (Round 19 站1). Omitted from the record when None, so every existing
    caller's entry is byte-identical to what it wrote before.

    `owner` — whose tree has to change (core/fault_owner.py's vocabulary).
    Round 50 站4. Round 48 built `classify_fault` to answer this after the
    fact, from the text of a halt. Measured 2026-08-13 against nine real
    messages from a full P1-P8 run: nine UNKNOWNs. Six of the nine were not
    halt messages at all — they were rows THIS function wrote, and no rule
    table can recover from prose what the call site knew and did not say.

    So the answer is written here, by the site that knows it, and Round 48's
    reader becomes the last resort rather than the first. `unknown` is a real
    answer and stays available (Round 48's rule: never rounded down to
    PROJECT) — what `tests/test_degradation_owner.py` forbids is leaving it
    to the default. A site that has not decided is a site nobody has read,
    and those are exactly the ones that produced the nine.
    """
    key = (component, what)
    if key not in _warned:
        _warned.add(key)
        suffix = f" ({why})" if why else ""
        # Round 83 站2: the live line is a courtesy to whoever is watching; the
        # JSONL below is the record. Before this, a stderr that had gone away
        # took the record with it — the append never ran, so the one artefact
        # that outlives the run was lost in exactly the runs worth debugging.
        _to_stderr(f"[DEGRADED] {component}: {what}{suffix}")
    try:
        ledger_path = Path(project) / LEDGER_RELPATH
        ledger_path.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "ts": time.time(),
            "component": component,
            "what": what,
            "why": why,
            "owner": owner,
        }
        if data is not None:
            entry["data"] = data
        with open(ledger_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError as exc:
        _to_stderr(f"[WARN] failed to write degradation ledger entry: {exc}")


def read_degradations(project: "str | Path") -> list[dict]:
    """Every ledger entry for *project*, oldest first; [] when there is none.

    Round 41 站3 — the ledger has been append-only in both senses since Round 13
    站1: written by many callers, read by none. It was designed to outlive the
    run it describes (Round 27 站3 moved it out of the ephemeral .sessi-work
    for exactly that reason), which makes it the one place a per-step process
    can learn what previous processes already tried.

    Malformed lines are skipped rather than raising: a debugging trail that
    can crash the run it is describing would be worse than one with a hole,
    and a partially-written last line is the normal shape of a killed process.
    """
    ledger_path = Path(project) / LEDGER_RELPATH
    try:
        raw = ledger_path.read_text(encoding="utf-8")
    except OSError:
        return []
    entries: list[dict] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(parsed, dict):
            entries.append(parsed)
    return entries
