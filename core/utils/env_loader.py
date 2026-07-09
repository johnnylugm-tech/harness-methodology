""".env file loader (no external dependency).

Moved verbatim from harness_cli.py (絞殺者續章 S2) — pure-stdlib
infrastructure, no CLI concerns.
"""

from __future__ import annotations

import os
from pathlib import Path

__all__ = ["load_env_file"]


def load_env_file(env_path: Path) -> list[str]:
    """Load KEY=VALUE pairs from a .env file into os.environ.

    Rules:
    - Lines starting with # or blank are skipped.
    - Does NOT override variables already set in the shell environment.
    - Strips surrounding single/double quotes from values.
    - Inline comments (value # comment) are stripped.

    Returns list of keys that were loaded (empty if file not found).
    """
    if not env_path.is_file():
        return []
    loaded: list[str] = []
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if not key:
            continue
        value = value.split("#")[0].strip().strip('"').strip("'")
        if key not in os.environ:   # never override shell-level vars
            os.environ[key] = value
            loaded.append(key)
    return loaded
