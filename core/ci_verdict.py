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

__all__ = ["CiVerdict", "fetch_ci_verdict", "await_ci_verdict",
           "find_latest_green_sha", "Runner",
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


def find_latest_green_sha(
    project: "Path | str",
    *,
    runner: "Runner | None" = None,
    max_walk: int = 20,
    branch: str = "main",
    check_name: str = "Framework Self-Tests",
) -> "str | None":
    """Newest commit on *branch* whose *check_name* CI is green, or None.

    Walks `gh api repos/<slug>/commits?sha=<branch>&per_page=<max_walk>` (newest
    first), asks `gh api .../check-runs` for each, and returns the first SHA
    whose *check_name* conclusion is in `_SUCCESSFUL`. None when no commit in
    the window has the expected green check (network error, all-red window,
    no `gh`, no origin remote).

    Bounded by *max_walk* (default 20) so preflight cost stays bounded; the
    default covers the typical fix-and-retry distance — a misfire is fixed
    within a handful of commits in practice. Pass a smaller window to keep
    cost lower when only a near-term scan is meaningful.

    *runner* is the same injection point `fetch_ci_verdict` uses; tests
    inject a callable that returns canned payloads based on the command
    (detectable by `gh api .../commits?...sha=` vs `...check-runs`).

    Round 79: the preflight's red verdict pointed the operator at "<green-sha>"
    without telling them how to find it. Measured 2026-08-26 on taskq-cc-new:
    pin 4c24cf37 had failing Framework Self-Tests; d01adf0e (one commit later)
    fixed it. Without this helper the operator's only options were (a) read
    `gh api .../check-runs` by hand for every candidate or (b) rewind to a
    stale-but-green commit (0978364c) that had been superseded — the latter
    loses the fix and re-introduces the regression on the next submodule bump.
    This helper makes (a) cheap and the wrong choice (b) unnecessary.
    """
    project = Path(project)
    slug = repo_slug(project) if runner is None else "o/r"
    if not slug:
        return None
    run = runner or _default_runner

    # 1. Walk commit history (newest first) on `branch`.
    rc, out, err = run([
        "gh", "api", f"repos/{slug}/commits?sha={branch}&per_page={max_walk}",
    ])
    if rc != 0:
        return None
    shas = _shas_from_commits_payload(out)
    if not shas:
        return None

    # 2. For each SHA, ask GitHub which checks ran and read the ones named
    #    `check_name`. Conclude the walk on the first green; skipping on
    #    transient errors keeps a flaky network from blackholing the helper.
    for sha in shas:
        rc, out, err = run([
            "gh", "api", f"repos/{slug}/commits/{sha}/check-runs?per_page=100",
        ])
        if rc != 0:
            continue
        if _named_check_is_green(out, check_name):
            return sha
    return None


def _shas_from_commits_payload(out: "str | None") -> list[str]:
    """SHAs out of `GET /repos/{slug}/commits`, newest first.

    Round 80: this used to be `--jq .[].sha` and the SHAs arrived as lines.
    Parsing here rather than in the shell is the same choice `fetch_ci_verdict`
    already makes for `gh run list` — it puts the shape the code depends on
    where a test can hand it a real payload, instead of in a quoted filter
    string that no test in this repo evaluated.
    """
    try:
        payload = json.loads(out or "[]")
    except json.JSONDecodeError:
        return []
    if not isinstance(payload, list):
        return []
    return [c["sha"] for c in payload
            if isinstance(c, dict) and isinstance(c.get("sha"), str)]


def _named_check_is_green(out: "str | None", check_name: str) -> bool:
    """True when every run of *check_name* on this commit concluded green.

    `GET /repos/{slug}/commits/{sha}/check-runs` answers with an OBJECT —
    `{"total_count": n, "check_runs": [...]}` — not an array. The filter this
    replaces was `[.[] | select(.name == …) | .conclusion] | .[0]`, which
    iterated that object: `.[]` yielded the integer `total_count` first and jq
    aborted with `Cannot index number with string "name"` (exit 5), so the
    caller's `if rc != 0: continue` skipped every commit and the helper
    returned None every time it was asked. Verified 2026-08-28 against
    repos/johnnylugm-tech/harness-methodology.

    GitHub returns one row per dispatch, so a re-run adds another: dff609e6
    really carries two "Framework Self-Tests" rows a second apart. `| .[0]`
    took an arbitrary one of them. The rule here is `fetch_ci_verdict`'s,
    applied to the single named check rather than invented a second time for
    the same module — no conclusion yet is not green, any non-success
    conclusion is not green, and a check that never ran is not green either
    (Round 46: an absent witness is not a passing one).
    """
    try:
        payload = json.loads(out or "{}")
    except json.JSONDecodeError:
        return False
    if not isinstance(payload, dict):
        return False
    runs = payload.get("check_runs")
    if not isinstance(runs, list):
        return False
    named = [r for r in runs
             if isinstance(r, dict) and r.get("name") == check_name]
    if not named:
        return False
    return all((r.get("conclusion") or "") in _SUCCESSFUL for r in named)


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
