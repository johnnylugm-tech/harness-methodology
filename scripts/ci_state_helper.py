#!/usr/bin/env python3
"""
CI state-helper — safe extraction of fields from .methodology/state.json.

Why this script exists (CV-5 from robustness audit):
  The CI workflows embedded bare `python3 -c "import json; d=json.load(open(...))"`
  one-liners. If state.json is corrupted or empty (CV-3 race, mis-edit, partial
  write), that one-liner raises an uncaught traceback. The CI run dies and
  every push to main is blocked until someone manually repairs state.json.

This helper:
  * Returns sensible defaults on missing / unreadable / malformed state.json.
  * Emits a stderr [WARN] so operators see why a default was used.
  * Never raises an uncaught exception, even with a truncated file.

Exit codes:
  0  — value printed to stdout (the only output)
  10 — invalid usage (bad arguments)

Examples:
  $ python3 scripts/ci_state_helper.py get current_phase --default 0
  3
  $ python3 scripts/ci_state_helper.py get last_milestone_command --default ""
  push-milestone --type p3-pre-gate2
  $ python3 scripts/ci_state_helper.py is-p8 --state-file .methodology/state.json
  true
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Optional


def _read_state(state_file: Path) -> Optional[dict]:
    """Read state.json safely. Returns None on any failure, emits [WARN] to stderr."""
    if not state_file.exists():
        return None
    try:
        text = state_file.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"[WARN] ci_state_helper: read {state_file} failed: {exc}", file=sys.stderr)
        return None
    if not text.strip():
        print(f"[WARN] ci_state_helper: {state_file} is empty", file=sys.stderr)
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        print(
            f"[WARN] ci_state_helper: {state_file} is not valid JSON ({exc}). "
            f"Treating as empty state.",
            file=sys.stderr,
        )
        return None


def cmd_get(args: argparse.Namespace) -> int:
    """Print a field value from state.json, or the default if anything fails."""
    state = _read_state(Path(args.state_file))
    if state is None:
        print(args.default)
        return 0
    value: Any = state.get(args.field, args.default)
    # Print scalar values directly; for nested structures, dump JSON.
    if isinstance(value, (dict, list)):
        print(json.dumps(value, ensure_ascii=False))
    else:
        print(value if value is not None else args.default)
    return 0


def cmd_is_p8(args: argparse.Namespace) -> int:
    """Print 'true' iff state is P8-completed (P8 milestone pushed). Else 'false'."""
    state = _read_state(Path(args.state_file))
    if state is None:
        print("false")
        return 0
    phase = state.get("current_phase", 0)
    last_milestone = state.get("last_milestone_command", "") or ""
    try:
        phase_int = int(phase)
    except (ValueError, TypeError):
        phase_int = 0
    if phase_int > 8 or (phase_int == 8 and "p8" in last_milestone.lower()):
        print("true")
    else:
        print("false")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Safe state.json field extractor for CI.")
    sub = p.add_subparsers(dest="command", required=True)

    g = sub.add_parser("get", help="Print a single field value or default.")
    g.add_argument("field", help="JSON key to extract (e.g. current_phase)")
    g.add_argument("--state-file", default=".methodology/state.json")
    g.add_argument("--default", default="", help="Value to print if field missing or state.json unreadable.")
    g.set_defaults(func=cmd_get)

    p8 = sub.add_parser("is-p8", help="Print 'true' iff P8 milestone pushed, else 'false'.")
    p8.add_argument("--state-file", default=".methodology/state.json")
    p8.set_defaults(func=cmd_is_p8)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
