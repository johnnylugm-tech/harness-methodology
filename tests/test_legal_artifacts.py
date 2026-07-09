"""Tests for the single source of truth: ``core.quality_gate.legal_artifacts``.

The 2026-07-09 P1 replay run surfaced a real DRY violation: the same
deliverable-filename list was hardcoded in two places
(``harness_cli._PHASE_DELIVERABLES`` and
``artifact_consistency._LEGAL_ARTIFACTS``) with concrete inconsistencies
(P1 omitted ``TEST_INVENTORY.yaml`` in one copy; P2 omitted ``ADR.md`` and
``TEST_SPEC.md`` in another; P6 had ``quality_manifest`` in one but not the
other). This module is the single source of truth; these tests pin its
shape and verify the import surface used by both call sites.
"""

from __future__ import annotations

import importlib


def test_legal_artifacts_exports() -> None:
    mod = importlib.import_module("core.quality_gate.legal_artifacts")
    assert mod.LEGAL_ARTIFACTS == mod.LEGAL_ARTIFACTS  # sanity
    assert mod.PHASE_DELIVERABLES == mod.PHASE_DELIVERABLES  # sanity
    assert set(mod.__all__) == {"LEGAL_ARTIFACTS", "PHASE_DELIVERABLES"}


def test_legal_artifacts_has_p1_p2_p4_to_p8() -> None:
    """P1, P2, P4, P5, P6, P7, P8 — phases with named framework deliverables.
    P3 (implementation, src/tests) and P9 (maintenance) have no document artifacts.
    """
    mod = importlib.import_module("core.quality_gate.legal_artifacts")
    expected_stages = {
        "01-requirements",
        "02-architecture",
        "04-testing",
        "05-verification",
        "06-quality",
        "07-risk",
        "08-config",
    }
    assert set(mod.LEGAL_ARTIFACTS) == expected_stages


def test_p1_includes_test_inventory() -> None:
    """TEST_INVENTORY.yaml is a real P1 deliverable per harness_cli and per
    agent_b_approvals/*.  Pin it explicitly so a future edit cannot silently
    drop it (the previous project_layout copy omitted it — audit evidence)."""
    mod = importlib.import_module("core.quality_gate.legal_artifacts")
    assert "TEST_INVENTORY.yaml" in mod.LEGAL_ARTIFACTS["01-requirements"]


def test_p2_includes_sad_adr_test_spec() -> None:
    """P2 has 3 deliverables: SAD.md, ADR.md, TEST_SPEC.md. The previous
    project_layout copy listed only SAD.md — pin all 3 here."""
    mod = importlib.import_module("core.quality_gate.legal_artifacts")
    assert mod.LEGAL_ARTIFACTS["02-architecture"] == {"SAD.md", "ADR.md", "TEST_SPEC.md"}


def test_phase_deliverables_keys_are_p1_p2_p6() -> None:
    """P1/2/6 only — per-FR approval is only meaningful from P3 onwards."""
    mod = importlib.import_module("core.quality_gate.legal_artifacts")
    assert set(mod.PHASE_DELIVERABLES) == {1, 2, 6}


def test_phase_deliverables_p6_includes_quality_manifest() -> None:
    """P6 Agent B approval file keys include the internal 'quality_manifest'
    JSON in .methodology/ (not a forward-ref target, so it's only here, not
    in LEGAL_ARTIFACTS). Pin so the asymmetry is intentional, not accidental."""
    mod = importlib.import_module("core.quality_gate.legal_artifacts")
    assert "quality_manifest" in mod.PHASE_DELIVERABLES[6]


def test_harness_cli_re_exports_phase_deliverables() -> None:
    """harness_cli._PHASE_DELIVERABLES is a backward-compat alias. The
    tests in test_harness_cli.py import it directly; if the alias is removed
    these tests fail at import time."""
    import sys
    # Inject a dummy arg so the argparse parser at the bottom of harness_cli
    # does not SystemExit before we read the alias.
    sys.argv = ["harness_cli", "status"]
    sys.path.insert(0, ".")
    hc = importlib.import_module("harness_cli")
    assert hc._PHASE_DELIVERABLES is importlib.import_module(
        "core.quality_gate.legal_artifacts"
    ).PHASE_DELIVERABLES
