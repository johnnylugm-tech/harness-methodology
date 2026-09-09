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

    for issue_id, row in sorted(by_id.items()):
        status = str(row.get("status") or "").lower()
        if status not in {"open", "resolved"}:
            findings.append(f"{issue_id} status must be open or resolved")
            continue
        if status == "resolved":
            ref = str(row.get("resolution_ref") or "").strip()
            if not ref:
                findings.append(f"{issue_id} is resolved but has no resolution_ref")
                continue
            rel = ref.split("#", 1)[0].split(":", 1)[0].strip()
            if not rel or Path(rel).is_absolute() or not (project / rel).is_file():
                findings.append(f"{issue_id} resolution_ref does not resolve: {ref}")
            continue
        blocks = row.get("blocks_phase")
        if not isinstance(blocks, int) or blocks < 2 or blocks > 9:
            findings.append(f"{issue_id} open issue needs integer blocks_phase 2..9")
        elif blocks <= entering_phase:
            findings.append(
                f"{issue_id} remains open and blocks entry to Phase {entering_phase}"
            )
    return findings

