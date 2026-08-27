"""harness_repair.py — the executor the crash-triage ticket never had (Round 48 站3).

`crash-triage --open-cr` files a CR-BUG into harness's own
`.methodology/change_requests/` and stops. docs/ERROR_HANDLING.md states it
outright: "a production run never calls --open-cr automatically". So the one
path that already identifies a harness defect ends at a ticket with no
executor — Round 43's shape (detected, no executor), one layer up.

This module is the deterministic half of the executor. The multi-agent half
(reproduce → root-cause hypothesis → adversarial review → fix) is the
generated `harness-repair` workflow; everything a test can pin lives here.

WHAT IT REFUSES, AND WHY EACH REFUSAL EXISTS

  no reproduction        A report is a claim; the reproduction is the evidence.
                         Round 33 is the cost of skipping it — two rounds fixed
                         a symptom whose premise ("the file is missing") was
                         false, and the class survived both fixes.
  revert stays green     If undoing the change leaves the reproduction passing,
                         the change did not cause the pass. 老闆's 根源性.
  generated file edited  883e9ca hand-edited four shipped .claude/workflows/*.js
                         files; all 94 workflowgen tests stayed green while
                         `generate_workflows.py --check` reported 4/9 DRIFT.
                         A repair agent handed a workflow bug reaches for the
                         .js file first, because that is the file the trace
                         names.
  threshold moved        Round 38 站2 settled that the floor lives in
                         harness/gate_configs/*.yaml and is the same one CI
                         applies. Editing it is not fixing a defect, it is
                         lowering the bar for every consuming project at once.
  guard deleted          Guards only go up. Removing an entry removes the
                         memory of the incident it exists for.
  self-gate not green    老闆's ruling this round. harness is a submodule and
                         main is what every project tracks, so "mostly green"
                         ships a regression to all of them simultaneously.

WHAT IT DELIBERATELY DOES NOT REFUSE

Edits under `core/quality_gate/` and `harness/harness_bridge.py` — the
checkers themselves. R31, R32, R33 and R45 were all defects inside that code;
a blanket ban would refuse every real repair. The protection is not a
forbidden-path list, it is the counter-proof: revert the change and the
reproduction must come back red.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

__all__ = [
    "SELF_GATE_CHECKS",
    "SelfGateOutcome",
    "RepairPreconditions",
    "CheckoutPlan",
    "generated_file_violations",
    "forbidden_edit_violations",
    "guard_count_violations",
    "checkout_plan",
    "push_failure_reason",
    "changed_paths",
    "run_self_gate",
    "reproduce",
]

# The six checks this repo actually closes a round with. Named here so a repair
# cannot quietly close on five of them; tests/test_repair_harness.py pins the set.
SELF_GATE_CHECKS: tuple[str, ...] = (
    "pytest", "guards", "ruff", "workflowgen", "node", "sim",
)

# argv per check, with `{py}` substituted for the harness checkout's own
# interpreter. Each is run EXPLICITLY rather than leaned on pytest to cover
# transitively: three of them (workflowgen --check, node --check, the sim) do
# have pytest tests that shell out to the same tools, but "the suite happens to
# reach it today" is not the same statement as "this is one of the six things a
# round closes on", and the first can stop being true without anyone noticing.
_SELF_GATE_ARGV: dict[str, list[str]] = {
    "pytest": ["{py}", "-m", "pytest", "tests/", "-q"],
    "guards": ["{py}", "scripts/verify_regression_guards.py"],
    "ruff": ["{py}", "-m", "ruff", "check", "."],
    "workflowgen": ["{py}", "scripts/workflowgen/generate_workflows.py", "--check"],
    # No wrapper script exists for this one — `node --check` is invoked per file
    # by tests/test_workflow_js_conventions.py and nowhere else, so the loop is
    # spelled here rather than inventing a script to point at.
    "node": ["bash", "-c",
             'for f in .claude/workflows/*.js; do node --check "$f" || exit 1; done'],
    # Round 80 站1: reporter named rather than defaulted, same as the two
    # pytest bridges. This gate reads the exit code only, but a self-gate whose
    # output shape moves under it is the shape being closed.
    "sim": ["node", "--test", "--test-reporter=tap",
            "scripts/workflowgen/js_src/sim_runner.test.mjs"],
}

_GENERATED_WORKFLOW_RE = re.compile(r"^\.claude/workflows/.+\.js$")
_GENERATOR_PREFIX = "scripts/workflowgen/"

# Paths a repair may not touch at all. Deliberately short: everything on it is
# a number other enforcers read, not code that can be wrong.
_FORBIDDEN_PREFIXES: tuple[tuple[str, str], ...] = (
    ("harness/gate_configs/",
     "gate thresholds — Round 38 站2 made these the floor every enforcer "
     "shares, including CI. A repair that edits one is not fixing a defect"),
)

_GUARD_ENTRY_RE = re.compile(r"^\s*-\s+test:\s*\S", re.MULTILINE)


@dataclass(frozen=True)
class SelfGateOutcome:
    """Which of the six checks passed. `None` means "could not run".

    Round 30's rule: a check that could not run must not return the value that
    means "ran, clean". `None` is its own answer, and it blocks — measured
    against the alternative, a repair that pushed because the sim runner was
    missing from the host would be indistinguishable from one that pushed
    because the sim passed.
    """

    results: dict[str, "bool | None"] = field(default_factory=dict)

    @property
    def blocking(self) -> list[str]:
        return [name for name in SELF_GATE_CHECKS if self.results.get(name) is not True]

    @property
    def green(self) -> bool:
        return not self.blocking


@dataclass(frozen=True)
class RepairPreconditions:
    """Reality, then root cause. In that order, both measured, neither claimed."""

    reproduced: bool
    counterproof_red: bool

    @property
    def may_fix(self) -> bool:
        return self.reproduced

    @property
    def may_commit(self) -> bool:
        return self.reproduced and self.counterproof_red

    @property
    def refusal(self) -> str:
        if not self.reproduced:
            return (
                "the reported failure could not be reproduced on this tree — "
                "its reality is unverified, so there is nothing to fix yet. Do "
                "not edit harness on the strength of a report alone"
            )
        if not self.counterproof_red:
            return (
                "reverting the change left the reproduction PASSING, so the "
                "change is not what made it pass — the counter-proof did not go "
                "red and the root cause is still somewhere else"
            )
        return ""


@dataclass(frozen=True)
class CheckoutPlan:
    must_checkout: bool
    target_branch: str
    refusal: str


def generated_file_violations(paths: "list[str]") -> list[str]:
    """A generated workflow may only change alongside its generator."""
    touched = [p for p in paths if _GENERATED_WORKFLOW_RE.match(p)]
    if not touched:
        return []
    if any(p.startswith(_GENERATOR_PREFIX) for p in paths):
        return []
    return [
        f"{p}: generated file edited with no change under {_GENERATOR_PREFIX} — "
        f"edit the generator and re-run "
        f"`python3 scripts/workflowgen/generate_workflows.py --write`"
        for p in touched
    ]


def forbidden_edit_violations(paths: "list[str]") -> list[str]:
    out: list[str] = []
    for path in paths:
        for prefix, why in _FORBIDDEN_PREFIXES:
            if path.startswith(prefix):
                out.append(f"{path}: {why}")
    return out


def guard_count_violations(*, before_text: str, after_text: str) -> list[str]:
    """Guards only ever go up."""
    before = len(_GUARD_ENTRY_RE.findall(before_text))
    after = len(_GUARD_ENTRY_RE.findall(after_text))
    if after >= before:
        return []
    return [
        f"tests/REGRESSION_GUARDS.yaml lost {before - after} entr(ies) "
        f"({before} -> {after}) — a guard records an incident this project "
        f"already paid for; removing one removes the memory, not the debt"
    ]


def checkout_plan(*, current_branch: str, dirty_paths: "list[str]") -> CheckoutPlan:
    """Where to commit, and whether it is safe to move there first.

    Measured 2026-08-12 across the six live projects: taskq-plus and
    taskq-renew both carry the harness submodule on a DETACHED HEAD. A commit
    made there is reachable from nothing — which is exactly how Round 29/30
    lost enforcer_sha 01bb3bb4, still named by eight gate results.

    The clobber refusal fires only when a checkout is actually needed. A dirty
    tree already on the target branch is not a hazard, it is the normal state
    of a repair mid-flight, and refusing it would have made this predicate
    unusable at the only moment it matters.
    """
    must_checkout = current_branch != "main"
    if must_checkout and dirty_paths:
        return CheckoutPlan(False, "main", (
            "the harness submodule is not on main and has uncommitted edits, "
            "so moving to main would carry or clobber them: " +
            ", ".join(sorted(dirty_paths)[:10]) +
            ". Commit or stash them, then re-run"
        ))
    return CheckoutPlan(must_checkout, "main", "")


# `git push` failures this repo can name. Anything else returns "" — a wrong
# diagnosis sends the operator at the wrong problem, and Round 45 is the round
# that measured what 30 false accusations cost.
_PUSH_DIAGNOSES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bnon-fast-forward\b|\(fetch first\)|\(non-fast-forward\)", re.I),
     "the remote branch carries commits this checkout does not have, so the "
     "push was rejected as non-fast-forward. Fetch, rebase onto origin/main, "
     "and re-run the self-gate on THAT tree before pushing — the six checks "
     "passed on a tree that no longer describes what would land"),
    (re.compile(r"protected branch|refusing to allow|GH006", re.I),
     "the remote refused the write on branch-protection grounds. This is not a "
     "repairable condition from here and no rule may be changed to clear it"),
    (re.compile(r"Authentication failed|Permission denied|could not read Username",
                re.I),
     "the remote rejected the credentials. Nothing about the fix is in question"),
)


def push_failure_reason(stderr: str) -> str:
    """Name the cause when it is recognisable, and say nothing when it is not."""
    for pattern, reason in _PUSH_DIAGNOSES:
        if pattern.search(stderr or ""):
            return reason
    return ""


def _python_for(harness_root: "str | Path") -> str:
    """The harness checkout's own interpreter, else the running one.

    Same resolution rule as harness/env_repair.py::_installer_python — one
    statement of "which python", not a second copy with its own drift.
    """
    import sys

    from scripts.bootstrap_env import venv_python

    found = venv_python(harness_root)
    return str(found) if found is not None else sys.executable


def changed_paths(harness_root: "str | Path", *, run=subprocess.run) -> list[str]:
    """Repo-relative paths git sees as changed, staged or untracked."""
    proc = run(
        ["git", "-C", str(harness_root), "status", "--porcelain", "--untracked-files=normal"],
        capture_output=True, text=True,
    )
    if getattr(proc, "returncode", 1) != 0:
        return []
    out: list[str] = []
    for line in (getattr(proc, "stdout", "") or "").splitlines():
        if len(line) > 3:
            out.append(line[3:].strip().split(" -> ")[-1])
    return out


def run_self_gate(harness_root: "str | Path", *, run=subprocess.run) -> SelfGateOutcome:
    """Run all six checks and report each one's own answer.

    Every check runs even after one fails: an operator reading the refusal
    wants the whole list, not the first thing that broke. A check that raises
    records None rather than False — "could not run" and "ran and failed" are
    different findings (Round 32).
    """
    python = _python_for(harness_root)
    results: dict[str, "bool | None"] = {}
    for name in SELF_GATE_CHECKS:
        argv = [python if part == "{py}" else part for part in _SELF_GATE_ARGV[name]]
        try:
            proc = run(argv, cwd=str(harness_root), capture_output=True, text=True)
            results[name] = getattr(proc, "returncode", 1) == 0
        except OSError as exc:
            print(f"[repair-harness] self-gate check {name!r} could not run: {exc}")
            results[name] = None
    return SelfGateOutcome(results)


def reproduce(
    harness_root: "str | Path", command: str, *, run=subprocess.run
) -> "bool | None":
    """Run the ticket's reproduction. True when it FAILS (i.e. reproduces).

    None when the command could not be executed at all — which is neither
    "reproduced" nor "did not reproduce", and must not be rounded to either.
    """
    try:
        proc = run(
            ["bash", "-c", command], cwd=str(harness_root),
            capture_output=True, text=True,
        )
    except OSError as exc:
        print(f"[repair-harness] reproduction command could not run: {exc}")
        return None
    return getattr(proc, "returncode", 1) != 0
