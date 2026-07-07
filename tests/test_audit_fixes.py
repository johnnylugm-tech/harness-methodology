# -*- coding: utf-8 -*-
"""
Unit tests for the 14-feature system audit fixes.
"""

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
        "detection": {
            "detection/drift_detector"
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


# (test_steering_loop_weight_normalization removed with steering/ — 減法 T4)

def test_agent_proof_hook_path_init(tmp_path):
    from enforcement.agent_proof_hook import AgentProofHook
    
    # Create fake hooks directory
    (tmp_path / ".git" / "hooks").mkdir(parents=True, exist_ok=True)
    
    hook = AgentProofHook(str(tmp_path))
    
    # Assert it targets commit-msg and NOT pre-commit
    assert hook.hook_path.name == "commit-msg"
    assert "pre-commit" not in str(hook.hook_path)
