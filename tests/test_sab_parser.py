"""Unit tests for core.quality_gate.sab_parser."""
import pytest


import json

from core.quality_gate.sab_parser import SABSpec, extract_sab_from_sad, _NFR_TYPE_TO_DIM


_MINIMAL_SAD = """\
# SAD

## §6 SAB (Software Architecture Baseline)

<!-- SAB:START -->
```yaml
sab:
  version: "2.0"
  created_at: "2026-05-17"
  phase: 2
  project: "omnibot"

  layers:
    - name: api
      modules:
        - FastAPIApp
        - ApiResponse
    - name: data_layer
      modules:
        - users
        - messages

  allowed_dependencies:
    - from: api
      to: data_layer

  quality_targets:
    p95_latency_ms: 1500
```
<!-- SAB:END -->
"""

_SAD_NO_BLOCK = "# SAD\n\nNo SAB here.\n"

_SAD_WITH_EXTRAS = """\
## §6

<!-- SAB:START -->
```yaml
sab:
  version: "1.5"
  phase: 3
  project: "myapp"
  layers:
    - name: core
      modules: [A, B]
    - name: infra
      modules: [DB]
  allowed_dependencies:
    - from: core
      to: infra
  nfr_dimension_mapping:
    NFR-01: security
  architecture_constraints:
    - no_direct_db_from_api
  high_risk_modules:
    - A
```
<!-- SAB:END -->
"""


class TestExtractSabFromSad:

    def test_returns_none_when_no_block(self, tmp_path):
        sad = tmp_path / "SAD.md"
        sad.write_text(_SAD_NO_BLOCK)
        assert extract_sab_from_sad(sad) is None

    def test_parses_minimal_block(self, tmp_path):
        sad = tmp_path / "SAD.md"
        sad.write_text(_MINIMAL_SAD)
        spec = extract_sab_from_sad(sad)
        assert spec is not None
        assert spec.version == "2.0"
        assert spec.phase == 2
        assert spec.project == "omnibot"
        assert len(spec.layers) == 2

    def test_modules_property_flattened(self, tmp_path):
        sad = tmp_path / "SAD.md"
        sad.write_text(_MINIMAL_SAD)
        spec = extract_sab_from_sad(sad)
        assert "FastAPIApp" in spec.modules  # type: ignore[reportOptionalMemberAccess]
        assert "users" in spec.modules  # type: ignore[reportOptionalMemberAccess]
        assert len(spec.modules) == 4  # type: ignore[reportOptionalMemberAccess]

    def test_extra_fields(self, tmp_path):
        sad = tmp_path / "SAD.md"
        sad.write_text(_SAD_WITH_EXTRAS)
        spec = extract_sab_from_sad(sad)
        assert spec.nfr_dimension_mapping == {"NFR-01": "security"}  # type: ignore[reportOptionalMemberAccess]
        assert spec.architecture_constraints == ["no_direct_db_from_api"]  # type: ignore[reportOptionalMemberAccess]
        assert spec.high_risk_modules == ["A"]  # type: ignore[reportOptionalMemberAccess]

    def test_raises_on_corrupt_yaml(self, tmp_path):
        sad = tmp_path / "SAD.md"
        sad.write_text("<!-- SAB:START -->\n```yaml\n: [\nbad yaml\n```\n<!-- SAB:END -->")
        with pytest.raises(RuntimeError, match="Failed to parse SAB YAML"):
            extract_sab_from_sad(sad)

    def test_raises_on_invalid_phase(self, tmp_path):
        sad = tmp_path / "SAD.md"
        sad.write_text(
            "<!-- SAB:START -->\n```yaml\nsab:\n  phase: not-a-number\n  project: x\n```\n<!-- SAB:END -->"
        )
        with pytest.raises(RuntimeError, match="Invalid 'phase'"):
            extract_sab_from_sad(sad)

    def test_parses_block_without_code_fence(self, tmp_path):
        """SAB block may contain raw YAML without a ``` fence."""
        sad = tmp_path / "SAD.md"
        sad.write_text(
            "<!-- SAB:START -->\nsab:\n  version: '3.0'\n  phase: 4\n  project: raw\n<!-- SAB:END -->"
        )
        spec = extract_sab_from_sad(sad)
        assert spec is not None
        assert spec.version == "3.0"
        assert spec.phase == 4
        assert spec.project == "raw"

    def test_file_not_found_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            extract_sab_from_sad(tmp_path / "missing.md")

    def test_accepts_path_string(self, tmp_path):
        sad = tmp_path / "SAD.md"
        sad.write_text(_MINIMAL_SAD)
        spec = extract_sab_from_sad(str(sad))
        assert spec is not None


