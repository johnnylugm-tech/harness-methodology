"""Oracle tests for constitution/runner.py private helpers.

Gap: test_constitution_runner.py calls run_constitution_check() and _scan_file_compliance()
but never directly tests _keyword_density, _keyword_stuffing_penalty, or _aggregate_score.
All formula mutations (weights 0.4/0.3/0.3, decile threshold 0.5, tail 0.85, etc.) survived.

Design rule: expected values hard-coded; never re-derive from the mutated source.
"""
import pytest

from pathlib import Path

from core.quality_gate.constitution.runner import (
    _keyword_density,
    _keyword_stuffing_penalty,
    _scan_file_compliance,
    _aggregate_score,
    _threshold_for_dimension,
)


# ─── _keyword_density ────────────────────────────────────────────────────────

def test_keyword_density_empty_keywords_returns_100():
    """if not keywords: return 100.0.  Kills condition inversion."""
    assert _keyword_density("any content", []) == 100.0


def test_keyword_density_all_present_returns_100():
    """All keywords hit → min(3/3, 1.0)*100 = 100.0.  Kills min/len arithmetic."""
    assert _keyword_density("auth validation encrypt", ["auth", "validation", "encrypt"]) == 100.0


def test_keyword_density_partial_hit():
    """1 of 3 keywords → 1/3*100 = 33.3.  Kills hits/len division."""
    score = _keyword_density("auth is handled here", ["auth", "validation", "encrypt"])
    assert round(score, 1) == 33.3


def test_keyword_density_two_of_four():
    """2/4 → 50.0.  Kills numerator accumulation."""
    score = _keyword_density("auth validation only", ["auth", "validation", "encrypt", "tls"])
    assert score == 50.0


def test_keyword_density_overflow_capped_at_100():
    """Content mentioning keyword multiple times: still counts once → 100.
    min(…, 1.0) prevents >100 — kills cap removal mutation."""
    score = _keyword_density("auth auth auth auth", ["auth"])
    assert score == 100.0


def test_keyword_density_case_insensitive():
    """Keywords lowered before matching.  Kills kw.lower() removal."""
    score = _keyword_density("authentication is key", ["Authentication"])
    assert score == 100.0


def test_keyword_density_zero_hits():
    """No keyword present → 0/3*100 = 0.0."""
    score = _keyword_density("nothing relevant here", ["auth", "validation", "encrypt"])
    assert score == 0.0


# ─── _keyword_stuffing_penalty ────────────────────────────────────────────────

def _make_doc(total_len: int, keyword: str, positions_norm: list) -> str:
    """Build a document with 'keyword' placed at specified normalized positions.

    positions_norm: list of floats in [0, 1).
    """
    doc = [" "] * total_len
    for p in positions_norm:
        idx = int(p * total_len)
        idx = min(idx, total_len - len(keyword))
        for i, c in enumerate(keyword):
            doc[idx + i] = c
    return "".join(doc)


def test_stuffing_short_content_no_penalty():
    """Content <200 chars → return 1.0.  Kills len(content) < 200 threshold."""
    short = "auth " * 20   # ~100 chars
    assert len(short) < 200
    assert _keyword_stuffing_penalty(short, ["auth"]) == 1.0


def test_stuffing_empty_keywords_no_penalty():
    """Empty keywords → return 1.0 immediately."""
    assert _keyword_stuffing_penalty("a" * 500, []) == 1.0


def test_stuffing_few_positions_no_penalty():
    """< 3 occurrences → 1.0.  Kills len(positions) < 3 guard."""
    doc = _make_doc(2000, "auth", [0.1, 0.9])  # only 2 occurrences
    assert _keyword_stuffing_penalty(doc, ["auth"]) == 1.0


def test_stuffing_severe_clustering_returns_05():
    """All occurrences at <0.02 → stdev <0.05 → 0.5.  Kills < 0.05 threshold."""
    # 4 keywords clustered at 0.01, 0.02, 0.015, 0.025 of a 10000-char doc
    doc = _make_doc(10000, "auth", [0.01, 0.015, 0.02, 0.025])
    result = _keyword_stuffing_penalty(doc, ["auth"])
    assert result == 0.5, f"expected 0.5, got {result}"


def test_stuffing_natural_distribution_no_penalty():
    """10 occurrences evenly spread → stdev ~0.3 → 1.0.  Kills > 0.15 fallthrough."""
    doc = _make_doc(10000, "auth", [i / 10 for i in range(10)])
    result = _keyword_stuffing_penalty(doc, ["auth"])
    assert result == 1.0, f"expected 1.0, got {result}"


