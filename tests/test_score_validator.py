"""
Tests for score.py protocol compliance validator (R1-R6 + auto-fix).

Covers:
  validate_score_file()   — R1-R6 per-dimension checks
  _auto_fix_scores()      — R4 auto-fix, R7 warning
  _validate_all_scores()  — orchestration + ScoreProtocolError
"""
import pytest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent / "harness" / "ssi" / "scripts"))

from score import (  # pyright: ignore[reportMissingImports]
    validate_score_file,
    _auto_fix_scores,
    _validate_all_scores,
    ScoreProtocolError,
)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

def _t1_score(tmp_path, *, tool_score=80, llm_score=85, score=None,
              provider="gemini", tier=1, **extra):
    """Minimal valid Tier 1 score dict with a real tool_outputs file."""
    tool_file = tmp_path / "linting.txt"
    if not tool_file.exists():
        tool_file.write_text("no errors", encoding="utf-8")
    base = {
        "dimension": "linting",
        "round": 1,
        "llm_tier": tier,
        "llm_provider": provider,
        "tool_score": tool_score,
        "llm_score": llm_score,
        "score": score if score is not None else min(tool_score, llm_score),
        "tool_outputs": str(tool_file),
        "findings": [],
    }
    base.update(extra)
    return base


def _t3_score(tmp_path, *, tool_score=75, llm_score=80, **extra):
    """Minimal valid Tier 3 score dict."""
    tool_file = tmp_path / "architecture.txt"
    if not tool_file.exists():
        tool_file.write_text("radon output", encoding="utf-8")
    base = {
        "dimension": "architecture",
        "round": 1,
        "llm_tier": 3,
        "llm_provider": "claude_native",
        "tool_score": tool_score,
        "llm_score": llm_score,
        "score": min(tool_score, llm_score),
        "tool_outputs": str(tool_file),
        "findings": [],
    }
    base.update(extra)
    return base


# ---------------------------------------------------------------------------
# R1: required fields
# ---------------------------------------------------------------------------

class TestR1RequiredFields:
    def test_valid_passes(self, tmp_path):
        issues = validate_score_file("linting", _t1_score(tmp_path), project_root=tmp_path)
        assert issues == []

    @pytest.mark.parametrize("field", [
        "dimension", "round", "tool_score", "llm_score",
        "score", "tool_outputs", "llm_tier", "llm_provider",
    ])
    def test_missing_field_flagged(self, tmp_path, field):
        d = _t1_score(tmp_path)
        del d[field]
        issues = validate_score_file("linting", d, project_root=tmp_path)
        assert any("R1" in i and field in i for i in issues)


# ---------------------------------------------------------------------------
# R2: tool_outputs file existence
# ---------------------------------------------------------------------------

class TestR2ToolOutputs:
    def test_missing_file_flagged(self, tmp_path):
        d = _t1_score(tmp_path)
        d["tool_outputs"] = str(tmp_path / "ghost.txt")
        issues = validate_score_file("linting", d, project_root=tmp_path)
        assert any("R2" in i for i in issues)

    def test_empty_path_with_non_null_score_flagged(self, tmp_path):
        d = _t1_score(tmp_path)
        d["tool_outputs"] = ""
        issues = validate_score_file("linting", d, project_root=tmp_path)
        assert any("R2" in i for i in issues)

    def test_empty_path_with_null_score_passes(self, tmp_path):
        """tool_score=null + empty tool_outputs = tool unavailable, not an error."""
        d = _t1_score(tmp_path)
        d["tool_score"] = None
        d["score"] = d["llm_score"]
        d["tool_outputs"] = ""
        issues = validate_score_file("linting", d, project_root=tmp_path)
        assert not any("R2" in i for i in issues)

    def test_relative_path_resolved_against_project_root(self, tmp_path):
        """Relative tool_outputs resolved via project_root, not CWD."""
        tool_dir = tmp_path / ".sessi-work" / "round_1" / "tools"
        tool_dir.mkdir(parents=True)
        (tool_dir / "linting.txt").write_text("ok", encoding="utf-8")
        d = _t1_score(tmp_path)
        d["tool_outputs"] = ".sessi-work/round_1/tools/linting.txt"
        issues = validate_score_file("linting", d, project_root=tmp_path)
        assert not any("R2" in i for i in issues)


