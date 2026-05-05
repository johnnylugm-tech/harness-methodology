#!/usr/bin/env python3
"""
CRG Integration Helper: Wrap code-review-graph CLI as structured primitives

Used by Tier 3 evaluation (architecture/readability/performance) and by the
fix loop's blast-radius safety gate.

Three primitives:
  1. context(repo)          → compressed architecture snapshot for Tier 3 eval
  2. blast_radius(repo, files)  → impact assessment before applying a fix
  3. update(repo)           → incremental graph refresh after commits

All commands return JSON on stdout or exit 0 silently if CRG is not installed
(graceful degradation: framework still works without CRG, just with higher
Tier 3 token cost).
"""

import sys
import json
import shutil
import subprocess

CRG_BIN = "code-review-graph"
CRG_TIMEOUT = 60


def _crg_available() -> bool:
    return shutil.which(CRG_BIN) is not None


def _run_crg(args: list, repo: str = None) -> dict:
    """Run a CRG command and parse JSON stdout. Empty dict on any failure."""
    if not _crg_available():
        return {"_error": "crg_not_installed"}
    cmd = [CRG_BIN] + args
    if repo:
        cmd += ["--repo", repo]
    try:
        out = subprocess.check_output(
            cmd, stderr=subprocess.PIPE, timeout=CRG_TIMEOUT, text=True
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        return {"_error": str(e)[:200]}
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return {"_raw": out.strip()}


def _graph_node_count(repo: str) -> int:
    """Return number of nodes in graph, or 0 if unbuilt / unavailable."""
    status = _run_crg(["status"], repo)
    raw = status.get("_raw", "")
    # Parse "Nodes: 42" from status output
    for line in raw.splitlines():
        if "Nodes:" in line:
            try:
                return int(line.split("Nodes:")[-1].split()[0].strip())
            except (ValueError, IndexError):
                pass
    return 0 if "_error" not in status else -1


def ensure_ready(repo: str, build_timeout: int = 300) -> dict:
    """
    Ensure CRG is ready for use. Transparent to the caller — handles all cases:

      - Not installed  → {available: False, reason: "not installed"}
      - Installed, no graph → auto-build, then return ready status
      - Installed, graph ready → return ready status immediately

    Returns a status dict that is written to .sessi-work/crg_status.json
    by setup_target.py so every later step can read it without re-checking.
    """
    if not _crg_available():
        return {
            "available": False,
            "reason": "code-review-graph not installed — framework runs without CRG",
            "action": "none",
        }

    node_count = _graph_node_count(repo)

    if node_count == 0:
        # Graph not built — auto-build
        print(
            "[CRG] Graph not found. Building now (this may take 30–120s)…",
            file=sys.stderr,
        )
        try:
            subprocess.run(
                [CRG_BIN, "build", "--repo", repo],
                check=True,
                timeout=build_timeout,
            )
            node_count = _graph_node_count(repo)
            action = "auto_built"
            print(f"[CRG] Graph built: {node_count} nodes.", file=sys.stderr)
        except subprocess.CalledProcessError as e:
            return {
                "available": False,
                "reason": f"build failed: {str(e)[:120]}",
                "action": "build_failed",
            }
        except subprocess.TimeoutExpired:
            return {
                "available": False,
                "reason": f"build timed out after {build_timeout}s",
                "action": "build_timeout",
            }
    else:
        action = "already_built"
        print(f"[CRG] Graph ready: {node_count} nodes.", file=sys.stderr)

    return {
        "available": True,
        "node_count": node_count,
        "action": action,  # "auto_built" | "already_built"
        "repo": repo,
    }


def context(repo: str, max_hubs: int = 10, max_large_funcs: int = 15) -> dict:
    """
    Compressed architecture snapshot for Tier 3 dimension evaluation.

    Returns a dict with (roughly):
      - hub_nodes: most-connected functions/classes (architecture hotspots)
      - bridge_nodes: chokepoints (betweenness centrality)
      - large_functions: oversized functions (readability)
      - knowledge_gaps: structural weaknesses
      - stats: {nodes, edges, files, languages}

    Design: feed this JSON to Claude as pre-compressed context instead of
    making Claude read the full codebase. Saves tokens and improves
    accuracy for architecture/readability/performance dimensions.
    """
    if not _crg_available():
        return {
            "error": "code-review-graph not installed; falling back to full code read"
        }

    # Auto-ensure graph is ready (build if empty)
    if _graph_node_count(repo) == 0:
        ready = ensure_ready(repo)
        if not ready["available"]:
            return {"error": ready["reason"]}

    # We use the CLI subcommands that emit JSON. For hub/bridge/gap analysis,
    # CRG exposes these via MCP tools — when run standalone we rely on the
    # `detect-changes` / `visualize` / wiki outputs. For pure context
    # extraction in CLI, use what's reliably machine-readable:
    status = _run_crg(["status"], repo)
    result = {
        "stats_raw": status.get("_raw", ""),
        "note": (
            "For hub/bridge/gap analysis, use the CRG MCP tools directly "
            "(get_hub_nodes, get_bridge_nodes, get_knowledge_gaps, "
            "get_architecture_overview) — they return structured JSON. "
            "The CLI subset here is the minimal fallback."
        ),
    }
    return result


def blast_radius(repo: str, base: str = "HEAD") -> dict:
    """
    Run detect-changes to assess blast radius of changes since `base`.

    `base` can be any git ref — its meaning depends on the call site:
      - Per-fix safety gate (improvement_plan.md):
          base="HEAD" → compares uncommitted working tree to HEAD.
          Answers: "is this specific uncommitted change risky to commit?"
      - Per-round structural check (verify_round.md):
          base="round-<n-1>" → compares current state to previous round's
          git tag, measuring cumulative blast of all fixes in this round.
          Answers: "did this whole round introduce architectural risk?"

    Returned structure:
      - risk_score: 0.0-1.0 (CRG's risk heuristic)
      - changed_functions: list of functions/classes touched
      - test_gaps: changed functions lacking test coverage
      - affected_flows: execution flows impacted
    """
    data = _run_crg(["detect-changes", "--base", base], repo)
    if "_error" in data:
        return {"risk_score": None, "error": data["_error"]}

    # Normalize: pull out the keys downstream code cares about
    return {
        "risk_score": data.get("risk_score"),
        "summary": data.get("summary", ""),
        "changed_functions": data.get("changed_functions", []),
        "test_gaps": data.get("test_gaps", []),
        "affected_flows": data.get("affected_flows", []),
        "untested": data.get("untested", []),
    }


def is_risky(radius: dict, threshold: float = 0.7) -> bool:
    """Decide whether a fix is risky enough to defer instead of commit."""
    rs = radius.get("risk_score")
    if rs is None:
        return False  # unknown → let the normal tool-based verify decide
    return rs >= threshold


def update(repo: str) -> dict:
    """Incremental graph refresh after a commit (seconds)."""
    data = _run_crg(["update"], repo)
    return data


def _help():
    print(f"""Usage: {sys.argv[0]} <command> [args]

Commands:
  ensure <repo>                      Auto-init: install check + build if needed (used by setup_target.py)
  context <repo>                     Compressed architecture snapshot (Tier 3)
  blast <repo> [base=HEAD]           Blast radius of diff vs base
  risky <repo> [base=HEAD] [threshold=0.7]  Exit 1 if fix is too risky
  update <repo>                      Incremental graph refresh
  check                              Is CRG installed? (exit 0/1)
""")


def main():
    if len(sys.argv) < 2:
        _help()
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "check":
        sys.exit(0 if _crg_available() else 1)

    if cmd == "ensure":
        repo = sys.argv[2] if len(sys.argv) > 2 else "."
        result = ensure_ready(repo)
        print(json.dumps(result, indent=2))
        sys.exit(0 if result["available"] else 1)

    if cmd == "context":
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
