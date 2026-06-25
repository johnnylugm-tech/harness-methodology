"""
phase_paths.py — Artifact path registry per phase.

Back-compat shim: PHASE_ARTIFACT_PATHS is now derived from the canonical
PHASE_ARTIFACTS map in core.utils.project_layout (single source of truth).
This module re-exports the legacy PHASE_ARTIFACT_PATHS symbol for any
caller that still imports it (e.g. tests/test_phase_paths.py).
"""
from typing import Dict, List
from core.utils.project_layout import PHASE_ARTIFACTS

PHASE_ARTIFACT_PATHS: Dict[int, List[str]] = {
    phase_num: paths for phase_num, paths in PHASE_ARTIFACTS.items()
}
