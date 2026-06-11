"""v2.9 C4+C5 — mutation survivor persistence and the hunt targeting manifest."""

import argparse
import json
from pathlib import Path

import harness_cli
from core.quality_gate.mutation_enforcer import (
    _parse_mutmut_survivors,
    _write_survivors_artifact,
)


MUTMUT_RESULTS = """\
To apply a mutant on disk:
    mutmut apply <id>

Survived 🙁 (3)

---- src/metrics.py (2) ----

10, 24

---- src/splitter.py (1) ----

7
"""


class TestSurvivorPersistence:
    def test_parse_mutmut_grouped_format(self):
        survivors = _parse_mutmut_survivors(MUTMUT_RESULTS)
        assert [(s["file"], s["mutant_id"]) for s in survivors] == [
            ("src/metrics.py", "10"), ("src/metrics.py", "24"),
            ("src/splitter.py", "7"),
        ]

    def test_unrecognized_format_yields_empty(self):
        assert _parse_mutmut_survivors("Killed 120 of 120") == []

    def test_artifact_written_with_raw_tail(self, tmp_path):
        _write_survivors_artifact(
            tmp_path, "mutmut", _parse_mutmut_survivors(MUTMUT_RESULTS),
            raw=MUTMUT_RESULTS,
        )
        data = json.loads(
            (tmp_path / ".methodology" / "mutation_survivors.json").read_text()
        )
        assert data["tool"] == "mutmut"
        assert data["survivor_count"] == 3
        assert "raw" in data

    def test_empty_pass_still_writes_evidence(self, tmp_path):
        _write_survivors_artifact(tmp_path, "stryker", [])
        data = json.loads(
            (tmp_path / ".methodology" / "mutation_survivors.json").read_text()
        )
        assert data["survivor_count"] == 0 and data["survivors"] == []


def _project(tmp_path: Path) -> Path:
    meth = tmp_path / ".methodology"
    meth.mkdir()
    (meth / "state.json").write_text(
        json.dumps({"state": "RUNNING", "current_phase": 4,
                    "language": "python"}), encoding="utf-8")
    src = tmp_path / "src"
    src.mkdir()
    for name in ("synthesis.py", "metrics.py", "models.py"):
        (src / name).write_text("def f():\n    return 1\n", encoding="utf-8")
    return tmp_path


class TestBugHuntTargets:
    def _run(self, project: Path) -> dict:
        rc = harness_cli.cmd_bug_hunt_targets(
            argparse.Namespace(project=str(project))
        )
        assert rc == 0
        return json.loads(
            (project / ".methodology" / "bug_hunt_targets.json").read_text()
        )

    def test_aggregates_all_sources(self, tmp_path):
        project = _project(tmp_path)
        (project / ".methodology" / "quality_manifest.json").write_text(json.dumps({
            "high_risk_modules": [
                {"path": "src/synthesis.py", "risk": "parallel dispatch + concat"},
            ],
        }), encoding="utf-8")
        (project / ".sessi-work").mkdir()
        (project / ".sessi-work" / "crg_metrics.json").write_text(json.dumps({
            "hub_risk_map": {"hubs": [
                {"name": "convert", "file": "src/models.py", "fan_in": 9,
                 "untested": True, "severity": "high"},
                {"name": "minor", "file": "src/metrics.py", "fan_in": 2,
                 "untested": False, "severity": "low"},
            ]},
        }), encoding="utf-8")
        _write_survivors_artifact(
            project, "mutmut",
            [{"file": "src/metrics.py", "line": None, "mutant_id": str(i),
              "mutator": None} for i in range(4)],
        )
        (project / ".methodology" / "gate3_result.json").write_text(json.dumps({
            "breakdown": {"integration_coverage": {"score": 78.0}},
        }), encoding="utf-8")

        targets = self._run(project)
        high = {t["path"]: t["reasons"] for t in targets["high_risk"]}
        # declared + crg high hub + survivor-dense file all promoted
        assert set(high) == {"src/synthesis.py", "src/models.py", "src/metrics.py"}
        assert any("declared" in r for r in high["src/synthesis.py"])
        assert any(r.startswith("crg_hub:high") for r in high["src/models.py"])
        assert any(r == "mutation_survivors:4" for r in high["src/metrics.py"])
        # low-severity hub did NOT promote metrics on its own
        assert all(not r.startswith("crg_hub") for r in high["src/metrics.py"])
        assert targets["integration_coverage"] == {"gate": 3, "score": 78.0}
        assert targets["standard"] == []  # all three promoted
        assert targets["sources"]["mutation_survivors"] == 4

    def test_no_signals_still_produces_inventory(self, tmp_path):
        project = _project(tmp_path)
        targets = self._run(project)
        assert targets["high_risk"] == []
        assert {t["path"] for t in targets["standard"]} == {
            "src/synthesis.py", "src/metrics.py", "src/models.py",
        }
        assert targets["language"] == "python"

    def test_sparse_survivors_annotate_standard_tier(self, tmp_path):
        project = _project(tmp_path)
        _write_survivors_artifact(
            project, "stryker",
            [{"file": "src/models.py", "line": 3, "mutant_id": "m1",
              "mutator": "EqualityOperator"}],
        )
        targets = self._run(project)
        assert targets["high_risk"] == []  # 1 survivor < 3 → not promoted
        models = next(t for t in targets["standard"]
                      if t["path"] == "src/models.py")
        assert models["survivors"] == 1
