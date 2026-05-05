"""
Harness Bridge: Integration layer between the quality harness and the methodology.

Handles gate execution, results parsing, and quality manifest updates.
"""

from __future__ import annotations
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from harness.crg_bridge import CRGBridge
from harness.decision_log import DecisionLogWriter, DecisionLogEntry, DecisionContext
from harness.effort_tracker import EffortTracker, EffortRecord


@dataclass
class DimResult:
    """Result of a single quality dimension evaluation."""
    name: str
    score: float
    threshold: float
    issues: list[dict] = field(default_factory=list)


@dataclass
class GateResult:
    """Summary result of a quality gate execution."""
    gate_num: int
    score: float
    dimensions: list[DimResult] = field(default_factory=list)
    open_critical: int = 0
    open_high: int = 0
    quality_complete: bool = False
    rounds_used: int = 0


class GateBlockedError(Exception):
    """Exception raised when a quality gate fails to meet its targets."""
    def __init__(self, gate_num: int, result: GateResult):
        self.gate_num = gate_num
        self.result = result
        super().__init__(
            f"Gate {gate_num} BLOCKED — score={result.score:.1f}, "
            f"critical={result.open_critical}, high={result.open_high}"
        )


@dataclass
class GateContext:
    """
    Context object returned by prepare_gate().

    Contains everything Claude needs to perform an inline gate evaluation:
    - configuration loaded from the gate YAML
    - paths to embedded SSI scripts, prompts, and schemas
    - a work directory for writing gate{N}_result.json

    After evaluation Claude writes gate{N}_result.json to work_dir and calls
    finalize_gate(ctx) to complete threshold checks and manifest updates.
    """
    gate_num: int
    config: dict
    project_root: str
    phase: int
    fr_id: str | None
    ssi_scripts_dir: str
    ssi_prompts_dir: str
    ssi_schemas_dir: str
    work_dir: str

    def evaluation_prompt(self) -> str:
        """Return a human-readable evaluation instruction for Claude."""
        dims = [d["name"] for d in self.config.get("dimensions", [])]
        score_gate = self.config.get("score_gate", "n/a")
        max_rounds = self.config.get("max_rounds", 3)
        result_path = str(Path(self.work_dir) / f"gate{self.gate_num}_result.json")
        return (
            f"Gate {self.gate_num} evaluation ready.\n"
            f"  project   : {self.project_root}\n"
            f"  phase     : {self.phase}\n"
            f"  fr_id     : {self.fr_id or 'n/a'}\n"
            f"  dimensions: {', '.join(dims) if dims else 'see gate config'}\n"
            f"  score_gate: {score_gate}\n"
            f"  max_rounds: {max_rounds}\n"
            f"\nFollow  : {self.ssi_prompts_dir}/evaluate_dimension.md\n"
            f"Scripts : {self.ssi_scripts_dir}/\n"
            f"Write result to: {result_path}\n"
            f"\nAfter writing result.json, run:\n"
            f"  python3 harness_cli.py finalize-gate {self.gate_num} "
            f"--project-root {self.project_root} --phase {self.phase}"
            + (f" --fr-id {self.fr_id}" if self.fr_id else "")
            + "\n"
        )


