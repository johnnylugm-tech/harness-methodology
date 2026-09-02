"""The file that is a score's only backing has to say who produced it.

Round 91. `license_compliance` is tier-1 and its threshold is 100. scancode is
on the skip list — too slow for the harness to re-run — so `harness_bridge`
accepts the agent's number if a committed `tool_output` file "matches the
tool's output format" (its own words, harness_bridge.py:797). Measured on
taskq-redo's Gate 4:

    .methodology/gate_evidence/gate4/license_compliance.json
        22893 bytes, json.loads fails at line 1 column 1
    gate4_result.json.breakdown.license_compliance
        score = 100.0,  score_source = artifact_verified

The file holds real scancode JSON — 48 files scanned, 0 errors — sandwiched
between Python import warnings and a `Scanning done.` summary, because
`scancode --json-pp -` writes the document to stdout and everything else to
stderr and the prompt did not separate them. `_validate_tool_content` returned
no violations: check 3 is an OR over the words `license`, `SPDX`, `copyright`
and `scan:`, and the progress line "Scan files for: licenses" contains the
first one.

WHY THIS DIMENSION AND NOT THE OTHERS

`mutation_testing` is also skip-list, and its number does NOT rest on its
tool_output: `.methodology/mutation_score.json` is written by the framework
and checked before the agent's claim is read (Round 35 站3, whose comment says
"mutmut is the only member today"). `license_compliance` has no counterpart —
the committed file is the whole of it.

WHY `headers[0].tool_name` AND NOT "IS IT VALID JSON"

Measured over the corpus's twenty committed licence evidence files:

    9   headers[0].tool_name == scancode-toolkit    genuine --json-pp output
    8   not JSON                                    stdout/stderr interleaved
    2   JSON, no headers                            an agent's own summary
    1   JSON that is not an object

A "must be valid JSON" rule passes taskq-super's 45-byte hand-written summary.
A "must have scancode's top-level keys" rule passes it too — it has
`license_detections`. Asking who produced it is the question with no ambiguous
answers. Separately measured: keying the rule on the `.json` SUFFIX was
rejected, because 32 of the 125 `.json` files under `gate_evidence/` across the
corpus are ordinary tool text (pytest's dots, ruff's `All checks passed!`).
"""
from __future__ import annotations

import json

import pytest

pytestmark = [pytest.mark.core]

#: A genuine `--json-pp` document, trimmed. Shape from taskq-cc's committed
#: Gate 4 evidence — deliberately NOT built from the check that reads it.
GENUINE = json.dumps({
    "headers": [{"tool_name": "scancode-toolkit", "tool_version": "32.4.1",
                 "errors": [], "warnings": []}],
    "license_detections": [],
    "files": [{"path": "src/app.py", "type": "file",
               "detected_license_expression": "apache-2.0",
               "license_detections": [{"license_expression": "apache-2.0"}]}],
}, indent=2)

#: What taskq-redo committed: the same document with stderr interleaved.
INTERLEAVED = (
    "/Users/x/Library/Python/3.9/lib/python/site-packages/extractcode/"
    "libarchive2.py:107: UserWarning: Using \"libarchive\" library found in a "
    "system location.\n  warnings.warn(\n"
    "Setup plugins...\nCollect file inventory...\n"
    "Scan files for: licenses with 2 process(es)...\n"
    + GENUINE +
    "Scanning done.\nSummary:        licenses with 2 process(es)\n"
    "Errors count:   0\nScan Speed:     9.78 files/sec.\n"
)


def _check(content: str, tool: str = "scancode", *, inline: bool = False) -> list[str]:
    from harness.gate_checks import _validate_tool_content

    return _validate_tool_content(content, tool, "license_compliance", inline=inline)


# ── the defect ───────────────────────────────────────────────────────────


def test_interleaved_stdout_and_stderr_is_not_scancode_output() -> None:
    """The reproduction. Before Round 91 this returned []."""
    problems = _check(INTERLEAVED)
    assert problems, (
        "a file that json.loads rejects was accepted as the sole backing for a "
        "tier-1 score — this is taskq-redo's committed Gate 4 evidence"
    )
    assert "not parseable JSON" in problems[0]
    assert "2>/dev/null" in problems[0], "the message has to name the repair"


def test_the_word_license_alone_no_longer_buys_a_score() -> None:
    """Check 3 passes on any of four bare words; that is what let the file
    above through. This pins that the words are no longer sufficient."""
    assert _check("Scan files for: licenses with 2 process(es)...\n"), (
        "a progress line containing 'license' still satisfies the check"
    )


