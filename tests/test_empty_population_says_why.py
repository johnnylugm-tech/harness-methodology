"""Round 74 站4 — zero is a reading that needs explaining, not a result.

Round 73 站6 made the NFR Layering Hard Rule executable and measured the
three projects where it started returning targets. Six of the nine still
return none, `nfr_layering_violations` returned `[]` for every one of them,
and `[]` reads exactly like "checked and clean". That round's docstring went
further and asserted the six were empty because those projects "declare no
unit/static NFR entry at all" — true of two, false of the rest.

Measured across the nine, by container and by field:

    taskq-api      76 entries · 34 NFR · 17 unit/static      ran
    taskq-advance  92 entries · 46 NFR ·  6 unit/static      ran
    run-all        63 entries · 28 NFR · 11 unit/static      ran
    taskq-renew    74 entries · 49 NFR ·  0 unit/static      ran, truly none
    taskq-super    74 entries · 14 NFR ·  0 unit/static      ran, truly none
    taskq          34 entries ·  0 NFR, at top-level `tests:`   NOT checked
    taskq-cc       92 entries ·  0 NFR, NFRs in cross_cutting   NOT checked
    taskq-new      50 entries ·  0 NFR, NFRs in cross_cutting   NOT checked
    taskq-plus     no entry list at all                         NOT checked

The last four are not a missing spelling, and widening a list of spellings
is the defect shape Round 73 站3 rejected. `cross_cutting` and `fr_tests` —
the two sections `templates/TEST_INVENTORY.yaml` actually defines — map a
dimension or an FR to a list of bare names, and a bare name has no layer.
The rule's question is which UNIT/STATIC NFR tests belong in the Deferred
section; under that schema it is not recoverable. Round 43: a check with no
executor is written down, not invented.

So it is said. It does not block — the framework's own template never
defined the shape the rule needs (station 5), and charging a project for
writing what it was given is Round 42's defect.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = [pytest.mark.core]


def _project(tmp_path: Path, inventory: str, spec: str = "") -> Path:
    proj = tmp_path / "proj"
    (proj / "02-architecture").mkdir(parents=True)
    (proj / "TEST_INVENTORY.yaml").write_text(inventory, encoding="utf-8")
    (proj / "02-architecture" / "TEST_SPEC.md").write_text(
        spec or "# TEST_SPEC.md\n\n## Deferred to Downstream Phases\n\n(none)\n",
        encoding="utf-8")
    return proj


# Each block is the skeleton of one corpus project's TEST_INVENTORY.yaml,
# transcribed. What differs between them is exactly what this station reads.
_SKELETONS = {
    # taskq-api / advance / run-all: entry list with layer AND an NFR subject.
    "entries": """\
test_inventory:
  tests:
    - {tc_id: TC-N02-01, nfr: NFR-02, layer: unit, test_function: test_nfr02_scan}
""",
    # taskq-renew / taskq-super: NFR subjects present, every layer integration.
    "entries_but_no_unit_or_static": """\
test_inventory:
  tests:
    - {tc_id: TC-N02-01, nfr: NFR-02, layer: integration, test_function: test_nfr02_scan}
    - {tc_id: TC-N03-01, nfr: NFR-03, layer: integration, test_function: test_nfr03_fault}
""",
    # taskq-cc / taskq-new: a full entry list, every row with a layer, no row
    # naming an NFR — the NFR tests are in cross_cutting, which has no layer.
    "nfr_elsewhere": """\
test_inventory:
  tests:
    - {tc_id: TC-FR01-01, fr: FR-01, layer: unit, test_name: test_fr01_creates}
cross_cutting:
  NFR-01:
    - test_ac_n1_1_get_task_p95_below_30ms
  NFR-02:
    - test_ac_n2_1_grep_shell_true_zero_hits
""",
    # taskq: the same shape with the entry list at the top level instead.
    "nfr_elsewhere_top_level_tests": """\
tests:
  - {tc_id: TC-FR01-03a, fr_id: FR-01, layer: unit, test_function: test_submit_injection}
cross_cutting:
  NFR-02:
    security:
      - test_shell_true_grep_zero_matches
""",
    # taskq-plus: fr_tests and cross_cutting only, no entry-shaped list.
    "no_entries": """\
test_inventory:
  format_version: '1.1'
  total_test_cases: 63
fr_tests:
  FR-01:
    unit:
      - test_fr01_example_unit
cross_cutting:
  security:
    - test_nfr02_a
