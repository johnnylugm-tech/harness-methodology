"""Oracle tests for score.py compute_overall_score().

Gap: test_score_validator.py covers validate_score_file rules (R1-R8).
     compute_overall_score() — the weighted-average gate logic — had ZERO tests.

Design rule: expected values are HARD-CODED literals; never derive from the
function's own constants (mutations to those constants make the test trivial).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "harness" / "ssi" / "scripts"))

from score import compute_overall_score, validate_score_file  # noqa: E402


# ─── helpers ─────────────────────────────────────────────────────────────────

def _cfg(dims, gate=85):
    """Build minimal config dict.  dims = [(name, weight, target), ...]"""
    return {
        "quality": {"score_gate": gate},
        "dimensions": {
            n: {"enabled": True, "weight": w, "target": t}
            for n, w, t in dims
        },
    }


def _sc(**kwargs):
    """Build scores dict.  kwargs: dim_name=score_value."""
    return {
        k: {"score": v, "tool_score": v, "dimension": k, "round": 1}
        for k, v in kwargs.items()
    }


# ─── weighted average ─────────────────────────────────────────────────────────

def test_overall_score_weighted_average():
    """(80×0.6 + 60×0.4) / 1.0 = 72.0.  Kills weighted_sum/weight_sum mutations."""
    cfg = _cfg([("linting", 0.6, 100), ("security", 0.4, 100)])
    result = compute_overall_score(_sc(linting=80, security=60), cfg)
    assert result["overall_score"] == 72.0


def test_overall_score_single_dim():
    """Single dim: overall = score exactly.  Kills normalization mutations."""
    cfg = _cfg([("linting", 0.5, 100)])
    result = compute_overall_score(_sc(linting=84), cfg)
    assert result["overall_score"] == 84.0


def test_overall_score_rounding():
    """66.666... rounds to 66.67, not truncated or floor'd.  Kills round(2) mutation."""
    # weights 1/3 and 2/3, scores 100 and 50:
    # (100×⅓ + 50×⅔) / 1.0 = 66.666... → round(2) = 66.67
    cfg = _cfg([("a", 1 / 3, 100), ("b", 2 / 3, 100)])
    result = compute_overall_score(_sc(a=100, b=50), cfg)
    assert result["overall_score"] == 66.67


def test_overall_score_exposed_in_result():
    """overall_score key exists in return dict."""
    cfg = _cfg([("linting", 1.0, 100)])
    result = compute_overall_score(_sc(linting=75), cfg)
    assert "overall_score" in result
    assert result["overall_score"] == 75.0


# ─── score gate ───────────────────────────────────────────────────────────────

def test_meets_target_true_above_gate():
    """overall=72 >= gate=70 → meets_target=True.  Kills >= → > mutation."""
    cfg = _cfg([("linting", 1.0, 100)], gate=70)
    result = compute_overall_score(_sc(linting=72), cfg)
    assert result["meets_target"] is True


def test_meets_target_false_below_gate():
    """overall=72 < gate=75 → meets_target=False."""
    cfg = _cfg([("linting", 1.0, 100)], gate=75)
    result = compute_overall_score(_sc(linting=72), cfg)
    assert result["meets_target"] is False


def test_meets_target_at_exact_gate():
    """overall=70 == gate=70 → True (>= not >).  Kills > vs >= mutation."""
    cfg = _cfg([("linting", 1.0, 100)], gate=70)
    result = compute_overall_score(_sc(linting=70), cfg)
    assert result["meets_target"] is True


def test_score_gate_default_is_85():
    """No score_gate or target in quality → default=85.  Kills 85 literal."""
    cfg = {
        "quality": {},
        "dimensions": {"linting": {"enabled": True, "weight": 1.0, "target": 100}},
    }
    result = compute_overall_score(_sc(linting=90), cfg)
    assert result["score_gate"] == 85


def test_score_gate_from_target_alias():
    """Legacy 'target' key in quality cfg when 'score_gate' absent.  Kills fallback."""
    cfg = {
        "quality": {"target": 70},
        "dimensions": {"linting": {"enabled": True, "weight": 1.0, "target": 100}},
    }
    result = compute_overall_score(_sc(linting=90), cfg)
    assert result["score_gate"] == 70


