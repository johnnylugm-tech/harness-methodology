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
"""

from __future__ import annotations

import subprocess  # nosec B404
from pathlib import Path
from typing import Any, Callable, Optional

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
        proc = subprocess.run(  # nosec B603 B607
            ["git", "-C", str(project), "submodule", "status", _SUBMODULE_DIRNAME],
            capture_output=True, text=True, timeout=_TIMEOUT,
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

    Three outcomes, and the third is the one Round 37 insisted on: a verdict
    that could not be obtained is not a green verdict. It carries
    `infra: True` so the operator is pointed at gh/network rather than at a
    framework commit they cannot fix from here.
    """
    if not pinned_sha:
        return {"passed": True, "skipped": True,
                "message": "no harness submodule pin to check"}

    from core.ci_verdict import fetch_ci_verdict

    verdict = fetch_ci_verdict(Path(project) / _SUBMODULE_DIRNAME,
                              pinned_sha, runner=runner)
    short = pinned_sha[:8]

    if verdict.status == "green":
        return {"passed": True, "pinned_sha": pinned_sha,
                "message": f"harness pin {short}: {verdict.detail}"}

    if verdict.status == "red":
        return {
            "passed": False, "pinned_sha": pinned_sha,
            "failed_jobs": list(verdict.failed),
            "message": (
                f"harness pin {short} has failing CI: "
                f"{', '.join(verdict.failed) or 'unnamed job(s)'}. Every gate "
                f"this project runs is executed by that code. Move the "
                f"submodule to a commit whose Framework Self-Tests are green "
                f"(`git -C {_SUBMODULE_DIRNAME} fetch && git -C "
                f"{_SUBMODULE_DIRNAME} checkout <green-sha>`) and commit the "
                f"new pointer."
            ),
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
