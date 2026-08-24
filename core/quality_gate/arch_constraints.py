"""Who, if anyone, enforces a declared architecture constraint (Round 51 站2).

The SAB carries `architecture_constraints`, a list of strings the P2 agent
writes. Traced end to end, the field reaches exactly two readers, and both are
documents an agent will later read:

  * `core/claude_md.py:105` renders it into the project's own CLAUDE.md, and
  * `harness/harness_bridge.py:1908` renders it into the gate evaluation
    prompt under "When evaluating the `architecture` dimension, validate code
    against these constraints."

Nothing deterministic reads a constraint. The gate *dimension* named
`architecture_constraints` is a different mechanism that happens to share the
name — it runs `import-linter` (`harness/toolchains/registry.py:535`) over a
contract the project itself wrote — which is why the list has looked enforced
for as long as it has existed.

taskq-api's `05-verification/VERIFICATION_REPORT.md` §3 certifies all five of
its constraints "honored at HEAD". Two of them are not:
`sqlalchemy_only_in_repository` while `app.py:39` does
`from sqlalchemy import create_engine`, and
`single_auth_dependency_at_api_layer` while `/v1/metrics` is mounted with no
auth dependency at all. Both statements were written by the agent that read
the list out of CLAUDE.md.

Round 43's rule applies: when a check has no executor, the fix is not to
invent one, it is to write down that there is none — so a report cannot claim
the constraint was kept.

`enforced` here is a narrow claim with evidence attached: a tool the framework
runs has a contract whose configuration encodes this constraint, and the row
names it. Everything else is `declared_only`, including constraints that are
perfectly real and simply have no checker (`fr07_round_trip_must_preserve_data`
is a true requirement of the system; nothing in this repository can decide it).
Constraint strings are project-invented — the two trees built from the same
SPEC.md share one string out of twelve — so the registry matches on what a
constraint is *about*, and the project's own config decides whether it is
actually checked.
"""

from __future__ import annotations

import configparser
import logging
from pathlib import Path

__all__ = [
    "CONSTRAINT_EXECUTOR_CANDIDATES",
    "STATUS_DECLARED_ONLY",
    "STATUS_ENFORCED",
    "STATUS_UNCONFIGURED",
    "classify_constraints",
    "contract_coverage_blocking_reason",
    "contract_coverage_gap",
    "read_bandit_config",
    "read_import_contracts",
    "record_constraint_status",
    "unconfigured_blocking_reason",
]

STATUS_ENFORCED = "enforced"
STATUS_UNCONFIGURED = "unconfigured"
STATUS_DECLARED_ONLY = "declared_only"

