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
  NFR types: see nfr_type_vocabulary() — enforceable values map to a gate
             dimension, advisory ones have no scoring tool and are auto-added to
             advisory_only. Not restated here: the list changed in Round 27 and
             every hand-copy of it then disagreed with the table.
  NFR dimension: nfr_traceability[*].dimension names the gate dimension
             directly and outranks the type guess. `none` = no automated scorer;
             a name no gate scores raises rather than being silently dropped.

For the canonical template, call render_canonical_sab_template() — do not
hand-write the YAML anywhere else.  SABSpec is the type-level authority for
field names and types; this docstring is the human-readable projection.
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field, fields as _dc_fields
from pathlib import Path
from typing import Optional

import yaml

from core.quality_gate.gate_thresholds import load_gate_thresholds


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
    # Round 68 站1: repo-relative paths this project must ship, checked against
    # the delivered tree at every finalize (core.quality_gate.required_artifacts).
    required_artifacts: list = field(default_factory=list)

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
            "required_artifacts": self.required_artifacts,
        }


# Canonical map from SAD.md nfr_traceability `type` values to ACTUAL harness gate
# dimension names (must exist in the gate config 14-dimension set — otherwise the NFR
# maps to a non-existent dimension and is silently un-enforced).
#
# Round 27 站2b: this table had five entries, so five of sixteen dimensions were
# the only ones any NFR could name — and every dimension taskq-plus's SPEC was
# written to light up (architecture_constraints, license_compliance,
# mutation_testing, integration_coverage, execute_verification_target) was in
# the other eleven. That SPEC stated `dimension:` for all twelve of its NFRs;
# with no way for those words to reach the parser, the framework keyword-matched
# from `type:` instead and got six of twelve wrong.
#
# The direct route is now `dimension:` on the NFR entry itself (see
# extract_sab_from_sad), which accepts any dimension a gate scores. This table
# remains the fallback for specs that give a `type:` and nothing else, so it
# covers the dimensions an NFR plausibly describes — not all sixteen. The ones
# absent by design (linting / type_safety / test_coverage / secrets_scanning /
# architecture) are what the framework applies to every project regardless; a
# project that genuinely wants a floor on one of them says so with `dimension:`.
_NFR_TYPE_TO_DIM: dict[str, str] = {
    "performance":     "performance",             # native gate dimension
    "security":        "security",                # native gate dimension
    "maintainability": "readability",             # radon-mi maintainability index
    "reliability":     "error_handling",          # try/except coverage ≈ reliability
    "testability":     "test_assertion_quality",  # assertion quality ≈ testability
    "layering":        "architecture_constraints",  # import-linter layer contracts
    "licensing":       "license_compliance",      # dependency licence allowlist
    "mutation":        "mutation_testing",        # mutmut kill rate
    "integration":     "integration_coverage",    # cross-module suite coverage
    "verifiability":   "execute_verification_target",  # make verify-system
    "documentation":   "documentation",           # docstring coverage
}

# NFR types with NO corresponding automated scoring tool. They are still recorded in
# nfr_traceability (context injection + human review) but are NOT mapped to any gate
# dimension — honestly surfaced as advisory_only rather than faking enforcement.
_NFR_ADVISORY_TYPES: frozenset[str] = frozenset(
    {"deployability", "scalability", "usability"}
)

# Values of a per-NFR `dimension:` that mean "there is no automated scorer for
# this" — accepted and skipped rather than refused, so an accurate statement is
# never punished into becoming a plausible wrong answer.
_NFR_NO_DIMENSION: frozenset[str] = frozenset({"none", "n/a", "na", "-"})

# Standard gate-4 dimension thresholds — the floor an NFR-backed dimension must
# clear. Read from gate4_p6_full.yaml, the file HarnessBridge actually scores
# against, rather than hand-copied and parity-tested: a copy that must be kept
# in sync is a copy that eventually is not (Round 18 站2).
_GATE_DIMENSION_STANDARD: dict[str, float] = load_gate_thresholds(4)

