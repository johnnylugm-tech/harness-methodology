#!/usr/bin/env python3
"""
CRG Integration CLI: Standalone bash-invocable wrapper for code-review-graph.

Used by prompt-driven evaluation (evaluate_dimension.md, crg_reconnaissance.md,
improvement_plan.md, verify_round.md) where Claude invokes it as a bash command:
    python3 scripts/crg_integration.py blast . HEAD
    python3 scripts/crg_integration.py ensure .

For programmatic use (Python API within HarnessBridge / AutoFixEngine), see
harness/crg_bridge.py (CRGBridge class).

Three primitives:
  1. context(repo)            → compressed architecture snapshot for Tier 3 eval
  2. blast_radius(repo, base) → impact assessment before applying a fix
  3. update(repo)             → incremental graph refresh after commits

CRG is mandatory (same tier as ruff/mypy/pytest). All functions use CRG MCP
tools directly when running inside Claude Code.
"""

import sys
import json
import shutil
import subprocess
from pathlib import Path

# CRG MCP tools are injected by Claude Code runtime — only available inside CC
try:
    from mcp_tools import (  # type: ignore[import-untyped]
        mcp__code_review_graph__build_or_update_graph_tool as _crg_build,
        mcp__code_review_graph__get_minimal_context_tool as _crg_minimal_context,
        mcp__code_review_graph__detect_changes_tool as _crg_detect_changes,
        mcp__code_review_graph__list_graph_stats_tool as _crg_stats,
    )

    _CRG_MCP_AVAILABLE = True
except ImportError:
    _CRG_MCP_AVAILABLE = False
    _crg_build = None  # pyright: ignore[reportAssignmentType]
    _crg_minimal_context = None  # pyright: ignore[reportAssignmentType]
    _crg_detect_changes = None  # pyright: ignore[reportAssignmentType]
    _crg_stats = None  # pyright: ignore[reportAssignmentType]


def _crg_available() -> bool:
    return _CRG_MCP_AVAILABLE and _crg_build is not None


# ── CLI / subprocess fallback (works outside Claude Code, where mcp_tools is
#    absent — Bash subprocesses, CI, plain Python). Mirrors crg_bridge's backend
#    but self-contained, since this script runs with ssi/scripts on sys.path and
#    cannot import the harness package. ────────────────────────────────────────
_CRG_CLI = shutil.which("code-review-graph")
_TOOL_RUNNER = Path(__file__).parent / "crg_tool_runner.py"


def _crg_interp() -> "str | None":
    """Interpreter that has code_review_graph (read from the CLI shebang)."""
    if not _CRG_CLI:
        return None
    try:
        first = Path(_CRG_CLI).read_text(encoding="utf-8", errors="replace").splitlines()[0]
        if first.startswith("#!"):
            interp = first[2:].strip().split()[0]
            if interp and Path(interp).exists():
                return interp
    except (OSError, IndexError):
        pass
    return sys.executable


def _run_tool(repo: str, func: str, **kwargs) -> dict:
    """Invoke code_review_graph.tools.<func> via crg_tool_runner under CRG's interpreter."""
    interp = _crg_interp()
    if not interp or not _TOOL_RUNNER.exists():
        return {}
    payload = json.dumps({"func": func, "repo_root": repo, "kwargs": kwargs})
    try:
        proc = subprocess.run(
            [interp, str(_TOOL_RUNNER), payload],
            cwd=repo, capture_output=True, text=True, timeout=180,
        )
        return json.loads(proc.stdout) if proc.returncode == 0 else {}
    except (subprocess.SubprocessError, OSError, json.JSONDecodeError):
        return {}


def _cli_run(repo: str, *args: str, timeout: int = 600) -> bool:
    """Run a code-review-graph CLI subcommand from `repo`. Returns success."""
    if not _CRG_CLI:
        return False
    try:
        proc = subprocess.run(
            [_CRG_CLI, *args], cwd=repo, capture_output=True, text=True, timeout=timeout,
        )
        return proc.returncode == 0
    except (subprocess.SubprocessError, OSError):
        return False