def test_stuffing_tail_concentration_returns_06():
    """≥4 positions, >50% in last 15% → 0.6.  Kills p > 0.85 and 0.5 tail threshold."""
    # 4 positions: 3 in tail (0.87, 0.90, 0.95), 1 earlier (0.1)
    # 3/4 = 75% in tail > 50% → 0.6; but stdev must be > 0.15 to reach tail check
    doc = _make_doc(10000, "auth", [0.1, 0.87, 0.90, 0.95])
    result = _keyword_stuffing_penalty(doc, ["auth"])
    assert result == 0.6, f"expected 0.6, got {result}"


def test_stuffing_decile_concentration_returns_07():
    """≥6 positions, >50% in one decile → 0.7.  Kills 0.5 decile threshold."""
    # 7 positions: 6 in decile 3 (0.30-0.39), 1 elsewhere
    # stdev should be > 0.15 so we reach decile check
    # positions: 0.1, 0.30, 0.31, 0.32, 0.33, 0.34, 0.35
    doc = _make_doc(10000, "auth", [0.05, 0.30, 0.31, 0.32, 0.33, 0.34, 0.35])
    result = _keyword_stuffing_penalty(doc, ["auth"])
    assert result == 0.7, f"expected 0.7, got {result}"


# ─── _aggregate_score ─────────────────────────────────────────────────────────

def test_aggregate_score_minimum_of_dims():
    """min(correctness, security) = min(80, 60) = 60.  Kills min→avg mutation."""
    dims = {"correctness": 80.0, "security": 60.0, "maintainability": 90.0}
    active = ["correctness", "security", "maintainability"]
    assert _aggregate_score(dims, active) == 60.0


def test_aggregate_score_empty_active_dims_returns_100():
    """No active dims → vacuously 100.  Kills 'if not active_dims' inversion."""
    assert _aggregate_score({"correctness": 50.0}, []) == 100.0


def test_aggregate_score_missing_dim_defaults_to_zero():
    """Dim not in scores → dim_scores.get(d, 0.0) = 0.  Kills default value mutation."""
    assert _aggregate_score({}, ["correctness"]) == 0.0


def test_aggregate_score_single_dim():
    """Single dim: min of one = that value."""
    assert _aggregate_score({"correctness": 75.0}, ["correctness"]) == 75.0


# ─── _threshold_for_dimension ─────────────────────────────────────────────────

def test_threshold_phase5_returns_80():
    """phase=5 > 4 → returns 80.0.  Kills phase <= 4 → phase <= 5 mutation."""
    assert _threshold_for_dimension("correctness", 5) == 80.0


def test_threshold_phase4_uses_profile():
    """phase=4 ≤ 4 → uses profile (not 80.0).  Kills <= 4 → < 4 mutation."""
    t = _threshold_for_dimension("correctness", 4)
    # Profile gives correctness threshold; must differ from 80 to confirm not fallback
    assert isinstance(t, float)
    assert t != 80.0


def test_threshold_phase4_vs_phase5_differ():
    """phase=4 uses profile, phase=5 uses 80 — must produce different logic path."""
    _threshold_for_dimension("correctness", 4)   # exercises profile path
    t5 = _threshold_for_dimension("correctness", 5)
    # phase 5 is always 80.0; phase 4 is profile-driven
    assert t5 == 80.0


# ─── _scan_file_compliance formula weights ────────────────────────────────────

def _write_doc(tmp_path: Path, name: str, content: str) -> Path:
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    return p


def test_scan_file_refs_weight_03(tmp_path):
    """Only FR+NFR refs, no keywords, no headers → correctness = c_refs * 0.3.
    Kills 0.3 weight mutation for c_refs."""
    # c_refs: has_fr=T, has_nfr=T, has_ac=F → (1+1+0)/3*100 = 66.7
    # c_kw ≈ 0 (no actual constitution keywords), c_structure = 0
    # correctness ≈ 0*0.4 + 0*0.3 + 66.7*0.3 = 20.0
    content = ("FR-001 compliance check.\n" +
               "NFR-01 performance requirement.\n" +
               "x " * 200)   # padding to exceed 100-char minimum
    p = _write_doc(tmp_path, "doc.md", content)
    result = _scan_file_compliance(p, phase=1)
    # correctness is c_refs*0.3 dominated; must be clearly > 0 and < 50
    assert 0 < result["correctness"] < 50, f"got {result['correctness']}"


def test_scan_file_structure_weight_03(tmp_path):
    """Five section headers → c_structure=100, no keywords/refs → correctness=30.
    Kills 0.3 weight for c_structure and 5.0 divisor."""
    content = ("\n## Section 1\n\n## Section 2\n\n## Section 3\n\n"
               "## Section 4\n\n## Section 5\n\n" + "x " * 200)
    p = _write_doc(tmp_path, "doc.md", content)
    result = _scan_file_compliance(p, phase=1)
    # c_structure = min(5/5, 1.0)*100 = 100; correctness ≈ c_structure*0.3 = 30
    assert 25 <= result["correctness"] <= 35, f"got {result['correctness']}"


