"""Tests for PhaseAuditor — fallback path and C3 session separation."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

import pytest

from scripts.phase_auditor import (
    PhaseAuditor, _ENTRY_GATE_MAP, LocalFetcher,
)

# Standard valid A/B JSONL for testing
VALID_AB_JSONL = (
    json.dumps({"timestamp": "2026-01-01T10:00:00", "fr_id": "FR-01", "role": "developer",
                "session_id": "dev-001", "status": "success", "confidence": 8}) + "\n" +
    json.dumps({"timestamp": "2026-01-01T10:05:00", "fr_id": "FR-01", "role": "reviewer",
                "session_id": "rev-001", "status": "success", "review_status": "APPROVE"}) + "\n"
)


class FakeGitHubFetcher:
    """Test double that serves files from a dict in memory — no gh CLI calls."""

    def __init__(self, files: dict[str, str]):
        self.repo = "fake/repo"
        self._files = files  # path -> content

    def get_tree(self) -> list[dict]:
        """Dynamically produce tree entries from stored file keys."""
        return [{"path": path, "type": "blob"} for path in self._files]

    def resolve_path(self, candidates: list[str]) -> Optional[str]:
        for path in candidates:
            if path in self._files:
                return path
        return None

    def get_file_content(self, path: str) -> Optional[str]:
        return self._files.get(path)

    def file_exists(self, path: str) -> bool:
        return path in self._files

    def get_commits(self, per_page: int = 30) -> list[dict]:
        """Return empty commit list — commit timeline tests not in scope here."""
        return []


@pytest.fixture
def auditor_factory():
    """Build a PhaseAuditor that reads from an in-memory file dict."""
    def _make(files: dict[str, str], phase: int = 3) -> PhaseAuditor:
        fetcher = FakeGitHubFetcher(files)
        return PhaseAuditor(fetcher, phase)  # type: ignore[reportArgumentType]
    return _make


class TestC9GatePass:
    """C9: quality_manifest.json gate PASS verification."""

    def _make_auditor(self, phase: int, manifest_data: Optional[dict] = None,
                      missing: bool = False) -> PhaseAuditor:
        files: dict[str, str] = {} if missing else {
            ".methodology/quality_manifest.json": json.dumps(manifest_data or {})
        }
        return PhaseAuditor(FakeGitHubFetcher(files), phase)  # type: ignore[reportArgumentType]

    def test_phase_below_4_returns_info(self):
        """Phases 1-3 have no gate entry requirement — C9 should report INFO."""
        a = self._make_auditor(3)
        a.check_c9_gate_pass()
        assert any(f.severity == "INFO" and f.check_id == "C9"
                   for f in a.result.findings)

    def test_manifest_missing_returns_critical(self):
        """Phase 4+ without quality_manifest.json should be CRITICAL."""
        a = self._make_auditor(4, missing=True)
        a.check_c9_gate_pass()
        assert any(f.severity == "CRITICAL" and f.check_id == "C9"
                   for f in a.result.findings)

    def test_gate2_pass_returns_pass(self):
        """Phase 4 requires Gate 2 PASS — quality_complete=True should give PASS."""
        assert _ENTRY_GATE_MAP[4] == 2
        manifest = {"gate_results": {"gate2": {"quality_complete": True}}}
        a = self._make_auditor(4, manifest)
        a.check_c9_gate_pass()
        assert any(f.severity == "PASS" and f.check_id == "C9"
                   for f in a.result.findings)

    def test_gate2_not_passed_returns_critical(self):
        """quality_complete=False should yield CRITICAL."""
        manifest = {"gate_results": {"gate2": {"quality_complete": False}}}
        a = self._make_auditor(4, manifest)
        a.check_c9_gate_pass()
        assert any(f.severity == "CRITICAL" and f.check_id == "C9"
                   for f in a.result.findings)

    def test_gate_key_missing_returns_critical(self):
        """If gate_results exists but the required gate key is absent, that is CRITICAL."""
        manifest = {"gate_results": {}}
        a = self._make_auditor(4, manifest)
        a.check_c9_gate_pass()
        assert any(f.severity == "CRITICAL" and f.check_id == "C9"
                   for f in a.result.findings)

    def test_gate4_required_for_phase7(self):
        """Phase 7 requires Gate 4 PASS."""
        assert _ENTRY_GATE_MAP[7] == 4
        manifest = {"gate_results": {"gate4": {"quality_complete": True}}}
        a = self._make_auditor(7, manifest)
        a.check_c9_gate_pass()
        assert any(f.severity == "PASS" and f.check_id == "C9"
                   for f in a.result.findings)


class FakeLocalFetcher:
    """Test double for LocalFetcher — in-memory dict, is_local=True."""

    is_local: bool = True

    def __init__(self, files: dict[str, str]):
        self.repo = "/fake/local/project"
        self._files = files

    def get_tree(self) -> list[dict]:
        return [{"path": path, "type": "blob"} for path in self._files]

    def resolve_path(self, candidates: list[str]) -> Optional[str]:
        for path in candidates:
            if path in self._files:
                return path
        return None

    def get_file_content(self, path: str) -> Optional[str]:
        return self._files.get(path)

    def file_exists(self, path: str) -> bool:
        return path in self._files

    def get_commits(self, per_page: int = 30) -> list[dict]:
        return []

    def get_repo_info(self) -> dict:
        return {"name": "fake-project", "full_name": "/fake/local/project"}


class TestC10LocalState:
    """C10: Local-only state consistency checks (state.json, gate4_result.json)."""

    def _make_auditor(self, phase: int, files: dict) -> PhaseAuditor:
        return PhaseAuditor(FakeLocalFetcher(files), phase)  # type: ignore[reportArgumentType]

    def test_github_fetcher_skips_c10(self):
        """C10 must no-op when using GitHubFetcher (is_local=False)."""
        a = PhaseAuditor(FakeGitHubFetcher({".methodology/state.json": '{"current_phase": 3}'}), 3)  # type: ignore[reportArgumentType]
        a.check_c10_local_state()
        assert not any(f.check_id == "C10" for f in a.result.findings)

    def test_state_phase_match_is_pass(self):
        """state.json current_phase == audited phase → PASS."""
        a = self._make_auditor(3, {
            ".methodology/state.json": json.dumps({"current_phase": 3})
        })
        a.check_c10_local_state()
        assert any(f.severity == "PASS" and f.check_id == "C10"
                   for f in a.result.findings)

    def test_state_phase_mismatch_is_warning(self):
        """state.json current_phase != audited phase → WARNING."""
        a = self._make_auditor(4, {
            ".methodology/state.json": json.dumps({"current_phase": 3})
        })
        a.check_c10_local_state()
        assert any(f.severity == "WARNING" and f.check_id == "C10"
                   and "current_phase=3" in f.title
                   for f in a.result.findings)

    def test_gate4_missing_p6_is_critical(self):
        """P6+ without any gate4_result.json → CRITICAL."""
        a = self._make_auditor(6, {
            ".methodology/state.json": json.dumps({"current_phase": 6})
        })
        a.check_c10_local_state()
        assert any(f.severity == "CRITICAL" and "gate4_result.json missing" in f.title
                   for f in a.result.findings if f.check_id == "C10")

    def test_gate4_sessi_work_path_found(self):
        """gate4_result.json at .sessi-work/ (primary path) should be found."""
        gate4 = json.dumps({"quality_complete": True, "model_used": "claude"})
        a = self._make_auditor(6, {
            ".methodology/state.json": json.dumps({"current_phase": 6}),
            ".sessi-work/gate4_result.json": gate4,
        })
        a.check_c10_local_state()
        assert any(f.severity == "PASS" and f.check_id == "C10"
                   and ".sessi-work/gate4_result.json" in f.title
                   for f in a.result.findings)

    def test_gate4_methodology_path_fallback(self):
        """gate4_result.json at .methodology/ (fallback path) should also work."""
        gate4 = json.dumps({"quality_complete": True})
        a = self._make_auditor(7, {
            ".methodology/state.json": json.dumps({"current_phase": 7}),
            ".methodology/gate4_result.json": gate4,
        })
        a.check_c10_local_state()
        assert any(f.severity == "PASS" and f.check_id == "C10"
                   and ".methodology/gate4_result.json" in f.title
                   for f in a.result.findings)

    def test_gate4_explicit_quality_complete_false_is_critical(self):
        """quality_complete=False must not be overridden by a truthy
        'passed' field — the old `data.get("quality_complete") or
        data.get("passed")` treated False the same as missing."""
        gate4 = json.dumps({"quality_complete": False, "passed": True})
        a = self._make_auditor(6, {
            ".methodology/state.json": json.dumps({"current_phase": 6}),
            ".sessi-work/gate4_result.json": gate4,
        })
        a.check_c10_local_state()
        assert any(f.severity == "CRITICAL" and f.check_id == "C10"
                   and "quality_complete=False" in f.title
                   for f in a.result.findings)

    def test_gate4_falls_back_to_passed_when_quality_complete_absent(self):
        """When quality_complete is entirely absent, "passed" is still a
        valid fallback signal."""
        gate4 = json.dumps({"passed": True})
        a = self._make_auditor(6, {
            ".methodology/state.json": json.dumps({"current_phase": 6}),
            ".sessi-work/gate4_result.json": gate4,
        })
        a.check_c10_local_state()
        assert any(f.severity == "PASS" and f.check_id == "C10"
                   for f in a.result.findings)

    def test_gate4_not_required_below_p6(self):
        """P5 and below: no gate4_result.json check should run."""
        a = self._make_auditor(5, {
            ".methodology/state.json": json.dumps({"current_phase": 5})
        })
        a.check_c10_local_state()
        gate4_findings = [f for f in a.result.findings
                          if f.check_id == "C10" and "gate4" in f.title.lower()]
        assert len(gate4_findings) == 0


class TestLocalFetcher:
    """LocalFetcher: filesystem read, git-exclude, get_commits format."""

    def test_get_tree_excludes_git(self, tmp_path: Path):
        """Files inside .git/ must not appear in the tree."""
        (tmp_path / ".git").mkdir()
        (tmp_path / ".git" / "config").write_text("gitconfig")
        (tmp_path / "README.md").write_text("# project")
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.py").write_text("pass")

        f = LocalFetcher(str(tmp_path))
        tree = f.get_tree()
        paths = {item["path"] for item in tree}
        assert ".git/config" not in paths
        assert os.path.join(".git", "config") not in paths
        assert "README.md" in paths
        assert os.path.join("src", "main.py") in paths

    def test_get_file_content_reads_utf8(self, tmp_path: Path):
        (tmp_path / "hello.md").write_text("héllo wörld", encoding="utf-8")
        f = LocalFetcher(str(tmp_path))
        content = f.get_file_content("hello.md")
        assert content == "héllo wörld"

    def test_get_file_content_missing_returns_none(self, tmp_path: Path):
        f = LocalFetcher(str(tmp_path))
        assert f.get_file_content("nonexistent.md") is None

    def test_resolve_path_returns_first_existing(self, tmp_path: Path):
        (tmp_path / "B.md").write_text("b")
        f = LocalFetcher(str(tmp_path))
        assert f.resolve_path(["A.md", "B.md", "C.md"]) == "B.md"

    def test_resolve_path_returns_none_when_all_missing(self, tmp_path: Path):
        f = LocalFetcher(str(tmp_path))
        assert f.resolve_path(["X.md", "Y.md"]) is None

    def test_get_repo_info_returns_dir_name(self, tmp_path: Path):
        f = LocalFetcher(str(tmp_path))
        info = f.get_repo_info()
        assert info["name"] == tmp_path.name

    def test_is_local_flag(self, tmp_path: Path):
        f = LocalFetcher(str(tmp_path))
        assert f.is_local is True

    def test_tree_cache(self, tmp_path: Path):
        """get_tree() should cache — filesystem changes after first call not reflected."""
        (tmp_path / "a.md").write_text("a")
        f = LocalFetcher(str(tmp_path))
        tree1 = f.get_tree()
        (tmp_path / "b.md").write_text("b")  # add file after first call
        tree2 = f.get_tree()
        assert tree1 is tree2  # same object (cached)


# ---------------------------------------------------------------------------
# TestC7FRCoverage — C7 redesigned (FR coverage vs quality_manifest)
# ── C5 P2: SAD FR coverage ───────────────────────────────────────────────────

class TestC5P2SadFrCoverage:
    def _make(self, sad=None, manifest=None, srs=None):
        files = {}
        if sad is not None:
            files["02-architecture/SAD.md"] = sad
        if manifest is not None:
            files[".methodology/quality_manifest.json"] = json.dumps(manifest)
        if srs is not None:
            files["01-requirements/SRS.md"] = srs
        return PhaseAuditor(FakeGitHubFetcher(files), 2)  # type: ignore[reportArgumentType]

    def test_no_sad_skips(self):
        a = self._make()
        a.check_c5_content_depth()
        assert not any(f.check_id == "C5" for f in a.result.findings)

    def test_full_fr_coverage_passes(self):
        a = self._make(
            "Module Architecture FR-01 covers login. FR-02 covers profile.",
            manifest={"fr_ids": ["FR-01", "FR-02"]},
        )
        a.check_c5_content_depth()
        assert any(f.check_id == "C5" and f.severity == "PASS"
                   and "2/2" in f.title for f in a.result.findings)

    def test_partial_coverage_warning_or_critical(self):
        a = self._make(
            "Module Architecture FR-01 mentioned.",
            manifest={"fr_ids": ["FR-01", "FR-02", "FR-03", "FR-04"]},
        )
        a.check_c5_content_depth()
        assert any(f.check_id == "C5" and f.severity in ("WARNING", "CRITICAL")
                   for f in a.result.findings)

    def test_no_fr_ids_info(self):
        a = self._make("Module Architecture", manifest={"fr_ids": []})
        a.check_c5_content_depth()
        assert any(f.check_id == "C5" and f.severity == "INFO"
                   for f in a.result.findings)

    def test_srs_fallback_coverage(self):
        a = self._make(
            "Module Architecture FR-01 FR-02.",
            srs="## FR-01 Login\n## FR-02 Profile\n",
        )
        a.check_c5_content_depth()
        assert any(f.check_id == "C5" and f.severity == "PASS"
                   for f in a.result.findings)


# ── C5 P4: TEST_RESULTS depth ────────────────────────────────────────────────

class TestC5P4TestResultsDepth:
    def _make(self, plan=None, results=None):
        files = {}
        if plan is not None:
            files["04-testing/TEST_PLAN.md"] = plan
        if results is not None:
            files["04-testing/TEST_RESULTS.md"] = results
        return PhaseAuditor(FakeGitHubFetcher(files), 4)  # type: ignore[reportArgumentType]

    def test_missing_results_critical(self):
        a = self._make("TC-01 TC-02 TC-03", None)
        a.check_c5_content_depth()
        assert any(f.check_id == "C5" and f.severity == "CRITICAL"
                   for f in a.result.findings)

    def test_full_results_passes(self):
        a = self._make(
            "TC-01 TC-02 TC-03",
            "42 passed, 0 failed\nTC-01 PASS\nTC-02 PASS\nTC-03 PASS\nTC-04 PASS",
        )
        a.check_c5_content_depth()
        assert any(f.check_id == "C5" and f.severity == "PASS"
                   for f in a.result.findings)

    def test_missing_rate_warning(self):
        a = self._make("TC-01", "TC-01 done. TC-02 done. TC-03 done. No pass count.")
        a.check_c5_content_depth()
        assert any(f.check_id == "C5" and f.severity == "WARNING"
                   for f in a.result.findings)


# ── C1: git tracking ─────────────────────────────────────────────────────────

class TestC1GitTracking:
    def _make_local(self, tmp_path, files: dict, tracked: list):
        for rel, content in files.items():
            p = tmp_path / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content)
        from scripts.phase_auditor import LocalFetcher as LF
        fetcher = LF(project_root=str(tmp_path))
        fetcher._is_git_tracked = lambda p: p in tracked  # type: ignore[reportAttributeAccessIssue]
        return PhaseAuditor(fetcher, 3)

    def test_tracked_file_no_git_critical(self, tmp_path):
        # Use Phase3_STAGE_PASS.md — a required FILE deliverable in Phase 3 spec
        a = self._make_local(
            tmp_path,
            {"00-summary/Phase3_STAGE_PASS.md": "content"},
            tracked=["00-summary/Phase3_STAGE_PASS.md"],
        )
        a.check_c1_deliverables()
        assert not any(
            f.check_id == "C1" and f.severity == "CRITICAL"
            and "git" in f.title.lower()
            for f in a.result.findings
        )

    def test_untracked_required_file_critical(self, tmp_path):
        # File is on disk but NOT git-tracked → C1 CRITICAL
        a = self._make_local(
            tmp_path,
            {"00-summary/Phase3_STAGE_PASS.md": "content"},
            tracked=[],
        )
        a.check_c1_deliverables()
        assert any(
            f.check_id == "C1" and f.severity == "CRITICAL"
            and "git" in (f.title + f.detail).lower()
            for f in a.result.findings
        )

    def test_remote_fetcher_skips_git_check(self):
        a = PhaseAuditor(FakeGitHubFetcher({"00-summary/Phase3_STAGE_PASS.md": "content"}), 3)  # type: ignore[reportArgumentType]
        a.check_c1_deliverables()
        assert not any(
            "git" in (f.title + f.detail).lower()
            for f in a.result.findings
        )


class TestC9GateScoreThreshold:
    def _manifest(self, gate_num, score, quality_complete=True):
        return json.dumps({
            "gate_results": {
                f"gate{gate_num}": {
                    "quality_complete": quality_complete,
                    "score": score,
                }
            }
        })

    def _make(self, phase, manifest_content):
        files = {".methodology/quality_manifest.json": manifest_content}
        return PhaseAuditor(FakeGitHubFetcher(files), phase)  # type: ignore[reportArgumentType]

    def test_gate2_score_above_threshold_passes(self):
        a = self._make(4, self._manifest(2, 55.0))
        a.check_c9_gate_pass()
        assert any(f.check_id == "C9" and f.severity == "PASS"
                   and "55" in f.title for f in a.result.findings)

    def test_gate2_score_below_threshold_critical(self):
        a = self._make(4, self._manifest(2, 35.0))
        a.check_c9_gate_pass()
        assert any(f.check_id == "C9" and f.severity == "CRITICAL"
                   and "35" in f.title for f in a.result.findings)

    def test_gate3_score_below_70_critical(self):
        a = self._make(5, self._manifest(3, 65.0))
        a.check_c9_gate_pass()
        assert any(f.check_id == "C9" and f.severity == "CRITICAL"
                   and "65" in f.title for f in a.result.findings)

    def test_gate4_score_below_88_critical(self):
        a = self._make(7, self._manifest(4, 85.0))
        a.check_c9_gate_pass()
        assert any(f.check_id == "C9" and f.severity == "CRITICAL"
                   and "85" in f.title for f in a.result.findings)

    def test_gate4_score_at_threshold_passes(self):
        a = self._make(7, self._manifest(4, 88.0))
        a.check_c9_gate_pass()
        assert any(f.check_id == "C9" and f.severity == "PASS"
                   and "88" in f.title for f in a.result.findings)

    def test_missing_score_field_warning(self):
        content = json.dumps({
            "gate_results": {"gate2": {"quality_complete": True}}
        })
        a = self._make(4, content)
        a.check_c9_gate_pass()
        assert any(f.check_id == "C9" and f.severity == "WARNING"
                   for f in a.result.findings)


class TestTraceabilityFrCoverage:
    def _make(self, matrix: str | None, srs: str | None = None):
        files = {}
        if matrix is not None:
            files["01-requirements/TRACEABILITY_MATRIX.md"] = matrix
        if srs is not None:
            files["01-requirements/SRS.md"] = srs
        return PhaseAuditor(FakeGitHubFetcher(files), 1)  # type: ignore[reportArgumentType]

    def test_full_fr_coverage_passes(self):
        a = self._make(
            "| FR | Module |\n| FR-01 | AuthModule |\n| FR-02 | UserModule |",
            "## FR-01 Login\n## FR-02 Profile",
        )
        a.check_c5_content_depth()
        assert any(f.check_id == "C5" and f.severity == "PASS"
                   and "covers all" in f.title for f in a.result.findings)

    def test_missing_fr_in_matrix_critical(self):
        a = self._make(
            "| FR | Module |\n| FR-01 | AuthModule |",
            "## FR-01 Login\n## FR-02 Profile\n## FR-03 Settings\n## FR-04 Admin",
        )
        a.check_c5_content_depth()
        assert any(f.check_id == "C5" and f.severity == "CRITICAL"
                   for f in a.result.findings)

    def test_partial_coverage_warning(self):
        # 4/5 = 80% → WARNING
        a = self._make(
            "| FR | Module |\n| FR-01 | A |\n| FR-02 | B |\n| FR-03 | C |\n| FR-04 | D |",
            "## FR-01\n## FR-02\n## FR-03\n## FR-04\n## FR-05",
        )
        a.check_c5_content_depth()
        assert any(f.check_id == "C5" and f.severity == "WARNING"
                   and "missing" in f.title for f in a.result.findings)

    def test_no_srs_skips_fr_coverage(self):
        a = self._make("| FR | Module |\n| FR-01 | A |", srs=None)
        a.check_c5_content_depth()
        # No SRS → no FR IDs to cross-check → no CRITICAL about FR coverage
        assert not any(
            f.check_id == "C5" and f.severity == "CRITICAL"
            and "covers only" in f.title
            for f in a.result.findings
        )

    def test_tbd_module_entries_warning(self):
        a = self._make(
            "| FR | Module |\n| FR-01 | TBD |\n| FR-02 | AuthModule |",
            "## FR-01\n## FR-02",
        )
        a.check_c5_content_depth()
        assert any(f.check_id == "C5" and f.severity == "WARNING"
                   and "TBD" in f.title for f in a.result.findings)


class TestC2StagePassMachineGenerated:
    """C2 must not CRITICAL-fail against the sole active generator's output.

    _generate_stage_pass() (harness_cli.py) is documented as "No LLM
    involvement" — it can never contain Agent B review keywords or a
    Confidence Score. Checks that assume LLM-authored content would
    CRITICAL-fail on every advance-phase run.
    """

    MACHINE_GENERATED_CONTENT = (
        "# Phase 3 STAGE_PASS\n\n"
        "Generated: 2026-07-05 00:00 UTC\n\n"
        "## Gate Score\n"
        "Gate 1 Composite Score: **95.0**\n\n"
        "## Quality Status\n"
        "quality_complete: **True**\n\n"
        "## Deliverables\n"
        "Phase 3 deliverables verified by PhaseArtifactRegistry.\n\n"
        "## Summary\n"
        "Phase 3 exit gate PASS.\n"
    )

    def test_machine_generated_content_has_no_c2_critical(self):
        files = {"00-summary/Phase3_STAGE_PASS.md": self.MACHINE_GENERATED_CONTENT}
        a = PhaseAuditor(FakeGitHubFetcher(files), 3)  # type: ignore[reportArgumentType]
        a.check_c2_stage_pass()
        criticals = [f for f in a.result.findings if f.check_id == "C2" and f.severity == "CRITICAL"]
        assert not criticals, f"Unexpected C2 CRITICAL finding(s): {[c.title for c in criticals]}"

    def test_machine_generated_true_marker_passes_quality_status(self):
        """2026-07-13 fix: the generator writes `quality_complete: **True**`
        (markdown bold) but the checker used to test the literal substring
        `quality_complete: True` (no asterisks) — never a match, so this
        exact realistic fixture always WARNING'd even though qc was True."""
        files = {"00-summary/Phase3_STAGE_PASS.md": self.MACHINE_GENERATED_CONTENT}
        a = PhaseAuditor(FakeGitHubFetcher(files), 3)  # type: ignore[reportArgumentType]
        a.check_c2_stage_pass()
        qc_findings = [f for f in a.result.findings if f.check_id == "C2" and "quality_complete" in f.title.lower()]
        assert qc_findings and qc_findings[0].severity == "PASS", (
            f"expected a PASS quality_complete finding, got {[(f.severity, f.title) for f in qc_findings]}"
        )

    def test_machine_generated_false_marker_warns_quality_status(self):
        """Sanity: **False** must still WARNING (not silently pass)."""
        content = self.MACHINE_GENERATED_CONTENT.replace(
            "quality_complete: **True**", "quality_complete: **False**"
        )
        files = {"00-summary/Phase3_STAGE_PASS.md": content}
        a = PhaseAuditor(FakeGitHubFetcher(files), 3)  # type: ignore[reportArgumentType]
        a.check_c2_stage_pass()
        qc_findings = [f for f in a.result.findings if f.check_id == "C2" and "quality_complete" in f.title.lower()]
        assert qc_findings and qc_findings[0].severity == "WARNING", (
            f"expected a WARNING quality_complete finding, got {[(f.severity, f.title) for f in qc_findings]}"
        )
