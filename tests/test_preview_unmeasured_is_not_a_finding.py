"""A reading nobody took is not a finding (Round 69 站1).

`render_preview_next_phase` (dc92fb5) collapses two different outcomes into
one boolean::

    previewClean = !!(previewReport && previewReport.pass === true)

`previewReport` is `null` when the dispatch was skipped, rate-limited or hit a
terminal API error — the agent never ran `preview-next-phase` at all. The
block then treats that identically to "obligations were found": it dispatches
the FIXER with ``String((previewReport && previewReport.reason) ?? '')`` — an
empty obligation list — twice, and finally halts with

    'Phase N entry obligations still present after 3 round(s) — escalate to human'

which asserts as fact the one thing that was never measured, and words the
stop as the project's fault. Round 35's rule ("量不出來不是零分") and Round
48's (every stop names an owner) are both broken by the same three lines.

The sibling dispatch in the same file already gets this right:
`render_entry_preflight` halts with
``'agent returned null (skipped or terminal API error)'`` when its schema
dispatch comes back empty. This is that precedent, applied.
"""
from __future__ import annotations

import re

from core.phase_hooks import _DELAYED_BLOCKING_PREFLIGHTS
from scripts.workflowgen import js_blocks as B


def _preview(phase: int = 3) -> str:
    return B.render_preview_next_phase(phase)


def test_a_null_reading_halts_as_unmeasured_not_as_findings() -> None:
    text = _preview()
    assert "preview-next-phase-unmeasured" in text, (
        "a null checker reply must take its own exit, distinguishable from "
        "'obligations were found'"
    )
    assert "agent returned null" in text


def test_the_fixer_is_not_dispatched_without_an_obligation_list() -> None:
    """The fixer's prompt interpolates the checker's `reason`. Sending it an
    empty string buys a dispatch that can only report 'nothing named'."""
    text = _preview()
    assert "previewReason" in text, (
        "the obligation list must be captured and checked before the fixer is "
        "dispatched, not interpolated inline"
    )
    fixer_prompt = text[:text.index("label: 'preview-fix-r'")]
    assert "(previewReport && previewReport.reason) ?? ''" not in fixer_prompt, (
        "the fixer is still reachable with an empty obligation list"
    )


def test_the_halt_message_does_not_blame_the_project_for_a_missing_reading() -> None:
    """`halt` text is what the operator and the fault-owner classifier read."""
    text = _preview()
    unmeasured = text[text.index("preview-next-phase-unmeasured"):]
    assert "obligations still present" not in unmeasured[:400], (
        "the unmeasured exit must not claim obligations exist"
    )


def test_the_docstring_does_not_restate_the_category_registry() -> None:
    """dc92fb5's docstring enumerated ten `_DELAYED_BLOCKING_PREFLIGHTS` names.

    Two of them (`previous_phase_artifacts`, `bvs_phase_order`) never write a
    `blocking` key, so `preview_next_phase_blocking`'s
    ``not res.get("blocking")`` drops them unconditionally — a claim about ten
    categories that eight could reach, written down because the list was
    copied rather than read. Naming one or two while explaining something is
    fine; restating the set is what drifts.
    """
    doc = B.render_preview_next_phase.__doc__ or ""
    named = set(re.findall(r"[a-z_]{4,}", doc)) & _DELAYED_BLOCKING_PREFLIGHTS
    assert len(named) < 5, f"the docstring re-enumerates the registry: {sorted(named)}"
    assert "_DELAYED_BLOCKING_PREFLIGHTS" in doc, (
        "it must point at where the set actually lives"
    )
