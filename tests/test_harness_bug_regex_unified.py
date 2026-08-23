r"""Round 69 follow-up (code review of 17f5f448/7584a7da).

`HARNESS_BUG_RE_JS`'s own comment claimed to be "one statement of the
crash-banner shape, shared by every site that routes on it" — it wasn't.
`render_terminal_abort_detectors` had grown its own independently-narrowed
inline copy (a `{0,200}`-window regex) while the module constant — the one
`render_sync_verified`'s Sync step actually tests against — stayed the OLD
broad `/\[HARNESS-BUG\]/`, still open to the exact FR-04 proof-by-absence
false-positive 7584a7da meant to fix everywhere. The two-detector split also
meant a genuine crash whose exception message put the fixed banner sentence
past 200 chars from the tag would silently read as an ordinary GATE1 FAIL.
"""
from __future__ import annotations

import re

from scripts.workflowgen.generate_workflows import generate, generate_composite
from scripts.workflowgen.js_blocks import HARNESS_BUG_RE_JS


def _js_regex_to_python(js_regex: str) -> re.Pattern:
    assert js_regex.startswith("/") and js_regex.endswith("/i")
    body = js_regex[1:-2]
    return re.compile(body, re.IGNORECASE)


def test_the_module_constant_is_the_narrow_form() -> None:
    assert "This is a bug in harness-methodology itself" in HARNESS_BUG_RE_JS, (
        "the shared constant must require the banner's literal second line, "
        "not just the bracketed [HARNESS-BUG] tag"
    )
    assert "{0," not in HARNESS_BUG_RE_JS, (
        "a bounded distance window between the tag and the banner sentence "
        "reintroduces the false-negative this round removed: "
        "format_harness_bug_banner puts the exception's own unbounded "
        "message on the same line as the tag, before the fixed sentence"
    )


def test_a_far_apart_banner_still_matches() -> None:
    """The exact failure mode: a long exception summary pushes the fixed
    second line of the banner past what a {0,200} window could reach."""
    pattern = _js_regex_to_python(HARNESS_BUG_RE_JS)
    long_summary = "x" * 400
    banner = (
        f"[HARNESS-BUG] ValueError: {long_summary}\n"
        "  This is a bug in harness-methodology itself, NOT a problem with "
        "your project's code or tests.\n"
    )
    assert pattern.search(banner), (
        "a genuine crash banner with a long exception summary must still "
        "be detected — a distance cap silently drops it"
    )


def test_a_proof_by_absence_quote_does_not_match() -> None:
    """Round 66/7584a7da's FR-04 incident, restated for the shared regex."""
    pattern = _js_regex_to_python(HARNESS_BUG_RE_JS)
    proof_by_absence = (
        "GATE1: PASS. No [FATAL] / [HARNESS-BUG] / [BLOCKED] found in log."
    )
    assert not pattern.search(proof_by_absence), (
        "quoting the bracketed tag alone (to prove its absence) must not "
        "trigger the detector — that is the FR-04 false positive"
    )


def test_the_prompts_own_paraphrase_does_not_match() -> None:
    """Code-review follow-up (2026-08-23): this round's initial fix widened
    the regex to unbounded [\\s\\S]*, which matched the GATE1 dispatch
    prompt's OWN description of the banner shape
    (scripts/workflowgen/spec_phase3.py): '...the harness crash banner
    (`[HARNESS-BUG] <ExcType>: <summary>` then `This is a bug in
    harness-methodology itself`)...' — both phrases inline on ONE line, no
    newline between them. An agent quoting/paraphrasing its own dispatch
    prompt while explaining no crash occurred could trip a false positive
    on its own instructions. The real banner always separates the two
    phrases onto two lines (core/errors.py's format_harness_bug_banner);
    requiring that literal shape must reject this inline prose."""
    pattern = _js_regex_to_python(HARNESS_BUG_RE_JS)
    prose = (
        "the log contains the harness crash banner (`[HARNESS-BUG] "
        "<ExcType>: <summary>` then `This is a bug in harness-methodology "
        "itself`)."
    )
    assert not pattern.search(prose), (
        "the GATE1 prompt's own inline description of the banner shape "
        "(both phrases on the SAME line, no newline between them) must "
        f"not trigger the detector: {prose!r}"
    )


def test_both_sites_use_the_shared_constant_not_a_second_copy() -> None:
    """Sync (render_sync_verified) and the per-FR loop
    (render_terminal_abort_detectors) must test the SAME pattern — a second,
    independently-drifting inline copy is what let the Sync site stay on the
    old broad regex after 7584a7da narrowed the other one."""
    run_all = generate_composite("run-all")
    occurrences = run_all.count(HARNESS_BUG_RE_JS)
    assert occurrences >= 2, (
        f"expected the shared HARNESS_BUG_RE_JS pattern at both the per-FR "
        f"loop site and the Sync site in run-all.js; found {occurrences} "
        f"occurrence(s) — a site has its own independent copy again"
    )


def test_spec_phase3_names_the_signal_the_agent_can_actually_read() -> None:
    """7584a7da's paraphrasing pass weakened the AAP-INFRA prompt case from
    literal detection strings to vague prose ("AAP block or INFRA fatal"),
    giving the TDD agent nothing to pattern-match the log against. The
    original fix named the substring `_abort_dispatch_infra_or_harness_bug`
    prints; Round 70 站3 replaced the whole class of prose-matching with the
    exit code that call site returns, so what the prompt must name is the
    RC and how to read it. The property the assertion protects is unchanged
    — the agent must be told a signal it can extract deterministically, not
    asked to characterise the log in its own words."""
    phase3 = generate(3)
    assert 'RC=<integer>' in phase3, (
        "the GATE1 prompt must tell the agent exactly what to read off the "
        "log; a paraphrase is what 7584a7da regressed to"
    )
    for rc in ('RC=23', 'RC=70', 'RC=25'):
        assert rc in phase3, (
            f"{rc} is one of the three terminal aborts the prompt must name "
            "so the agent stops instead of retrying into it"
        )
