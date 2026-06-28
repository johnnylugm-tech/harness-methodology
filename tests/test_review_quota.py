"""Unit tests for core/review_quota.py — cap and triage LLM reviewer findings.

Improvement H of convergence plan: LLM reviewers emit unbounded findings,
each round triggers another fix cycle, no terminal condition. review_quota
caps findings via a severity-weighted quota so:
  - 'must-fix' findings (weight=3) are prioritised
  - 'should-fix' findings (weight=2) fit next
  - 'nit' findings (weight=1) fill remaining quota
  - overflow goes to triage, not throw

Tests cover:
  - categorize_finding rules (severity × evidence_type matrix)
  - enforce_quota: weighted allocation
  - auto_close_stale: annotation helper
  - Edge cases: empty findings, all-nits, all-must-fix
  - Determinism (no LLM dependency)
"""

import pytest

from core.review_quota import (
    DEFAULT_MAX_QUOTA,
    SEVERITY_WEIGHT,
    auto_close_stale,
    categorize_finding,
    enforce_quota,
)


# ---------------------------------------------------------------------------
# categorize_finding: severity × evidence_type matrix
# ---------------------------------------------------------------------------


class TestCategorizeFinding:
    @pytest.mark.parametrize("severity,evidence_type,expected", [
        # High severity
        ("high", "real_invention", "must-fix"),
        ("high", "over_interpretation", "should-fix"),  # HR-12 cap
        ("high", "methodology_artifact", "nit"),
        ("high", None, "must-fix"),  # missing evidence_type → real_invention default
        ("high", "", "must-fix"),
        ("HIGH", "REAL_INVENTION", "must-fix"),  # case-insensitive
        # Medium severity
        ("medium", "real_invention", "should-fix"),
        ("medium", "over_interpretation", "nit"),
        ("medium", "methodology_artifact", "nit"),
        ("medium", None, "should-fix"),
        # Low severity
        ("low", "real_invention", "nit"),
        ("low", "over_interpretation", "nit"),
        ("low", "methodology_artifact", "nit"),
        ("low", None, "nit"),
        # Missing fields
        (None, None, "nit"),
        ("", "", "nit"),
    ])
    def test_categorize_matrix(self, severity, evidence_type, expected):
        finding = {}
        if severity is not None:
            finding["severity"] = severity
        if evidence_type is not None:
            finding["evidence_type"] = evidence_type
        assert categorize_finding(finding) == expected


# ---------------------------------------------------------------------------
# enforce_quota: weighted allocation
# ---------------------------------------------------------------------------


