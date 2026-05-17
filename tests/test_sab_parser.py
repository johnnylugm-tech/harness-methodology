"""Unit tests for core.quality_gate.sab_parser."""

import json
import pytest

from core.quality_gate.sab_parser import SABSpec, extract_sab_from_sad


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
                    "architecture_constraints", "high_risk_modules"):
            assert key in d, f"Missing key: {key}"

    def test_to_dict_dependencies_built_from_allowed(self):
        d = self._make_spec().to_dict()
        assert d["dependencies"] == {"api": ["data_layer"]}

    def test_to_dict_layers_include_allowed_deps(self):
        d = self._make_spec().to_dict()
        api_layer = next(l for l in d["layers"] if l["name"] == "api")
        assert api_layer["allowed_dependencies"] == ["data_layer"]
        data_layer = next(l for l in d["layers"] if l["name"] == "data_layer")
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
