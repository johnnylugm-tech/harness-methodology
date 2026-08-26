"""Is the framework commit this project pins a green one?

Round 67 站4. Round 37 built `core/ci_verdict` after taskq-renew pushed 52
times onto a red build with nothing ever asking GitHub what happened. It
closed the loop for the commit being pushed, and left the other direction
open: the harness commit a consuming project has PINNED as its submodule.

Measured 2026-08-22 across the eight projects on this machine, by asking
`repos/<harness>/commits/<pin>/check-runs` for each `git submodule status`
SHA:

    taskq                a8ab61a1  ALL GREEN
    taskq-plus           d5810d68  ALL GREEN
    taskq-renew          c09fae1f  ALL GREEN
    taskq-api            11c4eafd  ALL GREEN
    taskq-advance        5a87e35f  ALL GREEN
    taskq-super          f99a8b0d  Framework Self-Tests=failure
    taskq-cc             f6d984bc  Framework Self-Tests=failure
    run-all-by-workflow  68209a97  ALL GREEN

Two of eight run every gate through a framework whose own suite was red at
that commit. taskq-cc's `f6d984bc` is the one Round 66 pushed and had to
correct in `36ff4e5` — the pin carries the regression, the project ran P6
through P8 on it, and nothing said a word.

No new network code: the submodule's own origin IS the harness repo, so
`fetch_ci_verdict` answers this as it stands. What was missing was a caller.

Round 2026-08-23 (HARNESS-FIX): a 4th outcome was needed — a pin on a
LOCAL-ONLY commit the harness submodule has not pushed to its origin yet.
Until pushed, no CI has run on that commit; the verdict is structurally
unavailable. The Round 37 rule "a verdict that could not be obtained is
not a green verdict" is meant for transient/missing-CI conditions, not
for "the commit isn't on the remote yet". Block on local-only pins
silently halts every consuming project whenever an operator lands a
local harness fix ahead of the next push, and that is the failure mode
that triggered this fix. The bypass reports `skipped:
"local_only_pin"` (Round 46: an absent verification is reported as
absent, not as a pass); the runner is told, in the message, to push the
harness commit if CI verification is required.
"""

from __future__ import annotations

import subprocess  # nosec B404
from pathlib import Path
from typing import Any, Callable, Optional

from core.utils.subprocess_group import run_isolated

_TIMEOUT = 10
_SUBMODULE_DIRNAME = "harness"


