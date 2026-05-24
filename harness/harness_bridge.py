"""
Harness Bridge: Integration layer between the quality harness and the methodology.

Handles gate execution, results parsing, and quality manifest updates.
"""

from __future__ import annotations
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from harness.crg_bridge import CRGBridge
from harness.decision_log import DecisionLogWriter, DecisionLogEntry, DecisionContext
from harness.effort_tracker import EffortTracker, EffortRecord
from core.quality_gate.constitution.profile import GateConfig


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


# ---------------------------------------------------------------------------
# S3-A: Tool-output content patterns (Solution A)
# ---------------------------------------------------------------------------
# For each tool name, at least one pattern must match the file/inline content.
# Patterns use re.IGNORECASE | re.MULTILINE.
_TOOL_CONTENT_PATTERNS: dict[str, list[str]] = {
    "ruff": [
        r"All checks passed",          # clean run
        r"\S+\.pyi?:\d+:\d+:",         # file:line:col violation line
        r"Found \d+ error",            # summary
        r"\[[\w-]+\]",                 # rule code like [E501] or [ruff]
    ],
    "mypy": [
        r"Success: no issues found",
        r"Found \d+ error",
        r"\.pyi?:\d+: (error|note):",
    ],
    "pytest-cov": [
        r"\d+ passed",
        r"TOTAL\s+\d+",
        r"coverage:",
        r"Coverage report",
    ],
    "pytest": [
        r"\d+ passed",
        r"\d+ failed",
        r"no tests ran",
        r"={3,}",                      # pytest separator bars
    ],
    "gitleaks": [
        r"No leaks found",
        r"Secret",
        r"leaks?\s+found",
        r"gitleaks",
        r"WRN\[",                      # gitleaks warning format
        r"INF\[",                      # gitleaks info format
    ],
    "mutmut": [
        r"Killed",
        r"Survived",
        r"mutation score",
        r"mutmut",
    ],
    "scancode": [
        r"license",
        r"SPDX",
        r"copyright",
        r"scan:",
    ],
}

# Minimum byte size for a tool_output file to be considered non-stub.
# Real tool output is always larger than this; pure comment lines are typically
# under 80 bytes.
_TOOL_OUTPUT_MIN_BYTES: int = 5


def _validate_tool_content(
    content: str,
    tool: str | None,
    dim_name: str,
    *,
    inline: bool,
) -> list[str]:
    """S3-A: Verify that *content* looks like genuine tool output.

    Checks (in order):
      1. Minimum size (file only — inline snippets are expected to be short)
      2. Comment-header stub detection (applies to both file and inline)
      3. Tool-specific structural pattern match (applies to both)

    Returns list of violation messages (empty = OK).
    """
    violations: list[str] = []

    # 1. Minimum size (file only)
    if not inline:
        size = len(content.encode("utf-8"))
        if size < _TOOL_OUTPUT_MIN_BYTES:
            violations.append(
                f"{dim_name}: tool_output file is too small ({size} bytes) — "
                f"likely a stub; real tool output is at least {_TOOL_OUTPUT_MIN_BYTES} bytes"
            )
            return violations  # Early exit — no point checking further

    # 2. Comment-header stub detection
    first_nonblank = next((ln for ln in content.splitlines() if ln.strip()), "")
    if first_nonblank.strip().startswith("#"):
        kind = "tool_evidence" if inline else "tool_output"
        violations.append(
            f"{dim_name}: {kind} starts with '#' comment — "
            f"this is a stub marker, not genuine tool output"
        )
        return violations  # Early exit

    # 3. Tool-specific structural pattern
    if tool and tool in _TOOL_CONTENT_PATTERNS:
        patterns = _TOOL_CONTENT_PATTERNS[tool]
        if not any(
            re.search(p, content, re.IGNORECASE | re.MULTILINE)
            for p in patterns
        ):
            kind = "tool_evidence" if inline else "tool_output"
            violations.append(
                f"{dim_name}: {kind} does not match any expected output pattern for "
                f"'{tool}' — content may not be genuine {tool} output"
            )

    return violations


def _check_tool_evidence(ctx: "GateContext", raw: dict) -> list[str]:
    """S3: Verify tool execution evidence in gate result JSON.

    For dimensions with requires_tool_execution:true in the gate YAML config,
    the result JSON breakdown entry MUST include either:
      - tool_output: path to a file containing raw tool stdout/stderr
      - tool_evidence: inline string of tool output snippet

    Additionally (S3-A), the content of tool_output files and tool_evidence
    strings is validated for structural authenticity — stub files and comment
    placeholders are rejected.

    Returns list of violation messages (empty = all good).
    """
    import yaml as _yaml
    from pathlib import Path as _Path

    # Load gate config to find requires_tool_execution dimensions
    cfg_path = None
    for pattern in [
        f"gate{ctx.gate_num}_p*.yaml",
        f"gate{ctx.gate_num}_*.yaml",
    ]:
        import glob as _glob
        candidates = _glob.glob(
            str(_Path(ctx.project_root) / "harness" / "gate_configs" / pattern)
        )
        if candidates:
            cfg_path = _Path(candidates[0])
            break

    if not cfg_path or not cfg_path.exists():
        return []  # No config — cannot enforce

    try:
        cfg = _yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    except Exception:
        return []

    violations: list[str] = []
    breakdown = raw.get("breakdown", {})

    for dim in cfg.get("dimensions", []):
        dim_name = dim.get("name", "")
        requires_tool = dim.get("requires_tool_execution", False)
        if not requires_tool:
            continue

        tool = dim.get("tool")
        dim_data = breakdown.get(dim_name, {})
        tool_output = dim_data.get("tool_output")
        tool_evidence = dim_data.get("tool_evidence")

        if tool_output:
            out_path = _Path(ctx.project_root) / tool_output
            if not out_path.exists():
                violations.append(
                    f"{dim_name}: tool_output path '{tool_output}' does not exist"
                )
            else:
                try:
                    content = out_path.read_text(encoding="utf-8", errors="replace")
                except OSError as exc:
                    violations.append(f"{dim_name}: cannot read tool_output file: {exc}")
                    continue
                violations.extend(
                    _validate_tool_content(content, tool, dim_name, inline=False)
                )
        elif tool_evidence:
            evidence_str = str(tool_evidence).strip()
            if len(evidence_str) < 10:
                violations.append(
                    f"{dim_name}: tool_evidence too short "
                    f"({len(evidence_str)} chars) — must be real tool output snippet"
                )
            else:
                violations.extend(
                    _validate_tool_content(evidence_str, tool, dim_name, inline=True)
                )
        else:
            violations.append(
                f"{dim_name}: requires tool execution but result JSON has neither "
                f"tool_output nor tool_evidence — scores must come from actual tool runs"
            )

    return violations


