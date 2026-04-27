# SAD — Harness Methodology v1.0 (As-Built, Reverse-Engineered from main @ 2026-04-27)

> **Sync guarantee**: This document is reverse-engineered from the live codebase.
> Any change to the code **must** be reflected here, and vice-versa.
> Verification: an engineering team can rebuild the repo from this document alone.

---

## 1. Architectural Drivers

The architecture is driven by five strict non-functional requirements defined in the SRS.

### Driver 1 — Reviewer Independence (NFR-4)
- **Requirement**: Eliminate confirmation bias from "AI reviewing itself."
- **Decision**: Created `ReviewerRouter` + Hermes MCP external interface. All review responsibility is architecturally separated into an independently configurable external service.

### Driver 2 — Traceability & Auditability (NFR-3, NFR-6)
- **Requirement**: Every development step and decision must be traceable and post-hoc auditable.
- **Decision**: 8-Phase Pipe-and-Filter macro architecture with standardized artifacts flowing between stages. `quality_manifest.json` (gate results) and `DecisionLogWriter` (YAML per-decision) are the audit trail backbone.

### Driver 3 — Reliability & Reproducibility (NFR-1)
- **Requirement**: Quality assessment must be stable and not over-dependent on LLM stochasticity.
- **Decision**: Two mechanisms:
  1. **Hybrid scoring**: `min(tool_score, llm_score)` — LLM cannot score higher than deterministic tools.
  2. **CRG integration**: Code Review Graph provides graph-theory-based reproducible structural metrics for architecture/error-handling dimensions.

### Driver 4 — Security (NFR-2)
- **Requirement**: AI agents must be blocked from destructive operations or vulnerability introduction.
- **Decision**: Standalone `KillSwitch` module (circuit-breaker pattern) + Pre-fix Impact Analysis inside the Gate improvement loop (`CRGBridge.check_impact` before each fix round).

### Driver 5 — Maintainability (NFR-5)
- **Requirement**: The framework itself must be easy to understand, extend, and maintain.
- **Decision**: Lazy-Loading Factory (in `cli.py`) and Bridge pattern (in `harness/`). Lazy loading decouples subsystems; Bridge separates core workflow from quality-gate implementations and CRG tooling.

---

## 2. Macro Architecture & Design Patterns

### 2.1 8-Phase Pipe & 4-Gate Filter

The system uses this macro architecture:

- **Pipe**: 8 software development phases (P1–P8) form the main pipeline.
- **Filter**: 4 quality gates intercept at specific phase exits:
  - **Gate 1**: Per-FR check at P3/P5/P7/P8 (trigger: `per_fr_completion`)
  - **Gate 2**: Phase exit at P3 end (trigger: `phase_exit`, phase: 3)
  - **Gate 3**: Phase exit at P4 end (trigger: `phase_exit`, phase: 4)
  - **Gate 4**: Phase exit at P6 end (trigger: `phase_exit`, phase: 6)
- `harness/harness_bridge.py` is the gate lifecycle controller implementing this filter logic.

### 2.2 Key Design Patterns

| Pattern | Applied In | Purpose |
|---|---|---|
| Lazy-Loading Factory | `cli.py` | Deferred subsystem init |
| Strategy Pattern | `core/agent_spawner.py` | Switch between Task tool vs Hermes reviewer |
| Bridge Pattern | `harness/` directory | Decouple methodology flow from quality tools |
| Façade Pattern | `cli.py` | Unified external interface over complex subsystems |
| Proxy Pattern | `harness/reviewer_router.py` | Local proxy to remote Hermes MCP service |
| Circuit Breaker | `implement/kill_switch/` | Safety backstop independent of main flow |
| Graceful Degradation | `harness/crg_bridge.py` | All CRG methods no-op if CRG not installed |

---

## 3. Detailed Module Design

### 3.1 `harness/harness_bridge.py` — Gate Controller & Bridge

**Responsibility**: Manages quality gate lifecycle. Core bridge between the methodology workflow and the `software_self_improvement` (SSI) framework.

**Data structures** (all in this file):

```python
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
    def __init__(self, gate_num: int, result: GateResult): ...
    # message: "Gate {n} BLOCKED — score={:.1f}, critical={c}, high={h}"
```

**Public API**:

```python
class HarnessBridge:
    def __init__(self):
        self.crg = CRGBridge()       # graceful degradation if unavailable
        self._log = DecisionLogWriter()
        self._effort = EffortTracker()

    def run_gate(
        self,
        gate_num: int,
        project_root: str,
        phase: int,
        fr_id: str | None = None,
    ) -> GateResult: ...

    def generate_quality_manifest(
        self,
        fr_ids: list[str],
        sad_path: str,
    ) -> Path: ...
```

**`run_gate` execution order**:
1. `_load_config(gate_num)` — loads `harness/gate_configs/gate{n}_*.yaml` via PyYAML
2. Record `t0 = time.time()`
3. If `config["crg"]["reconnaissance"]` is set → `self.crg.run_reconnaissance(project_root)`
4. `result = self._invoke_harness(config, project_root, fr_id)` — calls SSI runner subprocess (see below)
5. `self._update_quality_manifest(gate_num, fr_id, result)` — writes to `.methodology/quality_manifest.json`
6. `self._effort.record(EffortRecord(phase, gate_num, "GATE", "gate_run", duration_s=time.time()-t0))`
7. `self._log.write(DecisionLogEntry(..., decision="GATE_PASS" or "GATE_BLOCK", gate_score=result.score))`
8. **Blocking logic**:
   - Gate 1: `raise GateBlockedError` if any `d.score < d.threshold` in `result.dimensions`
   - Gate 2/3/4: `raise GateBlockedError` if `result.score < config["score_gate"]` OR `not result.quality_complete`
