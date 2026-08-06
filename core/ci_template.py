"""Where the CI workflow lives, and whether a project's copy is still it.

Round 40 站1. `init-project` deploys `templates/harness_quality_gate.yml` into
a project with `write_text()` — no substitution, no templating. That is what
makes this module possible: the deployed file and the template are the same
bytes or the copy is out of date, with no third option to reason about.

Nothing compared them after the first write. `_harness_workflow_template`'s
docstring said "both deploy the same file, so there is no drift", which is a
statement about the moment of deployment and about no moment after it. The
framework edits the template; nobody edits the copy.

Measured on taskq-renew (2026-08-06), two framework rounds behind:

    pip install -r harness/requirements.txt || true    R37 站4 removed `|| true`
    crg-arch-check --project . --threshold 80          R38 站2 removed the flag

Neither changed that project's verdict. That is the reason to check rather
than a reason not to: Round 38 removed `--threshold` from every call site the
framework owns *because* a second source of a gate floor is a defect waiting
for the two numbers to disagree, and `tests/test_crg_threshold_ssot.py`
enforces it — across the framework's own files. A consumer's copy is outside
every scan the framework runs, so the flag came back on the one file no test
could see.

This module owns both paths so "where the CI workflow lives" is stated once:
`cli/project_cmds.py` deploys through it, and `core/doctor.py` reconciles
through it.
"""

from __future__ import annotations

import difflib
from pathlib import Path

__all__ = ["ci_template_path", "deployed_ci_path", "ci_template_drift",
           "REDEPLOY_COMMAND"]

WORKFLOW_FILENAME = "harness_quality_gate.yml"

# Safe to paste: `--ci-only` keeps `--overwrite` off the git hooks, so this
# rewrites the workflow and nothing else.
REDEPLOY_COMMAND = (
    "python3 harness/harness_cli.py init-project --project . "
    "--ci-only --overwrite"
)

# Enough context to recognise the drift, not enough to bury a doctor report.
_MAX_DIFF_LINES = 14


def ci_template_path() -> Path:
    """The framework's canonical CI workflow — the file init-project copies."""
    return Path(__file__).resolve().parent.parent / "templates" / WORKFLOW_FILENAME


def deployed_ci_path(project: Path | str) -> Path:
    """Where that copy lands in a target project."""
    return Path(project) / ".github" / "workflows" / WORKFLOW_FILENAME


def ci_template_drift(project: Path | str) -> str | None:
    """None when the project's workflow is byte-identical to the template.

    Otherwise a report naming what differs and the command that repairs it.
    An unreadable template returns None rather than accusing the project of a
    drift the framework cannot substantiate — Round 32's rule that a
    measurement which could not be taken is not a failing measurement.
    """
    template = ci_template_path()
    try:
        want = template.read_text(encoding="utf-8")
    except OSError:
        return None

    deployed = deployed_ci_path(project)
    if not deployed.is_file():
        # Jurisdiction: this function is about a *deployed copy*. A project
        # with no .github/workflows/ at all was never CI-installed — the
        # harness is usable without init-project, and saying "your copy is out
        # of date" about a copy that was never made is an accusation the
        # framework cannot substantiate. A workflows directory that exists
        # without ours in it is the opposite case: the deployment happened and
        # the file is gone.
        if not deployed.parent.is_dir():
            return None
        return (
            f"{deployed.relative_to(Path(project))} is not deployed while "
            f"{deployed.parent.relative_to(Path(project))}/ is, so none of the "
            f"CI-side gates (CRG architecture, D4 coverage, push-milestone) run "
            f"on push. Fix: {REDEPLOY_COMMAND}"
        )

    try:
        got = deployed.read_text(encoding="utf-8")
    except OSError as exc:
        return f"{deployed} could not be read ({exc})"

    if got == want:
        return None

    diff = list(difflib.unified_diff(
        got.splitlines(), want.splitlines(),
        fromfile="deployed", tofile="template", lineterm="", n=0,
    ))
    shown = diff[:_MAX_DIFF_LINES]
    if len(diff) > _MAX_DIFF_LINES:
        shown.append(f"... ({len(diff) - _MAX_DIFF_LINES} more diff lines)")
    return (
        f"{deployed.name} differs from the template the framework ships "
        f"({template}). The copy is deployed once and never re-synced, so it "
        f"keeps running the gates of whichever harness version installed it:\n"
        + "\n".join(f"      {line}" for line in shown)
        + f"\n    Fix: {REDEPLOY_COMMAND}"
    )
