"""B-review JSON schema validator + escalation logic.

Root cause (Bug B of 5-point plan): workflow JS B-2 dispatch returns
{review_status, gaps: [{severity, message, fr_id}]} as free-form JSON. The
escalation rules (high → fix → retry; REJECT × 5 → human) lived only as
plan text. LLM mis-parses of the JSON shape caused silent regressions:

  - Missing `evidence_type` (the field that distinguishes real_invention from
    over_interpretation) caused over-interpretation to escalate to high
    severity, hitting HR-12 deadlock on ambiguous SPEC phrases
    (Bug-fix commit 2045331 documented this).

  - Schema drift (e.g. B reviewer returns `issues` instead of `gaps`) was
    invisible to the framework — workflow JS would silently treat gaps as [].

Fix: enforce b_review_schema.json as the authoritative shape. Schema violations
do NOT block the workflow — they synthesize a CANCELLED B with one
methodology_artifact gap that triggers exactly ONE B-2 retry (no infinite
loop). Within-schema gaps with `evidence_type=over_interpretation` are
downgraded from any B-stated severity to medium (HR-12 regression guard).

Commonality: phase-agnostic. Used by:
  - reviewer_router._parse_response (defensive layer for Python sub-task dispatch)
  - auto_fix.dispatch (decides fix strategy from evidence_type)
  - core/constitution (any future invariant that grades B output)
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Optional

import jsonschema

from core.review_quota import enforce_quota


SCHEMA_PATH = Path(__file__).parent.parent / "schemas" / "b_review.schema.json"


class EscalationAction(str, Enum):
    APPROVE = "approve"
    RETRY = "retry"
    REJECT = "reject"
    ESCALATE_HUMAN = "escalate_human"


@dataclass(frozen=True)
class ValidationResult:
    valid: bool
    normalized: dict
    error: Optional[str] = None
    synthesized: bool = False


def _load_schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text())


def validate_b_output(raw: dict | Any, phase: int = 0, deliverable: str = "") -> ValidationResult:
    """Validate B reviewer JSON against b_review.schema.json.

    Returns ValidationResult:
      - valid=True: raw passes schema (after downgrade rules applied to gaps)
      - valid=False, synthesized=True: schema violated → synthesized CANCELLED
        with one methodology_artifact gap (triggers one B-2 retry, no loop)
      - valid=False, synthesized=False: not a dict at all (caller decides)
    """
    if not isinstance(raw, dict):
        return ValidationResult(False, {}, "raw is not a dict", synthesized=False)

    schema = _load_schema()
    validator = jsonschema.Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(raw), key=lambda e: list(e.absolute_path))
    if errors:
        synthesized = _synthesize_cancelled(raw, errors[0].message)
        return ValidationResult(False, synthesized, errors[0].message, synthesized=True)

    # Schema-valid — apply HR-12 regression guard: over_interpretation caps at medium
    normalized = _downgrade_over_interpretation(dict(raw))
    # H: apply review_quota — cap findings under DEFAULT_MAX_QUOTA weight.
    # Findings that don't fit go to overflow (annotation, not throw) so callers
    # can decide whether to triage or surface. Annotated gap.category is added.
    gaps = normalized.get("gaps") or []
    kept, overflow = enforce_quota(gaps)
    normalized["gaps"] = kept
    if overflow:
        normalized["_overflow_findings"] = overflow
        normalized["_overflow_count"] = len(overflow)
    return ValidationResult(True, normalized, None, synthesized=False)


def _synthesize_cancelled(raw: dict | Any, schema_error: str) -> dict:
    """Build a synthesized CANCELLED B-2 response on schema violation.

    Triggers exactly ONE retry (workflow JS sees review_status=CANCELLED, treats
    it like REJECT → fix → re-dispatch). The methodology_artifact gap marks the
    retry as 'framework issue, not A issue' so auto_fix.dispatch does NOT engage
    fix_over_interpretation_gap (would loop).
    """
    raw_str = json.dumps(raw)[:200] if isinstance(raw, (dict, list)) else str(raw)[:200]
    return {
        "review_status": "CANCELLED",
        "confidence": 0.0,
        "summary": f"Schema violation: {schema_error}",
        "reason": f"Schema violation — synthesized CANCELLED for retry. raw={raw_str}",
        "gaps": [
            {
                "severity": "high",
                "evidence_type": "methodology_artifact",
                "canonical_ref": "",
                "message": f"B JSON failed schema validation: {schema_error}. Synthesized CANCELLED — single retry; if it fails again, escalate.",
                "fr_id": None,
            }
        ],
    }


def _downgrade_over_interpretation(b: dict) -> dict:
    """HR-12 regression guard (Bug B fix): over_interpretation caps at medium.

    If a gap is marked evidence_type=over_interpretation but B stated severity=high,
    downgrade to medium. This prevents 5-round deadlock on ambiguous canonical
    phrases (the original HR-12 trigger).

    Returns a deep-enough copy so the caller's dict is never mutated.
    """
    import copy

    out = copy.deepcopy(b)
    gaps = out.get("gaps")
    if not isinstance(gaps, list):
        return out

    for g in gaps:
        if not isinstance(g, dict):
            continue
        if g.get("evidence_type") == "over_interpretation" and g.get("severity") == "high":
            g["severity"] = "medium"
            g["_downgraded_from"] = "high"

    return out


def enforce_escalation(
    b2: dict, round_num: int, max_rounds: int = 5
) -> tuple[EscalationAction, str]:
    """Decide escalation action from B-2 review result.

    Rules (mirrored from plan §[B-2]):
      - APPROVE + no medium/high gap → APPROVE (regardless of round)
      - APPROVE + any medium or high gap → RETRY (fix → re-dispatch),
        escalate_human at round == max_rounds (retry would exceed budget)
      - REJECT  → RETRY until round == max_rounds, then ESCALATE_HUMAN
      - CANCELLED → RETRY (single retry — synthesized CANCELLED is
                    framework-side, not user-fixable); escalate at ceiling

    round_num > max_rounds → ESCALATE_HUMAN (programming-error guard,
    unreachable in normal flow because the round loop terminates at max_rounds).

    Bug history: pre-fix this function short-circuited to ESCALATE_HUMAN
    whenever round_num >= max_rounds regardless of status, wrongly
    escalating APPROVE+all-low at round 5. Hit by taskq Phase 2
    TEST_SPEC.md (B-2 round 5 returned APPROVE + 7×low gaps and was
    wrongly escalated). Now follows the docstring rules exactly.
    """
    if round_num > max_rounds:
        return EscalationAction.ESCALATE_HUMAN, (
            f"HR-12: round_num {round_num} exceeds max_rounds {max_rounds}"
        )

    status = b2.get("review_status", "")
    gaps = b2.get("gaps") or []
    has_medium_or_high = any(
        isinstance(g, dict) and g.get("severity") in ("medium", "high")
        for g in gaps
    )

    if status == "APPROVE":
        if has_medium_or_high:
            if round_num >= max_rounds:
                return EscalationAction.ESCALATE_HUMAN, (
                    f"HR-12: round {round_num}/{max_rounds}, APPROVE with "
                    f"{sum(1 for g in gaps if isinstance(g, dict) and g.get('severity') in ('medium','high'))} "
                    f"medium/high gap(s) still open"
                )
            return EscalationAction.RETRY, (
                f"APPROVE but {sum(1 for g in gaps if isinstance(g, dict) and g.get('severity') in ('medium','high'))} "
                f"medium/high gap(s) remain"
            )
        return EscalationAction.APPROVE, "APPROVE with all gaps low"
    if status == "REJECT":
        if round_num >= max_rounds:
            return EscalationAction.ESCALATE_HUMAN, (
                f"HR-12: reached round {round_num}/{max_rounds} without convergence"
            )
        return EscalationAction.RETRY, "REJECT — fix and re-dispatch"
    if status == "CANCELLED":
        if round_num >= max_rounds:
            return EscalationAction.ESCALATE_HUMAN, (
                f"HR-12: reached round {round_num}/{max_rounds} without convergence"
            )
        return EscalationAction.RETRY, "CANCELLED — framework-side retry"
    # Unknown status — retry under ceiling, escalate at ceiling
    if round_num >= max_rounds:
        return EscalationAction.ESCALATE_HUMAN, (
            f"HR-12: unknown review_status at round {round_num}/{max_rounds}"
        )
    return EscalationAction.RETRY, f"unknown review_status: {status!r} — retrying"