9. Gate 4 only: `_require_hermes_approve(result, phase, fr_id)` — Hermes reviewer must APPROVE
10. Return `GateResult`

**`_invoke_harness` implementation**:
- Writes gate config to `.sessi-work/gate{n}_config.yaml`
- Clears `.sessi-work/gate{n}_result.json`
- Runs: `python3 -m software_self_improvement.runner --config <path> --root <root> --output <result_path> [--fr-id <fr_id>]`
- Timeout: `config["max_rounds"] * 300` seconds
- On completion: reads and parses `.sessi-work/gate{n}_result.json` → `GateResult`
- **Runtime prerequisite**: `software_self_improvement` package must be installed and on `PYTHONPATH`

**Expected SSI runner output JSON** (`.sessi-work/gate{n}_result.json`):
```json
{
  "score": 85.5,
  "quality_complete": true,
  "rounds_used": 2,
  "open_critical": 0,
  "open_high": 1,
  "dimensions": [
    {"name": "linting", "score": 92.0, "threshold": 90, "issues": []}
  ]
}
```

**`_require_hermes_approve(result, phase, fr_id)`** (Gate 4 only):
- Instantiates `ReviewerRouter()` — silently skips if `HERMES_REVIEWER_TARGET` not set
- Sends gate score + dimension summary for human review via Hermes
- `review_status != "APPROVE"` → logs `REVIEWER_REJECT` + raises `GateBlockedError(4, result)`

**`generate_quality_manifest` logic**:
- Called at P2 exit
- Calls `from scripts.generate_sab import parse_sad` — **now functional** (added in fix ②+⑤)
- `parse_sad` returns `{nfr_dim_map, constraints, high_risk, ...}` from SAD.md SAB block
- Writes JSON to `.methodology/quality_manifest.json` with schema:
  ```json
  {
    "schema_version": "1.0",
    "generated_at_phase": 2,
    "fr_ids": [...],
    "nfr_dimension_mapping": {},
    "architecture_constraints": [],
    "high_risk_modules": [],
    "gate_score_overrides": {},
    "gate_results": {
      "gate1": {},
      "gate2": null,
      "gate3": null,
      "gate4": null
    }
  }
  ```

**`_update_quality_manifest` logic**:
- Reads existing `.methodology/quality_manifest.json`
- Gate 1 (with `fr_id`): `manifest["gate_results"]["gate1"][fr_id] = payload`
- Gate 2/3/4: `manifest["gate_results"]["gate{n}"] = payload`
- `payload = {score, quality_complete, rounds_used, open_critical, open_high}`

---

### 3.2 `harness/reviewer_router.py` — Reviewer Proxy

**Responsibility**: Routes review requests to heterogeneous backend via Hermes MCP. Hermes is the sole reviewer path; Gemini has been removed from the reviewer chain (since v1.3).

**Module-level constants & functions**:

```python
HERMES_TARGET = os.environ.get("HERMES_REVIEWER_TARGET", "")
HERMES_TIMEOUT_MS = int(os.environ.get("HERMES_TIMEOUT_MS", "120000"))  # default 120 s
_CLAUDE_PHASES = {7, 8}   # Risk Assessment + Config Mgmt stay on Claude

REVIEWER_POLICY = {
    "default": "hermes",
    "p7_risk": "claude",
    "p8_config": "claude",
}

def get_reviewer_model(phase: int, role: str = "reviewer") -> str:
    """Returns 'claude' if phase in {7, 8}, else REVIEWER_POLICY[role] (defaults to 'hermes')."""
    return "claude" if phase in _CLAUDE_PHASES else REVIEWER_POLICY.get(role, "hermes")
```

> **⚠️ Implementation note**: `get_reviewer_model()` is defined but **not called** by `review()`. The `review()` method always uses the Hermes path regardless of phase. P7/P8 Claude routing is policy-declared but not yet wired in.

**Public API**:

```python
class ReviewerRouter:
    def __init__(self, target: str = HERMES_TARGET):
        # Raises ValueError if target is empty string
        # e.g. export HERMES_REVIEWER_TARGET=telegram:6308981865

    def review(
        self,
        role: str,
        prompt: str,
        phase: int,
        fr_id: str | None = None,
    ) -> dict:
        # Returns: {"review_status": "APPROVE|REJECT", "confidence": 0-1,
        #           "violations": [], "summary": ""}
```

**`review()` execution sequence**:
1. Check `_HERMES_AVAILABLE` (import guard) → raise `RuntimeError` if False
2. `full_prompt = self._build_prompt(role, prompt, phase, fr_id)`
3. `mcp__hermes__messages_send(target=self.target, message=full_prompt)`
4. `mcp__hermes__events_wait(session_key=self.target, timeout_ms=HERMES_TIMEOUT_MS)` — long-poll
5. `msgs = mcp__hermes__messages_read(session_key=self.target, limit=1)`
6. `raw = msgs[-1]["content"] if msgs else ""`
7. `return self._parse_response(raw)`

