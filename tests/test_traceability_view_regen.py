"""Tests for advance-phase auto-regeneration of traceability views (SSOT閉環).

TRACEABILITY_MATRIX.md / SPEC_TRACKING.md are render-only views of the live
build_traceability scan — never a gate input. advance-phase refreshes them so a
phase advance can't leave a stale/hand-mocked matrix, staging each only if its
bytes changed (no empty no-op commits).
"""

from __future__ import annotations

import subprocess

from pathlib import Path

from core.utils.project_layout import ProjectLayout
import harness_cli  # noqa: F401  entry-first load order (cli-first crashes until S5)
from cli.phase_cmds import _regen_and_stage_view, _regen_traceability_views

_REPO = Path(__file__).resolve().parent.parent


def test_regen_stages_only_when_content_changes(tmp_path, monkeypatch) -> None:

    path = tmp_path / "V.md"
    path.write_text("old", encoding="utf-8")
    calls: list = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: calls.append(a))

    _regen_and_stage_view(tmp_path, path, lambda p: p.write_text("new", encoding="utf-8"))
    assert path.read_text(encoding="utf-8") == "new"
    assert len(calls) == 1, "changed content must be git-added"


def test_regen_is_noop_when_content_identical(tmp_path, monkeypatch) -> None:

    path = tmp_path / "V.md"
    path.write_text("same", encoding="utf-8")
    calls: list = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: calls.append(a))

    _regen_and_stage_view(tmp_path, path, lambda p: p.write_text("same", encoding="utf-8"))
    assert calls == [], "identical content must NOT be staged (no empty no-op commit)"


def test_regen_render_error_is_nonfatal(tmp_path) -> None:

    def boom(_p):
        raise RuntimeError("render fail")

    # A render error must be swallowed — views are best-effort, never fatal.
    _regen_and_stage_view(tmp_path, tmp_path / "V.md", boom)


def test_regen_traceability_view_renders_to_layout_path(tmp_path, monkeypatch) -> None:

    monkeypatch.setattr("scripts.build_traceability.build_traceability", lambda _p: "RT")

    def fake_gen(rt, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"matrix<{rt}>", encoding="utf-8")

    monkeypatch.setattr("scripts.build_traceability.generate_markdown_matrix", fake_gen)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: None)

    _regen_traceability_views(tmp_path)

    matrix = ProjectLayout(tmp_path).traceability_matrix_path
    assert matrix.exists() and "matrix<RT>" in matrix.read_text(encoding="utf-8")


def test_regen_traceability_scan_failure_is_nonfatal(tmp_path, monkeypatch) -> None:

    def boom(_p):
        raise RuntimeError("scan blew up")

    monkeypatch.setattr("scripts.build_traceability.build_traceability", boom)
    # Must not raise — a scan failure only skips the view refresh.
    _regen_traceability_views(tmp_path)


def test_advance_phase_wires_traceability_view_regen() -> None:
    """Wiring guard: advance-phase must keep calling the view regen (else the
    閉環 silently breaks and matrices go stale again)."""
    src = (_REPO / "cli" / "phase_cmds.py").read_text(encoding="utf-8")
    assert "_regen_traceability_views(project)" in src, (
        "traceability view auto-regen unwired from advance-phase"
    )


# ── Round 33 站2 ────────────────────────────────────────────────────────

def test_regen_records_a_degradation_when_a_view_loses_its_anchor(tmp_path, monkeypatch):
    """A view that no longer satisfies its registered loader anchor is
    recorded, not silently staged. WARN rather than BLOCK: the anchor is read
    only on re-entry into Phase 1, and the defect was the framework's own."""
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: None)
    matrix = ProjectLayout(tmp_path).traceability_matrix_path
    matrix.parent.mkdir(parents=True, exist_ok=True)

    _regen_and_stage_view(
        tmp_path, matrix,
        lambda p: p.write_text("\n\n<!-- AUTO-GEN:START -->\n", encoding="utf-8"),
    )
    ledger = tmp_path / ".methodology" / "degradations.jsonl"
    assert ledger.exists() and "does not start with" in ledger.read_text(encoding="utf-8"), (
        "a regenerated view whose first line fails its own anchor left no trace"
    )


def test_a_view_that_keeps_its_anchor_records_nothing(tmp_path, monkeypatch):
    """Discriminating half — a ledger entry on every advance is noise, not a
    signal."""
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: None)
    matrix = ProjectLayout(tmp_path).traceability_matrix_path
    matrix.parent.mkdir(parents=True, exist_ok=True)

    _regen_and_stage_view(
        tmp_path, matrix,
        lambda p: p.write_text("# Traceability Matrix — x\n\n<!-- AUTO-GEN:START -->\n",
                               encoding="utf-8"),
    )
    assert not (tmp_path / ".methodology" / "degradations.jsonl").exists()


def test_migrate_trace_overlay_targets_the_real_deliverable(tmp_path, capsys):
    """F6: the command hardcoded `<project>/TRACEABILITY_MATRIX.md` and a
    root-level overlay, while the deliverable lives at 01-requirements/ and
    build_traceability defaults the overlay to the matrix's own directory. It
    migrated a file that was not the deliverable and wrote an overlay nothing
    reads."""
    import argparse

    from cli.check_cmds import cmd_migrate_trace_overlay

    matrix = ProjectLayout(tmp_path).traceability_matrix_path
    matrix.parent.mkdir(parents=True, exist_ok=True)
    matrix.write_text("# Traceability Matrix — x\n\nlegacy body\n", encoding="utf-8")

    rc = cmd_migrate_trace_overlay(
        argparse.Namespace(project=str(tmp_path), dry_run=False)
    )
    capsys.readouterr()
    assert rc == 0
    assert "<!-- AUTO-GEN:START -->" in matrix.read_text(encoding="utf-8"), (
        "the deliverable at 01-requirements/ was not the file that got migrated"
    )
    assert (matrix.parent / "TRACEABILITY_MATRIX.overlay.yaml").exists(), (
        "the overlay landed somewhere generate_markdown_matrix will not read "
        "(it defaults to output_path.parent)"
    )
    assert not (tmp_path / "TRACEABILITY_MATRIX.overlay.yaml").exists()