def _cli_node_count(repo: str) -> int:
    """Node count via `code-review-graph status` (CLI, no mcp_tools)."""
    if not _CRG_CLI:
        return -1
    if not (Path(repo) / ".code-review-graph" / "graph.db").exists():
        return 0
    try:
        proc = subprocess.run(
            [_CRG_CLI, "status"], cwd=repo, capture_output=True, text=True, timeout=60,
        )
        for line in proc.stdout.splitlines():
            if line.lower().startswith("nodes:"):
                return int(line.split(":", 1)[1].strip())
    except (subprocess.SubprocessError, OSError, ValueError):
        pass
    return 0


def _ensure_ready_cli(repo: str) -> dict:
    """ensure_ready without mcp_tools: build/refresh the graph via the CLI."""
    if not _CRG_CLI:
        return {
            "available": False,
            "reason": "code-review-graph not on PATH",
            "action": "none",
        }
    node_count = _cli_node_count(repo)
    if node_count <= 0:
        has_db = (Path(repo) / ".code-review-graph" / "graph.db").exists()
        if not _cli_run(repo, "update" if has_db else "build", timeout=600):
            return {"available": False, "reason": "CLI graph build failed", "action": "build_failed"}
        _cli_run(repo, "postprocess", timeout=300)
        node_count = _cli_node_count(repo)
        action = "auto_built"
    else:
        action = "already_built"
    return {"available": True, "node_count": node_count, "action": action, "repo": repo}


def _graph_node_count(repo: str) -> int:
    """Return number of nodes in graph, or 0 if unbuilt / unavailable."""
    if not _crg_available():
        return _cli_node_count(repo)
    try:
        stats = _crg_stats(repo_root=repo)
        return int(stats.get("total_nodes", stats.get("node_count", 0)))
    except Exception:
        return -1


def ensure_ready(repo: str) -> dict:
    """
    Ensure CRG graph is built and ready. Auto-builds if needed.

    Returns a status dict written to .sessi-work/crg_status.json by
    setup_target.py so downstream steps can read it without re-checking.
    """
    if not _crg_available():
        return _ensure_ready_cli(repo)

    node_count = _graph_node_count(repo)

    if node_count <= 0:
        print(
            "[CRG] Graph not found. Building now (this may take 30–120s)…",
            file=sys.stderr,
        )
        try:
            _crg_build(repo_root=repo, full_rebuild=True)
            node_count = _graph_node_count(repo)
            action = "auto_built"
            print(f"[CRG] Graph built: {node_count} nodes.", file=sys.stderr)
        except Exception as e:
            return {
                "available": False,
                "reason": f"build failed: {str(e)[:120]}",
                "action": "build_failed",
            }
    else:
        action = "already_built"
        print(f"[CRG] Graph ready: {node_count} nodes.", file=sys.stderr)

    return {
        "available": True,
        "node_count": node_count,
        "action": action,
        "repo": repo,
    }


def context(repo: str) -> dict:
    """
    Compressed architecture snapshot for Tier 3 dimension evaluation.

    Returns a dict with hub_nodes, bridge_nodes, large_functions, stats, etc.
    Feed this to the LLM as pre-compressed context instead of full codebase reads.
    """
    if not _crg_available():
        ctx = _run_tool(repo, "get_minimal_context", task="quality evaluation")
        return ctx or {
            "error": "CRG MCP tools not available; falling back to full code read"
        }

    if _graph_node_count(repo) <= 0:
        ready = ensure_ready(repo)
        if not ready["available"]:
            return {"error": ready["reason"]}

    try:
        result = _crg_minimal_context(
            task="quality evaluation", repo_root=repo
        )
        return result
    except Exception as e:
        return {"error": str(e)[:200]}


