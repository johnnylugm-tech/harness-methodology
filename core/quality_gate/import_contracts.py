"""What the project's own tool configs say — read, never guessed.

Round 106 站C, moved out of `core/quality_gate/arch_constraints.py` when that
file reached 950 lines. One module was answering two questions: *what does the
project's tool configuration say* (here) and *who enforces a declared
constraint, and what is not covered* (there). This half is a leaf — nothing in
it reads anything else the other file defines — which is why it is the half
that moves.

The functions are byte-identical to their pre-move text;
`tests/test_god_file_split_safety.py` fingerprinted all six BEFORE the move
(Round 106 站C-網) and those fingerprints are the proof that this was a move
and not a rewrite. `arch_constraints` re-exports every name, so no existing
import changes.

The grammar these readers honour belongs to import-linter, not to this
framework — `layer_module_tails` delegates to `importlinter.contracts.layers`
and returns None rather than guessing when it cannot ask. Round 105 站1a has
the measurement: a project rewrote its own architecture declaration to fit a
parser this repository had invented.
"""

from __future__ import annotations

import configparser
import logging
from pathlib import Path

__all__ = [
    "contract_decides",
    "layer_module_tails",
    "read_bandit_config",
    "read_import_contracts",
]


def _config_sources(project: Path) -> list[Path]:
    """The two files import-linter reads its contracts from, in its own order."""
    return [project / ".importlinter", project / "setup.cfg"]


#: Characters import-linter's own field grammar gives a meaning to inside a
#: `layers` line: `|` separates independent sibling layers, `:` non-independent
#: ones, and `(m)` marks a layer that need not exist. A line containing none of
#: them names one module under any parser — which is why the newline split
#: below is still exactly right without import-linter installed, and is the
#: state of all thirteen corpus configs today.
_LAYER_GRAMMAR: frozenset[str] = frozenset("|:()")

#: A module EXPRESSION rather than a module name. `sources` are matched in
#: `contract_coverage_gap` by dotted prefix, which cannot decide `pkg.*`, so a
#: contract using one is abstained on rather than read as naming a module
#: literally called "pkg.*".
_MODULE_WILDCARD = "*"


def layer_module_tails(line: str) -> "list[str] | None":
    """The module tails one `layers = ` line names, per import-linter's grammar.

    `None` means this framework could not read the line — never an empty list
    and never a guess.

    Round 105. `.importlinter` is import-linter's file and import-linter
    defines what a line in it means; this module split on newlines and stopped,
    so `taskq_api.config | taskq_api.exceptions` — one layer holding two
    independent siblings — was read as one module name nobody wrote. taskq-sn
    hit it, `contract_coverage_gap` reported both modules outside every
    contract, Gate 1 blocked, and the project changed its ARCHITECTURE to fit
    the parser (`f97e5be`, splitting two siblings into two ordered layers).
    Its commit message diagnoses this framework correctly.

    The grammar has grown before — `:` and the optional bracket are not in
    every version — so asking the tool is the only arrangement in which a
    future delimiter does not silently become part of a module name.

    Public because it is a seam:
    `tests/test_the_contract_grammar_belongs_to_import_linter.py` replaces it
    to exercise the abstention path, and `tests/test_patch_discipline.py` is
    right that the answer to "I need to replace this to test it" is a public
    seam rather than a patched private name.

    import-linter is a dependency this framework already declares and installs
    — `harness/toolchains/bootstrap.PINS["import-linter"]` via
    `PIP_STEPS["gate-extras"]`, which `scripts/bootstrap_env.py` runs against
    the framework's own venv as well as a project's. It is deliberately NOT in
    requirements.txt: that file resolves as one unit and the gate-extras step
    exists precisely because the combined resolve is impossible
    (`PIP_STEPS[1].why`). So it CAN be absent here, and absence answers None.
    """
    _log = logging.getLogger(__name__)
    try:
        from importlinter.contracts.layers import LayerField
    except Exception as exc:
        _log.debug("import-linter not importable, cannot read layer %r: %s",
                   line, exc)
        return None
    try:
        return sorted(t.name for t in LayerField().parse(line).module_tails)
    except Exception as exc:
        # A line import-linter itself rejects. `lint-imports` will refuse the
        # config too, so this is the project's error to see there — what must
        # not happen is this framework inventing a module name out of it.
        _log.debug("import-linter refused layer %r: %s", line, exc)
        return None


