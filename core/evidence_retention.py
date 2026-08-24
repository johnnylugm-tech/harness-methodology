"""evidence_retention.py — a verdict may not cite a directory that gets deleted.

Round 50 站6. Round 45 站1 established the rule and moved the agent's cited
``tool_output`` under ``.methodology/gate_evidence/``. This is the case that
station did not reach: the file the FRAMEWORK writes about its own run.

S4 cross-validation runs each dimension's tool itself and writes what it saw
to an audit file, then names that file in the message the operator is meant to
act on ("see the audit file in ..."). The file was written under
``.sessi-work/``, and ``cli/phase_cmds.py`` clears ``.sessi-work/`` at every
phase transition — deliberately, because stale artifacts there made the next
phase's gate skip re-computation.

Both behaviours are individually correct. Together they mean the audit trail
for a gate verdict is gone one advance later.

Measured 2026-08-13: taskq-api's Gate 4 recorded a cross-validation gap for
`performance` at 06:19 UTC and published PASS at 06:29. Asked ten days later
which S4 branch that second run took, the answer is unavailable — the project
has no ``harness_verification/`` directory, so the question cannot be settled
from the record. The investigation into this defect was stopped by it.

Scope, stated so it is not mistaken for more: this makes the audit file
survive the phase transition. A second run of the SAME gate still overwrites
its predecessor's file, exactly as before. That is a different question
(per-run retention) and this module does not answer it.

Round 72 站6 — the same collision, one layer out, in the JUDGED project

The rule above is about files the framework writes. `evidence_in_cleared_dirs`
below is about files the PROJECT's own delivered tests read, and it exists
because taskq-new paid for the collision twice with byte-identical commits.

Its NFR-07 and NFR-11 tests read
``.sessi-work/round_1/tools/pip_licenses.json`` and ``readability_v2.txt``,
and `pytest.skip` when they are not there. So: advance clears the directory →
next phase those tests skip → Round 46 站1's absent-witness rule turns
NFR-07/NFR-11 PARTIAL → completeness falls under 90% → advance is refused →
the agent regenerates the artifacts by hand and re-renders the matrix. `cd47fae`
(leaving P5) and `8b9a309` (leaving P7) have the same subject and the same
body, differing in the phase number.

Nothing told the project where evidence may live, though this module already
knew: `cited_evidence_dir` is under ``.methodology/``, and
`delivery_fingerprint.py` says why in one line — "advance-phase clears the
work directory at every transition, and a fact recorded for a future round to
compare against has to outlive the run that recorded it".
"""

from __future__ import annotations

import ast
import subprocess  # nosec B404
from pathlib import Path

__all__ = [
    "ADVANCE_CLEARED_DIRS",
    "cited_evidence_dir",
    "evidence_in_cleared_dirs",
]

# Project-relative directories `advance-phase` deletes at every phase
# transition. The cleanup reads this list; so does the rule above it. Adding a
# scratch directory here is what makes it cleared, which is what keeps the two
# from drifting.
ADVANCE_CLEARED_DIRS: tuple[str, ...] = (
    ".sessi-work",
)


def cited_evidence_dir(project: "str | Path") -> Path:
    """Where the framework writes evidence a verdict's message points at.

    Under ``.methodology/gate_evidence/`` because that is the directory a
    clone gets and the one Round 45 站1 already named as where a verdict's
    proof lives — one place, not a second one alongside it.
    """
    from core.quality_gate.gate_evidence_store import EVIDENCE_DIR_RELPATH
    return Path(project) / EVIDENCE_DIR_RELPATH / "harness_verification"


def _docstring_constants(tree: ast.AST) -> set[int]:
    """`id()` of every node that is a module/class/function docstring."""
    out: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(
            node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
        ):
            continue
        body = getattr(node, "body", None)
        if not body:
            continue
        first = body[0]
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            out.add(id(first.value))
    return out


def evidence_in_cleared_dirs(project: "str | Path") -> list[dict]:
    """Delivered Python that reads a path under a directory advance deletes.

    Returns ``[{"path", "line", "literal"}, …]``, one per string LITERAL naming
    a cleared directory. Empty when there is nothing to say — including when
    git is unavailable, which is could-not-measure and not a finding
    (Rounds 32/35).

    Three deliberate limits, each from the measurement across the nine projects
    on this machine (nine hits, all in taskq-new, and only four of them real):

    * `git ls-files -- '*.py'` is the file set. The delivered tree is what git
      has (Round 44 站2), and it excludes `.venv`, caches and anything
      gitignored without a second definition of "delivered".
    * String literals only, read via `ast`, so `conftest.py`'s
      ``# ``.sessi-work/benchmark_report.json``.`` — a comment recording where
      a file used to go — is not a finding. A comment does not read anything.
    * Docstrings are excluded for the same reason: prose about a path is not a
      dependency on it.

    The four remaining hits are the real ones, all in
    `tests/test_nfr07_08_11_lint.py`, and they are what cost that project two
    identical repair commits.
    """
    from core.utils.subprocess_group import run_isolated

    project = Path(project)
    try:
        listed = run_isolated(
            ["git", "-C", str(project), "ls-files", "--", "*.py"], timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if listed.returncode != 0:
        return []

    findings: list[dict] = []
    for rel in listed.stdout.splitlines():
        rel = rel.strip()
        if not rel:
            continue
        path = project / rel
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError, ValueError):
            continue
        docstrings = _docstring_constants(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                continue
            if id(node) in docstrings:
                continue
            for cleared in ADVANCE_CLEARED_DIRS:
                if cleared in node.value:
                    findings.append({
                        "path": rel,
                        "line": node.lineno,
                        "literal": node.value,
                    })
                    break
    return findings
