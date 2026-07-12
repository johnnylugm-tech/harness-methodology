#!/usr/bin/env python3
"""b_gap_validator.py — deterministic B gap claim verification.

Root cause (F of 5-meta-pattern convergence plan): workflow JS v7 added
`validateBGaps()` — JS code extracted "distinctive terms" from B reviewer's
gap messages and verified at least 1 term appeared in actual document content.
The intent was good (downgrade gaps grounded in hallucinated content to 'low'),
but the implementation was a workflow-JS LLM-coupled helper:
  - Terms extracted by regex over LLM-emitted strings → fragile
  - Verification done in JS over 60k+ token DOC embeds → slow
  - If B reviewer changed output style, terms extracted would miss → false
    negative / false positive
  - Same logic duplicated in 5 sites (sub-tasks 1-5 of P1)

Each cross-phase port risked drift (v7 applied to phase1; phase2 port was
a separate commit with subtle differences). This module is the framework-
side deterministic replacement:

  - `validate_gaps(gaps, doc_content, technical_vocabulary=None) -> dict`
    Returns a structured report with one row per gap:
      verified: bool — at least one distinctive term appears in doc_content
      matched_terms: list[str] — terms that DID appear
      unverified_claims: list[str] — claims without any matched term
      severity_recommendation: 'low'|'medium'|'high' — based on verification
        + original severity + evidence_type
    The severity_recommendation is what workflow JS uses to override B's
    claim. Verified high → keep high; unverified high → downgrade to low.

CLI:
  python3 b_gap_validator.py --gaps gaps.json --doc-content path/to/file
                             [--vocab path/to/vocab.json]
                             [--json-out out.json]

Commonality: phase-agnostic. Same engine validates B output for all 8 phases.
Used by workflow JS in lieu of in-line validateBGaps() implementation.

Determinism: same input gaps + same doc_content → same output. No LLM
involvement in verification, so no fabrication surface.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Default technical vocabulary — distinctive terms commonly mis-hallucinated
# (Phase 1 run wf_f002d30a-b34: B claimed 'Node.js library' for a Python
# taskq; vocabulary catches this by requiring terms to be present in doc).
# ---------------------------------------------------------------------------

DEFAULT_TECHNICAL_VOCAB: dict[str, list[str]] = {
    # Languages / runtimes
    "python": ["python", "python3", "py3", ".py", "pyright", "pytest", "ruff", "mypy"],
    "node":   ["node", "node.js", "nodejs", "javascript", "typescript", "npm"],
    # Task / queue terminology (generic — never a target project's package name)
    "task_queue": ["task_queue", "task queue", "tasks.json", "submit", "status", "clear", "queue"],
    "redis":  ["redis", "rq", "celery", "broker"],
    "rabbitmq": ["rabbitmq", "amqp", "pika"],
    # Web / API
    "http":   ["http", "https", "rest", "api", "endpoint", "request", "response"],
    "grpc":   ["grpc", "protobuf", "proto"],
    # Storage
    "sql":    ["sql", "sqlite", "postgres", "mysql", "select", "insert", "update"],
    "kv":     ["key-value", "key value", "kv store", "dict", "map"],
    # Concepts
    "async":  ["async", "asyncio", "await", "concurrent", "threading"],
    "subprocess": ["subprocess", "exec", "fork", "spawn", "Popen"],
    # Security
    "shell":  ["shell", "shell=true", "shell=false", "bash"],
    "redaction": ["redact", "redacted", "redaction", "sk-", "token"],
}


def load_vocabulary(vocab_path: str | Path | None) -> dict[str, list[str]]:
    """Load technical vocabulary from JSON file or return defaults."""
    if vocab_path is None:
        return DEFAULT_TECHNICAL_VOCAB
    p = Path(vocab_path)
    if not p.exists():
        print(f"[b_gap_validator] WARNING: vocab file missing: {p}, using defaults",
              file=sys.stderr)
        return DEFAULT_TECHNICAL_VOCAB
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        print(f"[b_gap_validator] WARNING: vocab file unreadable: {e}, using defaults",
              file=sys.stderr)
        return DEFAULT_TECHNICAL_VOCAB


# ---------------------------------------------------------------------------
# Term extraction — pull distinctive terms from gap.message
# ---------------------------------------------------------------------------

# Pattern: quoted strings (single or double)
_QUOTED_RE = re.compile(r'"([^"\\]*(?:\\.[^"\\]*)*)"|\'([^\'\\]*(?:\\.[^\'\\]*)*)\'')

# Pattern: backtick code spans
_BACKTICK_RE = re.compile(r"`([^`]+)`")

# Pattern: identifiers with digits / underscores (FR-01, ac_fr02_3, test_fr07)
_IDENT_RE = re.compile(r"\b[A-Za-z][A-Za-z0-9_-]*\d[A-Za-z0-9_-]*\b")

# Pattern: technical terms from vocabulary (longest-match-first)
# Built dynamically from vocabulary to avoid recompiling per call.


def _build_vocab_regex(vocab: dict[str, list[str]]) -> re.Pattern[str]:
    """Compile a single case-insensitive regex matching any vocab term.

    Uses word boundaries where possible; falls back to literal substring
    for terms containing special chars (e.g. `shell=true`).
    """
    all_terms: list[str] = []
    for term_list in vocab.values():
        for term in term_list:
            all_terms.append(term)
    # Sort by length DESC so longer matches win (e.g. "shell=true" before "shell")
    all_terms.sort(key=len, reverse=True)
    # Escape each term and join with |. Use \b boundaries only for word-y terms.
    parts: list[str] = []
    for term in all_terms:
        escaped = re.escape(term)
        # Add word boundary only if term starts/ends with word char
        if re.match(r"\w", term) and re.search(r"\w$", term):
            parts.append(rf"\b{escaped}\b")
        else:
            parts.append(escaped)
    return re.compile("|".join(parts), re.IGNORECASE)


def extract_terms(gap_message: str,
                  vocabulary_regex: re.Pattern[str] | None = None) -> list[str]:
    """Pull distinctive terms from a gap message.

    Sources (in priority order):
    1. Quoted strings: "foo bar", 'baz'
    2. Backtick code spans: `ac_fr02_3`
    3. Identifier-with-digit tokens: FR01, ac_fr02_3, test_fr07
    4. Vocabulary matches: python, taskq, redis, etc.

    Returns de-duplicated terms preserving first-seen order.
    """
    seen: set[str] = set()
    terms: list[str] = []

    def add(term: str) -> None:
        t = term.strip()
        if not t or len(t) < 2:
            return
        key = t.lower()
        if key in seen:
            return
        seen.add(key)
        terms.append(t)

    # 1. Quoted strings
    for m in _QUOTED_RE.finditer(gap_message):
        for group in m.groups():
            if group:
                add(group)

    # 2. Backtick spans
    for m in _BACKTICK_RE.finditer(gap_message):
        add(m.group(1))

    # 3. Identifier-with-digit tokens
    for m in _IDENT_RE.finditer(gap_message):
        add(m.group(0))

    # 4. Vocabulary matches
    if vocabulary_regex is not None:
        for m in vocabulary_regex.finditer(gap_message):
            add(m.group(0))

    return terms


def verify_gap_against_doc(gap_message: str, doc_content: str,
                          vocabulary_regex: re.Pattern[str] | None = None
                          ) -> tuple[list[str], list[str]]:
    """Return (matched_terms, unverified_claims).

    unverified_claims are quoted/backtick substrings from the gap that do NOT
    appear in doc_content. (Identifiers and vocab matches are checked too,
    but those are usually generic and many will appear.)
    """
    terms = extract_terms(gap_message, vocabulary_regex)
    doc_lower = doc_content.lower()

    matched: list[str] = []
    unverified: list[str] = []

    for term in terms:
        # Bug M18 fix: previous code used `term in doc_lower` (substring
        # containment), so short terms like "py" matched "pyright",
        # "python", "type.py" — inflating matched_terms and verification
        # rates. Use word-boundary regex matching instead.
        pattern = re.compile(rf"\b{re.escape(term.lower())}\b")
        if pattern.search(doc_lower):
            matched.append(term)
        else:
            unverified.append(term)

    return matched, unverified


# ---------------------------------------------------------------------------
# Severity recommendation — the workflow JS override
# ---------------------------------------------------------------------------


def recommend_severity(
    gap: dict[str, Any],
    matched_terms: list[str],
    unverified_claims: list[str],
) -> str:
    """Return the severity that workflow JS should apply for this gap.

    Rules:
    - Original severity 'low' → keep 'low'
    - Original 'medium' + ALL claims unverified → downgrade to 'low'
      (B may have produced a medium-severity gap about a non-existent feature)
    - Original 'high' + ALL claims unverified → downgrade to 'low'
      (this is the v7 validateBGaps core behavior: hallucinated high → low)
    - Original 'medium' + AT LEAST ONE matched term → keep 'medium'
    - Original 'high' + AT LEAST ONE matched term → keep 'high'
    - evidence_type='methodology_artifact' → never escalate, always 'low'
      (consistent with review_schema_validator.py: schema downgrades these)
    - evidence_type='over_interpretation' → cap at 'medium' regardless
      (consistent with Bug B fix)

    Workflow JS calls this with the validated gap dict and uses the returned
    string to override gap.severity before the hasHighGap() check.
    """
    original = (gap.get("severity") or "low").lower()
    evidence_type = (gap.get("evidence_type") or "").lower()

    # Hard caps by evidence_type
    if evidence_type == "methodology_artifact":
        return "low"
    if evidence_type == "over_interpretation" and original == "high":
        return "medium"

    # If we extracted any terms, check whether they match
    # If gap has no extractable terms at all, treat as auto-verified (e.g.,
    # very short gap like "see canonical_ref") — workflow should not penalize
    if not matched_terms and not unverified_claims:
        return original  # unchanged

    # If unverified_claims is non-empty AND matched_terms is empty: pure hallucination
    if unverified_claims and not matched_terms:
        return "low"

    # Otherwise: at least one term verified → keep original
    return original


# ---------------------------------------------------------------------------
# Top-level validate_gaps
# ---------------------------------------------------------------------------


def validate_gaps(
    gaps: list[dict[str, Any]],
    doc_content: str,
    vocabulary: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    """Validate every gap against doc_content.

    Returns a structured report. Workflow JS consumes the per-gap rows
    and applies the severity_recommendation override.
    """
    if vocabulary is None:
        vocabulary = DEFAULT_TECHNICAL_VOCAB
    vocab_re = _build_vocab_regex(vocabulary)

    rows: list[dict[str, Any]] = []
    severity_counter: dict[str, int] = {"high": 0, "medium": 0, "low": 0}
    stats: dict[str, Any] = {
        "total": len(gaps),
        "verified_count": 0,
        "downgraded_count": 0,
        "by_original_severity": dict(severity_counter),
        "by_recommended_severity": dict(severity_counter),
    }

    for i, gap in enumerate(gaps):
        message = (gap.get("message") or "").strip()
        if not message:
            rows.append({
                "gap_index": i,
                "verified": False,
                "matched_terms": [],
                "unverified_claims": [],
                "severity_recommendation": "low",
                "diagnostic": "empty gap message; cannot verify",
            })
            stats["downgraded_count"] += 1
            stats["by_recommended_severity"]["low"] += 1
            continue

        matched, unverified = verify_gap_against_doc(message, doc_content, vocab_re)
        recommendation = recommend_severity(gap, matched, unverified)

        original = (gap.get("severity") or "low").lower()
        stats["by_original_severity"][original] = stats["by_original_severity"].get(original, 0) + 1
        stats["by_recommended_severity"][recommendation] = stats["by_recommended_severity"].get(recommendation, 0) + 1
        if matched:
            stats["verified_count"] += 1
        if recommendation != original:
            stats["downgraded_count"] += 1

        rows.append({
            "gap_index": i,
            "gap_severity": original,
            "gap_evidence_type": gap.get("evidence_type"),
            "gap_canonical_ref": gap.get("canonical_ref"),
            "verified": bool(matched),
            "matched_terms": matched,
            "unverified_claims": unverified,
            "severity_recommendation": recommendation,
        })

    return {
        "summary": stats,
        "gaps": rows,
        "vocabulary_size": sum(len(v) for v in vocabulary.values()),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _cli() -> int:
    parser = argparse.ArgumentParser(
        description="Deterministic B gap claim verifier — replaces validateBGaps() JS code."
    )
    parser.add_argument(
        "--gaps",
        required=True,
        help="Path to JSON file containing list of gap dicts.",
    )
    parser.add_argument(
        "--doc-content",
        required=True,
        help="Path to the document B is reviewing (content to verify terms against).",
    )
    parser.add_argument(
        "--vocab",
        default=None,
        help="Optional JSON file with custom technical vocabulary.",
    )
    parser.add_argument(
        "--json-out",
        default=None,
        help="If set, write JSON result to this path; otherwise print to stdout.",
    )
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    gaps_path = Path(args.gaps)
    doc_path = Path(args.doc_content)
    if not gaps_path.exists():
        print(f"[b_gap_validator] ERROR: gaps file missing: {gaps_path}", file=sys.stderr)
        return 2
    if not doc_path.exists():
        print(f"[b_gap_validator] ERROR: doc file missing: {doc_path}", file=sys.stderr)
        return 2

    try:
        gaps = json.loads(gaps_path.read_text(encoding="utf-8"))
        if not isinstance(gaps, list):
            print(f"[b_gap_validator] ERROR: gaps JSON must be a list, got {type(gaps).__name__}",
                  file=sys.stderr)
            return 2
    except json.JSONDecodeError as e:
        print(f"[b_gap_validator] ERROR: gaps JSON parse failed: {e}", file=sys.stderr)
        return 2

    try:
        doc_content = doc_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        print(f"[b_gap_validator] ERROR: doc read failed: {e}", file=sys.stderr)
        return 2

    vocabulary = load_vocabulary(args.vocab)
    result = validate_gaps(gaps, doc_content, vocabulary)

    json_text = json.dumps(result, indent=2, ensure_ascii=False)
    if args.json_out:
        Path(args.json_out).write_text(json_text, encoding="utf-8")
    else:
        print(json_text)

    if not args.quiet:
        s = result["summary"]
        print(f"[b_gap_validator] {s['total']} gap(s); "
              f"verified={s['verified_count']} "
              f"downgraded={s['downgraded_count']} "
              f"vocab_terms={result['vocabulary_size']}",
              file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(_cli())