def _contract_sources(
    parser: "configparser.ConfigParser", section: str,
) -> "tuple[list[str] | None, str]":
    """`(module names this contract constrains, why they could not be read)`.

    Exactly one of the two is meaningful: sources is None when the reason is
    non-empty. An empty list would read as "this contract constrains nothing",
    which both callers turn into modules to charge the project with — the
    false accusation this parse exists to avoid (Round 46).
    """
    def _lines(key: str) -> list[str]:
        return [ln.strip() for ln
                in parser.get(section, key, fallback="").splitlines()
                if ln.strip()]

    containers = _lines("containers")
    layer_lines = _lines("layers")
    # `layers` names its modules under `layers`; `forbidden` and `independence`
    # name theirs under `source_modules` / `modules`. `forbidden_modules` is
    # the target of the ban, not a module the contract constrains, so it is not
    # among `sources` — it is read only to answer `decides` below.
    flat = _lines("source_modules") + _lines("modules")

    if any(_MODULE_WILDCARD in s for s in (*containers, *layer_lines, *flat)):
        return None, (
            "the contract names a module expression (`pkg.*`); this framework "
            "matches contract sources by dotted prefix and cannot decide one")

    tails: list[str] = []
    for line in layer_lines:
        if _LAYER_GRAMMAR.isdisjoint(line):
            tails.append(line)
            continue
        parsed = layer_module_tails(line)
        if parsed is None:
            return None, (
                "the contract uses import-linter's sibling/optional layer "
                "grammar and import-linter is not importable here, so the "
                "modules it names cannot be read")
        tails.extend(parsed)

    # `containers` makes every layer a TAIL: the module is the container plus
    # it. Reading a tail as a full module name leaves every delivered module
    # outside every contract — the same defect as the pipe, over the whole
    # tree instead of two modules.
    if containers:
        tails = [f"{c}.{t}" for c in containers for t in tails]
    return tails + flat, ""


def contract_decides(kind: str, sources: list, targets: list) -> bool:
    """Can this contract produce a violation, whatever the code does?

    Round 99 站3. The rule and its wording are Round 55's, moved here from
    inside `classify_constraints` so that both readers of this parse get the
    same answer: "a `layers` contract IS a statement about ordering, and one
    element has none. Two layers is not a threshold." Its two siblings say
    the same thing about a different relation — `independence` is a
    statement about pairs, `forbidden` about targets — and a statement with
    no second term cannot be false.

    `contract_coverage_gap` never asked the question at all, which is why
    naming the bare root package in a one-module `independence` stanza took
    a project's gap to zero: taskq-new's 13 uncovered modules (every
    `migrations/*`, `security.redact`) are "covered" by
    `modules = taskq`, taskq-redo's root and `__main__` likewise.

    Deliberately not asked: whether a `forbidden` contract's named target
    is reachable. taskq-wow's `forbidden_modules =
    nonexistent_module_for_coverage` cannot fail, and it is structurally
    identical to a deliberate "this project must never import django" guard
    — import-linter accepts both without complaint when
    `include_external_packages = True`. Judging it would be a false
    accusation of the other (Round 46), so it is written down in
    docs/PROPOSAL_ADJUDICATIONS.md rather than decided.

    An unrecognised contract type answers True: this framework does not
    have standing to call a contract it does not understand vacuous.

    Public because it is a seam, not because anything outside this module
    calls it. Counter-proof CP-13b showed the "one statement, two consumers"
    claim was unpinned — a faithful second implementation inside
    `contract_coverage_gap` passed every test — and the two tests that now
    pin it replace this definition and assert both consumers move. Round 55
    put the same judgement inline; `tests/test_patch_discipline.py` is
    right that the answer to "I need to replace this to test it" is a public
    seam rather than a patched private name.
    """
    if kind == "layers":
        return len(sources) >= 2
    if kind == "independence":
        return len(sources) >= 2
    if kind == "forbidden":
        return len(targets) >= 1
    return True


