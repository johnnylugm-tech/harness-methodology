"""Round 99 站3 — a contract of the right kind is not a contract that decides.

That sentence is not new here. `arch_constraints.classify_constraints` has
carried it since Round 55, in an inline branch, with its own reasoning:

    "a contract of the right kind is not automatically a contract that
     decides anything. `type = layers` with a single layer named satisfies
     the question above and expresses no order at all ... Two layers is not
     a threshold: a `layers` contract IS a statement about ordering, and one
     element has none."

`read_import_contracts` has two production consumers. That one has the rule.
`contract_coverage_gap`, twenty lines further down the same file, does not —
it counts a delivered module as constrained whenever its dotted name, or an
ancestor's, appears in ANY contract's module list, without asking whether
that contract can produce a violation. And the version that does have the
rule asks it only of `layers`, never of `independence` or `forbidden`.

Measured over the 13 corpus projects with an `.importlinter`: five have an
empty coverage gap, and all five name the bare root package in some
contract. Three of those five did it with a contract that cannot fail —

    taskq-redo   `independence`, modules = taskq_api            (1 module)
    taskq-redo   `independence`, modules = taskq_api.__main__   (1 module)
    taskq-new    `independence`, modules = taskq                (1 module)

— and taskq-new's single stanza is the only thing "covering" 13 delivered
modules, among them every `migrations/*` and `security.redact`.

The gate's own block message is where this comes from. `block_reason.py`
told the project, verbatim: "or name a package above them (naming the root
package covers everything under it)". The sentence is true — import-linter's
forbidden and independence contracts do include descendants — and following
it satisfies this gate for every module the project will ever add.

WHAT THIS ROUND DOES NOT DECIDE

taskq-wow wrote `type = forbidden`, `source_modules = taskq_api`,
`forbidden_modules = nonexistent_module_for_coverage`, and it stays covered
after this change. A forbidden contract naming an external module the
project does not depend on is structurally identical to a deliberate
"never import django" guard — import-linter accepts both without complaint
when `include_external_packages = True` (contracts/forbidden.py:220-233) —
and calling one of them vacuous would be a false accusation of the other.
Recorded in docs/PROPOSAL_ADJUDICATIONS.md with its reopen condition rather
than guessed at.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from core.quality_gate.arch_constraints import (
    contract_coverage_blocking_reason,
    contract_coverage_gap,
    read_import_contracts,
)

pytestmark = [pytest.mark.core]

REPO = Path(__file__).resolve().parents[1]

_LAYERS = """\
[importlinter]
root_package = pkg

[importlinter:contract:layers]
name = layers
type = layers
layers =
    pkg.api
    pkg.repo
"""


def _project(tmp_path: Path, contracts: str, modules=("api/a.py", "repo/b.py")) -> Path:
    project = tmp_path / "proj"
    src = project / "03-development" / "src" / "pkg"
    src.mkdir(parents=True)
    (src / "__init__.py").write_text("", encoding="utf-8")
    for rel in modules:
        path = src / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if not (path.parent / "__init__.py").exists() and path.parent != src:
            (path.parent / "__init__.py").write_text("", encoding="utf-8")
        path.write_text("", encoding="utf-8")
    (project / ".importlinter").write_text(contracts, encoding="utf-8")
    return project


# ---- the rule -------------------------------------------------------------

def test_a_one_module_independence_contract_covers_nothing(tmp_path) -> None:
    """taskq-redo and taskq-new's shape: one module, no pair to check."""
    project = _project(tmp_path, _LAYERS + """
[importlinter:contract:root-marker]
name = root-marker
type = independence
modules =
    pkg
""")
    assert "pkg" in contract_coverage_gap(project), contract_coverage_gap(project)


def test_a_two_module_independence_contract_does_cover(tmp_path) -> None:
    """Reverse control. Two modules is a statement that can be violated,
    and the rule must not read as 'independence never counts'."""
    project = _project(
        tmp_path,
        _LAYERS + """
[importlinter:contract:pair]
name = pair
type = independence
modules =
    pkg
    pkg.repo
""")
    assert "pkg" not in contract_coverage_gap(project), contract_coverage_gap(project)


def test_a_one_layer_layers_contract_covers_nothing(tmp_path) -> None:
    """Round 55's own case, asked of the second consumer."""
    project = _project(tmp_path, """\
[importlinter]
root_package = pkg

[importlinter:contract:solo]
name = solo
type = layers
layers =
    pkg
""")
    assert "pkg.api" in contract_coverage_gap(project), contract_coverage_gap(project)


def test_a_forbidden_contract_with_no_targets_covers_nothing(tmp_path) -> None:
    project = _project(tmp_path, _LAYERS + """
[importlinter:contract:empty]
name = empty
type = forbidden
source_modules =
    pkg
forbidden_modules =
""")
    assert "pkg" in contract_coverage_gap(project), contract_coverage_gap(project)


