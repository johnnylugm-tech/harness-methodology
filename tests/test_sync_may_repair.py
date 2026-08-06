"""The post-advance push is blocked by content, so its step must be able to fix content.

Round 43 站0. `scripts/hooks/pre-push` runs `harness_cli.py run-phase --phase N`
— the full fifteen-check preflight — on every push. What it rejects is almost
never the network; it is a pragma, a missing FR Block, an unregistered SAB
module. Yet the Sync step that performs that push is the one step in the whole
pipeline with no authority to fix anything:

    scripts/workflowgen/js_blocks.py::render_sync_verified   (P1/P2/P4-P8)
        prompt = 'Run EXACTLY this command via Bash:\\n git push origin main'
        one attempt, then `return { error: ... }`

    scripts/workflowgen/spec_phase3.py::render_sync          (P3, run-all)
        the same prompt, dispatched a second time verbatim —
        `// retrying once (covers transient network failures)` —
        then MANUAL_REQUIRED, which ends the whole run-all pipeline.

The correct shape is already in the same generated file. `render_push_loop`'s
P1 Push step runs `push-checkpoint` up to five times with

    'Do NOT use --no-verify. Read the error and fix if FAIL.'

and that step goes through the identical hook. Round 41 站3's rule — diagnose
the transport from the transport's own text, and stop buying the same failure
twice — is what the P3 retry violates: it classifies a deterministic content
failure as a transient one by re-sending the identical instruction.

The assertion reads the clause from the generator rather than restating it, so
rewording the prompt moves the test with it instead of falsely reddening it
(the render-from-SSOT shape Round 33 站1 / Round 36 站1 settled on).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.workflow_audit.extract import JS_PHASE_RE

_WORKFLOWS = Path(__file__).resolve().parents[1] / ".claude" / "workflows"


def _sync_blocks() -> "list[tuple[str, str, str]]":
    """(file name, phase label, block text) for every shipped Sync step."""
    out: list[tuple[str, str, str]] = []
    for js in sorted(_WORKFLOWS.glob("*.js")):
        text = js.read_text(encoding="utf-8")
        marks = list(JS_PHASE_RE.finditer(text))
        for i, m in enumerate(marks):
            if "Sync" not in m.group(1):
                continue
            end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
            out.append((js.name, m.group(1), text[m.start():end]))
    return out


def test_the_shipped_workflows_have_sync_steps():
    """Guard the guard: an empty scan must not read as a pass."""
    blocks = _sync_blocks()
    assert len(blocks) >= 8, (
        f"expected a Sync step in each phase workflow; found {len(blocks)}"
    )


@pytest.mark.parametrize(
    "name,label,block",
    [pytest.param(n, la, b, id=f"{n}:{la}") for n, la, b in _sync_blocks()],
)
def test_every_sync_step_may_repair_its_blocker(name, label, block):
    from scripts.workflowgen.js_blocks import SYNC_REPAIR_CLAUSE

    assert SYNC_REPAIR_CLAUSE in block, (
        f"{name} phase({label!r}) pushes through the pre-push preflight with "
        f"no authority to fix what that preflight rejects. Every other "
        f"blocking step in the pipeline grants it."
    )


@pytest.mark.parametrize(
    "name,label,block",
    [pytest.param(n, la, b, id=f"{n}:{la}") for n, la, b in _sync_blocks()],
)
def test_every_sync_step_retries_under_a_bound(name, label, block):
    from scripts.workflowgen.js_blocks import SYNC_ATTEMPTS_CONST

    assert SYNC_ATTEMPTS_CONST in block, (
        f"{name} phase({label!r}) has no bounded retry — a content blocker "
        f"needs a second attempt AFTER the fix, and an unbounded one would "
        f"loop on a blocker no agent can clear"
    )
