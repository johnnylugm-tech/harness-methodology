"""Unit tests for core.quality_gate.sab_parser."""
import pytest
pytestmark = pytest.mark.mutation_oracle


import json
import pytest

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
        assert "FastAPIApp" in spec.modules
        assert "users" in spec.modules
        assert len(spec.modules) == 4

    def test_extra_fields(self, tmp_path):
        sad = tmp_path / "SAD.md"
        sad.write_text(_SAD_WITH_EXTRAS)
        spec = extract_sab_from_sad(sad)
        assert spec.nfr_dimension_mapping == {"NFR-01": "security"}
        assert spec.architecture_constraints == ["no_direct_db_from_api"]
        assert spec.high_risk_modules == ["A"]

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

_SAD_BOTH_NFR_FIELDS = """\
<!-- SAB:START -->
```yaml
sab:
  version: "1.0"
  phase: 2
  project: "testapp"
  nfr_dimension_mapping:
    NFR-01: reliability
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
        assert spec.nfr_dimension_mapping.get("NFR-01") == "performance"
        assert spec.nfr_dimension_mapping.get("NFR-02") == "security"
        # deployability has no scoring tool → advisory_only, NOT in the dimension mapping
        assert "NFR-09" not in spec.nfr_dimension_mapping
        assert "deployability" in spec.advisory_only

    def test_unknown_nfr_type_silently_omitted(self, tmp_path):
        """NFRs with a type not in _NFR_TYPE_TO_DIM are excluded from auto-derivation."""
        sad = tmp_path / "SAD.md"
        sad.write_text(_SAD_NFR_TRACEABILITY_ONLY)
        spec = extract_sab_from_sad(sad)
        assert "NFR-99" not in spec.nfr_dimension_mapping

    def test_explicit_nfr_dim_mapping_wins_over_traceability(self, tmp_path):
        """When both fields present, explicit nfr_dimension_mapping is NOT overwritten."""
        sad = tmp_path / "SAD.md"
        sad.write_text(_SAD_BOTH_NFR_FIELDS)
        spec = extract_sab_from_sad(sad)
        # NFR-01 type is 'performance' but explicit mapping says 'reliability' → must keep 'reliability'
        assert spec.nfr_dimension_mapping.get("NFR-01") == "reliability"
        # NFR-02 only in traceability, but auto-derive did NOT run → absent from dim mapping
        assert "NFR-02" not in spec.nfr_dimension_mapping

    def test_traceability_stored_on_spec(self, tmp_path):
        """nfr_traceability is stored verbatim on SABSpec."""
        sad = tmp_path / "SAD.md"
        sad.write_text(_SAD_NFR_TRACEABILITY_ONLY)
        spec = extract_sab_from_sad(sad)
        assert spec.nfr_traceability["NFR-01"]["module"] == "app.pipeline"
        assert spec.nfr_traceability["NFR-01"]["target"] == "p95 < 200ms"

    def test_neither_field_gives_empty_dicts(self, tmp_path):
        """SAD with neither nfr_dimension_mapping nor nfr_traceability → both empty."""
        sad = tmp_path / "SAD.md"
        sad.write_text(_MINIMAL_SAD)
        spec = extract_sab_from_sad(sad)
        assert spec.nfr_dimension_mapping == {}
        assert spec.nfr_traceability == {}

    def test_to_dict_includes_nfr_traceability(self, tmp_path):
        """to_dict() must serialise nfr_traceability."""
        sad = tmp_path / "SAD.md"
        sad.write_text(_SAD_NFR_TRACEABILITY_ONLY)
        d = extract_sab_from_sad(sad).to_dict()
        assert "nfr_traceability" in d
        assert d["nfr_traceability"]["NFR-02"]["module"] == "app.security"

    def test_nfr_type_to_dim_maps_to_real_gate_dimensions(self):
        """The 5 enforceable NFR types must map to ACTUAL gate-14 dimensions; the 3
        advisory types (no scoring tool) are tracked separately, never faked into the map."""
        from core.quality_gate.sab_parser import _NFR_ADVISORY_TYPES
        _GATE_14_DIMS = {
            "linting", "type_safety", "test_coverage", "security", "secrets_scanning",
            "license_compliance", "mutation_testing", "architecture", "readability",
            "error_handling", "documentation", "performance", "integration_coverage",
            "test_assertion_quality",
        }
        for t in ("performance", "security", "maintainability", "reliability", "testability"):
            assert t in _NFR_TYPE_TO_DIM, f"Missing enforceable type: {t}"
            assert _NFR_TYPE_TO_DIM[t] in _GATE_14_DIMS, \
                f"{t}→{_NFR_TYPE_TO_DIM[t]} is not a real gate dimension"
        for t in ("deployability", "scalability", "usability"):
            assert t in _NFR_ADVISORY_TYPES
            assert t not in _NFR_TYPE_TO_DIM

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
        assert spec.gate_score_overrides.get("performance") == 75.0
        assert spec.gate_score_overrides.get("security") == 80.0
        # advisory NFR (deployability) contributes no override
        assert "deployability" not in spec.gate_score_overrides

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


class TestRoundTrip:
    """parse SAD.md → SABSpec.to_dict() → write SAB.json → read back."""

    def test_roundtrip_via_file(self, tmp_path):
        sad = tmp_path / "SAD.md"
        sad.write_text(_MINIMAL_SAD)
        spec = extract_sab_from_sad(sad)
        sab_json = tmp_path / ".methodology" / "SAB.json"
        sab_json.parent.mkdir()
        sab_json.write_text(json.dumps(spec.to_dict(), indent=2))

        data = json.loads(sab_json.read_text())
        assert data["phase"] == 2
        assert data["project"] == "omnibot"
        assert len(data["layers"]) == 2
        assert data["dependencies"] == {"api": ["data_layer"]}
