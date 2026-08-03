"""Round 33 站0/站1/站2 — one contract, five statements, none of them the source.

The Phase 1 and Phase 2 orchestrators reload every deliverable through
`loadFileViaPython(diskPath, diskPrefix, ...)`, which delegates to
`harness_cli.py read-file --expect-prefix`. `scripts/file_loader.py:178` checks
it with `first_line.startswith(expect_prefix)` — an anchor on the H1, not a
search of the file.

That one rule is currently written down in six places, and three of them are
wrong:

    scripts/file_loader.py:178   `first_line.startswith(...)`     the implementation
    scripts/file_loader.py:25    "first line doesn't CONTAIN"     WRONG
    tests/test_file_loader.py:12 "exact SUBSTRING match"          WRONG
    spec_phase{1,2}.py           the diskPrefix literal, 3x per deliverable
    templates/<X>.md             the H1 the agent starts from
    spec_phase1.py:607           "or any H1 line CONTAINING the phrase"  WRONG
                                 — prose that tells the agent to produce
                                   something the loader will reject

Round 28 站2's follow-up (1620b2c) fixed the fifth statement for SAD.md after
the P2 orchestrator aborted a live run with PREFIX_MISMATCH ->
LOADER_FAILED_AFTER_3_ATTEMPTS: templates/SAD.md shipped `# SAD - {Project Name}`
and Agent A filled the 520-line body without touching the H1. It fixed one
deliverable. Measured across all seven, four fail the same check:

    SRS.md                  # Software Requirements Specification  vs  # SRS - {Project Name}
    SPEC_TRACKING.md        # Specification Tracking Matrix        vs  # SPEC_TRACKING.md
    TRACEABILITY_MATRIX.md  # Traceability Matrix                  vs  # TRACEABILITY_MATRIX.md
    ADR.md                  # Architecture Decision Records        vs  # ADR-{ID}: {Title}

ADR.md is a Phase 2 template declared in the same spec file as SAD.md.

Separately, the framework's own regenerated view cannot satisfy the anchor it
declares for itself. `core/traceability/overlay.py::render_markdown` emits
`<!-- AUTO-GEN:START -->` BEFORE `# Traceability Matrix`, and
`scripts/build_traceability.py`'s `intro = head.rstrip() + "\\n\\n"` prepends two
newlines when there is nothing above the sentinel. Measured with the framework's
own loader on four real projects:

    01-requirements/TRACEABILITY_MATRIX.md  ->  PREFIX_MISMATCH   (4 of 4)

Latent, not live: the only readers that pass that prefix are the Phase 1
sections of `phase1-requirements.js` and `run-all.js`, and Phase 1's Agent A
rewrites the file whole. It is still the Round 32 shape — the framework
producing what its own check refuses — so this file pins it.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = [pytest.mark.core]

REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = REPO_ROOT / "templates"


def _rendered_phase_js() -> str:
    """Every rendered Phase 1 + Phase 2 sub-task, concatenated.

    Read from the spec renderers rather than the generated `.claude/workflows/`
    files so this test pins the producer, not one of its outputs.
    """
    from scripts.workflowgen import spec_phase1, spec_phase2

    out = []
    for mod, prefix in ((spec_phase1, "_render_phase1_"), (spec_phase2, "_render_phase2_")):
        for name in dir(mod):
            if name.startswith(prefix):
                out.append(getattr(mod, name)())
    return "\n".join(out)


def _declared_anchors() -> "dict[str, str]":
    """{deliverable filename: diskPrefix} as the orchestrator declares it."""
    js = _rendered_phase_js()
    paths = re.findall(r"diskPath:\s*'([^']*)'", js)
    prefixes = re.findall(r"diskPrefix:\s*'([^']*)'", js)
    assert paths and len(paths) == len(prefixes), (
        f"diskPath/diskPrefix did not pair up: {len(paths)} vs {len(prefixes)}"
    )
    anchors: dict[str, str] = {}
    for p, pref in zip(paths, prefixes):
        anchors.setdefault(Path(p).name, pref)
    return anchors


def _first_line(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    return text.splitlines()[0] if text else ""


# ── the templates the agent starts from ─────────────────────────────────

def test_every_template_h1_satisfies_its_own_loader_anchor():
    """The measured failure, generalised from SAD.md to all seven.

    `cli/project_cmds.py::_init_copy_templates` seeds each of these into the
    project, and its PROTECTED rule (content != template means never
    overwrite) makes a template fix invisible to projects that already
    exist — so the template is the only place this can be got right before
    the run starts.
    """
    anchors = _declared_anchors()
    broken = []
    for name, prefix in sorted(anchors.items()):
        template = TEMPLATES_DIR / name
        if not template.is_file():
            continue  # not every diskPath has a seeded template
        first = _first_line(template)
        if not first.startswith(prefix):
            broken.append(f"{name}: H1 {first!r} does not start with {prefix!r}")
    assert not broken, (
        "template H1 does not satisfy the anchor the orchestrator will demand; "
        "every reload returns PREFIX_MISMATCH -> LOADER_FAILED_AFTER_3_ATTEMPTS "
        "(Round 28 站2, live on a P2 run):\n  " + "\n  ".join(broken)
    )


def test_every_seeded_template_keeps_a_project_name_placeholder():
    """A corrected H1 must still be templatable — Agent A substitutes
    `{Project Name}`, so an H1 with nothing to substitute is a different
    defect wearing the fix's clothes."""
    # Only the prose deliverables carry a project name; TEST_INVENTORY.yaml and
    # TEST_SPEC.md are catalogues whose H1 is the filename itself.
    named = ("SRS.md", "SPEC_TRACKING.md", "TRACEABILITY_MATRIX.md", "SAD.md", "ADR.md")
    missing = [
        n for n in named
        if (TEMPLATES_DIR / n).is_file()
        and "{" not in _first_line(TEMPLATES_DIR / n)
    ]
    assert not missing, (
        f"template H1 lost its substitutable placeholder: {missing} — Agent A "
        "would have nothing to replace, so every project ships the same title"
    )


