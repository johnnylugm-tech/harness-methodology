"""Round 14 站2 — core/state_io.py: converged reads for state.json /
quality_manifest.json, with a strict/lenient choice replacing the three
divergent shapes (uncaught raise, silent `except: pass`, broad fail-open)
found scattered across ~60 call sites before this station.
"""

from __future__ import annotations

import json

import pytest

from core.state_io import (
    StateCorruptError,
    load_quality_manifest,
    load_state,
    sync_missing_fr_traceability,
)


class TestLoadStateMissingFile:
    def test_no_methodology_dir_returns_empty_dict(self, tmp_path):
        assert load_state(tmp_path) == {}

    def test_methodology_dir_without_state_json_returns_empty_dict(self, tmp_path):
        (tmp_path / ".methodology").mkdir()
        assert load_state(tmp_path) == {}


class TestLoadStateValid:
    def test_reads_existing_state(self, tmp_path):
        d = tmp_path / ".methodology"
        d.mkdir()
        (d / "state.json").write_text(json.dumps({"current_phase": 3}), encoding="utf-8")
        assert load_state(tmp_path) == {"current_phase": 3}


class TestLoadStateStrictCorrupt:
    def test_malformed_json_raises_state_corrupt_error(self, tmp_path):
        d = tmp_path / ".methodology"
        d.mkdir()
        (d / "state.json").write_text("{not json", encoding="utf-8")
        with pytest.raises(StateCorruptError) as exc_info:
            load_state(tmp_path)
        assert "state.json" in str(exc_info.value)

    def test_non_object_json_raises_state_corrupt_error(self, tmp_path):
        """A state.json that parses but isn't a dict (e.g. a bare list) is
        just as unusable to every caller that does state.get(...)."""
        d = tmp_path / ".methodology"
        d.mkdir()
        (d / "state.json").write_text("[1, 2, 3]", encoding="utf-8")
        with pytest.raises(StateCorruptError):
            load_state(tmp_path)

    def test_error_message_carries_a_fix_instruction(self, tmp_path):
        d = tmp_path / ".methodology"
        d.mkdir()
        (d / "state.json").write_text("{bad", encoding="utf-8")
        with pytest.raises(StateCorruptError) as exc_info:
            load_state(tmp_path)
        assert "Fix:" in str(exc_info.value)


class TestLoadStateLenientCorrupt:
    def test_malformed_json_degrades_to_empty_dict(self, tmp_path):
        d = tmp_path / ".methodology"
        d.mkdir()
        (d / "state.json").write_text("{not json", encoding="utf-8")
        assert load_state(tmp_path, lenient=True) == {}

    def test_lenient_corrupt_state_records_degradation(self, tmp_path):
        d = tmp_path / ".methodology"
        d.mkdir()
        (d / "state.json").write_text("{not json", encoding="utf-8")
        load_state(tmp_path, lenient=True)
        ledger = tmp_path / ".methodology" / "degradations.jsonl"
        assert ledger.exists()
        entry = json.loads(ledger.read_text(encoding="utf-8").strip())
        assert entry["component"] == "state-io"
        assert "state.json" in entry["what"]

    def test_lenient_missing_file_does_not_write_degradation(self, tmp_path):
        """A missing file is the normal early-phase case, not a
        degradation — only an existing-but-corrupt file is."""
        load_state(tmp_path, lenient=True)
        assert not (tmp_path / ".methodology" / "degradations.jsonl").exists()


class TestLoadQualityManifest:
    def test_missing_returns_empty_dict(self, tmp_path):
        assert load_quality_manifest(tmp_path) == {}

    def test_reads_existing_manifest(self, tmp_path):
        d = tmp_path / ".methodology"
        d.mkdir()
        (d / "quality_manifest.json").write_text(
            json.dumps({"gate_results": {"gate1": {}}}), encoding="utf-8"
        )
        assert load_quality_manifest(tmp_path) == {"gate_results": {"gate1": {}}}

    def test_corrupt_manifest_raises_strict(self, tmp_path):
        d = tmp_path / ".methodology"
        d.mkdir()
        (d / "quality_manifest.json").write_text("{oops", encoding="utf-8")
        with pytest.raises(StateCorruptError):
            load_quality_manifest(tmp_path)

    def test_corrupt_manifest_degrades_lenient(self, tmp_path):
        d = tmp_path / ".methodology"
        d.mkdir()
        (d / "quality_manifest.json").write_text("{oops", encoding="utf-8")
        assert load_quality_manifest(tmp_path, lenient=True) == {}

    def test_state_and_manifest_corruption_are_independent_ledger_entries(self, tmp_path):
        d = tmp_path / ".methodology"
        d.mkdir()
        (d / "state.json").write_text("{bad1", encoding="utf-8")
        (d / "quality_manifest.json").write_text("{bad2", encoding="utf-8")
        load_state(tmp_path, lenient=True)
        load_quality_manifest(tmp_path, lenient=True)
        ledger = tmp_path / ".methodology" / "degradations.jsonl"
        lines = [line for line in ledger.read_text(encoding="utf-8").splitlines() if line.strip()]
        assert len(lines) == 2