class TestSABSpecToDict:

    def _make_spec(self) -> SABSpec:
        return SABSpec(
            version="2.0",
            created_at="2026-05-17",
            phase=2,
            project="omnibot",
            layers=[
                {"name": "api", "modules": ["FastAPIApp"]},
                {"name": "data_layer", "modules": ["users"]},
            ],
            allowed_dependencies=[
                {"from": "api", "to": "data_layer"},
            ],
            quality_targets={"p95_latency_ms": 1500},
            nfr_dimension_mapping={"NFR-01": "security"},
            architecture_constraints=["no_cycles"],
            high_risk_modules=["FastAPIApp"],
        )

    def test_to_dict_top_level_keys(self):
        d = self._make_spec().to_dict()
        for key in ("version", "created_at", "phase", "project", "layers",
                    "dependencies", "quality_targets", "nfr_dimension_mapping",
                    "nfr_traceability", "fr_module_traceability",
                    "architecture_constraints", "high_risk_modules"):
            assert key in d, f"Missing key: {key}"

    def test_to_dict_dependencies_built_from_allowed(self):
        d = self._make_spec().to_dict()
        assert d["dependencies"] == {"api": ["data_layer"]}

    def test_to_dict_layers_include_allowed_deps(self):
        d = self._make_spec().to_dict()
        api_layer = next(layer for layer in d["layers"] if layer["name"] == "api")
        assert api_layer["allowed_dependencies"] == ["data_layer"]
        data_layer = next(layer for layer in d["layers"] if layer["name"] == "data_layer")
        assert data_layer["allowed_dependencies"] == []

    def test_to_dict_is_json_serialisable(self):
        d = self._make_spec().to_dict()
        raw = json.dumps(d)
        assert json.loads(raw) == d

    def test_empty_spec_defaults(self):
        d = SABSpec().to_dict()
        assert d["layers"] == []
        assert d["dependencies"] == {}
        assert d["nfr_dimension_mapping"] == {}
        assert d["architecture_constraints"] == []
        assert d["high_risk_modules"] == []


_SAD_NFR_TRACEABILITY_ONLY = """\
<!-- SAB:START -->
```yaml
sab:
  version: "1.0"
  phase: 2
  project: "testapp"
  nfr_traceability:
    NFR-01:
      type: performance
      target: "p95 < 200ms"
      module: app.pipeline
    NFR-02:
      type: security
      target: "reject unsigned webhooks"
      module: app.security
    NFR-09:
      type: deployability
      target: "docker compose up within 60s"
      module: docker-compose.yml
    NFR-99:
      type: unknown_future_type
      target: "some new requirement"
      module: app.future
```
<!-- SAB:END -->
"""

# Round 27 站2: per-NFR `dimension:` — what SPEC.md actually states, carried
# through instead of re-derived. NFR-01 deliberately omits it so the type table
# still has to answer for one entry.
_SAD_PER_NFR_DIMENSION = """\
<!-- SAB:START -->
```yaml
sab:
  version: "1.0"
  phase: 2
  project: "testapp"
  nfr_traceability:
    NFR-01:
      type: performance
      target: "p95 < 200ms"
      module: app.pipeline
    NFR-06:
      type: maintainability
      dimension: architecture_constraints
      target: "lint-imports exit 0"
      module: app.layers
    NFR-07:
      type: maintainability
      dimension: license_compliance
      target: "all deps MIT/BSD/Apache-2.0"
      module: requirements.txt
    NFR-08:
      type: testability
      dimension: mutation_testing
      target: "≥70"
      module: app.service
```
<!-- SAB:END -->
"""

_SAD_BOTH_NFR_FIELDS = """\
<!-- SAB:START -->
```yaml
sab:
  version: "1.0"
  phase: 2
  project: "testapp"
  nfr_dimension_mapping:
    # Round 27 站2: was `reliability` — a TYPE name, not a dimension name, and
    # nothing checked. That this fixture could carry it for as long as it did is
    # the defect this station closes, so the value is now a real dimension.
    NFR-01: error_handling
  nfr_traceability:
    NFR-01:
      type: performance
      target: "p95 < 200ms"
      module: app.pipeline
    NFR-02:
      type: security
      target: "reject unsigned webhooks"
      module: app.security
```
<!-- SAB:END -->
"""


