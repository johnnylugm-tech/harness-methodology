"""Round 105 站1a — the framework wrote a second, wrong copy of a grammar.

`.importlinter` is import-linter's file and import-linter defines what a line
in it means. `arch_constraints.read_import_contracts` split `layers = ` on
newlines and stopped, so five things import-linter 2.5.2 gives a meaning to
were read as literal characters in a module name:

    contracts/layers.py:19  _INDEPENDENT_LAYER_DELIMITER      "|"
    contracts/layers.py:20  _NON_INDEPENDENT_LAYER_DELIMITER  ":"
    contracts/layers.py:48  (module) — an optional layer
    contracts/layers.py     containers — the prefix of every layer tail
    domain/fields.py        ModuleExpressionField — `pkg.*` wildcards

This is not hypothetical and it is not cheap. taskq-sn wrote its bottom layer
as `taskq_api.config | taskq_api.exceptions` — legal, and exactly what the
contract means. Replayed byte-for-byte from `git show f97e5be^:.importlinter`:

    OLD sources  -> [..., 'taskq_api.config | taskq_api.exceptions']
    covered?     -> {'taskq_api.config': False, 'taskq_api.exceptions': False}

`contract_coverage_gap` reported two delivered modules outside every contract
and Gate 1 blocked. The project then changed its ARCHITECTURE to fit the
framework's parser — `f97e5be`, "split config/exceptions into separate
import-linter layers" — and the two are no longer siblings but ordered layers.
Its commit message diagnoses the framework correctly. That is Round 42's rule
being paid by the wrong party.

The fix is not a second delimiter in this repository; it is that the grammar
has one owner. `layer_module_tails` asks import-linter. When import-linter is
not importable the newline split is still exactly right for a line with none
of those characters in it — which is all thirteen corpus configs today — and
for a line that does have them the answer is "this framework cannot read
this", never a module name nobody wrote.

Abstention is the whole point, so it is tested in both directions: an
unreadable contract must not put its modules in the gap (that is the false
accusation this round removes), and it must not make the check silently
report a clean bill either — `contract_coverage_gap` abstains for the whole
file and `record_constraint_status` files the ledger row.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = [pytest.mark.core]


def _project(tmp_path: Path, importlinter: str) -> Path:
    """A delivered tree with four modules and the given contract file."""
    root = tmp_path / "proj"
    pkg = root / "taskq_api"
    pkg.mkdir(parents=True)
    for name in ("app", "config", "exceptions", "service"):
        (pkg / f"{name}.py").write_text("", encoding="utf-8")
    (root / ".importlinter").write_text(importlinter, encoding="utf-8")
    return root


_HEADER = "[importlinter]\nroot_package = taskq_api\n\n"


def _layers(body: str, extra: str = "") -> str:
    return (
        _HEADER
        + "[importlinter:contract:layers]\n"
        + "name = layered\ntype = layers\n"
        + extra
        + "layers =\n"
        + body
    )


# ── the shapes import-linter defines and this framework did not read ────────

def test_pipe_separated_siblings_are_two_sources(tmp_path) -> None:
    """taskq-sn's exact line, replayed. RED before this round."""
    from core.quality_gate.arch_constraints import contract_coverage_gap

    project = _project(tmp_path, _layers(
        "    taskq_api.app\n"
        "    taskq_api.service\n"
        "    taskq_api.config | taskq_api.exceptions\n"))
    gap = contract_coverage_gap(project)
    assert "taskq_api.config" not in gap and "taskq_api.exceptions" not in gap, (
        "`a | b` names two independent sibling layers (import-linter "
        f"contracts/layers.py:19); both were reported outside every contract: {gap}")


def test_colon_separated_siblings_are_two_sources(tmp_path) -> None:
    """The other delimiter, which the corpus has never used and which the
    newline split gets wrong in exactly the same way."""
    from core.quality_gate.arch_constraints import contract_coverage_gap

    project = _project(tmp_path, _layers(
        "    taskq_api.app\n"
        "    taskq_api.service\n"
        "    taskq_api.config : taskq_api.exceptions\n"))
    gap = contract_coverage_gap(project)
    assert "taskq_api.config" not in gap and "taskq_api.exceptions" not in gap, gap


def test_an_optional_layer_is_still_a_named_module(tmp_path) -> None:
    """`(mypackage.foo)` is a layer that need not exist, not a module whose
    name begins with a bracket."""
    from core.quality_gate.arch_constraints import contract_coverage_gap

    project = _project(tmp_path, _layers(
        "    taskq_api.app\n"
        "    taskq_api.service\n"
        "    (taskq_api.config)\n"))
    assert "taskq_api.config" not in contract_coverage_gap(project)


