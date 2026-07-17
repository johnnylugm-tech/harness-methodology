#!/usr/bin/env python3
"""Scan all manifest.json files and output module inventory.

Usage:
    python scripts/list-modules.py              # table output
    python scripts/list-modules.py --json       # JSON output
    python scripts/list-modules.py --validate   # validate all manifests (exit 1 on error)
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent

MANIFEST_DIRS = [
    "core/auto_fix",
    "core/quality_gate",
    "core/adapters",
    "enforcement",
    "harness",
    "detection",
    "gap_detector",
    "kill_switch",
]

REQUIRED_FIELDS = ["name", "version", "category", "description", "depends_on", "compat"]
VALID_CATEGORIES = {"core", "detection", "infrastructure", "safety", "control"}


_SEMVER_RE = re.compile(
    r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.\-]+)?$"  # MAJOR.MINOR.PATCH[-prerelease][+build]
)


def _is_semver(version: str) -> bool:
    # Bug M13 fix: previous implementation split on '.' and required exactly
    # 3 parts, which rejected "1.0.0-beta.1" (4 parts after split). Use a
    # proper semver regex that accepts pre-release / build metadata suffix.
    if not isinstance(version, str) or not version:
        return False
    return bool(_SEMVER_RE.match(version))


def _load_manifest(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def load_all_manifests() -> list[dict]:
    modules = []
    for rel in MANIFEST_DIRS:
        mp = REPO_ROOT / rel / "manifest.json"
        if not mp.exists():
            modules.append({"_path": rel, "_error": "manifest.json not found"})
            continue
        try:
            data = _load_manifest(mp)
        except json.JSONDecodeError as e:
            modules.append({"_path": rel, "_error": f"Invalid JSON: {e}"})
            continue
        data["_path"] = rel
        modules.append(data)
    return modules


def validate_manifest(manifest: dict, all_modules: list[str]) -> list[str]:
    errors = []
    for field in REQUIRED_FIELDS:
        if field not in manifest:
            errors.append(f"missing required field: {field}")

    if "version" in manifest and not _is_semver(manifest["version"]):
        errors.append(f"invalid version '{manifest['version']}' (must be SemVer X.Y.Z)")

    if "category" in manifest and manifest["category"] not in VALID_CATEGORIES:
        errors.append(
            f"unknown category '{manifest['category']}' "
            f"(allowed: {', '.join(sorted(VALID_CATEGORIES))})"
        )

    if "depends_on" in manifest:
        for dep in manifest["depends_on"]:
            if dep not in all_modules:
                errors.append(f"depends_on '{dep}' not found in any manifest")

    if "compat" in manifest:
        compat = manifest["compat"]
        if not isinstance(compat, dict):
            errors.append("compat must be a dict")
        elif "python" not in compat or "harness-methodology" not in compat:
            errors.append("compat must include 'python' and 'harness-methodology'")

    if "description" in manifest and len(manifest.get("description", "")) < 20:
        errors.append("description is suspiciously short (<20 chars)")

    return errors


def validate_skill_md_frontmatter() -> list[str]:
    """Check SKILL.md frontmatter name matches repo convention."""
    errors = []
    skill_md = REPO_ROOT / "SKILL.md"
    if not skill_md.exists():
        errors.append("SKILL.md not found")
        return errors
    content = skill_md.read_text(encoding="utf-8")
    if not content.startswith("---"):
        errors.append("SKILL.md is missing YAML frontmatter (--- ... ---)")
        return errors
    try:
        parts = content.split("---")
        if len(parts) < 3:
            errors.append("SKILL.md frontmatter not properly closed")
        else:
            import yaml
            fm = yaml.safe_load(parts[1])
            # Bug M14 fix: yaml.safe_load on empty / whitespace-only input
            # returns None. Calling None.get("name") raised AttributeError
            # which the bare except wrapped as a generic "parse error",
            # hiding the real cause. Check the type explicitly.
            if fm is None:
                errors.append("SKILL.md frontmatter is empty (no name declared)")
            elif not isinstance(fm, dict):
                errors.append(
                    f"SKILL.md frontmatter is not a mapping (got {type(fm).__name__})"
                )
            elif fm.get("name") != "harness-methodology":
                errors.append(
                    f"SKILL.md frontmatter name '{fm.get('name')}' "
                    f"does not match 'harness-methodology'"
                )
    except Exception as e:
        print(f"[WARN] list-modules: SKILL.md frontmatter parse error: {e}", file=sys.stderr)
        errors.append(f"SKILL.md frontmatter parse error: {e}")
    return errors


def cmd_table(modules: list[dict]) -> None:
    name_pad = max(len(m.get("name", m.get("_path", "?"))) for m in modules)
    ver_pad = max(len(m.get("version", "?")) for m in modules)
    cat_pad = max(len(m.get("category", "?")) for m in modules)

    header = f"{'name'.ljust(name_pad)}  {'version'.ljust(ver_pad)}  {'category'.ljust(cat_pad)}  depends_on"
    print(header)
    print("-" * len(header))
    for m in modules:
        if "_error" in m:
            print(f"{m['_path'].ljust(name_pad)}  ERROR: {m['_error']}")
            continue
        name = m["name"].ljust(name_pad)
        ver = m["version"].ljust(ver_pad)
        cat = m["category"].ljust(cat_pad)
        deps = ", ".join(m.get("depends_on", [])) or "—"
        print(f"{name}  {ver}  {cat}  {deps}")


def cmd_json(modules: list[dict]) -> None:
    out = []
    for m in modules:
        if "_error" in m:
            out.append({"name": m["_path"], "error": m["_error"]})
        else:
            out.append(
                {
                    "name": m["name"],
                    "version": m["version"],
                    "category": m["category"],
                    "description": m["description"],
                    "depends_on": m.get("depends_on", []),
                    "path": m["_path"],
                }
            )
    print(json.dumps(out, indent=2))


def cmd_validate(modules: list[dict]) -> bool:
    all_names = [m["name"] for m in modules if "_error" not in m]
    errors_found = 0

    for m in modules:
        label = m.get("_path", m.get("name", "?"))
        if "_error" in m:
            print(f"[FAIL] {label}: {m['_error']}")
            errors_found += 1
            continue
        errs = validate_manifest(m, all_names)
        if errs:
            for e in errs:
                print(f"[FAIL] {label}: {e}")
            errors_found += len(errs)
        else:
            print(f"[OK]   {label} v{m['version']}")

    # Validate SKILL.md frontmatter
    fm_errs = validate_skill_md_frontmatter()
    for e in fm_errs:
        print(f"[FAIL] SKILL.md: {e}")
        errors_found += 1
    if not fm_errs:
        print("[OK]   SKILL.md frontmatter")

    if errors_found:
        print(f"\n{errors_found} error(s) found.")
    else:
        print("\nAll valid.")
    return errors_found == 0


def main() -> None:
    args = sys.argv[1:]
    modules = load_all_manifests()

    if "--json" in args:
        cmd_json(modules)
    elif "--validate" in args:
        ok = cmd_validate(modules)
        sys.exit(0 if ok else 1)
    else:
        cmd_table(modules)


if __name__ == "__main__":
    main()
