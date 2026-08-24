"""Round 73 站5 — a dimension nobody scored, and nothing said it was absent.

taskq-new's `quality_manifest.json` pins `"NFR-06": "architecture_constraints"`,
which is a legal answer: SPEC §4's own rule is that every NFR's dimension must
be a key `harness/toolchains/registry.py::DIMENSION_TOOLS` actually has, and it
is one. `architecture_constraints` appears in exactly one gate config —
`gate1_per_fr.yaml`. Gates 2, 3 and 4 do not list it.

`measurement_scope` builds both of its lists from the dimensions the GATE
CONFIG produced, so a dimension the config never mentions is neither scored nor
unscored. taskq-new's committed Gate 4:

    "weight_covered": 1.0,
    "dimensions_unscored": [],
    "dimensions_scored": [ …16 names, none of them architecture_constraints… ]

published beside composite 94.59 — a number a reader takes for the whole
quality surface. Round 37's rule is that the denominator travels with the
number; Round 42 站4 applied it to a dimension that WAS in the config and was
not scored. This is the layer above: a dimension that never reached the config
at all.

Deliberately non-blocking, and the reason is in the code. Which dimensions a
gate runs is a framework decision — `architecture_constraints` is a per-FR
dimension and its absence from Gate 4 has a rationale — so blocking here would
stop every project on a choice none of them made. The substantive judgement for
NFR-06 is Round 73 站3's, which blocks in Gate 4 through
`unconfigured_blocking_reason`. What this station fixes is that a dimension
left out of the average must not read as though it were in it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = [pytest.mark.core]


def _dim(name: str, score: "float | None" = 90.0):
    from harness.harness_bridge import DimResult

    return DimResult(name=name, score=score, threshold=80)


def _manifest(tmp_path: Path, mapping: dict) -> Path:
    proj = tmp_path / "proj"
    (proj / ".methodology").mkdir(parents=True)
    (proj / ".methodology" / "quality_manifest.json").write_text(
        json.dumps({"schema_version": "1.0", "nfr_dimension_mapping": mapping}),
        encoding="utf-8")
    return proj


def test_a_dimension_the_manifest_pins_and_the_gate_omits_is_named(tmp_path):
    """taskq-new's shape, reduced to the two names that carry it."""
    from harness.harness_bridge import declared_dimensions, measurement_scope

    proj = _manifest(tmp_path, {
        "NFR-06": "architecture_constraints", "NFR-09": "test_assertion_quality",
    })
    scope = measurement_scope(
        [_dim("test_assertion_quality")], {"test_assertion_quality": 1.0},
        declared=declared_dimensions(proj),
    )

    assert scope["dimensions_declared_absent"] == ["architecture_constraints"]
    assert scope["dimensions_scored"] == ["test_assertion_quality"]
    assert scope["dimensions_unscored"] == []


def test_a_manifest_that_agrees_with_the_gate_names_nothing(tmp_path):
    """The counter-direction: every project here would otherwise carry a row."""
    from harness.harness_bridge import declared_dimensions, measurement_scope

    proj = _manifest(tmp_path, {"NFR-09": "test_assertion_quality"})
    scope = measurement_scope(
        [_dim("test_assertion_quality")], {"test_assertion_quality": 1.0},
        declared=declared_dimensions(proj),
    )
    assert scope["dimensions_declared_absent"] == []


def test_a_dimension_in_the_gate_but_unscored_stays_where_it_was(tmp_path):
    """Round 42 站4's list is a different fact and is not absorbed.

    In the config and not scored means the framework could not measure it
    (Round 35). Never in the config means nobody was going to.
    """
    from harness.harness_bridge import declared_dimensions, measurement_scope

    proj = _manifest(tmp_path, {"NFR-08": "mutation_testing"})
    scope = measurement_scope(
        [_dim("mutation_testing", score=None)], {"mutation_testing": 1.0},
        declared=declared_dimensions(proj),
    )

    assert scope["dimensions_unscored"] == ["mutation_testing"]
    assert scope["dimensions_declared_absent"] == []


def test_no_manifest_is_an_empty_declaration_not_an_empty_gate(tmp_path):
    """Could-not-measure is not a finding (Rounds 32/35). A project without a
    manifest has declared nothing, and reporting every gate dimension as
    "declared absent" would be the inversion of this check."""
    from harness.harness_bridge import declared_dimensions, measurement_scope

    proj = tmp_path / "proj"
    proj.mkdir()
    assert declared_dimensions(proj) == []

    scope = measurement_scope([_dim("linting")], {"linting": 1.0},
                              declared=declared_dimensions(proj))
    assert scope["dimensions_declared_absent"] == []


def test_the_scope_is_computed_with_the_projects_declaration(tmp_path):
    """A key computed and never passed is Round 43's defect; pinned on the
    source, where the scope is stashed on the context."""
    import inspect

    from harness import harness_bridge

    src = inspect.getsource(harness_bridge)
    assert "declared=declared_dimensions(" in src, (
        "measurement_scope is called without the project's declared "
        "dimensions, so `dimensions_declared_absent` can only ever be empty"
    )
