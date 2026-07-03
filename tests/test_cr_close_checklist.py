"""Integration tests for cmd_cr_close — the fail-closed re-entry checklist.

Covers: VERIFIED precondition, Gate 1 quality_complete per affected FR
(quality_manifest is the authority), attestation branch, happy-path close
(MAINTENANCE_LOG append + ticket CLOSED), and cr-open/cr-update CLI glue.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from core.maintenance import CRManager
from harness_cli import cmd_cr_close, cmd_cr_open, cmd_cr_status, cmd_cr_update


def _open_args(tmp_path: Path, **kw) -> argparse.Namespace:
    base = dict(type="bug", title="Integration bug ticket", description="",
                severity="high", project=str(tmp_path))
    base.update(kw)
    return argparse.Namespace(**base)


def _close_args(tmp_path: Path, cr: str, skip_attestation: bool = True) -> argparse.Namespace:
    return argparse.Namespace(cr=cr, project=str(tmp_path),
                              skip_attestation=skip_attestation)


def _write_manifest(project: Path, fr_ids: list[str], gate1: dict) -> None:
    mdir = project / ".methodology"
    mdir.mkdir(exist_ok=True)
    (mdir / "quality_manifest.json").write_text(json.dumps({
        "schema_version": "1.0",
        "generated_at_phase": 2,
        "fr_ids": fr_ids,
        "fr_module_traceability": {},
        "gate_results": {"gate1": gate1},
    }), encoding="utf-8")


def _verified_bug(tmp_path: Path) -> str:
    """Drive a CR-BUG to VERIFIED with full evidence; return its id."""
    assert cmd_cr_open(_open_args(tmp_path)) == 0
    mgr = CRManager(tmp_path)
    cr_id = mgr.list_all()[-1]["id"]
    repro = tmp_path / "tests" / "test_cr_repro.py"
    repro.parent.mkdir(parents=True, exist_ok=True)
    repro.write_text("def test_repro(): assert True\n", encoding="utf-8")
    mgr.update_fields(cr_id, {
        "root_cause": "off-by-one",
        "repro_test": "tests/test_cr_repro.py",
        "affected_frs": ["FR-01"],
        "resolution": {"fix_commit": "abc1234"},
    })
    for status in ("ANALYZED", "APPROVED", "IN_PROGRESS", "VERIFIED"):
        mgr.transition(cr_id, status)
    return cr_id


class TestCrCloseBlocked:
    def test_blocked_before_verified(self, tmp_path: Path, capsys):
        assert cmd_cr_open(_open_args(tmp_path)) == 0
        cr_id = CRManager(tmp_path).list_all()[-1]["id"]
        rc = cmd_cr_close(_close_args(tmp_path, cr_id))
        assert rc == 1
        assert "requires VERIFIED" in capsys.readouterr().err

    def test_blocked_without_gate1_record(self, tmp_path: Path, capsys):
        cr_id = _verified_bug(tmp_path)
        _write_manifest(tmp_path, ["FR-01"], gate1={})  # no FR-01 record
        rc = cmd_cr_close(_close_args(tmp_path, cr_id))
        err = capsys.readouterr().err
        assert rc == 1
        assert "FR-01" in err and "Gate 1" in err
        # ticket unchanged
        assert CRManager(tmp_path).load(cr_id)["status"] == "VERIFIED"

    def test_blocked_without_manifest(self, tmp_path: Path, capsys):
        cr_id = _verified_bug(tmp_path)
        rc = cmd_cr_close(_close_args(tmp_path, cr_id))
        assert rc == 1
        assert "quality_manifest.json not found" in capsys.readouterr().err

    def test_blocked_on_dirty_attestation(self, tmp_path: Path, capsys, monkeypatch):
        cr_id = _verified_bug(tmp_path)
        _write_manifest(tmp_path, ["FR-01"],
                        gate1={"FR-01": {"score": 95.0, "quality_complete": True}})
        import scripts.verify_trace_attestation as vta
        monkeypatch.setattr(vta, "verify_attestation",
                            lambda project, **kw: (1, "SHA mismatch"))
        rc = cmd_cr_close(_close_args(tmp_path, cr_id, skip_attestation=False))
        assert rc == 1
        assert "attestation" in capsys.readouterr().err.lower()


class TestCrCloseHappyPath:
    def test_close_succeeds_with_full_evidence(self, tmp_path: Path, capsys):
        cr_id = _verified_bug(tmp_path)
        _write_manifest(tmp_path, ["FR-01"],
                        gate1={"FR-01": {"score": 95.0, "quality_complete": True}})
        rc = cmd_cr_close(_close_args(tmp_path, cr_id))
        out = capsys.readouterr().out
        assert rc == 0
        assert "CLOSED" in out
        # ticket state
        cr = CRManager(tmp_path).load(cr_id)
        assert cr["status"] == "CLOSED"
        assert cr.get("closed_at")
        # MAINTENANCE_LOG appended
        log = tmp_path / "09-maintenance" / "MAINTENANCE_LOG.md"
        assert log.exists()
        assert cr_id in log.read_text(encoding="utf-8")
        # decision log audit entry written
        dlogs = list((tmp_path / ".methodology" / "decision_logs").rglob("*.yaml"))
        assert len(dlogs) == 1

    def test_close_is_terminal(self, tmp_path: Path):
        cr_id = _verified_bug(tmp_path)
        _write_manifest(tmp_path, ["FR-01"],
                        gate1={"FR-01": {"score": 95.0, "quality_complete": True}})
        assert cmd_cr_close(_close_args(tmp_path, cr_id)) == 0
        rc = cmd_cr_close(_close_args(tmp_path, cr_id))
        assert rc == 1  # already CLOSED → status precondition fails


class TestCrCliGlue:
    def test_cr_update_set_and_status(self, tmp_path: Path):
        assert cmd_cr_open(_open_args(tmp_path)) == 0
        cr_id = CRManager(tmp_path).list_all()[-1]["id"]
        args = argparse.Namespace(
            cr=cr_id, status="ANALYZED",
            set=["root_cause=loop bound"], project=str(tmp_path))
        assert cmd_cr_update(args) == 0
        cr = CRManager(tmp_path).load(cr_id)
        assert cr["status"] == "ANALYZED"
        assert cr["root_cause"] == "loop bound"

    def test_cr_update_dotted_key_merges(self, tmp_path: Path):
        assert cmd_cr_open(_open_args(tmp_path)) == 0
        cr_id = CRManager(tmp_path).list_all()[-1]["id"]
        for kv in ("resolution.fix_commit=abc123", "resolution.notes=done"):
            args = argparse.Namespace(cr=cr_id, status=None, set=[kv],
                                      project=str(tmp_path))
            assert cmd_cr_update(args) == 0
        res = CRManager(tmp_path).load(cr_id)["resolution"]
        assert res == {"fix_commit": "abc123", "notes": "done"}

    def test_cr_update_invalid_transition_exit1(self, tmp_path: Path, capsys):
        assert cmd_cr_open(_open_args(tmp_path)) == 0
        cr_id = CRManager(tmp_path).list_all()[-1]["id"]
        args = argparse.Namespace(cr=cr_id, status="VERIFIED", set=[],
                                  project=str(tmp_path))
        assert cmd_cr_update(args) == 1
        assert "BLOCKED" in capsys.readouterr().err

    def test_cr_status_lists_tickets(self, tmp_path: Path, capsys):
        assert cmd_cr_open(_open_args(tmp_path)) == 0
        assert cmd_cr_open(_open_args(tmp_path, type="feat",
                                      title="Feature ticket two",
                                      severity=None)) == 0
        capsys.readouterr()
        args = argparse.Namespace(cr=None, json=False, project=str(tmp_path))
        assert cmd_cr_status(args) == 0
        out = capsys.readouterr().out
        assert "CR-01" in out and "CR-02" in out
        assert "CR-BUG" in out and "CR-FEAT" in out


pytestmark = pytest.mark.gate