class TestNfrTraceability:
    """Tests for nfr_traceability parsing and nfr_dimension_mapping auto-derivation."""

    def test_auto_derives_nfr_dim_mapping_from_traceability(self, tmp_path):
        """SAD with only nfr_traceability → nfr_dimension_mapping auto-derived."""
        sad = tmp_path / "SAD.md"
        sad.write_text(_SAD_NFR_TRACEABILITY_ONLY)
        spec = extract_sab_from_sad(sad)
        assert spec.nfr_dimension_mapping.get("NFR-01") == "performance"  # type: ignore[reportOptionalMemberAccess]
        assert spec.nfr_dimension_mapping.get("NFR-02") == "security"  # type: ignore[reportOptionalMemberAccess]
        # deployability has no scoring tool → advisory_only, NOT in the dimension mapping
        assert "NFR-09" not in spec.nfr_dimension_mapping  # type: ignore[reportOptionalMemberAccess]
        assert "deployability" in spec.advisory_only  # type: ignore[reportOptionalMemberAccess]

    def test_unknown_nfr_type_silently_omitted(self, tmp_path):
        """NFRs with a type not in _NFR_TYPE_TO_DIM are excluded from auto-derivation."""
        sad = tmp_path / "SAD.md"
        sad.write_text(_SAD_NFR_TRACEABILITY_ONLY)
        spec = extract_sab_from_sad(sad)
        assert "NFR-99" not in spec.nfr_dimension_mapping  # type: ignore[reportOptionalMemberAccess]

    def test_explicit_nfr_dim_mapping_wins_over_traceability(self, tmp_path):
        """When both fields present, explicit nfr_dimension_mapping is NOT overwritten."""
        sad = tmp_path / "SAD.md"
        sad.write_text(_SAD_BOTH_NFR_FIELDS)
        spec = extract_sab_from_sad(sad)
        # NFR-01's type is 'performance' but the explicit mapping says
        # 'error_handling' → the explicit value must survive.
        assert spec.nfr_dimension_mapping.get("NFR-01") == "error_handling"  # type: ignore[reportOptionalMemberAccess]
        # NFR-02 only in traceability, but auto-derive did NOT run → absent from dim mapping
        assert "NFR-02" not in spec.nfr_dimension_mapping  # type: ignore[reportOptionalMemberAccess]

    def test_traceability_stored_on_spec(self, tmp_path):
        """nfr_traceability is stored verbatim on SABSpec."""
        sad = tmp_path / "SAD.md"
        sad.write_text(_SAD_NFR_TRACEABILITY_ONLY)
        spec = extract_sab_from_sad(sad)
        assert spec.nfr_traceability["NFR-01"]["module"] == "app.pipeline"  # type: ignore[reportOptionalMemberAccess]
        assert spec.nfr_traceability["NFR-01"]["target"] == "p95 < 200ms"  # type: ignore[reportOptionalMemberAccess]

    def test_neither_field_gives_empty_dicts(self, tmp_path):
        """SAD with neither nfr_dimension_mapping nor nfr_traceability → both empty."""
        sad = tmp_path / "SAD.md"
        sad.write_text(_MINIMAL_SAD)
        spec = extract_sab_from_sad(sad)
        assert spec.nfr_dimension_mapping == {}  # type: ignore[reportOptionalMemberAccess]
        assert spec.nfr_traceability == {}  # type: ignore[reportOptionalMemberAccess]

    def test_to_dict_includes_nfr_traceability(self, tmp_path):
        """to_dict() must serialise nfr_traceability."""
        sad = tmp_path / "SAD.md"
        sad.write_text(_SAD_NFR_TRACEABILITY_ONLY)
        d = extract_sab_from_sad(sad).to_dict()  # type: ignore[reportOptionalMemberAccess]
        assert "nfr_traceability" in d
        assert d["nfr_traceability"]["NFR-02"]["module"] == "app.security"

    def test_nfr_type_to_dim_maps_to_real_gate_dimensions(self):
        """Every enforceable NFR type must map to a dimension some gate actually scores.

        Round 27 站2: the 14-dimension set used to be a literal here, which is the
        same fixture-and-rule-from-different-sources shape Round 19 closed
        elsewhere — a dimension renamed in the YAML would leave this test green
        while the mapping pointed at nothing. It now reads the gate configs.
        """
        from core.quality_gate.sab_parser import (
            _NFR_ADVISORY_TYPES, scoreable_dimension_names,
        )
        real = scoreable_dimension_names()
        assert {"performance", "security", "readability", "error_handling",
                "test_assertion_quality"} <= set(_NFR_TYPE_TO_DIM.values())
        for t, dim in _NFR_TYPE_TO_DIM.items():
            assert dim in real, f"{t}→{dim} is not a dimension any gate scores"
        for t in ("deployability", "scalability", "usability"):
            assert t in _NFR_ADVISORY_TYPES
            assert t not in _NFR_TYPE_TO_DIM

    def test_the_type_table_reaches_the_dimensions_it_used_to_orphan(self):
        """Round 27 站2b — the fallback vocabulary, widened.

        Five enforceable type values could name five dimensions, so eleven of
        sixteen were unreachable by any NFR — including every one taskq-plus's
        SPEC was written to light up. With no way to say it, the framework
        keyword-matched instead and got six of twelve mappings wrong.

        Station 2a makes `dimension:` the direct route and imposes no allowlist
        on it — an NFR may name any dimension a gate scores, including
        linting/test_coverage, because raising a floor on those is a legitimate
        thing for a project to want. This table is only the fallback for specs
        that state a `type:` and no dimension, so it covers the ones an NFR
        plausibly describes, not all sixteen.
        """
        reachable = set(_NFR_TYPE_TO_DIM.values())
        for dim in ("architecture_constraints", "license_compliance",
                    "mutation_testing", "integration_coverage",
                    "execute_verification_target", "documentation"):
            assert dim in reachable, f"no NFR type can name {dim} without an explicit dimension:"

    def test_per_nfr_dimension_field_beats_the_type_guess(self, tmp_path):
        """Round 27 站2a — what the spec states outranks what the framework infers.

        The P2 prompt used to order the agent to leave nfr_dimension_mapping
        empty, so a `dimension:` written in SPEC.md had no way to reach the
        parser at all and a five-entry keyword table decided instead.
        """
        sad = tmp_path / "SAD.md"
        sad.write_text(_SAD_PER_NFR_DIMENSION)
        spec = extract_sab_from_sad(sad)
        assert spec.nfr_dimension_mapping["NFR-06"] == "architecture_constraints"
        assert spec.nfr_dimension_mapping["NFR-07"] == "license_compliance"
        assert spec.nfr_dimension_mapping["NFR-08"] == "mutation_testing"
        # NFR-01 has no `dimension:` — the type table still answers for it.
        assert spec.nfr_dimension_mapping["NFR-01"] == "performance"

    def test_a_dimension_that_does_not_exist_is_refused(self, tmp_path):
        """Silently dropping it is what made the previous round's NFR-06 vanish."""
        sad = tmp_path / "SAD.md"
        sad.write_text(_SAD_PER_NFR_DIMENSION.replace(
            "dimension: architecture_constraints", "dimension: deployability"))
        with pytest.raises(RuntimeError, match="not a dimension any gate scores"):
            extract_sab_from_sad(sad)

    def test_an_honestly_unscoreable_nfr_still_has_a_way_through(self, tmp_path):
        """`dimension: none` is the honest channel and must not raise.

        Without it, the refusal above would punish an accurate statement that a
        requirement has no automated scorer — closing the honest path is how you
        get a plausible-looking wrong dimension instead.
        """
        sad = tmp_path / "SAD.md"
        sad.write_text(_SAD_PER_NFR_DIMENSION.replace(
            "dimension: architecture_constraints", "dimension: none"))
        spec = extract_sab_from_sad(sad)
        assert "NFR-06" not in spec.nfr_dimension_mapping

    def test_a_floor_exists_for_dimensions_outside_gate_4(self):
        """Round 27 站2 — derive_gate_score_overrides only ever consulted gate 4.

        architecture_constraints appears only in gate 1 and
        execute_verification_target only in gate 2, so both scored `floor is
        None` and were skipped: even a correct mapping produced no threshold
        floor. Measured on a probe SAD during station 0 — gate_score_overrides
        came back {} with NFR-01 correctly mapped to architecture_constraints.
        """
        from core.quality_gate.sab_parser import derive_gate_score_overrides
        got = derive_gate_score_overrides(
            {"NFR-06": "architecture_constraints",
             "NFR-12": "execute_verification_target"},
            {"NFR-06": {"type": "maintainability", "target": "lint-imports exit 0"},
             "NFR-12": {"type": "testability", "target": "make verify-system"}},
        )
        assert got == {"architecture_constraints": 100.0,
                       "execute_verification_target": 100.0}

    def test_derive_gate_score_overrides_standard_floor(self):
        """NFR-mapped dimension → its standard gate-4 threshold floor; free-form
        targets ('p95 < 3s') are NOT parsed as a score floor."""
        from core.quality_gate.sab_parser import derive_gate_score_overrides
        m = {"NFR-01": "performance", "NFR-02": "security"}
        t = {"NFR-01": {"type": "performance", "target": "p95 < 3s"},
             "NFR-02": {"type": "security", "target": "no plaintext secrets"}}
        assert derive_gate_score_overrides(m, t) == {"performance": 75.0, "security": 80.0}

    def test_derive_gate_score_overrides_explicit_numeric_wins(self):
        """An explicit ≥N target raises the floor above the standard threshold."""
        from core.quality_gate.sab_parser import derive_gate_score_overrides
        m = {"NFR-01": "security"}
        t = {"NFR-01": {"type": "security", "target": "≥95"}}
        assert derive_gate_score_overrides(m, t) == {"security": 95.0}

    def test_extract_auto_derives_gate_score_overrides(self, tmp_path):
        """SABSpec.gate_score_overrides is auto-derived from nfr_dimension_mapping."""
        sad = tmp_path / "SAD.md"
        sad.write_text(_SAD_NFR_TRACEABILITY_ONLY)
        spec = extract_sab_from_sad(sad)
        assert spec.gate_score_overrides.get("performance") == 75.0  # type: ignore[reportOptionalMemberAccess]
        assert spec.gate_score_overrides.get("security") == 80.0  # type: ignore[reportOptionalMemberAccess]
        # advisory NFR (deployability) contributes no override
        assert "deployability" not in spec.gate_score_overrides  # type: ignore[reportOptionalMemberAccess]

    def test_gate_dimension_standard_in_sync_with_gate4_config(self):
        """_GATE_DIMENSION_STANDARD (the NFR floor source) must not drift from the
        gate-4 config thresholds it mirrors."""
        import yaml
        from pathlib import Path
        from core.quality_gate.sab_parser import _GATE_DIMENSION_STANDARD
        # Resolve the gate config path regardless of cwd (normal pytest or mutmut).
        # Under mutmut: __file__ = .../mutants/tests/test_sab_parser.py
        #   → parent.parent = .../mutants/ → gate4_yaml not there
        #   → fall back to parent.parent.parent = repo root ✓
        # Normal pytest: __file__ = .../tests/test_sab_parser.py
        #   → parent.parent = repo root ✓
        _test_dir = Path(__file__).resolve().parent
        _repo_root = _test_dir.parent
        if not (_repo_root / "harness" / "gate_configs").exists():
            _repo_root = _repo_root.parent  # mutmut: step up from mutants/
        gate4_yaml = _repo_root / "harness" / "gate_configs" / "gate4_p6_full.yaml"
        cfg = yaml.safe_load(gate4_yaml.read_text(encoding="utf-8"))
        for dim in cfg["dimensions"]:
            name, thr = dim["name"], float(dim["threshold"])
            assert _GATE_DIMENSION_STANDARD.get(name) == thr, \
                f"{name}: standard {_GATE_DIMENSION_STANDARD.get(name)} != gate4 {thr}"