**`_build_prompt(role, prompt, phase, fr_id=None) -> str`**:
```
[Harness Reviewer | Phase {phase}{ | FR {fr_id}}]
Role: {role}

{prompt}

Output JSON: {"review_status": "APPROVE|REJECT", "confidence": 0-1, "violations": [], "summary": ""}
```

**`_parse_response(raw: str) -> dict`**:
- Extracts first JSON object via `re.search(r"\{.*\}", raw, re.DOTALL)`
- On success: `json.loads(match.group())`
- On failure: `{"review_status": "REJECT", "confidence": 0.0, "violations": ["parse_error"], "summary": raw[:200]}`

**Hermes MCP imports** (with graceful fallback):
```python
try:
    from mcp_tools import (mcp__hermes__messages_send,
                           mcp__hermes__events_wait,
                           mcp__hermes__messages_read)
    _HERMES_AVAILABLE = True
except ImportError:
    _HERMES_AVAILABLE = False
```

---

### 3.3 `harness/crg_bridge.py` — Deterministic Analysis Bridge

**Responsibility**: Wraps `software_self_improvement`'s `crg_integration.py` and `crg_analysis.py`. All interaction is via `subprocess.run`. Gracefully degrades if CRG not installed.

**Class-level cache**:
```python
class CRGBridge:
    _available: bool | None = None  # lazy singleton, cached after first check
```

**Public API**:

| Method | Subprocess Command | Return |
|---|---|---|
| `is_available() -> bool` | `python3 -c "import mcp__code_review_graph"` | cached bool |
| `run_reconnaissance(project_root) -> dict` | `scripts/crg_integration.py ensure {root}` | reads `.sessi-work/crg_reconnaissance.json` |
| `get_minimal_context(project_root, dimension) -> dict` | `scripts/crg_integration.py context {root} {dim}` | parsed stdout JSON |
| `check_impact(project_root, ref="HEAD", threshold=0.7) -> bool` | `scripts/crg_integration.py risky {root} {ref} {threshold}` | `returncode == 1` means risky |
| `check_drift(project_root, threshold=0.4) -> bool` | reads `.sessi-work/crg_metrics.json` | `structural_drift > threshold` |
| `load_metrics(project_root) -> dict` | reads `.sessi-work/crg_metrics.json` | full metrics dict (6 formula-driven signals) |

**Environment dependency**:
- `SSI_ROOT` env var (default: `"software_self_improvement"`) — used as `cwd` for all subprocess calls via `_ssi_root()`.

**Graceful degradation**: If `is_available()` is `False`, all methods return `{}` or `False` immediately.

**CRG integration points** (§6.5):
1. **Point 1 — Structural Reconnaissance** (Gate 3/4 entry): `run_reconnaissance` — 9 CRG queries, seeds issue registry, ~3,900 tokens, once per session.
2. **Point 2 — Tier 3 Guidance** (before each Tier 3 eval): `get_minimal_context` — reduces Tier 3 eval tokens 30–50%.
3. **Point 3 — Pre-fix Safety Gate** (before each improvement round): `check_impact` — defers fix if risky.
4. **Point 4 — Post-round Drift Check** (after each improvement round): `check_drift` — triggers revert protocol if structural drift > threshold.

---

### 3.4 `harness/decision_log.py` — Decision Audit Log Writer

**Responsibility**: Write per-decision YAML audit entries. Implements the traceability requirement (NFR-3/NFR-6).

**Data structures**:

```python
@dataclass
class DecisionLogEntry:
    agent_id: str
    phase: int
    fr_id: str | None
    decision: str          # e.g. "GATE_PASS", "GATE_BLOCK"
    reasoning: str
    gate_score: float | None = None
    created_at: datetime = field(default_factory=datetime.now)
```

**`DecisionLogWriter`**:

```python
class DecisionLogWriter:
    BASE = Path(".methodology/decision_logs")

    def write(self, entry: DecisionLogEntry) -> Path:
        # Output path: BASE/{YYYY-MM-DD}/{agent_id}_{phase}_{seq:03d}.yaml
        # seq = count of existing files in today's directory
```

- Creates parent directories automatically.
- YAML format: all dataclass fields as top-level keys, `created_at` as ISO string.

---

### 3.5 `harness/effort_tracker.py` — Gate Effort Metrics

**Responsibility**: SQLite-backed gate effort tracking for performance monitoring.

**Data structures**:

```python
@dataclass
class EffortRecord:
    phase: int
    gate_num: int
    agent_id: str
    operation: str         # e.g. "gate_run"
    duration_s: float
    created_at: datetime = field(default_factory=datetime.now)
```

**`EffortTracker`**:

```python
class EffortTracker:
    DB_PATH = Path(".methodology/effort_metrics.db")

    def record(self, r: EffortRecord) -> None:
        # INSERT into SQLite table `effort_records`

    def summary(self, phase: int | None = None) -> dict:
        # Aggregated stats: total records, mean/max duration, breakdown by phase/gate

    def export_csv(self, path: str | Path) -> Path:
        # Export all records to CSV
```

