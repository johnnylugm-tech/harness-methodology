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
