"""
Regression tests for 2 follow-up bugs (CWD-rel / CWD-pollution patterns
already partly addressed in earlier commits, but two more sites left):

  1. _update_quality_manifest (line 2634) — uses
     `Path(".methodology/quality_manifest.json")` (CWD-relative).
     Same pattern as the generate_quality_manifest fix from a
     previous commit, but for the UPDATER path. A CLI invocation
     with --project-root <path> from a different cwd writes
     quality-gate results into the wrong tree.

  2. HarnessBridge.__init__ — instantiates EffortTracker with the
     default `db_path=".methodology/effort_metrics.db"`, which
     `mkdir(parents=True)` in CWD on every bridge construction.
     Pollutes any cwd the bridge is constructed in. Fix: make
     EffortTracker lazy — the .methodology/ side effect must
     not happen on __init__, only on first actual write.
"""
from __future__ import annotations

import json
from pathlib import Path

from harness.harness_bridge import HarnessBridge, GateResult


# ── Bug 1: _update_quality_manifest CWD-rel ─────────────────────────────────

class TestUpdateQualityManifestProjectRoot:
    def test_update_writes_under_explicit_project_root(
        self, tmp_path: Path, monkeypatch,
    ):
        """_update_quality_manifest must write the manifest FILE
        under the explicit project_root, not CWD. (Same pattern
        as generate_quality_manifest — the CWD-rel hazard is
        identical.)"""
        # Chdir to a directory that does NOT contain the project.
        unrelated = tmp_path / "unrelated_cwd"
        unrelated.mkdir()
        monkeypatch.chdir(unrelated)

        project_root = tmp_path / "real_project"
        project_root.mkdir()
        (project_root / ".methodology").mkdir()
        # Pre-populate the manifest at the project_root so the
        # function has something to update.
        (project_root / ".methodology" / "quality_manifest.json").write_text(
            json.dumps({
                "gate_results": {"gate1": {}, "gate2": None,
                                 "gate3": None, "gate4": None},
            }, indent=2),
            encoding="utf-8",
        )

        bridge = HarnessBridge()
        result = GateResult(
            gate_num=3, score=85.0, quality_complete=True, rounds_used=1,
        )
        bridge._update_quality_manifest(
            gate_num=3, fr_id="FR-001", result=result,
            project_root=str(project_root),
        )

        # The manifest at the project_root must have been updated.
        data = json.loads(
            (project_root / ".methodology" / "quality_manifest.json")
            .read_text(encoding="utf-8")
        )
        assert "FR-001" in data["gate_results"]["gate3"], (
            f"manifest at project_root must have been updated; "
            f"data: {data!r}"
        )
        # No STALE manifest file at the unrelated cwd.
        assert not (unrelated / ".methodology" / "quality_manifest.json").exists()

    def test_the_manifest_carries_no_waiver_fields(self, tmp_path: Path):
        """Round 38: `da_waiver_applied` / `da_waiver_needs_human_review` are
        gone from the manifest payload, because no threshold can be waived.

        This pins their absence rather than deleting the test. A field that can
        only ever be missing is one a later reader may "restore" by reflex; the
        assertion says the removal was the decision, not an oversight."""
        project_root = tmp_path / "proj"
        (project_root / ".methodology").mkdir(parents=True)
        (project_root / ".methodology" / "quality_manifest.json").write_text(
            json.dumps({
                "gate_results": {"gate1": {}, "gate2": None,
                                 "gate3": None, "gate4": None},
            }, indent=2),
            encoding="utf-8",
        )

        bridge = HarnessBridge()
        result = GateResult(
            gate_num=3, score=93.4, quality_complete=True, rounds_used=1,
        )
        bridge._update_quality_manifest(
            gate_num=3, fr_id=None, result=result,
            project_root=str(project_root),
        )

        data = json.loads(
            (project_root / ".methodology" / "quality_manifest.json")
            .read_text(encoding="utf-8")
        )
        g3 = data["gate_results"]["gate3"]
        assert g3["score"] == 93.4  # the payload is written, just without waivers
        assert "da_waiver_applied" not in g3
        assert "da_waiver_needs_human_review" not in g3

    def test_update_falls_back_to_cwd_with_warning(
        self, tmp_path: Path, monkeypatch, caplog,
    ):
        """When project_root is NOT passed (legacy callers), the
        function falls back to CWD for backward compat but emits
        a WARNING so the CWD-rel hazard is visible. This mirrors
        the generate_quality_manifest fallback contract."""
        unrelated = tmp_path / "unrelated_cwd"
        unrelated.mkdir()
        monkeypatch.chdir(unrelated)

        bridge = HarnessBridge()
        result = GateResult(
            gate_num=1, score=80.0, quality_complete=True, rounds_used=1,
        )
        with caplog.at_level("WARNING", logger="harness.harness_bridge"):
            bridge._update_quality_manifest(
                gate_num=1, fr_id="FR-001", result=result,
            )
        # CWD fallback used; WARNING log surfaces the hazard.
        assert any(
            "project_root" in rec.message.lower() or
            "cwd" in rec.message.lower()
            for rec in caplog.records
        ), (
            f"CWD-rel _update_quality_manifest must log a WARNING; "
            f"got: {[(r.levelname, r.message) for r in caplog.records]}"
        )


