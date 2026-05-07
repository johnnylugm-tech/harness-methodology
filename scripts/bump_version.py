#!/usr/bin/env python3
"""
Version Bump Script
==================
Synchronizes version number across all project files.

Usage:
    python scripts/bump_version.py          # Show current version
    python scripts/bump_version.py 6.13.0  # Bump to specified version
"""

import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent


def get_current_version():
    """Read current version from __init__.py"""
    init_file = PROJECT_ROOT / "__init__.py"
    content = init_file.read_text()
    match = re.search(r'__version__\s*=\s*"([^"]+)"', content)
    return match.group(1) if match else None


def bump_version(new_version: str):
    """Update version across all project files."""
    files_patterns = {
        "__init__.py": (r'__version__\s*=\s*"[^"]+"', f'__version__ = "{new_version}"'),
        "pyproject.toml": (r'version\s*=\s*"[^"]+"', f'version = "{new_version}"'),
        "README.md": (r'v\d+\.\d+\.\d+', f"v{new_version}"),
    }

    updated = []
    for filename, (pattern, replacement) in files_patterns.items():
        filepath = PROJECT_ROOT / filename
        if not filepath.exists():
            print(f"  SKIP: {filename} not found")
            continue

        content = filepath.read_text()
        new_content, count = re.subn(pattern, replacement, content)
        if count > 0:
            filepath.write_text(new_content)
            updated.append((filename, count))
            print(f"  OK: {filename}: updated {count} occurrence(s)")
        else:
            print(f"  SKIP: {filename}: pattern not found")

    print(f"\nVersion bumped to v{new_version}")
    return updated


def main():
    """CLI entry point."""
    if len(sys.argv) > 1:
        new_version = sys.argv[1]
        if not re.match(r'^\d+\.\d+\.\d+$', new_version):
            print(f"Invalid version format: {new_version}")
            print("Expected: MAJOR.MINOR.PATCH (e.g., 6.13.0)")
            sys.exit(1)
        bump_version(new_version)
    else:
        current = get_current_version()
        print(f"Current version: v{current}")


if __name__ == "__main__":
    main()
