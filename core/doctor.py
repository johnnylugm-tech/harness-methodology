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

from core.fsm.fsm import VALID_FSM_STATES
from core.phase_topology import VALID_PHASES
from core.quality_gate.gate1_evidence import GATE_TIMESTAMPS_FILE
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
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
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
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
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

    return findings


def _check_gate1_evidence(project: Path, layout: ProjectLayout) -> list[Finding]:
    manifest_path = layout.quality_manifest_path
    if not manifest_path.is_file():
        return []
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
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
                if isinstance(entry, dict) and entry.get("gate") == 1:
                    ts_frs.add(str(entry.get("fr_id", "")).replace("-", "").lower())
        except OSError:
            pass

    sentinels_dir = project / ".sessi-work" / "sentinels"
    findings: list[Finding] = []
    for fr_id, rec in gate1.items():
        if not (isinstance(rec, dict) and rec.get("quality_complete")):
            continue
        fr_key = str(fr_id).replace("-", "").lower()
        try:
            has_sentinel = (
                any(sentinels_dir.glob(f"g1_p*_{fr_key}.flag"))
                or any(sentinels_dir.glob(f"g1_p*_{fr_key}.finalized"))
            )
        except OSError:
            has_sentinel = False
        if has_sentinel or fr_key in ts_frs:
            continue
        findings.append(Finding(
            "gate1-evidence", "ERROR",
            f"quality_manifest.json marks {fr_id} Gate 1 quality_complete but "
            f"no evidence exists in any channel (sentinel .flag/.finalized, "
            f"{GATE_TIMESTAMPS_FILE}) — fabricated or hand-edited result; "
            f"re-run run-gate/finalize-gate for this FR or correct the manifest"))
    return findings


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
