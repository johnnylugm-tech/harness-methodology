"""Code-review follow-up (2026-08-23) — --force must not be a blanket
sentinel bypass.

f0de7eac added --force as a way to re-finalize the FR-99 recovery shape:
run-gate genuinely ran, gate{N}_result.json genuinely has real evidence,
but the sentinel file itself was lost/rolled back. As written, `--force`
skipped the sentinel check for ANY FR/gate — including one that never
called run-gate at all — which is exactly the fabrication the sentinel's
own message says it exists to block ("Writing gate{N}_result.json
directly without run-gate is not permitted"). This pins the narrowed
version: --force only bypasses the sentinel when a genuine
gate{N}_result.json for this fr_id already exists on disk.
"""
from __future__ import annotations

import argparse
import json

import harness_cli as _hc_entry  # noqa: F401  entry-first before cli imports
from cli import gate_cmds  # noqa: E402


def _args(project_path, *, gate=1, phase=3, fr_id="FR-01", force=False):
    return argparse.Namespace(
        gate=gate, phase=phase, project=str(project_path), fr_id=fr_id,
        force=force,
    )


def _stub_s0a_s0b(monkeypatch):
    monkeypatch.setattr(gate_cmds.tool_checks, "verify_gate_tools",
                        lambda *a, **k: (True, []))
    monkeypatch.setattr(gate_cmds.gate1_evidence, "check_commit_intervals",
                        lambda *a, **k: (True, ""))


def test_force_without_any_evidence_still_blocks(monkeypatch, tmp_path):
    """The exact loophole this round closes: --force on an FR/gate that
    never called run-gate at all (no sentinel, no gate{N}_result.json
    anywhere) must NOT be enough to skip the anti-fabrication sentinel."""
    _stub_s0a_s0b(monkeypatch)
    args = _args(tmp_path, force=True)
    rc = gate_cmds._finalize_gate_preflight(args, tmp_path)
    assert rc == 1, (
        "--force alone, with zero gate1_result.json evidence on disk, must "
        f"still block — got rc={rc}"
    )


def test_force_with_genuine_matching_evidence_bypasses_sentinel(monkeypatch, tmp_path):
    """The FR-99 recovery shape this flag exists for: run-gate genuinely
    ran (gate1_result.json for this exact fr_id exists), only the sentinel
    itself is missing."""
    _stub_s0a_s0b(monkeypatch)
    (tmp_path / ".sessi-work").mkdir()
    (tmp_path / ".sessi-work" / "gate1_result.json").write_text(json.dumps({
        "fr_id": "FR-01", "quality_complete": True, "overall_score": 95.0,
    }), encoding="utf-8")
    args = _args(tmp_path, force=True)
    rc = gate_cmds._finalize_gate_preflight(args, tmp_path)
    assert rc is None, (
        "--force with genuine gate1_result.json evidence for this fr_id "
        f"must bypass the sentinel — got rc={rc}"
    )


def test_force_with_a_different_frs_evidence_still_blocks(monkeypatch, tmp_path):
    """Evidence on disk for a DIFFERENT FR must not authorize --force for
    this one — the file is shared across FRs within a phase, so the
    fr_id inside it, not just its existence, has to match."""
    _stub_s0a_s0b(monkeypatch)
    (tmp_path / ".sessi-work").mkdir()
    (tmp_path / ".sessi-work" / "gate1_result.json").write_text(json.dumps({
        "fr_id": "FR-02", "quality_complete": True, "overall_score": 95.0,
    }), encoding="utf-8")
    args = _args(tmp_path, fr_id="FR-01", force=True)
    rc = gate_cmds._finalize_gate_preflight(args, tmp_path)
    assert rc == 1, (
        f"gate1_result.json belonging to FR-02 must not authorize --force "
        f"for FR-01 — got rc={rc}"
    )


def test_without_force_missing_sentinel_still_blocks(monkeypatch, tmp_path):
    """Baseline, unrelated to --force: no sentinel and no --force must
    block exactly as before this round."""
    _stub_s0a_s0b(monkeypatch)
    args = _args(tmp_path, force=False)
    rc = gate_cmds._finalize_gate_preflight(args, tmp_path)
    assert rc == 1


def test_a_real_sentinel_needs_no_force_at_all(monkeypatch, tmp_path):
    """A genuine run-gate sentinel bypasses the whole check on its own,
    --force or not — unaffected by this round's narrowing."""
    _stub_s0a_s0b(monkeypatch)
    sf = gate_cmds._shared._sentinel_path(tmp_path, 1, "FR-01", phase=3)
    sf.parent.mkdir(parents=True, exist_ok=True)
    sf.write_text("2026-08-23T00:00:00+00:00\n", encoding="utf-8")
    args = _args(tmp_path, force=False)
    rc = gate_cmds._finalize_gate_preflight(args, tmp_path)
    assert rc is None
