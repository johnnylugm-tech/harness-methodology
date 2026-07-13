#!/usr/bin/env python3
"""structured_b_review.py — Bash-callable wrapper for B review JSON validation.

Root cause (F of 5-meta-pattern convergence plan): workflow JS calls
`agent()` for B-2 review and receives free-form text. The text USUALLY
contains JSON, but the agent may wrap it in prose, paraphrase it, or
fabricate fields. Previously workflow JS had to parse this text with
`balancedJsonAt` (the walker at root of 3 documented bugs: #122, #134, #135).

This module is the framework-side deterministic bridge:
  1. Take B's raw text output
  2. Extract the embedded JSON via balanced brace walking
  3. Validate against `b_review.schema.json` via `core/review_schema_validator`
  4. Apply downgrade rules (over_interpretation cap, methodology_artifact cap)
  5. Return a clean structured JSON for workflow JS to consume directly

CLI:
  python3 structured_b_review.py --raw-text PATH
                                 [--json-out PATH]

Where PATH is a file containing B's raw agent() output (free text + JSON).
Output JSON shape:
  {
    "status": "OK|CANCELLED|UNRECOVERABLE",
    "extraction": {
      "found": bool,           # did we find balanced JSON in the raw text?
      "candidates_considered": int,
      "byte_offset": int | null,
      "diagnostic": str | null
    },
    "validation": {
      "valid": bool,
      "synthesized": bool,     # true if framework synthesized CANCELLED on schema fail
      "errors": list[str],     # jsonschema validation errors (empty if valid)
    },
    "review_status": "APPROVE|REJECT|CANCELLED",
    "gaps": [...],             # post-downgrade, post-verification gaps
    "diagnostic": str | null
  }

Commonality: phase-agnostic. Used by all 8 phase workflow JS files.
Workflow JS pattern:
  bash('python3 structured_b_review.py --raw-text $RAW --json-out /tmp/b.json')
  result = JSON.parse(fs.readFileSync('/tmp/b.json'))
  review_status = result.review_status
  gaps = result.gaps

Note: workflow JS cannot import Python directly (forbids require()), so this
script is the bridge. The Python framework-side `core.review_schema_validator`
remains the authoritative validator; this script is its CLI face for JS.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


# Import the existing framework validator (authoritative)
try:
    from core.review_schema_validator import EscalationAction, enforce_escalation, validate_b_output
except ImportError:
    # Allow running from harness/ root or scripts/ dir
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from core.review_schema_validator import EscalationAction, enforce_escalation, validate_b_output

from scripts.b_gap_validator import validate_b2_response


# ---------------------------------------------------------------------------
# JSON extraction from free text — balanced brace walker
# ---------------------------------------------------------------------------

def _extract_json_candidates(raw_text: str) -> list[tuple[int, int]]:
    """Return list of (start, end) byte offsets for balanced JSON candidates.

    Walks the text looking for top-level '{' and matching '}', respecting
    string literals (with escapes) so braces inside strings don't count.
    Returns candidates in source order.

    A "candidate" is a span where the brace count balances to zero at end.
    Does NOT verify the candidate is valid JSON — that's the caller's job.
    """
    candidates: list[tuple[int, int]] = []
    n = len(raw_text)

    i = 0
    while i < n:
        # Find next '{' at brace-depth 0
        while i < n and raw_text[i] != "{":
            i += 1
        if i >= n:
            break

        start = i
        depth = 0
        in_string = False
        escape = False
        j = i
        while j < n:
            ch = raw_text[j]
            if in_string:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_string = False
                # else: regular char in string
            else:
                if ch == '"':
                    in_string = True
                elif ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        candidates.append((start, j + 1))
                        i = j + 1
                        break
            j += 1
        else:
            # Reached end without closing — abandon this candidate
            break

    return candidates


def _try_parse_json(text: str, span: tuple[int, int]) -> dict | None:
    """Try to parse text[span[0]:span[1]] as JSON. Return dict or None."""
    candidate = text[span[0]:span[1]]
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def extract_b_review_json(raw_text: str) -> tuple[dict | None, dict]:
    """Walk raw_text, return (parsed_dict, extraction_meta).

    extraction_meta: {found, candidates_considered, byte_offset, diagnostic}
    """
    candidates = _extract_json_candidates(raw_text)
    meta: dict[str, Any] = {
        "found": False,
        "candidates_considered": len(candidates),
        "byte_offset": None,
        "diagnostic": None,
    }

    if not candidates:
        meta["diagnostic"] = "no balanced JSON object found in raw text"
        return None, meta

    # Prefer the LAST candidate (LLMs often restate JSON in a final summary)
    for span in reversed(candidates):
        parsed = _try_parse_json(raw_text, span)
        if parsed is not None:
            meta["found"] = True
            meta["byte_offset"] = span[0]
            return parsed, meta

    meta["diagnostic"] = f"{len(candidates)} balanced brace span(s) found but none parse as JSON object"
    return None, meta


# ---------------------------------------------------------------------------
# Top-level structured_b_review
# ---------------------------------------------------------------------------


def _with_escalation(out: dict[str, Any], round_num: int | None, max_rounds: int) -> dict[str, Any]:
    """Attach escalation_action/escalation_reason if round_num was supplied.

    round_num=None (the default) means the caller didn't ask for an escalation
    decision — leave those keys absent so pre-existing callers/tests that only
    care about status/review_status/gaps are unaffected.
    """
    if round_num is None:
        return out
    action, reason = enforce_escalation(
        {"review_status": out.get("review_status"), "gaps": out.get("gaps") or []},
        round_num, max_rounds,
    )
    out["escalation_action"] = action.value if isinstance(action, EscalationAction) else action
    out["escalation_reason"] = reason
    return out


def structured_b_review(raw_text: str, phase: int = 0,
                        deliverable: str = "",
                        round_num: int | None = None,
                        max_rounds: int = 5,
                        doc_content: str | None = None,
                        vocabulary: dict[str, list[str]] | None = None) -> dict[str, Any]:
    """End-to-end: extract JSON from raw text, validate, return structured dict.

    round_num/max_rounds (optional): when supplied, also computes the
    EscalationAction (approve|retry|escalate_human) via
    core.review_schema_validator.enforce_escalation — the single source of
    truth for B-2 round-loop control flow, replacing the hand-rolled
    hasHighGap()/round-loop logic previously duplicated in workflow JS.

    doc_content/vocabulary (optional): when doc_content is supplied, runs
    scripts.b_gap_validator.validate_b2_response against it and overrides
    each gap's severity with its deterministic severity_recommendation
    before escalation is computed — this replaces workflow JS's "X1 self-
    verify" (a second LLM agent re-checking the first) and the VETO guard
    (which let that second LLM's self-reported confidence silently flip a
    REJECT to APPROVE) with a deterministic, non-LLM check. Note a REJECT
    review_status is never overridden into APPROVE by this — only the gap
    severities feed into escalation, so a script can suppress a false-alarm
    gap inside an APPROVE, but it can never promote a genuine REJECT.
    """
    extracted, extraction_meta = extract_b_review_json(raw_text)

    if extracted is None:
        # No JSON found — synthesize CANCELLED with diagnostic gap
        synthesized_gap = {
            "severity": "high",
            "evidence_type": "methodology_artifact",
            "canonical_ref": "",
            "message": (
                f"B reviewer returned no parseable JSON: {extraction_meta['diagnostic']}. "
                "Framework synthesized CANCELLED status; one B-2 retry will be triggered."
            ),
            "_synthesized": True,
        }
        return _with_escalation({
            "status": "CANCELLED",
            "extraction": extraction_meta,
            "validation": {
                "valid": False,
                "synthesized": True,
                "errors": [extraction_meta["diagnostic"] or "no json"],
            },
            "review_status": "CANCELLED",
            "gaps": [synthesized_gap],
            "diagnostic": extraction_meta["diagnostic"],
        }, round_num, max_rounds)

    # We have a parsed dict — validate against b_review.schema.json
    result = validate_b_output(extracted, phase=phase, deliverable=deliverable)

    validation_meta = {
        "valid": result.valid,
        "synthesized": result.synthesized,
        "errors": [result.error] if result.error else [],
    }

    if not result.valid and result.synthesized:
        # Schema violation — framework synthesized CANCELLED
        return _with_escalation({
            "status": "CANCELLED",
            "extraction": extraction_meta,
            "validation": validation_meta,
            "review_status": "CANCELLED",
            "gaps": result.normalized.get("gaps", []),
            "diagnostic": result.error,
        }, round_num, max_rounds)

    if not result.valid:
        # Not a dict at all (shouldn't happen here since extract_b_review_json
        # returns dict only)
        return {
            "status": "UNRECOVERABLE",
            "extraction": extraction_meta,
            "validation": validation_meta,
            "review_status": None,
            "gaps": [],
            "diagnostic": result.error,
        }

    # Schema-valid — apply deterministic gap/reason/citation verification
    # before escalation, if a doc to verify against was supplied.
    gaps = result.normalized.get("gaps", [])
    b2_verification: dict[str, Any] | None = None
    if doc_content is not None:
        from core.review_quota import categorize_finding
        b2_verification = validate_b2_response(result.normalized, doc_content, vocabulary)
        recs = b2_verification["gaps"]["gaps"]
        for gap, rec in zip(gaps, recs):
            gap["severity"] = rec["severity_recommendation"]
            # Re-derive category: enforce_quota (inside validate_b_output, above)
            # categorized on the pre-verification severity — recompute so
            # category and severity never disagree in the returned gap.
            gap["category"] = categorize_finding(gap)

    out = {
        "status": "OK",
        "extraction": extraction_meta,
        "validation": validation_meta,
        "review_status": result.normalized.get("review_status"),
        "gaps": gaps,
        "diagnostic": None,
    }
    if b2_verification is not None:
        out["b2_verification"] = b2_verification
    return _with_escalation(out, round_num, max_rounds)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _cli() -> int:
    parser = argparse.ArgumentParser(
        description="Extract + validate B reviewer JSON from raw agent output."
    )
    parser.add_argument(
        "--raw-text",
        required=True,
        help="Path to file containing B reviewer's raw free-text output.",
    )
    parser.add_argument(
        "--phase",
        type=int,
        default=0,
        help="Phase number (for validator context).",
    )
    parser.add_argument(
        "--deliverable",
        default="",
        help="Deliverable name (for validator context).",
    )
    parser.add_argument(
        "--round",
        type=int,
        default=None,
        dest="round_num",
        help="Current B-2 round number (1-based). When set, also computes "
             "escalation_action (approve|retry|escalate_human) via "
             "core.review_schema_validator.enforce_escalation.",
    )
    parser.add_argument(
        "--max-rounds",
        type=int,
        default=5,
        help="HR-12 round ceiling (default: 5). Only used when --round is set.",
    )
    parser.add_argument(
        "--doc-content",
        default=None,
        help="Path to the deliverable B is reviewing. When set, deterministically "
             "verifies gaps/reason/citations against it (scripts.b_gap_validator) "
             "and overrides gap severities with the verified recommendation before "
             "escalation is computed.",
    )
    parser.add_argument(
        "--vocab",
        default=None,
        help="Optional JSON file with custom technical vocabulary (passed through "
             "to b_gap_validator; defaults to its built-in vocabulary).",
    )
    parser.add_argument(
        "--json-out",
        default=None,
        help="If set, write JSON to this path; otherwise print to stdout.",
    )
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    raw_path = Path(args.raw_text)
    if not raw_path.exists():
        print(f"[structured_b_review] ERROR: raw text file missing: {raw_path}",
              file=sys.stderr)
        return 2

    try:
        raw_text = raw_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        print(f"[structured_b_review] ERROR: raw text read failed: {e}",
              file=sys.stderr)
        return 2

    doc_content = None
    if args.doc_content:
        doc_path = Path(args.doc_content)
        if not doc_path.exists():
            print(f"[structured_b_review] ERROR: doc-content file missing: {doc_path}",
                  file=sys.stderr)
            return 2
        try:
            doc_content = doc_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as e:
            print(f"[structured_b_review] ERROR: doc-content read failed: {e}",
                  file=sys.stderr)
            return 2

    vocabulary = None
    if args.vocab:
        from scripts.b_gap_validator import load_vocabulary
        vocabulary = load_vocabulary(args.vocab)

    result = structured_b_review(
        raw_text, phase=args.phase, deliverable=args.deliverable,
        round_num=args.round_num, max_rounds=args.max_rounds,
        doc_content=doc_content, vocabulary=vocabulary,
    )

    json_text = json.dumps(result, indent=2, ensure_ascii=False)
    if args.json_out:
        Path(args.json_out).write_text(json_text, encoding="utf-8")
    else:
        print(json_text)

    if not args.quiet:
        s = result["status"]
        rs = result.get("review_status") or "(none)"
        gaps_count = len(result.get("gaps", []))
        msg = f"[structured_b_review] {s} review_status={rs} gaps={gaps_count}"
        if result.get("escalation_action"):
            msg += f" escalation_action={result['escalation_action']}"
        if result.get("diagnostic"):
            msg += f" — {result['diagnostic']}"
        print(msg, file=sys.stderr)

    # Exit codes (when --round given, escalation_action is authoritative):
    #   0 = approve, 1 = retry, 2 = escalate_human
    # Fallback (no --round given, no escalation computed):
    #   0 OK, 1 CANCELLED (synthesized — recoverable, B-2 retries), 2 UNRECOVERABLE
    action = result.get("escalation_action")
    if action is not None:
        if action == "approve":
            return 0
        if action == "escalate_human":
            return 2
        return 1
    if result["status"] == "OK":
        return 0
    if result["status"] == "CANCELLED":
        return 1
    return 2


if __name__ == "__main__":
    sys.exit(_cli())