def test_containers_prefix_every_layer(tmp_path) -> None:
    """With `containers`, a layer line is a TAIL. Reading it as a full module
    name leaves every delivered module uncovered — the same false accusation
    as the pipe, over the whole tree instead of two modules."""
    from core.quality_gate.arch_constraints import contract_coverage_gap

    project = _project(tmp_path, _layers(
        "    app\n    service\n    config\n    exceptions\n",
        extra="containers =\n    taskq_api\n"))
    gap = contract_coverage_gap(project)
    for module in ("taskq_api.app", "taskq_api.service",
                   "taskq_api.config", "taskq_api.exceptions"):
        assert module not in gap, (
            f"{module} is `containers` + a layer tail and was reported "
            f"outside every contract: {gap}")


# ── what the framework may not decide ───────────────────────────────────────

def test_a_wildcard_source_is_abstained_on_not_accused(tmp_path) -> None:
    """`taskq_api.*` is a module EXPRESSION. This framework matches sources by
    prefix, which cannot decide an expression — so the honest answer is that
    the gap is unknown, never that every module is outside the contract."""
    from core.quality_gate.arch_constraints import contract_coverage_gap

    project = _project(tmp_path, _layers(
        "    taskq_api.app\n    taskq_api.service.*\n"))
    assert contract_coverage_gap(project) == [], (
        "a contract this framework cannot decide was turned into a list of "
        "modules to charge the project with")


def test_an_unreadable_layer_line_abstains_for_the_whole_file(
        tmp_path, monkeypatch) -> None:
    """Counter-proof CP-2's shape, as a test.

    `covered` is a union over contracts. Dropping one contract from the union
    makes its modules look uncovered, so an abstention that is not file-wide
    is the false accusation wearing a different hat.
    """
    import core.quality_gate.arch_constraints as ac

    monkeypatch.setattr(ac, "layer_module_tails", lambda _line: None)
    project = _project(tmp_path, _layers(
        "    taskq_api.app\n"
        "    taskq_api.config | taskq_api.exceptions\n"))
    assert ac.contract_coverage_gap(project) == [], (
        "with the grammar unreadable, the gap must be unknown — not the set "
        "of modules the unreadable contract would have covered")


def test_an_unreadable_contract_says_so_in_the_ledger(tmp_path, monkeypatch) -> None:
    """Round 30/32: abstaining silently is how a check stops working without
    anybody finding out. The row names the framework as the owner, because
    the parser is the framework's and so is its absence."""
    import core.quality_gate.arch_constraints as ac

    monkeypatch.setattr(ac, "layer_module_tails", lambda _line: None)
    project = _project(tmp_path, _layers(
        "    taskq_api.app\n"
        "    taskq_api.config | taskq_api.exceptions\n"))
    (project / ".methodology").mkdir(exist_ok=True)

    ac.record_constraint_status(project, {"architecture_constraints": []})

    ledger = project / ".methodology" / "degradations.jsonl"
    assert ledger.exists(), "no degradation ledger was written at all"
    text = ledger.read_text(encoding="utf-8")
    assert "import-linter" in text and "harness" in text, (
        "the abstention did not name what could not be read or whose "
        f"debt it is:\n{text}")


# ── the seam this round rests on ────────────────────────────────────────────

def test_the_grammar_comes_from_import_linter_not_from_here() -> None:
    """Behaviour pin on `LayerField`, which is not a documented public API.

    If an import-linter upgrade moves or changes it, this fails loudly here
    rather than silently turning every sibling layer back into one opaque
    module name. `layer_module_tails` returning None is the honest failure and
    is tested above; returning the WRONG tails is what this refuses.
    """
    from core.quality_gate.arch_constraints import layer_module_tails

    assert layer_module_tails("a | b") == ["a", "b"]
    assert layer_module_tails("a : b") == ["a", "b"]
    assert layer_module_tails("(a)") == ["a"]
    assert layer_module_tails("plain.mod") == ["plain.mod"]


def test_a_plain_line_needs_no_parser(tmp_path, monkeypatch) -> None:
    """Reverse control, and the reason this round adds no hard dependency.

    A line with none of import-linter's grammar characters in it is one module
    name under any parser, so the newline split is provably right for it — all
    thirteen corpus configs are in that state today. Abstaining there would
    switch the check off for every project to fix a case none of them has.
    """
    import core.quality_gate.arch_constraints as ac

    monkeypatch.setattr(ac, "layer_module_tails", lambda _line: None)
    project = _project(tmp_path, _layers(
        "    taskq_api.app\n    taskq_api.service\n"))
    gap = ac.contract_coverage_gap(project)
    assert "taskq_api.config" in gap and "taskq_api.exceptions" in gap, (
        "the two modules no layer names must still be reported: "
        f"{gap}")
