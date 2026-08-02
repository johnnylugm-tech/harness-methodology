"""Round 27 站3 — a gate verdict outlives its evidence, so it must carry proof.

Measured on taskq-plus's Gate 4: 13 of 14 dimensions cite a `tool_output` under
`.sessi-work/`, which is gitignored and has since been cleaned. The verdict
itself (composite 98.707, PASS) is committed and permanent. S3 verified those
files existed and looked genuine — at the time — and nothing carried that
verification forward, so the gate cannot be re-checked by anyone.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from harness.harness_bridge import GateContext, _check_tool_evidence


def _ctx(root: Path, gate: int = 4) -> GateContext:
    return GateContext(
        gate_num=gate, config={}, project_root=str(root), phase=6, fr_id=None,
        ssi_scripts_dir="", ssi_prompts_dir="", ssi_schemas_dir="",
        work_dir=str(root / ".sessi-work"), sab_data={},
    )


def _gate_yaml(tmp_path: Path, gate: int, dims: list[dict], monkeypatch) -> None:
    import yaml
    import core.quality_gate.gate_thresholds as _gt
    cfg_path = tmp_path / f"gate{gate}_p6_full.yaml"
    cfg_path.write_text(yaml.dump({"gate": gate, "dimensions": dims}))
    monkeypatch.setattr(_gt, "gate_config_path", lambda g: cfg_path)


class TestDigestPrimitives:
    def test_file_digest_matches_sha256_of_the_bytes(self, tmp_path):
        from core.quality_gate.evidence_digest import digest_of_file
        p = tmp_path / "linting.txt"
        p.write_text("All checks passed!\n", encoding="utf-8")
        d = digest_of_file(p, source=".sessi-work/round_1/tools/linting.txt")
        assert d["sha256"] == hashlib.sha256(p.read_bytes()).hexdigest()
        assert d["bytes"] == len(p.read_bytes())
        assert "All checks passed" in d["head"]
        assert d["source"] == ".sessi-work/round_1/tools/linting.txt"

    def test_head_is_bounded(self, tmp_path):
        """The head is a look at the evidence, not a second copy of it."""
        from core.quality_gate.evidence_digest import HEAD_MAX_LINES, digest_of_file
        p = tmp_path / "big.txt"
        p.write_text("\n".join(f"line {i}" for i in range(5000)), encoding="utf-8")
        d = digest_of_file(p, source="big.txt")
        assert len(d["head"].splitlines()) <= HEAD_MAX_LINES
        assert d["bytes"] > len(d["head"])

    def test_unreadable_file_is_recorded_not_swallowed(self, tmp_path):
        from core.quality_gate.evidence_digest import digest_of_file
        d = digest_of_file(tmp_path / "gone.txt", source="gone.txt")
        assert "error" in d and "unreadable" in d["error"]


class TestS3RecordsWhatItCleared:
    def test_a_digest_is_taken_for_evidence_that_passes(self, tmp_path, monkeypatch):
        _gate_yaml(tmp_path, 4, [
            {"name": "linting", "requires_tool_execution": True, "tool": "ruff",
             "threshold": 90},
        ], monkeypatch)
        out = tmp_path / ".sessi-work" / "tools" / "linting.txt"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("All checks passed!\n", encoding="utf-8")
        raw = {"breakdown": {"linting": {
            "score": 100.0, "tool_output": ".sessi-work/tools/linting.txt"}}}

        digests: dict = {}
        assert _check_tool_evidence(_ctx(tmp_path), raw, digests) == []
        assert digests["linting"]["sha256"] == hashlib.sha256(out.read_bytes()).hexdigest()

    def test_the_digest_survives_the_evidence(self, tmp_path, monkeypatch):
        """The whole point: delete the file, and the verdict can still say what
        it read. This is the taskq-plus Gate 4 situation, replayed."""
        _gate_yaml(tmp_path, 4, [
            {"name": "linting", "requires_tool_execution": True, "tool": "ruff",
             "threshold": 90},
        ], monkeypatch)
        out = tmp_path / ".sessi-work" / "tools" / "linting.txt"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("All checks passed!\n", encoding="utf-8")
        raw = {"breakdown": {"linting": {
            "score": 100.0, "tool_output": ".sessi-work/tools/linting.txt"}}}
        digests: dict = {}
        _check_tool_evidence(_ctx(tmp_path), raw, digests)

        recorded = json.loads(json.dumps(digests))   # as it lands in the result
        out.unlink()
        assert not out.exists()
        assert recorded["linting"]["sha256"]
        assert "All checks passed" in recorded["linting"]["head"]

    def test_rejected_evidence_is_not_fingerprinted(self, tmp_path, monkeypatch):
        """A digest asserts "this passed", so failing content must not get one —
        otherwise the record would vouch for evidence the gate refused."""
        _gate_yaml(tmp_path, 4, [
            {"name": "linting", "requires_tool_execution": True, "tool": "ruff",
             "threshold": 90},
        ], monkeypatch)
        raw = {"breakdown": {"linting": {
            "score": 100.0,
            "tool_evidence": "Reviewed the code by hand and it looked clean to me."}}}
        digests: dict = {}
        violations = _check_tool_evidence(_ctx(tmp_path), raw, digests)
        assert violations
        assert digests == {}

    def test_digests_are_optional(self, tmp_path, monkeypatch):
        """Callers that only want the verdict pass nothing and see no change."""
        _gate_yaml(tmp_path, 4, [
            {"name": "linting", "requires_tool_execution": True, "tool": "ruff",
             "threshold": 90},
        ], monkeypatch)
        raw = {"breakdown": {"linting": {
            "score": 100.0, "tool_evidence": "All checks passed!"}}}
        assert _check_tool_evidence(_ctx(tmp_path), raw) == []


class TestLedgerOutlivesTheWorkDirectory:
    def test_the_ledger_is_not_in_the_gitignored_work_dir(self):
        """taskq-plus's run recorded 7 turn-budget exhaustions and the ledger was
        absent afterwards — with no way to tell "never written" from "written and
        then cleaned", which is the question the ledger exists to answer."""
        from core.degradation_ledger import LEDGER_RELPATH
        assert not LEDGER_RELPATH.startswith(".sessi-work/")
        assert LEDGER_RELPATH.startswith(".methodology/")

    def test_run_report_names_the_real_path(self, tmp_path):
        """The report header used to spell the path out by hand, so a move would
        have left it pointing at a file nobody writes any more."""
        from core.degradation_ledger import LEDGER_RELPATH, record_degradation
        from cli.report_cmds import _render_human, build_report
        record_degradation(tmp_path, component="probe", what="something gave way")
        assert (tmp_path / LEDGER_RELPATH).is_file()
        text = _render_human(build_report(tmp_path))
        assert LEDGER_RELPATH in text
        assert ".sessi-work/degradations.jsonl" not in text
