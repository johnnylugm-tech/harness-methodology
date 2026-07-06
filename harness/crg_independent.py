"""harness/crg_independent.py — framework-owned CRG metrics.

Computes the *architecture* dimension (community_cohesion) independently of the
agent by driving the `code-review-graph` CLI + Python API as a subprocess, then
writing `.sessi-work/crg_metrics.json`. The agent never produces these scores.

CRG is a **required component** (like ruff/mypy/pytest), verified at preflight.
Any failure raises `CrgIndependentError` — there is NO graceful degradation to
agent-reported scores (that would reopen the fabrication path this closes).

The CRG package lives under its own interpreter (the `code-review-graph` console
script's shebang), which is generally NOT the harness interpreter, so the graph
dump runs via subprocess under that interpreter (`crg_dump_communities.py`).
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

_DUMP_SCRIPT = Path(__file__).parent / "ssi" / "scripts" / "crg_dump_communities.py"
_BUILD_TIMEOUT = 600
_POST_TIMEOUT = 300
_DUMP_TIMEOUT = 120


class CrgIndependentError(RuntimeError):
    """Raised when the independent CRG run cannot produce metrics (hard failure)."""


def crg_binary() -> str:
    """Return the path to the `code-review-graph` CLI, or raise if absent."""
    binary = shutil.which("code-review-graph")
    if not binary:
        raise CrgIndependentError(
            "code-review-graph not found on PATH. CRG is a required component "
            "(install it during project setup, like ruff/mypy/pytest). The framework "
            "cannot compute the architecture dimension independently without it."
        )
    return binary


def _crg_interpreter(binary: str) -> str:
    """Return the Python interpreter that has code_review_graph installed.

    `code-review-graph` is a console script; its shebang points at the right
    interpreter. Falls back to the harness interpreter only if the shebang is
    unreadable (the subprocess will then surface an ImportError if wrong).
    """
    try:
        first = Path(binary).read_text(encoding="utf-8", errors="replace").splitlines()[0]
        if first.startswith("#!"):
            interp = first[2:].strip().split()[0]
            if interp and Path(interp).exists():
                return interp
    except (OSError, IndexError):
        pass
    return sys.executable


def _run(cmd: list[str], *, cwd: str | None, timeout: int, label: str) -> str:
    try:
        proc = subprocess.run(
            cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise CrgIndependentError(f"{label} timed out after {timeout}s") from exc
    except OSError as exc:
        raise CrgIndependentError(f"{label} could not be executed: {exc}") from exc
    if proc.returncode != 0:
        raise CrgIndependentError(
            f"{label} failed (rc={proc.returncode}): {(proc.stderr or proc.stdout)[-500:]}"
        )
    return proc.stdout


def _ensure_gitignored(project_root: str) -> None:
    """Ensure `.code-review-graph/` is git-ignored.

    `build` writes a tens-of-MB graph DB there; without this entry
    `commit_and_push_gate` could commit it. CRG's own `init` adds the entry, but a
    manually-installed binary may not have run it. Non-fatal hygiene only.
    """
    gi = Path(project_root) / ".gitignore"
    try:
        existing = gi.read_text(encoding="utf-8") if gi.exists() else ""
        if ".code-review-graph" in existing:
            return
        with gi.open("a", encoding="utf-8") as fh:
            if existing and not existing.endswith("\n"):
                fh.write("\n")
            fh.write("# Added by harness crg_independent\n.code-review-graph/\n")
    except OSError:
        pass


def run_independent_crg(project_root: str, work_dir: str) -> dict:
    """Build the graph, dump communities, compute crg_metrics, write crg_metrics.json.

    Returns the metrics dict. Raises CrgIndependentError on any failure.
    """
    binary = crg_binary()
    root = str(Path(project_root).resolve())

    # 0. Keep the graph DB out of version control.
    _ensure_gitignored(root)

    # 1. Build (first time) or incremental update (only re-parses changed files), then
    #    post-process to (re)compute communities. Using `update` when a graph already
    #    exists keeps re-finalize cheap — important under the Gate 4 auto-fix loop, which
    #    re-runs finalize_gate (and thus this) every round.
    _graph_db = Path(root) / ".code-review-graph" / "graph.db"
    _sub = "update" if _graph_db.exists() else "build"
    _run([binary, _sub], cwd=root, timeout=_BUILD_TIMEOUT, label=f"code-review-graph {_sub}")
    _run([binary, "postprocess"], cwd=root, timeout=_POST_TIMEOUT, label="code-review-graph postprocess")

    # 2. Dump communities via CRG's own interpreter.
    interp = _crg_interpreter(binary)
    stdout = _run(
        [interp, str(_DUMP_SCRIPT), root],
        cwd=None, timeout=_DUMP_TIMEOUT, label="crg_dump_communities",
    )
    try:
        recon = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise CrgIndependentError(f"crg_dump_communities produced invalid JSON: {exc}") from exc

    # 3. Reuse the existing deterministic cohesion formula.
    _scripts_dir = str(_DUMP_SCRIPT.parent)
    if _scripts_dir not in sys.path:
        sys.path.insert(0, _scripts_dir)
    from crg_analysis import compute_community_cohesion_score  # reused formula

    # Per-project calibration (crg_cohesion_healthy / crg_excludes in
    # .methodology/harness_config.json). Defensive import: this module
    # normally runs under the harness interpreter where `core` is on the
    # path, but odd sys.path setups must not break the gate.
    try:
        from core.harness_config import get_crg_settings
        _crg_cfg = get_crg_settings(root)
    except ImportError:
        _crg_cfg = {"cohesion_healthy": None, "excludes": []}

    cohesion = compute_community_cohesion_score(
        recon.get("communities", []),
        cohesion_healthy=_crg_cfg["cohesion_healthy"],
        extra_excludes=_crg_cfg["excludes"],
        project_root=root,
    )

    # 4. Large-function penalty (Phase 1 gatekeeper).
    #    crg_dump_communities.py includes large_functions_critical when
    #    find_large_functions_func is available. Each function ≥ 500 lines
    #    penalises the architecture score by 5 pts, capped at 20.
    critical_fns = recon.get("large_functions_critical", [])
    lf_penalty = min(len(critical_fns) * 5, 20)
    architecture_score = round(max(0.0, (cohesion.get("score") or 0.0) - lf_penalty), 1)

    if lf_penalty > 0:
        print(
            f"[crg] large_functions_penalty: -{lf_penalty} pts "
            f"({len(critical_fns)} function(s) ≥ 500 lines) "
            f"cohesion {cohesion.get('score'):.1f} → architecture_score {architecture_score:.1f}",
            file=sys.stderr,
        )

    metrics = {
        "community_cohesion": cohesion,
        "large_functions_critical": critical_fns,
        "large_functions_penalty": lf_penalty,
        "architecture_score": architecture_score,
        "_source": "framework-independent",
    }
    out = Path(work_dir) / "crg_metrics.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    return metrics
