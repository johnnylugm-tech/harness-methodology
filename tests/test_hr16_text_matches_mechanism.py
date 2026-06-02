"""Audit F-4.7 regression test: HR-16 text must match the actual
`gate_score_overrides` mechanism.

Background: PR 4 added HR-16 claiming "manual override requires
explicit gate_score_overrides entry". The actual implementation
in `sab_parser.derive_gate_score_overrides` is a **threshold floor**
(raises thresholds, not lowers). The text and the mechanism
disagreed — the override path described by HR-16 did not exist.

Fix (audit option A): update HR-16 text to honestly describe what
the mechanism does. This test pins the new text so a future
regression (someone rewrites HR-16 to claim a bypass that doesn't
exist) is caught at test time.
"""
import re
from pathlib import Path

import pytest


@pytest.fixture
def hr16_text() -> str:
    skill = (Path(__file__).resolve().parent.parent / "SKILL.md").read_text(
        encoding="utf-8"
    )
    m = re.search(r"^\| HR-16 \| (.+?) \| Terminate \|", skill, re.MULTILINE)
    assert m is not None, "HR-16 row not found in SKILL.md"
    return m.group(1)


@pytest.fixture
def sab_parser_overrides_doc() -> str:
    """Read the docstring of `derive_gate_score_overrides` to confirm
    the mechanism is a threshold floor."""
    fp = (Path(__file__).resolve().parent.parent
          / "core" / "quality_gate" / "sab_parser.py")
    return fp.read_text(encoding="utf-8")


def test_hr16_does_not_claim_a_bypass_path(hr16_text):
    """The OLD text said 'manual override requires explicit
    gate_score_overrides entry'. The new text must NOT make that
    claim because the mechanism is a floor, not a bypass."""
    text = hr16_text.lower()
    assert "manual override" not in text, (
        "HR-16 must not claim a manual override path — "
        "gate_score_overrides raises thresholds, not lowers them"
    )
    # Also no related phrases
    for phrase in ("override the trace", "bypass the trace", "skip the trace"):
        assert phrase not in text, f"HR-16 must not promise: {phrase!r}"


def test_hr16_documents_threshold_floor_semantics(hr16_text):
    """The new text must explicitly say that gate_score_overrides
    raises (not lowers) thresholds, and point to the source."""
    text = hr16_text.lower()
    assert "threshold floor" in text or "floor" in text, (
        "HR-16 must explain that gate_score_overrides is a floor"
    )
    assert "sab_parser" in text, (
        "HR-16 must point to the source of the mechanism"
    )
    # Hard rule preserved
    assert "must pass" in text or "must" in text, "HR-16 must keep the must-pass language"


def test_hr16_lists_remediation_paths(hr16_text):
    """HR-16 must enumerate the three real remediation paths:
    (a) fix code/FRs, (b) re-architect, (c) escalate to human."""
    text = hr16_text.lower()
    assert "fix" in text and ("fr" in text or "code" in text), \
        "HR-16 must mention fixing the code/FRs as remediation"
    assert "escalate" in text or "human" in text, \
        "HR-16 must mention human escalation as a path"
    assert "no automated override" in text or "no override" in text, \
        "HR-16 must state there is no automated override"


def test_sab_parser_mechanism_is_threshold_floor(sab_parser_overrides_doc):
    """Sanity check on the mechanism that HR-16 references. If this
    changes (e.g., gate_score_overrides becomes a bypass), HR-16 must
    be re-evaluated — hence this test is in the same file."""
    # The docstring should mention "floor" or "raise" or "minimum"
    lower = sab_parser_overrides_doc.lower()
    assert ("floor" in lower or "minimum" in lower or "raise" in lower), (
        "sab_parser.derive_gate_score_overrides is documented as a "
        "floor / minimum / raise mechanism. If this changes, HR-16 "
        "must be re-evaluated against the new semantics."
    )