# ── Bug 2: HarnessBridge.__init__ pollutes CWD ───────────────────────────────

class TestHarnessBridgeInitNoCwdPollution:
    def test_constructing_bridge_does_not_create_methodology_in_cwd(
        self, tmp_path: Path, monkeypatch,
    ):
        """Constructing HarnessBridge in a directory that does NOT
        contain a .methodology/ must NOT create one. The current
        EffortTracker.__init__ does `mkdir(parents=True)` on
        `.methodology/effort_metrics.db`, which silently pollutes
        cwd. Fix: lazy initialization (defer mkdir to first use)."""
        clean_cwd = tmp_path / "clean_cwd"
        clean_cwd.mkdir()
        monkeypatch.chdir(clean_cwd)

        # Construct the bridge
        HarnessBridge()

        # cwd must not have gained a .methodology/ directory.
        assert not (clean_cwd / ".methodology").exists(), (
            f"constructing HarnessBridge in {clean_cwd} created "
            f".methodology — the EffortTracker init is polluting cwd"
        )

    def test_bridge_init_does_not_create_effort_db_in_cwd(
        self, tmp_path: Path, monkeypatch,
    ):
        """A more specific check: no effort_metrics.db file should
        appear in cwd after bridge construction."""
        clean_cwd = tmp_path / "clean_cwd2"
        clean_cwd.mkdir()
        monkeypatch.chdir(clean_cwd)

        HarnessBridge()

        assert not (clean_cwd / ".methodology" / "effort_metrics.db").exists(), (
            f"effort_metrics.db leaked into cwd {clean_cwd}"
        )

    def test_effort_tracker_record_creates_dir_lazily(self, tmp_path: Path):
        """Sanity guard: the lazy fix must still create the DB
        on first record() — the data is not lost, just the
        __init__ side effect is gone."""
        from harness.effort_tracker import EffortTracker, EffortRecord
        # Don't pass a db_path — use the default but be ready
        # to clean up after the test.
        tracker = EffortTracker(
            db_path=str(tmp_path / ".methodology" / "effort_metrics.db")
        )
        # Before record(): no DB file yet (lazy fix)
        assert not (tmp_path / ".methodology" / "effort_metrics.db").exists()
        # After record(): DB created
        tracker.record(EffortRecord(
            phase=3, gate_num=1, agent_id="GATE", operation="test",
            duration_s=0.1,
        ))
        assert (tmp_path / ".methodology" / "effort_metrics.db").exists()