# Gate 1 (per-FR checkpoint) thresholds, from gate1_per_fr.yaml. Deliberately
# a separate read from _GATE_DIMENSION_STANDARD rather than the same table:
# Gate 1 sets linting/type_safety to 100 (both are boolean in nature — a lint
# violation or type error either exists or it doesn't) while Gate 2/3/4 stay at
# 90/85, and those feed different things (this one feeds the GATE1 dispatch
# prompt; the gate-4 one floors the NFR-backed gate_score_overrides in
# derive_gate_score_overrides below). Reading each gate's own YAML keeps that
# distinction where it is enforced instead of encoding it here.
_GATE1_DIMENSION_STANDARD: dict[str, float] = load_gate_thresholds(1)

# Only an explicit "at least N" target (≥N / >=N) is read as a dimension-score floor.
# Free-form targets like "p95 < 3s" are intentionally NOT parsed (different semantics).
_NFR_TARGET_NUM_RE = re.compile(r"(?:≥|>=)\s*(\d+(?:\.\d+)?)")

# A dimension score is 0-100. Anything above that is not one, whatever the
# sentence around it says, and admitting it produces a floor no score can ever
# clear — a gate that can only block, with a message naming a "threshold" that
# is really a millisecond budget. That exact failure happened once already on
# the sibling `quality_targets` path (harness_bridge.py:3733, `p95_latency_ms`
# → performance floor 3000) and was fixed there and only there.
#
# Round 46 站4 measured the four live projects before touching this. The parse
# is load-bearing and correct three times — taskq-plus test_assertion_quality
# 80 (standard 70), taskq-renew and taskq-advance integration_coverage 80
# (standard 75) — so the planned deletion would have LOWERED three projects'
# floors, and a "%-suffix required" rule would have discarded five more
# correct floors written without the sign ("MI >= 80", "mutation score >= 70").
# The refusal is therefore narrow: reject the impossible, keep the rest
# byte-identical.
_MAX_DIMENSION_SCORE = 100.0

# The standard floor for every dimension ANY gate scores, gate 4 first.
#
# Round 27 站2: derive_gate_score_overrides consulted gate 4 alone, and three
# dimensions were not in gate 4 at all — architecture_constraints lives only in
# gate 1, and execute_verification_target lived only in gate 2 until Round 46
# 站5 put it back into gates 3 and 4. Their floor came back None
# and the loop skipped them, so even a correct NFR mapping produced no override.
# Measured during station 0 on a probe SAD: NFR-01 mapped to
# architecture_constraints and gate_score_overrides was {}.
#
# Gate 4 keeps precedence where it declares a dimension (it is the full-project
# audit, and its numbers are what the existing overrides were calibrated
# against); the earlier gates only fill in what gate 4 never scores.
_ALL_GATE_DIMENSION_STANDARD: dict[str, float] = {
    **load_gate_thresholds(3),
    **load_gate_thresholds(2),
    **_GATE1_DIMENSION_STANDARD,
    **_GATE_DIMENSION_STANDARD,
}


def nfr_type_vocabulary() -> tuple[list[str], list[str]]:
    """The legal `type:` values, as (enforceable, advisory) sorted lists.

    Round 27 站2b: the vocabulary was hand-listed in six places — this module's
    docstring, the canonical template, two spots in spec_phase2.py's P2 prompt,
    plangen's artifact_parsers, and P2_SOP.md — each of which said "8 legal
    values". Widening the table would have left five of those saying otherwise,
    so the generators now interpolate this instead of restating it.
    """
    return sorted(_NFR_TYPE_TO_DIM), sorted(_NFR_ADVISORY_TYPES)


def nfr_type_vocabulary_inline() -> str:
    """The same vocabulary as one slash-separated string for prompt text."""
    enforceable, advisory = nfr_type_vocabulary()
    return "/".join(enforceable + advisory)


def scoreable_dimension_names() -> frozenset[str]:
    """Every dimension name some gate config actually scores.

    The one answer to "is this a real dimension" — read from the YAML the gates
    are driven by, so a dimension renamed there cannot leave a stale allowlist
    behind (the Round 18 站2 rule, and the reason the 14-name literal that used
    to live in test_sab_parser.py is gone).
    """
    return frozenset(_ALL_GATE_DIMENSION_STANDARD)


