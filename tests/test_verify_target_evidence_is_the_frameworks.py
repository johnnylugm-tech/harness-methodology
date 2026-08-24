"""Round 73 站4 — the dimension's score is the framework's, its sentence was not.

taskq-new's committed gate4_result.json, `execute_verification_target`:

    "score": 100.0, "threshold": 100, "score_source": "framework",
    "tool_evidence": "make verify-system exited 0 with output 'verify-system:
                      PASS (healthz=200 readyz=200)'. migrate-roundtrip: PASS
                      precedes. NFR-12 satisfied."

The score is the framework's. `tool_evidence` has no writer anywhere in this
repository — that sentence is the agent's, and its last clause is false.
AC-N12.1 requires the recipe to chain four steps; the delivered Makefile does
one and a half:

    migrate-roundtrip:            # not alembic — a reset_db() call
        $(PYTHON) -c "from taskq.repository.tasks import reset_db; reset_db()"
    verify-system: migrate-roundtrip
        ... uvicorn ... /healthz /readyz ...

`alembic upgrade head` is absent, the round-trip is absent, and the `test:`
target exists while `verify-system` does not depend on it.

The framework's two checks here both passed and neither is about that.
`verify_target.blocking_reason` asks whether the recipe invokes the delivered
entry point and whether that step can fail; `verify_system_reach` asks whether
the boundaries the suite stubs are executed by something. Whether the recipe
does what the requirement says it does has no executor — and Round 43's rule
is that when a check has no executor the fix is to write down that there is
none, so a report cannot claim otherwise.

So the dimension states what it actually decided, in the framework's own
words, and stops claiming an NFR. Same shape as Round 72 站2 for
mutation_testing: the score was already the framework's and the sentence
beside it was not.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = [pytest.mark.core]


_MAKEFILE = """\
ROOT := $(shell pwd)
PYTHON ?= $(ROOT)/.venv/bin/python
SRC := $(ROOT)/03-development/src

migrate-roundtrip:
\t@PYTHONPATH=$(SRC) $(PYTHON) -c "from taskq.repository.tasks import reset_db; reset_db()"

verify-system: migrate-roundtrip
\t@PYTHONPATH=$(SRC) $(PYTHON) -m uvicorn taskq.api.app:create_app --factory --port 8765 &
\t@curl -fs http://127.0.0.1:8765/healthz
"""


def _project(tmp_path: Path, *, makefile: "str | None", gate: int = 4,
             evidence: str = "NFR-12 satisfied.") -> Path:
    proj = tmp_path / "proj"
    (proj / ".sessi-work").mkdir(parents=True)
    (proj / "03-development" / "src" / "taskq").mkdir(parents=True)
    (proj / "03-development" / "src" / "taskq" / "__init__.py").write_text("", encoding="utf-8")
    if makefile is not None:
        (proj / "Makefile").write_text(makefile, encoding="utf-8")
    (proj / ".sessi-work" / f"gate{gate}_result.json").write_text(
        json.dumps({"breakdown": {"execute_verification_target": {
            "score": 100.0, "threshold": 100, "score_source": "framework",
            "tool_evidence": evidence,
        }}}), encoding="utf-8")
    return proj


def _entry(proj: Path, gate: int = 4) -> dict:
    return json.loads(
        (proj / ".sessi-work" / f"gate{gate}_result.json").read_text(encoding="utf-8")
    )["breakdown"]["execute_verification_target"]


def test_the_agents_nfr_claim_is_replaced(tmp_path):
    """The whole point: the dimension may not certify a requirement."""
    from cli.gate_cmds import _patch_verify_target_evidence

    proj = _project(tmp_path, makefile=_MAKEFILE)
    _patch_verify_target_evidence(proj, 4)

    evidence = _entry(proj)["tool_evidence"]
    assert "NFR-12 satisfied" not in evidence, evidence
    assert evidence.startswith("framework: verify_target_findings"), evidence


def test_the_replacement_names_the_step_that_runs_the_product(tmp_path):
    """Round 45's rule: a verdict's sentence has to carry its own proof.

    The agent's line was specific ("healthz=200 readyz=200"); replacing it
    with something vaguer trades one kind of imprecision for another.
    `entrypoint_lines` is exactly the evidence the framework already computed
    for its own tautology check.
    """
    from cli.gate_cmds import _patch_verify_target_evidence

    proj = _project(tmp_path, makefile=_MAKEFILE)
    _patch_verify_target_evidence(proj, 4)

    evidence = _entry(proj)["tool_evidence"]
    assert "uvicorn taskq.api.app:create_app" in evidence, evidence
    assert "no step swallows" in evidence or "swallow" in evidence, evidence


def test_a_recipe_that_could_not_be_read_says_so(tmp_path):
    """Could-not-measure is a sentence, not an empty string (Rounds 32/35).

    `expand_recipe` abstains on an `include`, and the score beside it is still
    100 because `make verify-system` still exits 0. The line has to say which
    of the two it is.
    """
    from cli.gate_cmds import _patch_verify_target_evidence

    proj = _project(tmp_path, makefile="include other.mk\n\nverify-system:\n\t@true\n")
    _patch_verify_target_evidence(proj, 4)

    evidence = _entry(proj)["tool_evidence"]
    assert "could not" in evidence.lower(), evidence
    assert "NFR" not in evidence, evidence


def test_a_project_with_no_makefile_is_left_alone(tmp_path):
    """The counter-direction. A JS project scored by a different runner has no
    Makefile, and rewriting its evidence into a claim about a Makefile would
    be this station's own defect."""
    from cli.gate_cmds import _patch_verify_target_evidence

    proj = _project(tmp_path, makefile=None, evidence="npm run verify exited 0.")
    _patch_verify_target_evidence(proj, 4)

    assert _entry(proj)["tool_evidence"] == "npm run verify exited 0."


def test_the_patch_is_called_from_the_cross_checks(tmp_path):
    """A detector with no executor is Round 43's defect, and Round 30's is a
    mechanism that ships half-built. Pinned on the source, beside the
    `_patch_mutation_score` call it mirrors, because that is the only place a
    later refactor would drop it from."""
    import inspect

    from cli import gate_cmds

    src = inspect.getsource(gate_cmds._finalize_gate_cross_checks)
    assert "_patch_verify_target_evidence(project_path" in src, src[:400]


def test_a_gate_without_the_dimension_gains_nothing(tmp_path):
    """Round 53 站5a: only a gate that runs this dimension may speak for it."""
    from cli.gate_cmds import _patch_verify_target_evidence

    proj = tmp_path / "proj"
    (proj / ".sessi-work").mkdir(parents=True)
    (proj / "Makefile").write_text(_MAKEFILE, encoding="utf-8")
    (proj / ".sessi-work" / "gate1_result.json").write_text(
        json.dumps({"breakdown": {"linting": {"score": 100.0}}}), encoding="utf-8")

    _patch_verify_target_evidence(proj, 1)

    gr = json.loads((proj / ".sessi-work" / "gate1_result.json").read_text(encoding="utf-8"))
    assert "execute_verification_target" not in gr["breakdown"], gr