def test_a_forbidden_contract_with_a_target_does_cover(tmp_path) -> None:
    """Reverse control, and the shape this round deliberately does not
    judge: whether the named target is reachable is not decidable apart
    from a deliberate never-import guard."""
    project = _project(tmp_path, _LAYERS + """
[importlinter:contract:no-orm]
name = no-orm
type = forbidden
source_modules =
    pkg
forbidden_modules =
    sqlalchemy
""")
    assert "pkg" not in contract_coverage_gap(project), contract_coverage_gap(project)


# ---- one statement, both consumers ---------------------------------------

def test_the_verdict_travels_on_the_parse_not_in_each_consumer(tmp_path) -> None:
    """`decides` is answered once, where the contract is read."""
    project = _project(tmp_path, _LAYERS + """
[importlinter:contract:root-marker]
name = root-marker
type = independence
modules =
    pkg
""")
    by_name = {c["name"]: c for c in read_import_contracts(project)["contracts"]}
    assert by_name["root-marker"]["decides"] is False
    assert by_name["layers"]["decides"] is True


def test_the_coverage_gap_reads_that_answer_rather_than_recomputing_it(
        tmp_path, monkeypatch) -> None:
    """Counter-proof CP-13b, written after it found this missing.

    The test above pins that the PARSE answers the question. It says nothing
    about whether the consumer reads the answer: a second implementation
    inside `contract_coverage_gap`, faithful to all three branches and never
    touching `decides`, passed every other test in this file. That is the
    shape Round 97 CP-5b and Round 98 CP-11 both caught — a guard that
    checks the SSOT exists rather than that it is the thing being used —
    and this is its third appearance.

    Replacing the single definition must change what the consumer says.
    """
    project = _project(tmp_path, _LAYERS)
    assert contract_coverage_gap(project) == ["pkg"], "fixture drifted"

    import core.quality_gate.arch_constraints as ac
    monkeypatch.setattr(ac, "contract_decides", lambda *a, **k: False)
    assert "pkg.api" in contract_coverage_gap(project), (
        "contract_coverage_gap did not follow `contract_decides` — it is "
        "deciding for itself, and the two answers can now drift")


def test_the_constraint_classifier_reads_that_answer_too(
        tmp_path, monkeypatch) -> None:
    """The other consumer, held to the same rule. Round 55's branch lived
    here; moving it out is only a de-duplication if this reads the move."""
    import core.quality_gate.arch_constraints as ac

    project = _project(tmp_path, _LAYERS)
    rows = ac.classify_constraints(["layering"], project)
    assert rows and rows[0]["status"] == ac.STATUS_ENFORCED, rows

    monkeypatch.setattr(ac, "contract_decides", lambda *a, **k: False)
    rows = ac.classify_constraints(["layering"], project)
    assert rows and rows[0]["status"] == ac.STATUS_UNCONFIGURED, (
        "classify_constraints did not follow `contract_decides` — Round 55's "
        f"branch is still being answered locally: {rows}")


def test_the_remedy_no_longer_offers_the_root_package(tmp_path) -> None:
    """The block used to name the one action that silences it forever."""
    project = _project(tmp_path, """\
[importlinter]
root_package = pkg

[importlinter:contract:layers]
name = layers
type = layers
layers =
    pkg.api
    pkg.repo
""")
    reason = contract_coverage_blocking_reason(project)
    assert reason, "a project with an uncovered root package must still block"
    assert "covers everything under it" not in reason, reason


def _live_strings(tree: ast.AST) -> "list[tuple[int, str]]":
    docstrings: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)):
            body = getattr(node, "body", None)
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                docstrings.add(id(body[0].value))
    out: list = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Constant) and isinstance(n.value, str) \
                and id(n) not in docstrings:
            out.append((n.lineno, n.value))
        elif isinstance(n, ast.JoinedStr):
            out.append((n.lineno, "".join(
                v.value for v in n.values
                if isinstance(v, ast.Constant) and isinstance(v.value, str))))
    return out


def test_nothing_in_the_tree_still_advertises_the_root_package_escape() -> None:
    """Two files carried the sentence. A guard on one of them is how this
    repository's oldest defect keeps recurring."""
    offenders: list[str] = []
    for d in ("cli", "core", "harness", "scripts"):
        for path in sorted((REPO / d).rglob("*.py")):
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except (SyntaxError, OSError):
                continue
            for lineno, value in _live_strings(tree):
                if "naming the root package covers everything" in value.lower():
                    offenders.append(f"{path.relative_to(REPO)}:{lineno}")
    assert not offenders, (
        "a block message still tells the project that naming the root "
        "package clears this gate. It does — for every module the project "
        "will ever add, which is why it must not be offered:\n  "
        + "\n  ".join(offenders))
