"""Round 32 站0/站1/站2 — the proof that a gate was verified must cost what the
verification cost.

Measured on a live P4 run (`.sessi-work/sentinels/`, a consumer project):

    g1_p3_fr01.finalized   08-01 19:00:36   2026-08-01T11:00:36.636446+00:00   33 B
    g3_p4_phase.finalized  08-03 11:27:40   2026-08-03T03:27:39.443042+00:00   33 B
    g1_p4_fr01..fr08       08-03 11:55:09   2026-08-03T03:55:09+00:00          26 B
                                 ^ all eight, same second, no microseconds

The framework has exactly two writers of a `.finalized` sentinel:

    cli/gate_cmds.py:1920   datetime.now(timezone.utc).isoformat()  -> always µs
    cli/_shared.py:61,67    the literal "test-sentinel\\n"          -> test fixture

Neither can produce row three. One minute before those eight files appeared,
Gate 1 for FR-01 had BLOCKED with `tool_score_fabrication`; six minutes after,
the phase was recorded complete. The three registries that finalize-gate writes
alongside the sentinel held **zero** phase-4 gate-1 rows:

    .methodology/gate_timestamps.jsonl   10 rows, none with (phase=4, gate=1)
    .methodology/.gate1_scores.json      only the key "3"
    .methodology/gate_results/gate1/     8 files, all still at their P3 mtimes

Two independent defects put that state within reach, and both are pinned here.

1. The proof has no content contract. `_advance_prechecks` asks `.exists()`
   (cli/phase_cmds.py:1990) and `core/doctor.py` ORs three channels together
   (`if has_sentinel or fr_key in ts_frs`), so satisfying the cheapest one is
   enough — and the cheapest one is a file `date -u` can write. Worse, the
   sentinel glob doctor accepts includes `.flag`, which run-gate writes when
   the gate STARTS.

2. The framework itself can leave that state behind. In `cmd_finalize_gate`
   the sentinel is written at line 1920 and the registries at ~2170/2179, and
   between them sit FIVE blocking returns:

       2069 return 5   post-flight structural check failed
       2080 return 5   post-flight error at Gate 4
       2125 return 1   all dimension scores identical (the variance detector)
       2161 return 11  Phase Truth < 90%
       2166 return 11  PhaseTruthVerifier unavailable

   Every one of them blocks the gate *after* writing the file that says the
   gate finalized. Station 0's premise check (P2: "sentinel ⇒ registries has
   no legitimate counter-example") was FALSE because of these five, and the
   fix is the same one either way: the sentinel is written last, or it is not
   a receipt.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import harness_cli  # noqa: F401  entry-first load order
from cli._shared import _write_finalize_sentinels_for_tests  # noqa: E402
from core.quality_gate import gate1_evidence  # noqa: E402
from core.quality_gate.gate1_evidence import (  # noqa: E402
    GATE1_SCORES_FILE,
    GATE_TIMESTAMPS_FILE,
)

pytestmark = [pytest.mark.core]

REPO = Path(__file__).resolve().parents[1]


def _make_p3_project(tmp_path: Path) -> None:
    """The minimum `_advance_prechecks` needs before its sentinel check."""
    (tmp_path / ".methodology").mkdir()
    (tmp_path / "03-development" / "src").mkdir(parents=True)
    (tmp_path / "03-development" / "tests").mkdir(parents=True)
    (tmp_path / ".methodology" / "phase4_plan.md").touch()
    (tmp_path / ".methodology" / "quality_manifest.json").write_text(
        json.dumps({"fr_ids": ["FR-01"]}), encoding="utf-8"
    )


# ── the receipt itself ──────────────────────────────────────────────────

def test_a_bare_timestamp_is_not_a_receipt():
    """The format the forged files were in. A parser that accepts it accepts
    anything `date -u` can print, which is the whole defect."""
    assert gate1_evidence.read_finalize_receipt_text(
        "2026-08-03T03:55:09+00:00\n"
    ) is None, (
        "a bare timestamp parsed as a valid finalize receipt — that string "
        "carries no link to the gate result it claims to attest"
    )


def test_a_receipt_names_the_gate_result_it_attests(tmp_path):
    """A receipt costs what the verification cost: forging one means forging a
    gate result that passes S3/S4 and then computing its digest."""
    result = tmp_path / "gate1_result.json"
    result.write_text(json.dumps({"verdict": "PASS"}), encoding="utf-8")

    text = gate1_evidence.format_finalize_receipt(
        gate=1, phase=3, fr_id="FR-01", score=100.0, result_path=result,
        enforcer_sha="deadbeef",
    )
    parsed = gate1_evidence.read_finalize_receipt_text(text)
    assert parsed is not None, text
    assert parsed["gate"] == 1 and parsed["phase"] == 3
    assert parsed["fr_id"] == "FR-01" and parsed["score"] == 100.0
    assert parsed["result_sha256"], "the receipt does not fingerprint the result"

    # Change one byte of the result and the receipt no longer describes it.
    result.write_text(json.dumps({"verdict": "FAIL"}), encoding="utf-8")
    again = gate1_evidence.format_finalize_receipt(
        gate=1, phase=3, fr_id="FR-01", score=100.0, result_path=result,
        enforcer_sha="deadbeef",
    )
    assert (gate1_evidence.read_finalize_receipt_text(again)["result_sha256"]
            != parsed["result_sha256"])


def test_an_unreadable_receipt_reads_as_absent_not_as_valid():
    """Round 31's rule, one level down: a parse failure means 'this text
    carries no receipt', never 'the receipt says everything is fine'."""
    for junk in ("", "not json at all", "{}", '{"schema": 999}'):
        assert gate1_evidence.read_finalize_receipt_text(junk) is None, junk


