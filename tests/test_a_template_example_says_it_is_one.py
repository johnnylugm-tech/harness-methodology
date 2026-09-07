"""Round 105 站3 — the framework writes example values into the judged tree.

`cli/project_cmds.py:1617` copies `templates/TEST_INVENTORY.yaml` into every
new project's root on day one, and `render_canonical_sab_template()` renders
the SAB block every P2 agent fills in. Both carry values this framework
invented for illustration, and the project is supposed to replace them.

Measured across the corpus, the only variable that decides whether it does is
whether the value says it is an example:

    SAB `layers` (app.api.routes, …)     marked      0 / 13 shipped as-is
    SAB quality_targets.min_coverage: 80 UNMARKED    2 / 13 shipped as-is
    TEST_INVENTORY `*_example_*` names   UNMARKED    3 / 13 shipped as-is
                                                     (taskq-sn, taskq-wow,
                                                      taskq-forever, 4 each)

The marked example leaked nowhere. The two unmarked ones leaked into five
project-rounds between them, and `min_coverage` is not decoration:
`advance_checks._check_gate1_live_coverage` reads
`min_coverage_floor(manifest)`, so taskq-sn's Gate 1 live-coverage check ran
at 80% while its own SPEC.md:358 and :442 require TOTAL 100%. Eleven projects
overrode it to 100; two inherited the framework's number. Round 101's mother —
the framework filling in the answer the project was supposed to give.

What this guard checks, and what it deliberately does not: it checks the
TEMPLATE end — that every value the framework invents says so on its own line.
It does NOT check the delivered end. "The project still has 80" is not a
defect: a project that considered the question and chose 80 must not be
accused of inheriting it (Round 46), and this framework has no way to tell the
two apart. The marker is what makes the choice conscious, and the measurement
above says that is the half that moves the number.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = [pytest.mark.core]

REPO = Path(__file__).resolve().parents[1]


def test_every_example_value_the_framework_ships_is_marked_as_one() -> None:
    from core.quality_gate.legal_artifacts import (
        TEMPLATE_EXAMPLE_MARKER, TEMPLATE_EXAMPLE_VALUES,
    )

    offenders: list[str] = []
    for rel, values in sorted(TEMPLATE_EXAMPLE_VALUES.items()):
        text = (REPO / rel).read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), 1):
            for value in values:
                if value in line and TEMPLATE_EXAMPLE_MARKER not in line:
                    offenders.append(f"{rel}:{lineno}  {line.strip()}")
    assert not offenders, (
        "these lines put a value this framework invented into a file it "
        "writes into the project's own tree, without saying it is an example "
        f"the project must replace (marker: {TEMPLATE_EXAMPLE_MARKER!r}):\n  "
        + "\n  ".join(offenders))


def test_the_rendered_sab_block_marks_its_examples_too() -> None:
    """The SAD template and the P2 SOP are copies; this is the generator.

    `sab_parser`'s own comment says every place that shows a SAB block example
    "MUST render this function instead of hand-writing YAML", and two of them
    hand-write it anyway. Checking the generator as well as the two files is
    what keeps a marker added to the copies from passing while the source of
    the copies is still unmarked.
    """
    from core.quality_gate.legal_artifacts import (
        SAB_TEMPLATE_EXAMPLE_VALUES, TEMPLATE_EXAMPLE_MARKER,
    )
    from core.quality_gate.sab_parser import render_canonical_sab_template

    rendered = render_canonical_sab_template()
    offenders = [
        line.strip() for line in rendered.splitlines()
        if any(v in line for v in SAB_TEMPLATE_EXAMPLE_VALUES)
        and TEMPLATE_EXAMPLE_MARKER not in line
    ]
    assert not offenders, (
        "render_canonical_sab_template emits an invented value with no "
        "marker:\n  " + "\n  ".join(offenders))


def test_the_marker_is_the_one_the_layers_block_already_used() -> None:
    """Reverse control on the registry itself.

    `layers` has carried this marker since Round 39 and is the example nobody
    shipped. If the constant stopped matching the wording that block uses, the
    guard above would be checking a marker no reader has ever seen — and the
    one example with a measured 0/13 leak rate would fall out of the rule
    derived from it.
    """
    from core.quality_gate.legal_artifacts import TEMPLATE_EXAMPLE_MARKER
    from core.quality_gate.sab_parser import render_canonical_sab_template

    layers_lines = [ln for ln in render_canonical_sab_template().splitlines()
                    if ln.strip().startswith("layers:")]
    assert layers_lines, "the SAB template no longer renders a `layers:` block"
    assert TEMPLATE_EXAMPLE_MARKER in layers_lines[0], (
        f"{TEMPLATE_EXAMPLE_MARKER!r} is not what the layers block says: "
        f"{layers_lines[0]!r}")


def test_the_registry_names_values_that_are_actually_there() -> None:
    """A registry entry for a literal no template contains is a rule that
    cannot fail — the shape `contract_decides` refuses for import contracts,
    applied to this guard's own inputs."""
    from core.quality_gate.legal_artifacts import TEMPLATE_EXAMPLE_VALUES

    missing: list[str] = []
    for rel, values in sorted(TEMPLATE_EXAMPLE_VALUES.items()):
        path = REPO / rel
        assert path.exists(), f"{rel} is registered and does not exist"
        text = path.read_text(encoding="utf-8")
        missing += [f"{rel}: {v!r}" for v in values if v not in text]
    assert not missing, (
        "these registered example values are in no template, so the rule "
        "above cannot fail for them:\n  " + "\n  ".join(missing))


def test_the_test_inventory_template_still_parses() -> None:
    """The markers are YAML comments; adding them must not change what the
    file means. `cli/project_cmds.py` copies this into every project root and
    `spec_coverage._flatten_test_names` reads it."""
    import yaml

    data = yaml.safe_load(
        (REPO / "templates" / "TEST_INVENTORY.yaml").read_text(encoding="utf-8"))
    tests = data["test_inventory"]["tests"]
    assert len(tests) == 4
    assert tests[0]["test_function"] == "test_fr01_example_integration"
    assert data["fr_tests"]["FR-01"]["unit"] == ["test_fr01_example_unit"]