# ---------------------------------------------------------------------------
# S4: Harness cross-validation (Solution B)
# ---------------------------------------------------------------------------

def _run_harness_cross_validation(ctx: "GateContext", raw: dict) -> list[str]:
    """S4: Run tools independently and cross-validate agent-reported scores.

    For each Tier 1/2 dimension with requires_tool_execution:true, the harness
    executes the tool itself (via harness.tool_runners), computes a score, and
    blocks when:
      - harness_score < threshold  (harness says the code fails)
      AND
      - agent_score >= threshold   (agent claims the code passes)

    This eliminates score fabrication for tool-based dimensions: even if the
    agent writes a perfectly-structured stub, the harness independently verifies
    the actual code.

    Slow tools (mutmut, scancode) are skipped here; Solution A (content
    validation) still applies to their evidence files.

    Raw tool output is written to .sessi-work/harness_verification/ for audit.

    Returns list of fabrication violation messages (empty = all clear).
    """
    import yaml as _yaml
    from pathlib import Path as _Path

    cfg_path = None
    for pattern in [f"gate{ctx.gate_num}_p*.yaml", f"gate{ctx.gate_num}_*.yaml"]:
        import glob as _glob
        candidates = _glob.glob(
            str(_Path(ctx.project_root) / "harness" / "gate_configs" / pattern)
        )
        if candidates:
            cfg_path = _Path(sorted(candidates)[0])
            break

    if not cfg_path or not cfg_path.exists():
        return []

    try:
        cfg = _yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"  [S4-WARN] cross-validation disabled: failed to parse {cfg_path.name}: {exc}")
        return []

    try:
        from harness.tool_runners import run_tool, compute_tool_score
    except ImportError as exc:
        print(f"  [S4-WARN] cross-validation disabled: harness.tool_runners unavailable: {exc}")
        return []

    verification_dir = _Path(ctx.project_root) / ".sessi-work" / "harness_verification"
    verification_dir.mkdir(parents=True, exist_ok=True)

    violations: list[str] = []
    breakdown = raw.get("breakdown", {})

    for dim in cfg.get("dimensions", []):
        dim_name = dim.get("name", "")
        requires_tool = dim.get("requires_tool_execution", False)
        tool = dim.get("tool")
        threshold = float(dim.get("threshold", 0))

        if not requires_tool or not tool:
            continue

        agent_score = float(breakdown.get(dim_name, {}).get("score", 0))

        # Only cross-validate when the agent claims a passing score.
        # If the agent already reports FAIL, there is no fabrication concern.
        if agent_score < threshold:
            continue

        output, returncode = run_tool(tool, ctx.project_root)

        # Write audit trail regardless of outcome
        audit_file = verification_dir / f"{dim_name}_harness.txt"
        try:
            audit_file.write_text(
                f"# Harness-executed: {tool}\n"
                f"# returncode: {returncode}\n"
                f"# agent_score: {agent_score} | threshold: {threshold}\n\n"
                f"{output}\n",
                encoding="utf-8",
            )
        except OSError:
            pass  # Audit write failure is non-fatal

        if returncode == -1:
            # Tool is on the skip list (mutmut / scancode) — S3-A covers it
            print(
                f"  [S4] {dim_name}: '{tool}' skipped for cross-validation "
                f"(too slow/complex) — S3-A content check still applies"
            )
            continue
        if returncode in (-2, -3, -4):
            # Timed out / not found / error — cannot cross-validate; warn only
            print(
                f"  [S4-WARN] {dim_name}: '{tool}' cross-validation skipped "
                f"(returncode={returncode}) — verify manually"
            )
            continue

        harness_score = compute_tool_score(tool, output, returncode)
        if harness_score is None:
            continue

        print(
            f"  [S4] {dim_name}: harness={harness_score:.1f} | "
            f"agent={agent_score:.1f} | threshold={threshold}"
        )

        if harness_score < threshold:
            violations.append(
                f"{dim_name}: fabrication detected — "
                f"harness ran '{tool}' and scored {harness_score:.1f} "
                f"(below threshold {threshold}), but agent reported {agent_score:.1f} "
                f"(above threshold). "
                f"See {audit_file.relative_to(_Path(ctx.project_root))}"
            )

    return violations


