"""A removed mechanism must not survive in what the framework says or reads.

Round 39 站0. Round 38 站3 removed the DA score-threshold waiver: the set of
waivable dimensions is empty, `adjudicate_waivers` is gone, `finalize_gate` no
longer takes a `da_waivers` argument, and a request is refused at collection.
The code was right. Six places that *talk* were not:

    scripts/workflowgen/spec_phase4.py   "complete DA challenge + set da_waiver"
    scripts/workflowgen/spec_phase6.py   'also add "da_waiver": {"architecture": true}'
    scripts/plangen/blocks.py:1426       "add `da_waiver` … to bypass the"
    scripts/plangen/blocks.py:1599       "set devil_advocate + da_waiver + …"
    harness/ssi/prompts/evaluate_dimension.md   the whole waiver section
    docs/ERROR_HANDLING.md               a block-reason row for a removed key

Four of those reach a shipped workflow (`phase4-testing.js`, `phase6-quality.js`,
`run-all.js` ×2), which means the framework was telling the agent to do
something the framework itself now blocks — Round 17's prompt↔gate drift,
reproduced by the very round that was closing a different instance of it.

And one consumer outlived its producer: `scripts/generate_quality_report.py`
still read `quality_manifest…gate_results.gateN.da_waiver_applied`, a field
Round 38 站3 stopped writing. That is Round 30's zombie shape — a reader with
nothing to read.

The registry below is the reusable part. Removing a mechanism is not a
one-file edit, and the next removal deserves somewhere to declare itself
rather than a fresh grep.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import NamedTuple

import pytest

pytestmark = [pytest.mark.core]

REPO = Path(__file__).resolve().parent.parent


class RemovedMechanism(NamedTuple):
    name: str
    removed_in: str
    # Text that would advise an agent to use the removed mechanism.
    advises: re.Pattern
    # Artifact fields the mechanism used to write; nothing may read them.
    orphan_fields: tuple[str, ...]
    # Where the replacement is documented, quoted in the failure message.
    instead: str


REMOVED_MECHANISMS: tuple[RemovedMechanism, ...] = (
    RemovedMechanism(
        name="da_waiver (DA score-threshold waiver)",
        removed_in="Round 38 站3",
        # Two shapes of advice, because the framework used both:
        #   a verb form  — "complete the DA challenge AND add da_waiver"
        #   a shape form — `"da_waiver": {...}` inside a JSON block the agent
        #                  is told to write. Showing a field in the template is
        #                  the strongest instruction there is; the verb-only
        #                  first draft of this pattern missed
        #                  evaluate_dimension.md's whole example block.
        advises=re.compile(
            r"(?:(?:set|add|complete)[^.\n]{0,80}\bda_waiver\b)"
            r"|(?:\"da_waiver\"\s*:)",
            re.IGNORECASE),
        orphan_fields=("da_waiver_applied", "da_waiver_needs_human_review"),
        instead=(
            "core/quality_gate/block_reason.py::DIMENSION_HINTS['architecture'] — "
            "fix the architecture, or calibrate crg_excludes / "
            "crg_cohesion_healthy in the committed harness_config.json"
        ),
    ),
)

_EXPECTED_MECHANISMS = {"da_waiver (DA score-threshold waiver)"}

# Files whose contents become instructions to an agent, or are read as data.
# `.claude/workflows` is included deliberately: it is the shipped artifact, and
# Round 36 spent a round learning that verifying the generator is not the same
# as verifying what ships.
_INSTRUCTION_ROOTS = (
    REPO / "scripts" / "workflowgen",
    REPO / "scripts" / "plangen",
    REPO / "harness" / "ssi" / "prompts",
    REPO / ".claude" / "workflows",
)
_CODE_ROOTS = (REPO / "core", REPO / "cli", REPO / "harness", REPO / "scripts")

_SUFFIXES = {".py", ".md", ".js"}


def _files(roots) -> list[Path]:
    out: list[Path] = []
    for root in roots:
        if not root.exists():
            continue
        out.extend(
            p for p in root.rglob("*")
            if p.is_file() and p.suffix in _SUFFIXES
            and "__pycache__" not in p.parts
        )
    return sorted(out)


def test_the_registry_is_pinned() -> None:
    """Adding a removal must be a deliberate edit here, not an accident."""
    assert {m.name for m in REMOVED_MECHANISMS} == _EXPECTED_MECHANISMS


@pytest.mark.parametrize("mech", REMOVED_MECHANISMS, ids=lambda m: m.name)
def test_no_instruction_advises_a_removed_mechanism(mech: RemovedMechanism) -> None:
    """The framework must not tell an agent to do what the framework blocks."""
    offenders: list[str] = []
    for path in _files(_INSTRUCTION_ROOTS):
        for lineno, line in enumerate(
            path.read_text(encoding="utf-8", errors="replace").splitlines(), 1
        ):
            if mech.advises.search(line):
                offenders.append(f"{path.relative_to(REPO)}:{lineno}")
    assert not offenders, (
        f"{mech.name} was removed in {mech.removed_in}, but these still advise "
        f"an agent to use it:\n  " + "\n  ".join(offenders)
        + f"\nSay this instead: {mech.instead}"
    )


@pytest.mark.parametrize("mech", REMOVED_MECHANISMS, ids=lambda m: m.name)
def test_no_consumer_reads_a_field_nothing_writes(mech: RemovedMechanism) -> None:
    """A reader with no writer is a mechanism that looks alive from one side.

    Scoped to production code: the tests that pin the *absence* of these
    fields legitimately name them.
    """
    offenders: list[str] = []
    for path in _files(_CODE_ROOTS):
        if path.suffix != ".py":
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for field in mech.orphan_fields:
            for lineno, line in enumerate(text.splitlines(), 1):
                if field in line and not line.lstrip().startswith("#"):
                    offenders.append(f"{path.relative_to(REPO)}:{lineno} ({field})")
    assert not offenders, (
        f"{mech.name} stopped writing these fields in {mech.removed_in}, but "
        f"production code still reads them:\n  " + "\n  ".join(offenders)
    )


def test_the_scan_would_catch_a_reintroduced_instruction() -> None:
    """Negative control — a checker that cannot fire is not a checker."""
    mech = REMOVED_MECHANISMS[0]
    revived = 'prompt += "If architecture fails, set da_waiver to true"\n'
    assert mech.advises.search(revived)


def test_the_scan_does_not_fire_on_a_mere_mention() -> None:
    """A sentence explaining that the mechanism is gone must not trip the rule.

    Without this, the honest fix — writing down why a waiver no longer works —
    would read as the defect, and the next reader would delete the explanation
    to get the suite green. Round 38 站2 hit exactly that with its own comment.
    """
    mech = REMOVED_MECHANISMS[0]
    for benign in (
        "# Round 38 站3 removed da_waiver; calibrate instead.",
        "no da_waiver route exists any more",
        "the da_waiver key is refused at collection",
    ):
        assert not mech.advises.search(benign), benign
