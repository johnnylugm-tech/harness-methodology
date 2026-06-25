"""Regression tests for Improvement I: fixture factory + golden SAB.json.

Locks in the contract between tests/fixtures/example_sad.md and the
sab_golden.json it should produce. If extract_sab_from_sad changes its
output schema, sab_golden.json must be regenerated; this test will
fail loudly so the regeneration is not silently missed.
"""
import json
from pathlib import Path

import pytest

from core.quality_gate.sab_parser import extract_sab_from_sad


FIXTURES = Path(__file__).parent / "fixtures"


class TestSabGoldenFixture:
    def test_example_sad_exists(self):
        assert (FIXTURES / "example_sad.md").exists()

    def test_golden_json_matches_extracted_sad(self):
        """sab_golden.json must byte-equal extract_sab_from_sad(example_sad.md).

        If this fails, regenerate with:
            python -c "import json; from pathlib import Path; \\
              from core.quality_gate.sab_parser import extract_sab_from_sad; \\
              print(json.dumps(extract_sab_from_sad(Path('tests/fixtures/example_sad.md'))\\
              .to_dict(), indent=2))" > tests/fixtures/sab_golden.json
        """
        sad_path = FIXTURES / "example_sad.md"
        golden_path = FIXTURES / "sab_golden.json"
        if not golden_path.exists():
            pytest.fail(f"{golden_path} missing — regenerate per docstring")
        sab = extract_sab_from_sad(sad_path)
        assert sab is not None, f"could not parse SAB block from {sad_path}"
        assert json.loads(golden_path.read_text()) == sab.to_dict()

    def test_golden_has_all_required_sabspec_fields(self):
        """Every field in SABSpec.to_dict() must be present in the golden
        so future schema additions land in fixtures immediately."""
        golden = json.loads((FIXTURES / "sab_golden.json").read_text())
        required = {
            "version", "created_at", "phase", "project", "layers",
            "dependencies", "quality_targets", "nfr_dimension_mapping",
            "nfr_traceability", "advisory_only", "gate_score_overrides",
            "fr_module_traceability", "architecture_constraints",
            "high_risk_modules",
        }
        assert required.issubset(golden.keys()), \
            f"missing fields: {required - set(golden.keys())}"


class TestWriteSabFromSadFixture:
    """Verify the conftest `write_sab_from_sad` fixture works."""

    def test_fixture_produces_valid_sab_json(self, write_sab_from_sad):
        sab_path = write_sab_from_sad(
            """<!-- SAB:START -->
```yaml
sab:
  version: "1.0"
  phase: 2
  project: test_proj
  layers:
    - name: L1
      modules:
        - foo.py
```
<!-- SAB:END -->"""
        )
        assert sab_path.exists()
        sab = json.loads(sab_path.read_text())
        assert sab["project"] == "test_proj"
        assert sab["layers"][0]["modules"] == ["foo.py"]

    def test_fixture_reflects_real_sabspec_schema(self, write_sab_from_sad):
        """Output must include SABSpec-only fields, not just inline dict.

        This is the key invariant: if extract_sab_from_sad adds a new
        field to SABSpec.to_dict(), every test using write_sab_from_sad
        automatically picks it up — no per-test fixture updates needed.
        """
        sab_path = write_sab_from_sad(
            """<!-- SAB:START -->
```yaml
sab:
  phase: 3
  project: schema_test
  layers:
    - name: L1
      modules: [a.py]
```
<!-- SAB:END -->"""
        )
        sab = json.loads(sab_path.read_text())
        # SABSpec-only fields that an inline literal would miss
        assert "fr_module_traceability" in sab
        assert "architecture_constraints" in sab
        assert "high_risk_modules" in sab