""",
}


def test_a_project_the_rule_can_read_reports_nothing_unchecked(tmp_path):
    from cli.checks.specs import nfr_layering_not_checked, nfr_layering_targets

    proj = _project(tmp_path, _SKELETONS["entries"],
                    "# TEST_SPEC.md\n\n## Deferred to Downstream Phases\n\n"
                    "| # | NFR | Test Function | Layer | Title |\n"
                    "|---|---|---|---|---|\n"
                    "| 1 | NFR-02 | `test_nfr02_scan` | unit | scan |\n")
    assert len(nfr_layering_targets(proj)) == 1
    assert nfr_layering_not_checked(proj) == ""


def test_declaring_none_and_being_unreadable_are_different_answers(tmp_path):
    """The whole station in one assertion.

    Both projects yield zero targets and zero violations. One of them ran the
    rule and found nothing to check; the other could not run it. Round 73
    reported both as `[]`.
    """
    from cli.checks.specs import nfr_layering_not_checked, nfr_layering_targets

    ran = _project(tmp_path / "ran",
                   _SKELETONS["entries_but_no_unit_or_static"])
    could_not = _project(tmp_path / "could_not", _SKELETONS["nfr_elsewhere"])

    assert nfr_layering_targets(ran) == []
    assert nfr_layering_targets(could_not) == []

    assert nfr_layering_not_checked(ran) == "", (
        "49 NFR entries all at integration layer is an ANSWER — this project "
        "declares no unit/static NFR test, which is what taskq-renew and "
        "taskq-super genuinely do")
    assert nfr_layering_not_checked(could_not), (
        "a project whose NFR tests live in cross_cutting was never checked")


@pytest.mark.parametrize("skeleton,expect", [
    ("nfr_elsewhere", "nfr_elsewhere"),
    ("nfr_elsewhere_top_level_tests", "nfr_elsewhere"),
    ("no_entries", "no_entries"),
    ("entries", "entries"),
    ("entries_but_no_unit_or_static", "entries"),
])
def test_the_population_is_classified_from_the_file(tmp_path, skeleton, expect):
    """Four states, each decided from what the file contains.

    `nfr_elsewhere` in particular is not an assumption: the entry list is
    read, every row is found to carry a layer and none to name an NFR, and
    the file is found to name NFR ids somewhere else. Three corpus projects
    are in exactly that state and each declares its NFR tests in
    `cross_cutting`.
    """
    from cli.checks.specs import nfr_layering_population

    proj = _project(tmp_path, _SKELETONS[skeleton])
    assert nfr_layering_population(proj)["status"] == expect


def test_the_top_level_tests_list_is_read(tmp_path):
    """taskq puts the same entry list one level up, and it is an entry list.

    Reading it is not spelling-guessing: it is the same `tests:` key, and its
    rows carry the same `tc_id` / `layer` / `test_function` fields. What
    Round 73 hardcoded was the path to it, one level down.
    """
    from cli.checks.specs import nfr_layering_population, nfr_layering_targets

    proj = _project(tmp_path, """\
tests:
  - {tc_id: TC-N02-01, nfr: NFR-02, layer: static, test_function: test_nfr02_scan}
""")
    assert nfr_layering_population(proj)["container"] == "tests"
    assert [t["test_fn"] for t in nfr_layering_targets(proj)] == [
        "test_nfr02_scan"]


def test_an_unreadable_population_does_not_become_a_violation(tmp_path):
    """Round 42: a project must not be blocked for the shape it was given.

    `templates/TEST_INVENTORY.yaml` ships `fr_tests` and `cross_cutting`, and
    neither carries a layer. Turning "I could not check" into a FAIL would
    fail four of the nine projects here for writing what the framework's own
    template gave them.
    """
    from cli.checks.specs import (nfr_layering_not_checked,
                                  nfr_layering_violations)

    proj = _project(tmp_path, _SKELETONS["no_entries"])
    assert nfr_layering_not_checked(proj)
    assert nfr_layering_violations(proj) == []


def test_the_reason_names_something_the_reader_can_act_on(tmp_path):
    """"Could not read" with no object is the silence it replaced.

    Each sentence names the container it did read, or the keys it found
    instead — Round 48's rule that a halt with no owner is not a halt, applied
    to a reading rather than to a stop.
    """
    from cli.checks.specs import nfr_layering_not_checked

    elsewhere = nfr_layering_not_checked(
        _project(tmp_path / "a", _SKELETONS["nfr_elsewhere"]))
    assert "test_inventory.tests" in elsewhere, elsewhere
    assert "1 entries" in elsewhere, elsewhere

    none = nfr_layering_not_checked(
        _project(tmp_path / "b", _SKELETONS["no_entries"]))
    assert "cross_cutting" in none and "fr_tests" in none, none
