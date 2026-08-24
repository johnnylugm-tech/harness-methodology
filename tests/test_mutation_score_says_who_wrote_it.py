"""Round 72 站2 — the mutation score is the framework's, or it says so.

`_mutation_artifact_violations`'s own docstring reads "So the framework's own
artifact is the source." Nothing made it one. Both writers in
`core/quality_gate/mutation_enforcer.py` stamp `enforcer_sha` into the payload
— Round 19 站3's provenance field — and every reader took `data["score"]` and
asked nothing else.

Measured on taskq-new's committed Gate 4:

    {"generated_at": "2026-08-23T14:51:22.f+00:00",   <- strftime placeholder
     "score": 72.1, "killed": 256, "survived": 99,
     "note": "reconstructed from .mutmut-cache … 685 untested mutants are
              post-R2 additions and out-of-scope …"}

No `enforcer_sha`. 256/(256+99) = 72.1 clears the threshold of 70;
256/(256+99+685) is 24.6. `gate4_result.json` records the row as
`"framework: compute_mutation_score → killed=256 survived=99 score=72.1"` with
`"framework_override": true`.

Corpus measurement behind the choice of rule (presence of the key, not its
shape, and not a schema-closure test): of the six projects here holding this
artifact, the five whose runs this code performed all carry `enforcer_sha`
and no keys outside the writers' payload. taskq-new is the only one missing
it. `enforcer_sha()` returns "unknown" when git is unavailable, so a
value-shape rule would fail legitimate runs.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

pytestmark = [pytest.mark.core]

# taskq-new's artifact, verbatim apart from the truncated note.
_HAND_BUILT = {
    "generated_at": "2026-08-23T14:51:22.f+00:00",
    "tool": "mutmut",
    "score": 72.1,
    "killed": 256,
    "survived": 99,
    "paths_to_mutate": "03-development/src/taskq/repository,03-development/src/taskq/service",
    "paths_to_exclude": [],
    "mutated_files": 3,
    "cache_sha256": "1e09628c6b2e344679c9ad94030f36c7cda80de64f960cc284f52c6be65fddeb",
    "note": "reconstructed from .mutmut-cache; 685 untested mutants out-of-scope",
}


def _write(project: Path, payload: dict) -> None:
    from core.quality_gate.mutation_enforcer import MUTATION_SCORE_ARTIFACT

    path = project / MUTATION_SCORE_ARTIFACT
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _violations(project: Path, agent_score: "float | None", threshold: float):
    from harness.harness_bridge import _mutation_artifact_violations

    ctx = SimpleNamespace(project_root=str(project))
    return _mutation_artifact_violations(
        ctx, "mutation_testing", agent_score, threshold,
    )


@pytest.fixture(autouse=True)
def _no_scope_drift(monkeypatch):
    """The scope check runs before this one and is a separate finding."""
    monkeypatch.setattr(
        "core.quality_gate.mutmut_scope.scope_drift", lambda _p: None,
    )


def test_an_artifact_without_a_provenance_stamp_is_not_a_measurement(tmp_path):
    _write(tmp_path, _HAND_BUILT)
    fabrication, unverifiable = _violations(tmp_path, 72.1, 70.0)
    assert unverifiable, (
        "a mutation_score.json this framework never wrote was accepted as "
        "'the framework's own artifact' — the phrase the docstring uses"
    )
    assert "enforcer_sha" in unverifiable[0]
    assert fabrication == [], (
        "an unwritten artifact is the same fact as an absent one, and its "
        "remedy is to run the command — not tool_score_fabrication, whose "
        "registered remediation reads 'do NOT re-run'"
    )


def test_the_stamped_artifact_the_framework_writes_passes(tmp_path):
    """The counter-direction, over the payload the writer actually emits."""
    stamped = dict(_HAND_BUILT)
    stamped.pop("note")
    stamped["enforcer_sha"] = "0" * 40
    _write(tmp_path, stamped)
    fabrication, unverifiable = _violations(tmp_path, 72.1, 70.0)
    assert (fabrication, unverifiable) == ([], [])


def test_an_unavailable_git_still_produces_an_acceptable_stamp(tmp_path):
    """`enforcer_sha()` returns "unknown" with no git — a real run, not a fake.

    This is why the rule is the key's presence and not its value's shape.
    """
    stamped = dict(_HAND_BUILT)
    stamped.pop("note")
    stamped["enforcer_sha"] = "unknown"
    _write(tmp_path, stamped)
    assert _violations(tmp_path, 72.1, 70.0) == ([], [])


def test_both_writers_stamp_the_key_they_are_checked_on(tmp_path):
    """Writer and reader name one constant, so a rename cannot split them."""
    import inspect

    from core.quality_gate import mutation_enforcer

    for fn in (mutation_enforcer._write_score_artifact,
               mutation_enforcer._write_unmeasured_artifact):
        src = inspect.getsource(fn)
        assert "MUTATION_SCORE_PROVENANCE_KEY" in src, (
            f"{fn.__name__} spells the provenance key literally; the reader "
            f"imports the constant, and two spellings of one key is how a "
            f"check silently stops checking"
        )


def test_the_evidence_line_does_not_claim_an_unstamped_number_is_measured(
    tmp_path, monkeypatch,
):
    """The verdict is blocked; the sentence beside it must not survive.

    taskq-new's gate4_result.json carries "framework: compute_mutation_score →
    killed=256 survived=99 score=72.1" in front of a hand-rebuilt number. A
    block that leaves its own false evidence line in the artifact is Round 69's
    write-after-the-verdict, one field over.
    """
    from cli import gate_cmds

    _write(tmp_path, _HAND_BUILT)
    work = tmp_path / ".sessi-work"
    work.mkdir()
    (work / "gate4_result.json").write_text(
        json.dumps({"breakdown": {"mutation_testing": {"score": 72.1}}}),
        encoding="utf-8",
    )
    gate_cmds._patch_mutation_score(tmp_path, 4)

    entry = json.loads(
        (work / "gate4_result.json").read_text()
    )["breakdown"]["mutation_testing"]
    assert "framework: compute_mutation_score" not in entry["tool_evidence"], (
        f"the evidence line still says the framework measured it: "
        f"{entry['tool_evidence']}"
    )
    assert "enforcer_sha" in entry["tool_evidence"]