# ─── quality_complete ─────────────────────────────────────────────────────────

def test_quality_complete_true_when_gate_met_no_issues():
    """meets_gate=True, registry=None (no open issues) → quality_complete=True."""
    cfg = _cfg([("linting", 1.0, 100)], gate=70)
    result = compute_overall_score(_sc(linting=80), cfg)
    assert result["quality_complete"] is True


def test_quality_complete_false_when_below_gate():
    """meets_gate=False → quality_complete=False.  Kills 'and' short-circuit mutation."""
    cfg = _cfg([("linting", 1.0, 100)], gate=90)
    result = compute_overall_score(_sc(linting=80), cfg)
    assert result["quality_complete"] is False


# ─── failing_dimensions ───────────────────────────────────────────────────────

def test_failing_dim_gap_and_impact():
    """score=60, target=80, weight=0.3 → gap=20, impact=6.0.
    Kills dim_target-score subtraction and gap×weight multiplication."""
    cfg = _cfg([("linting", 0.3, 80)])
    result = compute_overall_score(_sc(linting=60), cfg)
    fd = result["failing_dimensions"]
    assert len(fd) == 1
    assert fd[0]["gap"] == 20       # 80 − 60 = 20
    assert fd[0]["impact"] == 6.0   # 20 × 0.3 = 6.0


def test_failing_dims_sorted_by_impact_descending():
    """Higher-impact dim appears first.  Kills reverse=True → reverse=False mutation.

    dim_a: gap=30, weight=0.5 → impact=15.0  (HIGHER, first)
    dim_b: gap=50, weight=0.2 → impact=10.0  (lower, second)
    """
    cfg = _cfg([("dim_a", 0.5, 100), ("dim_b", 0.2, 100)])
    result = compute_overall_score(_sc(dim_a=70, dim_b=50), cfg)
    fd = result["failing_dimensions"]
    assert len(fd) == 2
    assert fd[0]["dimension"] == "dim_a"
    assert fd[0]["impact"] == 15.0   # 30 × 0.5
    assert fd[1]["dimension"] == "dim_b"
    assert fd[1]["impact"] == 10.0   # 50 × 0.2


def test_no_failing_dims_when_all_pass():
    """score=100 >= target=80 → gap=0 → failing_dimensions is empty."""
    cfg = _cfg([("linting", 1.0, 80)])
    result = compute_overall_score(_sc(linting=100), cfg)
    assert result["failing_dimensions"] == []


def test_gap_floored_at_zero_when_score_exceeds_target():
    """score=90 > target=80 → gap=max(0, -10)=0.  Kills max(0,…) mutation."""
    cfg = _cfg([("linting", 1.0, 80)])
    result = compute_overall_score(_sc(linting=90), cfg)
    assert result["breakdown"]["linting"]["gap"] == 0


# ─── disabled dimensions ──────────────────────────────────────────────────────

def test_disabled_dim_excluded_from_breakdown():
    """enabled=False dim not in breakdown; its score=0 must not drag down overall.
    If mutation removes the 'continue', code raises ValueError (missing from scores)
    → test errors → mutation killed."""
    cfg = {
        "quality": {"score_gate": 85},
        "dimensions": {
            "linting":  {"enabled": True,  "weight": 1.0, "target": 100},
            "disabled": {"enabled": False, "weight": 1.0, "target": 100},
        },
    }
    result = compute_overall_score(
        {"linting": {"score": 90, "tool_score": 90, "dimension": "linting", "round": 1}},
        cfg,
    )
    assert "disabled" not in result["breakdown"]
    assert result["overall_score"] == 90.0   # disabled dim's score=0 must not pollute


# ─── R4 tolerance boundary (complement to test_score_validator.py) ────────────

def test_r4_tolerance_exact_boundary_passes(tmp_path):
    """diff=1.5 exactly → passes (> 1.5, not >=).  Kills > → >= mutation."""
    f = tmp_path / "t.txt"
    f.write_text("x", encoding="utf-8")
    d = {
        "dimension": "linting", "round": 1,
        "tool_score": 70.0, "score": 71.5,
        "tool_outputs": str(f), "findings": [],
    }
    issues = validate_score_file("linting", d, project_root=tmp_path)
    assert not any("R4" in i for i in issues)


