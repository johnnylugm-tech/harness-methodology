import json
from scripts.generate_quality_report import _find_latest_gate_result, _build_dimension_table

def test_l1_find_latest_gate_result_methodology_fallback(tmp_path):
    """Test L1: .methodology/ directory fallback in _find_latest_gate_result."""
    (tmp_path / ".methodology").mkdir()
    
    # Write to .methodology instead of .sessi-work
    res_path = tmp_path / ".methodology" / "gate3_result.json"
    res_path.write_text(json.dumps({"passed": True, "score": 95}))
    
    gate_num, data = _find_latest_gate_result(tmp_path)
    assert gate_num == 3
    assert data["score"] == 95

def test_l1_build_dimension_table_breakdown_schema():
    """Test L1: breakdown schema parsing in _build_dimension_table."""
    gate_result = {
        "breakdown": {
            "code_coverage": {"score": 80, "detail": "foo"},
            "type_safety": {"score": 90, "detail": "bar"}
        }
    }
    
    lines = _build_dimension_table(gate_result)
    text = "\n".join(lines)
    # Checks if auto-generated title label works correctly
    assert "Code Coverage" in text
    assert "Type Safety" in text
    assert "| 80 |" in text or "| 80" in text
    assert "| 90 |" in text or "| 90" in text


def test_methodology_wins_over_sessi_work_same_gate(tmp_path):
    """When both dirs hold the same gate result, the committable .methodology copy
    (composite patched by finalize-gate) must win over the ephemeral, unpatched
    .sessi-work copy. Root cause of the 0/100 placeholder QUALITY_REPORT: the old
    search order read .sessi-work first (agent self-assessed score, never patched).
    """
    (tmp_path / ".methodology").mkdir()
    (tmp_path / ".sessi-work").mkdir()
    (tmp_path / ".sessi-work" / "gate4_result.json").write_text(
        json.dumps({"composite_score": 0, "passed": False}))
    (tmp_path / ".methodology" / "gate4_result.json").write_text(
        json.dumps({"composite_score": 96.41, "passed": True}))
    gate_num, data = _find_latest_gate_result(tmp_path)
    assert gate_num == 4
    assert data["composite_score"] == 96.41


def test_da_waiver_dimension_renders_pass_not_fail():
    """A dimension with raw tool score 0 but a DA waiver must render PASS (DA-waiver),
    not a bare FAIL — the waiver is the authoritative verdict (Architecture CRG case).
    """
    gate_result = {
        "breakdown": {"architecture": {"score": 0, "detail": "cohesion 0.228"}},
        "da_waiver": {"architecture": True},
    }
    text = "\n".join(_build_dimension_table(gate_result))
    assert "Architecture" in text
    assert "DA-waiver" in text
    assert "✗ FAIL" not in text


def test_non_waived_low_score_still_fails():
    """The waiver must not blanket-pass every low dimension — a dim without its own
    waiver keeps failing even when a different dim is waived."""
    gate_result = {
        "breakdown": {"linting": {"score": 20, "detail": ""}},
        "da_waiver": {"architecture": True},
    }
    text = "\n".join(_build_dimension_table(gate_result))
    assert "✗ FAIL" in text
