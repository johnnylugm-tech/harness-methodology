"""Smoke tests for SSI toolkit — ensures imported scripts are importable and callable.

These are minimal existence/import tests to close the coverage gap where
harness/ssi/scripts/ had 0% test coverage in Round 1.
"""

import importlib
import sys
from pathlib import Path

import pytest

SSI_DIR = Path(__file__).parent.parent / "harness" / "ssi" / "scripts"
sys.path.insert(0, str(SSI_DIR))


@pytest.mark.parametrize("module_name", [
    "config_loader",
    "crg_analysis",
    "crg_integration",
    "issue_tracker",
    "report_gen",
    "checkpoint",
    "setup_target",
    "llm_router",
    "verify",
])
def test_ssi_module_import(module_name):
    """Smoke: module can be imported without runtime errors."""
    mod = importlib.import_module(module_name)
    assert mod is not None


def test_checkpoint_save_load_round(tmp_path):
    """checkpoint.create_round_snapshot and create_round_summary work."""
    from checkpoint import create_round_snapshot, create_round_summary

    data = {"overall_score": 85.0, "dimensions": {"linting": {"score": 100}}}
    snap = create_round_snapshot(1, data["dimensions"], data["overall_score"])
    assert snap["round"] == 1
    assert snap["overall_score"] == 85.0


def test_setup_target_resolve(tmp_path):
    """setup_target.resolve_target resolves a local path."""
    from setup_target import resolve_target

    result = resolve_target(str(tmp_path))
    assert isinstance(result, str)


def test_llm_router_route():
    """llm_router.route returns a dict with model key."""
    from llm_router import route

    result = route("linting")
    assert isinstance(result, dict)
    assert "model" in result


def test_config_loader_defaults(tmp_path):
    """config_loader returns valid config dict with required keys."""
    from config_loader import load_config

    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text("dimensions:\n  linting:\n    weight: 1.0\n    enabled: true\n")
    cfg = load_config(str(cfg_file))
    assert isinstance(cfg, dict)
    assert "dimensions" in cfg


def test_issue_tracker_create_and_load(tmp_path):
    """issue_tracker.load/save round-trip preserves data."""
    from issue_tracker import load, save, add_finding

    reg_path = tmp_path / "registry.json"
    reg = load(str(reg_path))
    assert "issues" in reg

    fid = add_finding(reg, {
        "severity": "high",
        "message": "Test finding",
        "file": "test.py",
        "line": 1,
    }, "linting", 1)
    assert isinstance(fid, str)

    save(reg, str(reg_path))
    reg2 = load(str(reg_path))
    assert len(reg2["issues"]) == 1


def test_issue_tracker_idempotent():
    """Same finding produces same deterministic ID."""
    from issue_tracker import _issue_id

    id1 = _issue_id("linting", "test.py", 1, "Test message")
    id2 = _issue_id("linting", "test.py", 1, "Test message")
    assert id1 == id2


def test_crg_analysis_eval_depth():
    """compute_eval_depth maps risk scores to depth levels."""
    from crg_analysis import compute_eval_depth

    assert compute_eval_depth(0.2) in ("fast", "standard", "deep")
    assert compute_eval_depth(0.8) == "deep"


def test_crg_analysis_dead_code_ratio():
    """compute_dead_code_ratio returns ratio dict with expected keys."""
    from crg_analysis import compute_dead_code_ratio

    result = compute_dead_code_ratio([], 100)
    assert isinstance(result, dict)
    assert result["ratio"] == 0.0


def test_is_risky_low_risk():
    """Low risk_score + non-hub → not risky."""
    from crg_integration import is_risky

    assert not is_risky({"risk_score": 0.3, "is_hub": False})


def test_is_risky_high_risk():
    """High risk_score → risky."""
    from crg_integration import is_risky

    assert is_risky({"risk_score": 0.85, "is_hub": False})


def test_verify_tools_check_command():
    """check_command returns bool for known commands."""
    from verify_tools import check_command

    result = check_command("python3")
    assert isinstance(result, bool)


def test_verify_tools_check_tools():
    """check_tools returns categorized results dict."""
    from verify_tools import check_tools

    result = check_tools({"ruff": "ruff --version"}, "Linting")
    assert isinstance(result, dict)


