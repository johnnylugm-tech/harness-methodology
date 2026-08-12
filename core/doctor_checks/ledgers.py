"""doctor checks: the run's own ledgers, read back after the fact.

Split out of core/doctor.py in R49-B. Four checks over the files a run leaves
behind — the heartbeat, the spawn log, crash bundles, and the workflow-block
ledger Round 48 added. What they have in common is their standing: every one
is a POST-HOC reading of evidence the run wrote about itself, so none of them
can be the thing that decides anything. They report, and a reader decides.

`_check_spawn_log_authenticity` states that most explicitly in its own
docstring, and it is the reason the four sit together: a check that grades a
ledger is one edit away from being a check that trusts one.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from core.doctor_checks import Finding
from core.errors import CRASH_DIR_RELPATH
from core.utils.project_layout import ProjectLayout

def _check_heartbeat(project: Path) -> list[Finding]:
    """WARN when no harness command has completed for a long time.

    Round 24 站5a. In the run-all-by-workflow P1-P8 run, Phase 6 reached Gate 4
    PASS and then made no progress for 1h18m with nothing noticing; the only
    liveness judgement available at the time was an improvised "the journal has
    had no new entry for 3 minutes".

    WARN, never ERROR, and the message says what it cannot see: a stale
    heartbeat is evidence of a stall, not proof of one (a legitimately long
    single dispatch looks identical), and a fresh one is not proof of health.
    """
    from core.heartbeat import (
        STALL_THRESHOLD_MINUTES,
        minutes_since,
        read_heartbeat,
    )
    from core.utils.timefmt import utc_now_iso

    beat = read_heartbeat(project)
    if beat is None:
        return []  # never run, or pre-migration project — not a finding
    idle = minutes_since(beat, utc_now_iso())
    if idle is None or idle < STALL_THRESHOLD_MINUTES:
        return []
    return [Finding(
        "heartbeat", "WARN",
        f"no harness command has completed for {idle:.0f} min "
        f"(threshold {STALL_THRESHOLD_MINUTES}); last was "
        f"{beat.get('command', '?')!r} at {beat.get('utc', '?')}. "
        f"This detects a stall in the HARNESS layer only — an agent thinking, "
        f"waiting on an LLM, or stuck inside a sub-agent dispatch is invisible "
        f"here, and a long single dispatch looks the same as a stall. Check the "
        f"workflow's own progress before concluding the run is dead."
    )]


# Fields core/agent_spawner.py lifts from the `claude -p --output-format json`
# envelope onto every completed dispatch (Round 14 站0). Absent on TIMEOUT,
# non-zero exit, and on lines written before that station — so their absence
# alone is not suspicious; it is only a signal alongside the session_id shape.
_ENVELOPE_FIELDS = ("total_cost_usd", "num_turns", "duration_api_ms", "usage")

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE
)


def _check_spawn_log_authenticity(layout: ProjectLayout) -> list[Finding]:
    """Flag sessions_spawn.log entries that no dispatch could have produced.

    This is forensics, NOT enforcement, and the distinction is the point. The
    log is written by the agent whose work it documents, it is gitignored, and
    appending to it costs one Bash call — so anyone able to forge an entry is
    equally able to forge the envelope fields this check reads. Round 21 站3
    removed it from PhaseTruthVerifier's score for exactly that reason; it must
    never become a gate term again.

    What it is good for is noticing after the fact. taskq's Phase 6 carried six
    entries with `session_id` values like "round3-da-architect-fr01", no
    envelope, duration_seconds 0, whole-second timestamps one apart, and the
    task field holding a conclusion rather than a prompt — written 45 seconds
    before the first Gate 4 PASS commit, with roles and phase matching precisely
    what the (then-scored) A/B branch looked for.

    Signal: a `complete`/`COMPLETED` entry whose session_id is neither empty nor
    a UUID AND which carries no envelope field at all. Both halves are required:
    real pre-Round-14 lines lack the envelope, and real failure lines lack both
    the envelope and a session_id.
    """
    log_path = layout.sessions_spawn_log
    if not log_path.is_file():
        return []
    try:
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        return [Finding("spawn-log", "INFO",
                        f"could not read {log_path.name}: {exc}")]

    suspect: list[str] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue  # malformed lines are a separate concern
        if not isinstance(entry, dict):
            continue
        if str(entry.get("status", "")).lower() not in ("complete", "completed"):
            continue
        session_id = str(entry.get("session_id") or "").strip()
        if not session_id or _UUID_RE.match(session_id):
            continue
        if any(entry.get(f) is not None for f in _ENVELOPE_FIELDS):
            continue
        suspect.append(
            f"{entry.get('timestamp', '?')} role={entry.get('role', '?')} "
            f"phase={entry.get('phase', '?')} session_id={session_id!r}"
        )

    if not suspect:
        return []
    shown = "; ".join(suspect[:3])
    more = f" (+{len(suspect) - 3} more)" if len(suspect) > 3 else ""
    return [Finding(
        "spawn-log", "WARN",
        f"{len(suspect)} sessions_spawn.log entr(ies) report completion but carry "
        f"neither a dispatch session id nor any dispatch envelope, so no dispatch "
        f"produced them: {shown}{more}. "
        f"Nothing scores this log (Round 21 站3), so these changed no verdict — but "
        f"hand-written entries mean something was recorded as done that was not run. "
        f"Fix: identify what those entries claim and confirm it actually happened."
    )]


def _check_crash_bundles(project: Path) -> list[Finding]:
    from core.errors import crash_bundle_paths
    untriaged = [
        p for p in crash_bundle_paths(project)
        if not p.with_name(p.name + ".triaged").exists()
    ]
    if not untriaged:
        return []
    return [Finding(
        "crash-bundles", "WARN",
        f"{len(untriaged)} untriaged harness-methodology crash bundle(s) in "
        f"{CRASH_DIR_RELPATH}/ — harness-methodology crashed on its own bug at "
        f"least once. Triage: harness_cli.py crash-triage --project {project} "
        f"(add --open-cr to file a CR-BUG ticket in the harness repo)")]


def _check_open_workflow_blocks(project: Path) -> list[Finding]:
    """Halts nobody has closed (Round 48 站2).

    A harness-owned block is reported at WARN rather than ERROR for the same
    reason `_check_crash_bundles` is: it is a true statement about a run that
    already ended, not a defect in the tree being inspected right now. What
    makes it worth a line at all is that it names the repair route — a block
    the framework attributed to ITSELF must not be handed to a fix agent
    pointed at the project's code.

    Deliberately narrow: ONLY harness-owned blocks are reported. `run-report`
    already lists every open block with its owner and repeat count, and a
    second reader printing the same rows would be one fact in two places.
    What earns a doctor line is the routing consequence, which run-report can
    describe but doctor is the command people run when something is wrong.
    """
    from core.workflow_blocks import LEDGER_RELPATH, harness_owned_open_blocks

    harness_owned = harness_owned_open_blocks(project)
    if not harness_owned:
        return []
    where = ", ".join(f"P{r.get('phase')}/{r.get('step')}" for r in harness_owned[:5])
    returned = [r for r in harness_owned if r.get("recurred_after_resolution")]
    findings = [Finding(
        "workflow-blocks", "WARN",
        f"{len(harness_owned)} unresolved harness-owned block(s) in "
        f"{LEDGER_RELPATH} ({where}) — the framework attributed these to its "
        f"own code, so they are not project quality failures. Run the harness "
        f"repair workflow; do not dispatch a fix agent at this project. Full "
        f"list: harness_cli.py run-report --project {project}")]
    if returned:
        # Round 48 站5: ERROR, not WARN. An unresolved block is a run that
        # stopped; a block that was marked resolved and came back at the same
        # coordinate is a recorded verdict contradicted by the next run.
        findings.append(Finding(
            "workflow-blocks", "ERROR",
            f"{len(returned)} block(s) marked RESOLVED have returned at the "
            f"same coordinate — a repair was recorded and did not hold. Do not "
            f"re-run the same repair: read the previous_resolution field in "
            f"{LEDGER_RELPATH} first"))
    return findings
