# SAD — Harness Methodology v2.2 (As-Built — Audit: 100% SAD↔code consistency, 2026-05-04)

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
  - **Gate 1**: Per-FR check at P3/P4/P5/P7/P8 (trigger: `per_fr_completion`)
  - **Gate 2**: Phase exit at P3 end (trigger: `phase_exit`, phase: 3)
  - **Gate 3**: Phase exit at P4 end (trigger: `phase_exit`, phase: 4)
  - **Gate 4**: Phase exit at P6 end (trigger: `phase_exit`, phase: 6)
- `harness/harness_bridge.py` is the gate lifecycle controller implementing this filter logic.

### 2.2 Key Design Patterns

| Pattern | Applied In | Purpose |
|---|---|---|
| Lazy-Loading Factory | `cli.py` (full system) | Deferred subsystem init across 30+ modules |
| Strategy Pattern | `core/agent_spawner.py` | Switch between Task tool vs Hermes reviewer |
| Bridge Pattern | `harness/` directory | Decouple methodology flow from quality tools |
| Façade Pattern | `harness_cli.py` (standalone) | Minimal harness-only CLI facade |
| Proxy Pattern | `harness/reviewer_router.py` | Local proxy to remote Hermes MCP service |
| Circuit Breaker | `kill_switch/` | Safety backstop independent of main flow |
| Graceful Degradation | `harness/crg_bridge.py` | All CRG methods no-op if CRG not installed |
| LLM-as-Judge | `steering/steering_loop.py` | Objective A/B output evaluation via LLM |

### 2.3 CLI Architecture: Two-Entry-Point Design

| Entry Point | File | Scope | Runnable Standalone |
|---|---|---|---|
| **Full system CLI** | `cli.py` | Requires 30+ external modules (`progress_dashboard`, `gantt_chart`, `sprint_planner`, `enterprise_hub`, `steering`, etc.) — belongs to the parent system | ❌ Not runnable in this repo alone |
| **Harness CLI** | `harness_cli.py` | Only uses modules present in this repo (`core/`, `harness/`) | ✅ Runnable standalone |

`cli.py` is retained as-is because it is the entrypoint for the full parent system that contains harness-methodology as a sub-component. Any work purely within harness-methodology should use `harness_cli.py`.

**`harness_cli.py` commands** (11 total):
```
python harness_cli.py plan-phase        --phase 3 [--repo .] [--output plan.md]
python harness_cli.py run-phase         --phase 3 [--project .] [--force]
python harness_cli.py run-gate          --gate 2 --phase 3 [--project .] [--fr-id FR-01] [--no-git]
python harness_cli.py finalize-gate     --gate 2 --phase 3 [--project .] [--fr-id FR-01] [--no-git]
python harness_cli.py generate-next-plan [--project .] [--phase N]
python harness_cli.py run-pipeline      [--phase-from 1] [--phase-to 8] [--project .] [--force] [--no-git]
python harness_cli.py push-checkpoint   --phase 1|2 [--project .] [--fr-ids FR-01,FR-02] [--no-git]
python harness_cli.py manifest          --fr-ids FR-01 FR-02 [--sad SAD.md] [--no-git]
python harness_cli.py status            [--project .]
python harness_cli.py effort            [--phase 3] [--project .]
python harness_cli.py reload-policy     [--policy-file enforcement/enforcement.json]
```

**Gate evaluation (two-phase)**: `run-gate` prepares context and prints evaluation instructions; Claude evaluates inline and writes `.sessi-work/gate{N}_result.json`; `finalize-gate` reads the result and checks thresholds. SSI assets are embedded in `harness/ssi/`.

**`run-pipeline`** exit codes:
- `0` — all phases complete
- `1` — hard error (SSI unavailable, manifest missing)
- `10` — PAUSE: human intervention needed; resume with `--phase-from N`

**P3+ dynamic planning**: `run-pipeline` generates each phase plan dynamically at phase start. Phases P3+ read FR IDs from `quality_manifest.json` (written at P2 exit from SAD.md), so SAD.md must exist before the pipeline can plan any FR-level work.

**Gate BLOCKED diagnostic** (`run-gate` exit 1 / `run-pipeline` exit 10): Both commands emit a structured per-dimension diagnosis on block. Output includes: composite score, open_critical/high counts, per-failing-dimension score/threshold/gap and a fix hint, passing dimension summary, and copy-pasteable resume commands. Full report written to `.methodology/last_block.md`. Fix hints cover all 12 dimension names: `linting`, `type_safety`, `test_coverage`, `security`, `secrets_scanning`, `license_compliance`, `mutation_testing`, `architecture`, `readability`, `error_handling`, `documentation`, `performance`. Implemented in `_format_block_diagnostic()` (module-level helper in `harness_cli.py`); the dict `_DIMENSION_HINTS` maps dimension name → actionable fix string.

**ECC hooks (globally active)**: `~/.claude/hooks/hooks.json` runs ECC (everything-claude-code) hooks across all Claude Code sessions. Relevant to harness:
- `pre:bash:dispatcher` — blocks `git --no-verify` (prevents HR violation from bypassing hooks), push reminders
- `pre:edit-write:suggest-compact` — suggests compaction when context nears limit (prevents gate score drift from truncated context)
- `stop:cost-tracker` — tracks token/cost per session
These hooks operate at the Claude Code session layer, independently of the harness Python pipeline. They are **pre-installed** and require no harness-side configuration.

**Agent A TDD mandate** (SKILL.md §6): Agent A must follow RED→GREEN→IMPROVE before returning results. Gate 1 `test_coverage` dimension verifies outcomes; implementations without prior failing tests are expected to score lower on `mutation_testing`.

---

### 2.4 GitHub Integration & Automation Layer

Full integration guide: **[INTEGRATION.md](INTEGRATION.md)**. Summary:

| Mechanism | File | Context | Purpose |
|---|---|---|---|
| **GitHub Actions CI** | `.github/workflows/harness_ci.yml` | This repo (framework self-test) | Mutation testing (median-3, threshold ≥70) + `pytest tests/` on push/PR to `main` |
| **Git Hooks installer** | `scripts/setup-git-hooks.sh` | Target project | Installs `prepare-commit-msg` (block commit), `post-merge` (warn), `pre-push` (block push) keyed on `git config quality.phase` |
| **Drift Monitor cron** | `scripts/cron_drift_monitor.py` | Target project (crontab) | Hourly architecture drift detection; alert via log / email / Slack. Path via `DRIFT_PROJECT_PATH` env var |
| **On-demand scripts** | `scripts/*.py` | Target project | FR audit, phase audit, spec compliance, FR mapping — see INTEGRATION.md §3.4 |

**Key rule**: `setup-git-hooks.sh` must run inside the target project (not inside this repo). Hooks call `quality_gate.cli` — the `quality_gate/` module must be importable from the target project root (submodule, pip, or copy).

---

## 3. Detailed Module Design

### 3.1 `harness/harness_bridge.py` — Gate Controller & Bridge

**Responsibility**: Manages quality gate lifecycle. Core bridge between the methodology workflow and the embedded SSI evaluation skill.

**Architectural note**: SSI is a Claude Code skill — Claude IS the evaluation engine. Gates use a two-phase API instead of subprocess IPC. SSI assets are embedded in `harness/ssi/`.

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

@dataclass
class GateContext:
    gate_num: int; config: dict; project_root: str; phase: int; fr_id: str | None
    ssi_scripts_dir: str; ssi_prompts_dir: str; ssi_schemas_dir: str; work_dir: str
    def evaluation_prompt(self) -> str: ...  # Returns evaluation instructions for Claude

class GateBlockedError(Exception):
    def __init__(self, gate_num: int, result: GateResult): ...
    # message: "Gate {n} BLOCKED — score={:.1f}, critical={c}, high={h}"
```

**Public API** (two-phase gate evaluation):

```python
class HarnessBridge:
    def __init__(self):
        self.crg = CRGBridge()       # graceful degradation if unavailable
        self._log = DecisionLogWriter()
        self._effort = EffortTracker()

    def prepare_gate(
        self,
        gate_num: int,
        project_root: str,
        phase: int,
        fr_id: str | None = None,
    ) -> GateContext:
        """Phase 1: load config, CRG recon, return context for Claude evaluation."""

    def finalize_gate(self, ctx: GateContext) -> GateResult:
        """Phase 2: read gate{N}_result.json, check thresholds, update manifest."""

    def run_gate(self, ...) -> GateResult:
        """DEPRECATED — raises NotImplementedError. Use prepare_gate + finalize_gate."""

    def generate_quality_manifest(self, fr_ids: list[str], sad_path: str) -> Path: ...
```

**`prepare_gate` execution order**:
1. `_load_config(gate_num)` — loads `harness/gate_configs/gate{n}_*.yaml` via PyYAML
2. If `config["crg"]["reconnaissance"]` is set → `self.crg.run_reconnaissance(project_root)`
3. Resolves `ssi_dir = Path(__file__).parent / "ssi"` (embedded assets)
4. Creates `.sessi-work/` work directory
5. Returns `GateContext` with all paths and config

**`finalize_gate` execution order**:
1. Reads `.sessi-work/gate{N}_result.json` (raises `FileNotFoundError` if missing)
2. Parses JSON into `GateResult` (accepts both `overall_score`/`score` and `open_critical_count`/`open_critical` field names)
3. `self._update_quality_manifest(gate_num, fr_id, result)` — writes to `.methodology/quality_manifest.json`
4. `self._effort.record(EffortRecord(phase, gate_num, "GATE", "gate_finalize", ...))`
5. `self._log.write(DecisionLogEntry(... decision="GATE_PASS"|"GATE_BLOCK" ...))`
6. **Blocking logic**:
   - Gate 1: `raise GateBlockedError` if any `d.score < d.threshold` in `result.dimensions`
   - Gate 2/3/4: `raise GateBlockedError` if `result.score < config["score_gate"]` OR `not result.quality_complete`
7. Gate 4 only: `_require_hermes_approve(result, phase, fr_id)` — Hermes reviewer must APPROVE
8. Return `GateResult`

**Result file contract** (`.sessi-work/gate{N}_result.json`):
```json
{
  "overall_score": 85.5,
  "meets_target": true,
  "quality_complete": true,
  "open_critical_count": 0,
  "open_high_count": 0,
  "breakdown": {
    "linting": {"score": 92.0, "threshold": 90.0, "passed": true, "issues": []}
  }
}
```
Schema: `harness/ssi/schemas/harness_gate_result.schema.json`

**`_require_hermes_approve(result, phase, fr_id)`** (Gate 4 only):
- Instantiates `ReviewerRouter()` — silently skips if `HERMES_REVIEWER_TARGET` not set
- Sends gate score + dimension summary for human review via Hermes
- `review_status != "APPROVE"` → logs `REVIEWER_REJECT` + raises `GateBlockedError(4, result)`

**`generate_quality_manifest` logic**:
- Called at P2 exit
- Calls `from scripts.generate_sab import parse_sad` — functional (added in fix ②+⑤)
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

### 3.2 `harness/reviewer_router.py` — Reviewer Proxy (v2.1)

**Responsibility**: Routes review requests through a **priority-ordered chain** of backends
(Hermes MCP → Gemini CLI MCP → sub-agent). Supports **dependency-ordered decomposition** of
large/complex tasks with **sequential A/B execution** (one subtask completes full A/B chain
before the next starts) and graceful degradation with full audit trail.

**Module-level constants** (all overridable via env vars):

| Constant | Env Var | Default | Purpose |
|---|---|---|---|
| `HERMES_TARGET` | `HERMES_REVIEWER_TARGET` | `""` | Hermes channel (e.g. `telegram:6308981865`) |
| `HERMES_TIMEOUT_MS` | `HERMES_TIMEOUT_MS` | `90000` | Hermes wait timeout (ms) — per CLAUDE.md protocol |
| `GEMINI_TIMEOUT_MS` | `GEMINI_TIMEOUT_MS` | `60000` | Gemini CLI MCP timeout (ms) |
| `TASK_SIZE_THRESHOLD` | `TASK_SIZE_THRESHOLD` | `2000` | Chars above which task is auto-decomposed |
| `SUBTASK_MAX_SIZE` | `SUBTASK_MAX_SIZE` | `800` | Target chars per subtask (paragraph fallback) |
| `MAX_CONTEXT_LINES` | `MAX_CONTEXT_LINES` | `6` | Approved-subtask summaries injected as context |
| `REVIEWER_CHAIN_CONFIG` | `REVIEWER_CHAIN` | `"hermes,gemini"` | Priority-ordered chain; `subagent` always appended |

```python
@dataclass
class SubTask:
    content: str          # subtask text
    label: str            # e.g. "Phase 3", "FR-01", "§3.2", "para_1"
    dependencies: list[str] = field(default_factory=list)  # other labels this depends on
    index: int = 1        # 1-based position in execution order
    total: int = 1        # total subtask count

