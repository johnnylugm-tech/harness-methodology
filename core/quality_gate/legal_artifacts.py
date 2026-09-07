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

from pathlib import Path

__all__ = ["LEGAL_ARTIFACTS", "PHASE_DELIVERABLES", "DELIVERABLE_ANCHORS", "anchor_for",
           "SAB_TEMPLATE_EXAMPLE_VALUES", "TEMPLATE_EXAMPLE_MARKER",
           "TEMPLATE_EXAMPLE_VALUES", "TEMPLATE_EXAMPLE_TEST_NAMES",
           "framework_examples_in", "sab_template_module_paths"]


# ── the inverse of DELIVERABLE_ANCHORS ──────────────────────────────────────
# That registry names the text a template ships and the project must KEEP.
# This one names the text a template ships and the project must REPLACE.
#
# Round 105 站3. Both registries exist for the same reason: this framework
# writes files into the tree it later judges, and every value it invents in
# one of them is an answer the project was supposed to give.
#
# CORRECTED, Round 106. Round 105 wrote here that marking was the only
# variable — "the marked SAB layers example leaked into 0 of 13 projects" —
# and that measurement was wrong: it grepped `app.api.routes`, and the
# template emits `app.api.webhooks`. What the corpus actually shows:
#
#     SAB layers example (4 module paths)   MARKED    1 / 13  taskq-forever
#     SAB quality_targets.min_coverage: 80  unmarked  2 / 13
#     TEST_INVENTORY `*_example_*` names    unmarked  3 / 13
#
# taskq-forever's SAD.md carries the marker line verbatim with
# `app.api.webhooks` under it. Marking reduces the leak; it does not stop it,
# and `sab_template_module_paths()` below now derives the literals from the
# template instead of restating them, because restating them is what produced
# the wrong number.
#
# `min_coverage` is not decoration: `advance_checks._check_gate1_live_coverage`
# reads it through `min_coverage_floor`, so taskq-sn's Gate 1 live-coverage
# check ran at 80% while its own SPEC.md required TOTAL 100%.
#
# The marker is checked at the TEMPLATE end only. "The delivered file still
# says 80" is not a defect — a project that considered the question and chose
# 80 must not be accused of inheriting it, and nothing here can tell those two
# apart (Round 46). The delivered end is `framework_examples_in`, which asks a
# narrower question that IS decidable.
TEMPLATE_EXAMPLE_MARKER = "EXAMPLE — replace"

#: The invented values in the SAB block, which `sab_parser` renders and
#: `templates/SAD.md` / `docs/P2_SOP.md` hand-copy. Listed once here so the
#: generator and both copies are held to the same rule.
SAB_TEMPLATE_EXAMPLE_VALUES: tuple[str, ...] = (
    "max_complexity: 15",
    "min_coverage: 80",
    "max_coupling: 0.3",
)

#: Repo-relative file -> the values in it this framework invented. Every line
#: containing one must carry TEMPLATE_EXAMPLE_MARKER.
TEMPLATE_EXAMPLE_VALUES: dict[str, tuple[str, ...]] = {
    # Copied into every new project's root by cli/project_cmds.py.
    "templates/TEST_INVENTORY.yaml": (
        "test_fr01_example_integration",
        "test_fr01_example_unit",
        "test_security_example",
        "test_deployment_example",
    ),
    "templates/SAD.md": SAB_TEMPLATE_EXAMPLE_VALUES,
    "docs/P2_SOP.md": SAB_TEMPLATE_EXAMPLE_VALUES,
}


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


# ── what the project must have replaced, at the delivered end ───────────────
# Round 106 站A. Round 105 marked the framework's example values and stopped
# there, on a measurement that said marking was the only variable: the marked
# SAB `layers` example had leaked into 0 of 13 corpus projects. That
# measurement grepped `app.api.routes`, and the template's literal is
# `app.api.webhooks`. Re-measured against what the template actually emits,
# taskq-forever shipped all four of its module paths WITH the marker line
# still above them. Marking reduces the leak; it does not stop it.
#
# Three candidate rules were measured and two are false accusations:
#
#   "the marker is still there"   taskq-renew keeps the marker line and lists
#                                 its own `taskq_plus.cli.main` under it.
#   "the value is still 80"       a project may consider it and choose 80.
#
# What is left is two rules that are each decidable on their own terms, and
# `framework_examples_in` is where they live so the rule and the registry
# cannot drift apart.

