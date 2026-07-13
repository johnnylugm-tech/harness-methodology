"""Tests for cli/check_cmds.py's cmd_check_manifest_integrity (T1-A).

Thin CLI wrapper around PhaseHooks.preflight_manifest_integrity() — the
correct, canonical implementation of the manifest-corruption check that
several workflow JS files previously reimplemented inline with the
truncation-comparison direction inverted.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def _write_manifest(project: Path, *, fr_ids, fr_trace, gate1) -> None:
    md = project / ".methodology"
    md.mkdir(parents=True, exist_ok=True)
    (md / "quality_manifest.json").write_text(json.dumps({
        "fr_ids": fr_ids,
        "fr_module_traceability": fr_trace,
        "gate_results": {"gate1": gate1},
    }), encoding="utf-8")


def test_no_manifest_yet_passes_skipped(tmp_path, capsys):
    from cli.check_cmds import cmd_check_manifest_integrity

    rc = cmd_check_manifest_integrity(
        argparse.Namespace(project=str(tmp_path), phase=None)
    )
    out = capsys.readouterr().out
    assert rc == 0
    assert '"passed": true' in out
    assert '"skipped": true' in out


def test_healthy_manifest_passes(tmp_path):
    from cli.check_cmds import cmd_check_manifest_integrity

    _write_manifest(
        tmp_path,
        fr_ids=["FR-01", "FR-02"],
        fr_trace={"FR-01": "app.a", "FR-02": "app.b"},
        gate1={},
    )
    rc = cmd_check_manifest_integrity(
        argparse.Namespace(project=str(tmp_path), phase=None)
    )
    assert rc == 0


def test_truncated_fr_ids_blocks(tmp_path, capsys):
    """Pattern A: fr_ids has fewer entries than fr_module_traceability —
    this is the exact case the inverted JS one-liner used to miss."""
    from cli.check_cmds import cmd_check_manifest_integrity

    _write_manifest(
        tmp_path,
        fr_ids=["FR-01"],
        fr_trace={"FR-01": "app.a", "FR-02": "app.b"},
        gate1={},
    )
    rc = cmd_check_manifest_integrity(
        argparse.Namespace(project=str(tmp_path), phase=None)
    )
    out = capsys.readouterr().out
    assert rc == 1
    assert '"passed": false' in out
    assert '"blocked": true' in out


def test_single_fr_project_not_flagged_as_broken(tmp_path):
    """A legitimate single-FR project must not be misjudged as corrupted —
    the JS's ad hoc `len(fr_ids) >= 2` floor had no basis in harness logic."""
    from cli.check_cmds import cmd_check_manifest_integrity

    _write_manifest(
        tmp_path,
        fr_ids=["FR-01"],
        fr_trace={"FR-01": "app.a"},
        gate1={},
    )
    rc = cmd_check_manifest_integrity(
        argparse.Namespace(project=str(tmp_path), phase=None)
    )
    assert rc == 0


def test_unreadable_manifest_blocks_with_recovery_hint(tmp_path, capsys):
    from cli.check_cmds import cmd_check_manifest_integrity

    md = tmp_path / ".methodology"
    md.mkdir(parents=True, exist_ok=True)
    (md / "quality_manifest.json").write_text("{not valid json", encoding="utf-8")
    rc = cmd_check_manifest_integrity(
        argparse.Namespace(project=str(tmp_path), phase=None)
    )
    out = capsys.readouterr().out
    assert rc == 1
    assert "git checkout HEAD" in out
