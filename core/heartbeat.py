"""Round 24 站5a — a liveness trail for long unattended runs. PARTIAL SOLUTION.

READ THIS BEFORE TRUSTING IT
----------------------------
This records when the harness last did something. It CANNOT see an agent that
is alive but not calling the harness — thinking, waiting on an LLM response,
or stuck inside a sub-agent dispatch. The workflow runtime exposes no
heartbeat API, so the harness has no view into that. A stale heartbeat means
"no harness command has completed recently", which is evidence of a stall but
not proof, and a fresh heartbeat is not proof of health.

The gap it does close is the one that actually bit. In the run-all-by-workflow
P1-P8 validation run, Phase 6 reached `Gate4 PASS 97.4` and then made no
further progress for 1h18m. Nothing noticed. It surfaced only because 老闆
asked, and the agent's own liveness call at that moment was "the journal has
had no new entry for 3 minutes, treat it as dead" — a number invented on the
spot with no mechanism behind it. run-all.js stretches a single unattended
launch to ~9h, which widens exactly this window.

`harness_cli.py::_dispatch` touches the file after every subcommand, success
or failure — the single funnel every CLI entry passes through, so a new
subcommand cannot forget to participate. `doctor` reads it and WARNs past a
threshold, printing the last command so the reader knows where progress
stopped.
"""
from __future__ import annotations

import json
from pathlib import Path

HEARTBEAT_RELPATH = ".methodology/heartbeat.json"

# A phase can legitimately spend a long time inside one agent dispatch (P3's
# per-FR TDD loop routinely runs 20-40 min per FR on the observed run). The
# threshold is deliberately well above that: this reports "nothing at all has
# happened for a long time", not "this step is slow".
STALL_THRESHOLD_MINUTES = 45


def record_heartbeat(project: "str | Path", command: str) -> None:
    """Record that `command` just finished. Never raises.

    Best-effort by construction: a heartbeat that fails to write must not turn
    a successful command into a failed one, and the failure is self-reporting
    (the next doctor run sees a stale file).
    """
    from core.utils.timefmt import utc_now_iso

    try:
        path = Path(project) / HEARTBEAT_RELPATH
        if not path.parent.is_dir():
            return  # not an initialised methodology project — nothing to track
        payload = {"command": command, "utc": utc_now_iso()}
        path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    except (OSError, TypeError, ValueError):
        return


def read_heartbeat(project: "str | Path") -> dict | None:
    """The last recorded heartbeat, or None if absent/unreadable."""
    try:
        path = Path(project) / HEARTBEAT_RELPATH
        if not path.is_file():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def minutes_since(heartbeat: dict, now_iso: str) -> float | None:
    """Minutes between the heartbeat and `now_iso`, or None if unparseable.

    `now_iso` is passed in rather than read from the clock so the caller (and
    its tests) control the reference point.
    """
    from datetime import datetime

    try:
        then = datetime.fromisoformat(str(heartbeat.get("utc", "")))
        now = datetime.fromisoformat(now_iso)
    except (TypeError, ValueError):
        return None
    if then.tzinfo is None or now.tzinfo is None:
        # Round 24 站3 made every artifact timestamp offset-aware; a naive one
        # here is a pre-migration file whose zone is unknown. Refusing to
        # subtract is correct — guessing is what produced this round's own
        # eight-hour misreading.
        return None
    return (now - then).total_seconds() / 60.0
