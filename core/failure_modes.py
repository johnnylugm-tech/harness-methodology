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

# Statuses that mean a dispatch FAILED (everything else is a success). The
# authority for this list; cli/fr_cmds.py imports it rather than keeping the
# second copy it used to own.
#
# Round 19 站1 moved it here because the denominator was wrong, not just
# duplicated: `summarize` counted UNCLASSIFIED over EVERY entry, successes
# included. A successful dispatch matches no failure rule BY CONSTRUCTION, so
# it landed in UNCLASSIFIED and inflated the number that is supposed to mean
# "failures this classifier cannot explain". taskq's 91 entries read 95.6%
# UNCLASSIFIED; 72 of those were `complete`. The honest figure — unexplained
# FAILURES over failures — was 78.9%. Both are reported now (see summarize),
# but only the failure-scoped one is a defect signal.
DISPATCH_FAILURE_STATUSES: frozenset[str] = frozenset({
    "REJECT", "BLOCKED", "FAILED", "ERROR", "TIMEOUT", "REGRESSION_GUARD",
    "AWAITING_CONFIRMATION", "NOTHING_TO_DO",
})


def is_failure_entry(entry: dict) -> bool:
    """True when this log entry records a failed dispatch."""
    return str(entry.get("status") or "") in DISPATCH_FAILURE_STATUSES


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
    authored output prefix (not free-form agent text).

    Reads `error_output`, which is the field name the log actually carries:
    _log_dispatch writes `error_output=result["output"]`. Round 19 站1 found
    this rule reading `output` — the spawn-result key, never a log key — so it
    returned False for every one of taskq's 91 real entries, including the one
    that literally starts with "Commit-required step 'TDD-IMPROVE'". Its unit
    fixture used `output` too, so both sides agreed with each other and neither
    agreed with production. `test_failure_mode_fields_exist_in_the_log_schema`
    now pins every rule's field reads against the logger's own schema.
    """
    return str(entry.get("error_output") or "").startswith("Commit-required step")


def _effective_error_class(entry: dict) -> str:
    """The entry's error class, recomputed from `error_output` when the stamped
    value carries no information.

    `error_class` is a LABEL that core.agent_spawner._classify_dispatch_error
    stamped at dispatch time. Two of its three values are positive findings —
    "STRUCTURAL" and "INFRA_ERROR" mean a signature actually matched — and those
    are always honoured here: this module never overrides the spawner's verdict.

    "EXECUTION_ERROR" is different. It is that function's `else` branch: "no
    INFRA signature matched", i.e. the absence of a finding, evaluated against
    whatever the signature registry contained on the day the entry was written.
    Re-deriving it is not second-guessing a verdict, it is re-running a fallback
    against the current registry — which is the only way a fix to that registry
    can reach data already on disk.

    Round 19 站1 needed exactly this: after teaching _INFRA_ERROR_RE about
    "stream idle timeout", taskq's 12 stream-idle entries still read
    UNCLASSIFIED, each frozen at EXECUTION_ERROR from before the fix. It also
    makes tests/fixtures/failure_corpus/ (which strips the field entirely) an
    end-to-end exercise of `error_output -> class -> mode` rather than a replay.

    Entries whose EXECUTION_ERROR came from _validate_inner_json rather than the
    regex are unaffected: their text matches no INFRA signature either, and the
    semantic-no-op / missing-commit rules sit ahead of the INFRA rule anyway.

    Re-derivation is restricted to FAILED entries, and that restriction is
    load-bearing. _log_dispatch writes `error_output` on every entry — on a
    SUCCESSFUL dispatch it holds the sub-agent's ordinary reply text, not an
    error — and `error_class` is written only on failures precisely because of
    that. Running error signatures over a success message produced 3 false
    INFRA_ERROR hits on taskq's log the first time this was tried (an ordinary
    "Committed successfully..." reply among them), which is the same shape of
    mistake this whole station exists to remove.
    """
    if not is_failure_entry(entry):
        return str(entry.get("error_class") or "")
    stamped = str(entry.get("error_class") or "")
    if stamped and stamped != "EXECUTION_ERROR":
        return stamped
    output = str(entry.get("error_output") or "")
    if not output:
        return stamped
    from core.agent_spawner import _classify_dispatch_error
    return _classify_dispatch_error(output)


def _is_structural_env_breakage(entry: dict) -> bool:
    """Deterministic environment breakage (e.g. connectors disabled) —
    core.agent_spawner._classify_dispatch_error's "STRUCTURAL" class."""
    return _effective_error_class(entry) == "STRUCTURAL"


