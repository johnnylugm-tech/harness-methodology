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
from inspect import signature as _mutmut_signature
from typing import Annotated
from typing import Callable
from typing import ClassVar


MutantDict = Annotated[dict[str, Callable], "Mutant"]


def _mutmut_trampoline(orig, mutants, call_args, call_kwargs, self_arg = None):
    """Forward call to original or mutated function, depending on the environment"""
    import os
    mutant_under_test = os.environ['MUTANT_UNDER_TEST']
    if mutant_under_test == 'fail':
        from mutmut.__main__ import MutmutProgrammaticFailException
        raise MutmutProgrammaticFailException('Failed programmatically')      
    elif mutant_under_test == 'stats':
        from mutmut.__main__ import record_trampoline_hit
        record_trampoline_hit(orig.__module__ + '.' + orig.__name__)
        result = orig(*call_args, **call_kwargs)
        return result
    prefix = orig.__module__ + '.' + orig.__name__ + '__mutmut_'
    if not mutant_under_test.startswith(prefix):
        result = orig(*call_args, **call_kwargs)
        return result
    mutant_name = mutant_under_test.rpartition('.')[-1]
    if self_arg:
        # call to a class method where self is not bound
        result = mutants[mutant_name](self_arg, *call_args, **call_kwargs)
    else:
        result = mutants[mutant_name](*call_args, **call_kwargs)
    return result


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
    "test_assertion_quality": 70,
}

# Only an explicit "at least N" target (≥N / >=N) is read as a dimension-score floor.
# Free-form targets like "p95 < 3s" are intentionally NOT parsed (different semantics).
_NFR_TARGET_NUM_RE = re.compile(r"(?:≥|>=)\s*(\d+(?:\.\d+)?)")


def x_derive_gate_score_overrides__mutmut_orig(nfr_dim_mapping: dict, nfr_traceability: dict) -> dict:
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


def x_derive_gate_score_overrides__mutmut_1(nfr_dim_mapping: dict, nfr_traceability: dict) -> dict:
    """(7) SAB enforcement: turn NFR-backed dimensions into gate_score_overrides
    (threshold floors, only-raise). An NFR mapped to a dimension means that dimension
    must clear at least its standard gate threshold — a da_waiver cannot drop it below.
    If the NFR target carries an explicit "≥N" floor, the stricter value wins.
    """
    overrides: dict = None
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


def x_derive_gate_score_overrides__mutmut_2(nfr_dim_mapping: dict, nfr_traceability: dict) -> dict:
    """(7) SAB enforcement: turn NFR-backed dimensions into gate_score_overrides
    (threshold floors, only-raise). An NFR mapped to a dimension means that dimension
    must clear at least its standard gate threshold — a da_waiver cannot drop it below.
    If the NFR target carries an explicit "≥N" floor, the stricter value wins.
    """
    overrides: dict = {}
    for nfr_id, dim in nfr_dim_mapping.items():
        floor = None
        if floor is None:
            continue
        v = nfr_traceability.get(nfr_id)
        if isinstance(v, dict):
            m = _NFR_TARGET_NUM_RE.search(str(v.get("target", "")))
            if m:
                floor = max(floor, float(m.group(1)))
        overrides[dim] = max(overrides.get(dim, 0.0), float(floor))
    return overrides


def x_derive_gate_score_overrides__mutmut_3(nfr_dim_mapping: dict, nfr_traceability: dict) -> dict:
    """(7) SAB enforcement: turn NFR-backed dimensions into gate_score_overrides
    (threshold floors, only-raise). An NFR mapped to a dimension means that dimension
    must clear at least its standard gate threshold — a da_waiver cannot drop it below.
    If the NFR target carries an explicit "≥N" floor, the stricter value wins.
    """
    overrides: dict = {}
    for nfr_id, dim in nfr_dim_mapping.items():
        floor = _GATE_DIMENSION_STANDARD.get(None)
        if floor is None:
            continue
        v = nfr_traceability.get(nfr_id)
        if isinstance(v, dict):
            m = _NFR_TARGET_NUM_RE.search(str(v.get("target", "")))
            if m:
                floor = max(floor, float(m.group(1)))
        overrides[dim] = max(overrides.get(dim, 0.0), float(floor))
    return overrides


def x_derive_gate_score_overrides__mutmut_4(nfr_dim_mapping: dict, nfr_traceability: dict) -> dict:
    """(7) SAB enforcement: turn NFR-backed dimensions into gate_score_overrides
    (threshold floors, only-raise). An NFR mapped to a dimension means that dimension
    must clear at least its standard gate threshold — a da_waiver cannot drop it below.
    If the NFR target carries an explicit "≥N" floor, the stricter value wins.
    """
    overrides: dict = {}
    for nfr_id, dim in nfr_dim_mapping.items():
        floor = _GATE_DIMENSION_STANDARD.get(dim)
        if floor is not None:
            continue
        v = nfr_traceability.get(nfr_id)
        if isinstance(v, dict):
            m = _NFR_TARGET_NUM_RE.search(str(v.get("target", "")))
            if m:
                floor = max(floor, float(m.group(1)))
        overrides[dim] = max(overrides.get(dim, 0.0), float(floor))
    return overrides


def x_derive_gate_score_overrides__mutmut_5(nfr_dim_mapping: dict, nfr_traceability: dict) -> dict:
    """(7) SAB enforcement: turn NFR-backed dimensions into gate_score_overrides
    (threshold floors, only-raise). An NFR mapped to a dimension means that dimension
    must clear at least its standard gate threshold — a da_waiver cannot drop it below.
    If the NFR target carries an explicit "≥N" floor, the stricter value wins.
    """
    overrides: dict = {}
    for nfr_id, dim in nfr_dim_mapping.items():
        floor = _GATE_DIMENSION_STANDARD.get(dim)
        if floor is None:
            break
        v = nfr_traceability.get(nfr_id)
        if isinstance(v, dict):
            m = _NFR_TARGET_NUM_RE.search(str(v.get("target", "")))
            if m:
                floor = max(floor, float(m.group(1)))
        overrides[dim] = max(overrides.get(dim, 0.0), float(floor))
    return overrides


def x_derive_gate_score_overrides__mutmut_6(nfr_dim_mapping: dict, nfr_traceability: dict) -> dict:
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
        v = None
        if isinstance(v, dict):
            m = _NFR_TARGET_NUM_RE.search(str(v.get("target", "")))
            if m:
                floor = max(floor, float(m.group(1)))
        overrides[dim] = max(overrides.get(dim, 0.0), float(floor))
    return overrides


def x_derive_gate_score_overrides__mutmut_7(nfr_dim_mapping: dict, nfr_traceability: dict) -> dict:
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
        v = nfr_traceability.get(None)
        if isinstance(v, dict):
            m = _NFR_TARGET_NUM_RE.search(str(v.get("target", "")))
            if m:
                floor = max(floor, float(m.group(1)))
        overrides[dim] = max(overrides.get(dim, 0.0), float(floor))
    return overrides


def x_derive_gate_score_overrides__mutmut_8(nfr_dim_mapping: dict, nfr_traceability: dict) -> dict:
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
            m = None
            if m:
                floor = max(floor, float(m.group(1)))
        overrides[dim] = max(overrides.get(dim, 0.0), float(floor))
    return overrides


def x_derive_gate_score_overrides__mutmut_9(nfr_dim_mapping: dict, nfr_traceability: dict) -> dict:
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
            m = _NFR_TARGET_NUM_RE.search(None)
            if m:
                floor = max(floor, float(m.group(1)))
        overrides[dim] = max(overrides.get(dim, 0.0), float(floor))
    return overrides


def x_derive_gate_score_overrides__mutmut_10(nfr_dim_mapping: dict, nfr_traceability: dict) -> dict:
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
            m = _NFR_TARGET_NUM_RE.search(str(None))
            if m:
                floor = max(floor, float(m.group(1)))
        overrides[dim] = max(overrides.get(dim, 0.0), float(floor))
    return overrides


def x_derive_gate_score_overrides__mutmut_11(nfr_dim_mapping: dict, nfr_traceability: dict) -> dict:
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
            m = _NFR_TARGET_NUM_RE.search(str(v.get(None, "")))
            if m:
                floor = max(floor, float(m.group(1)))
        overrides[dim] = max(overrides.get(dim, 0.0), float(floor))
    return overrides


def x_derive_gate_score_overrides__mutmut_12(nfr_dim_mapping: dict, nfr_traceability: dict) -> dict:
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
            m = _NFR_TARGET_NUM_RE.search(str(v.get("target", None)))
            if m:
                floor = max(floor, float(m.group(1)))
        overrides[dim] = max(overrides.get(dim, 0.0), float(floor))
    return overrides


def x_derive_gate_score_overrides__mutmut_13(nfr_dim_mapping: dict, nfr_traceability: dict) -> dict:
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
            m = _NFR_TARGET_NUM_RE.search(str(v.get("")))
            if m:
                floor = max(floor, float(m.group(1)))
        overrides[dim] = max(overrides.get(dim, 0.0), float(floor))
    return overrides


def x_derive_gate_score_overrides__mutmut_14(nfr_dim_mapping: dict, nfr_traceability: dict) -> dict:
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
            m = _NFR_TARGET_NUM_RE.search(str(v.get("target", )))
            if m:
                floor = max(floor, float(m.group(1)))
        overrides[dim] = max(overrides.get(dim, 0.0), float(floor))
    return overrides


def x_derive_gate_score_overrides__mutmut_15(nfr_dim_mapping: dict, nfr_traceability: dict) -> dict:
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
            m = _NFR_TARGET_NUM_RE.search(str(v.get("XXtargetXX", "")))
            if m:
                floor = max(floor, float(m.group(1)))
        overrides[dim] = max(overrides.get(dim, 0.0), float(floor))
    return overrides


def x_derive_gate_score_overrides__mutmut_16(nfr_dim_mapping: dict, nfr_traceability: dict) -> dict:
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
            m = _NFR_TARGET_NUM_RE.search(str(v.get("TARGET", "")))
            if m:
                floor = max(floor, float(m.group(1)))
        overrides[dim] = max(overrides.get(dim, 0.0), float(floor))
    return overrides


def x_derive_gate_score_overrides__mutmut_17(nfr_dim_mapping: dict, nfr_traceability: dict) -> dict:
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
            m = _NFR_TARGET_NUM_RE.search(str(v.get("target", "XXXX")))
            if m:
                floor = max(floor, float(m.group(1)))
        overrides[dim] = max(overrides.get(dim, 0.0), float(floor))
    return overrides