def derive_gate_score_overrides(nfr_dim_mapping: dict, nfr_traceability: dict) -> dict:
    """(7) SAB enforcement: turn NFR-backed dimensions into gate_score_overrides
    (threshold floors, only-raise). An NFR mapped to a dimension means that dimension
    must clear at least its standard gate threshold — a da_waiver cannot drop it below.
    If the NFR target carries an explicit "≥N" floor and N is a possible
    dimension score, the stricter value wins; an N above 100 is refused and
    reported, because the target's unit is not stated anywhere and a floor
    nothing can reach is worse than no floor at all (see `_MAX_DIMENSION_SCORE`).
    """
    overrides: dict = {}
    for nfr_id, dim in nfr_dim_mapping.items():
        floor = _ALL_GATE_DIMENSION_STANDARD.get(dim)
        if floor is None:
            continue
        v = nfr_traceability.get(nfr_id)
        if isinstance(v, dict):
            m = _NFR_TARGET_NUM_RE.search(str(v.get("target", "")))
            if m:
                claimed = float(m.group(1))
                if claimed <= _MAX_DIMENSION_SCORE:
                    floor = max(floor, claimed)
                else:
                    print(
                        f"[harness] {nfr_id}: target asks for '>= {m.group(1)}', "
                        f"which is not a 0-100 {dim} score — its unit is not "
                        f"stated and it cannot be used as a threshold floor. "
                        f"{dim} keeps its standard floor of {floor}. Express a "
                        f"stricter floor as a score, or declare it directly in "
                        f"the SAB's gate_score_overrides.",
                        file=sys.stderr,
                    )
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

    # Resolve each NFR to a gate dimension, most specific source first:
    #   1. an explicit block-level nfr_dimension_mapping
    #   2. `dimension:` on the NFR's own traceability entry  (Round 27 站2a)
    #   3. the _NFR_TYPE_TO_DIM fallback keyed on `type:`
    #
    # (2) is what a spec means when it writes `- **dimension**: license_compliance`
    # under an NFR. Before this, the P2 prompt ordered the agent to leave
    # nfr_dimension_mapping empty and there was no per-entry field, so those words
    # had no route into the parser at all and (3) — five keyword pairs — decided
    # instead. taskq-plus stated a dimension for all twelve of its NFRs and the
    # framework got six of them wrong.
    if not nfr_dim_mapping and nfr_traceability:
        nfr_dim_mapping = {}
        for nfr_id, v in nfr_traceability.items():
            if not isinstance(v, dict):
                continue
            declared = str(v.get("dimension", "") or "").strip().lower()
            if declared in _NFR_NO_DIMENSION:
                # The honest channel: this requirement has no automated scorer.
                # It must not raise — closing this path is how you get a
                # plausible-looking wrong dimension in place of an accurate
                # statement that there is none.
                continue
            if declared:
                nfr_dim_mapping[nfr_id] = declared
                continue
            _type = str(v.get("type", "") or "").lower()
            if _type in _NFR_TYPE_TO_DIM:
                nfr_dim_mapping[nfr_id] = _NFR_TYPE_TO_DIM[_type]

    # Refuse a dimension no gate scores instead of dropping it.
    #
    # Silent omission is what made the previous testbed's NFR-06 disappear: it
    # was labelled `deployability`, nothing scores that, and the entry simply was
    # not in the mapping — 10 NFRs, 8 mapped, no message anywhere. A name that
    # looks like a dimension but is not one is a mistake worth stopping P2 for;
    # `dimension: none` (above) remains the way to say there is no scorer.
    _real = scoreable_dimension_names()
    _bogus = {n: d for n, d in nfr_dim_mapping.items() if d not in _real}
    if _bogus:
        raise RuntimeError(
            "Invalid nfr dimension in SAB block ({}): {} — not a dimension any "
            "gate scores. Valid: {}. Use `dimension: none` when the requirement "
            "genuinely has no automated scorer.".format(
                sad_path,
                ", ".join(f"{n}={d!r}" for n, d in sorted(_bogus.items())),
                ", ".join(sorted(_real)),
            )
        )

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
        required_artifacts=sab_data.get("required_artifacts", []),
    )


# ─────────────────────────────────────────────────────────────────────────────
# Canonical template factory — SINGLE SOURCE OF TRUTH
# ─────────────────────────────────────────────────────────────────────────────
# All places that show a "SAB block example" to humans (templates/SAD.md,
# generate_full_plan.py, docs/P2_SOP.md) MUST render this function instead of
# hand-writing YAML. The template is generated from SABSpec dataclass fields so
# adding a new field propagates automatically to every rendered template.

