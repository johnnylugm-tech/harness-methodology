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

LEDGER_RELPATH = ".sessi-work/degradations.jsonl"

# Warn once per (component, what) per process — a hot loop hitting the same
# fallback shouldn't spam stderr once per iteration (same rationale as
# harness_config.py's _warned_unknown).
_warned: set[tuple[str, str]] = set()


def record_degradation(project: "str | Path", component: str, what: str, why: str = "") -> None:
    """Record a graceful degradation: print a `[DEGRADED]` line to stderr
    (once per component+what per process) and append a JSON record to
    `<project>/.sessi-work/degradations.jsonl`. Never raises — a failure
    to write the ledger must not be worse than the degradation it was
    trying to record.
    """
    key = (component, what)
    if key not in _warned:
        _warned.add(key)
        suffix = f" ({why})" if why else ""
        print(f"[DEGRADED] {component}: {what}{suffix}", file=sys.stderr)
    try:
        ledger_path = Path(project) / LEDGER_RELPATH
        ledger_path.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "ts": time.time(),
            "component": component,
            "what": what,
            "why": why,
        }
        with open(ledger_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError as exc:
        print(f"[WARN] failed to write degradation ledger entry: {exc}", file=sys.stderr)
