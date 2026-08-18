"""The generator validates the bytes it is about to write, or it writes nothing.

Round 60 站0/站1. Two defects shipped in one batch (`f4be095`, `c939bbf`,
`e37151e`) because the two guards that would have caught them live in
``tests/test_workflow_js_conventions.py`` and the author ran a self-selected
subset of the suite that did not include that file:

* `f4be095` rendered ``run-gate --gate N's printed dim list`` into a JS
  single-quoted string literal, closing it early. Measured 2026-08-19 against
  the shipped tree: a bare ``node --check`` on the file exits **0** (Round 23
  already recorded that dead-guard shape — a ``.js`` path with no package.json
  ``type`` parses as CommonJS and ``export const meta`` fails first), while the
  wrapper the conventions test uses — strip ``export``, wrap the body in an
  async function — exits **1** and points at the line.
* the same batch pushed ``run-all.js`` to 347,147 bytes against a 345,400-byte
  ratchet that was not raised, so CI went red twice and a third commit raised
  the ceiling.

Both are properties of the artifact. The producer is what should hold them:
``--write`` may not put a file on disk that the runtime cannot parse, and may
not put one there that exceeds the ceiling. The conventions test keeps
guarding the *shipped* files (a hand edit is a different entrance); this file
guards the *generator*.

Validation happens for every target before any target is written — a partial
write would leave the eight phase files and run-all disagreeing about which
generation they came from.
"""

from __future__ import annotations

import hashlib
import shutil
import sys
from pathlib import Path

import pytest

pytestmark = [pytest.mark.core]

_SENTINEL = "// untouched\n"
_GENERATED = [
    "phase1-requirements.js", "phase2-architecture.js",
    "phase3-implementation.js", "phase4-testing.js",
    "phase5-verification.js", "phase6-quality.js",
    "phase7-risk.js", "phase8-config.js",
    "run-all.js", "harness-repair.js",
]


def _seed(workflows_dir: Path) -> dict[str, str]:
    """Pre-fill the target directory and return each file's digest."""
    workflows_dir.mkdir(parents=True, exist_ok=True)
    digests = {}
    for name in _GENERATED:
        target = workflows_dir / name
        target.write_text(_SENTINEL, encoding="utf-8")
        digests[name] = hashlib.sha256(target.read_bytes()).hexdigest()
    return digests


def _unchanged(workflows_dir: Path, digests: dict[str, str]) -> list[str]:
    return [
        name for name, digest in digests.items()
        if hashlib.sha256((workflows_dir / name).read_bytes()).hexdigest() != digest
    ]


def _run(argv: list[str], workflows_dir: Path, monkeypatch) -> int:
    from scripts.workflowgen import generate_workflows as gw

    # REPO_ROOT travels with WORKFLOWS_DIR: main() prints each target relative
    # to it, and a tmp target under the real repo root is not a subpath.
    monkeypatch.setattr(gw, "REPO_ROOT", workflows_dir.parent)
    monkeypatch.setattr(gw, "WORKFLOWS_DIR", workflows_dir)
    monkeypatch.setattr(sys, "argv", ["generate_workflows.py", *argv])
    return gw.main()


@pytest.mark.skipif(
    shutil.which("node") is None,
    reason="node not found on PATH — the parse half needs Node.js (dev-only)",
)
def test_write_refuses_output_the_runtime_cannot_parse(tmp_path, monkeypatch):
    """`f4be095`'s apostrophe, injected as a generator, must stop the write.

    The injection is a stand-in generator rather than a patched renderer on
    purpose: `spec_phase3._GATE2_STEPS` is a module-level list whose f-strings
    evaluate at IMPORT time, so a renderer patched after import either does
    nothing or — if the patch happens to precede the first import — freezes
    the broken text into the module for the rest of the process. Measured
    while writing this file: the second effect made a later test in the same
    session fail on files this one had never touched.
    """
    workflows = tmp_path / "workflows"
    digests = _seed(workflows)
    monkeypatch.setattr(
        "scripts.workflowgen.generate_workflows.GENERATORS",
        {3: (lambda: "export const meta = {\n  name: 'p3',\n}\n"
                     "const REPO = 'it's broken'\n",
             "phase3-implementation.js")},
    )

    rc = _run(["--write", "--phase", "3"], workflows, monkeypatch)

    assert rc != 0, "the generator wrote a file the Workflow runtime cannot parse"
    assert _unchanged(workflows, digests) == [], (
        "validation must happen before the write, not after it"
    )


def test_write_refuses_output_over_the_runall_ceiling(tmp_path, monkeypatch):
    """The ratchet is the producer's business too, and no write is partial."""
    from scripts.workflowgen import artifact_limits

    monkeypatch.setattr(artifact_limits, "RUNALL_MAX_BYTES", 100)
    workflows = tmp_path / "workflows"
    digests = _seed(workflows)

    rc = _run(["--write"], workflows, monkeypatch)

    assert rc != 0, "run-all.js exceeded its ceiling and the generator wrote it anyway"
    assert _unchanged(workflows, digests) == [], (
        "one target failing validation must leave every target untouched — a "
        "partial write leaves run-all.js and the phase files disagreeing"
    )


def test_the_shipped_tree_passes_its_own_validation():
    """The guard has to be satisfiable by what is actually shipped."""
    from scripts.workflowgen import generate_workflows as gw

    problems = []
    for phase, (_, filename) in sorted(gw.GENERATORS.items()):
        problems += gw.validate_generated(filename, gw.generate(phase))
    assert problems == [], problems
