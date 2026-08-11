"""A verdict must outlive its own proof, not just its own fingerprint.

Round 45 站0. Round 27 站3 wrote the intended property into a comment at
``harness/harness_bridge.py:2650``:

    the verdict and the proof of what it read then live in one file and one
    commit, **and cannot be separated by a cleanup of the gitignored work
    directory**.

Half of that shipped. `_check_tool_evidence` verifies each cited `tool_output`
exists (harness_bridge.py:1211) and fingerprints it (:1227) — both for every
`requires_tool_execution` dimension, not just the skip-list pair. What nothing
does is keep the file. Every one of those paths points under `.sessi-work/`,
which the harness itself writes as the first line of every project's
`.gitignore` (`harness/git_strategy.py::_GITIGNORE_ENTRIES`).

Measured 2026-08-11 over five projects' committed gate results:

    project        cited tool_output   files still present
    taskq                  36                   0
    taskq-plus             37                   1
    taskq-renew            36                   0
    taskq-api              12                   0
    taskq-advance          41                  12
    ------------------------------------------------------
    total                 162                  13   (92% dangling)

taskq-advance's P6 release verdict (`gate4_result.json`, composite 95.978,
PASS) carries 14 dimension fingerprints. Fourteen sha256 values, fourteen
files that do not exist — a promise that can never be redeemed. A digest of
a file nobody has is not proof; it is a claim that cannot be checked.

The fix adds no judgement: the framework already decided this evidence was
genuine and already recorded what it read. It only has to keep it.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from harness.harness_bridge import GateContext, _check_tool_evidence

pytestmark = [pytest.mark.core]


def _ctx(root: Path, gate: int = 4) -> GateContext:
    """Same shape as tests/test_evidence_digest.py's — one fixture, one story."""
    return GateContext(
        gate_num=gate, config={}, project_root=str(root), phase=6, fr_id=None,
        ssi_scripts_dir="", ssi_prompts_dir="", ssi_schemas_dir="",
        work_dir=str(root / ".sessi-work"), sab_data={},
    )


def _gate_yaml(tmp_path: Path, gate: int, dims: list[dict], monkeypatch) -> None:
    import yaml

    import core.quality_gate.gate_thresholds as _gt
    cfg_path = tmp_path / f"gate{gate}_cfg.yaml"
    cfg_path.write_text(yaml.dump({"gate": gate, "dimensions": dims}))
    monkeypatch.setattr(_gt, "gate_config_path", lambda g: cfg_path)


@pytest.fixture()
def cited(tmp_path: Path):
    """A gate result citing one tool_output under the gitignored work dir."""
    out = tmp_path / ".sessi-work" / "round_1" / "tools" / "linting.txt"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("All checks passed!\n", encoding="utf-8")
    raw = {"breakdown": {"linting": {
        "score": 100.0,
        "tool_output": ".sessi-work/round_1/tools/linting.txt",
    }}}
    return tmp_path, out, raw


# ── the evidence survives the work directory ────────────────────────────────

def test_the_cited_evidence_survives_a_cleanup_of_the_work_directory(cited):
    """The taskq-advance Gate 4 situation, replayed and refused."""
    import shutil

    from core.quality_gate.gate_evidence_store import persist_cited_evidence

    project, _original, raw = cited
    persist_cited_evidence(project, 4, raw)

    shutil.rmtree(project / ".sessi-work")

    cited_now = project / raw["breakdown"]["linting"]["tool_output"]
    assert cited_now.is_file(), (
        f"the verdict cites {raw['breakdown']['linting']['tool_output']} and "
        f"the file is gone — this is the 92%-dangling shape the round measured"
    )
    assert cited_now.read_bytes() == b"All checks passed!\n"


def test_the_surviving_copy_lives_under_methodology(cited):
    """`.methodology/` is what a clone gets; `.sessi-work/` is what it does not."""
    from core.quality_gate.gate_evidence_store import persist_cited_evidence

    project, _original, raw = cited
    persist_cited_evidence(project, 4, raw)

    cited_now = raw["breakdown"]["linting"]["tool_output"]
    assert cited_now.startswith(".methodology/gate_evidence/gate4/"), cited_now


def test_the_digest_names_the_path_that_survived(cited, monkeypatch):
    """S3 runs after the copy, so `evidence_digest.source` points somewhere real.

    Without this the verdict carries a fingerprint whose `source` names a file
    the reader cannot open — which is the state every shipped gate result is
    in today.
    """
    from core.quality_gate.gate_evidence_store import persist_cited_evidence

    project, original, raw = cited
    _gate_yaml(project, 4, [
        {"name": "linting", "requires_tool_execution": True, "tool": "ruff",
         "threshold": 90},
    ], monkeypatch)

    persist_cited_evidence(project, 4, raw)
    digests: dict = {}
    assert _check_tool_evidence(_ctx(project), raw, digests) == []

    assert digests["linting"]["source"].startswith(".methodology/gate_evidence/")
    assert digests["linting"]["sha256"] == hashlib.sha256(
        original.read_bytes()
    ).hexdigest()
    assert (project / digests["linting"]["source"]).is_file()


