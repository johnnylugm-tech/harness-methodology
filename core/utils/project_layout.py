from pathlib import Path
from typing import Union

# Canonical artifact paths per phase, relative to project root.
# Single source of truth: every entry is derived from the matching
# ``ProjectLayout`` property below (see _PHASE_PROP_MAP). To rename a
# phase deliverable, update the property; to add a new one, add the
# property + extend the corresponding _PHASE_PROP_MAP entry.
_PHASE_PROP_MAP: dict[int, list[tuple[str, str]]] = {
    1: [
        ("phase1_requirements_dir", "SRS.md"),
        ("phase1_requirements_dir", "SPEC_TRACKING.md"),
        ("phase1_requirements_dir", "TRACEABILITY_MATRIX.md"),
    ],
    2: [("phase2_architecture_dir", "SAD.md")],
    3: [("active_src_dir", ""), ("active_test_dir", "")],
    4: [
        ("phase4_testing_dir", "TEST_PLAN.md"),
        ("phase4_testing_dir", "TEST_RESULTS.md"),
    ],
    5: [
        ("phase5_verification_dir", "BASELINE.md"),
        ("phase5_verification_dir", "VERIFICATION_REPORT.md"),
    ],
    6: [("phase6_quality_dir", "QUALITY_REPORT.md")],
    7: [
        ("phase7_risk_dir", "RISK_REGISTER.md"),
        ("phase7_risk_dir", "RISK_MITIGATION_PLANS.md"),
        ("phase7_risk_dir", "RISK_STATUS_REPORT.md"),
    ],
    8: [
        ("phase8_config_dir", "CONFIG_RECORDS.md"),
        ("phase8_config_dir", "RELEASE_CHECKLIST.md"),
    ],
}


def phase_artifacts(phase_num: int) -> list[str]:
    """Return a copy of the canonical artifact paths for ``phase_num``.

    Paths are derived from the corresponding ``ProjectLayout`` properties
    (see ``_PHASE_PROP_MAP``). Empty list when the phase has no
    mandatory document artifacts. P3 uses ``active_src_dir`` /
    ``active_test_dir`` so the documented ``./src`` / ``./tests`` fallback
    for projects that do not have a ``03-development/`` directory is
    preserved.
    """
    layout = ProjectLayout(".")
    mapping = _PHASE_PROP_MAP.get(phase_num, [])
    out: list[str] = []
    for prop_name, suffix in mapping:
        base = getattr(layout, prop_name)
        path = (base / suffix) if suffix else base
        rel = layout.get_relative_str(path)
        if suffix == "":
            rel = rel + "/" if not rel.endswith("/") else rel
        out.append(rel)
    return out


# Convenience view: same shape as the old PHASE_ARTIFACTS module dict
# (Phase -> [relative paths]) for callers that walk all phases.
# Initialized lazily below the ProjectLayout class definition.
PHASE_ARTIFACTS: dict[int, list[str]] = {}