def get_reviewer_model(phase: int, role: str = "reviewer") -> str:
    """Returns 'claude' if phase in {7, 8}, else REVIEWER_POLICY[role] (defaults to 'hermes')."""
    return "claude" if phase in _CLAUDE_PHASES else REVIEWER_POLICY.get(role, "hermes")
```

> **Note**: P7/P8 phase routing to Claude is enforced at the caller level — `agent_spawner.spawn()`
> calls `get_reviewer_model(phase, role)` before dispatching to `ReviewerRouter` (see §3.7).

**Public API**:

```python
class ReviewerRouter:
    def __init__(self, target: str = HERMES_TARGET, chain_config: str = REVIEWER_CHAIN_CONFIG):
        # target: Hermes channel (empty string OK if hermes not in chain)
        # chain_config: "hermes,gemini" | "hermes" | "gemini" (subagent always appended)

    def review(
        self,
        role: str,
        prompt: str,
        phase: int,
        fr_id: str | None = None,
        timeout_ms: int | None = None,
    ) -> dict:
        # Returns: {"review_status": "APPROVE|REJECT", "confidence": 0-1,
        #           "violations": [], "summary": "",
        #           "_reviewer_used": "hermes|gemini|subagent",
        #           "_degraded": bool,         # True if primary(s) timed out
        #           "_degradation": list,      # [{reviewer, reason}, ...]
        #           "_degradation_note": str,  # human-readable degradation summary
        #           "_stopped_at": str,        # label of REJECT subtask (multi-subtask only)
        #           "_completed_subtasks": int,
        #           "_total_subtasks": int}
```

**Priority chain execution** (`_try_chain`):
```
For each ReviewerSpec in chain (hermes → gemini → subagent):
  1. hermes: send → events_wait(90s) → messages_read → parse
             TimeoutError on: events_wait fail | messages_read "Session database unavailable" | no msgs
  2. gemini: mcp__gemini_cli__ask_gemini → _clean_gemini_response() → parse
             RuntimeError on: import fail | API error
  3. subagent: AgentSpawner.spawn(model="claude") — always succeeds (lazy import, no circular)
               Returns _degraded=True if any previous step failed

On timeout/error: log to _degradation, try next in chain.
```

**Task decomposition** (`_decompose_with_deps`):
```
IF len(prompt) <= TASK_SIZE_THRESHOLD (2000 chars):
    → no decomposition, returns [SubTask(prompt, label="full")]

Detection pipeline (tried in order):
  1. _extract_phase_sections(): regex "Phase N" / "PX" headers (SRS.md style)
  2. _extract_fr_sections(): FR-XXX boundary split
  3. _extract_heading_sections(): §X.Y / "X.Y Title" numbered headings (SAD.md style)
  4. _paragraph_subtasks(): fallback — paragraph split targeting SUBTASK_MAX_SIZE (800 chars)
                            sequential deps: para_N depends on para_(N-1)

Dependency graph (_build_dep_graph):
  - Cross-reference scan: if label B appears in label A's content → A depends on B
  - Implicit phase ordering: Phase-N → depends on Phase-(N-1)

Execution order (_topological_sort):
  - Kahn's algorithm (BFS on in-degree=0 nodes)
  - Cycle-safe: remaining nodes appended in original order if cycle detected
  - Result: list[SubTask] in dependency-safe execution order with index/total set
```

**`review()` execution sequence** (v2.1 — sequential A/B with context accumulation):
```python
subtasks = self._decompose_with_deps(prompt, role)  # topologically sorted
approved_context: list[str] = []                     # grows as subtasks APPROVE

for subtask in subtasks:
    enriched = self._enrich_with_context(subtask, approved_context)
    # enriched = subtask.content + injected last MAX_CONTEXT_LINES approved summaries

    result = self._try_chain(role, enriched, phase, fr_id, timeout_ms,
                             task_idx=subtask.index, task_total=subtask.total)
    # ↑ Full A/B chain (hermes → gemini → subagent) runs to completion for THIS subtask
    #   before moving to the next subtask

    if result.get("review_status") == "REJECT":
        result["_stopped_at"] = subtask.label       # which subtask caused REJECT
        result["_completed_subtasks"] = len(results)
        result["_total_subtasks"] = subtask.total
        return self._merge_results(results)          # early exit — no further subtasks

    if result.get("summary"):
        approved_context.append(f"✅ [{subtask.label}] {result['summary']}")
        # last MAX_CONTEXT_LINES (6) injected into next subtask

return self._merge_results(results)
```

**`_build_prompt(role, prompt, phase, fr_id=None, task_idx=1, task_total=1) -> str`**:
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
- `SSI_ROOT` env var (default: `harness/ssi` — the embedded directory) — used as `cwd` for CRG subprocess calls via `_ssi_root()`.
- Priority: `SSI_ROOT` env var → embedded `harness/ssi/` (set by `Path(__file__).parent / "ssi"`).

**Graceful degradation**: If `is_available()` is `False`, all methods return `{}` or `False` immediately.

**CRG integration points** (§6.5):
1. **Point 1 — Structural Reconnaissance** (Gate 3/4 entry): `run_reconnaissance` — 9 CRG queries, seeds issue registry, ~3,900 tokens, once per session.
2. **Point 2 — Tier 3 Guidance** (before each Tier 3 eval): `get_minimal_context` — reduces Tier 3 eval tokens 30–50%.
3. **Point 3 — Pre-fix Safety Gate** (before each improvement round): `check_impact` — defers fix if risky.
4. **Point 4 — Post-round Drift Check** (after each improvement round): `check_drift` — triggers revert protocol if structural drift > threshold.

---

### 3.4 `harness/decision_log.py` — Decision Audit Log Writer

**Responsibility**: Write per-decision YAML audit entries. Implements the traceability requirement (NFR-3/NFR-6).

**Data structures** (refactored in `quality-improvement-round-3`):

```python
@dataclass
class DecisionContext:
    """Groups identity/time metadata to reduce DecisionLogEntry parameter count."""
    agent_id: str
    phase: int
    fr_id: str | None = None
    trace_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

@dataclass
class DecisionLogEntry:
    ctx: DecisionContext
    decision: str           # "APPROVE" | "REJECT" | "GATE_PASS" | "GATE_BLOCK" | "REVIEWER_REJECT"
    reasoning: str
    scores: dict[str, float] = field(default_factory=dict)  # e.g. {"gate_score": 87.5}
    metadata: dict = field(default_factory=dict)

    # Backward-compat properties
    @property
    def agent_id(self): return self.ctx.agent_id
    @property
    def phase(self): return self.ctx.phase
```

**`DecisionLogWriter`**:

```python
class DecisionLogWriter:
    def __init__(self, log_root: str = ".methodology/decision_logs"):
        self.log_root = Path(log_root)

    def write(self, entry: DecisionLogEntry) -> Path:
        # Output: log_root/{YYYY-MM-DD}/{agent_id}_{phase}_{seq:03d}.yaml
        # seq = count of matching files in today's dir + 1

    def read_phase(self, phase: int) -> list[dict]:
        # Returns all YAML entries matching *_{phase}_*.yaml (recursive glob)
```

- Creates parent directories automatically.
- YAML serialized via `dataclasses.asdict(entry)` → nested: `ctx: {agent_id, phase, fr_id, trace_id, timestamp}`.
- Callers access phase/agent from deserialized dict as `data["ctx"]["phase"]` (not flat `data["phase"]`).
- Falls back to `json.dumps` if PyYAML not installed.

---

### 3.5 `harness/effort_tracker.py` — Gate Effort Metrics

**Responsibility**: SQLite-backed gate effort tracking for performance monitoring.

**Data structures**:

```python
@dataclass
class EffortRecord:
    phase: int
    agent_id: str
    operation: str          # "gate_run" | "tier1_eval" | "tier3_eval" | "fix_round" | "review"
    duration_s: float
    gate_num: int | None = None
    token_in: int = 0
    token_out: int = 0
    fr_id: str | None = None
    # Note: created_at is NOT a dataclass field — managed by SQLite DEFAULT (datetime('now'))
```

**`EffortTracker`**:

```python
class EffortTracker:
    def __init__(self, db_path: str = ".methodology/effort_metrics.db"):
        # Auto-creates DB and schema on init

    def record(self, r: EffortRecord) -> None:
        # INSERT into SQLite table `effort` (not `effort_records`)

    def summary(self, phase: int | None = None) -> dict:
        # Returns: {total_operations, total_duration_s, total_tokens}
        # If phase given: delegates to query_phase_summary(phase)

    def query_phase_summary(self, phase: int) -> dict:
        # Per-operation breakdown: {operation: {duration_s, total_tokens}}

    def query_gate_summary(self, gate_num: int) -> dict:
        # {runs, total_duration_s, total_tokens}
