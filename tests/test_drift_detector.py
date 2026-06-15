"""
Unit tests for DriftDetector.
"""

import json

from detection.drift_detector import DriftDetector
from unittest.mock import patch


class TestDriftDetector:
    """Tests for the DriftDetector class."""

    def test_detect_phase_drift_no_state(self, tmp_path):
        """Verify drift detection when state.json is missing."""
        detector = DriftDetector(str(tmp_path))
        result = detector.detect_phase_drift()
        assert result.has_drift is False
        assert result.score == 1.0

    def test_detect_phase_drift_with_missing_artifacts(self, tmp_path):
        """Verify drift detection for previously-completed phases only."""
        # Setup methodology state
        method_dir = tmp_path / ".methodology"
        method_dir.mkdir()
        state_path = method_dir / "state.json"
        # Phase 3: checks P1 (SRS.md, TRACEABILITY_MATRIX.md) + P2 (SAD.md, ADR.md)
        state_path.write_text('{"current_phase": 3}')

        detector = DriftDetector(str(tmp_path))
        result = detector.detect_phase_drift()

        assert result.has_drift is True
        # At least 4 artifacts checked (P1: 2 + P2: 2)
        assert result.checked >= 4
        assert any("02-architecture/SAD.md" in i.description for i in result.drift_items)

    def test_detect_spec_drift_finds_missing_frs(self, tmp_path):
        """Verify drift detection when code doesn't cover all SRS FRs."""
        # Create SRS with FR-01 and FR-02
        req_dir = tmp_path / "01-requirements"
        req_dir.mkdir()
        srs_path = req_dir / "SRS.md"
        srs_path.write_text("Requirements: FR-01, FR-02")
        
        # Create implementation with only FR-01
        app_file = tmp_path / "app.py"
        app_file.write_text('""" [FR-01] """\ndef main(): pass')
        
        detector = DriftDetector(str(tmp_path))
        result = detector.detect_spec_drift()
        
        assert result.has_drift is True
        assert result.drifted == 1
        assert "FR-02" in result.drift_items[0].location

    def test_detect_sad_drift_finds_missing_files(self, tmp_path):
        """Verify drift detection when SAD points to non-existent files."""
        arch_dir = tmp_path / "02-architecture"
        arch_dir.mkdir()
        sad_path = arch_dir / "SAD.md"
        sad_path.write_text("| FR-01 | `missing.py` |")

        detector = DriftDetector(str(tmp_path))
        result = detector.detect_sad_drift()

        assert result.has_drift is True
        assert "missing.py" in result.drift_items[0].description

    def test_detect_sad_drift_no_sad_file(self, tmp_path):
        detector = DriftDetector(str(tmp_path))
        result = detector.detect_sad_drift()
        assert result.has_drift is False
        assert result.score == 1.0

    def test_detect_all_returns_dict(self, tmp_path):
        detector = DriftDetector(str(tmp_path))
        results = detector.detect_all()
        assert "sad" in results
        assert "spec" in results
        assert "phase" in results

    def test_drift_result_to_dict(self, tmp_path):
        from detection.drift_detector import DriftResult
        r = DriftResult(drift_type="phase", has_drift=True, checked=3,
                       drifted=1, score=0.67)
        d = r.to_dict()
        assert d["has_drift"] is True
        assert d["drifted"] == 1

    def test_find_file_found(self, tmp_path):
        req_dir = tmp_path / "01-requirements"
        req_dir.mkdir()
        (req_dir / "SRS.md").write_text("test")
        detector = DriftDetector(str(tmp_path))
        found = detector._find_file(["01-requirements/SRS.md"])
        assert found is not None

    def test_find_file_not_found(self, tmp_path):
        detector = DriftDetector(str(tmp_path))
        assert detector._find_file(["nonexistent.md"]) is None


