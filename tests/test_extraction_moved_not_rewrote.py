"""An extracted helper must be the code that was there, not code like it.

Round 81 站6-9. Round 80 froze this repo's four largest functions rather than
split them, and the reason it recorded was:

    區塊抽取會改動函式自身文字,byte-equal 規則不適用

Half true. Extraction changes a function's own text — the run leaves and a call
site arrives — but it does not change the text of the run. And because a
module-level function's body and a run inside another module-level function
both sit at ONE indent level, the moved lines need no reindentation at all:
they are byte-identical, and Round 49-B's rule applies unchanged.

That is what this file checks, against the file as it stood before the
extraction rather than against the extraction's own output. `tests/golden/
extraction/<module>.py.before` is that file. It is large and it is meant to be:
a recording of the source at the moment of the move is the only thing that can
answer "was this a move?" afterwards.

WHY NOT REGENERATE IT LIKE THE OTHER GOLDENS

tests/golden/god_file_split/surface.json carries fingerprints and is
regenerable with REGEN_SPLIT_GOLDEN when a function is deliberately changed.
This one is not regenerable at all. Once an extracted body is edited on purpose,
its entry is REMOVED from _EXTRACTED with the reason in the commit — the claim
"this is the code that was there" has an expiry date, and pretending otherwise
by re-recording it would delete the only evidence the move ever happened.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests.support.dataflow import _bound, _loaded

pytestmark = [pytest.mark.core]

REPO = Path(__file__).resolve().parents[1]
GOLDEN = REPO / "tests" / "golden" / "extraction"

#: module path -> helpers extracted out of it, and the function they came from.
#: An entry leaves this dict the moment its body is deliberately edited.
_EXTRACTED: "dict[str, tuple[str, tuple[str, ...]]]" = {
    "cli/phase_cmds.py": ("_advance_prechecks", (
        "_precheck_cleared_dir_evidence",
        "_precheck_backup_artifacts",
        "_precheck_manifest_and_p1_baselines",
        "_precheck_per_fr_gate1_and_phase_truth",
        "_precheck_early_stage_pass",
        "_precheck_deliverable_anchors",
        "_precheck_scope_violations",
        "_precheck_p3_security_and_quality",
        "_precheck_stage_pass_staging",
    )),
}


def _function(tree: ast.Module, name: str) -> "ast.FunctionDef":
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name} is not defined any more")


def _body_text(source: str, func: "ast.FunctionDef") -> str:
    """The helper's body, docstring excluded — the part that was moved.

    A trailing `return None` is excluded too. It is generated rather than
    moved: mypy requires the fall-through path of an `int | None` return to be
    explicit, and the runs themselves never contain one — the extraction rule
    refuses a run that returns None, because the call site uses exactly that to
    mean "the helper did not return".
    """
    lines = source.splitlines(keepends=True)
    first = func.body[0]
    start = func.body[1].lineno if (
        isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
        and isinstance(first.value.value, str) and len(func.body) > 1
    ) else first.lineno

    end = func.end_lineno
    last = func.body[-1]
    if isinstance(last, ast.Return) and isinstance(last.value, ast.Constant) \
            and last.value.value is None:
        end = last.lineno - 1
        while end > start and lines[end - 1].lstrip().startswith("#"):
            end -= 1

    return "".join(lines[start - 1:end])


@pytest.mark.parametrize("module", sorted(_EXTRACTED))
def test_every_extracted_body_is_byte_identical_to_what_it_replaced(module):
    before = (GOLDEN / (Path(module).name + ".before")).read_text(encoding="utf-8")
    after = (REPO / module).read_text(encoding="utf-8")
    tree = ast.parse(after)

    rewritten = []
    for name in _EXTRACTED[module][1]:
        body = _body_text(after, _function(tree, name))
        if body not in before:
            rewritten.append(name)

    assert not rewritten, (
        f"these helpers are not the code that was extracted — their bodies do "
        f"not appear verbatim in {module} as it stood before the move:\n  "
        + "\n  ".join(rewritten)
        + "\n\nAn extraction is a MOVE. If one of these was deliberately "
          "changed, remove it from _EXTRACTED in that commit and say why; do "
          "not re-record the 'before' file, which would delete the only "
          "evidence the move happened."
    )


@pytest.mark.parametrize("module", sorted(_EXTRACTED))
def test_no_helper_reads_a_name_nobody_gives_it(module):
    """The static replacement for "every extracted run must be covered".

    The plan gated extraction on the suite executing each run, because a
    miscomputed parameter list surfaces as a `NameError` and a `NameError` is
    only loud where something runs. That gate was unreachable honestly — the
    runs inside `_advance_prechecks` sit behind its manifest-integrity check,
    and getting a fixture past it means hand-writing finalize receipts, which
    tests/test_evidence_outlives_the_phase.py already adjudicated as "writing
    fake gate evidence to test a guard is the thing the guard exists to stop".

    This asks the same question exhaustively instead. Every name a helper reads
    must be its own parameter, something bound inside it (at any depth — a
    comprehension variable is read in the scope that binds it), or a
    module-level name. Nothing left over means no path through it can raise
    `NameError`, whether or not any test walks that path.

    ruff's F821 is the primary net for this and is the one that actually fired:
    the first generated call site passed a comprehension variable as a
    parameter and F821 said so before any test ran. This assertion is the part
    F821 cannot express — it is scoped to the helpers this round created, so a
    later edit that reintroduces a free name in one of them is attributed to
    the extraction rather than to whoever touches the file next.
    """
    import builtins

    source = (REPO / module).read_text(encoding="utf-8")
    tree = ast.parse(source)
    module_scope = _bound(list(tree.body)) | set(dir(builtins))

    def bound_at_any_depth(nodes: "list[ast.stmt]") -> "set[str]":
        """Unlike `_bound`, descends into nested scopes: for the NameError
        question a comprehension variable IS resolved where it is read."""
        names: set[str] = set()
        for node in nodes:
            for child in ast.walk(node):
                if isinstance(child, ast.Name) and isinstance(
                    child.ctx, (ast.Store, ast.Del)
                ):
                    names.add(child.id)
                elif isinstance(child, ast.arg):
                    names.add(child.arg)
                elif isinstance(child, (ast.Import, ast.ImportFrom)):
                    for alias in child.names:
                        names.add((alias.asname or alias.name).split(".")[0])
                elif isinstance(child, ast.ExceptHandler) and child.name:
                    names.add(child.name)
                elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef,
                                        ast.ClassDef)):
                    names.add(child.name)
        return names

    unresolved: "list[str]" = []
    for name in _EXTRACTED[module][1]:
        func = _function(tree, name)
        params = {a.arg for a in func.args.args + func.args.kwonlyargs}
        loose = _loaded(func.body) - bound_at_any_depth(func.body) - params - module_scope
        if loose:
            unresolved.append(f"{name}: {sorted(loose)}")

    assert not unresolved, (
        "these extracted helpers read names that are neither their parameters, "
        "nor bound inside them, nor module-level — every one is a NameError "
        "waiting for the right input:\n  " + "\n  ".join(unresolved)
    )


@pytest.mark.parametrize("module", sorted(_EXTRACTED))
def test_the_caller_still_propagates_every_early_return(module):
    """A helper that can return a code, called without checking it, is a
    silently disabled check — the shape Round 43 named (detected, no executor).
    """
    source = (REPO / module).read_text(encoding="utf-8")
    tree = ast.parse(source)
    caller = _function(tree, _EXTRACTED[module][0])
    helpers = set(_EXTRACTED[module][1])

    can_return = {
        name for name in helpers
        if any(isinstance(n, ast.Return) and n.value is not None
               for n in ast.walk(_function(tree, name)))
    }

    checked: "set[str]" = set()
    body = caller.body
    for index, stmt in enumerate(body):
        if not (isinstance(stmt, ast.Assign) and isinstance(stmt.value, ast.Call)
                and isinstance(stmt.value.func, ast.Name)
                and stmt.value.func.id in helpers):
            continue
        target = stmt.targets[0]
        following = body[index + 1] if index + 1 < len(body) else None
        if (isinstance(target, ast.Name) and isinstance(following, ast.If)
                and any(isinstance(n, ast.Return) for n in ast.walk(following))):
            checked.add(stmt.value.func.id)

    assert can_return <= checked, (
        f"these helpers can return an exit code that "
        f"{_EXTRACTED[module][0]} never looks at, so the check they perform "
        f"cannot block anything: {sorted(can_return - checked)}"
    )