# ── the reading end: advance-phase ──────────────────────────────────────

def test_a_hand_written_sentinel_does_not_clear_the_advance_check(tmp_path, monkeypatch):
    """The forged file, verbatim, against the check it defeated."""
    from cli import phase_cmds

    _make_p3_project(tmp_path)
    sentinel = gate1_evidence._finalize_sentinel_path(tmp_path, 1, "FR-01", phase=3)
    sentinel.parent.mkdir(parents=True, exist_ok=True)
    sentinel.write_text("2026-08-03T03:55:09+00:00\n", encoding="utf-8")
    for gate, phase in ((2, 3), (3, 4), (4, 6)):
        p = gate1_evidence._finalize_sentinel_path(tmp_path, gate, None, phase=phase)
        p.write_text("2026-08-03T03:55:09+00:00\n", encoding="utf-8")

    violations = gate1_evidence.verify_finalize_evidence(tmp_path, 1, 3, "FR-01")
    assert violations, (
        "a sentinel whose entire content is a hand-typeable timestamp was "
        "accepted as proof that finalize-gate ran and passed"
    )
    assert any("receipt" in v.lower() for v in violations), violations
    _ = phase_cmds  # the consumer this function exists for (wired in 站2)


def test_a_receipt_with_no_registry_rows_is_rejected(tmp_path):
    """The measured combination: proof present, every registry empty. No
    framework path produces it once the sentinel is written last."""
    _make_p3_project(tmp_path)
    result = tmp_path / ".methodology" / "gate1_result.json"
    result.write_text(json.dumps({"verdict": "PASS"}), encoding="utf-8")
    gate1_evidence.write_finalize_receipt(
        tmp_path, gate=1, phase=3, fr_id="FR-01", score=100.0, result_path=result,
    )

    violations = gate1_evidence.verify_finalize_evidence(tmp_path, 1, 3, "FR-01")
    assert violations, (
        "a receipt with no gate_timestamps row and no .gate1_scores.json entry "
        "was accepted — those three files have one author, so two of them "
        "being empty means the third was not written by that author"
    )


