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
- Two static delegation checks confirm ``_advance_prechecks`` actually
  invokes the SSOT audit and renders the allowlist symbol verbatim (same
  style as ``test_advance_phase_pragma_guidance.py``'s Round 22 binding).
- Two direct-call checks exercise the ``_audit_pragma_no_cover`` function
  on minimal fixtures — narrowly scoped to the SSOT allowlist policy
  itself, without invoking ``_advance_prechecks``'s broader pipeline
  (which depends on a fully materialised project layout including
  ``00-summary/``, ``02-architecture/``, etc. — out of scope here).
"""

import inspect

from cli.phase_cmds import _advance_prechecks
from core.phase_hooks import _audit_pragma_no_cover


def test_advance_prechecks_calls_audit_pragma_no_cover():
    """Plan E: _advance_prechecks must invoke the SSOT audit BEFORE
    coverage/lint/type. Static delegation check — same style as
    test_prompt_gate_parity.py's _check_spec_parser.
    """
    src = inspect.getsource(_advance_prechecks)
    assert "_audit_pragma_no_cover" in src, (
        "Plan E: _advance_prechecks must call _audit_pragma_no_cover to "
        "catch non-allowlist pragma BEFORE coverage/lint/type run."
    )


def test_advance_prechecks_renders_ssot_allowlist_in_early_block():
    """Plan E's BLOCKED message must render ``PRAGMA_NO_COVER_ALLOWLIST``
    verbatim — same SSOT binding Round 22 enforced on the coverage-gate
    BLOCK (``test_advance_phase_pragma_guidance.py``). The Plan E block
    adds a second ``PRAGMA_NO_COVER_ALLOWLIST`` reference so the count
    must reach at least 2 across the function.
    """
    src = inspect.getsource(_advance_prechecks)
    assert src.count("PRAGMA_NO_COVER_ALLOWLIST") >= 2, (
        "Plan E's early BLOCK must render PRAGMA_NO_COVER_ALLOWLIST "
        "in addition to the coverage-gate BLOCK's reference."
    )


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