- DB auto-created on first `record()` call.
- Schema: `CREATE TABLE effort_records (phase, gate_num, agent_id, operation, duration_s, created_at)`

---

### 3.6 `harness/issue_tracker_ext.py` — FR-Tagged Issue Tracker

**Responsibility**: Extends `software_self_improvement`'s `IssueTracker` with per-FR tagging and saturation detection. Addresses Gap G5.

**Import guard** (inline stub if SSI not installed):
```python
try:
    from software_self_improvement.scripts.issue_tracker import IssueTracker
except ImportError:
    class IssueTracker:  # minimal stub
        def add_finding(self, dimension, severity, file, line, message, evidence): ...
        def open_issues(self): ...
```

**`IssueTrackerExt(IssueTracker)`**:

```python
def add_finding(
    self, dimension, severity, file, line, message, evidence,
    fr_id: str | None = None,
) -> str:
    # Calls super(), then tags the issue with fr_id if provided

def get_findings_by_fr(self, fr_id: str) -> list[dict]:
    # Returns open issues where fr_id in issue["fr_ids"]

def fr_saturation_check(
    self, fr_id: str, current_finding_ids: set[str], threshold: int = 2
) -> bool:
    # Returns True if no new issues for `threshold` consecutive rounds
    # Internal state: _round_findings[fr_id] and _saturation_counters[fr_id]

def fr_coverage_summary(self, fr_ids: list[str]) -> dict:
    # Returns {fr_id: open_finding_count, ...}
```

---

### 3.7 `core/agent_spawner.py` — Agent Strategy Router

**Responsibility**: Routes agent invocations to Task tool (Claude Code) or ReviewerRouter (Hermes MCP). Implements Gap G2 (heterogeneous reviewer). Applies need-to-know isolation: each agent receives only its persona + current-phase SOP + task.

**File layout helpers** (module-level):
```python
def _load_persona(role: str) -> str:
    # Reads agent_personas/{ROLE.upper()}.md — empty string if missing

def _load_phase_sop(phase: int) -> str:
    # Reads docs/P{phase}_SOP.md — empty string if missing
```

**`AgentSpawner`**:

```python
class AgentSpawner:
    _reviewer = None   # class-level lazy-init: avoids crash if HERMES env not set

    def spawn(
        self,
        role: str,
        prompt: str,
        context: dict,
        model: str = "claude",   # "claude" | "hermes"
        task_timeout: int = 300,
        phase: int = 0,
        fr_id: str | None = None,
    ) -> dict: ...
```

**`spawn()` routing** (with phase policy enforcement):
```
model == "hermes":
    effective = get_reviewer_model(phase, role)   # checks _CLAUDE_PHASES = {7, 8}
    if effective == "hermes":
        → ReviewerRouter.review(role, full_prompt, phase, fr_id)  [return]
    # effective == "claude" for P7/P8 — fall through to Task tool

model == "claude" (or P7/P8 auto-routed):
    → claude_code_sdk.Task(description=..., prompt=..., timeout=task_timeout)
      raises RuntimeError if claude_code_sdk not importable
```
- P7 (Risk Assessment) and P8 (Config Mgmt) **always use Claude**, even when caller passes `model="hermes"`

**`_build_prompt(role, prompt, context, phase) -> str`** — constructs:
```
[PERSONA]
{persona content}

[SOP]
{phase SOP content}

[TASK]
{prompt}

[CONTEXT]
  {key}: {value}   # excludes "phase" key
```

**`_parse_result(result) -> dict`**: If result is already dict, return as-is; else `{"output": str(result), "status": "complete"}`.

---

### 3.8 `core/phase_hooks.py` — Phase Execution Hooks Framework

**Responsibility**: Pre-flight, monitoring, and post-flight hooks for agent phase execution.

**`PhaseHooks(project_path: str, phase: int = None)`**:

File paths used:
- `.methodology/state.json` — FSM state
- `.methodology/run-phase.log` — append-only run log
- `docs/` — for constitution checks

**Pre-flight hooks** (`preflight_all() -> dict` calls all three):

| Method | Check | Blocks if |
|---|---|---|
| `preflight_fsm_check()` | reads `state.json` | state in `{"FREEZE", "PAUSED"}` or phase regression |
| `preflight_constitution(check_mode="preflight")` | calls `quality_gate.constitution.run_constitution_check` | violations found |
| `preflight_tool_registry()` | checks `ToolRegistry.list_tools()` | skipped if not installed |

**Monitoring hooks** (append to `self.monitoring_events` + write to `run-phase.log`):

| Method | Signature | Records |
|---|---|---|
| `monitoring_before_dev` | `(fr_id)` | `{"type": "before_dev", "fr_id": ...}` |
| `monitoring_after_dev` | `(fr_id, result=None)` | `status`, `confidence` from result |
| `monitoring_before_rev` | `(fr_id)` | `{"type": "before_rev", "fr_id": ...}` |
| `monitoring_after_rev` | `(fr_id, result=None)` | `review_status`, `status`, `confidence` |
| `monitoring_hr12_check` | `(fr_id, iteration, max_iterations=5)` | Returns `False` if `iteration >= max_iterations` |

**Post-flight hooks** (`postflight_all() -> dict` calls all three):

