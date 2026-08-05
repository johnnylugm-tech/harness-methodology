"""Read-only cross-file state consistency checks (`harness_cli.py doctor`).

Framework state spans several files with no transaction across them:
`.methodology/state.json` (authoritative), `quality_manifest.json`,
`trace/attestation.json`, and the CLAUDE.md auto status block. The P8→9
incident showed what half-state looks like: state.json advanced while
HANDOVER.md was never regenerated. doctor detects such states and the
interruption evidence StateTransaction leaves behind (journal + tmps).

Fail-closed by design: doctor only REPORTS — it never auto-repairs.
An auto-repair path would itself become a fabrication surface.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from core.errors import CRASH_DIR_RELPATH
from core.fsm.fsm import VALID_FSM_STATES
from core.phase_topology import VALID_PHASES
from core.quality_gate.gate1_evidence import (
    EVIDENCE_SOURCE_SKIP,
    GATE_TIMESTAMPS_FILE,
    verify_finalize_evidence,
)
from core.state_io import StateCorruptError, load_quality_manifest, load_state
from core.utils.project_layout import ProjectLayout

_CLAUDE_BLOCK_PHASE = re.compile(r"Phase:\s*\*\*(\d+)")
# Durable phase-advance record: every successful advance-phase lands a commit
# with this exact subject (cli/phase_cmds.py cmd_advance_phase). Message-level
# anchor — survives the rebases that make SHAs unreliable in this workflow.
_ADVANCE_SUBJECT = re.compile(r"^handover: advance to Phase (\d+)$")


@dataclass(frozen=True)
class Finding:
    check: str
    severity: str  # "ERROR" | "WARN" | "INFO"
    message: str


def run_doctor(project_root: Path) -> list[Finding]:
    """Run all consistency checks; returns findings (empty = clean)."""
    project = Path(project_root)
    layout = ProjectLayout(project)
    findings: list[Finding] = []

    if not layout.methodology_dir.is_dir():
        return [Finding("init", "INFO",
                        ".methodology/ not found — project not initialised, nothing to check")]

    # 1. state.json — the authoritative file must exist and be sane.
    current_phase: int | None = None
    state_path = layout.state_json_path
    if not state_path.is_file():
        findings.append(Finding("state", "ERROR",
                                "state.json missing from .methodology/"))
    else:
        try:
            state = load_state(project)
        except StateCorruptError as exc:
            state = None
            findings.append(Finding("state", "ERROR",
                                    f"state.json parse failure: {exc}"))
        if isinstance(state, dict):
            fsm_state = state.get("state")
            if fsm_state not in VALID_FSM_STATES:
                findings.append(Finding("state", "ERROR",
                                        f"FSM state {fsm_state!r} not in {sorted(VALID_FSM_STATES)}"))
            phase = state.get("current_phase")
            if isinstance(phase, int) and phase in VALID_PHASES:
                current_phase = phase
            else:
                findings.append(Finding("state", "ERROR",
                                        f"current_phase {phase!r} outside {list(VALID_PHASES)}"))

    # 2. Interrupted StateTransaction evidence.
    journal_path = layout.methodology_dir / ".txn_journal.json"
    if journal_path.is_file():
        try:
            pending = json.loads(journal_path.read_text(encoding="utf-8")).get("pending", [])
            targets = ", ".join(entry.get("target", "?") for entry in pending)
        except (json.JSONDecodeError, OSError):
            targets = "unreadable journal"
        findings.append(Finding("transaction", "ERROR",
                                f"interrupted state transaction — journal lists pending: {targets}. "
                                "Inspect the *.txn.tmp files, complete or discard the change, "
                                "then remove .methodology/.txn_journal.json"))
    for tmp in sorted(project.rglob("*.txn.tmp")):
        if ".git" in tmp.parts:
            continue
        findings.append(Finding("transaction", "WARN",
                                f"stray staging file: {tmp.relative_to(project)}"))

    # 3. quality_manifest ↔ state phase relation. Older-than-current is
    # normal (the manifest ages as the project advances); newer means the
    # manifest claims a phase the project never reached — half-state.
    manifest_path = layout.quality_manifest_path
    if manifest_path.is_file():
        try:
            manifest = load_quality_manifest(project)
        except StateCorruptError as exc:
            manifest = None
            findings.append(Finding("manifest", "ERROR",
                                    f"quality_manifest.json parse failure: {exc}"))
        if isinstance(manifest, dict) and current_phase is not None:
            gen_phase = manifest.get("generated_at_phase")
            if isinstance(gen_phase, int) and gen_phase > current_phase:
                findings.append(Finding("manifest", "ERROR",
                                        f"manifest generated_at_phase={gen_phase} is ahead of "
                                        f"state.json current_phase={current_phase}"))

    # 4. CLAUDE.md auto status block must agree with state.json.
    claude_md = project / "CLAUDE.md"
    if claude_md.is_file() and current_phase is not None:
        text = claude_md.read_text(encoding="utf-8", errors="replace")
        if "harness:auto-start" in text:
            m = _CLAUDE_BLOCK_PHASE.search(text)
            if m and int(m.group(1)) != current_phase:
                findings.append(Finding("claude-md", "ERROR",
                                        f"CLAUDE.md status block says Phase {m.group(1)} but "
                                        f"state.json says {current_phase}"))

    # 5. P5+ must have a trace attestation (blocking traceability phases).
    if current_phase is not None and current_phase >= 5:
        if not layout.attestation_path.is_file():
            findings.append(Finding("attestation", "ERROR",
                                    f"phase {current_phase} (P5+) requires "
                                    ".methodology/trace/attestation.json — not found"))

    # 6. git-sync (弱點強化 B2): state.json vs the durable advance record in
    # git history. Every successful advance lands "handover: advance to
    # Phase N"; state.json claiming a phase git never recorded is the
    # split-brain ghost state (advance commit failed after the state write,
    # pre-B1 runs, or a hand-edited state.json). Read-only and fail-soft:
    # non-git projects are silently skipped, git errors degrade to INFO.
    if current_phase is not None:
        findings.extend(_check_git_sync(project, current_phase))

    # 7. gate1-evidence (弱點強化 Round 3 J): quality_manifest claiming an
    # FR's Gate 1 quality_complete with ZERO records in any of the three
    # co-equal evidence channels (O2: sentinel .flag / .finalized /
    # gate_timestamps.jsonl) is a fabricated or hand-edited result.
    # Deliberately any-phase — at-rest reconciliation optimizes for zero
    # false positives; phase strictness stays at the enforcement sites
    # (push-milestone p3-post-gate2, advance-phase).
    findings.extend(_check_gate1_evidence(project, layout))

    # 7b. declared vs collected test set (Round 32 站5): a project may narrow
    # its own default `pytest` run — that is its decision — but the framework
    # measures test_coverage over an explicit directory, which overrides
    # `testpaths`. Measured on a live P4: nine declared entries against
    # sixteen collected files, two of them the FR tests for FR-02 and FR-07.
    # WARN, never ERROR: the difference has to be visible, not forbidden.
    findings.extend(_check_testpaths_drift(project))

    # 8. enforcement.json zombie keys (Round 9 站0): the EnforcementConfig
    # dataclass that once consumed mode/platform/enforce_on_*/thresholds was
    # removed as dead code — the only keys anything still reads are
    # hr_overrides and phase_truth (core/quality_gate/phase_truth_verifier.py).
    # An operator editing e.g. quality_gate_threshold gets silence today;
    # WARN so a zombie setting can't masquerade as a working knob.
    findings.extend(_check_enforcement_zombie_keys(layout))

    # 9. unfiled harness crash bundles (Round 13 站3): core/errors.py's
    # top-level crash boundary writes one of these when harness-methodology
    # itself crashes. A bundle sitting untriaged means nobody has looked at
    # a confirmed harness bug yet — WARN (not ERROR: this is a maintenance
    # backlog item, not a state inconsistency blocking the current run).
    findings.extend(_check_crash_bundles(project))

    # 10. spawn-log authenticity (Round 21 站3): entries that carry none of the
    # dispatch envelope AgentSpawner always records were not produced by a
    # dispatch. Strictly a post-hoc diagnostic — see _check_spawn_log_authenticity.
    findings.extend(_check_spawn_log_authenticity(layout))

    # 11. Liveness (Round 24 站5a). PARTIAL: this sees only whether a harness
    # command completed recently. It cannot see an agent that is alive but not
    # calling the harness. See core/heartbeat.py's module docstring.
    findings.extend(_check_heartbeat(project))

    # 11b. Enforcer provenance still resolvable (Round 30 站4). Round 29 站4
    # recorded enforcer_surface on every verdict and gave it no reader, so a
    # recorded enforcer_sha that no longer exists stayed invisible.
    findings.extend(_check_enforcer_provenance(project))

    # 12. harness/ submodule behind origin (Round 25 站3b). Relocated from
    # _advance_prechecks, where it was advance-phase's ONLY network call — a
    # `git fetch` on every single advance, non-blocking, printing the same four
    # remediation lines whether or not anyone acted on them. It accounted for
    # 60% of the wall time of a P1 or P2 advance. Nothing about it belongs on
    # the phase-transition critical path: being a few commits behind origin does
    # not make this phase's work wrong, and doctor is where at-rest
    # reconciliation already lives (next to _check_git_sync).
    findings.extend(_check_submodule_behind(project))

    # NOT here: the CI verdict for HEAD (Round 37). It was wired in and taken
    # back out — doctor is at-rest, offline, cross-FILE reconciliation, and a
    # `gh run list` per invocation makes every doctor call network-bound and
    # environment-dependent for information the push path already gates on
    # (cli/_shared.post_push_ci_gate) and records in the degradation ledger.
    # Re-open if doctor ever grows a --online mode, or if the ledger entry
    # turns out to need a reader here.

    return findings


def _check_submodule_behind(project: Path) -> list[Finding]:
    """WARN when harness/ is behind origin/main. Silent when offline or current.

    Network-touching by nature (core.submodule_sync.behind_count fetches), which
    is exactly why it lives in an on-demand command rather than in every advance.
    """
    sub = project / "harness"
    if not sub.is_dir():
        return []
    try:
        from core.submodule_sync import behind_count
        behind = behind_count(sub)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        return [Finding("submodule", "INFO",
                        f"harness/ drift check skipped: {exc}")]
    if behind <= 0:
        return []  # offline (-1) or already up to date (0)
    return [Finding("submodule", "WARN",
                    f"harness/ is {behind} commit(s) behind origin/main — CI may "
                    f"have landed test-fix commits. One-shot sync: "
                    f"`python3 -m harness.cli sync-harness`, then commit the "
                    f"submodule bump. Non-blocking: the local checkout still works")]


def _check_enforcer_provenance(project: Path) -> list[Finding]:
    """WARN when a recorded `enforcer_sha` no longer resolves in the harness.

    Round 30 站4. Round 19 站3 made every verdict record the harness commit that
    produced it, so "which enforcer said this?" became answerable from the
    artifact. The identifier it records is MUTABLE: taskq-advance's 8 Gate 1
    results, its Gate 2 result and both `state.json.phase_completed` entries all
    cite `01bb3bb4`, and a rebase of the harness submodule on 2026-08-02 left
    that commit reachable from nothing:

        git merge-base --is-ancestor 01bb3bb4 main  → NO
        git branch -a --contains 01bb3bb4           → (empty)

    The content still exists as `7154768`; the recorded name does not resolve.
    Rebasing is a normal part of this workflow, so this will keep happening —
    which is why Round 29 站4 added `enforcer_surface`, git object IDs for the
    three paths that actually produce verdicts. Those survive a rebase (measured:
    identical across `01bb3bb4` and `7154768`, and correctly DIFFERENT from the
    pre-fix base `c5971cd`). Round 29 wrote them and gave them no reader.

    WARN, never ERROR: an unreachable enforcer commit does not make the verdict
    wrong, and this framework is developed while it runs. What it costs is the
    ability to answer the question the field was added for, so it has to be
    visible somewhere.
    """
    from core.harness_provenance import enforcer_surface, harness_root

    recorded: dict[str, list[str]] = {}
    method = project / ".methodology"
    if not method.is_dir():
        return []
    for path in sorted(method.rglob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for sha in _enforcer_shas_in(data):
            recorded.setdefault(sha, []).append(str(path.relative_to(project)))
    if not recorded:
        return []

    root = harness_root()
    findings: list[Finding] = []
    for sha, sources in sorted(recorded.items()):
        bare = sha.removesuffix("-dirty")
        if bare in ("", "unknown"):
            continue
        try:
            proc = subprocess.run(
                ["git", "-C", str(root), "cat-file", "-e", f"{bare}^{{commit}}"],
                capture_output=True, text=True, timeout=10,
            )
        except (OSError, subprocess.SubprocessError):
            return []  # git unusable — see _check_submodule_behind's precedent
        if proc.returncode == 0:
            continue
        current = enforcer_surface()
        findings.append(Finding(
            "provenance", "WARN",
            f"verdict(s) in {', '.join(sorted(set(sources))[:3])} name enforcer "
            f"{bare[:12]}, which resolves to no commit in {root} — most likely a "
            f"rebase of the harness after the verdict was written. The recorded "
            f"`enforcer_surface` still answers whether the enforcing code was the "
            f"same; today's surface is "
            f"{ {k.rsplit('/', 1)[-1]: v[:8] for k, v in current.items()} }. "
            f"Fix: compare that against the `enforcer_surface` in those files; "
            f"if they match, the verdict stands and only its label is stale.",
        ))
    return findings


def _enforcer_shas_in(data: object) -> "list[str]":
    """Every `enforcer_sha` value in a parsed artifact, at any nesting depth.

    state.json carries them under `phase_completed.<n>`, gate results at the top
    level, and the quality manifest not at all — one walker rather than three
    readers that each know one shape.
    """
    out: list[str] = []
    if isinstance(data, dict):
        value = data.get("enforcer_sha")
        if isinstance(value, str) and value:
            out.append(value)
        for nested in data.values():
            out.extend(_enforcer_shas_in(nested))
    elif isinstance(data, list):
        for item in data:
            out.extend(_enforcer_shas_in(item))
    return out


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


# hr_overrides + phase_truth: legacy fallbacks read by phase_truth_verifier
# (migrated to harness_config values.phase_truth_* in Round 9 站3).
# constitution: still a live override layer — constitution/profile.py's
# load_profile() merges enforcement.json["constitution"] into the on-demand
# constitution profile (found by dogfooding this very check on the harness
# repo: the station-0 sweep grepped for the dataclass names and missed
# profile.py's string-literal path read).
_ENFORCEMENT_LIVE_KEYS = {"hr_overrides", "phase_truth", "constitution"}


def _check_enforcement_zombie_keys(layout: ProjectLayout) -> list[Finding]:
    cfg_path = layout.enforcement_config_path
    if not cfg_path.is_file():
        return []
    try:
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return [Finding("enforcement-config", "WARN",
                        f"{cfg_path.name} is not valid JSON — nothing reads a "
                        f"broken file, but a hand-edit probably went wrong")]
    if not isinstance(cfg, dict):
        return []
    zombie = sorted(k for k in cfg if k not in _ENFORCEMENT_LIVE_KEYS)
    if not zombie:
        return []
    return [Finding("enforcement-config", "WARN",
                    f"enforcement.json keys {zombie} have no consumer (the "
                    f"EnforcementConfig reader was removed as dead code); only "
                    f"{sorted(_ENFORCEMENT_LIVE_KEYS)} are still read, and only "
                    f"as legacy fallbacks — migrate them to harness_config.json "
                    f"values.phase_truth_threshold / values.phase_truth_pytest_timeout "
                    f"and delete this file")]


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


def _check_gate1_evidence(project: Path, layout: ProjectLayout) -> list[Finding]:
    manifest_path = layout.quality_manifest_path
    if not manifest_path.is_file():
        return []
    try:
        manifest = load_quality_manifest(project)
    except StateCorruptError:
        return []  # check 3 already reports the parse failure
    if not isinstance(manifest, dict):
        return []
    gate_results = manifest.get("gate_results")
    gate1 = gate_results.get("gate1") if isinstance(gate_results, dict) else None
    if not isinstance(gate1, dict):
        return []

    ts_frs: set[str] = set()
    ts_file = layout.methodology_dir / GATE_TIMESTAMPS_FILE
    if ts_file.is_file():
        try:
            for line in ts_file.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not (isinstance(entry, dict) and entry.get("gate") == 1):
                    continue
                # Round 20 站4: only a `finalize` row is independent evidence.
                # A `skip` row is written by GATE1-DELTA's already-done branch,
                # whose precondition is that a sentinel/commit ALREADY exists —
                # so counting it here would corroborate the sentinel channel
                # with a shadow of itself. Rows written before that station
                # carry no `source` and are accepted, since they predate the
                # distinction and the skip branch was the rarer writer.
                if entry.get("source") == EVIDENCE_SOURCE_SKIP:
                    continue
                ts_frs.add(str(entry.get("fr_id", "")).replace("-", "").lower())
        except OSError:
            pass

    sentinels_dir = project / ".sessi-work" / "sentinels"
    findings: list[Finding] = []
    for fr_id, rec in gate1.items():
        if not (isinstance(rec, dict) and rec.get("quality_complete")):
            continue
        fr_key = str(fr_id).replace("-", "").lower()
        # Round 32 站2: `.flag` no longer counts. run-gate writes it when the
        # gate STARTS — measured on a live P4, g1_p4_fr01.flag was written at
        # 03:49:53 and the gate BLOCKED at 03:54:07, and this check read that
        # flag as evidence FR-01 had passed. Only a finalize receipt attests a
        # verdict, and it has to agree with the registries written beside it.
        try:
            receipts = sorted(sentinels_dir.glob(f"g1_p*_{fr_key}.finalized"))
        except OSError:
            receipts = []
        if not receipts and fr_key not in ts_frs:
            findings.append(Finding(
                "gate1-evidence", "ERROR",
                f"quality_manifest.json marks {fr_id} Gate 1 quality_complete but "
                f"no evidence exists in any channel (finalize receipt, "
                f"{GATE_TIMESTAMPS_FILE}) — fabricated or hand-edited result; "
                f"re-run run-gate/finalize-gate for this FR or correct the manifest"))
            continue
        # One implementation, two consumers: cli/phase_cmds.py's advance-phase
        # precheck calls the same function. Each having its own copy of the
        # rule is how the two drifted into disagreeing about what counts.
        for _receipt in receipts:
            _phase = _phase_from_sentinel_name(_receipt.name)
            for problem in verify_finalize_evidence(project, 1, _phase, str(fr_id)):
                findings.append(Finding("gate1-evidence", "ERROR", problem))
    return findings


def _check_testpaths_drift(project: Path) -> list[Finding]:
    """Name the test files the project left out of its own default run.

    Reports, never rewrites — same contract as Round 31 站4's mutation
    `scope_drift`. The file carrying the declaration is separately
    fingerprinted into the verdict (DIMENSION_EXCLUSION_FILES), so the
    decision is in the artifacts; this says out loud what it means.
    """
    from core.quality_gate.testpaths_scope import testpaths_drift

    drift = testpaths_drift(project)
    if not drift or not drift["not_in_declared"]:
        return []
    missing = drift["not_in_declared"]
    shown = ", ".join(missing[:5]) + (f" +{len(missing) - 5} more"
                                      if len(missing) > 5 else "")
    return [Finding(
        "testpaths-drift", "WARN",
        f"{Path(drift['declared_source']).name} declares "
        f"{len(drift['declared'])} testpaths entr"
        f"{'y' if len(drift['declared']) == 1 else 'ies'}, but "
        f"{len(missing)} collected test file(s) are not covered by any of "
        f"them: {shown}. A bare `pytest` measures the declared set; the "
        f"framework measures the whole test directory. Both numbers are "
        f"real — they are just not the same number.")]


def _phase_from_sentinel_name(name: str) -> "int | None":
    """`g1_p4_fr01.finalized` -> 4. None when the name is not phase-scoped
    (the pre-v2.13 form, which verify_finalize_evidence then checks for shape
    only — there is no phase to look the registry rows up under)."""
    m = re.match(r"^g\d+_p(\d+)_", name)
    return int(m.group(1)) if m else None


def _check_git_sync(project: Path, current_phase: int) -> list[Finding]:
    def _git(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", "-C", str(project), *args],
            capture_output=True, text=True, timeout=5,
        )

    try:
        if _git("rev-parse", "--is-inside-work-tree").returncode != 0:
            return []  # not a git repo — nothing to cross-check
        # No -n cap (Round 2 Station G): -n applies to grep-filtered results,
        # not raw history depth, so any cap risks truncating past a real
        # match if enough near-miss commits (loosely matching --grep but
        # failing the strict _ADVANCE_SUBJECT regex below) precede it. The
        # 5s subprocess timeout below is the actual safety valve.
        log = _git("log", "--grep=^handover: advance to Phase ", "--format=%s")
        if log.returncode != 0:
            # e.g. unborn HEAD (repo initialised, nothing committed yet)
            return []
    except (OSError, subprocess.TimeoutExpired) as exc:
        return [Finding("git-sync", "INFO",
                        f"git cross-check skipped: {exc}")]

    git_phase: int | None = None
    for line in log.stdout.splitlines():
        m = _ADVANCE_SUBJECT.match(line.strip())
        if m:
            git_phase = int(m.group(1))  # log is reverse-chron: first = latest
            break

    if git_phase is None:
        if current_phase <= 1:
            return []  # fresh project — no advance has happened yet
        return [Finding("git-sync", "WARN",
                        f"state.json says Phase {current_phase} but git history has "
                        f"no 'handover: advance to Phase N' commit — pre-convention "
                        f"project or rewritten history; verify the phase manually")]
    if git_phase < current_phase:
        return [Finding("git-sync", "ERROR",
                        f"ghost state: state.json says Phase {current_phase} but the "
                        f"latest committed advance is Phase {git_phase} — an advance "
                        f"commit likely failed after state.json was written. Re-run "
                        f"advance-phase (it now rolls back on commit failure), or "
                        f"repair state.json to match git history")]
    if git_phase > current_phase:
        return [Finding("git-sync", "ERROR",
                        f"state.json says Phase {current_phase} but git history "
                        f"already records 'advance to Phase {git_phase}' — state "
                        f"regressed behind its own durable record (hand-edit or "
                        f"restored backup?)")]
    return []
