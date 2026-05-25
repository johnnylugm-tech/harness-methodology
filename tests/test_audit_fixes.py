# -*- coding: utf-8 -*-
"""
Unit tests for the 14-feature system audit fixes.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

# ─── 1. Test SAB Drift Detector Import Parsing ─────────────────────────────────

def test_resolve_import_layer_bidirectional_matching():
    from detection.drift_detector import DriftDetector
    
    # Mock project root path
    detector = DriftDetector("/tmp/fake_project")
    
    layer_to_modules = {
        "quality_gate": {
            "core/quality_gate/sab_parser",
            "core/quality_gate/phase_truth_verifier"
        },
        "steering": {
            "steering/steering_loop"
        }
    }
    
    # Test case A: exact match (standard directory form)
    assert detector._resolve_import_layer("core.quality_gate.sab_parser", layer_to_modules) == "quality_gate"
    
    # Test case B: parent match (from core import quality_gate)
    # The import_path is a parent directory of a registered module
    assert detector._resolve_import_layer("core.quality_gate", layer_to_modules) == "quality_gate"
    
    # Test case C: child match (from core.quality_gate.sab_parser import SABSpec)
    # The import_path contains the registered module as a prefix
    assert detector._resolve_import_layer("core.quality_gate.sab_parser.SABSpec", layer_to_modules) == "quality_gate"
    
    # Test case D: unmatched imports
    assert detector._resolve_import_layer("external_package.utils", layer_to_modules) is None


# ─── 2. Test Steering Loop Math & Normalized Weights ─────────────────────────────

def test_steering_loop_weight_normalization():
    from steering.steering_loop import SteeringLoop, SteeringConfig
    
    mock_provider = MagicMock()
    config = SteeringConfig(
        weights={
            "quality": 0.4,
            "efficiency": 0.2,
            "clarity": 0.2,
            "consistency": 0.2
        }
    )
    
    loop = SteeringLoop(mock_provider, config=config, history_path=None)
    
    # Test case A: all dimensions are 1.0 -> total score should be exactly 1.0
    scores_perfect = {
        "correctness": 1.0,
        "completeness": 1.0,
        "consistency": 1.0,
        "concision": 1.0,
        "maintainability": 1.0
    }
    score_a = loop._compute_weighted_score(scores_perfect)
    assert abs(score_a - 1.0) < 1e-5
    
    # Test case B: all dimensions are 0.5 -> total score should be exactly 0.5
    scores_half = {
        "correctness": 0.5,
        "completeness": 0.5,
        "consistency": 0.5,
        "concision": 0.5,
        "maintainability": 0.5
    }
    score_b = loop._compute_weighted_score(scores_half)
    assert abs(score_b - 0.5) < 1e-5
    
    # Test case C: verifying Quality subscore normalization
    # Quality: correctness * 0.7 + completeness * 0.3
    # If correctness = 1.0, completeness = 0.0 -> Quality subscore = 0.7
    # If others = 0.0 -> total = 0.7 * 0.4 = 0.28
    scores_custom = {
        "correctness": 1.0,
        "completeness": 0.0,
        "consistency": 0.0,
        "concision": 0.0,
        "maintainability": 0.0
    }
    score_c = loop._compute_weighted_score(scores_custom)
    assert abs(score_c - 0.28) < 1e-5


# ─── 3. Test Agent Proof Hook Path Setup ───────────────────────────────────────

def test_agent_proof_hook_path_init(tmp_path):
    from enforcement.agent_proof_hook import AgentProofHook
    
    # Create fake hooks directory
    (tmp_path / ".git" / "hooks").mkdir(parents=True, exist_ok=True)
    
    hook = AgentProofHook(str(tmp_path))
    
    # Assert it targets commit-msg and NOT pre-commit
    assert hook.hook_path.name == "commit-msg"
    assert "pre-commit" not in str(hook.hook_path)
