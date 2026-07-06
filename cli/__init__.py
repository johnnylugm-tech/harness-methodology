"""Command-family modules extracted from harness_cli.py (方案六).

Each module owns one command family: its cmd_* handlers plus a
``register(subparsers)`` that wires its argparse parsers. harness_cli.py
remains the entry point and re-exports every moved symbol, so existing
``from harness_cli import cmd_x`` imports keep working unchanged.

Rules:
- Function bodies move VERBATIM — no drive-by refactoring.
- One family per commit, full suite green before the next.
- core/ must never import cli/ (the CLI layer sits on top).
"""