def test_r4_just_above_tolerance_flagged(tmp_path):
    """diff=1.6 > 1.5 → R4 fires.  Confirms > threshold is 1.5, not 1.6."""
    f = tmp_path / "t.txt"
    f.write_text("x", encoding="utf-8")
    d = {
        "dimension": "linting", "round": 1,
        "tool_score": 70.0, "score": 71.6,
        "tool_outputs": str(f), "findings": [],
    }
    issues = validate_score_file("linting", d, project_root=tmp_path)
    assert any("R4" in i for i in issues)


# ─── return dict key coverage ─────────────────────────────────────────────────
# Accessing each return key kills KEY-name mutations ("open_critical_count"→"XX…XX")

def test_result_return_dict_all_keys_present():
    """All 12 return keys must exist.  Kills key-name mutations in return dict."""
    cfg = _cfg([("linting", 1.0, 100)], gate=70)
    result = compute_overall_score(_sc(linting=80), cfg)
    for key in (
        "overall_score", "score_gate", "target", "meets_target",
        "quality_complete", "open_critical_count", "open_high_count",
        "open_medium_count", "open_total", "failing_dimensions",
        "breakdown", "crg_adjustments",
    ):
        assert key in result, f"return dict missing key '{key}'"


def test_result_open_counts_zero_without_registry():
    """registry=None → all open_* counts are 0."""
    cfg = _cfg([("linting", 1.0, 100)], gate=70)
    result = compute_overall_score(_sc(linting=80), cfg)
    assert result["open_critical_count"] == 0
    assert result["open_high_count"] == 0
    assert result["open_medium_count"] == 0
    assert result["open_total"] == 0


def test_result_target_legacy_alias_equals_score_gate():
    """'target' key is a legacy alias for score_gate value."""
    cfg = _cfg([("linting", 1.0, 100)], gate=75)
    result = compute_overall_score(_sc(linting=80), cfg)
    assert result["target"] == 75
    assert result["target"] == result["score_gate"]


def test_breakdown_contains_weighted_score():
    """breakdown[dim] has 'weighted_score' key with correct value.
    Kills 'weighted_score' key mutation and score*weight arithmetic."""
    cfg = _cfg([("linting", 0.6, 100)])
    result = compute_overall_score(_sc(linting=80), cfg)
    wb = result["breakdown"]["linting"]
    assert "weighted_score" in wb
    assert wb["weighted_score"] == 48.0    # 80 × 0.6


def test_breakdown_target_matches_config():
    """breakdown[dim]['target'] reflects config target.  Kills 'target' key mutation."""
    cfg = _cfg([("linting", 1.0, 80)])
    result = compute_overall_score(_sc(linting=60), cfg)
    assert result["breakdown"]["linting"]["target"] == 80


# ─── _apply_crg_subscores ─────────────────────────────────────────────────────
# 56 survivors at L250-299 — entire function was untested.

import json as _json  # noqa: E402
import pytest as _pytest  # noqa: E402

from score import _apply_crg_subscores, _auto_fix_scores, _resolve_tool_outputs, load_scores  # noqa: E402


def test_crg_subscores_no_crg_no_structural_returns_empty():
    """crg_metrics=None + no structural dims → returns {}.
    Kills mutations in the early-return path."""
    result = _apply_crg_subscores({"linting": {"score": 80}}, None)
    assert result == {}


def test_crg_subscores_no_crg_with_architecture_raises():
    """crg_metrics=None + 'architecture' in scores → RuntimeError.
    Kills 'architecture' string mutation in the guard condition."""
    with _pytest.raises(RuntimeError, match="CRG metrics required"):
        _apply_crg_subscores({"architecture": {"score": 80}}, None)


def test_crg_subscores_no_crg_with_error_handling_raises():
    """crg_metrics=None + 'error_handling' in scores → RuntimeError.
    Kills 'error_handling' string mutation and 'or' → 'and' mutation."""
    with _pytest.raises(RuntimeError, match="CRG metrics required"):
        _apply_crg_subscores({"error_handling": {"score": 80}}, None)


