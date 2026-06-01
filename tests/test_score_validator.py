"""
Tests for score.py protocol compliance validator (R1, R2, R4, R5, R8 + auto-fix).

LLM scoring was removed:  score = tool_score for all tiers.
Removed rules: R3 (provider constraint), R6 (Tier 3 inflation gate), R8b (deviation warning).
R8 now applies to ALL tiers — tool_score=null is never permitted.

Covers:
  validate_score_file()   — R1, R2, R4, R5, R8 per-dimension checks
  _auto_fix_scores()      — R4 auto-fix (score = tool_score), R7 warning
  _validate_all_scores()  — orchestration + ScoreProtocolError
"""
import pytest
pytestmark = pytest.mark.mutation_oracle

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

def _score(tmp_path, *, dim="linting", tool_score=80, score=None, **extra):
    """Minimal valid score dict with a real tool_outputs file.

    score defaults to tool_score (pure-tool scoring contract).
    Pass score= explicitly to test R4 violations.
    """
    tool_file = tmp_path / f"{dim}.txt"
    if not tool_file.exists():
        tool_file.write_text("tool output", encoding="utf-8")
    base = {
        "dimension": dim,
        "round": 1,
        "tool_score": tool_score,
        "score": score if score is not None else tool_score,
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
        issues = validate_score_file("linting", _score(tmp_path), project_root=tmp_path)
        assert issues == []

    @pytest.mark.parametrize("field", [
        "dimension", "round", "tool_score", "score", "tool_outputs",
    ])
    def test_missing_field_flagged(self, tmp_path, field):
        d = _score(tmp_path)
        del d[field]
        issues = validate_score_file("linting", d, project_root=tmp_path)
        assert any("R1" in i and field in i for i in issues)

    @pytest.mark.parametrize("optional_field", [
        "llm_score", "llm_tier", "llm_provider",
    ])
    def test_llm_fields_optional(self, tmp_path, optional_field):
        """llm_score / llm_tier / llm_provider are now optional annotations — R1 must not fire."""
        d = _score(tmp_path)
        # These fields are not present by default — confirm R1 does not fire for them.
        assert optional_field not in d
        issues = validate_score_file("linting", d, project_root=tmp_path)
        assert not any(optional_field in i for i in issues)


# ---------------------------------------------------------------------------
# R2: tool_outputs file existence
# ---------------------------------------------------------------------------

class TestR2ToolOutputs:
    def test_missing_file_flagged(self, tmp_path):
        d = _score(tmp_path)
        d["tool_outputs"] = str(tmp_path / "ghost.txt")
        issues = validate_score_file("linting", d, project_root=tmp_path)
        assert any("R2" in i for i in issues)

    def test_empty_path_with_non_null_score_flagged(self, tmp_path):
        d = _score(tmp_path)
        d["tool_outputs"] = ""
        issues = validate_score_file("linting", d, project_root=tmp_path)
        assert any("R2" in i for i in issues)

    def test_empty_path_with_null_score_triggers_r8(self, tmp_path):
        """tool_score=null triggers R8 (all tiers); R2 stays silent."""
        d = _score(tmp_path)
        d["tool_score"] = None
        d["score"] = 80
        d["tool_outputs"] = ""
        issues = validate_score_file("linting", d, project_root=tmp_path)
        assert not any("R2" in i for i in issues)
        assert any("R8" in i for i in issues)

    def test_relative_path_resolved_against_project_root(self, tmp_path):
        """Relative tool_outputs resolved via project_root, not CWD."""
        tool_dir = tmp_path / ".sessi-work" / "round_1" / "tools"
        tool_dir.mkdir(parents=True)
        (tool_dir / "linting.txt").write_text("ok", encoding="utf-8")
        d = _score(tmp_path)
        d["tool_outputs"] = ".sessi-work/round_1/tools/linting.txt"
        issues = validate_score_file("linting", d, project_root=tmp_path)
        assert not any("R2" in i for i in issues)

    def test_relative_path_with_project_root_none_uses_cwd(self, tmp_path, monkeypatch):
        """project_root=None → relative path resolved against CWD (fallback branch)."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "linting_rel.txt").write_text("output", encoding="utf-8")
        d = _score(tmp_path)
        d["tool_outputs"] = "linting_rel.txt"
        issues = validate_score_file("linting", d, project_root=None)
        assert not any("R2" in i for i in issues)

    def test_relative_path_with_project_root_none_missing_file(self, tmp_path, monkeypatch):
        """project_root=None + non-existent relative path → R2 fires via CWD resolution."""
        monkeypatch.chdir(tmp_path)
        d = _score(tmp_path)
        d["tool_outputs"] = "ghost_relative.txt"
        issues = validate_score_file("linting", d, project_root=None)
        assert any("R2" in i for i in issues)


# ---------------------------------------------------------------------------
# R4: score must equal tool_score
# ---------------------------------------------------------------------------

class TestR4ScoreEqualsToolScore:
    def test_score_equals_tool_score_passes(self, tmp_path):
        """score == tool_score → R4 does not fire."""
        issues = validate_score_file("linting", _score(tmp_path, tool_score=70),
                                     project_root=tmp_path)
        assert not any("R4" in i for i in issues)

    def test_score_above_tool_score_flagged(self, tmp_path):
        """score > tool_score is not permitted — LLM cannot inflate."""
        d = _score(tmp_path, tool_score=70, score=90)
        issues = validate_score_file("linting", d, project_root=tmp_path)
        assert any("R4" in i for i in issues)

    def test_score_below_tool_score_flagged(self, tmp_path):
        """score < tool_score is also rejected — score must equal tool_score exactly."""
        d = _score(tmp_path, tool_score=80, score=60)
        issues = validate_score_file("linting", d, project_root=tmp_path)
        assert any("R4" in i for i in issues)

    def test_within_tolerance_passes(self, tmp_path):
        """Tolerance is 1.5 — score=71.0 for tool_score=70 is acceptable."""
        d = _score(tmp_path, tool_score=70, score=71)
        issues = validate_score_file("linting", d, project_root=tmp_path)
        assert not any("R4" in i for i in issues)

    def test_tier3_score_equals_tool_score_passes(self, tmp_path):
        """Tier 3 obeys the same rule: score = tool_score."""
        issues = validate_score_file("architecture", _score(tmp_path, dim="architecture",
                                                             tool_score=82),
                                     project_root=tmp_path)
        assert not any("R4" in i for i in issues)

    def test_tier3_score_above_tool_score_flagged(self, tmp_path):
        """Tier 3 with score > tool_score is blocked by R4."""
        d = _score(tmp_path, dim="architecture", tool_score=70, score=88)
        issues = validate_score_file("architecture", d, project_root=tmp_path)
        assert any("R4" in i for i in issues)


# ---------------------------------------------------------------------------
# R5: findings must have evidence
# ---------------------------------------------------------------------------

class TestR5FindingEvidence:
    def test_finding_without_evidence_flagged(self, tmp_path):
        d = _score(tmp_path)
        d["findings"] = [{"message": "bad line", "severity": "high", "evidence": ""}]
        issues = validate_score_file("linting", d, project_root=tmp_path)
        assert any("R5" in i for i in issues)

    def test_finding_with_evidence_passes(self, tmp_path):
        d = _score(tmp_path)
        d["findings"] = [{"message": "bad line", "severity": "high",
                          "evidence": "src/foo.py:42 error"}]
        issues = validate_score_file("linting", d, project_root=tmp_path)
        assert not any("R5" in i for i in issues)

    def test_multiple_findings_all_checked(self, tmp_path):
        d = _score(tmp_path)
        d["findings"] = [
            {"message": "ok", "severity": "low", "evidence": "line 1"},
            {"message": "bad", "severity": "high", "evidence": ""},  # missing
        ]
        issues = validate_score_file("linting", d, project_root=tmp_path)
        r5 = [i for i in issues if "R5" in i]
        assert len(r5) == 1 and "finding[1]" in r5[0]


# ---------------------------------------------------------------------------
# R8: tool_score must not be null for ANY dimension (all tiers)
# ---------------------------------------------------------------------------

class TestR8NullToolScore:
    def test_tier1_null_tool_score_blocked(self, tmp_path):
        d = _score(tmp_path)
        d["tool_score"] = None
        d["score"] = 80
        issues = validate_score_file("linting", d, project_root=tmp_path)
        assert any("R8" in i for i in issues)

    def test_tier2_null_tool_score_blocked(self, tmp_path):
        d = _score(tmp_path, dim="security")
        d["tool_score"] = None
        d["score"] = 80
        issues = validate_score_file("security", d, project_root=tmp_path)
        assert any("R8" in i for i in issues)

    def test_tier3_null_tool_score_blocked(self, tmp_path):
        """Tier 3 with tool_score=null is NOW blocked — no LLM fallback for scoring."""
        d = _score(tmp_path, dim="architecture")
        d["tool_score"] = None
        d["score"] = 80
        issues = validate_score_file("architecture", d, project_root=tmp_path)
        assert any("R8" in i for i in issues)

    def test_valid_tool_score_no_r8(self, tmp_path):
        issues = validate_score_file("linting", _score(tmp_path), project_root=tmp_path)
        assert not any("R8" in i for i in issues)

    def test_r8_fires_in_validate_all_scores(self, tmp_path):
        """R8 propagates through _validate_all_scores → ScoreProtocolError."""
        tool_file = tmp_path / "linting.txt"
        tool_file.write_text("x", encoding="utf-8")
        scores = {
            "linting": {
                "dimension": "linting", "round": 1,
                "tool_score": None, "score": 80,
                "tool_outputs": str(tool_file), "findings": [],
            }
        }
        with pytest.raises(ScoreProtocolError) as exc:
            _validate_all_scores(scores, project_root=tmp_path)
        assert "R8" in str(exc.value)


# ---------------------------------------------------------------------------
# _auto_fix_scores
# ---------------------------------------------------------------------------

class TestAutoFixScores:
    def test_r4_auto_fixes_score_above_tool_score(self):
        """score > tool_score is auto-fixed to tool_score."""
        scores = {"linting": {"tool_score": 70, "score": 90}}
        warnings = _auto_fix_scores(scores)
        assert scores["linting"]["score"] == 70
        assert scores["linting"]["_score_autofixed"] is True
        assert any("auto-fixed" in w for w in warnings)

    def test_r4_auto_fixes_score_below_tool_score(self):
        """score < tool_score is also auto-fixed to tool_score."""
        scores = {"linting": {"tool_score": 80, "score": 60}}
        _auto_fix_scores(scores)
        assert scores["linting"]["score"] == 80
        assert scores["linting"]["_score_autofixed"] is True

    def test_r4_no_fix_when_score_correct(self):
        scores = {"linting": {"tool_score": 70, "score": 70}}
        warnings = _auto_fix_scores(scores)
        assert "_score_autofixed" not in scores["linting"]
        assert not any("auto-fixed" in w for w in warnings)

    def test_r7_warns_on_null_tool_score_without_note(self):
        """R7 fires as a warning for null tool_score without explanation."""
        scores = {"architecture": {"tool_score": None, "score": 80}}
        warnings = _auto_fix_scores(scores)
        assert any("tool_note" in w for w in warnings)

    def test_r7_no_warn_when_tool_note_present(self):
        scores = {"architecture": {
            "tool_score": None, "score": 80,
            "tool_note": "radon not installed",
        }}
        warnings = _auto_fix_scores(scores)
        assert not any("tool_note" in w for w in warnings)


# ---------------------------------------------------------------------------
# _validate_all_scores (integration)
# ---------------------------------------------------------------------------

class TestValidateAllScores:
    def _make_scores(self, tmp_path):
        tool_file = tmp_path / "linting.txt"
        tool_file.write_text("x", encoding="utf-8")
        return {
            "linting": {
                "dimension": "linting", "round": 1,
                "tool_score": 80, "score": 80,
                "tool_outputs": str(tool_file), "findings": [],
            }
        }

    def test_valid_scores_no_raise(self, tmp_path):
        _validate_all_scores(self._make_scores(tmp_path), project_root=tmp_path)

    def test_r8_violation_raises(self, tmp_path):
        """R8 (null tool_score) propagates via _validate_all_scores."""
        scores = self._make_scores(tmp_path)
        scores["linting"]["tool_score"] = None
        scores["linting"]["score"] = 80
        with pytest.raises(ScoreProtocolError) as exc:
            _validate_all_scores(scores, project_root=tmp_path)
        assert "linting" in str(exc.value)
        assert "R8" in str(exc.value)

    def test_error_message_lists_all_failing_dims(self, tmp_path):
        """ScoreProtocolError names every failing dimension."""
        tool_file = tmp_path / "t.txt"
        tool_file.write_text("x", encoding="utf-8")
        scores = {
            "linting":  {"dimension": "linting",  "round": 1,
                         "tool_score": None, "score": 80,
                         "tool_outputs": str(tool_file), "findings": []},
            "security": {"dimension": "security", "round": 1,
                         "tool_score": None, "score": 80,
                         "tool_outputs": str(tool_file), "findings": []},
        }
        with pytest.raises(ScoreProtocolError) as exc:
            _validate_all_scores(scores, project_root=tmp_path)
        msg = str(exc.value)
        assert "linting" in msg and "security" in msg
