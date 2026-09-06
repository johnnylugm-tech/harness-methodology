"""Round 103 — the block listed a module and forbade the only way to cover it.

`contract_coverage_gap` lists the delivered modules no import-linter contract
constrains. `_delivered_modules` resolves `src/taskq_api/__init__.py` to the
dotted name `taskq_api`, so the tree's own root package is in that list, and
`contract_coverage_blocking_reason` printed it among the modules to add to a
contract's sources — directly above:

    "Two shortcuts do not count ... deleting the contracts ... and naming the
     root package, which covers every module you will ever add and retires
     this check for the project."

There is no third option. A contract that reaches the root package reaches
everything under it. The block asked for something and forbade the only answer.

Round 99 站3 wrote that remedy and added `contract_decides` so a contract that
cannot fail confers nothing. It did not remove the demand that produces the
shape, and taskq-done walked into it four days later — the fifth instance of
this escape, and the first by a project that had read the corrected wording.

Reproduced exactly, from taskq-done's tree at 698b4a8 with the layering-only
contract it had at the time:

    gap  ['taskq_api', 'taskq_api.app', 'taskq_api.errors']

three, matching its ledger row "3 delivered module(s) are outside every
import-linter contract" verbatim, with the root package first. It answered all
three in one `forbidden` stanza; because one of them is the root, the gap went
to zero, and `lint-imports` on that same tree reports `Contracts: 1 kept, 1
broken` — the root reaches SQLAlchemy through `repository`, the layer that
contract exists to exempt.

Measured over the eleven corpus projects with a contract and a readable root
package: 68 modules in the gaps, and the bare root is one of them in **eight**
of the eleven. taskq-redo, finished at P9, is still being told to cover
`taskq_api`.

RECORDED, NOT BLOCKED — AND WHY THAT IS NOT A DELETION

Round 67 站0 is right that `shopfront/__init__.py` ships and can hold imports
no contract reaches. That finding is kept: `record_constraint_status` writes
the WHOLE gap to the degradation ledger as `uncovered_modules`, and
`delivery_fingerprint` records it as `modules_outside_every_contract`. Only the
*stop* changes, which is the split Round 54 made for `declared_only` — a
demand whose only satisfaction makes the project's own contract less true is
recorded and never blocked.

TWO THINGS DELIBERATELY NOT DONE

Refusing coverage credited by a contract `lint-imports` reports BROKEN:
`architecture_constraints` is tier 1, threshold 100, `requires_tool_execution:
true`, so a broken contract already scores 0 and blocks. A second stop on the
same fact is a tripwire with no live effect (Round 30).

Dropping modules that currently import nothing: measured, that would remove 19
of the 68 gap entries, and it contradicts `contract_coverage_gap`'s own stated
rationale — "this reports the contract's shape and not the violation: by the
time there is a violation the contract has already stopped being the thing
that would have caught it". An empty `__init__.py` that gains an import later
is exactly the case that rationale exists for. The root package is different
not because it imports nothing today but because it has no answer at all.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.quality_gate.arch_constraints import (
    contract_coverage_blocking_reason,
    contract_coverage_gap,
)

pytestmark = [pytest.mark.core]

_SAB = {"layers": [], "architecture_constraints": []}

_LAYERING_ONLY = """\
[importlinter]
root_package = pkg

[importlinter:contract:layering]
name = layering
type = layers
layers =
    pkg.api
    pkg.repository
"""


def _project(tmp_path: Path, importlinter: str, modules=(), *,
             pkg: str = "pkg", src_rel: str = "03-development/src") -> Path:
    (tmp_path / ".methodology").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".methodology" / "SAB.json").write_text(
        json.dumps(_SAB), encoding="utf-8")
    (tmp_path / ".importlinter").write_text(importlinter, encoding="utf-8")
    src = tmp_path / src_rel / pkg
    src.mkdir(parents=True)
    (src / "__init__.py").write_text("", encoding="utf-8")
    for rel in modules:
        path = src / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.parent != src and not (path.parent / "__init__.py").exists():
            (path.parent / "__init__.py").write_text("", encoding="utf-8")
        path.write_text("", encoding="utf-8")
    return tmp_path


# ── the rule ────────────────────────────────────────────────────────────────

def test_the_block_does_not_name_the_root_package(tmp_path) -> None:
    """`pkg` is the container of every layer. Which contract constrains it?

    The only answer covers the whole tree — the shortcut this same message
    calls wrong two lines further down.
    """
    project = _project(tmp_path, _LAYERING_ONLY, ["api/routes.py", "app.py"])
    reason = contract_coverage_blocking_reason(project)
    assert reason, "pkg.app is uncovered and the gate must still stop"
    assert "\n  pkg\n" not in reason, (
        f"the block lists the root package as a module to cover, above a "
        f"remedy that forbids covering it:\n{reason}")


def test_the_root_package_is_still_recorded(tmp_path) -> None:
    """Round 54's split, and Round 67 站0's finding kept. The producer is
    unchanged, so the ledger row and the delivery fingerprint still carry it."""
    project = _project(tmp_path, _LAYERING_ONLY, ["api/routes.py", "app.py"])
    assert "pkg" in contract_coverage_gap(project), contract_coverage_gap(project)


def test_a_module_outside_every_contract_is_still_blocked(tmp_path) -> None:
    """Reverse control. `pkg.app` is the composition root and has an honest
    answer — put it in a layer, or in a forbidden contract's sources."""
    project = _project(tmp_path, _LAYERING_ONLY, ["api/routes.py", "app.py"])
    reason = contract_coverage_blocking_reason(project)
    assert reason and "pkg.app" in reason, reason
    assert reason.startswith("1 delivered module(s)"), (
        f"the count must describe what is being blocked on, not the gap it "
        f"was filtered from:\n{reason}")