# Round 54. Three states, because two were saying three different things:
#
#   enforced       a tool the framework runs decides this, and the evidence
#                  names the tool and what in its config makes the claim true
#   unconfigured   such a tool exists and runs, and THIS project has not
#                  enabled it — the only state that can be blocked, because it
#                  is the only one where the framework can say what to do
#   declared_only  nothing in this framework can decide it; recorded forever,
#                  never blocked. Blocking it would make projects delete true
#                  statements about themselves rather than write checks.
#
# What a constraint is about -> which tool could decide it, and what has to be
# present in that tool's config before the claim is made. Keyed on substrings
# because the strings are the project's own words: taskq-api wrote
# `sqlalchemy_only_in_repository`, taskq-advance wrote
# `sqlalchemy_imports_only_in_repository_layer`, and the framework must read
# both without either project having been told a vocabulary.
#
# Adding an entry is a claim that the named tool decides that constraint, and
# `requires` says what has to be true of the project's config before the claim
# is made. The two executors ask that question with opposite polarity, which is
# why it is a per-executor predicate rather than one lookup:
#
#   import-linter  checks only what the project declared, so an absent
#                  contract of the right kind means nothing is checked
#   bandit         runs every test unless the project opts out, so an absent
#                  config means everything is checked
#
# Round 73 站3 — an import-linter candidate must list the contract type its
# own `requires` names, and there is a guard on it
# (tests/test_constraint_keywords_speak_the_executor_vocabulary.py). These
# lists were induced from the strings this corpus happened to use, and the
# comment above already says why that cannot hold: the strings are
# project-invented. import-linter has three contract types — `layers`,
# `forbidden`, `independence` — and only the third was ever a keyword, while
# SPEC §4 NFR-06 in this corpus says "forbidden contract" and "layers
# contract" in exactly those words.
#
# What that cost, measured on one real constraint declared by five projects
# ("sqlalchemy may only be imported from the repository layer"):
#
#   taskq-api      sqlalchemy_only_in_repository                  enforced
#   taskq-advance  sqlalchemy_imports_only_in_repository_layer    enforced
#   taskq-super    sqlalchemy_only_in_repository                  unconfigured
#   taskq-cc       sqlalchemy imports allowed only in repository  declared_only
#   taskq-new      sqlalchemy import forbidden outside repository declared_only
#
# The last two are wrong in opposite directions — taskq-cc HAS the contract,
# taskq-new has none and shipped Gate 4 PASS at 94.59 with the ban unenforced.
# Round 54's own comment cites taskq-super's spelling as the case
# `unconfigured` exists for; taskq-new wrote the same constraint in different
# words and was never asked.
#
# Singular `layer` is deliberately absent, and that is the harder half.
# taskq-api declares `single_auth_dependency_at_api_layer`, which no
# import-linter contract can decide; with `layer` listed it would match this
# candidate, and taskq-api HAS a layers contract — so the row would read
# `enforced`. Trading an abstention for a false endorsement is worse than the
# abstention (Round 72 站4 settled the same trade the other way round).
#
# `limits` is not decoration. Station 0 measured bandit on a fixture holding
# both the direct and the indirect form of each violation: it flags
# `subprocess(cmd, shell=True)` as B602 but reads `subprocess(cmd, **opts)` as
# B603, and it flags `eval(x)` but not `fn = eval; fn(x)`. `enforced` inherits
# the executor's reach and the evidence string has to say so — the same way
# `contract_coverage_gap` already reports separately that an import-linter
# contract can be kept while leaving modules unconstrained.
CONSTRAINT_EXECUTOR_CANDIDATES: tuple[dict, ...] = (
    {
        "about": "no import cycles between layers",
        "keywords": ("circular", "cycle", "layering", "layered", "layers"),
        "executor": "import-linter",
        "requires": ("contract", "layers"),
        "limits": "only the modules the contract names",
        "remedy": "add an [importlinter:contract:…] section with `type = layers` "
                  "listing this project's layers, top to bottom",
    },
    {
        "about": "a package may only be imported from one layer",
        "keywords": ("only_in", "only in", "imports_only", "isolation",
                     "restricted_to", "forbidden"),
        "executor": "import-linter",
        "requires": ("contract", "forbidden"),
        "limits": "only the modules the contract names",
        "remedy": "add an [importlinter:contract:…] section with "
                  "`type = forbidden`, `source_modules` the layers that may "
                  "not import it and `forbidden_modules` the package itself",
    },
    {
        "about": "two modules may not import each other",
        "keywords": ("independence", "independent"),
        "executor": "import-linter",
        "requires": ("contract", "independence"),
        "limits": "only the modules the contract names",
        "remedy": "add an [importlinter:contract:…] section with "
                  "`type = independence` listing the modules under `modules`",
    },
    {
        "about": "no shell invocation",
        "keywords": ("shell_true", "shell=true", "no_shell"),
        "executor": "bandit",
        "requires": ("bandit_tests", ("B602", "B604", "B605", "B609")),
        "limits": "syntactic — a shell=True passed through **kwargs reads as "
                  "B603 and is not flagged",
        "remedy": "remove these ids from `skips` (or from `tests`) in .bandit "
                  "or setup.cfg [bandit]",
    },
    {
        "about": "no eval or exec",
        "keywords": ("eval", "exec"),
        "executor": "bandit",
        "requires": ("bandit_tests", ("B307", "B102")),
        "limits": "syntactic — an aliased `fn = eval; fn(x)` is not flagged",
        "remedy": "remove these ids from `skips` (or from `tests`) in .bandit "
                  "or setup.cfg [bandit]",
    },
    {
        "about": "no string-built SQL",
        "keywords": ("sql_concat", "string_sql", "sql_string", "concatenat"),
        "executor": "bandit",
        "requires": ("bandit_tests", ("B608",)),
        "limits": "syntactic — catches f-string and `+` construction, "
                  "including inside a helper, but not SQL assembled across "
                  "several statements",
        "remedy": "remove B608 from `skips` (or from `tests`) in .bandit or "
                  "setup.cfg [bandit]",
    },
)

