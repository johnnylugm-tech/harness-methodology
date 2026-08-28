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
from pathlib import Path

from core.doctor_checks import Finding
from core.doctor_checks.config_drift import (
    _check_enforcement_zombie_keys,
    _check_testpaths_drift,
    _check_verify_target_recipe,
)
from core.doctor_checks.git_state import (
    _check_ci_template_drift,
    _check_git_sync,
    _check_hook_wiring,
    _check_submodule_behind,
)
from core.doctor_checks.ledgers import (
    _check_crash_bundles,
    _check_heartbeat,
    _check_open_workflow_blocks,
    _check_spawn_log_authenticity,
)
from core.doctor_checks.verdicts import (  # noqa: F401  (_enforcer_shas_in,
    # _phase_from_sentinel_name: re-exported, not called here. Both are helpers
    # of the verdict checks, and both are imported FROM core.doctor by tests —
    # tests/test_enforcer_surface.py names the first. A split that dropped them
    # from this module's surface would break callers that never asked where the
    # code lived, which is the whole promise a façade makes.
    _check_enforcer_provenance,
    _check_gate1_evidence,
    _check_milestone_tree_matches_verdict,
    _check_phase_record_gaps,
    _check_phase_verdict_staleness,
    _enforcer_shas_in,
    _phase_from_sentinel_name,
)
from core.fsm.fsm import VALID_FSM_STATES
from core.phase_topology import VALID_PHASES
from core.state_io import StateCorruptError, load_quality_manifest, load_state
from core.utils.project_layout import ProjectLayout

_CLAUDE_BLOCK_PHASE = re.compile(r"Phase:\s*\*\*(\d+)")
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
    findings.extend(_check_open_workflow_blocks(project))

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

    # 11c. Which recorded phase verdicts were made under a different enforcer
    # (Round 43 站4). 11b asks whether the recorded SHA still resolves; this
    # asks the question the operator actually has when a check fires on an
    # artifact from a phase that passed — did the rules move, or did I break
    # something.
    findings.extend(_check_phase_verdict_staleness(project))

    # 12. harness/ submodule behind origin (Round 25 站3b). Relocated from
    # _advance_prechecks, where it was advance-phase's ONLY network call — a
    # `git fetch` on every single advance, non-blocking, printing the same four
    # remediation lines whether or not anyone acted on them. It accounted for
    # 60% of the wall time of a P1 or P2 advance. Nothing about it belongs on
    # the phase-transition critical path: being a few commits behind origin does
    # not make this phase's work wrong, and doctor is where at-rest
    # reconciliation already lives (next to _check_git_sync).
    findings.extend(_check_submodule_behind(project))

    # 13. dimension scope vs the latest verdict (Round 39 站2). Switching a
    # dimension off in harness_config.json is legitimate; doing it between a
    # gate verdict and now means the recorded verdict was measured over a
    # different set of dimensions than the one in force. WARN, not ERROR: the
    # missing-verdict case is already advance-phase's BLOCK, and doctor does
    # not re-litigate it.

    # 14. The deployed CI workflow vs the template this harness ships
    # (Round 40 站1). Offline, cross-file, at-rest — the same shape as
    # git-sync and dimension-scope, and doctor is the one command that runs
    # inside a consumer repo with the harness beside it, so it can see both
    # files at once.
    findings.extend(_check_ci_template_drift(project))

    # 14b. The OTHER thing init-project installs (Round 81 站4). Check 14 goes
    # back to the CI workflow that command's step 2 wrote; nothing went back to
    # the git hooks its step 3 installed, and those are the ones a `git clone`
    # silently drops — .git/hooks/ and core.hooksPath both live outside the
    # object store. Numbered beside 14 rather than appended, because it is the
    # same check asked of the other half of the same command.
    findings.extend(_check_hook_wiring(project))

    # 15. what `make verify-system` will actually run (Round 52 站1). The one
    # gate dimension that executes the delivered system runs a recipe the
    # project writes, and finalize_gate now blocks on two of its shapes. Here
    # so the operator meets that at P1 rather than at the P6 exit; WARN only,
    # because the enforcement is the gate's and doctor does not get to be a
    # second enforcer of it.
    findings.extend(_check_verify_target_recipe(project))

    # 16. the tree a milestone certifies vs the tree its commit records
    # (Round 44 站4). Station 2 makes the two agree from now on; this is the
    # reader for records written before that, and the only one that can name
    # a phase already turned over on work that was never committed. WARN, and
    # it re-judges nothing — the same standing as _check_phase_verdict_staleness.
    findings.extend(_check_milestone_tree_matches_verdict(project))
    findings.extend(_check_phase_record_gaps(project))

    # NOT here: the CI verdict for HEAD (Round 37). It was wired in and taken
    # back out — doctor is at-rest, offline, cross-FILE reconciliation, and a
    # `gh run list` per invocation makes every doctor call network-bound and
    # environment-dependent for information the push path already gates on
    # (cli/_shared.post_push_ci_gate) and records in the degradation ledger.
    # Re-open if doctor ever grows a --online mode, or if the ledger entry
    # turns out to need a reader here.

    return findings


