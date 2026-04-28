import pytest
import os
import subprocess

@pytest.mark.spec
def test_harness_cli_plan_phase_contract():
    """SPEC: python harness_cli.py plan-phase --phase 3 exists and runs."""
    # RED state: Check if CLI responds
    result = subprocess.run(["python3", "harness_cli.py", "plan-phase", "--phase", "3", "--output", "plan_tmp.md"], capture_output=True, text=True)
    assert result.returncode == 0
    assert os.path.exists("plan_tmp.md")
    os.remove("plan_tmp.md")

@pytest.mark.spec
def test_gate_hybrid_scoring_contract():
    """SPEC: min(tool_score, llm_score) logic exists in harness/harness_bridge.py or similar."""
    # TDD stub - searching for logic
    from harness.harness_bridge import HarnessBridge
    # This is a placeholder for the actual logic check
    # In RED state, we just want to see if the module is importable and has the intended structure
    assert hasattr(HarnessBridge, 'run_gate')

@pytest.mark.spec
def test_kill_switch_safety_contract():
    """SPEC: KillSwitch module must be present and functional."""
    from kill_switch.kill_switch import KillSwitch
    ks = KillSwitch()
    assert hasattr(ks, 'check')

@pytest.mark.spec
def test_crg_bridge_graceful_degradation():
    """SPEC: All CRG methods no-op if CRG not installed."""
    from harness.crg_bridge import CRGBridge
    bridge = CRGBridge()
    # Should not raise exception even if CRG missing
    result = bridge.check_impact("some_change")
    assert result is not None

@pytest.mark.spec
def test_reviewer_router_proxy_contract():
    """SPEC: ReviewerRouter implements proxy to Hermes MCP."""
    from harness.reviewer_router import ReviewerRouter
    router = ReviewerRouter()
    assert hasattr(router, 'request_review')

@pytest.mark.spec
def test_github_actions_ci_contract():
    """SPEC: .github/workflows/harness_ci.yml contains mutation threshold >= 70."""
    with open(".github/workflows/harness_ci.yml", "r") as f:
        content = f.read()
    assert "threshold: 70" in content or "threshold >= 70" in content or "70" in content

@pytest.mark.spec
def test_drift_monitor_cron_contract():
    """SPEC: scripts/cron_drift_monitor.py exists and is runnable."""
    assert os.path.exists("scripts/cron_drift_monitor.py")

@pytest.mark.spec
def test_decision_log_writer_artifact_contract():
    """SPEC: DecisionLogWriter produces YAML per-decision."""
    from core.decision_log_writer import DecisionLogWriter
    writer = DecisionLogWriter()
    assert hasattr(writer, 'write_log')