def test_crg_subscores_architecture_score_overwritten():
    """crg_metrics.community_cohesion.score=85 → scores['architecture']['score']=85.
    Kills the assignment and the 'community_cohesion' key string."""
    scores = {"architecture": {"score": 50, "tool_score": 50}}
    crg = {"community_cohesion": {"score": 85}}
    _apply_crg_subscores(scores, crg)
    assert scores["architecture"]["score"] == 85           # overwritten
    assert scores["architecture"]["scorer"] == "crg"       # labelled
    assert scores["architecture"]["crg_cohesion_score"] == 85


def test_crg_subscores_error_handling_score_overwritten():
    """crg_metrics.flow_coverage.score=70 → scores['error_handling']['score']=70.
    Kills 'flow_coverage' key string and error_handling assignment."""
    scores = {"error_handling": {"score": 40, "tool_score": 40}}
    crg = {"flow_coverage": {"score": 70}}
    _apply_crg_subscores(scores, crg)
    assert scores["error_handling"]["score"] == 70
    assert scores["error_handling"]["scorer"] == "crg"
    assert scores["error_handling"]["crg_flow_score"] == 70


def test_crg_subscores_adjustments_dict():
    """Returned adjustments dict has correct structure.
    Kills 'crg_community_cohesion' source string and adjustments key mutations."""
    scores = {"architecture": {"score": 50, "tool_score": 50}}
    crg = {"community_cohesion": {"score": 90}}
    adj = _apply_crg_subscores(scores, crg)
    assert "architecture" in adj
    assert adj["architecture"]["score"] == 90
    assert adj["architecture"]["source"] == "crg_community_cohesion"


def test_crg_subscores_missing_cohesion_raises():
    """crg_metrics truthy but community_cohesion score=None → RuntimeError for architecture.
    Kills 'is None' check inversion."""
    scores = {"architecture": {"score": 50, "tool_score": 50}}
    # crg must be truthy so we pass the 'if not crg_metrics' guard, but cohesion=None
    crg = {"community_cohesion": {"score": None}}
    with _pytest.raises(RuntimeError, match="community_cohesion"):
        _apply_crg_subscores(scores, crg)


def test_crg_subscores_missing_flow_raises():
    """crg_metrics truthy but flow_coverage score=None → RuntimeError for error_handling."""
    scores = {"error_handling": {"score": 50, "tool_score": 50}}
    crg = {"flow_coverage": {"score": None}}
    with _pytest.raises(RuntimeError, match="flow_coverage"):
        _apply_crg_subscores(scores, crg)


def test_crg_subscores_both_dims_updated():
    """Both architecture and error_handling updated in one call."""
    scores = {
        "architecture":    {"score": 50, "tool_score": 50},
        "error_handling":  {"score": 40, "tool_score": 40},
    }
    crg = {
        "community_cohesion": {"score": 88},
        "flow_coverage":      {"score": 72},
    }
    adj = _apply_crg_subscores(scores, crg)
    assert scores["architecture"]["score"] == 88
    assert scores["error_handling"]["score"] == 72
    assert "architecture" in adj and "error_handling" in adj


def test_compute_overall_score_with_crg_metrics():
    """crg_adjustments appears in compute_overall_score result.
    Kills 'crg_adjustments' return-dict key mutation."""
    cfg = {
        "quality": {"score_gate": 70},
        "dimensions": {
            "architecture": {"enabled": True, "weight": 1.0, "target": 80},
        },
    }
    scores = {"architecture": {"score": 60, "tool_score": 60, "dimension": "architecture", "round": 1}}
    crg = {"community_cohesion": {"score": 85}}
    result = compute_overall_score(scores, cfg, crg_metrics=crg)
    assert result["overall_score"] == 85.0   # CRG overwrote 60 → 85
    assert "crg_adjustments" in result
    assert result["crg_adjustments"]["architecture"]["score"] == 85


# ─── _auto_fix_scores boundary (L158: abs(sc-ts) > 1.5) ─────────────────────

