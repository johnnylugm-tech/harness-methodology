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
import sys
import ast
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Set
from core.utils.project_layout import phase_artifacts, ProjectLayout


# ---------------------------------------------------------------------------
# SAB module path resolution (Bug #119 fix)
# ---------------------------------------------------------------------------
def read_package_dir(project_path: Path) -> Optional[str]:
    """Read [options] package_dir from setup.cfg to detect src/-layout projects.

    setup.cfg may declare ``package_dir =\n    =src`` to put the package
    source under ``src/<pkg>/``. SAB modules written in non-prefixed form
    (e.g. ``taskq.cli``) need to be matched against the actual file path
    ``src/taskq/cli.py``. Returns the package source dir (e.g. ``"src"``)
    or ``None`` if no src-layout is detected.

    Promoted to module level (Bug #119) so PhaseHooks can call it without
    instantiating a DriftDetector.
    """
    import configparser

    for candidate in (ProjectLayout(project_path).phase3_development_dir / "setup.cfg",
                      project_path / "setup.cfg"):
        if not candidate.exists():
            continue
        try:
            cp = configparser.ConfigParser()
            cp.read(str(candidate), encoding="utf-8")
            # A setup.cfg without `[options] package_dir` is inconclusive, not
            # a definitive "no src-layout" — the project may declare src-layout
            # a different way (e.g. `[tool:pytest] pythonpath=`) or not use
            # setuptools' package_dir convention at all. Fall through to the
            # pyproject.toml check and ultimately the ProjectLayout fallback
            # below instead of short-circuiting the whole function here.
            if cp.has_section("options") and cp.has_option("options", "package_dir"):
                raw = cp.get("options", "package_dir")
                for line in raw.splitlines():
                    line = line.strip()
                    if line.startswith("="):
                        val = line[1:].strip()
                        if val:
                            return val
        except Exception as exc:  # pylint: disable=broad-exception-caught  # nosec B110
            print(f"[WARN] drift_detector: setup.cfg src-layout probe failed for "
                  f"{candidate}: {exc}", file=sys.stderr)

    import re
    for candidate in (ProjectLayout(project_path).phase3_development_dir / "pyproject.toml",
                      project_path / "pyproject.toml"):
        if not candidate.exists():
            continue
        try:
            text = candidate.read_text(encoding="utf-8")
            m = re.search(r'where\s*=\s*\[\s*"([^"]+)"\s*\]', text)
            if m:
                return m.group(1)
        except Exception as exc:
            print(f"[WARN] drift_detector: pyproject.toml src-layout probe failed "
                  f"for {candidate}: {exc}", file=sys.stderr)

    # Fallback: config-file declaration (setuptools-style `[options] package_dir`
    # or PEP 621 `where=[...]`) is absent — a project declaring src-layout via
    # `[tool:pytest] pythonpath=` (or any other non-setuptools convention) would
    # otherwise never resolve. Reuse ProjectLayout.active_src_dir — the same
    # canonical, filesystem-existence-based abstraction phase_hooks.py,
    # phase_truth_verifier.py, phase_artifact_enforcer.py, and sab_amender.py
    # already rely on — instead of re-deriving src-layout detection here.
    # Resolve both sides before relative_to(): ProjectLayout always returns an
    # absolute active_src_dir, but callers (e.g. DriftDetector.__init__) may
    # pass a relative project_path (e.g. Path(".")) without resolving it —
    # relative_to() between an absolute and a relative path raises ValueError,
    # which would otherwise be silently swallowed below.
    try:
        resolved_project = project_path.resolve()
        active_src = ProjectLayout(resolved_project).active_src_dir
        if active_src.is_dir():
            return str(active_src.relative_to(resolved_project))
    except Exception as exc:  # pylint: disable=broad-exception-caught  # nosec B110
        print(f"[WARN] drift_detector: ProjectLayout src-layout fallback failed "
              f"for {project_path}: {exc}", file=sys.stderr)

    return None