class TestEnforceQuota:
    def test_empty_findings(self):
        kept, overflow = enforce_quota([])
        assert kept == []
        assert overflow == []

    def test_single_must_fix_fits(self):
        # 1 must-fix = 3 weight, fits in DEFAULT_MAX_QUOTA=8
        kept, overflow = enforce_quota([
            {"severity": "high", "evidence_type": "real_invention", "msg": "x"}
        ])
        assert len(kept) == 1
        assert overflow == []
        assert kept[0]["category"] == "must-fix"

    def test_three_must_fix_overflow(self):
        # 3 must-fix = 9 weight, exceeds quota=8 → 2 kept, 1 overflow
        findings = [
            {"severity": "high", "evidence_type": "real_invention", "msg": f"x{i}"}
            for i in range(3)
        ]
        kept, overflow = enforce_quota(findings, max_quota=8)
        assert len(kept) == 2
        assert len(overflow) == 1

    def test_eight_nit_fits(self):
        # 8 nit = 8 weight, fits exactly
        findings = [{"severity": "low", "msg": f"x{i}"} for i in range(8)]
        kept, overflow = enforce_quota(findings, max_quota=8)
        assert len(kept) == 8
        assert overflow == []

    def test_nine_nit_overflow(self):
        findings = [{"severity": "low", "msg": f"x{i}"} for i in range(9)]
        kept, overflow = enforce_quota(findings, max_quota=8)
        assert len(kept) == 8
        assert len(overflow) == 1
        assert overflow[0]["category"] == "nit"

    def test_must_fix_uses_more_quota(self):
        # 1 must-fix + 6 nit = 3 + 6 = 9 weight → 1 must-fix + 5 nit kept, 1 nit overflow
        findings = [{"severity": "high", "evidence_type": "real_invention", "msg": "must"}]
        findings += [{"severity": "low", "msg": f"nit{i}"} for i in range(6)]
        kept, overflow = enforce_quota(findings, max_quota=8)
        assert len(kept) == 6  # 1 must + 5 nit
        assert len(overflow) == 1  # 1 nit

    def test_category_annotated_on_kept(self):
        findings = [{"severity": "high", "msg": "x"}]
        kept, _ = enforce_quota(findings)
        assert kept[0]["category"] == "must-fix"

    def test_category_annotated_on_overflow(self):
        # 2 must-fix = 6 weight, fits in 8. Then add 1 must-fix = 9 weight → overflow
        findings = [
            {"severity": "high", "msg": f"x{i}"}
            for i in range(3)
        ]
        kept, overflow = enforce_quota(findings, max_quota=8)
        assert len(kept) == 2
        assert len(overflow) == 1
        assert overflow[0]["category"] == "must-fix"

    def test_order_preserved_in_kept(self):
        # Quota enforcement should NOT reorder findings (preserves input order)
        findings = [
            {"severity": "low", "msg": f"nit{i}"}
            for i in range(10)
        ]
        kept, overflow = enforce_quota(findings, max_quota=5)
        # Quota=5 means 5 nit kept. Order should be nit0..nit4.
        assert [f["msg"] for f in kept] == ["nit0", "nit1", "nit2", "nit3", "nit4"]
        assert [f["msg"] for f in overflow] == ["nit5", "nit6", "nit7", "nit8", "nit9"]

    def test_zero_quota_drops_all(self):
        # max_quota=0 → all overflow
        findings = [{"severity": "low", "msg": f"x{i}"} for i in range(3)]
        kept, overflow = enforce_quota(findings, max_quota=0)
        assert kept == []
        assert len(overflow) == 3


# ---------------------------------------------------------------------------
# auto_close_stale
# ---------------------------------------------------------------------------


class TestAutoCloseStale:
    def test_annotates_with_auto_closed(self):
        finding = {"severity": "high", "msg": "x"}
        closed = auto_close_stale([finding], commits_back=3)
        assert closed[0]["auto_closed"] is True
        assert "stale" in closed[0]["auto_close_reason"]
        assert closed[0]["commits_back"] == 3

    def test_preserves_original_fields(self):
        finding = {"severity": "high", "msg": "x", "fr_id": "FR-01"}
        closed = auto_close_stale([finding])
        assert closed[0]["msg"] == "x"
        assert closed[0]["fr_id"] == "FR-01"

    def test_empty_findings(self):
        assert auto_close_stale([]) == []

    def test_custom_commits_back(self):
        finding = {"msg": "x"}
        closed = auto_close_stale([finding], commits_back=10)
        assert closed[0]["commits_back"] == 10


# ---------------------------------------------------------------------------
# Severity weights — sanity check
# ---------------------------------------------------------------------------


class TestSeverityWeights:
    def test_must_fix_heaviest(self):
        assert SEVERITY_WEIGHT["must-fix"] > SEVERITY_WEIGHT["should-fix"]
        assert SEVERITY_WEIGHT["should-fix"] > SEVERITY_WEIGHT["nit"]

    def test_default_quota_reasonable(self):
        # Default quota should fit ~3 must-fix or ~5 should-fix or ~8 nit
        assert SEVERITY_WEIGHT["must-fix"] * 3 > DEFAULT_MAX_QUOTA
        assert SEVERITY_WEIGHT["must-fix"] * 2 <= DEFAULT_MAX_QUOTA


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


class TestDeterminism:
    def test_same_input_same_output(self):
        findings = [{"severity": "high", "msg": f"x{i}"} for i in range(10)]
        r1 = enforce_quota(findings)
        r2 = enforce_quota(findings)
        assert r1 == r2

    def test_no_llm_dependency(self):
        import core.review_quota as mod
        src_path = mod.__file__
        assert src_path is not None
        src = open(src_path).read()
        for token in ["requests", "urllib", "claude", "openai", "anthropic"]:
            assert token not in src, f"LLM/network call found: {token}"