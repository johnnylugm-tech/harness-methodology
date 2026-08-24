"""Round 74 站5 — the template and the prompt describe one artifact.

The framework made three statements about TEST_INVENTORY.yaml and no two of
them agreed:

  templates/TEST_INVENTORY.yaml      format_version, fr_tests, cross_cutting
    (cli/project_cmds.py::_init_copy_templates copies it into every new
     project root on day one)

  scripts/workflowgen/spec_phase1.py "REQUIRED TOP-LEVEL KEY (must include
    (the P1 prompt)                   "test_inventory:")… Non-conforming
                                      schema fails the load step", plus a
                                      `tests:` block with tc_id / layer /
                                      cross_ref_nfrs and by_fr / by_layer
                                      arithmetic

  cli/phase_cmds.py::_validate_handoff_p1_to_p2
                                      requires `fr_tests:` or `cross_cutting:`
                                      and never looks at `test_inventory:`

So every project began with a file its own prompt calls non-conforming, and
each agent invented the rest. Seven projects, four spellings of the test
function field and three container shapes; the rule in cli/checks/specs.py
that reads that block could see three of nine of them.

This is Round 17's prompt-to-gate drift class with an artifact schema in
place of a threshold, and the fix is the one Round 17 used: the statements
are bound, and the binding is tested rather than remembered.

Round 42 note: nothing here is enforced on a project. Nine delivered
projects wrote four spellings because the framework never said which, and
`_entry_test_fn` still reads all of them by content. What changes is that
the next project is not asked to guess.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = [pytest.mark.core]

REPO = Path(__file__).resolve().parents[1]
TEMPLATE = REPO / "templates" / "TEST_INVENTORY.yaml"
P1_SPEC = REPO / "scripts" / "workflowgen" / "spec_phase1.py"


def _template() -> dict:
    import yaml

    return yaml.safe_load(TEMPLATE.read_text(encoding="utf-8"))


def test_every_key_the_prompt_calls_required_is_in_the_template():
    """Read out of the generator, not copied here.

    The prompt's sentence is the authority; if it starts naming a different
    key this test asks the template about that one.
    """
    source = P1_SPEC.read_text(encoding="utf-8")
    required = set(re.findall(
        r"REQUIRED TOP-LEVEL KEY \(must include \\\"([A-Za-z_][A-Za-z0-9_]*):\\\"\)",
        source))
    assert required, (
        "the P1 prompt no longer states a REQUIRED TOP-LEVEL KEY for "
        "TEST_INVENTORY.yaml — this test can no longer check the template "
        "against it, so either restore the sentence or delete this test with "
        "the reason")

    missing = required - set(_template())
    assert not missing, (
        f"templates/TEST_INVENTORY.yaml is missing {sorted(missing)}, which "
        f"scripts/workflowgen/spec_phase1.py tells every project is required "
        f"and whose absence it says 'fails the load step'. Every new project "
        f"gets this file on day one from _init_copy_templates."
    )


def test_the_template_carries_the_fields_the_prompt_asks_entries_to_carry():
    """`tc_id`, `layer`, and a per-entry requirement id.

    The prompt requires each matrix tc_id to be its own entry, requires
    `by_layer.<L>.count` to equal the entries with that layer, and names
    `cross_ref_frs` / `cross_ref_nfrs` as how a cross-cut is signalled. All
    three are statements about an entry's fields; none of them was ever
    shown.
    """
    entries = (_template().get("test_inventory") or {}).get("tests")
    assert isinstance(entries, list) and entries, "no `tests:` entries"

    for entry in entries:
        assert entry.get("tc_id"), entry
        assert entry.get("layer"), entry
        assert ("fr" in entry) ^ ("nfr" in entry), (
            f"an entry names exactly one requirement as its subject; "
            f"cross-cuts go in cross_ref_* metadata: {entry}")

    assert any("cross_ref_nfrs" in e for e in entries), (
        "the prompt names cross_ref_nfrs as the way to signal a cross-cut "
        "without deleting an entry; the template never showed one, and six "
        "projects wrote it as a list while the rule reading it had to guess")
    assert any(e.get("nfr") and str(e.get("layer")) in ("unit", "static")
               for e in entries), (
        "the NFR Layering Hard Rule's entire input is an NFR entry at unit or "
        "static layer. A template without one shows nothing about the shape "
        "the rule reads — which is how six of nine projects ended up with a "
        "population it could not see. An FR entry at unit layer does not "
        "stand in for it: the counter-proof for this test demoted the NFR "
        "entry to integration and this assertion stayed green on the FR one.")


def test_the_templates_arithmetic_closes():
    """The prompt checks it in every project; it has to hold in the example.

    `by_fr.<FR>.tc_count` and `by_layer.<L>.count` must each sum to
    `total_test_cases`, and the prompt calls any drift HIGH severity.
    """
    summary = (_template().get("test_inventory") or {}).get("coverage_summary")
    assert summary, "no coverage_summary in the template"

    entries = _template()["test_inventory"]["tests"]
    total = summary["total_test_cases"]
    assert total == len(entries), (total, len(entries))
    assert sum(v["tc_count"] for v in summary["by_fr"].values()) == total
    assert sum(v["count"] for v in summary["by_layer"].values()) == total

    by_layer = summary["by_layer"]
    for entry in entries:
        assert entry["layer"] in by_layer, entry


def test_every_reader_in_the_tree_can_read_the_template(tmp_path):
    """Three readers, three container vocabularies, one file.

    Before this station the template satisfied `_flatten_test_names` and the
    P1→P2 validator and was invisible to the NFR Layering rule. A template
    only two of three readers can read is the same defect one layer up.
    """
    import shutil

    from cli.checks.specs import (nfr_layering_not_checked,
                                  nfr_layering_targets,
                                  nfr_layering_violations)
    from core.quality_gate.spec_coverage import _flatten_test_names

    inventory = _template()

    # Reader 1 — P1 Naming Authority (name-only view).
    names = _flatten_test_names(inventory)
    assert names, "no names reachable through fr_tests / cross_cutting"

    # Reader 2 — the P1→P2 handoff validator's minimum.
    assert inventory.get("fr_tests") or inventory.get("cross_cutting")

    # Reader 3 — the NFR Layering Hard Rule.
    project = tmp_path / "proj"
    (project / "02-architecture").mkdir(parents=True)
    shutil.copy2(TEMPLATE, project / "TEST_INVENTORY.yaml")
    targets = nfr_layering_targets(project)
    assert targets, "the template declares no unit/static NFR entry"

    (project / "02-architecture" / "TEST_SPEC.md").write_text(
        "# TEST_SPEC.md\n\n## Deferred to Downstream Phases\n\n"
        "| # | NFR | Test Function | Layer | Title |\n|---|---|---|---|---|\n"
        + "".join(f"| {i} | {t['nfr']} | `{t['test_fn']}` | {t['layer']} | x |\n"
                 for i, t in enumerate(targets, start=1)),
        encoding="utf-8")
    assert nfr_layering_not_checked(project) == ""
    assert nfr_layering_violations(project) == []


def test_the_two_views_name_the_same_tests():
    """`fr_tests` / `cross_cutting` are a view of `tests:`, not a second list.

    Nothing reconciles them in production — `_flatten_test_names` reads one,
    `nfr_layering_targets` the other — so a template showing two disagreeing
    lists would teach every project that they are independent.
    """
    from core.quality_gate.spec_coverage import _flatten_test_names

    inventory = _template()
    view = _flatten_test_names(inventory)
    entries = {e["test_function"]
               for e in inventory["test_inventory"]["tests"]}

    assert view <= entries, (
        f"named in fr_tests/cross_cutting but not enumerated in `tests:`: "
        f"{sorted(view - entries)}")
