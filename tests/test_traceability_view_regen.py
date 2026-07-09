"""Tests for advance-phase auto-regeneration of traceability views (SSOT閉環).

TRACEABILITY_MATRIX.md / SPEC_TRACKING.md are render-only views of the live
build_traceability scan — never a gate input. advance-phase refreshes them so a
phase advance can't leave a stale/hand-mocked matrix, staging each only if its
bytes changed (no empty no-op commits).
"""

from __future__ import annotations

from pathlib import Path

from core.utils.project_layout import ProjectLayout

_REPO = Path(__file__).resolve().parent.parent


def test_regen_stages_only_when_content_changes(tmp_path, monkeypatch) -> None:
    import harness_cli as hc

    path = tmp_path / "V.md"
    path.write_text("old", encoding="utf-8")
    calls: list = []
    monkeypatch.setattr(hc.subprocess, "run", lambda *a, **k: calls.append(a))

    hc._regen_and_stage_view(tmp_path, path, lambda p: p.write_text("new", encoding="utf-8"))
    assert path.read_text(encoding="utf-8") == "new"
    assert len(calls) == 1, "changed content must be git-added"


def test_regen_is_noop_when_content_identical(tmp_path, monkeypatch) -> None:
    import harness_cli as hc

    path = tmp_path / "V.md"
    path.write_text("same", encoding="utf-8")
    calls: list = []
    monkeypatch.setattr(hc.subprocess, "run", lambda *a, **k: calls.append(a))

    hc._regen_and_stage_view(tmp_path, path, lambda p: p.write_text("same", encoding="utf-8"))
    assert calls == [], "identical content must NOT be staged (no empty no-op commit)"


def test_regen_render_error_is_nonfatal(tmp_path) -> None:
    import harness_cli as hc

    def boom(_p):
        raise RuntimeError("render fail")

    # A render error must be swallowed — views are best-effort, never fatal.
    hc._regen_and_stage_view(tmp_path, tmp_path / "V.md", boom)


def test_regen_traceability_view_renders_to_layout_path(tmp_path, monkeypatch) -> None:
    import harness_cli as hc

    monkeypatch.setattr("scripts.build_traceability.build_traceability", lambda _p: "RT")

    def fake_gen(rt, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"matrix<{rt}>", encoding="utf-8")

    monkeypatch.setattr("scripts.build_traceability.generate_markdown_matrix", fake_gen)
    monkeypatch.setattr(hc.subprocess, "run", lambda *a, **k: None)

    hc._regen_traceability_views(tmp_path)

    matrix = ProjectLayout(tmp_path).traceability_matrix_path
    assert matrix.exists() and "matrix<RT>" in matrix.read_text(encoding="utf-8")


def test_regen_traceability_scan_failure_is_nonfatal(tmp_path, monkeypatch) -> None:
    import harness_cli as hc

    def boom(_p):
        raise RuntimeError("scan blew up")

    monkeypatch.setattr("scripts.build_traceability.build_traceability", boom)
    # Must not raise — a scan failure only skips the view refresh.
    hc._regen_traceability_views(tmp_path)


def test_advance_phase_wires_traceability_view_regen() -> None:
    """Wiring guard: advance-phase must keep calling the view regen (else the
    閉環 silently breaks and matrices go stale again)."""
    src = (_REPO / "harness_cli.py").read_text(encoding="utf-8")
    assert "_regen_traceability_views(project)" in src, (
        "traceability view auto-regen unwired from advance-phase"
    )