def x_derive_gate_score_overrides__mutmut_18(nfr_dim_mapping: dict, nfr_traceability: dict) -> dict:
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
                floor = None
        overrides[dim] = max(overrides.get(dim, 0.0), float(floor))
    return overrides


def x_derive_gate_score_overrides__mutmut_19(nfr_dim_mapping: dict, nfr_traceability: dict) -> dict:
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
                floor = max(None, float(m.group(1)))
        overrides[dim] = max(overrides.get(dim, 0.0), float(floor))
    return overrides


def x_derive_gate_score_overrides__mutmut_20(nfr_dim_mapping: dict, nfr_traceability: dict) -> dict:
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
                floor = max(floor, None)
        overrides[dim] = max(overrides.get(dim, 0.0), float(floor))
    return overrides


def x_derive_gate_score_overrides__mutmut_21(nfr_dim_mapping: dict, nfr_traceability: dict) -> dict:
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
                floor = max(float(m.group(1)))
        overrides[dim] = max(overrides.get(dim, 0.0), float(floor))
    return overrides


def x_derive_gate_score_overrides__mutmut_22(nfr_dim_mapping: dict, nfr_traceability: dict) -> dict:
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
                floor = max(floor, )
        overrides[dim] = max(overrides.get(dim, 0.0), float(floor))
    return overrides


def x_derive_gate_score_overrides__mutmut_23(nfr_dim_mapping: dict, nfr_traceability: dict) -> dict:
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
                floor = max(floor, float(None))
        overrides[dim] = max(overrides.get(dim, 0.0), float(floor))
    return overrides


def x_derive_gate_score_overrides__mutmut_24(nfr_dim_mapping: dict, nfr_traceability: dict) -> dict:
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
                floor = max(floor, float(m.group(None)))
        overrides[dim] = max(overrides.get(dim, 0.0), float(floor))
    return overrides


def x_derive_gate_score_overrides__mutmut_25(nfr_dim_mapping: dict, nfr_traceability: dict) -> dict:
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
                floor = max(floor, float(m.group(2)))
        overrides[dim] = max(overrides.get(dim, 0.0), float(floor))
    return overrides


def x_derive_gate_score_overrides__mutmut_26(nfr_dim_mapping: dict, nfr_traceability: dict) -> dict:
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
        overrides[dim] = None
    return overrides


def x_derive_gate_score_overrides__mutmut_27(nfr_dim_mapping: dict, nfr_traceability: dict) -> dict:
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
        overrides[dim] = max(None, float(floor))
    return overrides


def x_derive_gate_score_overrides__mutmut_28(nfr_dim_mapping: dict, nfr_traceability: dict) -> dict:
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
        overrides[dim] = max(overrides.get(dim, 0.0), None)
    return overrides


def x_derive_gate_score_overrides__mutmut_29(nfr_dim_mapping: dict, nfr_traceability: dict) -> dict:
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
        overrides[dim] = max(float(floor))
    return overrides


def x_derive_gate_score_overrides__mutmut_30(nfr_dim_mapping: dict, nfr_traceability: dict) -> dict:
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
        overrides[dim] = max(overrides.get(dim, 0.0), )
    return overrides


def x_derive_gate_score_overrides__mutmut_31(nfr_dim_mapping: dict, nfr_traceability: dict) -> dict:
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
        overrides[dim] = max(overrides.get(None, 0.0), float(floor))
    return overrides


def x_derive_gate_score_overrides__mutmut_32(nfr_dim_mapping: dict, nfr_traceability: dict) -> dict:
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
        overrides[dim] = max(overrides.get(dim, None), float(floor))
    return overrides


def x_derive_gate_score_overrides__mutmut_33(nfr_dim_mapping: dict, nfr_traceability: dict) -> dict:
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
        overrides[dim] = max(overrides.get(0.0), float(floor))
    return overrides


def x_derive_gate_score_overrides__mutmut_34(nfr_dim_mapping: dict, nfr_traceability: dict) -> dict:
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
        overrides[dim] = max(overrides.get(dim, ), float(floor))
    return overrides


def x_derive_gate_score_overrides__mutmut_35(nfr_dim_mapping: dict, nfr_traceability: dict) -> dict:
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
        overrides[dim] = max(overrides.get(dim, 1.0), float(floor))
    return overrides


def x_derive_gate_score_overrides__mutmut_36(nfr_dim_mapping: dict, nfr_traceability: dict) -> dict:
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
        overrides[dim] = max(overrides.get(dim, 0.0), float(None))
    return overrides

x_derive_gate_score_overrides__mutmut_mutants : ClassVar[MutantDict] = {
'x_derive_gate_score_overrides__mutmut_1': x_derive_gate_score_overrides__mutmut_1, 
    'x_derive_gate_score_overrides__mutmut_2': x_derive_gate_score_overrides__mutmut_2, 
    'x_derive_gate_score_overrides__mutmut_3': x_derive_gate_score_overrides__mutmut_3, 
    'x_derive_gate_score_overrides__mutmut_4': x_derive_gate_score_overrides__mutmut_4, 
    'x_derive_gate_score_overrides__mutmut_5': x_derive_gate_score_overrides__mutmut_5, 
    'x_derive_gate_score_overrides__mutmut_6': x_derive_gate_score_overrides__mutmut_6, 
    'x_derive_gate_score_overrides__mutmut_7': x_derive_gate_score_overrides__mutmut_7, 
    'x_derive_gate_score_overrides__mutmut_8': x_derive_gate_score_overrides__mutmut_8, 
    'x_derive_gate_score_overrides__mutmut_9': x_derive_gate_score_overrides__mutmut_9, 
    'x_derive_gate_score_overrides__mutmut_10': x_derive_gate_score_overrides__mutmut_10, 
    'x_derive_gate_score_overrides__mutmut_11': x_derive_gate_score_overrides__mutmut_11, 
    'x_derive_gate_score_overrides__mutmut_12': x_derive_gate_score_overrides__mutmut_12, 
    'x_derive_gate_score_overrides__mutmut_13': x_derive_gate_score_overrides__mutmut_13, 
    'x_derive_gate_score_overrides__mutmut_14': x_derive_gate_score_overrides__mutmut_14, 
    'x_derive_gate_score_overrides__mutmut_15': x_derive_gate_score_overrides__mutmut_15, 
    'x_derive_gate_score_overrides__mutmut_16': x_derive_gate_score_overrides__mutmut_16, 
    'x_derive_gate_score_overrides__mutmut_17': x_derive_gate_score_overrides__mutmut_17, 
    'x_derive_gate_score_overrides__mutmut_18': x_derive_gate_score_overrides__mutmut_18, 
    'x_derive_gate_score_overrides__mutmut_19': x_derive_gate_score_overrides__mutmut_19, 
    'x_derive_gate_score_overrides__mutmut_20': x_derive_gate_score_overrides__mutmut_20, 
    'x_derive_gate_score_overrides__mutmut_21': x_derive_gate_score_overrides__mutmut_21, 
    'x_derive_gate_score_overrides__mutmut_22': x_derive_gate_score_overrides__mutmut_22, 
    'x_derive_gate_score_overrides__mutmut_23': x_derive_gate_score_overrides__mutmut_23, 
    'x_derive_gate_score_overrides__mutmut_24': x_derive_gate_score_overrides__mutmut_24, 
    'x_derive_gate_score_overrides__mutmut_25': x_derive_gate_score_overrides__mutmut_25, 
    'x_derive_gate_score_overrides__mutmut_26': x_derive_gate_score_overrides__mutmut_26, 
    'x_derive_gate_score_overrides__mutmut_27': x_derive_gate_score_overrides__mutmut_27, 
    'x_derive_gate_score_overrides__mutmut_28': x_derive_gate_score_overrides__mutmut_28, 
    'x_derive_gate_score_overrides__mutmut_29': x_derive_gate_score_overrides__mutmut_29, 
    'x_derive_gate_score_overrides__mutmut_30': x_derive_gate_score_overrides__mutmut_30, 
    'x_derive_gate_score_overrides__mutmut_31': x_derive_gate_score_overrides__mutmut_31, 
    'x_derive_gate_score_overrides__mutmut_32': x_derive_gate_score_overrides__mutmut_32, 
    'x_derive_gate_score_overrides__mutmut_33': x_derive_gate_score_overrides__mutmut_33, 
    'x_derive_gate_score_overrides__mutmut_34': x_derive_gate_score_overrides__mutmut_34, 
    'x_derive_gate_score_overrides__mutmut_35': x_derive_gate_score_overrides__mutmut_35, 
    'x_derive_gate_score_overrides__mutmut_36': x_derive_gate_score_overrides__mutmut_36
}

def derive_gate_score_overrides(*args, **kwargs):
    result = _mutmut_trampoline(x_derive_gate_score_overrides__mutmut_orig, x_derive_gate_score_overrides__mutmut_mutants, args, kwargs)
    return result 

derive_gate_score_overrides.__signature__ = _mutmut_signature(x_derive_gate_score_overrides__mutmut_orig)
x_derive_gate_score_overrides__mutmut_orig.__name__ = 'x_derive_gate_score_overrides'


_SAB_BLOCK_RE = re.compile(
    r"<!--\s*SAB:START\s*-->(.*?)<!--\s*SAB:END\s*-->",
    re.DOTALL,
)
_CODE_FENCE_RE = re.compile(r"```(?:yaml|json)?\s*(.*?)```", re.DOTALL)


def x_extract_sab_from_sad__mutmut_orig(sad_path) -> Optional[SABSpec]:
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


def x_extract_sab_from_sad__mutmut_1(sad_path) -> Optional[SABSpec]:
    """
    Parse SAD.md and return a SABSpec from the <!-- SAB:START/END --> block.

    Returns None if no SAB block is found.
    Raises RuntimeError if the block exists but cannot be parsed.
    """
    sad_path = None
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


def x_extract_sab_from_sad__mutmut_2(sad_path) -> Optional[SABSpec]:
    """
    Parse SAD.md and return a SABSpec from the <!-- SAB:START/END --> block.

    Returns None if no SAB block is found.
    Raises RuntimeError if the block exists but cannot be parsed.
    """
    sad_path = Path(None)
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


def x_extract_sab_from_sad__mutmut_3(sad_path) -> Optional[SABSpec]:
    """
    Parse SAD.md and return a SABSpec from the <!-- SAB:START/END --> block.

    Returns None if no SAB block is found.
    Raises RuntimeError if the block exists but cannot be parsed.
    """
    sad_path = Path(sad_path)
    content = None

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


def x_extract_sab_from_sad__mutmut_4(sad_path) -> Optional[SABSpec]:
    """
    Parse SAD.md and return a SABSpec from the <!-- SAB:START/END --> block.

    Returns None if no SAB block is found.
    Raises RuntimeError if the block exists but cannot be parsed.
    """
    sad_path = Path(sad_path)
    content = sad_path.read_text(encoding=None)

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