def test_each_half_of_the_rule_is_load_bearing(tmp_path):
    """Written after the station-2 counter-proof failed to go red.

    The test above seeds a receipt with BOTH registries empty, so removing
    either half of the check still leaves the other one firing — it cannot
    tell which half is doing the work. These two cases isolate them: one
    registry populated, the other not.
    """
    result = tmp_path / ".methodology" / "gate1_result.json"

    # (a) score recorded, no finalize timestamp row
    _make_p3_project(tmp_path)
    result.write_text(json.dumps({"verdict": "PASS"}), encoding="utf-8")
    gate1_evidence.write_finalize_receipt(
        tmp_path, gate=1, phase=3, fr_id="FR-01", score=100.0, result_path=result,
    )
    gate1_evidence.record_gate1_score(tmp_path, 3, "FR-01", 100.0)
    problems = gate1_evidence.verify_finalize_evidence(tmp_path, 1, 3, "FR-01")
    assert any(GATE_TIMESTAMPS_FILE in p for p in problems), problems

    # (b) timestamp row present, no recorded score
    gate1_evidence.record_gate_timestamp(tmp_path, 3, 1, "FR-01")
    (tmp_path / ".methodology" / GATE1_SCORES_FILE).unlink()
    problems = gate1_evidence.verify_finalize_evidence(tmp_path, 1, 3, "FR-01")
    assert any(GATE1_SCORES_FILE in p for p in problems), problems


def test_a_receipt_whose_score_contradicts_the_registry_is_rejected(tmp_path):
    """Both registries populated, and they disagree with the receipt. Round 24's
    rule: a field being present is not the same as its content being true."""
    _make_p3_project(tmp_path)
    result = tmp_path / ".methodology" / "gate1_result.json"
    result.write_text(json.dumps({"verdict": "PASS"}), encoding="utf-8")
    gate1_evidence.write_finalize_receipt(
        tmp_path, gate=1, phase=3, fr_id="FR-01", score=100.0, result_path=result,
    )
    gate1_evidence.record_gate_timestamp(tmp_path, 3, 1, "FR-01")
    gate1_evidence.record_gate1_score(tmp_path, 3, "FR-01", 62.0)
    problems = gate1_evidence.verify_finalize_evidence(tmp_path, 1, 3, "FR-01")
    assert any("does not match" in p for p in problems), problems


def test_the_legitimate_combination_passes(tmp_path):
    """The counter-case, so the check cannot be satisfied by blocking
    everything: a genuine finalize writes all three, and that must pass.

    Round 45 站3: a genuine finalize writes a FOURTH thing — the FR's own
    `gate_results/gate1/FR-01.json`, which cli/gate_cmds.py has persisted since
    2026-07-15 and which the receipt now points at. Writing it here is what
    makes this fixture the legitimate combination rather than three quarters
    of one.
    """
    _make_p3_project(tmp_path)
    per_fr = gate1_evidence.per_fr_result_path(tmp_path, 1, "FR-01")
    per_fr.parent.mkdir(parents=True, exist_ok=True)
    per_fr.write_text(json.dumps({"verdict": "PASS", "fr_id": "FR-01"}),
                      encoding="utf-8")
    result = tmp_path / ".methodology" / "gate1_result.json"
    result.write_text(json.dumps({"verdict": "PASS"}), encoding="utf-8")
    gate1_evidence.write_finalize_receipt(
        tmp_path, gate=1, phase=3, fr_id="FR-01", score=100.0, result_path=result,
    )
    gate1_evidence.record_gate_timestamp(tmp_path, 3, 1, "FR-01")
    gate1_evidence.record_gate1_score(tmp_path, 3, "FR-01", 100.0)

    assert gate1_evidence.verify_finalize_evidence(tmp_path, 1, 3, "FR-01") == []


def test_the_test_fixture_helper_produces_a_valid_receipt(tmp_path):
    """`_write_finalize_sentinels_for_tests` is the test-side mirror of the
    production writer. If it keeps writing "test-sentinel\\n" while the
    production path writes receipts, every test using it passes for a reason
    the real code path does not have."""
    _make_p3_project(tmp_path)
    _write_finalize_sentinels_for_tests(tmp_path, ["FR-01"], phase=3)
    path = gate1_evidence._finalize_sentinel_path(tmp_path, 1, "FR-01", phase=3)
    assert gate1_evidence.read_finalize_receipt_text(
        path.read_text(encoding="utf-8")
    ) is not None, (
        "the fixture helper writes a shape the production reader rejects — "
        "tests would then be exercising a path production never takes"
    )