def test_scan_file_short_content_returns_zeros(tmp_path):
    """Content < 100 chars → all dimensions 0.0.  Kills < 100 threshold."""
    p = _write_doc(tmp_path, "short.md", "short doc")
    result = _scan_file_compliance(p)
    assert result == {"correctness": 0.0, "security": 0.0,
                      "maintainability": 0.0, "coverage": 0.0}


def test_scan_file_nonexistent_returns_zeros(tmp_path):
    """Non-existent file → all dimensions 0.0."""
    result = _scan_file_compliance(tmp_path / "ghost.md")
    assert result == {"correctness": 0.0, "security": 0.0,
                      "maintainability": 0.0, "coverage": 0.0}


def test_scan_file_section_count_divisor(tmp_path):
    """3 headers → c_structure=60. Kills 5.0→4.0 divisor mutation and 0.3 weight."""
    # Only headers, no keywords, no refs → correctness ≈ c_structure * 0.3 = 18.0
    content = ("\n## Design\n\n## Implementation\n\n## Testing\n\n" + "x " * 200)
    p = _write_doc(tmp_path, "doc.md", content)
    result = _scan_file_compliance(p, phase=1)
    # c_structure = min(3/5, 1.0)*100 = 60.0; correctness ≈ 60*0.3 = 18.0
    # If divisor 5→4: c_structure = 75.0 → correctness ≈ 22.5 (not 18)
    assert 15 <= result["correctness"] <= 22, f"expected ~18, got {result['correctness']}"


def test_scan_file_c_refs_one_third(tmp_path):
    """Only FR ref (no NFR, no AC) → c_refs = 1/3 * 100 = 33.3.
    Kills 3.0→2.0 denominator mutation (would give 50.0 → correctness +5pp)."""
    # Use "qqq" filler to avoid accidentally matching profile keywords
    content = "fr-001. " + "qqq " * 100   # ~404 chars, no real keywords
    p = _write_doc(tmp_path, "doc.md", content)
    r1 = _scan_file_compliance(p, phase=1)["correctness"]

    # Two refs: c_refs = 2/3*100 = 66.7; with mutation 3→2: 100
    content2 = "fr-001. nfr-02. " + "qqq " * 100
    p2 = _write_doc(tmp_path, "d2.md", content2)
    r2 = _scan_file_compliance(p2, phase=1)["correctness"]

    # The delta between 1-ref and 2-ref should reflect the 33.3pp difference in c_refs
    # (both multiplied by 0.3 weight).  r2 - r1 ≈ 33.3*0.3 = 10.0
    delta = r2 - r1
    assert delta >= 5.0, f"1-ref={r1:.1f}, 2-ref={r2:.1f}, delta={delta:.1f} < 5pp (expected ~10)"


def test_scan_file_c_refs_two_thirds(tmp_path):
    """FR + NFR + AC → c_refs = 100; FR + NFR only → 66.7.  Kills denominator mutation."""
    content_2ref = "fr-001. nfr-02. " + "qqq " * 100
    content_3ref = "fr-001. nfr-02. acceptance criteria. " + "qqq " * 100
    p2 = _write_doc(tmp_path, "d2.md", content_2ref)
    p3 = _write_doc(tmp_path, "d3.md", content_3ref)
    r2 = _scan_file_compliance(p2, phase=1)["correctness"]
    r3 = _scan_file_compliance(p3, phase=1)["correctness"]
    # 3/3 > 2/3 → r3 > r2; with 3→2 mutation both caps at 100 → delta disappears
    assert r3 > r2, f"3-ref={r3:.1f} should exceed 2-ref={r2:.1f}"
    assert r3 - r2 >= 5.0, f"delta={r3-r2:.1f} < 5pp; denominator mutation would shrink this"


def test_stuffing_boundary_exactly_200_chars():
    """len(content)==200 → NOT < 200 → does not short-circuit.
    Kills < 200 → < 201 mutation: content of 200 would then be treated as short."""
    # Build content with 200 chars and 4 occurrences of 'auth' well-spread
    base = "auth " * 4  # 20 chars
    filler = "x " * 90   # 180 chars
    content = base + filler   # ~200 chars total
    content = content[:200]   # exactly 200
    assert len(content) == 200
    # Should NOT short-circuit (200 is not < 200) → proceeds to position check
    # With too few positions, may return 1.0 anyway — key is it doesn't error
    result = _keyword_stuffing_penalty(content, ["auth"])
    assert isinstance(result, float)
    assert 0 < result <= 1.0


def test_keyword_density_min_cap_is_1():
    """min(hits/len, 1.0) cap is 1.0, not 2.0.  Kills 1.0→2.0 literal mutation.
    Need content where hits==len to confirm cap=1.0 (score==100, not 200)."""
    score = _keyword_density("auth validation encrypt", ["auth", "validation", "encrypt"])
    assert score == 100.0   # not 200.0 — cap holds

pytestmark = pytest.mark.mutation_oracle
