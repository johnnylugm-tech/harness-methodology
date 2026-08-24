"""Round 73 站3 — the same defect blocked one project and passed another.

`CONSTRAINT_EXECUTOR_CANDIDATES` matches a SAB constraint string against a
keyword list, and its own comment records that the strings are project-invented
("the two trees built from the same SPEC.md share one string out of twelve").
The keyword lists were induced from the strings this corpus happened to use —
not from the vocabulary of the tool that would decide the constraint.

import-linter has exactly three contract types: `layers`, `forbidden`,
`independence`. `independence` was a keyword. The other two were not.

Measured across the corpus, one real constraint — "sqlalchemy may only be
imported from the repository layer" — declared by five projects:

    taskq-api      sqlalchemy_only_in_repository                  enforced       correct
    taskq-advance  sqlalchemy_imports_only_in_repository_layer    enforced       correct
    taskq-super    sqlalchemy_only_in_repository                  unconfigured   correct, blocked
    taskq-cc       sqlalchemy imports allowed only in repository  declared_only  HAS a forbidden contract
    taskq-new      sqlalchemy import forbidden outside repository declared_only  has NONE, passed

`only_in` carries an underscore, so taskq-cc's "only in" misses; taskq-new
says `forbidden` and `outside`, and neither was a keyword. Round 54's comment
cites taskq-super's version of this constraint as the case `unconfigured`
exists for. taskq-new's version reached Gate 4 PASS at 94.59 with `.importlinter`
carrying no forbidden contract at all, and an external audit found it later.

The `layers` family missed all ten of its declarations for the same reason:
the keywords were `layering` / `layered`, never `layers`.

Singular `layer` is deliberately NOT a keyword, and that is the important
half. taskq-api declares `single_auth_dependency_at_api_layer`, which no
import-linter contract can decide; with `layer` in the list it would match the
layers candidate, and taskq-api HAS a layers contract — so the row would read
`enforced`. Trading an abstention for a false endorsement is worse than the
abstention.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = [pytest.mark.core]


def _project(tmp_path: Path, importlinter: "str | None") -> Path:
    proj = tmp_path / "proj"
    proj.mkdir(parents=True)
    if importlinter is not None:
        (proj / ".importlinter").write_text(importlinter, encoding="utf-8")
    return proj


_LAYERS_ONLY = """\
[importlinter]
root_package = taskq

[importlinter:contract:fr01-layers]
type = layers
name = fr01-layers
layers =
    taskq.api
    taskq.service
    taskq.repository
"""

_FORBIDDEN_AND_LAYERS = _LAYERS_ONLY + """
[importlinter:contract:no-orm-leak]
type = forbidden
name = no-orm-leak
source_modules =
    taskq.api
    taskq.service
forbidden_modules =
    sqlalchemy
"""


def test_taskq_new_declared_a_forbidden_contract_and_shipped_none(tmp_path):
    """The case the audit found, verbatim.

    `.importlinter` here is taskq-new's: three contracts, `layers` and two
    `independence`, no `forbidden`. SPEC §4 NFR-06 requires one in as many
    words ("額外禁令(forbidden contract)"), the SAB repeats it, and the row
    read `declared_only` — the state that is never blocked, because for a
    constraint nothing can decide the only way to clear a block is to delete
    a true declaration.
    """
    from core.quality_gate.arch_constraints import (
        STATUS_UNCONFIGURED, classify_constraints, unconfigured_blocking_reason,
    )

    proj = _project(tmp_path, _LAYERS_ONLY)
    rows = classify_constraints(
        ["sqlalchemy import forbidden outside repository and models "
         "(NFR-06 forbidden contract)"], proj)

    assert rows[0]["status"] == STATUS_UNCONFIGURED, rows[0]
    reason = unconfigured_blocking_reason(rows)
    assert reason and "forbidden" in reason, reason


def test_the_same_constraint_with_the_contract_present_is_enforced(tmp_path):
    """taskq-cc's wording over taskq-cc's config: it really is checked."""
    from core.quality_gate.arch_constraints import (
        STATUS_ENFORCED, classify_constraints,
    )

    proj = _project(tmp_path, _FORBIDDEN_AND_LAYERS)
    row = classify_constraints(
        ["sqlalchemy imports allowed only in repository layer"], proj)[0]

    assert row["status"] == STATUS_ENFORCED, row
    assert "no-orm-leak" in row["evidence"], row


def test_a_layers_constraint_is_read_as_a_layers_constraint(tmp_path):
    """`layers` was never a keyword — only `layering` and `layered`.

    taskq-new writes `layers api > service > repository > models (NFR-06)` and
    ships a layers contract; the row said nothing could decide it.
    """
    from core.quality_gate.arch_constraints import (
        STATUS_ENFORCED, STATUS_UNCONFIGURED, classify_constraints,
    )

    wording = "layers api > service > repository > models (NFR-06)"

    with_contract = classify_constraints([wording], _project(tmp_path / "a", _LAYERS_ONLY))
    assert with_contract[0]["status"] == STATUS_ENFORCED, with_contract[0]

    without = classify_constraints([wording], _project(tmp_path / "b", None))
    assert without[0]["status"] == STATUS_UNCONFIGURED, without[0]


def test_a_constraint_about_one_layer_is_not_a_layering_contract(tmp_path):
    """taskq-api's `single_auth_dependency_at_api_layer`, and the line not crossed.

    Adding singular `layer` would recover a few more matches and would also
    make this row read `enforced` — taskq-api has a layers contract, and that
    contract has nothing to say about auth dependencies. The row stays
    `declared_only`: nothing here can decide it, and saying so is the answer
    Round 43 requires.
    """
    from core.quality_gate.arch_constraints import (
        STATUS_DECLARED_ONLY, classify_constraints,
    )

    proj = _project(tmp_path, _FORBIDDEN_AND_LAYERS)
    row = classify_constraints(["single_auth_dependency_at_api_layer"], proj)[0]

    assert row["status"] == STATUS_DECLARED_ONLY, row


def test_every_import_linter_candidate_names_its_own_contract_type():
    """The invariant, so the next keyword list is not induced from a corpus.

    An import-linter candidate declares in `requires` which contract type
    decides it. That word is the executor's own vocabulary and it is what a
    project writing the constraint reaches for — SPEC §4 NFR-06 says
    "forbidden contract" and "layers contract" in exactly those words. A
    candidate whose keywords omit it is matching on what some earlier project
    happened to call the thing.
    """
    from core.quality_gate.arch_constraints import CONSTRAINT_EXECUTOR_CANDIDATES

    for cand in CONSTRAINT_EXECUTOR_CANDIDATES:
        if cand["executor"] != "import-linter":
            continue
        kind, contract_type = cand["requires"]
        assert kind == "contract", cand
        assert contract_type in cand["keywords"], (
            f"candidate {cand['about']!r} is decided by a `{contract_type}` "
            f"contract and does not list {contract_type!r} as a keyword"
        )
