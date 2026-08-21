"""Round 67 站0 — a contract that does not cover the delivered code is not a
contract the gate may read as kept.

`core/quality_gate/arch_constraints.contract_coverage_gap()` returns the
delivered modules of the root package that no import-linter contract
constrains. Its own docstring ends:

    The caller decides what a missing contract means for its gate.

There is one caller. It writes a degradation row. taskq-cc's ledger carries
130 of them:

    {"uncovered_modules": ["taskq_api", "taskq_api.__main__", "taskq_api.cli"]}

and `lint-imports` reported the contract kept for the whole run, because those
three modules are in no contract's `sources`. The architecture dimension
scored 88.9 and Gate 4 published PASS.

Same shape as the rest of Round 67: the framework computed the true thing and
no verdict read it. Unlike the `declared_only` constraints beside it, this one
needs no judgement call and no guessing at module names — the gap is a list
the framework already has.

Scope note: this does NOT reopen Round 54's adjudication that a `declared_only`
constraint is recorded and never blocked. A constraint nothing can decide stays
undecided; a contract that exists and does not cover the tree is a different
statement.
"""

from __future__ import annotations

import pytest


def _project_with_a_partial_contract(tmp_path):
    """A tree whose import-linter config names two of its three modules."""
    src = tmp_path / "03-development" / "src" / "shopfront"
    src.mkdir(parents=True)
    for name in ("__init__.py", "api.py", "service.py", "cli.py"):
        (src / name).write_text("", encoding="utf-8")
    (tmp_path / ".importlinter").write_text(
        "[importlinter]\n"
        "root_package = shopfront\n"
        "\n"
        "[importlinter:contract:1]\n"
        "name = layers\n"
        "type = layers\n"
        "layers =\n"
        "    shopfront.api\n"
        "    shopfront.service\n",
        encoding="utf-8",
    )
    return tmp_path


def test_the_gap_is_still_computed(tmp_path):
    """The producer half, pinned so the consumer half has something to read."""
    from core.quality_gate.arch_constraints import contract_coverage_gap

    gap = contract_coverage_gap(_project_with_a_partial_contract(tmp_path))
    assert "shopfront.cli" in gap, (
        f"a delivered module outside every contract was not reported: {gap}"
    )


def test_an_uncovered_module_blocks(tmp_path):
    """The half that does not exist yet.

    Shape follows `unconfigured_blocking_reason`, which sits ten lines away in
    the same module and answers the same kind of question: a string when the
    gate must stop, None when it may go on.
    """
    from core.quality_gate.arch_constraints import contract_coverage_blocking_reason

    reason = contract_coverage_blocking_reason(
        _project_with_a_partial_contract(tmp_path)
    )
    assert reason, (
        "a project whose import-linter contract leaves a delivered module "
        "unconstrained was allowed through — `lint-imports` will report that "
        "contract kept no matter what that module imports"
    )
    assert "shopfront.cli" in reason, (
        f"the block has to name the modules the contract does not reach: {reason}"
    )


def test_a_project_with_no_contract_at_all_is_not_blocked(tmp_path):
    """Round 46's rule: an absent witness is absent, not failing.

    A project that declares no layering is not a project with a leaky one, and
    `contract_coverage_gap` returns [] there by design. Blocking it would be
    this round inventing a requirement nobody stated.
    """
    from core.quality_gate.arch_constraints import contract_coverage_blocking_reason

    (tmp_path / "03-development" / "src").mkdir(parents=True)
    assert contract_coverage_blocking_reason(tmp_path) is None


@pytest.mark.parametrize("covered", ["shopfront.cli", "shopfront"])
def test_a_contract_that_covers_the_tree_does_not_block(tmp_path, covered):
    """Satisfiable two ways — name the module, or name a package above it."""
    from core.quality_gate.arch_constraints import contract_coverage_blocking_reason

    project = _project_with_a_partial_contract(tmp_path)
    (project / ".importlinter").write_text(
        "[importlinter]\n"
        "root_package = shopfront\n"
        "\n"
        "[importlinter:contract:1]\n"
        "name = forbidden\n"
        "type = forbidden\n"
        "source_modules =\n"
        "    shopfront.api\n"
        "    shopfront.service\n"
        f"    {covered}\n"
        "forbidden_modules =\n"
        "    sqlalchemy\n",
        encoding="utf-8",
    )
    assert contract_coverage_blocking_reason(project) is None, (
        f"a contract naming {covered} still reported a gap"
    )
