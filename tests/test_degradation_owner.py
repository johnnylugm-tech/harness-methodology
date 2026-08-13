"""Every degradation says whose tree has to change (Round 50 站4).

Round 48 站1 made fault ownership a first-class field and gave it one reader:
`classify_fault`, which recovers the owner after the fact from the text of a
halt. Round 48's own Self-Review committed the next round to measuring that
reader against real messages, and set the consequence in advance:

    if it cannot manage most of them, the classifier needs a NEW EVIDENCE
    SOURCE, not more rules — and that changes the shape, so it must be
    decided before implementation, not patched afterwards.

Measured 2026-08-13 on the first project to run P1-P8 end to end under that
harness: nine real halt messages, nine UNKNOWNs.

The measurement also said where to put the fix. SIX of the nine were not halt
messages — they were rows `record_degradation` wrote:

    P5 exit blocked by 01-requirements/TRACEABILITY_MATRIX.md
    P4 entry blocked by FR-01: declares a property invariant but no test
    'pytest-benchmark' produced no score the harness could read
    SAB scope_layers resolve to non-existent director(ies) [...]
    FR-01 TDD-GREEN: TURN_BUDGET
    incremental graph covered 6 of 41 delivered source file(s)

Every one of those call sites knew the answer. `milestone:uncommitted` is the
project's tree by construction; `gate:s4:*` is the framework failing to
measure; `agent:*` timing out is neither tree. The information was discarded
at the write and guessed at the read — the exact shape Round 48's own
docstring records for `_abort_dispatch_infra_or_harness_bug`, which receives
the class, prints it, and returns one exit code for both.

This file is the completeness guard. It does not require a site to be RIGHT —
`unknown` is a legitimate answer and Round 48's rule that it is never rounded
down to PROJECT still holds. It requires the site to have DECIDED.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

# Where production degradation sites live. tests/ is excluded: a test that
# records a degradation is exercising the ledger, not diagnosing a run.
_SCANNED = ("core", "cli", "harness", "scripts")


def _sites() -> list[tuple[str, int, bool]]:
    """(location, lineno, has_explicit_owner) for every production call."""
    found: list[tuple[str, int, bool]] = []
    for package in _SCANNED:
        for path in sorted((REPO / package).rglob("*.py")):
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except SyntaxError:  # pragma: no cover - another test's job
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                if getattr(node.func, "id", None) != "record_degradation":
                    continue
                explicit = any(kw.arg == "owner" for kw in node.keywords)
                found.append((
                    f"{path.relative_to(REPO).as_posix()}:{node.lineno}",
                    node.lineno, explicit,
                ))
    return found


def test_every_degradation_site_states_an_owner():
    sites = _sites()
    assert sites, "no degradation sites found — the scan lost its target"
    silent = [loc for loc, _, explicit in sites if not explicit]
    assert not silent, (
        f"{len(silent)} of {len(sites)} degradation site(s) leave the owner to "
        "the default. A row that does not say whose tree has to change cannot "
        "be routed, and the reader downstream can only guess:\n  "
        + "\n  ".join(silent)
    )


def test_the_recorded_owner_is_from_the_shared_vocabulary():
    """One set of words for ownership, not one per writer."""
    from core.fault_owner import ALL_OWNERS

    for package in _SCANNED:
        for path in sorted((REPO / package).rglob("*.py")):
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except SyntaxError:  # pragma: no cover
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                if getattr(node.func, "id", None) != "record_degradation":
                    continue
                for kw in node.keywords:
                    if kw.arg != "owner":
                        continue
                    # `Owner.HARNESS` (an attribute of the vocabulary class)
                    # or a bare string that must be one of its values.
                    if isinstance(kw.value, ast.Attribute):
                        assert kw.value.attr in {
                            "HARNESS", "PROJECT", "INFRA", "UNKNOWN", "NONE",
                        }, f"{path}:{node.lineno}: unknown Owner.{kw.value.attr}"
                    elif isinstance(kw.value, ast.Constant):
                        assert kw.value.value in ALL_OWNERS, (
                            f"{path}:{node.lineno}: owner={kw.value.value!r} is "
                            f"not one of {sorted(ALL_OWNERS)}"
                        )


def test_the_ledger_records_what_the_site_said(tmp_path):
    import json

    from core.degradation_ledger import record_degradation
    from core.fault_owner import Owner

    record_degradation(tmp_path, "gate:s4:performance", "tool produced no score",
                       why="x", owner=Owner.HARNESS)
    rows = [
        json.loads(line)
        for line in (tmp_path / ".methodology" / "degradations.jsonl")
        .read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    assert rows[-1]["owner"] == Owner.HARNESS


def test_a_site_that_has_not_decided_still_writes_a_row(tmp_path):
    """The default must remain honest, not absent.

    The lint above forbids relying on it in production; the runtime behaviour
    for a caller that does is `unknown` — a real answer — rather than a
    missing field the reader has to interpret.
    """
    import json

    from core.degradation_ledger import record_degradation

    record_degradation(tmp_path, "probe", "something gave way")
    row = json.loads(
        (tmp_path / ".methodology" / "degradations.jsonl")
        .read_text(encoding="utf-8").strip().splitlines()[-1]
    )
    assert row["owner"] == "unknown"