| Method | Action |
|---|---|
| `postflight_constitution()` | Re-runs constitution check with `check_mode="postflight"` |
| `postflight_update_state(success=True)` | Advances `state.json` current_phase if `self.phase > old_phase` |
| `postflight_summary()` | Returns `{total_frs, approved, fr_results, monitoring_events}` |

**Success condition for `postflight_all`**: `constitution.passed AND all FR results have review_status == "APPROVE"`.

---

### 3.9 `core/hybrid_workflow.py` — Smart-Routing Workflow

**Responsibility**: Three-mode workflow controller that auto-routes between single-agent and A/B review based on change size/type.

**Enums & dataclasses**:

```python
class WorkflowMode(Enum):
    OFF = "off"        # Single agent, no review
    HYBRID = "hybrid"  # Smart routing
    ON = "on"          # Forced A/B review always

class ChangeType(Enum):
    SMALL = "small"
    LARGE = "large"

@dataclass
class ChangeAnalysis:
    type: ChangeType
    lines_changed: int
    files_affected: int
    is_security_related: bool
    is_new_feature: bool
    reason: str
```

**`HybridWorkflow(mode=HYBRID, small_change_threshold=10, large_change_threshold=30)`**:

```python
def analyze_change(self, diff: str) -> ChangeAnalysis:
    # Security keywords: ['auth','password','token','permission','security']
    # New-feature keywords: ['def new_','class new_','# new']
    # LARGE if: is_security OR is_new_feature OR total_changes > large_threshold
    # SMALL if: total_changes < small_threshold
    # SMALL otherwise (medium change auto-passes)

def should_review(self, analysis: ChangeAnalysis) -> bool:
    # OFF  → False (auto-approve)
    # ON   → True  (always review)
    # HYBRID → True if ChangeType.LARGE, False if SMALL

def execute(self, diff: str, code_func: Callable) -> dict:
    # Returns {"status": "needs_review", ...} or {"status": "auto_approved", "result": ..., ...}

def get_stats(self) -> dict:
    # Returns {total_tasks, auto_approved, review_required, auto_approve_rate, review_rate}
```

---

## 4. Core Workflow Sequences

### 4.1 Gate Run (e.g. Gate 2, P3 exit)

```
Operator -> HarnessBridge.run_gate(gate_num=2, project_root, phase=3, fr_id=None)
  │
  ├─ 1. _load_config(2) → reads harness/gate_configs/gate2_p3_exit.yaml
  │
  ├─ 2. t0 = time.time()
  │
  ├─ 3. [gate2: crg.impact_check=true but no crg.reconnaissance — no recon step here]
  │
  ├─ 4. _invoke_harness(config, project_root, None)
  │      ├─ writes .sessi-work/gate2_config.yaml
  │      ├─ subprocess.run(["python3", "-m", "software_self_improvement.runner",
  │      │                  "--config", ..., "--root", ..., "--output", ...])
  │      │   timeout = max_rounds(3) * 300 = 900s
  │      └─ reads .sessi-work/gate2_result.json → GateResult
  │         [RuntimeError if result file missing after subprocess exits]
  │
  ├─ 5. _update_quality_manifest(2, None, result)
  ├─ 6. EffortTracker.record(...)
  ├─ 7. DecisionLogWriter.write(decision="GATE_PASS|GATE_BLOCK")
  ├─ 8. if result.score < 75 or not result.quality_complete → raise GateBlockedError
  └─ 9. return GateResult
  [Gate 4 only: step 9 = _require_hermes_approve() before return]
```

> **Runtime prerequisite**: `software_self_improvement` package must be installed. The subprocess interface is wired; the external package is the remaining dependency.

### 4.2 A/B Review via Hermes MCP

```
AgentSpawner.spawn(model="hermes", role="Reviewer", prompt, context, phase, fr_id)
  │
  └─ ReviewerRouter.review(role, full_prompt, phase, fr_id)
       │
       ├─ 1. _build_prompt() → "[Harness Reviewer | Phase N | FR X]\nRole: ...\n{prompt}\nOutput JSON: ..."
       │
       ├─ 2. mcp__hermes__messages_send(target=HERMES_TARGET, message=full_prompt)
       │
       ├─ 3. mcp__hermes__events_wait(session_key=HERMES_TARGET, timeout_ms=120000)
       │      └─ Long-poll (default 120 s)
       │
       ├─ 4. msgs = mcp__hermes__messages_read(session_key=HERMES_TARGET, limit=1)
       │
       ├─ 5. raw = msgs[-1]["content"] if msgs else ""
       │
       └─ 6. _parse_response(raw)
              ├─ Success: json.loads(re.search(r"\{.*\}", raw, re.DOTALL).group())
              └─ Failure: {"review_status": "REJECT", "confidence": 0.0,
                           "violations": ["parse_error"], "summary": raw[:200]}
```

### 4.3 Quality Manifest Generation (P2 exit)

```
HarnessBridge.generate_quality_manifest(fr_ids=["FR-01","FR-02"], sad_path="SAD.md")
  │
  ├─ try: from scripts.generate_sab import parse_sad  ← ⚠️ ALWAYS FAILS (fn doesn't exist)
  │  except: sab = {}
  │
  ├─ manifest = {schema_version, generated_at_phase=2, fr_ids, nfr_dimension_mapping={},
  │              architecture_constraints=[], high_risk_modules=[], gate_score_overrides={},
  │              gate_results={gate1:{}, gate2:null, gate3:null, gate4:null}}
  │
  └─ Path(".methodology/quality_manifest.json").write_text(json.dumps(manifest, indent=2))
```

