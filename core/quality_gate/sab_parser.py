"""
SAB Parser — extract Software Architecture Baseline from SAD.md §5.

CONTRACT (single source of truth — do not duplicate in templates/docs):
  Marker:    <!-- SAB:START --> ... <!-- SAB:END --> (REQUIRED)
  Body:      ```yaml (recommended) or ```json (legacy) or raw (no fence).
             A ```yaml code fence is STRONGLY RECOMMENDED.
  Root key:  `sab:` (recommended) — if absent, parser treats the whole body
             as the SAB block. Including `sab:` is the canonical form.
  Fields:    14 fields, mirroring the SABSpec dataclass:
               version (str, default "1.0")
               created_at (str, ISO date)
               phase (int — STRINGS RAISE RuntimeError)
               project (str)
               layers (list of {name, modules, allowed_dependencies})
               allowed_dependencies (list of {from, to})
               quality_targets (dict)
               nfr_dimension_mapping (dict, optional — auto-derived)
               nfr_traceability (dict, optional)
               advisory_only (list, AUTO-FILLED by parser — omit or leave [])
               gate_score_overrides (dict, AUTO-DERIVED by parser — omit or leave {})
               fr_module_traceability (dict)
               architecture_constraints (list)
               high_risk_modules (list)
  NFR types: 8 legal values in nfr_traceability[*].type:
               Enforceable (mapped to gate dim):
                 performance, security, maintainability, reliability, testability
               Advisory (no scoring tool, auto-added to advisory_only):
                 deployability, scalability, usability

For the canonical template, call render_canonical_sab_template() — do not
hand-write the YAML anywhere else.  SABSpec is the type-level authority for
field names and types; this docstring is the human-readable projection.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, fields as _dc_fields
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

# Gate 1 (per-FR checkpoint) dimension thresholds — deliberately stricter than
# _GATE_DIMENSION_STANDARD (Gate 4's floor) for linting/type_safety: those two
# dimensions are boolean in nature (a lint violation or type error either
# exists or doesn't — there's no "acceptable amount"), so Gate 1 requires the
# same zero-tolerance bar the whole-repo checks already enforce, applied at
# the earliest possible checkpoint instead of accumulating until Phase exit.
# Kept in sync with harness/gate_configs/gate1_per_fr.yaml (test enforces
# parity — test_prompt_gate_parity.py::test_gate1_yaml_thresholds_match_standard_ssot).
# Deliberately NOT unified with _GATE_DIMENSION_STANDARD: raising that shared
# constant would also raise Gate 2/3/4's NFR-backed gate_score_overrides floor
# (derive_gate_score_overrides below), which is out of this fix's scope.
_GATE1_DIMENSION_STANDARD: dict[str, float] = {
    "linting": 100, "type_safety": 100, "test_coverage": 80,
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
    if isinstance(raw_phase, str):
        raise RuntimeError(
            f"Invalid 'phase' in SAB block ({sad_path}): {raw_phase!r} — "
            f"expected an integer literal, not a string (remove the quotes around the number)"
        )
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


# ─────────────────────────────────────────────────────────────────────────────
# Canonical template factory — SINGLE SOURCE OF TRUTH
# ─────────────────────────────────────────────────────────────────────────────
# All places that show a "SAB block example" to humans (templates/SAD.md,
# generate_full_plan.py, docs/P2_SOP.md) MUST render this function instead of
# hand-writing YAML. The template is generated from SABSpec dataclass fields so
# adding a new field propagates automatically to every rendered template.

_NFR_TYPES_ENFORCEABLE: tuple[str, ...] = (
    "performance", "security", "maintainability", "reliability", "testability",
)
_NFR_TYPES_ADVISORY: tuple[str, ...] = (
    "deployability", "scalability", "usability",
)
ALL_NFR_TYPES: tuple[str, ...] = _NFR_TYPES_ENFORCEABLE + _NFR_TYPES_ADVISORY

# Set of top-level fields rendered explicitly before the dataclass loop.
_RENDERED_FIRST = frozenset({"version", "created_at", "phase", "project"})


def render_canonical_sab_template(
    project: str = "{project_name}",
    layer_example: str = "api",
    module_example: str = "app.api.webhooks",
    fr_id: str = "FR-01",
    nfr_id: str = "NFR-01",
) -> str:
    """Return the canonical SAB YAML block as a string (no surrounding markers/fence).

    EXAMPLE — replace placeholder values with your project's real values.

    Callers that need the full markdown form wrap the output:
        '<!-- SAB:START -->\\n```yaml\\n' + render_canonical_sab_template() + '```\\n<!-- SAB:END -->'

    Generated dynamically from SABSpec dataclass fields so the template can
    never drift.  All 8 legal NFR type values are enumerated explicitly — no
    ellipsis allowed.
    """
    lines: list[str] = []
    lines.append("sab:")
    lines.append('  version: "1.0"')
    lines.append('  created_at: "{YYYY-MM-DD}"')
    lines.append("  phase: 2  # MUST be int, NOT a string — parser raises on 'phase: \"2\"'")
    lines.append(f'  project: "{project}"')
    lines.append("")

    for f in _dc_fields(SABSpec):
        if f.name in _RENDERED_FIRST:
            continue
        if f.name == "layers":
            lines.append("  layers:  # EXAMPLE — replace with your project's layers")
            lines.append(f"    - name: {layer_example}")
            lines.append("      modules:")
            lines.append(f'        - name: "{module_example}"')
            lines.append(f'          implemented_in: "{module_example.split(".")[0]}.main"  # OPTIONAL — Use if consolidated into another file')
            lines.append('      allowed_dependencies: ["service"]')
        elif f.name == "allowed_dependencies":
            lines.append("  allowed_dependencies:")
            lines.append(f"    - from: {layer_example}")
            lines.append("      to: service")
        elif f.name == "quality_targets":
            lines.append("  quality_targets:")
            lines.append("    max_complexity: 15")
            lines.append("    min_coverage: 80")
            lines.append("    max_coupling: 0.3")
        elif f.name == "nfr_dimension_mapping":
            lines.append("  nfr_dimension_mapping: {}  # OPTIONAL — auto-derived from nfr_traceability.type")
        elif f.name == "nfr_traceability":
            lines.append("  nfr_traceability:")
            lines.append(f"    {nfr_id}:")
            lines.append("      # type MUST be one of 8 legal values listed below:")
            lines.append("      # Enforceable (mapped to gate dim):")
            lines.append("      #   performance, security, maintainability, reliability, testability")
            lines.append("      # Advisory (no scoring tool, auto-added to advisory_only):")
            lines.append("      #   deployability, scalability, usability")
            lines.append("      type: performance")
            lines.append('      target: "p95 < 200ms"  # use \">=N\" or \"≥N\" to raise the gate floor')
            lines.append("      module: app.processing.pipeline")
        elif f.name == "advisory_only":
            lines.append("  advisory_only: []  # AUTO-FILLED by parser — omit or leave []")
        elif f.name == "gate_score_overrides":
            lines.append("  gate_score_overrides: {}  # AUTO-DERIVED by parser — omit or leave {}")
        elif f.name == "fr_module_traceability":
            lines.append("  fr_module_traceability:  # EXAMPLE — one entry per FR")
            lines.append(f'    {fr_id}: "{module_example}"')
        elif f.name == "architecture_constraints":
            lines.append("  architecture_constraints:")
            lines.append('    - "no_circular_dependencies"')
        elif f.name == "high_risk_modules":
            lines.append("  high_risk_modules:")
            lines.append(f'    - "{module_example}"')
        else:
            raise RuntimeError(
                f"render_canonical_sab_template: unhandled SABSpec field {f.name!r} — "
                "add a render branch so the canonical template never silently drops a field"
            )
        lines.append("")

    return "\n".join(lines)


# Module-level constant: the canonical template with default placeholder values.
# Import this anywhere you need to embed a SAB block example — never hand-write.
SAB_BLOCK_TEMPLATE: str = render_canonical_sab_template()


def validate_sab_block(sad_path) -> list[str]:
    """Validate the SAB block in SAD.md. Returns list of human-readable error
    strings (empty list = valid).

    Covers:
    - parse errors (bad YAML, bad phase type, missing markers)
    - unknown NFR type values (not in ALL_NFR_TYPES)

    Does NOT flag missing optional fields (parser fills them with defaults).
    Use this from `generate_sab.py --validate` and CI hooks.
    """
    sad_path = Path(sad_path)
    try:
        spec = extract_sab_from_sad(sad_path)
    except RuntimeError as exc:
        return [f"PARSE ERROR: {exc}"]

    if spec is None:
        return [f"No <!-- SAB:START -->...<!-- SAB:END --> block found in {sad_path}"]

    errors: list[str] = []
    for nfr_id, nfr in spec.nfr_traceability.items():
        if not isinstance(nfr, dict):
            errors.append(f"nfr_traceability.{nfr_id} is not a mapping (got {type(nfr).__name__})")
            continue
        nfr_type = str(nfr.get("type", "")).lower()
        if nfr_type and nfr_type not in ALL_NFR_TYPES:
            errors.append(
                f"nfr_traceability.{nfr_id}.type={nfr_type!r} is not a legal NFR type. "
                f"Legal types: {', '.join(ALL_NFR_TYPES)}"
            )
    return errors