class TestSabDriftDetection:
    """Tests for SAB architecture baseline drift detection."""

    def test_detect_sab_drift_no_baseline(self, tmp_path):
        """No SAB.json and no SAD.md → graceful empty result."""
        detector = DriftDetector(str(tmp_path))
        result = detector.detect_sab_drift()
        assert result.has_drift is False
        assert result.score == 1.0
        assert result.checked == 0

    def test_detect_sab_drift_missing_files(self, tmp_path):
        """SAB declares files that don't exist on disk."""
        method_dir = tmp_path / ".methodology"
        method_dir.mkdir()
        sab_json = {
            "layers": [
                {"name": "L1", "modules": ["missing_one.py", "missing_two.py"],
                 "allowed_dependencies": []},
            ],
            "dependencies": {"L1": []},
        }
        (method_dir / "SAB.json").write_text(
            __import__("json").dumps(sab_json)
        )
        detector = DriftDetector(str(tmp_path))
        result = detector.detect_sab_drift()
        assert result.has_drift is True
        assert result.drifted >= 2
        assert any("missing_one.py" in i.description for i in result.drift_items)

    def test_detect_sab_drift_unregistered_file(self, tmp_path):
        """New Python file not in any SAB layer is flagged."""
        method_dir = tmp_path / ".methodology"
        method_dir.mkdir()
        sab_json = {
            "layers": [
                {"name": "L1", "modules": ["known.py"], "allowed_dependencies": []},
            ],
            "dependencies": {"L1": []},
        }
        (method_dir / "SAB.json").write_text(
            __import__("json").dumps(sab_json)
        )
        (tmp_path / "known.py").write_text("# known")
        (tmp_path / "unregistered.py").write_text("# unknown")

        detector = DriftDetector(str(tmp_path))
        result = detector.detect_sab_drift()
        assert result.has_drift is True
        assert any("unregistered.py" in i.location for i in result.drift_items)

    def test_detect_sab_drift_dotted_module_resolves_to_path_bug30(self, tmp_path):
        """Regression test for Bug #30: SAB module entries using Python dotted
        notation (e.g. 'src.taskq.config') must be resolved to filesystem path
        notation ('src/taskq/config.py') when checking file existence.

        Before fix: detector compared dotted notation literally as a path,
        causing all dotted entries to be flagged as missing → 100% SAB drift
        for any P3+ project using 'src/<pkg>/<mod>.py' layout.
        """
        method_dir = tmp_path / ".methodology"
        method_dir.mkdir()
        sab_json = {
            "layers": [
                {"name": "core", "modules": [
                    "src.taskq.config",
                    "src.taskq.models",
                    "src.taskq.store",
                    "src.taskq.executor",
                    "src.taskq.cli",
                ], "allowed_dependencies": []},
            ],
            "dependencies": {"core": []},
        }
        (method_dir / "SAB.json").write_text(__import__("json").dumps(sab_json))
        # Simulate the standard P3 layout: 03-development/src/taskq/{mod}.py
        dev_dir = tmp_path / "03-development" / "src" / "taskq"
        dev_dir.mkdir(parents=True)
        for mod in ("config", "models", "store", "executor", "cli"):
            (dev_dir / f"{mod}.py").write_text(f"# {mod}")

        detector = DriftDetector(str(tmp_path))
        result = detector.detect_sab_drift()
        # None of the 5 dotted modules should be reported as missing
        missing_locs = [i.description for i in result.drift_items
                        if "file not found" in i.description]
        assert missing_locs == [], (
            f"Bug #30 regression: dotted module entries were flagged as missing: "
            f"{missing_locs}"
        )

    def test_detect_sab_drift_dotted_module_at_project_root(self, tmp_path):
        """Bug #30 variant: dotted module at project root (no 03-development/ prefix)."""
        method_dir = tmp_path / ".methodology"
        method_dir.mkdir()
        sab_json = {
            "layers": [
                {"name": "core", "modules": ["taskq.config", "taskq.store"],
                 "allowed_dependencies": []},
            ],
            "dependencies": {"core": []},
        }
        (method_dir / "SAB.json").write_text(__import__("json").dumps(sab_json))
        pkg_dir = tmp_path / "taskq"
        pkg_dir.mkdir()
        (pkg_dir / "config.py").write_text("# config")
        (pkg_dir / "store.py").write_text("# store")

        detector = DriftDetector(str(tmp_path))
        result = detector.detect_sab_drift()
        missing_locs = [i.description for i in result.drift_items
                        if "file not found" in i.description]
        assert missing_locs == [], (
            f"Bug #30 root variant: {missing_locs}"
        )

    def test_dotted_sab_entry_not_flagged_as_unregistered_bug31(self, tmp_path):
        """Regression test for Bug #31 (Check 2): a file whose path form matches
        a dotted SAB entry must NOT be reported as unregistered.

        Before fix: Check 2 compared the file's relative path directly against
        sab_file_set which contained dotted strings. 'src/taskq/config.py' was
        never equal to 'src.taskq.config', so every file in a dotted-notation
        SAB was incorrectly flagged as unregistered.

        After fix: _rel_dotted converts the path back to dotted form before the
        sab_file_set lookup, so registered files are suppressed correctly.
        """
        import json as _json
        method_dir = tmp_path / ".methodology"
        method_dir.mkdir()
        sab_json = {
            "layers": [
                {"name": "core", "modules": [
                    "src.taskq.config",
                    "src.taskq.store",
                ], "allowed_dependencies": []},
            ],
            "dependencies": {"core": []},
        }
        (method_dir / "SAB.json").write_text(_json.dumps(sab_json))
        dev_dir = tmp_path / "03-development" / "src" / "taskq"
        dev_dir.mkdir(parents=True)
        (dev_dir / "config.py").write_text("# config")
        (dev_dir / "store.py").write_text("# store")

        detector = DriftDetector(str(tmp_path))
        result = detector.detect_sab_drift()

        unregistered = [i for i in result.drift_items if i.actual == "unregistered"]
        assert unregistered == [], (
            f"Bug #31 regression: registered dotted-module files falsely flagged "
            f"as unregistered: {[i.location for i in unregistered]}"
        )

    def test_dotted_sab_entry_unregistered_file_still_caught(self, tmp_path):
        """Check 2 with dotted SAB entries: a truly unregistered file alongside
        registered ones must still be flagged (fix must not over-suppress).
        """
        import json as _json
        method_dir = tmp_path / ".methodology"
        method_dir.mkdir()
        sab_json = {
            "layers": [
                {"name": "core", "modules": ["src.taskq.config"],
                 "allowed_dependencies": []},
            ],
            "dependencies": {"core": []},
        }
        (method_dir / "SAB.json").write_text(_json.dumps(sab_json))
        dev_dir = tmp_path / "03-development" / "src" / "taskq"
        dev_dir.mkdir(parents=True)
        (dev_dir / "config.py").write_text("# config")       # registered
        (dev_dir / "secret.py").write_text("# not in SAB")   # unregistered

        detector = DriftDetector(str(tmp_path))
        result = detector.detect_sab_drift()

        unregistered_locs = [i.location for i in result.drift_items
                             if i.actual == "unregistered"]
        assert any("secret.py" in loc for loc in unregistered_locs), (
            f"Unregistered file was not caught; unregistered items: {unregistered_locs}"
        )
        assert not any("config.py" in loc for loc in unregistered_locs), (
            f"Registered file was falsely flagged; unregistered items: {unregistered_locs}"
        )

    def test_detect_sab_drift_import_violation(self, tmp_path):
        """Cross-layer import where dependency is not allowed."""
        method_dir = tmp_path / ".methodology"
        method_dir.mkdir()
        sab_json = {
            "layers": [
                {"name": "L1", "modules": ["layer1_mod.py"],
                 "allowed_dependencies": []},
                {"name": "L2", "modules": ["layer2_mod.py"],
                 "allowed_dependencies": []},
            ],
            "dependencies": {"L1": [], "L2": ["L1"]},
        }
        (method_dir / "SAB.json").write_text(
            __import__("json").dumps(sab_json)
        )
        (tmp_path / "layer1_mod.py").write_text("import layer2_mod\n# violates: L1→L2 not allowed")
        (tmp_path / "layer2_mod.py").write_text("import layer1_mod\n# allowed: L2→L1")

        detector = DriftDetector(str(tmp_path))
        result = detector.detect_sab_drift()
        # layer1_mod imports layer2_mod which is disallowed
        critical_items = [i for i in result.drift_items if i.severity.value == "CRITICAL"]
        assert len(critical_items) >= 1
        assert any("layer1_mod" in i.location for i in critical_items)

    def test_detect_sab_drift_clean(self, tmp_path):
        """All files match SAB — no drift."""
        method_dir = tmp_path / ".methodology"
        method_dir.mkdir()
        sab_json = {
            "layers": [
                {"name": "L1", "modules": ["mod_a.py"], "allowed_dependencies": []},
            ],
            "dependencies": {"L1": []},
        }
        (method_dir / "SAB.json").write_text(
            __import__("json").dumps(sab_json)
        )
        (tmp_path / "mod_a.py").write_text("# part of L1")

        detector = DriftDetector(str(tmp_path))
        result = detector.detect_sab_drift()
        assert result.has_drift is False
        assert result.score == 1.0

    def test_resolve_import_layer_dotted(self, tmp_path):
        """Dotted import path resolves to correct layer."""
        detector = DriftDetector(str(tmp_path))
        layer_map = {
            "Core": {"core/phase_hooks", "core/agent_spawner"},
            "Bridge": {"harness/harness_bridge", "harness"},
        }
        assert detector._resolve_import_layer("core.phase_hooks", layer_map) == "Core"
        assert detector._resolve_import_layer("harness.harness_bridge", layer_map) == "Bridge"

    def test_resolve_import_layer_directory(self, tmp_path):
        """Import under a directory-registered path resolves correctly."""
        detector = DriftDetector(str(tmp_path))
        layer_map = {"Bridge": {"harness"}}
        assert detector._resolve_import_layer("harness.git_strategy", layer_map) == "Bridge"
        assert detector._resolve_import_layer("harness.crg_bridge", layer_map) == "Bridge"

    def test_resolve_import_layer_unmatched(self, tmp_path):
        """Unknown import returns None."""
        detector = DriftDetector(str(tmp_path))
        layer_map = {"Core": {"core/phase_hooks"}}
        assert detector._resolve_import_layer("nonexistent.module", layer_map) is None

    def test_detect_all_includes_sab(self, tmp_path):
        """detect_all returns 'sab' key in results dict."""
        detector = DriftDetector(str(tmp_path))
        results = detector.detect_all()
        assert "sab" in results
        assert results["sab"].drift_type == "sab"

    def test_sab_drift_skips_before_phase_3(self, tmp_path):
        """Phase < 3: drift check returns has_drift=False regardless of SAB content."""
        method_dir = tmp_path / ".methodology"
        method_dir.mkdir()
        (method_dir / "state.json").write_text(json.dumps({"current_phase": 2}))
        # Include a SAB with a missing file so drift *would* fire if phase gate didn't short-circuit.
        sab_json = {"layers": [{"name": "L1", "modules": ["missing.py"]}], "dependencies": {}}
        (method_dir / "SAB.json").write_text(json.dumps(sab_json))

        result = DriftDetector(str(tmp_path)).detect_sab_drift()
        assert result.has_drift is False
        assert result.score == 1.0
        assert "skipped" in result.drift_items[0].description.lower()

    def test_sab_drift_runs_at_phase_3(self, tmp_path):
        """Phase 3: drift check executes and flags missing modules."""
        method_dir = tmp_path / ".methodology"
        method_dir.mkdir()
        (method_dir / "state.json").write_text(json.dumps({"current_phase": 3}))
        sab_json = {"layers": [{"name": "L1", "modules": ["missing.py"]}], "dependencies": {}}
        (method_dir / "SAB.json").write_text(json.dumps(sab_json))

        result = DriftDetector(str(tmp_path)).detect_sab_drift()
        assert result.has_drift is True

    def test_load_sab_baseline_from_sad_fallback(self, tmp_path):
        """Falls back to parsing SAD.md §6 SAB block when SAB.json missing."""
        arch_dir = tmp_path / "02-architecture"
        arch_dir.mkdir()
        sad = arch_dir / "SAD.md"
        sad.write_text("""<!-- SAB:START -->
```json
{"layers": [{"name": "L0", "modules": ["main.py"], "allowed_dependencies": []}],
 "dependencies": {"L0": []}, "version": "1.0", "project": "test"}
```
<!-- SAB:END -->""")
        (tmp_path / "main.py").write_text("# main")

        detector = DriftDetector(str(tmp_path))
        with patch("scripts.generate_sab.parse_sad") as mock_parse:
            mock_parse.return_value = {
                "layers": [{"name": "L0", "modules": ["main.py"],
                            "allowed_dependencies": []}],
                "dependencies": {"L0": []},
            }
            result = detector.detect_sab_drift()
            assert result.has_drift is False

    def test_sab_drift_skips_venv_and_pycache(self, tmp_path):
        """venv and __pycache__ files are excluded from SAB drift checks."""
        method_dir = tmp_path / ".methodology"
        method_dir.mkdir()
        sab_json = {"layers": [], "dependencies": {}}
        (method_dir / "SAB.json").write_text(
            __import__("json").dumps(sab_json)
        )
        venv = tmp_path / "venv" / "lib"
        venv.mkdir(parents=True)
        (venv / "third_party.py").write_text("# venv")
        pycache = tmp_path / "__pycache__"
        pycache.mkdir()
        (tmp_path / "real_module.py").write_text("# real")

        detector = DriftDetector(str(tmp_path))
        result = detector.detect_sab_drift()
        # real_module.py is flagged, venv/__pycache__ files are not
        flagged = [i for i in result.drift_items if "venv" in i.location or "__pycache__" in i.location]
        assert len(flagged) == 0
