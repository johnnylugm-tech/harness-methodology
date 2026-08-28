"""Will `git push` from this clone run a hook, and does anything ever ask?

Round 81 站4. Round 80 站3 wrote this question as a shell script and pointed it
at the framework's own clone. It never got asked about the projects the
framework installs hooks INTO, and that is where the answer decays fastest.

`cli/project_cmds.py cmd_init_project` installs two things: step 2 writes the CI
workflow, step 3 runs scripts/setup-git-hooks.sh. `core/doctor.py` has sixteen
checks. One of them — `_check_ci_template_drift`, Round 40 站1 — goes back and
asks whether the deployed CI workflow is still the one this harness ships.
Nothing asks the same of the hooks.

The two have opposite durability. The CI workflow is a committed file: it
travels with the repository. The hooks are `.git/hooks/*` and `core.hooksPath`
in `.git/config`, and **`git clone` copies neither**. Every consumer project
that anyone has cloned since `init-project` ran has four dead hooks — pre-push,
pre-commit, post-merge, prepare-commit-msg — and no part of this framework has
ever been in a position to notice.

That is a different hole from the one Round 80 站3 declared unclosable. Its
"HONEST LIMIT: closing it needs a required status check on the branch" is about
someone who runs `git push --no-verify`, or who never runs self_check. It is
not about a project whose hooks are simply not there. 站3 wrote the two as one
limitation and only the first half needs branch protection.

ONE PREDICATE, TWO READERS

`git rev-parse --git-path hooks/pre-push` is git's own answer to "which file
would I run", and it resolves through `core.hooksPath` when that is set, so it
accepts both shapes setup-git-hooks.sh produces without this module having to
know which is in play.

`scripts/check_hook_wiring.sh` is now a wrapper around `main()` here, and
`core/doctor_checks/git_state.py` calls `pre_push_hook_status` directly. The
operator report and the doctor finding are rendered separately on purpose —
one is a terminal, the other is one line in a degradation ledger — but the
question is asked in exactly one place.

NOT ASKED: whether the resolved hook is canonical rather than a stale physical
copy. setup-git-hooks.sh already replaces legacy physical hooks with symlinks,
and no incident here has been a drifted copy.
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

#: `n/a` is a real answer, not a missing one: a tree git does not manage has no
#: hook to be wired, and CI clones never push. Round 79 站5's rule — a check
#: that did not run must not read like one that passed — is why it is named.
HookStatus = Literal["ok", "missing", "not_executable", "n/a"]


@dataclass(frozen=True)
class HookWiring:
    """What git would do with `pre-push` in this tree, and why."""

    status: HookStatus
    #: The path git reported, relative to the git dir or absolute. Empty when
    #: the question did not apply.
    hook_rel: str = ""
    #: `core.hooksPath` as configured, or "" when unset.
    hooks_path: str = ""
    #: Why the question did not apply. Empty unless status is "n/a".
    na_reason: str = ""


def pre_push_hook_status(repo: Path | str) -> HookWiring:
    """Ask git which pre-push hook it would run, and whether it could."""
    repo = Path(repo)

    if os.environ.get("CI") or os.environ.get("GITHUB_ACTIONS"):
        return HookWiring(
            "n/a",
            na_reason="CI does not push, so no pre-push hook is installed here",
        )

    if not _git(repo, "rev-parse", "--git-dir"):
        return HookWiring("n/a", na_reason=f"{repo} is not a git repository")

    hook_rel = _git(repo, "rev-parse", "--git-path", "hooks/pre-push")
    if hook_rel is None or not hook_rel:
        return HookWiring(
            "n/a", na_reason=f"git could not say which pre-push hook it would run in {repo}"
        )

    hooks_path = _git(repo, "config", "--get", "core.hooksPath") or ""
    hook_path = Path(hook_rel) if os.path.isabs(hook_rel) else repo / hook_rel

    if not hook_path.is_file():
        return HookWiring("missing", hook_rel, hooks_path)
    if not os.access(hook_path, os.X_OK):
        return HookWiring("not_executable", hook_rel, hooks_path)
    return HookWiring("ok", hook_rel, hooks_path)


def _git(repo: Path, *args: str) -> "str | None":
    """`git -C repo <args>` stdout, or None when git could not answer.

    Fail-soft on git itself, the rule core/doctor_checks/git_state.py states in
    its own module docstring: "git could not answer" is not evidence about the
    tree.

    Through `run_isolated` rather than `subprocess.run(timeout=…)`, because
    tests/test_subprocess_group.py's ratchet is right about what `timeout=`
    means: this call promises to kill the command, and killing has to mean
    killing the group. Round 80 站11 added a bare one here and had it caught by
    the same guard; the first draft of this module repeated it.
    """
    from core.utils.subprocess_group import run_isolated

    try:
        result = run_isolated(
            ["git", "-C", str(repo), *args], timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def operator_report(wiring: HookWiring) -> str:
    """The terminal text `scripts/check_hook_wiring.sh` prints.

    Byte-for-byte what that script printed before Round 81 站4 turned it into a
    wrapper — pinned by tests/test_hook_wiring_is_asked_of_projects.py against
    a recording made at bb15023d, because "the messages are the same" is the
    only counter-proof a rewrite can offer where a move offers sha256.
    """
    configured = wiring.hooks_path or "<unset>"

    if wiring.status == "n/a":
        return f"  not applicable: {wiring.na_reason}"

    if wiring.status == "missing":
        return (
            f"\n"
            f"  git would run '{wiring.hook_rel}' as the pre-push hook, and that file does\n"
            f"  not exist. core.hooksPath is '{configured}'.\n"
            f"\n"
            f"  Nothing you push from this clone is checked by the hook, so every\n"
            f"  check it performs is unreached — including the self-check this\n"
            f"  script is the first step of.\n"
            f"\n"
            f"  Fix:  scripts/setup-git-hooks.sh"
        )

    if wiring.status == "not_executable":
        return (
            f"\n"
            f"  '{wiring.hook_rel}' exists but is not executable, and git will not run a\n"
            f"  hook it cannot execute. core.hooksPath is '{configured}'.\n"
            f"\n"
            f"  Fix:  chmod +x '{wiring.hook_rel}'   (or re-run scripts/setup-git-hooks.sh)"
        )

    return f"  pre-push hook wired: {wiring.hook_rel} (core.hooksPath='{configured}')"


def main(argv: "list[str] | None" = None) -> int:
    """Exit 0 when a pre-push hook would run, or when the question does not apply."""
    args = sys.argv[1:] if argv is None else argv
    repo = Path(args[0]) if args else Path(__file__).resolve().parents[1]
    wiring = pre_push_hook_status(repo)
    print(operator_report(wiring))
    return 0 if wiring.status in ("ok", "n/a") else 1


if __name__ == "__main__":  # pragma: no cover - entry point
    raise SystemExit(main())
