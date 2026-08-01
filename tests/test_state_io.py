"""Round 14 站2 — core/state_io.py: converged reads for state.json /
quality_manifest.json, with a strict/lenient choice replacing the three
divergent shapes (uncaught raise, silent `except: pass`, broad fail-open)
found scattered across ~60 call sites before this station.
"""

from __future__ import annotations

import json

import pytest

from core.state_io import StateCorruptError, load_quality_manifest, load_state


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