```

- DB auto-created on `__init__` (not on first `record()`).
- SQLite table name: **`effort`** (not `effort_records`).
- Schema: `(id, phase, gate_num, agent_id, operation, duration_s, token_in, token_out, fr_id, created_at)`

---

### 3.6 `harness/issue_tracker_ext.py` — FR-Tagged Issue Tracker

**Responsibility**: Extends `software_self_improvement`'s `IssueTracker` with per-FR tagging. Addresses Gap G5. Refactored in `quality-improvement-round-3` to use `FindingData` parameter object.

**Import guard** (inline stub if SSI not installed):
```python
try:
    from software_self_improvement.scripts.issue_tracker import IssueTracker
except ImportError:
    class IssueTracker:  # minimal stub
        def __init__(self): self._issues: list[dict] = []
        def add_finding(self, dimension, severity, file, line, message, evidence) -> str: ...
        def open_issues(self) -> list[dict]: ...
```

**`FindingData` (parameter object, reduces `add_finding` parameter count)**:
```python
@dataclass
class FindingData:
    dimension: str
    severity: str
    file: str
    line: int
    message: str
    evidence: str
    fr_id: str | None = None
```

**`IssueTrackerExt(IssueTracker)`**:

```python
def add_finding_data(self, data: FindingData) -> str:
    # Primary method — calls super().add_finding(), then tags issue with data.fr_id

def add_finding(
    self, dimension, severity, file, line, message, evidence,
    fr_id: str | None = None,
) -> str:
    # Legacy compatibility wrapper — delegates to add_finding_data(FindingData(...))

def get_findings_by_fr(self, fr_id: str) -> list[dict]:
    # Returns open issues where fr_id in issue["fr_ids"]

def fr_coverage_summary(self) -> dict[str, int]:
    # Returns {fr_id: open_finding_count} — only includes FRs that HAVE open findings
    # (FRs with zero findings are absent from the result dict)
    # No `fr_ids` argument — auto-aggregates from all open issues
```

> **Removed in `quality-improvement-round-3`**: `fr_saturation_check(fr_id, current_finding_ids, threshold)` — removed entirely. Tests that relied on it are skipped.

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

### 3.10 `core/subagent_isolator.py` — Need-to-Know Isolation

**Responsibility**: Enforces On-Demand / Need-to-Know isolation for subagent spawning. Each subagent receives a fresh message context with only the artifacts relevant to its task.

**Key classes**:

```python
@dataclass
class ArtifactSpec:
    path: str
    role: str          # "input" | "output" | "reference"
    required: bool = True
    description: str = ""

@dataclass
class SubagentContext:
    task: str
    role: str
    artifacts: List[ArtifactSpec]
    persona_prompt: str
    metadata: Dict[str, Any]
    messages: List[Dict]    # Always fresh (enforced empty at creation)
    isolation_id: str       # SHA-256[:16] of {task, role}
```

**`SubagentIsolator`**:

```python
class SubagentIsolator:
    def create_context(task, role, artifacts, persona_prompt, metadata) -> SubagentContext:
        # messages=[] enforced — no prior conversation history

    def validate(ctx) -> None:
        # Raises ArtifactValidationError if any required input artifact missing

    def validate_outputs(ctx) -> dict:
        # Returns {complete, produced, missing} after subagent completes

    def verify_isolation(ctx) -> None:
        # Raises IsolationViolationError if messages[] is non-empty

    def spawn(task, role, artifacts, persona_prompt, metadata, validate=True) -> dict:
        # High-level: create_context → validate → return to_spawn_config()

    def release(isolation_id) -> None:
        # Free context reference after subagent completes
```

**Convenience factory**:
```python
def create_isolated_spawn(task, role, input_paths, output_paths, persona_prompt) -> dict:
    # One-shot: build + validate + return spawn config
```

---

### 3.11 `core/verification_gate.py` — Task Verification Gates

**Responsibility**: Generic verification gate manager for task lifecycle state tracking. Distinct from quality gates (§3.1) — these track agent task state (created → assigned → generated → approved → completed). Also contains `GateRemediationReport` (structured gate-failure diagnosis for HANDOVER.md).

**Key classes**:

```python
class GateStatus(Enum):
    NOT_REACHED = "not_reached"
    PASSED = "passed"
    FAILED = "failed"
    BYPASSED = "bypassed"

class Gate:
    def __init__(name, required_output=None, validator=None, auto_pass=False): ...
    def check(context: dict) -> bool: ...
    def bypass(reason: str = None): ...
    def reset(): ...

class VerificationGates:
    DEFAULT_GATES = {
        "task_created", "agent_assigned", "output_generated",
        "quality_check", "human_approved", "completed"
    }
    def register_gate(gate_id, gate): ...
    def execute_sequence(context) -> dict: ...
    def get_status() -> dict: ...
    def get_passed_count() -> int: ...
    def reset_all(): ...

class HITLGates(VerificationGates):
    # Sequence: task_created → output_generated → human_approved → completed

class AutonomousGates(VerificationGates):
    # Sequence: task_created → agent_assigned → output_generated → quality_check → completed
```

**Module-level constants** (gate remediation support):

```python
_GATE_THRESHOLDS: Dict[int, float] = {1: 70.0, 2: 75.0, 3: 80.0, 4: 85.0}
_GATE_ACTION_TEMPLATES: Dict[int, List[str]]  # per-gate ordered fix hints (Gates 1–4)
```

**`GateRemediationReport`** (`@dataclass`):

```python
@dataclass
class GateRemediationReport:
    gate_num: int
    phase: int
    score: float
    threshold: Optional[float] = None       # None → uses _GATE_THRESHOLDS[gate_num]
    failing_checks: List[str] = []          # e.g. ["D3_Coverage", "D5_Security"]
    gate_evidence: Optional[Dict] = None    # raw Gate.evidence dict

    @property
    def effective_threshold(self) -> float: ...   # override or default
    @property
    def gap(self) -> float: ...                   # max(0, threshold - score)
    def action_items(self) -> List[str]: ...      # failing_checks items first, then templates
    def to_status_string(self) -> str: ...        # "Gate N FAILED: score=X (threshold=Y, gap=Z)..."
    def to_dict(self) -> Dict[str, Any]: ...      # JSON-serialisable; used by HandoverGenerator
```

`action_items()` ordering: each `failing_checks` entry prepends `"Fix failing check: **{check}** (score=X)"` before the generic `_GATE_ACTION_TEMPLATES[gate_num]` list. For unknown gate_num, falls back to `["Investigate Gate N failure...", "Re-run gate after fixing..."]`.

---

### 3.12 `steering/` — AB Workflow Steering Engine

**Responsibility**: LLM-as-judge A/B iteration control. Drives the AB Workflow component referenced by the full-system `cli.py`. Used when two candidate outputs must be compared and converged toward a winner.

#### `steering/steering_loop.py` — Core Iteration Engine

**Key classes**:

```python
class IterationStage(Enum):
    EXPLORATION = "exploration"   # first N rounds, free competition
    COMPETITION = "competition"   # middle rounds, score differences emerge
    CONVERGENCE = "convergence"   # final rounds, converging to winner

@dataclass
class SteeringConfig:
    max_iterations: int = 5
    min_iterations: int = 3
    exploration_rounds: int = 2
    convergence_threshold: float = 0.05   # delta < this = converged
    quality_threshold: float = 0.85
    weights: Dict[str, float] = {
        "quality": 0.4, "efficiency": 0.2, "clarity": 0.2, "consistency": 0.2
    }

class LLMJudgeScorer:
    def score(output_a, output_b) -> {"A": {scores}, "B": {scores}}:
        # Dimensions: correctness, completeness, consistency, concision, maintainability
        # Fallback: pessimistic 0.5 across all dims on parse failure

    def generate_feedback(output_a, output_b, scores_a, scores_b, winner) -> dict:
        # Returns: {winner_advantages, loser_improvements, actionable_guidance}
```

**`SteeringLoop(provider, config, history_path)`**:

```python
def iterate(output_a, output_b) -> IterationResult:
    # 1. Update stage (EXPLORATION → COMPETITION → CONVERGENCE)
    # 2. LLM-judge score both outputs
    # 3. Compute weighted total (quality*0.4 + clarity*0.2 + consistency*0.2)
    # 4. Determine winner, update best_output
    # 5. Generate feedback
    # 6. Compute convergence score (avg delta of last 3 rounds)
    # 7. Persist history to .methodology/steering_history.json
    # → Returns IterationResult

def should_continue() -> (bool, str):
    # Returns False if: max_iterations reached, quality_threshold met,
    # or CONVERGENCE stage + delta <= convergence_threshold
    # Resolves HR-12 conflict: "no >5 meaningless iterations" via early stop

def run_until_converge(get_next_pair_fn, max_rounds=None) -> IterationResult:
    # Drives iteration loop until should_continue() is False