def pinned_submodule_sha(project: "str | Path") -> "str | None":
    """The commit this project's `harness` submodule points at, or None.

    None covers every shape of "there is nothing to check here": no
    submodule, no git, a vendored copy rather than a pin. Round 46's rule —
    reported as absent, never as a pass — is applied by the caller, which is
    why this returns None rather than a verdict.
    """
    project = Path(project)
    try:
        # Round 66's rule: a call carrying a timeout is a call that intends to
        # kill, and killing has to mean killing the group. `run_isolated`
        # supplies capture_output/text.
        proc = run_isolated(
            ["git", "-C", str(project), "submodule", "status", _SUBMODULE_DIRNAME],
            timeout=_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    # " f6d984bc4... harness (v1.0-1856-gf6d984bc)" — the leading character is
    # a status flag (' ', '-', '+', 'U') and is not part of the SHA.
    line = proc.stdout.strip()
    if not line:
        return None
    sha = line[1:].split()[0] if line[0] in " -+U" else line.split()[0]
    return sha or None


def _commit_pushed_to_origin(submodule_dir: "str | Path", sha: str) -> bool:
    """True iff `sha` exists on any remote-tracking branch in the submodule.

    Used by `submodule_pin_verdict` to distinguish a pin on a pushed commit
    (where CI can have run) from a pin on a local-only commit (where the
    verdict is structurally unavailable until the commit is pushed).

    Conservative on the unanswerable cases: if the submodule directory
    does not exist, is not a git working tree, or git itself errors out,
    this returns True (i.e. "could not prove local-only") and the caller
    will fall through to the strict INFRA path. Only an explicit,
    successful `git branch -r --contains` that returns no rows counts
    as proof a commit is local-only.

    Code-review follow-up (2026-08-23): this used to answer purely from
    whatever remote-tracking refs happened to be cached locally, with no
    fetch — described as network-free by design. That is unsafe: the
    standard way a pin gets applied is `git submodule update`, which
    fetches only the pinned commit OBJECT and does not reliably update
    `refs/remotes/origin/*`. A normal, freshly-updated checkout can
    therefore fail `--contains` against a commit that has been on GitHub
    (and had CI run, possibly red) for a while, silently misreporting it
    as local-only and skipping `fetch_ci_verdict` entirely. A best-effort
    `git fetch origin` now refreshes the cache first; a failed fetch (no
    `origin` remote, no network — exactly what this module's own unit
    tests construct) is swallowed and falls through to the `--contains`
    check unchanged, so no existing caller's behavior changes.
    """
    submodule_path = Path(submodule_dir)
    if not submodule_path.exists():
        return True
    # A vendored (non-submodule) copy has neither .git/ nor a real pin;
    # fall through to whichever verdict rule the outer function selects.
    if not (submodule_path / ".git").exists() and not submodule_path.is_file():
        # .git may legitimately be a file (gitfile/submodule-as-pointer);
        # only treat "missing AND not a file" as "not a submodule".
        return True
    try:
        run_isolated(
            ["git", "-C", str(submodule_path), "fetch", "origin", "--quiet"],
            timeout=_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError):
        pass  # best-effort refresh; --contains below still runs on whatever is cached
    try:
        proc = run_isolated(
            ["git", "-C", str(submodule_path), "branch", "-r", "--contains", sha],
            timeout=_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError):
        return True
    if proc.returncode != 0:
        # `--contains` exits non-zero when no remote-tracking branch
        # contains the commit, which is exactly the local-only case.
        # Confirmed by a working `git` invocation, not by a missing one.
        return False
    return bool(proc.stdout.strip())


def submodule_pin_verdict(
    project: "str | Path",
    *,
    pinned_sha: "str | None",
    runner: Optional[Callable[[list[str]], tuple[int, str, str]]] = None,
) -> "dict[str, Any]":
    """Preflight-shaped verdict on the pinned framework commit.

    Returns the `{"passed": bool, "message": str, ...}` dict every
    `preflight_*` method returns, so the registry-driven pipeline needs no
    special case for it.

    Four outcomes. The 4th (local-only pin) was added in Round 2026-08-23:
    the strict Round 37 rule "unavailable is INFRA" blocked every project
    the moment an operator landed a local harness commit (no remote CI
    has run yet, so the verdict is structurally absent, not red and not
    a real INFRA failure). The bypass reports the verdict as absent — not
    as a pass — and the message tells the operator how to get an actual
    CI verdict (push). Pushed pins keep the strict behavior.
    """
    if not pinned_sha:
        return {"passed": True, "skipped": True,
                "message": "no harness submodule pin to check"}

    short = pinned_sha[:8]

    # Round 2026-08-23: a pin on a local-only commit cannot have a CI verdict
    # yet — the commit isn't on any remote-tracking branch, so GitHub has
    # never been asked. Reporting this as INFRA would block every project
    # whenever a harness fix lands locally before the next push; the
    # refusal to "convert absent into pass" intended by Round 37 was about
    # transient network / CI-config failures, not this structural absence.
    if not _commit_pushed_to_origin(Path(project) / _SUBMODULE_DIRNAME, pinned_sha):
        return {
            "passed": True, "skipped": "local_only_pin", "pinned_sha": pinned_sha,
            "message": (
                f"harness pin {short} is local-only (no remote-tracking branch "
                f"in the {pinned_sha[:12]} history); CI verdict is structurally "
                f"absent until pushed. Push the submodule commit to its origin "
                f"(`git -C {_SUBMODULE_DIRNAME} push origin <sha>`) if CI "
                f"verification is required. The preflight skipped this check; "
                f"the project's own tests + preflight gates remain enforced."
            ),
        }

    from core.ci_verdict import fetch_ci_verdict, find_latest_green_sha

    verdict = fetch_ci_verdict(Path(project) / _SUBMODULE_DIRNAME,
                              pinned_sha, runner=runner)

    if verdict.status == "green":
        return {"passed": True, "pinned_sha": pinned_sha,
                "message": f"harness pin {short}: {verdict.detail}"}

    if verdict.status == "red":
        # Round 79: red used to tell the operator "move to a green commit"
        # without telling them HOW to find one. Measured on taskq-cc-new
        # 2026-08-26: pin 4c24cf37 red → operator (or an LLM agent) walked
        # `git log` by hand and landed on 0978364c, which IS green but is
        # two commits behind d01adf0e (the commit that actually fixed the
        # failure). Rewinding loses the fix and re-introduces the regression
        # on the next bump. find_latest_green_sha walks origin/main from the
        # tip and returns the first commit whose Framework Self-Tests is
        # green, so the verdict points at the right SHA instead of leaving
        # "<green-sha>" as a placeholder the operator has to fill in.
        suggested = find_latest_green_sha(
            Path(project) / _SUBMODULE_DIRNAME, runner=runner,
        )
        if suggested:
            message = (
                f"harness pin {short} has failing CI: "
                f"{', '.join(verdict.failed) or 'unnamed job(s)'}. The newest "
                f"commit on origin/main with a green Framework Self-Tests is "
                f"{suggested[:8]}. Move the submodule to it "
                f"(`git -C {_SUBMODULE_DIRNAME} fetch && git -C "
                f"{_SUBMODULE_DIRNAME} checkout {suggested}`) and commit the "
                f"new pointer."
            )
        else:
            message = (
                f"harness pin {short} has failing CI: "
                f"{', '.join(verdict.failed) or 'unnamed job(s)'}. Every gate "
                f"this project runs is executed by that code. Move the "
                f"submodule to a commit whose Framework Self-Tests are green "
                f"(`git -C {_SUBMODULE_DIRNAME} fetch && git -C "
                f"{_SUBMODULE_DIRNAME} checkout <green-sha>`) and commit the "
                f"new pointer. `core.ci_verdict.find_latest_green_sha` could "
                f"not find one on origin/main within the default 20-commit "
                f"window — check that the harness repo's main branch is "
                f"reachable from this checkout's origin."
            )
        return {
            "passed": False, "pinned_sha": pinned_sha,
            "failed_jobs": list(verdict.failed),
            "suggested_sha": suggested,
            "message": message,
        }

    return {
        "passed": False, "infra": True, "pinned_sha": pinned_sha,
        "message": (
            f"harness pin {short}: CI verdict unavailable — {verdict.detail}. "
            f"A verdict that could not be obtained is not a green verdict "
            f"(INFRA, not a project failure): restore gh/network access and "
            f"re-run."
        ),
    }