def sab_module_to_path_variants(
    mod: str, pkg_dir: Optional[str] = None
) -> List[str]:
    """Expand a SAB `modules` entry into filesystem path candidates.

    A SAB module entry may be expressed in any of the following forms:
      - dotted notation:  "taskq.cli", "src.taskq.config"
      - slash / .py path: "taskq/cli.py", "src/taskq/config.py"
      - project-relative: "03-development/src/taskq/cli.py"
      - directory marker: "taskq/"  (caller skips via .endswith("/"))

    Returns a list of candidate paths to try in order. Both
    ``DriftDetector.detect_sab_drift`` and ``PhaseHooks.preflight_sab_check``
    use this helper so the two checks agree on what counts as "on disk".
    """
    if mod.endswith("/"):
        return [mod]

    candidates: List[str] = [mod]

    # If already a path (has "/" or ends with .py), return as-is.
    if "/" in mod or mod.endswith(".py"):
        return candidates

    # Dotted → path with .py suffix.
    dotted_path = mod.replace(".", "/") + ".py"
    candidates.append(dotted_path)

    # If a package dir is known (e.g. "src"), also try the prefixed form so
    # that SAB "taskq.cli" matches src/taskq/cli.py in src/-layout projects.
    # Guard against double-prefixing: a module may already self-declare its
    # src segment (mod="src.taskq.config" -> dotted_path="src/taskq/config.py")
    # — if dotted_path's first segment already equals pkg_dir's basename, the
    # caller's own "03-development/"-prefixed existence check (applied to the
    # unprefixed dotted_path candidate) already resolves it; prepending pkg_dir
    # here would instead produce a bogus doubled segment ("<pkg_dir>/src/...").
    if pkg_dir and not dotted_path.startswith(f"{pkg_dir}/"):
        pkg_dir_basename = pkg_dir.rsplit("/", 1)[-1]
        if dotted_path.split("/", 1)[0] != pkg_dir_basename:
            candidates.append(f"{pkg_dir}/{dotted_path}")

    # A dotted SAB entry may name a PACKAGE, not a leaf module (e.g.
    # "taskq.cli" referring to the taskq/cli/ subpackage, whose actual file
    # is taskq/cli/__init__.py) — distinct from "taskq.cli.cli" (the leaf
    # module taskq/cli/cli.py, a separate SAB entry). Without this, every
    # dotted SAB entry that names a package is false-flagged as missing.
    init_path = mod.replace(".", "/") + "/__init__.py"
    candidates.append(init_path)
    if pkg_dir and not init_path.startswith(f"{pkg_dir}/"):
        pkg_dir_basename = pkg_dir.rsplit("/", 1)[-1]
        if init_path.split("/", 1)[0] != pkg_dir_basename:
            candidates.append(f"{pkg_dir}/{init_path}")

    return candidates


# ---------------------------------------------------------------------------
# ASTDependencyScanner
# ---------------------------------------------------------------------------

