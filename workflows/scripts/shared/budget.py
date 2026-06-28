"""budget.py — Bash-callable budget guard for workflow JS agent() calls.

Root cause (G of 5-meta-pattern convergence plan): 10 occurrences of
`if budget.remaining() < 100000` / `if budget.spent() > N` checks across
phase workflow JS. Each phase implements its own threshold (100k, 80k, 50k)
and decision logic (exit / continue / warning).

This module consolidates budget-checking into ONE Bash-callable helper:

    bash('python3 workflows/scripts/shared/budget.py --budget 100000 \\
         --threshold 0.2 --spent 50000 --total 200000 --json-out /tmp/out.json')

Returns structured JSON:
    {
      "status": "OK" | "LOW" | "EXHAUSTED" | "INVALID",
      "remaining": int,
      "remaining_ratio": float,   # remaining / total
      "threshold_ratio": float,
      "decision": "continue" | "warn" | "exit",
      "diagnostic": str | None
    }

Commonality: 8 phase workflow JS files all use budget guards.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


# Status enum
STATUS_OK = "OK"
STATUS_LOW = "LOW"
STATUS_EXHAUSTED = "EXHAUSTED"
STATUS_INVALID = "INVALID"


def check_budget(
    spent: int,
    total: int,
    threshold_ratio: float = 0.2,
    exhausted_ratio: float = 0.05,
) -> dict[str, Any]:
    """Check budget state and decide continue / warn / exit.

    Args:
      spent: tokens (or units) consumed so far
      total: total budget allocated
      threshold_ratio: when remaining/total <= this, decision=warn (default 0.2)
      exhausted_ratio: when remaining/total <= this, decision=exit (default 0.05)

    Returns:
      {
        "status": "OK"|"LOW"|"EXHAUSTED"|"INVALID",
        "remaining": int,
        "remaining_ratio": float,
        "threshold_ratio": float,
        "decision": "continue"|"warn"|"exit",
        "diagnostic": str | None
      }
    """
    if total <= 0:
        return {
            "status": STATUS_INVALID,
            "remaining": 0,
            "remaining_ratio": 0.0,
            "threshold_ratio": threshold_ratio,
            "decision": "exit",
            "diagnostic": f"invalid total={total} (must be > 0)",
        }
    if spent < 0:
        return {
            "status": STATUS_INVALID,
            "remaining": total,
            "remaining_ratio": 1.0,
            "threshold_ratio": threshold_ratio,
            "decision": "continue",
            "diagnostic": f"invalid spent={spent} (must be >= 0)",
        }

    remaining = max(0, total - spent)
    ratio = remaining / total

    if ratio <= exhausted_ratio:
        status = STATUS_EXHAUSTED
        decision = "exit"
        diagnostic = (
            f"budget exhausted: {remaining}/{total} remaining "
            f"({ratio:.1%} <= {exhausted_ratio:.1%} threshold)"
        )
    elif ratio <= threshold_ratio:
        status = STATUS_LOW
        decision = "warn"
        diagnostic = (
            f"budget low: {remaining}/{total} remaining "
            f"({ratio:.1%} <= {threshold_ratio:.1%} threshold)"
        )
    else:
        status = STATUS_OK
        decision = "continue"
        diagnostic = None

    return {
        "status": status,
        "remaining": remaining,
        "remaining_ratio": round(ratio, 4),
        "threshold_ratio": threshold_ratio,
        "decision": decision,
        "diagnostic": diagnostic,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _cli() -> int:
    parser = argparse.ArgumentParser(
        description="Check budget state and return continue/warn/exit decision."
    )
    parser.add_argument(
        "--spent", type=int, required=True,
        help="Units consumed so far.",
    )
    parser.add_argument(
        "--total", type=int, required=True,
        help="Total budget allocated.",
    )
    parser.add_argument(
        "--threshold", type=float, default=0.2,
        help="When remaining/total <= this, decision=warn (default: 0.2).",
    )
    parser.add_argument(
        "--exhausted-threshold", type=float, default=0.05,
        help="When remaining/total <= this, decision=exit (default: 0.05).",
    )
    parser.add_argument(
        "--json-out", default=None,
        help="If set, write JSON to this path; otherwise print to stdout.",
    )
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    result = check_budget(
        spent=args.spent,
        total=args.total,
        threshold_ratio=args.threshold,
        exhausted_ratio=args.exhausted_threshold,
    )

    json_text = json.dumps(result, indent=2, ensure_ascii=False)
    if args.json_out:
        Path(args.json_out).write_text(json_text, encoding="utf-8")
    else:
        print(json_text)

    if not args.quiet:
        s = result["status"]
        d = result["decision"]
        msg = f"[budget] {s} decision={d} remaining={result['remaining']}"
        if result["diagnostic"]:
            msg += f" — {result['diagnostic']}"
        print(msg, file=sys.stderr)

    # Exit codes:
    # 0 OK (continue), 1 LOW (warn, recoverable), 2 EXHAUSTED (exit), 3 INVALID
    return {
        STATUS_OK: 0,
        STATUS_LOW: 1,
        STATUS_EXHAUSTED: 2,
        STATUS_INVALID: 3,
    }[result["status"]]


if __name__ == "__main__":
    sys.exit(_cli())