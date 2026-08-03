"""Single source of truth for framework deliverable filenames.

Two prior hardcoded copies of this list (with concrete inconsistencies) lived
in:

  * ``harness_cli._PHASE_DELIVERABLES`` — P1/2/6 only, used as Agent B
    approval file keys.
  * ``core.quality_gate.artifact_consistency._LEGAL_ARTIFACTS`` — forward-ref
    whitelist for ``NN-stage/FILE.md`` references in P1/P2 artifacts.

The duplication caused an agent hallucination slip-through in the 2026-07-09
P1 replay run: the workflow JS's SPEC_TRACKING sub-task prompt had no whitelist
of legal per-stage filenames, the agent invented ``02-architecture/ARCHITECTURE.md``
(mapping ``Phase 2 → Architecture Design`` from ``phase1_plan.md`` to a
non-existent filename), and the ``check_forward_refs`` gate correctly
blocked the post-advance push.  Both call sites now import from here, so
adding a new deliverable means editing exactly one file.

Inconsistencies observed before this consolidation (audit evidence):

  * P1: ``harness_cli._PHASE_DELIVERABLES`` and ``artifact_consistency`` agree
    (4 docs, including ``TEST_INVENTORY.yaml``); ``project_layout._PHASE_PROP_MAP``
    omits ``TEST_INVENTORY.yaml`` (3 docs only).
  * P2: ``harness_cli._PHASE_DELIVERABLES`` and ``artifact_consistency`` agree
    (3 docs: ``SAD.md``, ``ADR.md``, ``TEST_SPEC.md``); ``project_layout._PHASE_PROP_MAP``
    lists only ``SAD.md``.
  * P6: ``harness_cli._PHASE_DELIVERABLES`` has 4 entries
    (``QUALITY_REPORT.md``, ``RELEASE_NOTES.md``, ``FINAL_SIGN_OFF.md``,
    ``quality_manifest``); ``artifact_consistency`` has 3 (the
    ``quality_manifest`` entry is an internal JSON in ``.methodology/``, not
    a forward-ref target).

This module is the authoritative list for both runtime gates.
"""

from __future__ import annotations

__all__ = ["LEGAL_ARTIFACTS", "PHASE_DELIVERABLES", "DELIVERABLE_ANCHORS", "anchor_for"]


# Forward-reference whitelist, keyed by stage directory.
#
# Used by ``check_forward_refs`` to validate any ``NN-stage/FILE.md``
# reference in a P1/P2 artifact. Catches invented filenames (e.g. an agent
# writing ``02-architecture/ARCHITECTURE.md`` when the real P2 deliverable
# is ``SAD.md``).
LEGAL_ARTIFACTS: dict[str, set[str]] = {
    "01-requirements": {"SRS.md", "SPEC_TRACKING.md", "TRACEABILITY_MATRIX.md", "TEST_INVENTORY.yaml"},
    "02-architecture": {"SAD.md", "ADR.md", "TEST_SPEC.md"},
    "04-testing": {"TEST_PLAN.md", "TEST_RESULTS.md"},
    "05-verification": {"BASELINE.md", "VERIFICATION_REPORT.md"},
    "06-quality": {"QUALITY_REPORT.md", "RELEASE_NOTES.md", "FINAL_SIGN_OFF.md"},
    "07-risk": {"RISK_REGISTER.md", "RISK_MITIGATION_PLANS.md", "RISK_STATUS_REPORT.md"},
    "08-config": {"CONFIG_RECORDS.md", "RELEASE_CHECKLIST.md"},
}


# Agent B approval file keys, keyed by phase number.
#
# P1/2/6 only — per-FR approval is only meaningful from P3 onwards (the FR
# registry itself is populated at P3).  ``quality_manifest`` is the internal
# JSON in ``.methodology/`` (not a forward-ref target, so it appears only
# here, not in ``LEGAL_ARTIFACTS``).
PHASE_DELIVERABLES: dict[int, list[str]] = {
    1: ["SRS.md", "SPEC_TRACKING.md", "TRACEABILITY_MATRIX.md", "TEST_INVENTORY.yaml"],
    2: ["SAD.md", "ADR.md", "TEST_SPEC.md"],
    6: ["QUALITY_REPORT.md", "RELEASE_NOTES.md", "FINAL_SIGN_OFF.md", "quality_manifest"],
}


# Round 33 站1 — the H1 anchor each deliverable must carry, keyed by the
# project-relative path the orchestrator loads.
#
# The Phase 1 and Phase 2 orchestrators reload every deliverable through
# ``loadFileViaPython(diskPath, diskPrefix, ...)`` -> ``read-file
# --expect-prefix`` -> ``scripts/file_loader.py``'s
# ``first_line.startswith(expect_prefix)``. That one rule used to be written
# down six times — the implementation, the implementation's own docstring
# (which said "contain"), the file_loader test's docstring (which said
# "substring"), the ``diskPrefix`` literal three times per deliverable inside
# its spec renderer, the template's H1, and the Phase 1 prompt prose (which
# told the agent any H1 *containing* the phrase would do). Three of those six
# were wrong at the same time, and the one the agent reads was one of them.
#
# Measured consequence, Round 28 站2 follow-up: templates/SAD.md shipped
# ``# SAD - {Project Name}``, Agent A filled a 520-line body without touching
# the H1, and every orchestrator reload returned PREFIX_MISMATCH until the run
# aborted with LOADER_FAILED_AFTER_3_ATTEMPTS. That was fixed per-site; four
# of the other six deliverables were still broken the same way
# (SRS.md / SPEC_TRACKING.md / TRACEABILITY_MATRIX.md / ADR.md), which is what
# a per-site fix leaves behind.
#
# Same shape, and the same reason, as sab_parser.nfr_type_vocabulary_inline():
# a value the prompt states and a gate enforces belongs in one place, and the
# prompt interpolates it rather than restating it.
DELIVERABLE_ANCHORS: dict[str, str] = {
    "01-requirements/SRS.md": "# Software Requirements Specification",
    "01-requirements/SPEC_TRACKING.md": "# Specification Tracking Matrix",
    "01-requirements/TRACEABILITY_MATRIX.md": "# Traceability Matrix",
    "TEST_INVENTORY.yaml": "# TEST_INVENTORY.yaml",
    "02-architecture/SAD.md": "# Software Architecture Document",
    "02-architecture/adr/ADR.md": "# Architecture Decision Records",
    "02-architecture/TEST_SPEC.md": "# TEST_SPEC.md",
}


def anchor_for(deliverable: str) -> str:
    """The H1 anchor for a deliverable, by project-relative path or basename.

    Raises KeyError rather than returning "" for an unknown name: an empty
    prefix disables the loader's check entirely (`file_loader` treats a falsy
    ``expect_prefix`` as "no anchor"), so a typo that silently degraded to
    "check nothing" is exactly the failure this registry exists to stop.
    """
    if deliverable in DELIVERABLE_ANCHORS:
        return DELIVERABLE_ANCHORS[deliverable]
    tail = deliverable.rsplit("/", 1)[-1]
    for path, anchor in DELIVERABLE_ANCHORS.items():
        if path.rsplit("/", 1)[-1] == tail:
            return anchor
    raise KeyError(
        f"no H1 anchor registered for {deliverable!r}; add it to "
        "DELIVERABLE_ANCHORS rather than hand-writing the prefix at the "
        "call site"
    )