def x_extract_sab_from_sad__mutmut_5(sad_path) -> Optional[SABSpec]:
    """
    Parse SAD.md and return a SABSpec from the <!-- SAB:START/END --> block.

    Returns None if no SAB block is found.
    Raises RuntimeError if the block exists but cannot be parsed.
    """
    sad_path = Path(sad_path)
    content = sad_path.read_text(encoding="XXutf-8XX")

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


def x_extract_sab_from_sad__mutmut_6(sad_path) -> Optional[SABSpec]:
    """
    Parse SAD.md and return a SABSpec from the <!-- SAB:START/END --> block.

    Returns None if no SAB block is found.
    Raises RuntimeError if the block exists but cannot be parsed.
    """
    sad_path = Path(sad_path)
    content = sad_path.read_text(encoding="UTF-8")

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


def x_extract_sab_from_sad__mutmut_7(sad_path) -> Optional[SABSpec]:
    """
    Parse SAD.md and return a SABSpec from the <!-- SAB:START/END --> block.

    Returns None if no SAB block is found.
    Raises RuntimeError if the block exists but cannot be parsed.
    """
    sad_path = Path(sad_path)
    content = sad_path.read_text(encoding="utf-8")

    block_match = None
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


def x_extract_sab_from_sad__mutmut_8(sad_path) -> Optional[SABSpec]:
    """
    Parse SAD.md and return a SABSpec from the <!-- SAB:START/END --> block.

    Returns None if no SAB block is found.
    Raises RuntimeError if the block exists but cannot be parsed.
    """
    sad_path = Path(sad_path)
    content = sad_path.read_text(encoding="utf-8")

    block_match = _SAB_BLOCK_RE.search(None)
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


def x_extract_sab_from_sad__mutmut_9(sad_path) -> Optional[SABSpec]:
    """
    Parse SAD.md and return a SABSpec from the <!-- SAB:START/END --> block.

    Returns None if no SAB block is found.
    Raises RuntimeError if the block exists but cannot be parsed.
    """
    sad_path = Path(sad_path)
    content = sad_path.read_text(encoding="utf-8")

    block_match = _SAB_BLOCK_RE.search(content)
    if block_match:
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


def x_extract_sab_from_sad__mutmut_10(sad_path) -> Optional[SABSpec]:
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

    block = None

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


def x_extract_sab_from_sad__mutmut_11(sad_path) -> Optional[SABSpec]:
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

    block = block_match.group(None)

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


def x_extract_sab_from_sad__mutmut_12(sad_path) -> Optional[SABSpec]:
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

    block = block_match.group(2)

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


def x_extract_sab_from_sad__mutmut_13(sad_path) -> Optional[SABSpec]:
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

    fence_match = None
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


def x_extract_sab_from_sad__mutmut_14(sad_path) -> Optional[SABSpec]:
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

    fence_match = _CODE_FENCE_RE.search(None)
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


def x_extract_sab_from_sad__mutmut_15(sad_path) -> Optional[SABSpec]:
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
    yaml_str = None

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


def x_extract_sab_from_sad__mutmut_16(sad_path) -> Optional[SABSpec]:
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
    yaml_str = fence_match.group(None) if fence_match else block

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


def x_extract_sab_from_sad__mutmut_17(sad_path) -> Optional[SABSpec]:
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
    yaml_str = fence_match.group(2) if fence_match else block

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


def x_extract_sab_from_sad__mutmut_18(sad_path) -> Optional[SABSpec]:
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
        data = None
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


def x_extract_sab_from_sad__mutmut_19(sad_path) -> Optional[SABSpec]:
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
        data = yaml.safe_load(None)
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


def x_extract_sab_from_sad__mutmut_20(sad_path) -> Optional[SABSpec]:
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
        raise RuntimeError(None) from exc

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


def x_extract_sab_from_sad__mutmut_21(sad_path) -> Optional[SABSpec]:
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

    if data:
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


def x_extract_sab_from_sad__mutmut_22(sad_path) -> Optional[SABSpec]:
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

    sab_data = None

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


def x_extract_sab_from_sad__mutmut_23(sad_path) -> Optional[SABSpec]:
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

    sab_data = data.get(None, data)

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


def x_extract_sab_from_sad__mutmut_24(sad_path) -> Optional[SABSpec]:
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

    sab_data = data.get("sab", None)

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


def x_extract_sab_from_sad__mutmut_25(sad_path) -> Optional[SABSpec]:
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

    sab_data = data.get(data)

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


def x_extract_sab_from_sad__mutmut_26(sad_path) -> Optional[SABSpec]:
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

    sab_data = data.get("sab", )

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


def x_extract_sab_from_sad__mutmut_27(sad_path) -> Optional[SABSpec]:
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

    sab_data = data.get("XXsabXX", data)

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


def x_extract_sab_from_sad__mutmut_28(sad_path) -> Optional[SABSpec]:
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

    sab_data = data.get("SAB", data)

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


def x_extract_sab_from_sad__mutmut_29(sad_path) -> Optional[SABSpec]:
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

    raw_phase = None
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


def x_extract_sab_from_sad__mutmut_30(sad_path) -> Optional[SABSpec]:
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

    raw_phase = sab_data.get(None, 0)
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


def x_extract_sab_from_sad__mutmut_31(sad_path) -> Optional[SABSpec]:
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

    raw_phase = sab_data.get("phase", None)
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


def x_extract_sab_from_sad__mutmut_32(sad_path) -> Optional[SABSpec]:
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

    raw_phase = sab_data.get(0)
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


def x_extract_sab_from_sad__mutmut_33(sad_path) -> Optional[SABSpec]:
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

    raw_phase = sab_data.get("phase", )
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


def x_extract_sab_from_sad__mutmut_34(sad_path) -> Optional[SABSpec]:
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

    raw_phase = sab_data.get("XXphaseXX", 0)
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


def x_extract_sab_from_sad__mutmut_35(sad_path) -> Optional[SABSpec]:
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

    raw_phase = sab_data.get("PHASE", 0)
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


def x_extract_sab_from_sad__mutmut_36(sad_path) -> Optional[SABSpec]:
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

    raw_phase = sab_data.get("phase", 1)
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


def x_extract_sab_from_sad__mutmut_37(sad_path) -> Optional[SABSpec]:
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
        phase = None
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


def x_extract_sab_from_sad__mutmut_38(sad_path) -> Optional[SABSpec]:
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
        phase = int(None)
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


def x_extract_sab_from_sad__mutmut_39(sad_path) -> Optional[SABSpec]:
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
            None
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


def x_extract_sab_from_sad__mutmut_40(sad_path) -> Optional[SABSpec]:
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

    nfr_traceability = None
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


def x_extract_sab_from_sad__mutmut_41(sad_path) -> Optional[SABSpec]:
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

    nfr_traceability = sab_data.get(None, {})
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


def x_extract_sab_from_sad__mutmut_42(sad_path) -> Optional[SABSpec]:
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

    nfr_traceability = sab_data.get("nfr_traceability", None)
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


def x_extract_sab_from_sad__mutmut_43(sad_path) -> Optional[SABSpec]:
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

    nfr_traceability = sab_data.get({})
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


def x_extract_sab_from_sad__mutmut_44(sad_path) -> Optional[SABSpec]:
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

    nfr_traceability = sab_data.get("nfr_traceability", )
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


def x_extract_sab_from_sad__mutmut_45(sad_path) -> Optional[SABSpec]:
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

    nfr_traceability = sab_data.get("XXnfr_traceabilityXX", {})
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


def x_extract_sab_from_sad__mutmut_46(sad_path) -> Optional[SABSpec]:
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

    nfr_traceability = sab_data.get("NFR_TRACEABILITY", {})
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


def x_extract_sab_from_sad__mutmut_47(sad_path) -> Optional[SABSpec]:
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
    nfr_dim_mapping = None

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


def x_extract_sab_from_sad__mutmut_48(sad_path) -> Optional[SABSpec]:
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
    nfr_dim_mapping = sab_data.get(None, {})

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


def x_extract_sab_from_sad__mutmut_49(sad_path) -> Optional[SABSpec]:
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
    nfr_dim_mapping = sab_data.get("nfr_dimension_mapping", None)

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


def x_extract_sab_from_sad__mutmut_50(sad_path) -> Optional[SABSpec]:
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
    nfr_dim_mapping = sab_data.get({})

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


def x_extract_sab_from_sad__mutmut_51(sad_path) -> Optional[SABSpec]:
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
    nfr_dim_mapping = sab_data.get("nfr_dimension_mapping", )

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


def x_extract_sab_from_sad__mutmut_52(sad_path) -> Optional[SABSpec]:
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
    nfr_dim_mapping = sab_data.get("XXnfr_dimension_mappingXX", {})

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


def x_extract_sab_from_sad__mutmut_53(sad_path) -> Optional[SABSpec]:
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
    nfr_dim_mapping = sab_data.get("NFR_DIMENSION_MAPPING", {})

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


def x_extract_sab_from_sad__mutmut_54(sad_path) -> Optional[SABSpec]:
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
    if not nfr_dim_mapping or nfr_traceability:
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


def x_extract_sab_from_sad__mutmut_55(sad_path) -> Optional[SABSpec]:
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
    if nfr_dim_mapping and nfr_traceability:
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


def x_extract_sab_from_sad__mutmut_56(sad_path) -> Optional[SABSpec]:
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
        nfr_dim_mapping = None

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


def x_extract_sab_from_sad__mutmut_57(sad_path) -> Optional[SABSpec]:
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
            nfr_id: _NFR_TYPE_TO_DIM[v.get("type", "").upper()]
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


def x_extract_sab_from_sad__mutmut_58(sad_path) -> Optional[SABSpec]:
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
            nfr_id: _NFR_TYPE_TO_DIM[v.get(None, "").lower()]
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


def x_extract_sab_from_sad__mutmut_59(sad_path) -> Optional[SABSpec]:
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
            nfr_id: _NFR_TYPE_TO_DIM[v.get("type", None).lower()]
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


def x_extract_sab_from_sad__mutmut_60(sad_path) -> Optional[SABSpec]:
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
            nfr_id: _NFR_TYPE_TO_DIM[v.get("").lower()]
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


def x_extract_sab_from_sad__mutmut_61(sad_path) -> Optional[SABSpec]:
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
            nfr_id: _NFR_TYPE_TO_DIM[v.get("type", ).lower()]
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


def x_extract_sab_from_sad__mutmut_62(sad_path) -> Optional[SABSpec]:
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
            nfr_id: _NFR_TYPE_TO_DIM[v.get("XXtypeXX", "").lower()]
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