_SAD_FR_MODULE_TRACEABILITY = """\
<!-- SAB:START -->
```yaml
sab:
  version: "1.0"
  phase: 3
  project: "testapp"
  fr_module_traceability:
    FR-01: app.models
    FR-02: app.api.webhooks
    FR-14: app.infrastructure.health
```
<!-- SAB:END -->
"""

_SAD_FR_MODULE_TRACEABILITY_LIST = """\
<!-- SAB:START -->
```yaml
sab:
  version: "1.0"
  phase: 3
  project: "testapp"
  fr_module_traceability:
    FR-01: app.models
    FR-05:
      - app.cli.main
      - app.cli.commands
```
<!-- SAB:END -->
"""


class TestFrModuleTraceability:
    """Tests for fr_module_traceability parsing and propagation."""

    def test_parses_fr_module_traceability(self, tmp_path):
        sad = tmp_path / "SAD.md"
        sad.write_text(_SAD_FR_MODULE_TRACEABILITY)
        spec = extract_sab_from_sad(sad)
        assert spec is not None
        assert spec.fr_module_traceability == {
            "FR-01": "app.models",
            "FR-02": "app.api.webhooks",
            "FR-14": "app.infrastructure.health",
        }

    def test_absent_fr_module_traceability_gives_empty_dict(self, tmp_path):
        sad = tmp_path / "SAD.md"
        sad.write_text(_MINIMAL_SAD)
        spec = extract_sab_from_sad(sad)
        assert spec is not None
        assert spec.fr_module_traceability == {}

    def test_to_dict_includes_fr_module_traceability(self, tmp_path):
        sad = tmp_path / "SAD.md"
        sad.write_text(_SAD_FR_MODULE_TRACEABILITY)
        spec = extract_sab_from_sad(sad)
        assert spec is not None
        d = spec.to_dict()
        assert "fr_module_traceability" in d
        assert d["fr_module_traceability"]["FR-01"] == "app.models"

    def test_to_dict_fr_module_traceability_json_serialisable(self, tmp_path):
        sad = tmp_path / "SAD.md"
        sad.write_text(_SAD_FR_MODULE_TRACEABILITY)
        spec = extract_sab_from_sad(sad)
        assert spec is not None
        raw = json.dumps(spec.to_dict())
        assert json.loads(raw)["fr_module_traceability"]["FR-14"] == "app.infrastructure.health"

    def test_parses_list_valued_fr_module_traceability(self, tmp_path):
        """An FR that legitimately owns multiple modules (SAD §6 maps it to
        more than one file) must round-trip as a YAML list, not collapse to
        a single string. Downstream consumers (gate_cmds._filter_phantoms_for_fr,
        cov_utils.resolve_fr_scoped_src_files) already accept str or list —
        this pins that the parser itself doesn't silently drop entries."""
        sad = tmp_path / "SAD.md"
        sad.write_text(_SAD_FR_MODULE_TRACEABILITY_LIST)
        spec = extract_sab_from_sad(sad)
        assert spec is not None
        assert spec.fr_module_traceability == {
            "FR-01": "app.models",
            "FR-05": ["app.cli.main", "app.cli.commands"],
        }

    def test_to_dict_list_valued_fr_module_traceability_json_serialisable(self, tmp_path):
        sad = tmp_path / "SAD.md"
        sad.write_text(_SAD_FR_MODULE_TRACEABILITY_LIST)
        spec = extract_sab_from_sad(sad)
        assert spec is not None
        raw = json.dumps(spec.to_dict())
        assert json.loads(raw)["fr_module_traceability"]["FR-05"] == [
            "app.cli.main", "app.cli.commands",
        ]