### 4.4 PhaseHooks Pre/Post Flight

```
PhaseHooks("/path/to/project", phase=3)
  │
  ├─ preflight_all()
  │    ├─ preflight_fsm_check()     → reads .methodology/state.json
  │    ├─ preflight_constitution()  → quality_gate.constitution.run_constitution_check(...)
  │    └─ preflight_tool_registry() → ToolRegistry.list_tools() (skipped if not installed)
  │
  ├─ [per-FR development loop]
  │    ├─ monitoring_before_dev(fr_id)
  │    ├─ [developer executes]
  │    ├─ monitoring_after_dev(fr_id, result)
  │    ├─ monitoring_before_rev(fr_id)
  │    ├─ [reviewer executes]
  │    ├─ monitoring_after_rev(fr_id, result)
  │    └─ monitoring_hr12_check(fr_id, iteration)  → False if iteration >= 5
  │
  └─ postflight_all()
       ├─ postflight_constitution()          → re-check with check_mode="postflight"
       ├─ postflight_update_state(success)   → advance state.json current_phase
       └─ postflight_summary()               → {total_frs, approved, fr_results, ...}
```

---

## 5. Appendix A — Configuration Schemas

### 5.1 `harness/gate_configs/*.yaml` — Gate Configuration

Four files: `gate1_per_fr.yaml`, `gate2_p3_exit.yaml`, `gate3_p4_exit.yaml`, `gate4_p6_full.yaml`.

**Schema** (all fields):

| Field | Type | Required | Description |
|---|---|---|---|
| `gate` | `int` | Yes | Gate number (1–4) |
| `trigger` | `str` | Yes | `per_fr_completion` or `phase_exit` |
| `phase` | `int` | Conditional | Required when `trigger: phase_exit` |
| `scope` | `str` | Yes | `single_fr`, `full_phase`, or `full_project` |
| `dimensions` | `list[dict]` | Yes | Scoring dimensions (see below) |
| `dimensions[].name` | `str` | Yes | Dimension name |
| `dimensions[].tier` | `int` | Yes | LLM tier: 1=fastest, 3=most capable |
| `dimensions[].model` | `str` | Yes | `gemini-flash` or `claude` |
| `dimensions[].threshold` | `int` | Yes | Pass threshold (0–100) |
| `dimensions[].weight` | `float` | Yes | Weight in composite score (must sum to 1.0) |
| `blocking` | `bool` | Yes | Whether gate is blocking |
| `score_gate` | `int` | Gate 2/3/4 only | Composite score threshold |
| `max_rounds` | `int` | Yes | Max auto-fix iterations |
| `early_stop` | `bool` | Yes | Stop eval after first issue found |
| `saturation_rounds` | `int` | Gate 2/3/4 only | Consecutive no-new-issue rounds → saturated |
| `mutation_testing` | `dict` | Gate 2/3/4 only | `{median_runs: int, timeout_per_run: int}` |
| `crg` | `dict` | Gate 2/3/4 only | CRG integration settings (see below) |
| `crg.enabled` | `bool` | Gate 3/4 | Enable CRG |
| `crg.reconnaissance` | `bool` | Gate 3/4 | Run structural recon at gate entry |
| `crg.tier3_guidance` | `bool` | Gate 3/4 | Get CRG guidance before each Tier 3 eval |
| `crg.impact_check` | `bool` | Gate 2 | Run impact analysis before each fix round |
| `crg.impact_threshold` | `float` | Gate 2/3/4 | Risk score threshold (default: 0.7) |
| `crg.drift_threshold` | `float` | Gate 3/4 | Structural drift threshold (default: 0.4) |
| `replaces` | `str` | Yes | Legacy SOP component this gate replaces |

**Actual gate configurations**:

**Gate 1** — `gate1_per_fr.yaml` (per-FR at P3/P5/P7/P8):
```yaml
gate: 1
trigger: per_fr_completion
scope: single_fr
dimensions:
  - { name: linting,       tier: 1, model: gemini-flash, threshold: 90, weight: 0.33 }
  - { name: type_safety,   tier: 1, model: gemini-flash, threshold: 85, weight: 0.33 }
  - { name: test_coverage, tier: 1, model: gemini-flash, threshold: 80, weight: 0.34 }
blocking: true
early_stop: false
max_rounds: 1
replaces: check_fr_full_layer3
```
> Note: No `score_gate` — Gate 1 uses per-dimension thresholds only. No auto-iteration on failure; developer must manually fix and re-run.

