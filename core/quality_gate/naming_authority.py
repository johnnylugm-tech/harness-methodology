"""What TEST_INVENTORY.yaml declared at the P1 exit, and what it declares now.

Round 111 站F1. `cli/advance_prechecks.py` writes
`state.json.test_inventory_checksum` — a sha256 of TEST_INVENTORY.yaml taken
the moment Phase 1 completes — and until this module existed nothing read it
back. A baseline with no comparator is a measurement that was taken and
thrown away (Round 43).

The digest exists to protect `spec_coverage`'s P1 Naming Authority check,
which blocks at 0.0 when a name in TEST_INVENTORY.yaml is missing from
TEST_SPEC.md. That check reads the working-tree file, so a declaration that
shrinks passes it: 15 of the 21 corpus projects carry a digest that no longer
matches their file, and taskq-new declared 100 names at P1 and 50 today.

This module reports the movement; it does not judge it. Checked what
taskq-new's 50 "retracted" names are: eight of them are
`test_fr10_ac5_status_mapping_422_401_403_404_409_429_503_500` split into the
eight rows TEST_SPEC.md actually carries, two more are
`..._done_failed_timeout` split in half. Blocking on shrinkage would charge
the one project that refined its declaration (Round 46), so the record goes
to the degradation ledger and the verdict stays where it was (Round 103).

`None` rather than `set()` when the frozen declaration cannot be read: an
empty baseline would report every name in the file as newly added, which is
an unmade measurement published as a made one (Round 35).
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

from core.quality_gate.legal_artifacts import TEMPLATE_EXAMPLE_VALUES
from core.utils.subprocess_group import run_isolated

INVENTORY_RELPATH = "TEST_INVENTORY.yaml"

#: The names this framework invented and copied into the project's file. A
#: project that replaced them did what templates/TEST_INVENTORY.yaml told it
#: to (Round 105), and reporting that as a retraction would make the two
#: corpus projects that never edited the template the loudest of all.
_EXAMPLE_NAMES = frozenset(TEMPLATE_EXAMPLE_VALUES[f"templates/{INVENTORY_RELPATH}"])


def _names_in(text: str) -> set[str]:
    """The declared test names in one TEST_INVENTORY.yaml blob, examples removed.

    Falls back to `spec_coverage`'s YAML-free parser on a malformed blob as
    well as on a missing PyYAML: the frozen copy comes out of a commit that
    may predate the shape the project settled on, and a parse failure here
    must degrade to fewer names rather than take down the caller. It says so
    when it happens — a blob nobody could parse produces an empty set, and an
    empty frozen set reads as "everything was retracted".
    """
    from core.quality_gate.spec_coverage import (
        _flatten_test_names,
        _parse_inventory_fallback,
    )
    try:
        import yaml
    except ImportError:
        inventory = _parse_inventory_fallback(text)
    else:
        try:
            inventory = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            print(f"[WARN] naming_authority: {INVENTORY_RELPATH} did not parse "
                  f"as YAML ({exc}); reading it as a flat list", file=sys.stderr)
            inventory = _parse_inventory_fallback(text)
    if not isinstance(inventory, dict):
        inventory = _parse_inventory_fallback(text)
    return _flatten_test_names(inventory) - _EXAMPLE_NAMES


def current_declarations(project: "str | Path") -> "set[str] | None":
    """What TEST_INVENTORY.yaml declares in the working tree, or None if absent."""
    path = Path(project) / INVENTORY_RELPATH
    if not path.exists():
        return None
    try:
        return _names_in(path.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return None


def frozen_declarations(project: "str | Path") -> "tuple[set[str] | None, str]":
    """What TEST_INVENTORY.yaml declared when Phase 1 completed.

    Returns `(names, why)`. `names` is None when the P1 declaration cannot be
    read, and `why` then says which of the three ways it was unreachable —
    the file is gone, no commit was recorded for Phase 1, or git could not
    produce that commit's copy. Never raises.
    """
    root = Path(project)
    path = root / INVENTORY_RELPATH
    if not path.exists():
        return None, f"{INVENTORY_RELPATH} is not in the project"

    from core.state_io import load_state
    try:
        state = load_state(root, lenient=True)
    except Exception:  # noqa: BLE001 — a naming report must not be able to block
        return None, "state.json could not be read"

    # Fast path, and the reader `test_inventory_checksum` never had: a digest
    # that still matches the file says the P1 declaration IS the current one,
    # with no git and no subprocess. It is also the only path available to a
    # project that is not a git repository.
    try:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        return None, f"{INVENTORY_RELPATH} could not be read ({exc})"
    if state.get("test_inventory_checksum") == digest:
        return current_declarations(root), (
            "state.json.test_inventory_checksum still matches the file on disk"
        )

    entry = (state.get("phase_completed") or {}).get("1")
    sha = entry.get("sha") if isinstance(entry, dict) else None
    if not sha:
        return None, (
            "the digest no longer matches the file and state.json records no "
            "phase_completed[1].sha to read the P1 copy from"
        )

    try:
        shown = run_isolated(
            ["git", "show", f"{sha}:{INVENTORY_RELPATH}"],
            timeout=30, cwd=str(root),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return None, f"git show {sha[:8]}:{INVENTORY_RELPATH} could not run ({exc})"
    if shown.returncode != 0:
        return None, (
            f"git show {sha[:8]}:{INVENTORY_RELPATH} failed: "
            f"{(shown.stderr or '').strip()[:200]}"
        )
    return _names_in(shown.stdout), f"frozen at phase_completed[1].sha={sha[:8]}"


def declaration_movement(project: "str | Path") -> "dict | None":
    """What the declaration RETRACTED since P1, or None if nothing left it.

    `{"retracted": [...], "added": [...], "frozen_at": <the `why` above>}`.

    A name that was ADDED after P1 is already under judgement: the live check
    reads the working-tree file, so every name in it is compared against
    TEST_SPEC.md on every run. The one thing that file cannot show is a name
    that is no longer in it, which is the whole reason a frozen copy is worth
    reading. So the trigger is retraction, and `added` travels along as
    context for whoever reads the row.

    Measured 2026-09-09 over the 21 corpus projects: 11 have moved, 10 of
    them purely by addition (P2 derives test cases and the inventory grows —
    the normal path), and one, taskq-new, retracts 50. Triggering on movement
    in either direction would file a row on ten projects doing nothing wrong.
    """
    frozen, why = frozen_declarations(project)
    if frozen is None:
        return None
    current = current_declarations(project)
    if current is None:
        return None
    retracted = sorted(frozen - current)
    if not retracted:
        return None
    return {"retracted": retracted, "added": sorted(current - frozen),
            "frozen_at": why}


def movement_report(moved: dict, limit: int = 10) -> str:
    """One sentence per direction, naming the names. Both call sites read this."""
    lines = [
        f"[spec-coverage] P1 naming authority moved since it was frozen: "
        f"{len(moved['retracted'])} retracted, {len(moved['added'])} added "
        f"({moved['frozen_at']}). Reported, not blocking — a declaration split "
        f"into the tests it meant is refinement."
    ]
    for label in ("retracted", "added"):
        names = moved[label]
        for name in names[:limit]:
            lines.append(f"  [{label}] {name}")
        if len(names) > limit:
            lines.append(f"  [{label}] … +{len(names) - limit} more")
    return "\n".join(lines)
