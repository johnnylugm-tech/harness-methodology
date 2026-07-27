"""Executable shape contract for gate{N}_result.json.

Round 21 root cause — a schema nothing loads is a schema nothing keeps honest.
``harness/ssi/schemas/harness_gate_result.schema.json`` has existed since the
gate result format was introduced and was never read by any code path (the only
validated artifact in the repo was b_review.schema.json, via
core/review_schema_validator.py). Left unexecuted, it drifted into describing a
file no run produces:

  * it required a per-dimension ``passed`` that no writer emits
  * it typed ``score`` as a number, while CRG-only dimensions carry ``null``
  * it typed ``overall_score`` / ``meets_target`` as required non-null, while
    real agents leave both null because finalize-gate recomputes them
  * it omitted ``tool_output`` / ``tool_evidence`` / ``issues`` / ``findings``,
    present in every breakdown entry ever written

With no enforced contract, each consumer had to guess the field names. That is
how ``tool_score`` and later ``target`` — neither of which any writer has ever
produced — ended up as the keys the DA-waiver safeguard read, leaving it dead
through two separate "fixes" (see core/quality_gate/da_waiver.py).

Validation is advisory-with-teeth: violations are surfaced loudly and returned
to the caller, which decides. finalize_gate treats them as a BLOCK, because a
gate verdict computed from a shape nobody agrees on is not a verdict.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import jsonschema

SCHEMA_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "harness" / "ssi" / "schemas" / "harness_gate_result.schema.json"
)

# Cap on how many individual violations are reported. A structurally wrong file
# can produce one error per dimension; the first few identify the problem.
_MAX_REPORTED = 5


@dataclass(frozen=True)
class GateResultValidation:
    """Outcome of validating one gate result against the schema.

    valid:      the document conforms.
    violations: human-readable messages, most specific first (capped).
    """

    valid: bool
    violations: tuple[str, ...]

    def as_block_message(self, gate: int) -> str:
        """Render the violations as a [BLOCKED]-style diagnostic."""
        lines = [
            f"gate{gate}_result.json does not match its schema "
            f"({SCHEMA_PATH.name}):"
        ]
        lines += [f"  - {v}" for v in self.violations]
        lines.append(
            "  Fix: correct the gate result to match the documented shape. If the "
            "shape itself has legitimately changed, update the schema in the same "
            "commit — a consumer guessing field names is how the DA-waiver "
            "safeguard stayed dead through two fixes."
        )
        return "\n".join(lines)


def _load_schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def validate_gate_result(raw: "dict | Any") -> GateResultValidation:
    """Validate a parsed gate{N}_result.json against the shape contract.

    Returns a :class:`GateResultValidation`; never raises for a merely
    non-conforming document. A non-dict input is reported as a violation rather
    than a crash, so a caller that already has the parsed JSON can route it
    through the same path as any other shape problem.
    """
    if not isinstance(raw, dict):
        return GateResultValidation(
            False, (f"expected a JSON object, got {type(raw).__name__}",)
        )

    validator = jsonschema.Draft7Validator(_load_schema())
    errors = sorted(validator.iter_errors(raw), key=lambda e: list(e.absolute_path))
    if not errors:
        return GateResultValidation(True, ())

    messages: list[str] = []
    for err in errors[:_MAX_REPORTED]:
        where = ".".join(str(p) for p in err.absolute_path) or "<root>"
        messages.append(f"{where}: {err.message}")
    if len(errors) > _MAX_REPORTED:
        messages.append(f"... and {len(errors) - _MAX_REPORTED} more violation(s)")
    return GateResultValidation(False, tuple(messages))
