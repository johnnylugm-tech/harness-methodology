"""What the push produced — the CI verdict for a commit (Round 37).

The framework pushes at every milestone and, until this module, never looked
at the result. Measured on taskq-renew: 52 GitHub Actions runs, 48 red, red
on every push from Phase 3 onward, while the local pipeline declared every
phase and gate PASS and advanced state.json to Phase 9. A full-tree search of
core/ cli/ harness/ scripts/ and .claude/workflows/ found no reader of a
workflow run's conclusion — scripts/phase_auditor.py's GitHubFetcher reads
the repo tree only.

"The push happened" and "the push produced a green build" are two different
propositions. Only the first had an enforcer.

A verdict that cannot be obtained — no `gh`, no network, the run not started
yet — is `unavailable`, never `green`. That is the same rule Round 32 and
Round 35 applied to mutation scoring: a number we could not measure is not a
passing number.
"""

from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Sequence

__all__ = ["CiVerdict", "fetch_ci_verdict", "await_ci_verdict", "Runner",
           "DEFAULT_WAIT_SECONDS", "repo_slug", "render_block_message"]

# (returncode, stdout, stderr)
Runner = Callable[[list[str]], "tuple[int, str, str]"]

_TIMEOUT = 60
_POLL_INTERVAL = 10.0
# How long the push path waits for a verdict before calling it unobtainable.
# Deliberately a constant with a --wait override rather than a harness_config
# key: one number, one override, no per-project tuning surface until a project
# actually needs one. taskq-renew's runs finished in 15s-2m.
DEFAULT_WAIT_SECONDS = 300
_SUCCESSFUL = {"success", "skipped", "neutral"}


@dataclass
class CiVerdict:
    status: str                       # "green" | "red" | "unavailable"
    failed: list[str] = field(default_factory=list)
    pending: list[str] = field(default_factory=list)
    runs: list[dict] = field(default_factory=list)
    detail: str = ""
    # True only when waiting could change the answer: the run has not appeared
    # yet, or it is still in progress. False for structural unavailability —
    # no origin remote, no `gh`, a gh error — because no amount of waiting
    # makes an origin remote appear, and polling one would burn the whole
    # timeout on a question already answered.
    retryable: bool = False

    @property
    def urls(self) -> list[str]:
        return [r.get("url", "") for r in self.runs
                if r.get("name") in self.failed]


def _default_runner(cmd: list[str]) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=_TIMEOUT)
    except FileNotFoundError:
        return 127, "", "gh: command not found"
    except (OSError, subprocess.SubprocessError) as exc:
        return 1, "", f"gh could not be run: {exc}"
    return proc.returncode, proc.stdout, proc.stderr


def repo_slug(project: Path) -> str | None:
    """owner/name from the origin remote, or None."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(project), "remote", "get-url", "origin"],
            capture_output=True, text=True, timeout=_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    url = proc.stdout.strip().removesuffix(".git")
    if url.startswith("git@") and ":" in url:
        return url.split(":", 1)[1]
    parts = url.rstrip("/").split("/")
    return "/".join(parts[-2:]) if len(parts) >= 2 else None


def await_ci_verdict(
    project: Path | str, sha: str, wait_seconds: int,
    runner: Runner | None = None, sleep: Callable[[float], None] = time.sleep,
) -> CiVerdict:
    """Poll until CI reaches a terminal verdict, or *wait_seconds* elapses.

    A run takes tens of seconds to appear and minutes to finish, so asking
    once immediately after a push always answers "unavailable" — which, under
    the rule that unavailable blocks, would block every push. Waiting is what
    makes "red blocks" enforceable rather than merely stated.

    Timing out still yields `unavailable`: we waited and the answer did not
    arrive, which is not the same as the answer being green.
    """
    deadline = time.monotonic() + max(0, wait_seconds)
    verdict = fetch_ci_verdict(project, sha, runner=runner)
    while verdict.retryable and time.monotonic() < deadline:
        sleep(min(_POLL_INTERVAL, max(0.0, deadline - time.monotonic())))
        verdict = fetch_ci_verdict(project, sha, runner=runner)
    return verdict


def fetch_ci_verdict(
    project: Path | str, sha: str, runner: Runner | None = None,
) -> CiVerdict:
    """The CI verdict for *sha*, as GitHub Actions reports it.

    `runner` is the injection point: production passes nothing and gets the
    real `gh` subprocess; tests pass a callable. The seam is a parameter, not
    a patched module global, so a test never has to reach into a private name.
    """
    run = runner or _default_runner
    project = Path(project)
    slug = repo_slug(project) if runner is None else "o/r"
    if not slug:
        return CiVerdict("unavailable", detail=(
            "no origin remote — cannot ask GitHub what this commit produced"))

    rc, out, err = run(_gh_cmd_for(slug, sha))
    if rc != 0:
        return CiVerdict("unavailable", detail=(
            f"gh run list failed (rc={rc}): {(err or out).strip()[:300]}"))
    try:
        runs = json.loads(out or "[]")
    except json.JSONDecodeError as exc:
        return CiVerdict("unavailable",
                         detail=f"gh returned invalid JSON: {exc}")
    if not isinstance(runs, list) or not runs:
        return CiVerdict("unavailable", runs=[], retryable=True, detail=(
            f"no workflow run has appeared for {sha[:8]} yet"))

    failed = [r.get("name", "?") for r in runs
              if (r.get("conclusion") or "") not in _SUCCESSFUL
              and r.get("conclusion")]
    pending = [r.get("name", "?") for r in runs if not r.get("conclusion")]
    if pending:
        return CiVerdict("unavailable", failed=failed, pending=pending,
                         runs=runs, retryable=True, detail=(
                             f"{len(pending)} run(s) still in progress for "
                             f"{sha[:8]} — the answer is not in yet"))
    if failed:
        return CiVerdict("red", failed=failed, runs=runs,
                         detail=f"{len(failed)} failing run(s) for {sha[:8]}")
    return CiVerdict("green", runs=runs, detail=f"all runs green for {sha[:8]}")


def _gh_cmd_for(slug: str, sha: str) -> list[str]:
    return [
        "gh", "run", "list", "-R", slug, "--commit", sha, "--limit", "50",
        "--json", "name,conclusion,databaseId,url",
    ]


def render_block_message(verdict: CiVerdict, sha: str) -> Sequence[str]:
    """The [BLOCKED] lines for a red verdict — failing job names and URLs.

    Round 24's rule: the block message carries the real reason, not a code.
    """
    lines = [
        f"[BLOCKED] CI is red for {sha[:8]} — the push landed, the build did not.",
        f"  {len(verdict.failed)} failing run(s):",
    ]
    for r in verdict.runs:
        if r.get("name") in verdict.failed:
            lines.append(f"    - {r.get('name')}  {r.get('url', '')}")
    lines.append("  Fix the failing job(s) and re-push; do not advance on a red build.")
    return lines
