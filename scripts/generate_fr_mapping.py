#!/usr/bin/env python3
"""
Generate FR Mapping - Generate FR -> code file mapping from project structure.
=============================================================================

Purpose: Quickly generate FR mapping table for Phase 3.

Usage:
    python scripts/generate_fr_mapping.py --project /path/to/project

Output:
    .methodology/fr_mapping.json - FR -> code file mapping

Scan strategy:
    1. FR Tag parse: scan [FR-XX] or FR-XX: patterns in docstrings
"""

import argparse
import json
import re
import sys
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # repo root for core imports

from core.utils.project_layout import ProjectLayout  # noqa: E402

def extract_fr_tags(content: str) -> list:
    """Parse [FR-XX] or FR-XX: pattern from docstrings or code."""
    fr_ids = []
    patterns = [
        r'\[FR-(\d+)\]',
        r'FR-(\d+):',
        r'FR-(\d+)\s*-',
        r'FR-(\d+)\.',
        r'FR-(\d+)\s',
    ]
    for pattern in patterns:
        for m in re.findall(pattern, content, re.IGNORECASE):
            fr_ids.append(f"FR-{m}")
    return list(set(fr_ids))


def scan_for_fr_tags(project: Path) -> dict:
    """Scan all Python files, parse FR tags from docstrings."""
    fr_files: dict[str, list[str]] = defaultdict(list)
    src_dirs = [
        ProjectLayout(project).phase3_development_dir / "src",
        ProjectLayout(project).phase3_development_dir / "tests",
        project / "src",
        project / "tests",
        project / "lib",
    ]
    for src_dir in src_dirs:
        if not src_dir.exists():
            continue
        for py_file in src_dir.rglob("*.py"):
            try:
                content = py_file.read_text(errors="ignore")
                fr_ids = extract_fr_tags(content)
                rel_path = str(py_file.relative_to(project))
                for fr_id in fr_ids:
                    if rel_path not in fr_files[fr_id]:
                        fr_files[fr_id].append(rel_path)
            except Exception:  # nosec B112
                continue
    return dict(fr_files)


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(description="Generate FR -> Code mapping")
    parser.add_argument("--project", required=True, help="Project path")
    parser.add_argument("--output", default=".methodology/fr_mapping.json", help="Output path")
    args = parser.parse_args()

    project = Path(args.project)
    output_file = project / args.output

    print(f"\n{'='*50}")
    print("FR Mapping Generator")
    print(f"{'='*50}")
    print(f"Project: {project}")

    print("\n[Step 1] Scanning FR tags from docstrings...")
    fr_tag_mapping = scan_for_fr_tags(project)
    for fr_id, files in sorted(fr_tag_mapping.items()):
        print(f"  {fr_id}: {len(files)} files (FR tag)")

    print("\n[Step 2] Formatting results...")
    mapping = {}
    for fr_id in sorted(fr_tag_mapping.keys()):
        tag_files = fr_tag_mapping[fr_id]
        mapping[fr_id] = {
            "files": tag_files,
            "file_count": len(tag_files),
            "source": ["fr_tag"],
            "fr_tag_files": tag_files,
        }
        print(f"  {fr_id}: {len(tag_files)} files (fr_tag)")

    output_file.parent.mkdir(parents=True, exist_ok=True)
    with open(output_file, "w") as f:
        json.dump(mapping, f, indent=2, ensure_ascii=False)

    print(f"\nMapping saved to: {output_file}")
    total_files = sum(m["file_count"] for m in mapping.values())
    print(f"Total FRs: {len(mapping)}")
    print(f"FRs with docstring tags: {len(mapping)}")
    print(f"Total file mappings: {total_files}")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
