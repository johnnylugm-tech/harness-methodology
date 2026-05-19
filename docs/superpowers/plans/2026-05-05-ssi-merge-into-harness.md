# SSI → Harness Merge (Option B) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Absorb `software_self_improvement` fully into `harness-methodology` as `harness/ssi/`, replace the subprocess IPC model with the correct Claude conversation execution model, and update all APIs + docs to match.

**Architecture:** SSI is a Claude Code skill executed natively in the conversation window — not a subprocess. `harness_bridge.py` is redesigned as a state coordinator (`prepare_gate` → Claude evaluates inline → `finalize_gate`). All SSI scripts/prompts live under `harness/ssi/` as first-class source files.

**Tech Stack:** Python 3.9+, pytest, PyYAML, dataclasses, existing harness infrastructure

---

## File Map

### Create
- `harness/ssi/__init__.py`
- `harness/ssi/scripts/__init__.py` + 12 Python scripts + 1 bash script (from SSI repo)
- `harness/ssi/prompts/` — 5 markdown prompt files (from SSI repo)
- `harness/ssi/schemas/harness_gate_result.schema.json` (from SSI repo)

### Modify
- `harness/harness_bridge.py` — add `GateContext`, `prepare_gate()`, `finalize_gate()`; deprecate `run_gate()` + remove `_invoke_harness()`
- `harness/crg_bridge.py` — update `_ssi_root()` to point to `harness/ssi/`
- `harness_cli.py` — update `run-gate` to use `prepare_gate()`; add `finalize-gate` subcommand; update `run-pipeline` _(note: run-pipeline was removed in v2.5)_
- `SKILL.md` — add §12 Gate Evaluation Protocol
- `docs/HARNESS_INTEGRATION.md` — update execution model + architecture
- `SAD.md` — update §SSI integration section

### Test
- `tests/test_harness_bridge.py` — remove `_invoke_harness` mocks; add `prepare_gate` / `finalize_gate` tests
- `tests/test_crg_bridge.py` (create) — test new `_ssi_root()` default
- `tests/test_ssi_scripts.py` (create) — smoke tests: scripts importable from new location

---

## Task 1: Create harness/ssi/ directory and copy SSI assets

**Files:**
- Create: `harness/ssi/__init__.py`
- Create: `harness/ssi/scripts/__init__.py`
- Create: `harness/ssi/prompts/` (directory)
- Create: `harness/ssi/schemas/` (directory)
- Copy from `.quality_dashboard/`: all SSI scripts + prompts

- [ ] **Step 1: Create directory skeleton**

```bash
mkdir -p harness/ssi/scripts harness/ssi/prompts harness/ssi/schemas
touch harness/ssi/__init__.py harness/ssi/scripts/__init__.py
```

