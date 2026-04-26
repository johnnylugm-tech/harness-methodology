# harness/harness_bridge.py
# Bridge between software_self_improvement and methodology-v2.
# Handles: Gate triggering, CRG integration, result parsing, blocking decisions,
# quality_manifest updates.
from __future__ import annotations
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from harness.crg_bridge import CRGBridge
from harness.decision_log import DecisionLogWriter, DecisionLogEntry
from harness.effort_tracker import EffortTracker, EffortRecord


@dataclass
class DimResult:
    name: str
    score: float
    threshold: float
    issues: list[dict] = field(default_factory=list)


@dataclass
class GateResult:
    gate_num: int
    score: float
    dimensions: list[DimResult] = field(default_factory=list)
    open_critical: int = 0
    open_high: int = 0
    quality_complete: bool = False
    rounds_used: int = 0


class GateBlockedError(Exception):
    def __init__(self, gate_num: int, result: GateResult):
        self.gate_num = gate_num
        self.result = result
        super().__init__(
            f"Gate {gate_num} BLOCKED — score={result.score:.1f}, "
            f"critical={result.open_critical}, high={result.open_high}"
        )


class HarnessBridge:
    """
    Bridge layer between software_self_improvement and methodology-v2.
    Gate triggering, CRG integration, blocking decisions, quality_manifest updates.
    """

    def __init__(self):
        self.crg = CRGBridge()        # gracefully degrades if CRG unavailable
        self._log = DecisionLogWriter()
        self._effort = EffortTracker()

    # ------------------------------------------------------------------ Public API

    def run_gate(
        self,
        gate_num: int,
        project_root: str,
        phase: int,
        fr_id: str | None = None,
    ) -> GateResult:
        config = self._load_config(gate_num)
        t0 = time.time()

        # § 6.5 Point 1 — CRG Reconnaissance at Gate 3/4 entry
        if config.get("crg", {}).get("reconnaissance"):
            self.crg.run_reconnaissance(project_root)

        result = self._invoke_harness(config, project_root, fr_id)
        self._update_quality_manifest(gate_num, fr_id, result)

        self._effort.record(EffortRecord(
            phase=phase, gate_num=gate_num, agent_id="GATE",
            operation="gate_run", duration_s=time.time() - t0,
        ))
        self._log.write(DecisionLogEntry(
            agent_id="GATE", phase=phase, fr_id=fr_id,
            decision="GATE_PASS" if result.quality_complete else "GATE_BLOCK",
            reasoning=(
                f"Gate {gate_num}: score={result.score:.1f}, "
                f"critical={result.open_critical}, high={result.open_high}, "
                f"rounds={result.rounds_used}"
            ),
            gate_score=result.score,
        ))

        # Gate 1: per-dim threshold (no composite score_gate)
        if gate_num == 1:
            if any(d.score < d.threshold for d in result.dimensions):
                raise GateBlockedError(gate_num, result)
        else:
            # Gates 2/3/4: composite score < score_gate OR not quality_complete
            if result.score < config.get("score_gate", 0) or not result.quality_complete:
                raise GateBlockedError(gate_num, result)

        return result

    def generate_quality_manifest(self, fr_ids: list[str], sad_path: str) -> Path:
        """Called at P2 exit. Parses SAD.md → constraints + high_risk_modules."""
        try:
            from scripts.generate_sab import parse_sad
            sab = parse_sad(sad_path)
        except Exception:
            sab = {}

        manifest: dict[str, Any] = {
            "schema_version": "1.0",
            "generated_at_phase": 2,
            "fr_ids": fr_ids,
            "nfr_dimension_mapping": sab.get("nfr_dim_map", {}),
            "architecture_constraints": sab.get("constraints", []),
            "high_risk_modules": sab.get("high_risk", []),
            "gate_score_overrides": {},
            "gate_results": {"gate1": {}, "gate2": None, "gate3": None, "gate4": None},
        }
        out = Path(".methodology/quality_manifest.json")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(manifest, indent=2, ensure_ascii=False))
        return out

    # ------------------------------------------------------------------ Internal

    def _load_config(self, gate_num: int) -> dict:
        import yaml
        names = {1: "gate1_per_fr.yaml", 2: "gate2_p3_exit.yaml",
                 3: "gate3_p4_exit.yaml", 4: "gate4_p6_full.yaml"}
        with open(Path(__file__).parent / "gate_configs" / names[gate_num]) as f:
            return yaml.safe_load(f)

    def _invoke_harness(self, config: dict, project_root: str, fr_id: str | None) -> GateResult:
        """
        Invoke software_self_improvement runner (Steps 3a-3f loop).
        max_rounds / early_stop / saturation_rounds from config.

        TODO: Replace stub with real SSI runner:
            subprocess.run(["python3", "-m", "software_self_improvement.runner",
                           "--config", config_path, "--root", project_root])
        """
        raise NotImplementedError(
            "Wire up software_self_improvement runner. "
            "Interface: run(config, project_root, fr_id) -> GateResult"
        )

    def _update_quality_manifest(
        self, gate_num: int, fr_id: str | None, result: GateResult
    ) -> None:
        p = Path(".methodology/quality_manifest.json")
        if not p.exists():
            return
        manifest = json.loads(p.read_text())
        key = f"gate{gate_num}"
        payload: dict[str, Any] = {
            "score": result.score, "quality_complete": result.quality_complete,
            "rounds_used": result.rounds_used, "open_critical": result.open_critical,
            "open_high": result.open_high,
        }
        if fr_id:
            if not isinstance(manifest["gate_results"][key], dict):
                manifest["gate_results"][key] = {}
            manifest["gate_results"][key][fr_id] = payload
        else:
            manifest["gate_results"][key] = payload
        p.write_text(json.dumps(manifest, indent=2, ensure_ascii=False))
