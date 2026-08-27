"""Tests for SPEC_TRACKING.md Status-column render-from-SSOT (Direction 閉環).

Status is refreshed in place from build_traceability (the same authoritative
scan the gate traceability dimension uses); the P1-authored semantic columns are
preserved. The refresh never creates the file and never deletes a row.
"""

from __future__ import annotations

import re
from pathlib import Path

from core.requirement_traceability import TraceStatus
from core.traceability.spec_tracking_render import (
    refresh_status_table,
    write_spec_tracking,
)
from core.utils.project_layout import ProjectLayout


_HDR = (
    "## Specification Status\n\n"
    "| FR ID | Spec Description | Intent Class | Decision Framework | Status | Notes |\n"
    "|-------|-----------------|--------------|-------------------|--------|-------|\n"
)


def _table(rows: list[tuple[str, str]]) -> str:
    body = "".join(f"| {fr} | desc-{fr} | CRUD | fw | {st} | note-{fr} |\n" for fr, st in rows)
    return _HDR + body


class _Req:
    def __init__(self, status) -> None:
        self.status = status


class _RT:
    def __init__(self, mapping: dict) -> None:
        self.requirements = {fr: _Req(st) for fr, st in mapping.items()}


def test_status_overwritten_semantic_columns_preserved() -> None:
    md = _table([("FR-01", "DRAFT"), ("FR-02", "DRAFT")])
    new, changed = refresh_status_table(md, {"FR-01": "VERIFIED", "FR-02": "IN_PROGRESS"})
    assert changed
    assert "| FR-01 | desc-FR-01 | CRUD | fw | VERIFIED | note-FR-01 |" in new
    assert "| FR-02 | desc-FR-02 | CRUD | fw | IN_PROGRESS | note-FR-02 |" in new


def test_no_change_when_status_already_correct() -> None:
    md = _table([("FR-01", "VERIFIED")])
    new, changed = refresh_status_table(md, {"FR-01": "VERIFIED"})
    assert changed is False and new == md


def test_scanned_fr_absent_from_table_is_appended() -> None:
    md = _table([("FR-01", "VERIFIED")])
    new, changed = refresh_status_table(md, {"FR-01": "VERIFIED", "FR-03": "PENDING"})
    assert changed
    assert re.search(r"\| FR-03 \|.*\| PENDING \|", new)


def test_row_absent_from_scan_is_left_untouched() -> None:
    # FR-99 tracked manually, not in the scan → its row (incl. Status) preserved.
    md = _table([("FR-01", "DRAFT"), ("FR-99", "DRAFT")])
    new, _ = refresh_status_table(md, {"FR-01": "VERIFIED"})
    assert "| FR-99 | desc-FR-99 | CRUD | fw | DRAFT | note-FR-99 |" in new


def test_no_table_is_noop() -> None:
    md = "# SPEC_TRACKING\n\nno table here\n"
    new, changed = refresh_status_table(md, {"FR-01": "VERIFIED"})
    assert changed is False and new == md


def test_write_refreshes_status_from_rt(tmp_path: Path) -> None:
    path = ProjectLayout(tmp_path).spec_tracking_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_table([("FR-01", "DRAFT")]), encoding="utf-8")
    write_spec_tracking(tmp_path, _RT({"FR-01": TraceStatus.VERIFIED}))
    assert "VERIFIED" in path.read_text(encoding="utf-8")


def test_write_is_noop_when_file_absent(tmp_path: Path) -> None:
    write_spec_tracking(tmp_path, _RT({"FR-01": TraceStatus.VERIFIED}))
    assert not ProjectLayout(tmp_path).spec_tracking_path.exists(), (
        "refresh must never CREATE SPEC_TRACKING.md — only keep an existing one honest"
    )


def test_spec_tracking_wired_into_view_regen() -> None:
    """The regen calls the refresh — asked of the AST, not of the text.

    Round 80 站7c. This used to read `cli/phase_cmds.py` and assert the string
    `write_spec_tracking` appeared somewhere in it. That went red when the
    function was MOVED to cli/advance_checks.py with its body byte-identical:
    a test that fires on a repair and not on a break, which is precisely the
    shape tests/test_source_reading_discipline.py's ratchet exists to shrink,
    and one of the legitimate conversions that file names — "pinning the
    existence of a call inside a function too expensive to drive, read off the
    AST (ast.Call, ast.Name), not the text. A comment cannot forge a call
    node."

    Asking for the call by name inside the function that must make it is also
    the stronger statement: the old grep stayed green if the call were deleted
    while the name survived in a comment or an import.
    """
    import ast
    import importlib

    module = importlib.import_module("cli.advance_checks")
    assert module.__file__
    tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
    regen = next(
        (node for node in tree.body
         if isinstance(node, ast.FunctionDef)
         and node.name == "_regen_traceability_views"),
        None,
    )
    assert regen is not None, "_regen_traceability_views is gone from cli.advance_checks"
    called = {
        node.func.id
        for node in ast.walk(regen)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "write_spec_tracking" in called, (
        "SPEC_TRACKING Status refresh unwired from advance-phase view regen — "
        f"_regen_traceability_views calls {sorted(called)}"
    )
