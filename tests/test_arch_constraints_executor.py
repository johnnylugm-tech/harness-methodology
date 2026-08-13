"""A declared constraint either has an executor or says it has none (Round 51 站0).

Two measurements, both on trees built from the same SPEC.md.

**The SAB's `architecture_constraints` list has exactly one consumer, and it is
the judged party.** A repository-wide grep for the field finds
`sab_parser` → `quality_manifest.json` → two readers: `core/claude_md.py:105`,
which renders the list into the project's own CLAUDE.md, and
`harness/harness_bridge.py:1908`, which renders it into the gate's evaluation
prompt under the line

    > When evaluating the `architecture` dimension, validate code against
    > these constraints.

No deterministic code ever reads a constraint. The `architecture_constraints`
*gate dimension* shares the name but is a different thing: it runs
`import-linter` (`harness/toolchains/registry.py:535`) over a contract the
project wrote. So the SAB list is enforced by asking the agent to enforce it,
and the same agent then writes `05-verification/VERIFICATION_REPORT.md`.
taskq-api's says `sqlalchemy_only_in_repository` and
`single_auth_dependency_at_api_layer` "are honored at HEAD". Neither is:
`app.py:39` does `from sqlalchemy import create_engine, text as sql_text`, and
`/v1/metrics` is mounted on the app with no auth dependency at all.

**The contract that does run has a hole the delivered tree walked through.**
Both projects' `.importlinter` name `taskq_api.api` and `taskq_api.service` as
the forbidden-import sources. `taskq_api.app` is in neither, and is not a
submodule of either — so `lint-imports` passes while the composition root
imports SQLAlchemy directly. taskq-advance has the identical hole and simply
did not use it, which is why the guard below has to fire on the contract, not
on the violation.
"""

from __future__ import annotations

from pathlib import Path

import pytest

# The six strings taskq-api's SAB.json actually carries, verbatim.
_API_CONSTRAINTS = [
    "no_circular_dependencies",
    "sqlalchemy_only_in_repository",
    "single_auth_dependency_at_api_layer",
    "errors_and_config_are_independence_modules",
    "fr07_round_trip_must_preserve_data",
    "rate_limit_update_in_single_transaction_with_row_lock",
]

# taskq-advance's, verbatim — a different vocabulary for the same six slots,
# which is why the registry cannot be a closed enum of constraint names.
_ADVANCE_CONSTRAINTS = [
    "no_circular_dependencies",
    "sqlalchemy_imports_only_in_repository_layer",
    "no_shell_true_eval_exec_in_src",
    "handler_max_lines_40",
    "file_max_lines_400",
    "directory_max_files_15",
]

# taskq-api's `.importlinter`, verbatim.
_IMPORTLINTER = """\
[importlinter]
root_package = taskq_api
include_external_packages = True

[importlinter:contract:layered-architecture]
name = layered-architecture
type = layers
layers =
    taskq_api.api
    taskq_api.service
    taskq_api.repository
    taskq_api.models

[importlinter:contract:sqlalchemy-isolation]
name = sqlalchemy-isolation
type = forbidden
source_modules =
    taskq_api.api
    taskq_api.service
forbidden_modules =
    sqlalchemy
allow_indirect_imports = True
"""

_MODULES = [
    "taskq_api/__init__.py",
    "taskq_api/app.py",
    "taskq_api/config.py",
    "taskq_api/errors.py",
    "taskq_api/api/__init__.py",
    "taskq_api/api/tasks.py",
    "taskq_api/service/__init__.py",
    "taskq_api/service/auth.py",
    "taskq_api/repository/__init__.py",
    "taskq_api/repository/session.py",
    "taskq_api/models/__init__.py",
    "taskq_api/models/orm.py",
]


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    (tmp_path / ".importlinter").write_text(_IMPORTLINTER, encoding="utf-8")
    src = tmp_path / "03-development" / "src"
    for rel in _MODULES:
        p = src / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("", encoding="utf-8")
    return tmp_path


@pytest.mark.parametrize(
    "constraints", [_API_CONSTRAINTS, _ADVANCE_CONSTRAINTS],
    ids=["taskq-api", "taskq-advance"],
)
def test_every_declared_constraint_is_classified(constraints):
    """No constraint may pass through unclassified.

    `unknown` is a legitimate answer and `declared_only` is a legitimate
    status. Silence is not: today the list reaches a prompt and a document,
    and nothing anywhere records that six of six have no executor.
    """
    from core.quality_gate.arch_constraints import classify_constraints

    rows = classify_constraints(constraints)
    assert [r["constraint"] for r in rows] == constraints, (
        "classify_constraints must return one row per declared constraint, "
        "in order — a constraint that falls out of the list is a constraint "
        "nobody has to account for"
    )
    for row in rows:
        assert row["status"] in {"enforced", "declared_only"}, (
            f"{row['constraint']!r} came back with status {row.get('status')!r}"
        )
        if row["status"] == "enforced":
            assert row.get("executor"), (
                f"{row['constraint']!r} claims to be enforced but names no executor"
            )


