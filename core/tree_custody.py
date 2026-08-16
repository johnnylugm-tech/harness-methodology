"""What the framework writes into the tree it judges, and who closes the books (Round 53 站1).

Every guard in this repository is of one shape — read the judged project's
tree or its artifacts, and decide. None was of the other shape: *the framework
just wrote into that tree; was the write accounted for?*

The write that matters most is mutation testing, because it edits the delivered
source on purpose. `core/quality_gate/source_tree_lock.py` says so in its own
docstring: mutmut "mutates ``paths_to_mutate`` files in place at their real
project path", and `cwd=workdir` "never isolated the mutated files themselves".
What shipped with that finding was a **concurrency lock** — it stops another
reader from observing a mutant, and says nothing about a mutation window that
never closes.

Round 53 站0 reproduced the open window. Kill `mutmut run` mid-flight and the
tree keeps both halves:

    probeapp/calc.py        `return a * 2`  ->  `return a / 2`
    probeapp/calc.py.bak    the original, written by mutmut

taskq-super shipped exactly that. `5535033`, whose message is
`release(P6): Gate4 PASS score=93.9 — pipeline complete`, contains
`"sqlite:///:memory:"` rewritten to `"XXsqlite:///:memory:XX"` — mutmut's
string-mutation signature — together with `rate_repo.py.bak`.

Round 44 站1 and Round 38 站4 already refuse to advance on a tree the gate did
not measure. They did not catch it because the reference point is taken *after*
the framework has written: the digest matched a tree that was already corrupt.
The invariant was right and anchored downstream of the framework's own hand.
Closing the window before anything measures is what puts the anchor back.

**Why a registry and not one fix in `mutation_enforcer`.** Station 0's
inventory found mutation is not the only framework write into a judged
project — `harness/ssot_manifest.py` scaffolds `requirements.txt` (taskq-super
has two `gate:env-repair` rows saying so), and Round 52 站2 installs a `.pth`
into the project venv. Those are legitimate and differently-shaped, and the way
this codebase keeps such a set honest is a declared registry with a
completeness meta-test (`core/pre_flight.py`'s checks,
`core/quality_gate/block_reason.py`'s details, `tests/REGRESSION_GUARDS.yaml`).
A write path that is not in `FRAMEWORK_WRITES` is a write nobody declared.

The restore itself reuses `core.atomic_io.FileSnapshot` (Round 49 B0) rather
than hand-rolling a third copy of write→revert. The **verification** does not
reuse it: the bytes are re-read from disk and compared against digests this
module captured independently, because a restorer that reports its own success
is the failure mode this whole area exists to remove.
"""

from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Generator, Iterable

__all__ = [
    "CUSTODY_RELPATH",
    "FRAMEWORK_WRITES",
    "KIND_DELIVERABLE",
    "KIND_TRANSIENT",
    "TreeCustodyResidue",
    "WriteSpec",
    "assert_no_open_custody",
    "custody",
    "open_custody_ids",
]

KIND_TRANSIENT = "transient"
KIND_DELIVERABLE = "deliverable"

# `.methodology/`, not `.sessi-work/`: advance-phase clears the work directory
# at every transition, and the one moment this record has to survive is a crash
# that left a window open — after which the next command is quite likely to be
# an advance (Round 50 站6, same reasoning as gate evidence).
CUSTODY_RELPATH = ".methodology/tree_custody.json"


class TreeCustodyResidue(RuntimeError):
    """The framework could not put the judged tree back the way it found it.

    Raised, not logged. Round 13 站0 routes any exception reaching the crash
    boundary to `EX_HARNESS_BUG` (70) with a `[HARNESS-BUG]` banner and a crash
    bundle, which is the correct owner: a project cannot fix a tree the
    framework broke, and letting the run continue is how a mutant reaches a
    release commit.
    """


@dataclass(frozen=True)
class WriteSpec:
    """One declared framework write into a judged project."""

    kind: str
    owner: str
    what: str


FRAMEWORK_WRITES: dict[str, WriteSpec] = {
    "mutation:src": WriteSpec(
        kind=KIND_TRANSIENT,
        owner="harness",
        what="mutmut rewrites each file under [mutmut] paths_to_mutate in "
             "place and leaves a <file>.bak beside it; both are the "
             "framework's and neither may outlive the run",
    ),
    "reach:pth": WriteSpec(
        kind=KIND_TRANSIENT,
        owner="harness",
        what="Round 52 站2 installs a .pth (and its hook module) into the "
             "project venv's site-packages for the duration of one "
             "`make verify-system`, so subprocess coverage can be collected",
    ),
    "ssot:manifest": WriteSpec(
        kind=KIND_DELIVERABLE,
        owner="harness",
        what="harness/ssot_manifest.py scaffolds requirements.txt from the "
             "SSOT documents when the project has none; the file is meant to "
             "be committed, and the ledger row names its inputs",
    ),
}


