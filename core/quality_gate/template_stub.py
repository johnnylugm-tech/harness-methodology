"""Is a deliverable still the framework template it was copied from?

The one definition the live path reads (B-review stub synthesis,
`load-context`'s stub warning). Kept out of `cross_artifact.py`, which owns
`_template_placeholders` but sits at the god-file ratchet.
"""

from core.quality_gate.constitution.runner import (
    _STUB_PLACEHOLDER_RE,
    _TEMPLATE_STUB_SENTINEL,
)
from core.quality_gate.cross_artifact import _template_placeholders


def is_unfilled_template(content: str, basename: "str | None" = None) -> bool:
    """True iff *content* is still the framework template it was copied from.

    Stub = the `<!-- harness:template-stub -->` sentinel, or >=8 occurrences
    of placeholders that `templates/<basename>` itself ships. Counting every
    `{word}` instead (`runner._is_stub_template`) reads a filled SRS.md with
    14 × `/v1/tasks/{id}` as a stub — taskq-open P1 halted at HR-12 twice on
    exactly that. Without a template to compare against, only the sentinel
    can mark a stub.
    """
    if _TEMPLATE_STUB_SENTINEL in content:
        return True
    if not basename:
        return False
    expected = _template_placeholders(basename)
    if not expected:
        return False
    left = [p for p in _STUB_PLACEHOLDER_RE.findall(content) if p in expected]
    return len(left) >= 8