class ProjectLayout:
    """全域專案路徑解析器 (Single Source of Truth)"""
    def __init__(self, project_root: Union[Path, str]):
        self.root = Path(project_root).resolve()

    # ==========================================
    # 1. 階段目錄 (Phase Directories)
    # ==========================================
    @property
    def summary_dir(self) -> Path:                 return self.root / "00-summary"
    @property
    def phase1_requirements_dir(self) -> Path: return self.root / "01-requirements"
    @property
    def phase2_architecture_dir(self) -> Path: return self.root / "02-architecture"
    @property
    def phase3_development_dir(self) -> Path:  return self.root / "03-development"
    @property
    def phase4_testing_dir(self) -> Path:      return self.root / "04-testing"
    @property
    def phase5_verification_dir(self) -> Path: return self.root / "05-verification"
    @property
    def phase6_quality_dir(self) -> Path:      return self.root / "06-quality"
    @property
    def phase7_risk_dir(self) -> Path:         return self.root / "07-risk"
    @property
    def phase8_config_dir(self) -> Path:       return self.root / "08-config"

    # ==========================================
    # 2. 核心產物 (Core Artifacts)
    # ==========================================
    @property
    def srs_path(self) -> Path:                    return self.phase1_requirements_dir / "SRS.md"
    @property
    def spec_tracking_path(self) -> Path:          return self.phase1_requirements_dir / "SPEC_TRACKING.md"
    @property
    def traceability_matrix_path(self) -> Path:    return self.phase1_requirements_dir / "TRACEABILITY_MATRIX.md"
    @property
    def spec_path(self) -> Path:                   return self.root / "SPEC.md"
    @property
    def test_inventory_path(self) -> Path:         return self.root / "TEST_INVENTORY.yaml"

    def _get_file_path(self, filename: str, phase_dir: Path) -> Path:
        phase_path = phase_dir / filename
        if phase_path.exists():
            return phase_path
        return self.root / filename

    @property
    def sad_path(self) -> Path:                return self._get_file_path("SAD.md", self.phase2_architecture_dir)
    @property
    def test_spec_path(self) -> Path:          return self._get_file_path("TEST_SPEC.md", self.phase2_architecture_dir)

    @property
    def test_plan_path(self) -> Path:              return self.phase4_testing_dir / "TEST_PLAN.md"
    @property
    def test_results_path(self) -> Path:           return self.phase4_testing_dir / "TEST_RESULTS.md"

    @property
    def baseline_path(self) -> Path:               return self.phase5_verification_dir / "BASELINE.md"
    @property
    def verification_report_path(self) -> Path:    return self.phase5_verification_dir / "VERIFICATION_REPORT.md"

    @property
    def quality_report_path(self) -> Path:         return self.phase6_quality_dir / "QUALITY_REPORT.md"

    @property
    def risk_status_report_path(self) -> Path:     return self.phase7_risk_dir / "RISK_STATUS_REPORT.md"
    @property
    def risk_register_path(self) -> Path:          return self.phase7_risk_dir / "RISK_REGISTER.md"
    @property
    def risk_mitigation_plans_path(self) -> Path:  return self.phase7_risk_dir / "RISK_MITIGATION_PLANS.md"

    @property
    def config_records_path(self) -> Path:         return self.phase8_config_dir / "CONFIG_RECORDS.md"
    @property
    def release_checklist_path(self) -> Path:      return self.phase8_config_dir / "RELEASE_CHECKLIST.md"

    @property
    def handover_path(self) -> Path:               return self.root / "HANDOVER.md"

    # ==========================================
    # 3. 配置與清單 (Manifests & Configs)
    # ==========================================
    @property
    def manifest_dir(self) -> Path:            return self.root / "manifest"
    @property
    def quality_manifest_path(self) -> Path:   return self.methodology_dir / "quality_manifest.json"
    @property
    def enforcement_config_path(self) -> Path: return self.methodology_dir / "enforcement.json"
    @property
    def root_tests_dir(self) -> Path:          return self.root / "tests"

    @property
    def root_test_dir_candidates(self) -> list[Path]:
        """Candidate test directories at the project root."""
        return [self.active_test_dir, self.root / "test"]

    @staticmethod
    def subdir_test_dirs(cwd: Path) -> list[Path]:
        """Candidate test directories for a subdirectory-override cwd.

        When ``[mutmut]`` lives in a subdirectory's ``setup.cfg`` (e.g.
        ``03-development/setup.cfg``), the source has moved into that
        subdirectory and tests live alongside it. Returns the candidates
        the override scenario should search, in priority order.
        """
        return [cwd / "tests", cwd / "test"]

    # ==========================================
    # 4. 內部方法論狀態 (.methodology)
    # ==========================================
    @property
    def methodology_dir(self) -> Path: return self.root / ".methodology"
    
    @property
    def state_json_path(self) -> Path:        return self.methodology_dir / "state.json"
    @property
    def sessions_spawn_log(self) -> Path:     return self.methodology_dir / "sessions_spawn.log"
    @property
    def quality_score_path(self) -> Path:     return self.methodology_dir / ".quality_score"
    
    # Traceability Subsystem
    @property
    def trace_dir(self) -> Path:              return self.methodology_dir / "trace"
    @property
    def attestation_path(self) -> Path:       return self.trace_dir / "attestation.json"
    @property
    def proposed_fix_diff_path(self) -> Path: return self.trace_dir / "proposed_fix.diff"
    
    # Reports
    @property
    def traceability_report_path(self) -> Path: return self.root / "traceability_report.json"
    @property
    def report_json_path(self) -> Path:         return self.root / "report.json"

    # ==========================================
    # 5. 動態解析與輔助方法 (Dynamic Resolution)
    # ==========================================
    @property
    def active_test_dir(self) -> Path:
        """
        解析當前有效的測試目錄。
        優先回傳 03-development/tests，若不存在則退回根目錄下的 tests。
        """
        dev_tests = self.phase3_development_dir / "tests"
        if dev_tests.is_dir():
            return dev_tests
        return self.root_tests_dir

    @property
    def active_src_dir(self) -> Path:
        """
        解析當前有效的原始碼目錄。
        優先回傳 03-development/src，若不存在則退回根目錄下的 src。
        """
        dev_src = self.phase3_development_dir / "src"
        if dev_src.is_dir():
            return dev_src
        return self.root / "src"

    def get_relative_str(self, target_path: Path) -> str:
        """回傳相對於專案根目錄的相對路徑字串"""
        try:
            return str(target_path.relative_to(self.root))
        except ValueError:
            return str(target_path)

    def get_phase_dir(self, phase: int) -> Path:
        """取得指定 Phase 的專屬目錄。"""
        mapping = {
            1: self.phase1_requirements_dir,
            2: self.phase2_architecture_dir,
            3: self.phase3_development_dir,
            4: self.phase4_testing_dir,
            5: self.phase5_verification_dir,
            6: self.phase6_quality_dir,
            7: self.phase7_risk_dir,
            8: self.phase8_config_dir,
        }
        return mapping.get(phase, self.root / "docs")


# Populate PHASE_ARTIFACTS now that ProjectLayout is defined. Done as a
# single eager materialization (no per-call cost) and immediately after
# the class so any future property rename triggers a deterministic
# layout.get_relative_str() recompute.
PHASE_ARTIFACTS.update(
    {phase_num: phase_artifacts(phase_num) for phase_num in _PHASE_PROP_MAP}
)
