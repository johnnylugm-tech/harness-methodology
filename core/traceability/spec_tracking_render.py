"""SPEC_TRACKING.md Status-column refresher (render-from-SSOT, Direction 閉環).

SPEC_TRACKING.md is a render-only *view*: the only machine-derivable column is
Status, whose authoritative source is `build_traceability`'s live code/test scan
(the same source the gate `traceability` dimension uses). The hand-filled score
that a P1 agent may write here is NEVER a gate input — so instead of writing a
second authority into an agent-editable Markdown, this module refreshes ONLY the
Status column *in place* from the scan, preserving the P1-authored semantic
columns (Spec Description / Intent Class / Decision Framework / Notes). advance-
phase calls it so a phase advance can't leave a stale/hand-mocked Status behind;
because it re-derives Status every advance, a hand-edit is overwritten (最終一致).

In-place (rather than the AUTO-GEN-sentinel + overlay mechanism used by
TRACEABILITY_MATRIX) is deliberate: SPEC_TRACKING's semantic columns live in the
table itself, so an in-place Status overwrite preserves them without a migration
or an overlay file — least-intrusive for a view whose only machine column is
Status.
"""

from __future__ import annotations

import re
from pathlib import Path

from core.utils.project_layout import ProjectLayout

__all__ = ["write_spec_tracking", "refresh_status_table"]

# (?<!N) so an NFR row is never hijacked by the same-numbered FR's status
# (parity-locked by tests/test_fr_token_parity.py).
_FR_CELL = re.compile(r"(?<!N)FR-(\d+)")


def _status_str(status) -> str:
    """TraceStatus enum → display string; tolerate a plain string too."""
    return str(getattr(status, "value", status)).upper()


def refresh_status_table(markdown: str, fr_status: dict[str, str]) -> tuple[str, bool]:
    """Return (new_markdown, changed).

    Overwrite the Status column of each FR row in the Specification Status table
    with fr_status[fr_id]; append rows for scanned FRs not yet present. Rows for
    FRs absent from fr_status (e.g. a manually tracked row) are left untouched —
    the refresh never deletes.
    """
    lines = markdown.splitlines()
    header_idx = next(
        (i for i, ln in enumerate(lines)
         if ln.strip().startswith("|") and "status" in ln.lower()
         and re.search(r"\bfr\b|fr id", ln.lower())),
        None,
    )
    if header_idx is None:
        return markdown, False
    header_cells = [c.strip() for c in lines[header_idx].strip().strip("|").split("|")]
    status_col = next((j for j, c in enumerate(header_cells) if c.lower() == "status"), None)
    if status_col is None:
        return markdown, False

    changed = False
    seen: set[str] = set()
    row = header_idx + 1
    if row < len(lines) and lines[row].strip() and set(lines[row].strip()) <= set("|-: "):
        row += 1  # separator row
    while row < len(lines) and lines[row].strip().startswith("|"):
        cells = [c.strip() for c in lines[row].strip().strip("|").split("|")]
        m = _FR_CELL.search(cells[0]) if cells else None
        if m and status_col < len(cells):
            fr = f"FR-{int(m.group(1)):02d}"
            seen.add(fr)
            if fr in fr_status and cells[status_col] != fr_status[fr]:
                cells[status_col] = fr_status[fr]
                lines[row] = "| " + " | ".join(cells) + " |"
                changed = True
        row += 1

    ncols = len(header_cells)
    new_rows: list[str] = []
    for fr in sorted(set(fr_status) - seen):
        cells = ["—"] * ncols
        cells[0] = fr
        if status_col < ncols:
            cells[status_col] = fr_status[fr]
        new_rows.append("| " + " | ".join(cells) + " |")
    if new_rows:
        lines[row:row] = new_rows
        changed = True

    out = "\n".join(lines)
    if markdown.endswith("\n"):
        out += "\n"
    return out, changed


def write_spec_tracking(project: Path, rt, out_path: Path | None = None) -> None:
    """Refresh SPEC_TRACKING.md's Status column in place from `rt`.

    No-op if the file or its Specification Status table is absent (nothing to
    refresh — this never *creates* the file, only keeps an existing one honest).
    """
    path = out_path or ProjectLayout(project).spec_tracking_path
    if not path.exists():
        return
    fr_status = {
        fr_id: _status_str(getattr(req, "status", ""))
        for fr_id, req in getattr(rt, "requirements", {}).items()
    }
    if not fr_status:
        return
    old = path.read_text(encoding="utf-8", errors="replace")
    new, changed = refresh_status_table(old, fr_status)
    if changed:
        path.write_text(new, encoding="utf-8")