# ── the anchor must have exactly one source ─────────────────────────────

def test_the_anchor_is_read_from_a_registry_not_hand_written_per_site():
    """Each anchor literal appears three times in its own spec file (sub-task
    cfg, approvedDocs list, and the prompt prose that names the prefix), plus
    once in the template, plus the loader's docstring, plus this repo's
    file_loader test docstring. Six statements of one rule; three were wrong
    at the same time. Bind them to one source, the way spec_phase1.py:13
    already binds the NFR type vocabulary."""
    from core.quality_gate import legal_artifacts

    assert hasattr(legal_artifacts, "DELIVERABLE_ANCHORS"), (
        "no single source for the H1 anchor: the diskPrefix literal is "
        "hand-written at every site, which is how templates/SAD.md and "
        "spec_phase2.py drifted apart without anything noticing"
    )
    anchors = legal_artifacts.DELIVERABLE_ANCHORS
    declared = _declared_anchors()
    for name, prefix in declared.items():
        key = next((k for k in anchors if Path(k).name == name), None)
        assert key is not None, f"{name} has a diskPrefix but no registry entry"
        assert anchors[key] == prefix, (
            f"{name}: registry says {anchors[key]!r}, the rendered orchestrator "
            f"says {prefix!r} — the registry is not the source"
        )


def test_the_loader_contract_is_described_as_an_anchor_everywhere_it_is_described():
    """`startswith` is not `contain`. The implementation's own docstring, this
    repo's file_loader test docstring, and the Phase 1 prompt all said
    "contain"/"substring" — and the prompt is read by the agent that writes
    the file, so the wrong wording actively produced non-conforming H1s."""
    offenders = []
    for rel in ("scripts/file_loader.py", "tests/test_file_loader.py"):
        text = (REPO_ROOT / rel).read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), 1):
            low = line.lower()
            if "expect_prefix" not in low and "expect-prefix" not in low:
                continue
            if "contain" in low or "substring" in low:
                offenders.append(f"{rel}:{lineno}: {line.strip()}")

    prompt = _rendered_phase_js()
    if "H1 line containing" in prompt:
        offenders.append(
            "spec_phase1.py: the Traceability Matrix prompt tells the agent any "
            "H1 line *containing* the phrase is acceptable"
        )

    assert not offenders, (
        "the loader anchors on the start of the first line; these say it "
        "matches a substring, and the last one says it to the agent that "
        "writes the file:\n  " + "\n  ".join(offenders)
    )


# ── the framework's own rendered view must clear its own bar ────────────

def test_the_regenerated_traceability_view_still_satisfies_its_anchor(tmp_path):
    """Measured PREFIX_MISMATCH on four real projects.

    Two causes, and fixing only the first is not enough — which is why this
    asserts on the rendered bytes rather than on the `intro` expression:

      1. `intro = head.rstrip() + "\\n\\n"` prepends two newlines when there is
         nothing above the sentinel, so the first line is empty.
      2. even with `intro` empty, `render_markdown` puts `AUTO-GEN:START`
         BEFORE the H1, so the first line is the sentinel.
    """
    from core.quality_gate.legal_artifacts import DELIVERABLE_ANCHORS
    from scripts.build_traceability import build_traceability, generate_markdown_matrix

    project = tmp_path / "proj"
    (project / "01-requirements").mkdir(parents=True)
    (project / ".methodology").mkdir()

    rt = build_traceability(project)
    out = project / "01-requirements" / "TRACEABILITY_MATRIX.md"
    generate_markdown_matrix(rt, out)

    key = next(k for k in DELIVERABLE_ANCHORS if Path(k).name == "TRACEABILITY_MATRIX.md")
    anchor = DELIVERABLE_ANCHORS[key]
    first = _first_line(out)
    assert first.startswith(anchor), (
        f"the framework regenerated this file and its first line is {first!r}, "
        f"which its own loader refuses against the anchor {anchor!r} it declares "
        "for the same path (measured PREFIX_MISMATCH on 4 of 4 real projects)"
    )


def test_regenerating_twice_does_not_duplicate_the_heading(tmp_path):
    """The H1 moves above the AUTO-GEN sentinel, and content above the sentinel
    is preserved verbatim on the next run — so the fix has to be idempotent or
    every advance-phase adds another heading."""
    from scripts.build_traceability import build_traceability, generate_markdown_matrix

    project = tmp_path / "proj"
    (project / "01-requirements").mkdir(parents=True)
    (project / ".methodology").mkdir()

    rt = build_traceability(project)
    out = project / "01-requirements" / "TRACEABILITY_MATRIX.md"
    generate_markdown_matrix(rt, out)
    once = out.read_text(encoding="utf-8")
    generate_markdown_matrix(rt, out)
    twice = out.read_text(encoding="utf-8")

    assert once.count("# Traceability Matrix") == twice.count("# Traceability Matrix"), (
        "regenerating the view added another heading; content above the "
        "AUTO-GEN sentinel is preserved, so a heading emitted there must not "
        "also be re-emitted"
    )
