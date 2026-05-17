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
            "architecture_constraints": self.architecture_constraints,
            "high_risk_modules": self.high_risk_modules,
        }


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

    return SABSpec(
        version=str(sab_data.get("version", "1.0")),
        created_at=str(sab_data.get("created_at", "")),
        phase=phase,
        project=str(sab_data.get("project", "")),
        layers=sab_data.get("layers", []),
        allowed_dependencies=sab_data.get("allowed_dependencies", []),
        quality_targets=sab_data.get("quality_targets", {}),
        nfr_dimension_mapping=sab_data.get("nfr_dimension_mapping", {}),
        architecture_constraints=sab_data.get("architecture_constraints", []),
        high_risk_modules=sab_data.get("high_risk_modules", []),
    )