# ---------------------------------------------------------------------------
# R3: Tier 1/2 provider constraint
# ---------------------------------------------------------------------------

class TestR3ProviderConstraint:
    def test_tier1_gemini_passes(self, tmp_path):
        issues = validate_score_file("linting", _t1_score(tmp_path, provider="gemini"),
                                     project_root=tmp_path)
        assert not any("R3" in i for i in issues)

    def test_tier1_hermes_passes(self, tmp_path):
        issues = validate_score_file("linting", _t1_score(tmp_path, provider="hermes"),
                                     project_root=tmp_path)
        assert not any("R3" in i for i in issues)

    def test_tier1_claude_native_blocked(self, tmp_path):
        issues = validate_score_file("linting", _t1_score(tmp_path, provider="claude_native"),
                                     project_root=tmp_path)
        assert any("R3" in i for i in issues)

    def test_tier1_degraded_claude_native_allowed(self, tmp_path):
        """_degraded=True marks provider_chain exhausted — R3 must not fire."""
        d = _t1_score(tmp_path, provider="claude_native", _degraded=True)
        issues = validate_score_file("linting", d, project_root=tmp_path)
        assert not any("R3" in i for i in issues)

    def test_tier2_claude_native_blocked(self, tmp_path):
        d = _t1_score(tmp_path, tier=2, provider="claude_native")
        d["dimension"] = "security"
        issues = validate_score_file("security", d, project_root=tmp_path)
        assert any("R3" in i for i in issues)

    def test_tier3_claude_native_passes(self, tmp_path):
        issues = validate_score_file("architecture", _t3_score(tmp_path),
                                     project_root=tmp_path)
        assert not any("R3" in i for i in issues)


# ---------------------------------------------------------------------------
# R4: score = min(tool_score, llm_score)
# ---------------------------------------------------------------------------

class TestR4ScoreReconciliation:
    def test_correct_min_passes(self, tmp_path):
        issues = validate_score_file("linting", _t1_score(tmp_path, tool_score=70, llm_score=90),
                                     project_root=tmp_path)
        assert not any("R4" in i for i in issues)

    def test_inflated_score_flagged(self, tmp_path):
        d = _t1_score(tmp_path, tool_score=70, llm_score=90, score=90)
        issues = validate_score_file("linting", d, project_root=tmp_path)
        assert any("R4" in i for i in issues)

    def test_within_tolerance_passes(self, tmp_path):
        """Tolerance is 1.5 — score=71 for min=70 is acceptable."""
        d = _t1_score(tmp_path, tool_score=70, llm_score=90, score=71)
        issues = validate_score_file("linting", d, project_root=tmp_path)
        assert not any("R4" in i for i in issues)


# ---------------------------------------------------------------------------
# R5: findings must have evidence
# ---------------------------------------------------------------------------

class TestR5FindingEvidence:
    def test_finding_without_evidence_flagged(self, tmp_path):
        d = _t1_score(tmp_path)
        d["findings"] = [{"message": "bad line", "severity": "high", "evidence": ""}]
        issues = validate_score_file("linting", d, project_root=tmp_path)
        assert any("R5" in i for i in issues)

    def test_finding_with_evidence_passes(self, tmp_path):
        d = _t1_score(tmp_path)
        d["findings"] = [{"message": "bad line", "severity": "high",
                          "evidence": "src/foo.py:42 error"}]
        issues = validate_score_file("linting", d, project_root=tmp_path)
        assert not any("R5" in i for i in issues)

    def test_multiple_findings_all_checked(self, tmp_path):
        d = _t1_score(tmp_path)
        d["findings"] = [
            {"message": "ok", "severity": "low", "evidence": "line 1"},
            {"message": "bad", "severity": "high", "evidence": ""},  # missing
        ]
        issues = validate_score_file("linting", d, project_root=tmp_path)
        r5 = [i for i in issues if "R5" in i]
        assert len(r5) == 1 and "finding[1]" in r5[0]


# ---------------------------------------------------------------------------
# R6: Tier 3 inflation gate
# ---------------------------------------------------------------------------