def read_import_contracts(project: "str | Path") -> dict:
    """Parse the project's import-linter configuration.

    Returns ``{"root_package": str, "contracts": [{"name", "type", "sources"}]}``
    with ``contracts`` empty when there is no configuration — an absent
    contract file is a project with no layering enforcement, which is a
    different fact from a project whose contracts leave a module out, and both
    callers below need to tell them apart.

    Round 55: import-linter accepts ``root_package`` (one) and ``root_packages``
    (a newline list); this read only the singular. taskq-plus and taskq-super
    both write the plural, so both came back with an empty root package — and
    ``contract_coverage_gap``'s first guard is `if not root_package … return []`,
    so the check that asks which delivered modules no contract constrains
    returned "none" for the two projects with the emptiest contracts. With the
    plural read, taskq-super goes from 0 to 20 uncovered modules (every module
    it delivers) and taskq-plus from 0 to 5; the three projects that spell it in
    the singular are unchanged. ``root_package`` stays singular in the return —
    the first entry is the one every caller here means, and a multi-package
    project is not in this corpus.
    """
    project = Path(project)
    root_package = ""
    contracts: list[dict] = []

    for path in _config_sources(project):
        if not path.is_file():
            continue
        parser = configparser.ConfigParser()
        try:
            parser.read(path, encoding="utf-8")
        except (configparser.Error, OSError, UnicodeDecodeError):
            continue
        if not parser.has_section("importlinter"):
            continue
        _roots = "\n".join(
            parser.get("importlinter", key, fallback="")
            for key in ("root_package", "root_packages")
        )
        root_package = next(
            (ln.strip() for ln in _roots.splitlines() if ln.strip()), "")
        for section in parser.sections():
            if not section.startswith("importlinter:contract:"):
                continue
            kind = parser.get(section, "type", fallback="").strip()
            sources, unreadable = _contract_sources(parser, section)
            targets = [
                ln.strip() for ln in
                parser.get(section, "forbidden_modules", fallback="").splitlines()
                if ln.strip()
            ]
            contracts.append({
                "name": parser.get(section, "name", fallback=section).strip(),
                "type": kind,
                "sources": sources,
                "unreadable": unreadable,
                # A contract this framework could not read is not one it may
                # call vacuous. `decides=False` is the claim "this contract
                # cannot produce a violation whatever the code does", and that
                # is a judgement about a statement nobody here parsed — the
                # same reason an unrecognised contract type answers True.
                "decides": (True if sources is None
                            else contract_decides(kind, sources, targets)),
            })
        break

    return {"root_package": root_package, "contracts": contracts}


def read_bandit_config(project: "str | Path") -> dict:
    """The project's bandit `skips` / `tests` lists.

    Returns ``{"skips": frozenset, "tests": frozenset, "configured": bool}``.
    `configured` is False when the project has no `[bandit]` section anywhere,
    which for bandit means **every test is enabled** — the opposite of
    import-linter, where no config means nothing is checked. Station 0 measured
    six of the seven projects here in that state.

    Values are written as an ini list (`skips = B101,B307`) and sometimes with
    the brackets of a TOML list left in (`skips = []`), so both are stripped.
    """
    project = Path(project)
    for path, section in ((project / ".bandit", "bandit"),
                          (project / "setup.cfg", "bandit")):
        if not path.is_file():
            continue
        parser = configparser.ConfigParser()
        try:
            parser.read(path, encoding="utf-8")
        except (configparser.Error, OSError, UnicodeDecodeError):
            continue
        if not parser.has_section(section):
            continue

        def _ids(key: str) -> frozenset:
            raw = parser.get(section, key, fallback="")  # noqa: B023
            return frozenset(
                tok for tok in
                (t.strip().strip("[]'\" ") for t in raw.replace("\n", ",").split(","))
                if tok
            )

        return {"skips": _ids("skips"), "tests": _ids("tests"),
                "configured": True}
    return {"skips": frozenset(), "tests": frozenset(), "configured": False}
