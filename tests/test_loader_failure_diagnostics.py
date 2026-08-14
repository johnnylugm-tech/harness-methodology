"""2026-08-14 — a `LOADER_FAILED_AFTER_N_ATTEMPTS` halt used to discard the
reason for every attempt: `loadFileViaPython` (js_blocks.py) already computed
a specific failure cause per attempt (agent() threw, ERROR_LOAD_FAILED, too
short, prefix mismatch) and `log()`'d it, but the final return string carried
only the file path — the halt payload built from that string (see
`spec_phase1.py`'s `c.slice(0, 200)` at the peer-review load gate) gave no
clue whether the failure was a content defect or an infrastructure
interruption (observed in production: a background workflow's sub-agent got
"Your computer went to sleep mid-response" on all 3 attempts, and diagnosing
that required reading raw per-agent transcripts instead of the halt message
itself). This pins that the last attempt's reason is threaded into the
returned string, purely a diagnostics change — no retry/pass-fail logic.
"""
from __future__ import annotations

import pytest

pytestmark = [pytest.mark.core]


def _rendered() -> str:
    from scripts.workflowgen import js_blocks

    return js_blocks.render_load_file_via_python()


def test_last_fail_reason_is_declared_and_returned():
    rendered = _rendered()
    assert "let lastFailReason = 'unknown'" in rendered, (
        "lastFailReason accumulator is missing — the loop has no place to "
        "carry a per-attempt failure cause forward"
    )
    assert (
        "'ERROR: LOADER_FAILED_AFTER_' + maxAttempts + '_ATTEMPTS: ' + relPath"
        " + ' (last: ' + lastFailReason + ')'"
    ) in rendered, (
        "the terminal return string no longer appends the last failure "
        "reason — a loader halt would go back to being undiagnosable from "
        "its own message"
    )


@pytest.mark.parametrize(
    "marker",
    [
        "lastFailReason = 'agent_threw: '",
        "lastFailReason = 'ERROR_LOAD_FAILED'",
        "lastFailReason = 'too_short(len=' + text.length + '): '",
        "lastFailReason = 'prefix_mismatch: got=' + text.slice(0, 40)",
    ],
)
def test_every_failure_branch_sets_the_reason(marker):
    """Each of the 4 failure branches must set lastFailReason before `continue`,
    or that branch's halts would silently fall back to the 'unknown' default.
    """
    rendered = _rendered()
    assert marker in rendered, (
        f"expected failure branch to set lastFailReason via {marker!r} — "
        "this branch would leave 'unknown' in the halt message instead of "
        "its real cause"
    )
