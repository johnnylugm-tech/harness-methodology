"""doctor checks: settings that drifted from what reads them.

Split out of core/doctor.py in R49-B. Three checks that ask the same question
of three different files — whether a value the framework still reads matches
the value something else has since changed:

  enforcement keys  enforcement.json keys vs the ones any code still reads
  testpaths         pyproject's testpaths vs the suite the gate measures
  verify target     the Makefile recipe the one product-executing gate runs

They share no code with each other and none with the rest of doctor; grouping
them is about what the next reader needs open at once, which is the only
thing a split can buy.

Nothing here decides anything. doctor REPORTS — see core/doctor.py's own
docstring on why an auto-repair path would become a fabrication surface.
"""

from __future__ import annotations

import json
from pathlib import Path

from core.doctor_checks import Finding
from core.utils.project_layout import ProjectLayout

# hr_overrides + phase_truth: legacy fallbacks read by phase_truth_verifier
# (migrated to harness_config values.phase_truth_* in Round 9 站3).
# constitution: still a live override layer — constitution/profile.py's
# load_profile() merges enforcement.json["constitution"] into the on-demand
# constitution profile (found by dogfooding this very check on the harness
# repo: the station-0 sweep grepped for the dataclass names and missed
# profile.py's string-literal path read).
_ENFORCEMENT_LIVE_KEYS = {"hr_overrides", "phase_truth", "constitution"}


def _check_enforcement_zombie_keys(layout: ProjectLayout) -> list[Finding]:
    cfg_path = layout.enforcement_config_path
    if not cfg_path.is_file():
        return []
    try:
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return [Finding("enforcement-config", "WARN",
                        f"{cfg_path.name} is not valid JSON — nothing reads a "
                        f"broken file, but a hand-edit probably went wrong")]
    if not isinstance(cfg, dict):
        return []
    zombie = sorted(k for k in cfg if k not in _ENFORCEMENT_LIVE_KEYS)
    if not zombie:
        return []
    return [Finding("enforcement-config", "WARN",
                    f"enforcement.json keys {zombie} have no consumer (the "
                    f"EnforcementConfig reader was removed as dead code); only "
                    f"{sorted(_ENFORCEMENT_LIVE_KEYS)} are still read, and only "
                    f"as legacy fallbacks — migrate them to harness_config.json "
                    f"values.phase_truth_threshold / values.phase_truth_pytest_timeout "
                    f"and delete this file")]


def _check_testpaths_drift(project: Path) -> list[Finding]:
    """Name the test files the project left out of its own default run.

    Reports, never rewrites — same contract as Round 31 站4's mutation
    `scope_drift`. The file carrying the declaration is separately
    fingerprinted into the verdict (DIMENSION_EXCLUSION_FILES), so the
    decision is in the artifacts; this says out loud what it means.
    """
    from core.quality_gate.testpaths_scope import testpaths_drift

    drift = testpaths_drift(project)
    if not drift or not drift["not_in_declared"]:
        return []
    missing = drift["not_in_declared"]
    shown = ", ".join(missing[:5]) + (f" +{len(missing) - 5} more"
                                      if len(missing) > 5 else "")
    return [Finding(
        "testpaths-drift", "WARN",
        f"{Path(drift['declared_source']).name} declares "
        f"{len(drift['declared'])} testpaths entr"
        f"{'y' if len(drift['declared']) == 1 else 'ies'}, but "
        f"{len(missing)} collected test file(s) are not covered by any of "
        f"them: {shown}. A bare `pytest` measures the declared set; the "
        f"framework measures the whole test directory. Both numbers are "
        f"real — they are just not the same number.")]


def _check_verify_target_recipe(project: Path) -> list[Finding]:
    """WARN about what the one product-executing gate dimension will run.

    Round 52 站1. `execute_verification_target` runs `make verify-system`, and
    the recipe behind it is written by the project. The gate blocks on two of
    its shapes; this says so before the gate does, which is the difference
    between finding out at P3 and finding out at P6.

    Reports, never decides — core/doctor.py's contract. The severity is WARN
    even for the two blocking shapes: doctor does not get to be a second
    enforcer of a rule finalize_gate already enforces (Round 38), it gets to
    tell the operator early.
    """
    from core.quality_gate.verify_target import (
        STATUS_EXPANDED,
        blocking_reason,
        verify_target_findings,
        verify_target_name,
    )

    findings = verify_target_findings(project)
    if findings["status"] != STATUS_EXPANDED:
        # A missing target is the `execute_verification_target` dimension's
        # own failure and an unreadable Makefile is a could-not-measure; the
        # ledger carries both (record_verify_target_status). Neither is a
        # doctor finding about drift.
        return []

    rows: list[Finding] = []
    reason = blocking_reason(project)
    if reason:
        rows.append(Finding("verify-target", "WARN",
                            f"{reason}. The exit gate blocks on this."))
    benign = [r for r in findings["swallowed"]
              if r not in findings["swallowed_product"]]
    if benign:
        shown = "; ".join(f"{r['idiom']} in `{r['line']}`" for r in benign[:3])
        rows.append(Finding(
            "verify-target", "WARN",
            f"{len(benign)} step(s) of `make {verify_target_name()}` cannot "
            f"fail, so their verdict is not in its exit code: {shown}. Not "
            f"blocking — none of them runs the product."))
    return rows