def _is_infra_precondition_blocked(entry: dict) -> bool:
    """The sub-agent reported a precondition blocker — the tools never ran.

    core.agent_spawner._INNER_BLOCKED_SIGNATURES (currently `INFRA_BLOCKED`, the
    status cli/fr_prompts/gate.py orders a Gate 1 evaluator to report when
    run-gate itself prints [BLOCKED]). Round 26.

    Ordered ahead of `commit_required_step_no_commit` deliberately: a blocked step
    also has no commit, so both predicates match the same entry, and the registry's
    documented "first match wins" makes the ordering the carve-out. It has to be
    first because the two answers are not equally wrong — `specification` sends the
    reader (and the fix loop) looking for an agent-logic defect, while the truth is
    an unmet precondition no code change can resolve. That mis-filing is on record:
    tests/fixtures/failure_corpus/integration_test.jsonl carries a verbatim
    `status='INFRA_BLOCKED'` entry imported in Round 19, classified `specification`
    from then until now, and taskq-plus FR-05 reproduced the live consequence on
    2026-07-30 — a 51-turn CODE-FIX dispatched at an unresolvable SAB phantom.
    """
    return _effective_error_class(entry) == "INFRA"


def _is_infra_error(entry: dict) -> bool:
    """Network/auth/rate-limit/model-unavailable signature — the model could
    not be reached or used, distinct from an agent-logic failure."""
    return _effective_error_class(entry) == "INFRA_ERROR"


def _is_dispatch_timeout(entry: dict) -> bool:
    """Sub-agent dispatch exceeded its turn/time budget with no regression
    flags raised (a timeout that also tripped regression flags is reported
    as REGRESSION_GUARD instead — see _has_regression_flags).

    Two shapes, one meaning — the docstring already said "turn/time budget"
    but only the time half was detected:
      - wall-clock: subprocess.TimeoutExpired -> status="TIMEOUT"
      - turn budget: the CLI's own `error_max_turns` result subtype, which
        exits non-zero and so arrives as status="ERROR" with that subtype in
        error_output (taskq P3: 2 occurrences, both UNCLASSIFIED before this).
    """
    if entry.get("status") == "TIMEOUT":
        return True
    return "error_max_turns" in str(entry.get("error_output") or "")


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
        "infra_precondition_blocked",
        MastCategory.INFRA,
        "the sub-agent reported a precondition blocker (INFRA_BLOCKED) — the "
        "dimension tools never ran, so there is no quality verdict and no code "
        "to fix",
        _is_infra_precondition_blocked,
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
    percentage (None when the denominator is empty — a percentage of zero
    denominator is not a real number).

    Reports the unclassified floor twice, over two different denominators:

      unclassified_pct          over ALL entries — kept for continuity with
                                pre-Round-19 reports, but NOT a defect signal:
                                successful dispatches match no failure rule by
                                construction, so this number rises whenever a
                                run goes well.
      unclassified_failure_pct  over failed entries only (is_failure_entry).
                                THIS is the coverage gap — failures the rules
                                cannot explain. It is what the corpus ratchet
                                (tests/test_failure_corpus_coverage.py) bounds.
    """
    mode_counts: dict[str, int] = {}
    category_counts: dict[str, int] = {}
    total = 0
    unclassified = 0
    failures = 0
    unclassified_failures = 0
    for entry in entries:
        total += 1
        classified = classify_entry(entry)
        mode_id = classified["mode_id"]
        mode_counts[mode_id] = mode_counts.get(mode_id, 0) + 1
        category = classified["mast_category"] or UNCLASSIFIED
        category_counts[category] = category_counts.get(category, 0) + 1
        is_unclassified = mode_id == UNCLASSIFIED
        if is_unclassified:
            unclassified += 1
        if is_failure_entry(entry):
            failures += 1
            if is_unclassified:
                unclassified_failures += 1
    return {
        "total": total,
        "mode_counts": mode_counts,
        "category_counts": category_counts,
        "unclassified_count": unclassified,
        "unclassified_pct": round(100.0 * unclassified / total, 1) if total else None,
        "failure_total": failures,
        "unclassified_failure_count": unclassified_failures,
        "unclassified_failure_pct": (
            round(100.0 * unclassified_failures / failures, 1) if failures else None
        ),
    }