```

**Three defects fixed in current implementation**:
- Defect A: Scoring was fake (hard-coded 0.5) → replaced with LLM-as-judge
- Defect B: Efficiency logic inverted → fixed to quality/tokens ratio
- Defect C: Convergence logic inverted → delta < threshold means converged (not diverged)

#### `steering/integrations.py` — Integration Adapters

**Responsibility**: Connects `SteeringLoop` to BVS, Constitution, CQG, and HR-12 enforcement systems.

| Class | Integrates With | Key Method |
|---|---|---|
| `SteeringBVSIntegrator` | BVS Runner (HR-03 phase invariants) | `check_phase_invariants(steering_result, context)` |
| `SteeringConstitutionIntegrator` | Constitution Checker (HR-07/09/15) | `check_output_compliance(output, phase)` |
| `SteeringCQGIntegrator` | CQG code quality checker | `measure_code_quality(output) -> {quality, complexity, readability}` |
| `HR12Resolution` | HR-12 conflict resolver | `should_stop(current_round, score_delta) -> (bool, reason)` |
| `SteeringIntegrator` | Unified facade | `iterate_with_full_check(output_a, output_b) -> (IterationResult, [IntegrationResult])` |

**Import behavior**: Constitution module imports (`from constitution.bvs_runner`, `from constitution.citation_parser`, `from constitution.verification_constitution_checker`) are lazy-loaded inside try/except blocks. `constitution/` is not present in this repo as a top-level package — these calls gracefully degrade to warnings when the full-system constitution module is absent.

**HR-12 Resolution**:
- HR-12 says "no >5 rounds of ineffective iteration" (negative constraint)
- `SteeringLoop.max_iterations=5` is positive upper bound
- Not contradictory: `should_continue()` terminates early when convergence met, satisfying HR-12 intent without contradicting the cap

#### `steering/__init__.py` — Package Re-exporter

Re-exports all public classes from `steering_loop` and `integrations` so callers can use `from steering import SteeringLoop, SteeringIntegrator` without knowing the submodule layout.

```python
__all__ = [
    "SteeringLoop", "SteeringConfig", "IterationStage",
    "ScoredOutput", "IterationResult", "LLMJudgeScorer",      # from steering_loop
    "SteeringBVSIntegrator", "SteeringConstitutionIntegrator",  # from integrations
    "SteeringCQGIntegrator", "SteeringIntegrator", "HR12Resolution",
]
```

---

### 3.13 `kill_switch/` — Agent Safety Kill Switch

**Responsibility**: Circuit-breaker safety system. Monitors agent health, trips circuit on threshold violation, issues interrupt events, and logs all actions for audit.

**Module structure** (10 files, all relative imports — self-contained):

| File | Class/Purpose |
|---|---|
| `kill_switch.py` | `KillSwitch` — main facade |
| `circuit_breaker.py` | `CircuitBreaker` — trip/reset logic |
| `health_monitor.py` | `HealthMonitor` — per-agent metric collection |
| `interrupt_engine.py` | `InterruptEngine` — interrupt event lifecycle |
| `state_manager.py` | `StateManager` — persistent agent state (killed/active) |
| `audit_logger.py` | `AuditLogger` — interrupt audit trail |
| `models.py` | `InterruptEvent`, `MonitorConfig`, `HealthMetrics`, `CircuitBreakerState` — dataclasses |
| `enums.py` | `CircuitState`, `KillReason`, `KillSwitchEventType`, `InterruptOutcome` — enumerations |
| `exceptions.py` | `InterruptInProgressError` |
| `__init__.py` | Package exports |

**`KillSwitch` public API**:

```python
class KillSwitch:
    def __init__(self, audit_logger=None, state_manager=None):
        # Composes: HealthMonitor, CircuitBreaker, StateManager, InterruptEngine

    def start_monitoring(agent_id: str, config: MonitorConfig = None) -> None:
        # Starts health monitoring + initializes circuit for agent

    def stop_monitoring(agent_id: str) -> None

    def is_agent_circuit_open(agent_id: str) -> bool:
        # True if state_manager.is_agent_killed OR circuit_breaker.is_open

    def get_agent_state(agent_id: str) -> CircuitState

    def manual_trigger(agent_id, reason, operator_id) -> InterruptEvent:
        # Immediate human-initiated kill

    def evaluate_and_trigger(agent_id, config: MonitorConfig) -> bool:
        # Auto-evaluation: check metrics → record failure → open circuit if threshold exceeded
        # Returns True if interrupt was triggered

    def re_enable(agent_id, operator_id, acknowledgment) -> bool:
        # Clear killed state, reset circuit, stop monitoring
        # Returns True if successfully re-enabled (or already active)

    def get_interrupt_history(agent_id=None, limit=100) -> List[InterruptEvent]
```

**`MonitorConfig` fields** (from `kill_switch/models.py`):
- `agent_id: str`
- `failure_threshold: int` — consecutive failures before circuit trips
- `cooldown_seconds: int` — circuit open duration before half-open retry

---

### 3.14 `enforcement/` — Policy Enforcement Framework

**Responsibility**: Enforces behavioral policies on agents and commits. 7 files covering hook installation, constitution-as-code enforcement, policy evaluation, and server-side enforcement.

| File | Class | Purpose |
|---|---|---|
| `agent_proof_hook.py` | `AgentProofHook` | Git pre-commit hook that agents cannot bypass; validates commit message task ID format |
| `constitution_as_code.py` | `ConstitutionAsCode` | Constitution rules expressed as executable Python checks |
| `constitution_policy_sync.py` | `ConstitutionPolicyGenerator` | Synchronizes policy definitions with constitution document |
| `execution_registry.py` | `ExecutionRegistry` | Registry tracking all executed enforcement actions |
| `framework_enforcer.py` | `FrameworkEnforcer` | Main enforcement orchestrator; coordinates all enforcement subsystems |
| `policy_engine.py` | `PolicyEngine` | Evaluates named policies against runtime context |
| `server_enforcer.py` | `ServerEnforcer` | Server-side enforcement for remote agent actions |

**`AgentProofHook` key behavior**:
- Installs to `.git/hooks/pre-commit` (thin wrapper) + `.methodology/agent_hook_core.py` (core logic)
- Core logic: validates commit messages contain `[TASK-123]` pattern; detects `--no-verify` bypass attempts
- Attempts Unix immutable attribute (`chattr +i`) on hook file
- Commands: `install`, `verify`, `uninstall`

---

### 3.15 `quality_dashboard/` — Quality Research & Dashboard

**Responsibility**: Auto-research loop and quality metrics dashboard. Used by the full-system CLI to provide ongoing quality monitoring.

| File | Class | Purpose |
|---|---|---|
| `dashboard.py` (24KB) | `QualityDashboard` | Main dashboard aggregating all quality metrics |
| `agent_auto_research.py` (34KB) | `AgentDrivenAutoResearch` | AI-driven automated research and quality investigation |
| `auto_research_loop.py` (17KB) | `AutoResearchLoop` | Orchestrates iterative research rounds |

**Relationship to Gate 2**: `gate2_p3_exit.yaml` declares `replaces: auto_research_p3` — the Gate 2 automated evaluation replaces what was previously the `auto_research_p3` component from this dashboard.

---

### 3.16 `core/quality_gate/` — Quality Gate Implementations

**Responsibility**: Concrete quality check implementations used by the gate evaluation pipeline. Subdirectory of `core/`.

**Business logic modules:**

| File | Class | Purpose |
|---|---|---|
| `ab_enforcer.py` | `ABEnforcer` | A/B enforcement; HR-12 compliance checking. Delegates parsing to `parsers.DevelopmentLogParser` |
| `phase_truth_verifier.py` | `PhaseTruthVerifier` | Verifies phase completion truth via sessions_spawn.log, pytest, coverage, framework BLOCK |
| `spec_tracking_checker.py` | `SpecTrackingChecker` | Tracks SPEC_TRACKING.md completeness. Delegates parsing to `parsers.SpecTrackingParser` |
| `stage_pass_generator.py` | `IntegratedStagePassGenerator` | Generates stage pass certificates; integrates FrameworkEnforcer + ClaimsVerifier |
| `feedback_hook.py` | `AutoQualityGateWithFeedback` | AutoQualityGate subclass that submits feedback on gate completion |
| `constitution/__init__.py` | — | Constitution sub-package (used by `preflight_constitution`) |

**Support modules (stubs/config — crg-003 additions):**

| File | Purpose |
|---|---|
| `claims_verifier.py` | `ClaimsVerifier` + `ClaimsVerifyResult` — verifies sessions_spawn.log A/B role claims |
| `phase_config.py` | `PHASE_CONFIG` dict — per-phase config consumed by `IntegratedStagePassGenerator` |
| `phase_paths.py` | `PHASE_ARTIFACT_PATHS` — artifact path registry per phase |

**`parsers/` sub-package** (crg-003 refactor — breaks coupling with test-parsing community):

| File | Class | Extracted from | Methods |
|---|---|---|---|
| `parsers/development_log_parser.py` | `DevelopmentLogParser` | `ab_enforcer.py` | `extract_phase_content`, `extract_session`, `normalize_session` |
| `parsers/spec_tracking_parser.py` | `SpecTrackingParser` | `spec_tracking_checker.py` | `has_table`, `has_update_log`, `find_entries_without_status`, `count_status` |

> **Design invariant**: All regex / Markdown parsing lives in `parsers/`. Checker classes contain only orchestration and business logic — zero `re.search()` calls.

---

### 3.17 `detection/` — Anomaly & Drift Detection

**Responsibility**: Detects code drift, scoring anomalies, and suspicious patterns across evaluation rounds.

| File | Class | Purpose |
|---|---|---|
| `drift_detector.py` | `DriftDetector` | Detects structural drift between evaluation rounds; feeds `CRGBridge.check_drift` |
| `ensemble_scorer.py` | `EnsembleScorer` | Combines multiple scoring signals into ensemble score |
| `pattern_matcher.py` | `PatternMatcher` | Pattern-based detection of known anti-patterns |

---

### 3.18 `gap_detector/` — Specification Gap Detection

**Responsibility**: Detects gaps between requirements specification and implementation. Used to surface uncovered FRs or missing artifacts.

| File | Class | Purpose |
|---|---|---|
| `detector.py` | `GapDetector` | Core gap identification logic |
| `parser.py` | `SpecParser` | Parses spec documents to extract expected coverage |
| `reporter.py` | `GapReporter` | Formats gap findings for reporting |
| `scanner.py` | `CodeScanner` | Scans codebase for coverage evidence |

### §3.21 — `scripts/check_spec_trace.py` — FR Spec Trace Validator

**Responsibility**: Validates that every FR-XXX ID found in SAD.md has a corresponding
test file in the target project's `tests/` directory. Called at P4 Gate 3 entry (before SSI runner)
to enforce 100% SPEC trace coverage. Implements the P3 TDD scaffolding contract — ensuring
tests written in P3 Step 0 are present before P4 quality evaluation begins.

**Usage** (called by `harness_bridge.run_gate(gate_num=3)` pre-flight):
```bash
python3 harness/scripts/check_spec_trace.py SAD.md tests/
# Exit 0: all FRs traced → Gate 3 proceeds
# Exit 1: untested FRs found → GateBlockedError raised before SSI runner
```

**Logic**:
- Extracts all `\bFR-\d+\b` IDs from SAD.md
- Scans `tests/test_fr_*.py` files for FR references (filename + content)
- Reports: `FRs: N | Tested: M | Untested: K`
- Exit 0 = Gate 3 may proceed | Exit 1 = Gate 3 blocked until `test_fr_XXX.py` files created

**Integration**: `harness_bridge.prepare_gate(gate_num=3)` triggers this check at Gate 3 entry.
Failure raises `GateBlockedError(3, ...)` listing untested FRs. The script is in `scripts/` alongside
`check_fr_full.py` and `verify_spec_compliance.py`.


---

## 4. Core Workflow Sequences

### 4.1 Gate Run (e.g. Gate 2, P3 exit) — Two-Phase Flow

```
Phase 1 — Prepare:
  Operator -> HarnessBridge.prepare_gate(gate_num=2, project_root, phase=3)
    │
    ├─ 1. _load_config(2) → reads harness/gate_configs/gate2_p3_exit.yaml
    ├─ 2. [gate2: no crg.reconnaissance configured — skipped]
    ├─ 3. ssi_dir = harness/ssi/  (embedded, no external repo needed)
    ├─ 4. work_dir = project_root/.sessi-work/  (created if absent)
    └─ 5. return GateContext(gate_num=2, config=..., ssi_scripts_dir=..., ...)
    [GateContext.evaluation_prompt() prints full evaluation instructions for Claude]

