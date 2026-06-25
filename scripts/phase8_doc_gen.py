#!/usr/bin/env python3
"""Phase 8 deterministic doc generator.

Reads state.json, quality_manifest.json, and the latest git commit,
then renders templates/CONFIG_RECORDS.md + templates/RELEASE_CHECKLIST.md
into 08-config/ via string.Template. Replaces the LLM-agent-based doc
production that previously stalled 4 times in P8 (workflow stall count
before this change).

Why deterministic:
  - CONFIG_RECORDS.md and RELEASE_CHECKLIST.md are 100% derivable from
    project metadata that already exists in .methodology/. The LLM
    had nothing to invent — every value came from git/state/manifest.
  - Phase 8 reviewer still has work: read the script's output, flag
    any missing sections, append human-only context. But the heavy
    lifting (placeholder filling, atomic writes) is no longer at the
    mercy of model latency.

Usage:
    python phase8_doc_gen.py --project .
    python phase8_doc_gen.py --project . --output-dir /tmp/p8test
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from string import Template

_HERE = Path(__file__).resolve().parent


def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _git(*args: str, cwd: Path) -> str:
    """Run a git command and return stdout. Empty string on failure."""
    try:
        r = subprocess.run(
            ["git", *args],
            capture_output=True, text=True,
            cwd=str(cwd), timeout=10,
        )
        return r.stdout.strip() if r.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


def _collect(project: Path) -> dict:
    """Aggregate all data needed to fill the templates."""
    state = _read_json(project / ".methodology" / "state.json")
    manifest = _read_json(project / ".methodology" / "quality_manifest.json")
    gate_results = manifest.get("gate_results", {}) if isinstance(manifest, dict) else {}
    gate1 = gate_results.get("gate1", {}) if isinstance(gate_results, dict) else {}

    frs = []
    for fr_id, info in sorted(gate1.items()):
        if isinstance(info, dict):
            frs.append({
                "id": fr_id,
                "score": info.get("score", "—"),
                "passed": info.get("passed", False),
            })

    return {
        "project_name": state.get("project", project.name),
        "version": _git("describe", "--tags", "--always", cwd=project) or "v0.0.0",
        "git_hash": _git("rev-parse", "--short", "HEAD", cwd=project) or "unknown",
        "release_date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "current_phase": state.get("current_phase", 8),
        "phase_truth_passed": state.get("phase_truth_passed", False),
        "frs": frs,
        "fr_summary": ", ".join(f["id"] for f in frs) or "(none)",
        "min_coverage": (manifest.get("quality_targets", {}) or {}).get("min_coverage", 80),
    }


def _render_template(template_path: Path, context: dict) -> str:
    """Render a Template file with safe_substitute (missing keys → literal ${key}).

    Safe substitute prevents KeyError when templates reference fields the
    generator does not yet collect — useful during incremental rollout
    where a new template field appears before the generator learns it.
    """
    text = template_path.read_text(encoding="utf-8")
    # Convert {var} placeholders to ${var} for Template. Three boundaries:
    #   (?<!\\)   — not preceded by a backslash (skip escaped \{)
    #   (?<!\{)   — not preceded by another { (skip {{var}} double braces
    #                which Template leaves literal as long as we don't
    #                touch the inner one)
    #   (?!\})    — not followed by a } (skip the closing half of {{var}})
    converted = re.sub(
        r"(?<!\\)(?<!\{)\{([a-zA-Z_][a-zA-Z_0-9]*)\}(?!\})", r"${\1}", text
    )
    return Template(converted).safe_substitute(context)


def generate(project_root: Path, output_dir: Path | None = None) -> dict:
    """Render both templates and write to output_dir. Returns a summary
    dict with the paths written (useful for tests)."""
    output_dir = output_dir or (project_root / "08-config")
    output_dir.mkdir(parents=True, exist_ok=True)
    context = _collect(project_root)

    config_text = _render_template(
        _HERE.parent / "templates" / "CONFIG_RECORDS.md", context
    )
    release_text = _render_template(
        _HERE.parent / "templates" / "RELEASE_CHECKLIST.md", context
    )

    (output_dir / "CONFIG_RECORDS.md").write_text(config_text, encoding="utf-8")
    (output_dir / "RELEASE_CHECKLIST.md").write_text(release_text, encoding="utf-8")
    return {
        "config_path": output_dir / "CONFIG_RECORDS.md",
        "release_path": output_dir / "RELEASE_CHECKLIST.md",
        "context": context,
    }


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--project", required=True, help="Project root")
    p.add_argument("--output-dir", help="Override output directory")
    args = p.parse_args()

    project = Path(args.project).resolve()
    if not (project / ".methodology").is_dir():
        print(f"[ERROR] {project}/.methodology/ not found", file=sys.stderr)
        return 1

    result = generate(project, Path(args.output_dir) if args.output_dir else None)
    print(f"  [P8] {result['config_path']}")
    print(f"  [P8] {result['release_path']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())