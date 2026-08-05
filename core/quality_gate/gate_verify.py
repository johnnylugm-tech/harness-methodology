"""The gate verdict, written down (Round 38 站4).

The workflow decides whether a gate passed from three numbers:

    gate4Pass = !!(g4v && g4v.last_gate_ok === true
                       && g4v.d4_rc === 0
                       && g4v.crg_rc === 0)

Every one of them is transcribed by an agent out of command output, and none
of them is written anywhere. A full-text search of taskq-renew's
`.methodology/` for ``crg_rc`` after a complete P1-P8 run returns zero hits.

That is not merely untidy. taskq-renew's P6 wrote `crg_baseline_p6.json` with
`architecture_score: 77.8` — below the floor of 80 its own gate config states
— and `gate4-verify-r1` passed on the first round, which requires
``crg_rc === 0``. One of those two is wrong. It could be that an intervening
`code-review-graph update` moved the score over 80 between the two steps; it
could be that the RC was transcribed wrong. Both are defects. Neither is
adjudicable, because the framework kept no record capable of distinguishing
them.

`record_verdict` writes the three checks, the commit, and — the part that
makes "matching" mean something — a digest of the delivered tree they were
measured on. `has_matching_pass` is what `advance-phase` asks before letting
an exit gate through: not "did a PASS ever exist?" but "does a PASS exist for
*this* tree?". Round 37's lesson one level up: a number is only as good as the
tree it was measured over, and so is the verdict that number produced.

The ledger is append-only and lives beside `gate_timestamps.jsonl` and
`degradations.jsonl`, in the same one-JSON-object-per-line shape, for the same
reason: a record that can be overwritten is not an audit trail.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

from core.utils.delivery_scope import delivered_tree_digest

__all__ = ["LEDGER_NAME", "record_verdict", "has_matching_pass", "read_verdicts"]

LEDGER_NAME = "gate_verify.jsonl"

PASS = "PASS"
FAIL = "FAIL"


def _ledger_path(project: Path) -> Path:
    return Path(project) / ".methodology" / LEDGER_NAME


def _git_sha(project: Path) -> str:
    import subprocess
    try:
        proc = subprocess.run(
            ["git", "-C", str(project), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return proc.stdout.strip() if proc.returncode == 0 else ""


def record_verdict(
    project: Path, gate: int, phase: int, checks: dict, verdict: str,
) -> dict:
    """Append one verdict to the ledger and return the record written.

    `checks` is the raw per-check outcome (`last_gate_ok`, `spec_coverage_rc`,
    `crg_rc`) rather than a summary. The summary is what the workflow acts on;
    the raw values are what makes a later "which of these two is wrong?"
    answerable at all, which is the whole reason this file exists.
    """
    project = Path(project)
    now = time.time()
    record = {
        # Round 24: one time base, both forms — epoch for arithmetic, ISO for
        # a human reading the file.
        "ts": now,
        "iso": datetime.fromtimestamp(now, tz=timezone.utc).isoformat(),
        "gate": int(gate),
        "phase": int(phase),
        "git_sha": _git_sha(project),
        "delivered_tree_sha256": delivered_tree_digest(project),
        "checks": dict(checks),
        "verdict": verdict,
    }
    path = _ledger_path(project)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")
    return record


def read_verdicts(project: Path) -> "tuple[list[dict], str]":
    """(records, error). A non-empty error means the ledger is unusable."""
    path = _ledger_path(Path(project))
    if not path.is_file():
        return [], "no gate_verify.jsonl"
    rows: list[dict] = []
    for lineno, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), 1
    ):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            return [], f"{LEDGER_NAME} line {lineno} is not valid JSON: {exc}"
        if isinstance(row, dict):
            rows.append(row)
    return rows, ""


def has_matching_pass(project: Path, gate: int) -> "tuple[bool, str]":
    """(ok, why_not) — is there a PASS for *gate* on the tree as it stands now?

    The *latest* verdict for the gate decides. A gate that failed, was fixed
    and re-verified must be able to advance; a gate that passed and then
    regressed must not, and only "latest wins" gives both.

    An unreadable ledger is a refusal, not a pass (Round 32/35).
    """
    project = Path(project)
    rows, err = read_verdicts(project)
    if err:
        return False, (
            f"{err}. Run `harness_cli.py verify-gate --gate {gate}` to produce one."
        )
    mine = [r for r in rows if r.get("gate") == gate]
    if not mine:
        return False, (
            f"no gate {gate} verdict recorded. Run "
            f"`harness_cli.py verify-gate --gate {gate}`."
        )
    latest = mine[-1]
    if latest.get("verdict") != PASS:
        return False, (
            f"the latest gate {gate} verdict is {latest.get('verdict')!r} "
            f"({latest.get('checks')}). Fix the failing check, then re-run "
            f"`harness_cli.py verify-gate --gate {gate}`."
        )
    current = delivered_tree_digest(project)
    if latest.get("delivered_tree_sha256") != current:
        return False, (
            f"the gate {gate} PASS was measured on a different tree — the "
            f"delivered files have changed since it was recorded. Re-run "
            f"`harness_cli.py verify-gate --gate {gate}` against the tree you "
            f"are about to advance."
        )
    return True, ""
