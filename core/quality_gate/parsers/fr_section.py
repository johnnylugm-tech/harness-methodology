"""One FR's own markdown section, out of a document that has many.

Moved here from `cli/fr_prompts/_shared._extract_srs_fr_section` in Round 87
站5, when `core.quality_gate.criteria_review` needed it and `core/` may not
import `cli/` (tests/test_cli_layering.py). The regex and its boundary rule
are unchanged — `cli.fr_prompts._shared` now delegates, so every FR prompt
renders the same bytes it did before the move.

Written for SRS.md and used for SPEC.md too. Measured across thirteen corpus
projects before that second use: every FR excerpt is bounded, the taskq family
at 368-687 characters and omnibot-new (a 275KB SPEC with 34 FRs) at a median
of 5,917 and a maximum of 14,057. The `\\n---\\n` boundary is what stops the
last FR in a document from swallowing everything after it.
"""
from __future__ import annotations

import re
from pathlib import Path

__all__ = ["extract_fr_section"]


def extract_fr_section(doc_path: "Path | None", fr_id: str) -> str:
    """Extract a single FR's full markdown section from a requirements doc.

    Returns text between '### FR-XX: ...' header and the next '### FR-' or
    '---'. Falls back to empty string if the section is not found.
    """
    if not doc_path or not doc_path.exists():
        return ""
    content = doc_path.read_text(encoding="utf-8")
    pat = re.compile(
        rf"(### {re.escape(fr_id)}:[^\n]+\n)(.*?)(?=\n---\n|\n### FR-\d+|$)",
        re.DOTALL,
    )
    m = pat.search(content)
    return (m.group(1) + m.group(2)).strip() if m else ""
