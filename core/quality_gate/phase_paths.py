"""
phase_paths.py — Artifact path registry per phase.

Stub module: provides PHASE_ARTIFACT_PATHS used by PhaseTruthVerifier.
The verifier builds its own inline checklist and only imports this
symbol to avoid NameError; actual path resolution is inlined there.
"""
import os
from typing import Dict, List
from pathlib import Path
from core.utils.project_layout import ProjectLayout

_dummy_layout = ProjectLayout(Path(os.getcwd()))

PHASE_ARTIFACT_PATHS: Dict[int, List[str]] = {
    1: [
        _dummy_layout.get_relative_str(_dummy_layout.srs_path),
        _dummy_layout.get_relative_str(_dummy_layout.spec_tracking_path),
        _dummy_layout.get_relative_str(_dummy_layout.traceability_matrix_path),
    ],
    2: [_dummy_layout.get_relative_str(_dummy_layout.sad_path)],
    3: [
        _dummy_layout.get_relative_str(_dummy_layout.active_src_dir) + "/",
        _dummy_layout.get_relative_str(_dummy_layout.active_test_dir) + "/",
    ],
    4: [
        _dummy_layout.get_relative_str(_dummy_layout.test_plan_path),
        _dummy_layout.get_relative_str(_dummy_layout.test_results_path),
    ],
    5: [
        _dummy_layout.get_relative_str(_dummy_layout.baseline_path),
        _dummy_layout.get_relative_str(_dummy_layout.verification_report_path),
    ],
    6: [_dummy_layout.get_relative_str(_dummy_layout.quality_report_path)],
    7: [
        _dummy_layout.get_relative_str(_dummy_layout.risk_register_path),
        _dummy_layout.get_relative_str(_dummy_layout.risk_mitigation_plans_path),
        _dummy_layout.get_relative_str(_dummy_layout.risk_status_report_path),
    ],
    8: [
        _dummy_layout.get_relative_str(_dummy_layout.config_records_path),
        _dummy_layout.get_relative_str(_dummy_layout.release_checklist_path),
    ],
}
