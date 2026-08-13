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
from pathlib import Path

__all__ = [
    "CONSTRAINT_EXECUTOR_CANDIDATES",
    "STATUS_DECLARED_ONLY",
    "STATUS_ENFORCED",
    "classify_constraints",
    "contract_coverage_gap",
    "read_import_contracts",
    "record_constraint_status",
]

STATUS_ENFORCED = "enforced"
STATUS_DECLARED_ONLY = "declared_only"

# What a constraint is about -> which tool could decide it, and what has to be
# present in that tool's config before the claim is made. Keyed on substrings
# because the strings are the project's own words: taskq-api wrote
# `sqlalchemy_only_in_repository`, taskq-advance wrote
# `sqlalchemy_imports_only_in_repository_layer`, and the framework must read
# both without either project having been told a vocabulary.
#
# Adding an entry is a claim that the named tool decides that constraint. It is
# not enough for the tool to run — `contract_kind` names the shape its config
# must have, and `classify_constraints` looks for it before saying `enforced`.
CONSTRAINT_EXECUTOR_CANDIDATES: tuple[dict, ...] = (
    {
        "about": "no import cycles between layers",
        "keywords": ("circular", "cycle", "layering", "layered"),
        "executor": "import-linter",
        "contract_kind": "layers",
    },
    {
        "about": "a package may only be imported from one layer",
        "keywords": ("only_in", "imports_only", "isolation", "restricted_to"),
        "executor": "import-linter",
        "contract_kind": "forbidden",
    },
)


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
        root_package = parser.get("importlinter", "root_package", fallback="").strip()
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


def classify_constraints(
    constraints: "list[str] | tuple[str, ...]",
    project: "str | Path | None" = None,
) -> list[dict]:
    """One row per declared constraint, in declaration order.

    Each row is ``{"constraint", "status", "executor", "evidence"}``.
    ``executor`` and ``evidence`` are empty for a `declared_only` row — there
    is nothing to name.

    With no *project* nothing can be `enforced`: the claim depends on the
    project's own contract file, and a classification made without reading it
    would be the same guess this module exists to remove.
    """
    contracts = read_import_contracts(project)["contracts"] if project else []
    kinds_present = {c["type"]: c["name"] for c in contracts if c["type"]}

    rows: list[dict] = []
    for constraint in constraints:
        lowered = str(constraint).lower()
        row = {
            "constraint": str(constraint),
            "status": STATUS_DECLARED_ONLY,
            "executor": "",
            "evidence": "",
        }
        for candidate in CONSTRAINT_EXECUTOR_CANDIDATES:
            if not any(k in lowered for k in candidate["keywords"]):
                continue
            contract_name = kinds_present.get(candidate["contract_kind"])
            if contract_name:
                row["status"] = STATUS_ENFORCED
                row["executor"] = candidate["executor"]
                row["evidence"] = (
                    f"{candidate['executor']} contract {contract_name!r} "
                    f"(type {candidate['contract_kind']})"
                )
            break
        rows.append(row)
    return rows


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
        unenforced = [r["constraint"] for r in rows
                      if r["status"] == STATUS_DECLARED_ONLY]
        if unenforced:
            record_degradation(
                project, "gate:arch-constraints",
                f"{len(unenforced)} of {len(rows)} declared architecture "
                f"constraints have no executor",
                "the SAB list reaches CLAUDE.md and the gate prompt; no "
                "deterministic check reads it, so a report may not certify "
                "these as honoured",
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
