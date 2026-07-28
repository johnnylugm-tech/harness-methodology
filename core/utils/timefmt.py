"""Round 24 站3 — one time base for every artifact the harness writes.

A single P1-P8 run used to leave three mutually unalignable clocks behind:

  .methodology/sessions_spawn.log   datetime.now().isoformat()  — LOCAL, no offset
  .methodology/last_block.md        datetime.now().isoformat()  — LOCAL, no offset
  .methodology/state.json           datetime.now(timezone.utc)  — UTC, offset present
  .methodology/fr_progress.json     datetime.now(timezone.utc)  — UTC, offset present
  .methodology/gate_timestamps.jsonl  time.time()               — epoch float

None of the three is labelled, and two of them look identical while differing
by the host's UTC offset. Reading the run-all-by-workflow P1-P8 artifacts
during the Round 24 audit, that mismatch produced a wrong conclusion — a
sessions_spawn.log line at "15:44" was compared against a state.json entry at
"07:43+00:00" and read as an eight-hour stall. The real gap was 1h18m. An
observability layer whose own timestamps cannot be lined up answers questions
incorrectly rather than not answering them, which is worse.

Every machine-readable timestamp the harness writes now comes from
`utc_now_iso()`: UTC, ISO 8601, offset always present. `time.time()` epoch
floats stay where a reader already depends on them (gate_timestamps.jsonl's
`ts`), with an `iso` field added alongside rather than a format swap that
would break existing readers.

Enforced by tests/test_timestamp_convention.py, which has no allowlist —
the fix is always one call.
"""
from __future__ import annotations

from datetime import datetime, timezone

__all__ = ["utc_now_iso"]


def utc_now_iso() -> str:
    """Current UTC time, ISO 8601, always carrying the +00:00 offset.

    Use for every timestamp written into an artifact. A local-time stamp with
    no offset cannot be compared with anything, and a naive UTC stamp is
    indistinguishable from one.
    """
    return datetime.now(timezone.utc).isoformat()
