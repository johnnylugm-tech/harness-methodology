"""doctor checks: what git says, against what the framework believes.

Split out of core/doctor.py in R49-B. Three checks that reach for the same
tool and share its failure modes — a repository that is not one, a network
that is not there, a submodule pointer nobody moved.

All three are fail-soft on git itself: a git error degrades to INFO rather
than an ERROR about the project, because "git could not answer" is not
evidence about the tree. That rule is the reason they belong together, and
`_check_submodule_behind` carries the precedent the other two cite.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from core.doctor_checks import Finding

# Durable phase-advance record: every successful advance-phase lands a commit
# with this exact subject (cli/phase_cmds.py cmd_advance_phase). Message-level
# anchor — survives the rebases that make SHAs unreliable in this workflow.
_ADVANCE_SUBJECT = re.compile(r"^handover: advance to Phase (\d+)$")


def _check_ci_template_drift(project: Path) -> list[Finding]:
    """WARN when the project's CI workflow is not the one this harness ships.

    Round 40 站1. `init-project` copies templates/harness_quality_gate.yml
    verbatim and never revisits it, so a project keeps running the gates of
    whichever harness version installed it. taskq-renew was measured carrying
    `|| true` (Round 37 removed it) and `--threshold 80` (Round 38 removed it)
    long after both landed here.

    WARN, never ERROR: an out-of-date CI file is not a wrong verdict, it is an
    old one, and the repair is a single command the operator runs when they
    choose to.
    """
    from core.ci_template import ci_template_drift

    drift = ci_template_drift(project)
    return [Finding("ci-template", "WARN", drift)] if drift else []


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
