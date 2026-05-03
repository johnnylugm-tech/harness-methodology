# tests/test_gate_remediation.py
import pytest
from core.verification_gate import GateRemediationReport, _GATE_THRESHOLDS


class TestGateRemediationReport:
    def test_effective_threshold_default(self):
        r = GateRemediationReport(gate_num=2, phase=3, score=70.0)
        assert r.effective_threshold == _GATE_THRESHOLDS[2]  # 75.0

    def test_effective_threshold_override(self):
        r = GateRemediationReport(gate_num=2, phase=3, score=70.0, threshold=80.0)
        assert r.effective_threshold == 80.0

    def test_gap_computed_correctly(self):
        r = GateRemediationReport(gate_num=2, phase=3, score=70.0)
        assert r.gap == pytest.approx(5.0)

    def test_gap_zero_when_above_threshold(self):
        r = GateRemediationReport(gate_num=2, phase=3, score=80.0)
        assert r.gap == 0.0

    def test_action_items_non_empty(self):
        r = GateRemediationReport(gate_num=3, phase=4, score=75.0)
        items = r.action_items()
        assert len(items) >= 1
        assert all(isinstance(i, str) for i in items)

    def test_failing_checks_appear_first(self):
        r = GateRemediationReport(
            gate_num=2, phase=3, score=68.0,
            failing_checks=["D3_Coverage", "D5_Security"]
        )
        items = r.action_items()
        assert items[0].startswith("Fix failing check: **D3_Coverage**")
        assert items[1].startswith("Fix failing check: **D5_Security**")

    def test_to_status_string_contains_key_info(self):
        r = GateRemediationReport(gate_num=2, phase=3, score=70.0)
        s = r.to_status_string()
        assert "Gate 2 FAILED" in s
        assert "70.0" in s
        assert "75.0" in s  # threshold

    def test_to_status_string_includes_failing_checks(self):
        r = GateRemediationReport(
            gate_num=1, phase=3, score=65.0,
            failing_checks=["unit_tests"]
        )
        s = r.to_status_string()
        assert "unit_tests" in s

    def test_to_dict_serialisable(self):
        import json
        r = GateRemediationReport(
            gate_num=4, phase=6, score=82.0,
            failing_checks=["D5_Security"],
            gate_evidence={"validator_result": False}
        )
        d = r.to_dict()
        # Must be JSON-serialisable
        json.dumps(d)
        assert d["gate_num"] == 4
        assert d["gap"] == pytest.approx(3.0)
        assert "action_items" in d

    def test_unknown_gate_falls_back_to_generic_actions(self):
        r = GateRemediationReport(gate_num=9, phase=9, score=50.0)
        items = r.action_items()
        assert any("Gate 9" in item for item in items)

    @pytest.mark.parametrize("gate_num,expected_threshold", [
        (1, 70.0), (2, 75.0), (3, 80.0), (4, 85.0)
    ])
    def test_all_gate_thresholds(self, gate_num, expected_threshold):
        r = GateRemediationReport(gate_num=gate_num, phase=gate_num + 1, score=0.0)
        assert r.effective_threshold == expected_threshold
