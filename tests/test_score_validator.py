"""
Tests for score.py protocol compliance validator (R1-R8 + auto-fix).

Covers:
  validate_score_file()   — R1-R8 per-dimension checks
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

    def test_empty_path_with_null_score_tier1_triggers_r8(self, tmp_path):
        """Tier 1 tool_score=null → R8 fires (not a valid state for Tier 1/2)."""
        d = _t1_score(tmp_path)
        d["tool_score"] = None
        d["score"] = d["llm_score"]
        d["tool_outputs"] = ""
        issues = validate_score_file("linting", d, project_root=tmp_path)
        assert not any("R2" in i for i in issues)  # R2 silent — R8 is the relevant block
        assert any("R8" in i for i in issues)

    def test_empty_path_with_null_score_tier3_passes(self, tmp_path):
        """Tier 3 tool_score=null + empty tool_outputs = helper tool absent, not an error."""
        d = _t3_score(tmp_path)
        d["tool_score"] = None
        d["score"] = d["llm_score"]
        d["tool_outputs"] = ""
        issues = validate_score_file("architecture", d, project_root=tmp_path)
        assert not any("R2" in i for i in issues)
        assert not any("R8" in i for i in issues)

    def test_relative_path_resolved_against_project_root(self, tmp_path):
        """Relative tool_outputs resolved via project_root, not CWD."""
        tool_dir = tmp_path / ".sessi-work" / "round_1" / "tools"
        tool_dir.mkdir(parents=True)
        (tool_dir / "linting.txt").write_text("ok", encoding="utf-8")
        d = _t1_score(tmp_path)
        d["tool_outputs"] = ".sessi-work/round_1/tools/linting.txt"
        issues = validate_score_file("linting", d, project_root=tmp_path)
        assert not any("R2" in i for i in issues)

    def test_relative_path_with_project_root_none_uses_cwd(self, tmp_path, monkeypatch):
        """project_root=None → relative path resolved against CWD (fallback branch).

        Creates the tool file relative to tmp_path, sets CWD to tmp_path,
        then passes a relative path to exercise the CWD fallback.
        All other R2 tests use absolute paths (str(tool_file) from pytest tmp_path),
        so this test is the only one that exercises the project_root=None branch.
        """
        monkeypatch.chdir(tmp_path)
        (tmp_path / "linting_rel.txt").write_text("output", encoding="utf-8")
        d = _t1_score(tmp_path)
        d["tool_outputs"] = "linting_rel.txt"   # relative, resolvable from CWD
        issues = validate_score_file("linting", d, project_root=None)
        assert not any("R2" in i for i in issues)

    def test_relative_path_with_project_root_none_missing_file(self, tmp_path, monkeypatch):
        """project_root=None + non-existent relative path → R2 fires via CWD resolution."""
        monkeypatch.chdir(tmp_path)
        d = _t1_score(tmp_path)
        d["tool_outputs"] = "ghost_relative.txt"   # does not exist in tmp_path/CWD
        issues = validate_score_file("linting", d, project_root=None)
        assert any("R2" in i for i in issues)


# ---------------------------------------------------------------------------
# R3: Tier 1/2 provider constraint
# ---------------------------------------------------------------------------

class TestR3ProviderConstraint:
    def test_tier1_gemini_passes(self, tmp_path):
        issues = validate_score_file("linting", _t1_score(tmp_path, provider="gemini"),
                                     project_root=tmp_path)
        assert not any("R3" in i for i in issues)

    def test_tier1_gemini_flash_passes(self, tmp_path):
        """'gemini-flash' is a valid gemini-family variant — must not trigger R3."""
        issues = validate_score_file("linting", _t1_score(tmp_path, provider="gemini-flash"),
                                     project_root=tmp_path)
        assert not any("R3" in i for i in issues)

    def test_tier1_gemini_25_flash_passes(self, tmp_path):
        """'gemini-2.5-flash' variant also accepted."""
        issues = validate_score_file("linting", _t1_score(tmp_path, provider="gemini-2.5-flash"),
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
        r6 = [i for i in issues if "R6" in i]
        assert r6
        # Error message should tell agent exactly what to add
        assert "da_challenge" in r6[0] and "inflation_capped" in r6[0]

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
# R8: Tier 1/2 tool_score must not be null
# ---------------------------------------------------------------------------

class TestR8NullToolScore:
    def test_tier1_null_tool_score_blocked(self, tmp_path):
        """Tier 1 with tool_score=null triggers R8 — LLM self-eval not permitted."""
        d = _t1_score(tmp_path)
        d["tool_score"] = None
        d["score"] = d["llm_score"]
        issues = validate_score_file("linting", d, project_root=tmp_path)
        assert any("R8" in i for i in issues)

    def test_tier2_null_tool_score_blocked(self, tmp_path):
        """Tier 2 with tool_score=null also triggers R8."""
        d = _t1_score(tmp_path, tier=2, provider="gemini")
        d["dimension"] = "security"
        d["tool_score"] = None
        d["score"] = d["llm_score"]
        issues = validate_score_file("security", d, project_root=tmp_path)
        assert any("R8" in i for i in issues)

    def test_tier3_null_tool_score_allowed(self, tmp_path):
        """Tier 3 with tool_score=null is permitted (helper tool optional)."""
        d = _t3_score(tmp_path)
        d["tool_score"] = None
        d["score"] = d["llm_score"]
        issues = validate_score_file("architecture", d, project_root=tmp_path)
        assert not any("R8" in i for i in issues)

    def test_tier1_with_tool_score_no_r8(self, tmp_path):
        """Tier 1 with valid tool_score — R8 must not fire."""
        issues = validate_score_file("linting", _t1_score(tmp_path), project_root=tmp_path)
        assert not any("R8" in i for i in issues)

    def test_r8_fires_in_validate_all_scores(self, tmp_path):
        """R8 propagates through _validate_all_scores → ScoreProtocolError."""
        tool_file = tmp_path / "linting.txt"
        tool_file.write_text("x", encoding="utf-8")
        scores = {
            "linting": {
                "dimension": "linting", "round": 1,
                "llm_tier": 1, "llm_provider": "gemini",
                "tool_score": None, "llm_score": 80, "score": 80,
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
        """R7 fires as a warning for Tier 3 null tool_score (helper tool absent, no note)."""
        scores = {"architecture": {"tool_score": None, "llm_score": 80, "score": 80}}
        warnings = _auto_fix_scores(scores)
        assert any("tool_note" in w for w in warnings)

    def test_r7_no_warn_when_tool_note_present(self):
        """R7 is silent when tool_note is provided (Tier 3 helper tool absent)."""
        scores = {"architecture": {
            "tool_score": None, "llm_score": 80, "score": 80,
            "tool_note": "radon not installed",
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