class TestRoundTrip:
    """parse SAD.md → SABSpec.to_dict() → write SAB.json → read back."""

    def test_roundtrip_via_file(self, tmp_path):
        sad = tmp_path / "SAD.md"
        sad.write_text(_MINIMAL_SAD)
        spec = extract_sab_from_sad(sad)
        sab_json = tmp_path / ".methodology" / "SAB.json"
        sab_json.parent.mkdir()
        sab_json.write_text(json.dumps(spec.to_dict(), indent=2))  # type: ignore[reportOptionalMemberAccess]

        data = json.loads(sab_json.read_text())
        assert data["phase"] == 2
        assert data["project"] == "omnibot"
        assert len(data["layers"]) == 2
        assert data["dependencies"] == {"api": ["data_layer"]}

class TestCanonicalTemplate:
    """The canonical template is the single source of truth — it MUST
    round-trip through the parser and cover all 14 SABSpec fields."""

    def test_template_contains_all_sabspec_fields(self):
        from core.quality_gate.sab_parser import SAB_BLOCK_TEMPLATE
        for field_name in (
            "version", "created_at", "phase", "project",
            "layers", "allowed_dependencies", "quality_targets",
            "nfr_dimension_mapping", "nfr_traceability",
            "advisory_only", "gate_score_overrides",
            "fr_module_traceability", "architecture_constraints",
            "high_risk_modules",
        ):
            assert field_name in SAB_BLOCK_TEMPLATE, (
                f"SABSpec field {field_name!r} missing from SAB_BLOCK_TEMPLATE"
            )

    def test_template_phase_is_int_not_string(self):
        from core.quality_gate.sab_parser import render_canonical_sab_template
        import yaml
        # Parse the template as YAML (wrapped in markers/fence) and confirm
        # the `phase` key is an int at parse time, not a string.
        block = render_canonical_sab_template()
        # Strip comment lines so the YAML parses cleanly.
        yaml_lines = [ln for ln in block.splitlines() if not ln.lstrip().startswith("#")]
        data = yaml.safe_load("\n".join(yaml_lines))
        sab = data.get("sab", data)
        assert isinstance(sab["phase"], int), (
            f"phase must be an int in the canonical template, got {type(sab['phase'])}"
        )

    def test_template_lists_all_eight_nfr_types(self):
        from core.quality_gate.sab_parser import SAB_BLOCK_TEMPLATE
        for t in (
            "performance", "security", "maintainability",
            "reliability", "testability",
            "deployability", "scalability", "usability",
        ):
            assert t in SAB_BLOCK_TEMPLATE, (
                f"NFR type {t!r} missing from SAB_BLOCK_TEMPLATE"
            )

    def test_template_round_trips_through_parser(self, tmp_path):
        from core.quality_gate.sab_parser import (
            render_canonical_sab_template, extract_sab_from_sad,
        )
        block = render_canonical_sab_template(
            project="rtproj", layer_example="L1",
            module_example="M1", fr_id="FR-99", nfr_id="NFR-99",
        )
        sad = tmp_path / "SAD.md"
        sad.write_text(f"<!-- SAB:START -->\n```yaml\n{block}\n```\n<!-- SAB:END -->")
        spec = extract_sab_from_sad(sad)
        assert spec is not None
        assert spec.project == "rtproj"
        assert spec.phase == 2
        assert spec.fr_module_traceability.get("FR-99") == "M1"

    def test_template_no_ellipsis_drift_guard(self):
        """Drift guard: NFR type list must not use '...' as a placeholder."""
        from core.quality_gate.sab_parser import SAB_BLOCK_TEMPLATE
        assert "..." not in SAB_BLOCK_TEMPLATE, (
            "NFR type list must enumerate all 8 values — '...' is not allowed"
        )

    def test_sad_template_sab_block_is_factory_snapshot(self):
        """templates/SAD.md §5 SAB block MUST be a verbatim snapshot of
        render_canonical_sab_template(). The static markdown cannot call the
        factory at runtime, so this test is the only guard against the two
        drifting apart (the exact failure this design set out to prevent)."""
        import re
        from pathlib import Path
        from core.quality_gate.sab_parser import render_canonical_sab_template

        sad_path = Path(__file__).resolve().parent.parent / "templates" / "SAD.md"
        text = sad_path.read_text(encoding="utf-8")
        m = re.search(
            r"<!-- SAB:START -->\n```yaml\n(.*?)\n```\n<!-- SAB:END -->",
            text, re.DOTALL,
        )
        assert m, "fenced ```yaml SAB block not found in templates/SAD.md"
        assert m.group(1).strip("\n") == render_canonical_sab_template().strip("\n"), (
            "templates/SAD.md §5 SAB block has drifted from "
            "render_canonical_sab_template() — re-paste the factory output."
        )

    def test_p2_sop_sab_example_stays_structurally_in_sync(self, tmp_path):
        """docs/P2_SOP.md ships a hand-written 8-NFR-type SAB example (Chinese
        comments + concrete values, richer than the single-NFR factory output),
        so it can't be a verbatim factory snapshot. It MUST stay structurally in
        sync: parseable, validate-clean, all 8 NFR types shown, and every SABSpec
        field present — so a new field or NFR-type change is caught here too."""
        import re
        from pathlib import Path
        from dataclasses import fields
        from core.quality_gate.sab_parser import (
            SABSpec, extract_sab_from_sad, validate_sab_block, ALL_NFR_TYPES,
        )

        sop_path = Path(__file__).resolve().parent.parent / "docs" / "P2_SOP.md"
        sop = sop_path.read_text(encoding="utf-8")
        m = re.search(r"```yaml\n(sab:\n.*?)\n```", sop, re.DOTALL)
        assert m, "sab: YAML example not found in docs/P2_SOP.md"
        block = m.group(1)

        # Run the real parser + validator over the example (wrapped in markers).
        sad = tmp_path / "SAD.md"
        sad.write_text(f"<!-- SAB:START -->\n```yaml\n{block}\n```\n<!-- SAB:END -->")
        spec = extract_sab_from_sad(sad)
        assert spec is not None
        assert validate_sab_block(sad) == []

        # Teaching intent: the example demonstrates all 8 legal NFR types.
        used_types = {v["type"] for v in spec.nfr_traceability.values()}
        assert used_types == set(ALL_NFR_TYPES), (
            "docs/P2_SOP.md example must show all 8 NFR types; missing "
            f"{set(ALL_NFR_TYPES) - used_types}"
        )

        # Structural drift guard: every SABSpec field appears in the example.
        for f in fields(SABSpec):
            assert f.name in block, (
                f"SABSpec field {f.name!r} missing from docs/P2_SOP.md SAB example"
            )


