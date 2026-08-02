"""Round 26 — a recorded verdict is read from where the verdict was recorded.

Two separate reads, two separate defects, one shape: the judgement was taken from
a file that does not hold it.

#7 `run-report`'s provenance section (Round 19 站3, "which harness commit produced
   each verdict") called `gate_result_paths`, whose priority order puts
   `.sessi-work/gate{N}_result.json` FIRST — correct for the read that drives
   scoring, because that is the fresh in-flight write finalize_gate consumes, and
   wrong here, because that copy is the agent's PRE-finalize draft with no
   `verdict`, no `composite_score` and no `passed`. It also never passed `fr_id`,
   so `.methodology/gate_results/gate{N}/<FR>.json` — the authoritative per-FR
   record — was not even a candidate. Measured on taskq-plus: the report printed
   `verdict=None` while that file said `verdict: PASS`.

#6 `state.phase_completed` recorded the project sha and timestamp of each
   completed phase but not WHICH framework version produced it. Gate results have
   carried `enforcer_sha` since Round 19 站3; phase artifacts did not. During
   taskq-plus P1-P3 five framework commits landed between 06:02 and 10:24, one of
   them fixing the very P2 SAB-WRITE step that had completed seven hours earlier —
   and nothing anywhere could say that the artifact predated its fix.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import harness_cli as _hc_entry  # noqa: F401  entry-first before cli imports
from cli._shared import gate_result_paths, gate_verdict_paths  # noqa: E402
from cli.project_cmds import _enforcer_skew_warnings  # noqa: E402
from cli.report_cmds import _gate_provenance_report  # noqa: E402

_DRAFT = {
    "gate": 1, "phase": 3, "fr_id": "FR-04",
    "overall_score": 95.87878787878788,
    "quality_complete": True,
    "breakdown": {"linting": {"score": 100.0, "threshold": 100}},
    "enforcer_sha": "draftsha0000",
}
_FINALIZED = {
    **_DRAFT,
    "composite_score": 95.8788,
    "verdict": "PASS",
    "passed": True,
    "enforcer_sha": "finalsha1111",
}


def _project(tmp_path: Path, *, draft=True, finalized=True) -> Path:
    if draft:
        (tmp_path / ".sessi-work").mkdir(parents=True, exist_ok=True)
        (tmp_path / ".sessi-work" / "gate1_result.json").write_text(
            json.dumps(_DRAFT), encoding="utf-8")
    if finalized:
        per_fr = tmp_path / ".methodology" / "gate_results" / "gate1"
        per_fr.mkdir(parents=True, exist_ok=True)
        (per_fr / "FR-04.json").write_text(json.dumps(_FINALIZED), encoding="utf-8")
    return tmp_path


class TestProvenanceReadsTheFinalizedVerdict:
    def test_the_draft_no_longer_wins(self, tmp_path):
        report = _gate_provenance_report(_project(tmp_path))
        assert report["available"] is True
        row = report["gates"][0]
        assert row["verdict"] == "PASS", (
            "the provenance section read the agent's pre-finalize draft again — "
            "that file has no verdict, which is why every Gate 1 reported None"
        )
        assert row["composite_score"] == 95.8788
        assert row["enforcer_sha"] == "finalsha1111"

    def test_the_per_fr_canonical_file_is_reachable_without_an_fr_id(self, tmp_path):
        """The reporter does not know the FR, so the directory must be enumerated."""
        paths = gate_verdict_paths(_project(tmp_path), 1)
        assert any("gate_results" in str(p) and p.name == "FR-04.json" for p in paths)

    def test_a_draft_alone_is_not_reported_as_a_verdict(self, tmp_path):
        """Nothing finalized -> no verdict on record. Silence beats a draft."""
        report = _gate_provenance_report(_project(tmp_path, finalized=False))
        assert report.get("available") is not True or not report.get("gates")

    def test_the_in_flight_helper_keeps_its_own_order(self):
        """gate_result_paths answers a different question and must not change:
        finalize_gate needs the fresh .sessi-work write it is about to consume."""
        paths = gate_result_paths(Path("/nowhere"), 1)
        assert ".sessi-work" in str(paths[0])

    def test_the_verdict_helper_never_offers_a_draft(self):
        paths = gate_verdict_paths(Path("/nowhere"), 1, fr_id="FR-04")
        assert all(".sessi-work" not in str(p) for p in paths), (
            "a draft is not a verdict — including it is the defect this helper exists "
            "to avoid"
        )


class TestEnforcerSkewIsVisible:
    def _state(self, tmp_path: Path, phase_completed: dict) -> Path:
        meth = tmp_path / ".methodology"
        meth.mkdir(parents=True, exist_ok=True)
        (meth / "state.json").write_text(json.dumps({
            "state": "RUNNING", "current_phase": 3, "language": "python",
            "phase_completed": phase_completed,
        }), encoding="utf-8")
        return tmp_path

    def test_a_different_enforcer_is_named(self, tmp_path):
        proj = self._state(tmp_path, {
            "1": {"sha": "aaa", "timestamp": "t", "enforcer_sha": "deadbeefcafe1234"},
        })
        warnings = _enforcer_skew_warnings(proj)
        assert any("enforcer skew" in w and "P1" in w for w in warnings)
        assert any("deadbeefcafe" in w for w in warnings)

    def test_the_same_enforcer_is_silent(self, tmp_path):
        from core.harness_provenance import enforcer_sha
        proj = self._state(tmp_path, {
            "1": {"sha": "aaa", "timestamp": "t", "enforcer_sha": enforcer_sha()},
        })
        assert _enforcer_skew_warnings(proj) == []

    def test_a_missing_stamp_is_reported_as_missing_not_as_agreement(self, tmp_path):
        """An unstated gap must not read as coverage — phases completed before
        Round 26 carry no stamp, and 'cannot tell' is the honest answer."""
        proj = self._state(tmp_path, {"2": {"sha": "bbb", "timestamp": "t"}})
        warnings = _enforcer_skew_warnings(proj)
        assert any("provenance missing" in w and "2" in w for w in warnings)
        assert not any("enforcer skew" in w for w in warnings)

    @pytest.mark.parametrize("completed", [None, {}, "not-a-dict", {"1": "not-a-dict"}])
    def test_degrades_quietly_on_junk(self, tmp_path, completed):
        proj = self._state(tmp_path, completed)  # type: ignore[arg-type]
        assert _enforcer_skew_warnings(proj) == []

    def test_no_state_file_is_not_an_error(self, tmp_path):
        """Provenance reporting may not break load-context."""
        assert _enforcer_skew_warnings(tmp_path) == []

    def test_advance_phase_records_the_stamp(self):
        """The writer and the reader must agree on the field name.

        Asserted against the writer's source rather than a full advance-phase run:
        the point is that `enforcer_sha` reaches phase_completed at all, which is
        the half Round 19 站3 did for gate results and skipped for phases.
        """
        import inspect

        import cli.phase_cmds as pc
        src = inspect.getsource(pc)
        assert '"enforcer_sha": enforcer_sha()' in src, (
            "advance-phase stopped stamping phase_completed with the enforcer that "
            "produced the phase, so the skew check above has nothing to compare"
        )

    def test_both_writers_also_record_the_enforcer_surface(self):
        """Round 30 站4 — the rebase-proof half must be written too.

        Round 29 站4 added `enforcer_surface` to both writers and pinned neither.
        Removing either line left the whole provenance test file green, so its
        own listed counter-proof could not fire. The commit SHA goes stale on any
        rebase (taskq-advance's 11 verdicts all name an orphaned `01bb3bb4`);
        the surface is what still answers "was the enforcing code the same?", and
        core.doctor._check_enforcer_provenance is the reader that needs it there.
        """
        import inspect

        import cli.gate_cmds as gc
        import cli.phase_cmds as pc

        assert 'data["enforcer_surface"] = enforcer_surface()' in inspect.getsource(gc), (
            "gate results stopped carrying enforcer_surface — a rebase now erases "
            "every trace of which code produced the verdict"
        )
        assert '"enforcer_surface": enforcer_surface()' in inspect.getsource(pc), (
            "state.json.phase_completed stopped carrying enforcer_surface"
        )