def test_an_already_durable_citation_is_still_resolvable(tmp_path):
    """`.methodology/mutation_score.json` is cited by real gate results and
    already survives. Copying it too is harmless; leaving the verdict pointing
    at something unreadable is not. Either way the invariant is the same:
    after persist, the cited path resolves."""
    from core.quality_gate.gate_evidence_store import persist_cited_evidence

    meth = tmp_path / ".methodology"
    meth.mkdir()
    (meth / "mutation_score.json").write_text('{"score": 77.6}', encoding="utf-8")
    raw = {"breakdown": {"mutation_testing": {
        "score": 77.6, "tool_output": ".methodology/mutation_score.json"}}}

    persist_cited_evidence(tmp_path, 4, raw)

    cited = tmp_path / raw["breakdown"]["mutation_testing"]["tool_output"]
    assert cited.is_file()
    assert json.loads(cited.read_text())["score"] == 77.6


# ── the re-pointing has to reach the file, not just the dict ────────────────

def test_finalize_writes_the_new_citation_to_the_result_file(
    tmp_path, monkeypatch,
):
    """`finalize_gate` holds `raw` in memory but the digest block re-reads the
    file from disk, and cli/gate_cmds.py copies that same file into
    `.methodology/gate{N}_result.json`. A re-pointing that lived only in the
    dict would be thrown away by both.

    Blocked on purpose, via a second dimension that cites nothing: the citation
    must be persisted even when a later check refuses the gate — same rule the
    evidence digests already follow (`harness_bridge.py:2657`).
    """
    from harness.harness_bridge import GateBlockedError, HarnessBridge
    from core.quality_gate.constitution.profile import DimensionConfig, GateConfig

    _gate_yaml(tmp_path, 2, [
        {"name": "linting", "requires_tool_execution": True, "tool": "ruff",
         "threshold": 90},
        {"name": "type_safety", "requires_tool_execution": True,
         "tool": "pyright", "threshold": 85},
    ], monkeypatch)

    work = tmp_path / ".sessi-work"
    (work / "round_1" / "tools").mkdir(parents=True)
    (work / "round_1" / "tools" / "linting.txt").write_text(
        "All checks passed!\n", encoding="utf-8")
    result_path = work / "gate2_result.json"
    result_path.write_text(json.dumps({
        "overall_score": 95.0, "meets_target": True, "quality_complete": True,
        "open_critical_count": 0, "open_high_count": 0,
        "breakdown": {
            "linting": {"score": 100.0, "threshold": 90,
                        "tool_output": ".sessi-work/round_1/tools/linting.txt"},
            "type_safety": {"score": 90.0, "threshold": 85},
        },
    }), encoding="utf-8")

    ssi = Path(__file__).parent.parent / "harness" / "ssi"
    ctx = GateContext(
        gate_num=2,
        config=GateConfig(gate_num=2, score_gate=80.0, max_rounds=3,
                          dimensions=[DimensionConfig(name="linting",
                                                      threshold=90.0)]),
        project_root=str(tmp_path), phase=3, fr_id=None,
        ssi_scripts_dir=str(ssi / "scripts"),
        ssi_prompts_dir=str(ssi / "prompts"),
        ssi_schemas_dir=str(ssi / "schemas"),
        work_dir=str(work),
    )

    with pytest.raises(GateBlockedError):
        HarnessBridge().finalize_gate(ctx)

    on_disk = json.loads(result_path.read_text(encoding="utf-8"))
    cited = on_disk["breakdown"]["linting"]["tool_output"]
    assert cited.startswith(".methodology/gate_evidence/gate2/"), cited
    assert (tmp_path / cited).is_file()


# ── the two ways it must NOT act ────────────────────────────────────────────

def test_a_path_escaping_the_project_is_left_for_s3_to_refuse(tmp_path):
    """Containment is already S3's job (`harness_bridge.py:1199`). This must not
    quietly copy the file and thereby launder the escape into a valid citation —
    it leaves the path untouched so the existing violation still fires."""
    from core.quality_gate.gate_evidence_store import persist_cited_evidence

    outside = tmp_path.parent / "outside.txt"
    outside.write_text("secret\n", encoding="utf-8")
    raw = {"breakdown": {"linting": {
        "score": 100.0, "tool_output": "../outside.txt"}}}

    persist_cited_evidence(tmp_path, 4, raw)

    assert raw["breakdown"]["linting"]["tool_output"] == "../outside.txt"
    assert not (tmp_path / ".methodology" / "gate_evidence").exists()


def test_an_oversized_file_is_recorded_not_silently_dropped(tmp_path, monkeypatch):
    """Round 32/35: could-not-do is not the same as did-not-happen.

    The largest cited evidence measured across five projects was 19,994 bytes,
    so the cap is an order-of-magnitude guard rather than a routine path. When
    it does fire the citation stays pointed at the original — and the ledger
    says why, so nobody reads the dangling path as an accident.
    """
    from core.degradation_ledger import LEDGER_RELPATH
    from core.quality_gate.gate_evidence_store import persist_cited_evidence

    monkeypatch.setattr(
        "core.harness_config.get_value",
        lambda _p, key: 16 if key == "gate_evidence_max_bytes" else None,
    )
    out = tmp_path / ".sessi-work" / "huge.txt"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("x" * 4096, encoding="utf-8")
    raw = {"breakdown": {"linting": {
        "score": 100.0, "tool_output": ".sessi-work/huge.txt"}}}

    persist_cited_evidence(tmp_path, 4, raw)

    assert raw["breakdown"]["linting"]["tool_output"] == ".sessi-work/huge.txt"
    ledger = (tmp_path / LEDGER_RELPATH).read_text(encoding="utf-8")
    assert "gate:evidence-too-large" in ledger
    assert "4096" in ledger
