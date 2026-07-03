"""Tests for core/maintenance/cr_manager.py — Phase 9 CR ticket lifecycle.

Covers: state machine transitions (legal/illegal), CR-BUG anti-fabrication
(repro_test must exist on disk), CR-FEAT approval requirements, closure
evidence contract, field updates, MAINTENANCE_LOG append.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.maintenance import CRManager, CRValidationError


@pytest.fixture
def mgr(tmp_path: Path) -> CRManager:
    return CRManager(tmp_path)


def _make_repro(project: Path, rel: str = "tests/test_cr01_repro.py") -> str:
    p = project / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("def test_repro(): assert True\n", encoding="utf-8")
    return rel


class TestCreate:
    def test_create_bug_assigns_sequential_ids(self, mgr: CRManager):
        a = mgr.create("bug", "First bug ticket")
        b = mgr.create("feat", "First feature ticket")
        assert a["id"] == "CR-01"
        assert b["id"] == "CR-02"
        assert a["status"] == "OPEN"
        assert a["type"] == "bug"
        assert b["type"] == "feat"

    def test_create_rejects_bad_type(self, mgr: CRManager):
        with pytest.raises(CRValidationError, match="type"):
            mgr.create("hotfix", "Some title")

    def test_create_rejects_short_title(self, mgr: CRManager):
        with pytest.raises(CRValidationError, match="title"):
            mgr.create("bug", "ab")

    def test_ticket_persisted_as_json(self, mgr: CRManager, tmp_path: Path):
        cr = mgr.create("bug", "Persisted ticket")
        p = tmp_path / ".methodology" / "change_requests" / f"{cr['id']}.json"
        assert p.is_file()
        data = json.loads(p.read_text(encoding="utf-8"))
        assert data["id"] == cr["id"]


class TestTransitions:
    def test_bug_blocked_without_root_cause(self, mgr: CRManager):
        cr = mgr.create("bug", "Bug without root cause")
        with pytest.raises(CRValidationError, match="root_cause"):
            mgr.transition(cr["id"], "ANALYZED")

    def test_bug_approved_requires_existing_repro(self, mgr: CRManager, tmp_path: Path):
        cr = mgr.create("bug", "Bug with fake repro")
        mgr.update_fields(cr["id"], {"root_cause": "off-by-one"})
        mgr.transition(cr["id"], "ANALYZED")
        # repro path recorded but file does NOT exist → anti-fabrication block
        mgr.update_fields(cr["id"], {"repro_test": "tests/test_missing.py"})
        with pytest.raises(CRValidationError, match="does not exist"):
            mgr.transition(cr["id"], "APPROVED")
        # once the file exists, the transition passes
        _make_repro(tmp_path, "tests/test_missing.py")
        out = mgr.transition(cr["id"], "APPROVED")
        assert out["status"] == "APPROVED"

    def test_feat_analyzed_requires_frs_and_impact(self, mgr: CRManager):
        cr = mgr.create("feat", "Feature without analysis")
        with pytest.raises(CRValidationError) as exc:
            mgr.transition(cr["id"], "ANALYZED")
        assert "affected_frs" in str(exc.value)
        assert "impact_analysis" in str(exc.value)

    def test_feat_approved_requires_approval(self, mgr: CRManager):
        cr = mgr.create("feat", "Feature without approval")
        mgr.update_fields(cr["id"], {
            "affected_frs": ["FR-01"],
            "impact_analysis": {"srs": True},
        })
        mgr.transition(cr["id"], "ANALYZED")
        with pytest.raises(CRValidationError, match="approval"):
            mgr.transition(cr["id"], "APPROVED")
        mgr.update_fields(cr["id"], {
            "approval": {"approved_by": "johnny", "justification": "roadmap item"},
        })
        assert mgr.transition(cr["id"], "APPROVED")["status"] == "APPROVED"

    def test_non_sequential_transition_blocked(self, mgr: CRManager):
        cr = mgr.create("bug", "Skip-ahead attempt")
        with pytest.raises(CRValidationError, match="non-sequential"):
            mgr.transition(cr["id"], "IN_PROGRESS")

    def test_rejected_requires_reason_and_is_terminal(self, mgr: CRManager):
        cr = mgr.create("feat", "Feature to reject")
        with pytest.raises(CRValidationError, match="rejected_reason"):
            mgr.transition(cr["id"], "REJECTED")
        mgr.update_fields(cr["id"], {"rejected_reason": "duplicate of CR-05"})
        out = mgr.transition(cr["id"], "REJECTED")
        assert out["status"] == "REJECTED"
        assert out.get("closed_at")
        with pytest.raises(CRValidationError, match="terminal"):
            mgr.transition(cr["id"], "ANALYZED")

    def test_unknown_status_rejected(self, mgr: CRManager):
        cr = mgr.create("bug", "Unknown status probe")
        with pytest.raises(CRValidationError, match="unknown status"):
            mgr.transition(cr["id"], "WONTFIX")


class TestClosureContract:
    def _verified_bug(self, mgr: CRManager, project: Path) -> dict:
        cr = mgr.create("bug", "Full lifecycle bug")
        rel = _make_repro(project)
        mgr.update_fields(cr["id"], {"root_cause": "rc", "repro_test": rel})
        for status in ("ANALYZED", "APPROVED", "IN_PROGRESS", "VERIFIED"):
            mgr.transition(cr["id"], status)
        return mgr.load(cr["id"])

    def test_closed_requires_fix_commit_and_frs(self, mgr: CRManager, tmp_path: Path):
        cr = self._verified_bug(mgr, tmp_path)
        with pytest.raises(CRValidationError) as exc:
            mgr.transition(cr["id"], "CLOSED")
        assert "fix_commit" in str(exc.value)
        assert "affected_frs" in str(exc.value)

    def test_closed_with_full_evidence(self, mgr: CRManager, tmp_path: Path):
        cr = self._verified_bug(mgr, tmp_path)
        mgr.update_fields(cr["id"], {
            "affected_frs": ["FR-01"],
            "resolution": {"fix_commit": "abc1234"},
        })
        out = mgr.transition(cr["id"], "CLOSED")
        assert out["status"] == "CLOSED"
        assert out.get("closed_at")

    def test_bug_close_reverifies_repro_still_exists(self, mgr: CRManager, tmp_path: Path):
        cr = self._verified_bug(mgr, tmp_path)
        mgr.update_fields(cr["id"], {
            "affected_frs": ["FR-01"],
            "resolution": {"fix_commit": "abc1234"},
        })
        (tmp_path / cr["repro_test"]).unlink()  # repro deleted after VERIFIED
        with pytest.raises(CRValidationError, match="does not exist"):
            mgr.transition(cr["id"], "CLOSED")


class TestUpdateFields:
    def test_affected_frs_canonicalized(self, mgr: CRManager):
        cr = mgr.create("feat", "Canonical FR ids")
        out = mgr.update_fields(cr["id"], {"affected_frs": ["fr-1", "FR_02"]})
        assert out["affected_frs"] == ["FR-01", "FR-02"]

    def test_status_change_via_fields_rejected(self, mgr: CRManager):
        cr = mgr.create("bug", "Status via fields")
        with pytest.raises(CRValidationError, match="transition"):
            mgr.update_fields(cr["id"], {"status": "CLOSED"})

    def test_id_immutable(self, mgr: CRManager):
        cr = mgr.create("bug", "Immutable id")
        with pytest.raises(CRValidationError, match="immutable"):
            mgr.update_fields(cr["id"], {"id": "CR-99"})

    def test_terminal_ticket_fields_frozen(self, mgr: CRManager):
        cr = mgr.create("bug", "Frozen after reject")
        mgr.update_fields(cr["id"], {"rejected_reason": "obsolete"})
        mgr.transition(cr["id"], "REJECTED")
        with pytest.raises(CRValidationError, match="terminal"):
            mgr.update_fields(cr["id"], {"description": "late edit"})


class TestMaintenanceLog:
    def test_append_creates_index_with_header(self, mgr: CRManager, tmp_path: Path):
        cr = mgr.create("bug", "Logged bug")
        rel = _make_repro(tmp_path)
        mgr.update_fields(cr["id"], {
            "root_cause": "rc", "repro_test": rel,
            "affected_frs": ["FR-01"], "resolution": {"fix_commit": "abc1234"},
        })
        for status in ("ANALYZED", "APPROVED", "IN_PROGRESS", "VERIFIED", "CLOSED"):
            mgr.transition(cr["id"], status)
        log = mgr.append_maintenance_log(mgr.load(cr["id"]))
        content = log.read_text(encoding="utf-8")
        assert log == tmp_path / "09-maintenance" / "MAINTENANCE_LOG.md"
        assert "| CR | Type | Title" in content        # header created
        assert "| CR-01 | CR-BUG | Logged bug | CLOSED | FR-01 | abc1234 |" in content

    def test_append_is_additive(self, mgr: CRManager, tmp_path: Path):
        for i in (1, 2):
            cr = mgr.create("feat", f"Feature {i} ticket")
            mgr.update_fields(cr["id"], {"rejected_reason": "scope cut"})
            mgr.transition(cr["id"], "REJECTED")
            mgr.append_maintenance_log(mgr.load(cr["id"]))
        content = (tmp_path / "09-maintenance" / "MAINTENANCE_LOG.md").read_text(encoding="utf-8")
        assert content.count("CR-FEAT") == 2
        assert content.count("| CR | Type | Title") == 1  # header not duplicated


class TestListAll:
    def test_lists_sorted_and_surfaces_corrupt(self, mgr: CRManager, tmp_path: Path):
        mgr.create("bug", "Ticket one")
        mgr.create("feat", "Ticket two")
        bad = tmp_path / ".methodology" / "change_requests" / "CR-99.json"
        bad.write_text("{not json", encoding="utf-8")
        rows = mgr.list_all()
        ids = [r["id"] for r in rows]
        assert ids == ["CR-01", "CR-02", "CR-99"]
        assert rows[2]["status"] == "CORRUPT"
