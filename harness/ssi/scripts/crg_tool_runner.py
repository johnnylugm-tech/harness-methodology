#!/usr/bin/env python3
"""crg_tool_runner.py — invoke a single code_review_graph.tools function as JSON.

Runs under the code-review-graph interpreter (the one with code_review_graph
installed), NOT the harness interpreter. Invoked as a subprocess by
harness/crg_api.py so that CRG's rich analysis tools (review_context,
impact_radius, affected_flows, minimal_context, knowledge_gaps, …) work in
ANY environment — not just inside a Claude Code session where the `mcp_tools`
module is injected.

This is the universal-backend twin of crg_dump_communities.py (which is
architecture-score-specific). It dispatches an arbitrary tools function by name.

Protocol:
    argv[1] = JSON: {"func": "<tools_func_name>", "repo_root": "<path>",
                     "kwargs": {<extra kwargs>}}
    stdout  = JSON result dict from the tools function
    stderr  = human-readable error; non-zero exit on failure

Usage: <crg-python> crg_tool_runner.py '<json-payload>'
"""
import json
import sys


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: crg_tool_runner.py '<json-payload>'", file=sys.stderr)
        return 2
    try:
        payload = json.loads(sys.argv[1])
    except json.JSONDecodeError as exc:
        print(f"invalid JSON payload: {exc}", file=sys.stderr)
        return 2

    func_name = payload.get("func")
    repo_root = payload.get("repo_root")
    kwargs = payload.get("kwargs", {}) or {}
    if not func_name or not repo_root:
        print("payload must contain 'func' and 'repo_root'", file=sys.stderr)
        return 2

    try:
        import code_review_graph.tools as t  # type: ignore[import-not-found]
    except ImportError as exc:
        print(f"code_review_graph not importable: {exc}", file=sys.stderr)
        return 3

    fn = getattr(t, func_name, None)
    if fn is None or not callable(fn):
        print(f"code_review_graph.tools has no callable '{func_name}'", file=sys.stderr)
        return 4

    try:
        # Bug fix P6-2026-07-07: filter kwargs against the tool's actual
        # signature so a CRG tool version mismatch (e.g. v2.x dropping
        # `min_fan_in` from get_hub_nodes_func) returns {} via the
        # graceful `args.gate_kwargs_filter` path instead of crashing on
        # `got an unexpected keyword argument`. The CRG bridge calls
        # these tools with extra kwargs that earlier versions accepted;
        # older tools should still succeed with what they do support.
        try:
            import inspect
            _sig = inspect.signature(fn)
            if not any(
                p.kind == inspect.Parameter.VAR_KEYWORD
                for p in _sig.parameters.values()
            ):
                _accepted = set(_sig.parameters.keys())
                kwargs = {k: v for k, v in kwargs.items() if k in _accepted}
        except (TypeError, ValueError):
            pass  # signature introspection failed — pass kwargs verbatim
        result = fn(repo_root=repo_root, **kwargs)
    except Exception as exc:  # surface the tool's own error to the caller
        print(f"{func_name} failed: {exc}", file=sys.stderr)
        return 5

    json.dump(result if result is not None else {}, sys.stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main())
