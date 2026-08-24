"""Round 73 站6 — two bugs pointing opposite ways, each hiding the other.

The NFR Layering Hard Rule (`cli/checks/specs.py`) requires every unit/static
NFR test named in `TEST_INVENTORY.yaml` to appear in TEST_SPEC.md's "Deferred
to Downstream Phases" section. It has never run against any project, and if it
had it would have failed all of them.

**Bug 1 — the population is always empty.** It reads
`tc.get("function_name")` and `tc.get("nfr")`. `templates/TEST_INVENTORY.yaml`
defines neither: it defines `format_version`, `fr_tests` and `cross_cutting`,
and has no `test_inventory:` key at all. That key exists because
`scripts/workflowgen/spec_phase1.py:735` requires it for a reason that has
nothing to do with schema — a YAML file has no H1, so the loader identifies
the artifact by that key. The framework mandated the key's presence and never
defined what goes under it, and seven projects wrote four different spellings
of the function-name field:

    taskq-renew / taskq-advance / taskq-cc     test_function
    taskq-api                                  test_function_name
    taskq-super / taskq-new / run-all          test_name

Not one is `function_name`. Measured across all seven: the old predicate
selects 0 entries in every project.

**Bug 2 — the haystack is always empty.** The section capture is

    re.search(r"(?i)^#{1,4}\\s+[^\\n]*Deferred[^\\n]*\\n(.*?)(?=\\n#{1,4}\\s+|$)",
              text, re.MULTILINE | re.DOTALL)

With `re.MULTILINE` in force, `$` matches at every line end, so `(.*?)`
satisfies the lookahead immediately and the group captures the empty string.
Measured on the three projects that have a Deferred section: 0 characters
captured, 17 / 0 / 11 `test_nfr` occurrences left in the remainder.

So bug 1 keeps bug 2 invisible. Fixing the field names alone would have
reported every unit/static NFR test in three delivered projects as missing
from a section it is sitting in.

With both fixed, the rule executes and passes: taskq-api 17 targets,
taskq-advance 6, run-all-by-workflow 11, all present, zero violations.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = [pytest.mark.core]


_DEFERRED_SPEC = """\
# TEST_SPEC.md

## FR-01: Task CRUD

| # | Test Function | Inputs | Type | Derivation |
|---|---|---|---|---|
| 1 | `test_fr01_ac1_creates` | body | integration | AC-1.1 |

## Deferred to Downstream Phases

> Unit / Static NFRs whose verifier is not `tests/integration/` are deferred
> to P3.

