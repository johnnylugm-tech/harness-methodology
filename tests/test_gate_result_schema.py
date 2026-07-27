"""The gate-result shape contract, checked against a real gate result.

Round 21 站2. harness/ssi/schemas/harness_gate_result.schema.json existed from
the start and was loaded by nothing. Unexecuted, it drifted into describing a
document no run produces — a per-dimension `passed` no writer emits, numeric
`score` where CRG-only dimensions carry null, required non-null `overall_score`
and `meets_target` that real agents leave null — and the only test guarding it
re-stated the schema's own required list, so it could confirm the file had not
been edited and nothing else.

With no enforced contract each consumer guessed the field names. That is how the
DA-waiver safeguard came to read `tool_score`, and then `target`, neither of
which any writer has ever produced, staying dead through two fixes.

The parity class below is the load-bearing part: it reads a verbatim copy of
taskq's committed gate4_result.json — a document produced by an actual pipeline
run, not by this repo — and asserts the schema accepts it. A schema that only
agrees with fixtures written alongside it is the same closed loop in a new place.
"""

import json
from pathlib import Path

import pytest

from core.quality_gate.gate_result_schema import (
    SCHEMA_PATH,
    validate_gate_result,
)

_REAL = Path(__file__).parent / "fixtures" / "gate_results" / "taskq_gate4.json"


@pytest.fixture
def real_gate_result() -> dict:
    return json.loads(_REAL.read_text(encoding="utf-8"))


@pytest.fixture
def schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


class TestSchemaMatchesReality:
    """Parity against an artifact this repo did not author."""

    def test_a_real_gate_result_validates(self, real_gate_result):
        verdict = validate_gate_result(real_gate_result)
        assert verdict.valid, "\n".join(verdict.violations)

    def test_top_level_required_fields_are_all_present_in_a_real_result(
        self, schema, real_gate_result
    ):
        missing = set(schema["required"]) - set(real_gate_result)
        assert not missing, (
            f"schema requires {sorted(missing)}, absent from a real gate result — "
            "the schema is describing something the pipeline does not produce"
        )

    def test_per_dimension_required_fields_are_all_present_in_a_real_result(
        self, schema, real_gate_result
    ):
        required = set(schema["properties"]["breakdown"]["additionalProperties"]["required"])
        for dim, row in real_gate_result["breakdown"].items():
            missing = required - set(row)
            assert not missing, (
                f"schema requires {sorted(missing)} per dimension, absent from "
                f"'{dim}' in a real gate result"
            )

    def test_every_field_a_real_result_carries_is_described(
        self, schema, real_gate_result
    ):
        """Undocumented fields are how a reader ends up guessing.

        Not a validation failure (the schema deliberately does not set
        additionalProperties: false, so an unexpected key cannot block a gate) —
        but an undescribed field is one nobody can rely on.
        """
        described = set(schema["properties"])
        undescribed = set(real_gate_result) - described
        assert not undescribed, (
            f"a real gate result carries undocumented top-level field(s) "
            f"{sorted(undescribed)} — document them in the schema"
        )

        dim_described = set(
            schema["properties"]["breakdown"]["additionalProperties"]["properties"]
        )
        for dim, row in real_gate_result["breakdown"].items():
            extra = set(row) - dim_described
            assert not extra, (
                f"'{dim}' carries undocumented field(s) {sorted(extra)}"
            )

    def test_the_waiver_bug_field_names_are_not_in_the_contract(self, schema):
        """`tool_score` and `target` were read by consumers; neither exists.

        Pinned by name because the cost of this particular guess was two
        successive fixes that both left the safeguard dead.
        """
        dim_props = set(
            schema["properties"]["breakdown"]["additionalProperties"]["properties"]
        )
        assert "tool_score" not in dim_props
        assert "target" not in dim_props
        assert "threshold" in dim_props


class TestValidation:
    def test_missing_required_top_level_field_is_reported_by_name(
        self, real_gate_result
    ):
        del real_gate_result["breakdown"]
        verdict = validate_gate_result(real_gate_result)
        assert not verdict.valid
        assert any("breakdown" in v for v in verdict.violations)

    def test_missing_per_dimension_threshold_is_reported_with_its_path(
        self, real_gate_result
    ):
        del real_gate_result["breakdown"]["linting"]["threshold"]
        verdict = validate_gate_result(real_gate_result)
        assert not verdict.valid
        assert any("linting" in v and "threshold" in v for v in verdict.violations)

    def test_null_score_is_accepted(self, real_gate_result):
        """CRG-only dimensions carry null — that is the shape, not a defect."""
        real_gate_result["breakdown"]["readability"]["score"] = None
        assert validate_gate_result(real_gate_result).valid

    def test_non_dict_input_is_a_violation_not_a_crash(self):
        verdict = validate_gate_result(["not", "an", "object"])
        assert not verdict.valid
        assert verdict.violations

    def test_block_message_names_the_gate_and_carries_remediation(
        self, real_gate_result
    ):
        del real_gate_result["quality_complete"]
        verdict = validate_gate_result(real_gate_result)
        msg = verdict.as_block_message(4)
        assert "gate4_result.json" in msg
        assert "Fix:" in msg