class TestSyncMissingFrTraceability:
    """FR-99-class placeholder FRs: SAB.json's fr_module_traceability is
    amended in Phase 3 (module path decided post-P2); quality_manifest.json's
    copy is a Phase-2-exit snapshot that never regenerates. Every per-FR
    coverage-scope reader reads only the manifest, so the FR's genuinely
    resolvable scope was reported as unresolvable and the FR silently
    scored against the whole project instead of its own module."""

    def _manifest_dir(self, tmp_path):
        d = tmp_path / ".methodology"
        d.mkdir()
        return d

    def test_backfills_from_sab_when_manifest_lacks_the_key(self, tmp_path):
        d = self._manifest_dir(tmp_path)
        (d / "quality_manifest.json").write_text(
            json.dumps({"fr_module_traceability": {"FR-01": ["pkg.api"]}}),
            encoding="utf-8",
        )
        (d / "SAB.json").write_text(
            json.dumps({"fr_module_traceability": {
                "FR-01": ["pkg.api"], "FR-99": ["taskq.framework_paths"],
            }}),
            encoding="utf-8",
        )
        manifest = load_quality_manifest(tmp_path)

        updated = sync_missing_fr_traceability(tmp_path, "FR-99", manifest)

        assert updated["fr_module_traceability"]["FR-99"] == ["taskq.framework_paths"]
        assert updated["fr_module_traceability"]["FR-01"] == ["pkg.api"], (
            "must not touch an unrelated, already-present key"
        )
        # Persisted, not just returned in-memory — a later, independent
        # `load_quality_manifest` call (e.g. from a different process/CLI
        # invocation) must see it too.
        on_disk = load_quality_manifest(tmp_path)
        assert on_disk["fr_module_traceability"]["FR-99"] == ["taskq.framework_paths"]

    def test_never_overwrites_an_existing_key(self, tmp_path):
        """Round 46: an FR whose scope truly cannot be resolved is scored
        against the harsher whole-project number, deliberately, not given
        an easier one. This function must never override a manifest entry
        that already exists, even if SAB.json disagrees with it."""
        d = self._manifest_dir(tmp_path)
        (d / "quality_manifest.json").write_text(
            json.dumps({"fr_module_traceability": {"FR-99": ["old.stale.path"]}}),
            encoding="utf-8",
        )
        (d / "SAB.json").write_text(
            json.dumps({"fr_module_traceability": {"FR-99": ["new.path"]}}),
            encoding="utf-8",
        )
        manifest = load_quality_manifest(tmp_path)

        updated = sync_missing_fr_traceability(tmp_path, "FR-99", manifest)

        assert updated["fr_module_traceability"]["FR-99"] == ["old.stale.path"]

    def test_no_op_when_sab_also_lacks_the_fr(self, tmp_path):
        """Genuinely unresolvable scope (neither file has it) must stay
        `None` downstream — this is the Round 46 harsher-fallback case,
        untouched by this sync."""
        d = self._manifest_dir(tmp_path)
        (d / "quality_manifest.json").write_text(
            json.dumps({"fr_module_traceability": {}}), encoding="utf-8")
        (d / "SAB.json").write_text(
            json.dumps({"fr_module_traceability": {}}), encoding="utf-8")
        manifest = load_quality_manifest(tmp_path)

        updated = sync_missing_fr_traceability(tmp_path, "FR-99", manifest)

        assert "FR-99" not in updated.get("fr_module_traceability", {})

    def test_no_op_and_no_raise_when_sab_json_is_missing(self, tmp_path):
        """Mirrors `test_gate1_live_coverage.py`'s `project_with_fr` fixture,
        which builds a project with no SAB.json at all and asserts the
        per-FR lookup still returns `None`, not an exception."""
        d = self._manifest_dir(tmp_path)
        (d / "quality_manifest.json").write_text(
            json.dumps({"fr_module_traceability": {}}), encoding="utf-8")
        manifest = load_quality_manifest(tmp_path)

        updated = sync_missing_fr_traceability(tmp_path, "FR-99", manifest)

        assert "FR-99" not in updated.get("fr_module_traceability", {})

    def test_no_op_and_no_raise_when_sab_json_is_corrupt(self, tmp_path):
        d = self._manifest_dir(tmp_path)
        (d / "quality_manifest.json").write_text(
            json.dumps({"fr_module_traceability": {}}), encoding="utf-8")
        (d / "SAB.json").write_text("{not json", encoding="utf-8")
        manifest = load_quality_manifest(tmp_path)

        updated = sync_missing_fr_traceability(tmp_path, "FR-99", manifest)

        assert "FR-99" not in updated.get("fr_module_traceability", {})

    def test_records_a_degradation_entry_when_it_backfills(self, tmp_path):
        d = self._manifest_dir(tmp_path)
        (d / "quality_manifest.json").write_text(
            json.dumps({"fr_module_traceability": {}}), encoding="utf-8")
        (d / "SAB.json").write_text(
            json.dumps({"fr_module_traceability": {"FR-99": ["taskq.framework_paths"]}}),
            encoding="utf-8",
        )
        manifest = load_quality_manifest(tmp_path)

        sync_missing_fr_traceability(tmp_path, "FR-99", manifest)

        ledger = tmp_path / ".methodology" / "degradations.jsonl"
        assert ledger.exists()
        entry = json.loads(ledger.read_text(encoding="utf-8").strip())
        assert entry["component"] == "state-io.sync_missing_fr_traceability"
        assert "FR-99" in entry["what"]
