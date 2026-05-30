#!/usr/bin/env python3
"""crg_dump_communities.py — dump CRG communities as JSON.

Runs under the code-review-graph interpreter (the one with code_review_graph
installed), NOT the harness interpreter. Invoked as a subprocess by
harness/crg_independent.py against an already-built + post-processed graph.

Emits to stdout:
    {"communities": [{"name": str, "cohesion": float, "size": int}, ...]}

This is the framework's independent source of architecture (community_cohesion)
scores — produced from the real graph with no agent involvement.

Usage: <crg-python> crg_dump_communities.py <repo_root>
"""
import json
import sys


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: crg_dump_communities.py <repo_root>", file=sys.stderr)
        return 2
    repo = sys.argv[1]
    try:
        import code_review_graph.tools as t  # type: ignore[import-not-found]
    except ImportError as exc:
        print(f"code_review_graph not importable: {exc}", file=sys.stderr)
        return 3

    resp = t.list_communities_func(repo_root=repo)
    communities = [
        {
            "name": c.get("name", "unknown"),
            "cohesion": c.get("cohesion", 1.0),
            "size": c.get("size", 0),
        }
        for c in resp.get("communities", [])
    ]
    json.dump({"communities": communities}, sys.stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main())
