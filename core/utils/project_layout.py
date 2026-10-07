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
    9: [("phase9_maintenance_dir", "MAINTENANCE_LOG.md")],
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
    @property
    def phase9_maintenance_dir(self) -> Path:  return self.root / "09-maintenance"

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
    def adr_path(self) -> Path:
        """The architecture decision record — the existing one, or where one goes.

        Round 26: `amend-sab --resolve-phantom` appends its amendment here, and a
        writer needs the canonical path even when the file does not exist yet, so
        this is not `_get_file_path`'s exists-first lookup (which would silently
        retarget a first write to the project root).

        Round 97: it was a plain join to `02-architecture/ADR.md`, and that is
        not where the framework puts the ADR. `init-project`'s artifact map
        deploys `templates/ADR.md` to `02-architecture/adr/ADR.md` and
        `legal_artifacts.DELIVERABLE_ANCHORS` keys the anchor on that path, so
        on all eleven corpus projects the real document (277-1069 lines) is in
        the sub-directory and this property pointed at nothing. `amend-sab`
        then CREATED something there: taskq-final's `02-architecture/ADR.md` is
        eight lines holding one amendment, beside an 893-line ADR the framework
        wrote elsewhere; taskq-new's is thirty-six. The required-artifact check
        is satisfied by the stub, and a reader coming through this API gets an
        amendment log instead of the architecture decisions.

        Both layouts stay supported — a project that keeps its ADR directly in
        the architecture dir is not asked to move it. Only the fallback moved,
        to where the framework itself deploys. `artifact_consistency._adr_path`
        was the only resolver that knew both and now calls this one, so the
        writer and the reader cannot point at different files again.
        """
        arch = self.phase2_architecture_dir
        for candidate in (arch / "adr" / "ADR.md", arch / "ADR.md"):
            if candidate.is_file():
                return candidate
        return arch / "adr" / "ADR.md"

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
    def maintenance_log_path(self) -> Path:        return self.phase9_maintenance_dir / "MAINTENANCE_LOG.md"
    @property
    def change_requests_dir(self) -> Path:         return self.methodology_dir / "change_requests"

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
    def uses_phase_layout(self) -> bool:
        """True when the project is laid out in phase directories (any of
        `core.phase_topology.PHASES`' dirs exists) — tracked content, unlike
        the empty `03-development/{tests,src}` init-project creates."""
        from core.phase_topology import PHASES

        return any((self.root / spec.dir).is_dir() for spec in PHASES.values())

    @staticmethod
    def _holds_files(directory: Path) -> bool:
        """At least one file under *directory*, caches and dot-paths aside."""
        try:
            return directory.is_dir() and any(
                p.is_file() and "__pycache__" not in p.parts
                and not any(part.startswith(".") for part in p.relative_to(directory).parts)
                for p in directory.rglob("*"))
        except OSError:
            return False

    def _active_root(self, phase_dir: Path, root_dir: Path) -> Path:
        """Round 116 站1: the root that holds files; neither → the layout's default.

        This used to ask whether `phase_dir` EXISTED. init-project creates it
        empty and git does not track empty directories, so every fresh checkout
        of an initialised project answered "root" until the first test was
        committed — taskq-final's FR-01 RED was told to write
        `tests/test_fr01.py`, and the framework's own golden prompts pair that
        path with `--cov=03-development/src`. Files are tracked; the answer no
        longer changes between a clone and the tree it was cloned from. A
        project that keeps its code at the root (supported by the JS
        toolchain templates) keeps it.
        """
        if self._holds_files(phase_dir):
            return phase_dir
        if self._holds_files(root_dir):
            return root_dir
        return phase_dir if self.uses_phase_layout else root_dir

    def stray_files(self) -> list[str]:
        """Files in the root the framework does not measure, project-relative.

        Round 116 站2. For tests and src alike: every file under the root
        `_active_root` did not choose, unless it resolves to a file the chosen
        root reaches (a symlink mirror IS run). taskq-final kept seven such
        files — four bug-hunt repros, a benchmark, two conftests — that no
        framework run ever executed, and nothing said so.
        """
        def files(d: Path) -> list[Path]:
            if not d.is_dir():
                return []
            return [p for p in d.rglob("*") if p.is_file() and "__pycache__" not in p.parts
                    and not any(part.startswith(".") for part in p.relative_to(d).parts)]

        out: list[str] = []
        for phase_dir, root_dir in ((self.phase3_development_dir / "tests", self.root_tests_dir),
                                    (self.phase3_development_dir / "src", self.root / "src")):
            active = self._active_root(phase_dir, root_dir)
            other = root_dir if active == phase_dir else phase_dir
            if not other.exists() or (active.exists() and other.resolve() == active.resolve()):
                continue
            reached = {p.resolve() for p in files(active)}
            out += sorted(self.get_relative_str(p) for p in files(other) if p.resolve() not in reached)
        return out

    def fr_test_file(self, fr_id: str) -> Path:
        """Where an FR's tests live: `test_fr<NN>.py` in the measured test root.

        Round 116 站3: one definition for run-fr-step, the FR prompts and
        check-test-mirrors-spec (the P3 workflow used to hard-code
        `tests/test_fr<NN>.py`, a root the suite may not run).
        """
        from core.canonical_form import fr_num_str

        return self.active_test_dir / f"test_fr{fr_num_str(fr_id)}.py"

    @property
    def active_test_dir(self) -> Path:
        """The test root the framework's suite runs (see `_active_root`)."""
        return self._active_root(self.phase3_development_dir / "tests", self.root_tests_dir)

    @property
    def active_src_dir(self) -> Path:
        """The source root the framework measures (see `_active_root`)."""
        return self._active_root(self.phase3_development_dir / "src", self.root / "src")

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
            9: self.phase9_maintenance_dir,
        }
        return mapping.get(phase, self.root / "docs")


# Populate PHASE_ARTIFACTS now that ProjectLayout is defined. Done as a
# single eager materialization (no per-call cost) and immediately after
# the class so any future property rename triggers a deterministic
# layout.get_relative_str() recompute.
PHASE_ARTIFACTS.update(
    {phase_num: phase_artifacts(phase_num) for phase_num in _PHASE_PROP_MAP}
)
