"""v2.9 C1+C2 — bug-hunt report verifier and the adversarial_review override."""

import json
from pathlib import Path

import yaml

from core.quality_gate.bug_hunt_verifier import verify_bug_hunt_report
from harness.harness_bridge import (
    DimResult,
    _override_adversarial_review_dim_score,
)

REPO_ROOT = Path(__file__).parent.parent


def _finding(fid="cb#9", severity="critical", status="open", confirmed=True,
             **resolution_extra):
    return {
        "id": fid, "module": "circuit_breaker", "lens": "concurrency",
        "severity": severity, "title": "HALF_OPEN multi-probe",
        "file": "src/cb.py", "line_start": 103,
        "reasoning": "no lock around state transition", "confidence": "high",
        "confirmed": confirmed,
        "resolution": {"status": status, **resolution_extra},
    }


def _report(tmp_path: Path, findings, git_sha="abc123") -> Path:
    meth = tmp_path / ".methodology"
    meth.mkdir(exist_ok=True)
    path = meth / "bug_hunt_report.json"
    path.write_text(json.dumps({
        "generated_at": "2026-06-11T00:00:00+00:00",
        "git_sha": git_sha,
        "lenses": ["correctness", "concurrency", "resilience", "general"],
        "findings": findings,
    }), encoding="utf-8")
    return path


class TestVerifier:
    def test_missing_report_blocks_with_hunt_pointer(self, tmp_path):
        verdict = verify_bug_hunt_report(str(tmp_path))
        assert verdict.ok is False and verdict.score == 0.0
        assert verdict.report_found is False
        assert "hunt_bugs.md" in verdict.reasons[0]

    def test_open_critical_blocks(self, tmp_path):
        _report(tmp_path, [_finding(status="open")])
        verdict = verify_bug_hunt_report(str(tmp_path))
        assert verdict.ok is False
        assert verdict.open_blocking == 1
        assert "OPEN" in verdict.reasons[0]

    def test_open_high_blocks_too(self, tmp_path):
        _report(tmp_path, [_finding(severity="high", status="open")])
        assert verify_bug_hunt_report(str(tmp_path)).ok is False

    def test_open_medium_low_do_not_block(self, tmp_path):
        _report(tmp_path, [
            _finding(fid="m#1", severity="medium", status="open"),
            _finding(fid="l#1", severity="low", status="open"),
        ])
        verdict = verify_bug_hunt_report(str(tmp_path))
        assert verdict.ok is True and verdict.score == 100.0

    def test_unconfirmed_finding_never_blocks(self, tmp_path):
        _report(tmp_path, [_finding(status="open", confirmed=False)])
        assert verify_bug_hunt_report(str(tmp_path)).ok is True

    def test_resolved_needs_evidence(self, tmp_path):
        _report(tmp_path, [_finding(status="resolved")])  # no evidence
        verdict = verify_bug_hunt_report(str(tmp_path))
        assert verdict.ok is False
        assert "anti-fabrication" in verdict.reasons[0]

    def test_resolved_with_existing_repro_test_passes(self, tmp_path):
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_fr09_probe_race.py").write_text(
            "def test_fr09_probe_race():\n    assert True\n", encoding="utf-8")
        _report(tmp_path, [_finding(
            status="resolved", repro_test="tests/test_fr09_probe_race.py")])
        assert verify_bug_hunt_report(str(tmp_path)).ok is True

    def test_resolved_with_nonexistent_repro_test_blocks(self, tmp_path):
        _report(tmp_path, [_finding(
            status="resolved", repro_test="tests/test_ghost.py")])
        verdict = verify_bug_hunt_report(str(tmp_path))
        assert verdict.ok is False
        assert "does not exist" in verdict.reasons[0]

    def test_resolved_with_fix_commit_passes(self, tmp_path):
        _report(tmp_path, [_finding(status="resolved", fix_commit="dcc9f0b")])
        assert verify_bug_hunt_report(str(tmp_path)).ok is True

    def test_refuted_needs_evidence(self, tmp_path):
        _report(tmp_path, [_finding(status="refuted")])
        verdict = verify_bug_hunt_report(str(tmp_path))
        assert verdict.ok is False
        assert "refute_evidence" in verdict.reasons[0]

    def test_refuted_with_evidence_passes(self, tmp_path):
        _report(tmp_path, [_finding(
            status="refuted",
            refute_evidence="reset() is FSM-internal; external pollution "
                            "not in attack surface (cb#2 precedent)")])
        assert verify_bug_hunt_report(str(tmp_path)).ok is True

    def test_structurally_invalid_report_blocks(self, tmp_path):
        meth = tmp_path / ".methodology"
        meth.mkdir()
        (meth / "bug_hunt_report.json").write_text(
            json.dumps({"findings": "not-a-list"}), encoding="utf-8")
        verdict = verify_bug_hunt_report(str(tmp_path))
        assert verdict.ok is False
        assert any("must be a list" in r for r in verdict.reasons)

    def test_schema_file_matches_verifier_contract(self):
        schema = json.loads(
            (REPO_ROOT / "schemas" / "bug_hunt_report.schema.json").read_text()
        )
        finding_required = set(
            schema["properties"]["findings"]["items"]["required"]
        )
        from core.quality_gate import bug_hunt_verifier as bhv
        assert set(bhv._REQUIRED_FINDING_FIELDS) <= finding_required
        assert set(bhv._REQUIRED_TOP_FIELDS) <= set(schema["required"])


class TestGateOverride:
    CONFIG_DIMS = [{"name": "adversarial_review", "threshold": 100,
                    "requires_tool_execution": False}]

    def test_appended_when_agent_omits_dimension(self, tmp_path):
        _report(tmp_path, [_finding(status="open")])
        dims = [DimResult(name="linting", score=95.0, threshold=90.0)]
        new_dims, changed = _override_adversarial_review_dim_score(
            dims, str(tmp_path), self.CONFIG_DIMS)
        assert changed is True
        ar = next(d for d in new_dims if d.name == "adversarial_review")
        assert ar.score == 0.0 and ar.threshold == 100.0
        assert ar.issues  # reasons surfaced for the agent

    def test_agent_self_score_overridden(self, tmp_path):
        _report(tmp_path, [_finding(status="open")])
        dims = [DimResult(name="adversarial_review", score=100.0, threshold=100.0)]
        new_dims, changed = _override_adversarial_review_dim_score(
            dims, str(tmp_path), self.CONFIG_DIMS)
        assert changed is True
        assert new_dims[0].score == 0.0  # agent's optimistic 100 replaced

    def test_clean_report_scores_100(self, tmp_path):
        _report(tmp_path, [_finding(status="resolved", fix_commit="abc")])
        new_dims, _ = _override_adversarial_review_dim_score(
            [], str(tmp_path), self.CONFIG_DIMS)
        assert new_dims[0].score == 100.0

    def test_noop_when_gate_config_lacks_dimension(self, tmp_path):
        dims = [DimResult(name="linting", score=95.0, threshold=90.0)]
        new_dims, changed = _override_adversarial_review_dim_score(
            dims, str(tmp_path), [{"name": "linting"}])
        assert changed is False and new_dims == dims

    def test_gate3_yaml_declares_dimension(self):
        cfg = yaml.safe_load(
            (REPO_ROOT / "harness" / "gate_configs" / "gate3_p4_exit.yaml")
            .read_text(encoding="utf-8")
        )
        ar = next(d for d in cfg["dimensions"]
                  if d["name"] == "adversarial_review")
        assert ar["threshold"] == 100
        assert ar["weight"] == 0.00
        assert ar["requires_tool_execution"] is False
