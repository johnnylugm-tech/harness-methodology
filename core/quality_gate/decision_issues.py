"""Deterministic lifecycle gate for specification decisions deferred past P1.

An identifier ending in ``-deferred`` is an explicit statement that the
canonical requirement is not yet settled.  P2 must register it in the SAB and
either resolve it to durable evidence or explicitly assign a later blocking
phase.  This prevents an implementation agent from silently making an
architecture decision merely because prose was carried forward.
"""

from __future__ import annotations

import re
from pathlib import Path

from core.utils.project_layout import ProjectLayout

_DEFERRED_ID = re.compile(r"\b((?:FR|NFR)-\d+-deferred)\b", re.IGNORECASE)


def deferred_ids_from_srs(project: str | Path) -> set[str]:
    path = ProjectLayout(Path(project)).srs_path
    if not path.is_file():
        return set()
    return {m.upper() for m in _DEFERRED_ID.findall(
        path.read_text(encoding="utf-8", errors="replace"))}


def decision_issue_findings(
    project: str | Path, issues: list | None, *, entering_phase: int,
) -> list[str]:
    """Return every malformed, unregistered, or currently-blocking issue."""
    project = Path(project)
    declared = deferred_ids_from_srs(project)
    rows = issues if isinstance(issues, list) else []
    findings: list[str] = []
    by_id: dict[str, dict] = {}
    for index, row in enumerate(rows, 1):
        if not isinstance(row, dict):
            findings.append(f"decision_issues[{index}] must be a mapping")
            continue
        issue_id = str(row.get("id") or "").upper()
        if not issue_id:
            findings.append(f"decision_issues[{index}] has no id")
            continue
        if issue_id in by_id:
            findings.append(f"decision issue {issue_id} is declared more than once")
            continue
        by_id[issue_id] = row

    for issue_id in sorted(declared - set(by_id)):
        findings.append(
            f"{issue_id} appears in SRS.md but has no decision_issues lifecycle entry"
        )

    # Round 113 站9: a TEST_SPEC case whose Inputs name a deferred id is a
    # test Phase 3 must write against that decision — taskq-sol has 12 such
    # cases over 6 ids, each `precondition="… from ADR (SRS §7 FR-xx-deferred)"`.
    for issue_id, cases in sorted(_test_spec_dependencies(project).items()):
        row = by_id.get(issue_id)
        if row is None:
            if issue_id in declared:
                continue  # already named above as unregistered
            findings.append(
                f"{issue_id} is a precondition of TEST_SPEC case(s) {', '.join(cases)} "
                "but has no decision_issues lifecycle entry"
            )
        elif str(row.get("status") or "").lower() != "resolved" and entering_phase >= 3:
            findings.append(
                f"TEST_SPEC case(s) {', '.join(cases)} depend on {issue_id}, which is "
                f"still open — Phase 3 would have to invent the decision to write them"
            )

    for issue_id, row in sorted(by_id.items()):
        status = str(row.get("status") or "").lower()
        if status not in {"open", "resolved"}:
            findings.append(f"{issue_id} status must be open or resolved")
            continue
        if status == "resolved":
            reason = _resolution_defect(project, issue_id, row)
            if reason:
                findings.append(reason)
            continue
        findings.extend(due_open_decision_findings([row], entering_phase=entering_phase))
    return findings


def due_open_decision_findings(issues: list | None, *, entering_phase: int) -> list[str]:
    """Open rows whose own deadline has arrived — the question for every boundary.

    Round 113 站9. `decision_issue_findings` is asked once, at the P2 exit; an
    open row with `blocks_phase: 5` was never asked again. This is the part of
    it that holds at any boundary: only each row's declared deadline. Whether
    every SRS id is registered stays a P2-exit question, so a project that
    closed Phase 2 before this gate existed is not judged by it a second time.
    """
    findings: list[str] = []
    for row in issues if isinstance(issues, list) else []:
        if not isinstance(row, dict) or str(row.get("status") or "").lower() != "open":
            continue
        issue_id = str(row.get("id") or "").upper()
        blocks = row.get("blocks_phase")
        if not isinstance(blocks, int) or blocks < 2 or blocks > 9:
            findings.append(f"{issue_id} open issue needs integer blocks_phase 2..9")
        elif blocks <= entering_phase:
            findings.append(
                f"{issue_id} remains open and blocks entry to Phase {entering_phase}"
            )
    return findings


def _resolution_defect(project: Path, issue_id: str, row: dict) -> "str | None":
    """Why a `resolved` row's evidence does not close it, or None.

    Round 113 站9. 91fd4ba9 accepted any reference to a file that exists, and
    taskq-sol's ADR.md exists while saying "remain unresolved" of the very
    decisions it would be cited for. The evidence is the RECORD: a line
    reading `<id>: resolved — <decision>`.

    Round 114 站3: found by content in the file the ref names (`path`, or
    `path:N` with N ignored). Pinning the line number added nothing to that
    check but a way for an edit above the record to break it — taskq-open
    re-pinned four refs by hand (55415df) after one inserted line.
    """
    from core.quality_gate.content_ref import record_line, ref_file

    ref = str(row.get("resolution_ref") or "").strip()
    if not ref:
        return f"{issue_id} is resolved but has no resolution_ref"
    if ref_file(project, ref) is None:
        return f"{issue_id} resolution_ref does not resolve: {ref} is not a file in this project"
    if record_line(project, ref, issue_id, ("resolved",)) is None:
        return (f"{issue_id} resolution_ref does not resolve: {ref} has no line reading "
                f"`{issue_id}: resolved — <decision>`")
    return None


def _test_spec_dependencies(project: Path) -> "dict[str, list[str]]":
    """{deferred id: [FR#case, …]} for every TEST_SPEC case whose Inputs name it."""
    path = ProjectLayout(project).test_spec_path
    if not path.is_file():
        return {}
    from core.quality_gate.parsers.spec_assertion_parser import SpecAssertionParser

    try:
        parsed = SpecAssertionParser.parse(
            path.read_text(encoding="utf-8", errors="replace"))
    except ValueError:
        return {}  # a malformed table is check-test-spec-consistency's finding
    deps: dict[str, list[str]] = {}
    for fr_id, (cases, _assertions) in parsed.items():
        for case in cases:
            for value in case.inputs.values():
                for issue_id in {m.upper() for m in _DEFERRED_ID.findall(value)}:
                    deps.setdefault(issue_id, []).append(f"{fr_id}#{case.case_id}")
    return deps

