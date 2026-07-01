"""Regression tests for phase_auditor — Bugs M15/M16.

M15 (line 968): _check_srs_depth uses `logic_count >= max(1, fr_count // 2)`.
   With 3 FRs, threshold is 1 — so 1 logic method for 3 FRs passes.
   SKILL.md intent is 1:1, so the half-count threshold silently violates it.
M16 (line 1469): integrity_score may be a string ("90%") from .integrity_tracker.json.
   Line 1472 `if score >= 80` raises TypeError on int vs str in Python 3.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest


def _load_module():
    scripts_dir = str(Path(__file__).resolve().parents[1] / "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    return importlib.import_module("phase_auditor")


@pytest.fixture
def module():
    return _load_module()


def _auditor(module, project_root: str | Path, phase: int = 1):
    """Build a PhaseAuditor wired to a LocalFetcher."""
    fetcher = module.LocalFetcher(str(project_root))
    return module.PhaseAuditor(fetcher, phase)


# ---------------------------------------------------------------------------
# Bug M15: SRS with 3 FRs and 1 logic method must FAIL
# ---------------------------------------------------------------------------

class TestM15SrsLogicThreshold:
    def test_three_frs_one_logic_method_fails(
        self, module, tmp_path
    ):
        """Bug M15 regression: 3 FRs + 1 Logic Verification Method must NOT
        pass. With SKILL.md's 1:1 intent, the threshold must require at
        least 3 logic methods (or document the relaxed ratio)."""
        req_dir = tmp_path / "01-requirements"
        req_dir.mkdir(parents=True, exist_ok=True)
        srs = req_dir / "SRS.md"
        srs.write_text(
            "SRS\n"
            "FR-01: do X\n"
            "FR-02: do Y\n"
            "FR-03: do Z\n"
            "Logic Verification Method: one method covers all 3 FRs\n",
            encoding="utf-8",
        )
        auditor = _auditor(module, tmp_path)
        auditor._check_srs_depth()
        # Look ONLY for the logic-method finding (filter on dimension or text)
        logic_findings = [
            f for f in auditor.result.findings
            if "logic verification method" in f.title.lower()
        ]
        assert len(logic_findings) == 1, (
            f"M15: expected exactly 1 logic-method finding, got {len(logic_findings)}"
        )
        assert logic_findings[0].severity in ("WARNING", "CRITICAL"), (
            f"M15: 3 FRs + 1 logic method should be WARNING/CRITICAL, got "
            f"severity={logic_findings[0].severity}, title={logic_findings[0].title!r}"
        )

    def test_three_frs_three_logic_methods_passes(
        self, module, tmp_path
    ):
        """Sanity: 3 FRs + 3 Logic Verification Methods still passes."""
        req_dir = tmp_path / "01-requirements"
        req_dir.mkdir(parents=True, exist_ok=True)
        srs = req_dir / "SRS.md"
        srs.write_text(
            "SRS\n"
            "FR-01: a\nFR-02: b\nFR-03: c\n"
            "Logic Verification Method: a\n"
            "Logic Verification Method: b\n"
            "Logic Verification Method: c\n",
            encoding="utf-8",
        )
        auditor = _auditor(module, tmp_path)
        auditor._check_srs_depth()
        logic_findings = [
            f for f in auditor.result.findings
            if "logic verification method" in f.title.lower()
        ]
        assert len(logic_findings) == 1
        assert logic_findings[0].severity == "PASS", (
            f"M15: 3 FRs + 3 logic should PASS, got {logic_findings[0].severity}"
        )


# ---------------------------------------------------------------------------
# Bug M16: integrity_score must accept string percentages like "90%"
# ---------------------------------------------------------------------------

class TestM16IntegrityScoreStringCoercion:
    def test_string_percentage_does_not_crash(
        self, module, tmp_path, monkeypatch
    ):
        """Bug M16 regression: .integrity_tracker.json may store
        integrity_score as '90%' (string). Previous code did
        `if score >= 80` → TypeError on str vs int. Must coerce."""
        (tmp_path / ".integrity_tracker.json").write_text(
            '{"integrity_score": "90%", "violations": []}\n',
            encoding="utf-8",
        )
        auditor = _auditor(module, tmp_path)
        # Should not raise
        try:
            auditor.check_c8_integrity()
        except TypeError as exc:
            pytest.fail(f"M16: string percentage caused TypeError: {exc}")
        # The score 90% should be PASS (>= 80)
        findings = auditor.result.findings
        assert any(f.severity == "PASS" for f in findings), (
            f"M16: 90% string should evaluate as PASS, got findings="
            f"{[f.severity for f in findings]}"
        )

    def test_numeric_score_still_works(self, module, tmp_path):
        """Sanity: numeric score still works."""
        (tmp_path / ".integrity_tracker.json").write_text(
            '{"integrity_score": 75, "violations": []}\n',
            encoding="utf-8",
        )
        auditor = _auditor(module, tmp_path)
        auditor.check_c8_integrity()
        findings = auditor.result.findings
        # 75 → WARNING (50-79)
        assert any(f.severity == "WARNING" for f in findings), (
            f"M16: numeric 75 should be WARNING, got "
            f"{[f.severity for f in findings]}"
        )
