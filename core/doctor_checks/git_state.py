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
from core.utils.subprocess_group import run_isolated

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


def _check_hook_wiring(project: Path) -> list[Finding]:
    """WARN when git would run no pre-push hook in this project.

    Round 81 站4. `init-project` installs two things and this file already asks
    about one of them: `_check_ci_template_drift` goes back to the CI workflow
    step 2 wrote. Nothing went back to the hooks step 3 installed, and the two
    decay differently — the workflow is a committed file, while the hooks are
    `.git/hooks/*` plus `core.hooksPath` in `.git/config`, and **`git clone`
    copies neither**. A project someone cloned after init has four dead hooks
    and nothing here could see it.

    WHY WARN, AFTER THIS WAS WRITTEN AS ERROR

    The plan for this station argued ERROR: an out-of-date CI file is an old
    verdict, a hook that does not run is no verdict at all. That reads well and
    it is not the line this module actually draws. Every other ERROR here is a
    RECORDED FACT that is wrong — a stale verdict, a forged spawn entry, a
    manifest ahead of the state. Missing hooks are environment, repaired by one
    command the operator runs when they choose to, which is verbatim the reason
    `_check_ci_template_drift` gives for being WARN.

    The evidence that settled it arrived from the suite rather than from the
    argument: `tests/e2e/conftest.py`'s project — faithful enough to read
    `_GITIGNORE_ENTRIES` from the harness SSOT so it delivers what a real
    project delivers — has no hooks, so `doctor` began exiting 1 on two e2e
    journeys. `cmd_doctor` returns 1 when any finding is an ERROR, and a FRESH
    CLONE of any project is in exactly this state. A signal that fires on the
    normal condition of a new checkout is one everybody learns to scroll past.

    Re-open as ERROR if a project is ever measured losing work because this
    line went unread.

    Silent on a tree git does not manage, per this module's fail-soft rule:
    "git could not answer" is not evidence about the project. That is also why
    the guard for this check has to build a real repository — every doctor
    fixture in tests/ is a bare tmp_path, where this is correctly quiet, and a
    guard that only ever exercises the quiet path is an absent witness.
    """
    from core.git_hooks import pre_push_hook_status

    wiring = pre_push_hook_status(project)
    if wiring.status in ("ok", "n/a"):
        return []

    configured = wiring.hooks_path or "<unset>"
    if wiring.status == "missing":
        detail = (f"git would run '{wiring.hook_rel}' and that file does not "
                  f"exist (core.hooksPath={configured})")
    else:
        detail = (f"'{wiring.hook_rel}' is not executable, and git will not run "
                  f"a hook it cannot execute (core.hooksPath={configured})")

    return [Finding("git-hooks", "WARN",
                    f"no pre-push hook would run in this project — {detail}. "
                    f"`git clone` copies neither .git/hooks/ nor core.hooksPath, "
                    f"so a fresh clone loses every hook init-project installed. "
                    f"Reinstall: `bash harness/scripts/setup-git-hooks.sh`")]


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


def _check_head_ci_verdict(project: Path, runner=None) -> list[Finding]:
    """ERROR when the commit this tree is sitting on is red on CI.

    Round 83 站4. During this round's own investigation `doctor` reported
    "0 error(s)" on a main whose Framework Self-Tests had been failing for
    three hours (6ba535e7 pushed 16:37, still red until aacac81f at 19:16).
    Nothing in the tooling said so — CI's own UI was the only place it
    existed, and `doctor` is the command an operator runs when they want to
    know whether anything is wrong.

    Framework repo only, by the same predicate `scripts/hooks/pre-push` uses:
    a repo that TRACKS `scripts/self_check.sh` is this one. A consuming
    project has no `Framework Self-Tests` check, so the question is not
    meaningful there and asking it would spend a network call to learn
    nothing. `git ls-files` rather than a filesystem test, for the reason
    Round 79 站5 recorded: a chmod cannot change what git tracks.

    One `gh` call, for HEAD, not a walk. `find_latest_green_sha` exists and is
    already read by `preflight_submodule_pin_ci` (measured this round: it
    returns the last green commit and refuses a red HEAD, so consuming
    projects are protected). What was missing is only the framework repo's
    own view of itself, and the cheapest true form of that question is "is
    the commit I am on red", which `fetch_ci_verdict` answers in one request.

    `unavailable` produces NO finding — no origin remote, no `gh`, no network,
    or a run that has not appeared yet. Round 32 站4's rule: could-not-measure
    is not a finding, and turning an offline laptop into an ERROR about the
    tree would be the inversion of the check this is.
    """
    # `run_isolated`, not a bare `subprocess.run(timeout=)`: Round 66's rule is
    # that a `timeout=` promises to KILL the command, and killing has to mean
    # killing the group. `_check_git_sync` above still has the bare form — it
    # predates the rule and is inside the count tests/test_subprocess_group.py
    # ratchets down; converting it is a separate change with its own reason,
    # and is noted here rather than done in passing.
    def _git(*args: str) -> subprocess.CompletedProcess:
        return run_isolated(["git", "-C", str(project), *args], timeout=5)

    try:
        if _git("ls-files", "--error-unmatch",
                "scripts/self_check.sh").returncode != 0:
            return []  # not the framework repo — no such check to ask about
        head = _git("rev-parse", "HEAD")
        if head.returncode != 0:
            return []
    except (OSError, subprocess.TimeoutExpired):
        return []

    sha = head.stdout.strip()
    from core.ci_verdict import fetch_ci_verdict

    verdict = fetch_ci_verdict(project, sha, runner)
    if verdict.status != "red":
        return []
    return [Finding(
        "head-ci", "ERROR",
        f"HEAD ({sha[:8]}) is RED on CI: {', '.join(verdict.failed) or 'unknown check'}"
        f" — anything pinning this commit pins a harness whose own self-tests "
        f"fail. Fix it here, or find the last good pin: python3 -c "
        f"\"from core.ci_verdict import find_latest_green_sha; "
        f"print(find_latest_green_sha('.'))\"")]
