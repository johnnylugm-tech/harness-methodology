"""Round 34 站0/站1 — the H1 anchor rule has one semantics, not two.

The rule is implemented twice on purpose, because the two layers see different
inputs: `scripts/file_loader.py` validates the file on disk, and the workflow
JS validates the text the shell-wrapper agent handed back. The JS layer exists
because the agent is an untrusted relay — `file_loader.py`'s own docstring
lists Bug v5 (a fine-tuned model prepending "Acknowledged" after any tool call)
and Bug v8 (the loader returning fabricated content for a file that did not
match).

Measured before this file existed: the JS layer used

    new RegExp('^#\\\\s+[^\\\\n]*' + escaped, 'm').test(text.slice(0, 500))

— "any H1 line in the first 500 characters that contains the phrase" — so

    "Acknowledged.\\n\\n# Software Requirements Specification — taskq\\n…"

passed. That is precisely the Bug v5 shape the layer was built to reject. The
same function's own header comment (js_blocks.py:1349-1350) said the rule is
"a first-line startswith()", and the `<think>`-strip patch above it (1390-1393)
described the check below as "^-anchored". Three statements, one implementation,
and the implementation was the odd one out.

Two consumers now read `tests/fixtures/anchor_semantics_cases.json`: this file
runs each case through `load_file`, and
`scripts/workflowgen/js_src/anchor_check.test.mjs` runs the same cases through
`firstLineHasAnchor`. A drift in either direction fails here.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = [pytest.mark.core]

_REPO = Path(__file__).resolve().parent.parent
_FIXTURE = _REPO / "tests" / "fixtures" / "anchor_semantics_cases.json"
_MJS = _REPO / "scripts" / "workflowgen" / "js_src" / "anchor_check.mjs"


def _cases() -> list[dict]:
    data = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    cases = data["cases"]
    assert cases, "an empty case list would pass this file vacuously"
    return cases


@pytest.mark.parametrize("case", _cases(), ids=lambda c: c["name"])
def test_the_disk_side_agrees_with_the_shared_cases(case, tmp_path):
    """`load_file` is the authoritative half; the fixture states its verdict."""
    from scripts.file_loader import load_file

    target = tmp_path / "artefact.md"
    target.write_text(case["content"], encoding="utf-8")
    result = load_file(str(target), expect_prefix=case["prefix"])

    accepted = result["status"] == "OK"
    assert accepted == case["expected"], (
        f"{case['name']}: load_file said {result['status']}, the shared fixture "
        f"says accepted={case['expected']} — diagnostic: {result['diagnostic']}"
    )


def test_the_transport_side_exists_and_is_the_one_the_generator_inlines():
    """The JS half is a real module, not a string literal inside the generator.

    A prefix rule written as a literal in `js_blocks.py` cannot be executed by a
    test; the only available assertion would be a grep over the generated file,
    which is the shape Round 33 站1's second counter-proof caught (a docstring
    scan that went green when the sentence was reflowed). `node --test` runs
    the module; `render_load_file_via_python()` inlines the same file; the
    golden byte-equal tests pin that the generated workflows carry it.
    """
    assert _MJS.is_file(), (
        f"{_MJS.relative_to(_REPO)} is missing — the JS-side anchor rule has no "
        "executable source, so anchor_check.test.mjs cannot run it"
    )
    from scripts.workflowgen import js_blocks

    rendered = js_blocks.render_load_file_via_python()
    assert "firstLineHasAnchor" in rendered, (
        "render_load_file_via_python() no longer inlines the shared anchor "
        "check; the transport layer has its own copy of the rule again"
    )
    assert "anchorRe" not in rendered, (
        "the multiline `anchorRe` implementation is back in the generated "
        "loader — it accepts an anchor on any line, which is the defect this "
        "module replaced"
    )
