"""Round 47 站3 — the executor the six detection points never had.

Before this module the framework could say a tool was missing in six places
and put one there in none:

    cli/project_cmds.py:348   init-project [11/11]
    cli/phase_cmds.py:2090    run-phase (every phase entry, P1 included)
    cli/gate_cmds.py:1081     run-gate
    cli/gate_cmds.py:1553     finalize-gate
    cli/fr_cmds.py:1594       fr-step Gate 1 preflight
    cli/gate_cmds.py:270      _finalize_env_result (env-check)

All six printed a sentence of prose and returned non-zero. This is Round 43's
pattern at the environment layer — detected, with no executor — crossed with
Round 36's: the repair knowledge existed, in seven contradicting copies, and
was never executable. Station 1 made it a single fact; this makes it an action.

WHO CALLS IT. Repair belongs to callers that intend to change the tree, never
to ones that measure it. Round 43 站1 established that rule when it moved the
traceability auto-fix out of `preflight_traceability` (whose docstring promised
it mutated nothing) and into `run-phase`. So five of the six are wired and
finalize-gate is deliberately not: finalize-gate is the JUDGE. A tool that
vanished between run-gate and finalize-gate is a fact worth blocking on, not
one to paper over.

WHAT IT WILL NOT DO. 老闆's Round 47 boundary is pip into the project's own
venv. gitleaks is a Go binary, make is a platform toolchain, and the JS tools
belong to the project's package.json — for those the advice is printed and the
call still blocks. Running brew or sudo would change host state the framework
cannot undo.

WHY IT RE-PROBES. `pip install` exiting 0 is a claim about a resolve, not
evidence a tool is runnable. Round 24's pattern — a field existing is not the
field being true — applies unchanged, and station 2 measured the other
direction too: a pip round can fail on one unbuildable package
(scancode-toolkit needs ICU headers) while every tool it promised is already
present. The probe decides; pip's exit code is a diagnostic.
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

import harness.toolchains.bootstrap as _ssot

__all__ = ["RepairOutcome", "repair_missing_tools"]


@dataclass
class RepairOutcome:
    """What was attempted, what pip said, and what is still true afterwards."""

    attempted_steps: list[str] = field(default_factory=list)
    unfixable: list[str] = field(default_factory=list)
    still_missing: list[str] = field(default_factory=list)
    pip_failures: list[str] = field(default_factory=list)
    skipped_reason: str = ""

    @property
    def ok(self) -> bool:
        return not self.still_missing and not self.unfixable

    def advice_for(self, tool_id: str) -> str:
        """What a human must do for a tool the framework will not install."""
        return _ssot.install_advice(tool_id) or (
            f"no install path registered for {tool_id!r} — see "
            f"harness/toolchains/bootstrap.py"
        )

    def report_lines(self) -> list[str]:
        """Operator-facing detail: what is still absent and whose move it is."""
        lines: list[str] = []
        for tool_id in self.unfixable:
            lines.append(f"  ✗ {tool_id} — {self.advice_for(tool_id)}")
        for tool_id in self.still_missing:
            if tool_id in self.unfixable:
                continue
            lines.append(
                f"  ✗ {tool_id} — install attempted and it is still not "
                f"resolvable; {self.advice_for(tool_id)}"
            )
        lines.extend(f"  [pip] {failure}" for failure in self.pip_failures)
        return lines


def _installer_python(project: Path) -> str:
    """The interpreter pip runs under: the project's venv, else this one.

    The fallback is not a convenience — CI has no project venv (it installs
    into the runner's system python), and station 0's premise 3 measured that
    the only CI job running run-phase installs everything first, so repair
    cannot fire there anyway. One resolution rule covers both without a
    CI-shaped branch.
    """
    from scripts.bootstrap_env import venv_python

    found = venv_python(project)
    return str(found) if found is not None else sys.executable


def _default_reprobe(tool_ids: "list[str]", project: Path) -> list[str]:
    """Re-ask each tool's own check_cmd, venv-scoped like the gate will."""
    from core.utils.venv_env import venv_scoped_env
    from harness.tool_checks import run_tool_check
    from harness.toolchains.registry import TOOL_SPECS

    env = venv_scoped_env(project)
    still: list[str] = []
    for tool_id in tool_ids:
        spec = TOOL_SPECS.get(tool_id)
        if spec is None:
            still.append(tool_id)
            continue
        try:
            if not run_tool_check(spec.check_cmd, cwd=str(project), env=env):
                still.append(tool_id)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            # Fail CLOSED and say so — a probe that raised did not measure.
            print(
                f"[WARN] env-repair: probe for {tool_id!r} raised ({exc}) — "
                f"counting it as still missing",
                file=sys.stderr,
            )
            still.append(tool_id)
    return still


def _pip_targets(tool_ids: "list[str]") -> "tuple[dict[str, list[str]], list[str]]":
    """Split *tool_ids* into {step -> packages to install} and the unfixable.

    gate-extras is installed PER PACKAGE rather than as the whole round.
    Station 2 measured why: scancode-toolkit fails to build on a host without
    ICU headers, and a single pip invocation is all-or-nothing, so asking for
    the whole round would deny import-linter and code-review-graph over an
    unrelated package's native dependency.
    """
    targets: dict[str, list[str]] = {}
    unfixable: list[str] = []
    for tool_id in tool_ids:
        step = _ssot.step_for_tool(tool_id)
        if step is None:
            unfixable.append(tool_id)
            continue
        package = _ssot.package_for_tool(tool_id)
        targets.setdefault(step, [])
        if package:
            targets[step].append(package)
    return targets, unfixable


def repair_missing_tools(
    project: "Path | str",
    tool_ids: "list[str]",
    *,
    run=subprocess.run,
    reprobe=_default_reprobe,
) -> RepairOutcome:
    """Install what pip can install, then re-measure. One attempt, no loop.

    A retry loop here would turn a broken index or a native-build failure into
    minutes of silence; the caller blocks with the true cause instead
    (Round 24's block_reason contract).
    """
    outcome = RepairOutcome()
    root = Path(project)
    tool_ids = list(dict.fromkeys(tool_ids))  # stable, de-duplicated
    if not tool_ids:
        return outcome

    targets, outcome.unfixable = _pip_targets(tool_ids)
    fixable = [t for t in tool_ids if t not in outcome.unfixable]

    if targets:
        python = _installer_python(root)
        for step_name, packages in targets.items():
            step = next(s for s in _ssot.PIP_STEPS if s.name == step_name)
            args = packages or list(_ssot.pip_args(step))
            proc = run(
                [python, "-m", "pip", "install", *args],
                capture_output=True,
                text=True,
            )
            outcome.attempted_steps.append(step_name)
            if getattr(proc, "returncode", 1) != 0:
                outcome.pip_failures.append(
                    f"{step_name}: {(getattr(proc, 'stderr', '') or '')[-400:]}"
                )

    outcome.still_missing = reprobe(fixable, root) if fixable else []
    _record(root, tool_ids, outcome)
    return outcome


def _record(project: Path, requested: "list[str]", outcome: RepairOutcome) -> None:
    """Every repair attempt lands in the degradation ledger.

    Auto-installing removes a loud signal: a host that was failing every run is
    now quietly succeeding. The ledger is what keeps "this machine needs
    repairing every time" answerable instead of invisible — same reason
    Round 46 站2 put skip counts there.
    """
    try:
        from core.degradation_ledger import record_degradation

        record_degradation(
            project,
            "gate:env-repair",
            f"{len(requested)} tool(s) missing, "
            f"{len(requested) - len(outcome.still_missing) - len(outcome.unfixable)} repaired",
            why=("; ".join(outcome.pip_failures)[:300] if outcome.pip_failures else ""),
            data={
                "requested": requested,
                "attempted_steps": outcome.attempted_steps,
                "unfixable": outcome.unfixable,
                "still_missing": outcome.still_missing,
                "installer_python": _installer_python(project),
                "ci": bool(os.environ.get("CI") or os.environ.get("GITHUB_ACTIONS")),
            },
        )
    except Exception as exc:  # pylint: disable=broad-exception-caught
        print(f"[WARN] env-repair: could not write the degradation ledger: {exc}",
              file=sys.stderr)