#: Rule 1. Identifiers this framework invented that say so in their own name.
#: No project names a test `test_security_example`, so one in a delivered
#: artifact is the template's row rather than the project's.
TEMPLATE_EXAMPLE_TEST_NAMES: tuple[str, ...] = (
    "test_fr01_example_integration",
    "test_fr01_example_unit",
    "test_security_example",
    "test_deployment_example",
)

#: The delivered artifacts each phase is answerable for. Read once, at the
#: boundary that closes the phase which wrote them — asking every phase about
#: every artifact is Round 20.
_EXAMPLE_SCOPE: dict[int, tuple[str, ...]] = {
    1: ("TEST_INVENTORY.yaml",),
    2: ("02-architecture/SAD.md", "02-architecture/TEST_SPEC.md",
        ".methodology/SAB.json"),
}


def sab_template_module_paths() -> tuple[str, ...]:
    """The module paths the SAB template hands a project, read from the template.

    Rule 2's subject. Derived rather than listed: Round 105 hand-wrote
    `app.api.routes`, measured zero leaks with it, and concluded that marking
    worked. The template says `app.api.webhooks`. A literal beside the
    generator is a second statement of the generator's own content, and this
    is what that costs.

    Parsed out of the rendered block's YAML rather than scraped with a regex,
    because the prose in it contains dotted tokens too (`e.g`, `env.example`,
    `nfr_traceability.type`) and a regex would have to decide which of those
    is a module — which is the same guess in a different hat.

    Imported lazily: `sab_parser` imports this module for
    ``TEMPLATE_EXAMPLE_MARKER``, so a module-level import here is a cycle.
    """
    import yaml

    from core.quality_gate.sab_parser import render_canonical_sab_template

    block = render_canonical_sab_template()
    data = yaml.safe_load("\n".join(
        ln for ln in block.splitlines() if not ln.lstrip().startswith("#")))
    sab = (data or {}).get("sab", data) or {}

    found: set[str] = set()
    for layer in sab.get("layers") or []:
        for mod in (layer or {}).get("modules") or []:
            if isinstance(mod, str):
                found.add(mod)
            elif isinstance(mod, dict):
                found.update(str(mod[k]) for k in ("name", "implemented_in")
                             if mod.get(k))
    for entry in (sab.get("nfr_traceability") or {}).values():
        if isinstance(entry, dict) and entry.get("module"):
            found.add(str(entry["module"]))
    for value in (sab.get("fr_module_traceability") or {}).values():
        if isinstance(value, str):
            found.add(value)
    return tuple(sorted(found))


def _delivered_root_packages(project: Path) -> "set[str]":
    """Top-level package names the project actually ships.

    Rule 2 rests on this and nothing else. `app.main` is the most common
    module path in any FastAPI project; a project whose package IS `app` owns
    that name and must never be charged with copying the template (Round 46).
    A project that delivers no package at all — taskq-forever — owns none of
    them, which is exactly the state rule 2 is for.
    """
    from core.utils.delivery_scope import iter_delivered_files

    roots: set[str] = set()
    for path in iter_delivered_files(project):
        if path.suffix != ".py":
            continue
        parts = path.relative_to(project).parts
        cur = project
        for segment in parts[:-1]:
            cur = cur / segment
            if (cur / "__init__.py").is_file():
                roots.add(segment)
    return roots


def framework_examples_in(project: Path, phase: int) -> list[str]:
    """`file:line  identifier  (rule)` for every framework example still there.

    Empty when the phase owns no artifact, when the artifacts are absent, or
    when the project replaced them — all three are the same answer to the
    caller and none of them is a finding.
    """
    project = Path(project)
    scope = _EXAMPLE_SCOPE.get(phase, ())
    if not scope:
        return []

    module_paths = sab_template_module_paths()
    roots = _delivered_root_packages(project) if module_paths else set()
    unowned = tuple(p for p in module_paths if p.split(".")[0] not in roots)

    found: list[str] = []
    for rel in scope:
        path = project / rel
        if not path.is_file():
            continue
        for lineno, line in enumerate(
                path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            for name in TEMPLATE_EXAMPLE_TEST_NAMES:
                if name in line:
                    found.append(f"{rel}:{lineno}  {name}  "
                                 f"(the framework's example test name)")
            for module in unowned:
                if module in line:
                    found.append(f"{rel}:{lineno}  {module}  "
                                 f"(the SAB template's example module; this "
                                 f"project delivers no `{module.split('.')[0]}` "
                                 f"package)")
    return found
