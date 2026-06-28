"""retry.py — Bash-callable retry helper for workflow JS agent() calls.

Root cause (G of 5-meta-pattern convergence plan): 8 phase workflow JS files
each have their own try/catch agent() retry loops (14 occurrences total).
Each loop is a slight variation: maxAttempts=3 vs 5, different backoff,
different failure messages. Patching one loop doesn't fix the others.

This module consolidates retry logic into ONE Bash-callable helper:

    bash('python3 workflows/scripts/shared/retry.py --command "echo X" \\
         --max-attempts 3 --initial-delay 1.0 --json-out /tmp/out.json')

Returns structured JSON:
    {
      "status": "OK|EXHAUSTED|INVALID",
      "attempts": int,
      "exit_code": int,
      "stdout": str,
      "stderr": str,
      "elapsed_seconds": float
    }

Commonality: 8 phase workflow JS files all use the same retry pattern.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


def run_with_retry(
    command: list[str],
    max_attempts: int = 3,
    initial_delay: float = 1.0,
    backoff: float = 2.0,
    max_delay: float = 30.0,
    timeout: float = 300.0,
) -> dict[str, Any]:
    """Run command with exponential backoff retry.

    Returns:
      {
        "status": "OK" | "EXHAUSTED" | "INVALID",
        "attempts": int,
        "exit_code": int | None,
        "stdout": str,
        "stderr": str,
        "elapsed_seconds": float,
        "diagnostic": str | None
      }

    Args:
      command: shell command as list (e.g. ['cat', '/path/to/file'])
      max_attempts: total attempts including first try (default 3)
      initial_delay: seconds before first retry (default 1.0)
      backoff: multiplier per retry (default 2.0 → 1s, 2s, 4s, ...)
      max_delay: cap on per-retry delay (default 30s)
      timeout: per-attempt timeout in seconds (default 300s)
    """
    if not command:
        return {
            "status": "INVALID",
            "attempts": 0,
            "exit_code": None,
            "stdout": "",
            "stderr": "",
            "elapsed_seconds": 0.0,
            "diagnostic": "empty command list",
        }

    start = time.monotonic()
    last_stdout = ""
    last_stderr = ""
    last_exit: int | None = None
    delay = initial_delay

    for attempt in range(1, max_attempts + 1):
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
            last_stdout = result.stdout or ""
            last_stderr = result.stderr or ""
            last_exit = result.returncode
            if result.returncode == 0:
                return {
                    "status": "OK",
                    "attempts": attempt,
                    "exit_code": 0,
                    "stdout": last_stdout,
                    "stderr": last_stderr,
                    "elapsed_seconds": round(time.monotonic() - start, 3),
                    "diagnostic": None,
                }
        except subprocess.TimeoutExpired as e:
            last_stdout = (e.stdout.decode("utf-8", errors="replace") if e.stdout else "")
            last_stderr = (e.stderr.decode("utf-8", errors="replace") if e.stderr else "")
            last_exit = -1
        except Exception as e:
            last_stdout = ""
            last_stderr = f"{type(e).__name__}: {e}"
            last_exit = -1

        # Don't sleep after the last attempt
        if attempt < max_attempts:
            time.sleep(min(delay, max_delay))
            delay *= backoff

    return {
        "status": "EXHAUSTED",
        "attempts": max_attempts,
        "exit_code": last_exit,
        "stdout": last_stdout,
        "stderr": last_stderr,
        "elapsed_seconds": round(time.monotonic() - start, 3),
        "diagnostic": f"command failed {max_attempts} times; last exit={last_exit}",
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _cli() -> int:
    parser = argparse.ArgumentParser(
        description="Run command with exponential backoff retry."
    )
    parser.add_argument(
        "--command",
        required=True,
        help="Shell command to run (single string, executed via /bin/sh -c).",
    )
    parser.add_argument(
        "--max-attempts", type=int, default=3,
        help="Total attempts including first try (default: 3).",
    )
    parser.add_argument(
        "--initial-delay", type=float, default=1.0,
        help="Initial delay between retries in seconds (default: 1.0).",
    )
    parser.add_argument(
        "--backoff", type=float, default=2.0,
        help="Backoff multiplier (default: 2.0).",
    )
    parser.add_argument(
        "--max-delay", type=float, default=30.0,
        help="Max delay between retries in seconds (default: 30.0).",
    )
    parser.add_argument(
        "--timeout", type=float, default=300.0,
        help="Per-attempt timeout in seconds (default: 300.0).",
    )
    parser.add_argument(
        "--json-out", default=None,
        help="If set, write JSON to this path; otherwise print to stdout.",
    )
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    result = run_with_retry(
        command=["/bin/sh", "-c", args.command],
        max_attempts=args.max_attempts,
        initial_delay=args.initial_delay,
        backoff=args.backoff,
        max_delay=args.max_delay,
        timeout=args.timeout,
    )

    json_text = json.dumps(result, indent=2, ensure_ascii=False)
    if args.json_out:
        Path(args.json_out).write_text(json_text, encoding="utf-8")
    else:
        print(json_text)

    if not args.quiet:
        s = result["status"]
        attempts = result["attempts"]
        ec = result["exit_code"]
        msg = f"[retry] {s} attempts={attempts} exit_code={ec}"
        if result["diagnostic"]:
            msg += f" — {result['diagnostic']}"
        print(msg, file=sys.stderr)

    # Exit codes:
    # 0 OK, 1 EXHAUSTED (recoverable, caller decides), 2 INVALID (bad input)
    if result["status"] == "OK":
        return 0
    if result["status"] == "EXHAUSTED":
        return 1
    return 2


if __name__ == "__main__":
    sys.exit(_cli())