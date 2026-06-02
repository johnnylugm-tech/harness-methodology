"""
SAB Parser — extract Software Architecture Baseline from SAD.md §6.

The SAB block is delimited by <!-- SAB:START --> and <!-- SAB:END --> in SAD.md,
and contains a YAML code fence with the architecture baseline spec.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml


@dataclass
class SABSpec:
    version: str = "1.0"
    created_at: str = ""
    phase: int = 0
    project: str = ""
    layers: list[dict] = field(default_factory=list)
    allowed_dependencies: list[dict] = field(default_factory=list)
    quality_targets: dict = field(default_factory=dict)
    nfr_dimension_mapping: dict = field(default_factory=dict)
    nfr_traceability: dict = field(default_factory=dict)
    advisory_only: list = field(default_factory=list)         # NFR types with no gate dimension
    gate_score_overrides: dict = field(default_factory=dict)  # NFR-derived threshold floors
    fr_module_traceability: dict = field(default_factory=dict)
    architecture_constraints: list = field(default_factory=list)
    high_risk_modules: list = field(default_factory=list)

    @property
    def modules(self) -> list:
        """Flattened list of all module names across all layers."""
        result = []
        for layer in self.layers:
            result.extend(layer.get("modules", []))
        return result

    def to_dict(self) -> dict:
        # Build {layer_name: [dep_layer_name, ...]} from allowed_dependencies list
        dependencies: dict[str, list[str]] = {}
        for dep in self.allowed_dependencies:
            src = dep.get("from", "")
            dst = dep.get("to", "")
            if src:
                dependencies.setdefault(src, []).append(dst)

        layers_out = []
        for layer in self.layers:
            name = layer.get("name", "")
            layers_out.append({
                "name": name,
                "modules": layer.get("modules", []),
                "allowed_dependencies": dependencies.get(name, []),
            })

        return {
            "version": self.version,
            "created_at": self.created_at,
            "phase": self.phase,
            "project": self.project,
            "layers": layers_out,
            "dependencies": dependencies,
            "quality_targets": self.quality_targets,
            "nfr_dimension_mapping": self.nfr_dimension_mapping,
            "nfr_traceability": self.nfr_traceability,
            "advisory_only": self.advisory_only,
            "gate_score_overrides": self.gate_score_overrides,
            "fr_module_traceability": self.fr_module_traceability,
            "architecture_constraints": self.architecture_constraints,
            "high_risk_modules": self.high_risk_modules,
        }


# Canonical map from SAD.md nfr_traceability `type` values to ACTUAL harness gate
# dimension names (must exist in the gate config 14-dimension set — otherwise the NFR
# maps to a non-existent dimension and is silently un-enforced).
_NFR_TYPE_TO_DIM: dict[str, str] = {
    "performance":     "performance",             # native gate dimension
    "security":        "security",                # native gate dimension
    "maintainability": "readability",             # radon-mi maintainability index
    "reliability":     "error_handling",          # try/except coverage ≈ reliability
    "testability":     "test_assertion_quality",  # assertion quality ≈ testability
}

# NFR types with NO corresponding automated scoring tool. They are still recorded in
# nfr_traceability (context injection + human review) but are NOT mapped to any gate
# dimension — honestly surfaced as advisory_only rather than faking enforcement.
_NFR_ADVISORY_TYPES: frozenset[str] = frozenset(
    {"deployability", "scalability", "usability"}
)

# Standard gate-4 dimension thresholds — the floor an NFR-backed dimension must clear.
# Kept in sync with harness/gate_configs/gate4_p6_full.yaml (test enforces parity).
_GATE_DIMENSION_STANDARD: dict[str, float] = {
    "linting": 90, "type_safety": 85, "test_coverage": 80, "security": 80,
    "secrets_scanning": 100, "license_compliance": 100, "mutation_testing": 70,
    "architecture": 80, "readability": 80, "error_handling": 80,
    "documentation": 75, "performance": 75, "integration_coverage": 75,
    "test_assertion_quality": 70, "traceability": 100,
}

# Only an explicit "at least N" target (≥N / >=N) is read as a dimension-score floor.
# Free-form targets like "p95 < 3s" are intentionally NOT parsed (different semantics).
_NFR_TARGET_NUM_RE = re.compile(r"(?:≥|>=)\s*(\d+(?:\.\d+)?)")


def derive_gate_score_overrides(nfr_dim_mapping: dict, nfr_traceability: dict) -> dict:
    """(7) SAB enforcement: turn NFR-backed dimensions into gate_score_overrides
    (threshold floors, only-raise). An NFR mapped to a dimension means that dimension
    must clear at least its standard gate threshold — a da_waiver cannot drop it below.
    If the NFR target carries an explicit "≥N" floor, the stricter value wins.
    """
    overrides: dict = {}
    for nfr_id, dim in nfr_dim_mapping.items():
        floor = _GATE_DIMENSION_STANDARD.get(dim)
        if floor is None:
            continue
        v = nfr_traceability.get(nfr_id)
        if isinstance(v, dict):
            m = _NFR_TARGET_NUM_RE.search(str(v.get("target", "")))
            if m:
                floor = max(floor, float(m.group(1)))
        overrides[dim] = max(overrides.get(dim, 0.0), float(floor))
    return overrides


_SAB_BLOCK_RE = re.compile(
    r"<!--\s*SAB:START\s*-->(.*?)<!--\s*SAB:END\s*-->",
    re.DOTALL,
)
_CODE_FENCE_RE = re.compile(r"```(?:yaml|json)?\s*(.*?)```", re.DOTALL)


def extract_sab_from_sad(sad_path) -> Optional[SABSpec]:
    """
    Parse SAD.md and return a SABSpec from the <!-- SAB:START/END --> block.

    Returns None if no SAB block is found.
    Raises RuntimeError if the block exists but cannot be parsed.
    """
    sad_path = Path(sad_path)
    content = sad_path.read_text(encoding="utf-8")

    block_match = _SAB_BLOCK_RE.search(content)
    if not block_match:
        return None

    block = block_match.group(1)

    fence_match = _CODE_FENCE_RE.search(block)
    yaml_str = fence_match.group(1) if fence_match else block

    try:
        data = yaml.safe_load(yaml_str)
    except yaml.YAMLError as exc:
        raise RuntimeError(f"Failed to parse SAB YAML block in {sad_path}: {exc}") from exc

    if not data:
        return None

    sab_data = data.get("sab", data)

    raw_phase = sab_data.get("phase", 0)
    try:
        phase = int(raw_phase)
    except (ValueError, TypeError) as exc:
        raise RuntimeError(
            f"Invalid 'phase' in SAB block ({sad_path}): {raw_phase!r} — expected integer"
        ) from exc

    nfr_traceability = sab_data.get("nfr_traceability", {})
    nfr_dim_mapping = sab_data.get("nfr_dimension_mapping", {})

    # Auto-derive nfr_dimension_mapping from nfr_traceability when mapping is absent.
    # Explicit nfr_dimension_mapping in the SAB block always takes precedence.
    if not nfr_dim_mapping and nfr_traceability:
        nfr_dim_mapping = {
            nfr_id: _NFR_TYPE_TO_DIM[v.get("type", "").lower()]
            for nfr_id, v in nfr_traceability.items()
            if isinstance(v, dict) and v.get("type", "").lower() in _NFR_TYPE_TO_DIM
        }

    # NFR types with no scoring tool → advisory_only (honestly surfaced, not enforced).
    advisory_only = sorted({
        v.get("type", "").lower()
        for v in nfr_traceability.values()
        if isinstance(v, dict) and v.get("type", "").lower() in _NFR_ADVISORY_TYPES
    })

    # (7) NFR-backed dimensions → gate_score_overrides (threshold floors, applied by
    # harness_bridge.finalize_gate). An explicit block value takes precedence.
    gate_score_overrides = sab_data.get("gate_score_overrides") or \
        derive_gate_score_overrides(nfr_dim_mapping, nfr_traceability)

    return SABSpec(
        version=str(sab_data.get("version", "1.0")),
        created_at=str(sab_data.get("created_at", "")),
        phase=phase,
        project=str(sab_data.get("project", "")),
        layers=sab_data.get("layers", []),
        allowed_dependencies=sab_data.get("allowed_dependencies", []),
        quality_targets=sab_data.get("quality_targets", {}),
        nfr_dimension_mapping=nfr_dim_mapping,
        nfr_traceability=nfr_traceability,
        advisory_only=advisory_only,
        gate_score_overrides=gate_score_overrides,
        fr_module_traceability=sab_data.get("fr_module_traceability", {}),
        architecture_constraints=sab_data.get("architecture_constraints", []),
        high_risk_modules=sab_data.get("high_risk_modules", []),
    )
