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

    def test_detect_sad_drift_ignores_nfr_rows(self, tmp_path):
        """NFR-XX table rows must not be parsed as phantom FR-XX mappings.

        Reset-rerun 2026-07-11: SAD.md NFR rows like
        `| NFR-06 | deployability | \\`config.py\\` ... |` matched
        SAD_FR_PATTERN via the FR-06 substring inside NFR-06, producing a
        HIGH "SAD maps FR-06 to config.py but file not found" drift for an
        FR that does not exist in the spec.
        """
        arch_dir = tmp_path / "02-architecture"
        arch_dir.mkdir()
        sad_path = arch_dir / "SAD.md"
        sad_path.write_text(
            "| NFR-06 | deployability | `config.py` reads 8 env vars |\n"
            "| NFR-03 | reliability | `store.py` atomic writes |\n"
        )

        detector = DriftDetector(str(tmp_path))
        result = detector.detect_sad_drift()

        assert result.checked == 0
        assert result.drifted == 0
        assert result.has_drift is False

    def test_detect_sad_drift_still_matches_real_fr_rows(self, tmp_path):
        """Real FR-XX rows keep matching after the NFR lookbehind fix."""
        arch_dir = tmp_path / "02-architecture"
        arch_dir.mkdir()
        (arch_dir / "SAD.md").write_text(
            "| FR-01 | `missing.py` |\n"
            "| NFR-06 | `config.py` env vars |\n"
        )

        result = DriftDetector(str(tmp_path)).detect_sad_drift()

        assert result.checked == 1
        assert result.drifted == 1
        assert result.drift_items[0].location == "FR-01"

    def test_detect_spec_drift_ignores_nfr_mentions(self, tmp_path):
        """NFR-XX text in SRS/code must not create phantom FR requirements."""
        req_dir = tmp_path / "01-requirements"
        req_dir.mkdir()
        (req_dir / "SRS.md").write_text(
            "Requirements: FR-01. Non-functional: NFR-06 deployability."
        )
        (tmp_path / "app.py").write_text('""" [FR-01] """\ndef main(): pass')

        result = DriftDetector(str(tmp_path)).detect_spec_drift()

        assert result.checked == 1  # FR-01 only — NFR-06 is not FR-06
        assert result.drifted == 0
        assert result.has_drift is False

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

    def test_detect_sab_drift_dict_module_blank_implemented_in_falls_back_to_name(
        self, tmp_path
    ):
        """Dict-shaped module entry with a *blank* (present-but-empty)
        ``implemented_in`` must fall back to ``name`` for the existence
        check, exactly like a missing key does.

        Round 6 station 1: the pre-fix inline unwrap was
        ``mod.get("implemented_in", mod.get("name", ""))`` — ``.get()``
        only falls back to its default when the key is *absent*, not when
        it is present-but-blank, so ``implemented_in: ""`` resolved to the
        literal empty string, and ``Path(x) / "" == Path(x)`` (the project
        root, which always exists) silently passed the existence check for
        ANY module carrying this shape. Delegating to
        ``sab_amender.sab_module_candidate()`` closes this.
        """
        method_dir = tmp_path / ".methodology"
        method_dir.mkdir()
        sab_json = {
            "layers": [
                {
                    "name": "L1",
                    "modules": [
                        {"name": "missing_pkg.py", "implemented_in": ""},
                    ],
                    "allowed_dependencies": [],
                },
            ],
            "dependencies": {"L1": []},
        }
        (method_dir / "SAB.json").write_text(
            __import__("json").dumps(sab_json)
        )
        # Deliberately do NOT create missing_pkg.py anywhere.

        detector = DriftDetector(str(tmp_path))
        result = detector.detect_sab_drift()
        assert result.has_drift is True, (
            "blank implemented_in must not silently mask a missing module "
            f"(got: {result})"
        )
        assert any("missing_pkg.py" in i.description for i in result.drift_items)

    def test_detect_sab_drift_unregistered_file(self, tmp_path):
        """New Python file not in any SAB layer is flagged.

        Note: v2.11 exempts auto-generated / standard wrapper files at project
        root (harness_cli.py, __init__.py, __main__.py). Unregistered detection
        therefore targets files inside actual project subdirectories.
        """
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
        # Put unregistered file in a subdirectory so root-level exemption doesn't apply
        sub = tmp_path / "pkg"
        sub.mkdir()
        (sub / "unregistered.py").write_text("# unknown")

        detector = DriftDetector(str(tmp_path))
        result = detector.detect_sab_drift()
        assert result.has_drift is True
        assert any("unregistered.py" in i.location for i in result.drift_items)

    def test_detect_sab_drift_scripts_dir_exempt_from_unregistered_check(self, tmp_path):
        """scripts/ is tooling/CI helpers (same category as harness/), not
        application code. amend_sab()/discover_modules() only ever scan
        src_dir (03-development/src/), so a scripts/*.py file can never be
        legitimately registered in any SAB layer — Check 2 must not flag it
        as unregistered (there is no way to ever clear such a finding)."""
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
        scripts_dir = tmp_path / "scripts"
        scripts_dir.mkdir()
        (scripts_dir / "shell_audit.py").write_text("# tooling script")

        detector = DriftDetector(str(tmp_path))
        result = detector.detect_sab_drift()
        assert not any("shell_audit.py" in i.location for i in result.drift_items)

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

    def test_sab_drift_missing_module_counted_once(self, tmp_path):
        """A missing SAB module must produce exactly ONE drift item.

        Reset-rerun 2026-07-11: the registry registers each bare module key
        plus a pkg_dir-prefixed alias (built only so Check 2 can match
        src-layout paths — see the comment above the alias registration).
        Check 1 iterated both keys, so every missing module was double-counted
        (3 real gaps reported as 6 drifts) and existing modules double-counted
        as passing, distorting the sab score in both directions.
        """
        import json as _json
        method_dir = tmp_path / ".methodology"
        method_dir.mkdir()
        sab_json = {
            "layers": [
                {"name": "core", "modules": ["taskq.cli", "taskq.config"],
                 "allowed_dependencies": []},
            ],
            "dependencies": {"core": []},
        }
        (method_dir / "SAB.json").write_text(_json.dumps(sab_json))
        dev_dir = tmp_path / "03-development" / "src" / "taskq"
        dev_dir.mkdir(parents=True)
        (dev_dir / "cli.py").write_text("# cli")
        # taskq.config deliberately missing

        result = DriftDetector(str(tmp_path)).detect_sab_drift()

        missing = [i for i in result.drift_items if i.actual == "not found"]
        assert len(missing) == 1, (
            f"missing module double-counted: {[i.expected for i in missing]}"
        )
        assert missing[0].expected == "taskq.config"
        assert result.checked == 2  # one entry per declared module
        assert result.drifted == 1

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

    def test_sab_drift_resolves_package_dir_v2_11(self, tmp_path):
        """Regression test for v2.11: SAB modules written in non-prefixed form
        (e.g. 'taskq.cli') must be resolved against src/-layout projects where
        the actual file lives at 'src/taskq/cli.py'.

        Before fix: drift_detector only tried 'taskq/cli.py' and
        '03-development/taskq/cli.py', missing '03-development/src/taskq/cli.py'
        even when setup.cfg declared ``package_dir = src``. Triggered 13 false
        SAB drifts on integration-test (5 missing + 8 unregistered).
        """
        import json as _json
        # setup.cfg with src/-layout
        dev = tmp_path / "03-development"
        dev.mkdir()
        (dev / "setup.cfg").write_text(
            "[options]\npackage_dir =\n    =src\n"
            "[options.packages.find]\nwhere = src\n"
        )
        # Real source under src/
        src_pkg = dev / "src" / "taskq"
        src_pkg.mkdir(parents=True)
        for mod in ("config", "models", "store", "executor", "cli"):
            (src_pkg / f"{mod}.py").write_text(f"# {mod}")

        # SAB declares non-prefixed dotted modules (matches SAD.md §7 style)
        method_dir = tmp_path / ".methodology"
        method_dir.mkdir()
        sab_json = {
            "layers": [
                {"name": "entry",         "modules": ["taskq.cli"],
                 "allowed_dependencies": []},
                {"name": "application",   "modules": ["taskq.executor"],
                 "allowed_dependencies": []},
                {"name": "domain",        "modules": ["taskq.models"],
                 "allowed_dependencies": []},
                {"name": "infrastructure","modules": ["taskq.store"],
                 "allowed_dependencies": []},
                {"name": "config",        "modules": ["taskq.config"],
                 "allowed_dependencies": []},
            ],
            "dependencies": {},
        }
        (method_dir / "SAB.json").write_text(_json.dumps(sab_json))

        detector = DriftDetector(str(tmp_path))
        result = detector.detect_sab_drift()

        # Must have zero false-positive "file not found"
        missing = [i for i in result.drift_items
                   if "file not found" in i.description]
        assert missing == [], (
            f"v2.11 regression: src/-layout projects with non-prefixed SAB "
            f"modules were falsely flagged as missing: {[i.description for i in missing]}"
        )

        # Must have zero false-positive "unregistered" for the 5 SAB files
        unregistered = [i for i in result.drift_items
                        if i.actual == "unregistered"
                        and "/taskq/" in i.location
                        and not i.location.endswith("__init__.py")
                        and not i.location.endswith("__main__.py")]
        assert unregistered == [], (
            f"v2.11 regression: src/-layout project files falsely flagged as "
            f"unregistered: {[i.location for i in unregistered]}"
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

    def test_resolve_import_layer_shared_top_level_package_is_ambiguous(self, tmp_path):
        """A bare top-level package shared by multiple layers (e.g. pkg.cli /
        pkg.service / pkg.store) must not silently resolve to whichever layer
        iterates first — it's genuinely ambiguous from the import path alone."""
        detector = DriftDetector(str(tmp_path))
        layer_map = {
            "cli": {"pkgX.cli", "pkgX.__main__"},
            "service": {"pkgX.executor"},
            "store": {"pkgX.breaker", "pkgX.cache"},
        }
        assert detector._resolve_import_layer("pkgX", layer_map) is None
        # Fully-qualified submodule imports must still resolve uniquely —
        # the ambiguity fix must not regress the unambiguous case.
        assert detector._resolve_import_layer("pkgX.cache", layer_map) == "store"
        assert detector._resolve_import_layer("pkgX.executor", layer_map) == "service"
        assert detector._resolve_import_layer("pkgX.cli", layer_map) == "cli"

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

    def test_check3_catches_violation_with_dict_shaped_implemented_in(self, tmp_path):
        """Regression: Check 3 (import-dependency violations) must normalize
        dict-shaped SAB module entries to dotted form before comparing
        against real import statements.

        2026-07-15: the pre-fix inline unwrap (sab_module_candidate() +
        manual .rstrip("/")/.py-strip) never stripped the src_dir/"src/"
        path prefix, so a dict-shaped entry declaring `implemented_in:
        "taskq/cli.py"` normalized to "taskq/cli" instead of "taskq.cli" —
        _resolve_import_layer() then never matched it against a real
        `import taskq.config`, silently skipping the architecture-violation
        check entirely (a false PASS with zero warning, not a false BLOCK).
        Fixed by routing through the same normalize_sab_module_to_dotted()
        SSOT already used by SEC-R6's owner_module cross-check.
        """
        method_dir = tmp_path / ".methodology"
        method_dir.mkdir()
        pkg = tmp_path / "taskq"
        pkg.mkdir()
        (pkg / "cli.py").write_text("import taskq.config\n")
        (pkg / "config.py").write_text("X = 1\n")

        sab_json = {
            "layers": [
                {"name": "entry", "modules": [
                    {"name": "taskq.cli", "implemented_in": "taskq/cli.py"},
                ], "allowed_dependencies": []},
                {"name": "foundation", "modules": [
                    {"name": "taskq.config", "implemented_in": "taskq/config.py"},
                ], "allowed_dependencies": []},
            ],
            "dependencies": {"entry": [], "foundation": []},
        }
        (method_dir / "SAB.json").write_text(json.dumps(sab_json))

        detector = DriftDetector(str(tmp_path))
        result = detector.detect_sab_drift()
        assert result.has_drift is True, (
            "entry -> foundation import must be flagged as an architecture "
            f"violation (got: {result})"
        )
        assert any(
            "taskq.config" in i.description and "not an allowed dependency" in i.description
            for i in result.drift_items
        )

    def test_check3_catches_violation_with_dotted_only_sab_entry(self, tmp_path):
        """Regression (Fix 15, 2026-07-15): a plain dotted-string SAB module
        entry (no dict wrapper, no path form at all — e.g. "taskq.config")
        must be caught by Check 3, not just dict-shaped entries.

        Before Fix 15, `source_layer = sab_files.get(rel)` compared the raw
        SAB declaration string against the file's filesystem-relative path
        by EXACT match. A dotted-only entry's key ("taskq.config") could
        never equal a filesystem rel path ("taskq/config.py") — Check 3 was
        silently a no-op for this shape. Confirmed via direct debugging while
        building Fix 13's regression tests (see Part F of the plan): this
        was NOT a Fix 13 regression, it was a pre-existing, broader defect.
        Fix 15 resolves both the source file's own path and the import
        target through the same normalize_sab_module_to_dotted() +
        _resolve_import_layer() pair, closing this gap.
        """
        method_dir = tmp_path / ".methodology"
        method_dir.mkdir()
        pkg = tmp_path / "taskq"
        pkg.mkdir()
        (pkg / "cli.py").write_text("import taskq.config\n")
        (pkg / "config.py").write_text("X = 1\n")

        sab_json = {
            "layers": [
                {"name": "entry", "modules": ["taskq.cli"], "allowed_dependencies": []},
                {"name": "foundation", "modules": ["taskq.config"], "allowed_dependencies": []},
            ],
            "dependencies": {"entry": [], "foundation": []},
        }
        (method_dir / "SAB.json").write_text(json.dumps(sab_json))

        detector = DriftDetector(str(tmp_path))
        result = detector.detect_sab_drift()
        assert result.has_drift is True, (
            "entry -> foundation import via dotted-only SAB entries must be "
            f"flagged as an architecture violation (got: {result})"
        )
        assert any(
            "taskq.config" in i.description and "not an allowed dependency" in i.description
            for i in result.drift_items
        )

    def test_check3_catches_violation_with_dev_dir_src_prefix(self, tmp_path):
        """Regression (Fix 15): the real taskq-project layout — files under
        03-development/src/, SAB entries declared relative to the
        development dir (e.g. "src/taskq/cli.py" or bare "taskq.config")
        — must resolve correctly. This is the combination that was
        completely broken before Fix 15 (confirmed via direct debugging:
        `sab_files.get(rel)` compared "03-development/src/taskq/cli.py"
        against SAB's "src/taskq/cli.py" key — never equal)."""
        method_dir = tmp_path / ".methodology"
        method_dir.mkdir()
        dev_pkg = tmp_path / "03-development" / "src" / "taskq"
        dev_pkg.mkdir(parents=True)
        (dev_pkg / "cli.py").write_text("import taskq.config\n")
        (dev_pkg / "config.py").write_text("X = 1\n")

        sab_json = {
            "layers": [
                {"name": "entry", "modules": [
                    {"name": "taskq.cli", "implemented_in": "src/taskq/cli.py"},
                ], "allowed_dependencies": []},
                {"name": "foundation", "modules": ["taskq.config"], "allowed_dependencies": []},
            ],
            "dependencies": {"entry": [], "foundation": []},
        }
        (method_dir / "SAB.json").write_text(json.dumps(sab_json))

        detector = DriftDetector(str(tmp_path))
        result = detector.detect_sab_drift()
        assert result.has_drift is True, (
            "entry -> foundation import must be flagged even with the "
            f"03-development/src/ project layout (got: {result})"
        )
        assert any(
            "taskq.config" in i.description and "not an allowed dependency" in i.description
            for i in result.drift_items
        )

    def test_check3_unregistered_file_still_skipped(self, tmp_path):
        """Negative control (Fix 15): a file not declared in any SAB layer
        must NOT produce a Check 3 drift item — it stays exclusively Check
        2's responsibility ("unregistered file"). Guards against Fix 15's
        more permissive source_layer resolution (parent/child dotted-path
        matching via _resolve_import_layer) accidentally start claiming
        files that were never meant to be layer members."""
        method_dir = tmp_path / ".methodology"
        method_dir.mkdir()
        pkg = tmp_path / "taskq"
        pkg.mkdir()
        (pkg / "cli.py").write_text("import taskq.config\n")
        (pkg / "config.py").write_text("X = 1\n")
        (pkg / "scratch.py").write_text("import os\n")  # not in SAB at all

        sab_json = {
            "layers": [
                {"name": "entry", "modules": ["taskq.cli"], "allowed_dependencies": []},
                {"name": "foundation", "modules": ["taskq.config"], "allowed_dependencies": []},
            ],
            "dependencies": {"entry": [], "foundation": []},
        }
        (method_dir / "SAB.json").write_text(json.dumps(sab_json))

        detector = DriftDetector(str(tmp_path))
        result = detector.detect_sab_drift()
        assert not any(
            "scratch" in i.description and "not an allowed dependency" in i.description
            for i in result.drift_items
        ), f"unregistered file must not be claimed by Check 3 (got: {result})"

    def test_check3_layer_to_modules_normalizes_dotted_only_entry_unchanged(self, tmp_path):
        """A plain dotted-string SAB module entry must still normalize to
        the same dotted form it already was (no-op through
        normalize_sab_module_to_dotted) — this pins the specific behavior
        Fix 13 touches (the layer_to_modules construction). Uses
        detect_sab_drift with a dict-shaped entry whose implemented_in
        exactly matches the real file path, then confirms adding a
        dotted-only sibling entry for the same violation path doesn't
        change the outcome — i.e. layer_to_modules treats both shapes
        identically once normalized. (Fix 15 additionally closes the
        source_layer lookup gap this test's docstring used to describe as
        out-of-scope — see test_check3_catches_violation_with_dotted_only_sab_entry
        above for the now-fixed pure-dotted-only case.)"""
        method_dir = tmp_path / ".methodology"
        method_dir.mkdir()
        pkg = tmp_path / "taskq"
        pkg.mkdir()
        (pkg / "cli.py").write_text("import taskq.config\n")
        (pkg / "config.py").write_text("X = 1\n")

        sab_json = {
            "layers": [
                {"name": "entry", "modules": [
                    {"name": "taskq.cli", "implemented_in": "taskq/cli.py"},
                ], "allowed_dependencies": []},
                {"name": "foundation", "modules": ["taskq.config"], "allowed_dependencies": []},
            ],
            "dependencies": {"entry": [], "foundation": []},
        }
        (method_dir / "SAB.json").write_text(json.dumps(sab_json))

        detector = DriftDetector(str(tmp_path))
        result = detector.detect_sab_drift()
        assert result.has_drift is True
        assert any(
            "taskq.config" in i.description and "not an allowed dependency" in i.description
            for i in result.drift_items
        )