Phase 2 — Claude Evaluation (not a subprocess, Claude IS the engine):
  Claude reads harness/ssi/prompts/evaluate_dimension.md
  Claude uses harness/ssi/scripts/ for static analysis
  Claude writes project_root/.sessi-work/gate2_result.json

Phase 3 — Finalize:
  Operator -> HarnessBridge.finalize_gate(ctx)
    │
    ├─ 1. reads .sessi-work/gate2_result.json → GateResult
    │      [FileNotFoundError if Claude did not write result file]
    ├─ 2. _update_quality_manifest(2, None, result)
    ├─ 3. EffortTracker.record(...)
    ├─ 4. DecisionLogWriter.write(decision="GATE_PASS|GATE_BLOCK")
    ├─ 5. if result.score < 75 or not result.quality_complete → raise GateBlockedError
    │      [CLI layer catches GateBlockedError → _format_block_diagnostic() →
    │       structured stdout + writes .methodology/last_block.md]
    └─ 6. return GateResult
    [Gate 4 only: step 6 = _require_hermes_approve() before return]
```

### 4.2 A/B Review via Hermes MCP (v2.1 — Sequential + Dep-Ordered)

```
AgentSpawner.spawn(model="hermes", role="Reviewer", prompt, context, phase, fr_id)
  │
  └─ ReviewerRouter.review(role, full_prompt, phase, fr_id)
       │
       ├─ 1. _decompose_with_deps(prompt) → list[SubTask] (topologically sorted)
       │
       │      IF len(prompt) <= 2000 chars:
       │        → [SubTask(prompt, label="full")]   — no decomposition
       │
       │      Detection pipeline (tried in order):
       │        A. _extract_phase_sections(): "Phase N" / "PX" (SRS.md style)
       │        B. _extract_fr_sections(): FR-XXX boundaries
       │        C. _extract_heading_sections(): §X.Y / "X.Y Title" (SAD.md style)
       │        D. _paragraph_subtasks(): para-split ≤800 chars (sequential deps)
       │
       │      _build_dep_graph(): cross-ref scan + implicit Phase-N → Phase-(N-1)
       │      _topological_sort(): Kahn's BFS, cycle-safe
       │
       ├─ 2. Sequential execution (ONE subtask completes A/B chain → NEXT starts):
       │
       │      approved_context = []
       │      FOR subtask IN subtasks (dependency order):
       │        enriched = subtask.content + last 6 ✅ approved summaries
       │        │
       │        result = _try_chain(role, enriched, phase, fr_id) ◄── FULL A/B runs here
       │        │
       │        IF result == REJECT:
       │          result._stopped_at = subtask.label
       │          STOP → _merge_results(completed so far)    ← early exit
       │        ELSE:
       │          approved_context.append(f"✅ [{subtask.label}] {result['summary']}")
       │          CONTINUE to next subtask
       │
       │    _try_chain priority loop (runs to completion for each subtask):
       │    ├─ [P1] Hermes MCP (HERMES_TIMEOUT_MS = 90000ms):
       │    │    ├─ mcp__hermes__messages_send(target, enriched)
       │    │    ├─ mcp__hermes__events_wait(timeout_ms=90000)
       │    │    │    └─ Timeout/error → TimeoutError → try P2
       │    │    ├─ mcp__hermes__messages_read(limit=1)
       │    │    │    └─ "Session database unavailable" → TimeoutError → try P2
       │    │    └─ No msgs → TimeoutError → try P2
       │    │
       │    ├─ [P2] Gemini CLI MCP (GEMINI_TIMEOUT_MS = 60000ms):
       │    │    ├─ mcp__gemini_cli__ask_gemini(enriched, model="gemini-2.5-flash")
       │    │    ├─ _clean_gemini_response() → strip ECC hook contamination
       │    │    └─ RuntimeError on any failure → try P3
       │    │
       │    └─ [P3] Sub-agent (graceful degradation — always succeeds):
       │         ├─ AgentSpawner.spawn(model="claude", ...) — lazy import (no circular)
       │         ├─ result["_degraded"] = True
       │         └─ result["_degradation_note"] = "[DEGRADED] Fell back to sub-agent after: ..."
       │
       └─ 3. _merge_results([subtask_results]):
              ├─ Any REJECT → return REJECT (with _stopped_at, _completed_subtasks)
              ├─ confidence = min(all subtask confidences)
              ├─ violations = union(all violations)
              └─ summary = " | ".join(all summaries)

Result keys: review_status, confidence, violations, summary,
             _reviewer_used, _degraded, _degradation, _degradation_note,
             _stopped_at (REJECT only), _completed_subtasks, _total_subtasks