class GateBlockedError(Exception):
    """Exception raised when a quality gate fails to meet its targets."""
    def __init__(self, gate_num: int, result: GateResult, details: dict | None = None):
        self.gate_num = gate_num
        self.result = result
        self.details = details or {}
        msg = (
            f"Gate {gate_num} BLOCKED — score={result.score:.1f}, "
            f"critical={result.open_critical}, high={result.open_high}"
        )
        if details:
            for key, val in details.items():
                if isinstance(val, list):
                    msg += f"\n  {key}: {', '.join(str(v) for v in val[:3])}"
        super().__init__(msg)


class PreflightBlockedError(Exception):
    """Raised when preflight validation fails before gate evaluation (Item 9)."""

    def __init__(self, preflight_result: dict):
        self.preflight_result = preflight_result
        failing = [
            k for k, v in preflight_result.get("details", {}).items()
            if isinstance(v, dict) and not v.get("passed", False)
        ]
        super().__init__(f"Preflight BLOCKED — failing checks: {failing}")


@dataclass
class GateContext:
    """
    Context object returned by prepare_gate().

    Contains everything Claude needs to perform an inline gate evaluation:
    - configuration loaded from the gate YAML
    - SAB baseline data from quality_manifest.json (architecture_constraints, high_risk_modules)
    - paths to embedded SSI scripts, prompts, and schemas
    - a work directory for writing gate{N}_result.json

    After evaluation Claude writes gate{N}_result.json to work_dir and calls
    finalize_gate(ctx) to complete threshold checks and manifest updates.
    """
    gate_num: int
    config: GateConfig | dict
    project_root: str
    phase: int
    fr_id: str | None
    ssi_scripts_dir: str
    ssi_prompts_dir: str
    ssi_schemas_dir: str
    work_dir: str
    sab_data: dict = field(default_factory=dict)
    tier3_context: dict = field(default_factory=dict)  # CRG Point 2 — per-dim context
    crg_safety_context: dict = field(default_factory=dict)  # CRG Points 3+4 — pre-computed
    auto_fix_rounds: int = 0

    def evaluation_prompt(self) -> str:
        """Return a human-readable evaluation instruction for Claude."""
        if isinstance(self.config, GateConfig):
            dims = [d.name for d in self.config.dimensions]
            score_gate = self.config.score_gate
            max_rounds = self.config.max_rounds
        else:
            dims = [d["name"] for d in self.config.get("dimensions", [])]
            score_gate = self.config.get("score_gate", "n/a")
            max_rounds = self.config.get("max_rounds", 3)
        result_path = str(Path(self.work_dir) / f"gate{self.gate_num}_result.json")

        sab_lines = ""
        if self.sab_data:
            constraints = self.sab_data.get("architecture_constraints", [])
            high_risk = self.sab_data.get("high_risk_modules", [])
            nfr_map = self.sab_data.get("nfr_dimension_mapping", {})
            sab_lines = "\n[SAB Baseline — from quality_manifest.json]\n"
            if constraints:
                sab_lines += f"  architecture_constraints: {constraints}\n"
            if high_risk:
                sab_lines += f"  high_risk_modules: {high_risk}\n"
            nfr_trace = self.sab_data.get("nfr_traceability", {})
            # Only show the flat mapping when detailed traceability is absent
            # (traceability is a strict superset of nfr_dimension_mapping).
            if nfr_map and not nfr_trace:
                sab_lines += f"  nfr_dimension_mapping: {nfr_map}\n"
            if nfr_trace:
                sab_lines += "  nfr_traceability (module → quality target):\n"
                for nfr_id, v in nfr_trace.items():
                    if isinstance(v, dict):
                        sab_lines += (
                            f"    {nfr_id}: [{v.get('type', '')}] "
                            f"{v.get('module', '')} — {v.get('target', '')}\n"
                        )
                sab_lines += (
                    "  > When evaluating NFR-related dimensions, "
                    "refer to the module and target above for concrete scope.\n"
                )
            sab_lines += (
                "  > When evaluating the `architecture` dimension, validate code "
                "against these constraints.\n"
                "  > high_risk_modules deserve extra scrutiny in all dimensions.\n"
            )

        # CRG Point 2: Tier 3 guidance context
        crg_lines = ""
        if self.tier3_context:
            crg_lines = "\n[CRG Tier 3 Guidance — structural context for high-cost dimensions]\n"
            for dim_name, ctx in self.tier3_context.items():
                if ctx:
                    crg_lines += f"  {dim_name}: {ctx.get('task', ctx.get('summary', 'context available'))}\n"
            crg_lines += (
                "  > Use this structural context when evaluating Tier 3 dimensions"
                " (architecture, error_handling, readability, documentation, performance).\n"
            )

        # CRG Point 3: pre-computed safety context.
        # Point 4 (drift) is per-round — handled by AutoFixEngine, not pre-computed here.
        if self.crg_safety_context:
            crg_lines += "\n[CRG Safety Context — pre-computed by HarnessBridge]\n"
            pre_fix = self.crg_safety_context.get("pre_fix_safety", {})
            if pre_fix:
                safe = "SAFE" if pre_fix.get("safe", True) else "UNSAFE"
                crg_lines += f"  pre_fix_safety: {safe} — {pre_fix.get('message', '')}\n"
            xp_drift = self.crg_safety_context.get("cross_phase_drift")
            if xp_drift:
                drift_val = xp_drift.get("drift", 0)
                level = "CRITICAL" if drift_val > 0.5 else ("WARNING" if drift_val > 0.3 else "STABLE")
                bl_phase = xp_drift.get("baseline_phase", "?")
                crg_lines += (
                    f"  cross_phase_drift: {level} — {drift_val:.3f} "
                    f"(baseline=P{bl_phase}, sha={xp_drift.get('baseline_sha','?')[:8]})\n"
                )
                if drift_val > 0.5:
                    crg_lines += (
                        "  > CRITICAL: significant structural degradation since last phase exit.\n"
                        "  > Increase architecture/error_handling scrutiny in this gate evaluation.\n"
                    )
                elif drift_val > 0.3:
                    crg_lines += (
                        "  > WARNING: moderate structural drift since last phase exit.\n"
                        "  > Review architecture findings against baseline changes.\n"
                    )
            crg_lines += (
                "  > Before each fix round, defer if pre_fix_safety is UNSAFE.\n"
            )

        return (
            f"Gate {self.gate_num} evaluation ready.\n"
            f"  project   : {self.project_root}\n"
            f"  phase     : {self.phase}\n"
            f"  fr_id     : {self.fr_id or 'n/a'}\n"
            f"  dimensions: {', '.join(dims) if dims else 'see gate config'}\n"
            f"  score_gate: {score_gate}\n"
            f"  max_rounds: {max_rounds}\n"
            f"{sab_lines}"
            f"{crg_lines}"
            f"\nFollow  : {self.ssi_prompts_dir}/evaluate_dimension.md\n"
            f"Scripts : {self.ssi_scripts_dir}/\n"
            f"Write result to: {result_path}\n"
            f"\nAfter writing result.json, run:\n"
            f"  python3 harness_cli.py finalize-gate {self.gate_num} "
            f"--project-root {self.project_root} --phase {self.phase}"
            + (f" --fr-id {self.fr_id}" if self.fr_id else "")
            + "\n"
        )