**Gate 2** — `gate2_p3_exit.yaml` (P3 exit — all FRs complete):
```yaml
gate: 2
trigger: phase_exit
phase: 3
scope: full_phase
dimensions:
  - { name: linting,            tier: 1, model: gemini-flash, threshold: 90,  weight: 0.15 }
  - { name: type_safety,        tier: 1, model: gemini-flash, threshold: 85,  weight: 0.15 }
  - { name: test_coverage,      tier: 1, model: gemini-flash, threshold: 80,  weight: 0.15 }
  - { name: security,           tier: 2, model: gemini-flash, threshold: 80,  weight: 0.15 }
  - { name: secrets_scanning,   tier: 1, model: gemini-flash, threshold: 100, weight: 0.10 }
  - { name: license_compliance, tier: 1, model: gemini-flash, threshold: 100, weight: 0.10 }
  - { name: mutation_testing,   tier: 1, model: gemini-flash, threshold: 70,  weight: 0.20 }
blocking: true
score_gate: 75
max_rounds: 3
early_stop: true
saturation_rounds: 3
mutation_testing: { median_runs: 3, timeout_per_run: 120 }
crg: { impact_check: true, impact_threshold: 0.7 }
replaces: auto_research_p3
```

**Gate 3** — `gate3_p4_exit.yaml` (P4 exit — testing complete, first full 12-dim eval):
```yaml
gate: 3
trigger: phase_exit
phase: 4
scope: full_phase
dimensions:
  - { name: linting,            tier: 1, model: gemini-flash, threshold: 90,  weight: 0.10 }
  - { name: type_safety,        tier: 1, model: gemini-flash, threshold: 85,  weight: 0.10 }
  - { name: test_coverage,      tier: 1, model: gemini-flash, threshold: 80,  weight: 0.10 }
  - { name: security,           tier: 2, model: gemini-flash, threshold: 80,  weight: 0.10 }
  - { name: secrets_scanning,   tier: 1, model: gemini-flash, threshold: 100, weight: 0.08 }
  - { name: license_compliance, tier: 1, model: gemini-flash, threshold: 100, weight: 0.07 }
  - { name: mutation_testing,   tier: 1, model: gemini-flash, threshold: 70,  weight: 0.10 }
  - { name: architecture,       tier: 3, model: claude,       threshold: 80,  weight: 0.10 }
  - { name: readability,        tier: 3, model: claude,       threshold: 80,  weight: 0.07 }
  - { name: error_handling,     tier: 3, model: claude,       threshold: 80,  weight: 0.10 }
  - { name: documentation,      tier: 3, model: claude,       threshold: 75,  weight: 0.03 }
  - { name: performance,        tier: 3, model: claude,       threshold: 75,  weight: 0.05 }
blocking: true
score_gate: 80
max_rounds: 3
early_stop: true
saturation_rounds: 3
mutation_testing: { median_runs: 3, timeout_per_run: 120 }
crg:
  enabled: true
  reconnaissance: true
  tier3_guidance: true
  impact_threshold: 0.7
  drift_threshold: 0.4
replaces: auto_research_p4
```

**Gate 4** — `gate4_p6_full.yaml` (P6 exit — final gate, full project):
```yaml
gate: 4
trigger: phase_exit
phase: 6
scope: full_project
dimensions:
  - { name: linting,            tier: 1, model: gemini-flash, threshold: 90,  weight: 0.08 }
  - { name: type_safety,        tier: 1, model: gemini-flash, threshold: 85,  weight: 0.08 }
  - { name: test_coverage,      tier: 1, model: gemini-flash, threshold: 80,  weight: 0.08 }
  - { name: security,           tier: 2, model: gemini-flash, threshold: 80,  weight: 0.10 }
  - { name: secrets_scanning,   tier: 1, model: gemini-flash, threshold: 100, weight: 0.07 }
  - { name: license_compliance, tier: 1, model: gemini-flash, threshold: 100, weight: 0.07 }
  - { name: mutation_testing,   tier: 1, model: gemini-flash, threshold: 70,  weight: 0.08 }
  - { name: architecture,       tier: 3, model: claude,       threshold: 80,  weight: 0.14 }
  - { name: readability,        tier: 3, model: claude,       threshold: 80,  weight: 0.08 }
  - { name: error_handling,     tier: 3, model: claude,       threshold: 80,  weight: 0.10 }
  - { name: documentation,      tier: 3, model: claude,       threshold: 75,  weight: 0.07 }
  - { name: performance,        tier: 3, model: claude,       threshold: 75,  weight: 0.05 }
blocking: true
score_gate: 85
max_rounds: 3
early_stop: true
saturation_rounds: 3
mutation_testing: { median_runs: 3, timeout_per_run: 120 }
crg:
  enabled: true
  reconnaissance: true
  tier3_guidance: true
  impact_threshold: 0.7
  drift_threshold: 0.4
replaces: p6_sop_entirely
```
> Gate 4 additionally requires Hermes reviewer APPROVE (enforced at a higher orchestration level, not in the YAML itself).

---

### 5.2 `.methodology/quality_manifest.json`

Schema defined by `schemas/quality_manifest.schema.json` (JSON Schema Draft 7). Structure:

```json
{
  "schema_version": "1.0",
  "generated_at_phase": 2,
  "fr_ids": ["FR-01", "FR-02"],
  "nfr_dimension_mapping": {},
  "architecture_constraints": [],
  "high_risk_modules": [],
  "gate_score_overrides": {},
  "gate_results": {
    "gate1": {
      "FR-01": {
        "score": 92.5,
        "quality_complete": true,
        "rounds_used": 1,
        "open_critical": 0,
        "open_high": 0
      }
    },
    "gate2": null,
    "gate3": null,
    "gate4": null
  }
}
```

