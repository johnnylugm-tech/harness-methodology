"""Agent-B approval verification (moved verbatim from harness_cli.py, S4e).

Checks .methodology/agent_b_approvals/<deliverable>.json files carry a
substantive APPROVE (anti-rubber-stamp minimum reason length).
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

__all__ = [
    "MIN_REVIEW_REASON_CHARS",
    "REQUIRED_EMBEDDED_DOCS",
    "unresolvable_citations",
    "verify_agent_b_approvals_core",
]

# Documents that Agent B must embed per phase (SAD.md doesn't exist until P2)
REQUIRED_EMBEDDED_DOCS: dict[int, list[str]] = {
    1: ["SRS.md"],
    2: ["SRS.md", "SAD.md"],
    6: ["QUALITY_REPORT.md", "RELEASE_NOTES.md", "FINAL_SIGN_OFF.md", "VERIFICATION_REPORT.md"],
}

MIN_REVIEW_REASON_CHARS = 40  # minimum length for an Agent B APPROVE reason to count as substantive

_CITATION = re.compile(r"^(?P<path>.+?):(?P<line>\d+)(?::\d+)?$")


def unresolvable_citations(project: Path, citations: "list") -> "list[str]":
    """Citations whose `file:line` does not name a position that exists.

    Round 24 站2c. Before this, a citation only had to be a non-empty list
    entry — nothing checked that the path resolved or that the line was inside
    the file. In the run-all-by-workflow P1-P8 run, Agent B approved
    QUALITY_REPORT.md with a reason describing "14 dimensions + Mutation Testing
    excluded by feature flag" (text the report does not contain — the report
    said "Mutation Testing | 0/100 | FAIL") and cited `QUALITY_REPORT.md:7` and
    `:13`, neither of which is the Mutation Testing row. The citation format was
    valid; the citation was not evidence.

    DELIBERATE SCOPE: existence only. This says the reviewer opened a real file
    at a real line — it does NOT say the cited line supports the stated reason.
    Checking that needs a second LLM judgement, which would rebuild the verdict
    on top of the thing under review (the Round 21 "verdict before truth"
    failure). Bounded, mechanical, zero false positives is worth more here than
    a smarter check that can be argued with.

    A citation with no `:line` suffix is treated as a whole-file reference and
    only the file's existence is required. Paths are accepted absolute or
    relative to the project root.
    """
    bad: list[str] = []
    for raw in citations:
        text = str(raw).strip()
        if not text:
            bad.append("<empty citation>")
            continue
        m = _CITATION.match(text)
        rel, line_no = (m["path"], int(m["line"])) if m else (text, None)
        path = Path(rel)
        if not path.is_absolute():
            path = project / rel
        if not path.is_file():
            bad.append(f"{text} (no such file)")
            continue
        if line_no is None:
            continue
        try:
            total = len(path.read_text(encoding="utf-8", errors="replace").splitlines())
        except OSError as exc:
            bad.append(f"{text} (unreadable: {exc})")
            continue
        if line_no < 1 or line_no > total:
            bad.append(f"{text} (file has {total} lines)")
    return bad


def verify_agent_b_approvals_core(
    project: Path, phase: int, deliverable_ids: "list[str]"
) -> "tuple[bool, str]":
    """Verify agent_b_approvals/<id>.json files exist and carry APPROVE status.

    Returns (passed, report) where report is a human-readable summary.
    Uses phase-appropriate required_embedded_docs (P1 only needs SRS.md;
    P2 needs SRS.md + SAD.md).
    """
    required_docs = REQUIRED_EMBEDDED_DOCS.get(phase, ["SRS.md", "SAD.md"])
    approvals_dir = project / ".methodology" / "agent_b_approvals"
    lines: list[str] = [
        f"[verify-agent-b] Phase {phase} — checking {len(deliverable_ids)} deliverables",
        f"  Approvals dir : {approvals_dir}",
    ]
    missing: list[str] = []
    rejected: list[str] = []
    errors: list[str] = []

    for did in deliverable_ids:
        approval_file = approvals_dir / f"{did}.json"
        if not approval_file.exists():
            missing.append(did)
            continue
        try:
            data = json.loads(approval_file.read_text(encoding="utf-8"))
        except Exception as exc:  # pylint: disable=broad-exception-caught
            print(f"[WARN] agent_b_approvals: {did} approval file JSON parse error: {exc}", file=sys.stderr)
            errors.append(f"{did}: JSON parse error — {exc}")
            continue
        status = data.get("review_status", "")
        if status != "APPROVE":
            rejected.append(f"{did}: review_status={status!r} (expected APPROVE)")
            continue
        # A1 structure guard: an APPROVE must carry a substantive review, not an empty
        # rubber-stamp. This cannot verify Agent B authenticity (a structural limit of a
        # document-phase review) but it blocks the trivially-faked empty APPROVE.
        _reason = str(data.get("reason", "")).strip()
        _citations = data.get("citations", [])
        if len(_reason) < MIN_REVIEW_REASON_CHARS:
            errors.append(
                f"{did}: APPROVE with empty/too-short reason "
                f"(need ≥{MIN_REVIEW_REASON_CHARS} chars of review rationale)"
            )
            continue
        if not isinstance(_citations, list) or not _citations:
            errors.append(
                f"{did}: APPROVE without citations[] — Agent B must cite what it reviewed."
            )
            continue
        _bad_citations = unresolvable_citations(project, _citations)
        if _bad_citations:
            errors.append(
                f"{did}: citation(s) do not point at a position that exists — "
                + "; ".join(_bad_citations)
                + ". Re-read the file and cite the line you actually read."
            )
            continue
        embedded = data.get("docs_embedded", [])
        # Bug v26 fix (2026-06-29): required_docs may be basenames ("SRS.md") while
        # B agent writes full repo-relative paths ("01-requirements/SRS.md"). Normalize
        # both sides to a comparable form (basename + full path) before the membership
        # check so neither authoring convention triggers a false-positive missing-docs
        # failure. Previously the strict `d not in embedded` rejected "SRS.md" because
        # the list contained "01-requirements/SRS.md" — a contract mismatch, not a
        # real coverage gap.
        def _norm(s: str) -> set[str]:
            p = Path(s)
            return {s, p.name, str(p).lstrip("./")}
        embedded_norm: set[str] = set()
        for e in embedded:
            embedded_norm |= _norm(str(e))
        missing_docs = [d for d in required_docs if not (_norm(d) & embedded_norm)]
        if missing_docs:
            errors.append(
                f"{did}: docs_embedded missing {missing_docs} — "
                "Agent B prompt must embed the required source documents."
            )

    passed = not (missing or rejected or errors)
    if passed:
        lines.append(f"  ✓ All {len(deliverable_ids)} Agent B approvals verified.")
    else:
        lines.append("\n[BLOCKED] Agent B approval verification failed:")
        if missing:
            lines.append(f"  Missing approval files ({len(missing)}):")
            for d in missing:
                lines.append(f"    • {approvals_dir / d}.json")
        if rejected:
            lines.append(f"  Non-APPROVE statuses ({len(rejected)}):")
            for r in rejected:
                lines.append(f"    • {r}")
        if errors:
            lines.append(f"  Schema/content errors ({len(errors)}):")
            for e in errors:
                lines.append(f"    • {e}")
        lines.append(
            "\n  Fix: ensure Agent B writes approval JSON for each deliverable:\n"
            '    {"fr": "<id>", "review_status": "APPROVE", '
            '"docs_embedded": ["SRS.md"], "confidence": 0.9}'
        )
    return passed, "\n".join(lines)
