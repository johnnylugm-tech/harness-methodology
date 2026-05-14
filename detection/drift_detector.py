#!/usr/bin/env python3
"""
M2: Drift Detector
==================
Detects drift between code artifacts and specification documents.

Detects:
- SAD drift  : code structure deviates from SAD module mapping.
- Spec drift : implemented features missing from SRS.
- Phase drift: current phase state inconsistent with artifacts.
"""

from __future__ import annotations

import re
import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Set


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

class DriftSeverity(Enum):
    """Severity levels for drift findings."""
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


@dataclass
class DriftItem:
    """A single drift finding."""
    drift_type: str
    severity: DriftSeverity
    location: str
    description: str
    expected: Optional[str] = None
    actual: Optional[str] = None


@dataclass
class DriftResult:
    """Result of a drift detection run."""
    drift_type: str
    has_drift: bool
    drift_items: List[DriftItem] = field(default_factory=list)
    checked: int = 0
    drifted: int = 0
    score: float = 1.0  # 1.0 = no drift, 0.0 = full drift

    def to_dict(self) -> Dict:
        """Serialize the drift result to a dictionary."""
        return {
            "drift_type": self.drift_type,
            "has_drift": self.has_drift,
            "checked": self.checked,
            "drifted": self.drifted,
            "score": round(self.score, 3),
            "items": [
                {
                    "type": i.drift_type,
                    "severity": i.severity.value,
                    "location": i.location,
                    "description": i.description,
                }
                for i in self.drift_items
            ],
        }


# ---------------------------------------------------------------------------
# DriftDetector
# ---------------------------------------------------------------------------

