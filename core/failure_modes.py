"""core/failure_modes.py — deterministic MAST-aligned failure classifier
(Round 16 站2).

MAST ("Why Do Multi-Agent LLM Systems Fail?", arXiv:2503.13657, NeurIPS 2025
spotlight) taxonomizes multi-agent failures into 14 modes across 3 categories:
specification issues, inter-agent misalignment, and task verification. This
module reclassifies our own `sessions_spawn.log` entries (see
core/sessions_spawn_logger.py / core/agent_spawner.py) against that same
3-category shape, plus a 4th orthogonal INFRA bucket for environment/network/
model failures MAST doesn't cover (it studies MAS reasoning failures, not
infrastructure outages).

Station-2a reconnaissance (recorded in this station's commit message) found
real, grounded signal for SPECIFICATION and INFRA only:
  - SPECIFICATION: agent_spawner's regression-guard flags (destructive edit /
    XX-mutator-marker), semantic-no-op inner status
    (AWAITING_CONFIRMATION/NOTHING_TO_DO), and the commit-required-step-
    returned-no-commit signature.
  - INFRA: the existing STRUCTURAL/INFRA_ERROR error_class split, plus a
    plain dispatch TIMEOUT.
INTER_AGENT and VERIFICATION have ZERO detectable signal in current
artifacts: B-review's escalation_action (approve/retry/escalate_human,
core/review_schema_validator.py) is never persisted onto a spawn-log entry,
and gate PASS/FAIL verdicts live in per-gate result files, not per-dispatch
records. This is a genuine, documented gap (see docs/OBSERVABILITY.md's
"Failure modes" section) — not something this module papers over with a
guessed rule. Any entry matching none of the rules below is UNCLASSIFIED,
not silently miscounted into the nearest bucket.
"""

from __future__ import annotations

from typing import Any, Callable, NamedTuple


class MastCategory:
    """The 3 MAST categories, plus INFRA as our orthogonal 4th bucket."""

    SPECIFICATION = "specification"
    INTER_AGENT = "inter_agent"
    VERIFICATION = "verification"
    INFRA = "infra"


ALL_CATEGORIES = frozenset({
    MastCategory.SPECIFICATION,
    MastCategory.INTER_AGENT,
    MastCategory.VERIFICATION,
    MastCategory.INFRA,
})

UNCLASSIFIED = "UNCLASSIFIED"

_NOOP_INNER_STATUSES = frozenset({"AWAITING_CONFIRMATION", "NOTHING_TO_DO"})


class FailureModeRule(NamedTuple):
    mode_id: str
    mast_category: str
    description: str
    predicate: Callable[[dict], bool]


def _has_regression_flags(entry: dict) -> bool:
    """Destructive edit (>50 lines net-removed) or an XX-mutator-marker was
    introduced — core.agent_spawner._dispatch_diff_budget's regression_flags,
    non-empty exactly when the entry's status was overwritten to
    "REGRESSION_GUARD", so checking the flags dict directly is the more
    fundamental (and equally sufficient) signal."""
    return bool(entry.get("regression_flags"))


def _is_semantic_noop(entry: dict) -> bool:
    """Sub-agent exited 0 claiming completion but its inner JSON status was
    AWAITING_CONFIRMATION/NOTHING_TO_DO — core.agent_spawner._validate_inner_json."""
    return entry.get("inner_status") in _NOOP_INNER_STATUSES


def _is_missing_required_commit(entry: dict) -> bool:
    """A commit-required FR step reported completion but produced no commit
    — matches core.agent_spawner._validate_inner_json's fixed, framework-
    authored output prefix (not free-form agent text)."""
    return str(entry.get("output") or "").startswith("Commit-required step")


def _is_structural_env_breakage(entry: dict) -> bool:
    """Deterministic environment breakage (e.g. connectors disabled) —
    core.agent_spawner._classify_dispatch_error's "STRUCTURAL" class."""
    return entry.get("error_class") == "STRUCTURAL"