def test_a_hand_written_summary_is_not_the_tools_own_file() -> None:
    """45 bytes of valid JSON with scancode-ish keys — taskq-super's Gate 4.
    Both weaker candidate rules accept it."""
    summary = json.dumps({"files_count": 48, "license_detections": []})
    problems = _check(summary)
    assert problems, "a hand-written summary passed as scancode's own output"
    assert "headers" in problems[0] and "tool_evidence" in problems[0], (
        "the message should say where a summary does belong"
    )


def test_json_that_is_not_an_object_is_rejected() -> None:
    problems = _check(json.dumps([{"path": "a.py", "license": "mit"}]))
    assert problems and "not a scancode document" in problems[0]


def test_output_from_a_different_tool_is_rejected() -> None:
    other = json.dumps({"headers": [{"tool_name": "pip-licenses"}], "files": []})
    problems = _check(other)
    assert problems and "pip-licenses" in problems[0]


# ── it must not reject what it exists to admit ───────────────────────────


def test_genuine_scancode_output_passes() -> None:
    assert _check(GENUINE) == [], (
        "the check rejects a real `scancode --json-pp` document — it would "
        "block every honest project"
    )


def test_inline_evidence_is_still_an_excerpt() -> None:
    """`tool_evidence` is defined as an excerpt in evaluate_dimension.md, so it
    cannot be expected to parse. Only the committed file carries the score."""
    assert _check("detected_license_expression: apache-2.0", inline=True) == []


def test_no_other_tool_is_affected() -> None:
    """The rule is scancode's alone. ruff's clean run is two bytes of JSON and
    bandit's output is text; requiring provenance of thirty tools is how a
    guard manufactures the fabrication it exists to prevent (Round 67 站3)."""
    assert _check("All checks passed!", tool="ruff") == []
    assert _check("[main]\tINFO\tprofile include tests: None\n"
                  "Test results:\n\tNo issues identified.", tool="bandit") == []


# ── the chain that makes this dimension the only member ──────────────────


def test_mutation_keeps_its_framework_produced_number() -> None:
    """If mutmut ever loses `_mutation_artifact_violations`, its tool_output
    becomes the sole backing too and this rule needs a second member."""
    from harness.gate_checks import _mutation_artifact_violations  # noqa: F401
    from harness import harness_bridge

    src = harness_bridge.__file__
    with open(src, encoding="utf-8") as fh:
        text = fh.read()
    assert "_mutation_artifact_violations(" in text, (
        "mutation_testing no longer has a framework-produced number checked "
        "before the agent's claim — re-derive whether scancode is still the "
        "only dimension whose score rests entirely on a committed file"
    )


def test_the_prompt_tells_the_agent_to_separate_the_streams() -> None:
    """The detection above blocks; this is the cause. The prescribed command
    used to be `scancode ... --json-pp - src/ | head -300` — no stderr
    separation, and `head` truncates a pretty-printed document mid-object."""
    from pathlib import Path

    prompt = (Path(__file__).resolve().parents[1] / "harness" / "ssi" / "prompts"
              / "evaluate_dimension.md").read_text(encoding="utf-8")
    line = next((ln for ln in prompt.splitlines()
                 if ln.strip().startswith("scancode ")), None)
    assert line is not None, "the scancode command left evaluate_dimension.md"
    assert "2>/dev/null" in line, (
        f"the prescribed scancode command does not separate stderr: {line!r} — "
        f"the framework would be teaching agents to produce the file its own "
        f"check rejects"
    )
    assert "head" not in line, (
        f"`head` truncates a pretty-printed JSON document mid-object: {line!r}"
    )


def test_the_fixtures_standing_in_for_scancode_are_real_shapes() -> None:
    """Round 19's shape, found in this round's own dependencies.

    Two existing tests write a stand-in scancode artifact, one of them the test
    that BUILT `artifact_verified` (Round 83 站1). Both wrote the sentence
    "Scan completed. No license violations found.", which scancode cannot emit.
    A rule underwritten by a sample that could not exist is a rule nothing has
    tested.
    """
    from pathlib import Path

    tests_dir = Path(__file__).resolve().parent
    for name in ("test_anti_fabrication.py", "test_every_score_names_its_source.py"):
        text = (tests_dir / name).read_text(encoding="utf-8")
        assert "scancode_out" in text, f"{name} no longer writes a scancode stand-in"
        assert "Scan completed. No license violations found." not in text, (
            f"{name} is back to standing in for scancode output with a sentence "
            f"scancode cannot produce"
        )
        assert "scancode-toolkit" in text, (
            f"{name}'s stand-in artifact does not identify its producer, so it "
            f"no longer stands in for anything the harness would accept"
        )