def blast_radius(repo: str, base: str = "HEAD") -> dict:
    """
    Run detect-changes to assess blast radius of changes since `base`.

    `base` can be any git ref — its meaning depends on the call site:
      - Per-fix safety gate: base="HEAD" → uncommitted working tree vs HEAD
      - Per-round structural check: base="round-<n-1>" tag

    Returned structure:
      - risk_score: 0.0-1.0
      - changed_functions: list of functions/classes touched
      - test_gaps: changed functions lacking test coverage
      - affected_flows: execution flows impacted
    """
    if not _crg_available():
        data = _run_tool(repo, "detect_changes_func", base=base, detail_level="standard")
        if not data:
            return {"risk_score": None, "error": "CRG MCP tools not available"}
        return {
            "risk_score": data.get("risk_score"),
            "summary": data.get("summary", ""),
            "changed_functions": data.get("changed_functions", []),
            "test_gaps": data.get("test_gaps", []),
            "affected_flows": data.get("affected_flows", []),
            "untested": data.get("untested", []),
        }

    try:
        data = _crg_detect_changes(
            base=base, repo_root=repo, detail_level="standard"
        )
        return {
            "risk_score": data.get("risk_score"),
            "summary": data.get("summary", ""),
            "changed_functions": data.get("changed_functions", []),
            "test_gaps": data.get("test_gaps", []),
            "affected_flows": data.get("affected_flows", []),
            "untested": data.get("untested", []),
        }
    except Exception as e:
        return {"risk_score": None, "error": str(e)[:200]}


def is_risky(radius: dict, threshold: float = 0.7) -> bool:
    """Decide whether a fix is risky enough to defer instead of commit."""
    rs = radius.get("risk_score")
    if rs is None:
        return False
    return rs >= threshold


def update(repo: str) -> dict:
    """Incremental graph refresh after a commit (seconds)."""
    if not _crg_available():
        return ({"status": "updated"} if _cli_run(repo, "update", timeout=300)
                else {"error": "CRG update failed (CLI)"})
    try:
        _crg_build(repo_root=repo, full_rebuild=False)
        return {"status": "updated"}
    except Exception as e:
        return {"error": str(e)[:200]}


def _help():
    print(f"""Usage: {sys.argv[0]} <command> [args]

Commands:
  ensure <repo>                      Auto-init: build graph if needed
  context <repo>                     Compressed architecture snapshot (Tier 3)
  blast <repo> [base=HEAD]           Blast radius of diff vs base
  risky <repo> [base=HEAD] [threshold=0.7]  Exit 1 if fix is too risky
  update <repo>                      Incremental graph refresh
""")


def main():
    if len(sys.argv) < 2:
        _help()
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "ensure":
        repo = sys.argv[2] if len(sys.argv) > 2 else "."
        result = ensure_ready(repo)
        print(json.dumps(result, indent=2))
        sys.exit(0 if result["available"] else 1)

    elif cmd == "context":
        repo = sys.argv[2] if len(sys.argv) > 2 else "."
        print(json.dumps(context(repo), indent=2))

    elif cmd == "blast":
        repo = sys.argv[2] if len(sys.argv) > 2 else "."
        base = sys.argv[3] if len(sys.argv) > 3 else "HEAD"
        print(json.dumps(blast_radius(repo, base), indent=2))

    elif cmd == "risky":
        repo = sys.argv[2] if len(sys.argv) > 2 else "."
        base = sys.argv[3] if len(sys.argv) > 3 else "HEAD"
        threshold = float(sys.argv[4]) if len(sys.argv) > 4 else 0.7
        radius = blast_radius(repo, base)
        risky = is_risky(radius, threshold)
        print(
            json.dumps(
                {
                    "risky": risky,
                    "risk_score": radius.get("risk_score"),
                    "threshold": threshold,
                    "reason": radius.get("summary", ""),
                },
                indent=2,
            )
        )
        sys.exit(1 if risky else 0)

    elif cmd == "update":
        repo = sys.argv[2] if len(sys.argv) > 2 else "."
        print(json.dumps(update(repo), indent=2))

    else:
        _help()
        sys.exit(1)


if __name__ == "__main__":
    main()