class TestR6InflationGate:
    def test_tier3_score_85_without_da_flagged(self, tmp_path):
        d = _t3_score(tmp_path, tool_score=85, llm_score=85)
        issues = validate_score_file("architecture", d, project_root=tmp_path)
        assert any("R6" in i for i in issues)

    def test_tier3_score_85_with_da_challenge_passes(self, tmp_path):
        d = _t3_score(tmp_path, tool_score=85, llm_score=85, da_challenge=False)
        issues = validate_score_file("architecture", d, project_root=tmp_path)
        assert not any("R6" in i for i in issues)

    def test_tier3_score_85_with_inflation_capped_passes(self, tmp_path):
        d = _t3_score(tmp_path, tool_score=85, llm_score=85, inflation_capped=True)
        issues = validate_score_file("architecture", d, project_root=tmp_path)
        assert not any("R6" in i for i in issues)

    def test_tier3_score_below_85_no_da_needed(self, tmp_path):
        d = _t3_score(tmp_path, llm_score=84)  # below threshold
        issues = validate_score_file("architecture", d, project_root=tmp_path)
        assert not any("R6" in i for i in issues)


# ---------------------------------------------------------------------------
# _auto_fix_scores
# ---------------------------------------------------------------------------

class TestAutoFixScores:
    def test_r4_auto_fixes_inflated_score(self):
        scores = {"linting": {"tool_score": 70, "llm_score": 90, "score": 90}}
        warnings = _auto_fix_scores(scores)
        assert scores["linting"]["score"] == 70
        assert scores["linting"]["_score_autofixed"] is True
        assert any("auto-fixed" in w for w in warnings)

    def test_r4_no_fix_when_score_correct(self):
        scores = {"linting": {"tool_score": 70, "llm_score": 90, "score": 70}}
        warnings = _auto_fix_scores(scores)
        assert "_score_autofixed" not in scores["linting"]
        assert not any("auto-fixed" in w for w in warnings)

    def test_r7_warns_on_null_tool_score_without_note(self):
        scores = {"linting": {"tool_score": None, "llm_score": 80, "score": 80}}
        warnings = _auto_fix_scores(scores)
        assert any("tool_note" in w for w in warnings)

    def test_r7_no_warn_when_tool_note_present(self):
        scores = {"linting": {
            "tool_score": None, "llm_score": 80, "score": 80,
            "tool_note": "ruff not installed",
        }}
        warnings = _auto_fix_scores(scores)
        assert not any("tool_note" in w for w in warnings)


# ---------------------------------------------------------------------------
# _validate_all_scores (integration)
# ---------------------------------------------------------------------------

class TestValidateAllScores:
    def _make_scores(self, tmp_path, provider="gemini"):
        tool_file = tmp_path / "linting.txt"
        tool_file.write_text("x", encoding="utf-8")
        return {
            "linting": {
                "dimension": "linting", "round": 1,
                "llm_tier": 1, "llm_provider": provider,
                "tool_score": 80, "llm_score": 85, "score": 80,
                "tool_outputs": str(tool_file), "findings": [],
            }
        }

    def test_valid_scores_no_raise(self, tmp_path):
        _validate_all_scores(self._make_scores(tmp_path), project_root=tmp_path)

    def test_r3_violation_raises(self, tmp_path):
        with pytest.raises(ScoreProtocolError) as exc:
            _validate_all_scores(
                self._make_scores(tmp_path, provider="claude_native"),
                project_root=tmp_path,
            )
        assert "linting" in str(exc.value)
        assert "R3" in str(exc.value)

    def test_error_message_lists_all_dims(self, tmp_path):
        tool_file = tmp_path / "t.txt"
        tool_file.write_text("x", encoding="utf-8")
        scores = {
            "linting":  {"dimension": "linting",  "round": 1, "llm_tier": 1,
                         "llm_provider": "claude_native", "tool_score": 80,
                         "llm_score": 80, "score": 80,
                         "tool_outputs": str(tool_file), "findings": []},
            "security": {"dimension": "security", "round": 1, "llm_tier": 2,
                         "llm_provider": "claude_native", "tool_score": 80,
                         "llm_score": 80, "score": 80,
                         "tool_outputs": str(tool_file), "findings": []},
        }
        with pytest.raises(ScoreProtocolError) as exc:
            _validate_all_scores(scores, project_root=tmp_path)
        msg = str(exc.value)
        assert "linting" in msg and "security" in msg
