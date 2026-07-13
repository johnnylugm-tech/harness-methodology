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

    def test_threat_model_source_seeds_high_risk(self, tmp_path):
        """Round 10: a SAD.md §6 threat's owner_module is a forced
        attack-vector seed, resolved to its on-disk path (same candidate
        expansion preflight_sab_check uses for SAB modules)."""
        project = _project(tmp_path)
        (project / "SAD.md").write_text(
            "# SAD\n\n<!-- SEC:START -->\n```yaml\n"
            'security_design:\n'
            '  version: "1.0"\n'
            "  applicability: full\n"
            "  trust_boundaries:\n"
            "    - id: TB-01\n"
            '      name: "model input"\n'
            "  threats:\n"
            "    - id: T-01\n"
            "      boundary: TB-01\n"
            "      category: tampering\n"
            '      description: "malicious model params"\n'
            '      mitigation: "schema validation"\n'
            '      owner_module: "models"\n'
            '      verified_by: "test_sec_t01_ok"\n'
            "```\n<!-- SEC:END -->\n",
            encoding="utf-8",
        )
        targets = self._run(project)
        high = {t["path"]: t["reasons"] for t in targets["high_risk"]}
        assert "src/models.py" in high
        assert any(r.startswith("threat_model:T-01") for r in high["src/models.py"])
        assert targets["sources"]["threat_model"] == 1
        assert targets["threat_model"][0]["threat_id"] == "T-01"
        assert targets["threat_model"][0]["owner_module"] == "models"

    def test_no_sec_block_yields_zero_threat_model(self, tmp_path):
        project = _project(tmp_path)
        targets = self._run(project)
        assert targets["sources"]["threat_model"] == 0
        assert targets["threat_model"] == []

    def test_applicability_none_yields_zero_threat_model(self, tmp_path):
        project = _project(tmp_path)
        (project / "SAD.md").write_text(
            "# SAD\n\n<!-- SEC:START -->\n```yaml\n"
            'security_design:\n'
            '  version: "1.0"\n'
            "  applicability: none\n"
            '  justification: "pure CLI formatting tool, no attack surface."\n'
            "```\n<!-- SEC:END -->\n",
            encoding="utf-8",
        )
        targets = self._run(project)
        assert targets["sources"]["threat_model"] == 0
        assert targets["threat_model"] == []
