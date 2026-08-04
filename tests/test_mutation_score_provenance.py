"""Round 31 站0/站2/站4 — who computes the mutation number, and over what.

`mutation_testing` is a tier-1, `objective_primary: true` dimension, and it is
the only tier-1 dimension whose number the framework never computes.
`compute_mutation_score` (core/quality_gate/mutation_enforcer.py) exists, is
correct, reads the authoritative sqlite cache — and has zero production
callers. Its only entry point is the `mutation-test-score` CLI, which is
*suggested* to the agent in a prompt. On a live Gate 2 the recorded score came
from a prose file the agent wrote:

    .sessi-work/round_N/tools/mutation_testing.txt
      "mutmut run baseline passed after Round-1 fixes..."
      "Cumulative cache: 548 mutants, 240 killed, 308 survived."

which passed `_validate_tool_content` because the mutmut pattern list includes
the bare word `mutmut`.

Two structural facts made that unverifiable rather than merely unverified:

1. The kill-rate cross-check lives inside the `returncode == -1` (skip-list)
   branch of `_run_harness_cross_validation`. mutmut's ToolSpec carries
   `skip_inline=False` — under a comment that says "Inline skip list … e.g.
   mutmut". The branch is unreachable for mutmut. What happens instead, when
   an agent claims a passing score, is a bare `mutmut run` spawned from the
   project root with a 1800s budget: precisely the invocation
   `evaluate_dimension.md` tells agents never to issue, because without the
   workdir setup.cfg rewrite mutmut 2.x's hardcoded `python` runner fails on
   any host without that symlink.

2. The scope is generated once, at the P2→P3 handoff
   (`cli/phase_cmds.py:_regenerate_mutmut_scope`), and never reconciled. A
   project that corrects its SAB mid-P3 keeps whatever `setup.cfg` says, and
   `[mutmut] paths_to_exclude` — a list of basenames that removes files from
   the denominator — is written by the party being scored, with no fingerprint
   in the verdict.

So: the framework writes the number into an artifact it owns, the gate reads
that artifact, and a scope that disagrees with the SAB blocks instead of
scoring.
"""
from __future__ import annotations

import json
import subprocess

import pytest

import harness_cli  # noqa: F401  entry-first load order
import core.quality_gate.gate_thresholds as _gt  # noqa: E402
import harness.tool_runners as tr  # noqa: E402
from harness.harness_bridge import _run_harness_cross_validation  # noqa: E402
from harness.toolchains import get_tool_spec  # noqa: E402

pytestmark = [pytest.mark.core]


class _Ctx:
    def __init__(self, project_root, gate_num=2):
        self.project_root = str(project_root)
        self.gate_num = gate_num
        self.work_dir = str(project_root)


def _gate_config(tmp_path):
    path = tmp_path / "gate2_p3_exit.yaml"
    path.write_text(
        "dimensions:\n"
        "  - {name: mutation_testing, tier: 1, threshold: 70, tool: mutmut, "
        "requires_tool_execution: true}\n",
        encoding="utf-8",
    )
    return path


def _claiming_pass(tool_output="tools/mutation_testing.txt"):
    return {"breakdown": {"mutation_testing": {
        "score": 100.0, "tool_output": tool_output}}}


@pytest.fixture()
def project(tmp_path, monkeypatch):
    """A project claiming a passing mutation score, with a plausible-looking
    tool_output file and no framework-written score artifact."""
    (tmp_path / ".methodology").mkdir()
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "mutation_testing.txt").write_text(
        "mutmut run completed.\n"
        "Cumulative cache: 548 mutants, 240 killed, 308 survived.\n"
        "Score = 240/548 * 100 = 43.8\n" * 3,
        encoding="utf-8",
    )
    monkeypatch.setattr(_gt, "gate_config_path", lambda _n: _gate_config(tmp_path))

    # Safety net for the red phase: today's code really would spawn
    # `mutmut run` here, against the project root, for up to half an hour.
    def _refuse(cmd, **kwargs):
        assert "mutmut" not in cmd[0], (
            f"S4 spawned mutmut inline: {cmd}. Mutation testing is the one "
            f"tool the framework owns end-to-end; re-running it here bypasses "
            f"the workdir isolation compute_mutation_score exists to provide."
        )
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(tr.subprocess, "run", _refuse)
    return tmp_path


# ── 1. mutmut is never executed inline ───────────────────────────────────

