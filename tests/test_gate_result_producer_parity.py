"""Round 26 — every producer of gate{N}_result.json is held to the schema.

The Round 21 station-2 close made `harness/ssi/schemas/harness_gate_result.schema.json`
executable at the read that drives scoring (`harness_bridge.finalize_gate`), and pinned it
against reality with one artifact this repo did not author — taskq's committed
`gate4_result.json`. That closed the "schema nobody loads" hole and left a narrower one
open: the schema was checked against a *product*, never against the *instructions that
produce it*.

`required` listed `open_critical_count` / `open_high_count`. No producer instruction has
ever asked for them:

  * `cli/fr_prompts/gate.py`'s Gate 1 template says "with this EXACT schema" and omits both;
  * `harness/ssi/prompts/evaluate_dimension.md` stated no top-level shape at all until
    Round 26 added one;
  * the Gate 2/3/4 write steps in `.claude/workflows/phase{3,4,6}-*.js` say "Write
    .sessi-work/gate{N}_result.json" and name dimensions, never keys.

The gate-4 fixture carried them anyway, so validation agreed with reality by luck while an
obedient Gate 1 agent produced an off-schema file. taskq-plus P3 blocked on
`malformed_gate_result` at FR-01 (`.methodology/lessons/1fa904636bd1.md`) and again at
FR-04, passing 8 seconds later only because the retry volunteered fields nothing had asked
for: whether Gate 1 passed depended on the agent exceeding its instructions.

This module is the other half of the parity. It reads what each producer *actually renders*
— not what it claims — and holds it to the schema. Same declarative-registry +
completeness-meta-test shape as tests/test_prompt_gate_parity.py (Round 17 站1) and
tests/test_workflow_dispatch_registry.py.

Deliberate asymmetry, not an oversight: the markdown producer's block is valid JSON, so it
is parsed and validated in both directions. The Gate 1 template carries `<float>` /
`<0-100>` placeholders and cannot be parsed, so it is checked by key presence over the whole
rendered prompt. Slicing out "the template region" would require this test to hold a second
copy of the prompt's structure — the exact duplication it exists to prevent.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

import harness_cli as _hc_entry  # noqa: F401  entry-first before cli imports
from cli.fr_cmds import _build_fr_step_prompt  # noqa: E402
from core.quality_gate.gate_result_schema import (  # noqa: E402
    SCHEMA_PATH,
    validate_gate_result,
)
from core.utils.project_layout import ProjectLayout  # noqa: E402

REPO = Path(__file__).resolve().parent.parent

_EVAL_DIMENSION = REPO / "harness" / "ssi" / "prompts" / "evaluate_dimension.md"
_SHAPE_BLOCK_RE = re.compile(
    r"<!--\s*GATE_RESULT_SHAPE:START\s*-->\s*```json\n(.*?)```\s*<!--\s*GATE_RESULT_SHAPE:END\s*-->",
    re.DOTALL,
)

# Which gates each producer instructs. A gate with no producer in this map is
# caught by test_every_gate_with_a_writer_has_a_declared_shape below.
_PRODUCER_GATES: dict[str, tuple[int, ...]] = {
    "gate1_prompt": (1,),
    "evaluate_dimension_md": (2, 3, 4),
}


@pytest.fixture(scope="module")
def schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def _min_project(tmp_path: Path) -> tuple[Path, Path]:
    """Smallest project the GATE1 prompt branch renders against.

    Paths come from ProjectLayout rather than string literals so the fixture
    tracks the layout SSOT (docs/PROPOSAL_ADJUDICATIONS.md, Round 20 站2).
    """
    proj = tmp_path / "producer-parity-fixture"
    layout = ProjectLayout(proj)

    meth = proj / ".methodology"
    meth.mkdir(parents=True)
    (meth / "quality_manifest.json").write_text(
        json.dumps({"fr_ids": ["FR-01"], "gate_score_overrides": {}}), encoding="utf-8"
    )

    srs = layout.srs_path
    srs.parent.mkdir(parents=True, exist_ok=True)
    srs.write_text(
        "### FR-01: Widget submission\n\nThe system MUST accept a submission.\n\n---\n",
        encoding="utf-8",
    )

    spec = layout.test_spec_path
    spec.parent.mkdir(parents=True, exist_ok=True)
    spec.write_text(
        "### FR-01: Widget submission\n\n"
        "| # | Test Function | Type |\n"
        "|---|--------------|------|\n"
        "| 1 | test_fr01_01_happy | Functional |\n",
        encoding="utf-8",
    )

    test_dir = layout.active_test_dir
    test_dir.mkdir(parents=True, exist_ok=True)
    (test_dir / "test_fr01.py").write_text(
        "def test_fr01_01_happy():\n    assert True\n", encoding="utf-8"
    )
    (proj / "03-development" / "src").mkdir(parents=True, exist_ok=True)
    return proj, srs


@pytest.fixture
def gate1_prompt(tmp_path: Path) -> str:
    proj, srs = _min_project(tmp_path)
    return _build_fr_step_prompt("GATE1", "FR-01", 3, proj, srs)


@pytest.fixture(scope="module")
def declared_shape() -> dict:
    """The top-level shape `evaluate_dimension.md` tells Gate 2/3/4 to write."""
    text = _EVAL_DIMENSION.read_text(encoding="utf-8")
    match = _SHAPE_BLOCK_RE.search(text)
    assert match, (
        "evaluate_dimension.md carries no GATE_RESULT_SHAPE block — the Gate 2/3/4 "
        "writer has no statement of the document's top level, which is the condition "
        "Round 26 removed. Restore the block, do not delete this test."
    )
    return json.loads(match.group(1))


class TestForwardDirection:
    """Every key the schema requires is stated by every producer for its gates."""

    def test_gate1_prompt_states_every_required_key(self, schema, gate1_prompt):
        missing = [k for k in schema["required"] if f'"{k}"' not in gate1_prompt]
        assert not missing, (
            f"the Gate 1 dispatch prompt never mentions {sorted(missing)}, which "
            f"finalize_gate REQUIRES — an agent following the prompt exactly produces a "
            f"file the gate rejects as malformed_gate_result. Either state the key in "
            f"cli/fr_prompts/gate.py or drop it from the schema's required list; do not "
            f"leave the producer and the contract disagreeing."
        )

    def test_evaluate_dimension_states_every_required_key(self, schema, declared_shape):
        missing = set(schema["required"]) - set(declared_shape)
        assert not missing, (
            f"evaluate_dimension.md's declared shape omits required key(s) "
            f"{sorted(missing)} — Gate 2/3/4 agents are told to write a file "
            f"finalize_gate will reject."
        )


class TestReverseDirection:
    """The declared shape may not invent keys the schema does not describe."""

    def test_declared_shape_uses_only_described_keys(self, schema, declared_shape):
        undescribed = set(declared_shape) - set(schema["properties"])
        assert not undescribed, (
            f"evaluate_dimension.md declares {sorted(undescribed)}, absent from the "
            f"schema's properties. A key the contract does not describe is how "
            f"`tool_score` and `target` became consumer-side guesses (Round 21)."
        )

    def test_declared_shape_validates_against_the_schema(self, declared_shape):
        verdict = validate_gate_result(declared_shape)
        assert verdict.valid, (
            "the shape the prompt tells agents to write does not itself pass the "
            "validator that will judge it:\n" + "\n".join(verdict.violations)
        )


class TestCompleteness:
    """A new gate, or a new writer, cannot silently escape the parity."""

    def test_every_gate_with_a_writer_has_a_declared_shape(self):
        """Grep the tree for gate-result writers; each gate needs a producer entry.

        Without this, adding a Gate 5 (or moving Gate 3's write step to a new file)
        would reintroduce exactly the hole Round 26 closed — a writer with no stated
        shape, judged by a schema it was never shown.
        """
        covered = {g for gates in _PRODUCER_GATES.values() for g in gates}
        written: set[int] = set()
        for path in [
            *sorted((REPO / ".claude" / "workflows").glob("*.js")),
            REPO / "cli" / "fr_prompts" / "gate.py",
            _EVAL_DIMENSION,
        ]:
            text = path.read_text(encoding="utf-8")
            for gate in re.findall(r"gate([1-9])_result\.json", text):
                written.add(int(gate))
        assert written, "found no gate-result writer at all — the grep has gone stale"
        assert written <= covered, (
            f"gate(s) {sorted(written - covered)} have a writer but no producer entry in "
            f"_PRODUCER_GATES, so nothing checks their declared shape against the schema."
        )

    def test_phase_workflows_point_their_writers_at_the_declaration(self):
        """The Gate 2/3/4 write steps must reference the file that states the shape.

        The declaration only reaches the agent because the workflow step tells it to
        follow evaluate_dimension.md. If that pointer disappears, the block above is
        still present and still tested — and no agent ever reads it.
        """
        orphans = []
        for path in sorted((REPO / ".claude" / "workflows").glob("*.js")):
            text = path.read_text(encoding="utf-8")
            if not re.search(r"gate[234]_result\.json", text):
                continue
            if "evaluate_dimension.md" not in text:
                orphans.append(path.name)
        assert not orphans, (
            f"{orphans} instruct a Gate 2/3/4 result write without pointing at "
            f"evaluate_dimension.md, where the required top-level shape is declared."
        )