def test_auto_fix_triggered_just_above_tolerance():
    """diff=1.6 > 1.5 → auto-fixed.  Kills 1.5→2.5 literal mutation in _auto_fix_scores."""
    scores = {"linting": {"tool_score": 70.0, "score": 71.6}}
    warnings = _auto_fix_scores(scores)
    assert scores["linting"]["score"] == 70.0            # auto-fixed to tool_score
    assert scores["linting"].get("_score_autofixed") is True
    assert any("auto-fixed" in w for w in warnings)


def test_auto_fix_not_triggered_at_exact_tolerance():
    """diff=1.5 not > 1.5 → no auto-fix.  Kills > → >= mutation in _auto_fix_scores."""
    scores = {"linting": {"tool_score": 70.0, "score": 71.5}}
    _auto_fix_scores(scores)
    assert "_score_autofixed" not in scores["linting"]   # must NOT be fixed


# ─── _resolve_tool_outputs (L52) ─────────────────────────────────────────────

def test_resolve_tool_outputs_list_picks_first_truthy():
    """list input → first truthy element.  Kills isinstance+next mutations."""
    assert _resolve_tool_outputs(["/a/b.txt"]) == "/a/b.txt"
    assert _resolve_tool_outputs(["", "/real.txt"]) == "/real.txt"


def test_resolve_tool_outputs_empty_list_returns_empty():
    """Empty list → ''.  Kills the default '' in next(…, '')."""
    assert _resolve_tool_outputs([]) == ""


# ─── compute_overall_score: dim_config.get("target", 100) default ────────────

def test_target_defaults_to_100_when_missing_from_config():
    """dim config without 'target' key → default 100.
    Kills get('target', 100) literal and related breakdown mutations."""
    cfg = {
        "quality": {"score_gate": 70},
        "dimensions": {"linting": {"enabled": True, "weight": 1.0}},  # no "target"
    }
    result = compute_overall_score(_sc(linting=80), cfg)
    assert result["breakdown"]["linting"]["target"] == 100   # default
    assert result["breakdown"]["linting"]["gap"] == 20       # 100 − 80 = 20


# ─── compute_overall_score: weight_sum > 0 guard (L365) ─────────────────────

def test_overall_score_zero_when_all_dims_disabled():
    """All disabled → weight_sum=0 → overall=0 (not ZeroDivisionError).
    Kills 'if weight_sum > 0 else 0' mutation."""
    cfg = {
        "quality": {"score_gate": 70},
        "dimensions": {"linting": {"enabled": False, "weight": 1.0, "target": 100}},
    }
    result = compute_overall_score({}, cfg)   # no scores needed (all disabled)
    assert result["overall_score"] == 0.0
    assert result["breakdown"] == {}


# ─── load_scores filesystem integration (L200-249) ───────────────────────────

def test_load_scores_reads_correct_directory(tmp_path):
    """load_scores loads .json files from <round_dir>/scores/.
    Kills 'scores' directory-name string mutation."""
    round_dir = tmp_path / ".sessi-work" / "round_1"
    scores_dir = round_dir / "scores"
    scores_dir.mkdir(parents=True)

    # tool_outputs relative to project_root = tmp_path
    tool_file = tmp_path / "linting.txt"
    tool_file.write_text("ruff output", encoding="utf-8")

    score_file = scores_dir / "linting.json"
    score_file.write_text(_json.dumps({
        "dimension": "linting", "round": 1,
        "tool_score": 80, "score": 80,
        "tool_outputs": "linting.txt",   # relative to project_root
        "findings": [],
    }), encoding="utf-8")

    scores = load_scores(str(round_dir))
    assert "linting" in scores
    assert scores["linting"]["score"] == 80


def test_load_scores_raises_when_no_files(tmp_path):
    """Empty scores dir → ValueError.  Kills early-exit path mutations."""
    round_dir = tmp_path / ".sessi-work" / "round_1"
    (round_dir / "scores").mkdir(parents=True)
    with _pytest.raises(ValueError, match="No score files"):
        load_scores(str(round_dir))


def test_load_scores_raises_when_dir_missing(tmp_path):
    """Missing scores/ subdir → FileNotFoundError."""
    round_dir = tmp_path / ".sessi-work" / "round_1"
    round_dir.mkdir(parents=True)   # no "scores" subdir
    with _pytest.raises(FileNotFoundError):
        load_scores(str(round_dir))
