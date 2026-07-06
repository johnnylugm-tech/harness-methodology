#!/usr/bin/env python3
"""
Generate FR Mapping - Generate FR -> code file mapping from project structure.
=============================================================================

Purpose: Quickly generate FR mapping table for Phase 3.

Usage:
    python scripts/generate_fr_mapping.py --project /path/to/project

Output:
    .methodology/fr_mapping.json - FR -> code file mapping

Scan strategy (priority order):
    1. FR Tag parse: scan [FR-XX] or FR-XX: patterns in docstrings
    2. Keyword match: fallback keyword scan (at least 2 keywords)
"""

import argparse
import json
import re
import sys
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # repo root for core imports

from core.utils.project_layout import ProjectLayout  # noqa: E402

# FR -> keyword mapping (for keyword match fallback)
FR_KEYWORDS = {
    "FR-01": ["lexicon", "mapping", "taiwan"],
    "FR-02": ["ssml", "parser", "voice"],
    "FR-03": ["chunk", "text", "split"],
    "FR-04": ["synth", "engine", "parallel", "async"],
    "FR-05": ["circuit", "breaker"],
    "FR-06": ["redis", "cache"],
    "FR-07": ["cli", "command", "routes"],
    "FR-08": ["ffmpeg", "audio", "format", "converter"],
    "FR-09": ["kokoro", "proxy", "client"],
}


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


def scan_for_keywords(project: Path, fr_id: str, keywords: list) -> list:
    """Keyword match fallback."""
    files = []
    src_dirs = [ProjectLayout(project).phase3_development_dir / "src", project / "src", project / "lib"]
    for src_dir in src_dirs:
        if not src_dir.exists():
            continue
        for py_file in src_dir.rglob("*.py"):
            try:
                content = py_file.read_text(errors="ignore").lower()
                matches = sum(1 for kw in keywords if kw.lower() in content)
                if matches >= 2:
                    files.append(str(py_file.relative_to(project)))
            except Exception:  # nosec B112
                continue
    return list(set(files))


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

    print("\n[Step 2] Keyword matching (fallback)...")
    fr_keyword_mapping = {}
    for fr_id, keywords in FR_KEYWORDS.items():
        files = scan_for_keywords(project, fr_id, keywords)
        fr_keyword_mapping[fr_id] = files
        if fr_id not in fr_tag_mapping:
            print(f"  {fr_id}: {len(files)} files (keyword fallback)")

    print("\n[Step 3] Merging results...")
    mapping = {}
    all_fr_ids = set(list(fr_tag_mapping.keys()) + list(fr_keyword_mapping.keys()))
    for fr_id in sorted(all_fr_ids):
        tag_files = fr_tag_mapping.get(fr_id, [])
        keyword_files = fr_keyword_mapping.get(fr_id, [])
        all_files = list(set(tag_files + keyword_files))
        source = []
        if tag_files:
            source.append("fr_tag")
        if keyword_files and fr_id not in fr_tag_mapping:
            source.append("keyword_fallback")
        elif keyword_files:
            source.append("keyword_supplement")
        mapping[fr_id] = {
            "files": all_files,
            "file_count": len(all_files),
            "source": source,
            "keywords": FR_KEYWORDS.get(fr_id, []),
            "fr_tag_files": tag_files,
            "keyword_files": [f for f in keyword_files if f not in tag_files],
        }
        print(f"  {fr_id}: {len(all_files)} files ({', '.join(source)})")

    output_file.parent.mkdir(parents=True, exist_ok=True)
    with open(output_file, "w") as f:
        json.dump(mapping, f, indent=2, ensure_ascii=False)

    print(f"\nMapping saved to: {output_file}")
    total_files = sum(m["file_count"] for m in mapping.values())
    frs_with_tags = sum(1 for m in mapping.values() if "fr_tag" in m["source"])
    print(f"Total FRs: {len(mapping)}")
    print(f"FRs with docstring tags: {frs_with_tags}")
    print(f"Total file mappings: {total_files}")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
