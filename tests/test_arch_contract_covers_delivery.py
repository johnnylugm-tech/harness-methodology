"""A contract of the right kind is not a contract over the right modules
(Round 55 站3).

Round 54 gave `architecture_constraints` three states and made `unconfigured`
block. `_evaluate`'s contract branch asks one question — is there a contract
whose `type` is `layers` (or `forbidden`) — and answers `enforced` on the first
match. A single-layer `layers` section satisfies that question and constrains
nothing, so the state Round 54 built to stop a project switching its checker
off can be cleared by switching it back on over an empty domain.

The framework already owns the right question. `contract_coverage_gap` returns
the delivered modules of the root package that no contract's `sources` covers,
and `delivery_fingerprint` has been recording it as
`modules_outside_every_contract` since Round 54. Nothing consults it when
deciding whether a constraint is enforced.

Measured 2026-08-17 across the seven projects, and the reason this station is
two changes rather than one: `read_import_contracts` reads `root_package` and
import-linter also accepts `root_packages`. taskq-plus and taskq-super spell it
in the plural, so both came back with an empty root package, and
`contract_coverage_gap`'s first guard (`if not root_package … return []`)
returned "no gap" for both.

    project         root_package read   gap before   gap after
    taskq-plus      ''                  0            5
    taskq-super     ''                  0            20
    taskq-renew     taskq_plus          14           14
    taskq-advance   03-development…     5            5
    taskq-api       taskq_api           5            5

taskq-super's twenty are every module it delivers: its only contract is the
two-module `config_and_errors_independence`, and Gate 4 recorded
`architecture_constraints` as having no executor while reporting the tree clean.

Rejected here, and recorded because it was the plan: comparing the contract's
layer list against the layer chain in `SAB.json`'s `allowed_dependencies`.
Measured on the corpus it over-reaches — taskq-advance's SAB chain is
`app > api > service > repository > models` and its contract correctly names
the four that form the import order, so a chain rule flags a contract that is
right. Module coverage is decidable without deciding which layers belong in an
order.
"""

from __future__ import annotations

import json
from pathlib import Path

_SAB = {
    "layers": [
        {"name": "api", "modules": [{"name": "pkg.api"}],
         "allowed_dependencies": ["service"]},
        {"name": "service", "modules": [{"name": "pkg.service"}],
         "allowed_dependencies": ["repository"]},
        {"name": "repository", "modules": [{"name": "pkg.repository"}],
         "allowed_dependencies": []},
    ],
    "architecture_constraints": ["no_circular_dependencies"],
}

_CONTRACT_ONE_LAYER = """\
[importlinter]
root_packages =
    pkg

[importlinter:contract:layering]
name = Layering
type = layers
layers =
    pkg.api
"""

_CONTRACT_FULL = """\
[importlinter]
root_packages =
    pkg

[importlinter:contract:layering]
name = Layering
type = layers
layers =
    pkg.api
    pkg.service
    pkg.repository
"""


def _project(tmp_path: Path, importlinter: str) -> Path:
    (tmp_path / ".methodology").mkdir(parents=True)
    (tmp_path / ".methodology" / "SAB.json").write_text(
        json.dumps(_SAB), encoding="utf-8")
    (tmp_path / ".importlinter").write_text(importlinter, encoding="utf-8")
    src = tmp_path / "03-development" / "src" / "pkg"
    src.mkdir(parents=True)
    for name in ("__init__", "api", "service", "repository"):
        (src / f"{name}.py").write_text("", encoding="utf-8")
    return tmp_path


def test_root_packages_plural_is_read(tmp_path):
    """import-linter accepts both spellings; the parser read one.

    Two of the seven projects use the plural, and for both of them every check
    downstream of `root_package` returned the answer for "no configuration".
    """
    from core.quality_gate.arch_constraints import read_import_contracts

    parsed = read_import_contracts(_project(tmp_path, _CONTRACT_FULL))
    assert parsed["root_package"] == "pkg", (
        "`root_packages = \\n    pkg` is import-linter's own multi-package "
        "spelling and it read as no root package at all"
    )


def test_a_one_layer_contract_states_no_order(tmp_path):
    """The escape from Round 54's `unconfigured`, closed by a definition.

    `type = layers` with one entry answers "is there a contract of this kind"
    with yes and constrains nothing. Two is not a threshold — a `layers`
    contract IS a statement about ordering, and one element has no order.
    """
    from core.quality_gate.arch_constraints import (
        STATUS_UNCONFIGURED,
        classify_constraints,
    )

    rows = classify_constraints(
        ["no_circular_dependencies"], _project(tmp_path, _CONTRACT_ONE_LAYER))
    assert rows and rows[0]["status"] == STATUS_UNCONFIGURED, (
        "a `layers` contract naming one layer read as `enforced`, which is "
        "the state Round 54 introduced to mean 'this constraint is actually "
        "being decided'"
    )
    assert "order" in rows[0]["evidence"]


def test_a_real_contract_is_enforced_and_carries_its_gap(tmp_path):
    """The control, and the boundary written into the evidence.

    A contract that names an order is enforced. What it does NOT reach is
    reported in the same string and never decided on: measured over the seven
    projects, every correctly-layered one still leaves the composition root,
    `__main__`, config and errors outside every contract, so a rule that turned
    that count into a verdict would fail the projects it was written to pass
    (Round 32 站4 — a diagnostic is not a verdict).
    """
    from core.quality_gate.arch_constraints import (
        STATUS_ENFORCED,
        classify_constraints,
    )

    project = _project(tmp_path, _CONTRACT_FULL)
    # `pkg/__init__.py` is delivered and no layer names it — the same shape
    # every real project has, at its smallest.
    rows = classify_constraints(["no_circular_dependencies"], project)
    assert rows and rows[0]["status"] == STATUS_ENFORCED
    assert "outside every contract" in rows[0]["evidence"]
    assert "pkg" in rows[0]["evidence"]
