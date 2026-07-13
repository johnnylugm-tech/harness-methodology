"""Regression tests for phase_auditor — Bug M16.

M16 (line 1469): integrity_score may be a string ("90%") from .integrity_tracker.json.
   Line 1472 `if score >= 80` raises TypeError on int vs str in Python 3.

(Former Bug M15 coverage — SRS "Logic Verification Method" 1:1 ratio — was
removed 2026-07-13: the underlying check tested a requirement with no basis
in SKILL.md or phase1_plan.md, ported from a different codebase (methodology-v2,
commit 979f0e5) and never given a corresponding SKILL.md rule here. See
_check_srs_depth in scripts/phase_auditor.py.)
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
# Orphaned-check removal (2026-07-13): SRS.md without "Logic Verification
# Method" text must NOT produce any C5 finding referencing that phrase —
# the phrase has no basis in SKILL.md or phase1_plan.md (see module docstring).
# ---------------------------------------------------------------------------

class TestSrsDepthNoLogicVerificationMethodCheck:
    def test_srs_without_logic_verification_method_produces_no_such_finding(
        self, module, tmp_path
    ):
        req_dir = tmp_path / "01-requirements"
        req_dir.mkdir(parents=True, exist_ok=True)
        srs = req_dir / "SRS.md"
        srs.write_text(
            "SRS\n"
            "FR-01: do X\n"
            "FR-02: do Y\n"
            "FR-03: do Z\n",
            encoding="utf-8",
        )
        auditor = _auditor(module, tmp_path)
        auditor._check_srs_depth()
        logic_findings = [
            f for f in auditor.result.findings
            if "logic verification method" in f.title.lower()
        ]
        assert logic_findings == [], (
            f"expected no Logic Verification Method finding, got {logic_findings}"
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
