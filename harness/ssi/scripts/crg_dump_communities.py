#!/usr/bin/env python3
"""crg_dump_communities.py — dump CRG structural metrics as JSON.

Runs under the code-review-graph interpreter (the one with code_review_graph
installed), NOT the harness interpreter. Invoked as a subprocess by
harness/crg_independent.py against an already-built + post-processed graph.

Emits to stdout:
    {
      "communities": [{"name": str, "cohesion": float, "size": int}, ...],
      "large_functions_critical": [
          {"name": str, "line_count": int, "file_path": str}, ...
      ]
    }

`large_functions_critical` contains functions ≥ 500 lines and feeds the
large-function penalty in `crg_independent.run_independent_crg()` (Phase 1
gatekeeper: each critical function penalises architecture_score by 5 pts,
capped at 20). If `find_large_functions_func` is unavailable in this
version of code_review_graph, the key is omitted (no penalty applied).

Usage: <crg-python> crg_dump_communities.py <repo_root>
"""
import json
import sys

_LARGE_FN_THRESHOLD = 500  # lines; functions at or above this are "critical"


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

    # ── communities (required) ────────────────────────────────────────
    resp = t.list_communities_func(repo_root=repo)
    communities = []
    for c in resp.get("communities", []):
        # Deduplicate file paths from member nodes ("path/to/file.py::Symbol").
        # Passed to compute_community_cohesion_score so it can exclude test-only
        # and non-product communities (e.g. .methodology/) by path rather than
        # by community name alone.
        members = c.get("members") or c.get("nodes") or []
        files: list[str] = list(dict.fromkeys(
            m.split("::")[0] for m in members if "::" in m
        ))
        communities.append({
            "name": c.get("name", "unknown"),
            "cohesion": c.get("cohesion", 1.0),
            "size": c.get("size", 0),
            "files": files[:30],  # cap to keep JSON small; enough for path detection
        })

    # ── large functions ≥ 500 lines (optional — gatekeeper Phase 1) ──
    large_functions_critical: list[dict] = []
    try:
        lf_resp = t.find_large_functions_func(
            repo_root=repo, min_lines=_LARGE_FN_THRESHOLD, kind="Function",
        )
        for fn in lf_resp.get("results", []):
            lc = fn.get("line_count") or 0
            if lc >= _LARGE_FN_THRESHOLD:
                large_functions_critical.append({
                    "name": fn.get("name", "?"),
                    "line_count": lc,
                    "file_path": fn.get("relative_path") or fn.get("file_path", "?"),
                })
    except AttributeError:
        # find_large_functions_func not available in this version — no penalty
        pass

    output: dict = {"communities": communities,
                    "large_functions_critical": large_functions_critical}

    json.dump(output, sys.stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main())