def x_extract_sab_from_sad__mutmut_63(sad_path) -> Optional[SABSpec]:
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
            nfr_id: _NFR_TYPE_TO_DIM[v.get("TYPE", "").lower()]
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


def x_extract_sab_from_sad__mutmut_64(sad_path) -> Optional[SABSpec]:
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
            nfr_id: _NFR_TYPE_TO_DIM[v.get("type", "XXXX").lower()]
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


def x_extract_sab_from_sad__mutmut_65(sad_path) -> Optional[SABSpec]:
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
            if isinstance(v, dict) or v.get("type", "").lower() in _NFR_TYPE_TO_DIM
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


def x_extract_sab_from_sad__mutmut_66(sad_path) -> Optional[SABSpec]:
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
            if isinstance(v, dict) and v.get("type", "").upper() in _NFR_TYPE_TO_DIM
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


def x_extract_sab_from_sad__mutmut_67(sad_path) -> Optional[SABSpec]:
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
            if isinstance(v, dict) and v.get(None, "").lower() in _NFR_TYPE_TO_DIM
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


def x_extract_sab_from_sad__mutmut_68(sad_path) -> Optional[SABSpec]:
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
            if isinstance(v, dict) and v.get("type", None).lower() in _NFR_TYPE_TO_DIM
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


def x_extract_sab_from_sad__mutmut_69(sad_path) -> Optional[SABSpec]:
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
            if isinstance(v, dict) and v.get("").lower() in _NFR_TYPE_TO_DIM
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


def x_extract_sab_from_sad__mutmut_70(sad_path) -> Optional[SABSpec]:
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
            if isinstance(v, dict) and v.get("type", ).lower() in _NFR_TYPE_TO_DIM
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


def x_extract_sab_from_sad__mutmut_71(sad_path) -> Optional[SABSpec]:
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
            if isinstance(v, dict) and v.get("XXtypeXX", "").lower() in _NFR_TYPE_TO_DIM
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


def x_extract_sab_from_sad__mutmut_72(sad_path) -> Optional[SABSpec]:
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
            if isinstance(v, dict) and v.get("TYPE", "").lower() in _NFR_TYPE_TO_DIM
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


def x_extract_sab_from_sad__mutmut_73(sad_path) -> Optional[SABSpec]:
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
            if isinstance(v, dict) and v.get("type", "XXXX").lower() in _NFR_TYPE_TO_DIM
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


def x_extract_sab_from_sad__mutmut_74(sad_path) -> Optional[SABSpec]:
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
            if isinstance(v, dict) and v.get("type", "").lower() not in _NFR_TYPE_TO_DIM
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


def x_extract_sab_from_sad__mutmut_75(sad_path) -> Optional[SABSpec]:
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
    advisory_only = None

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


def x_extract_sab_from_sad__mutmut_76(sad_path) -> Optional[SABSpec]:
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
    advisory_only = sorted(None)

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


def x_extract_sab_from_sad__mutmut_77(sad_path) -> Optional[SABSpec]:
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
        v.get("type", "").upper()
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


def x_extract_sab_from_sad__mutmut_78(sad_path) -> Optional[SABSpec]:
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
        v.get(None, "").lower()
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


def x_extract_sab_from_sad__mutmut_79(sad_path) -> Optional[SABSpec]:
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
        v.get("type", None).lower()
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


def x_extract_sab_from_sad__mutmut_80(sad_path) -> Optional[SABSpec]:
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
        v.get("").lower()
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


def x_extract_sab_from_sad__mutmut_81(sad_path) -> Optional[SABSpec]:
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
        v.get("type", ).lower()
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


def x_extract_sab_from_sad__mutmut_82(sad_path) -> Optional[SABSpec]:
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
        v.get("XXtypeXX", "").lower()
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


def x_extract_sab_from_sad__mutmut_83(sad_path) -> Optional[SABSpec]:
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
        v.get("TYPE", "").lower()
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


def x_extract_sab_from_sad__mutmut_84(sad_path) -> Optional[SABSpec]:
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
        v.get("type", "XXXX").lower()
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


def x_extract_sab_from_sad__mutmut_85(sad_path) -> Optional[SABSpec]:
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
        if isinstance(v, dict) or v.get("type", "").lower() in _NFR_ADVISORY_TYPES
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


def x_extract_sab_from_sad__mutmut_86(sad_path) -> Optional[SABSpec]:
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
        if isinstance(v, dict) and v.get("type", "").upper() in _NFR_ADVISORY_TYPES
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


def x_extract_sab_from_sad__mutmut_87(sad_path) -> Optional[SABSpec]:
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
        if isinstance(v, dict) and v.get(None, "").lower() in _NFR_ADVISORY_TYPES
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


def x_extract_sab_from_sad__mutmut_88(sad_path) -> Optional[SABSpec]:
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
        if isinstance(v, dict) and v.get("type", None).lower() in _NFR_ADVISORY_TYPES
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


def x_extract_sab_from_sad__mutmut_89(sad_path) -> Optional[SABSpec]:
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
        if isinstance(v, dict) and v.get("").lower() in _NFR_ADVISORY_TYPES
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


def x_extract_sab_from_sad__mutmut_90(sad_path) -> Optional[SABSpec]:
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
        if isinstance(v, dict) and v.get("type", ).lower() in _NFR_ADVISORY_TYPES
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


def x_extract_sab_from_sad__mutmut_91(sad_path) -> Optional[SABSpec]:
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
        if isinstance(v, dict) and v.get("XXtypeXX", "").lower() in _NFR_ADVISORY_TYPES
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


def x_extract_sab_from_sad__mutmut_92(sad_path) -> Optional[SABSpec]:
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
        if isinstance(v, dict) and v.get("TYPE", "").lower() in _NFR_ADVISORY_TYPES
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


def x_extract_sab_from_sad__mutmut_93(sad_path) -> Optional[SABSpec]:
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
        if isinstance(v, dict) and v.get("type", "XXXX").lower() in _NFR_ADVISORY_TYPES
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


def x_extract_sab_from_sad__mutmut_94(sad_path) -> Optional[SABSpec]:
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
        if isinstance(v, dict) and v.get("type", "").lower() not in _NFR_ADVISORY_TYPES
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


def x_extract_sab_from_sad__mutmut_95(sad_path) -> Optional[SABSpec]:
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
    gate_score_overrides = None

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


def x_extract_sab_from_sad__mutmut_96(sad_path) -> Optional[SABSpec]:
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
    gate_score_overrides = sab_data.get("gate_score_overrides") and derive_gate_score_overrides(nfr_dim_mapping, nfr_traceability)

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


def x_extract_sab_from_sad__mutmut_97(sad_path) -> Optional[SABSpec]:
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
    gate_score_overrides = sab_data.get(None) or \
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


def x_extract_sab_from_sad__mutmut_98(sad_path) -> Optional[SABSpec]:
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
    gate_score_overrides = sab_data.get("XXgate_score_overridesXX") or \
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


def x_extract_sab_from_sad__mutmut_99(sad_path) -> Optional[SABSpec]:
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
    gate_score_overrides = sab_data.get("GATE_SCORE_OVERRIDES") or \
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


def x_extract_sab_from_sad__mutmut_100(sad_path) -> Optional[SABSpec]:
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
        derive_gate_score_overrides(None, nfr_traceability)

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


def x_extract_sab_from_sad__mutmut_101(sad_path) -> Optional[SABSpec]:
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
        derive_gate_score_overrides(nfr_dim_mapping, None)

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


def x_extract_sab_from_sad__mutmut_102(sad_path) -> Optional[SABSpec]:
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
        derive_gate_score_overrides(nfr_traceability)

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


def x_extract_sab_from_sad__mutmut_103(sad_path) -> Optional[SABSpec]:
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
        derive_gate_score_overrides(nfr_dim_mapping, )

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


def x_extract_sab_from_sad__mutmut_104(sad_path) -> Optional[SABSpec]:
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
        version=None,
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


def x_extract_sab_from_sad__mutmut_105(sad_path) -> Optional[SABSpec]:
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
        created_at=None,
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


def x_extract_sab_from_sad__mutmut_106(sad_path) -> Optional[SABSpec]:
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
        phase=None,
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


def x_extract_sab_from_sad__mutmut_107(sad_path) -> Optional[SABSpec]:
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
        project=None,
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


def x_extract_sab_from_sad__mutmut_108(sad_path) -> Optional[SABSpec]:
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
        layers=None,
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


def x_extract_sab_from_sad__mutmut_109(sad_path) -> Optional[SABSpec]:
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
        allowed_dependencies=None,
        quality_targets=sab_data.get("quality_targets", {}),
        nfr_dimension_mapping=nfr_dim_mapping,
        nfr_traceability=nfr_traceability,
        advisory_only=advisory_only,
        gate_score_overrides=gate_score_overrides,
        fr_module_traceability=sab_data.get("fr_module_traceability", {}),
        architecture_constraints=sab_data.get("architecture_constraints", []),
        high_risk_modules=sab_data.get("high_risk_modules", []),
    )


def x_extract_sab_from_sad__mutmut_110(sad_path) -> Optional[SABSpec]:
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
        quality_targets=None,
        nfr_dimension_mapping=nfr_dim_mapping,
        nfr_traceability=nfr_traceability,
        advisory_only=advisory_only,
        gate_score_overrides=gate_score_overrides,
        fr_module_traceability=sab_data.get("fr_module_traceability", {}),
        architecture_constraints=sab_data.get("architecture_constraints", []),
        high_risk_modules=sab_data.get("high_risk_modules", []),
    )


def x_extract_sab_from_sad__mutmut_111(sad_path) -> Optional[SABSpec]:
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
        nfr_dimension_mapping=None,
        nfr_traceability=nfr_traceability,
        advisory_only=advisory_only,
        gate_score_overrides=gate_score_overrides,
        fr_module_traceability=sab_data.get("fr_module_traceability", {}),
        architecture_constraints=sab_data.get("architecture_constraints", []),
        high_risk_modules=sab_data.get("high_risk_modules", []),
    )


def x_extract_sab_from_sad__mutmut_112(sad_path) -> Optional[SABSpec]:
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
        nfr_traceability=None,
        advisory_only=advisory_only,
        gate_score_overrides=gate_score_overrides,
        fr_module_traceability=sab_data.get("fr_module_traceability", {}),
        architecture_constraints=sab_data.get("architecture_constraints", []),
        high_risk_modules=sab_data.get("high_risk_modules", []),
    )


