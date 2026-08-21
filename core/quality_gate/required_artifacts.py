"""Round 68 站1 — the files a project declares it must ship, in the delivered tree.

Every existing check in this framework reads an artifact against another
artifact. None opens the tree and asks whether a path a requirement names is
there. Measured on taskq-cc, which published Gate 4 PASS at 95.28:

    SPEC §8 #26   `grep -c "^TASKQ_" .env.example` = 12
                  → the file does not exist anywhere in the tree
    SPEC §6       `migrations/` and `alembic.ini` at the project root
                  → both ship under `03-development/src/`
    SAD.md:45     "Source directories … matches SPEC.md §6 exactly"
                  → false, and read by nothing

The SRS even wrote the failure mode down (§2.9, on the mandatory config
files): "their absence silently turns the linked dimensions into free
points". A requirement that states its own failure mode and has no executor is
Round 43's shape; this module is the executor.

Three deliberate limits, each with its reason:

*The list is the project's.* `required_artifacts` is a SAB key, so a project
that declares nothing is not caught by anything here. That is Round 57's
mother defect and it is not solved — what is solved is a declaration that is
made and never checked. The alternative was measured and rejected: scraping
backticked paths out of SRS.md and SAD.md gives 68 candidates on taskq-cc of
which 46 do not resolve (`app.py`, `auth.py`, bare leaf names), so a guard
built on it would report 46 false positives on a project with two real ones.

*Declaring nothing is recorded, not free.* `record_required_artifacts` leaves
a ledger row when the key is absent or empty, for the reason Round 50 站4 kept
`unknown` as a fault owner: an empty list and a satisfied list must not look
the same afterwards.

*"Somewhere else" is its own answer.* A path that resolves under the source
root rather than at the declared location is reported with where it actually
is. Blocking on it and blocking on absence are the same rule — the
declaration is false either way — but only one of the two messages can be
acted on in a single edit, so the finding carries the location.
"""

from __future__ import annotations

import json
from pathlib import Path

__all__ = [
    "STATUS_ELSEWHERE",
    "STATUS_MISSING",
    "declared_artifact_findings",
    "record_required_artifacts",
    "required_artifacts_blocking_reason",
]

STATUS_MISSING = "missing"
STATUS_ELSEWHERE = "elsewhere"


def _declared(project: Path, sab: "dict | None") -> list[str]:
    """The declared list, from *sab* or from the project's SAB.json.

    Read from SAB.json rather than from `GateContext.sab_data` for the reason
    `boundary_realism._high_risk_modules` reads it: `_load_manifest_sab` is a
    hand-listed copy of eight keys out of the manifest, and a ninth key added
    to that list is the defect class Round 67 站1 spent a station on. The SAB
    on disk is the source; a copy of it is not.
    """
    if sab is not None:
        return [str(p).strip() for p in (sab.get("required_artifacts") or [])]
    path = project / ".methodology" / "SAB.json"
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(data, dict):
        return []
    return [str(p).strip() for p in (data.get("required_artifacts") or [])]


# Directories a delivered file never legitimately lives in; searching them
# turns a build cache into evidence of delivery.
_NOT_DELIVERY = {"__pycache__", ".venv", "node_modules", ".git"}


def _delivered_locations(project: Path) -> "list[Path]":
    """The trees a deliverable can honestly be in, nearest the root first.

    The canonical layout puts delivered code under `03-development/src`, and
    the coverage scope every gate measures is that directory — so a file the
    spec draws at the root and the tree carries under the source root is the
    common shape rather than an exotic one. taskq-cc's `alembic.ini` is one
    level deeper again (`03-development/src/migrations/alembic.ini`), so a
    fixed list of roots reports it absent and sends the project to write a
    file it already has. The search is by the declared path's own last
    segment, bounded to the development tree and to directories a delivery
    can be in.
    """
    from core.utils.project_layout import ProjectLayout

    return [ProjectLayout(project).phase3_development_dir]