def test_mutmut_is_on_the_inline_skip_list_its_comment_already_claims():
    spec = get_tool_spec("mutmut")
    assert spec is not None
    assert spec.skip_inline is True, (
        "the registry comment above this spec says mutmut is on the inline "
        "skip list; the flag says otherwise, so S4 spawns a bare `mutmut run` "
        "from the project root whenever an agent claims a passing score"
    )


# ── 2. the number comes from a framework-written artifact ────────────────

def test_a_passing_claim_without_the_framework_artifact_is_blocked(project):
    violations, _unver = _run_harness_cross_validation(_Ctx(project), _claiming_pass())
    assert violations, (
        "an agent-authored prose file was accepted as the source of a tier-1 "
        "objective_primary score"
    )
    joined = " ".join(violations)
    assert "mutation_score.json" in joined, (
        f"the block must name the framework artifact and how to produce it, "
        f"not merely report a number it could not reproduce: {violations}"
    )


def test_the_framework_artifact_supplies_the_score(project):
    (project / ".methodology" / "mutation_score.json").write_text(
        json.dumps({
            "score": 92.0, "killed": 46, "survived": 4,
            "paths_to_mutate": "03-development/src/app",
            "paths_to_exclude": [],
            "mutated_files": 3,
            "cache_sha256": "deadbeef",
            "generated_at": "2026-08-03T00:00:00+00:00",
        }),
        encoding="utf-8",
    )
    violations, _unver = _run_harness_cross_validation(_Ctx(project), _claiming_pass())
    assert not violations, violations


def test_a_claim_that_contradicts_the_artifact_is_blocked(project):
    (project / ".methodology" / "mutation_score.json").write_text(
        json.dumps({
            "score": 43.8, "killed": 240, "survived": 308,
            "paths_to_mutate": "03-development/src/app",
            "paths_to_exclude": [],
            "mutated_files": 8,
            "cache_sha256": "deadbeef",
            "generated_at": "2026-08-03T00:00:00+00:00",
        }),
        encoding="utf-8",
    )
    violations, _unver = _run_harness_cross_validation(_Ctx(project), _claiming_pass())
    assert violations and "43.8" in " ".join(violations), violations


# ── 2b. the producers are actually wired ─────────────────────────────────
# Round 30 cost three separate counter-proofs to this exact trap: a helper
# with tests, a call site with none, so unwiring it left the suite green.

def test_compute_mutation_score_writes_the_artifact():
    """Round 35 站2 points this at `_compute_mutation_score`, the producer.

    `compute_mutation_score` is now a two-line wrapper whose own job is the
    other half of the same rule: writing the artifact when the run could NOT
    produce a score, so the file's absence stops meaning two things at once.
    """
    import inspect

    from core.quality_gate.mutation_enforcer import (
        _compute_mutation_score,
        compute_mutation_score,
    )

    assert "_write_score_artifact" in inspect.getsource(_compute_mutation_score), (
        "the artifact the gate blocks on has no producer — the same shape as "
        "Round 29's write_paths_to_mutate, which was written and never called"
    )
    assert "_write_unmeasured_artifact" in inspect.getsource(compute_mutation_score), (
        "a run that could not measure leaves no record, so the gate reads its "
        "absence as 'nobody ran the command'"
    )


def test_finalize_patches_the_framework_score_into_the_verdict():
    import inspect

    from cli.gate_cmds import _finalize_gate_cross_checks

    assert "_patch_mutation_score" in inspect.getsource(_finalize_gate_cross_checks), (
        "S4 can block on the artifact and the verdict still record the agent's "
        "number; the override is what makes the recorded score the framework's"
    )


def test_the_patch_replaces_the_agents_number(tmp_path):
    from cli.gate_cmds import _patch_mutation_score

    (tmp_path / ".methodology").mkdir()
    (tmp_path / ".sessi-work").mkdir()
    (tmp_path / ".methodology" / "mutation_score.json").write_text(
        json.dumps({"score": 43.8, "killed": 240, "survived": 308,
                    "paths_to_mutate": "src/app", "paths_to_exclude": [],
                    "mutated_files": 8}),
        encoding="utf-8",
    )
    result = tmp_path / ".sessi-work" / "gate2_result.json"
    result.write_text(
        json.dumps({"breakdown": {"mutation_testing": {"score": 100.0}}}),
        encoding="utf-8",
    )

    _patch_mutation_score(tmp_path, 2)

    patched = json.loads(result.read_text(encoding="utf-8"))
    entry = patched["breakdown"]["mutation_testing"]
    assert entry["score"] == 43.8
    assert entry["framework_override"] is True
    assert "compute_mutation_score" in entry["tool_evidence"]
    assert "8 files" in entry["tool_evidence"], (
        f"the denominator must travel with the score: {entry['tool_evidence']}"
    )