def _is_infra_error(entry: dict) -> bool:
    """Network/auth/rate-limit/model-unavailable signature — the model could
    not be reached or used, distinct from an agent-logic failure."""
    return entry.get("error_class") == "INFRA_ERROR"


def _is_dispatch_timeout(entry: dict) -> bool:
    """Sub-agent dispatch exceeded its turn/time budget with no regression
    flags raised (a timeout that also tripped regression flags is reported
    as REGRESSION_GUARD instead — see _has_regression_flags)."""
    return entry.get("status") == "TIMEOUT"


# Declarative rule registry — evaluated in order, first match wins. An entry
# matching no rule is UNCLASSIFIED (see module docstring: this is an honest
# floor, not a bug). tests/test_failure_modes.py's completeness meta-test
# requires one hit-fixture and one miss-fixture per mode_id here.
FAILURE_MODE_RULES: tuple[FailureModeRule, ...] = (
    FailureModeRule(
        "destructive_edit_or_mutator_marker",
        MastCategory.SPECIFICATION,
        "sub-agent's diff tripped the destructive-edit or XX-mutator-marker "
        "regression guard — an edit outside the FR task's safe scope",
        _has_regression_flags,
    ),
    FailureModeRule(
        "semantic_noop_termination",
        MastCategory.SPECIFICATION,
        "sub-agent exited 0 with an AWAITING_CONFIRMATION/NOTHING_TO_DO inner "
        "status — claimed completion without making real progress",
        _is_semantic_noop,
    ),
    FailureModeRule(
        "commit_required_step_no_commit",
        MastCategory.SPECIFICATION,
        "a commit-required FR step (TDD-RED/GREEN/IMPROVE/...) reported "
        "completion but produced no commit",
        _is_missing_required_commit,
    ),
    FailureModeRule(
        "structural_env_breakage",
        MastCategory.INFRA,
        "deterministic environment breakage signature — every retry fails "
        "identically, not an agent-logic problem",
        _is_structural_env_breakage,
    ),
    FailureModeRule(
        "infra_error_transient",
        MastCategory.INFRA,
        "network/auth/rate-limit/model-unavailable signature — the model "
        "could not be reached or used",
        _is_infra_error,
    ),
    FailureModeRule(
        "dispatch_timeout",
        MastCategory.INFRA,
        "sub-agent dispatch exceeded its turn/time budget",
        _is_dispatch_timeout,
    ),
)


def classify_entry(entry: dict) -> dict[str, Any]:
    """Classify one sessions_spawn.log-shaped entry. Returns a dict with
    mode_id/mast_category/description, or the UNCLASSIFIED floor (preserving
    the entry's original error_class/status for a human to triage) when no
    rule matches."""
    for rule in FAILURE_MODE_RULES:
        if rule.predicate(entry):
            return {
                "mode_id": rule.mode_id,
                "mast_category": rule.mast_category,
                "description": rule.description,
            }
    return {
        "mode_id": UNCLASSIFIED,
        "mast_category": None,
        "description": "no rule matched — outside this classifier's current deterministic coverage",
        "original_error_class": entry.get("error_class"),
        "original_status": entry.get("status"),
    }


def summarize(entries: "list[dict]") -> dict[str, Any]:
    """Aggregate classify_entry over a list of entries: per-mode counts, a
    MAST-category rollup, and the unclassified floor as both a count and a
    percentage (None when entries is empty — a percentage of zero denominator
    is not a real number)."""
    mode_counts: dict[str, int] = {}
    category_counts: dict[str, int] = {}
    total = 0
    unclassified = 0
    for entry in entries:
        total += 1
        classified = classify_entry(entry)
        mode_id = classified["mode_id"]
        mode_counts[mode_id] = mode_counts.get(mode_id, 0) + 1
        category = classified["mast_category"] or UNCLASSIFIED
        category_counts[category] = category_counts.get(category, 0) + 1
        if mode_id == UNCLASSIFIED:
            unclassified += 1
    return {
        "total": total,
        "mode_counts": mode_counts,
        "category_counts": category_counts,
        "unclassified_count": unclassified,
        "unclassified_pct": round(100.0 * unclassified / total, 1) if total else None,
    }