class HarnessBridge:
    """
    Bridge layer between software_self_improvement and harness-methodology.

    Handles gate triggering, CRG integration, result parsing, and manifest updates.
    """

    # Default timeout for Gate 4 Hermes reviewer — reads HERMES_TIMEOUT_MS env var, default 120s
    GATE4_HERMES_TIMEOUT_MS: int = int(os.environ.get("HERMES_TIMEOUT_MS", "120000"))

    def __init__(self):
        """Initialize the bridge with its dependent subsystems."""
        self.crg = CRGBridge()        # gracefully degrades if CRG unavailable
        self._log = DecisionLogWriter()
        self._effort = EffortTracker()

    def run_gate(
        self,
        gate_num: int,
        project_root: str,
        phase: int,
        fr_id: str | None = None,
        max_rounds_override: int | None = None,
    ) -> GateResult:
        """
        DEPRECATED — use prepare_gate() + Claude inline evaluation + finalize_gate().

        The subprocess-based SSI runner is removed. SSI is a Claude Code skill,
        not a standalone process. Claude IS the evaluation engine.

        Raises:
            NotImplementedError: Always. This method is intentionally broken.
        """
        raise NotImplementedError(
            "run_gate() is deprecated. Use prepare_gate() + Claude inline evaluation + finalize_gate().\n"
            "See harness_cli.py run-gate / finalize-gate subcommands."
        )

    def prepare_gate(
        self,
        gate_num: int,
        project_root: str,
        phase: int,
        fr_id: str | None = None,
    ) -> GateContext:
        """
        Phase 1 of the two-phase gate evaluation API.

        Loads gate configuration, optionally triggers CRG reconnaissance,
        and returns a GateContext that Claude uses to perform inline evaluation.

        The caller (Claude) should:
        1. Read ctx.evaluation_prompt() for instructions.
        2. Evaluate all dimensions, writing ctx.work_dir/gate{N}_result.json.
        3. Call finalize_gate(ctx) to complete threshold checks + manifest update.

        Args:
            gate_num: Gate ID (1–4).
            project_root: Absolute path to the target project.
            phase: Current methodology phase.
            fr_id: Functional requirement ID (Gate 1 per-FR only).

        Returns:
            GateContext with all paths and config Claude needs.
        """
        config = self._load_config(gate_num)

        # CRG reconnaissance for gates that require it (e.g. Gate 3/4)
        if config.get("crg", {}).get("reconnaissance"):
            self.crg.run_reconnaissance(project_root)

        ssi_dir = Path(__file__).parent / "ssi"
        work_dir = Path(project_root) / ".sessi-work"
        work_dir.mkdir(parents=True, exist_ok=True)

        return GateContext(
            gate_num=gate_num,
            config=config,
            project_root=project_root,
            phase=phase,
            fr_id=fr_id,
            ssi_scripts_dir=str(ssi_dir / "scripts"),
            ssi_prompts_dir=str(ssi_dir / "prompts"),
            ssi_schemas_dir=str(ssi_dir / "schemas"),
            work_dir=str(work_dir),
        )

    def finalize_gate(self, ctx: GateContext) -> GateResult:
        """
        Phase 2 of the two-phase gate evaluation API.

        Reads gate{N}_result.json written by Claude's inline evaluation,
        checks thresholds, updates the quality manifest, and records decisions.

        Args:
            ctx: The GateContext returned by prepare_gate().

        Returns:
            GateResult if gate passes all thresholds.

        Raises:
            FileNotFoundError: If Claude did not write gate{N}_result.json.
            GateBlockedError: If the gate fails its quality targets.
        """
        import time

        result_path = Path(ctx.work_dir) / f"gate{ctx.gate_num}_result.json"
        if not result_path.exists():
            raise FileNotFoundError(
                f"gate{ctx.gate_num}_result.json not found in {ctx.work_dir}. "
                f"Claude must evaluate and write results before calling finalize_gate()."
            )

        t0 = time.time()
        raw = json.loads(result_path.read_text(encoding="utf-8"))

        # Build per-dimension results from breakdown if provided
        dims: list[DimResult] = []
        for dim_name, dim_data in raw.get("breakdown", {}).items():
            dims.append(DimResult(
                name=dim_name,
                score=dim_data.get("score", 0.0),
                threshold=dim_data.get("threshold", 0.0),
                issues=dim_data.get("issues", []),
            ))

        result = GateResult(
            gate_num=ctx.gate_num,
            score=raw.get("overall_score", raw.get("score", 0.0)),
            dimensions=dims,
            open_critical=raw.get("open_critical_count", raw.get("open_critical", 0)),
            open_high=raw.get("open_high_count", raw.get("open_high", 0)),
            quality_complete=raw.get("quality_complete", False),
            rounds_used=raw.get("rounds_used", 0),
        )

        self._update_quality_manifest(ctx.gate_num, ctx.fr_id, result)

        self._effort.record(EffortRecord(
            phase=ctx.phase, gate_num=ctx.gate_num, agent_id="GATE",
            operation="gate_finalize", duration_s=time.time() - t0,
        ))
        self._log.write(DecisionLogEntry(
            ctx=DecisionContext(agent_id="GATE", phase=ctx.phase, fr_id=ctx.fr_id),
            decision="GATE_PASS" if result.quality_complete else "GATE_BLOCK",
            reasoning=(
                f"Gate {ctx.gate_num}: score={result.score:.1f}, "
                f"critical={result.open_critical}, high={result.open_high}"
            ),
            scores={"gate_score": result.score},
        ))

        # Gate 1: per-dimension threshold check
        if ctx.gate_num == 1:
            if any(d.score < d.threshold for d in result.dimensions):
                raise GateBlockedError(ctx.gate_num, result)
        else:
            score_gate = ctx.config.get("score_gate", 0)
            if result.score < score_gate or not result.quality_complete:
                raise GateBlockedError(ctx.gate_num, result)

        # Gate 4: require explicit Hermes reviewer APPROVE
        if ctx.gate_num == 4:
            self._require_hermes_approve(result, ctx.phase, ctx.fr_id)

        return result

    def generate_quality_manifest(self, fr_ids: list[str], sad_path: str) -> Path:
        """Called at P2 exit. Parses SAD.md -> constraints + high_risk_modules."""
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
        out.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
        return out

    def _load_config(self, gate_num: int) -> dict:
        """Load the YAML configuration for a specific gate."""
        import yaml  # type: ignore[import-untyped]
        names = {1: "gate1_per_fr.yaml", 2: "gate2_p3_exit.yaml",
                 3: "gate3_p4_exit.yaml", 4: "gate4_p6_full.yaml"}
        config_path = Path(__file__).parent / "gate_configs" / names[gate_num]
        with open(config_path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)

    def _require_hermes_approve(
        self, result: GateResult, phase: int, fr_id: str | None,
        timeout_ms: int = GATE4_HERMES_TIMEOUT_MS,
    ) -> None:
        """
        Check for external approval from Hermes reviewer (Gate 4 only).

        Args:
            timeout_ms: Max wait time for Hermes response (default 120 s from HERMES_TIMEOUT_MS env var).
        """
        from harness.reviewer_router import ReviewerRouter
        try:
            router = ReviewerRouter()
        except (ValueError, RuntimeError):
            return

        dim_summary = ", ".join(f"{d.name}={d.score:.0f}" for d in result.dimensions)
        review = router.review(
            role="reviewer",
            prompt=(
                f"Gate 4 final quality review.\n"
                f"Score: {result.score:.1f} | rounds: {result.rounds_used}\n"
                f"open_critical: {result.open_critical} | open_high: {result.open_high}\n"
                f"Dimensions: {dim_summary}\n"
                f"Approve only if all dimensions meet thresholds and critical=0."
            ),
            phase=phase,
            fr_id=fr_id,
            timeout_ms=timeout_ms,
        )
        if review.get("review_status") != "APPROVE":
            self._log.write(DecisionLogEntry(
                ctx=DecisionContext(agent_id="GATE", phase=phase, fr_id=fr_id),
                decision="REVIEWER_REJECT",
                reasoning=f"Gate 4 Hermes REJECT: {review.get('summary', '')}",
                scores={"gate_score": result.score},
            ))
            raise GateBlockedError(4, result)

    def _update_quality_manifest(
        self, gate_num: int, fr_id: str | None, result: GateResult
    ) -> None:
        """Update the persistent manifest with latest gate results."""
        p = Path(".methodology/quality_manifest.json")
        if not p.exists():
            return
        manifest = json.loads(p.read_text(encoding="utf-8"))
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
        p.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