# Round 27 站2b: these were two hand-written tuples stating the same thing as
# _NFR_TYPE_TO_DIM's keys and _NFR_ADVISORY_TYPES' members. Widening the mapping
# table left them behind for the length of one edit, and validate_sab_block —
# which rejects "unknown NFR type" against ALL_NFR_TYPES — would have refused
# every new type the parser had just learned to map. Derived now, so there is
# one place a type is legal.
_NFR_TYPES_ENFORCEABLE: tuple[str, ...] = tuple(sorted(_NFR_TYPE_TO_DIM))
_NFR_TYPES_ADVISORY: tuple[str, ...] = tuple(sorted(_NFR_ADVISORY_TYPES))
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
    never drift.  Every legal NFR type value is enumerated explicitly from
    nfr_type_vocabulary() — no ellipsis, and no count restated in prose.
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
            # Round 39 Station 1a: declare every layer referenced by
            # allowed_dependencies. The example depends on 'service', so
            # 'service' must be a declared layer — otherwise the new
            # validator flags it. A single-layer SAB would write
            # allowed_dependencies: []; this two-layer shape teaches the
            # multi-layer pattern.
            lines.append("    - name: service")
            lines.append('      modules: ["app.service.handlers"]')
            lines.append("      allowed_dependencies: []")
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
            _enforceable, _advisory = nfr_type_vocabulary()
            lines.append("  nfr_traceability:")
            lines.append(f"    {nfr_id}:")
            lines.append(f"      # type MUST be one of {len(_enforceable) + len(_advisory)} legal values listed below:")
            lines.append("      # Enforceable (mapped to gate dim):")
            lines.append(f"      #   {', '.join(_enforceable)}")
            lines.append("      # Advisory (no scoring tool, auto-added to advisory_only):")
            lines.append(f"      #   {', '.join(_advisory)}")
            lines.append("      type: performance")
            # The legal dimension names are deliberately NOT enumerated here.
            # This template is embedded verbatim into templates/SAD.md, every
            # phase plan and the P2 prompt, so a list of eighteen names appears
            # in each — and one of those copies landed the word "mutation_testing"
            # in phase2_plan.md, where a check meant for "the plan gives fix
            # advice for mutation_testing" matched it. The prompt lists them for
            # the agent at authoring time and the parser's refusal message lists
            # them at failure time; those are the two moments the list is useful.
            lines.append("      # dimension: OPTIONAL and PREFERRED — the gate dimension this NFR")
            lines.append("      #   is scored by, copied verbatim from SPEC.md's own `dimension:`")
            lines.append("      #   for this NFR. Outranks the type guess above. `none` = no")
            lines.append("      #   automated scorer. A name no gate scores is REFUSED (the error")
            lines.append("      #   lists the legal names), never silently dropped.")
            lines.append('      target: "p95 < 200ms"  # use \">=N\" or \"≥N\" to raise the gate floor')
            lines.append("      module: app.processing.pipeline")
        elif f.name == "advisory_only":
            lines.append("  advisory_only: []  # AUTO-FILLED by parser — omit or leave []")
        elif f.name == "gate_score_overrides":
            lines.append("  gate_score_overrides: {}  # AUTO-DERIVED by parser — omit or leave {}")
        elif f.name == "fr_module_traceability":
            lines.append("  fr_module_traceability:  # EXAMPLE — one entry per FR")
            lines.append("    # If an FR owns MULTIPLE modules, use a YAML list instead of a single")
            lines.append('    # string, e.g. FR-02: ["app.a", "app.b"] — both forms are supported.')
            lines.append(f'    {fr_id}: "{module_example}"')
        elif f.name == "architecture_constraints":
            lines.append("  architecture_constraints:")
            lines.append('    - "no_circular_dependencies"')
        elif f.name == "high_risk_modules":
            lines.append("  high_risk_modules:")
            lines.append(f'    - "{module_example}"')
        elif f.name == "required_artifacts":
            # Round 68 站1. The paths are checked against the delivered tree at
            # every finalize, so the example has to be a shape a real project
            # writes: config files a spec calls mandatory, whose absence
            # otherwise turns the dimensions they feed into free points.
            lines.append("  required_artifacts:  # repo-relative paths this project MUST ship")
            lines.append("    # Checked against the delivered tree at every gate. A path that")
            lines.append("    # is absent, or that ships somewhere other than where it is")
            lines.append("    # declared, blocks and the message says which. Omit or leave []")
            lines.append("    # if the spec names no mandatory files.")
            lines.append('    - ".env.example"')
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
    # Round 29 Station 2a: collect valid layer names for scope_layers validation
    valid_layer_names: set[str] = {
        lyr.get("name", "")
        for lyr in spec.layers
        if isinstance(lyr, dict) and lyr.get("name")
    }
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
        # Round 29 Station 2a: scope_layers must reference existing layer names.
        # An unknown layer name means the SAB drifted or was hand-edited
        # incorrectly — raise rather than silently dropping the scope.
        declared_scope = nfr.get("scope_layers", [])
        if isinstance(declared_scope, list):
            for sl in declared_scope:
                if sl not in valid_layer_names:
                    errors.append(
                        f"nfr_traceability.{nfr_id}.scope_layers: "
                        f"{sl!r} is not a declared layer name. "
                        f"Valid layers: {', '.join(sorted(valid_layer_names))}"
                    )

    # Round 39 Station 1a: per-layer allowed_dependencies must reference
    # declared layer names. Mirrors the scope_layers check above: an
    # unknown layer name means the SAB drifted or was hand-edited
    # incorrectly — raise rather than silently dropping the dep, which
    # produces a SAB.json that preflight_sab_check (phase_hooks.py:644-648)
    # would then flag at P3+ pre-push (late, only at push time, never at
    # advance-phase). Catching it here means generate_sab.py's pre-write
    # validate (line 243) refuses to write the bad block in the first place.
    # Observed on taskq-api 2026-08-05: SAD.md wrote 'errors'/'config'
    # (module names) where 'independence' (the actual layer name) belonged.
    for _layer_idx, _layer in enumerate(spec.layers):
        if not isinstance(_layer, dict):
            continue
        _layer_name = _layer.get("name", "")
        for _dep_name in _layer.get("allowed_dependencies", []) or []:
            if not isinstance(_dep_name, str):
                errors.append(
                    f"layers[{_layer_idx}].allowed_dependencies: "
                    f"non-string entry {_dep_name!r}"
                )
                continue
            if _dep_name not in valid_layer_names:
                errors.append(
                    f"layers[{_layer_idx}] ({_layer_name!r}).allowed_dependencies: "
                    f"{_dep_name!r} is not a declared layer name. "
                    f"Valid layers: {', '.join(sorted(valid_layer_names))}"
                )

    # Round 39 Station 1a (cont.): top-level allowed_dependencies: [{from, to}]
    # — same rule, both sides of the pair must be declared layers. This is
    # the data shape that SABSpec.to_dict() (line 74-107) faithfully copies
    # into SAB.json's "dependencies" dict; an invalid reference there would
    # reach preflight_sab_check only at P3+ pre-push.
    for _dep_idx, _dep in enumerate(spec.allowed_dependencies):
        if not isinstance(_dep, dict):
            errors.append(
                f"allowed_dependencies[{_dep_idx}]: expected mapping, "
                f"got {type(_dep).__name__}"
            )
            continue
        _from = _dep.get("from", "")
        _to = _dep.get("to", "")
        if not isinstance(_from, str) or not _from:
            errors.append(
                f"allowed_dependencies[{_dep_idx}]: missing or non-string 'from'"
            )
        elif _from not in valid_layer_names:
            errors.append(
                f"allowed_dependencies[{_dep_idx}].from: {_from!r} is not a "
                f"declared layer name. Valid layers: {', '.join(sorted(valid_layer_names))}"
            )
        if not isinstance(_to, str) or not _to:
            errors.append(
                f"allowed_dependencies[{_dep_idx}]: missing or non-string 'to'"
            )
        elif _to not in valid_layer_names:
            errors.append(
                f"allowed_dependencies[{_dep_idx}].to: {_to!r} is not a "
                f"declared layer name. Valid layers: {', '.join(sorted(valid_layer_names))}"
            )

    return errors