# ── the reading end: doctor ─────────────────────────────────────────────

def test_doctor_does_not_accept_a_run_gate_flag_as_proof_of_a_pass(tmp_path):
    """`.flag` is written when run-gate STARTS. On the measured project
    `g1_p4_fr01.flag` was written at 03:49:53 and the gate BLOCKED at 03:54:07
    — doctor's glob counts that flag as evidence the FR passed."""
    from core import doctor
    from core.utils.project_layout import ProjectLayout

    _make_p3_project(tmp_path)
    (tmp_path / ".methodology" / "quality_manifest.json").write_text(
        json.dumps({
            "fr_ids": ["FR-01"],
            "gate_results": {"gate1": {"FR-01": {"quality_complete": True}}},
        }),
        encoding="utf-8",
    )
    flag = gate1_evidence._sentinel_path(tmp_path, 1, "FR-01", phase=4)
    flag.parent.mkdir(parents=True, exist_ok=True)
    flag.write_text("2026-08-03T03:49:53.359817+00:00\n", encoding="utf-8")

    findings = doctor._check_gate1_evidence(tmp_path, ProjectLayout(tmp_path))
    assert findings, (
        "doctor accepted a run-gate start flag as evidence that Gate 1 passed "
        "for FR-01 — the flag is written before the gate is evaluated"
    )


def test_doctor_and_advance_phase_share_one_implementation():
    """Two consumers each hand-rolling the same evidence rule is how the OR
    survived: doctor's version and advance-phase's version disagreed about
    what counts, and neither knew about the other."""
    import inspect

    from core import doctor

    src = inspect.getsource(doctor._check_gate1_evidence)
    assert "verify_finalize_evidence" in src, (
        "doctor still carries its own copy of the evidence rule instead of "
        "calling the one gate1_evidence exports"
    )


# ── the framework's own hole ────────────────────────────────────────────

def test_finalize_gate_writes_its_proof_after_everything_it_proves():
    """P2's falsification, as an invariant.

    In cmd_finalize_gate the sentinel write sat ~250 lines above the registry
    writes with five blocking `return`s in between, so a gate that blocked on
    post-flight, on the identical-scores fabrication detector, or on Phase
    Truth still left behind the file that says it finalized.

    Asserted on source order rather than by driving a full finalize-gate: the
    invariant IS "nothing can return between them", and the cheapest honest
    way to state that is that there is nothing between them at all.
    """
    src = (REPO / "cli" / "gate_cmds.py").read_text(encoding="utf-8").splitlines()
    receipt_line = timestamp_line = None
    for idx, line in enumerate(src, 1):
        if "write_finalize_receipt(" in line:
            receipt_line = idx
        if "record_gate_timestamp(" in line and "gate1_evidence" in line:
            timestamp_line = idx
    assert receipt_line and timestamp_line, (receipt_line, timestamp_line)
    assert receipt_line > timestamp_line, (
        f"cmd_finalize_gate writes the finalize receipt at line {receipt_line}, "
        f"before it records the gate timestamp at line {timestamp_line}. Every "
        f"blocking return in between leaves proof of a pass that did not happen "
        f"(measured: 5 such returns at 2069/2080/2125/2161/2166)."
    )


def test_only_one_function_writes_a_finalize_sentinel():
    """Premise P1, pinned and tightened. Before this round the sentinel had
    one production writer that spelled the format inline; now it has one
    function, and every other caller — including the fixture helper — goes
    through it. A second writer would mean a second, weaker format."""
    import ast

    allowed = {
        ("core/quality_gate/gate1_evidence.py", "write_finalize_receipt"),
    }
    writers: set[tuple[str, str]] = set()
    for path in sorted(REPO.rglob("*.py")):
        rel = path.relative_to(REPO).as_posix()
        if rel.startswith((".venv/", "tests/")) or "/.venv/" in rel:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            body = ast.dump(node)
            if "_finalize_sentinel_path" in body and "write_text" in body:
                writers.add((rel, node.name))
    assert writers <= allowed, (
        f"a site outside finalize-gate writes a finalize sentinel: "
        f"{sorted(writers - allowed)}. The receipt has one author by design."
    )
