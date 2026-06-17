"""
Tests for test compliance improvements (I-1 through I-6).

Covers:
  I-2: _check_fr_test_file_exists()   — Gate 1 FR→test file check
  I-3: _check_red_phase_ordering()    — D1 RED ordering
  I-1: cmd_check_test_inventory()     — D4 TEST_INVENTORY.yaml compliance
  I-6a: score.py R8b                  — objective_primary flag
  I-1 helpers: _scan_test_functions(), _flatten_test_names()
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path


sys.path.insert(0, str(Path(__file__).parent.parent))

from harness_cli import (  # pyright: ignore[reportMissingImports]
    _check_fr_test_file_exists,
    _check_red_phase_ordering,
    _scan_test_functions,
    _flatten_test_names,
    cmd_check_test_inventory,
)

sys.path.insert(0, str(Path(__file__).parent.parent / "harness" / "ssi" / "scripts"))
from score import (  # pyright: ignore[reportMissingImports]
    validate_score_file,
)


# ===================================================================
# I-2: _check_fr_test_file_exists
# ===================================================================

class TestCheckFrTestFileExists:
    """_check_fr_test_file_exists(project, fr_id)."""

    def test_fr07_file_exists_as_test_fr07(self, tmp_path: Path):
        """FR-07 matches test_fr07.py."""
        (tmp_path / "tests").mkdir(parents=True)
        (tmp_path / "tests" / "test_fr07.py").write_text("def test_something(): pass\n")
        ok, msg = _check_fr_test_file_exists(tmp_path, "FR-07")
        assert ok, f"expected OK, got: {msg}"

    def test_fr07_file_exists_as_test_fr7(self, tmp_path: Path):
        """FR-07 matches test_fr7.py (short form)."""
        (tmp_path / "tests").mkdir(parents=True)
        (tmp_path / "tests" / "test_fr7.py").write_text("def test_something(): pass\n")
        ok, msg = _check_fr_test_file_exists(tmp_path, "FR-07")
        assert ok, f"expected OK, got: {msg}"

    def test_fr_file_missing_blocks(self, tmp_path: Path):
        """Missing test file for FR-12 returns blocked."""
        (tmp_path / "tests").mkdir(parents=True)
        ok, msg = _check_fr_test_file_exists(tmp_path, "FR-12")
        assert not ok
        assert "BLOCKED" in msg
        assert "test_fr12.py" in msg

    def test_non_standard_fr_id_skipped(self, tmp_path: Path):
        """Non-matching FR-ID (e.g. 'TASK-42') returns OK."""
        (tmp_path / "tests").mkdir(parents=True)
        ok, msg = _check_fr_test_file_exists(tmp_path, "TASK-42")
        assert ok

    def test_fr_case_insensitive(self, tmp_path: Path):
        """fr-07 (lowercase) still matches."""
        (tmp_path / "tests").mkdir(parents=True)
        (tmp_path / "tests" / "test_fr07.py").write_text("def test_something(): pass\n")
        ok, msg = _check_fr_test_file_exists(tmp_path, "fr-07")
        assert ok, f"expected OK, got: {msg}"


# ===================================================================
# I-3: _check_red_phase_ordering
# ===================================================================

class TestCheckRedPhaseOrdering:
    """_check_red_phase_ordering(project, fr_id)."""

    def _init_git_repo(self, path: Path) -> None:
        """Initialize a git repo at path with user config for commits."""
        subprocess.run(["git", "init"], cwd=path, capture_output=True, timeout=10)
        subprocess.run(
            ["git", "config", "user.email", "test@test.com"],
            cwd=path, capture_output=True, timeout=10,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test"],
            cwd=path, capture_output=True, timeout=10,
        )

    def _commit_file(self, path: Path, rel_path: str, content: str) -> None:
        """Stage and commit a single file."""
        full = path / rel_path
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content)
        subprocess.run(["git", "add", rel_path], cwd=path, capture_output=True, timeout=10)
        subprocess.run(
            ["git", "commit", "-m", f"add {rel_path}"],
            cwd=path, capture_output=True, timeout=10,
        )

    def test_test_first_passes(self, tmp_path: Path):
        """Test committed before source → OK (TDD RED→GREEN)."""
        self._init_git_repo(tmp_path)
        (tmp_path / "tests").mkdir(parents=True)
        (tmp_path / "src").mkdir(parents=True)
        # Commit test first — sleep to ensure distinct timestamps
        self._commit_file(tmp_path, "tests/test_fr07.py", "def test_x(): pass\n")
        time.sleep(1)
        self._commit_file(tmp_path, "src/fr07_module.py", "def x(): return 1\n")
        ok, msg = _check_red_phase_ordering(tmp_path, "FR-07")
        assert ok, f"expected pass (test first), got: {msg}"

    def test_source_first_blocks(self, tmp_path: Path):
        """Source committed before test → blocked."""
        self._init_git_repo(tmp_path)
        (tmp_path / "tests").mkdir(parents=True)
        (tmp_path / "src").mkdir(parents=True)
        # Commit source first — sleep to ensure distinct timestamps
        self._commit_file(tmp_path, "src/fr07_module.py", "def x(): return 1\n")
        time.sleep(1)
        self._commit_file(tmp_path, "tests/test_fr07.py", "def test_x(): pass\n")
        ok, msg = _check_red_phase_ordering(tmp_path, "FR-07")
        assert not ok
        assert "BLOCKED" in msg


    def test_no_test_history_blocks(self, tmp_path: Path):
        """Test file never committed → blocked."""
        self._init_git_repo(tmp_path)
        (tmp_path / "tests").mkdir(parents=True)
        # Create test file but never commit it
        (tmp_path / "tests" / "test_fr07.py").write_text("def test_x(): pass\n")
        ok, msg = _check_red_phase_ordering(tmp_path, "FR-07")
        assert not ok
        assert "no git history" in msg

    def test_non_fr_skipped(self, tmp_path: Path):
        """Non FR-ID returns OK without checking git."""
        self._init_git_repo(tmp_path)
        ok, msg = _check_red_phase_ordering(tmp_path, "TASK-42")
        assert ok

    def test_short_form_naming(self, tmp_path: Path):
        """FR-7 (not FR-07) also matches."""
        self._init_git_repo(tmp_path)
        (tmp_path / "tests").mkdir(parents=True)
        (tmp_path / "src").mkdir(parents=True)
        self._commit_file(tmp_path, "tests/test_fr7.py", "def test_x(): pass\n")
        time.sleep(1)
        self._commit_file(tmp_path, "src/fr7_module.py", "def x(): return 1\n")
        ok, msg = _check_red_phase_ordering(tmp_path, "FR-07")
        assert ok, f"expected pass (short form naming), got: {msg}"


# ===================================================================
# I-1: _scan_test_functions / _flatten_test_names
# ===================================================================

class TestScanTestFunctions:
    """_scan_test_functions(test_dir)."""

    def test_finds_test_functions(self, tmp_path: Path):
        """Scans Python files and finds test_ prefixed functions."""
        test_dir = tmp_path / "tests"
        test_dir.mkdir(parents=True)
        (test_dir / "test_foo.py").write_text(
            "def test_hello(): pass\ndef test_world(): pass\ndef helper(): pass\n"
        )
        (test_dir / "test_bar.py").write_text(
            "def test_check(): pass\n"
        )
        fns = _scan_test_functions(test_dir)
        assert fns == {"test_hello", "test_world", "test_check"}

    def test_empty_dir(self, tmp_path: Path):
        """Empty directory returns empty set."""
        test_dir = tmp_path / "tests"
        test_dir.mkdir(parents=True)
        fns = _scan_test_functions(test_dir)
        assert fns == set()

    def test_non_existent_dir(self, tmp_path: Path):
        """Non-existent directory returns empty set."""
        fns = _scan_test_functions(tmp_path / "no-such-dir")
        assert fns == set()


class TestFlattenTestNames:
    """_flatten_test_names(inventory)."""

    def test_flatten_fr_tests(self):
        """FR tests are flattened to a set."""
        data = {
            "fr_tests": {
                "FR-07": {
                    "unit": ["test_a", "test_b"],
                    "integration": ["test_c"],
                },
            },
            "cross_cutting": {
                "security": ["test_sec1"],
            },
        }
        names = _flatten_test_names(data)
        assert names == {"test_a", "test_b", "test_c", "test_sec1"}

    def test_empty_inventory(self):
        """None or empty returns empty set."""
        assert _flatten_test_names(None) == set()
        assert _flatten_test_names({}) == set()

    def test_cross_cutting_as_list(self):
        """cross_cutting as flat list of names also works."""
        data = {
            "fr_tests": {},
            "cross_cutting": ["test_x", "test_y"],
        }
        names = _flatten_test_names(data)
        assert names == {"test_x", "test_y"}


# ===================================================================
# I-1: cmd_check_test_inventory
# ===================================================================

class TestCmdCheckTestInventory:
    """cmd_check_test_inventory(args)."""

    def _make_args(self, tmp_path: Path, **overrides) -> "any":  # type: ignore[reportGeneralTypeIssues]
        import argparse
        ns = argparse.Namespace()
        ns.project = str(tmp_path)
        ns.strict = False
        ns.threshold = 80.0
        ns.diff_mode = False
        ns.srs_crosscut = False
        ns.crg_gaps = False
        for k, v in overrides.items():
            setattr(ns, k, v)
        return ns

    def _write_inventory(self, path: Path, names: list[str]):
        """Write a minimal TEST_INVENTORY.yaml (for backward compat tests)."""
        target = path / "TEST_INVENTORY.yaml"
        lines = ["format_version: '1.0'", "fr_tests:", "  FR-01:", "    unit:"]
        for n in names:
            lines.append(f"      - {n}")
        lines.append("cross_cutting: {}")
        target.write_text("\n".join(lines) + "\n")

    def _write_test_spec(self, path: Path, names: list[str], fr_id: str = "FR-01"):
        """Write a minimal TEST_SPEC.md that spec-coverage-check can parse."""
        spec_dir = path / "02-architecture"
        spec_dir.mkdir(parents=True, exist_ok=True)
        lines = [
            "# TEST_SPEC.md",
            "",
            f"### {fr_id}: Example requirement",
            "",
            "| # | Test Function | Type | Derivation |",
            "|---|---|---|---|",
        ]
        for i, n in enumerate(names, 1):
            lines.append(f"| {i} | `{n}` | happy_path | Q1 |")
        (spec_dir / "TEST_SPEC.md").write_text("\n".join(lines) + "\n")

    def _write_test_file(self, path: Path, fns: list[str]):
        """Write a test file with given function names."""
        (path / "tests").mkdir(parents=True, exist_ok=True)
        content = "\n".join(f"def {f}(): pass" for f in fns)
        (path / "tests" / "test_dummy.py").write_text(content)

    def test_all_covered_passes(self, tmp_path: Path):
        """All required tests exist → passes (delegates to spec-coverage)."""
        self._write_test_spec(tmp_path, ["test_alpha", "test_bravo"])
        self._write_test_file(tmp_path, ["test_alpha", "test_bravo"])
        code = cmd_check_test_inventory(self._make_args(tmp_path))
        assert code == 0, "expected pass when all covered"

    def test_missing_functions_fails(self, tmp_path: Path):
        """Required tests missing → fails below threshold."""
        self._write_test_spec(tmp_path, ["test_alpha", "test_bravo", "test_charlie", "test_delta"])
        self._write_test_file(tmp_path, ["test_alpha"])  # only 1/4
        code = cmd_check_test_inventory(self._make_args(tmp_path, threshold=50.0))
        assert code == 1, "expected failure when 1/4 < 50%"

    def test_neither_spec_nor_inventory_with_strict(self, tmp_path: Path):
        """Neither TEST_SPEC.md nor TEST_INVENTORY.yaml with strict → blocked (8)."""
        code = cmd_check_test_inventory(self._make_args(tmp_path, strict=True))
        assert code == 8, "expected block (8) when neither file exists"

    def test_p1_naming_authority_enforced(self, tmp_path: Path):
        """If TEST_SPEC.md lacks names from TEST_INVENTORY.yaml, it blocks."""
        # 1. P1 inventory requires test_alpha and test_beta
        self._write_inventory(tmp_path, ["test_alpha", "test_beta"])
        # 2. P2 spec hallucinates and only has test_alpha
        self._write_test_spec(tmp_path, ["test_alpha", "test_hallucinated"])
        # 3. test implementations match spec
        self._write_test_file(tmp_path, ["test_alpha", "test_hallucinated"])
        # 4. the check should block because test_beta is missing in spec
        from harness_cli import _run_spec_coverage_check
        code, pct = _run_spec_coverage_check(tmp_path, threshold=60.0, verbose=False)
        assert code == 1, "expected block due to P1 naming authority violation"

    def test_no_spec_without_strict(self, tmp_path: Path):
        """No TEST_SPEC.md with strict=False → delegation returns 0 (missing → 100%)."""
        code = cmd_check_test_inventory(self._make_args(tmp_path))
        assert code == 0, "expected delegation pass (0) when no spec and not strict"

    def test_srs_crosscut_deprecated(self, tmp_path: Path):
        """--srs-crosscut prints deprecation, still delegates correctly."""
        self._write_test_spec(tmp_path, ["test_alpha"])
        self._write_test_file(tmp_path, ["test_alpha"])
        code = cmd_check_test_inventory(
            self._make_args(tmp_path, srs_crosscut=True)
        )
        assert code == 0  # delegation still passes

    def test_crg_gaps_deprecated(self, tmp_path: Path):
        """--crg-gaps prints deprecation, still delegates correctly."""
        self._write_test_spec(tmp_path, ["test_alpha"])
        self._write_test_file(tmp_path, ["test_alpha"])
        code = cmd_check_test_inventory(
            self._make_args(tmp_path, crg_gaps=True)
        )
        assert code == 0  # delegation still passes

    def test_diff_mode_preserved(self, tmp_path: Path):
        """--diff-mode preserved for backward compat, delegates correctly."""
        self._write_test_spec(tmp_path, ["test_alpha"])
        self._write_test_file(tmp_path, ["test_alpha"])
        code = cmd_check_test_inventory(
            self._make_args(tmp_path, diff_mode=True)
        )
        assert code == 0  # delegation still passes


# ===================================================================
# I-1 lifecycle: advance-phase checksum + finalize-gate D4 block
# ===================================================================

class TestI1LifecycleIntegration:
    """D4 lifecycle: P1 checksum write + Gate 2-4 imperative check."""

    def test_advance_phase_p1_checksum(self, tmp_path: Path):
        """P1 advance-phase writes test_inventory_checksum to state.json."""
        from harness_cli import _advance_prechecks
        from unittest.mock import patch
        (tmp_path / "TEST_INVENTORY.yaml").write_text(
            "format_version: '1.0'\nfr_tests:\n  FR-01:\n    unit:\n      - test_a\n"
        )
        state_dir = tmp_path / ".methodology"
        state_dir.mkdir(parents=True)
        state = {"phase": 1, "last_gate": 0}
        (state_dir / "state.json").write_text(json.dumps(state))

        from core.quality_gate.constitution.runner import ConstitutionResult
        _vacuous = ConstitutionResult(score=100.0, passed=True, violations=[])
        _fake_profile = type("_P", (), {"composite_threshold": lambda s, p: 75.0})()
        # Mock auditor + agent-B + constitution: test focuses on checksum logic only
        with patch("harness_cli._run_phase_auditor", return_value=0), \
             patch("harness_cli._verify_agent_b_approvals_core", return_value=(True, "mocked")), \
             patch("core.quality_gate.constitution.run_constitution_check", return_value=_vacuous), \
             patch("core.quality_gate.constitution.profile.get_profile", return_value=_fake_profile):
            code = _advance_prechecks(tmp_path, 1)
        assert code == 0, "expected P1 advance to proceed"

        updated = json.loads((state_dir / "state.json").read_text())
        cksum = updated.get("test_inventory_checksum", "")
        assert len(cksum) == 64, f"expected sha256 hexdigest, got {cksum!r}"

    def test_advance_phase_p1_no_inventory_skips(self, tmp_path: Path):
        """P1 advance without TEST_INVENTORY.yaml does not write checksum."""
        from harness_cli import _advance_prechecks
        from unittest.mock import patch
        state_dir = tmp_path / ".methodology"
        state_dir.mkdir(parents=True)
        state = {"phase": 1}
        (state_dir / "state.json").write_text(json.dumps(state))

        from core.quality_gate.constitution.runner import ConstitutionResult
        _vacuous = ConstitutionResult(score=100.0, passed=True, violations=[])
        _fake_profile = type("_P", (), {"composite_threshold": lambda s, p: 75.0})()
        # Mock auditor + agent-B + constitution: test focuses on checksum skip logic only
        with patch("harness_cli._run_phase_auditor", return_value=0), \
             patch("harness_cli._verify_agent_b_approvals_core", return_value=(True, "mocked")), \
             patch("core.quality_gate.constitution.run_constitution_check", return_value=_vacuous), \
             patch("core.quality_gate.constitution.profile.get_profile", return_value=_fake_profile):
            code = _advance_prechecks(tmp_path, 1)
        assert code == 0

        updated = json.loads((state_dir / "state.json").read_text())
        assert "test_inventory_checksum" not in updated



# ===================================================================
# I-6a: score.py R8b — objective_primary
# ===================================================================

class TestR8bObjectivePrimary:
    """validate_score_file R8b: objective_primary flag."""

    def _base_score(self) -> dict:
        return {
            "dimension": "mutation_testing",
            "round": 1,
            "llm_tier": 1,
            "llm_provider": "claude",
            "tool_outputs": "",
            "findings": [],
        }

    def test_objective_primary_close_scores_ok(self, tmp_path: Path):
        """tool_score=75, llm_score=70 (gap=5) → no R8b issue."""
        sd = self._base_score()
        sd.update({"tool_score": 75, "llm_score": 70, "score": 70,
                    "objective_primary": True})
        issues = validate_score_file("mutation_testing", sd, project_root=tmp_path)
        r8b = [i for i in issues if i.startswith("R8b")]
        assert not r8b, f"expected no R8b, got: {r8b}"

    def test_objective_primary_wide_deviation_no_r8b(self, tmp_path: Path):
        """R8b removed — objective_primary + wide llm/tool gap no longer warns."""
        sd = self._base_score()
        sd.update({"tool_score": 75, "llm_score": 50, "score": 75,
                    "objective_primary": True})
        issues = validate_score_file("mutation_testing", sd, project_root=tmp_path)
        r8b = [i for i in issues if i.startswith("R8b")]
        assert not r8b, f"R8b is removed; wide deviation must not generate R8b: {r8b}"

    def test_not_objective_primary_ignored(self, tmp_path: Path):
        """No objective_primary flag → R8b not triggered."""
        sd = self._base_score()
        sd.update({"tool_score": 75, "llm_score": 50, "score": 50})
        issues = validate_score_file("mutation_testing", sd, project_root=tmp_path)
        r8b = [i for i in issues if i.startswith("R8b")]
        assert not r8b, "expected no R8b when flag absent"

    def test_objective_primary_null_tool_score_not_r8b(self, tmp_path: Path):
        """tool_score=null triggers R8 instead (Tier 1)."""
        sd = self._base_score()
        sd.update({"tool_score": None, "llm_score": 80, "score": 80,
                    "objective_primary": True})
        issues = validate_score_file("mutation_testing", sd, project_root=tmp_path)
        r8 = [i for i in issues if i.startswith("R8")]
        assert r8, "expected R8 (null tool_score for Tier 1)"
        assert any("R8:" in i for i in r8), "must be R8 not R8b only"

# ===================================================================
# Fix 6: Wiring tests — I-2 and I-3 are called from cmd_finalize_gate
# ===================================================================

class TestFinalizeGateCompliance:
    """Verify I-2 (FR test file) and I-3 (RED ordering) block cmd_finalize_gate."""

    @staticmethod
    def _setup_finalize_context(tmp_path: Path, gate: int = 1,
                                phase: int = 3, fr_id: str = "FR-07") -> None:
        """Write minimal state needed to reach the I-2/I-3 check in finalize-gate.

        Bypasses: state.json seal (omitted → LEGACY warning, no block),
        commit interval (no timestamp file → first gate, passes).
        HR-10: sessions_spawn.log is populated with 2 valid A/B entries for
        fr_id (developer + reviewer, distinct session_ids) so HR-10 passes
        cleanly and execution reaches the I-2/I-3 checks.
        """
        import json as _json

        methodology = tmp_path / ".methodology"
        methodology.mkdir(parents=True, exist_ok=True)

        # state.json without seal → LEGACY, warns but doesn't block
        (methodology / "state.json").write_text(
            _json.dumps({"state": "ACTIVE", "current_phase": phase})
        )

        # quality_manifest.json (required by bridge pre-checks)
        (methodology / "quality_manifest.json").write_text(
            _json.dumps({"fr_ids": [fr_id], "gate_results": {"gate1": {}}})
        )

        # sessions_spawn.log with 2 valid A/B entries for fr_id so HR-10 passes
        # (HR-10 needs ≥2 entries with ≥2 distinct roles and distinct session_ids)
        (methodology / "sessions_spawn.log").write_text(
            _json.dumps({
                "fr_id": fr_id, "role": "developer",
                "session_id": "test-sid-agent-a",
            }) + "\n" +
            _json.dumps({
                "fr_id": fr_id, "role": "reviewer",
                "session_id": "test-sid-agent-b",
            }) + "\n"
        )

        # gate result file (bridge reads this)
        sessi = tmp_path / ".sessi-work"
        sessi.mkdir(parents=True, exist_ok=True)
        (sessi / f"gate{gate}_result.json").write_text(_json.dumps({
            "gate": gate, "phase": phase, "fr_id": fr_id,
            "score": 95.0, "quality_complete": True,
            "dimensions": {"linting": 95, "type_safety": 95, "test_coverage": 95},
        }))

        # sentinel file (run-gate must precede finalize-gate)
        key = fr_id.replace("-", "").lower()
        sentinel_dir = sessi / "sentinels"
        sentinel_dir.mkdir(parents=True, exist_ok=True)
        (sentinel_dir / f"g{gate}_{key}.flag").write_text("test")

    def _run(self, monkeypatch, tmp_path: Path,
             gate: int = 1, phase: int = 3, fr_id: str = "FR-07") -> tuple[int, str]:
        import argparse
        import io
        from harness_cli import cmd_finalize_gate
        monkeypatch.setattr(
            "harness_cli._make_git",
            lambda args, project: __import__(
                "harness.git_strategy"
            ).git_strategy.GitStrategy(project, enabled=False),
        )
        captured = io.StringIO()
        monkeypatch.setattr("sys.stdout", captured)
        a = argparse.Namespace(gate=gate, phase=phase,
                               project=str(tmp_path), fr_id=fr_id)
        try:
            code = cmd_finalize_gate(a)
        except SystemExit as e:
            code = e.code if e.code is not None else 0
        return code, captured.getvalue()  # type: ignore[reportReturnType]

    def test_i2_missing_test_file_returns_8(self, tmp_path: Path, monkeypatch):
        """cmd_finalize_gate returns 8 when FR test file is absent (I-2 wiring)."""
        self._setup_finalize_context(tmp_path)
        # tests/ dir exists but test_fr07.py is absent
        (tmp_path / "tests").mkdir()

        code, output = self._run(monkeypatch, tmp_path)
        assert code == 8, f"expected 8 (I-2 block), got {code}\n{output}"
        assert "BLOCKED" in output
        assert "test_fr07.py" in output

    def test_i2_present_test_file_does_not_block_on_i2(self, tmp_path: Path, monkeypatch):
        """cmd_finalize_gate does NOT return 8 for I-2 when test file exists."""
        self._setup_finalize_context(tmp_path)
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_fr07.py").write_text("def test_stub(): pass\n")

        # I-3 (RED ordering) may still block — that's fine; we only care it's not I-2.
        code, output = self._run(monkeypatch, tmp_path)
        # I-2 block would produce "test_fr07.py" with "BLOCKED" and exit 8
        assert not (code == 8 and "test_fr07.py" in output), (
            "I-2 incorrectly blocked despite test file being present"
        )

    def test_no_tests_dir_skips_i2_i3(self, tmp_path: Path, monkeypatch):
        """Without a tests/ dir (e.g. non-standard layout), I-2/I-3 are skipped."""
        self._setup_finalize_context(tmp_path)
        # Deliberately no tests/ directory

        code, output = self._run(monkeypatch, tmp_path)
        # Should not block on I-2 (test file check skipped when tests/ absent)
        assert not (code == 8 and "test_fr07.py" in output), (
            "I-2 check must not fire when tests/ directory is absent"
        )


# ===================================================================
# Fix 7: _parse_inventory_fallback coverage
# ===================================================================

class TestParseInventoryFallback:
    """_parse_inventory_fallback: YAML-free parser for CI/min-dep environments."""

    def _parse(self, text: str) -> dict:
        from harness_cli import _parse_inventory_fallback
        return _parse_inventory_fallback(text)

    def test_basic_fr_unit_names_extracted(self):
        yaml = (
            "fr_tests:\n"
            "  FR-07:\n"
            "    unit:\n"
            "      - test_knowledge_exact_match\n"
            "      - test_knowledge_no_match\n"
        )
        result = self._parse(yaml)
        assert "test_knowledge_exact_match" in result["fr_tests"].get("unit", [])
        assert "test_knowledge_no_match" in result["fr_tests"].get("unit", [])

    def test_integration_sub_key_distinguished_from_unit(self):
        yaml = (
            "fr_tests:\n"
            "  FR-07:\n"
            "    unit:\n"
            "      - test_unit_one\n"
            "    integration:\n"
            "      - test_integration_one\n"
        )
        result = self._parse(yaml)
        assert "test_unit_one" in result["fr_tests"].get("unit", [])
        assert "test_integration_one" in result["fr_tests"].get("integration", [])

    def test_cross_cutting_section_parsed(self):
        yaml = (
            "cross_cutting:\n"
            "  security:\n"
            "    - test_redteam_injection\n"
        )
        result = self._parse(yaml)
        assert "test_redteam_injection" in result["cross_cutting"].get("security", [])

    def test_empty_text_returns_empty_structure(self):
        result = self._parse("")
        assert result == {"fr_tests": {}, "cross_cutting": {}}

    def test_flatten_after_fallback_finds_all_names(self):
        """_flatten_test_names must work on fallback-parsed output."""
        from harness_cli import _flatten_test_names
        yaml = (
            "fr_tests:\n"
            "  FR-01:\n"
            "    unit:\n"
            "      - test_a\n"
            "    integration:\n"
            "      - test_b\n"
            "cross_cutting:\n"
            "  security:\n"
            "    - test_c\n"
        )
        parsed = self._parse(yaml)
        names = _flatten_test_names(parsed)
        assert names == {"test_a", "test_b", "test_c"}