# ── 3. the scope must still agree with the SAB ───────────────────────────

def _sab_scoped_to(layer_dirs):
    return {
        "layers": [
            {"name": name, "modules": [f"app.{name}"]} for name in layer_dirs
        ],
        "nfr_traceability": {
            "NFR-08": {"dimension": "mutation_testing",
                       "scope_layers": list(layer_dirs)},
        },
    }


def test_a_setup_cfg_scope_that_disagrees_with_the_sab_is_reported(tmp_path):
    from core.quality_gate.mutmut_scope import scope_drift

    (tmp_path / ".methodology").mkdir()
    (tmp_path / ".methodology" / "SAB.json").write_text(
        json.dumps(_sab_scoped_to(("service", "storage"))), encoding="utf-8"
    )
    for d in ("service", "storage", "cli"):
        (tmp_path / "03-development" / "src" / "app" / d).mkdir(parents=True)
    (tmp_path / "setup.cfg").write_text(
        "[mutmut]\npaths_to_mutate = 03-development/src/app\n", encoding="utf-8"
    )

    drift = scope_drift(tmp_path)
    assert drift, (
        "setup.cfg mutates the whole package while the SAB limits the NFR to "
        "service + storage, and nothing said so"
    )
    assert "03-development/src/app/service" in drift and "setup.cfg" in drift, drift


def test_a_scope_that_matches_the_sab_reports_no_drift(tmp_path):
    from core.quality_gate.mutmut_scope import resolve_mutation_scope, scope_drift

    (tmp_path / ".methodology").mkdir()
    sab = _sab_scoped_to(("service", "storage"))
    (tmp_path / ".methodology" / "SAB.json").write_text(
        json.dumps(sab), encoding="utf-8"
    )
    for d in ("service", "storage"):
        (tmp_path / "03-development" / "src" / "app" / d).mkdir(parents=True)
    derived = resolve_mutation_scope(sab, "03-development/src")
    assert derived
    (tmp_path / "setup.cfg").write_text(
        f"[mutmut]\npaths_to_mutate = {derived}\n", encoding="utf-8"
    )

    assert scope_drift(tmp_path) is None


def test_the_gate_actually_calls_the_drift_check(project):
    """The R30 trap, guarded: scope_drift can be perfect and never run.
    Driven through the real S4 entry point rather than asserting on source."""
    (project / ".methodology" / "mutation_score.json").write_text(
        json.dumps({"score": 92.0, "killed": 46, "survived": 4,
                    "paths_to_mutate": "03-development/src/app/service",
                    "paths_to_exclude": [], "mutated_files": 3,
                    "cache_sha256": "deadbeef"}),
        encoding="utf-8",
    )
    (project / ".methodology" / "SAB.json").write_text(
        json.dumps(_sab_scoped_to(("service", "storage"))), encoding="utf-8"
    )
    for d in ("service", "storage"):
        (project / "03-development" / "src" / "app" / d).mkdir(parents=True)
    (project / "setup.cfg").write_text(
        "[mutmut]\npaths_to_mutate = 03-development/src/app\n", encoding="utf-8"
    )

    violations, _unver = _run_harness_cross_validation(_Ctx(project), _claiming_pass())
    assert violations and "disagrees with the SAB" in " ".join(violations), (
        f"a score above threshold sailed through on a scope the SAB does not "
        f"declare: {violations}"
    )


def test_no_sab_scope_declared_is_not_drift(tmp_path):
    """A project that legitimately mutates everything must not be blocked by a
    check whose whole purpose is disagreement between two declarations."""
    from core.quality_gate.mutmut_scope import scope_drift

    (tmp_path / ".methodology").mkdir()
    (tmp_path / ".methodology" / "SAB.json").write_text(
        json.dumps({"layers": [], "nfr_traceability": {}}), encoding="utf-8"
    )
    (tmp_path / "setup.cfg").write_text(
        "[mutmut]\npaths_to_mutate = 03-development/src\n", encoding="utf-8"
    )
    assert scope_drift(tmp_path) is None
