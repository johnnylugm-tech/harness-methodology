"""Round 113 站4 — does a `SPEC.md` line citation point at a line with content?

Phase 1 deliverables cite the canonical spec by line number. Nothing read what
those numbers pointed at: Agent B saw taskq-sol's off-by-one citations, called
them non-blocking, and approved. The only decidable question without reading
prose is whether the cited line (the first line of a range) exists and is
neither blank nor a table separator — so that is the whole rule. A shift that
lands on another content line passes; that is this check's honest limit.

Three citation forms, each measured in the corpus (972 citations, 7 projects):
`SPEC.md:N[-M]`, `SPEC.md LN[-M]`, and `SPEC.md line(s) N[-M]` (with or
without backticks and a "(root)" qualifier). `SPEC.md` must not be the tail of
another name — `TEST_SPEC.md:12` cites a different file.

Two consumers, one rule: advance-phase blocks on it at the Phase 1 exit, and
`scripts/structured_b_review.py` raises it inside the Phase 1 A/B loop, where
Agent A can still correct the number — the Advance step has no fixer.
"""

from __future__ import annotations

import re
from pathlib import Path

from core.utils.project_layout import ProjectLayout

__all__ = ["misplaced_spec_citations", "misplaced_spec_citations_in"]

_NAME = r"(?<![\w/.-])SPEC\.md`?"
_CITATION = re.compile(
    _NAME + r"(?:"
    r":(?P<colon>\d+)"
    r"|\s+L(?P<ell>\d+)\b"
    r"|(?:\s*\(root\))?\s+lines?\s+(?P<word>\d+)"
    r")"
)
_SEPARATOR = re.compile(r"^\|[\s:|-]+\|$")


def _citing_files(layout: ProjectLayout) -> "list[Path]":
    return [layout.srs_path, layout.spec_tracking_path, layout.traceability_matrix_path]


def _defect(lines: "list[str]", number: int) -> "str | None":
    if not 1 <= number <= len(lines):
        return f"past the end of SPEC.md ({len(lines)} lines)"
    text = lines[number - 1].strip()
    if not text:
        return "a blank line"
    if _SEPARATOR.match(text):
        return "a table separator row"
    return None


def _rows(path: Path, project: Path, spec: "list[str]") -> "list[str]":
    rows: list[str] = []
    rel = path.relative_to(project).as_posix()
    text = path.read_text(encoding="utf-8", errors="replace")
    for lineno, line in enumerate(text.splitlines(), 1):
        for m in _CITATION.finditer(line):
            number = int(m.group("colon") or m.group("ell") or m.group("word"))
            defect = _defect(spec, number)
            if defect:
                rows.append(
                    f"{rel}:{lineno} cites SPEC.md:{number}, which is {defect}"
                )
    return rows


def misplaced_spec_citations(project: "str | Path") -> "list[str]":
    """One row per citation whose cited line is missing, blank or a separator."""
    project = Path(project)
    layout = ProjectLayout(project)
    if not layout.spec_path.is_file():
        return []
    spec = layout.spec_path.read_text(encoding="utf-8", errors="replace").splitlines()
    rows: list[str] = []
    for path in _citing_files(layout):
        if not path.is_file():
            continue
        rows.extend(_rows(path, project, spec))
    return rows


def misplaced_spec_citations_in(deliverable: "str | Path") -> "list[str]":
    """The same rows, for one deliverable — what the Phase 1 review loop asks.

    The project root is the ancestor whose layout names this file as a citing
    file; a file the rule does not cover answers [].
    """
    path = Path(deliverable).resolve()
    for root in path.parents:
        layout = ProjectLayout(root)
        if path in _citing_files(layout):
            if not layout.spec_path.is_file() or not path.is_file():
                return []
            spec = layout.spec_path.read_text(
                encoding="utf-8", errors="replace").splitlines()
            return _rows(path, layout.root, spec)
    return []