def x_extract_sab_from_sad__mutmut_113(sad_path) -> Optional[SABSpec]:
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
        advisory_only=None,
        gate_score_overrides=gate_score_overrides,
        fr_module_traceability=sab_data.get("fr_module_traceability", {}),
        architecture_constraints=sab_data.get("architecture_constraints", []),
        high_risk_modules=sab_data.get("high_risk_modules", []),
    )


def x_extract_sab_from_sad__mutmut_114(sad_path) -> Optional[SABSpec]:
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
        gate_score_overrides=None,
        fr_module_traceability=sab_data.get("fr_module_traceability", {}),
        architecture_constraints=sab_data.get("architecture_constraints", []),
        high_risk_modules=sab_data.get("high_risk_modules", []),
    )


def x_extract_sab_from_sad__mutmut_115(sad_path) -> Optional[SABSpec]:
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
        fr_module_traceability=None,
        architecture_constraints=sab_data.get("architecture_constraints", []),
        high_risk_modules=sab_data.get("high_risk_modules", []),
    )


def x_extract_sab_from_sad__mutmut_116(sad_path) -> Optional[SABSpec]:
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
        architecture_constraints=None,
        high_risk_modules=sab_data.get("high_risk_modules", []),
    )


def x_extract_sab_from_sad__mutmut_117(sad_path) -> Optional[SABSpec]:
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
        high_risk_modules=None,
    )


def x_extract_sab_from_sad__mutmut_118(sad_path) -> Optional[SABSpec]:
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


def x_extract_sab_from_sad__mutmut_119(sad_path) -> Optional[SABSpec]:
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


def x_extract_sab_from_sad__mutmut_120(sad_path) -> Optional[SABSpec]:
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


def x_extract_sab_from_sad__mutmut_121(sad_path) -> Optional[SABSpec]:
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


def x_extract_sab_from_sad__mutmut_122(sad_path) -> Optional[SABSpec]:
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


def x_extract_sab_from_sad__mutmut_123(sad_path) -> Optional[SABSpec]:
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
        quality_targets=sab_data.get("quality_targets", {}),
        nfr_dimension_mapping=nfr_dim_mapping,
        nfr_traceability=nfr_traceability,
        advisory_only=advisory_only,
        gate_score_overrides=gate_score_overrides,
        fr_module_traceability=sab_data.get("fr_module_traceability", {}),
        architecture_constraints=sab_data.get("architecture_constraints", []),
        high_risk_modules=sab_data.get("high_risk_modules", []),
    )


def x_extract_sab_from_sad__mutmut_124(sad_path) -> Optional[SABSpec]:
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
        nfr_dimension_mapping=nfr_dim_mapping,
        nfr_traceability=nfr_traceability,
        advisory_only=advisory_only,
        gate_score_overrides=gate_score_overrides,
        fr_module_traceability=sab_data.get("fr_module_traceability", {}),
        architecture_constraints=sab_data.get("architecture_constraints", []),
        high_risk_modules=sab_data.get("high_risk_modules", []),
    )


def x_extract_sab_from_sad__mutmut_125(sad_path) -> Optional[SABSpec]:
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
        nfr_traceability=nfr_traceability,
        advisory_only=advisory_only,
        gate_score_overrides=gate_score_overrides,
        fr_module_traceability=sab_data.get("fr_module_traceability", {}),
        architecture_constraints=sab_data.get("architecture_constraints", []),
        high_risk_modules=sab_data.get("high_risk_modules", []),
    )


def x_extract_sab_from_sad__mutmut_126(sad_path) -> Optional[SABSpec]:
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
        advisory_only=advisory_only,
        gate_score_overrides=gate_score_overrides,
        fr_module_traceability=sab_data.get("fr_module_traceability", {}),
        architecture_constraints=sab_data.get("architecture_constraints", []),
        high_risk_modules=sab_data.get("high_risk_modules", []),
    )


def x_extract_sab_from_sad__mutmut_127(sad_path) -> Optional[SABSpec]:
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
        gate_score_overrides=gate_score_overrides,
        fr_module_traceability=sab_data.get("fr_module_traceability", {}),
        architecture_constraints=sab_data.get("architecture_constraints", []),
        high_risk_modules=sab_data.get("high_risk_modules", []),
    )


def x_extract_sab_from_sad__mutmut_128(sad_path) -> Optional[SABSpec]:
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
        fr_module_traceability=sab_data.get("fr_module_traceability", {}),
        architecture_constraints=sab_data.get("architecture_constraints", []),
        high_risk_modules=sab_data.get("high_risk_modules", []),
    )


def x_extract_sab_from_sad__mutmut_129(sad_path) -> Optional[SABSpec]:
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
        architecture_constraints=sab_data.get("architecture_constraints", []),
        high_risk_modules=sab_data.get("high_risk_modules", []),
    )


def x_extract_sab_from_sad__mutmut_130(sad_path) -> Optional[SABSpec]:
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
        high_risk_modules=sab_data.get("high_risk_modules", []),
    )


def x_extract_sab_from_sad__mutmut_131(sad_path) -> Optional[SABSpec]:
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
        )


def x_extract_sab_from_sad__mutmut_132(sad_path) -> Optional[SABSpec]:
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
        version=str(None),
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


def x_extract_sab_from_sad__mutmut_133(sad_path) -> Optional[SABSpec]:
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
        version=str(sab_data.get(None, "1.0")),
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


def x_extract_sab_from_sad__mutmut_134(sad_path) -> Optional[SABSpec]:
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
        version=str(sab_data.get("version", None)),
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


def x_extract_sab_from_sad__mutmut_135(sad_path) -> Optional[SABSpec]:
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
        version=str(sab_data.get("1.0")),
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


def x_extract_sab_from_sad__mutmut_136(sad_path) -> Optional[SABSpec]:
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
        version=str(sab_data.get("version", )),
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


def x_extract_sab_from_sad__mutmut_137(sad_path) -> Optional[SABSpec]:
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
        version=str(sab_data.get("XXversionXX", "1.0")),
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


def x_extract_sab_from_sad__mutmut_138(sad_path) -> Optional[SABSpec]:
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
        version=str(sab_data.get("VERSION", "1.0")),
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


def x_extract_sab_from_sad__mutmut_139(sad_path) -> Optional[SABSpec]:
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
        version=str(sab_data.get("version", "XX1.0XX")),
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


def x_extract_sab_from_sad__mutmut_140(sad_path) -> Optional[SABSpec]:
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
        created_at=str(None),
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


def x_extract_sab_from_sad__mutmut_141(sad_path) -> Optional[SABSpec]:
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
        created_at=str(sab_data.get(None, "")),
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


def x_extract_sab_from_sad__mutmut_142(sad_path) -> Optional[SABSpec]:
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
        created_at=str(sab_data.get("created_at", None)),
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


def x_extract_sab_from_sad__mutmut_143(sad_path) -> Optional[SABSpec]:
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
        created_at=str(sab_data.get("")),
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


def x_extract_sab_from_sad__mutmut_144(sad_path) -> Optional[SABSpec]:
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
        created_at=str(sab_data.get("created_at", )),
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


def x_extract_sab_from_sad__mutmut_145(sad_path) -> Optional[SABSpec]:
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
        created_at=str(sab_data.get("XXcreated_atXX", "")),
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


def x_extract_sab_from_sad__mutmut_146(sad_path) -> Optional[SABSpec]:
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
        created_at=str(sab_data.get("CREATED_AT", "")),
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


def x_extract_sab_from_sad__mutmut_147(sad_path) -> Optional[SABSpec]:
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
        created_at=str(sab_data.get("created_at", "XXXX")),
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


def x_extract_sab_from_sad__mutmut_148(sad_path) -> Optional[SABSpec]:
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
        project=str(None),
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


def x_extract_sab_from_sad__mutmut_149(sad_path) -> Optional[SABSpec]:
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
        project=str(sab_data.get(None, "")),
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


def x_extract_sab_from_sad__mutmut_150(sad_path) -> Optional[SABSpec]:
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
        project=str(sab_data.get("project", None)),
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


def x_extract_sab_from_sad__mutmut_151(sad_path) -> Optional[SABSpec]:
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
        project=str(sab_data.get("")),
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


def x_extract_sab_from_sad__mutmut_152(sad_path) -> Optional[SABSpec]:
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
        project=str(sab_data.get("project", )),
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


def x_extract_sab_from_sad__mutmut_153(sad_path) -> Optional[SABSpec]:
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
        project=str(sab_data.get("XXprojectXX", "")),
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


def x_extract_sab_from_sad__mutmut_154(sad_path) -> Optional[SABSpec]:
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
        project=str(sab_data.get("PROJECT", "")),
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


def x_extract_sab_from_sad__mutmut_155(sad_path) -> Optional[SABSpec]:
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
        project=str(sab_data.get("project", "XXXX")),
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


def x_extract_sab_from_sad__mutmut_156(sad_path) -> Optional[SABSpec]:
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
        layers=sab_data.get(None, []),
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


def x_extract_sab_from_sad__mutmut_157(sad_path) -> Optional[SABSpec]:
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
        layers=sab_data.get("layers", None),
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


def x_extract_sab_from_sad__mutmut_158(sad_path) -> Optional[SABSpec]:
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
        layers=sab_data.get([]),
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


def x_extract_sab_from_sad__mutmut_159(sad_path) -> Optional[SABSpec]:
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
        layers=sab_data.get("layers", ),
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


def x_extract_sab_from_sad__mutmut_160(sad_path) -> Optional[SABSpec]:
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
        layers=sab_data.get("XXlayersXX", []),
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


def x_extract_sab_from_sad__mutmut_161(sad_path) -> Optional[SABSpec]:
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
        layers=sab_data.get("LAYERS", []),
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


def x_extract_sab_from_sad__mutmut_162(sad_path) -> Optional[SABSpec]:
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
        allowed_dependencies=sab_data.get(None, []),
        quality_targets=sab_data.get("quality_targets", {}),
        nfr_dimension_mapping=nfr_dim_mapping,
        nfr_traceability=nfr_traceability,
        advisory_only=advisory_only,
        gate_score_overrides=gate_score_overrides,
        fr_module_traceability=sab_data.get("fr_module_traceability", {}),
        architecture_constraints=sab_data.get("architecture_constraints", []),
        high_risk_modules=sab_data.get("high_risk_modules", []),
    )


def x_extract_sab_from_sad__mutmut_163(sad_path) -> Optional[SABSpec]:
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
        allowed_dependencies=sab_data.get("allowed_dependencies", None),
        quality_targets=sab_data.get("quality_targets", {}),
        nfr_dimension_mapping=nfr_dim_mapping,
        nfr_traceability=nfr_traceability,
        advisory_only=advisory_only,
        gate_score_overrides=gate_score_overrides,
        fr_module_traceability=sab_data.get("fr_module_traceability", {}),
        architecture_constraints=sab_data.get("architecture_constraints", []),
        high_risk_modules=sab_data.get("high_risk_modules", []),
    )


