"""Round 30 站0/站1 — every SAB field the framework CONSUMES must have a
PRODUCER clause in the P2 SAB-WRITE prompt.

Round 29 added `scope_layers` to the SAB: a validator
(core/quality_gate/sab_parser.py) that rejects unknown layer names, a consumer
(core/quality_gate/mutmut_scope.py) that derives `paths_to_mutate` from it, and
tests for both. It did not touch scripts/workflowgen/spec_phase2.py, so the P2
agent is never told the field exists. Measured after that commit:

    $ grep -rn "scope_layers" --include='*.py' --include='*.js' .
    → consumer, validator, tests. No prompt. No golden.

The consequence is not "a field goes unused". `resolve_mutation_scope` returns
None for every project, `_resolve_mutmut_workdir` falls back to the whole
`03-development/src`, and Gate 2 keeps mutating the code the SPEC excluded from
its own time budget — the exact defect Round 29 station 2 existed to remove.

This is the eighth appearance of "the detector was built, the consumer never
read it" in this repo, and the first where it happened INSIDE the commit that
was fixing it. A per-field test would catch this one field; the registry below
is the structural close, in the same declarative-registry + completeness shape
as tests/test_workflow_dispatch_registry.py and tests/test_prompt_gate_parity.py.

Scope note: only OPTIONAL fields the agent must volunteer belong here. Fields
the SAB generator computes (nfr_dimension_mapping) or that the prompt already
covers by another name are out — add them with a reason, do not silently widen.
"""
from __future__ import annotations

from typing import NamedTuple

import pytest

from scripts.workflowgen.generate_workflows import generate

pytestmark = [pytest.mark.core]


class SabFieldContract(NamedTuple):
    """One SAB field with a framework consumer and a required prompt clause.

    field:    the YAML key the P2 agent must write.
    consumer: `module:symbol` that reads it — the reason the field must exist.
    why:      what silently degrades when the agent is never told to write it.
    """

    field: str
    consumer: str
    why: str


SAB_FIELD_CONTRACTS: tuple[SabFieldContract, ...] = (
    SabFieldContract(
        field="scope_layers",
        consumer="core.quality_gate.mutmut_scope:resolve_mutation_scope",
        why=(
            "mutation_testing runs against the whole source tree instead of the "
            "layers the SPEC limited it to, and Gate 2 times out on code the "
            "spec explicitly excluded from its time budget"
        ),
    ),
    SabFieldContract(
        field="dimension",
        consumer="core.quality_gate.sab_parser:_NFR_TYPE_TO_DIM (explicit override)",
        why=(
            "an NFR is scored by the dimension the framework infers from its "
            "type keyword rather than the one the spec named (Round 27 station 2)"
        ),
    ),
)


def _p2_prompt_text() -> str:
    """The generated Phase 2 workflow JS — the only place the P2 agent reads."""
    return generate(2)


@pytest.mark.parametrize(
    "contract", SAB_FIELD_CONTRACTS, ids=lambda c: c.field
)
def test_consumed_sab_field_is_named_in_the_p2_prompt(contract: SabFieldContract):
    """A field with a consumer and no producer clause is a dead field.

    The assertion is deliberately weak — it checks the field NAME appears in the
    SAB-WRITE contract, not the exact wording. Pinning the wording would make
    this test a second copy of the prompt; what must not drift is the existence
    of the instruction.
    """
    text = _p2_prompt_text()
    assert "SAB-WRITE" in text, "P2 prompt no longer contains the SAB-WRITE step"
    assert contract.field in text, (
        f"SAB field '{contract.field}' is consumed by {contract.consumer} but the "
        f"P2 SAB-WRITE prompt never asks the agent to write it. "
        f"Without the producer clause: {contract.why}."
    )


def test_registry_is_not_empty_and_names_its_consumers():
    """Completeness floor: a registry that drifts to empty asserts nothing."""
    assert SAB_FIELD_CONTRACTS, "registry emptied — the parity check is now vacuous"
    for c in SAB_FIELD_CONTRACTS:
        assert ":" in c.consumer, (
            f"{c.field}: consumer must be 'module:symbol' so the next reader can "
            f"find who depends on the field"
        )
        assert c.why.strip(), f"{c.field}: state what degrades, not just that it is used"


# ── Round 110 站3b: the other reader of the field list ───────────────────────
#
# Round 64 finding 8 recorded `sab_parser.py`'s module docstring as hard-coding
# "14 fields" and the dimension comment as hard-coding a "14-dimension set",
# judged them correct that day, and deferred with `再開條件:詞彙表變動`. Round
# 110 站3 walked that condition and measured it MET: SABSpec is 15 fields, the
# gate configs are 12 / 17 / 16 dimensions, and the docstring's enumeration had
# been missing `required_artifacts` since Round 68 站1 added it.
#
# Both counts are gone rather than corrected — replacing 14 with 15 plants the
# same drift again, one round later. What is left is a list, and this is what
# keeps the list complete. The same docstring already carries the precedent:
# the NFR type vocabulary is NOT restated there, because "the list changed in
# Round 27 and every hand-copy of it then disagreed with the table".

def test_the_parser_docstring_names_every_field_it_parses() -> None:
    """A field absent from the docstring is a field the next reader does not
    know to write into the SAB.

    Both sides are machine-readable — dataclass attributes and one docstring —
    so this cannot misfire the way a natural-language scan would (Round 46).
    """
    import dataclasses

    from core.quality_gate import sab_parser
    from core.quality_gate.sab_parser import SABSpec

    doc = sab_parser.__doc__ or ""
    missing = [f.name for f in dataclasses.fields(SABSpec) if f.name not in doc]
    assert not missing, (
        f"core/quality_gate/sab_parser.py's module docstring enumerates the SAB "
        f"fields and is missing {missing}. That list is what a reader consults "
        f"before writing a SAB block; a field it omits is a field nobody is told "
        f"to write. Add it there, and do not restore a count in front of it — "
        f"Round 64 deferred exactly that count and Round 110 站3 found it wrong."
    )
