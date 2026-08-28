"""Plan E (Round 50+) — advance-phase early pragma audit.

Closes the d0b3b9a -> 476427d oscillation in taskq-cc-new by failing the
P3 exit (or any phase's advance) deterministically when any
``# pragma: no cover`` is present outside ``PRAGMA_NO_COVER_ALLOWLIST``
(SSOT, defined in ``core/phase_hooks.py``). Pre-Plan E the same check
ran at P4 entry's ``preflight_reliability_lint`` (push hook), forcing a
commit-and-revert cycle.

Why a deterministic audit (not a sub-agent dispatch loop): ``auto_fix``
13 strategies were retired in Round 48 because LLM-written tests
silently passed coverage gates. Plan E keeps the human-in-the-loop
contract intact and avoids the retry-LLM surface area.

These tests verify the two layers of the change:
- Two direct-call checks exercise the ``_audit_pragma_no_cover`` function
  on minimal fixtures — narrowly scoped to the SSOT allowlist policy
  itself, without invoking ``_advance_prechecks``'s broader pipeline
  (which depends on a fully materialised project layout including
  ``00-summary/``, ``02-architecture/``, etc. — out of scope here).
- Two wiring checks read the AST of ``_advance_prechecks``.

Round 78 站5 rewrote the second pair. They were
``assert "_audit_pragma_no_cover" in inspect.getsource(...)`` and
``assert src.count("PRAGMA_NO_COVER_ALLOWLIST") >= 2`` — a substring and an
occurrence count over source TEXT, both of which a comment satisfies. Plan F's
matching pair failed exactly that way: one of them asserted a comment was
present, and it stayed green through the entire period its check was blocking
all nine corpus projects (Round 78 站1). A call node and a `.join` argument
are structural facts; prose cannot forge them and a rename cannot slip past
them.
"""

import ast
from pathlib import Path

from core.phase_hooks import _audit_pragma_no_cover

_PHASE_CMDS = Path(__file__).resolve().parents[1] / "cli" / "phase_cmds.py"


def _advance_prechecks_ast() -> ast.FunctionDef:
    """The precheck pipeline, not just its entry point.

    Round 81 站6 moved these calls into `_precheck_*` helpers. `inlined` puts
    them back where they run, renumbered in execution order, so the ORDER half
    of the assertions below still asks what it always asked.
    """
    from tests.support.pipeline import inlined

    return inlined("cli/phase_cmds.py", "_advance_prechecks",
                   helper_prefix="_precheck_")


def test_advance_prechecks_calls_audit_pragma_no_cover():
    """Plan E: _advance_prechecks must invoke the SSOT audit, and invoke it
    BEFORE the stages it exists to save — ruff is the first of them.

    Anchored on the ruff stage's own argv rather than "the first subprocess
    call": _advance_prechecks shells out to git and gitleaks well before
    this, so "first" would be false and the test would be measuring the
    wrong thing.
    """
    fn = _advance_prechecks_ast()
    audit = [n.lineno for n in ast.walk(fn)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
             and n.func.id == "_audit_pragma_no_cover"]
    assert audit, (
        "Plan E: _advance_prechecks must call _audit_pragma_no_cover to "
        "catch non-allowlist pragma BEFORE coverage/lint/type run."
    )
    ruff = [n.lineno for n in ast.walk(fn)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
            and n.func.attr == "run" and n.args
            and isinstance(n.args[0], ast.List) and n.args[0].elts
            and isinstance(n.args[0].elts[0], ast.Constant)
            and n.args[0].elts[0].value == "ruff"]
    assert ruff, "expected the ruff stage — this test anchors on it"
    assert min(audit) < min(ruff), (
        f"the pragma audit runs at line {min(audit)}, after the ruff stage at "
        f"{min(ruff)} — Plan E's whole point is failing before lint/type/"
        f"coverage, not after them")


def test_advance_prechecks_renders_ssot_allowlist_in_early_block():
    """Plan E's BLOCKED message must render ``PRAGMA_NO_COVER_ALLOWLIST``
    verbatim — same SSOT binding Round 22 enforced on the coverage-gate
    BLOCK (``test_advance_phase_pragma_guidance.py``).

    The old form counted the symbol's occurrences in the source text, which
    an import line and a comment both satisfy. What the binding actually
    means is that the allowlist is JOINED INTO a printed message, so that is
    what gets asserted: two `", ".join(PRAGMA_NO_COVER_ALLOWLIST)` calls —
    Plan E's early block and the coverage-gate block it sits beside.
    """
    joins = [
        node for node in ast.walk(_advance_prechecks_ast())
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute) and node.func.attr == "join"
        and any(isinstance(a, ast.Name) and a.id == "PRAGMA_NO_COVER_ALLOWLIST"
                for a in node.args)
    ]
    assert len(joins) >= 2, (
        f"expected the allowlist to be rendered into both BLOCK messages "
        f"(Plan E's early one and the coverage gate's); found {len(joins)} "
        f"join(s). An operator told a pragma is forbidden and not told which "
        f"ones are exempt has to go read the source.")


def test_audit_pragma_no_cover_reports_non_allowlist_pragma(tmp_path):
    """SSOT behaviour: a ``# pragma: no cover`` outside
    PRAGMA_NO_COVER_ALLOWLIST must produce a ``py-pragma-no-cover`` finding.
    Direct call to the audit function — verifies the SSOT enforcement the
    Plan E caller relies on.
    """
    py = tmp_path / "module.py"
    py.write_text(
        "def branch():\n    if False:\n        return 1  # pragma: no cover\n",
        encoding="utf-8",
    )
    findings = _audit_pragma_no_cover([str(tmp_path)])
    matches = [f for f in findings if f.get("line") == 3]
    assert matches, (
        f"Expected py-pragma-no-cover finding on line 3 (non-allowlist); "
        f"got findings: {findings!r}"
    )


def test_audit_pragma_no_cover_allows_except_baseexception(tmp_path):
    """SSOT behaviour: ``# pragma: no cover`` co-located with
    ``except BaseException`` on the SAME line is allowlisted — must NOT
    produce a finding. Direct call, no mock.
    """
    py = tmp_path / "module.py"
    py.write_text(
        "try:\n"
        "    open('/tmp/x', 'w').write('a')\n"
        "except BaseException:  # pragma: no cover\n"
        "    pass\n",
        encoding="utf-8",
    )
    findings = _audit_pragma_no_cover([str(tmp_path)])
    assert findings == [], (
        f"except BaseException pragma must be allowlisted by SSOT; "
        f"unexpected findings: {findings!r}"
    )