class TestValidateSabBlock:
    """validate_sab_block() returns list[str] of errors (empty = valid)."""

    def test_valid_canonical_block_returns_no_errors(self, tmp_path):
        from core.quality_gate.sab_parser import (
            render_canonical_sab_template, validate_sab_block,
        )
        sad = tmp_path / "SAD.md"
        sad.write_text(
            "<!-- SAB:START -->\n```yaml\n"
            + render_canonical_sab_template()
            + "\n```\n<!-- SAB:END -->"
        )
        assert validate_sab_block(sad) == []

    def test_unknown_nfr_type_flagged(self, tmp_path):
        from core.quality_gate.sab_parser import validate_sab_block
        sad = tmp_path / "SAD.md"
        sad.write_text(
            "<!-- SAB:START -->\n```yaml\nsab:\n  phase: 2\n  project: x\n"
            "  nfr_traceability:\n    NFR-01:\n"
            "      type: nonexitype\n      target: 'n/a'\n      module: x\n"
            "```\n<!-- SAB:END -->"
        )
        errors = validate_sab_block(sad)
        assert any("nfr_traceability.NFR-01.type" in e and "nonexitype" in e for e in errors)

    def test_corrupt_yaml_returns_parse_error(self, tmp_path):
        from core.quality_gate.sab_parser import validate_sab_block
        sad = tmp_path / "SAD.md"
        sad.write_text("<!-- SAB:START -->\n```yaml\n: [\nbad\n```\n<!-- SAB:END -->")
        errors = validate_sab_block(sad)
        assert any("PARSE ERROR" in e for e in errors)

    def test_missing_block_returns_error(self, tmp_path):
        from core.quality_gate.sab_parser import validate_sab_block
        sad = tmp_path / "SAD.md"
        sad.write_text("# SAD\n\nNo SAB here.\n")
        errors = validate_sab_block(sad)
        assert any("SAB:START" in e for e in errors)


