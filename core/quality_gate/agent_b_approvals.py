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

# Accepts three line-spec formats and distinguishes them by the separator
# between the two numbers:
#   `path:N`        — single line
#   `path:N-M`      — line range, dash separator (tool/diff convention).
#                     End must be ≥ start and within the file.
#   `path:N:M`      — line + column (legacy contract, e.g. editor jump-to
#                     coordinates). Only the line is validated; the column
#                     is treated as an unverified position-on-line hint.
# An optional trailing `(annotation)` is accepted after the line spec — it is
# discarded by the validator and exists purely so reviewers can attach a human
# note to the citation without breaking the path:line parse. The annotation
# must be parenthesised (or absent); arbitrary unparenthesised trailing prose
# is still rejected, preserving the "no such file" check below.
# Round 26 found the agent-B review was rejected on the run-all-by-workflow
# P1-P8 run because reviewers wrote `SRS.md:103-225` /
# `TRACEABILITY_MATRIX.md:325-348` etc. — the contract previously only
# recognised `path:N:M`, so the dash variant was treated as a whole-file
# reference whose "path" was literally `SRS.md:103-225` (no such file).
# Round 27 (2026-08-04) extended this to accept an optional trailing
# `(annotation)` so `SRS.md:972 (FR-05 §10 verification array ...)` parses as
# path=SRS.md, line=972 — previously the trailing text forced the fallback
# whole-file branch, which tried to resolve `SRS.md:972 (...)` as a literal
# filename and rejected every existing file with "no such file" (observed on
# the taskq-full run-all Phase 1 advance-phase, 4/4 approvals blocked despite
# every cited line existing in-range).
_CITATION = re.compile(
    r"^(?P<path>.+?):(?P<line>\d+)(?:(?P<sep>[-:])(?P<end>\d+))?(?:\s*\([^)]*\))?$"
)

# Conventional phase directories in the harness scaffolding, in resolution
# order. Used as a fallback when a citation's path is a bare filename (no
# directory component) and the explicit project-relative lookup misses. The
# docs_embedded check (below) already accepts either "SRS.md" or
# "01-requirements/SRS.md" as the same document; this keeps citations
# consistent with that contract. The empty leading entry is the project root
# itself, which keeps e.g. `TEST_INVENTORY.yaml` (placed at root by the
# workflow) resolvable.
_PHASE_DIRS: tuple[str, ...] = (
    "",
    "01-requirements",
    "02-architecture",
    "03-development",
    "04-testing",
    "05-verification",
    "06-quality",
    "07-risk",
    "08-config",
    "09-maintenance",
)


def _resolve_citation_path(project: Path, rel: str) -> "Path | None":
    """Resolve a citation's path component to a real file on disk.

    Four resolution modes, in order:
      1. Absolute path — used as-is.
      2. Project-relative path — `project / rel`. The strict, expected case.
      3. Bare filename (no directory separator in `rel`) — search the
         conventional phase directories. Same flexibility the `docs_embedded`
         check grants to bare basenames (see `_norm` below).
      4. Non-bare path that failed at step 2 — basename search across the
         conventional phase directories. Catches the common typo of
         prepending `01-requirements/` to a file that actually lives at
         the repo root (e.g. `TEST_INVENTORY.yaml`, `HANDOVER.md`). When
         multiple candidates exist, prefer the one whose parent directory
         matches `rel`'s first component (so the typo correction lands
         near the reviewer's intent); fall back to root.

    Returns the resolved Path or None if the file cannot be located.
    """
    path = Path(rel)
    if path.is_absolute():
        return path if path.is_file() else None
    candidate = project / rel
    if candidate.is_file():
        return candidate
    # Bare-name search (step 3).
    if "/" not in rel and "\\" not in rel:
        for sub in _PHASE_DIRS:
            candidate = project / sub / rel if sub else project / rel
            if candidate.is_file():
                return candidate
        return None
    # Wrong-prefix basename search (step 4). The reviewer meant a specific
    # file but wrote the wrong directory; locate it by basename and pick
    # the candidate that best matches their stated directory.
    basename = path.name
    if not basename:
        return None
    matches: list[Path] = []
    for sub in _PHASE_DIRS:
        candidate = project / sub / basename if sub else project / basename
        if candidate.is_file():
            matches.append(candidate)
    if not matches:
        return None
    if len(matches) == 1:
        return matches[0]
    first_component = rel.split("/", 1)[0]
    for m in matches:
        if m.parent.name == first_component:
            return m
    return matches[0]  # deterministic tie-break: phase-dir order


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
    only the file's existence is required. Paths are accepted absolute, relative
    to the project root, or (for bare filenames) anywhere in the conventional
    phase directories. Line ranges use either `start-end` or `start:end`.
    """
    bad: list[str] = []
    for raw in citations:
        text = str(raw).strip()
        if not text:
            bad.append("<empty citation>")
            continue
        m = _CITATION.match(text)
        if m:
            rel = m["path"]
            line_no = int(m["line"])
            end_str = m["end"]
            end_line = int(end_str) if end_str else None
            end_sep = m["sep"] if end_str else None
        else:
            rel = text
            line_no = None
            end_line = None
            end_sep = None
        path = _resolve_citation_path(project, rel)
        if path is None:
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
            continue
        # Only the dash separator is a range — a colon means the legacy
        # line+column form (`path:N:M`), and the second number is the column
        # on line N, not a second line to validate. (A reviewer who writes
        # `SRS.md:103-225` is asserting they read the block from 103 to 225
        # — accept that, and only reject when the end genuinely runs off the
        # end of the file or sits before the start.)
        if end_line is not None and end_sep == "-":
            if end_line < line_no:
                bad.append(f"{text} (range end {end_line} is before start {line_no})")
                continue
            if end_line > total:
                bad.append(f"{text} (range end {end_line} exceeds file length {total})")
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