class DriftDetector:
    """
    Detects drift between code and specification artifacts.

    Checks:
    1. SAD drift  -- code file structure vs SAD module table
    2. Spec drift -- implemented code vs SRS FR list
    3. Phase drift-- state.json phase vs artifact presence
    """

    FR_PATTERN = re.compile(r'FR-(\d+)')
    MODULE_PATTERN = re.compile(r'`([^`]+\.py)`')
    SAD_FR_PATTERN = re.compile(r'FR-(\d+)[^\n]*?`([^`]+\.py)`')

    # Expected artifacts per phase (canonical 0X-name/ paths)
    PHASE_ARTIFACTS = {
        1: ["01-requirements/SRS.md", "01-requirements/SPEC_TRACKING.md", "01-requirements/TRACEABILITY_MATRIX.md"],
        2: ["02-architecture/SAD.md"],
        3: [],  # P3 produces code+tests (no document artifacts) — drift detection N/A
        4: ["04-testing/TEST_PLAN.md", "04-testing/TEST_RESULTS.md"],
        5: ["05-verify/BASELINE.md", "05-verify/VERIFICATION_REPORT.md"],
        6: ["06-quality/QUALITY_REPORT.md"],
        7: ["07-risk/RISK_ASSESSMENT.md", "07-risk/RISK_REGISTER.md"],
        8: ["08-config/CONFIG_RECORDS.md", "08-config/RELEASE_CHECKLIST.md"],
    }

    def __init__(self, project_path: str):
        """Initialize with the project root path."""
        self.project_path = Path(project_path)
        self.state_path = self.project_path / ".methodology" / "state.json"

    def detect_sad_drift(self) -> DriftResult:
        """
        Detect drift between code files and SAD module mapping.

        Reads SAD.md FR-to-file mapping and checks if mapped files exist.
        """
        sad_path = self._find_file(["02-architecture/SAD.md"])
        if not sad_path:
            return DriftResult(
                drift_type="sad", has_drift=False, checked=0, drifted=0, score=1.0,
                drift_items=[DriftItem(
                    drift_type="sad", severity=DriftSeverity.LOW,
                    location="SAD.md", description="SAD.md not found; skipping drift check"
                )]
            )

        content = sad_path.read_text(encoding="utf-8", errors="replace")
        mappings = self.SAD_FR_PATTERN.findall(content)  # [(fr_num, file_path), ...]

        items = []
        checked = 0
        drifted = 0

        for fr_num, rel_path in mappings:
            checked += 1
            # Try both relative and with prefix
            candidates = [
                self.project_path / rel_path,
                self.project_path / "03-development" / "src" / rel_path,
                self.project_path / "app" / rel_path.split("/")[-1],
            ]
            exists = any(p.exists() for p in candidates)
            if not exists:
                drifted += 1
                items.append(DriftItem(
                    drift_type="sad",
                    severity=DriftSeverity.HIGH,
                    location=f"FR-{fr_num}",
                    description=f"SAD maps FR-{fr_num} to {rel_path} but file not found",
                    expected=rel_path,
                    actual=None,
                ))

        score = 1.0 - (drifted / max(checked, 1))
        return DriftResult(
            drift_type="sad",
            has_drift=drifted > 0,
            drift_items=items,
            checked=checked,
            drifted=drifted,
            score=score,
        )

    def detect_spec_drift(self) -> DriftResult:
        """
        Detect drift between implemented Python files and SRS FR list.

        Scans Python code for [FR-XX] docstring annotations and checks
        whether each SRS FR is covered by at least one implementation file.
        """
        srs_path = self._find_file(["01-requirements/SRS.md"])
        if not srs_path:
            return DriftResult(drift_type="spec", has_drift=False, score=1.0)

        srs_content = srs_path.read_text(encoding="utf-8", errors="replace")
        required_frs: Set[str] = {f"FR-{m.zfill(2)}" for m in self.FR_PATTERN.findall(srs_content)}

        # Scan Python files for [FR-XX] annotations
        implemented_frs: Set[str] = set()
        for py_file in self.project_path.rglob("*.py"):
            if "venv" in str(py_file) or "__pycache__" in str(py_file):
                continue
            try:
                text = py_file.read_text(encoding="utf-8", errors="replace")
                for m in self.FR_PATTERN.finditer(text):
                    implemented_frs.add(f"FR-{m.group(1).zfill(2)}")
            except Exception:  # pylint: disable=broad-exception-caught  # nosec B110
                pass

        missing = required_frs - implemented_frs
        checked = len(required_frs)
        drifted = len(missing)

        items = [
            DriftItem(
                drift_type="spec",
                severity=DriftSeverity.HIGH,
                location=fr,
                description=f"{fr} defined in SRS but not annotated in any implementation file",
                expected="[FR-XX] annotation in docstring",
                actual="not found",
            )
            for fr in sorted(missing)
        ]

        score = 1.0 - (drifted / max(checked, 1))
        return DriftResult(
            drift_type="spec",
            has_drift=drifted > 0,
            drift_items=items,
            checked=checked,
            drifted=drifted,
            score=score,
        )

    def detect_phase_drift(self) -> DriftResult:
        """
        Detect drift between state.json phase and expected artifact presence.

        Maps phase number to expected artifact files and checks existence.
        """
        if not self.state_path.exists():
            return DriftResult(drift_type="phase", has_drift=False, score=1.0,
                               drift_items=[DriftItem(
                                   drift_type="phase", severity=DriftSeverity.LOW,
                                   location="state.json", description="state.json not found; skipping"
                               )])

        try:
            state = json.loads(self.state_path.read_text(encoding="utf-8"))
        except Exception:  # pragma: no cover
            return DriftResult(drift_type="phase", has_drift=False, score=1.0)

        current_phase = state.get("current_phase", 0)
        items = []
        checked = 0
        drifted = 0

        for phase in range(1, current_phase):
            expected = self.PHASE_ARTIFACTS.get(phase, [])
            for artifact in expected:
                checked += 1
                path = self.project_path / artifact
                if not path.exists():
                    drifted += 1
                    items.append(DriftItem(
                        drift_type="phase",
                        severity=DriftSeverity.MEDIUM,
                        location=f"Phase {phase}",
                        description=f"Phase {phase} artifact missing: {artifact}",
                        expected=artifact,
                        actual="not found",
                    ))

        score = 1.0 - (drifted / max(checked, 1))
        return DriftResult(
            drift_type="phase",
            has_drift=drifted > 0,
            drift_items=items,
            checked=checked,
            drifted=drifted,
            score=score,
        )

    def detect_sab_drift(self) -> DriftResult:
        """
        Detect architecture drift between code structure and SAB baseline.

        Compares actual file tree and import dependencies against SAB layers,
        allowed_dependencies, and quality_targets from .methodology/SAB.json.

        Falls back to parsing SAD.md §6 SAB block if SAB.json is not available.
        """
        sab = self._load_sab_baseline()
        if not sab:
            return DriftResult(
                drift_type="sab", has_drift=False, checked=0, drifted=0, score=1.0,
                drift_items=[DriftItem(
                    drift_type="sab", severity=DriftSeverity.LOW,
                    location="SAB.json", description="SAB baseline not found; skipping SAB drift check"
                )]
            )

        layers: list[dict] = sab.get("layers", [])
        allowed_deps: dict[str, list[str]] = sab.get("dependencies", {})

        if not layers:
            return DriftResult(drift_type="sab", has_drift=False, score=1.0)

        items: list[DriftItem] = []
        checked = 0
        drifted = 0

        # ── Build SAB file registry ───────────────────────────────────────
        sab_files: dict[str, str] = {}  # relative_path → layer_name
        for layer in layers:
            layer_name = layer.get("name", "")
            for mod in layer.get("modules", []):
                sab_files[mod] = layer_name
                # Also register files inside directories (e.g. "core/quality_gate/" → all files)
                if mod.endswith("/"):
                    for py_file in self.project_path.rglob(f"{mod}*.py"):
                        rel = str(py_file.relative_to(self.project_path))
                        sab_files[rel] = layer_name

        checked += len(sab_files)

        # ── Check 1: SAB files missing from codebase ──────────────────────
        for rel_path, layer_name in sab_files.items():
            if not rel_path.endswith("/") and not (self.project_path / rel_path).exists():
                drifted += 1
                items.append(DriftItem(
                    drift_type="sab",
                    severity=DriftSeverity.MEDIUM,
                    location=f"SAB layer {layer_name}",
                    description=f"SAB declares {rel_path} but file not found in codebase",
                    expected=rel_path,
                    actual="not found",
                ))

        # ── Check 2: New Python files not in any SAB layer ────────────────
        sab_file_set = {f for f in sab_files if not f.endswith("/")}
        for py_file in self.project_path.rglob("*.py"):
            if "venv" in str(py_file) or "__pycache__" in str(py_file):
                continue
            if ".sessi-work" in str(py_file) or ".methodology" in str(py_file):
                continue
            rel = str(py_file.relative_to(self.project_path))
            if rel not in sab_file_set and not rel.startswith("tests/"):
                checked += 1
                drifted += 1
                items.append(DriftItem(
                    drift_type="sab",
                    severity=DriftSeverity.LOW,
                    location=rel,
                    description=f"New file {rel} not registered in any SAB layer",
                    expected="SAB layer assignment",
                    actual="unregistered",
                ))

        # ── Check 3: Import dependency violations ──────────────────────────
        layer_to_modules: dict[str, set[str]] = {}
        for layer in layers:
            layer_name = layer.get("name", "")
            mods: set[str] = set()
            for mod in layer.get("modules", []):
                # Normalize: strip .py and trailing / so matching works against dotted imports
                clean = mod.rstrip("/")
                if clean.endswith(".py"):
                    clean = clean[:-3]
                mods.add(clean)
            layer_to_modules[layer_name] = mods

        for py_file in self.project_path.rglob("*.py"):
            if "venv" in str(py_file) or "__pycache__" in str(py_file):
                continue
            if ".sessi-work" in str(py_file) or ".methodology" in str(py_file):
                continue

            rel = str(py_file.relative_to(self.project_path))
            source_layer = sab_files.get(rel)
            if source_layer is None:
                continue  # already flagged in Check 2

            allowed = set(allowed_deps.get(source_layer, []))
            try:
                py_text = py_file.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue

            for match in re.finditer(
                r'^\s*(?:from|import)\s+(\S+)', py_text, re.MULTILINE
            ):
                imported = match.group(1)
                target_layer = self._resolve_import_layer(imported, layer_to_modules)
                if target_layer and target_layer != source_layer and target_layer not in allowed:
                    checked += 1
                    drifted += 1
                    items.append(DriftItem(
                        drift_type="sab",
                        severity=DriftSeverity.CRITICAL,
                        location=f"{rel} (layer {source_layer})",
                        description=(
                            f"Architecture violation: {rel} imports {imported} "
                            f"(layer {target_layer}), but {source_layer} → {target_layer} "
                            f"is not an allowed dependency"
                        ),
                        expected=f"allowed deps: {sorted(allowed)}",
                        actual=f"imports {imported} (layer {target_layer})",
                    ))

        score = 1.0 - (drifted / max(checked, 1))
        return DriftResult(
            drift_type="sab",
            has_drift=drifted > 0,
            drift_items=items,
            checked=checked,
            drifted=drifted,
            score=score,
        )

    def _resolve_import_layer(self, import_path: str,
                              layer_to_modules: dict[str, set[str]]) -> Optional[str]:
        """Map an import path to a SAB layer name. Returns None if unmatched."""
        normalized = import_path.replace(".", "/")
        for layer_name, modules in layer_to_modules.items():
            for mod in modules:
                if normalized == mod or normalized.startswith(mod + "/"):
                    return layer_name
                # Also match dotted form (e.g. harness.harness_bridge against harness/harness_bridge)
                dotted_mod = mod.replace("/", ".")
                if import_path == dotted_mod or import_path.startswith(dotted_mod + "."):
                    return layer_name
        return None

    def _load_sab_baseline(self) -> dict:
        """Load SAB baseline from .methodology/SAB.json, falling back to SAD.md parse."""
        sab_json = self.project_path / ".methodology" / "SAB.json"
        if sab_json.exists():
            try:
                return json.loads(sab_json.read_text(encoding="utf-8"))
            except Exception:
                pass
        # Fallback: try parsing from SAD.md
        try:
            from scripts.generate_sab import parse_sad
            sad_path = self._find_file(["02-architecture/SAD.md"])
            if sad_path:
                return parse_sad(str(sad_path))
        except Exception:
            pass
        return {}

    def detect_all(self) -> Dict[str, DriftResult]:
        """Run all drift detectors and return a combined report."""
        sad = self.detect_sad_drift()
        spec = self.detect_spec_drift()
        phase = self.detect_phase_drift()
        sab = self.detect_sab_drift()
        return {"sad": sad, "spec": spec, "phase": phase, "sab": sab}

    def _find_file(self, candidates: List[str]) -> Optional[Path]:
        """Utility to find a file among multiple candidate relative paths."""
        for c in candidates:
            p = self.project_path / c
            if p.exists():
                return p
        return None