```

### 4.3 Quality Manifest Generation (P2 exit)

```
HarnessBridge.generate_quality_manifest(fr_ids=["FR-01","FR-02"], sad_path="SAD.md")
  │
  ├─ from scripts.generate_sab import parse_sad  ← functional (fix ②+⑤ applied)
  │  sab = parse_sad(sad_path)  → {nfr_dim_map, constraints, high_risk, ...}
  │
  ├─ manifest = {schema_version, generated_at_phase=2, fr_ids, nfr_dimension_mapping,
  │              architecture_constraints, high_risk_modules, gate_score_overrides={},
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

**Gate 1** — `gate1_per_fr.yaml` (per-FR at P3/P4/P5/P7/P8):
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
> **TDD semantic**: `test_coverage(80)` in Gate 1 requires that TDD test stubs for the FR were
> committed **before** implementation (P3 Step 0). Coverage is measured against FR-specific
> acceptance criteria, not just line coverage.

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
CREATE TABLE IF NOT EXISTS effort (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    phase      INTEGER,
    gate_num   INTEGER,
    agent_id   TEXT,
    operation  TEXT,
    duration_s REAL,
    token_in   INTEGER DEFAULT 0,
    token_out  INTEGER DEFAULT 0,
    fr_id      TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);
```

---

## 6. SAB Block (machine-readable, v1.2 — As-Built)

> Updated from v1.2. Added to Layer 1: `harness/handover_generator.py`, `harness/fr_progress_tracker.py`, `harness/retry_utils.py`.

<!-- SAB:START -->
```json
{
  "version": "1.2",
  "created_at": "2026-04-28",
  "project": "harness-methodology",
  "layers": [
    {
      "name": "0_Entrypoint_Facade",
      "description": "CLI entrypoints. harness_cli.py is the standalone harness entrypoint. cli.py is the full-system entrypoint (requires 30+ external modules, not runnable in this repo alone).",
      "modules": ["harness_cli.py", "cli_phase_subagent.py", "cli.py"],
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
        "harness/handover_generator.py",
        "harness/fr_progress_tracker.py",
        "harness/retry_utils.py",
        "harness/effort_tracker.py",
        "harness/issue_tracker_ext.py"
      ],
      "allowed_dependencies": ["2_Core_Orchestration", "3_Quality_Features", "4_Base_Utilities"]
    },
    {
      "name": "2_Core_Orchestration",
      "description": "Manages agent lifecycle, phase execution, workflow routing, task decomposition, AB steering, and session logging.",
      "modules": [
        "core/agent_spawner.py",
        "core/phase_hooks.py",
        "core/hybrid_workflow.py",
        "core/task_splitter.py",
        "core/sessions_spawn_logger.py",
        "core/subagent_isolator.py",
        "core/verification_gate.py",
        "steering/"
      ],
      "allowed_dependencies": ["3_Quality_Features", "4_Base_Utilities"]
    },
    {
      "name": "3_Quality_Features",
      "description": "Concrete quality check implementations, safety features, gap detection, enforcement hooks, and quality monitoring dashboard.",
      "modules": [
        "core/quality_gate/",
        "core/requirement_traceability.py",
        "detection/",
        "gap_detector/",
        "kill_switch/",
        "enforcement/",
        "quality_dashboard/"
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

### §3.19 — `constitution/` (HR Compliance Package)

**Purpose**: Real implementations of the HR-compliance interfaces imported by `steering/integrations.py`. Eliminates the graceful-degrade no-ops; `SteeringIntegrator` is now fully operational.

| File | Class | Role |
|---|---|---|
| `constitution/__init__.py` | — | Package marker; re-exports all three classes |
| `constitution/bvs_runner.py` | `BVSRunner` | HR-03 phase-order checker: reads `.methodology/state.json`, validates phase prerequisites and FSM state |
| `constitution/citation_parser.py` | `CitationParser` | HR-07/09: regex extraction of citation markers (`[FR-01]`, `[§3.2]`, etc.) and obligation-verb claims; `verify_claim()` checks traceability keywords |
| `constitution/verification_constitution_checker.py` | `VerificationConstitutionChecker` | Bridges `steering/integrations.py` to `enforcement.constitution_as_code` (R001-R007); gracefully degrades to pass-through if `enforcement/` unavailable |

**Imports**: stdlib only (`re`, `json`, `pathlib`). No external dependencies.  
**Integration**: `SteeringIntegrator.bvs_integrator` property and `iterate_with_full_check()` now call real code instead of hitting `ImportError`.


### §3.20 — `scripts/` Directory Overview

Full inventory of the `scripts/` directory (21 items). Grouped by role:

#### Phase Lifecycle & Planning

| Script | Size | Purpose |
|---|---|---|
| `phase_auditor.py` | 65KB | Deep-audit a completed phase: validates artifacts, gate results, FR coverage; produces ASPICE-grade Markdown report |
| `generate_full_plan.py` | 22KB | Generates a full FR-level execution plan for a phase from SAD.md; outputs `.methodology/phase{N}_plan.md` |
| `generate_fr_mapping.py` | 6KB | Builds an FR→file mapping from SAD.md and codebase scan; consumed by `phase_auditor.py` and gate pre-flight |
| `generate_sab.py` | 3KB | CLI wrapper around `sab_parser.extract_sab_from_sad`; also exposes `parse_sad()` called by `HarnessBridge.generate_quality_manifest()` |

**`phase_auditor.py` usage**:
```bash
python /path/to/harness/scripts/phase_auditor.py --phase 3 [--project .] [--report audit_p3.md]
```

**`generate_full_plan.py` usage** (called by `harness_cli.py plan-phase`):
```bash
python scripts/generate_full_plan.py --phase 3 --repo /path/to/project \
  --output .methodology/phase3_plan.md
```

#### FR Verification

| Script | Size | Purpose |
|---|---|---|
| `check_fr_full.py` | 7KB | Full bidirectional FR audit: SRS → SAD → code → test chain completeness |
| `check_fr_quality.py` | 4KB | FR-level quality scoring: docstring coverage, citation format, `[FR-XX]` tag presence |
| `check_spec_trace.py` | 3KB | Validates every FR-ID in SAD.md has a matching `tests/test_fr_*.py`; Gate 3 pre-flight |

#### Integration & Automation

| Script | Size | Purpose |
|---|---|---|
| `setup-git-hooks.sh` | 9KB | Installs `prepare-commit-msg` / `post-merge` / `pre-push` hooks in a **target project** |
| `cron_drift_monitor.py` | 2KB | Hourly drift detection cron; reads `DRIFT_PROJECT_PATH` env var; alerts via log/email/Slack |
| `cron_docs_optimizer.py` | 9KB | Scheduled docs quality optimizer; runs against stale documentation |
| `drift_crontab.example` | 780B | Example crontab configuration for `cron_drift_monitor.py` |
| `DRIFT_CRON_SETUP.md` | 2KB | Setup guide for drift cron monitoring |
| `harness-init.sh` | 5KB | Bootstrap script: creates `.methodology/` skeleton, copies config templates to a new target project |

#### Compliance & Diagnostics

| Script | Size | Purpose |
|---|---|---|
| `spec_logic_checker.py` | 10KB | Validates spec logic consistency (no contradictory requirements, all FRs have priorities) |
| `dev_log_checker.py` | 12KB | Validates `DEVELOPMENT_LOG.md` format and HR-07 session_id presence |
| `verify_spec_compliance.py` | 7KB | End-to-end spec compliance: code must implement every FR declared in SAD.md |
| `verify_path_consistency.py` | 3KB | Confirms all path references in SAD.md and manifest match actual filesystem |
| `state_monitor.py` | 6KB | Reads `.methodology/state.json`; reports FSM state, phase, timestamps |

#### Release Management

| Script | Size | Purpose |
|---|---|---|
| `bump_version.py` | 2KB | Semantic version bump: updates `SKILL.md` + `SAD.md` version headers |
| `create_release.sh` | 1KB | Creates a GitHub release tag from current version |

---

### §3.22 — `core/task_splitter.py` — Task Decomposer

**Responsibility**: Decomposes a high-level development goal into a DAG of ordered `Task` objects with dependencies, priorities, and estimated hours. Used by `harness_cli.py plan-phase` and `AgentSpawner` for FR-level work breakdown.

**Key classes**:

```python
class TaskStatus(Enum):
    PENDING | RUNNING | COMPLETED | FAILED | BLOCKED

class TaskPriority(Enum):
    LOW(1) | MEDIUM(2) | HIGH(3) | CRITICAL(4)

@dataclass
class Task:
    id: str                          # "task-001", "task-002", ...
    name: str
    description: str
    status: TaskStatus = PENDING
    priority: TaskPriority = MEDIUM
    dependencies: List[str]          # list of task ids this depends on
    assignee: Optional[str]
    estimated_hours: float
    actual_hours: float
    output: Any
    error: Optional[str]
    created_at: str                  # ISO datetime
    completed_at: Optional[str]
```

**`TaskSplitter`**:

```python
class TaskSplitter:
    def create_task(name, description, priority, estimated_hours) -> Task
    def add_dependency(task_id, depends_on) -> None
    def split_from_goal(goal: str) -> List[Task]
        # Keyword-maps goal text → standard phase tasks:
        # research → Design → Development → Testing → Documentation → Deployment
        # Sequentially chained (each depends on previous)
    def get_ready_tasks() -> List[Task]     # PENDING with all deps COMPLETED
    def get_execution_order() -> List[Task] # topological sort
    def get_dag() -> Dict                   # {nodes: [...], edges: [...]}
    def get_summary() -> Dict               # counts by status + total_estimated_hours
```

---

### §3.23 — `core/sessions_spawn_logger.py` — Spawn Event Logger

**Responsibility**: Records agent spawn events to `.methodology/sessions_spawn.log` (HR-10 compliance). Supports two-phase write (PENDING → COMPLETED/FAILED via `log_update()`).

**Public API**:

```python
class SessionsSpawnLogger:
    LOG_FILENAME = ".methodology/sessions_spawn.log"

    def __init__(self, repo_path: Path)

    def log_spawn(
        role: str, task: str, session_id: str,
        confidence: Optional[int] = None,
        status: str = "SPAWNED",
        **kwargs,
    ) -> Dict                     # appends JSON line to log

    def log_update(session_id: str, **updates) -> Optional[Dict]
        # Finds entry by session_id, applies updates in-place
        # Used to flip PENDING → COMPLETED/FAILED after spawn completes

    def validate() -> Dict        # {valid: bool, count: int, errors: []}
    def get_summary() -> Dict     # {total_entries, role_counts, fr_tasks, status_counts, valid}
```

**Log format** (each line is a JSON object):
```json
{"timestamp": "...", "role": "developer", "task": "FR-01", "session_id": "dev-abc123", "status": "SPAWNED", "confidence": 8}
```

**Convenience function**:
```python
def log_spawn_event(repo_path, role, task, session_id, **kwargs) -> Dict
    # One-shot: SessionsSpawnLogger(repo_path).log_spawn(...)
```

**HR-10 compliance**: `validate()` checks every entry has `role` + `session_id` fields. Two entries per FR (developer + reviewer) required by HR-10.

---

### §3.24 — `core/requirement_traceability.py` — ASPICE Traceability Manager

**Responsibility**: FR → SRS → Code → Test bidirectional traceability. ASPICE SWE.3/SYS.4 compliant. Tracks which requirements are implemented, which code components satisfy them, and which test files verify the implementation.

**Key dataclasses**:

```python
class LinkType(Enum):
    FR_TO_SRS | SRS_TO_CODE | CODE_TO_TEST | TEST_TO_QUALITY | QUALITY_TO_AUDIT | BIDIRECTIONAL

@dataclass
class Requirement:       # req_id, title, description, priority, status, srs_section
@dataclass
class CodeComponent:     # file_path, functions, classes, fr_id, test_files, coverage
@dataclass
class TestCoverage:      # test_file, test_functions, fr_id, coverage_percentage, status
@dataclass
class TraceLink:         # link_id, source_type/id, target_type/id, link_type, bidirectional
```

**`RequirementTraceability` public API**:

```python
class RequirementTraceability:
    def __init__(self, project_id: str)
    def add_requirement(req_id, title, srs_section=None, ...) -> Requirement
    def add_code_component(file_path, fr_id=None, ...) -> CodeComponent
    def add_test_coverage(test_file, fr_id=None, ...) -> TestCoverage
    def add_link(source_type, source_id, target_type, target_id, link_type, ...) -> TraceLink
    def get_downstream(req_id) -> {srs: [], code: [], test: [], quality: []}
    def get_upstream(component_id) -> {fr: [], srs: [], code: []}
    def verify_completeness() -> {
        total_requirements, srs_coverage, code_coverage,
        test_coverage, verification_rate, total_links,
        missing_mappings: {fr_without_srs, fr_without_code, fr_without_test}
    }
    def get_traceability_matrix() -> list[dict]   # one row per FR
    def export_report(format="standard"|"aspice") -> dict
    def save(filepath) -> None
```

**ASPICE compliance checks** (when `format="aspice"`):
- `SWE_3_B_SP1`: 100% SRS coverage
- `SWE_3_B_SP2`: 100% code coverage
- `SWE_3_B_SP3`: 100% test coverage

**CLI usage**:
```bash
python core/requirement_traceability.py --project-id <id> [--verify] [--export report.json] [--format aspice]
```

---

### §3.25 — `agent_personas/` — Role Persona Library

**Responsibility**: Provides preset agent personas loaded by `AgentSpawner._load_persona()`. Each persona supplies a role description and personality traits injected into the agent prompt `[PERSONA]` block.

**Package structure**:

| File | Purpose |
|---|---|
| `persona.py` | `Persona` class + `generate_persona_prompt(persona_type, task)` |
| `__init__.py` | Re-exports `Persona`, `generate_persona_prompt`, `get_persona`, `PERSONAS` dict |
| `ARCHITECT.md` | Narrative persona document (read by `_load_persona("ARCHITECT")`) |
| `DEVELOPER.md` | Developer persona |
| `REVIEWER.md` | Reviewer persona |
| `QA_ENGINEER.md` | QA Engineer persona |
| `DEVOPS.md` | DevOps persona |
| `PRODUCT_MANAGER.md` | Product Manager persona |

**`get_persona(persona_type: str) -> Persona`** (6 presets):
- `architect` — Strategic, big-picture thinker, prioritizes scalability
- `developer` — Practical, efficiency-focused, follows best practices
- `reviewer` — Detail-oriented, critical thinker, focuses on quality
- `qa` — Thorough, systematic, prioritizes test coverage and edge cases
- `pm` — User-centric, data-driven, balances business and technical needs
- `devops` — Automation-first, reliability-focused, prioritizes CI/CD

**Usage**:
```python
from agent_personas import get_persona, generate_persona_prompt

# Object-oriented
persona = get_persona("developer")

# String prompt (called by AgentSpawner._load_persona)
prompt = generate_persona_prompt("developer", task="implement FR-01 login feature")
```

**Note**: `AgentSpawner._load_persona(role)` reads `agent_personas/{ROLE.upper()}.md` — the Markdown files are the authoritative persona source; `persona.py` provides the programmatic wrapper.

---

### §3.26 — `core/cli_phase_prompts.py` + `core/enforcement_config.py` — Layer 4 Utilities

**`core/cli_phase_prompts.py`** (24KB): Phase-specific prompt templates used by `harness_cli.py plan-phase` and the pipeline to generate FR execution prompts. Contains one prompt template per phase (P1–P8), formatted for the sub-agent that will execute the FR.

**`core/enforcement_config.py`** (5KB): Configuration schema and defaults for the `enforcement/` subsystem. Provides `EnforcementConfig` dataclass with fields for policy severity levels, audit trail retention, and hook behavior. Loaded by `FrameworkEnforcer.__init__()` at phase preflight.

---

### §3.27 — `harness/git_strategy.py` — Gate-Aligned Git Strategy (10-Push)

**Responsibility**: Implements the **10-Push Gate-Aligned commit + push policy** for harness pipelines. Each push auto-generates `HANDOVER.md` at the repo root via `HandoverGenerator` (§3.28). All operations are no-ops when `--no-git` is passed or `HARNESS_NO_GIT=1` is set. Git failures are logged as warnings — they never block the pipeline.

**Push schedule**:

| Push | ID | Trigger | Commit Message Pattern |
|---|---|---|---|
| ① | `P1-plan-YYYYMMDD` | P1 exit: task plan complete | `docs(P1): task plan committed` |
| ② | `P2-manifest-YYYYMMDD` | P2 exit: `manifest` command success | `docs(P2): finalize SRS + SAD; generate quality manifest [fr_ids=...]` |
| ③ | `P3-mid-YYYYMMDD` | P3 mid: FR completion ratio ≥ 50% | `feat(P3-mid): FR progress checkpoint — N/T FRs at Gate1 PASS` |
| ④ | `P3-pre-ssi-YYYYMMDD` | P3 pre-SSI: all FRs at Gate 1 PASS | `feat(P3-pre-ssi): all FRs Gate1 PASS — entering SSI round` |
| ⑤ | `Gate2-YYYYMMDD` | Gate 2 PASS (P3 exit, score ≥75) | `feat(P3): Gate2 PASS score=XX — N FR(s) implemented` |
| ⑥ | `Gate3-YYYYMMDD` | Gate 3 PASS (P4 exit, score ≥80) | `test(P4): Gate3 PASS score=XX — full test suite` |
| ⑦ | `P5-baseline-YYYYMMDD` | P5: BASELINE.md written | auto-included if `_has_changes()` returns True |
| ⑧ | `Gate4-YYYYMMDD` | Gate 4 APPROVE (P6, score ≥85) | `release(P6): Gate4 PASS score=XX — pipeline complete` + `git tag gate4-YYYYMMDD-scoreXX` |
| ⑨ | `P7-risk-YYYYMMDD` | P7 completion | `docs(P7): risk register complete` |
| ⑩ | `P8-config-YYYYMMDD` | P8 completion | `docs(P8): config mgmt records complete` |

**Local commit policy** (no push): Each Gate 1 PASS per FR commits locally:
```
feat(FR-01): Gate1 PASS — score=88.0 [phase=3]
```
`commit_fr_gate1()` also calls `FRProgressTracker.record_gate1_pass()` (§3.29) to persist progress to `.methodology/fr_progress.json`.

**HANDOVER.md auto-generation**: Every push calls `_write_handover(checkpoint_id, phase, background, status, steps, notes, extra)`, which invokes `HandoverGenerator.write()` (§3.28). The written HANDOVER.md includes a `/compact` prompt for the next Claude session. Failures are silently swallowed — never block the pipeline.

**`.gitignore` auto-maintenance** (`ensure_gitignore()`): Called once per pipeline run. Appends missing entries for harness runtime artifacts:
```
.sessi-work/
.methodology/last_block.md
.methodology/steering_history.json
```

**Public API**:

```python
class GitStrategy:
    def __init__(self, project: Path, enabled: bool = True, push: bool = True)

    def ensure_gitignore() -> None
    def commit_fr_gate1(fr_id, score, phase) -> bool        # Gate 1 local commit + FRProgressTracker

    # PUSH methods (each writes HANDOVER.md before push)
    def commit_and_push_p1(fr_ids, background, notes=None) -> bool        # PUSH ①
    def commit_and_push_p2(fr_ids, background=None, notes=None) -> bool   # PUSH ②
    def commit_and_push_p3_mid(fr_done, fr_total, fr_ids,
                               background=None, notes=None) -> bool       # PUSH ③
    def commit_and_push_p3_pre_ssi(fr_ids, background=None,
                                   notes=None) -> bool                    # PUSH ④
    def commit_and_push_gate(gate_num, phase, score, n_frs=0,
                             background=None, notes=None) -> bool         # PUSH ⑤⑥⑧ + tag
    def commit_and_push_p5_baseline(background=None, notes=None) -> bool  # PUSH ⑦
    def commit_and_push_p7(background=None, notes=None) -> bool           # PUSH ⑨
    def commit_and_push_p8(background=None, notes=None) -> bool           # PUSH ⑩
    def commit_and_push_final(phases, background=None, notes=None) -> bool  # deprecated → p7/p8
```

**Helpers** (private):
- `_write_handover(checkpoint_id, phase, background, status, steps, notes, extra)` — calls `HandoverGenerator`, prints checkpoint_id, never raises.
- `_cp(label) -> str` — builds checkpoint_id: `{label}-{YYYYMMDD}` (UTC).
- `_fr_summary(fr_ids) -> str` — compact FR list string, max 5 shown.

**CLI flag**: `--no-git` added to `run-gate`, `run-pipeline`, and `manifest` subcommands.
Env override: `HARNESS_NO_GIT=1` disables git across all commands without a flag.

---

### §3.28 — `harness/handover_generator.py` — Session Handover Writer

**Responsibility**: Renders `HANDOVER.md` at the project root after each significant push checkpoint. Provides the next Claude session with task background, current execution status, next steps, and notes (including `/compact` prompt).

**Module-level constants**:

```python
DEFAULT_NOTES: list[str] = [
    "100% follow SKILL.md",
    "Do NOT commit .sessi-work/ or .methodology/ runtime artifacts",
    "Git failures are warnings — never block the pipeline",
]
```

**Public API**:

```python
class HandoverGenerator:
    def __init__(self, project: Path)

    def write(
        self,
        checkpoint_id: str,   # e.g. "P3-pre-ssi-20260504"
        phase: int,
        task_background: str,
        current_status: str,
        next_steps: List[str],
        notes: List[str] | None = None,   # prepended AFTER DEFAULT_NOTES
        extra: Dict | None = None,        # optional freeform section
    ) -> Path
```

**Rendered HANDOVER.md structure**:
```markdown
# Harness Session Handover — {checkpoint_id}

> ⚠️ 開始下一個工作階段前，請先執行 /compact 壓縮上下文

**Phase**: {phase} | **Generated**: {UTC ISO timestamp}

## 任務背景
{task_background}

## 目前執行狀況
{current_status}

## 接下來的工作
1. {next_steps[0]}
...

## 注意事項
- 100% follow SKILL.md
- Do NOT commit .sessi-work/ or .methodology/ runtime artifacts
- Git failures are warnings — never block the pipeline
- {caller-supplied notes...}

## 附加資訊  ← only if extra provided
{extra key-value pairs}

---
*由 HandoverGenerator 自動生成。下次 push 時此檔案將被覆寫。*
```

**Behavior**: Each call **overwrites** the single `HANDOVER.md` at project root. The checkpoint_id (including date suffix) identifies which push created the file.

---

### §3.29 — `harness/fr_progress_tracker.py` — FR Gate-1 Progress Persistence

**Responsibility**: Persists FR Gate-1 PASS/FAIL status across sessions to `.methodology/fr_progress.json`. Enables mid-P3 session recovery — the next session can resume from the last known checkpoint without re-running completed FRs.

**Public API**:

```python
class FRProgressTracker:
    def __init__(self, project: Path, phase: int = 3)

    def record_gate1_pass(self, fr_id: str, score: float = 0.0, phase: int = None) -> None
    def record_gate1_fail(self, fr_id: str, score: float = 0.0,
                          phase: int = None, reason: str = "") -> None
    def reset(self) -> None                  # deletes fr_progress.json
    def load(self) -> dict                   # returns scaffold if missing or corrupt

    # Query methods
    def passed_fr_ids(self) -> List[str]     # sorted alphabetically
    def failed_fr_ids(self) -> List[str]
    def pending(self, all_fr_ids: List[str]) -> List[str]   # preserves input order
    def completion_ratio(self, total: int) -> float          # 0.0 if total == 0
    def summary(self, total: Optional[int] = None) -> str    # "2/5 Gate1 PASS"
    def to_status_string(self, total: Optional[int] = None) -> str  # includes failed FRs + "retry"
```

**Persistence schema** (`.methodology/fr_progress.json`):
```json
{
  "phase": 3,
  "updated_at": "2026-05-04T10:00:00+00:00",
  "frs": {
    "FR-001": {
      "status": "gate1_pass",   // "gate1_pass" | "gate1_fail"
      "score": 82.5,
      "phase": 3,
      "timestamp": "2026-05-04T10:00:00+00:00",
      "reason": ""              // only for gate1_fail
    }
  }
}
```

**Resilience**: `load()` returns `{"frs": {}}` scaffold on missing file or corrupt JSON. `_write()` auto-creates `.methodology/` directory. `record_gate1_pass()` and `record_gate1_fail()` overwrite any existing entry for the same `fr_id`.

**Integration with GitStrategy**: `commit_fr_gate1()` (§3.27) calls `FRProgressTracker.record_gate1_pass()` after each successful Gate 1 commit. Wrapped in `try/except` — tracker failure never blocks the git commit.

---

### §3.30 — `harness/retry_utils.py` — Exponential Backoff Utility

**Responsibility**: Generic retry decorator / wrapper with configurable exponential backoff + jitter. Used by `quality_dashboard/agent_auto_research.py` to wrap the SSI subprocess call.

**Public API**:

```python
def retry_with_backoff(
    fn: Callable[[], T],
    *,
    max_attempts: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
    jitter: float = 0.25,          # ±25% random jitter on computed delay
    retryable: Optional[Callable[[Exception], bool]] = None,
                                   # None → retries (OSError, TimeoutError) only
    on_retry: Optional[Callable[[int, Exception, float], None]] = None,
                                   # on_retry(attempt, exc, wait_secs)
) -> T

def _compute_delay(attempt: int, base: float, cap: float, jitter: float) -> float
    # delay = min(base * 2^(attempt-1), cap) * (1 ± jitter)
    # attempt=1 → base; attempt=2 → 2×base; capped at max_delay
```

**Behavior**:
- `max_attempts < 1` → raises `ValueError` immediately.
- Non-retryable exception (not matched by `retryable` predicate) → re-raised on first occurrence; never retried.
- Exhausted all attempts → re-raises the last exception.
- `on_retry` is `None` → prints `"Retry {attempt}/{max_attempts} after {wait:.1f}s: {exc}"`.

**Integration**: `quality_dashboard/agent_auto_research.py` wraps `_evaluate_all_dimensions()` inner subprocess call with `retry_with_backoff(max_attempts=3, base_delay=2.0, retryable=lambda exc: isinstance(exc, (OSError, subprocess.TimeoutExpired)))`. Falls back to `_fallback_evaluation()` if all retries exhausted.

---

## 7. Runtime Prerequisites & Remaining Work

All in-framework bugs and stubs have been resolved. The one remaining dependency is external:

| Item | Location | Status | Notes |
|---|---|---|---|
| `software_self_improvement` package | `harness_bridge._invoke_harness()` | **External dependency** | Install: `pip install -e path/to/software_self_improvement`. Once installed, `python3 -m software_self_improvement.runner` resolves and gate runs work end-to-end. |

### Integration Contract (authoritative source: `software_self_improvement` repo)

| Artifact | Path in SSI repo | Purpose |
|---|---|---|
| Entry point | `software_self_improvement/runner.py` | CLI called by `_invoke_harness()` |
| Result schema | `schemas/harness_gate_result.schema.json` | JSON Schema Draft-7 for output file |
| Integration doc | `docs/HARNESS_INTEGRATION.md` | Full interface spec: args, workspace layout, gate table, env vars |

**Subprocess call** (written in `_invoke_harness`):
```
python3 -m software_self_improvement.runner
    --config  .sessi-work/gate{n}_config.yaml
    --root    {project_root}
    --output  .sessi-work/gate{n}_result.json
    [--fr-id  {fr_id}]
```

**Exit codes**: `0` = `quality_complete=True` | `1` = gate not passed | `2+` = error

**Config translation** (`runner.translate_gate_config`):
- `dimensions[].threshold` (harness) → `dimensions[name].target` (SSI)
- `dimensions[].tier` / `model` → `llm_routing` tier assignment
- Gate 1: `score_gate` absent → per-dim threshold only (blocking handled by harness, not runner)

**Workspace** (both repos share `.sessi-work/` in project root):
```
.sessi-work/
  gate{n}_config.yaml          ← harness writes (runner input)
  gate{n}_ssi_config.json      ← runner writes (translated SSI config)
  gate{n}_result.json          ← runner writes (harness reads)
  round_{k}/scores/*.json      ← evaluators write, score.py reads
  issue_registry.json          ← persistent across rounds
  crg_metrics.json             ← crg_bridge writes, runner reads (optional)
```

### Previously Fixed Stubs (resolved in this version)

| Fix | Location | Resolution |
|---|---|---|
| ① SSI runner stub | `harness_bridge._invoke_harness()` | Replaced `NotImplementedError` with subprocess call + JSON result parsing |
| ② `parse_sad` import failure | `harness_bridge.generate_quality_manifest()` | Fixed by adding `parse_sad()` to `scripts/generate_sab.py` |
| ③ P7/P8 Claude routing not wired | `core/agent_spawner.spawn()` | `get_reviewer_model(phase, role)` now checked before Hermes dispatch; P7/P8 auto-route to Claude |
| ④ Gate 4 Hermes APPROVE not enforced | `harness_bridge.run_gate()` | Added `_require_hermes_approve()` called after score check passes |
| ⑤ `parse_sad` alias missing | `scripts/generate_sab.py` | Added `parse_sad()` function wrapping `extract_sab_from_sad`, with correct key mapping |


---

## 8. Future Work — Score Roadmap & Open Items

> **Baseline score (v2.0)**: 92/100 (Academic Benchmark, 7-dimension framework).
> **v2.1 delta**: Sequential A/B + dep-ordered decomposition — no score regression; improves A/B reliability for large docs (SRS.md, SAD.md).
> Target ceiling ~96/100; remaining 8 points have concrete unlock conditions below.

### 8.1 Score Roadmap (post-v2.0 unlocks)

| Priority | Action | Score Delta | Unlock Condition | Dimension |
|---|---|---|---|---|
| **P1** | SSI result field name verification | 0 pts (correctness fix) | Run one real gate end-to-end; confirm whether SSI runner renames `open_critical_count` -> `open_critical` before writing result JSON, or whether `harness_bridge._parse_result()` needs updating | A |
| **P1** | ~~`constitution/` package stub or real impl~~ | ✅ Done (v2.0.1) | `constitution/` implemented — `BVSRunner`, `CitationParser`, `VerificationConstitutionChecker` all deployed. | A |
| **P2** | `harness_bridge` empirical project validation | **+1 -> 93** | First full run against a real project. Confirms Tier 1 deterministic scoring is stable and subprocess call chain works end-to-end | A (20->21) |
| **P2** | CRG activation + empirical data | **+1 -> 94** | First real project run with CRG MCP available. Validates `min(tool, llm)` floor and `crg_metrics.json` structural signals. Currently `CRGBridge.is_available()` returns `False` in standalone mode | E (10->11) |
| **P3** | ASPICE full traceability matrix (Phase E docs) | **+1 -> 95** | `scripts/check_spec_trace.py` now automates FR→test traceability (partial). Complete `TRACEABILITY_MATRIX.md` linking FR-01..FR-N to code modules, test cases, and gate results for full ASPICE Level 2 alignment | C (15->16) |
| **P4** | Developer-side deterministic tooling | **+1-2 -> 96** | Replace or augment Claude developer agent with static analysis pipeline (mypy strict, semgrep, complexity checker). Reduces D-dimension LLM dependency from 13/15 to 15/15 | D (13->15) |

### 8.2 Open Integration Items (Ready, No External Blockers)

| Item | File | Status | Action |
|---|---|---|---|
| SSI output field mismatch | `harness/harness_bridge.py` | ✅ **Resolved (v2.0.2)** — `_parse_result()` now uses dual-fallback: `raw.get("open_critical", raw.get("open_critical_count", 0))` and `raw.get("open_high", raw.get("open_high_count", 0))`. Accepts both SSI runner field name variants. | — |
| `constitution.*` graceful degrade | `steering/integrations.py` | ✅ **Resolved (v2.0.1)** — `constitution/` package implemented: `BVSRunner` (HR-03 phase checks), `CitationParser` (HR-07/09), `VerificationConstitutionChecker` (bridges R001-R007). All imports now resolve; `SteeringIntegrator` fully operational. | See §3.19 |
| HR-12 real limiter not wired | `steering/integrations.py` | ✅ **Resolved (v2.0.2)** — `SteeringIntegrator.should_continue` property now cross-checks `HR12Resolution(max_allowed, early_stop_threshold, min_rounds_before_stop).should_stop()` against `SteeringLoop.should_continue()`. HR-12 takes priority; `VerificationConstitutionChecker.check()` called on stop. | — |
| Gate 4 Hermes approval timeout | `harness/harness_bridge.py` | ✅ **Resolved (v2.0.2, updated v2.2)** — `HarnessBridge.GATE4_HERMES_TIMEOUT_MS = int(os.environ.get("HERMES_TIMEOUT_MS", "120000"))` (default 120 s); `ReviewerRouter.HERMES_TIMEOUT_MS` reads same env var (default 120 s). Both sync via env var — set `HERMES_TIMEOUT_MS` to override globally. | — |
| `enforcement.json` policy hot-reload | `enforcement/policy_engine.py` | ✅ **Resolved (v2.0.2)** — `PolicyEngine.reload_policy(json_path)` hot-reloads policies by ID from `enforcement.json`; `PolicyEngine.from_json(json_path)` classmethod for fresh engine. `harness_cli.py reload-policy` command exposes this as CLI (7th command). | — |
| Sequential A/B + dep-ordered decomposition | `harness/reviewer_router.py` | ✅ **Resolved (v2.1)** — `_decompose_with_deps()` replaces `_maybe_decompose()`; `review()` sequential for-loop replaces list comprehension; `_enrich_with_context()` injects `approved_context`; `_topological_sort()` ensures dependency-safe order; `SubTask` dataclass tracks label/deps/index/total. | See §3.2, §4.2 |
| crg-003: high coupling quality_gate ↔ tests-parse | `core/quality_gate/` | ✅ **Resolved** — `parsers/` sub-package extracted: `DevelopmentLogParser` from `ab_enforcer.py`, `SpecTrackingParser` from `spec_tracking_checker.py`. All regex in `parsers/`; checkers contain zero `re.search()` calls. | See §3.16 |
| crg-004: test coverage <80% | `core/quality_gate/`, all scoped modules | ✅ **Resolved (v2.2)** — W0-W11 TDD waves: coverage 16% → **90.19%** (threshold met, exceeded). 986 tests (983 passed + 3 skipped). W7 added 92 tests (C+D); rounds 9-11 added 219 further tests. Production bugfix: `ab_enforcer.verify_qa_not_developer` returned string not bool (Python `and` short-circuit); wrapped with `bool()`. Scoped via `.coveragerc` to core business logic only. | See §8.4 |

### 8.4 Coverage Gap Analysis — v2.2 current state (rounds 9–11 complete)

**Current scoped coverage: 90.19%** (487 stmts uncovered / 4963 total). Rounds 9-11 closed remaining Category B/C gaps via targeted mocking.

#### History

| Version | Coverage | Tests | Notes |
|---|---|---|---|
| v1.7 baseline | 80.12% | — | 929 stmts uncovered / 4673 total |
| v1.8 (W7) | 84.16% | 767 | Category C+D closed via unit tests with tmp_path |
| v2.2 (rounds 9–11) | **90.19%** | **986** | Categories B/C further reduced; bugfix: `ab_enforcer.verify_qa_not_developer` bool coercion |

#### Category A — Intentionally Untestable (Excluded from priority)
| File | Miss | Reason |
|---|---|---|
| `core/adapters/phase_hooks_adapter.py` | 73 | Thin adapter over external `PhaseHooksRunner`; zero business logic — 100% subprocess delegation |
| `core/cli_phase_prompts.py` | 6 | Pure string constants (prompt templates); no executable logic |

**Verdict**: 0% is acceptable. Do not add tests.

#### Category B — Subprocess/External-API Bound (Residual)
| File | Miss (current) | Blocking reason |
|---|---|---|
| `enforcement/framework_enforcer.py` | ~94 | `run()`, `check_*()` paths call `subprocess.run(git)` + multi-file I/O; integration-test territory |
| `harness/harness_bridge.py` | 26 (78%) | Gates 2–4 require live Hermes MCP + real SSI subprocess; cannot stub without full integration env |
| `core/quality_gate/stage_pass_generator.py` | ~104 | `git_push()`, `_log_to_development_log()` — require real git repo + subprocess chain |

**Verdict**: Block behind `@pytest.mark.integration`; run in CI with full Docker env.

#### Category C — Business Logic (Residual — partially closed in rounds 9-11)
| File | Miss (current) | Status |
|---|---|---|
| `core/quality_gate/phase_truth_verifier.py` | 11 (93%) | Substantially closed (was 71/57%) |
| `core/quality_gate/spec_tracking_checker.py` | 19 (81%) | Partially closed (was 54/47%) |
| `core/quality_gate/ab_enforcer.py` | 6 (93%) | Substantially closed (was 70/33%) |
| `enforcement/constitution_policy_sync.py` | 0 (100%) | ✅ Fully closed (was 105/26%) |

#### Summary

| Category | Current Strategy |
|---|---|
| A — Untestable constants/adapters | Accept 0% — never |
| B — Integration/subprocess bound | `@pytest.mark.integration` + CI Docker env |
| C — Business logic residual | Closed 90%+; remainder (<20 stmts each) deferred |

### 8.3 Technical Debt (Lower Priority)

| Item | File | Notes |
|---|---|---|
| ~~Chinese-language comments~~ | `core/cli_phase_prompts.py`, `core/quality_gate/ab_enforcer.py`, `core/quality_gate/phase_truth_verifier.py`, `core/quality_gate/spec_tracking_checker.py` | ✅ **Resolved** — all 4 files confirmed 0 CJK characters (translated in Apr 2026 batch) |
| `cli.py` standalone boundary | `cli.py` (288KB, v6.102.0) | Requires 30+ external modules; cannot run in harness-only mode. Intentional design boundary — add explicit note in README that `harness_cli.py` is the standalone entry point |
| `phase_auditor.py` | `scripts/phase_auditor.py` | ✅ Documented in §3.20 |
| `EnsembleScorer` threshold calibration | `detection/ensemble_scorer.py` | `PASS_THRESHOLD = 0.65` is arbitrary; recalibrate against real project runs once empirical data is available (targeted after first P2 project run) |