@dataclass
class EnvCheckContext:
    """Context object returned by prepare_env_check().

    Contains project documentation excerpts that Claude uses to determine what
    environment variables, CLI tools, and infrastructure services are required,
    then verify them against the current environment.

    After evaluation Claude writes .sessi-work/env_check_result.json and calls
    finalize_env_check() to verify completeness.
    """
    project_root: str
    phase: int
    fr_id: str | None
    ssi_schemas_dir: str
    work_dir: str
    sad_excerpt: str = ""
    srs_excerpt: str = ""
    docker_compose_excerpt: str = ""

    def evaluation_prompt(self) -> str:
        """Return the evaluation instruction for Claude."""
        result_path = str(Path(self.work_dir) / "env_check_result.json")
        schema_path = str(Path(self.ssi_schemas_dir) / "env_check_result.schema.json")

        parts: list[str] = []

        if self.sad_excerpt:
            parts.append(
                "[SAD.md — Architecture & Technology]\n"
                f"{self.sad_excerpt}"
            )
        if self.srs_excerpt:
            parts.append(
                "[SRS.md — Requirements & Verification Methods]\n"
                f"{self.srs_excerpt}"
            )
        if self.docker_compose_excerpt:
            parts.append(
                "[docker-compose.yml — Infrastructure Services]\n"
                f"{self.docker_compose_excerpt}"
            )

        fr_line = f"  FR-ID    : {self.fr_id}\n" if self.fr_id else ""

        return (
            f"{'='*60}\n"
            f"run-env-check: Phase {self.phase} | project: {self.project_root}\n"
            f"{'='*60}\n"
            f"  Phase    : {self.phase}\n"
            f"{fr_line}"
            f"\n"
            + "\n\n".join(parts) +
            f"\n\n{'─'*60}\n"
            f"[TASK — Evaluate Environment Readiness]\n\n"
            f"1. IDENTIFY all required items from the project docs above:\n"
            f"   a. Environment variables (from app.infrastructure.config / FR-21)\n"
            f"   b. CLI tools (from Technology Choices / verification methods)\n"
            f"   c. Infrastructure services (from Architecture layers / docker-compose)\n"
            f"   d. Test framework + extensions (from verification methods / constraints)\n\n"
            f"2. VERIFY each item — run ALL checks in ONE shot, never one-by-one:\n"
            f"   [CRITICAL] Burning turns on individual commands will leave no room to\n"
            f"   write the result JSON. Do this instead:\n"
            f"   a. mkdir -p .sessi-work\n"
            f"   b. Write a single verification script (e.g., `.sessi-work/verify.sh`) that\n"
            f"      chains all `which`, `echo $VAR`, and connectivity checks together.\n"
            f"   c. Run the script once to collect all results.\n"
            f"   d. Write the result JSON to {result_path} in a\n"
            f"      single Write tool call — do NOT chain writes.\n"
            f"   If the script fails, run remaining checks individually and report partial\n"
            f"   findings rather than writing nothing.\n\n"
            f"3. REPORT findings to {result_path}\n"
            f"   Schema: {schema_path}\n"
            f"   For each missing item, include the exact install/fix command.\n\n"
            f"[FORBIDDEN]\n"
            f"- Guessing env var values — only check presence, not correctness\n"
            f"- Fabricating check results without actual tool execution\n"
            f"- Skipping a category because it seems obvious\n"
            f"- Writing result.json without running real verification commands\n\n"
            f"{'─'*60}\n"
            f"NEXT: After writing result.json, run:\n"
            f"  python harness_cli.py finalize-env-check "
            f"--phase {self.phase} --project {self.project_root}"
            + (f" --fr-id {self.fr_id}" if self.fr_id else "")
            + "\n" + "─"*60 + "\n"
        )


