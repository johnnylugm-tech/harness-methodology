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

The parity class below is the load-bearing part: it reads verbatim copies of
committed gate result files — documents produced by actual pipeline runs, not by
this repo — and asserts the schema accepts them. A schema that only agrees with
fixtures written alongside it is the same closed loop in a new place.

Round 26 — one fixture was not enough. `taskq_gate4.json` was the only artifact
here, and Gate 4 is one of four producers with four different write paths. It
happened to carry `open_critical_count` / `open_high_count`, which the schema
required and which NO producer instruction has ever asked for, so this class read
green while an obedient Gate 1 agent wrote an off-schema file (taskq-plus P3:
`malformed_gate_result` at FR-01 and again at FR-04). Adding a real Gate 1 result
immediately surfaced four fields the schema did not describe — `gate`, and the
per-dimension `tests_passed` / `tests_failed` / `tests_skipped` the Gate 1 template
mandates. The lesson is the coverage rule now enforced below: every gate with a
producer needs a real artifact here.

Producer-side parity (does each writer's INSTRUCTION agree with the schema?) lives
in tests/test_gate_result_producer_parity.py. This module is the product side.

Fixture provenance: both files are verbatim, with one documented exception — the
single absolute home path inside `taskq_plus_gate1.json`'s pytest tool_evidence was
rewritten to `/tmp/taskq-plus`, matching taskq_gate4.json, which carries none.
"""

import json
from pathlib import Path

import pytest

from core.quality_gate.gate_result_schema import (
    SCHEMA_PATH,
    validate_gate_result,
)

_FIXTURE_DIR = Path(__file__).parent / "fixtures" / "gate_results"

# gate number → committed artifact produced by that gate's real writer.
# test_every_gate_with_a_producer_has_a_real_fixture keeps this honest.
REAL_RESULTS: dict[int, Path] = {
    1: _FIXTURE_DIR / "taskq_plus_gate1.json",
    4: _FIXTURE_DIR / "taskq_gate4.json",
}

@pytest.fixture(params=sorted(REAL_RESULTS), ids=lambda g: f"gate{g}")
def real_gate_result(request) -> dict:
    return json.loads(REAL_RESULTS[request.param].read_text(encoding="utf-8"))


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

    def test_every_gate_with_a_producer_has_a_real_fixture(self):
        """One fixture out of four producers is how Round 26's block survived.

        The schema is validated at ONE chokepoint for ALL gates
        (harness_bridge.finalize_gate reads gate{N}_result.json for N in 1..4), so
        agreeing with one gate's output proves nothing about the other three. The
        gates whose producers are declared in
        tests/test_gate_result_producer_parity.py must each have a real artifact
        here.

        Gates 2 and 3 have no committed artifact on hand yet; they are named rather
        than silently omitted, because an unstated gap reads as coverage. Drop a
        real gate2/gate3 result into tests/fixtures/gate_results/ and add it to
        REAL_RESULTS — do NOT widen this exemption.
        """
        from tests.test_gate_result_producer_parity import _PRODUCER_GATES

        with_producer = {g for gates in _PRODUCER_GATES.values() for g in gates}
        awaiting_artifact = {2, 3}
        missing = with_producer - set(REAL_RESULTS) - awaiting_artifact
        assert not missing, (
            f"gate(s) {sorted(missing)} have a declared producer but no real result "
            f"fixture — the schema would be judged honest by an artifact from a "
            f"different gate's writer, which is exactly how the Round 26 block "
            f"(open_critical_count required, asked for by nobody) stayed invisible."
        )


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
        """CRG-only dimensions carry null — that is the shape, not a defect.

        Any dimension will do; naming one would tie this to a single gate's dim
        set (Gate 1 scores three dimensions, Gate 4 fourteen).
        """
        some_dim = sorted(real_gate_result["breakdown"])[0]
        real_gate_result["breakdown"][some_dim]["score"] = None
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
