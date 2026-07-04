"""
Regression tests for 2 MEDIUM bugs in harness_bridge.py:

  1. _crg_enrich_gate_findings (line 459-461) — gate_result.json is
     written via direct `Path.write_text` which is non-atomic. A
     crash mid-write between truncate and flush leaves a truncated
     file. The fix must use `core.atomic_io.atomic_write_json`.

  2. HarnessBridge.generate_quality_manifest (line 2458) — manifest
     is written to `Path(".methodology/quality_manifest.json")` which
     is CWD-relative. CLI invocations with `--project-root <path>`
     from a different cwd write to the wrong location, silently
     corrupting harness state. The fix must take a `project_root`
     parameter (or use one supplied via the bridge).
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch, MagicMock

from harness.harness_bridge import (
    HarnessBridge,
    _crg_enrich_gate_findings,
)


# ── Bug 1: non-atomic gate_result.json write in CRG enrichment ──────────────

class TestGateResultWriteAtomic:
    def test_gate_result_writes_use_atomic_write_json(
        self, tmp_path: Path, monkeypatch,
    ):
        """Every write to gate_result.json inside _crg_enrich_gate_findings
        must go through core.atomic_io.atomic_write_json, never
        Path.write_text directly."""
        work_dir = tmp_path / "work"
        work_dir.mkdir()
        result_path = work_dir / "gate3_result.json"
        # Seed the file with a valid JSON doc the enricher can read.
        result_path.write_text(
            json.dumps({"breakdown": {}}, indent=2), encoding="utf-8"
        )

        # Track all Path.write_text calls.
        write_text_calls: list[str] = []
        orig_write_text = Path.write_text

        def _tracking(self, *args, **kwargs):
            write_text_calls.append(str(self))
            return orig_write_text(self, *args, **kwargs)

        monkeypatch.setattr(Path, "write_text", _tracking)

        # CRG mocks — return values that exercise the 9-step enrichment.
        crg = MagicMock()
        # Step 4 (get_review_context) returns a real dict → triggers the
        # gate_result.json write at line 459-461 (the bug under test).
        crg.get_review_context.return_value = {"some": "context"}
        # All other CRG methods return None so no other write sites fire.
        crg.find_large_functions.return_value = None
        crg.get_hub_nodes.return_value = None
        crg.check_dead_code.return_value = None
        crg.get_impact_radius.return_value = None
        crg.get_affected_flows.return_value = None
        crg.get_knowledge_gaps.return_value = None
        crg.list_flows.return_value = None
        crg.query_graph.return_value = None

        _crg_enrich_gate_findings(
            crg=crg,
            dims=[],
            project_root=str(tmp_path),
            work_dir=str(work_dir),
            gate_num=3,
        )

        # The result_path must NEVER have been written via Path.write_text.
        result_writes = [
            p for p in write_text_calls
            if p.endswith("gate3_result.json")
        ]
        assert result_writes == [], (
            f"gate_result.json must be written via atomic_write_json, "
            f"not Path.write_text. Direct write_text calls: {result_writes}"
        )


# ── Bug 2: CWD-relative quality_manifest.json path ───────────────────────────

class TestQualityManifestProjectRoot:
    def test_manifest_written_under_explicit_project_root(
        self, tmp_path: Path, monkeypatch,
    ):
        """generate_quality_manifest must write the manifest FILE to
        `<project_root>/.methodology/quality_manifest.json`, NOT to
        `<cwd>/.methodology/quality_manifest.json`. (The `.methodology`
        dir itself may be created in cwd by HarnessBridge.__init__ for
        other purposes — that is a separate concern; the bug under test
        is the manifest FILE being silently written to the wrong tree.)"""
        # Change cwd to a directory that does NOT contain the project.
        unrelated_cwd = tmp_path / "unrelated_cwd"
        unrelated_cwd.mkdir()
        monkeypatch.chdir(unrelated_cwd)

        bridge = HarnessBridge()
        project_root = tmp_path / "my_project"
        project_root.mkdir()

        with patch("scripts.generate_sab.parse_sad", return_value={}):
            out = bridge.generate_quality_manifest(
                fr_ids=["FR-01"],
                sad_path="SAD.md",
                project_root=str(project_root),
            )

        # The manifest FILE must be under the explicit project_root, not cwd.
        assert out is not None
        assert out.is_absolute(), f"expected absolute path, got {out}"
        assert str(project_root) in str(out), (
            f"manifest path {out} must be under project_root "
            f"{project_root}, not under cwd {unrelated_cwd}"
        )
        assert (project_root / ".methodology" / "quality_manifest.json").exists()
        # The CWD must not have a STALE quality_manifest.json from this call.
        assert not (unrelated_cwd / ".methodology" / "quality_manifest.json").exists(), (
            f"manifest leaked into cwd {unrelated_cwd} — the bug under test"
        )

    def test_manifest_path_ignores_cwd_change(
        self, tmp_path: Path, monkeypatch,
    ):
        """Even after chdir to a totally different directory, the
        manifest must land under the configured project_root."""
        original_cwd = tmp_path / "start"
        original_cwd.mkdir()
        monkeypatch.chdir(original_cwd)

        project_root = tmp_path / "real_project"
        project_root.mkdir()

        # Create the unrelated cwd BEFORE chdir-ing into it.
        unrelated = tmp_path / "totally_different_cwd"
        unrelated.mkdir()
        monkeypatch.chdir(unrelated)

        bridge = HarnessBridge()
        with patch("scripts.generate_sab.parse_sad", return_value={}):
            out = bridge.generate_quality_manifest(
                fr_ids=["FR-01"],
                sad_path="SAD.md",
                project_root=str(project_root),
            )
        assert str(project_root) in str(out)
        assert (project_root / ".methodology" / "quality_manifest.json").exists()
        # The CWD must not have a STALE manifest from this call.
        assert not (unrelated / ".methodology" / "quality_manifest.json").exists()