def test_a_sub_package_is_still_blocked(tmp_path) -> None:
    """Only the ROOT is exempt. `pkg.support` is a package too, and unlike the
    root it can be named in a contract without covering everything."""
    project = _project(tmp_path, _LAYERING_ONLY,
                       ["api/routes.py", "support/util.py"])
    reason = contract_coverage_blocking_reason(project)
    assert reason and "pkg.support" in reason, reason


def test_the_root_is_exempt_in_the_projects_own_spelling(tmp_path) -> None:
    """taskq-advance writes `root_package = 03-development.src.taskq_api` —
    the source root spelled as dots (Round 55). The exemption compares against
    what the contract file declares, so it holds in that spelling too; a rule
    keyed on "a single dotted segment" would miss it, which is why this does
    not reuse `sab_amender.container_packages` (Round 101 站1b, the same idea
    one file over, on a population that has no declared root to compare to).
    """
    contract = _LAYERING_ONLY.replace(
        "root_package = pkg", "root_package = 03-development.src.pkg"
    ).replace("pkg.api", "03-development.src.pkg.api").replace(
        "pkg.repository", "03-development.src.pkg.repository")
    project = _project(tmp_path, contract, ["api/routes.py", "app.py"])
    reason = contract_coverage_blocking_reason(project)
    assert reason and "03-development.src.pkg.app" in reason, reason
    assert "\n  03-development.src.pkg\n" not in reason, reason


def test_naming_the_root_still_covers_what_is_under_it(tmp_path) -> None:
    """Unchanged, and deliberately so. A `forbidden` contract over the whole
    tree is a real boundary — every module's imports are checkable against its
    target — and refusing to credit a deliberate whole-tree ban would be a
    false accusation (Round 46). This round stops the framework ASKING for it,
    not the project choosing it."""
    project = _project(tmp_path, _LAYERING_ONLY + """
[importlinter:contract:no-orm]
name = no-orm
type = forbidden
source_modules =
    pkg
forbidden_modules =
    sqlalchemy
""", ["api/routes.py", "app.py"])
    assert contract_coverage_gap(project) == []
    assert contract_coverage_blocking_reason(project) is None


def test_a_project_whose_only_gap_was_the_root_stops_blocking(tmp_path) -> None:
    """The end of the chain: a project that covers every module it can answer
    for is not held open by the one question it cannot answer truthfully."""
    project = _project(tmp_path, _LAYERING_ONLY, ["api/routes.py",
                                                  "repository/db.py"])
    assert contract_coverage_gap(project) == ["pkg"]
    assert contract_coverage_blocking_reason(project) is None


# ── the witness ─────────────────────────────────────────────────────────────

_TASKQ_DONE_MODULES = (
    "api/deps.py", "api/exception_handlers.py", "api/tasks.py",
    "app.py", "errors.py",
    "models/base.py", "models/task.py", "models/task_result.py",
    "repository/session.py", "repository/task_repository.py",
    "service/auth.py", "service/task_service.py",
)

_TASKQ_DONE_LAYERING = """\
[importlinter]
root_package = taskq_api
include_external_packages = True

[importlinter:contract:layering]
name = layering
type = layers
layers =
    taskq_api.api
    taskq_api.service
    taskq_api.repository
    taskq_api.models
"""


def test_the_taskq_done_shape_no_longer_demands_the_root(tmp_path) -> None:
    """taskq-done at 698b4a8, layering-only contract — the state its ledger
    row "3 delivered module(s) are outside every import-linter contract"
    describes. Two of the three had an answer; the third was the root package,
    and answering it is what produced a contract `lint-imports` reports broken.
    """
    project = _project(tmp_path, _TASKQ_DONE_LAYERING, _TASKQ_DONE_MODULES,
                       pkg="taskq_api")
    assert contract_coverage_gap(project) == [
        "taskq_api", "taskq_api.app", "taskq_api.errors"], (
        "the producer must still see all three — the ledger row and the "
        "delivery fingerprint are where Round 67 站0's finding lives")

    reason = contract_coverage_blocking_reason(project)
    assert reason and reason.startswith("2 delivered module(s)"), reason
    assert "taskq_api.app" in reason and "taskq_api.errors" in reason, reason
    assert "\n  taskq_api\n" not in reason, (
        f"the block still names the root package:\n{reason}")