# Which gate dimension routes to each executor. Read through the registry's own
# per-language map rather than assuming: `security` is bandit for Python and
# semgrep-js for JS/TS, whose rule ids are a different vocabulary entirely, so
# a bandit candidate must not speak for a JS project.
_EXECUTOR_DIMENSION: dict[str, str] = {
    "import-linter": "architecture_constraints",
    "bandit": "security",
}


def _config_sources(project: Path) -> list[Path]:
    """The two files import-linter reads its contracts from, in its own order."""
    return [project / ".importlinter", project / "setup.cfg"]


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
            # `layers` names its modules under `layers`; `forbidden` and
            # `independence` name theirs under `source_modules` /
            # `modules`. `forbidden_modules` is the target of the ban, not a
            # module the contract constrains, so it is deliberately not read.
            raw = "\n".join(
                parser.get(section, key, fallback="")
                for key in ("layers", "source_modules", "modules")
            )
            sources = [ln.strip() for ln in raw.splitlines() if ln.strip()]
            contracts.append({
                "name": parser.get(section, "name", fallback=section).strip(),
                "type": kind,
                "sources": sources,
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


def _project_executor_tool(project: "str | Path | None", dimension: str) -> str:
    """Which tool this project's language routes *dimension* to."""
    from harness.toolchains.registry import DIMENSION_TOOLS

    language = "python"
    if project is not None:
        try:
            from core.state_io import load_state
            language = str(load_state(project, lenient=True).get("language")
                           or "python")
        except Exception:  # pylint: disable=broad-exception-caught
            logging.getLogger(__name__).debug(
                "arch-constraints: state.json unreadable for %s", project,
                exc_info=True,
            )
    tool = DIMENSION_TOOLS.get(language, {}).get(dimension, "")
    return tool if isinstance(tool, str) else ""


def _evaluate(candidate: dict, project: "str | Path | None") -> "tuple[str, str]":
    """`(status, evidence)` for a constraint that matched *candidate*.

    Never `declared_only`: reaching here means a tool that decides this kind of
    constraint exists. The only question left is whether this project has it
    switched on.
    """
    kind, want = candidate["requires"]
    limits = candidate["limits"]

    if kind == "contract":
        contracts = read_import_contracts(project)["contracts"] if project else []
        name = next((c["name"] for c in contracts if c["type"] == want), "")
        if not name:
            return STATUS_UNCONFIGURED, (
                f"{candidate['executor']} decides this, and this project has "
                f"no contract of type {want!r}"
            )
        # Round 55: a contract of the right kind is not automatically a
        # contract that decides anything. `type = layers` with a single layer
        # named satisfies the question above and expresses no order at all, so
        # `unconfigured` — the state Round 54 introduced to stop a project
        # switching its checker off — could be cleared by switching it back on
        # over an empty domain. Two layers is not a threshold: a `layers`
        # contract IS a statement about ordering, and one element has none.
        sources: list = next(
            (c["sources"] for c in contracts if c["type"] == want and c["name"] == name),
            [])
        if want == "layers" and len(sources) < 2:
            return STATUS_UNCONFIGURED, (
                f"{candidate['executor']} contract {name!r} names "
                f"{len(sources)} layer(s); a `layers` contract states an "
                f"order, and an order needs at least two"
            )
        # Which delivered modules no contract reaches is reported, never
        # decided on. `contract_coverage_gap`'s own docstring calls it "the
        # contract's shape and not the violation", and measured over the
        # corpus every correctly-layered project still leaves four modules
        # out (the composition root, `__main__`, config, errors) — a diagnostic
        # with that distribution is not a verdict (Round 32 站4).
        gap = contract_coverage_gap(project) if project else []
        note = ""
        if gap:
            shown = ", ".join(gap[:5]) + ("…" if len(gap) > 5 else "")
            note = (f" — {len(gap)} delivered module(s) sit outside every "
                    f"contract and are not decided by it: {shown}")
        return STATUS_ENFORCED, (
            f"{candidate['executor']} contract {name!r} (type {want}) — "
            f"{limits}{note}"
        )

    cfg = read_bandit_config(project) if project else {
        "skips": frozenset(), "tests": frozenset(), "configured": False}
    disabled = [t for t in want if t in cfg["skips"]]
    if cfg["tests"]:
        disabled += [t for t in want if t not in cfg["tests"] and t not in disabled]
    if disabled:
        # Partial disablement counts. A constraint reading "no shell, no eval,
        # no exec" is not enforced by a bandit told to ignore eval.
        return STATUS_UNCONFIGURED, (
            f"{candidate['executor']} decides this via {','.join(want)}, and "
            f"this project has disabled {','.join(sorted(disabled))}"
        )
    return STATUS_ENFORCED, (
        f"{candidate['executor']} tests {','.join(want)}"
        + ("" if cfg["configured"] else " (enabled by default; no [bandit] config)")
        + f" — {limits}"
    )


def classify_constraints(
    constraints: "list[str] | tuple[str, ...]",
    project: "str | Path | None" = None,
) -> list[dict]:
    """One row per declared constraint, in declaration order.

    Each row is ``{"constraint", "status", "executor", "evidence", "remedy"}``.
    `executor`, `evidence` and `remedy` are empty for a `declared_only` row —
    there is nothing to name and nothing to do.

    With no *project* nothing can be `enforced`: every claim depends on the
    project's own config, and a classification made without reading it would be
    the same guess this module exists to remove.

    A candidate whose executor is not the tool this project's language routes
    to falls through to `declared_only`. A JS project declaring `no_shell_true`
    is a real constraint that this framework cannot decide — semgrep-js has a
    different rule vocabulary — and saying so is the honest answer.
    """
    rows: list[dict] = []
    for constraint in constraints:
        lowered = str(constraint).lower()
        row = {
            "constraint": str(constraint),
            "status": STATUS_DECLARED_ONLY,
            "executor": "",
            "evidence": "",
            "remedy": "",
        }
        # ALL matching candidates, not the first. A constraint is one string
        # and may name several things: `no_shell_true_no_eval_no_exec` names
        # three, and matching only the first would give it the shell test ids
        # and silently drop eval and exec — so a project that skipped B307
        # would still read `enforced`. A compound constraint is enforced only
        # when every part of it is.
        #
        # With no *project* there is nothing to evaluate against, and the
        # honest answer is the one this function gave before Round 54: every
        # row is `declared_only`. Both of the other two states are claims about
        # a specific project's config — `enforced` that it enabled the tool,
        # `unconfigured` that it did not — and bandit's "absent config means
        # everything is on" would otherwise turn a project nobody looked at
        # into a project certified as covered.
        matched = [] if project is None else [
            c for c in CONSTRAINT_EXECUTOR_CANDIDATES
            if any(k in lowered for k in c["keywords"])
            and _project_executor_tool(
                project, _EXECUTOR_DIMENSION[c["executor"]]) == c["executor"]
        ]
        verdicts = [(c, *_evaluate(c, project)) for c in matched]
        if verdicts:
            unconfigured = [v for v in verdicts if v[1] == STATUS_UNCONFIGURED]
            decided = unconfigured or verdicts
            row["status"] = (STATUS_UNCONFIGURED if unconfigured
                             else STATUS_ENFORCED)
            row["executor"] = ", ".join(
                sorted({c["executor"] for c, _, _ in verdicts}))
            row["evidence"] = "; ".join(ev for _, _, ev in verdicts)
            if unconfigured:
                row["remedy"] = "; ".join(
                    sorted({c["remedy"] for c, _, _ in decided}))
        rows.append(row)
    return rows


def unconfigured_blocking_reason(rows: "list[dict]") -> "str | None":
    """Why the gate stops, or None.

    Only `unconfigured` rows appear. `declared_only` is deliberately absent:
    the only way a project could satisfy a block on a constraint nothing can
    decide is to delete the declaration, which would make the SAB less true
    rather than the code better (Round 51 站2's finding, kept).
    """
    stuck = [r for r in rows if r.get("status") == STATUS_UNCONFIGURED]
    if not stuck:
        return None
    lines = [
        f"  {r['constraint']}\n"
        f"    {r['evidence']}\n"
        f"    fix: {r['remedy']}"
        for r in stuck
    ]
    return (
        f"{len(stuck)} declared architecture constraint(s) name something this "
        f"framework already runs a tool for, and this project has not "
        f"configured that tool to decide them:\n" + "\n".join(lines)
    )


def contract_coverage_blocking_reason(project: "str | Path") -> "str | None":
    """Why the gate stops over an import-linter contract's shape, or None.

    Round 67 站7. `contract_coverage_gap` has computed this since Round 46 and
    its docstring ends "The caller decides what a missing contract means for
    its gate" — there was one caller and it wrote a degradation row.
    taskq-cc's ledger carries 130 of them, every one naming
    `["taskq_api", "taskq_api.__main__", "taskq_api.cli"]`, while
    `lint-imports` reported the contract kept for the whole run and the
    architecture dimension scored 88.9.

    This is not Round 54's `declared_only` reopened. That adjudication stands:
    a constraint nothing in this framework can decide is recorded and never
    blocked, because the only way to satisfy such a block is to delete a true
    declaration. This is the opposite situation — the contract exists, the
    tool runs, and the framework has already computed exactly which delivered
    modules it does not reach. Nothing is being guessed.

    None when the project ships no import-linter configuration at all: a
    project with no layering contract is not a project with a leaky one, and
    `contract_coverage_gap` returns [] there by design (Round 46's rule that
    an absent witness is absent, not failing).
    """
    gap = contract_coverage_gap(project)
    if not gap:
        return None
    return (
        f"{len(gap)} delivered module(s) are outside every import-linter "
        f"contract, so `lint-imports` reports the contract kept no matter "
        f"what they import:\n"
        + "\n".join(f"  {m}" for m in gap)
        + "\n    fix: add these to an existing contract's source modules (or "
          "name a package above them), or write a contract that covers them. "
          "Do NOT delete the contracts to clear this — a project with no "
          "contract is not blocked here, but it also stops claiming a "
          "boundary it is not keeping."
    )


def _delivered_modules(project: Path, root_package: str) -> list[str]:
    """Dotted names of every delivered module inside *root_package*.

    Reads the delivery boundary rather than walking the tree directly, so a
    file the project does not ship cannot be reported as an uncovered module
    (Round 37 站1's SSOT).

    `root_package` is whatever the project wrote, and the two trees built from
    the same SPEC.md wrote it two ways: taskq-api's is `taskq_api`, and
    taskq-advance's is `03-development.src.taskq_api` — the source root spelled
    as dots. Matching only the last segment would name advance's modules
    `taskq_api.app` while its contracts say
    `03-development.src.taskq_api.api`, and every module would look uncovered.
    Matching the whole sequence against the path answers in the project's own
    spelling, which is the one its contracts are written in. (Round 30 站2 is
    the same defect one layer down: a module path returned without its source
    root resolves to a directory that does not exist.)
    """
    from core.utils.delivery_scope import iter_delivered_files

    root_parts = [p for p in root_package.split(".") if p]
    if not root_parts:
        return []
    width = len(root_parts)

    modules: set[str] = set()
    for path in iter_delivered_files(project):
        if path.suffix != ".py":
            continue
        parts = list(path.parts)
        idx = next(
            (i for i in range(len(parts) - width + 1)
             if parts[i:i + width] == root_parts),
            None,
        )
        if idx is None:
            continue
        tail = parts[idx:]
        tail[-1] = tail[-1][: -len(".py")]
        if tail[-1] == "__init__":
            tail.pop()
        if tail:
            modules.add(".".join(tail))
    return sorted(modules)


def contract_coverage_gap(project: "str | Path") -> list[str]:
    """Delivered modules of the root package that no contract constrains.

    Both taskq trees name `taskq_api.api` and `taskq_api.service` as the
    forbidden-import sources and list four layers. `taskq_api.app` is in
    neither list and is not a submodule of anything in them, so `lint-imports`
    reports the contract kept while the composition root imports SQLAlchemy
    directly. taskq-advance has the identical hole and did not walk through
    it, which is why this reports the contract's shape and not the violation:
    by the time there is a violation the contract has already stopped being
    the thing that would have caught it.

    Returns [] when there is no import-linter configuration at all — a project
    with no layering contract is not a project with a leaky one, and Round 46's
    rule is that an absent witness is reported as absent, not as a pass. The
    caller decides what a missing contract means for its gate.
    """
    project = Path(project)
    parsed = read_import_contracts(project)
    root_package = parsed["root_package"]
    if not root_package or not parsed["contracts"]:
        return []

    covered: set[str] = set()
    for contract in parsed["contracts"]:
        covered.update(contract["sources"])

    gap = []
    for module in _delivered_modules(project, root_package):
        if any(module == c or module.startswith(c + ".") for c in covered):
            continue
        gap.append(module)
    return gap


def record_constraint_status(
    project: "str | Path", sab_data: "dict | None" = None,
) -> list[dict]:
    """Classify the SAB's constraints and write what has no executor to the ledger.

    Returns the classification rows so a caller can put them in a gate
    artifact; the ledger row is the part that outlives the run. Two rows at
    most per call — one naming the constraints nothing checks, one naming the
    delivered modules no import contract constrains — because the point is
    that the fact is on record, not that it is on record four times per phase.

    Never raises. A constraint list that cannot be read is a worse reason to
    stop a gate than the thing it was going to report.
    """
    from core.degradation_ledger import record_degradation

    project = Path(project)
    try:
        constraints = list((sab_data or {}).get("architecture_constraints") or [])
        rows = classify_constraints(constraints, project)
        # Round 54: two rows, because they are two different facts and only one
        # of them is the project's to fix. Before this the pair was one row
        # reading "no executor", which was true of seven of the 23 constraints
        # measured across the projects here and false of the other sixteen.
        unconfigured = [r["constraint"] for r in rows
                        if r["status"] == STATUS_UNCONFIGURED]
        if unconfigured:
            record_degradation(
                project, "gate:arch-constraints",
                f"{len(unconfigured)} of {len(rows)} declared architecture "
                f"constraints name a tool the framework runs, which this "
                f"project has not configured to decide them",
                "the executor exists and is not switched on for these; the "
                "gate blocks on this and the block names the config to write",
                data={"unconfigured": unconfigured},
                owner="project",
            )
        unenforced = [r["constraint"] for r in rows
                      if r["status"] == STATUS_DECLARED_ONLY]
        if unenforced:
            record_degradation(
                project, "gate:arch-constraints",
                f"{len(unenforced)} of {len(rows)} declared architecture "
                f"constraints have no executor in this framework",
                "the SAB list reaches CLAUDE.md and the gate prompt; nothing "
                "deterministic can decide these, so a report may not certify "
                "them as honoured. Recorded, never blocked — the only way to "
                "satisfy a block here would be to delete a true statement",
                data={"declared_only": unenforced},
                owner="project",
            )
        gap = contract_coverage_gap(project)
        if gap:
            record_degradation(
                project, "gate:arch-constraints",
                f"{len(gap)} delivered module(s) are outside every "
                f"import-linter contract",
                "lint-imports can report the contract kept while these "
                "modules import anything they like",
                data={"uncovered_modules": gap},
                owner="project",
            )
        return rows
    except Exception as exc:  # pragma: no cover — reporting must not stop a gate
        record_degradation(
            project, "gate:arch-constraints",
            "constraint classification failed",
            f"{type(exc).__name__}: {exc}",
            owner="harness",
        )
        return []
