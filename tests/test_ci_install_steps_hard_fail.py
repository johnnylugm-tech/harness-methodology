"""A dependency install that fails must stop the job (Round 37 站0/站4).

Measured, taskq-renew 2026-08-05: every CI job's "Install harness
dependencies" step ran `pip install -r harness/requirements.txt || true`.
When 1b89c28 pinned code-review-graph into requirements.txt, pip hit a
ResolutionImpossible and installed *nothing* — pyyaml included — and `|| true`
let the job continue. The failure then surfaced three steps later as
`ModuleNotFoundError: No module named 'yaml'` inside sab_parser.py, i.e. an
INFRA failure wearing the face of a content failure. 436604b's own commit
message records this chain.

docs/ERROR_HANDLING.md and core/failure_modes.py already forbid routing an
infrastructure failure into the code-fix path. This pins the same rule in the
one place that was still swallowing: the shipped CI template.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent

# The template shipped into every consumer project, and the harness's own CI.
_CI_FILES = (
    "templates/harness_quality_gate.yml",
    ".github/workflows/harness_ci.yml",
)

_PIP_SWALLOW = re.compile(r"pip install\b[^\n]*\|\|\s*true")


@pytest.mark.parametrize("rel", _CI_FILES)
def test_no_pip_install_swallows_its_own_failure(rel: str) -> None:
    text = (REPO / rel).read_text(encoding="utf-8")
    offenders = [
        f"  line {i}: {line.strip()}"
        for i, line in enumerate(text.splitlines(), 1)
        if _PIP_SWALLOW.search(line)
    ]
    assert not offenders, (
        f"{rel} lets a failed dependency install continue; the job then fails "
        f"later as a content error instead of an infra error:\n"
        + "\n".join(offenders)
    )


def test_the_consumer_template_and_the_shipped_copy_are_one_file() -> None:
    """Negative control: the rule above is only worth anything if the file
    tested is the file consumers get. taskq-renew's copy is byte-identical to
    templates/harness_quality_gate.yml, so the template is the SSOT — this
    pins that the template still carries the job names CI actually runs."""
    text = (REPO / "templates/harness_quality_gate.yml").read_text(encoding="utf-8")
    for job in ("gate-check", "ASPICE Traceability Check", "CRG Architecture Gate"):
        assert job in text, f"{job!r} is no longer in the shipped CI template"