def test_a_constraint_with_no_executor_is_recorded_as_such():
    """The four project-invented strings have no deterministic checker.

    This is the Round 43 shape: the correct fix for "detected but no executor"
    is not to pretend there is one, it is to write down that there is not — so
    a verification report cannot claim the constraint was honoured.
    """
    from core.quality_gate.arch_constraints import classify_constraints

    rows = {r["constraint"]: r for r in classify_constraints(_API_CONSTRAINTS)}
    assert rows["single_auth_dependency_at_api_layer"]["status"] == "declared_only", (
        "taskq-api's VERIFICATION_REPORT certifies this constraint as honoured "
        "while /v1/metrics is mounted with no auth dependency — nothing in the "
        "framework can check it, and that has to be the recorded answer"
    )
    assert rows["fr07_round_trip_must_preserve_data"]["status"] == "declared_only"


def test_the_import_contract_covers_the_package_it_claims_to_constrain(project):
    """`taskq_api.app` is outside every source_modules list, so it is unconstrained."""
    from core.quality_gate.arch_constraints import contract_coverage_gap

    gap = contract_coverage_gap(project)
    assert "taskq_api.app" in gap, (
        "the sqlalchemy-isolation contract names taskq_api.api and "
        "taskq_api.service as its sources; taskq_api.app is in neither, which "
        "is how the composition root came to import create_engine while "
        "lint-imports reported the contract kept"
    )
    assert "taskq_api.repository.session" not in gap, (
        "the repository layer is legitimately allowed to import sqlalchemy — "
        "a gap report that names it is reporting noise, not a hole"
    )


# taskq-advance spells the same field as a path: `root_package =
# 03-development.src.taskq_api`. Both trees are in scope, so both spellings
# have to resolve — the first version of this scanner matched only the last
# segment and reported advance as having zero delivered modules and therefore
# a clean contract, which is the same hole with a nicer answer.
_IMPORTLINTER_PATH_ROOT = """\
[importlinter]
root_package = 03-development.src.taskq_api
include_external_packages = True

[importlinter:contract:layering]
name = Layering
type = layers
layers =
    03-development.src.taskq_api.api
    03-development.src.taskq_api.service
    03-development.src.taskq_api.repository
    03-development.src.taskq_api.models

[importlinter:contract:sqlalchemy_repository_only]
name = SQLAlchemy in repository only
type = forbidden
source_modules =
    03-development.src.taskq_api.api
    03-development.src.taskq_api.service
forbidden_modules =
    sqlalchemy
allow_indirect_imports = True
"""


def test_a_dotted_source_root_resolves_in_the_projects_own_spelling(tmp_path):
    from core.quality_gate.arch_constraints import contract_coverage_gap

    (tmp_path / ".importlinter").write_text(
        _IMPORTLINTER_PATH_ROOT, encoding="utf-8")
    src = tmp_path / "03-development" / "src"
    for rel in _MODULES:
        p = src / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("", encoding="utf-8")

    gap = contract_coverage_gap(tmp_path)
    assert "03-development.src.taskq_api.app" in gap, (
        "with a path-shaped root_package the scanner must answer in that same "
        "shape — the contracts are written in it, so anything else compares "
        "two vocabularies and finds no overlap"
    )
    assert "03-development.src.taskq_api.repository.session" not in gap


def test_no_import_contract_at_all_is_reported_as_absent_not_clean(tmp_path):
    """Round 46: a project with no contract is not a project with a kept one."""
    from core.quality_gate.arch_constraints import contract_coverage_gap

    (tmp_path / "03-development" / "src" / "taskq_api").mkdir(parents=True)
    assert contract_coverage_gap(tmp_path) == []


def test_what_has_no_executor_reaches_the_ledger(tmp_path):
    """The record is the enforcement: a report cannot certify what this names."""
    import json

    from core.quality_gate.arch_constraints import record_constraint_status

    (tmp_path / ".importlinter").write_text(_IMPORTLINTER, encoding="utf-8")
    src = tmp_path / "03-development" / "src"
    for rel in _MODULES:
        p = src / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("", encoding="utf-8")

    record_constraint_status(
        tmp_path, {"architecture_constraints": _API_CONSTRAINTS})

    ledger = tmp_path / ".methodology" / "degradations.jsonl"
    rows = [json.loads(ln) for ln in ledger.read_text(encoding="utf-8").splitlines() if ln.strip()]
    by_what = {r["what"]: r for r in rows}

    unenforced = next(r for k, r in by_what.items() if "no executor" in k)
    assert "single_auth_dependency_at_api_layer" in unenforced["data"]["declared_only"]
    assert "no_circular_dependencies" not in unenforced["data"]["declared_only"]

    uncovered = next(r for k, r in by_what.items() if "outside every" in k)
    assert "taskq_api.app" in uncovered["data"]["uncovered_modules"]