class HarnessBridge:
    """
    Gate lifecycle controller — two-phase API (prepare_gate → finalize_gate).

    Handles gate configuration loading, CRG integration, result parsing, threshold
    enforcement, and quality manifest updates. The SSI evaluation engine (prompts,
    scripts, schemas) is embedded in harness/ssi/.
    """

    def __init__(self):
        """Initialize the bridge with its dependent subsystems."""
        self.crg = CRGBridge()        # gracefully degrades if CRG unavailable
        self._log = DecisionLogWriter()
        self._effort = EffortTracker()
        self._last_gate_num: int | None = None

    def _load_manifest_sab(self, project_root: str) -> dict:
        """Read SAB-derived fields from quality_manifest.json. Returns empty dict on failure."""
        manifest_path = Path(project_root) / ".methodology" / "quality_manifest.json"
        if not manifest_path.exists():
            return {}
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            return {
                "nfr_dimension_mapping": manifest.get("nfr_dimension_mapping", {}),
                "architecture_constraints": manifest.get("architecture_constraints", []),
                "high_risk_modules": manifest.get("high_risk_modules", []),
            }
        except Exception:
            return {}

    def prepare_env_check(
        self,
        project_root: str,
        phase: int,
        fr_id: str | None = None,
    ) -> EnvCheckContext:
        """Build an EnvCheckContext with project documentation excerpts.

        Reads SAD.md + SRS.md from the project root, extracts key sections
        (architecture layers, infrastructure config, technology choices,
        verification methods), and returns an EnvCheckContext whose
        evaluation_prompt() Claude uses for inline environment readiness
        evaluation.

        docker-compose.yml is included if present (for service health checks).
        """
        root = Path(project_root)

        sad_full = ""
        sad_path = None
        for sad_candidate in [
            root / "SAD.md",
            root / "02-architecture" / "SAD.md",
            root / "architecture" / "SAD.md",
            root / "docs" / "SAD.md",
        ]:
            if sad_candidate.exists():
                sad_full = sad_candidate.read_text(encoding="utf-8")
                sad_path = sad_candidate
                break
        sad_excerpt = ""
        if sad_full:
            max_sad = 8000
            if len(sad_full) > max_sad:
                sad_excerpt = (
                    sad_full[:max_sad]
                    + f"\n\n[... truncated at {max_sad} chars — full content at {sad_path} ...]"
                )
            else:
                sad_excerpt = sad_full

        srs_full = ""
        srs_path = None
        for srs_candidate in [
            root / "SRS.md",
            root / "01-requirements" / "SRS.md",
            root / "requirements" / "SRS.md",
            root / "docs" / "SRS.md",
        ]:
            if srs_candidate.exists():
                srs_full = srs_candidate.read_text(encoding="utf-8")
                srs_path = srs_candidate
                break
        srs_excerpt = ""
        if srs_full:
            max_srs = 6000
            if len(srs_full) > max_srs:
                srs_excerpt = (
                    srs_full[:max_srs]
                    + f"\n\n[... truncated at {max_srs} chars — full content at {srs_path} ...]"
                )
            else:
                srs_excerpt = srs_full

        dc_excerpt = ""
        dc = root / "docker-compose.yml"
        if dc.exists():
            dc_excerpt = dc.read_text(encoding="utf-8")[:2000]

        ssi_dir = Path(__file__).parent / "ssi"
        work_dir = root / ".sessi-work"
        # Note: callers that write to work_dir (cmd_run_env_check) are
        # responsible for mkdir. prepare_env_check is read-only.

        return EnvCheckContext(
            project_root=project_root,
            phase=phase,
            fr_id=fr_id,
            ssi_schemas_dir=str(ssi_dir / "schemas"),
            work_dir=str(work_dir),
            sad_excerpt=sad_excerpt,
            srs_excerpt=srs_excerpt,
            docker_compose_excerpt=dc_excerpt,
        )

    def finalize_env_check(self, ctx: EnvCheckContext) -> tuple[bool, str]:
        """Read env_check_result.json and verify it passes schema + readiness.

        Returns (ready, summary_message).
        """
        import json as _json
        result_path = Path(ctx.work_dir) / "env_check_result.json"
        if not result_path.exists():
            return False, f"Result file not found: {result_path} — run run-env-check first"
        try:
            data = _json.loads(result_path.read_text(encoding="utf-8"))
        except Exception:
            return False, f"Result file is malformed JSON: {result_path}"

        ready = data.get("ready", False)
        summary = data.get("summary", "No summary provided.")
        checked_at = data.get("checked_at")
        env_vars = data.get("env_vars", {})
        cli_tools = data.get("cli_tools", {})
        infra = data.get("infra_services", {})

        # Minimal anti-fabrication: required fields must be present
        if checked_at is None:
            return False, "Result missing required field: checked_at"
        if not isinstance(env_vars.get("required"), list):
            return False, "Result missing required field: env_vars.required"
        if not isinstance(cli_tools.get("required"), list):
            return False, "Result missing required field: cli_tools.required"
        if not isinstance(infra.get("required"), list):
            return False, "Result missing required field: infra_services.required"

        if ready:
            return True, f"Environment ready.\n{summary}"
        else:
            return False, f"Environment NOT ready.\n{summary}"

    def prepare_gate(
        self,
        gate_num: int,
        project_root: str,
        phase: int,
        fr_id: str | None = None,
        auto_fix_rounds: int = 0,
    ) -> GateContext:
        """
        Phase 1 of the two-phase gate evaluation API.

        Loads gate configuration, optionally triggers CRG reconnaissance,
        reads SAB baseline from quality_manifest.json, and returns a GateContext
        that Claude uses to perform inline evaluation.

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
        self._last_gate_num = gate_num
        config = self._load_config(gate_num)
        if auto_fix_rounds:
            config = GateConfig(
                gate_num=config.gate_num, score_gate=config.score_gate,
                dimensions=config.dimensions, per_dim_min=config.per_dim_min,
                max_rounds=auto_fix_rounds, blocking=config.blocking,
                trigger=config.trigger, scope=config.scope, crg=config.crg,
            )

        # CRG Point 1: structural reconnaissance for gates that require it (Gate 3/4)
        if config.crg.get("reconnaissance"):
            self.crg.run_reconnaissance(project_root)

        # CRG Gate 2: lightweight graph refresh for impact check (no full recon).
        # Gate 2 declares impact_check but not reconnaissance — ensure graph exists
        # so pre-fix blast radius checks have structural data to work with.
        # CRG is mandatory; refresh failure is a blocking error, same as Gate 3/4.
        if config.crg.get("impact_check") and not config.crg.get("reconnaissance"):
            self.crg.refresh_graph(project_root)

        # CRG Point 2: Tier 3 guidance — get minimal context for each Tier 3 dimension
        tier3_context: dict[str, dict] = {}
        if config.crg.get("tier3_guidance"):
            for dim in config.dimensions:
                if dim.tier == 3:
                    tier3_context[dim.name] = self.crg.get_minimal_context(
                        project_root, dim.name
                    )

        # CRG cross-phase drift: compare current structure against previous exit gate baseline.
        # Only meaningful for Gate 3 (P4, baseline=P3) and Gate 4 (P6, baseline=P4).
        # Gate 2 may lack metrics (no full recon), so baseline may be absent.
        _cross_phase_drift = None
        _baseline_phase_map = {4: 3, 6: 4}  # gate phase → previous exit gate phase
        _prev_phase = _baseline_phase_map.get(phase)
        if _prev_phase is not None:
            _baseline_path = (
                Path(project_root) / ".methodology"
                / f"crg_baseline_p{_prev_phase}.json"
            )
            if _baseline_path.is_file():
                try:
                    import json as _json
                    _baseline = _json.loads(_baseline_path.read_text(encoding="utf-8"))
                    _current_metrics_path = (
                        Path(project_root) / ".sessi-work" / "crg_metrics.json"
                    )
                    if _current_metrics_path.is_file():
                        _current = _json.loads(_current_metrics_path.read_text(encoding="utf-8"))
                        from harness.ssi.scripts.crg_analysis import compute_structural_drift
                        _drift = compute_structural_drift(_baseline, _current)
                        _cross_phase_drift = {
                            "drift": _drift,
                            "baseline_phase": _prev_phase,
                            "baseline_sha": _baseline.get("_baseline_sha", "unknown"),
                        }
                except Exception as _xp_exc:
                    print(
                        f"[CRG] WARN: cross-phase drift skipped — {_xp_exc}",
                        flush=True,
                    )

        # CRG Point 3: pre-compute pre-fix safety context (not just text hints).
        # Point 4 (drift check) is per-round, not pre-computed here — it fires
        # in AutoFixEngine.fix() after each fix round.
        crg_safety_context: dict[str, dict] = {}
        if _cross_phase_drift is not None:
            crg_safety_context["cross_phase_drift"] = _cross_phase_drift
        if config.crg.get("impact_check") or config.crg.get("enabled"):
            crg_safety_context["pre_fix_safety"] = self.check_pre_fix_safety(project_root)

        ssi_dir = Path(__file__).parent / "ssi"
        work_dir = Path(project_root) / ".sessi-work"
        work_dir.mkdir(parents=True, exist_ok=True)

        sab_data = self._load_manifest_sab(project_root)

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
            sab_data=sab_data,
            tier3_context=tier3_context,
            crg_safety_context=crg_safety_context,
            auto_fix_rounds=auto_fix_rounds,
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

        # ── S3: Tool execution evidence enforcement ──────────────────────────
        # For dimensions with requires_tool_execution:true in the gate YAML,
        # the result JSON must include tool_output (path to raw tool output)
        # or tool_evidence (inline snippet). Prevents LLM score fabrication
        # when tools are installed but never actually run.
        # S3-A (Solution A): content of those files/strings is also validated —
        # stub comments, files that are too small, and content that does not match
        # the expected tool output structure are all rejected.
        _tool_violations = _check_tool_evidence(ctx, raw)
        if _tool_violations:
            raise GateBlockedError(
                ctx.gate_num,
                GateResult(
                    gate_num=ctx.gate_num,
                    score=0.0,
                    dimensions=[],
                    open_critical=len(_tool_violations),
                    open_high=0,
                    quality_complete=False,
                    rounds_used=0,
                ),
                details={"tool_evidence_missing": _tool_violations},
            )

        # ── S4: Harness cross-validation (Solution B) ────────────────────────
        # For each Tier 1/2 dimension where the agent claims a passing score,
        # the harness independently runs the tool and computes its own score.
        # If harness_score < threshold but agent_score ≥ threshold, the gate is
        # blocked with a fabrication violation.
        # Slow tools (mutmut, scancode) are skipped here; S3-A covers them.
        print("\n[S4] Running harness cross-validation...")
        _s4_violations = _run_harness_cross_validation(ctx, raw)
        if _s4_violations:
            raise GateBlockedError(
                ctx.gate_num,
                GateResult(
                    gate_num=ctx.gate_num,
                    score=0.0,
                    dimensions=[],
                    open_critical=len(_s4_violations),
                    open_high=0,
                    quality_complete=False,
                    rounds_used=0,
                ),
                details={"tool_score_fabrication": _s4_violations},
            )

        # Build per-dimension results from breakdown if provided
        dims: list[DimResult] = []
        for dim_name, dim_data in raw.get("breakdown", {}).items():
            dims.append(DimResult(
                name=dim_name,
                score=dim_data.get("score", 0.0),
                threshold=dim_data.get("threshold", 0.0),
                issues=dim_data.get("issues", []),
            ))

        # SG-2 (robustness audit): per-dimension variance sanity check.
        # If ≥3 dimensions all share the SAME score, that's suspiciously uniform
        # — Claude's per-dim evaluation should produce naturally varied scores.
        # We don't BLOCK here (Claude may legitimately rate dims identically on
        # very small projects), but we LOG to decision_log for forensic review.
        # A future enhancement (deferred audit recommendation) is to compare
        # these scores against a per-dimension evidence trail in .sessi-work/.
        try:
            import statistics as _stats
            dim_scores = [d.score for d in dims]  # B3: include zero-scored dims
            if len(dim_scores) >= 3:
                _stdev = _stats.pstdev(dim_scores)
                if _stdev < 0.5:
                    self._log.write(DecisionLogEntry(
                        ctx=DecisionContext(agent_id="GATE", phase=ctx.phase, fr_id=ctx.fr_id),
                        decision="GATE_VARIANCE_LOW",
                        reasoning=(
                            f"Per-dimension scores cluster tightly "
                            f"(n={len(dim_scores)}, stddev={_stdev:.3f}, scores={dim_scores}). "
                            f"Forensic flag — manually verify evidence trail."
                        ),
                        scores={"dim_stddev": _stdev},
                    ))
        except Exception:  # pylint: disable=broad-exception-caught
            pass  # variance check is advisory — never block finalize

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
                self._trigger_hooks(ctx, "on_gate_fail")
                raise GateBlockedError(ctx.gate_num, result)
        else:
            if isinstance(ctx.config, GateConfig):
                score_gate = ctx.config.score_gate
            else:
                score_gate = ctx.config.get("score_gate", 0)
            if result.score < score_gate or not result.quality_complete:
                self._trigger_hooks(ctx, "on_gate_fail")
                raise GateBlockedError(ctx.gate_num, result)

        self._trigger_hooks(ctx, "after_gate_pass")

        return result

    def check_pre_fix_safety(self, project_root: str, ref: str = "HEAD") -> dict:
        """
        CRG Point 3: Pre-fix safety gate — check if pending changes are safe to modify.

        Call before each improvement round. Defers fix if CRG impact check reports risky.
        """
        threshold = 0.7
        if self._last_gate_num is not None:
            config = self._load_config(self._last_gate_num)
            threshold = config.crg.get("impact_threshold", 0.7)
        risky = self.crg.check_impact(project_root, ref=ref, threshold=threshold)
        return {
            "safe": not risky,
            "threshold": threshold,
            "message": "Safe to modify" if not risky else
                       f"DEFER: risk score >= {threshold} — structural impact too high",
        }

    def check_post_round_drift(self, project_root: str) -> dict:
        """
        CRG Point 4: Post-round drift check — verify no structural drift introduced.

        Call after each improvement round. Triggers revert protocol if drift detected.
        """
        threshold = 0.4
        if self._last_gate_num is not None:
            config = self._load_config(self._last_gate_num)
            threshold = config.crg.get("drift_threshold", 0.4)
        drifted = self.crg.check_drift(project_root, threshold=threshold)
        metrics = self.crg.load_metrics(project_root)
        structural_drift = metrics.get("structural_drift", 0.0)
        return {
            "drifted": drifted,
            "structural_drift": structural_drift,
            "threshold": threshold,
            "message": "No structural drift" if not drifted else
                       f"DRIFT DETECTED: structural_drift={structural_drift} > {threshold}",
        }

    @staticmethod
    def _nfr_type_to_dim(nfr_type: str) -> str:
        """Map an NFR type keyword to a harness quality dimension name."""
        t = nfr_type.lower()
        if any(k in t for k in ("performance", "latency", "throughput", "response")):
            return "performance"
        if any(k in t for k in ("security", "auth", "access control", "encryption")):
            return "security"
        if any(k in t for k in ("reliability", "availability", "uptime", "recovery")):
            return "reliability"
        if any(k in t for k in ("deploy", "deployability", "docker", "container", "rollout")):
            return "deployability"
        if any(k in t for k in ("maintainability", "modularity", "extensibility")):
            return "maintainability"
        if any(k in t for k in ("test", "coverage", "quality")):
            return "test_coverage"
        if any(k in t for k in ("traceability", "tracking", "audit")):
            return "traceability"
        if any(k in t for k in ("clarity", "documentation", "readability")):
            return "clarity"
        return "correctness"

    def _parse_nfr_from_srs(self, project_root: Path) -> dict[str, str]:
        """Extract NFR→dimension mapping from SRS.md NFR sections as fallback.

        Supports two SRS formats:
        - H3-heading:  ### NFR-01: Performance
        - Pipe-table:  | NFR-01 | Performance | description |
        """
        srs_path = project_root / "01-requirements" / "SRS.md"
        if not srs_path.exists():
            return {}
        try:
            text = srs_path.read_text(encoding="utf-8")
            nfr_map: dict[str, str] = {}
            # Format 1: ### NFR-XX: Title sections
            for m in re.finditer(r'^###\s+(NFR-\d+)\s*:\s*(.+)$', text, re.MULTILINE):
                nfr_id = m.group(1)
                nfr_map[nfr_id] = self._nfr_type_to_dim(m.group(2).strip())
            # Format 2 (fallback): pipe-table | NFR-01 | Type | ... |
            if not nfr_map:
                for m in re.finditer(
                    r'^\|\s*(NFR-\d+)\s*\|\s*([^|]+?)\s*\|', text, re.MULTILINE
                ):
                    nfr_id = m.group(1)
                    if nfr_id not in nfr_map:
                        nfr_map[nfr_id] = self._nfr_type_to_dim(m.group(2).strip())
            return nfr_map
        except Exception:
            return {}

    def _parse_nfr_fr_xref(self, project_root: Path) -> dict[str, list[str]]:
        """Extract NFR→[FR, ...] mapping from the §2 FR Cross-Reference table in SRS.md.

        Looks for a pipe-table whose header contains 'NFR Association'.
        Returns {nfr_id: [fr_id, ...]} reverse mapping.
        """
        srs_path = project_root / "01-requirements" / "SRS.md"
        if not srs_path.exists():
            return {}
        try:
            text = srs_path.read_text(encoding="utf-8")
            # Find table header with 'NFR Association' column
            header_re = re.compile(
                r'^(?:\|[^|\n]*)+\|\s*NFR\s*Association\s*\|', re.IGNORECASE | re.MULTILINE
            )
            header_match = header_re.search(text)
            if not header_match:
                return {}
            cols = [c.strip() for c in header_match.group(0).split('|') if c.strip()]
            nfr_col = next(
                (i for i, c in enumerate(cols) if 'nfr' in c.lower() and 'assoc' in c.lower()),
                -1,
            )
            if nfr_col == -1:
                return {}
            # Build FR→[NFR] map from table rows, then reverse it
            fr_nfr: dict[str, list[str]] = {}
            for line in text[header_match.end():].splitlines():
                line = line.strip()
                if not line.startswith('|'):
                    if line:
                        break
                    continue
                if re.match(r'^\|[\s\-|]+\|$', line):
                    continue
                cells = [c.strip() for c in line.split('|') if c.strip()]
                if not cells:
                    continue
                fr_match = re.match(r'^(FR-\d+)$', cells[0])
                if not fr_match:
                    continue
                fr_id = f"FR-{fr_match.group(1).split('-')[1].zfill(2)}"
                if nfr_col < len(cells):
                    nfr_ids = [f"NFR-{n.zfill(2)}" for n in re.findall(r'NFR-(\d+)', cells[nfr_col])]
                    if nfr_ids:
                        fr_nfr[fr_id] = nfr_ids
            # Reverse: NFR → [FR, ...]
            nfr_fr: dict[str, list[str]] = {}
            for fr_id, nfr_ids in fr_nfr.items():
                for nfr_id in nfr_ids:
                    nfr_fr.setdefault(nfr_id, []).append(fr_id)
            return nfr_fr
        except Exception:
            return {}

    def generate_quality_manifest(self, fr_ids: list[str], sad_path: str) -> Path:
        """Called at P2 exit. Parses SAD.md -> constraints + high_risk_modules."""
        try:
            from scripts.generate_sab import parse_sad
            sab = parse_sad(sad_path)
        except Exception:
            sab = {}

        _project_root = Path(sad_path).parent.parent
        nfr_map = sab.get("nfr_dim_map", {})
        # Fallback: if SAD.md nfr_dim_map is empty, parse from SRS.md
        if not nfr_map:
            srs_nfr = self._parse_nfr_from_srs(_project_root)
            nfr_map = srs_nfr or nfr_map

        # NFR→[FR] reverse mapping from §2 cross-reference table
        nfr_fr_map = self._parse_nfr_fr_xref(_project_root)

        manifest: dict[str, Any] = {
            "schema_version": "1.0",
            "generated_at_phase": 2,
            "fr_ids": fr_ids,
            "nfr_dimension_mapping": nfr_map,
            "nfr_fr_mapping": nfr_fr_map,
            "nfr_traceability": sab.get("nfr_traceability", {}),
            "architecture_constraints": sab.get("constraints", []),
            "high_risk_modules": sab.get("high_risk", []),
            "gate_score_overrides": {},
            "gate_results": {"gate1": {}, "gate2": None, "gate3": None, "gate4": None},
        }
        out = Path(".methodology/quality_manifest.json")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
        return out

    def _trigger_hooks(self, ctx: GateContext, event_name: str) -> None:
        """Trigger lifecycle hooks for gate events (non-fatal)."""
        try:
            from core.lifecycle_hooks import HookRunner, HookEvent
            event = HookEvent(event_name)
            runner = HookRunner(Path(ctx.project_root))
            runner.run_hooks(event, {"gate_num": str(ctx.gate_num), "phase": str(ctx.phase)})
        except Exception:
            pass  # hooks are non-fatal

    def _load_config(self, gate_num: int) -> GateConfig:
        """Load the YAML configuration for a specific gate."""
        import yaml  # type: ignore[import-untyped]
        names = {1: "gate1_per_fr.yaml", 2: "gate2_p3_exit.yaml",
                 3: "gate3_p4_exit.yaml", 4: "gate4_p6_full.yaml"}
        config_path = Path(__file__).parent / "gate_configs" / names[gate_num]
        with open(config_path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f)
        return GateConfig.from_dict(raw, gate_num)

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