def x_extract_sab_from_sad__mutmut_164(sad_path) -> Optional[SABSpec]:
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
        allowed_dependencies=sab_data.get([]),
        quality_targets=sab_data.get("quality_targets", {}),
        nfr_dimension_mapping=nfr_dim_mapping,
        nfr_traceability=nfr_traceability,
        advisory_only=advisory_only,
        gate_score_overrides=gate_score_overrides,
        fr_module_traceability=sab_data.get("fr_module_traceability", {}),
        architecture_constraints=sab_data.get("architecture_constraints", []),
        high_risk_modules=sab_data.get("high_risk_modules", []),
    )


def x_extract_sab_from_sad__mutmut_165(sad_path) -> Optional[SABSpec]:
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
        allowed_dependencies=sab_data.get("allowed_dependencies", ),
        quality_targets=sab_data.get("quality_targets", {}),
        nfr_dimension_mapping=nfr_dim_mapping,
        nfr_traceability=nfr_traceability,
        advisory_only=advisory_only,
        gate_score_overrides=gate_score_overrides,
        fr_module_traceability=sab_data.get("fr_module_traceability", {}),
        architecture_constraints=sab_data.get("architecture_constraints", []),
        high_risk_modules=sab_data.get("high_risk_modules", []),
    )


def x_extract_sab_from_sad__mutmut_166(sad_path) -> Optional[SABSpec]:
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
        allowed_dependencies=sab_data.get("XXallowed_dependenciesXX", []),
        quality_targets=sab_data.get("quality_targets", {}),
        nfr_dimension_mapping=nfr_dim_mapping,
        nfr_traceability=nfr_traceability,
        advisory_only=advisory_only,
        gate_score_overrides=gate_score_overrides,
        fr_module_traceability=sab_data.get("fr_module_traceability", {}),
        architecture_constraints=sab_data.get("architecture_constraints", []),
        high_risk_modules=sab_data.get("high_risk_modules", []),
    )


def x_extract_sab_from_sad__mutmut_167(sad_path) -> Optional[SABSpec]:
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
        allowed_dependencies=sab_data.get("ALLOWED_DEPENDENCIES", []),
        quality_targets=sab_data.get("quality_targets", {}),
        nfr_dimension_mapping=nfr_dim_mapping,
        nfr_traceability=nfr_traceability,
        advisory_only=advisory_only,
        gate_score_overrides=gate_score_overrides,
        fr_module_traceability=sab_data.get("fr_module_traceability", {}),
        architecture_constraints=sab_data.get("architecture_constraints", []),
        high_risk_modules=sab_data.get("high_risk_modules", []),
    )


def x_extract_sab_from_sad__mutmut_168(sad_path) -> Optional[SABSpec]:
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
        quality_targets=sab_data.get(None, {}),
        nfr_dimension_mapping=nfr_dim_mapping,
        nfr_traceability=nfr_traceability,
        advisory_only=advisory_only,
        gate_score_overrides=gate_score_overrides,
        fr_module_traceability=sab_data.get("fr_module_traceability", {}),
        architecture_constraints=sab_data.get("architecture_constraints", []),
        high_risk_modules=sab_data.get("high_risk_modules", []),
    )


def x_extract_sab_from_sad__mutmut_169(sad_path) -> Optional[SABSpec]:
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
        quality_targets=sab_data.get("quality_targets", None),
        nfr_dimension_mapping=nfr_dim_mapping,
        nfr_traceability=nfr_traceability,
        advisory_only=advisory_only,
        gate_score_overrides=gate_score_overrides,
        fr_module_traceability=sab_data.get("fr_module_traceability", {}),
        architecture_constraints=sab_data.get("architecture_constraints", []),
        high_risk_modules=sab_data.get("high_risk_modules", []),
    )


def x_extract_sab_from_sad__mutmut_170(sad_path) -> Optional[SABSpec]:
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
        quality_targets=sab_data.get({}),
        nfr_dimension_mapping=nfr_dim_mapping,
        nfr_traceability=nfr_traceability,
        advisory_only=advisory_only,
        gate_score_overrides=gate_score_overrides,
        fr_module_traceability=sab_data.get("fr_module_traceability", {}),
        architecture_constraints=sab_data.get("architecture_constraints", []),
        high_risk_modules=sab_data.get("high_risk_modules", []),
    )


def x_extract_sab_from_sad__mutmut_171(sad_path) -> Optional[SABSpec]:
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
        quality_targets=sab_data.get("quality_targets", ),
        nfr_dimension_mapping=nfr_dim_mapping,
        nfr_traceability=nfr_traceability,
        advisory_only=advisory_only,
        gate_score_overrides=gate_score_overrides,
        fr_module_traceability=sab_data.get("fr_module_traceability", {}),
        architecture_constraints=sab_data.get("architecture_constraints", []),
        high_risk_modules=sab_data.get("high_risk_modules", []),
    )


def x_extract_sab_from_sad__mutmut_172(sad_path) -> Optional[SABSpec]:
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
        quality_targets=sab_data.get("XXquality_targetsXX", {}),
        nfr_dimension_mapping=nfr_dim_mapping,
        nfr_traceability=nfr_traceability,
        advisory_only=advisory_only,
        gate_score_overrides=gate_score_overrides,
        fr_module_traceability=sab_data.get("fr_module_traceability", {}),
        architecture_constraints=sab_data.get("architecture_constraints", []),
        high_risk_modules=sab_data.get("high_risk_modules", []),
    )


def x_extract_sab_from_sad__mutmut_173(sad_path) -> Optional[SABSpec]:
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
        quality_targets=sab_data.get("QUALITY_TARGETS", {}),
        nfr_dimension_mapping=nfr_dim_mapping,
        nfr_traceability=nfr_traceability,
        advisory_only=advisory_only,
        gate_score_overrides=gate_score_overrides,
        fr_module_traceability=sab_data.get("fr_module_traceability", {}),
        architecture_constraints=sab_data.get("architecture_constraints", []),
        high_risk_modules=sab_data.get("high_risk_modules", []),
    )


def x_extract_sab_from_sad__mutmut_174(sad_path) -> Optional[SABSpec]:
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
        fr_module_traceability=sab_data.get(None, {}),
        architecture_constraints=sab_data.get("architecture_constraints", []),
        high_risk_modules=sab_data.get("high_risk_modules", []),
    )


def x_extract_sab_from_sad__mutmut_175(sad_path) -> Optional[SABSpec]:
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
        fr_module_traceability=sab_data.get("fr_module_traceability", None),
        architecture_constraints=sab_data.get("architecture_constraints", []),
        high_risk_modules=sab_data.get("high_risk_modules", []),
    )


def x_extract_sab_from_sad__mutmut_176(sad_path) -> Optional[SABSpec]:
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
        fr_module_traceability=sab_data.get({}),
        architecture_constraints=sab_data.get("architecture_constraints", []),
        high_risk_modules=sab_data.get("high_risk_modules", []),
    )


def x_extract_sab_from_sad__mutmut_177(sad_path) -> Optional[SABSpec]:
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
        fr_module_traceability=sab_data.get("fr_module_traceability", ),
        architecture_constraints=sab_data.get("architecture_constraints", []),
        high_risk_modules=sab_data.get("high_risk_modules", []),
    )


def x_extract_sab_from_sad__mutmut_178(sad_path) -> Optional[SABSpec]:
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
        fr_module_traceability=sab_data.get("XXfr_module_traceabilityXX", {}),
        architecture_constraints=sab_data.get("architecture_constraints", []),
        high_risk_modules=sab_data.get("high_risk_modules", []),
    )


def x_extract_sab_from_sad__mutmut_179(sad_path) -> Optional[SABSpec]:
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
        fr_module_traceability=sab_data.get("FR_MODULE_TRACEABILITY", {}),
        architecture_constraints=sab_data.get("architecture_constraints", []),
        high_risk_modules=sab_data.get("high_risk_modules", []),
    )


def x_extract_sab_from_sad__mutmut_180(sad_path) -> Optional[SABSpec]:
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
        architecture_constraints=sab_data.get(None, []),
        high_risk_modules=sab_data.get("high_risk_modules", []),
    )


def x_extract_sab_from_sad__mutmut_181(sad_path) -> Optional[SABSpec]:
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
        architecture_constraints=sab_data.get("architecture_constraints", None),
        high_risk_modules=sab_data.get("high_risk_modules", []),
    )


def x_extract_sab_from_sad__mutmut_182(sad_path) -> Optional[SABSpec]:
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
        architecture_constraints=sab_data.get([]),
        high_risk_modules=sab_data.get("high_risk_modules", []),
    )


def x_extract_sab_from_sad__mutmut_183(sad_path) -> Optional[SABSpec]:
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
        architecture_constraints=sab_data.get("architecture_constraints", ),
        high_risk_modules=sab_data.get("high_risk_modules", []),
    )


def x_extract_sab_from_sad__mutmut_184(sad_path) -> Optional[SABSpec]:
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
        architecture_constraints=sab_data.get("XXarchitecture_constraintsXX", []),
        high_risk_modules=sab_data.get("high_risk_modules", []),
    )


def x_extract_sab_from_sad__mutmut_185(sad_path) -> Optional[SABSpec]:
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
        architecture_constraints=sab_data.get("ARCHITECTURE_CONSTRAINTS", []),
        high_risk_modules=sab_data.get("high_risk_modules", []),
    )


def x_extract_sab_from_sad__mutmut_186(sad_path) -> Optional[SABSpec]:
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
        high_risk_modules=sab_data.get(None, []),
    )


def x_extract_sab_from_sad__mutmut_187(sad_path) -> Optional[SABSpec]:
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
        high_risk_modules=sab_data.get("high_risk_modules", None),
    )


def x_extract_sab_from_sad__mutmut_188(sad_path) -> Optional[SABSpec]:
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
        high_risk_modules=sab_data.get([]),
    )


def x_extract_sab_from_sad__mutmut_189(sad_path) -> Optional[SABSpec]:
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
        high_risk_modules=sab_data.get("high_risk_modules", ),
    )


def x_extract_sab_from_sad__mutmut_190(sad_path) -> Optional[SABSpec]:
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
        high_risk_modules=sab_data.get("XXhigh_risk_modulesXX", []),
    )


def x_extract_sab_from_sad__mutmut_191(sad_path) -> Optional[SABSpec]:
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
        high_risk_modules=sab_data.get("HIGH_RISK_MODULES", []),
    )

