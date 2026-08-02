"""Round 30 站3 — a check that could not run must not read as a check that passed.

This is the seventh appearance of the repo's recurring meta-pattern, and its
sharpest form. Round 29 station 1 fixed four gate-config consumers that resolved
`project_root/harness/gate_configs/` — a path that exists in no consumer project
— and returned `[]` / `(True, [])` when they could not find it. `[]` means "no
violations found", so four anti-fabrication checks had been reporting all-clear
in every consumer run since they were written.

One commit later, 63b9399 put a new abstention into the same file to unbreak
CI (`if os.environ.get("CI"): return True, []`), 877c1bb had to walk half of it
back, and the Round 29 result JSON still shipped `except ValueError: return []`
in two more places.

The four consumers below are where this keeps landing, so each one is pinned
behaviourally: given a config it cannot read, does it BLOCK or does it pass?

Deliberately NOT a tree-wide AST ratchet. The scan run for this station found
179 `if not <path>.exists(): return <empty>` / `except: return <empty>` sites
across cli/ core/ harness/ scripts/, and the overwhelming majority are correct —
`_read_json` returning `{}` for a missing file is a reader, not a checker, and
the caller decides. The distinction is semantic and AST cannot make it; a
ratchet here would be 179 false positives and would be silenced within a round.
The scan and that judgement are recorded in docs/PROPOSAL_ADJUDICATIONS.md.
"""
from __future__ import annotations

import pytest

import core.quality_gate.gate_thresholds as _gt
import harness_cli  # noqa: F401  entry-first load order
from harness import tool_checks  # noqa: E402
from harness.harness_bridge import (  # noqa: E402
    _check_tool_evidence,
    _run_harness_cross_validation,
)

pytestmark = [pytest.mark.core]


class _Ctx:
    """Minimal GateContext stand-in — these checks read three fields."""

    def __init__(self, project_root, gate_num=2):
        self.project_root = str(project_root)
        self.gate_num = gate_num
        self.work_dir = str(project_root)


def _absent(tmp_path):
    return tmp_path / "gate2_p3_exit.yaml"  # never created


# ── S3: tool-evidence authenticity ──────────────────────────────────────

def test_s3_blocks_when_the_gate_config_is_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(_gt, "gate_config_path", lambda _n: _absent(tmp_path))
    violations = _check_tool_evidence(_Ctx(tmp_path), {"breakdown": {}})
    assert violations, (
        "a missing framework-owned config must produce a violation — returning "
        "[] is indistinguishable from 'the evidence checked out'"
    )
    assert any("gate config" in v for v in violations)


def test_s3_blocks_when_the_gate_config_is_corrupt(tmp_path, monkeypatch):
    cfg = tmp_path / "gate2_p3_exit.yaml"
    cfg.write_text("dimensions: [{name: linting\n", encoding="utf-8")  # unclosed
    monkeypatch.setattr(_gt, "gate_config_path", lambda _n: cfg)
    violations = _check_tool_evidence(_Ctx(tmp_path), {"breakdown": {}})
    assert any("unreadable" in v for v in violations)


def test_s3_raises_on_an_unknown_gate_num(tmp_path, monkeypatch):
    """gate_num comes from the framework's own GateContext. A value outside 1-4
    is a caller bug, and Round 29 returned [] for it — a programming error
    reported as 'no fabrication found'. It now reaches the crash boundary."""
    monkeypatch.setattr(
        _gt, "gate_config_path",
        lambda g: (_ for _ in ()).throw(ValueError(f"bad gate {g}")),
    )
    with pytest.raises(ValueError):
        _check_tool_evidence(_Ctx(tmp_path, gate_num=99), {"breakdown": {}})


# ── S4: framework cross-validation ──────────────────────────────────────

def test_s4_blocks_when_the_gate_config_is_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(_gt, "gate_config_path", lambda _n: _absent(tmp_path))
    violations = _run_harness_cross_validation(_Ctx(tmp_path), {"breakdown": {}})
    assert violations


def test_s4_raises_on_an_unknown_gate_num(tmp_path, monkeypatch):
    monkeypatch.setattr(
        _gt, "gate_config_path",
        lambda g: (_ for _ in ()).throw(ValueError(f"bad gate {g}")),
    )
    with pytest.raises(ValueError):
        _run_harness_cross_validation(_Ctx(tmp_path, gate_num=99), {"breakdown": {}})


# ── S2: tool availability ───────────────────────────────────────────────

def test_s2_is_not_switched_off_by_an_environment_variable(tmp_path, monkeypatch):
    """`CI=1` must not disable tool verification — for harness's own CI or for
    anyone who sets it. See tests/test_tool_checks.py for the full history."""
    cfg = tmp_path / "gate1_per_fr.yaml"
    cfg.write_text(
        "dimensions:\n  - {name: linting, tool: ruff, requires_tool_execution: true}\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("HARNESS_SKIP_TOOL_CHECKS", raising=False)
    monkeypatch.setenv("CI", "true")
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setattr(_gt, "gate_config_path", lambda _n: cfg)
    monkeypatch.setattr(
        tool_checks, "check_tool_for_dim", lambda *_a, **_k: (False, "ruff missing")
    )
    ok, missing = tool_checks.verify_gate_tools(1, str(tmp_path))
    assert ok is False and missing


# ── B3: Gate 4 CRG reconnaissance requirement ───────────────────────────

def test_b3_blocks_when_the_gate4_config_is_missing(tmp_path, monkeypatch):
    """An unreadable gate-4 config left `_crg_recon_required` at its False
    default, so the requirement silently evaporated instead of blocking."""
    from cli.gate_cmds import _check_gate4_prerequisites

    (tmp_path / ".methodology").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".sessi-work").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(_gt, "gate_config_path", lambda _n: _absent(tmp_path))
    blocked, _waivers = _check_gate4_prerequisites(tmp_path)
    assert blocked is True