class ASTDependencyScanner(ast.NodeVisitor):
    """AST visitor to extract imports with robust relative path resolution."""
    
    def __init__(self, current_file_rel: str):
        self.imports: Set[str] = set()
        self.current_file_rel = current_file_rel

    def visit_Import(self, node: ast.Import):
        for name in node.names:
            self.imports.add(name.name)
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom):
        module = node.module or ""
        if node.level > 0:
            # Resolve relative import based on current file location
            parts = Path(self.current_file_rel).parent.parts
            if parts and parts[0] == ".":
                parts = parts[1:]
            
            slice_len = len(parts) - (node.level - 1)
            if slice_len >= 0:
                base_parts = parts[:slice_len]
                base_module = ".".join(base_parts)
            else:
                base_module = ""
            
            if base_module:
                module = f"{base_module}.{module}" if module else base_module
        
        if module:
            self.imports.add(module)
            
        for name in node.names:
            if module:
                self.imports.add(f"{module}.{name.name}")
            else:
                self.imports.add(name.name)
        self.generic_visit(node)


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
    expected_in_phase: Optional[int] = None


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
                    "expected": i.expected,
                    "actual": i.actual,
                    "expected_in_phase": i.expected_in_phase,
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

    # (?<!N) — "NFR-06" contains the substring "FR-06"; without the
    # lookbehind every NFR table row parses as a phantom FR mapping
    # (2026-07-11: SAD.md NFR-06 row produced a HIGH "FR-06 → config.py
    # not found" drift for an FR that does not exist in the spec).
    FR_PATTERN = re.compile(r'(?<!N)FR-(\d+)')
    MODULE_PATTERN = re.compile(r'`([^`]+\.py)`')
    SAD_FR_PATTERN = re.compile(r'(?<!N)FR-(\d+)[^\n]*?`([^`]+\.py)`')

    # Expected artifacts per phase (canonical 0X-name/ paths)
    PHASE_ARTIFACTS = {
        1: ["01-requirements/SRS.md", "01-requirements/SPEC_TRACKING.md", "01-requirements/TRACEABILITY_MATRIX.md"],
        2: ["02-architecture/SAD.md"],
        3: [],  # P3 produces code+tests (no document artifacts) — drift detection N/A
        4: ["04-testing/TEST_PLAN.md", "04-testing/TEST_RESULTS.md"],
        5: ["05-verification/BASELINE.md", "05-verification/VERIFICATION_REPORT.md"],
        6: ["06-quality/QUALITY_REPORT.md"],
        7: phase_artifacts(7),
        8: ["08-config/CONFIG_RECORDS.md", "08-config/RELEASE_CHECKLIST.md"],
        9: ["09-maintenance/MAINTENANCE_LOG.md"],
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
        current_phase = None
        if self.state_path.exists():
            try:
                current_phase = json.loads(
                    self.state_path.read_text(encoding="utf-8")
                ).get("current_phase", 0)
            except Exception as exc:
                print(f"[WARN] drift_detector: state.json unreadable, "
                      f"current_phase stays unknown: {exc}", file=sys.stderr)
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

        # SAD.md's own FR-mapping table only commits to a basename (e.g.
        # `models.py`), not a full path — it never claims which subpackage a
        # module lives under. active_src_dir is resolved once (not per-mapping)
        # so a recursive basename search can find a file regardless of nesting
        # depth (src-layout: 03-development/src/taskq/core/models.py), matching
        # the precision level SAD.md itself uses instead of guessing a fixed
        # prefix depth.
        try:
            active_src_dir = ProjectLayout(self.project_path).active_src_dir
        except Exception as exc:  # pylint: disable=broad-exception-caught  # nosec B110
            print(f"[WARN] drift_detector: active_src_dir resolution failed, "
                  f"falling back to fixed-depth basename search: {exc}", file=sys.stderr)
            active_src_dir = None

        for fr_num, rel_path in mappings:
            checked += 1
            # Try relative path and common prefix variants.
            # "03-development" / rel_path handles projects where SAD declares
            # src/X.py but files live at 03-development/src/X.py.
            candidates = [
                self.project_path / rel_path,
                ProjectLayout(self.project_path).phase3_development_dir / rel_path,
                self.project_path / "app" / rel_path.split("/")[-1],
            ]
            exists = any(p.exists() for p in candidates)
            if not exists and active_src_dir and active_src_dir.is_dir():
                basename = rel_path.split("/")[-1]
                exists = next(active_src_dir.rglob(basename), None) is not None
            if not exists:
                drifted += 1
                items.append(DriftItem(
                    drift_type="sad",
                    severity=DriftSeverity.HIGH,
                    location=f"FR-{fr_num}",
                    description=f"SAD maps FR-{fr_num} to {rel_path} but file not found",
                    expected=rel_path,
                    actual=None,
                    expected_in_phase=3 if (current_phase is not None and current_phase < 3) else None,
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
        read_error_items: List[DriftItem] = []
        for py_file in self.project_path.rglob("*.py"):
            if "venv" in str(py_file) or "__pycache__" in str(py_file):
                continue
            try:
                text = py_file.read_text(encoding="utf-8", errors="replace")
                for m in self.FR_PATTERN.finditer(text):
                    implemented_frs.add(f"FR-{m.group(1).zfill(2)}")
            except (OSError, UnicodeDecodeError) as exc:
                # Surface per-file read errors instead of silently dropping
                # them — a dropped file can make an implemented FR look
                # missing with no diagnostic. Mirrors the M10 fix in
                # scripts/spec_logic_checker.py.
                read_error_items.append(DriftItem(
                    drift_type="spec",
                    severity=DriftSeverity.LOW,
                    location=str(py_file),
                    description=f"Failed to read file while scanning for FR annotations: {exc}",
                    expected="file readable",
                    actual="read error",
                ))

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
        ] + read_error_items

        score = 1.0 - (drifted / max(checked, 1))
        return DriftResult(
            drift_type="spec",
            has_drift=drifted > 0 or bool(read_error_items),
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

    def _read_package_dir(self) -> str | None:
        """Read package_dir from setup.cfg (delegates to module-level helper,
        Bug #119)."""
        return read_package_dir(self.project_path)

    def detect_sab_drift(self) -> DriftResult:
        """
        Detect architecture drift between code structure and SAB baseline.

        Compares actual file tree and import dependencies against SAB layers,
        allowed_dependencies, and quality_targets from .methodology/SAB.json.

        Falls back to parsing SAD.md §6 SAB block if SAB.json is not available.
        Skips entirely before Phase 3 — no implementation code exists to check against.
        """
        if self.state_path.exists():
            try:
                current_phase = json.loads(
                    self.state_path.read_text(encoding="utf-8")
                ).get("current_phase", 0)
                if current_phase < 3:
                    return DriftResult(
                        drift_type="sab", has_drift=False, checked=0, drifted=0, score=1.0,
                        drift_items=[DriftItem(
                            drift_type="sab", severity=DriftSeverity.LOW,
                            location="state.json",
                            description=(
                                f"SAB drift skipped at Phase {current_phase} "
                                "— implementation code not yet written (P3+)"
                            ),
                        )],
                    )
            except Exception as exc:  # pragma: no cover
                print(
                    f"[WARN] drift_detector: could not read current_phase from state.json: {exc}",
                    file=sys.stderr,
                )

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

        # Read package_dir from setup.cfg to handle src/-layout projects
        # (Bug #v2.11 fix: SAB uses "taskq.cli", file is at "src/taskq/cli.py")
        pkg_dir = self._read_package_dir()
        from core.quality_gate.sab_amender import (
            normalize_sab_module_to_dotted,
            sab_module_candidate,
        )

        # ── Build SAB file registry ───────────────────────────────────────
        sab_files: dict[str, str] = {}  # relative_path → layer_name
        # pkg_dir-prefixed aliases exist ONLY so Check 2 can match src-layout
        # file paths; Check 1 and the `checked` denominator must skip them,
        # otherwise every module is counted twice (a missing module produced
        # two drift items and an existing one two passes — both distortions).
        alias_keys: set[str] = set()
        for layer in layers:
            layer_name = layer.get("name", "")
            for mod in layer.get("modules", []):
                actual_mod = sab_module_candidate(mod)
                if not isinstance(actual_mod, str):
                    continue
                sab_files[actual_mod] = layer_name
                # Pre-register a pkg_dir-prefixed dotted key so Check 2
                # (unregistered files) can match src/-layout file paths.
                # Normalise pkg_dir to dot notation — otherwise a
                # slash-containing pkg_dir like "03-development/src"
                # produces a malformed dotted+slash hybrid that
                # sab_module_to_path_variants() returns as a literal
                # path instead of resolving.
                # Guard against double-prefixing (same collision class as
                # sab_module_to_path_variants): a module may already
                # self-declare its src segment (actual_mod="src.taskq.config"),
                # in which case prepending pkg_dir's own "...src" basename
                # again produces a bogus doubled segment.
                if pkg_dir and not actual_mod.endswith("/") and not actual_mod.endswith(".py"):
                    pkg_dir_basename = pkg_dir.rsplit("/", 1)[-1]
                    if actual_mod.split(".", 1)[0] != pkg_dir_basename:
                        norm_pkg = pkg_dir.replace("/", ".")
                        alias_key = f"{norm_pkg}.{actual_mod}"
                        if alias_key not in sab_files:
                            sab_files[alias_key] = layer_name
                            alias_keys.add(alias_key)
                # Also register files inside directories (e.g. "core/quality_gate/" → all files)
                if actual_mod.endswith("/"):
                    for py_file in self.project_path.rglob(f"{actual_mod}*.py"):
                        rel = str(py_file.relative_to(self.project_path))
                        sab_files[rel] = layer_name

        checked += len(sab_files) - len(alias_keys)

        # ── Check 1: SAB files missing from codebase ──────────────────────
        # SAB `modules` entries use Python dotted notation (e.g. "src.taskq.config",
        # "taskq.config"); filesystem uses path notation with slashes. Convert
        # dotted → path before checking existence (Bug #30 fix).
        # Bug #119: use shared sab_module_to_path_variants() so this check
        # agrees with PhaseHooks.preflight_sab_check.
        for rel_path, layer_name in sab_files.items():
            if rel_path in alias_keys:
                continue
            if not rel_path.endswith("/") and not re.match(r'^FR-\d+$', rel_path):
                path_variants = sab_module_to_path_variants(rel_path, pkg_dir)
                exists = False
                for candidate in path_variants:
                    if ((self.project_path / candidate).exists() or
                            (ProjectLayout(self.project_path).phase3_development_dir / candidate).exists()):
                        exists = True
                        break
                if not exists:
                    drifted += 1
                    items.append(DriftItem(
                        drift_type="sab",
                        severity=DriftSeverity.MEDIUM,
                        location=f"SAB layer {layer_name}",
                        description=(
                            f"SAB declares {rel_path} but file not found in codebase "
                            f"(tried {', '.join(path_variants)})"
                        ),
                        expected=rel_path,
                        actual="not found",
                    ))

        # ── Check 2: New Python files not in any SAB layer ────────────────
        sab_file_set = {f for f in sab_files if not f.endswith("/")}
        for py_file in self.project_path.rglob("*.py"):
            py_str = str(py_file)
            if "venv" in py_str or "__pycache__" in py_str:
                continue
            if ".sessi-work" in py_str or ".methodology" in py_str:
                continue
            # Exclude git worktrees and build artifacts — they are transient
            # copies of source files and must not inflate the drift count.
            if ".claude/worktrees" in py_str or "/worktrees/" in py_str:
                continue
            if py_str.endswith(".py") and "/build/lib/" in py_str:
                continue
            rel = str(py_file.relative_to(self.project_path))
            if rel.startswith("harness/"):
                continue
            if rel.startswith("archive/"):
                continue
            # Exempt auto-generated / standard wrapper files (v2.11 extension):
            # - __init__.py / __main__.py are package init / entry-point markers,
            #   not application modules tracked in SAB layers.
            # - Root-level wrapper files (e.g. harness_cli.py) are emitted by
            #   init-project and never belong to a project layer.
            if rel.endswith("__init__.py") or rel.endswith("__main__.py"):
                continue
            if "/" not in rel:
                continue
            # Normalize 03-development/ prefix so files at 03-development/src/X.py
            # match SAB entries declared as src/X.py. Also normalize dotted form
            # (src.X.py → src/X.py) to match dotted-module SAB entries (Bug #31 fix).
            _rel_norm = rel[len("03-development/"):] if rel.startswith("03-development/") else rel
            _rel_dotted = _rel_norm[:-3].replace("/", ".") if _rel_norm.endswith(".py") else None
            # Also strip the resolved pkg_dir's own segment (e.g. "src/") so a
            # file at 03-development/src/taskq/executor.py normalizes to
            # "taskq/executor.py" (dotted "taskq.executor"), matching SAB's
            # bare dotted entries — the inverse direction of the src-layout
            # normalization sab_module_to_path_variants() applies in Check 1.
            # Without this, every src-layout file's dotted form keeps a
            # literal "src." segment SAB entries never declare, so Check 2
            # false-flags every real file as unregistered.
            _pkg_stripped = _rel_norm
            if pkg_dir:
                _pkg_rel = pkg_dir[len("03-development/"):] if pkg_dir.startswith("03-development/") else pkg_dir
                if _pkg_rel and _rel_norm.startswith(f"{_pkg_rel}/"):
                    _pkg_stripped = _rel_norm[len(_pkg_rel) + 1:]
            _pkg_dotted = _pkg_stripped[:-3].replace("/", ".") if _pkg_stripped.endswith(".py") else None
            if (rel not in sab_file_set
                    and _rel_norm not in sab_file_set
                    and _pkg_stripped not in sab_file_set
                    and (_rel_dotted is None or _rel_dotted not in sab_file_set)
                    and (_pkg_dotted is None or _pkg_dotted not in sab_file_set)
                    and not rel.startswith("tests/")
                    and "/tests/" not in rel):
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
                # Normalize to dotted form so matching works against dotted
                # imports. 2026-07-15: the prior inline unwrap (dict-unwrap
                # via sab_module_candidate + manual rstrip("/")/.py-strip)
                # never stripped the src_dir/"src/" path prefix, so a
                # dict-shaped entry declaring `implemented_in:
                # "src/taskq/cli.py"` normalized to "src/taskq/cli" instead
                # of "taskq.cli" — _resolve_import_layer() then never
                # matched it against a real `import taskq.cli`, silently
                # skipping the architecture-violation check entirely for
                # every dict-shaped module (not a false BLOCK — a false
                # PASS with zero warning). normalize_sab_module_to_dotted()
                # is the same SSOT already used by SEC-R6's owner_module
                # cross-check and cli/gate_cmds.py's SAB-alignment gate.
                dotted = normalize_sab_module_to_dotted(mod)
                if dotted:
                    mods.add(dotted)
            layer_to_modules[layer_name] = mods

        for py_file in self.project_path.rglob("*.py"):
            if "venv" in str(py_file) or "__pycache__" in str(py_file):
                continue
            if ".sessi-work" in str(py_file) or ".methodology" in str(py_file):
                continue

            rel = str(py_file.relative_to(self.project_path))
            if rel.startswith("harness/"):
                continue
            if rel.startswith("archive/"):
                continue
            # 2026-07-15: `sab_files.get(rel)` used to look up the file's own
            # layer by exact string match — but sab_files' keys are raw SAB
            # declarations (dotted or path, with or without a src_dir prefix)
            # while `rel` is always a project-relative filesystem path, so an
            # exact match essentially never succeeded (dotted-only entries,
            # or dict-shaped entries whose implemented_in carries a
            # "03-development/src/" prefix, never matched — Check 3 was
            # silently a no-op for realistic src-layout projects). Resolve
            # the file's own dotted module path through the same
            # normalize_sab_module_to_dotted() + _resolve_import_layer() pair
            # already used for the *target* side of the import below — a
            # file's own module path and the modules it imports are the same
            # kind of thing (a project-relative Python location needing
            # dotted normalization), so both sides should resolve identically.
            dotted_rel = normalize_sab_module_to_dotted(rel)
            source_layer = (
                self._resolve_import_layer(dotted_rel, layer_to_modules)
                if dotted_rel else None
            )
            if source_layer is None:
                continue  # not registered in any SAB layer — already flagged in Check 2

            allowed = set(allowed_deps.get(source_layer, []))
            try:
                py_text = py_file.read_text(encoding="utf-8", errors="replace")
            except Exception as exc:
                print(f"[WARN] drift_detector: could not read {py_file}, "
                      f"excluded from dependency-layer check: {exc}", file=sys.stderr)
                continue

            scanner = ASTDependencyScanner(rel)
            imported_list = set()
            try:
                tree = ast.parse(py_text)
                scanner.visit(tree)
                imported_list = scanner.imports
            except Exception:
                # Fallback to regex if parsing fails (e.g. syntax error in middle of refactoring)
                for match in re.finditer(
                    r'^\s*(?:from|import)\s+(\S+)', py_text, re.MULTILINE
                ):
                    imported_list.add(match.group(1))

            for imported in imported_list:
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
        # Canonicalize BOTH sides to dotted form so modules stored as
        # "core.quality_gate" still match an `import_path` of "core/quality_gate/sab_parser".
        normalized = import_path.replace("/", ".")
        for layer_name, modules in layer_to_modules.items():
            for mod in modules:
                mod_norm = mod.replace("/", ".")
                # 1. Exact match
                if normalized == mod_norm:
                    return layer_name
                # 2. Parent-directory match (e.g. from core import quality_gate matches core.quality_gate.sab_parser)
                if mod_norm.startswith(normalized + "."):
                    return layer_name
                # 3. Child-object match (e.g. from core.quality_gate.sab_parser import SABSpec)
                if normalized.startswith(mod_norm + "."):
                    return layer_name
        return None


    def _load_sab_baseline(self) -> dict:
        """Load SAB baseline from .methodology/SAB.json, falling back to SAD.md parse."""
        sab_json = self.project_path / ".methodology" / "SAB.json"
        if sab_json.exists():
            try:
                return json.loads(sab_json.read_text(encoding="utf-8"))
            except Exception as exc:
                print(f"[WARN] drift_detector: SAB.json unreadable, falling back "
                      f"to SAD.md parse: {exc}", file=sys.stderr)
        # Fallback: try parsing from SAD.md
        try:
            from scripts.generate_sab import parse_sad
            sad_path = self._find_file(["02-architecture/SAD.md"])
            if sad_path:
                return parse_sad(str(sad_path))
        except Exception as exc:
            from core.degradation_ledger import record_degradation
            record_degradation(
                self.project_path, "drift_detector._load_sab_baseline",
                "both SAB.json and SAD.md §5 parse failed — drift detection runs "
                "against an EMPTY architecture baseline this call (every module "
                "will appear unregistered)",
                why=str(exc),
            )
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