x_extract_sab_from_sad__mutmut_mutants : ClassVar[MutantDict] = {
'x_extract_sab_from_sad__mutmut_1': x_extract_sab_from_sad__mutmut_1, 
    'x_extract_sab_from_sad__mutmut_2': x_extract_sab_from_sad__mutmut_2, 
    'x_extract_sab_from_sad__mutmut_3': x_extract_sab_from_sad__mutmut_3, 
    'x_extract_sab_from_sad__mutmut_4': x_extract_sab_from_sad__mutmut_4, 
    'x_extract_sab_from_sad__mutmut_5': x_extract_sab_from_sad__mutmut_5, 
    'x_extract_sab_from_sad__mutmut_6': x_extract_sab_from_sad__mutmut_6, 
    'x_extract_sab_from_sad__mutmut_7': x_extract_sab_from_sad__mutmut_7, 
    'x_extract_sab_from_sad__mutmut_8': x_extract_sab_from_sad__mutmut_8, 
    'x_extract_sab_from_sad__mutmut_9': x_extract_sab_from_sad__mutmut_9, 
    'x_extract_sab_from_sad__mutmut_10': x_extract_sab_from_sad__mutmut_10, 
    'x_extract_sab_from_sad__mutmut_11': x_extract_sab_from_sad__mutmut_11, 
    'x_extract_sab_from_sad__mutmut_12': x_extract_sab_from_sad__mutmut_12, 
    'x_extract_sab_from_sad__mutmut_13': x_extract_sab_from_sad__mutmut_13, 
    'x_extract_sab_from_sad__mutmut_14': x_extract_sab_from_sad__mutmut_14, 
    'x_extract_sab_from_sad__mutmut_15': x_extract_sab_from_sad__mutmut_15, 
    'x_extract_sab_from_sad__mutmut_16': x_extract_sab_from_sad__mutmut_16, 
    'x_extract_sab_from_sad__mutmut_17': x_extract_sab_from_sad__mutmut_17, 
    'x_extract_sab_from_sad__mutmut_18': x_extract_sab_from_sad__mutmut_18, 
    'x_extract_sab_from_sad__mutmut_19': x_extract_sab_from_sad__mutmut_19, 
    'x_extract_sab_from_sad__mutmut_20': x_extract_sab_from_sad__mutmut_20, 
    'x_extract_sab_from_sad__mutmut_21': x_extract_sab_from_sad__mutmut_21, 
    'x_extract_sab_from_sad__mutmut_22': x_extract_sab_from_sad__mutmut_22, 
    'x_extract_sab_from_sad__mutmut_23': x_extract_sab_from_sad__mutmut_23, 
    'x_extract_sab_from_sad__mutmut_24': x_extract_sab_from_sad__mutmut_24, 
    'x_extract_sab_from_sad__mutmut_25': x_extract_sab_from_sad__mutmut_25, 
    'x_extract_sab_from_sad__mutmut_26': x_extract_sab_from_sad__mutmut_26, 
    'x_extract_sab_from_sad__mutmut_27': x_extract_sab_from_sad__mutmut_27, 
    'x_extract_sab_from_sad__mutmut_28': x_extract_sab_from_sad__mutmut_28, 
    'x_extract_sab_from_sad__mutmut_29': x_extract_sab_from_sad__mutmut_29, 
    'x_extract_sab_from_sad__mutmut_30': x_extract_sab_from_sad__mutmut_30, 
    'x_extract_sab_from_sad__mutmut_31': x_extract_sab_from_sad__mutmut_31, 
    'x_extract_sab_from_sad__mutmut_32': x_extract_sab_from_sad__mutmut_32, 
    'x_extract_sab_from_sad__mutmut_33': x_extract_sab_from_sad__mutmut_33, 
    'x_extract_sab_from_sad__mutmut_34': x_extract_sab_from_sad__mutmut_34, 
    'x_extract_sab_from_sad__mutmut_35': x_extract_sab_from_sad__mutmut_35, 
    'x_extract_sab_from_sad__mutmut_36': x_extract_sab_from_sad__mutmut_36, 
    'x_extract_sab_from_sad__mutmut_37': x_extract_sab_from_sad__mutmut_37, 
    'x_extract_sab_from_sad__mutmut_38': x_extract_sab_from_sad__mutmut_38, 
    'x_extract_sab_from_sad__mutmut_39': x_extract_sab_from_sad__mutmut_39, 
    'x_extract_sab_from_sad__mutmut_40': x_extract_sab_from_sad__mutmut_40, 
    'x_extract_sab_from_sad__mutmut_41': x_extract_sab_from_sad__mutmut_41, 
    'x_extract_sab_from_sad__mutmut_42': x_extract_sab_from_sad__mutmut_42, 
    'x_extract_sab_from_sad__mutmut_43': x_extract_sab_from_sad__mutmut_43, 
    'x_extract_sab_from_sad__mutmut_44': x_extract_sab_from_sad__mutmut_44, 
    'x_extract_sab_from_sad__mutmut_45': x_extract_sab_from_sad__mutmut_45, 
    'x_extract_sab_from_sad__mutmut_46': x_extract_sab_from_sad__mutmut_46, 
    'x_extract_sab_from_sad__mutmut_47': x_extract_sab_from_sad__mutmut_47, 
    'x_extract_sab_from_sad__mutmut_48': x_extract_sab_from_sad__mutmut_48, 
    'x_extract_sab_from_sad__mutmut_49': x_extract_sab_from_sad__mutmut_49, 
    'x_extract_sab_from_sad__mutmut_50': x_extract_sab_from_sad__mutmut_50, 
    'x_extract_sab_from_sad__mutmut_51': x_extract_sab_from_sad__mutmut_51, 
    'x_extract_sab_from_sad__mutmut_52': x_extract_sab_from_sad__mutmut_52, 
    'x_extract_sab_from_sad__mutmut_53': x_extract_sab_from_sad__mutmut_53, 
    'x_extract_sab_from_sad__mutmut_54': x_extract_sab_from_sad__mutmut_54, 
    'x_extract_sab_from_sad__mutmut_55': x_extract_sab_from_sad__mutmut_55, 
    'x_extract_sab_from_sad__mutmut_56': x_extract_sab_from_sad__mutmut_56, 
    'x_extract_sab_from_sad__mutmut_57': x_extract_sab_from_sad__mutmut_57, 
    'x_extract_sab_from_sad__mutmut_58': x_extract_sab_from_sad__mutmut_58, 
    'x_extract_sab_from_sad__mutmut_59': x_extract_sab_from_sad__mutmut_59, 
    'x_extract_sab_from_sad__mutmut_60': x_extract_sab_from_sad__mutmut_60, 
    'x_extract_sab_from_sad__mutmut_61': x_extract_sab_from_sad__mutmut_61, 
    'x_extract_sab_from_sad__mutmut_62': x_extract_sab_from_sad__mutmut_62, 
    'x_extract_sab_from_sad__mutmut_63': x_extract_sab_from_sad__mutmut_63, 
    'x_extract_sab_from_sad__mutmut_64': x_extract_sab_from_sad__mutmut_64, 
    'x_extract_sab_from_sad__mutmut_65': x_extract_sab_from_sad__mutmut_65, 
    'x_extract_sab_from_sad__mutmut_66': x_extract_sab_from_sad__mutmut_66, 
    'x_extract_sab_from_sad__mutmut_67': x_extract_sab_from_sad__mutmut_67, 
    'x_extract_sab_from_sad__mutmut_68': x_extract_sab_from_sad__mutmut_68, 
    'x_extract_sab_from_sad__mutmut_69': x_extract_sab_from_sad__mutmut_69, 
    'x_extract_sab_from_sad__mutmut_70': x_extract_sab_from_sad__mutmut_70, 
    'x_extract_sab_from_sad__mutmut_71': x_extract_sab_from_sad__mutmut_71, 
    'x_extract_sab_from_sad__mutmut_72': x_extract_sab_from_sad__mutmut_72, 
    'x_extract_sab_from_sad__mutmut_73': x_extract_sab_from_sad__mutmut_73, 
    'x_extract_sab_from_sad__mutmut_74': x_extract_sab_from_sad__mutmut_74, 
    'x_extract_sab_from_sad__mutmut_75': x_extract_sab_from_sad__mutmut_75, 
    'x_extract_sab_from_sad__mutmut_76': x_extract_sab_from_sad__mutmut_76, 
    'x_extract_sab_from_sad__mutmut_77': x_extract_sab_from_sad__mutmut_77, 
    'x_extract_sab_from_sad__mutmut_78': x_extract_sab_from_sad__mutmut_78, 
    'x_extract_sab_from_sad__mutmut_79': x_extract_sab_from_sad__mutmut_79, 
    'x_extract_sab_from_sad__mutmut_80': x_extract_sab_from_sad__mutmut_80, 
    'x_extract_sab_from_sad__mutmut_81': x_extract_sab_from_sad__mutmut_81, 
    'x_extract_sab_from_sad__mutmut_82': x_extract_sab_from_sad__mutmut_82, 
    'x_extract_sab_from_sad__mutmut_83': x_extract_sab_from_sad__mutmut_83, 
    'x_extract_sab_from_sad__mutmut_84': x_extract_sab_from_sad__mutmut_84, 
    'x_extract_sab_from_sad__mutmut_85': x_extract_sab_from_sad__mutmut_85, 
    'x_extract_sab_from_sad__mutmut_86': x_extract_sab_from_sad__mutmut_86, 
    'x_extract_sab_from_sad__mutmut_87': x_extract_sab_from_sad__mutmut_87, 
    'x_extract_sab_from_sad__mutmut_88': x_extract_sab_from_sad__mutmut_88, 
    'x_extract_sab_from_sad__mutmut_89': x_extract_sab_from_sad__mutmut_89, 
    'x_extract_sab_from_sad__mutmut_90': x_extract_sab_from_sad__mutmut_90, 
    'x_extract_sab_from_sad__mutmut_91': x_extract_sab_from_sad__mutmut_91, 
    'x_extract_sab_from_sad__mutmut_92': x_extract_sab_from_sad__mutmut_92, 
    'x_extract_sab_from_sad__mutmut_93': x_extract_sab_from_sad__mutmut_93, 
    'x_extract_sab_from_sad__mutmut_94': x_extract_sab_from_sad__mutmut_94, 
    'x_extract_sab_from_sad__mutmut_95': x_extract_sab_from_sad__mutmut_95, 
    'x_extract_sab_from_sad__mutmut_96': x_extract_sab_from_sad__mutmut_96, 
    'x_extract_sab_from_sad__mutmut_97': x_extract_sab_from_sad__mutmut_97, 
    'x_extract_sab_from_sad__mutmut_98': x_extract_sab_from_sad__mutmut_98, 
    'x_extract_sab_from_sad__mutmut_99': x_extract_sab_from_sad__mutmut_99, 
    'x_extract_sab_from_sad__mutmut_100': x_extract_sab_from_sad__mutmut_100, 
    'x_extract_sab_from_sad__mutmut_101': x_extract_sab_from_sad__mutmut_101, 
    'x_extract_sab_from_sad__mutmut_102': x_extract_sab_from_sad__mutmut_102, 
    'x_extract_sab_from_sad__mutmut_103': x_extract_sab_from_sad__mutmut_103, 
    'x_extract_sab_from_sad__mutmut_104': x_extract_sab_from_sad__mutmut_104, 
    'x_extract_sab_from_sad__mutmut_105': x_extract_sab_from_sad__mutmut_105, 
    'x_extract_sab_from_sad__mutmut_106': x_extract_sab_from_sad__mutmut_106, 
    'x_extract_sab_from_sad__mutmut_107': x_extract_sab_from_sad__mutmut_107, 
    'x_extract_sab_from_sad__mutmut_108': x_extract_sab_from_sad__mutmut_108, 
    'x_extract_sab_from_sad__mutmut_109': x_extract_sab_from_sad__mutmut_109, 
    'x_extract_sab_from_sad__mutmut_110': x_extract_sab_from_sad__mutmut_110, 
    'x_extract_sab_from_sad__mutmut_111': x_extract_sab_from_sad__mutmut_111, 
    'x_extract_sab_from_sad__mutmut_112': x_extract_sab_from_sad__mutmut_112, 
    'x_extract_sab_from_sad__mutmut_113': x_extract_sab_from_sad__mutmut_113, 
    'x_extract_sab_from_sad__mutmut_114': x_extract_sab_from_sad__mutmut_114, 
    'x_extract_sab_from_sad__mutmut_115': x_extract_sab_from_sad__mutmut_115, 
    'x_extract_sab_from_sad__mutmut_116': x_extract_sab_from_sad__mutmut_116, 
    'x_extract_sab_from_sad__mutmut_117': x_extract_sab_from_sad__mutmut_117, 
    'x_extract_sab_from_sad__mutmut_118': x_extract_sab_from_sad__mutmut_118, 
    'x_extract_sab_from_sad__mutmut_119': x_extract_sab_from_sad__mutmut_119, 
    'x_extract_sab_from_sad__mutmut_120': x_extract_sab_from_sad__mutmut_120, 
    'x_extract_sab_from_sad__mutmut_121': x_extract_sab_from_sad__mutmut_121, 
    'x_extract_sab_from_sad__mutmut_122': x_extract_sab_from_sad__mutmut_122, 
    'x_extract_sab_from_sad__mutmut_123': x_extract_sab_from_sad__mutmut_123, 
    'x_extract_sab_from_sad__mutmut_124': x_extract_sab_from_sad__mutmut_124, 
    'x_extract_sab_from_sad__mutmut_125': x_extract_sab_from_sad__mutmut_125, 
    'x_extract_sab_from_sad__mutmut_126': x_extract_sab_from_sad__mutmut_126, 
    'x_extract_sab_from_sad__mutmut_127': x_extract_sab_from_sad__mutmut_127, 
    'x_extract_sab_from_sad__mutmut_128': x_extract_sab_from_sad__mutmut_128, 
    'x_extract_sab_from_sad__mutmut_129': x_extract_sab_from_sad__mutmut_129, 
    'x_extract_sab_from_sad__mutmut_130': x_extract_sab_from_sad__mutmut_130, 
    'x_extract_sab_from_sad__mutmut_131': x_extract_sab_from_sad__mutmut_131, 
    'x_extract_sab_from_sad__mutmut_132': x_extract_sab_from_sad__mutmut_132, 
    'x_extract_sab_from_sad__mutmut_133': x_extract_sab_from_sad__mutmut_133, 
    'x_extract_sab_from_sad__mutmut_134': x_extract_sab_from_sad__mutmut_134, 
    'x_extract_sab_from_sad__mutmut_135': x_extract_sab_from_sad__mutmut_135, 
    'x_extract_sab_from_sad__mutmut_136': x_extract_sab_from_sad__mutmut_136, 
    'x_extract_sab_from_sad__mutmut_137': x_extract_sab_from_sad__mutmut_137, 
    'x_extract_sab_from_sad__mutmut_138': x_extract_sab_from_sad__mutmut_138, 
    'x_extract_sab_from_sad__mutmut_139': x_extract_sab_from_sad__mutmut_139, 
    'x_extract_sab_from_sad__mutmut_140': x_extract_sab_from_sad__mutmut_140, 
    'x_extract_sab_from_sad__mutmut_141': x_extract_sab_from_sad__mutmut_141, 
    'x_extract_sab_from_sad__mutmut_142': x_extract_sab_from_sad__mutmut_142, 
    'x_extract_sab_from_sad__mutmut_143': x_extract_sab_from_sad__mutmut_143, 
    'x_extract_sab_from_sad__mutmut_144': x_extract_sab_from_sad__mutmut_144, 
    'x_extract_sab_from_sad__mutmut_145': x_extract_sab_from_sad__mutmut_145, 
    'x_extract_sab_from_sad__mutmut_146': x_extract_sab_from_sad__mutmut_146, 
    'x_extract_sab_from_sad__mutmut_147': x_extract_sab_from_sad__mutmut_147, 
    'x_extract_sab_from_sad__mutmut_148': x_extract_sab_from_sad__mutmut_148, 
    'x_extract_sab_from_sad__mutmut_149': x_extract_sab_from_sad__mutmut_149, 
    'x_extract_sab_from_sad__mutmut_150': x_extract_sab_from_sad__mutmut_150, 
    'x_extract_sab_from_sad__mutmut_151': x_extract_sab_from_sad__mutmut_151, 
    'x_extract_sab_from_sad__mutmut_152': x_extract_sab_from_sad__mutmut_152, 
    'x_extract_sab_from_sad__mutmut_153': x_extract_sab_from_sad__mutmut_153, 
    'x_extract_sab_from_sad__mutmut_154': x_extract_sab_from_sad__mutmut_154, 
    'x_extract_sab_from_sad__mutmut_155': x_extract_sab_from_sad__mutmut_155, 
    'x_extract_sab_from_sad__mutmut_156': x_extract_sab_from_sad__mutmut_156, 
    'x_extract_sab_from_sad__mutmut_157': x_extract_sab_from_sad__mutmut_157, 
    'x_extract_sab_from_sad__mutmut_158': x_extract_sab_from_sad__mutmut_158, 
    'x_extract_sab_from_sad__mutmut_159': x_extract_sab_from_sad__mutmut_159, 
    'x_extract_sab_from_sad__mutmut_160': x_extract_sab_from_sad__mutmut_160, 
    'x_extract_sab_from_sad__mutmut_161': x_extract_sab_from_sad__mutmut_161, 
    'x_extract_sab_from_sad__mutmut_162': x_extract_sab_from_sad__mutmut_162, 
    'x_extract_sab_from_sad__mutmut_163': x_extract_sab_from_sad__mutmut_163, 
    'x_extract_sab_from_sad__mutmut_164': x_extract_sab_from_sad__mutmut_164, 
    'x_extract_sab_from_sad__mutmut_165': x_extract_sab_from_sad__mutmut_165, 
    'x_extract_sab_from_sad__mutmut_166': x_extract_sab_from_sad__mutmut_166, 
    'x_extract_sab_from_sad__mutmut_167': x_extract_sab_from_sad__mutmut_167, 
    'x_extract_sab_from_sad__mutmut_168': x_extract_sab_from_sad__mutmut_168, 
    'x_extract_sab_from_sad__mutmut_169': x_extract_sab_from_sad__mutmut_169, 
    'x_extract_sab_from_sad__mutmut_170': x_extract_sab_from_sad__mutmut_170, 
    'x_extract_sab_from_sad__mutmut_171': x_extract_sab_from_sad__mutmut_171, 
    'x_extract_sab_from_sad__mutmut_172': x_extract_sab_from_sad__mutmut_172, 
    'x_extract_sab_from_sad__mutmut_173': x_extract_sab_from_sad__mutmut_173, 
    'x_extract_sab_from_sad__mutmut_174': x_extract_sab_from_sad__mutmut_174, 
    'x_extract_sab_from_sad__mutmut_175': x_extract_sab_from_sad__mutmut_175, 
    'x_extract_sab_from_sad__mutmut_176': x_extract_sab_from_sad__mutmut_176, 
    'x_extract_sab_from_sad__mutmut_177': x_extract_sab_from_sad__mutmut_177, 
    'x_extract_sab_from_sad__mutmut_178': x_extract_sab_from_sad__mutmut_178, 
    'x_extract_sab_from_sad__mutmut_179': x_extract_sab_from_sad__mutmut_179, 
    'x_extract_sab_from_sad__mutmut_180': x_extract_sab_from_sad__mutmut_180, 
    'x_extract_sab_from_sad__mutmut_181': x_extract_sab_from_sad__mutmut_181, 
    'x_extract_sab_from_sad__mutmut_182': x_extract_sab_from_sad__mutmut_182, 
    'x_extract_sab_from_sad__mutmut_183': x_extract_sab_from_sad__mutmut_183, 
    'x_extract_sab_from_sad__mutmut_184': x_extract_sab_from_sad__mutmut_184, 
    'x_extract_sab_from_sad__mutmut_185': x_extract_sab_from_sad__mutmut_185, 
    'x_extract_sab_from_sad__mutmut_186': x_extract_sab_from_sad__mutmut_186, 
    'x_extract_sab_from_sad__mutmut_187': x_extract_sab_from_sad__mutmut_187, 
    'x_extract_sab_from_sad__mutmut_188': x_extract_sab_from_sad__mutmut_188, 
    'x_extract_sab_from_sad__mutmut_189': x_extract_sab_from_sad__mutmut_189, 
    'x_extract_sab_from_sad__mutmut_190': x_extract_sab_from_sad__mutmut_190, 
    'x_extract_sab_from_sad__mutmut_191': x_extract_sab_from_sad__mutmut_191
}

def extract_sab_from_sad(*args, **kwargs):
    result = _mutmut_trampoline(x_extract_sab_from_sad__mutmut_orig, x_extract_sab_from_sad__mutmut_mutants, args, kwargs)
    return result 

extract_sab_from_sad.__signature__ = _mutmut_signature(x_extract_sab_from_sad__mutmut_orig)
x_extract_sab_from_sad__mutmut_orig.__name__ = 'x_extract_sab_from_sad'