def _digest(path: Path) -> "str | None":
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except (OSError, IsADirectoryError):
        return None


def _custody_path(project: Path) -> Path:
    return project / CUSTODY_RELPATH


def _read(project: Path) -> dict:
    path = _custody_path(project)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write(project: Path, data: dict) -> None:
    path = _custody_path(project)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8")


def _mark_open(project: Path, write_id: str, paths: Iterable[Path]) -> None:
    data = _read(project)
    data[write_id] = {"open": True,
                      "paths": sorted(str(Path(p)) for p in paths)}
    _write(project, data)


def _mark_closed(project: Path, write_id: str) -> None:
    data = _read(project)
    data.pop(write_id, None)
    _write(project, data)


def open_custody_ids(project: "str | Path") -> list[str]:
    """Write ids whose window is still open, sorted."""
    return sorted(k for k, v in _read(Path(project)).items()
                  if isinstance(v, dict) and v.get("open"))


def assert_no_open_custody(project: "str | Path") -> "str | None":
    """Why the framework may not commit right now, or None.

    Consulted by the commit sites that stage the whole worktree. Station 0's
    premise P3 measured seven commit sites in this repository; five pass an
    explicit pathspec (`git commit -- <file>`) and so cannot pick up a stray
    mutant, and two commit the index. Only those two ask this.
    """
    project = Path(project)
    data = _read(project)
    stuck = []
    for write_id in open_custody_ids(project):
        paths = (data.get(write_id) or {}).get("paths") or []
        spec = FRAMEWORK_WRITES.get(write_id)
        what = spec.what if spec else "an undeclared framework write"
        stuck.append(f"  {write_id}: {what}\n"
                     + "".join(f"    {p}\n" for p in paths))
    if not stuck:
        return None
    return (
        "a framework write into this project's tree is still open, so the "
        "worktree may contain the framework's own scratch rather than the "
        "project's work:\n" + "".join(stuck)
        + f"  Inspect those paths, restore them, and delete {CUSTODY_RELPATH}."
    )


@contextmanager
def custody(
    project: "str | Path", write_id: str, *, paths: Iterable[Path],
) -> Generator[None]:
    """Hold the framework to putting *paths* back exactly as it found them.

    On the way in: record which paths are at risk, so a process that dies
    inside the window leaves a statement of what it was holding. On the way
    out: restore, then **re-read the bytes and compare** — a restore that
    reports success is not evidence, which is the whole lesson of the mutant
    that reached `release(P6)`.

    Only `KIND_TRANSIENT` writes belong here. A `KIND_DELIVERABLE` write is
    meant to survive; putting it under custody would delete the thing it
    exists to produce.
    """
    project = Path(project)
    spec = FRAMEWORK_WRITES.get(write_id)
    if spec is None:
        raise TreeCustodyResidue(
            f"{write_id!r} is not declared in FRAMEWORK_WRITES — a framework "
            f"write into a judged project has to be declared before it is made"
        )
    if spec.kind != KIND_TRANSIENT:
        raise TreeCustodyResidue(
            f"{write_id!r} is declared {spec.kind!r}; custody restores its "
            f"paths and would delete a deliverable the write exists to create"
        )

    from core.atomic_io import FileSnapshot

    watched = [Path(p) for p in paths]
    before = {p: _digest(p) for p in watched}
    snapshot = FileSnapshot(watched)

    _mark_open(project, write_id, watched)
    try:
        yield
    finally:
        restore_error: "Exception | None" = None
        try:
            snapshot.restore()
        except Exception as exc:  # pylint: disable=broad-exception-caught
            restore_error = exc

        residue = [str(p) for p in watched if _digest(p) != before[p]]
        if residue or restore_error is not None:
            detail = "".join(f"  {p}\n" for p in residue)
            cause = (f"  restore raised {type(restore_error).__name__}: "
                     f"{restore_error}\n" if restore_error is not None else "")
            raise TreeCustodyResidue(
                f"{write_id}: the framework changed the judged tree and could "
                f"not put it back. {spec.what}.\n"
                f"{detail}{cause}"
                f"  These bytes are the framework's, not the project's — do "
                f"not commit them. The open window is recorded at "
                f"{CUSTODY_RELPATH}."
            )
        _mark_closed(project, write_id)