class TestRendererRespectsDataclassFields:
    """Drift guard: every SABSpec dataclass field MUST appear in SAB_BLOCK_TEMPLATE."""

    def test_every_sabspec_field_in_template(self):
        from dataclasses import fields
        from core.quality_gate.sab_parser import SABSpec, SAB_BLOCK_TEMPLATE
        for f in fields(SABSpec):
            assert f.name in SAB_BLOCK_TEMPLATE, (
                f"SABSpec field {f.name!r} is not rendered in SAB_BLOCK_TEMPLATE. "
                "Update render_canonical_sab_template() to include it."
            )

    def test_unhandled_field_fails_loudly(self, monkeypatch):
        """A newly added SABSpec field with no render branch must raise at
        render time — not silently emit a blank line and drop the field."""
        import core.quality_gate.sab_parser as mod
        from dataclasses import fields as real_fields

        class _FakeField:
            name = "brand_new_unhandled_field"

        def _fake_dc_fields(cls):
            return list(real_fields(cls)) + [_FakeField()]

        monkeypatch.setattr(mod, "_dc_fields", _fake_dc_fields)
        with pytest.raises(RuntimeError, match="unhandled SABSpec field"):
            mod.render_canonical_sab_template()


class TestPhaseTypeContract:
    """The docstring, canonical template, and SAD.md all promise that a quoted
    string phase (phase: "2") is rejected. Enforce that contract at parse time
    — int("2") would otherwise coerce silently and make the promise a lie."""

    def _write(self, tmp_path, phase_line: str):
        sad = tmp_path / "SAD.md"
        sad.write_text(
            "<!-- SAB:START -->\n```yaml\nsab:\n"
            f"{phase_line}\n  project: x\n"
            "```\n<!-- SAB:END -->"
        )
        return sad

    def test_string_phase_raises(self, tmp_path):
        from core.quality_gate.sab_parser import extract_sab_from_sad
        sad = self._write(tmp_path, '  phase: "2"')
        with pytest.raises(RuntimeError, match="phase"):
            extract_sab_from_sad(sad)

    def test_int_phase_parses(self, tmp_path):
        from core.quality_gate.sab_parser import extract_sab_from_sad
        sad = self._write(tmp_path, "  phase: 2")
        spec = extract_sab_from_sad(sad)
        assert spec is not None
        assert spec.phase == 2 and isinstance(spec.phase, int)

    def test_string_phase_flagged_by_validate(self, tmp_path):
        from core.quality_gate.sab_parser import validate_sab_block
        sad = self._write(tmp_path, '  phase: "2"')
        errors = validate_sab_block(sad)
        assert any("PARSE ERROR" in e and "phase" in e for e in errors)


pytestmark = pytest.mark.mutation_oracle
