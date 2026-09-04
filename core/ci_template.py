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
import json
import os
import shutil
import subprocess  # nosec B404 — fixed argv, no shell
import tempfile
from pathlib import Path

__all__ = ["ci_template_path", "deployed_ci_path", "ci_template_drift",
           "gitleaks_template_path", "deployed_gitleaks_path",
           "gitleaks_scope_missing", "GITLEAKS_DEPLOY_COMMAND",
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


# ── the second template, and why its predicate is a different question ──────
#
# Round 96. Round 92 shipped `templates/.gitleaks.toml` and taught
# `init-project` to write it. init-project runs once, at project creation, so
# the fix could not reach any project that already existed. Measured on
# taskq-final, which bumped its submodule to Round 92's commit and then ran
# Phases 5 through 8: no `.gitleaks.toml`, six `.gitleaksignore` fingerprints,
# two of them added AFTER the bump — the fingerprint treadmill Round 92's own
# ledger judged a workaround, walked because nothing offered the alternative.
#
# The CI workflow above is checked for byte-equality. This one cannot be:
# Round 92 established that `.gitleaks.toml` is project-owned and never
# overwritten, because three corpus projects hand-author their own allowlists.
# So the question is not "does the copy match the template" but "is this
# project paying for not having one" — which is answerable only by running the
# scanner, the same way `harness.tool_runners.scanner_is_alive` answers whether
# the scanner is alive. Measured on taskq-final: 245 ms over 1.97 MB.

GITLEAKS_FILENAME = ".gitleaks.toml"

GITLEAKS_DEPLOY_COMMAND = (
    "python3 harness/harness_cli.py init-project --project . --gitleaks-only"
)

#: The rule the framework's own `.methodology/` output trips. Scoped: a
#: `private-key` under `.methodology/` is a real finding and this must not
#: offer to silence it.
_METHODOLOGY_NOISE_RULE = "generic-api-key"


def gitleaks_template_path() -> Path:
    """The framework's canonical gitleaks scope config."""
    return Path(__file__).resolve().parent.parent / "templates" / GITLEAKS_FILENAME


def deployed_gitleaks_path(project: Path | str) -> Path:
    """Where that copy lands in a target project."""
    return Path(project) / GITLEAKS_FILENAME


def gitleaks_scope_missing(project: Path | str) -> "str | None":
    """None unless this project is paying for having no gitleaks scope config.

    Four ways to be None, and each is a deliberate refusal to accuse:

      * the project has its own `.gitleaks.toml` — it answered the question
      * there is no `.methodology/` — the harness is usable without
        init-project, and this says nothing about a tree it never touched
        (same jurisdiction `ci_template_drift` draws)
      * gitleaks is not installed, or the run could not be read — Round 32: a
        measurement which could not be taken is not a failing measurement
      * `.methodology/` produces no `generic-api-key` finding — the config
        earns its place by silencing something real, and a permanent nag for a
        file the project has no use for is Round 42's defect
    """
    project = Path(project)
    if deployed_gitleaks_path(project).exists():
        return None
    methodology = project / ".methodology"
    if not methodology.is_dir():
        return None
    if shutil.which("gitleaks") is None:
        return None

    from core.utils.subprocess_group import run_isolated

    with tempfile.TemporaryDirectory(prefix="gitleaks-scope-") as tmp:
        report = os.path.join(tmp, "report.json")
        try:
            # `run_isolated`, not `subprocess.run`: a `timeout=` says this call
            # will kill the command, and Round 65/66's rule is that killing has
            # to mean killing the group. `scanner_is_alive` runs gitleaks the
            # same way for the same reason.
            run_isolated(  # nosec B603 B607 — fixed argv, no shell
                ["gitleaks", "detect", "--source", str(methodology), "--no-git",
                 "--report-format", "json", "--report-path", report,
                 "--no-banner"],
                timeout=120, cwd=tmp, env=os.environ.copy(),
            )
            with open(report, encoding="utf-8") as fh:
                findings = json.load(fh)
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError, ValueError):
            return None

    noise = [
        f for f in findings
        if isinstance(f, dict) and f.get("RuleID") == _METHODOLOGY_NOISE_RULE
    ]
    if not noise:
        return None

    where = sorted({str(f.get("File", "")) for f in noise})[:3]
    return (
        f"{len(noise)} {_METHODOLOGY_NOISE_RULE} finding(s) in .methodology/ "
        f"and no {GITLEAKS_FILENAME} in this project. Those files are the "
        f"framework's own audit records — test names and coverage summaries, "
        f"not credentials — and every commit that rewrites one creates a new "
        f"fingerprint, so silencing them one at a time in .gitleaksignore can "
        f"never catch up (measured on a corpus project: two more were added "
        f"after it adopted the fix that would have stopped them).\n"
        + "".join(f"      {p}\n" for p in where)
        + f"    Fix: {GITLEAKS_DEPLOY_COMMAND}\n"
        f"    It scopes ONLY {_METHODOLOGY_NOISE_RULE} to .methodology/; a real "
        f"key there is still caught by private-key / aws / github-pat. Your own "
        f"{GITLEAKS_FILENAME} is never overwritten."
    )
