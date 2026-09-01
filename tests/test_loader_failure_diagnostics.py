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
        "lastFailReason = 'relay_frame_broken: got=' + text.slice(0, 60)",
        "lastFailReason = 'prefix_mismatch: got=' + anchorAt.slice(0, 40)",
    ],
)
def test_every_failure_branch_sets_the_reason(marker):
    """Each failure branch must set lastFailReason before `continue`,
    or that branch's halts would silently fall back to the 'unknown' default.
    """
    rendered = _rendered()
    assert marker in rendered, (
        f"expected failure branch to set lastFailReason via {marker!r} — "
        "this branch would leave 'unknown' in the halt message instead of "
        "its real cause"
    )


def test_no_retry_branch_can_be_added_without_a_reason():
    """The list above names branches; this counts them.

    Round 86 站2 added a fifth branch and changed a fourth, and only the
    CHANGE was caught — a purely additive branch would have shipped with
    `lastFailReason` still reading 'unknown', which is the whole failure
    this module exists to prevent. Every `continue` inside the retry loop
    must be preceded by an assignment to lastFailReason.
    """
    rendered = _rendered()
    start = rendered.index("for (let attempt = 1;")
    loop = rendered[start:]
    branches = loop.count("      continue\n")
    reasons = loop.count("      lastFailReason = ")
    assert branches == reasons, (
        f"{branches} `continue` statements in loadFileViaPython's retry loop "
        f"but {reasons} lastFailReason assignments — a branch that retries "
        f"without recording why leaves 'unknown' in the terminal message"
    )
    assert branches >= 5, (
        f"only {branches} retry branches found; the loop is expected to carry "
        "at least the five it had at Round 86 (agent threw, ERROR_LOAD_FAILED, "
        "too short, relay frame broken, prefix mismatch)"
    )