- `gate1` stores a dict keyed by `fr_id` (per-FR)
- `gate2`/`gate3`/`gate4` store a single payload dict (full-phase/full-project)

---

### 5.3 `.methodology/decision_logs/{date}/{agent}_{phase}_{seq:03d}.yaml`

Written by `DecisionLogWriter.write()`:

```yaml
agent_id: GATE
phase: 3
fr_id: FR-01
decision: GATE_PASS
reasoning: "Gate 2: score=81.3, critical=0, high=0, rounds=2"
gate_score: 81.3
created_at: "2026-04-27T14:32:01.123456"
```

---

### 5.4 `.methodology/effort_metrics.db` — SQLite Schema

```sql
CREATE TABLE IF NOT EXISTS effort_records (
    phase      INTEGER,
    gate_num   INTEGER,
    agent_id   TEXT,
    operation  TEXT,
    duration_s REAL,
    created_at TEXT
);
```

---

## 6. SAB Block (machine-readable, v1.1 — As-Built)

> Updated from v1.2 original. Removed non-existent modules; added all verified harness/ and core/ modules.

<!-- SAB:START -->
```json
{
  "version": "1.1",
  "created_at": "2026-04-27",
  "project": "harness-methodology",
  "layers": [
    {
      "name": "0_Entrypoint_Facade",
      "description": "CLI entrypoints acting as facades over the full system.",
      "modules": ["cli.py", "cli_phase_subagent.py"],
      "allowed_dependencies": ["1_Integration_Bridge", "2_Core_Orchestration"]
    },
    {
      "name": "1_Integration_Bridge",
      "description": "Bridge layer connecting methodology workflow to external tools: Quality Gates, CRG, Hermes MCP, and audit/metrics sinks.",
      "modules": [
        "harness/harness_bridge.py",
        "harness/reviewer_router.py",
        "harness/crg_bridge.py",
        "harness/decision_log.py",
        "harness/effort_tracker.py",
        "harness/issue_tracker_ext.py"
      ],
      "allowed_dependencies": ["2_Core_Orchestration", "3_Quality_Features", "4_Base_Utilities"]
    },
    {
      "name": "2_Core_Orchestration",
      "description": "Manages agent lifecycle, phase execution, workflow routing, task decomposition, and session logging.",
      "modules": [
        "core/agent_spawner.py",
        "core/phase_hooks.py",
        "core/hybrid_workflow.py",
        "core/task_splitter.py",
        "core/sessions_spawn_logger.py",
        "core/subagent_isolator.py",
        "core/verification_gate.py"
      ],
      "allowed_dependencies": ["3_Quality_Features", "4_Base_Utilities"]
    },
    {
      "name": "3_Quality_Features",
      "description": "Concrete quality check implementations, safety features, gap detection, and enforcement hooks.",
      "modules": [
        "core/quality_gate/",
        "core/requirement_traceability.py",
        "detection/",
        "gap_detector/",
        "implement/kill_switch/",
        "enforcement/"
      ],
      "allowed_dependencies": ["4_Base_Utilities"]
    },
    {
      "name": "4_Base_Utilities",
      "description": "Cross-cutting concerns: schemas, configuration, templates, and CLI prompts.",
      "modules": [
        "schemas/",
        "core/enforcement_config.py",
        "core/cli_phase_prompts.py",
        "templates/"
      ],
      "allowed_dependencies": []
    }
  ],
  "dependencies": {
    "0_Entrypoint_Facade": ["1_Integration_Bridge", "2_Core_Orchestration"],
    "1_Integration_Bridge": ["2_Core_Orchestration", "3_Quality_Features", "4_Base_Utilities"],
    "2_Core_Orchestration": ["3_Quality_Features", "4_Base_Utilities"],
    "3_Quality_Features": ["4_Base_Utilities"],
    "4_Base_Utilities": []
  },
  "quality_targets": {
    "max_complexity": 20,
    "min_coverage": 80,
    "max_coupling": 0.35
  }
}
```
<!-- SAB:END -->

---

## 7. Runtime Prerequisites & Remaining Work

All in-framework bugs and stubs have been resolved. The one remaining dependency is external:

| Item | Location | Status | Notes |
|---|---|---|---|
| `software_self_improvement` package | `harness_bridge._invoke_harness()` | **External dependency** — not in this repo | Must be installed separately. Subprocess interface is wired and ready. Install, then gate runs work end-to-end. |

### Previously Fixed Stubs (resolved in this version)

| Fix | Location | Resolution |
|---|---|---|
| ① SSI runner stub | `harness_bridge._invoke_harness()` | Replaced `NotImplementedError` with subprocess call + JSON result parsing |
| ② `parse_sad` import failure | `harness_bridge.generate_quality_manifest()` | Fixed by adding `parse_sad()` to `scripts/generate_sab.py` |
| ③ P7/P8 Claude routing not wired | `core/agent_spawner.spawn()` | `get_reviewer_model(phase, role)` now checked before Hermes dispatch; P7/P8 auto-route to Claude |
| ④ Gate 4 Hermes APPROVE not enforced | `harness_bridge.run_gate()` | Added `_require_hermes_approve()` called after score check passes |
| ⑤ `parse_sad` alias missing | `scripts/generate_sab.py` | Added `parse_sad()` function wrapping `extract_sab_from_sad`, with correct key mapping |