| # | NFR | Test Function | Layer | Title |
|---|---|---|---|---|
| 1 | NFR-02 | `test_nfr02_no_shell_eval_exec` | static | grep gate |
"""


def _project(tmp_path: Path, inventory: str, spec: str = _DEFERRED_SPEC) -> Path:
    proj = tmp_path / "proj"
    (proj / "02-architecture").mkdir(parents=True)
    (proj / "TEST_INVENTORY.yaml").write_text(inventory, encoding="utf-8")
    (proj / "02-architecture" / "TEST_SPEC.md").write_text(spec, encoding="utf-8")
    return proj


# Each block is one corpus project's actual spelling of the same three facts.
_SPELLINGS = {
    "taskq-api": "        - {tc_id: TC-N02-01, fr: NFR-02, layer: unit,"
                 " test_function_name: test_nfr02_no_shell_eval_exec,"
                 " spec_ref: AC-N2.1}\n",
    "taskq-advance": "        - {tc_id: TC-N02-01, nfr: NFR-02, layer: unit,"
                     " test_function: test_nfr02_no_shell_eval_exec,"
                     " ac_ref: AC-N2.1}\n",
    "run-all": "        - {tc_id: TC-N02-01, fr_id: NFR-02, layer: unit,"
               " test_name: test_nfr02_no_shell_eval_exec,"
               " spec_reference: AC-N2.1}\n",
}


def _inventory(entry: str) -> str:
    return ("# TEST_INVENTORY.yaml — P1 Naming Authority\n"
            "format_version: '1.1'\n"
            "test_inventory:\n"
            "    tests:\n" + entry)


@pytest.mark.parametrize("spelling", sorted(_SPELLINGS), ids=sorted(_SPELLINGS))
def test_the_function_name_is_found_whatever_the_field_is_called(tmp_path, spelling):
    """The rule's population, built from what a value IS.

    No corpus project spells it `function_name`, and none was ever told to:
    the template defines no `test_inventory:` key, and the prompt that
    mandates one does so to identify the FILE, not to schema its contents.
    """
    from cli.checks.specs import nfr_layering_targets

    proj = _project(tmp_path, _inventory(_SPELLINGS[spelling]))
    targets = nfr_layering_targets(proj)

    assert [t["test_fn"] for t in targets] == ["test_nfr02_no_shell_eval_exec"]
    assert targets[0]["nfr"] == "NFR-02"


def test_a_cross_reference_is_not_the_subject(tmp_path):
    """`cross_ref_nfrs` is what an FR test mentions, not what it verifies.

    Reading any NFR-shaped token in the row turns 63 FR tests across five
    projects into NFR tests — `test_fr01_pydantic_validation_422` among them —
    and the rule would demand each be moved into a section for NFR tests. The
    subject is a singular field whose whole value is an NFR id.
    """
    from cli.checks.specs import nfr_layering_targets

    # A list, which is how all six projects that write the field write it.
    proj = _project(tmp_path, _inventory(
        "        - {tc_id: TC-FR02-02, fr: FR-02, layer: unit,"
        " test_name: test_fr02_ac2_subprocess_no_shell_true,"
        " cross_ref_nfrs: [NFR-02]}\n"))
    assert nfr_layering_targets(proj) == []

    # And as a bare string, which none of them writes today. The first version
    # of this test only had the list case, and the counter-proof showed why
    # that was not a test of anything: adding `cross_ref_nfrs` to the subject
    # fields left it green, because the list/str type check was doing the
    # work. Round 19's mother defect — the assertion passed for a reason other
    # than the rule it was written to pin.
    proj2 = _project(tmp_path / "b", _inventory(
        "        - {tc_id: TC-FR02-02, fr: FR-02, layer: unit,"
        " test_name: test_fr02_ac2_subprocess_no_shell_true,"
        " cross_ref_nfrs: NFR-02}\n"))
    assert nfr_layering_targets(proj2) == []


def test_the_deferred_section_is_not_captured_as_the_empty_string(tmp_path):
    """Bug 2 on its own: `re.M | re.S` makes `$` a line end.

    The lookahead `(?=\\n#{1,4}\\s+|$)` is satisfied at the first newline, so
    `(.*?)` captured nothing — on all three corpus projects that have the
    section, 0 characters, with 17 / 0 / 11 `test_nfr` occurrences left
    outside it.
    """
    from cli.checks.specs import deferred_section_text

    text = deferred_section_text(_DEFERRED_SPEC)

    assert "test_nfr02_no_shell_eval_exec" in text, repr(text)
    assert "test_fr01_ac1_creates" not in text, "the FR table is not deferred"


def test_a_declared_unit_nfr_test_outside_the_section_is_reported(tmp_path):
    """The rule doing its job — the direction that was never once exercised."""
    from cli.checks.specs import nfr_layering_violations

    spec = _DEFERRED_SPEC.replace("test_nfr02_no_shell_eval_exec",
                                  "test_nfr02_something_else")
    proj = _project(tmp_path, _inventory(_SPELLINGS["taskq-advance"]), spec)

    violations = nfr_layering_violations(proj)
    assert len(violations) == 1, violations
    assert "test_nfr02_no_shell_eval_exec" in violations[0]
    assert "NFR-02" in violations[0]


def test_the_shape_three_delivered_projects_ship_passes(tmp_path):
    """The counter-direction, and the reason both bugs had to be fixed together.

    Fixing the field names alone would have reported taskq-api's 17 targets,
    taskq-advance's 6 and run-all-by-workflow's 11 as absent from a section
    every one of them is sitting in.
    """
    from cli.checks.specs import nfr_layering_violations

    proj = _project(tmp_path, _inventory(
        _SPELLINGS["taskq-api"] + _SPELLINGS["taskq-advance"]))

    assert nfr_layering_violations(proj) == []


def test_an_entry_whose_name_cannot_be_read_is_not_silently_dropped(tmp_path):
    """Could-not-measure is a sentence, not a pass (Rounds 32/35).

    An entry with no identifier-shaped value names no test, and the rule that
    would have checked it has to say so rather than shrink its own population
    the way `function_name` silently did.
    """
    from cli.checks.specs import nfr_layering_violations

    proj = _project(tmp_path, _inventory(
        "        - {tc_id: TC-N02-01, nfr: NFR-02, layer: unit,"
        " description: 'the shell=True scan'}\n"))

    violations = nfr_layering_violations(proj)
    assert len(violations) == 1, violations
    assert "TC-N02-01" in violations[0]
    assert "no test function name" in violations[0]