def _find_elsewhere(project: Path, rel: str) -> str:
    """Project-relative location of *rel*'s last segment, or ""."""
    wanted = Path(rel).name
    hits: list[Path] = []
    for base in _delivered_locations(project):
        if not base.is_dir():
            continue
        for found in base.rglob(wanted):
            if any(part in _NOT_DELIVERY or part.startswith(".")
                   for part in found.relative_to(base).parts[:-1]):
                continue
            hits.append(found)
    if not hits:
        return ""
    # Nearest the project root: the deliverable, not a copy nested under it.
    nearest = min(hits, key=lambda p: (len(p.parts), str(p)))
    return str(nearest.relative_to(project))


def declared_artifact_findings(
    project: "str | Path", sab: "dict | None" = None,
) -> list[dict]:
    """Every declared path that is not at the path it was declared at.

    One row per finding: `{declared, status, found_at}`. `found_at` is the
    project-relative location for `elsewhere` and `""` for `missing`. A path
    delivered where it was declared produces no row.

    Pure — no ledger, no raising. Same split as
    `arch_constraints.classify_constraints` / `record_constraint_status`.
    """
    project = Path(project)

    findings: list[dict] = []
    for entry in _declared(project, sab):
        if not entry:
            continue
        rel = entry.rstrip("/")
        if (project / rel).exists():
            continue
        found_at = _find_elsewhere(project, rel)
        findings.append({
            "declared": entry,
            "status": STATUS_ELSEWHERE if found_at else STATUS_MISSING,
            "found_at": found_at,
        })
    return findings


def record_required_artifacts(
    project: "str | Path", sab: "dict | None" = None,
) -> list[dict]:
    """Classify the declaration and write to the ledger what the gate will
    not block on.

    Returns the findings so the caller can decide. Two ledger rows at most —
    one for a declaration nobody made, one for the findings — because the
    point is that the fact is on record, not that it is on record once per
    declared path.

    Never raises: a declaration that cannot be read is a worse reason to stop
    a gate than the thing it was going to report.
    """
    from core.degradation_ledger import record_degradation

    project = Path(project)
    try:
        declared = _declared(project, sab)
        if not declared:
            record_degradation(
                project, "gate:required-artifacts",
                "the SAB declares no required_artifacts",
                "nothing states which files this project must ship, so no "
                "check can tell a deliverable that was never written from one "
                "that was. Declare the spec's mandatory config files under "
                "`required_artifacts` in the SAB block of SAD.md",
                data={"declared": 0}, owner="project",
            )
            return []

        findings = declared_artifact_findings(project, sab)
        if findings:
            record_degradation(
                project, "gate:required-artifacts",
                f"{len(findings)} of {len(declared)} declared deliverable(s) "
                f"are absent or shipped somewhere other than the declared path",
                "the gate blocks on this and the block names each path and, "
                "where the file exists elsewhere, where it actually is",
                data={"findings": findings}, owner="project",
            )
        return findings
    except OSError:
        return []


def required_artifacts_blocking_reason(findings: "list[dict]") -> "str | None":
    """Why the gate stops over the declared deliverables, or None.

    Both statuses block. A declared path that is absent and a declared path
    that ships elsewhere are the same defect stated twice: the declaration is
    not true of the tree. Unlike `arch_constraints`' `declared_only`, there is
    no version of this a project cannot satisfy — moving the file or correcting
    the declaration are both one edit, and both make the SAB more true.
    """
    if not findings:
        return None
    lines = []
    for f in findings:
        if f.get("status") == STATUS_ELSEWHERE:
            lines.append(
                f"  {f['declared']}\n"
                f"    declared here, delivered at {f['found_at']}\n"
                f"    fix: move it, or correct the SAB's required_artifacts entry"
            )
        else:
            lines.append(
                f"  {f['declared']}\n"
                f"    not in the delivered tree\n"
                f"    fix: deliver it, or drop the entry if it is not required"
            )
    return (
        f"{len(findings)} declared deliverable(s) are not at the path the SAB "
        f"declares:\n" + "\n".join(lines)
    )