- [ ] **Step 2: Copy SSI scripts from .quality_dashboard/**

The `.quality_dashboard/` directory contains the exact SSI scripts from the SSI repo (placed there by past quality improvement runs on this repo). Copy them as the canonical source:

```bash
cp .quality_dashboard/checkpoint.py      harness/ssi/scripts/
cp .quality_dashboard/config_loader.py   harness/ssi/scripts/
cp .quality_dashboard/crg_analysis.py    harness/ssi/scripts/
cp .quality_dashboard/crg_integration.py harness/ssi/scripts/
cp .quality_dashboard/issue_tracker.py   harness/ssi/scripts/
cp .quality_dashboard/llm_router.py      harness/ssi/scripts/
cp .quality_dashboard/report_gen.py      harness/ssi/scripts/
cp .quality_dashboard/score.py           harness/ssi/scripts/
cp .quality_dashboard/setup_target.py    harness/ssi/scripts/
cp .quality_dashboard/verify.py          harness/ssi/scripts/
cp .quality_dashboard/verify_tools.py    harness/ssi/scripts/
cp .quality_dashboard/install_extended_tools.sh harness/ssi/scripts/
```

- [ ] **Step 3: Copy SSI prompts from .quality_dashboard/**

```bash
cp .quality_dashboard/crg_reconnaissance.md harness/ssi/prompts/
cp .quality_dashboard/evaluate_dimension.md  harness/ssi/prompts/
cp .quality_dashboard/final_report.md        harness/ssi/prompts/
cp .quality_dashboard/improvement_plan.md    harness/ssi/prompts/
cp .quality_dashboard/verify_round.md        harness/ssi/prompts/
```

- [ ] **Step 4: Write failing smoke test**

Create `tests/test_ssi_scripts.py`:

```python
"""Smoke tests: SSI scripts importable from harness/ssi/scripts/."""
import sys
from pathlib import Path

SSI_SCRIPTS = str(Path(__file__).parent.parent / "harness" / "ssi" / "scripts")


def _add_ssi_to_path():
    if SSI_SCRIPTS not in sys.path:
        sys.path.insert(0, SSI_SCRIPTS)


def test_config_loader_importable():
    _add_ssi_to_path()
    import importlib
    mod = importlib.import_module("config_loader")
    assert hasattr(mod, "normalize_weights")
    assert hasattr(mod, "DEFAULT_CONFIG")


def test_score_importable():
    _add_ssi_to_path()
    import importlib
    mod = importlib.import_module("score")
    assert hasattr(mod, "compute_overall_score")
    assert hasattr(mod, "load_scores")


def test_issue_tracker_importable():
    _add_ssi_to_path()
    import importlib
    mod = importlib.import_module("issue_tracker")
    assert hasattr(mod, "load")


def test_prompts_exist():
    prompts_dir = Path(__file__).parent.parent / "harness" / "ssi" / "prompts"
    expected = {
        "evaluate_dimension.md",
        "improvement_plan.md",
        "verify_round.md",
        "final_report.md",
        "crg_reconnaissance.md",
    }
    found = {f.name for f in prompts_dir.iterdir() if f.suffix == ".md"}
    assert expected <= found, f"Missing prompts: {expected - found}"


def test_scripts_directory_complete():
    scripts_dir = Path(__file__).parent.parent / "harness" / "ssi" / "scripts"
    expected = {
        "config_loader.py", "score.py", "issue_tracker.py",
        "checkpoint.py", "verify.py", "verify_tools.py",
        "report_gen.py", "crg_analysis.py", "crg_integration.py",
        "llm_router.py", "setup_target.py",
    }
    found = {f.name for f in scripts_dir.iterdir() if f.suffix == ".py"}
    assert expected <= found, f"Missing scripts: {expected - found}"
```

- [ ] **Step 5: Run test to verify it fails**

```bash
cd /Users/johnny/harness-methodology
pytest tests/test_ssi_scripts.py -v
```

Expected: FAIL — files not copied yet (if running before steps 2-3) or PASS if files are present. If files already copied, it should pass.

- [ ] **Step 6: Copy schema from SSI repo**

```bash
# Fetch schema from SSI GitHub (the .quality_dashboard/ doesn't include schemas/)
curl -sL https://raw.githubusercontent.com/johnnylugm-tech/software_self_improvement/main/schemas/harness_gate_result.schema.json \
  > harness/ssi/schemas/harness_gate_result.schema.json
```

OR if network unavailable, create minimal schema:
```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "HarnessGateResult",
  "type": "object",
  "required": ["score", "quality_complete", "dimensions"],
  "properties": {
    "score": {"type": "number"},
    "quality_complete": {"type": "boolean"},
    "rounds_used": {"type": "integer"},
    "open_critical": {"type": "integer"},
    "open_high": {"type": "integer"},
    "dimensions": {
      "type": "array",
      "items": {
        "type": "object",
        "required": ["name", "score", "threshold"],
        "properties": {
          "name": {"type": "string"},
          "score": {"type": "number"},
          "threshold": {"type": "number"},
          "issues": {"type": "array"}
        }
      }
    }
  }
}
```

- [ ] **Step 7: Run smoke tests — expect PASS**

```bash
pytest tests/test_ssi_scripts.py -v
```

Expected: all 5 tests PASS

- [ ] **Step 8: Commit**

```bash
git add harness/ssi/ tests/test_ssi_scripts.py
git commit -m "feat: embed SSI scripts/prompts/schemas into harness/ssi/ [Option B]"
```

---

## Task 2: Update crg_bridge._ssi_root() to embedded SSI path

**Files:**
- Modify: `harness/crg_bridge.py`
- Create: `tests/test_crg_bridge.py`

- [ ] **Step 1: Write failing test**

Create `tests/test_crg_bridge.py`:

```python
"""Tests for CRGBridge._ssi_root() with embedded SSI."""
import os
from pathlib import Path
from unittest.mock import patch
from harness.crg_bridge import CRGBridge


def test_ssi_root_default_points_to_embedded():
    """Without SSI_ROOT env var, _ssi_root() returns harness/ssi/ path."""
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("SSI_ROOT", None)
        bridge = CRGBridge()
        root = bridge._ssi_root()
    expected_suffix = str(Path("harness") / "ssi")
    assert root.endswith(expected_suffix), (
        f"Expected path ending with 'harness/ssi', got: {root}"
    )


def test_ssi_root_env_override():
    """SSI_ROOT env var overrides the embedded default."""
    with patch.dict(os.environ, {"SSI_ROOT": "/custom/ssi/path"}):
        bridge = CRGBridge()
        assert bridge._ssi_root() == "/custom/ssi/path"


def test_ssi_root_scripts_exist():
    """Embedded SSI root contains a scripts/ subdirectory."""
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("SSI_ROOT", None)
        bridge = CRGBridge()
        scripts_dir = Path(bridge._ssi_root()) / "scripts"
    assert scripts_dir.exists(), f"scripts/ not found at {scripts_dir}"
```

- [ ] **Step 2: Run test — expect FAIL**

```bash
pytest tests/test_crg_bridge.py::test_ssi_root_default_points_to_embedded -v
```

Expected: FAIL — `_ssi_root()` returns `"software_self_improvement"` not the embedded path.

- [ ] **Step 3: Update crg_bridge._ssi_root()**

In `harness/crg_bridge.py`, replace:

```python
def _ssi_root(self) -> str:
    """Resolve the SSI toolchain root directory."""
    return os.environ.get("SSI_ROOT", "software_self_improvement")
```

With:

```python
def _ssi_root(self) -> str:
    """Resolve the SSI toolchain root directory (embedded at harness/ssi/)."""
    ssi_env = os.environ.get("SSI_ROOT")
    if ssi_env:
        return ssi_env
    # Default: harness/ssi/ embedded within this package
    return str(Path(__file__).parent / "ssi")
```

- [ ] **Step 4: Run test — expect PASS**

```bash
pytest tests/test_crg_bridge.py -v
```

Expected: all 3 tests PASS

- [ ] **Step 5: Run existing tests to check no regressions**

```bash
pytest tests/ -v --tb=short -q
```

Expected: same pass/fail ratio as before (crg_bridge tests may not have existed before)

- [ ] **Step 6: Commit**

```bash
git add harness/crg_bridge.py tests/test_crg_bridge.py
git commit -m "fix: crg_bridge._ssi_root() points to embedded harness/ssi/ by default"
```

---

## Task 3: Add GateContext dataclass and prepare_gate() to harness_bridge.py

**Files:**
- Modify: `harness/harness_bridge.py`
- Modify: `tests/test_harness_bridge.py`

- [ ] **Step 1: Write failing tests for GateContext and prepare_gate()**

Add to `tests/test_harness_bridge.py`:

```python
class TestGateContext:
    """Tests for GateContext dataclass."""

    def test_gate_context_importable(self):
        from harness.harness_bridge import GateContext
        assert GateContext is not None

    def test_gate_context_has_required_fields(self):
        from harness.harness_bridge import GateContext
        ctx = GateContext(
            gate_num=2,
            config={"gate": 2, "score_gate": 75},
            project_root="/tmp/proj",
            phase=3,
            fr_id=None,
            ssi_scripts_dir="/harness/ssi/scripts",
            ssi_prompts_dir="/harness/ssi/prompts",
            ssi_schemas_dir="/harness/ssi/schemas",
            work_dir="/tmp/proj/.sessi-work",
        )
        assert ctx.gate_num == 2
        assert ctx.phase == 3
        assert ctx.fr_id is None

    def test_gate_context_evaluation_prompt(self):
        from harness.harness_bridge import GateContext
        ctx = GateContext(
            gate_num=2,
            config={"gate": 2, "score_gate": 75, "dimensions": [{"name": "linting"}]},
            project_root="/tmp/proj",
            phase=3,
            fr_id="FR-01",
            ssi_scripts_dir="/harness/ssi/scripts",
            ssi_prompts_dir="/harness/ssi/prompts",
            ssi_schemas_dir="/harness/ssi/schemas",
            work_dir="/tmp/proj/.sessi-work",
        )
        prompt = ctx.evaluation_prompt()
        assert "Gate 2" in prompt
        assert "linting" in prompt
        assert "evaluate_dimension.md" in prompt


class TestPreparGate:
    """Tests for HarnessBridge.prepare_gate()."""

    def test_prepare_gate_returns_gate_context(self):
        from harness.harness_bridge import HarnessBridge, GateContext
        bridge = HarnessBridge()
        with patch.object(bridge, "_load_config", return_value={"gate": 2, "score_gate": 75, "dimensions": []}):
            with patch.object(bridge.crg, "run_reconnaissance"):
                ctx = bridge.prepare_gate(gate_num=2, project_root=".", phase=3)
        assert isinstance(ctx, GateContext)
        assert ctx.gate_num == 2
        assert ctx.phase == 3
        assert "ssi" in ctx.ssi_scripts_dir

    def test_prepare_gate_runs_crg_recon_when_configured(self):
        from harness.harness_bridge import HarnessBridge
        bridge = HarnessBridge()
        config = {"gate": 3, "score_gate": 80, "dimensions": [],
                  "crg": {"reconnaissance": True}}
        with patch.object(bridge, "_load_config", return_value=config):
            with patch.object(bridge.crg, "run_reconnaissance") as mock_recon:
                bridge.prepare_gate(gate_num=3, project_root="/tmp/proj", phase=4)
        mock_recon.assert_called_once_with("/tmp/proj")

    def test_prepare_gate_skips_crg_recon_when_not_configured(self):
        from harness.harness_bridge import HarnessBridge
        bridge = HarnessBridge()
        config = {"gate": 2, "score_gate": 75, "dimensions": []}
        with patch.object(bridge, "_load_config", return_value=config):
            with patch.object(bridge.crg, "run_reconnaissance") as mock_recon:
                bridge.prepare_gate(gate_num=2, project_root=".", phase=3)
        mock_recon.assert_not_called()

    def test_prepare_gate_fr_id_propagated(self):
        from harness.harness_bridge import HarnessBridge, GateContext
        bridge = HarnessBridge()
        with patch.object(bridge, "_load_config", return_value={"gate": 1, "dimensions": []}):
            with patch.object(bridge.crg, "run_reconnaissance"):
                ctx = bridge.prepare_gate(gate_num=1, project_root=".", phase=3, fr_id="FR-05")
        assert ctx.fr_id == "FR-05"
```

- [ ] **Step 2: Run tests — expect FAIL**

```bash
pytest tests/test_harness_bridge.py::TestGateContext tests/test_harness_bridge.py::TestPreparGate -v
```

Expected: ImportError or AttributeError — `GateContext` and `prepare_gate` don't exist yet.

- [ ] **Step 3: Add GateContext dataclass to harness_bridge.py**

In `harness/harness_bridge.py`, after the `GateBlockedError` class (after line 51), add:

```python
@dataclass
class GateContext:
    """Context returned by prepare_gate() for Claude to start evaluation."""
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
        """Human-readable prompt for Claude to start gate evaluation."""
        dims = [d["name"] for d in self.config.get("dimensions", [])]
        return (
            f"Gate {self.gate_num} evaluation ready.\n"
            f"  project   : {self.project_root}\n"
            f"  phase     : {self.phase}\n"
            f"  fr_id     : {self.fr_id or 'n/a'}\n"
            f"  dimensions: {', '.join(dims)}\n"
            f"  score_gate: {self.config.get('score_gate', 'n/a')}\n"
            f"  max_rounds: {self.config.get('max_rounds', 3)}\n"
            f"\nFollow  : {self.ssi_prompts_dir}/evaluate_dimension.md\n"
            f"Scripts : {self.ssi_scripts_dir}/\n"
            f"Write result to: {self.work_dir}/gate{self.gate_num}_result.json\n"
        )
```

- [ ] **Step 4: Add prepare_gate() method to HarnessBridge**

In `harness/harness_bridge.py`, inside class `HarnessBridge`, add after `run_gate()`:

```python
def prepare_gate(
    self,
    gate_num: int,
    project_root: str,
    phase: int,
    fr_id: str | None = None,
) -> GateContext:
    """
    Prepare a quality gate for Claude evaluation.

    Runs CRG reconnaissance (if configured for this gate) and returns a
    GateContext containing all paths and config Claude needs to execute
    the evaluation loop defined in harness/ssi/prompts/evaluate_dimension.md.

    Args:
        gate_num: The gate ID (1-4).
        project_root: Absolute path to the target project.
        phase: Current methodology phase.
        fr_id: Optional Functional Requirement ID (Gate 1 only).

    Returns:
        GateContext with config, SSI paths, and work directory.
    """
    config = self._load_config(gate_num)

    # §6.5 Point 1 — CRG Reconnaissance at Gate 3/4 entry
    if config.get("crg", {}).get("reconnaissance"):
        self.crg.run_reconnaissance(project_root)

    ssi_dir = Path(__file__).parent / "ssi"
    return GateContext(
        gate_num=gate_num,
        config=config,
        project_root=project_root,
        phase=phase,
        fr_id=fr_id,
        ssi_scripts_dir=str(ssi_dir / "scripts"),
        ssi_prompts_dir=str(ssi_dir / "prompts"),
        ssi_schemas_dir=str(ssi_dir / "schemas"),
        work_dir=str(Path(project_root) / ".sessi-work"),
    )
```

- [ ] **Step 5: Run tests — expect PASS**

```bash
pytest tests/test_harness_bridge.py::TestGateContext tests/test_harness_bridge.py::TestPreparGate -v
```

Expected: all 7 tests PASS

- [ ] **Step 6: Commit**

```bash
git add harness/harness_bridge.py tests/test_harness_bridge.py
git commit -m "feat: add GateContext dataclass and prepare_gate() to HarnessBridge"
```

---

## Task 4: Add finalize_gate() to harness_bridge.py

**Files:**
- Modify: `harness/harness_bridge.py`
- Modify: `tests/test_harness_bridge.py`

- [ ] **Step 1: Write failing tests for finalize_gate()**

Add to `tests/test_harness_bridge.py`:

```python
class TestFinalizeGate:
    """Tests for HarnessBridge.finalize_gate()."""

    def _write_result(self, tmp_path: Path, gate_num: int, payload: dict) -> None:
        work_dir = tmp_path / ".sessi-work"
        work_dir.mkdir(parents=True, exist_ok=True)
        (work_dir / f"gate{gate_num}_result.json").write_text(
            json.dumps(payload), encoding="utf-8"
        )

    def test_finalize_gate_returns_gate_result(self, tmp_path):
        from harness.harness_bridge import HarnessBridge, GateResult
        bridge = HarnessBridge()
        self._write_result(tmp_path, 2, {
            "score": 82.0, "quality_complete": True, "rounds_used": 2,
            "open_critical": 0, "open_high": 0,
            "dimensions": [{"name": "linting", "score": 92.0, "threshold": 90.0}],
        })
        with patch.object(bridge, "_load_config", return_value={"gate": 2, "score_gate": 75}), \
             patch.object(bridge, "_update_quality_manifest"), \
             patch.object(bridge, "_log"), \
             patch.object(bridge._effort, "record"):
            result = bridge.finalize_gate(
                gate_num=2, project_root=str(tmp_path), phase=3
            )
        assert isinstance(result, GateResult)
        assert result.score == 82.0
        assert result.quality_complete is True
        assert result.rounds_used == 2

    def test_finalize_gate_raises_runtime_error_when_result_missing(self, tmp_path):
        from harness.harness_bridge import HarnessBridge
        bridge = HarnessBridge()
        with patch.object(bridge, "_load_config", return_value={"gate": 2, "score_gate": 75}):
            with pytest.raises(RuntimeError, match="Gate 2 result not found"):
                bridge.finalize_gate(
                    gate_num=2, project_root=str(tmp_path), phase=3
                )

    def test_finalize_gate_raises_blocked_error_when_score_below_threshold(self, tmp_path):
        from harness.harness_bridge import HarnessBridge, GateBlockedError
        bridge = HarnessBridge()
        self._write_result(tmp_path, 2, {
            "score": 60.0, "quality_complete": True, "rounds_used": 3,
            "open_critical": 0, "open_high": 0, "dimensions": [],
        })
        with patch.object(bridge, "_load_config", return_value={"gate": 2, "score_gate": 75}), \
             patch.object(bridge, "_update_quality_manifest"), \
             patch.object(bridge, "_log"), \
             patch.object(bridge._effort, "record"):
            with pytest.raises(GateBlockedError, match="Gate 2 BLOCKED"):
                bridge.finalize_gate(
                    gate_num=2, project_root=str(tmp_path), phase=3
                )

    def test_finalize_gate_raises_blocked_when_not_quality_complete(self, tmp_path):
        from harness.harness_bridge import HarnessBridge, GateBlockedError
        bridge = HarnessBridge()
        self._write_result(tmp_path, 2, {
            "score": 80.0, "quality_complete": False, "rounds_used": 3,
            "open_critical": 1, "open_high": 2, "dimensions": [],
        })
        with patch.object(bridge, "_load_config", return_value={"gate": 2, "score_gate": 75}), \
             patch.object(bridge, "_update_quality_manifest"), \
             patch.object(bridge, "_log"), \
             patch.object(bridge._effort, "record"):
            with pytest.raises(GateBlockedError):
                bridge.finalize_gate(
                    gate_num=2, project_root=str(tmp_path), phase=3
                )

    def test_finalize_gate_1_raises_blocked_on_dim_threshold_miss(self, tmp_path):
        from harness.harness_bridge import HarnessBridge, GateBlockedError
        bridge = HarnessBridge()
        self._write_result(tmp_path, 1, {
            "score": 88.0, "quality_complete": True, "rounds_used": 1,
            "open_critical": 0, "open_high": 0,
            "dimensions": [
                {"name": "linting", "score": 92.0, "threshold": 90.0},
                {"name": "test_coverage", "score": 55.0, "threshold": 80.0},  # fails
            ],
        })
        with patch.object(bridge, "_load_config", return_value={"gate": 1}), \
             patch.object(bridge, "_update_quality_manifest"), \
             patch.object(bridge, "_log"), \
             patch.object(bridge._effort, "record"):
            with pytest.raises(GateBlockedError):
                bridge.finalize_gate(
                    gate_num=1, project_root=str(tmp_path), phase=3, fr_id="FR-01"
                )

    def test_finalize_gate_4_calls_hermes_approve(self, tmp_path):
        from harness.harness_bridge import HarnessBridge
        bridge = HarnessBridge()
        self._write_result(tmp_path, 4, {
            "score": 88.0, "quality_complete": True, "rounds_used": 2,
            "open_critical": 0, "open_high": 0, "dimensions": [],
        })
        with patch.object(bridge, "_load_config", return_value={"gate": 4, "score_gate": 85}), \
             patch.object(bridge, "_update_quality_manifest"), \
             patch.object(bridge, "_log"), \
             patch.object(bridge._effort, "record"), \
             patch.object(bridge, "_require_hermes_approve") as mock_hermes:
            bridge.finalize_gate(gate_num=4, project_root=str(tmp_path), phase=6)
        mock_hermes.assert_called_once()

    def test_finalize_gate_updates_manifest(self, tmp_path):
        from harness.harness_bridge import HarnessBridge
        bridge = HarnessBridge()
        self._write_result(tmp_path, 2, {
            "score": 80.0, "quality_complete": True, "rounds_used": 2,
            "open_critical": 0, "open_high": 0, "dimensions": [],
        })
        with patch.object(bridge, "_load_config", return_value={"gate": 2, "score_gate": 75}), \
             patch.object(bridge, "_update_quality_manifest") as mock_update, \
             patch.object(bridge, "_log"), \
             patch.object(bridge._effort, "record"):
            bridge.finalize_gate(gate_num=2, project_root=str(tmp_path), phase=3)
        mock_update.assert_called_once()
        call_kwargs = mock_update.call_args
        assert call_kwargs[0][0] == 2   # gate_num
```

- [ ] **Step 2: Run tests — expect FAIL**

```bash
pytest tests/test_harness_bridge.py::TestFinalizeGate -v
```

Expected: AttributeError — `finalize_gate` method does not exist yet.

- [ ] **Step 3: Implement finalize_gate() in harness_bridge.py**

In `harness/harness_bridge.py`, inside class `HarnessBridge`, add after `prepare_gate()`:

```python
def finalize_gate(
    self,
    gate_num: int,
    project_root: str,
    phase: int,
    fr_id: str | None = None,
) -> GateResult:
    """
    Finalize a quality gate after Claude has completed evaluation.

    Reads the result written by Claude to .sessi-work/gate{N}_result.json,
    updates the quality manifest, logs the decision, and checks thresholds.

    Args:
        gate_num: The gate ID (1-4).
        project_root: Absolute path to the target project.
        phase: Current methodology phase.
        fr_id: Optional FR ID (Gate 1 only).

    Returns:
        GateResult if all thresholds are met.

    Raises:
        RuntimeError: If result file is missing (evaluation not yet completed).
        GateBlockedError: If quality thresholds are not met.
    """
    t0 = time.time()
    config = self._load_config(gate_num)

    # Read result written by Claude's evaluation loop
    result_path = Path(project_root) / ".sessi-work" / f"gate{gate_num}_result.json"
    if not result_path.exists():
        raise RuntimeError(
            f"Gate {gate_num} result not found at {result_path}. "
            f"Complete evaluation first — follow "
            f"harness/ssi/prompts/evaluate_dimension.md and write "
            f"output to {result_path}"
        )

    raw = json.loads(result_path.read_text(encoding="utf-8"))
    dims = [
        DimResult(
            name=d["name"], score=d["score"],
            threshold=d["threshold"], issues=d.get("issues", []),
        )
        for d in raw.get("dimensions", [])
    ]
    result = GateResult(
        gate_num=gate_num,
        score=raw["score"],
        dimensions=dims,
        open_critical=raw.get("open_critical", raw.get("open_critical_count", 0)),
        open_high=raw.get("open_high", raw.get("open_high_count", 0)),
        quality_complete=raw.get("quality_complete", False),
        rounds_used=raw.get("rounds_used", 0),
    )

    self._update_quality_manifest(gate_num, fr_id, result)

    self._effort.record(EffortRecord(
        phase=phase, gate_num=gate_num, agent_id="GATE",
        operation="gate_finalize", duration_s=time.time() - t0,
    ))
    self._log.write(DecisionLogEntry(
        ctx=DecisionContext(agent_id="GATE", phase=phase, fr_id=fr_id),
        decision="GATE_PASS" if result.quality_complete else "GATE_BLOCK",
        reasoning=(
            f"Gate {gate_num}: score={result.score:.1f}, "
            f"critical={result.open_critical}, high={result.open_high}, "
            f"rounds={result.rounds_used}"
        ),
        scores={"gate_score": result.score},
    ))

    # Gate 1: per-dim threshold (no composite score_gate)
    if gate_num == 1:
        if any(d.score < d.threshold for d in result.dimensions):
            raise GateBlockedError(gate_num, result)
    else:
        # Gates 2/3/4: composite score < score_gate OR not quality_complete
        if result.score < config.get("score_gate", 0) or not result.quality_complete:
            raise GateBlockedError(gate_num, result)

    # Gate 4: requires explicit Hermes reviewer APPROVE
    if gate_num == 4:
        self._require_hermes_approve(result, phase, fr_id)

    return result
```

- [ ] **Step 4: Run tests — expect PASS**

```bash
pytest tests/test_harness_bridge.py::TestFinalizeGate -v
```

Expected: all 7 tests PASS

- [ ] **Step 5: Run full test suite**

```bash
pytest tests/ -q --tb=short
```

Expected: no regressions introduced

- [ ] **Step 6: Commit**

```bash
git add harness/harness_bridge.py tests/test_harness_bridge.py
git commit -m "feat: add finalize_gate() to HarnessBridge — reads Claude-written result + checks thresholds"
```

---

## Task 5: Deprecate run_gate() and remove _invoke_harness()

**Files:**
- Modify: `harness/harness_bridge.py`
- Modify: `tests/test_harness_bridge.py`

- [ ] **Step 1: Update existing tests that mock _invoke_harness**

In `tests/test_harness_bridge.py`, remove or replace these tests:
- `test_run_gate_raises_blocked_error` — replace with deprecation test
- `test_run_gate_passes_with_good_score` — replace with deprecation test
- `test_run_gate_1_dimension_threshold_fails` — already covered by `TestFinalizeGate`

Replace with:

```python
class TestRunGateDeprecated:
    """run_gate() is deprecated — must raise NotImplementedError."""

    def test_run_gate_raises_not_implemented(self):
        from harness.harness_bridge import HarnessBridge
        bridge = HarnessBridge()
        with pytest.raises(NotImplementedError, match="prepare_gate"):
            bridge.run_gate(gate_num=2, project_root=".", phase=3)

    def test_run_gate_error_mentions_finalize_gate(self):
        from harness.harness_bridge import HarnessBridge
        bridge = HarnessBridge()
        with pytest.raises(NotImplementedError) as exc_info:
            bridge.run_gate(gate_num=1, project_root=".", phase=3, fr_id="FR-01")
        assert "finalize_gate" in str(exc_info.value)
```

- [ ] **Step 2: Run updated tests — expect FAIL**

```bash
pytest tests/test_harness_bridge.py::TestRunGateDeprecated -v
```

Expected: FAIL — `run_gate()` currently exists and does not raise NotImplementedError.

- [ ] **Step 3: Update run_gate() in harness_bridge.py to raise NotImplementedError**

Replace the entire `run_gate()` method body in `harness/harness_bridge.py` with:

```python
def run_gate(
    self,
    gate_num: int,
    project_root: str,
    phase: int,
    fr_id: str | None = None,
    max_rounds_override: int | None = None,
) -> GateResult:
    """
    Deprecated — subprocess model removed.

    Use the two-phase API instead:

        # 1. Prepare (CRG recon + get context)
        context = bridge.prepare_gate(gate_num, project_root, phase, fr_id)

        # 2. Claude evaluates inline following context.ssi_prompts_dir/evaluate_dimension.md
        #    Claude writes result to: {context.work_dir}/gate{N}_result.json

        # 3. Finalize (read result + manifest + threshold check)
        result = bridge.finalize_gate(gate_num, project_root, phase, fr_id)

    CLI equivalents:
        python harness_cli.py prepare-gate --gate N --phase N --project /path
        python harness_cli.py finalize-gate --gate N --phase N --project /path
    """
    raise NotImplementedError(
        "run_gate() subprocess model removed in Option B merge. "
        "Use prepare_gate() + Claude conversation evaluation + finalize_gate(). "
        "See harness/ssi/prompts/evaluate_dimension.md for the evaluation protocol."
    )
```

- [ ] **Step 4: Remove _invoke_harness() method from harness_bridge.py**

Delete the entire `_invoke_harness()` method (lines ~168-219 in original file). It is no longer referenced.

- [ ] **Step 5: Run tests — expect PASS**

```bash
pytest tests/test_harness_bridge.py -v
```

Expected: all tests PASS (old _invoke_harness-based tests replaced, new tests pass)

- [ ] **Step 6: Verify test count is reasonable**

```bash
pytest tests/test_harness_bridge.py -v --collect-only | grep "test session starts" -A 50
```

Expected: no fewer tests than before (old 5 removed, new 7 + 2 added = net +4)

- [ ] **Step 7: Commit**

```bash
git add harness/harness_bridge.py tests/test_harness_bridge.py
git commit -m "refactor: deprecate run_gate() subprocess model; remove _invoke_harness()"
```

---

## Task 6: Update harness_cli.py — add prepare-gate and finalize-gate subcommands

**Files:**
- Modify: `harness_cli.py`

> Note: harness_cli.py does not have a dedicated test file; the CLI is tested via integration in test_gate_remediation.py. Update the NotImplementedError catch block.

- [ ] **Step 1: Update cmd_run_gate() to use prepare_gate()**

In `harness_cli.py`, replace the body of `cmd_run_gate()` (lines ~106-153) with:

```python
def cmd_run_gate(args: argparse.Namespace) -> int:
    """Prepare a quality gate for Claude evaluation (Step 1 of 2)."""
    from harness.harness_bridge import HarnessBridge

    project = str(Path(args.project).resolve())
    bridge = HarnessBridge()
    fr_id = args.fr_id or None

    print(f"\n{'='*60}\nprepare-gate: Gate {args.gate} | Phase {args.phase}\n{'='*60}")

    context = bridge.prepare_gate(
        gate_num=args.gate,
        project_root=project,
        phase=args.phase,
        fr_id=fr_id,
    )
    print(context.evaluation_prompt())

    fr_flag = f" --fr-id {fr_id}" if fr_id else ""
    print(f"After evaluation, finalize with:")
    print(f"  python harness_cli.py finalize-gate --gate {args.gate} "
          f"--phase {args.phase}{fr_flag} --project {args.project}")
    return 0
```

- [ ] **Step 2: Add cmd_finalize_gate() function**

Add after `cmd_run_gate()` in `harness_cli.py`:

```python
# ---------------------------------------------------------------------------
# finalize-gate
# ---------------------------------------------------------------------------

def cmd_finalize_gate(args: argparse.Namespace) -> int:
    """Finalize a quality gate after Claude evaluation (Step 2 of 2)."""
    from harness.harness_bridge import HarnessBridge, GateBlockedError

    project = str(Path(args.project).resolve())
    bridge = HarnessBridge()
    fr_id = args.fr_id or None

    print(f"\n{'='*60}\nfinalize-gate: Gate {args.gate} | Phase {args.phase}\n{'='*60}")

    try:
        result = bridge.finalize_gate(
            gate_num=args.gate,
            project_root=project,
            phase=args.phase,
            fr_id=fr_id,
        )
        print(f"\nGATE {args.gate} PASSED")
        print(f"  score           : {result.score:.1f}")
        print(f"  quality_complete: {result.quality_complete}")
        print(f"  rounds_used     : {result.rounds_used}")
        print(f"  open_critical   : {result.open_critical}")
        print(f"  open_high       : {result.open_high}")
        git = _make_git(args, Path(args.project).resolve())
        git.ensure_gitignore()
        if args.gate == 1:
            git.commit_fr_gate1(fr_id or "unknown", result.score, args.phase)
        else:
            git.commit_and_push_gate(args.gate, args.phase, result.score)
        return 0

    except GateBlockedError as e:
        project_path = Path(args.project).resolve()
        print(_format_block_diagnostic(
            e, args.gate, args.phase, fr_id, 3, project_path,
        ))
        return 1

    except RuntimeError as e:
        print(f"\n[RUNTIME ERROR] {e}")
        print("  Ensure Claude has completed evaluation and written the result file.")
        return 2
```

- [ ] **Step 3: Add finalize-gate to build_parser()**

In `harness_cli.py`, inside `build_parser()`, add after the `run-gate` parser block:

```python
    # finalize-gate
    fg = sub.add_parser(
        "finalize-gate",
        help="Finalize a quality gate after Claude evaluation (Step 2 of 2)",
    )
    fg.add_argument("--gate",    type=int, required=True, choices=[1, 2, 3, 4])
    fg.add_argument("--phase",   type=int, required=True, help="Current phase number")
    fg.add_argument("--project", default=".", help="Project root (default: .)")
    fg.add_argument("--fr-id",   default=None, help="FR ID (Gate 1 only)")
    fg.add_argument("--no-git",  action="store_true", dest="no_git",
                    help="Disable git commit/push after gate pass")
    fg.set_defaults(func=cmd_finalize_gate)
```

- [x] **Step 4: Update run-pipeline gate calls to use prepare/finalize flow** _(run-pipeline later removed in v2.5 — only prepare/finalize flow remains)_

In `cmd_run_pipeline()`, replace Gate 1 per-FR block (find the block that calls `bridge.run_gate(gate_num=1, ...)`):

```python
        # ── Step 3: Per-FR Gate 1 ─────────────────────────────────────────
        if phase in _PER_FR_GATE1_PHASES:
            if not fr_ids:
                print(f"[ERROR] No FR IDs in manifest — cannot run Gate 1 for phase {phase}.")
                return 1
            print(f"\n[{phase}.3] Gate 1 for {len(fr_ids)} FR(s): {fr_ids}")
            for fr_id in fr_ids:
                print(f"  [{fr_id}] Gate 1 — prepare …")
                ctx = bridge.prepare_gate(
                    gate_num=1, project_root=str(project),
                    phase=phase, fr_id=fr_id,
                )
                print(ctx.evaluation_prompt())
                result_file = Path(project) / ".sessi-work" / "gate1_result.json"
                if not result_file.exists():
                    print(f"\n[PAUSE] Evaluate Gate 1/{fr_id}, then re-run pipeline:")
                    print(f"  python harness_cli.py finalize-gate --gate 1 --phase {phase}"
                          f" --fr-id {fr_id} --project {project} --no-git")
                    print(f"  python harness_cli.py run-pipeline --phase-from {phase}"
                          f" --project {project}")  # Note: run-pipeline removed in v2.5
                    return 10
                try:
                    g1_result = bridge.finalize_gate(
                        gate_num=1, project_root=str(project),
                        phase=phase, fr_id=fr_id,
                    )
                    print(f"  [{fr_id}] Gate 1 PASSED  score={g1_result.score:.1f}")
                    git.commit_fr_gate1(fr_id, g1_result.score, phase)
                except GateBlockedError as exc:
                    print(f"  [{fr_id}] Gate 1 BLOCKED")
                    print(_format_block_diagnostic(exc, 1, phase, fr_id, 3, project))
                    return 10
                except RuntimeError as exc:
                    print(f"\n[ERROR] Gate 1 / {fr_id}: {exc}")
                    return 1
```

Replace phase exit gate block (find the block calling `bridge.run_gate(gate_num=gate_num, ...)`):

```python
        # ── Step 4: Phase exit gate ───────────────────────────────────────
        if phase in _PHASE_EXIT_GATES:
            gate_num = _PHASE_EXIT_GATES[phase]
            print(f"\n[{phase}.4] Gate {gate_num} (phase exit) — prepare …")
            ctx = bridge.prepare_gate(
                gate_num=gate_num, project_root=str(project),
                phase=phase, fr_id=None,
            )
            print(ctx.evaluation_prompt())
            result_file = Path(project) / ".sessi-work" / f"gate{gate_num}_result.json"
            if not result_file.exists():
                print(f"\n[PAUSE] Evaluate Gate {gate_num}, then re-run pipeline:")
                print(f"  python harness_cli.py finalize-gate --gate {gate_num}"
                      f" --phase {phase} --project {project} --no-git")
                print(f"  python harness_cli.py run-pipeline --phase-from {phase}"
                      f" --project {project}")  # Note: run-pipeline removed in v2.5
                return 10
            try:
                result = bridge.finalize_gate(
                    gate_num=gate_num, project_root=str(project),
                    phase=phase, fr_id=None,
                )
                print(f"[{phase}.4] Gate {gate_num} PASSED  score={result.score:.1f}")
                git.commit_and_push_gate(gate_num, phase, result.score, n_frs=len(fr_ids))
            except GateBlockedError as exc:
                print("BLOCKED")
                print(_format_block_diagnostic(exc, gate_num, phase, None, 3, project))
                return 10
            except RuntimeError as exc:
                print(f"\n[ERROR] Gate {gate_num}: {exc}")
                return 1
```

- [ ] **Step 5: Remove unused NotImplementedError catch block in cmd_run_gate**

The old `cmd_run_gate` had a `except NotImplementedError` block. Since we removed it, verify the new function doesn't reference it. Review the updated function.

- [ ] **Step 6: Remove unused GateBlockedError TYPE_CHECKING import if needed**

At top of harness_cli.py, the `TYPE_CHECKING` block imports `GateBlockedError`. Now it's used directly in `cmd_finalize_gate`. Ensure the import is a real import (not TYPE_CHECKING only):

```python
# At top of cmd_finalize_gate (already inside the function):
from harness.harness_bridge import HarnessBridge, GateBlockedError
```

This is a local import inside the function — no change needed to the top-level TYPE_CHECKING block.

- [ ] **Step 7: Run smoke test on CLI**

```bash
cd /Users/johnny/harness-methodology
python harness_cli.py --help
python harness_cli.py finalize-gate --help
python harness_cli.py run-gate --help
```

Expected: no errors, finalize-gate subcommand listed

- [ ] **Step 8: Run full test suite**

```bash
pytest tests/ -q --tb=short
```

Expected: no regressions

- [ ] **Step 9: Commit**

```bash
git add harness_cli.py
git commit -m "feat: harness_cli adds finalize-gate subcommand; run-gate uses prepare_gate()"
```

---

## Task 6b: Add generate-next-plan subcommand to harness_cli.py

**Goal:** State-aware tactical plan generator. After each GitHub push (checkpoint), the main agent calls `generate-next-plan` to get the exact next steps. Replaces static `plan-phase` for tactical execution.

**Relationship with plan-phase:**
- `plan-phase` = phase overview blueprint (FR list, rules, global context) — keep as-is
- `generate-next-plan` = tactical next steps (reads manifest + state → emits exact checklist for main agent to 100% follow)

**Files:**
- Modify: `harness_cli.py`
- No new tests (generator output is markdown text; tested via smoke run)

- [ ] **Step 1: Add cmd_generate_next_plan() function**

Add after `cmd_run_phase()` in `harness_cli.py`:

```python
# ---------------------------------------------------------------------------
# generate-next-plan
# ---------------------------------------------------------------------------

def cmd_generate_next_plan(args: argparse.Namespace) -> int:
    """
    Generate a state-aware tactical plan for the next actions in a phase.

    Reads .methodology/quality_manifest.json + .sessi-work/ to determine
    what has been completed, then emits a concrete checklist plan the main
    agent can follow 100% without further reasoning.

    Workflow:
        push to GitHub (checkpoint)
        → python harness_cli.py generate-next-plan --phase N --project .
        → agent follows emitted plan
        → push → generate-next-plan again
    """
    project = Path(args.project).resolve()
    phase = args.phase

    manifest_path = project / ".methodology" / "quality_manifest.json"
    if not manifest_path.exists():
        print("[ERROR] quality_manifest.json not found. Run 'harness_cli.py manifest' first.")
        return 1

    import json as _json
    manifest = _json.loads(manifest_path.read_text(encoding="utf-8"))
    fr_ids: list[str] = manifest.get("fr_ids", [])
    gate_results: dict = manifest.get("gate_results", {})

    # Gate 1 per-FR completion: gate_results["gate1"] is a dict {fr_id: {...}}
    g1_results: dict = gate_results.get("gate1", {}) or {}
    completed_g1_frs = set(g1_results.keys()) if isinstance(g1_results, dict) else set()

    # Determine phase exit gate
    _exit_gates: dict[int, int] = {3: 2, 4: 3, 6: 4}
    exit_gate_num = _exit_gates.get(phase)

    lines: list[str] = []
    lines.append(f"# Next Plan — Phase {phase} | {project.name}")
    lines.append(f"\n> Generated: {__import__('datetime').datetime.now().isoformat()[:19]}")
    lines.append(f"> State source: `.methodology/quality_manifest.json`\n")

    # ── Find next incomplete FR ───────────────────────────────────────────
    next_fr = next((fr for fr in fr_ids if fr not in completed_g1_frs), None)

    if next_fr:
        g1_cfg = _load_gate1_dims(project)
        lines += [
            f"## Next: {next_fr} — Implement + Gate 1",
            "",
            "### 1. Implement (TDD)",
            f"- [ ] Write failing test for {next_fr}",
            f"- [ ] Implement {next_fr} until test passes",
            f"- [ ] Refactor (keep tests green)",
            f"- [ ] `git commit -m 'feat: {next_fr} implementation'`",
            "",
            "### 2. Gate 1 — Prepare",
            f"```bash",
            f"python harness_cli.py run-gate --gate 1 --phase {phase} "
            f"--fr-id {next_fr} --project {project} --no-git",
            f"```",
            "",
            "### 3. Gate 1 — Evaluate (inline)",
            f"- [ ] Follow `harness/ssi/prompts/evaluate_dimension.md`",
        ]
        for dim in g1_cfg:
            lines.append(f"  - [ ] {dim['name']} (threshold: {dim['threshold']})")
        lines += [
            f"- [ ] Write `.sessi-work/gate1_result.json`",
            "",
            "### 4. Gate 1 — Finalize",
            f"```bash",
            f"python harness_cli.py finalize-gate --gate 1 --phase {phase} "
            f"--fr-id {next_fr} --project {project}",
            f"```",
            "",
            "### 5. Checkpoint",
            f"```bash",
            f"git push",
            f"python harness_cli.py generate-next-plan --phase {phase} --project {project}",
            f"```",
        ]

    # ── All FRs done — check exit gate ───────────────────────────────────
    elif exit_gate_num is not None:
        exit_g_result = gate_results.get(f"gate{exit_gate_num}")
        if exit_g_result is None or (isinstance(exit_g_result, dict) and not exit_g_result.get("quality_complete")):
            g_cfg = _load_gate_dims(project, exit_gate_num)
            lines += [
                f"## Next: Gate {exit_gate_num} — Phase {phase} Exit",
                "",
                "### 1. Prepare",
                f"```bash",
                f"python harness_cli.py run-gate --gate {exit_gate_num} "
                f"--phase {phase} --project {project} --no-git",
                f"```",
                "",
                "### 2. Evaluate (inline)",
                f"- [ ] Follow `harness/ssi/prompts/evaluate_dimension.md`",
            ]
            for dim in g_cfg:
                lines.append(
                    f"  - [ ] {dim['name']} "
                    f"(threshold: {dim['threshold']}, score_gate: {dim.get('score_gate', '?')})"
                )
            lines += [
                f"- [ ] Write `.sessi-work/gate{exit_gate_num}_result.json`",
                "",
                "### 3. Finalize",
                f"```bash",
                f"python harness_cli.py finalize-gate --gate {exit_gate_num} "
                f"--phase {phase} --project {project}",
                f"```",
                "",
                "### 4. Checkpoint",
                f"```bash",
                f"git push",
                f"```",
                "",
                f"> Phase {phase} complete after Gate {exit_gate_num} passes.",
            ]
        else:
            lines.append(f"\n✅ Phase {phase} fully complete — all FRs done, Gate {exit_gate_num} passed.")
            lines.append(f"   Next: `python harness_cli.py run-pipeline --phase-from {phase + 1} --project {project}`")  # Note: run-pipeline removed in v2.5 — use advance-phase --completed N

    else:
        # Phase has no exit gate (P1, P2, P7, P8)
        if all(fr in completed_g1_frs for fr in fr_ids) or not fr_ids:
            lines.append(f"\n✅ Phase {phase} complete.")
        else:
            lines.append(f"\n[WARN] Phase {phase} has incomplete FRs but no standard exit gate.")

    plan_text = "\n".join(lines)
    print(plan_text)

    # Optionally write to file
    if args.output:
        Path(args.output).write_text(plan_text + "\n", encoding="utf-8")
        print(f"\n[Written → {args.output}]")

    return 0


def _load_gate1_dims(project: Path) -> list[dict]:
    """Load Gate 1 dimension list from gate config."""
    try:
        import yaml
        cfg_path = Path(__file__).parent / "harness" / "gate_configs" / "gate1_per_fr.yaml"
        cfg = yaml.safe_load(cfg_path.read_text())
        return [{"name": d["name"], "threshold": d["threshold"]} for d in cfg.get("dimensions", [])]
    except Exception:
        return [{"name": "linting", "threshold": 90}, {"name": "type_safety", "threshold": 85},
                {"name": "test_coverage", "threshold": 80}]


def _load_gate_dims(project: Path, gate_num: int) -> list[dict]:
    """Load dimension list for composite gates."""
    try:
        import yaml
        names = {2: "gate2_p3_exit.yaml", 3: "gate3_p4_exit.yaml", 4: "gate4_p6_full.yaml"}
        cfg_path = Path(__file__).parent / "harness" / "gate_configs" / names[gate_num]
        cfg = yaml.safe_load(cfg_path.read_text())
        score_gate = cfg.get("score_gate", "?")
        return [
            {"name": d["name"], "threshold": d["threshold"], "score_gate": score_gate}
            for d in cfg.get("dimensions", [])
        ]
    except Exception:
        return []
```

- [ ] **Step 2: Add generate-next-plan to build_parser()**

In `build_parser()`, after the `run-gate` parser block, add:

```python
    # generate-next-plan
    gnp = sub.add_parser(
        "generate-next-plan",
        help="Generate state-aware next-step plan (call after each git push checkpoint)",
    )
    gnp.add_argument("--phase",   type=int, required=True, help="Current phase number")
    gnp.add_argument("--project", default=".", help="Project root (default: .)")
    gnp.add_argument("--output",  default=None,
                     help="Write plan to file (default: stdout only)")
    gnp.set_defaults(func=cmd_generate_next_plan)
```

- [ ] **Step 3: Smoke test**

```bash
# Create a minimal test manifest
mkdir -p /tmp/test_proj/.methodology
cat > /tmp/test_proj/.methodology/quality_manifest.json << 'EOF'
{
  "fr_ids": ["FR-01", "FR-02"],
  "gate_results": {"gate1": {"FR-01": {"score": 90, "quality_complete": true}}, "gate2": null}
}
EOF

python harness_cli.py generate-next-plan --phase 3 --project /tmp/test_proj
```

Expected output: plan showing "Next: FR-02 — Implement + Gate 1" with exact CLI commands.

- [ ] **Step 4: Commit**

```bash
git add harness_cli.py
git commit -m "feat: harness_cli generate-next-plan — state-aware tactical plan after each push checkpoint"
```

---

## Task 7: Update SKILL.md — add §12 Gate Evaluation Protocol

**Files:**
- Modify: `SKILL.md`

- [ ] **Step 1: Add Gate Evaluation Protocol section**

Append to `SKILL.md` before the final footer line:

```markdown
---

## 12. Gate Evaluation Protocol (SSI Inline)

Gates execute as Claude conversation steps — **not as subprocesses**.
Claude reads the gate config YAML and follows `harness/ssi/prompts/evaluate_dimension.md`.

### Two-Phase CLI Flow

```bash
# Phase 1 — Prepare (CRG recon + print context)
python harness_cli.py run-gate --gate <N> --phase <P> --project /path

# Claude evaluates inline following harness/ssi/prompts/evaluate_dimension.md
# Claude calls scripts as Bash tools during evaluation:
#   python3 harness/ssi/scripts/score.py .sessi-work/round_<n> config.json
#   python3 harness/ssi/scripts/issue_tracker.py add .sessi-work/issue_registry.json ...
#   python3 harness/ssi/scripts/checkpoint.py round <n> scores.json <score>

# Phase 2 — Finalize (read result + manifest + threshold check)
python harness_cli.py finalize-gate --gate <N> --phase <P> --project /path
```

### Evaluation Loop (4 steps per round, max 3 rounds)

| Step | Action | Script |
|------|--------|--------|
| 3a Evaluate | Per-dim: tool score + LLM reasoning | Bash tools + evaluate_dimension.md |
| 3b Score | Weighted aggregate: min(tool, llm) | `score.py` |
| 3c Verify | Anti-bias: compare pre/post diffs | `verify.py` |
| 3d Checkpoint | Snapshot round + issue registry | `checkpoint.py` |
| 3e Early-stop | Pass OR plateau OR max_rounds | issue_tracker.py saturation check |
| 3f Improve | Fix open critical → high → medium | improvement_plan.md |

### Result File Contract

Claude writes evaluation result to `.sessi-work/gate{N}_result.json`:

```json
{
  "score": 82.5,
  "quality_complete": true,
  "rounds_used": 2,
  "open_critical": 0,
  "open_high": 0,
  "dimensions": [
    {"name": "linting", "score": 94.0, "threshold": 90.0, "issues": []},
    {"name": "type_safety", "score": 88.0, "threshold": 85.0, "issues": []}
  ]
}
```

### SSI Assets Location (embedded)

```
harness/ssi/
├── scripts/         ← Claude calls as Bash tools
│   ├── config_loader.py
│   ├── score.py
│   ├── issue_tracker.py
│   ├── checkpoint.py
│   ├── verify.py
│   ├── verify_tools.py
│   ├── crg_analysis.py
│   ├── crg_integration.py
│   ├── report_gen.py
│   └── ...
├── prompts/         ← Claude reads and follows
│   ├── evaluate_dimension.md
│   ├── improvement_plan.md
│   ├── verify_round.md
│   ├── final_report.md
│   └── crg_reconnaissance.md
└── schemas/
    └── harness_gate_result.schema.json
```
```

- [ ] **Step 2: Verify SKILL.md renders correctly**

```bash
wc -l SKILL.md
grep -n "## 12" SKILL.md
```

Expected: §12 present, file renders without syntax errors

- [ ] **Step 3: Commit**

```bash
git add SKILL.md
git commit -m "docs: SKILL.md §12 Gate Evaluation Protocol — inline Claude execution model"
```

---

## Task 8: Update docs/HARNESS_INTEGRATION.md

**Files:**
- Modify: `docs/HARNESS_INTEGRATION.md`

- [ ] **Step 1: Update Environment Variables table**

Replace the `SSI_ROOT` row to reflect the new default:

```markdown
| `SSI_ROOT` | No | `harness/ssi` | Path to SSI installation. Defaults to embedded `harness/ssi/` in Option B merged repo. |
```

- [ ] **Step 2: Update P3 SOP Changes execution model**

Replace the old subprocess model description in the After block:

```markdown
### After
```
Layer 3 (per-FR): harness_bridge.prepare_gate(gate_num=1, fr_id=FR-XXX, phase=3)
  → Returns GateContext with ssi_scripts_dir, ssi_prompts_dir
  → Claude evaluates inline: 3 dims (linting 90, type_safety 85, test_coverage 80)
  → Claude writes .sessi-work/gate1_result.json
  → harness_bridge.finalize_gate(gate_num=1, ...) → checks per-dim thresholds

POST-FLIGHT (phase exit): harness_bridge.prepare_gate(gate_num=2, phase=3)
  → Returns GateContext with 7 dims, score_gate=75, max_rounds=3
  → Claude evaluates inline, follows harness/ssi/prompts/evaluate_dimension.md
  → Claude writes .sessi-work/gate2_result.json
  → harness_bridge.finalize_gate(gate_num=2, ...) → score < 75 → GateBlockedError
```
```

- [ ] **Step 3: Update P6 SOP Step 6.1**

```markdown
### After
```
Step 6.1: harness_bridge.prepare_gate(gate_num=4, phase=6)
         → GateContext: 12 dims, All Tiers, score_gate=85, max_rounds=3
         → Claude evaluates: CRG recon + tier3 guidance + impact + drift
         → Claude writes .sessi-work/gate4_result.json
         → harness_bridge.finalize_gate(gate_num=4, phase=6)

Step 6.2: AgentSpawner.spawn(role="reviewer", model="hermes", phase=6)
         → Hermes MCP send→wait→read
         → Reviewer persona from agent_personas/REVIEWER.md
         → Prompt: Gate 4 score + dim breakdown + open issues

Exit: Gate 4 score ≥ 85 AND critical_open == 0 AND Hermes APPROVE
```
```

- [ ] **Step 4: Commit**

```bash
git add docs/HARNESS_INTEGRATION.md
git commit -m "docs: HARNESS_INTEGRATION.md updated for inline Claude execution model (Option B)"
```

---

## Task 9: Update SAD.md §SSI Integration

**Files:**
- Modify: `SAD.md`

- [ ] **Step 1: Find and update SSI integration section in SAD.md**

Search for the SSI integration section:

```bash
grep -n "SSI\|software_self_improvement\|subprocess" SAD.md | head -30
```

- [ ] **Step 2: Update SSI integration description**

Find the section describing the subprocess IPC contract and replace with:

```markdown
### SSI Integration (Option B — Embedded)

SSI (`software_self_improvement`) is fully embedded at `harness/ssi/` since Option B merge (2026-05-05).

**Execution model:** Claude conversation-native — no subprocess IPC.
- Claude reads gate config YAML → follows `harness/ssi/prompts/evaluate_dimension.md`
- Python scripts in `harness/ssi/scripts/` are called as Bash tool calls by Claude
- Results written natively to `.sessi-work/gate{N}_result.json`

**API:**
```python
context = bridge.prepare_gate(gate_num=2, project_root="/path", phase=3)
# Claude evaluates inline → writes .sessi-work/gate2_result.json
result  = bridge.finalize_gate(gate_num=2, project_root="/path", phase=3)
```

**Legacy note:** `run_gate()` subprocess model removed. `_invoke_harness()` deleted. `runner.py` in johnnylugm-tech/software_self_improvement repo is deprecated.
```

- [ ] **Step 3: Commit**

```bash
git add SAD.md
git commit -m "docs: SAD.md §SSI integration updated for Option B embedded model"
```

---

## Task 10: Run full test suite and verify coverage

**Files:**
- No changes — verification only

- [ ] **Step 1: Run full test suite**

```bash
cd /Users/johnny/harness-methodology
pytest tests/ -v --tb=short 2>&1 | tail -30
```

Expected: same or higher pass count than before; no new failures

- [ ] **Step 2: Check harness_bridge coverage**

```bash
pytest tests/test_harness_bridge.py tests/test_crg_bridge.py tests/test_ssi_scripts.py \
  --cov=harness --cov-report=term-missing --tb=short
```

Expected:
- `harness/harness_bridge.py` coverage ≥ previous level (was 78% / 26 miss)
- `harness/crg_bridge.py` coverage ≥ 80%
- `harness/ssi/scripts/` not required in coverage (CLI tools, not library code)

- [ ] **Step 3: Verify run_gate NotImplementedError is caught in harness_cli.py**

```bash
python harness_cli.py run-gate --gate 2 --phase 3 --project /tmp/test_proj 2>&1
```

Expected: prints gate context from prepare_gate() (not NotImplementedError stack trace), shows `finalize-gate` instructions

- [ ] **Step 4: Final commit if any fixes needed**

```bash
git add -p  # stage only specific fixes
git commit -m "fix: post-merge test fixes and coverage adjustments"
```

---

## Self-Review

### Spec Coverage

| Design Decision | Task |
|----------------|------|
| SSI scripts/prompts → harness/ssi/ | Task 1 |
| crg_bridge._ssi_root() → embedded default | Task 2 |
| GateContext dataclass | Task 3 |
| prepare_gate() method | Task 3 |
| finalize_gate() method | Task 4 |
| run_gate() deprecated (NotImplementedError) | Task 5 |
| _invoke_harness() removed | Task 5 |
| harness_cli run-gate uses prepare_gate() | Task 6 |
| harness_cli finalize-gate new subcommand | Task 6 |
| ~~run-pipeline uses prepare/finalize~~ _(run-pipeline removed in v2.5)_ | Task 6 |
| SKILL.md §12 gate execution protocol | Task 7 |
| HARNESS_INTEGRATION.md updated | Task 8 |
| SAD.md §SSI updated | Task 9 |

### Type Consistency
- `GateContext` defined in Task 3, used in Task 4 (finalize_gate) and Task 6 (harness_cli) ✓
- `GateResult` unchanged dataclass — finalize_gate returns it ✓
- `GateBlockedError` unchanged — finalize_gate raises it ✓
- `bridge.prepare_gate()` returns `GateContext` in Task 3, harness_cli calls it in Task 6 ✓
- `bridge.finalize_gate()` returns `GateResult` or raises — consistent across Task 4 and Task 6 ✓

### No Placeholders
- All method bodies shown in full ✓
- All test code complete ✓
- No "implement later" ✓

---
